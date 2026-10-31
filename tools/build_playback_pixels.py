"""Build the optional software playback pixel adapter without restarting a session."""
import io
import json
from pathlib import Path
import shlex
import subprocess

from elftools.elf.elffile import ELFFile

PROJECT=Path(__file__).resolve().parents[1]
OUTPUT=PROJECT/'outputs/firmware-runtime/playback-pixels'


def main():
    OUTPUT.mkdir(parents=True,exist_ok=True)
    base='/mnt/'+PROJECT.drive[0].lower()+PROJECT.as_posix()[2:]
    script=f'''set -eu
cd {shlex.quote(base)}
tc=/opt/nx300m-toolchain/usr
rf=/opt/nx300m-lab/stock-1.15
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \
 -std=gnu99 -O2 -Wall -Wextra -Werror -march=armv7-a -mfloat-abi=soft -fno-stack-protector \
 -D_GNU_SOURCE -D_TIME_BITS=32 -D_FILE_OFFSET_BITS=64 -include mod/metol/remote/compat.h \
 -shared -fPIC -nostdlib -Wl,--allow-shlib-undefined -Wl,-soname,playback-pixels.so \
 runtime/playback_pixels.c "$rf/lib/libc-2.13.so" "$rf/lib/libdl.so.2" -lgcc \
 -o outputs/firmware-runtime/playback-pixels/playback-pixels.so
'''
    result=subprocess.run(['wsl.exe','-u','root','-e','sh','-s'],input=script.encode(),capture_output=True)
    (OUTPUT/'build.stdout.log').write_bytes(result.stdout)
    (OUTPUT/'build.stderr.log').write_bytes(result.stderr)
    if result.returncode:
        raise RuntimeError(f'ARM build exit={result.returncode}: '+result.stderr.decode('utf-8','replace'))
    binary=ELFFile(io.BytesIO((OUTPUT/'playback-pixels.so').read_bytes()))
    names={symbol.name for symbol in binary.get_section_by_name('.dynsym').iter_symbols() if symbol['st_shndx']!='SHN_UNDEF'}
    required={'_ZN15CMmDeviceDriver11ResizeImageER4CYccS1_','_ZN15CMmDeviceDriver15ConvertYccToRGBER4CYccj',
              '_Z9resizeOneR4CYccS0_'}
    if binary['e_machine']!='EM_ARM' or not required<=names:
        raise RuntimeError('Missing ARM playback task exports')
    versions=binary.get_section_by_name('.gnu.version_r')
    versions=[a.name for _,aux in versions.iter_versions() for a in aux] if versions else []
    if any(v.startswith('GLIBC_') and tuple(map(int,v[6:].split('.')))>(2,13) for v in versions):
        raise RuntimeError(f'Unexpected stock glibc requirement: {versions}')
    report={'build_exit_code':0,'machine':binary['e_machine'],'versions':versions,'exports':sorted(required),
            'scope':'explicit-preload software CYcc resize and YCC-to-BGRA tasks; progressive semiplanar CbCr 422/420',
            'stock_ui_tested_by_this_builder':False,'hardware_bit_exact':False}
    (OUTPUT/'build-report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
