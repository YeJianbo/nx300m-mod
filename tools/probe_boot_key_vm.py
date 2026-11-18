"""Boot-order tail of the Custom-key shortcut: window present -> shortcut ready.

The stock camera window appears after the mod's service entry has already
started the supervisor. Each measurement: the supervisor runs with no window,
the fixture camera process exists but withholds its window, and the window is
created at a swept offset after a boot retry so the phase inside the helper
cycle is not always the same. Recorded per sample:

  window_ms   the window's own creation timestamp (guest uptime ms)
  ready_ms    first time the status endpoint reports state=ready
  w2r_ms      ready_ms - window_ms, the interval the user waits

Two or more remote builds can be compared in one guest with the same fixture,
same X server and same window offsets, so the only difference is the retry
policy compiled into the binary. Nothing is deployed: this is the ARM fixture.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import time

import arm_vm
import probe_menu_startup_vm as vm
from probe_lut_selection_vm import TAP_DRIVER
from probe_menu_entry_vm import collect
from wsl_paths import wsl_path

ROOT = Path(__file__).resolve().parents[1]

# Offsets swept after the observed retry count, so a sample can land anywhere
# in the helper's 1.5 s window wait or in the gap after it exits.
OFFSETS_MS = (0, 300, 700, 1100, 1500, 1900)

CAMERA_SETUP = r'''
        void *code=mmap((void *)0x80000,0x150000,PROT_READ|PROT_WRITE,
                       MAP_PRIVATE|MAP_ANONYMOUS|MAP_FIXED,-1,0);
        void *data=mmap((void *)0x4c0000,0x30000,PROT_READ|PROT_WRITE,
                        MAP_PRIVATE|MAP_ANONYMOUS|MAP_FIXED,-1,0);
        if(code==MAP_FAILED || data==MAP_FAILED)return 30;
        for(unsigned i=0;i<sizeof custom_key_stock/sizeof custom_key_stock[0];i++)
            memcpy((void *)(uintptr_t)custom_key_stock[i].address,custom_key_stock[i].bytes,custom_key_stock[i].size);
        *(volatile unsigned *)0x4c9c5c=0x24c0b8;
        *(volatile unsigned *)0x4c9f04=0x24c838;
        *(volatile unsigned *)0x4c9c64=0x4c9f04;
        *(volatile unsigned *)0x4de4d8=0;
'''

CAMERA_LOOP = r'''
        for(;;) {
            *(volatile unsigned *)0x4c9c64=0x4c9f04;
            *(volatile unsigned *)0x4de4d8=0;
            while(XPending(d)){XEvent e;XNextEvent(d,&e);}
            usleep(20000);
        }
'''

PROBE = r'''#!/bin/sh
set -eu
export DISPLAY=:97
now_ms() { awk '{printf "%d", $1*1000}' /proc/uptime; }
attempt=0
until [ -S /tmp/.X11-unix/X97 ]; do
    attempt=$((attempt+1)); if [ "$attempt" -ge 60 ]; then echo 'X server wait timed out' >&2; exit 1; fi; sleep 1
done
/perf/close.arm ping
export NX300M_MOD_ROOT=/perf/mod NX_STATE_DIR=/perf/state
mkdir -p /perf/state /perf/mod/.state
: >/perf/fn-input
custom_state() { wget -T 2 -Y off -q -O- http://127.0.0.1:8781/api/v1/camera/info 2>/dev/null | sed -n 's/.*"custom_key":{"enabled":[a-z]*,"mode":"async","state":"\([a-z-]*\)".*/\1/p' | head -1; }
status_field() { wget -T 2 -Y off -q -O- http://127.0.0.1:8781/api/v1/camera/info 2>/dev/null | sed -n "s/.*\"$1\":\([0-9a-z_-]*\).*/\1/p" | head -1; }
# sample <label> <remote-binary> <offset-ms> <sample-no>
sample() {
    label=$1; binary=$2; offset=$3; index=$4
    rm -f /perf/create-window /perf/stock-parent.ready /perf/stock-parent.ready-ms
    rm -f /perf/stock-parent.alive
    /usr/bin/di-camera-app-nx300 stock-parent > /perf/stock-$index.log 2>&1 &
    stock=$!
    n=0; until [ -s /perf/stock-parent.alive ]; do
        n=$((n+1)); if [ "$n" -ge 60 ]; then echo "sample $index fixture-alive wait timed out" >&2; return 1; fi
        /rescue/busybox usleep 100000
    done
    NX_REMOTE_CUSTOM_KEY=1 NX_REMOTE_PANEL_SHORTCUT=1 NX_REMOTE_PANEL_KEY=custom NX_REMOTE_MENU_KEY=0 \
        NX_FN_KEY_LOG=/perf/fn-key.log "$binary" --listen 127.0.0.1:8781 --web-root /perf/mod --media-root /perf/photos \
        > /perf/remote-$index.log 2>&1 &
    remote=$!
    # The X11 custom-key service is the one this probe measures. Its readiness
    # is reported through the status endpoint, so the probe waits for the API
    # rather than for the evdev menu-key helper's own ready line (that helper
    # needs the fixture's patched device path and is not part of this path).
    n=0; until [ -n "$(custom_state)" ]; do
        n=$((n+1)); if [ "$n" -ge 100 ]; then echo "sample $index API wait timed out" >&2; return 1; fi
        /rescue/busybox usleep 100000
    done
    # Let three boot retries accumulate, then sweep the phase inside the cycle.
    n=0; until [ "$(status_field boot_retries)" -ge 3 ] 2>/dev/null; do
        n=$((n+1)); if [ "$n" -ge 400 ]; then echo "sample $index retry wait timed out" >&2; return 1; fi
        /rescue/busybox usleep 100000
    done
    before=$(status_field boot_retries)
    if [ "$offset" -gt 0 ]; then /rescue/busybox usleep $((offset*1000)); fi
    touch /perf/create-window
    n=0; until [ -s /perf/stock-parent.ready ]; do
        n=$((n+1)); if [ "$n" -ge 60 ]; then echo "sample $index window wait timed out" >&2; return 1; fi
        /rescue/busybox usleep 100000
    done
    window_ms=$(cat /perf/stock-parent.ready-ms 2>/dev/null || echo 0)
    n=0; ready_ms=0
    while [ "$n" -lt 200 ]; do
        if [ "$(custom_state)" = ready ]; then ready_ms=$(now_ms); break; fi
        n=$((n+1)); /rescue/busybox usleep 20000
    done
    after=$(status_field boot_retries)
    printf '%s sample=%s offset=%s window_ms=%s ready_ms=%s w2r_ms=%s retries_before=%s retries_after=%s\n' \
        "$label" "$index" "$offset" "$window_ms" "$ready_ms" "$((ready_ms-window_ms))" "$before" "$after" \
        >> /perf/boot-key-samples.txt
    kill "$remote" 2>/dev/null || :
    wait "$remote" 2>/dev/null || :
    # Boot-phase evidence for this sample: the retry lines and the window wait.
    { printf '--- sample %s ---\n' "$index"; grep -E 'NX_CUSTOM_(RETRY|BOOT)' /perf/remote-$index.log 2>/dev/null || :; } \
        >> /perf/boot-key-helper.log
    kill "$stock" 2>/dev/null || :
    wait "$stock" 2>/dev/null || :
    rm -f /perf/create-window
    /rescue/busybox usleep 400000
}
sample @SAMPLE_1@
sample @SAMPLE_2@
sample @SAMPLE_3@
sample @SAMPLE_4@
: > /perf/boot-key-leftovers.txt
/rescue/busybox ps | grep -c 'remote-' >> /perf/boot-key-leftovers.txt || :
'''


def prepare_driver():
    driver = vm.DRIVER.replace(
        '#include <stdio.h>',
        '#include <stdio.h>\n#include <stdint.h>\n#include <sys/mman.h>\n#include <unistd.h>\n#include "'
        + wsl_path(ROOT / 'mod/metol/remote/custom_key_context.h') + '"')
    driver = driver.replace('Window stock=XCreateSimpleWindow', CAMERA_SETUP + '\n'
        '        {FILE *alive=fopen("/perf/stock-parent.alive","w");if(alive){fprintf(alive,"%ld\\n",(long)getpid());fclose(alive);}}\n'
        '        {int winwait=0;while(access("/perf/create-window",F_OK)&&winwait<1200){usleep(100000);winwait++;}}\n'
        '        Window stock=XCreateSimpleWindow')
    driver = driver.replace('for(;;){XEvent event;XNextEvent(d,&event);}', CAMERA_LOOP)
    driver = driver.replace('    if(argc==2) {',
                            TAP_DRIVER + '\n    if(argc==2) {', 1)
    driver = driver.replace('        fprintf(ready,"%lu\\n",stock);fclose(ready);',
        '        fprintf(ready,"%lu\\n",stock);fclose(ready);\n'
        '        {FILE *stamp=fopen("/perf/stock-parent.ready-ms","w");\n'
        '         if(stamp){FILE *up=fopen("/proc/uptime","r");char line[64]={0};\n'
        '           if(up){if(fgets(line,sizeof line,up)){double s=atof(line);\n'
        '             fprintf(stamp,"%d\\n",(int)(s*1000));}fclose(up);}fclose(stamp);}}')
    vm.DRIVER = driver


def analyze(text, offsets):
    samples = []
    for m in re.finditer(r'(\S+) sample=(\d+) offset=(\d+) window_ms=(\d+) ready_ms=(\d+) w2r_ms=(-?\d+) '
                         r'retries_before=(\S*) retries_after=(\S*)', text):
        samples.append(dict(label=m[1], sample=int(m[2]), offset_ms=int(m[3]),
                            window_ms=int(m[4]), ready_ms=int(m[5]), w2r_ms=int(m[6]),
                            retries_before=m[7], retries_after=m[8]))
    summary = {}
    for label in sorted({s['label'] for s in samples}):
        values = sorted(s['w2r_ms'] for s in samples if s['label'] == label and s['window_ms'] > 0)
        summary[label] = dict(
            samples=len(values), w2r_ms=values,
            worst_ms=values[-1] if values else None,
            median_ms=values[len(values) // 2] if values else None,
            best_ms=values[0] if values else None,
        )
    return samples, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--variant', action='append', default=[], metavar='LABEL=PATH',
                        help='remote binary to measure; repeat for several variants')
    parser.add_argument('--repeats', type=int, default=len(OFFSETS_MS))
    parser.add_argument('--shell-trace', action='store_true',
                        help='run the guest probe with sh -x so a failed guard leaves its line in serial.log')
    args = parser.parse_args()
    out = args.output_dir.resolve()
    if (out / 'report.json').exists():
        raise RuntimeError('Preserve earlier evidence; choose a new output directory')
    variants = []
    for item in args.variant:
        label, _, path = item.partition('=')
        if not label or not path or not re.fullmatch(r'[a-z0-9-]+', label):
            raise SystemExit('--variant needs LABEL=PATH with a [a-z0-9-] label')
        path = Path(path)
        if not path.exists():
            raise SystemExit(f'missing remote binary {path}')
        variants.append((label, path))
    if not variants:
        raise SystemExit('at least one --variant is required')
    offsets = OFFSETS_MS[:max(1, args.repeats)]
    calls = [f'{label} /perf/remote-{label}.arm {offset} {index}'
             for index, offset in enumerate(offsets) for label, _ in variants]
    offsets_for_call = [off for off in offsets for _ in variants]
    probe = PROBE
    for index, call in enumerate(calls, start=1):
        probe = probe.replace(f'@SAMPLE_{index}@', call)
    # More samples than placeholder slots: extend the script textually.
    if len(calls) > 4:
        probe = probe.replace(': > /perf/boot-key-leftovers.txt',
                              '\n'.join(f'sample {c}' for c in calls[4:]) + '\n: > /perf/boot-key-leftovers.txt')
    vm.OUT = out
    vm.PROBE = probe
    prepare_driver()
    vm.EXTRA_FILES = {
        'perf/mod/looks.txt': b'__off\n',
        'perf/photos/.keep': b'',
    }
    binaries = {}
    for label, path in variants:
        data = path.read_bytes()
        binaries[label] = data
        vm.EXTRA_FILES[f'perf/remote-{label}.arm'] = data
    original_cpio_add = arm_vm.cpio_add

    def add(stream, name, mode, data=b''):
        original_cpio_add(stream, name, mode, data)
        if name == 'perf/close.arm':
            original_cpio_add(stream, 'usr/bin/di-camera-app-nx300', 0o100755, data)

    arm_vm.cpio_add = add
    vm.prepare()
    report = dict(passed=False, hardware=False,
                  scope='ARM fixture: stock window withheld, then created at a swept offset; '
                        'window present -> Custom-key shortcut ready',
                  binaries={label: hashlib.sha256(data).hexdigest() for label, data in binaries.items()},
                  offsets_ms=offsets)
    started = False
    try:
        vm.call('start'); started = True
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if (out / 'serial.log').exists() and 'MENU_PERF_VM_READY' in (out / 'serial.log').read_text(errors='replace'):
                break
            time.sleep(.5)
        else:
            raise TimeoutError('boot-key VM startup')
        execution = vm.call('exec', ('/bin/sh -x /perf/probe.sh' if args.shell_trace else '/bin/sh /perf/probe.sh'))
        arm_vm.write_json(out / 'execution.json', execution)
        present = vm.call('exec', '[ -s /perf/boot-key-samples.txt ] && echo SAMPLES_PRESENT || echo SAMPLES_MISSING')
        report['probe_exit_code'] = execution['exit_code']
        report['samples_file'] = 'present' if 'SAMPLES_PRESENT' in present['output'] else 'missing'
        if report['samples_file'] == 'missing':
            report['probe_output_tail'] = execution['output'][-2000:]
            raise RuntimeError('guest probe produced no samples; guest exit ' + str(execution['exit_code']))
        collect(out, 'boot-key-samples.txt')
        try:
            collect(out, 'boot-key-helper.log')
        except RuntimeError:
            pass
        # The guest indexes samples and logs from 0, so every call's log is
        # remote-0.log .. remote-N-1.log; a missing one is itself a defect and
        # is judged below instead of silently skipped.
        missing_logs = []
        for index in range(len(calls)):
            try:
                collect(out, f'remote-{index}.log')
            except RuntimeError:
                missing_logs.append(f'remote-{index}.log')
            try:
                collect(out, f'stock-{index}.log')
            except RuntimeError:
                missing_logs.append(f'stock-{index}.log')
        try:
            collect(out, 'boot-key-leftovers.txt')
        except RuntimeError:
            missing_logs.append('boot-key-leftovers.txt')
        text = (out / 'boot-key-samples.txt').read_text(errors='replace') if (out / 'boot-key-samples.txt').exists() else ''
        (out / 'boot-key-raw.txt').write_text(text, encoding='utf-8')
        samples, summary = analyze(text, offsets)
        report['samples'] = samples
        report['summary'] = summary
        # Every variant x offset must appear exactly once and be fully observed:
        # a window or ready stamp of 0 means a fixture wait capped out, and a
        # negative w2r means the timestamps are inconsistent. A non-zero guest
        # exit is a failure by itself (boot-key-current-r1 let one slip through
        # because the old check only required >=1 sample per variant).
        expected = {(label, offset) for label, _ in variants for offset in offsets}
        seen = [(s['label'], s['offset_ms']) for s in samples]
        invalid = [dict(sample=s['sample'], offset=s['offset_ms'])
                   for s in samples if s['window_ms'] <= 0 or s['ready_ms'] <= 0 or s['w2r_ms'] < 0
                   or s['w2r_ms'] != s['ready_ms'] - s['window_ms']]
        report['coverage'] = dict(
            expected_samples=len(expected), recorded_samples=len(seen),
            missing=[list(pair) for pair in sorted(expected - set(seen))],
            duplicates=sorted(pair for pair in set(seen) if seen.count(pair) > 1),
            invalid=invalid, missing_guest_logs=missing_logs,
            guest_exit_code=execution['exit_code'])
        report['passed'] = (execution['exit_code'] == 0 and len(samples) == len(expected)
                            and set(seen) == expected and len(set(seen)) == len(seen)
                            and not invalid and not missing_logs)
        if execution['exit_code']:
            report['probe_output_tail'] = execution['output'][-2000:]
    except BaseException as error:
        report['failure'] = str(error)
        raise
    finally:
        arm_vm.cpio_add = original_cpio_add
        if started:
            report['stop'] = vm.call('stop')
            report['private_vm_stopped'] = 'return' in report['stop']
            if report['private_vm_stopped']:
                (out / 'initramfs.cpio.gz').unlink(missing_ok=True)
        arm_vm.write_json(out / 'report.json', report)
    print(json.dumps({k: report[k] for k in ('passed', 'summary')}, ensure_ascii=False, indent=2))
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
