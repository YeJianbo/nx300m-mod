"""Isolated ARM forwarding fixture for the native LCD observer."""
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

PROJECT = Path(__file__).resolve().parents[1]
GUEST = '/opt/nx300m-lab/stock-1.15'
OUTPUT = PROJECT / 'outputs/firmware-runtime'


def main():
    subprocess.run([sys.executable, '-X', 'utf8', 'tools/build_lcd_capture.py'],
                   cwd=PROJECT, check=True)
    subprocess.run([shutil.which('clang'), '--target=arm-linux-gnueabi', '-march=armv7-a',
                    '-mfloat-abi=soft', '-std=c99', '-O2', '-Wall', '-Wextra', '-Werror',
                    '-fPIC', '-ffreestanding', '-fno-builtin', '-fno-stack-protector',
                    '-nostdlib', '-shared', '-fuse-ld=lld', 'runtime/lcd_capture_fixture.c',
                    '-o', str(OUTPUT/'lcd-capture-fixture.so')], cwd=PROJECT, check=True)
    base = '/mnt/' + PROJECT.drive[0].lower() + PROJECT.as_posix()[2:]
    script = f'''set -eu
cd {shlex.quote(base)}
tc=/opt/nx300m-toolchain/usr
rf={shlex.quote(GUEST)}
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=c99 -O0 -Wall -Wextra -Werror -mfloat-abi=soft -fno-pic -no-pie -fno-stack-protector \\
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 \\
 -Wl,--allow-shlib-undefined -Wl,-rpath-link,"$rf/usr/lib" -Wl,-rpath-link,"$rf/lib" \\
 runtime/lcd_capture_probe.c work/reference/metol/test_server/filmlab/src/crt0_arm.S \\
 -Loutputs/firmware-runtime -Wl,--no-as-needed -Wl,-l:lcd-capture-fixture.so \\
 "$rf/lib/libpthread.so.0" "$rf/lib/librt.so.1" "$rf/lib/libdl.so.2" "$rf/lib/libc-2.13.so" \\
 -lgcc -o outputs/firmware-runtime/lcd-capture-probe.arm
mkdir -p "$rf/mod/lcd-capture-probe"
cp outputs/firmware-runtime/lcd-capture-probe.arm "$rf/mod/lcd-capture-probe/probe.arm"
cp outputs/firmware-runtime/lcd-capture.so "$rf/mod/lcd-capture-probe/capture.so"
cp outputs/firmware-runtime/lcd-capture-fixture.so "$rf/mod/lcd-capture-probe/lcd-capture-fixture.so"
chmod 755 "$rf/mod/lcd-capture-probe/probe.arm"
unset LD_LIBRARY_PATH
exec unshare --mount --net --ipc sh -c '
mount --make-rprivate /
mount -t tmpfs -o mode=0777,nosuid,nodev tmpfs {GUEST}/var/run
mount -t tmpfs -o mode=1777,nosuid,nodev tmpfs {GUEST}/tmp
mount -t tmpfs -o mode=0755,nosuid,nodev tmpfs {GUEST}/dev
touch {GUEST}/dev/fb0 {GUEST}/dev/d4_sma
chmod 666 {GUEST}/dev/fb0 {GUEST}/dev/d4_sma
mount -t proc proc {GUEST}/proc
exec timeout 15 chroot --userspec=65534:65534 {GUEST} /qemu-arm-static \\
 -E LD_LIBRARY_PATH=/mod/lcd-capture-probe \\
 -E LD_PRELOAD=/mod/lcd-capture-probe/capture.so:/mod/lcd-capture-probe/lcd-capture-fixture.so \\
 -cpu cortex-a9 -L / /mod/lcd-capture-probe/probe.arm'
'''
    process = subprocess.run(['wsl.exe', '-u', 'root', '-e', 'sh', '-c', script],
                             capture_output=True, timeout=40)
    report = {
        'exit_code': process.returncode,
        'scope': 'Isolated ARM forwarding fixture against stock glibc 2.13; fake ioctl return only',
        'physical_framebuffer_driver_verified': False,
        'physical_sma_mapping_verified': False,
        'production_modules_replaced': False,
        'stdout': process.stdout.decode('utf-8', 'replace'),
        'stderr': process.stderr.decode('utf-8', 'replace'),
    }
    (OUTPUT/'lcd-capture-probe.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, indent=2))
    raise SystemExit(process.returncode)


if __name__ == '__main__':
    main()
