"""Build the opt-in NX300M 1.15 no-lens 2D CS guard from verified stock bytes."""
import io
import json
from pathlib import Path
import shutil
import struct
import subprocess

from elftools.elf.elffile import ELFFile
from inspect_recipe_tables import all_symbols, read_symbol

PROJECT = Path(__file__).resolve().parents[1]
OUT = PROJECT / 'outputs/controls/manual-lens'
STOCK = Path('//wsl.localhost/Debian/opt/nx300m-lab/stock-1.15')


def main():
    compiler = shutil.which('clang')
    if not compiler:
        raise SystemExit('clang is required')
    OUT.mkdir(parents=True, exist_ok=True)
    specs = (
        ('INIT', 'usr/lib/libiq-fw-slpcam.so', '_ZN15CPreProcessorD411InitCsParamEv',
         0x34316c, 3192, 0x343534, (0xe5d63008, 0xe3530000, 0x0affffa3)),
        ('UPDATE', 'usr/lib/libiq-fw-slpcam.so', '_ZN15CPreProcessorD413UpdateCsParamEb',
         0x343e74, 1940, 0x344010, (0xe5d93008, 0xe3530000, 0x0affffde)),
        ('READY', 'usr/lib/liblens-fw-prod.so', '_ZN14CLensOperation13IsModuleReadyEv',
         0x2b250, 64, None, None),
        ('OPERATION', 'usr/lib/liblens-fw-prod.so', '_ZN14CLensOperation11m_pInstanceE',
         0x88164, 4, None, None),
        ('GAIN', 'usr/lib/libiq-fw-slpcam.so', '_Z22IQ_AE_GetCaptureCSGainv',
         0x324684, 16, None, None),
    )
    header, evidence = [], []
    for label, relative, name, address, size, site, words in specs:
        source = PROJECT / 'outputs/deployment/stock' / relative
        if not source.is_file():
            source = STOCK / relative
        elf = ELFFile(io.BytesIO(source.read_bytes()))
        symbol = all_symbols(elf)[name]
        if (symbol['st_value'], symbol['st_size']) != (address, size):
            raise ValueError(f'Unreviewed stock function: {name}')
        if label == 'OPERATION':
            if elf.get_section(symbol['st_shndx'])['sh_type'] != 'SHT_NOBITS':
                raise ValueError('Expected original singleton BSS slot')
            data = None
        else:
            data, _, _ = read_symbol(elf, symbol)
        if label == 'READY':
            if struct.unpack_from('<I', data)[0] != 0xe5902070:
                raise ValueError('Unreviewed electronic lens-state offset')
            table_address, table_size = 0x6fb54, 23 * 4
            segment = next(seg for seg in elf.iter_segments()
                           if seg['p_type'] == 'PT_LOAD' and seg['p_vaddr'] <= table_address
                           and table_address + table_size <= seg['p_vaddr'] + seg['p_filesz'])
            start = table_address - segment['p_vaddr']
            table = segment.data()[start:start + table_size]
            values = struct.unpack('<23I', table)
            if [i - 3 for i, value in enumerate(values) if value == 0x20000019] != [12, 19]:
                raise ValueError('Unreviewed electronic ready state table')
            header.extend((f'#define CS_STATE_TABLE_ADDRESS 0x{table_address:x}u\n',
                           'static const unsigned char cs_state_table_stock[]={' +
                           ','.join(f'0x{byte:02x}' for byte in table) + '};\n'))
        if label == 'INIT':
            result_offset = 0x3434b4 - address
            if struct.unpack_from('<II', data, result_offset) != (0xe1a05000, 0xe28d007c):
                raise ValueError('Unreviewed original LUT initialization result site')
            header.append(f'#define CS_RESULT_OFFSET 0x{result_offset:x}u\n')
        if site is not None and struct.unpack_from('<III', data, site-address) != words:
            raise ValueError(f'Unreviewed 2D ready gate: {name}')
        header.extend((f'#define CS_{label}_SYMBOL "{name}"\n',
                       f'#define CS_{label}_ADDRESS 0x{address:x}u\n'))
        if data is not None:
            header.append(f'static const unsigned char cs_{label.lower()}_stock[]={{' +
                          ','.join(f'0x{byte:02x}' for byte in data) + '};\n')
        if site is not None:
            header.append(f'#define CS_{label}_OFFSET 0x{site-address:x}u\n')
        evidence.append({'symbol': name, 'address': hex(address), 'size': size,
                         'source': relative, 'gate': hex(site) if site is not None else None,
                         'verified_words': [hex(word) for word in words] if words else None})
    (OUT / 'manual_lens_cs_stock.h').write_text(''.join(header), encoding='ascii')
    target = OUT / 'manual-lens-cs.so'
    subprocess.run([
        compiler, '--target=arm-linux-gnueabi', '-march=armv7-a', '-mfloat-abi=soft',
        '-std=c99', '-O2', '-Wall', '-Wextra', '-Werror', '-fPIC', '-ffreestanding',
        '-fno-builtin', '-fno-stack-protector', '-nostdlib', '-shared', '-fuse-ld=lld',
        '-Wl,-z,nocombreloc', '-I' + str(OUT), 'runtime/manual_lens_cs.c', '-o', str(target),
    ], cwd=PROJECT, check=True)
    (OUT / 'cs-guard-evidence.json').write_text(json.dumps({
        'module': str(target), 'bytes': target.stat().st_size,
        'scope': '2D stock ready gates and default rebuild on existing PP update thread',
        'lens_policy': 'electronic ready and IQ ready; mechanical GPIO closure alone is not recognition',
        'cache_policy': 'reuse default for unchanged PP settings/gain; pending DMA defers; cache only current LUT-init return 0',
        'result_gate_address': '0x3434b4', 'full_hardware_completion_verified': False,
        'rebuild_modes': [0, 1, 2, 3],
        'enabled_by': 'NX300M_MANUAL_LENS_CS=1 at process startup',
        'functions': evidence, 'physical_color_verified': False,
        'writes_calibration_storage': False, 'deployed': False,
    }, indent=2) + '\n', encoding='utf-8')
    print(f'Built {target} ({target.stat().st_size} bytes)')


if __name__ == '__main__':
    main()
