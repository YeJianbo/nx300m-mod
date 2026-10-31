"""Run the original libudd LUT writer in an isolated ARM register-memory fixture."""
import argparse
import json
import re
from pathlib import Path
import shlex
import subprocess
from look_library import LookLibrary

PROJECT = Path(__file__).resolve().parents[1]
GUEST = '/opt/nx300m-lab/stock-1.15'

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--look', help='Library look to forward through original libudd')
    parser.add_argument('--mod-root', default='/mod', help='Runtime LUT root, including a native SD directory')
    args = parser.parse_args()
    if (not re.fullmatch(r'/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*', args.mod_root)
            or len(args.mod_root) > 160 or any(part in ('.', '..') for part in args.mod_root.split('/'))):
        parser.error('Invalid mod root')
    library = LookLibrary()
    if args.look: library.get(args.look)
    table = 'looks/'+args.look+'/table.d4lut' if args.look else 'outputs/metol/identity.d4lut'
    base = '/mnt/' + PROJECT.drive[0].lower() + PROJECT.as_posix()[2:]
    mod = GUEST+args.mod_root
    script = f'''set -eu
cd {shlex.quote(base)}
tc=/opt/nx300m-toolchain/usr
rf={shlex.quote(GUEST)}
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \
 -std=c99 -O0 -Wall -Wextra -Werror -mfloat-abi=soft -fno-pic -no-pie -fno-stack-protector \
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 \
 -Wl,--allow-shlib-undefined -Wl,-rpath-link,"$rf/usr/lib" -Wl,-rpath-link,"$rf/lib" \
 runtime/lut_probe.c work/reference/metol/test_server/filmlab/src/crt0_arm.S \
 "$rf/usr/lib/libudd.so.3.1" "$rf/lib/libc-2.13.so" "$rf/lib/libdl.so.2" -lgcc \
 -o outputs/metol/lut_probe.arm
mkdir -p {shlex.quote(mod+'/looks/probe')}
cp outputs/metol/lut_probe.arm {shlex.quote(mod+'/lut_probe.arm')}
cp outputs/firmware-runtime/lut_port.so {shlex.quote(mod+'/lut_port.so')}
cp {shlex.quote(table)} {shlex.quote(mod+'/looks/probe/table.d4lut')}
printf invalid > {shlex.quote(mod+'/looks/probe/invalid.d4lut')}
chmod 755 {shlex.quote(mod+'/lut_probe.arm')} {shlex.quote(mod+'/lut_port.so')}
unset LD_LIBRARY_PATH
exec unshare --mount --net --ipc sh -c 'mount -t tmpfs -o mode=0777,nosuid,nodev tmpfs {GUEST}/var/run
exec chroot --userspec=65534:65534 {GUEST} /qemu-arm-static -E NX300M_MOD_ROOT={args.mod_root} -E LD_PRELOAD={args.mod_root}/lut_port.so -cpu cortex-a9 -L / {args.mod_root}/lut_probe.arm'
'''
    result = subprocess.run(['wsl.exe', '-u', 'root', '-e', 'sh', '-c', script], capture_output=True, timeout=45)
    output = PROJECT / 'outputs/metol'
    name = 'lut-probe' + ('-'+args.look if args.look else '')
    if args.mod_root != '/mod': name += '-custom-root'
    (output / (name+'.stdout.log')).write_bytes(result.stdout)
    (output / (name+'.stderr.log')).write_bytes(result.stderr)
    report = {'exit_code': result.returncode, 'look': args.look or 'identity', 'mod_root': args.mod_root, 'library': 'NX300M 1.15 unmodified libudd.so.3.1', 'register_backend': 'synthetic allocated RAM', 'image_verified': False, 'stock_channels_matched': result.stdout.count(b'STOCK_LUT_COPY'), 'invalid_table_passthrough': b'INVALID_TABLE_STOCK_PASSTHROUGH=1' in result.stdout}
    (output / (name+'.json')).write_text(json.dumps(report, indent=2))
    print(result.stdout.decode('utf-8', 'replace'))
    print(result.stderr.decode('utf-8', 'replace'))
    print(json.dumps(report))
    if result.returncode: raise SystemExit(result.returncode)

if __name__ == '__main__': main()
