"""Run the stock MnF splitter ABI and compare all output pixels with SRW packing.

This proves a software permutation under the observed register configuration.
It does not establish physical MnF bit accuracy or a complete imaging pipeline.
"""
import json
from pathlib import Path
import shlex
import shutil
import subprocess

PROJECT = Path(__file__).resolve().parents[1]
GUEST = '/opt/nx300m-lab/stock-1.15'
OUTPUT = PROJECT/'outputs/firmware-runtime'


def main():
    import numpy as np
    compiler = shutil.which('clang')
    if not compiler:
        raise SystemExit('clang is required')
    shim = OUTPUT/'ep-mnf-device.so'
    subprocess.run([compiler, '--target=arm-linux-gnueabi', '-march=armv7-a', '-mfloat-abi=soft',
                    '-std=c99', '-O2', '-Wall', '-Wextra', '-Werror', '-fPIC', '-ffreestanding',
                    '-fno-builtin', '-fno-stack-protector', '-nostdlib', '-shared', '-fuse-ld=lld',
                    'runtime/device_preview.c', '-o', str(shim)], cwd=PROJECT, check=True)
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
 runtime/ep_mnf_probe.c work/reference/metol/test_server/filmlab/src/crt0_arm.S \\
 "$rf/usr/lib/libudd.so.3.1" "$rf/lib/libpthread.so.0" "$rf/lib/librt.so.1" \\
 -Wl,--no-as-needed "$rf/lib/libdl.so.2" "$rf/lib/libc-2.13.so" -lgcc -o outputs/firmware-runtime/ep-mnf-probe.arm
mkdir -p "$rf/mod/ep-mnf-probe"
cp outputs/firmware-runtime/ep-mnf-probe.arm "$rf/mod/ep-mnf-probe/probe.arm"
cp outputs/firmware-runtime/ep-mnf-device.so "$rf/mod/ep-mnf-probe/device.so"
cp outputs/firmware-runtime/pp-active-bayer-fixture.bin "$rf/mod/ep-mnf-probe/packet.bin"
touch "$rf/mod/ep-mnf-probe/planes.bin"
chmod 666 "$rf/mod/ep-mnf-probe/planes.bin"
chmod 755 "$rf/mod/ep-mnf-probe/probe.arm"
unset LD_LIBRARY_PATH
unshare --mount --net --ipc sh -c 'mount -t tmpfs -o mode=0777,nosuid,nodev tmpfs {GUEST}/var/run
mount -t tmpfs -o mode=1777,nosuid,nodev tmpfs {GUEST}/tmp
exec timeout 15 chroot --userspec=65534:65534 {GUEST} /qemu-arm-static -E NX300M_ISP_SIM=1 -E LD_PRELOAD=/mod/ep-mnf-probe/device.so -cpu cortex-a9 -L / /mod/ep-mnf-probe/probe.arm'
cp "$rf/mod/ep-mnf-probe/planes.bin" outputs/firmware-runtime/ep-mnf-planes.bin
'''
    process = subprocess.run(['wsl.exe', '-u', 'root', '-e', 'sh', '-c', script], capture_output=True, timeout=40)
    report = {'exit_code': process.returncode, 'library': 'unmodified NX300M 1.15 libudd.so.3.1',
              'configuration': 'captured 5624x3696, 12-bit, four parity planes; physical address mode for isolated buffers',
              'fixture': 'pp-active-bayer-fixture.bin', 'hardware_bit_exact_verified': False,
              'sensor_or_pp_verified': False,
              'stdout': process.stdout.decode('utf-8', 'replace'), 'stderr': process.stderr.decode('utf-8', 'replace')}
    if not process.returncode:
        source = np.frombuffer((OUTPUT/'pp-active-bayer-fixture.bin').read_bytes(), dtype=np.uint8, offset=64).reshape(3714, 8448)
        # Independent vectorized bitstream decoding and parity slicing.
        groups = source[:3696, :8436].reshape(3696, 2812, 3).astype(np.uint16)
        mosaic = np.empty((3696, 5624), dtype=np.uint16)
        mosaic[:, ::2] = groups[:, :, 0] | ((groups[:, :, 1]&15)<<8)
        mosaic[:, 1::2] = (groups[:, :, 1]>>4) | (groups[:, :, 2]<<4)
        expected = np.full((4, 1857, 4224), 0xa5, dtype=np.uint8)
        for index in range(4):
            pixels = mosaic[index//2::2, index%2::2]
            first, second = pixels[:, ::2], pixels[:, 1::2]
            expected[index, :1848, :4218:3] = first&255
            expected[index, :1848, 1:4218:3] = (first>>8)|((second&15)<<4)
            expected[index, :1848, 2:4218:3] = second>>4
        actual = np.frombuffer((OUTPUT/'ep-mnf-planes.bin').read_bytes(), dtype=np.uint8)
        report['output_bytes'] = int(actual.size)
        report['expected_bytes'] = int(expected.size)
        report['all_output_bytes_equal'] = bool(np.array_equal(actual, expected.reshape(-1)))
        report['mosaic_pixels_compared'] = int(mosaic.size)
        report['padding_and_unwritten_rows_checked'] = True
        if not report['all_output_bytes_equal']:
            report['exit_code'] = 1
    (OUTPUT/'ep-mnf-probe.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, indent=2))
    raise SystemExit(report['exit_code'])


if __name__ == '__main__':
    main()
