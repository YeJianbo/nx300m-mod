"""Exercise live RAM loading in a private full-system ARM Linux VM.

The target is explicitly named lut_live_fixture.arm. Original IQ/UDD libraries
execute against synthetic MMIO; this checks real ptrace and ARM machine code,
not a physical sensor, original camera lifecycle, or rendered image.
"""
import argparse
import base64
import io
import json
from pathlib import Path
import re
import shlex
import shutil
import struct
import subprocess
import tarfile
import time

import arm_vm
import probe_mfw_ram_vm as vm

PROJECT = Path(__file__).resolve().parents[1]
OUT = PROJECT / 'outputs/deployment/lut-live-loader/arm-vm'
SOCKET = '/tmp/nx300m-lut-live-private'
LOOK = 'ricoh-negative-exact'
INIT = r'''#!/rescue/busybox sh
export PATH=/bin:/sbin:/usr/bin:/usr/sbin
/rescue/busybox mount -t devtmpfs devtmpfs /dev
exec </dev/ttyAMA0 >/dev/ttyAMA0 2>&1
/rescue/busybox mount -t proc proc /proc
/rescue/busybox mount -t sysfs sysfs /sys
/rescue/busybox mkdir -p /tmp /dev/pts /mnt/mmc /var/run /arm-vm/mfw-results
/rescue/busybox mount -t devpts devpts /dev/pts
/rescue/busybox mount -t tmpfs -o rw,exec,mode=1777,size=32m lut-runtime /tmp
/rescue/busybox mount --bind /arm-vm/mfw-source /mnt/mmc
/rescue/busybox mount -o remount,bind,ro /mnt/mmc
echo LUT_LIVE_PRIVATE_VM_SHELL_READY
while true; do /rescue/busybox setsid /bin/sh -i < /dev/ttyAMA0 > /dev/ttyAMA0 2>&1; done
'''
PROBE = r'''#!/bin/sh
set -eux
OUT=/arm-vm/mfw-results
LOADER=/mnt/mmc/bin/lut_live_loader.arm
ROOT=/mnt/mmc/nx300m-mod
finish() {
    result=$?; trap - EXIT; set +e
    if [ "$result" -ne 0 ] && [ -n "${pid:-}" ] && [ -r /proc/$pid/status ]; then
        cat /proc/$pid/status > "$OUT/failure.status"
        cat /proc/$pid/stat > "$OUT/failure.stat"
        cat /proc/$pid/maps > "$OUT/failure.maps"
        "$LOADER" status --pid "$pid" > "$OUT/status-failure.json" 2> "$OUT/status-failure.stderr"
        cp /var/run/lut-live-response "$OUT/failure.response"
    fi
    printf '%s\n' "$result" > "$OUT/probe.exit"; exit "$result"
}
trap finish EXIT
uname -a > "$OUT/uname.txt"
grep ' /mnt/mmc .* ro,' /proc/mounts > "$OUT/source-mount.txt"
grep ' /tmp ' /proc/mounts > "$OUT/ram-mount.txt"
/mnt/mmc/bin/lut_live_fixture.arm > "$OUT/fixture.stdout" 2> "$OUT/fixture.stderr" &
pid=$!
attempt=0
while [ ! -s "$OUT/fixture.ready" ]; do
    attempt=$((attempt+1)); [ "$attempt" -lt 40 ]; /rescue/busybox usleep 100000
done
read ready_pid ignored < "$OUT/fixture.ready"
[ "$ready_pid" = "$pid" ]
printf '%s\n' "$pid" > "$OUT/fixture.pid"
cat /proc/$pid/stat > "$OUT/before.stat"
cat /proc/$pid/maps > "$OUT/before.maps"
seq=0
rpc() {
    mode=$1; reset=$2; tag=$3
    seq=$((seq+1))
    printf '%s %s %s %s\n' "$seq" "$mode" "$reset" "$tag" > /var/run/lut-live-request.tmp
    mv /var/run/lut-live-request.tmp /var/run/lut-live-request
    attempt=0
    while :; do
        response=0
        [ ! -r /var/run/lut-live-response ] || read response ignored < /var/run/lut-live-response
        [ "$response" != "$seq" ] || break
        attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 100000
    done
    cp /var/run/lut-live-response "$OUT/$tag.response"
    [ ! -r /var/run/nx300m-lut-status.json ] || cp /var/run/nx300m-lut-status.json "$OUT/$tag.status.json"
    [ ! -r /var/run/nx300m-lut-trace-$pid.json ] || cp /var/run/nx300m-lut-trace-$pid.json "$OUT/$tag.trace.json"
}
generation=0
select_strength() {
    generation=$((generation+1))
    printf '%s %s %s\n' "$generation" "$ROOT/looks/ricoh-negative-exact/table.d4lut" "$1" > /var/run/nx300m-lut-selection.tmp
    mv /var/run/nx300m-lut-selection.tmp /var/run/nx300m-lut-selection
}
loader_status() {
    label=$1
    set +e
    "$LOADER" status --pid "$pid" > "$OUT/status-$label.json" 2> "$OUT/status-$label.stderr"
    result=$?
    set -e
    # A genuine fixture deliberately fails the production camera-name gate.
    [ "$result" -eq 0 ] || [ "$result" -eq 2 ]
}
restore_loader() {
    label=$1; attempt=0
    while :; do
        set +e
        "$LOADER" restore --pid "$pid" --fixture > "$OUT/$label.json" 2> "$OUT/$label.stderr"
        result=$?
        set -e
        [ "$result" -ne 0 ] || break
        [ "$result" -eq 4 ]
        attempt=$((attempt+1)); [ "$attempt" -lt 20 ]; /rescue/busybox usleep 100000
    done
}
# Reproduce a fresh camera before its first ipcs_3d_lut call. Taking the
# baseline first resolves JUMP_SLOT and hides this real startup state.
loader_status lazy-before
select_strength 100
"$LOADER" apply --pid "$pid" --blob /mnt/mmc/lut_ram_blob.bin --root "$ROOT" --fixture > "$OUT/lazy-apply.json" 2> "$OUT/lazy-apply.stderr"
rpc 1 1 lazy-composed
restore_loader lazy-restore
loader_status lazy-restored
rm -f "/var/run/nx300m-lut-live-$pid.state" /var/run/nx300m-lut-selection
if [ "$NX_LUT_FACTORY_ONLY" -eq 0 ]; then rpc 0 1 baseline-direct; fi
rpc 1 1 baseline-composed
# This unchanged Update must skip the original cached write before injection.
rpc 1 0 cached-before
loader_status before
select_strength 100
"$LOADER" apply --pid "$pid" --blob /mnt/mmc/lut_ram_blob.bin --root "$ROOT" --fixture > "$OUT/apply.json" 2> "$OUT/apply.stderr"
cat /proc/$pid/maps > "$OUT/installed.maps"
"$LOADER" apply --pid "$pid" --blob /mnt/mmc/lut_ram_blob.bin --root "$ROOT" --fixture > "$OUT/apply-idempotent.json" 2> "$OUT/apply-idempotent.stderr"
cat /proc/$pid/maps > "$OUT/idempotent.maps"
if [ -x /mnt/mmc/bin/ram_ready.arm ]; then
    /mnt/mmc/bin/ram_ready.arm "$pid" "$ROOT" > "$OUT/binding-installed.json" || [ "$?" -eq 3 ]
    cp /var/run/nx300m-lut-ram-hook "$OUT/ram-marker.original"
    read marker_pid marker_start marker_root marker_base marker_size < "$OUT/ram-marker.original"
    printf '%s %s %s %s %s\n' "$marker_pid" "$((marker_start+1))" "$marker_root" "$marker_base" "$marker_size" > /var/run/nx300m-lut-ram-hook
    set +e
    /mnt/mmc/bin/ram_ready.arm "$pid" "$ROOT" > "$OUT/binding-stale-start.json"
    binding_result=$?
    set -e
    [ "$binding_result" -eq 3 ]
    cp "$OUT/ram-marker.original" /var/run/nx300m-lut-ram-hook
fi
# The installed binding must not depend on mutable per-shot telemetry.
rm -f /var/run/nx300m-lut-status.json
/mnt/mmc/bin/ram_ready.arm "$pid" "$ROOT" > "$OUT/binding-no-telemetry.json"
# Same parameters as the pre-install cached call must now reach the adapter.
rpc 1 0 installed-same-params
/mnt/mmc/bin/ram_ready.arm "$pid" "$ROOT" > "$OUT/binding-after-shot.json"
printf '{"stage":"stock"}\n' > /var/run/nx300m-lut-status.json
/mnt/mmc/bin/ram_ready.arm "$pid" "$ROOT" > "$OUT/binding-replaced-telemetry.json"
for strength in 0 50 100; do
    select_strength "$strength"
    rpc 1 0 "composed-$strength"
done
if [ "$NX_LUT_FACTORY_ONLY" -eq 0 ]; then
    for strength in 0 50 100; do
        select_strength "$strength"
        rpc 0 0 "direct-$strength"
    done
fi
rm /var/run/nx300m-lut-selection
if [ "$NX_LUT_FACTORY_ONLY" -eq 0 ]; then rpc 0 0 stock-direct; fi
rpc 1 0 stock-composed
loader_status installed
restore_loader restore
if [ -x /mnt/mmc/bin/ram_ready.arm ]; then
    set +e
    /mnt/mmc/bin/ram_ready.arm "$pid" "$ROOT" > "$OUT/binding-restored.json"
    binding_result=$?
    set -e
    [ "$binding_result" -eq 3 ]
fi
# Original cache may remain populated after slot restoration. Change the
# genuine parameter index, rather than inventing a hardware reload event.
if [ "$NX_LUT_FACTORY_ONLY" -eq 0 ]; then
    rpc 0 0 restored-direct
    rpc 1 0 restored-composed
else
    rpc 1 1 restored-composed
fi
cat /proc/$pid/maps > "$OUT/restored.maps"
loader_status restored
# Reuse the retained allocation under the same process identity. Selection is
# still absent, so the reinstalled hook must forward the genuine stock table.
"$LOADER" apply --pid "$pid" --blob /mnt/mmc/lut_ram_blob.bin --root "$ROOT" --fixture > "$OUT/reapply.json" 2> "$OUT/reapply.stderr"
cat /proc/$pid/maps > "$OUT/reapplied.maps"
loader_status reapplied
if [ -x /mnt/mmc/bin/ram_ready.arm ]; then
    /mnt/mmc/bin/ram_ready.arm "$pid" "$ROOT" > "$OUT/binding-reapplied.json" || [ "$?" -eq 3 ]
fi
rpc 1 0 reapplied-stock-composed
restore_loader rerestore
loader_status rerestored
if [ -x /mnt/mmc/bin/ram_ready.arm ]; then
    set +e
    /mnt/mmc/bin/ram_ready.arm "$pid" "$ROOT" > "$OUT/binding-rerestored.json"
    binding_result=$?
    set -e
    [ "$binding_result" -eq 3 ]
fi
# Recreate a verified previous build with lost/corrupt runtime state. Recovery
# must reject a different image, then drain and restore only the exact image.
rm -f "/var/run/nx300m-lut-live-$pid.state"
/mnt/mmc/bin/legacy_loader.arm apply --pid "$pid" --blob /mnt/mmc/legacy_blob.bin --root "$ROOT" --fixture > "$OUT/legacy-apply.json" 2> "$OUT/legacy-apply.stderr"
loader_status legacy-before
printf 'broken state\n' > "/var/run/nx300m-lut-live-$pid.state"
set +e
"$LOADER" apply --pid "$pid" --blob /mnt/mmc/lut_ram_blob.bin --root "$ROOT" --fixture > "$OUT/legacy-refused.json" 2> "$OUT/legacy-refused.stderr"
refused=$?
"$LOADER" restore-known --pid "$pid" --blob /mnt/mmc/lut_ram_blob.bin --root "$ROOT" --fixture > "$OUT/legacy-wrong-image.json" 2> "$OUT/legacy-wrong-image.stderr"
wrong=$?
set -e
[ "$refused" -eq 3 ] && [ "$wrong" -eq 3 ]
loader_status legacy-after-refusal
"$LOADER" restore-known --pid "$pid" --blob /mnt/mmc/legacy_blob.bin --root "$ROOT" --fixture > "$OUT/legacy-restored.json" 2> "$OUT/legacy-restored.stderr"
loader_status legacy-restored
rpc 1 1 legacy-restored-composed
select_strength 100
"$LOADER" apply --pid "$pid" --blob /mnt/mmc/lut_ram_blob.bin --root "$ROOT" --fixture > "$OUT/recovered-apply.json" 2> "$OUT/recovered-apply.stderr"
rpc 1 0 recovered-composed
/mnt/mmc/bin/ram_ready.arm "$pid" "$ROOT" > "$OUT/binding-recovered.json"
restore_loader recovered-restore
cat /proc/$pid/stat > "$OUT/after.stat"
cat /proc/$pid/status > "$OUT/after.status"
ls /proc/$pid/task > "$OUT/after.tasks"
seq=$((seq+1))
printf '%s 2 0 quit\n' "$seq" > /var/run/lut-live-request.tmp
mv /var/run/lut-live-request.tmp /var/run/lut-live-request
wait "$pid"
echo LUT_LIVE_FULL_ARM_PASSED
'''


def configure(factory_only=False):
    vm.OUT = OUT
    vm.SOCKET = SOCKET
    vm.INIT = INIT
    vm.PROBE = PROBE.replace('set -eux', 'set -eux\nNX_LUT_FACTORY_ONLY=' + str(int(factory_only)), 1)


def compile_fixture():
    target = OUT / 'lut_live_fixture.arm'
    source = PROJECT / 'runtime/lut_live_fixture.c'
    script = f'''set -eu
tc=/opt/nx300m-toolchain/usr
rf=/opt/nx300m-lab/stock-1.15
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=gnu99 -O1 -Wall -Wextra -Werror -march=armv7-a -mfloat-abi=soft -fno-pic -no-pie -fno-stack-protector \\
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 \\
 -Wl,--allow-shlib-undefined -Wl,-rpath-link,"$rf/usr/lib" -Wl,-rpath-link,"$rf/lib" -L"$rf/usr/lib" \\
 {shlex.quote(arm_vm.wsl_path(source))} \\
 {shlex.quote(arm_vm.wsl_path(PROJECT / 'work/reference/metol/test_server/filmlab/src/crt0_arm.S'))} \\
 -Wl,--no-as-needed -Wl,-l:libiq-fw-slpcam.so -Wl,-l:libdiosal.so -Wl,-l:libproduction-mode.so \\
 "$rf/usr/lib/libudd.so.3.1" "$rf/lib/libpthread.so.0" "$rf/lib/libc-2.13.so" "$rf/lib/libdl.so.2" -lgcc \\
 -o {shlex.quote(arm_vm.wsl_path(target))}
'''
    result = subprocess.run(['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'sh', '-s'], input=script.encode(), capture_output=True)
    (OUT / 'fixture-build.stdout.log').write_bytes(result.stdout)
    (OUT / 'fixture-build.stderr.log').write_bytes(result.stderr)
    if result.returncode:
        raise RuntimeError(f'Fixture build exit {result.returncode}: {result.stderr.decode(errors="replace")}')
    return target


def compile_binding_helper():
    source = OUT / 'ram_ready.c'
    source.write_text('''/* Explicit fixture: validate binding without camera-name impersonation. */
#define _GNU_SOURCE
#include "mod/metol/lut_engine_shared.h"
int main(int argc,char **argv) {
    unsigned int pid;
    if (argc!=3 || !nx_lut_uint(argv[1],&pid) || !pid || pid>INT_MAX || !nx_mod_root_valid(argv[2])) return 2;
    int ready=nx_lut_ram_hook_ready((int)pid,(int)pid,argv[2]);
    printf("{\\\"binding_ready\\\":%s,\\\"camera_identity_verified\\\":false}\\n",ready?"true":"false");
    return ready?0:3;
}
''', encoding='utf-8', newline='\n')
    target = OUT / 'ram_ready.arm'
    script = f'''set -eu
tc=/opt/nx300m-toolchain/usr
rf=/opt/nx300m-lab/stock-1.15
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=gnu99 -O1 -Wall -Wextra -Werror -march=armv7-a -mfloat-abi=soft -fno-pic -no-pie -fno-stack-protector \\
 -D_TIME_BITS=32 -D_FILE_OFFSET_BITS=64 -I{shlex.quote(arm_vm.wsl_path(PROJECT))} \\
 -include {shlex.quote(arm_vm.wsl_path(PROJECT / 'mod/metol/remote/compat.h'))} \\
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 -Wl,-rpath-link,"$rf/lib" \\
 {shlex.quote(arm_vm.wsl_path(source))} \\
 {shlex.quote(arm_vm.wsl_path(PROJECT / 'work/reference/metol/test_server/filmlab/src/crt0_arm.S'))} \\
 "$rf/lib/libc-2.13.so" -lgcc -o {shlex.quote(arm_vm.wsl_path(target))}
'''
    result = subprocess.run(['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'sh', '-s'], input=script.encode(), capture_output=True)
    (OUT / 'binding-build.stdout.log').write_bytes(result.stdout)
    (OUT / 'binding-build.stderr.log').write_bytes(result.stderr)
    if result.returncode:
        raise RuntimeError(f'RAM binding helper build exit {result.returncode}: {result.stderr.decode(errors="replace")}')
    return target


def prepare():
    OUT.mkdir(parents=True, exist_ok=True)
    if not (arm_vm.WORK / 'initramfs.cpio.gz').is_file():
        raise ValueError('Cached full-stock ARM initramfs is required; shared caches are not rebuilt by this probe')
    fixture = compile_fixture()
    binding = compile_binding_helper()
    inputs = {
        'bin/lut_live_fixture.arm': fixture,
        'bin/lut_live_loader.arm': PROJECT / 'outputs/deployment/lut-live-loader/lut_live_loader.arm',
        'lut_ram_blob.bin': PROJECT / 'outputs/deployment/lut-live-loader/lut_ram_blob.bin',
        f'nx300m-mod/looks/{LOOK}/table.d4lut': PROJECT / f'looks/{LOOK}/table.d4lut',
        f'nx300m-mod/looks/{LOOK}/table-stock3d.d4lut': PROJECT / f'looks/{LOOK}/table-stock3d.d4lut',
    }
    if binding:
        inputs['bin/ram_ready.arm'] = binding
    compat = PROJECT / 'outputs/deployment/lut-live-loader/compat'
    legacy_images = sorted(compat.glob('blob-*.bin'))
    if not legacy_images:
        raise ValueError('A saved known previous image is required for migration regression checks')
    inputs['bin/legacy_loader.arm'] = compat / 'fixture-loader-before.arm'
    inputs['legacy_blob.bin'] = legacy_images[0]
    stage = OUT / 'source-files'
    if stage.exists():
        if stage.is_symlink() or stage.resolve().parent != OUT.resolve():
            raise ValueError('Unexpected private staging path')
        shutil.rmtree(stage)
    inventory = {}
    for name, source in inputs.items():
        target = stage / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        inventory[name] = target.stat().st_size
    arm_vm.write_json(OUT / 'candidate.json', {'inventory': inventory, 'look': LOOK,
        'target': 'explicit lut_live_fixture.arm; genuine stock IQ/UDD and synthetic allocated MMIO',
        'shared_cached_initramfs_modified': False})
    vm.initramfs()


def call(action, command=''):
    result = subprocess.run(['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'python3', arm_vm.wsl_path(__file__),
                             '--linux', action, base64.b64encode(command.encode()).decode()],
                            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=375)
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    return json.loads(result.stdout)


def collect():
    result = call('exec', r"set -e; /rescue/busybox dmesg -n 1; /rescue/busybox tar -czf /tmp/lut-live-evidence.tar.gz -C /arm-vm mfw-results; printf 'LUT_EVIDENCE_BEGIN\n'; /rescue/busybox base64 /tmp/lut-live-evidence.tar.gz; printf 'LUT_EVIDENCE_END\n'")
    arm_vm.write_json(OUT / 'collection.json', result)
    match = re.search(r'LUT_EVIDENCE_BEGIN\r?\n([A-Za-z0-9+/=\r\n]+)LUT_EVIDENCE_END', result['output'])
    if result['exit_code'] or not match:
        raise ValueError('Guest evidence collection failed')
    raw = base64.b64decode(re.sub(r'\s', '', match[1]), validate=True)
    (OUT / 'guest-evidence.tar.gz').write_bytes(raw)
    guest = OUT / 'guest'
    if guest.exists():
        if guest.is_symlink() or guest.resolve().parent != OUT.resolve():
            raise ValueError('Unexpected guest evidence directory')
        shutil.rmtree(guest)
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        archive.extractall(guest, filter='data')
    return guest / 'mfw-results'


def verify(guest):
    words = lambda path: struct.unpack('<2190H', path.read_bytes())
    modes = ('direct', 'composed') if (guest / 'baseline-direct.bin').is_file() else ('composed',)
    baseline = {mode: words(guest / f'baseline-{mode}.bin') for mode in modes}
    candidate = {'direct': words(OUT / f'source-files/nx300m-mod/looks/{LOOK}/table.d4lut'),
                 'composed': words(OUT / f'source-files/nx300m-mod/looks/{LOOK}/table-stock3d.d4lut')}
    if (guest / 'cached-before.bin').read_bytes() != bytes(4380):
        raise ValueError('Original unchanged parameter index did not skip its cached write')
    checks, trace_checks = [], []
    def check_trace(tag, mode, strength=None, passthrough=False):
        trace = json.loads((guest / f'{tag}.trace.json').read_text())
        fixture = json.loads((guest / f'{tag}.fixture.json').read_text())
        tid = fixture['update_tid']
        if trace.get('stage') != 'update-return' or trace.get('update_tid') != tid:
            raise ValueError(f'{tag}: trace did not finish in the fixture Update thread')
        if (trace.get('writer_tids') != [tid] * 3 or trace.get('channel_order') != [0, 1, 2]
                or trace.get('write_calls') != 3):
            raise ValueError(f'{tag}: writer thread or channel order differs')
        if trace.get('source_branch') != {'direct': 'unity', 'composed': 'factory'}[mode]:
            raise ValueError(f'{tag}: wrong genuine source branch')
        source, forwarded = trace.get('source_ptrs'), trace.get('forwarded_ptrs')
        if not isinstance(source, list) or not isinstance(forwarded, list) or len(source) != 3 or len(forwarded) != 3:
            raise ValueError(f'{tag}: missing three actual writer pointer observations')
        if any(not isinstance(pointer, int) or pointer <= 0 for pointer in source + forwarded):
            raise ValueError(f'{tag}: invalid writer pointer observation')
        if strength is not None and trace.get('strength') != strength:
            raise ValueError(f'{tag}: wrong traced strength')
        if passthrough and source != forwarded:
            raise ValueError(f'{tag}: original source pointers were not passed through unchanged')
        trace_checks.append({'tag': tag, 'update_tid': tid, 'writer_tids': trace['writer_tids'],
                             'channel_order': trace['channel_order'], 'source_branch': trace['source_branch'],
                             'source_pointers_preserved': source == forwarded})
    for mode in modes:
        for strength in (0, 50, 100):
            expected = tuple((a * (100-strength) + b * strength + 50) // 100 for a, b in zip(baseline[mode], candidate[mode]))
            actual = words(guest / f'{mode}-{strength}.bin')
            if actual != expected:
                index = next(i for i, pair in enumerate(zip(actual, expected)) if pair[0] != pair[1])
                raise ValueError(f'{mode} strength {strength} differs at word {index}: {actual[index]} vs {expected[index]}')
            checks.append({'mode': mode, 'strength': strength, 'matched_words': len(actual)})
            check_trace(f'{mode}-{strength}', mode, strength, passthrough=strength == 0)
        for stage in ('stock', 'restored'):
            if words(guest / f'{stage}-{mode}.bin') != baseline[mode]:
                raise ValueError(f'{stage} {mode} did not restore the genuine baseline')
        check_trace(f'stock-{mode}', mode, passthrough=True)
    if words(guest / 'installed-same-params.bin') != candidate['composed']:
        raise ValueError('First hook call did not invalidate the pre-existing original cache')
    check_trace('installed-same-params', 'composed', 100)
    if words(guest / 'reapplied-stock-composed.bin') != baseline['composed']:
        raise ValueError('Reinstalled hook did not preserve the stock table without a selection')
    check_trace('reapplied-stock-composed', 'composed', passthrough=True)
    def identity(path):
        text = path.read_text()
        fields = text[text.rfind(')')+2:].split()
        return fields[19]
    if identity(guest / 'before.stat') != identity(guest / 'after.stat'):
        raise ValueError('Target process identity changed')
    if not re.search(r'^TracerPid:\s+0$', (guest / 'after.status').read_text(), re.M):
        raise ValueError('Target remained traced after restoration')
    if len((guest / 'after.tasks').read_text().split()) != 2:
        raise ValueError('Fixture worker thread was not preserved')
    installed = json.loads((guest / 'apply.json').read_text())
    restored = json.loads((guest / 'restore.json').read_text())
    if installed.get('hooks_installed') is not True or restored.get('restored') is not True:
        raise ValueError('Loader did not report actual hook installation and restoration')
    idempotent = json.loads((guest / 'apply-idempotent.json').read_text())
    reapplied = json.loads((guest / 'reapply.json').read_text())
    rerestored = json.loads((guest / 'rerestore.json').read_text())
    if idempotent.get('hooks_installed') is not True or idempotent.get('already_installed') is not True:
        raise ValueError('Repeated apply did not preserve the installed hook')
    if reapplied.get('hooks_installed') is not True or reapplied.get('already_installed') is not False:
        raise ValueError('Apply after restore did not re-enable the retained hook')
    if any(item.get('mapping_reused') is not True or item.get('region') != installed.get('region')
           for item in (idempotent, reapplied)):
        raise ValueError('Repeated installation did not reuse the same RAM allocation')
    if (guest / 'installed.maps').read_text() != (guest / 'idempotent.maps').read_text():
        raise ValueError('Repeated installed apply changed target mappings')
    if rerestored.get('restored') is not True or rerestored.get('mapping_retained') is not True:
        raise ValueError('Second restore did not retain and disable the mapping')
    statuses = {label: json.loads((guest / f'status-{label}.json').read_text())
                for label in ('before', 'installed', 'restored', 'reapplied', 'rerestored')}
    lazy = json.loads((guest / 'status-lazy-before.json').read_text())
    original = json.loads((PROJECT / 'outputs/deployment/lut-live-loader/build-report.json').read_text())
    if lazy['slot_lut'] != lazy['iq_bias'] + original['stock']['iq']['lut_resolver'] or not lazy['rw_slots_original']:
        raise ValueError('Startup fixture did not retain and recognize the original unresolved LUT JUMP_SLOT')
    if not json.loads((guest / 'lazy-apply.json').read_text()).get('hooks_installed'):
        raise ValueError('Loading before the first original writer call failed')
    if words(guest / 'lazy-composed.bin') != candidate['composed']:
        raise ValueError('First writer call after lazy-state installation did not apply the selected LUT')
    check_trace('lazy-composed', 'composed', 100)
    if not json.loads((guest / 'status-lazy-restored.json').read_text()).get('rw_slots_original'):
        raise ValueError('Startup installation did not restore the original writer')
    for label, hooked in (('installed', True), ('restored', False), ('reapplied', True), ('rerestored', False)):
        status = statuses[label]
        if status.get('ram_hooks_installed') is not hooked or status.get('rw_slots_original') is not (not hooked):
            raise ValueError(f'{label}: actual call slots differ from the expected state')
        before_threads = [(t['tid'], t['starttime']) for t in statuses['before']['threads']]
        if [(t['tid'], t['starttime']) for t in status['threads']] != before_threads:
            raise ValueError(f'{label}: target thread identity changed')
        if any(t['tracer_pid'] != 0 for t in status['threads']):
            raise ValueError(f'{label}: a target thread remained traced')
    binding_checked = (guest / 'binding-installed.json').is_file()
    if binding_checked:
        for label, expected in (('installed', True), ('no-telemetry', True), ('after-shot', True),
                                ('replaced-telemetry', True), ('stale-start', False), ('restored', False),
                                ('reapplied', True), ('rerestored', False)):
            if json.loads((guest / f'binding-{label}.json').read_text()).get('binding_ready') is not expected:
                raise ValueError(f'Unexpected shared RAM binding result: {label}')
    legacy_before = json.loads((guest / 'status-legacy-before.json').read_text())
    legacy_after = json.loads((guest / 'status-legacy-after-refusal.json').read_text())
    for field in ('slot_update', 'slot_lut'):
        if legacy_before[field] != legacy_after[field]:
            raise ValueError('Rejected compatibility image changed a live call slot')
    if not json.loads((guest / 'legacy-restored.json').read_text()).get('restored'):
        raise ValueError('Known-image recovery did not restore the original slots')
    if not json.loads((guest / 'status-legacy-restored.json').read_text()).get('rw_slots_original'):
        raise ValueError('Known-image recovery left non-stock call slots')
    if words(guest / 'legacy-restored-composed.bin') != baseline['composed']:
        raise ValueError('Known-image recovery changed the original output table')
    if words(guest / 'recovered-composed.bin') != candidate['composed']:
        raise ValueError('Fresh installation after known-image recovery failed to apply the selected table')
    if not json.loads((guest / 'binding-recovered.json').read_text()).get('binding_ready'):
        raise ValueError('Fresh installation after recovery is not ready')
    return {'passed': True, 'native_arm_kernel': True, 'real_ptrace': True,
            'lazy_writer_admission_checked': True, 'first_call_after_lazy_install_checked': True,
            'known_image_recovery_checked': True, 'unknown_image_refused_without_slot_writes': True,
            'target_identity_preserved': True, 'original_cached_parameters_reloaded': True,
            'checks': checks, 'stock_selection_restored': True, 'original_slots_restored': True,
            'trace_checks': trace_checks, 'zero_strength_original_pointers_preserved': True,
            'repeated_apply_same_mapping': True, 'restore_reapply_stock_preserved': True,
            'direct_source_tested': 'direct' in modes,
            'restore_refresh_method': 'change genuine parameter index' if 'direct' in modes else 'explicit original node Reset in fixture',
            'fixture_threads_preserved': 2, 'synthetic_mmio': True, 'image_verified': False,
            'shared_ram_binding_checked': binding_checked,
            'readiness_independent_of_shot_telemetry': binding_checked,
            'physical_camera_verified': False, 'main_simulator_modified': False, 'sd_card_accessed': False}


def run():
    call('start')
    report = {'passed': False, 'scope': 'private ARM Linux; stock IQ/UDD fixture with synthetic MMIO'}
    try:
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            log = OUT / 'serial.log'
            if log.exists() and 'LUT_LIVE_PRIVATE_VM_SHELL_READY' in log.read_text(errors='replace'):
                break
            time.sleep(1)
        else:
            raise RuntimeError('Private ARM boot timed out')
        execution = call('exec', '/bin/sh /arm-vm/mfw-probe.sh')
        arm_vm.write_json(OUT / 'execution.json', execution)
        guest = collect()
        if execution['exit_code'] or 'LUT_LIVE_FULL_ARM_PASSED' not in execution['output']:
            raise RuntimeError(f'Guest probe exit {execution["exit_code"]}; inspect execution.json and guest evidence')
        report = verify(guest)
    except Exception as exc:
        report['error'] = str(exc)
        raise
    finally:
        shutdown = call('stop')
        arm_vm.write_json(OUT / 'shutdown.json', shutdown)
        report['private_vm_stopped'] = True
        arm_vm.write_json(OUT / 'report.json', report)
    print(json.dumps(report, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--linux', choices=['start', 'stop', 'exec'])
    parser.add_argument('payload', nargs='?', default='')
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--factory-only', action='store_true', help='Limit to the genuine factory-table branch')
    args = parser.parse_args()
    configure(args.factory_only)
    if args.linux:
        print(json.dumps(vm.linux_worker(args.linux, args.payload)))
        return
    prepare()
    if not args.prepare_only:
        run()


if __name__ == '__main__':
    main()
