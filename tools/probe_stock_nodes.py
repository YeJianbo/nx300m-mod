"""Run stock LUT/Gamma C++ parameter nodes against explicit inputs and MMIO RAM."""
import argparse
import json
from pathlib import Path
import shlex
import subprocess

from look_library import LookLibrary

PROJECT = Path(__file__).resolve().parents[1]
GUEST = '/opt/nx300m-lab/stock-1.15'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--look', default='ricoh-negative-exact')
    parser.add_argument('--alternate', default='ricoh-positive-exact')
    args = parser.parse_args()
    LookLibrary().get(args.look)
    LookLibrary().get(args.alternate)
    if args.look == args.alternate: raise ValueError('Use distinct looks for reload verification')
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
 runtime/lut_node_probe.c work/reference/metol/test_server/filmlab/src/crt0_arm.S \
 -L"$rf/usr/lib" -Wl,--no-as-needed -Wl,-l:libiq-fw-slpcam.so -Wl,-l:libdiosal.so \
 -Wl,-l:libproduction-mode.so "$rf/usr/lib/libudd.so.3.1" \
 "$rf/lib/libc-2.13.so" "$rf/lib/libdl.so.2" -lgcc -o outputs/metol/lut_node_probe.arm
mkdir -p "$rf/mod/looks/node-probe" "$rf/mod/looks/node-probe-alt"
cp outputs/metol/lut_node_probe.arm "$rf/mod/lut_node_probe.arm.tmp"
mv "$rf/mod/lut_node_probe.arm.tmp" "$rf/mod/lut_node_probe.arm"
cp outputs/firmware-runtime/lut_port.so "$rf/mod/lut_node_port.so.tmp"
mv "$rf/mod/lut_node_port.so.tmp" "$rf/mod/lut_node_port.so"
cp {shlex.quote('looks/'+args.look+'/table.d4lut')} "$rf/mod/looks/node-probe/table.d4lut"
cp {shlex.quote('looks/'+args.look+'/table-stock3d.d4lut')} "$rf/mod/looks/node-probe/table-stock3d.d4lut"
cp {shlex.quote('looks/'+args.alternate+'/table.d4lut')} "$rf/mod/looks/node-probe-alt/table.d4lut"
cp {shlex.quote('looks/'+args.alternate+'/table-stock3d.d4lut')} "$rf/mod/looks/node-probe-alt/table-stock3d.d4lut"
chmod 755 "$rf/mod/lut_node_probe.arm" "$rf/mod/lut_node_port.so"
unset LD_LIBRARY_PATH
exec unshare --mount --net --ipc sh -c 'mount -t tmpfs -o mode=0777,nosuid,nodev tmpfs {GUEST}/var/run
exec chroot --userspec=65534:65534 {GUEST} /qemu-arm-static -E LD_PRELOAD=/mod/lut_node_port.so -cpu cortex-a9 -L / /mod/lut_node_probe.arm'
'''
    result = subprocess.run(['wsl.exe', '-u', 'root', '-e', 'sh', '-c', script], capture_output=True, timeout=45)
    output = PROJECT / 'outputs/metol'
    for kind, data in (('stdout', result.stdout), ('stderr', result.stderr)):
        (output / ('lut-node-probe.'+kind+'.log')).write_bytes(data)
    stdout = result.stdout.decode('utf-8', 'replace')
    stderr = result.stderr.decode('utf-8', 'replace')
    report = {'exit_code': result.returncode, 'look': args.look, 'alternate': args.alternate,
              'libraries': ['unmodified NX300M 1.15 libiq-fw-slpcam.so', 'unmodified NX300M 1.15 libudd.so.3.1'],
              'input': 'explicit parameter structure, no sensor', 'register_backend': 'synthetic allocated RAM',
              'image_verified': False, 'stdout': stdout, 'stderr': stderr}
    (output / 'lut-node-probe.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(stdout); print(stderr); print(json.dumps({'exit_code': result.returncode, 'look': args.look}))
    raise SystemExit(result.returncode)


if __name__ == '__main__': main()
