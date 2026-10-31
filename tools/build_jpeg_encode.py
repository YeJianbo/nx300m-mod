"""Build optional stock-ABI CPU JPEG encoder and bounded standalone ARM probe.

Extracts the exact stock HAL quantization tables. Does not install a producer,
enable the encoder, modify media, or restart the simulator.
"""
from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import shlex
import struct
import subprocess
import sys

os.environ.setdefault('CONDA_PREFIX', sys.prefix)
from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM
from elftools.elf.elffile import ELFFile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'outputs/firmware-runtime/jpeg-encode'
STOCK = Path('//wsl.localhost/Debian/opt/nx300m-lab/stock-1.15')
INDICES = (10, 6, 2)


def wsl_path(path: Path) -> str:
    path = path.resolve()
    return '/mnt/'+path.drive[0].lower()+path.as_posix()[2:]


class StockElf:
    def __init__(self, name: str):
        self.name = name
        self.elf = ELFFile(io.BytesIO((STOCK/name).read_bytes()))

    def read(self, address: int, size: int) -> bytes:
        segment = next(s for s in self.elf.iter_segments()
                       if s['p_type'] == 'PT_LOAD' and s['p_vaddr'] <= address
                       and address+size <= s['p_vaddr']+s['p_filesz'])
        self.elf.stream.seek(segment['p_offset']+address-segment['p_vaddr'])
        return self.elf.stream.read(size)

    def word(self, address: int) -> int:
        return struct.unpack('<I', self.read(address, 4))[0]


def extract_tables(reference_raw: Path | None = None) -> dict:
    sif, udd = StockElf('usr/lib/libsif.so'), StockElf('usr/lib/libudd.so.3.1')
    # PC-relative literals belong to the inspected NX300M 1.15 HAL function.
    # Reject another instruction sequence rather than silently extracting junk.
    expected = {0x2983c: 0xe59f4364, 0x29840: 0xe08f4004,
                0x29a50: 0xe1a02203, 0x29a54: 0xe59f316c,
                0x29a58: 0xe0843003, 0x29a5c: 0xe0823003}
    for address, word in expected.items():
        if sif.word(address) != word:
            raise RuntimeError(f'Unexpected stock HAL instruction at {address:#x}')
    pc_base = (0x29840+8+sif.word(0x29ba8)) & 0xffffffff
    table_base = (pc_base+sif.word(0x29bc8)) & 0xffffffff
    tables, records = [], []
    for quality, index in enumerate(INDICES):
        pointers = struct.unpack('<4I', sif.read(table_base+index*16, 16))
        if not all(pointers[:2]) or any(pointers[2:]):
            raise RuntimeError('Unexpected quant table pointer row')
        values = [list(sif.read(pointer, 64)) for pointer in pointers[:2]]
        if any(not 1 <= value <= 255 for table in values for value in table):
            raise RuntimeError('Unsupported quantization precision')
        tables.append(values)
        records.append({'quality_enum': quality, 'index': index,
                        'pointer_row': hex(table_base+index*16),
                        'component_addresses': [hex(p) for p in pointers[:2]],
                        'natural_order_tables': values})
    from PIL import Image
    metadata = json.loads((ROOT/'outputs/looks/photo-reference.json').read_text(encoding='utf-8'))
    original_path = Path(metadata['external_jpeg']['path'])
    with Image.open(original_path) as image:
        exif = image.getexif()
        identity = {name: str(exif.get(tag, '')).strip() for name, tag in (
            ('make', 271), ('model', 272), ('firmware', 305))}
        if identity != {'make': 'SAMSUNG', 'model': 'NX300M', 'firmware': '1.15'}:
            raise RuntimeError(f'Unexpected reference JPEG identity: {identity}')
        if [image.quantization[i] for i in (0, 1)] != tables[0]:
            raise RuntimeError('Extracted index10 differs from original NX300M JPEG natural-order DQT')
    raw_path = reference_raw.resolve() if reference_raw else Path(metadata['raw']['path'])
    embedded = metadata['raw']['embedded_jpeg']
    with raw_path.open('rb') as stream:
        stream.seek(embedded['offset'])
        embedded_bytes = stream.read(embedded['bytes'])
    if embedded_bytes != (ROOT/'outputs/looks/photo-reference.jpg').read_bytes():
        raise RuntimeError('Embedded JPEG reference differs from original RAW bytes')
    with Image.open(io.BytesIO(embedded_bytes)) as image:
        if [image.quantization[i] for i in (0, 1)] != tables[2]:
            raise RuntimeError('Extracted index2 differs from original SRW embedded natural-order DQT')
    header = ['/* Generated from NX300M stock 1.15 libsif HAL tables; do not edit. */',
              '#ifndef NX300M_STOCK_QUANT_TABLES_H', '#define NX300M_STOCK_QUANT_TABLES_H',
              'static const unsigned int nx_stock_quant_indices[3]={10,6,2};',
              'static const unsigned char nx_stock_quant[3][2][64]={']
    for components in tables:
        header.append('  {')
        for values in components:
            header.append('    {'+','.join(map(str, values))+'},')
        header.append('  },')
    header.extend(['};', '#endif', ''])
    (OUT/'stock_quant_tables.h').write_text('\n'.join(header), encoding='utf-8', newline='\n')
    listing = []
    decoder = Cs(CS_ARCH_ARM, CS_MODE_ARM)
    for elf, start, size, description in (
            (sif, 0x2983c, 12, 'HAL PC-relative base'),
            (sif, 0x29988, 0x78, 'quality enum selects 10/6/2'),
            (sif, 0x29a4c, 0x18, 'index * 16 selects pointer row'),
            (udd, 0xd23e0, 0x10, 'request+0xc dispatches quant table pointers'),
            (udd, 0xd2b4c, 0x90, 'four optional tables; 16 raw u32 per table to registers')):
        listing.append(f'\n## {elf.name}: {description}')
        listing.extend(f'{i.address:08x} {i.mnemonic:8s} {i.op_str}'
                       for i in decoder.disasm(elf.read(start, size), start))
    (OUT/'quant-table-evidence.asm').write_text('\n'.join(listing)+'\n', encoding='utf-8')
    report = {'source': 'NX300M stock 1.15 usr/lib/libsif.so',
              'table_pointer_base': hex(table_base), 'table_bytes': 64,
              'storage': '8x8 natural order; register copy uses little-endian u32 words',
              'tables': records, 'hardware_bit_exact_proven': False,
              'order_evidence': {
                  'original_jpeg': str(original_path), 'exif': identity,
                  'original_jpeg_dqt_matches_index': 10,
                  'raw': str(raw_path), 'embedded_jpeg': embedded,
                  'embedded_bytes_equal_reference': True, 'embedded_dqt_matches_index': 2,
                  'index6_order': 'inferred from shared pointer-row layout and identical register-copy code; no separate quality1 original JPEG matched'}}
    (OUT/'quant-tables.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    jpeg, mm = StockElf('usr/lib/libjpeg.so.8.3.0'), StockElf('usr/lib/libmmutil_jpeg.so.0.0.0')
    listing = []
    for elf, start, size, description in (
            (jpeg, 0x35bc, 0x50, 'jpeg_CreateCompress version and struct-size checks'),
            (jpeg, 0xcd08, 0x30, 'comp_info+0x54; scale_num/denom+0x38/3c'),
            (jpeg, 0xcdbc, 0x48, 'raw_data_in+0xd0; dct_method+0xe8 defaults'),
            (mm, 0x1e38, 0x20, 'stock wrapper uses 0x1b0-byte JPEG80 compressor'),
            (mm, 0x2090, 0x48, 'stock wrapper uses raw_data_in and component stride0x58')):
        listing.append(f'\n## {elf.name}: {description}')
        listing.extend(f'{i.address:08x} {i.mnemonic:8s} {i.op_str}'
                       for i in decoder.disasm(elf.read(start, size), start))
    (OUT/'compress-abi-evidence.asm').write_text('\n'.join(listing)+'\n', encoding='utf-8')
    return report


def inspect_artifact(name: str) -> dict:
    data = (OUT/name).read_bytes()
    elf = ELFFile(io.BytesIO(data))
    dynamic = elf.get_section_by_name('.dynamic')
    needed = [t.needed for t in dynamic.iter_tags() if t.entry.d_tag == 'DT_NEEDED']
    section = elf.get_section_by_name('.gnu.version_r')
    versions = [a.name for _, aux in section.iter_versions() for a in aux] if section else []
    if any(v.startswith('GLIBC_') and tuple(map(int, v[6:].split('.'))) > (2, 13) for v in versions):
        raise RuntimeError(f'{name} requires newer libc: {versions}')
    if elf['e_machine'] != 'EM_ARM' or 'libjpeg.so.8' in needed:
        raise RuntimeError(f'{name}: expected ARM and dynamically opened libjpeg')
    symbols = elf.get_section_by_name('.dynsym')
    if name.endswith('.so') and not all(symbols.get_symbol_by_name(n) for n in (
            'nx300m_jpeg_cpu_encode', '_ZN7DscJfif10JpegEncodeER7sJpegOpb')):
        raise RuntimeError('Missing encoder exports')
    return {'file': name, 'bytes': len(data), 'machine': elf['e_machine'],
            'needed': needed, 'versions': versions}


def verify_jpegs(tables: dict) -> dict:
    from PIL import Image, JpegImagePlugin
    records = []
    for sampling in (422, 420):
        for quality in range(3):
            source = OUT/f'sampling{sampling}-quality{quality}.jpg'
            with Image.open(source) as image:
                image.load()
                if image.size != (34, 18) or JpegImagePlugin.get_sampling(image) != (1 if sampling == 422 else 2):
                    raise RuntimeError(f'{source.name}: incorrect dimensions or sampling')
                expected = tables['tables'][quality]['natural_order_tables']
                if [image.quantization[i] for i in (0, 1)] != expected:
                    raise RuntimeError(f'{source.name}: DQT differs from extracted stock tables')
                if any(high-low < 20 for low, high in image.getextrema()):
                    raise RuntimeError(f'{source.name}: decoded patterned fixture became constant')
                records.append({'file': source.name, 'bytes': source.stat().st_size,
                                'dimensions': list(image.size), 'sampling': sampling,
                                'dqt_matches_stock': True, 'rgb_extrema': image.getextrema()})
    with Image.open(OUT/'explicit-ijg90.jpg') as image:
        image.load()
        if image.size != (34, 18):
            raise RuntimeError('Explicit software quality output decode failed')
    return {'decoder': 'separate host process Pillow decode after ARM encoder exits', 'images': records}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe', action='store_true')
    parser.add_argument('--reference-raw', type=Path, help='Same original RAW recovered at another path; embedded bytes must match the preserved reference')
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    tables = extract_tables(args.reference_raw)
    # Modern toolchain headers redirect strtol to glibc 2.38 C23 ABI under
    # _GNU_SOURCE. This target uses the pre-C23 glibc 2.13 decimal parser.
    (OUT/'stock_libc_compat.h').write_text(
        '#ifndef _GNU_SOURCE\n#define _GNU_SOURCE\n#endif\n#include <features.h>\n'
        '#undef __GLIBC_USE_C23_STRTOL\n#define __GLIBC_USE_C23_STRTOL 0\n', encoding='utf-8')
    script = f'''set -eu
cd {shlex.quote(wsl_path(ROOT))}
tc=/opt/nx300m-toolchain/usr
rf=/opt/nx300m-lab/stock-1.15
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
cc="$tc/bin/arm-linux-gnueabi-gcc"
base="-B$tc/lib/gcc-cross/arm-linux-gnueabi/14/ -std=gnu99 -O2 -Wall -Wextra -march=armv7-a -mfloat-abi=soft -fno-stack-protector -D_TIME_BITS=32 -D_FILE_OFFSET_BITS=64 -Iwork/reference/jpeg8-headers -Iruntime -Ioutputs/firmware-runtime/jpeg-encode -include outputs/firmware-runtime/jpeg-encode/stock_libc_compat.h"
link="-nostdlib -Wl,--allow-shlib-undefined -Wl,-rpath-link,$rf/usr/lib -Wl,-rpath-link,$rf/lib"
out=outputs/firmware-runtime/jpeg-encode
"$cc" $base $link -shared -fPIC -Wl,-soname,jpeg-encode.so runtime/jpeg_cpu_adapter.c \\
 "$rf/lib/libdl.so.2" "$rf/lib/libc-2.13.so" -lgcc -o "$out/jpeg-encode.so"
"$cc" $base $link -no-pie -fno-pic -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 \\
 runtime/jpeg_encode_probe.c runtime/jpeg_cpu_adapter.c work/reference/metol/test_server/filmlab/src/crt0_arm.S \\
 "$rf/lib/libdl.so.2" "$rf/lib/libc-2.13.so" -lgcc -o "$out/jpeg-encode-probe.arm"
'''
    build = subprocess.run(['wsl.exe', '-u', 'root', '-e', 'sh', '-s'], input=script.encode(), capture_output=True)
    (OUT/'build.stdout.log').write_bytes(build.stdout)
    (OUT/'build.stderr.log').write_bytes(build.stderr)
    if build.returncode:
        print(build.stderr.decode('utf-8', 'replace'))
        return build.returncode
    report = {'artifacts': [inspect_artifact(n) for n in ('jpeg-encode.so', 'jpeg-encode-probe.arm')],
              'quant_tables': 'quant-tables.json', 'compress_abi_bytes': 432,
              'compress_abi_fields': {'comp_info': '0x54', 'raw_data_in': '0xd0', 'dct_method': '0xe8',
                                      'do_fancy_downsampling': '0xe0',
                                      'jpeg_component_info_bytes': '0x58', 'evidence': 'compress-abi-evidence.asm'},
              'probe_run': False, 'producer_integration': False,
              'simulator_restarted': False, 'hardware_bit_exact_proven': False}
    if args.probe:
        command = ['/opt/nx300m-lab/stock-1.15/qemu-arm-static', '-cpu', 'cortex-a9',
                   '-L', '/opt/nx300m-lab/stock-1.15', wsl_path(OUT/'jpeg-encode-probe.arm'), wsl_path(OUT)]
        probe = subprocess.run(['wsl.exe', '-u', 'root', '-e', 'env', '-u', 'LD_PRELOAD', '-u', 'LD_AUDIT', *command],
                               capture_output=True, timeout=120)
        (OUT/'probe.stdout.log').write_bytes(probe.stdout)
        (OUT/'probe.stderr.log').write_bytes(probe.stderr)
        report.update(probe_run=True, probe_exit=probe.returncode)
        if probe.returncode == 0:
            report['probe'] = json.loads(probe.stdout)
            report['decoded_outputs'] = verify_jpegs(tables)
        else:
            print(probe.stderr.decode('utf-8', 'replace'))
        (OUT/'build-report.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
        print(json.dumps(report))
        return probe.returncode
    (OUT/'build-report.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
