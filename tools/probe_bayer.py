"""Check real SRW packing using the original firmware's packed-pixel reader."""
import argparse
import json
from pathlib import Path
import shlex
import subprocess

from replay_bayer import inspect_packet

PROJECT = Path(__file__).resolve().parents[1]
GUEST = '/opt/nx300m-lab/stock-1.15'


def main():
    import rawpy
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('packet', type=Path)
    args = parser.parse_args()
    info = inspect_packet(args.packet)
    provenance = json.loads(args.packet.with_suffix('.json').read_text(encoding='utf-8'))
    x, y = 200, 200
    border = provenance.get('reconstructed_border')
    source_x, source_y = (x-border['active_offset'][0], y-border['active_offset'][1]) if border else (x, y)
    with rawpy.imread(provenance['source']) as raw:
        if not border and raw.raw_image.shape != (info['height'], info['width']): raise ValueError('Source geometry changed')
        if source_x < 0 or source_y < 0: raise ValueError('Probe sample is outside recorded active area')
        expected = [int(value) for value in raw.raw_image[source_y:source_y+2, source_x:source_x+2].reshape(-1)]
    base = '/mnt/'+PROJECT.drive[0].lower()+PROJECT.as_posix()[2:]
    packet = '/mnt/'+args.packet.resolve().drive[0].lower()+args.packet.resolve().as_posix()[2:]
    script = f'''set -eu
cd {shlex.quote(base)}
tc=/opt/nx300m-toolchain/usr
rf={shlex.quote(GUEST)}
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \
 -std=c99 -O0 -Wall -Wextra -Werror -mfloat-abi=soft -fno-pic -no-pie -fno-stack-protector \
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 \
 -Wl,--allow-shlib-undefined -Wl,-rpath-link,"$rf/usr/lib" -Wl,-rpath-link,"$rf/lib" \
 runtime/bayer_pack_probe.c work/reference/metol/test_server/filmlab/src/crt0_arm.S \
 -L"$rf/usr/lib" -Wl,--no-as-needed -Wl,-l:libcapture-fw-slpcam-nx300.so \
 "$rf/lib/libc-2.13.so" -lgcc -o outputs/firmware-runtime/bayer_pack_probe.arm
mkdir -p "$rf/mod/bayer-probe"
cp outputs/firmware-runtime/bayer_pack_probe.arm "$rf/mod/bayer-probe/probe.arm"
cp {shlex.quote(packet)} "$rf/mod/bayer-probe/packet.bin"
chmod 755 "$rf/mod/bayer-probe/probe.arm"
unset LD_LIBRARY_PATH
exec timeout 15 unshare --mount --net --ipc chroot --userspec=65534:65534 "$rf" \
 /qemu-arm-static -cpu cortex-a9 -L / /mod/bayer-probe/probe.arm \
 /mod/bayer-probe/packet.bin {x} {y} {' '.join(map(str, expected))}
'''
    result = subprocess.run(['wsl.exe', '-u', 'root', '-e', 'sh', '-c', script], capture_output=True, timeout=35)
    output = PROJECT/'outputs/firmware-runtime'
    report = {'exit_code': result.returncode, 'library': 'unmodified NX300M 1.15 libcapture-fw-slpcam-nx300.so',
              'function': 'dbg_get_pixelValue_fromPackBayer', 'fixture': str(args.packet),
              'descriptor': 'explicit getter-compatible synthetic CCapImage, no live camera state',
              'coordinates': [x, y], 'expected_from_source_srw': expected,
              'sensor_or_isp_verified': False,
              'stdout': result.stdout.decode('utf-8', 'replace'), 'stderr': result.stderr.decode('utf-8', 'replace')}
    (output/'bayer-pack-probe.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, indent=2))
    raise SystemExit(result.returncode)


if __name__ == '__main__': main()
