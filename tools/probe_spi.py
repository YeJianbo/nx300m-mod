"""Check original ARM SPI configuration, CS GPIO and failure without RX data."""
import argparse
import json
from pathlib import Path
import shlex
import subprocess

PROJECT = Path(__file__).resolve().parents[1]
GUEST = '/opt/nx300m-lab/stock-1.15'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mmio', action='store_true', help='Run original A32 loads/stores through the FIFO register model')
    parser.add_argument('--peer', choices=('none', 'loopback'), default='none')
    args = parser.parse_args()
    if args.peer != 'none' and not args.mmio: parser.error('Loopback requires --mmio')
    options = (' -E NX300M_SPI_MMIO=1 -E NX300M_SPI_PEER='+args.peer) if args.mmio else ''
    base = '/mnt/' + PROJECT.drive[0].lower() + PROJECT.as_posix()[2:]
    script = f'''set -eu
cd {shlex.quote(base)}
tc=/opt/nx300m-toolchain/usr
rf={shlex.quote(GUEST)}
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \
 -std=c99 -O0 -Wall -Wextra -Werror -mfloat-abi=soft -fno-pic -no-pie -fno-stack-protector \
 -Iwork/vendor-source/TIZEN/project/NX300M/imagedev/usr/include/drime4/udd \
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 \
 -Wl,--allow-shlib-undefined -Wl,-rpath-link,"$rf/usr/lib" -Wl,-rpath-link,"$rf/lib" \
 runtime/spi_probe.c work/reference/metol/test_server/filmlab/src/crt0_arm.S \
 "$rf/usr/lib/libudd.so.3.1" -L"$rf/usr/lib" -Wl,--no-as-needed -Wl,-l:libversion-info-nx300.so \
 "$rf/lib/libpthread.so.0" "$rf/lib/librt.so.1" "$rf/lib/libdl.so.2" "$rf/lib/libc-2.13.so" -lgcc \
 -o outputs/firmware-runtime/spi_probe.arm
mkdir -p "$rf/mod/spi-probe"
cp outputs/firmware-runtime/spi_probe.arm "$rf/mod/spi-probe/probe.arm"
cp outputs/firmware-runtime/gpio_preview.so "$rf/mod/spi-probe/gpio.so"
cp outputs/firmware-runtime/device_preview.so "$rf/mod/spi-probe/device.so"
chmod 755 "$rf/mod/spi-probe/"*
unset LD_LIBRARY_PATH
exec unshare --mount --net --ipc sh -c 'mount -t tmpfs -o mode=1777,nosuid,nodev tmpfs {GUEST}/tmp
mount -t tmpfs -o mode=0777,nosuid,nodev tmpfs {GUEST}/var/run
exec timeout 8 chroot --userspec=65534:65534 {GUEST} /qemu-arm-static{options} -E LD_PRELOAD=/mod/spi-probe/gpio.so:/mod/spi-probe/device.so -cpu cortex-a9 -L / /mod/spi-probe/probe.arm'
'''
    result = subprocess.run(['wsl.exe', '-u', 'root', '-e', 'sh', '-c', script], capture_output=True, timeout=30)
    output = PROJECT/'outputs/firmware-runtime'
    report = {'exit_code': result.returncode, 'library': 'original NX300M 1.15 libudd.so.3.1',
              'loops_per_jiffy': 1000, 'HZ': 200, 'calibration': 'explicit lab loop-budget input',
              'instruction_mmio': args.mmio, 'peer': args.peer,
              'sensor_response_generated': False, 'image_verified': False}
    prefix = 'spi-mmio-'+args.peer if args.mmio else 'spi-probe'
    for name, data in [('stdout', result.stdout), ('stderr', result.stderr)]:
        (output/f'{prefix}.{name}.log').write_bytes(data)
        report[name] = data.decode('utf-8', 'replace')
    (output/(prefix+'.json')).write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(report['stdout']); print(report['stderr']); print('exit_code='+str(result.returncode))
    raise SystemExit(result.returncode)


if __name__ == '__main__': main()
