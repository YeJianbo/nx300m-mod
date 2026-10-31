"""Run original IPCM configuration and real CPU YCC resize buffer writes."""
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
 runtime/ipcm_resize_probe.c work/reference/metol/test_server/filmlab/src/crt0_arm.S \\
 "$rf/usr/lib/libudd.so.3.1" "$rf/lib/libpthread.so.0" "$rf/lib/librt.so.1" \\
 "$rf/lib/libdl.so.2" "$rf/lib/libc-2.13.so" -lgcc -o outputs/firmware-runtime/ipcm_resize_probe.arm
mkdir -p "$rf/mod/ipcm-resize-probe"
cp outputs/firmware-runtime/ipcm_resize_probe.arm "$rf/mod/ipcm-resize-probe/probe.arm"
cp outputs/firmware-runtime/ipcm_resize.so "$rf/mod/ipcm-resize-probe/resize.so"
cp outputs/firmware-runtime/device_preview.so "$rf/mod/ipcm-resize-probe/device.so"
chmod 755 "$rf/mod/ipcm-resize-probe/"*
unset LD_LIBRARY_PATH
exec unshare --mount --net --ipc sh -c 'mount -t tmpfs -o mode=0777,nosuid,nodev tmpfs {GUEST}/var/run
mount -t tmpfs -o mode=1777,nosuid,nodev tmpfs {GUEST}/tmp
exec timeout 15 chroot --userspec=65534:65534 {GUEST} /qemu-arm-static -E NX300M_ISP_SIM=1 -E LD_PRELOAD=/mod/ipcm-resize-probe/device.so:/mod/ipcm-resize-probe/resize.so -cpu cortex-a9 -L / /mod/ipcm-resize-probe/probe.arm'
'''
    result = subprocess.run(['wsl.exe', '-u', 'root', '-e', 'sh', '-c', script], capture_output=True, timeout=40)
    output = PROJECT/'outputs/firmware-runtime'
    report = {'exit_code': result.returncode, 'library': 'unmodified NX300M 1.15 libudd.so.3.1',
              'events': 'original configuration/start; actual software bilinear YCC resize; completion follows output writes',
              'registers': 'private IPCM/IPCS transport, original libudd interrupt reader',
              'image_verified': False, 'software_resize_executed': result.returncode == 0, 'hardware_bit_exact': False,
              'stdout': result.stdout.decode('utf-8', 'replace'), 'stderr': result.stderr.decode('utf-8', 'replace')}
    for name, data in [('stdout', result.stdout), ('stderr', result.stderr)]:
        (output/f'ipcm-resize-probe.{name}.log').write_bytes(data)
    (output/'ipcm-resize-probe.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(report['stdout']); print(report['stderr']); print('exit_code='+str(result.returncode))
    raise SystemExit(result.returncode)


if __name__ == '__main__': main()

