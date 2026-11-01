"""Check the actual MFW start_script -> autoexec path in a private ARM VM.

The base VM and stock root remain unchanged. No camera/card or global binfmt.
"""
import argparse
import base64
import gzip
import io
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time

import arm_vm
import probe_mfw_ram_vm as vm
from mfw_runtime import LIBRARY, firmware_profile

PROJECT = Path(__file__).resolve().parents[1]
OUT = PROJECT / 'outputs/mfw-runtime/entry-vm'
SOCKET = '/tmp/nx300m-mfw-entry'
INIT = r'''#!/rescue/busybox sh
export PATH=/bin:/sbin:/usr/bin:/usr/sbin
/rescue/busybox mount -t devtmpfs devtmpfs /dev
exec </dev/ttyAMA0 >/dev/ttyAMA0 2>&1
/rescue/busybox mount -t proc proc /proc
/rescue/busybox mount -t sysfs sysfs /sys
/rescue/busybox mkdir -p /tmp /mnt/mmc
/rescue/busybox mount -t tmpfs -o mode=1777 tmpfs /tmp
/rescue/busybox cp /usr/lib/libmisc.so /arm-vm/libmisc.stock.so
/rescue/busybox mount --bind /arm-vm/libmisc.mfw.so /usr/lib/libmisc.so
echo MFW_ENTRY_VM_READY
while true; do /rescue/busybox setsid /bin/sh -i </dev/ttyAMA0 >/dev/ttyAMA0 2>&1; done
'''
AUTOEXEC = '#!/bin/sh\nprintf "MFW_AUTOEXEC_FROM_REAL_LIBRARY\\n" > /tmp/mfw-autoexec-proof\n'
PROBE = r'''set -e
uname -a
sha256sum /usr/lib/libmisc.so /arm-vm/libmisc.stock.so
echo STOCK_CONTROL_BEGIN
/arm-vm/mfw-entry-probe.arm /arm-vm/libmisc.stock.so 0
echo MFW_ENTRY_BEGIN
/arm-vm/mfw-entry-probe.arm /usr/lib/libmisc.so 1
/rescue/busybox cat /tmp/mfw-autoexec-proof
/rescue/busybox grep -x MFW_AUTOEXEC_FROM_REAL_LIBRARY /tmp/mfw-autoexec-proof
echo MFW_REAL_ENTRY_PASSED
'''


def linux_path(path):
    return arm_vm.wsl_path(path)


def call(action, command=''):
    result = subprocess.run(
        ['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'python3', linux_path(__file__),
         '--linux', action, base64.b64encode(command.encode()).decode()],
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    return json.loads(result.stdout)


def prepare():
    profile = firmware_profile('mfw', prepare=True)
    OUT.mkdir(parents=True, exist_ok=True)
    compile_command = f'''set -eu
tc=/opt/nx300m-toolchain/usr
rf=/opt/nx300m-lab/stock-1.15
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \
 -O2 -Wall -Wextra -Werror -mfloat-abi=soft -fno-pic -no-pie -fno-stack-protector \
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 \
 -Wl,-rpath-link,"$rf/lib" -Wl,-rpath-link,"$rf/usr/lib" \
 {shlex.quote(linux_path(PROJECT / 'runtime/mfw_entry_probe.c'))} \
 {shlex.quote(linux_path(PROJECT / 'work/reference/metol/test_server/filmlab/src/crt0_arm.S'))} \
 "$rf/lib/libdl.so.2" "$rf/lib/libc-2.13.so" -lgcc \
 -o {shlex.quote(linux_path(OUT / 'mfw-entry-probe.arm'))}
'''
    subprocess.run(['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'sh', '-c', compile_command], check=True)
    overlay = io.BytesIO()
    entries = [
        ('arm-vm', 0o40755, b''), ('mnt', 0o40755, b''), ('mnt/mmc', 0o40755, b''),
        ('init', 0o100755, INIT.encode()),
        ('arm-vm/libmisc.mfw.so', 0o100644, LIBRARY.read_bytes()),
        ('arm-vm/mfw-entry-probe.arm', 0o100755, (OUT / 'mfw-entry-probe.arm').read_bytes()),
        ('mnt/mmc/autoexec.sh', 0o100755, AUTOEXEC.encode()),
    ]
    for inode, (name, mode, data) in enumerate(entries, 1):
        arm_vm.cpio_add(overlay, name, mode, data, inode)
    arm_vm.cpio_add(overlay, 'TRAILER!!!', 0)
    shutil.copyfile(arm_vm.WORK / 'initramfs.cpio.gz', OUT / 'initramfs.cpio.gz')
    with (OUT / 'initramfs.cpio.gz').open('ab') as target:
        target.write(gzip.compress(overlay.getvalue(), mtime=0))
    arm_vm.write_json(OUT / 'profile.json', profile)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--linux', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('action', nargs='?', choices=('probe', 'start', 'exec', 'stop'), default='probe')
    parser.add_argument('command', nargs='?', default='')
    args = parser.parse_args()
    if args.linux:
        vm.OUT = OUT
        vm.SOCKET = SOCKET
        print(json.dumps(vm.linux_worker(args.action, args.command)))
        return
    if args.action == 'exec':
        print(json.dumps(call('exec', args.command), ensure_ascii=False, indent=2))
        return
    if args.action == 'stop':
        print(json.dumps(call('stop')))
        return
    prepare()
    print(json.dumps(call('start')), flush=True)
    if args.action == 'start':
        print('Private MFW entry VM started; use exec to run guest commands.', flush=True)
        return
    try:
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if 'MFW_ENTRY_VM_READY' in (OUT / 'serial.log').read_text(errors='replace'):
                break
            time.sleep(.3)
        else:
            raise TimeoutError('Private ARM VM did not become ready')
        result = call('exec', PROBE)
        arm_vm.write_json(OUT / 'result.json', result)
        print(result['output'])
        if result['exit_code'] or 'MFW_REAL_ENTRY_PASSED' not in result['output']:
            raise RuntimeError('MFW entry verification failed; see result.json')
    finally:
        call('stop')


if __name__ == '__main__':
    main()
