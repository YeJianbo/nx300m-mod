"""Verify and extract the official NX500 1.12 reference, without executing it."""
import hashlib
import json
from pathlib import Path
import shutil
import struct
import zipfile
import zlib

PROJECT = Path(__file__).resolve().parents[1]


def main():
    archive_path = PROJECT / 'work/downloads/NX500_FW_v1.12.samsung.zip'
    source = json.loads(archive_path.with_name(archive_path.name + '.source.json').read_text())
    prefix = (PROJECT / 'work/downloads/nx500-range.bin').read_bytes()
    with archive_path.open('rb') as stream:
        if stream.read(len(prefix)) != prefix:
            raise ValueError('ZIP differs from the official range response')
    output = PROJECT / 'work/reference/nx500-stock-1.12'
    output.mkdir(parents=True, exist_ok=True)
    binary = output / 'NX500.bin'
    temporary = binary.with_suffix('.partial')
    with zipfile.ZipFile(archive_path) as archive:
        entry = archive.getinfo('NX500.bin')
        with archive.open(entry) as stream, temporary.open('wb') as target:
            shutil.copyfileobj(stream, target)
        if temporary.stat().st_size != entry.file_size:
            raise ValueError('Firmware size mismatch')
    with temporary.open('rb') as stream:
        header = stream.read(64)
        magic, version, model, date, count, cache, reserved = struct.unpack('<4s8s16s16sBI15s', header)
        if magic != b'SLP\0' or model.rstrip(b'\0') != b'NX500' or version.rstrip(b'\0') != b'1.12':
            raise ValueError('Not NX500 1.12 firmware')
        if not 2 <= count <= 32:
            raise ValueError('Expected SLP v2 partition table')
        parts = []
        for index in range(count):
            length, crc, offset, marker, extra = struct.unpack('<IIII8s', stream.read(24))
            if offset < 64 + count * 24 or offset + length > entry.file_size:
                raise ValueError(f'Partition {index} outside firmware payload')
            parts.append({'index': index, 'offset': offset, 'length': length,
                          'header_crc32': f'{crc:08x}', 'marker': f'{marker:08x}',
                          'extra_hex': extra.hex()})
        for part in parts:
            stream.seek(part['offset'])
            target = output / f'partition-{part["index"]:02d}.bin'
            pending = target.with_suffix('.partial')
            remaining, crc = part['length'], 0
            digest = hashlib.sha256()
            with pending.open('wb') as destination:
                while remaining:
                    chunk = stream.read(min(1024 * 1024, remaining))
                    if not chunk: raise ValueError('Truncated partition')
                    destination.write(chunk)
                    digest.update(chunk)
                    crc = zlib.crc32(chunk, crc)
                    remaining -= len(chunk)
            part.update(sha256=digest.hexdigest(), file=target.name,
                        standard_crc32=f'{crc:08x}', complemented_crc32=f'{crc ^ 0xffffffff:08x}')
            part['crc_verified'] = part['complemented_crc32'] == part['header_crc32']
            if not part['crc_verified']:
                raise ValueError(f'Partition {part["index"]} CRC mismatch')
            pending.replace(target)
        stream.seek(0)
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    temporary.replace(binary)
    report = {'source': source, 'model': 'NX500', 'version': '1.12',
              'zip_prefix_matches_official': True, 'zip_entry_crc_verified': True,
              'binary_sha256': digest, 'partitions': parts,
              'scope': 'Read-only reference extraction; no execution or installation. Each partition is checked with complemented CRC32.'}
    (output / 'source.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
