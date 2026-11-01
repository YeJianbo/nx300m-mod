"""Build the single-slot SmartFilter controller against the ARM/MFW userspace ABI."""
import io
import json
from pathlib import Path
import shlex
import subprocess

from arm_vm import wsl_path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'outputs/deployment/smartfilter-preview'
LIBRARY = OUT/'libnx-smartfilter-lut.so'


def build():
    from elftools.elf.elffile import ELFFile

    OUT.mkdir(parents=True, exist_ok=True)
    script = f'''set -eu
tc=/opt/nx300m-toolchain/usr
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \
 -std=gnu99 -O2 -Wall -Wextra -Werror -march=armv7-a -mfloat-abi=soft \
 -fPIC -ffreestanding -fno-builtin -fno-stack-protector -nostdlib -shared \
 -Wl,-z,defs -Wl,-soname,libnx-smartfilter-lut.so \
 {shlex.quote(wsl_path(ROOT/'runtime/smartfilter_lut_bridge.c'))} \
 {shlex.quote(wsl_path(ROOT/'runtime/smartfilter_ipcs.c'))} \
 -o {shlex.quote(wsl_path(LIBRARY))}
'''
    result = subprocess.run(['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'sh', '-s'],
                            input=script.encode(), capture_output=True)
    (OUT/'bridge-build.stdout.log').write_bytes(result.stdout)
    (OUT/'bridge-build.stderr.log').write_bytes(result.stderr)
    if result.returncode:
        raise RuntimeError(f'Bridge build exit {result.returncode}: {result.stderr.decode(errors="replace")}')
    elf = ELFFile(io.BytesIO(LIBRARY.read_bytes()))
    symbols = list(elf.get_section_by_name('.dynsym').iter_symbols())
    undefined = [s.name for s in symbols if s.name and s['st_shndx'] == 'SHN_UNDEF']
    if elf['e_machine'] != 'EM_ARM' or elf.elfclass != 32 or undefined:
        raise ValueError(f'Unexpected ARM module or runtime dependencies: {undefined}')
    if any(elf.get_section_by_name(n) for n in ('.init_array', '.fini_array', '.ctors')):
        raise ValueError('The controller must not install hooks through constructors')
    report = {'build_exit_code': 0, 'library': str(LIBRARY), 'bytes': LIBRARY.stat().st_size,
              'exports': [s.name for s in symbols if s['st_info']['type'] == 'STT_FUNC' and s.name],
              'undefined_symbols': undefined, 'automatic_installation': False,
              'selection': 'One stock slot, independently selected look and strength cached in RAM',
              'native_ipcs_api_adapter_implemented': True,
              'physical_ipcs_arbitration_verified': False,
              'production_still_hook_changed': False}
    (OUT/'bridge-build-report.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    print(json.dumps(build(), indent=2))
