"""Build the independent X11 mode-dial controller against stock NX300M libs."""
from pathlib import Path
import json
import shlex
import subprocess
from wsl_paths import wsl_path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/controls/mode-dial-x11'

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    script=f'''set -eu
cd {shlex.quote(wsl_path(ROOT))}
tc=/opt/nx300m-toolchain/usr
rf=/opt/nx300m-lab/stock-1.15
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=c99 -O2 -Wall -Wextra -Werror -Wno-unused-function -D_GNU_SOURCE -I"$tc/include" \\
 -U_TIME_BITS -D_TIME_BITS=32 -U_FILE_OFFSET_BITS -D_FILE_OFFSET_BITS=32 \\
 -march=armv7-a -mfloat-abi=soft -fno-pic -no-pie -fno-stack-protector \\
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 \\
 -Wl,-rpath-link,"$rf/lib" -Wl,-rpath-link,"$rf/usr/lib" \\
 mod/metol/mode_dial_x11.c work/reference/metol/test_server/filmlab/src/crt0_arm.S \\
 "$rf/usr/lib/libX11.so.6" "$rf/lib/libc-2.13.so" -lgcc \\
 -o {shlex.quote(wsl_path(OUT/'mode_dial_x11.arm'))}
"$tc/bin/arm-linux-gnueabi-readelf" -d -V {shlex.quote(wsl_path(OUT/'mode_dial_x11.arm'))}
'''
    result=subprocess.run(['wsl.exe','-d','Debian','-e','sh','-s'],input=script.encode(),capture_output=True)
    (OUT/'build.stdout.log').write_bytes(result.stdout)
    (OUT/'build.stderr.log').write_bytes(result.stderr)
    if result.returncode:raise RuntimeError(result.stderr.decode('utf-8','replace'))
    print(json.dumps({'artifact':str(OUT/'mode_dial_x11.arm'),'build_exit_code':0}))

if __name__=='__main__':main()
