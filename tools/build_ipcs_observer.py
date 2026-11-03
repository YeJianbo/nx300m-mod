"""Build the passive procfs IPCS observer; no camera or VM is contacted."""
import json
from pathlib import Path
import shlex
import subprocess

from build_lut_live_loader import wsl
from inspect_ipcs_lifecycle import OUT, ROOT, UDD_SHA, identity


def main():
    rootfs = Path(json.loads((ROOT/'project.local.json').read_text(encoding='utf-8-sig'))['rootfs'])
    if identity(rootfs/'usr/lib/libudd.so.3.1')['sha256'] != UDD_SHA:
        raise ValueError('Unexpected UDD')
    if not (OUT/'ipcs_observe_stock.h').exists():
        raise ValueError('Run tools/inspect_ipcs_lifecycle.py first')
    target = OUT/'ipcs_observe.arm'
    script = f'''set -eu
tc=/opt/nx300m-toolchain/usr
rf=/opt/nx300m-lab/stock-1.15
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=gnu99 -O2 -Wall -Wextra -Werror -march=armv7-a -mfloat-abi=soft \\
 -D_TIME_BITS=32 -D_FILE_OFFSET_BITS=64 -fno-stack-protector -fno-pic -no-pie \\
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 \\
 -Wl,-z,defs -Wl,-rpath-link,"$rf/lib" -I{shlex.quote(wsl(OUT))} \\
 {shlex.quote(wsl(ROOT/'tools/ipcs_observe.c'))} \\
 {shlex.quote(wsl(ROOT/'work/reference/metol/test_server/filmlab/src/crt0_arm.S'))} \\
 "$rf/lib/librt.so.1" "$rf/lib/libc-2.13.so" -lgcc -o {shlex.quote(wsl(target))}
"$tc/bin/arm-linux-gnueabi-readelf" -h -d -V {shlex.quote(wsl(target))}
'''
    result = subprocess.run(['wsl.exe','-d','Debian','-u','root','-e','sh','-s'],
                            input=script.encode(), capture_output=True)
    (OUT/'observer-build.stdout.log').write_bytes(result.stdout)
    (OUT/'observer-build.stderr.log').write_bytes(result.stderr)
    if result.returncode:
        raise RuntimeError(f'Observer ARM build exit {result.returncode}:\n' + result.stderr.decode('utf-8','replace'))
    report = {'binary': identity(target), 'source': identity(ROOT/'tools/ipcs_observe.c'),
              'stock_header': identity(OUT/'ipcs_observe_stock.h'),
              'target_abi': 'ARMv7 soft-float, stock glibc 2.13',
              'physical_camera_run': False, 'camera_deployment': False}
    (OUT/'observer-build.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__ == '__main__':
    main()
