"""Repack only platform.img into a local SLP candidate; never invoke an updater."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import zlib

PROJECT = Path(__file__).resolve().parents[1]
STOCK = PROJECT / 'work/stock-1.15/nx300m.bin'
OUT = PROJECT / 'outputs/deployment/firmware-candidate'
NAMES = ('vImage', 'D4_IPL.bin', 'D4_PNLBL.bin', 'uImage', 'platform.img')
MAGICS = (0xffffffff, 0x7fffffff, 0x65ffffff, 0x543fffff, 0x4321ffff, 0x32100fff)


def crc(stream, length):
    value = 0
    while length:
        chunk = stream.read(min(length, 1024 * 1024))
        if not chunk:
            raise ValueError('Truncated partition')
        value = zlib.crc32(chunk, value)
        length -= len(chunk)
    return value ^ 0xffffffff


def layout(path):
    """Check offsets and exact EOF as well as the stock parser's CRC rules."""
    size = path.stat().st_size
    with path.open('rb') as stream:
        header = stream.read(144)
        if len(header) != 144:
            raise ValueError('Truncated SLP header')
        magic, version, model, date, pcache, tail, reserved = struct.unpack_from('<4s8s16s16sbI15s', header)
        if magic != b'SLP\0' or model.rstrip(b'\0') != b'NX300M' or pcache != 1:
            raise ValueError('Expected NX300M SLP with pcache')
        parts, end = [], 144
        for i, name in enumerate(NAMES):
            length, expected, offset, marker = struct.unpack_from('<4I', header, 64 + 16 * i)
            if offset != end or not length or marker != MAGICS[i] or offset + length > size:
                raise ValueError(f'Unexpected partition layout: {name}')
            stream.seek(offset)
            if crc(stream, length) != expected:
                raise ValueError(f'Partition CRC mismatch: {name}')
            parts.append({'name': name, 'offset': offset, 'length': length, 'crc32': expected, 'magic': marker})
            end = offset + length
        if tail != end or tail + 16 > size:
            raise ValueError('pcache header offset mismatch')
        stream.seek(tail)
        length, expected, offset, marker = struct.unpack('<4I', stream.read(16))
        if offset != tail + 16 or offset + length != size or marker != MAGICS[5]:
            raise ValueError('pcache bounds or exact EOF mismatch')
        if crc(stream, length) != expected:
            raise ValueError('pcache CRC mismatch')
        parts.append({'name': 'pcache.list', 'offset': offset, 'length': length, 'crc32': expected, 'magic': marker})
    return {'size': size, 'header': header, 'version': version.rstrip(b'\0').decode('ascii'),
            'model': model.rstrip(b'\0').decode('ascii'), 'parts': parts}


def copy_part(source, target, offset, length):
    source.seek(offset)
    while length:
        block = source.read(min(length, 1024 * 1024))
        if not block:
            raise ValueError('Truncated source partition')
        target.write(block)
        length -= len(block)


def build(platform, output):
    provenance = json.loads((STOCK.parent / 'source.json').read_text(encoding='utf-8'))
    with STOCK.open('rb') as stream:
        identity = hashlib.file_digest(stream, 'sha256').hexdigest()
    if identity != provenance['local_binary_sha256']:
        raise ValueError('Original firmware identity differs from verified source.json')
    original = layout(STOCK)
    if original['version'] != '1.15':
        raise ValueError('This builder targets verified stock 1.15')
    if output.resolve() in (STOCK.resolve(), platform.resolve()):
        raise ValueError('Output must be separate from both inputs')
    length = platform.stat().st_size
    with platform.open('rb') as stream:
        if not length or length % 131072 or stream.read(4) != b'UBI#':
            raise ValueError('Expected a complete 128-KiB-PEB UBI container')
        stream.seek(0)
        checksum = crc(stream, length)
    header = bytearray(original['header'])
    image = original['parts'][4]
    tail = image['offset'] + length
    if tail + 16 + original['parts'][5]['length'] > 0xffffffff:
        raise ValueError('SLP offsets exceed the 32-bit format')
    struct.pack_into('<I', header, 45, tail)
    struct.pack_into('<II', header, 128, length, checksum)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + '.partial')
    with STOCK.open('rb') as source, temporary.open('wb') as target:
        target.write(header)
        for part in original['parts'][:4]:
            copy_part(source, target, part['offset'], part['length'])
        with platform.open('rb') as source_platform:
            shutil.copyfileobj(source_platform, target, 1024 * 1024)
        cache = original['parts'][5]
        target.write(struct.pack('<4I', cache['length'], cache['crc32'], tail + 16, cache['magic']))
        copy_part(source, target, cache['offset'], cache['length'])
    candidate = layout(temporary)
    unchanged = []
    with STOCK.open('rb') as source, temporary.open('rb') as target:
        for before, after in zip(original['parts'], candidate['parts']):
            if before['name'] == 'platform.img':
                continue
            if before['length'] != after['length']:
                raise ValueError('An unrelated partition changed length')
            source.seek(before['offset']); target.seek(after['offset'])
            remaining = before['length']
            while remaining:
                count = min(remaining, 1024 * 1024)
                if source.read(count) != target.read(count):
                    raise ValueError('Unrelated partition changed: ' + before['name'])
                remaining -= count
            unchanged.append(before['name'])
        target.seek(candidate['parts'][4]['offset'])
        with platform.open('rb') as expected:
            remaining = length
            while remaining:
                count = min(remaining, 1024 * 1024)
                if target.read(count) != expected.read(count):
                    raise ValueError('Embedded platform differs from input')
                remaining -= count
    temporary.replace(output)
    report = {k: v for k, v in candidate.items() if k != 'header'}
    report.update({'output': str(output), 'platform_input': str(platform), 'unchanged_partitions': unchanged,
                   'platform_byte_equal': True, 'version_and_date_preserved': header[:44] == original['header'][:44],
                   'scope': 'SLP bounds, magic, CRC and byte-preservation checks; not upgrader or camera acceptance',
                   'updater_executed': False, 'camera_connected': False, 'release_ready': False})
    output.with_name('build-report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--platform', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=OUT / 'nx300m-mod-candidate.slp')
    args = parser.parse_args()
    report = build(args.platform.resolve(), args.output.resolve())
    print(json.dumps({k: report[k] for k in ('output', 'size', 'unchanged_partitions', 'platform_byte_equal')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
