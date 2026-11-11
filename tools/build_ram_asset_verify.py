"""Build the finite RAM asset verifier against the stock camera libc."""
from pathlib import Path
import shlex
import subprocess
from wsl_paths import wsl_path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/controls/ram-asset-verify'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    script = f'''set -eu
cd {shlex.quote(wsl_path(ROOT))}
tc=/opt/nx300m-toolchain/usr
rf=/opt/nx300m-lab/stock-1.15
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=c99 -O2 -Wall -Wextra -Werror -I"$tc/include" \\
 -D_TIME_BITS=32 -D_FILE_OFFSET_BITS=64 -march=armv7-a -mfloat-abi=soft \\
 -fno-pic -no-pie -fno-stack-protector -nostdlib -nostartfiles \\
 -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 -Wl,-rpath-link,"$rf/lib" \\
 runtime/ram_asset_verify.c work/reference/metol/test_server/filmlab/src/crt0_arm.S \\
 "$rf/lib/libc-2.13.so" -lgcc -o outputs/controls/ram-asset-verify/ram_asset_verify.arm
"$tc/bin/arm-linux-gnueabi-readelf" -d -V outputs/controls/ram-asset-verify/ram_asset_verify.arm
'''
    result = subprocess.run(['wsl.exe', '-d', 'Debian', '-e', 'sh', '-s'],
                            input=script.encode(), capture_output=True)
    (OUT / 'build.stdout.log').write_bytes(result.stdout)
    (OUT / 'build.stderr.log').write_bytes(result.stderr)
    if result.returncode:
        raise RuntimeError(f'ARM verifier build exited {result.returncode}:\n' + result.stderr.decode('utf-8', 'replace'))
    print('Built', OUT / 'ram_asset_verify.arm')


if __name__ == '__main__':
    main()
