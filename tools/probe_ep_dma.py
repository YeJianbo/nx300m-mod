"""Verify the formerly unsupported EP DMA wait ABI using stock libudd."""
import json
from pathlib import Path
import shlex
import subprocess

PROJECT = Path(__file__).resolve().parents[1]
GUEST = '/opt/nx300m-lab/stock-1.15'


def main():
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
 runtime/ep_dma_probe.c work/reference/metol/test_server/filmlab/src/crt0_arm.S \\
 "$rf/usr/lib/libudd.so.3.1" "$rf/lib/libpthread.so.0" "$rf/lib/librt.so.1" \\
 "$rf/lib/libdl.so.2" "$rf/lib/libc-2.13.so" -lgcc -o outputs/firmware-runtime/ep_dma_probe.arm
mkdir -p "$rf/mod/ep-dma-probe"
cp outputs/firmware-runtime/ep_dma_probe.arm "$rf/mod/ep-dma-probe/probe.arm"
cp outputs/firmware-runtime/device_preview.so "$rf/mod/ep-dma-probe/device.so"
chmod 755 "$rf/mod/ep-dma-probe/"*
unset LD_LIBRARY_PATH
exec unshare --mount --net --ipc sh -c 'mount -t tmpfs -o mode=0777,nosuid,nodev tmpfs {GUEST}/var/run
mount -t tmpfs -o mode=1777,nosuid,nodev tmpfs {GUEST}/tmp
exec timeout 8 chroot --userspec=65534:65534 {GUEST} /qemu-arm-static -E NX300M_ISP_SIM=1 -E LD_PRELOAD=/mod/ep-dma-probe/device.so -cpu cortex-a9 -L / /mod/ep-dma-probe/probe.arm'
'''
    result = subprocess.run(['wsl.exe', '-u', 'root', '-e', 'sh', '-c', script], capture_output=True, timeout=30)
    report = {'exit_code': result.returncode, 'library': 'unmodified NX300M 1.15 libudd.so.3.1',
              'scope': 'EP DMA queue init, absent-job timeout, argument and O_NONBLOCK ABI',
              'image_verified': False, 'pixels_generated': False,
              'stdout': result.stdout.decode('utf-8', 'replace'), 'stderr': result.stderr.decode('utf-8', 'replace')}
    (PROJECT/'outputs/firmware-runtime/ep-dma-probe.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(report['stdout']); print(report['stderr']); print('exit_code='+str(result.returncode))
    raise SystemExit(result.returncode)


if __name__ == '__main__': main()
