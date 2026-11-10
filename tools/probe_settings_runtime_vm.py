"""Isolated ARM VM verification of the runtime-settings entry (rev2, B6).

The fixture is a native ARM program built from FIXTURE_C by this script, in
the same output directory it runs from.  Its select slot is a *modifiable
function pointer variable* (like Ecore's real relocated slot), so the loader
patches a pointer word rather than dereferencing function code.  Key events
carry the action word at contract offset +8, which the handler reads as
event[2].

Assertions use only data collected from the guest after the run:
natural-thread bootstrap, select passthrough/return preservation, power-timer
both directions, one launch per press edge, original handler preserved,
slot restoration, and loop survival.
"""
import argparse
import base64
import hashlib
import io
import json
from pathlib import Path
import re
import shlex
import subprocess
import time

import arm_vm
import probe_menu_startup_vm as vm
from probe_menu_entry_vm import collect as menu_collect  # noqa: F401  (kept for parity)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/review/2026-11-08-runtime-controls/settings-runtime-vm-rev2'
SOCKET = '/tmp/nx300m-settings-runtime-private'

FIXTURE_C = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>

static unsigned orig_select_calls;
static unsigned block_mode;
static int orig_select(int nfds, void *r, void *w, void *e, void *t) {
    (void)nfds; (void)r; (void)w; (void)e;
    /* Phase A (block_mode=0, no watchdog): the caller passes 5 s and the
     * wrapper must hand it through unchanged.
     * Phase B (block_mode=1, watchdog active): the caller passes NULL, an
     * indefinite wait. If the wrapper works, the NULL is replaced by a
     * bounded timeout so a blocked select can wake and run the watchdog. */
    int ok = block_mode ? (t != 0) : (t && ((int *)t)[0] == 5);
    orig_select_calls++;
    if (block_mode && orig_select_calls == 61) {
        int fd = open("/perf/select-timeouts", O_WRONLY|O_CREAT|O_TRUNC, 0600);
        if (fd >= 0) {
            char b[40];
            int n = snprintf(b, sizeof b, "%s %d\n", t ? "tv" : "null", t ? ((int *)t)[0] : -1);
            write(fd, b, (size_t)n); close(fd);
        }
    }
    if (orig_select_calls == 50) {
        int fd = open("/perf/fixture-50-selects", O_WRONLY|O_CREAT|O_TRUNC, 0600);
        if (fd >= 0) { const char *s = ok ? "ok" : "arg"; write(fd, s, strlen(s)); close(fd); }
    }
    return 3; /* distinctive non-zero result the wrapper must pass through */
}

static void mock_power_timer(int stop) {
    int fd = open("/perf/pt-calls", O_WRONLY|O_CREAT|O_APPEND, 0600);
    if (fd >= 0) { char b[2] = {'0' + (stop ? 1 : 0), '\n'}; write(fd, b, 2); close(fd); }
}

static void orig_key_handler(void *event) {
    (void)event;
    int fd = open("/perf/orig-key-calls", O_WRONLY|O_CREAT|O_APPEND, 0600);
    if (fd >= 0) { write(fd, "k", 1); close(fd); }
}

/* Stand-in for the liveview key table. Entry 12 is the custom key.
 * The real table is in the stock image's read-only data, which the loader
 * mprotects to RW, patches, and returns to RX. Keeping this array in a
 * writable page would make the loader's protection change break the
 * fixture's own globals, so it gets its own page and is only written
 * through the loader after installation. */
static unsigned key_table[1024] __attribute__((aligned(4096)));

/* Raw svc helpers used only as an ABI control, built the same way as the
 * production blob's wrappers (register-bound operands). */
static inline int raw_svc3(unsigned nr, unsigned a, unsigned b, unsigned c) {
    register unsigned r0 __asm__("r0") = a;
    register unsigned r1 __asm__("r1") = b;
    register unsigned r2 __asm__("r2") = c;
    register unsigned r7 __asm__("r7") = nr;
    __asm__ volatile("svc 0" : "+r"(r0) : "r"(r1), "r"(r2), "r"(r7) : "memory");
    return (int)r0;
}
static inline int raw_svc4(unsigned nr, unsigned a, unsigned b, unsigned c, unsigned d) {
    register unsigned r0 __asm__("r0") = a;
    register unsigned r1 __asm__("r1") = b;
    register unsigned r2 __asm__("r2") = c;
    register unsigned r3 __asm__("r3") = d;
    register unsigned r7 __asm__("r7") = nr;
    __asm__ volatile("svc 0" : "+r"(r0) : "r"(r1), "r"(r2), "r"(r3), "r"(r7) : "memory");
    return (int)r0;
}

/* The select slot: a function-pointer VARIABLE, exactly what the loader
 * expects to overwrite. The manifest publishes its address. */
static int (*select_slot)(int, void *, void *, void *, void *) = 0;

int main(void) {
    select_slot = orig_select;
    key_table[12] = (unsigned)orig_key_handler;
    FILE *m = fopen("/perf/settings-fixture.json", "w");
    if (!m) return 2;
    fprintf(m, "{\"exe\":\"/perf/fixture.arm\","
               "\"slot\":\"0x%08x\","
               "\"pt\":\"0x%08x\","
               "\"key_entry\":\"0x%08x\","
               "\"key_orig\":\"0x%08x\"}\n",
            (unsigned)(size_t)&select_slot, (unsigned)(size_t)mock_power_timer,
            (unsigned)(size_t)&key_table[12], (unsigned)(size_t)orig_key_handler);
    fclose(m);
    int fd = open("/perf/fixture-ready", O_WRONLY|O_CREAT|O_TRUNC, 0600);
    if (fd >= 0) { write(fd, "ok", 2); close(fd); }

    /* Record the pre-install slot values so restoration can be compared
     * against the fixture's own bookkeeping, not just "some non-zero". */
    fd = open("/perf/fixture-orig", O_WRONLY|O_CREAT|O_TRUNC, 0600);
    if (fd >= 0) {
        char b[64];
        int n = snprintf(b, sizeof b, "select=%08x key=%08x\n",
                         (unsigned)(size_t)orig_select, (unsigned)(size_t)orig_key_handler);
        write(fd, b, (size_t)n); close(fd);
    }

    /* Does execve work from this process at all? A normal glibc program
     * performing the same call separates a guest restriction from a defect
     * in the blob's own syscall path. */
    {
        pid_t p = fork();
        if (p == 0) {
            char *av[] = {"sh", "-c", "echo fixture-exec-ok > /perf/fixture-exec", 0};
            char *ev[] = {"HOME=/root", "PATH=/bin:/usr/bin", 0};
            execve("/bin/sh", av, ev);
            int e = errno;
            int f2 = open("/perf/fixture-exec-diag", O_WRONLY|O_CREAT|O_TRUNC, 0600);
            if (f2 >= 0) { char b[24]; int n = snprintf(b, sizeof b, "errno=%d\n", e);
                write(f2, b, (size_t)n); close(f2); }
            _exit(9);
        }
    }
    /* Same call through raw svc from this process: separates "the guest
     * refuses this ABI" from "the injected blob builds the call wrongly". */
    {
        pid_t p = fork();
        if (p == 0) {
            char *av[] = {"sh", "-c", "echo raw-svc-ok > /perf/fixture-raw-svc", 0};
            char *ev[] = {"HOME=/root", "PATH=/bin:/usr/bin", 0};
            int r11 = raw_svc3(11, (unsigned)(size_t)"/bin/sh",
                               (unsigned)(size_t)av, (unsigned)(size_t)ev);
            int r387 = raw_svc4(387, (unsigned)(size_t)"/bin/sh",
                                (unsigned)(size_t)av, (unsigned)(size_t)ev, 0);
            int f2 = open("/perf/fixture-raw-diag", O_WRONLY|O_CREAT|O_TRUNC, 0600);
            if (f2 >= 0) { char b[48]; int n = snprintf(b, sizeof b, "r11=%d r387=%d\n", r11, r387);
                write(f2, b, (size_t)n); close(f2); }
            _exit(8);
        }
    }

    int results_ok = 1;
    for (unsigned i = 0; i < 1200; i++) {
        /* Phase A holds until the probe asks for phase B. */
        if (access("/perf/step-block", F_OK) == 0) {
            unlink("/perf/step-block");
            block_mode = 1;
            orig_select_calls = 60; /* make the next call the recorded one */
        }
        /* Call through the slot variable, as Ecore's wait does. In phase A the
         * caller passes 5 s; in phase B it passes NULL like a real blocking
         * Ecore wait. */
        int tv[2] = {5, 0};
        int r = select_slot ? select_slot(0, 0, 0, 0, block_mode ? 0 : tv) : -1;
        if (r != 3) results_ok = 0;
        if (access("/perf/step-press", F_OK) == 0) {
            unsigned ev[3] = {12, 0, 1};
            unlink("/perf/step-press");
            ((void (*)(void *))key_table[12])((void *)ev);
        }
        if (access("/perf/step-release", F_OK) == 0) {
            unsigned ev[3] = {12, 0, 2};
            unlink("/perf/step-release");
            ((void (*)(void *))key_table[12])((void *)ev);
        }
        if (access("/perf/step-press-disabled", F_OK) == 0) {
            unsigned ev[3] = {12, 0, 1};
            unlink("/perf/step-press-disabled");
            ((void (*)(void *))key_table[12])((void *)ev);
        }
        if (access("/perf/step-stop", F_OK) == 0) break;
        usleep(20000);
    }
    fd = open(results_ok ? "/perf/abi-ok" : "/perf/abi-bad", O_WRONLY|O_CREAT|O_TRUNC, 0600);
    if (fd >= 0) { write(fd, "x", 1); close(fd); }
    fd = open("/perf/done", O_WRONLY|O_CREAT|O_TRUNC, 0600);
    if (fd >= 0) { write(fd, "ok", 2); close(fd); }
    return 0;
}
'''

PROBE = r'''#!/bin/sh
set -u
mkdir -p /perf /var/run
/perf/fixture.arm > /perf/fixture.stdout 2>&1 &
fixture_pid=$!
n=0
while [ ! -f /perf/fixture-ready ]; do
    kill -0 "$fixture_pid" 2>/dev/null || { echo FIXTURE_DIED; cat /perf/fixture.stdout; exit 30; }
    n=$((n+1)); [ "$n" -lt 100 ] || { echo FIXTURE_TIMEOUT; exit 30; }
    /rescue/busybox usleep 100000
done
# Publish the slot values before any patch so restoration can be compared.
/rescue/busybox dd if=/proc/$fixture_pid/mem bs=1 skip=$((0)) count=0 2>/dev/null || true

export NX_SETTINGS_RUNTIME_FIXTURE=1
/perf/settings_runtime.arm 1 > /perf/install.log 2>&1
install_rc=$?
echo "INSTALL_RC=$install_rc"
if [ "$install_rc" != 0 ]; then
    echo "--- install.log ---"; cat /perf/install.log
    echo "--- fixture.stdout ---"; cat /perf/fixture.stdout
    exit 31
fi
# Second install must be idempotent, not a second mapping.
/perf/settings_runtime.arm 1 > /perf/reinstall.log 2>&1
echo "REINSTALL_RC=$?"

# Failure recovery: remove first so the next install is fresh, then inject a
# fault after the key slot was written. The loader must roll the key slot back,
# leave the select slot untouched, and never print a success line.
/perf/settings_runtime.arm 0 > /perf/prefault-remove.log 2>&1
NX_SETTINGS_RUNTIME_FAIL_SELECT=1 /perf/settings_runtime.arm 1 > /perf/fault.log 2>&1
echo "FAULT_RC=$?"
/rescue/busybox cp /proc/$fixture_pid/maps /perf/maps-after-fault
/perf/settings_runtime.arm 1 > /perf/reinstall.log 2>&1
echo "REINSTALL_RC=$?"

printf 'pt 1\n' > /var/run/nx300m-settings-request
/rescue/busybox usleep 400000
# Same content again must not renew the deadline (B5).
printf 'pt 1\n' > /var/run/nx300m-settings-request
/rescue/busybox usleep 400000
# Blocked-wakeup evidence: with the watchdog armed, switch the fixture to a
# NULL (indefinite) select wait. The wrapper must replace it with a bounded
# timeout so a blocked wait can wake and expire the watchdog.
printf 'pt 1\n' > /var/run/nx300m-settings-request
/rescue/busybox usleep 400000
: > /perf/step-block
/rescue/busybox usleep 800000
printf 'pt 0\n' > /var/run/nx300m-settings-request
/rescue/busybox usleep 400000

# Key test: press while enabled must launch once; release must not.
: > /perf/step-press
/rescue/busybox usleep 600000
: > /perf/step-release
/rescue/busybox usleep 600000
# Disabled: a press must not launch.
printf 'ck 0\n' > /var/run/nx300m-settings-request
/rescue/busybox usleep 400000
: > /perf/step-press-disabled
/rescue/busybox usleep 600000

/rescue/busybox cp /proc/$fixture_pid/maps /perf/maps-installed

/perf/settings_runtime.arm 0 > /perf/remove.log 2>&1
remove_rc=$?
echo "REMOVE_RC=$remove_rc"
[ "$remove_rc" = 0 ] || { echo "--- remove.log ---"; cat /perf/remove.log; exit 32; }

/rescue/busybox cp /proc/$fixture_pid/maps /perf/maps-restored
rm -f /var/run/nx300m-settings-request
n=0
while [ ! -f /perf/done ]; do
    kill -0 "$fixture_pid" 2>/dev/null || break
    n=$((n+1)); [ "$n" -lt 200 ] || { echo LOOP_TIMEOUT; exit 33; }
    /rescue/busybox usleep 100000
done
[ -f /perf/done ] || { echo LOOP_NOT_DONE; exit 34; }
echo SETTINGS_RUNTIME_VM_PASSED
'''

# Guest helper: read a 32-bit word from the fixture via /proc/pid/mem. The
# loader is not running here, so this uses the busybox shell only.
WORD_PROBE = r'''#!/bin/sh
# usage: word.sh PID ADDR  -> prints 8 hex digits
pid=$1; addr=$2
if [ -r /proc/$pid/mem ]; then
    /rescue/busybox dd if=/proc/$pid/mem bs=1 skip=$((addr)) count=4 2>/dev/null \
        | /rescue/busybox od -An -tx4 | /rescue/busybox tr -d ' \n'
fi
'''


def build_fixture(out):
    from wsl_paths import wsl_path
    (out / 'fixture.c').write_text(FIXTURE_C, encoding='utf-8', newline='\n')
    script = f'''set -eu
tc=/opt/nx300m-toolchain/usr
rf=/opt/nx300m-lab/stock-1.15
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=gnu99 -O2 -Wall -Wextra -Werror -march=armv7-a -mfloat-abi=soft \\
 -fno-pic -no-pie -fno-stack-protector -nostdlib -nostartfiles \\
 -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 \\
 -Wl,--allow-shlib-undefined -Wl,-rpath-link,"$rf/usr/lib" -Wl,-rpath-link,"$rf/lib" \\
 {shlex.quote(wsl_path(out / 'fixture.c'))} \\
 {shlex.quote(wsl_path(ROOT / 'work/reference/metol/test_server/filmlab/src/crt0_arm.S'))} \\
 "$rf/lib/libc-2.13.so" -lgcc -o {shlex.quote(wsl_path(out / 'fixture.arm'))}
'''
    result = subprocess.run(['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'sh', '-s'],
                            input=script.encode(), capture_output=True)
    (out / 'fixture-build.log').write_bytes(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError('Fixture build exit %d: %s' %
                           (result.returncode, result.stderr.decode(errors='replace')[-800:]))


def call(action, command='', out_dir=None):
    from wsl_paths import wsl_path
    out_dir = out_dir or OUT
    result = subprocess.run(['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'python3',
                             wsl_path(Path(__file__)), '--linux',
                             '--output-dir', wsl_path(out_dir), action,
                             base64.b64encode(command.encode()).decode()],
                            capture_output=True, text=True, encoding='utf-8',
                            errors='replace', timeout=360)
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    if not result.stdout.strip():
        raise RuntimeError('Empty worker stdout for ' + action)
    return json.loads(result.stdout)


def collect(out, name):
    """Read one guest file through THIS probe's private socket.

    The menu module's collect() reuses its own SOCKET module global and would
    address a different VM channel, which is exactly the B6 defect. A missing
    guest file is an error, never an empty artifact.
    """
    result = call('exec',
                  'printf "DATA_START\\n"; if [ -f /perf/' + name +
                  ' ]; then /rescue/busybox base64 /perf/' + name +
                  '; fi; printf "DATA_END\\n"', out_dir=out)
    match = re.search(r'\r?\nDATA_START\r?\n([A-Za-z0-9+/=\r\n]*)DATA_END', result['output'])
    if not match:
        raise RuntimeError('Cannot collect ' + name)
    body = base64.b64decode(match[1])
    if not body and name not in OPTIONAL_EMPTY:
        raise RuntimeError('Guest file /perf/' + name + ' is missing or empty')
    (out / name).write_bytes(body)


OPTIONAL_EMPTY = {'custom-launch', 'abi-bad', 'launch-attempt', 'launch-parent',
                  'launch-exec-fail', 'launch-diag', 'fixture-exec', 'fixture-exec-diag', 'fixture-raw-svc', 'fixture-raw-diag', 'fixture-orig'}


def build_overlay(out):
    """Menu module's init base + our perf payload, private socket."""
    vm.OUT = out
    vm.EXTRA_FILES = {'perf/probe.sh': PROBE.encode(),
                      'perf/word.sh': WORD_PROBE.encode()}
    # vm.prepare() builds close.arm and assembles the initramfs from the base.
    vm.prepare()
    entries = dict(arm_vm_read_cpio(out / 'overlay.cpio.gz'))
    build = json.loads((ROOT / 'outputs/controls/settings-runtime/build-report.json').read_text())
    extra = {
        'perf/settings_runtime.arm': Path(build['artifact']).read_bytes(),
        'perf/fixture.arm': (out / 'fixture.arm').read_bytes(),
    }
    entries.update({k: (0o100755, v) for k, v in extra.items()})
    overlay = io.BytesIO()
    for name, (mode, data) in entries.items():
        if name != 'TRAILER!!!':
            arm_vm.cpio_add(overlay, name, mode, data)
    arm_vm.cpio_add(overlay, 'TRAILER!!!', 0)
    compressed = io.BytesIO()
    compressed.write(__import__('gzip').compress(overlay.getvalue(), mtime=0))
    (out / 'overlay.cpio.gz').write_bytes(compressed.getvalue())
    with (arm_vm.WORK / 'initramfs.cpio.gz').open('rb') as source, \
            (out / 'initramfs.cpio.gz').open('wb') as target:
        target.write(source.read())
        target.write(compressed.getvalue())


def arm_vm_read_cpio(path):
    import gzip
    from build_stock_app_loader import read_cpio
    return read_cpio(gzip.decompress(path.read_bytes()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--linux', action='store_true')
    parser.add_argument('--output-dir', type=Path, default=OUT)
    parser.add_argument('action', nargs='?', default='run')
    parser.add_argument('payload', nargs='?', default='')
    args = parser.parse_args()

    if args.linux:
        vm.OUT = args.output_dir.resolve()
        vm.SOCKET = SOCKET
        arm_vm.SERIAL = SOCKET + '-serial.sock'
        arm_vm.QMP = SOCKET + '-qmp.sock'
        print(json.dumps(vm.linux_worker(args.action, args.payload)))
        return

    out = args.output_dir.resolve()
    if (out / 'report.json').exists():
        raise RuntimeError('Preserve earlier evidence; choose a new directory')
    out.mkdir(parents=True, exist_ok=True)
    build_fixture(out)
    build_overlay(out)
    build = json.loads((ROOT / 'outputs/controls/settings-runtime/build-report.json').read_text())

    vm.OUT = out
    vm.SOCKET = SOCKET
    arm_vm.SERIAL = SOCKET + '-serial.sock'
    arm_vm.QMP = SOCKET + '-qmp.sock'

    report = {
        'passed': False,
        'physical_camera_verified': False,
        'camera_program_running': False,
        'scope': 'Isolated ARM VM rev2: fixture process with a modifiable select-slot '
                 'variable; loader fixture mode; bootstrap, ABI, watchdog, key, restore',
        'loader_sha256': hashlib.sha256(Path(build['artifact']).read_bytes()).hexdigest(),
        'blob_sha256': hashlib.sha256(
            (ROOT / 'outputs/controls/settings-runtime/settings_runtime_blob.bin')
            .read_bytes()).hexdigest(),
        'fixture_sha256': hashlib.sha256((out / 'fixture.arm').read_bytes()).hexdigest(),
    }
    started = False
    try:
        call('start', out_dir=out)
        started = True
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            log = out / 'serial.log'
            if log.exists() and 'MENU_PERF_VM_READY' in log.read_text(errors='replace'):
                break
            time.sleep(0.5)
        else:
            raise TimeoutError('ARM boot timeout')
        execution = call('exec', '/bin/sh /perf/probe.sh', out_dir=out)
        arm_vm.write_json(out / 'execution.json', execution)
        uncollected = []
        for name in ('install.log', 'reinstall.log', 'remove.log', 'fixture.stdout',
                     'fixture-50-selects', 'pt-calls', 'orig-key-calls', 'custom-launch',
                     'done', 'abi-ok', 'abi-bad', 'maps-installed', 'maps-restored',
                     'fixture-ready', 'launch-attempt', 'launch-parent', 'launch-exec-fail', 'launch-diag', 'fixture-exec', 'fixture-exec-diag', 'fixture-raw-svc', 'fixture-raw-diag', 'fixture-orig', 'select-timeouts', 'fault.log', 'prefault-remove.log'):
            try:
                collect(out, name)
            except RuntimeError:
                uncollected.append(name)
        if uncollected:
            (out / 'uncollected.json').write_text(json.dumps(uncollected), encoding='utf-8')
        text = execution.get('output', '')
        if execution.get('exit_code') or 'SETTINGS_RUNTIME_VM_PASSED' not in text:
            raise RuntimeError('Probe did not reach its success marker; see execution.json')

        def read(name):
            path = out / name
            return path.read_text(errors='replace') if path.exists() else ''

        pt_calls = read('pt-calls').split()
        inst = read('install.log')
        report.update(
            fixture_alive=(out / 'fixture-ready').exists(),
            bootstrap_on_natural_thread=bool(re.search(
                r'^\d+ 1 1( region=[0-9a-f]+ key=[0-9a-f]+)?$', inst, re.M)),
            passthrough_preserved=read('fixture-50-selects').strip() == 'ok',
            select_result_preserved=(out / 'abi-ok').exists() and (out / 'abi-bad').stat().st_size == 0,
            power_timer_both_directions=('1' in pt_calls and '0' in pt_calls),
            power_timer_no_renewal_on_same_request=pt_calls.count('1') == 1,
            custom_key_launch_once=read('custom-launch').count('ok') == 1,
            original_key_handler_preserved=read('orig-key-calls').count('k') == 3,
            reinstall_idempotent='REINSTALL_RC=0' in text,
            remove_succeeded='REMOVE_RC=0' in text,
            slots_restored_readback=(
                bool(re.search(r'^restored select=([0-9a-f]{8}) key=([0-9a-f]{8})$',
                               read('remove.log'), re.M))
                and read('remove.log').split('restored ')[-1].split('\n')[0].strip()
                    == read('fixture-orig').strip()),
            blocked_wait_clamped=bool(re.search(r'^tv \d+$', read('select-timeouts'), re.M)),
            failed_install_rolled_back=(
                'FAULT_RC=' in text and 'FAULT_RC=0' not in text
                and 'Injected fault before select-slot patch.' in read('fault.log')
                and not re.search(r'^\d+ 1 1', read('fault.log'), re.M)),
            loop_survived=read('done').strip() == 'ok',
        )
        # Scope flags are not acceptance checks; including them made `passed`
        # unreachable because physical_camera_verified is false by design.
        checks = {k: v for k, v in report.items()
                  if isinstance(v, bool)
                  and k not in ('passed', 'physical_camera_verified',
                                'camera_program_running')}
        report['checks'] = checks
        report['passed'] = all(checks.values())
    except BaseException as error:
        report['failure'] = str(error)
        (out / 'failure.txt').write_text(str(error), encoding='utf-8')
        raise
    finally:
        if started:
            try:
                report['stop'] = call('stop', out_dir=out)
                if 'return' in report['stop']:
                    (out / 'initramfs.cpio.gz').unlink(missing_ok=True)
            except BaseException as stop_error:
                report['stop_failure'] = str(stop_error)
        arm_vm.write_json(out / 'report.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
