"""Check the failing stock SMA remain ioctl without starting or replacing a camera."""
import json
from pathlib import Path
import shlex
import shutil
import subprocess

PROJECT = Path(__file__).resolve().parents[1]
GUEST = '/opt/nx300m-lab/stock-1.15'
OUTPUT = PROJECT/'outputs/firmware-runtime'


def main():
    compiler = shutil.which('clang')
    if not compiler:
        raise SystemExit('clang is required')
    subprocess.run([compiler, '--target=arm-linux-gnueabi', '-march=armv7-a', '-mfloat-abi=soft',
                    '-std=c99', '-O2', '-Wall', '-Wextra', '-Werror', '-fPIC', '-ffreestanding',
                    '-fno-builtin', '-fno-stack-protector', '-nostdlib', '-shared', '-fuse-ld=lld',
                    'runtime/device_preview.c', '-o', str(OUTPUT/'sma-remain-device.so')], cwd=PROJECT, check=True)
    base = '/mnt/'+PROJECT.drive[0].lower()+PROJECT.as_posix()[2:]
    script = f'''set -eu
cd {shlex.quote(base)}
tc=/opt/nx300m-toolchain/usr
rf={shlex.quote(GUEST)}
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=c99 -O0 -Wall -Wextra -Werror -mfloat-abi=soft -fno-pic -no-pie -fno-stack-protector \\
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 \\
 -Wl,--allow-shlib-undefined -Wl,-rpath-link,"$rf/usr/lib" -Wl,-rpath-link,"$rf/lib" \\
 runtime/sma_remain_probe.c work/reference/metol/test_server/filmlab/src/crt0_arm.S \\
 -L"$rf/usr/lib" -Wl,--no-as-needed -Wl,-l:libsys-mmap-manager.so \\
 "$rf/lib/libpthread.so.0" "$rf/lib/librt.so.1" \\
 "$rf/lib/libdl.so.2" "$rf/lib/libc-2.13.so" -lgcc -o outputs/firmware-runtime/sma-remain-probe.arm
mkdir -p "$rf/mod/sma-remain-probe"
cp outputs/firmware-runtime/sma-remain-probe.arm "$rf/mod/sma-remain-probe/probe.arm"
cp outputs/firmware-runtime/sma-remain-device.so "$rf/mod/sma-remain-probe/device.so"
chmod 755 "$rf/mod/sma-remain-probe/probe.arm"
unset LD_LIBRARY_PATH
exec unshare --mount --net --ipc sh -c 'mount -t tmpfs -o mode=0777,nosuid,nodev tmpfs {GUEST}/var/run
mount -t tmpfs -o mode=1777,nosuid,nodev tmpfs {GUEST}/tmp
exec timeout 15 chroot --userspec=65534:65534 {GUEST} /qemu-arm-static -E NX300M_ISP_SIM=1 -E LD_PRELOAD=/mod/sma-remain-probe/device.so -cpu cortex-a9 -L / /mod/sma-remain-probe/probe.arm'
'''
    process = subprocess.run(['wsl.exe', '-u', 'root', '-e', 'sh', '-c', script], capture_output=True, timeout=40)
    report = {
        'exit_code': process.returncode,
        'stock_library': 'NX300M 1.15 libsys-mmap-manager.so',
        'stock_calls': ['sysmmap_osif_init', 'sysmmap_osif_open', 'sysmmap_osif_update_remain_buf'],
        'scope': 'Isolated original ABI set/clear plus readable/invalid ioctl arguments and CMA accounting',
        'full_camera_playback_roundtrip_verified': False,
        'production_device_so_replaced': False,
        'stdout': process.stdout.decode('utf-8', 'replace'),
        'stderr': process.stderr.decode('utf-8', 'replace'),
    }
    (OUTPUT/'sma-remain-probe.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, indent=2))
    raise SystemExit(process.returncode)


if __name__ == '__main__':
    main()
