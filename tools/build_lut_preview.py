"""Build a separate, RAM-only DisplayAdjust observer; leave still LUT unchanged."""
import hashlib
import io
import json
from pathlib import Path
import shlex
import shutil
import struct
import subprocess

from elftools.elf.elffile import ELFFile
from build_lut_live_loader import elf_record, bytes_at, wsl

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/deployment/lut-preview'
EXECUTE = '_ZN26CLiveviewNodeDisplayAdjust7ExecuteEP9CCapImagePvj'
VTABLE = '_ZTV26CLiveviewNodeDisplayAdjust'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    config = json.loads((ROOT / 'project.local.json').read_text(encoding='utf-8'))
    stock = ROOT / config['rootfs']
    cap, _, cap_info = elf_record(stock / 'usr/lib/libcapture-fw-slpcam-nx300.so',
                                  {'execute': EXECUTE, 'vtable': VTABLE})
    libc, _, libc_info = elf_record(stock / 'lib/libc-2.13.so', {'syscall': 'syscall'})
    _, _, app_info = elf_record(stock / 'usr/bin/di-camera-app-nx300', {})
    slot = cap_info['vtable'] + 28
    if slot != 0x39c8ac or cap_info['execute'] != 0x278fc0:
        raise ValueError('Unexpected NX300M DisplayAdjust ABI')
    rels = [r for sec in cap.iter_sections() if sec['sh_type'] == 'SHT_REL'
            for r in sec.iter_relocations() if r['r_offset'] == slot]
    if (len(rels) != 1 or rels[0]['r_info_type'] != 2 or
            cap.get_section_by_name('.dynsym').get_symbol(rels[0]['r_info_sym']).name != EXECUTE or
            bytes_at(cap, slot, 4) != b'\0' * 4):
        raise ValueError('DisplayAdjust slot relocation differs')
    symbol = libc.get_section_by_name('.dynsym').get_symbol_by_name('syscall')[0]
    code = bytes_at(libc, symbol['st_value'], symbol['st_size'])
    svc = next(symbol['st_value'] + i for i in range(0, len(code), 4)
               if code[i:i+4] == b'\0\0\0\xef')
    header = ['/* Generated from original NX300M 1.15 ELF. */']
    for key, rec in [('cap', cap_info), ('libc', libc_info), ('app', app_info)]:
        header += [f'#define STOCK_{key.upper()}_SIZE {rec["bytes"]}U',
                   f'#define STOCK_{key.upper()}_SHA "{rec["sha256"]}"']
    header += [f'#define STOCK_PREVIEW_SLOT {slot}U',
               f'#define STOCK_PREVIEW_EXECUTE {cap_info["execute"]}U', f'#define STOCK_SVC {svc}U']
    for key, elf, address in [('preview_execute', cap, cap_info['execute']), ('svc', libc, svc)]:
        header.append(f'static const unsigned char stock_{key}[32] = {{' +
                      ','.join(str(x) for x in bytes_at(elf, address, 32)) + '};')
    digest = hashlib.sha256()
    for name in ('lut_preview_blob.c', 'lut_preview_blob.h', 'lut_preview_blob.ld'):
        digest.update((ROOT / 'runtime' / name).read_bytes())
    build = int.from_bytes(digest.digest()[:4], 'little') or 1
    header += [f'#define NX_PREVIEW_BUILD {build}U']
    (OUT / 'lut_preview_stock.h').write_text('\n'.join(header) + '\n', encoding='ascii', newline='\n')
    elf_path = OUT / 'lut_preview_blob.elf'
    subprocess.run([shutil.which('clang'), '--target=arm-linux-gnueabi', '-march=armv7-a',
                    '-marm', '-mfloat-abi=soft', '-std=c99', '-O2', '-Wall', '-Wextra', '-Werror',
                    '-ffreestanding', '-fpie', '-fvisibility=hidden', '-fno-builtin',
                    '-fno-stack-protector', '-fno-unwind-tables', '-fno-asynchronous-unwind-tables',
                    '-nostdlib', '-static', '-fuse-ld=lld', '-Wl,--no-undefined', '-Wl,--build-id=none',
                    f'-Wl,--defsym=nx_preview_build={build}',
                    '-Wl,-T,' + str(ROOT / 'runtime/lut_preview_blob.ld'),
                    str(ROOT / 'runtime/lut_preview_blob.c'), '-o', str(elf_path)], check=True)
    blob_elf = ELFFile(io.BytesIO(elf_path.read_bytes()))
    if any(s['sh_type'] in ('SHT_REL', 'SHT_RELA', 'SHT_DYNAMIC') for s in blob_elf.iter_sections()):
        raise ValueError('Preview blob must be freestanding without relocations')
    words = struct.unpack('<16I', blob_elf.get_section_by_name('.header').data())
    symbols = {s.name: s for s in blob_elf.get_section_by_name('.symtab').iter_symbols()}
    if words[8] != symbols['nx_preview_config']['st_size'] or words[10] != symbols['nx_preview_stats']['st_size']:
        raise ValueError('Preview config/stats ABI differs')
    binary = bytearray(words[2])
    for sec in blob_elf.iter_sections():
        if sec['sh_flags'] & 2 and sec['sh_type'] != 'SHT_NOBITS':
            start = int(sec['sh_addr'])
            binary[start:start+sec['sh_size']] = sec.data()
    (OUT / 'lut_preview_blob.bin').write_bytes(binary)
    script = f'''set -eu
tc=/opt/nx300m-toolchain/usr
rf=/opt/nx300m-lab/stock-1.15
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \
 -std=gnu99 -O2 -Wall -Wextra -Werror -Wno-unused-function -march=armv7-a -mfloat-abi=soft \
 -D_TIME_BITS=32 -D_FILE_OFFSET_BITS=64 -fno-stack-protector -fno-pic -no-pie \
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 \
 -Wl,-z,defs -Wl,-rpath-link,"$rf/lib" -I{shlex.quote(wsl(OUT))} \
 {shlex.quote(wsl(ROOT / 'runtime/lut_preview_loader.c'))} \
 {shlex.quote(wsl(ROOT / 'work/reference/metol/test_server/filmlab/src/crt0_arm.S'))} \
 "$rf/lib/libc-2.13.so" -lgcc -o {shlex.quote(wsl(OUT / 'lut_preview_loader.arm'))}
'''
    result = subprocess.run(['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'sh', '-s'],
                            input=script.encode(), capture_output=True)
    (OUT / 'build.stdout.log').write_bytes(result.stdout)
    (OUT / 'build.stderr.log').write_bytes(result.stderr)
    if result.returncode:
        raise RuntimeError(f'Preview loader build exit {result.returncode}: {result.stderr.decode(errors="replace")}')
    built = ELFFile(io.BytesIO((OUT / 'lut_preview_loader.arm').read_bytes()))
    versions = built.get_section_by_name('.gnu.version_r')
    required = [aux.name for _, items in versions.iter_versions() for aux in items] if versions else []
    if any(v.startswith('GLIBC_') and tuple(map(int, v[6:].split('.'))) > (2, 13) for v in required):
        raise ValueError('New glibc ABI leaked into preview loader')
    report = {'build_exit_code': 0, 'build': build, 'blob_bytes': len(binary),
              'stock': {'capture': cap_info, 'libc': libc_info, 'app': app_info},
              'slot': slot, 'scope': 'DisplayAdjust passthrough and RAM metadata observation',
              'pixel_processing': False, 'isp_calls': False, 'still_config_abi_changed': False,
              'required_versions': required}
    (OUT / 'build-report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
