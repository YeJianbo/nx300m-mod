"""Cross-build the adapted upstream Metol UI against NX300M's own libraries."""
import json
import argparse
from pathlib import Path
import shlex
import subprocess

PROJECT = Path(__file__).resolve().parents[1]
GUEST = '/opt/nx300m-lab/stock-1.15'

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ui-only', action='store_true', help='保留已有配方目录，重编界面及后端程序')
    args = parser.parse_args()
    reference = PROJECT / 'work/reference/metol'
    source = json.loads((reference / 'scripts/filmlab/recipes.json').read_text(encoding='utf-8'))['recipes']
    output = PROJECT / 'outputs/metol'
    output.mkdir(parents=True, exist_ok=True)
    fields = ('R_COLOR', 'G_COLOR', 'B_COLOR', 'HUE', 'SATURATION', 'SHARPNESS', 'CONTRAST')
    if not args.ui_only:
        (output / 'recipes.txt').write_text(''.join('|'.join([key, recipe['label'], *[str(recipe[f]) for f in fields]])+'\n' for key, recipe in source.items()), encoding='utf-8')
    project = '/mnt/' + PROJECT.drive[0].lower() + PROJECT.as_posix()[2:]
    script = f'''set -eu
cd {shlex.quote(project)}
tc=/opt/nx300m-toolchain/usr
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
rf={shlex.quote(GUEST)}
ref=work/reference/metol/test_server/nxui
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \
 -std=c99 -O2 -Wall -Wextra -Wno-unused-function -Wno-unused-parameter \
 -I"$tc/include" -I"$tc/include/freetype2" -I"$ref" \
 -U_TIME_BITS -D_TIME_BITS=32 -U_FILE_OFFSET_BITS -D_FILE_OFFSET_BITS=32 \
 -mfloat-abi=soft -fno-pic -no-pie -fno-stack-protector \
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 \
 -Wl,--allow-shlib-undefined -Wl,-rpath-link,"$rf/usr/lib" -Wl,-rpath-link,"$rf/lib" \
 mod/metol/nxui.c "$ref/nxui_model.c" "$ref/nxui_services.c" \
 work/reference/metol/test_server/filmlab/src/crt0_arm.S \
 "$rf/usr/lib/libX11.so.6" "$rf/usr/lib/libXtst.so.6" "$rf/usr/lib/libfreetype.so.6" \
 "$rf/lib/libc-2.13.so" "$rf/lib/librt.so.1" -lgcc -o outputs/metol/nxui.arm
"$tc/bin/arm-linux-gnueabi-readelf" -V outputs/metol/nxui.arm > outputs/metol/versions.txt
"$tc/bin/arm-linux-gnueabi-readelf" -d outputs/metol/nxui.arm > outputs/metol/needed.txt
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \
 -std=c99 -O2 -Wall -Wextra -Werror -I"$tc/include" -I"$tc/include/freetype2" \
 -U_TIME_BITS -D_TIME_BITS=32 -U_FILE_OFFSET_BITS -D_FILE_OFFSET_BITS=32 \
 -mfloat-abi=soft -fno-pic -no-pie -fno-stack-protector \
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 \
 -Wl,--allow-shlib-undefined -Wl,-rpath-link,"$rf/usr/lib" -Wl,-rpath-link,"$rf/lib" \
 tools/panel_font_metadata.c work/reference/metol/test_server/filmlab/src/crt0_arm.S \
 "$rf/usr/lib/libfontconfig.so.1" "$rf/lib/libc-2.13.so" -lgcc -o outputs/metol/panel-font-metadata.arm
work/qemu/qemu-arm-static -L "$rf" outputs/metol/panel-font-metadata.arm \
 "$rf/usr/share/fonts/SDIC_GP_US_20120720.ttf" outputs/metol/mfw-font.charset
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \
 -std=c99 -O1 -Wall -Wextra -Werror -D_GNU_SOURCE -include mod/metol/remote/compat.h \
 -U_TIME_BITS -D_TIME_BITS=32 -U_FILE_OFFSET_BITS -D_FILE_OFFSET_BITS=64 \
 -mfloat-abi=soft -fno-pic -no-pie -fno-stack-protector \
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 \
 -Wl,-rpath-link,"$rf/lib" \
 mod/metol/lut_engine.c mod/metol/remote/compat.c work/reference/metol/test_server/filmlab/src/crt0_arm.S \
 "$rf/lib/libc-2.13.so" -lgcc -o outputs/metol/lut_engine.arm
"$tc/bin/arm-linux-gnueabi-readelf" -V outputs/metol/lut_engine.arm > outputs/metol/lut-engine-versions.txt
"$tc/bin/arm-linux-gnueabi-readelf" -d outputs/metol/lut_engine.arm > outputs/metol/lut-engine-needed.txt
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=c99 -O1 -Wall -Wextra -Werror -D_POSIX_C_SOURCE=200809L -include mod/metol/remote/compat.h \\
 -I"$ref" \\
 -U_TIME_BITS -D_TIME_BITS=32 -U_FILE_OFFSET_BITS -D_FILE_OFFSET_BITS=32 \\
 -mfloat-abi=soft -fno-pic -no-pie -fno-stack-protector \\
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 \\
 -Wl,-rpath-link,"$rf/lib" \\
 mod/metol/recipe_engine.c "$ref/nxui_model.c" work/reference/metol/test_server/filmlab/src/crt0_arm.S \\
 "$rf/lib/libc-2.13.so" "$rf/lib/librt.so.1" -lgcc -o outputs/metol/recipe_engine.arm
"$tc/bin/arm-linux-gnueabi-readelf" -V outputs/metol/recipe_engine.arm > outputs/metol/recipe-engine-versions.txt
"$tc/bin/arm-linux-gnueabi-readelf" -d outputs/metol/recipe_engine.arm > outputs/metol/recipe-engine-needed.txt
'''
    subprocess.run(['wsl.exe', '-u', 'root', '-e', 'sh', '-c', script], check=True)
    print(f'Metol NX300M ARM UI, LUT and recipe engines built; {len(source)} upstream recipes')

if __name__ == '__main__': main()
