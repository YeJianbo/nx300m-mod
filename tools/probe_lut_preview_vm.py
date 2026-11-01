"""Validate preview-observer RAM loading/restore against genuine stock ARM code."""
import argparse
import base64
import io
import json
from pathlib import Path
import re
import shlex
import subprocess
import tarfile
import time

import arm_vm
import probe_mfw_ram_vm as vm
from mfw_runtime import LIBRARY, firmware_profile

PROJECT = Path(__file__).resolve().parents[1]
OUT = PROJECT / 'outputs/deployment/lut-preview/arm-vm'
SOCKET = '/tmp/nx300m-lut-preview-private'
INIT = r'''#!/rescue/busybox sh
export PATH=/bin:/sbin:/usr/bin:/usr/sbin
/rescue/busybox mount -t devtmpfs devtmpfs /dev
exec </dev/ttyAMA0 >/dev/ttyAMA0 2>&1
/rescue/busybox mount -t proc proc /proc
/rescue/busybox mount -t sysfs sysfs /sys
/rescue/busybox mkdir -p /tmp /dev/pts /mnt/mmc /var/run /dev/shm /arm-vm/mfw-results
/rescue/busybox mount -t devpts devpts /dev/pts
/rescue/busybox mount -t tmpfs -o rw,exec,mode=1777,size=32m preview-runtime /tmp
/rescue/busybox mount -t tmpfs -o mode=1777 tmpfs /dev/shm
/rescue/busybox mount --bind /arm-vm/mfw-source /mnt/mmc
/rescue/busybox mount -o remount,bind,ro /mnt/mmc
/rescue/busybox mount --bind /mnt/mmc/lib/libmisc.so /usr/lib/libmisc.so
echo LUT_PREVIEW_PRIVATE_VM_READY
while true; do /rescue/busybox setsid /bin/sh -i </dev/ttyAMA0 >/dev/ttyAMA0 2>&1; done
'''
PROBE = r'''#!/bin/sh
set -eu
OUT=/arm-vm/mfw-results
LOADER=/mnt/mmc/bin/lut_preview_loader.arm
BLOB=/mnt/mmc/lut_preview_blob.bin
pid=
finish() {
    result=$?; trap - EXIT; set +e
    if [ -n "$pid" ] && [ -d /proc/$pid ]; then
        cat /proc/$pid/status > "$OUT/failure.status"
        cat /proc/$pid/maps > "$OUT/failure.maps"
        kill -TERM "$pid"
        wait "$pid"
    fi
    cp /tmp/preview-fixture.* "$OUT/"
    printf '%s\n' "$result" > "$OUT/probe.exit"
    exit "$result"
}
trap finish EXIT
uname -a > "$OUT/uname.txt"
sha256sum /usr/lib/libmisc.so > "$OUT/mfw-library.sha256"
/mnt/mmc/bin/lut_preview_fixture.arm > /tmp/preview-fixture.stdout 2> /tmp/preview-fixture.stderr &
pid=$!
attempt=0
while [ ! -s /tmp/preview-fixture.ready ]; do
    attempt=$((attempt+1)); [ "$attempt" -lt 60 ]; /rescue/busybox usleep 100000
done
read ready_pid slot < /tmp/preview-fixture.ready
[ "$ready_pid" = "$pid" ]
cat /proc/$pid/maps > "$OUT/before.maps"
cat /proc/$pid/stat > "$OUT/before.stat"
ls /proc/$pid/task > "$OUT/before.tasks"
seq=0
frame() {
    seq=$((seq+1))
    printf '%s 17\n' "$seq" > /tmp/preview-request.tmp
    mv /tmp/preview-request.tmp /tmp/preview-request
    attempt=0
    while :; do
        if [ -s /tmp/preview-response ]; then
            read got result heartbeat < /tmp/preview-response
            [ "$got" != "$seq" ] || break
        fi
        attempt=$((attempt+1)); [ "$attempt" -lt 50 ]; /rescue/busybox usleep 100000
    done
    [ "$result" = 1 ]
    cp "/tmp/preview-frame-$seq.bin" "$OUT/$1.frame.bin"
    printf '%s %s\n' "$result" "$heartbeat" > "$OUT/$1.response"
}
operation() {
    "$LOADER" "$1" --pid "$pid" --blob "$BLOB" --fixture > "$OUT/$2.json" 2> "$OUT/$2.stderr"
}
frame baseline
operation status baseline
set +e
"$LOADER" apply --pid "$pid" --blob "$BLOB" > "$OUT/wrong-target.stdout" 2> "$OUT/wrong-target.stderr"
wrong=$?
set -e
[ "$wrong" = 3 ]
operation apply apply
frame passthrough
operation status passthrough
operation observe observe
frame observed
operation status observed
operation pause pause
frame paused
operation status paused
operation restore restore
frame restored
operation status restored
operation apply reapply
operation observe reobserve
frame reobserved
operation status reobserved
operation restore final-restore
frame final
operation status final
cat /proc/$pid/maps > "$OUT/after.maps"
cat /proc/$pid/stat > "$OUT/after.stat"
cat /proc/$pid/status > "$OUT/after.status"
ls /proc/$pid/task > "$OUT/after.tasks"
for label in passthrough observed paused restored reobserved final; do
    /rescue/busybox cmp "$OUT/baseline.frame.bin" "$OUT/$label.frame.bin"
done
printf '%s 0\n' "$((seq+1))" > /tmp/preview-request.tmp
mv /tmp/preview-request.tmp /tmp/preview-request
wait "$pid"
pid=
echo LUT_PREVIEW_FULL_ARM_PASSED
'''


def configure():
    vm.OUT = OUT; vm.SOCKET = SOCKET; vm.INIT = INIT; vm.PROBE = PROBE


def call(action, command=''):
    result = subprocess.run(['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'python3',
                             arm_vm.wsl_path(__file__), '--linux', action,
                             base64.b64encode(command.encode()).decode()],
                            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    return json.loads(result.stdout)


def prepare():
    OUT.mkdir(parents=True, exist_ok=True)
    firmware_profile('mfw', prepare=True)
    fixture = OUT / 'lut_preview_fixture.arm'
    script = f'''set -eu
tc=/opt/nx300m-toolchain/usr
rf=/opt/nx300m-lab/stock-1.15
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \
 -std=gnu99 -O1 -Wall -Wextra -Werror -march=armv7-a -mfloat-abi=soft -fno-pic -no-pie -fno-stack-protector \
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 -Wl,--export-dynamic \
 -Wl,--allow-shlib-undefined -Wl,-rpath-link,"$rf/usr/lib" -Wl,-rpath-link,"$rf/lib" \
 {shlex.quote(arm_vm.wsl_path(PROJECT / 'runtime/lut_preview_fixture.c'))} \
 {shlex.quote(arm_vm.wsl_path(PROJECT / 'work/reference/metol/test_server/filmlab/src/crt0_arm.S'))} \
 "$rf/lib/libpthread.so.0" "$rf/lib/libc-2.13.so" "$rf/lib/libdl.so.2" -lgcc \
 -o {shlex.quote(arm_vm.wsl_path(fixture))}
'''
    built = subprocess.run(['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'sh', '-s'],
                           input=script.encode(), capture_output=True)
    (OUT / 'fixture-build.stderr.log').write_bytes(built.stderr)
    if built.returncode:
        raise RuntimeError(f'Fixture build exit {built.returncode}: {built.stderr.decode(errors="replace")}')
    inputs = {'bin/lut_preview_fixture.arm': fixture,
              'bin/lut_preview_loader.arm': OUT.parent / 'lut_preview_loader.arm',
              'lut_preview_blob.bin': OUT.parent / 'lut_preview_blob.bin',
              'lib/libmisc.so': LIBRARY}
    inventory = {}
    for name, source in inputs.items():
        target = OUT / 'source-files' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        inventory[name] = target.stat().st_size
    arm_vm.write_json(OUT / 'candidate.json', {'inventory': inventory,
        'scope': 'real stock DisplayAdjust; fixture replaces LiveviewCommon logging only',
        'mfw': firmware_profile('mfw'), 'source': 'current local build; no ZIP extraction'})
    vm.initramfs()


def collect():
    result = call('exec', '/rescue/busybox tar -czf /tmp/preview-evidence.tar.gz -C /arm-vm mfw-results; '
                         '/rescue/busybox base64 /tmp/preview-evidence.tar.gz')
    if result['exit_code']:
        raise RuntimeError('Evidence collection failed')
    raw = base64.b64decode(''.join(line.strip() for line in result['output'].splitlines()
                                  if re.fullmatch('[A-Za-z0-9+/=]+', line.strip())), validate=True)
    (OUT / 'guest-evidence.tar.gz').write_bytes(raw)
    with tarfile.open(fileobj=io.BytesIO(raw)) as package:
        package.extractall(OUT / 'guest', filter='data')
    return OUT / 'guest/mfw-results'


def verify(guest):
    def status(label):
        return json.loads((guest / (label + '.json')).read_text())
    base = status('baseline')
    if base['installed'] or base['calls']:
        raise ValueError('Fixture baseline was already hooked')
    for label, calls, observations, installed, observing in (
        ('passthrough', 1, 0, True, False), ('observed', 2, 1, True, True),
        ('paused', 3, 1, True, False), ('restored', 3, 1, False, False),
        ('reobserved', 4, 2, True, True), ('final', 4, 2, False, False)):
        s = status(label)
        if (s['calls'], s['observations'], s['installed'], s['observing'], s['inflight']) != (
                calls, observations, installed, observing, 0):
            raise ValueError('Observer lifecycle differs: ' + label)
        if (guest / (label + '.frame.bin')).read_bytes() != (guest / 'baseline.frame.bin').read_bytes():
            raise ValueError('Frame metadata or payload changed: ' + label)
    observed = status('observed')
    if observed['callback'] != 17 or observed['result'] != 1 or observed['geometry'][:6] != [800, 600, 640, 480, 8, 12]:
        raise ValueError('Original Execute or observation fields differ')
    if (guest / 'before.tasks').read_bytes() != (guest / 'after.tasks').read_bytes():
        raise ValueError('Fixture thread set changed')
    if not re.search(r'^TracerPid:\s+0$', (guest / 'after.status').read_text(), re.M):
        raise ValueError('A tracer remained attached')
    before = (guest / 'before.stat').read_text(); after = (guest / 'after.stat').read_text()
    if before[before.rfind(')')+2:].split()[19] != after[after.rfind(')')+2:].split()[19]:
        raise ValueError('Target restarted')
    ready = (guest / 'preview-fixture.ready').read_text().split()
    slot = int(ready[1])
    def permission(name):
        for line in (guest / name).read_text().splitlines():
            fields = line.split(); lo, hi = [int(x, 16) for x in fields[0].split('-')]
            if lo <= slot < hi: return fields[1]
        raise ValueError('Missing slot mapping')
    if permission('before.maps') != 'r--p' or permission('after.maps') != 'r--p':
        raise ValueError('Vtable page permissions changed')
    if (guest / 'mfw-library.sha256').read_text().split()[0] != firmware_profile('mfw')['libmisc_sha256']:
        raise ValueError('MFW library overlay differs')
    return {'passed': True, 'original_execute': True, 'read_only_vtable_preserved': True,
            'byte_identical_frame_and_metadata': True, 'restore_and_reapply': True,
            'fixture_threads_preserved': 2, 'mfw_library_verified': True,
            'pixel_processing': False, 'isp_dma_tested': False, 'camera_or_card_accessed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--linux', choices=('start', 'exec', 'stop'))
    parser.add_argument('payload', nargs='?', default='')
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args(); configure()
    if args.linux:
        print(json.dumps(vm.linux_worker(args.linux, args.payload))); return
    prepare()
    if args.prepare_only: return
    print(json.dumps(call('start')), flush=True)
    report = {'passed': False}
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if 'LUT_PREVIEW_PRIVATE_VM_READY' in (OUT / 'serial.log').read_text(errors='replace'): break
            time.sleep(.5)
        else: raise TimeoutError('Private preview VM boot timed out')
        execution = call('exec', '/bin/sh /arm-vm/mfw-probe.sh')
        arm_vm.write_json(OUT / 'execution.json', execution)
        guest = collect()
        if execution['exit_code'] or 'LUT_PREVIEW_FULL_ARM_PASSED' not in execution['output']:
            raise RuntimeError('Preview ARM probe failed; see execution.json and guest evidence')
        report = verify(guest)
    except Exception as exc:
        report['error'] = str(exc); raise
    finally:
        arm_vm.write_json(OUT / 'shutdown.json', call('stop'))
        report['private_vm_stopped'] = True
        arm_vm.write_json(OUT / 'report.json', report)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
