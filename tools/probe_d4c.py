"""Execute original D4C command/SPI code against a written-register peer."""
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
 -Iwork/vendor-source/TIZEN/project/NX300M/imagedev/usr/include/drime4/udd \\
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 \\
 -Wl,--allow-shlib-undefined -Wl,-rpath-link,"$rf/usr/lib" -Wl,-rpath-link,"$rf/lib" \\
 runtime/d4c_probe.c work/reference/metol/test_server/filmlab/src/crt0_arm.S \\
 "$rf/usr/lib/libd4c.so.0.19" "$rf/usr/lib/libudd.so.3.1" -L"$rf/usr/lib" -Wl,--no-as-needed -Wl,-l:libversion-info-nx300.so \\
 "$rf/lib/libpthread.so.0" "$rf/lib/librt.so.1" "$rf/lib/libdl.so.2" "$rf/lib/libc-2.13.so" -lgcc \\
 -o outputs/firmware-runtime/d4c_probe.arm
mkdir -p "$rf/mod/d4c-probe"
cp outputs/firmware-runtime/d4c_probe.arm "$rf/mod/d4c-probe/probe.arm"
cp outputs/firmware-runtime/gpio_preview.so "$rf/mod/d4c-probe/gpio.so"
cp outputs/firmware-runtime/device_preview.so "$rf/mod/d4c-probe/device.so"
chmod 755 "$rf/mod/d4c-probe/"*
unset LD_LIBRARY_PATH
exec unshare --mount --net --ipc sh -c 'mount -t tmpfs -o mode=1777,nosuid,nodev tmpfs {GUEST}/tmp
mount -t tmpfs -o mode=0777,nosuid,nodev tmpfs {GUEST}/var/run
exec timeout 15 chroot --userspec=65534:65534 {GUEST} /qemu-arm-static -E NX300M_SPI_MMIO=1 -E NX300M_D4C_REGISTERS=1 -E LD_PRELOAD=/mod/d4c-probe/gpio.so:/mod/d4c-probe/device.so -cpu cortex-a9 -L / /mod/d4c-probe/probe.arm'
'''
    result = subprocess.run(['wsl.exe', '-u', 'root', '-e', 'sh', '-c', script], capture_output=True, timeout=40)
    output = PROJECT/'outputs/firmware-runtime'
    report = {'exit_code': result.returncode, 'libraries': ['unmodified NX300M 1.15 libd4c.so.0.19', 'unmodified libudd.so.3.1'],
              'spi_configuration': 'stock libd4c internal helper at 0x5c08, same call as d4c_if_config',
              'register_reset_defaults': False, 'burst_interpretation': 'opaque transfer payload only',
              'image_verified': False, 'dsp_result_generated': False,
              'stdout': result.stdout.decode('utf-8', 'replace'), 'stderr': result.stderr.decode('utf-8', 'replace')}
    for name, data in [('stdout', result.stdout), ('stderr', result.stderr)]:
        (output/f'd4c-probe.{name}.log').write_bytes(data)
    (output/'d4c-probe.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(report['stdout']); print(report['stderr']); print('exit_code='+str(result.returncode))
    raise SystemExit(result.returncode)


if __name__ == '__main__': main()
