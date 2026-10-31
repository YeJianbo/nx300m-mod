"""Run the Allegro ABI with original ARM GPIO libraries and explicit test edges."""
import json
import argparse
from pathlib import Path
import shlex
import subprocess

PROJECT = Path(__file__).resolve().parents[1]
GUEST = '/opt/nx300m-lab/stock-1.15'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--motion', action='store_true', help='Check the optional ideal rotor and asynchronous IRQ wake')
    args = parser.parse_args()
    motion = '-E NX300M_SHUTTER_MOTION=ideal-8-sector -E NX300M_SHUTTER_PARK=10 -E NX300M_SHUTTER_STEP_US=20000' if args.motion else ''
    base = '/mnt/' + PROJECT.drive[0].lower() + PROJECT.as_posix()[2:]
    script = f'''set -eu
cd {shlex.quote(base)}
tc=/opt/nx300m-toolchain/usr
rf={shlex.quote(GUEST)}
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \
 -std=c99 -O0 -Wall -Wextra -Werror -mfloat-abi=soft -fno-pic -no-pie -fno-stack-protector \
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 \
 -Wl,--allow-shlib-undefined -Wl,-rpath-link,"$rf/usr/lib" -Wl,-rpath-link,"$rf/lib" \
 runtime/shutter_probe.c work/reference/metol/test_server/filmlab/src/crt0_arm.S \
 "$rf/usr/lib/libudd.so.3.1" -L"$rf/usr/lib" -Wl,--no-as-needed -Wl,-l:libversion-info-nx300.so \
 "$rf/lib/libpthread.so.0" "$rf/lib/librt.so.1" "$rf/lib/libdl.so.2" "$rf/lib/libc-2.13.so" -lgcc \
 -o outputs/firmware-runtime/shutter_probe.arm
mkdir -p "$rf/mod/shutter-probe"
cp outputs/firmware-runtime/shutter_probe.arm "$rf/mod/shutter-probe/probe.arm"
cp outputs/firmware-runtime/gpio_preview.so "$rf/mod/shutter-probe/gpio.so"
cp outputs/firmware-runtime/device_preview.so "$rf/mod/shutter-probe/device.so"
chmod 755 "$rf/mod/shutter-probe/"*
unset LD_LIBRARY_PATH
exec unshare --mount --net --ipc sh -c 'mount -t tmpfs -o mode=0777,nosuid,nodev tmpfs {GUEST}/var/run
exec timeout 8 chroot --userspec=65534:65534 {GUEST} /qemu-arm-static -E NX300M_SHUTTER_SIM=1 -E NX300M_HOTSHOE_ABSENT=1 {motion} -E LD_PRELOAD=/mod/shutter-probe/gpio.so:/mod/shutter-probe/device.so -cpu cortex-a9 -L / /mod/shutter-probe/probe.arm'
'''
    result = subprocess.run(['wsl.exe', '-u', 'root', '-e', 'sh', '-c', script], capture_output=True, timeout=30)
    output = PROJECT/'outputs/firmware-runtime'
    tag = 'shutter-motion-probe' if args.motion else 'shutter-probe'
    for name, data in [('stdout', result.stdout), ('stderr', result.stderr)]:
        (output/f'{tag}.{name}.log').write_bytes(data)
    report = {'exit_code': result.returncode, 'gpio_libraries': 'NX300M 1.15 libudd.so.3.1 and libversion-info-nx300.so',
              'input': 'uncalibrated ideal rotor driven by stock motor GPIO writes' if args.motion else 'explicit synthetic PI2 FIFO levels', 'timer': 'Linux CLOCK_MONOTONIC timerfd',
              'physical_shutter_verified': False, 'image_verified': False,
              'hotshoe_input': {'gpio': 56, 'level': 1, 'fixture': 'empty shoe'},
              'stdout': result.stdout.decode('utf-8', 'replace'), 'stderr': result.stderr.decode('utf-8', 'replace')}
    (output/f'{tag}.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(report['stdout']); print(report['stderr']); print('exit_code='+str(result.returncode))
    raise SystemExit(result.returncode)


if __name__ == '__main__': main()
