"""Read exFAT directory allocation for selected paths; never open the volume for writing."""
import argparse
import json
import os
from pathlib import Path
import struct


class ExfatReader:
    def __init__(self, volume):
        self.fd = os.open(volume, os.O_RDONLY | getattr(os, 'O_BINARY', 0))
        self.boot = self.read_at(0, 512)
        if self.boot[3:11] != b'EXFAT   ' or self.boot[510:512] != b'\x55\xaa':
            raise ValueError('Not an exFAT volume boot sector')
        self.sector = 1 << self.boot[108]
        self.cluster = self.sector << self.boot[109]
        self.fat_offset, self.fat_sectors, self.heap_sector, self.count, self.root = struct.unpack_from('<5I', self.boot, 80)
        self.cached_clusters = {}
        if not 512 <= self.sector <= 4096 or not self.sector <= self.cluster <= 32 * 1024 * 1024:
            raise ValueError('Unexpected exFAT geometry')
        if self.boot[110] != 1:
            raise ValueError('This diagnostic supports a single FAT only')

    def close(self):
        os.close(self.fd)

    def read_at(self, offset, size):
        os.lseek(self.fd, offset, os.SEEK_SET)
        data = os.read(self.fd, size)
        if len(data) != size:
            raise ValueError(f'Truncated volume read at {offset}: {len(data)} of {size}')
        return data

    def read_cluster(self, cluster):
        if not 2 <= cluster < self.count + 2:
            raise ValueError(f'Invalid cluster {cluster}')
        if cluster not in self.cached_clusters:
            self.cached_clusters[cluster] = self.read_at((self.heap_sector + (cluster - 2) * (self.cluster // self.sector)) * self.sector, self.cluster)
        return self.cached_clusters[cluster]

    def chain(self, first, length=None, contiguous=False):
        if contiguous:
            if length is None:
                raise ValueError('Contiguous stream length unavailable')
            count = (length + self.cluster - 1) // self.cluster
            if length < 0 or first < 2 or first + count > self.count + 2:
                raise ValueError('Invalid contiguous stream extent')
            return list(range(first, first + count))
        result = []
        current = first
        while current < 0xfffffff8:
            if current in result or len(result) >= 4096 or not 2 <= current < self.count + 2:
                raise ValueError('Invalid or excessive FAT chain')
            result.append(current)
            fat_byte = current * 4
            sector = self.read_at((self.fat_offset + fat_byte // self.sector) * self.sector, self.sector)
            current = struct.unpack_from('<I', sector, fat_byte % self.sector)[0]
        return result

    def directory(self, entry=None):
        chain = self.chain(self.root) if entry is None else self.chain(entry['first_cluster'], entry['data_length'], entry['contiguous'])
        data = b''.join(self.read_cluster(c) for c in chain)
        entries = []
        offset = 0
        while offset + 32 <= len(data):
            kind = data[offset]
            if kind == 0:
                break
            if kind != 0x85:
                offset += 32
                continue
            total = (data[offset + 1] + 1) * 32
            group = data[offset:offset + total]
            if len(group) != total or len(group) < 64 or group[32] != 0xc0:
                raise ValueError('Malformed file entry set')
            checksum = 0
            for index, value in enumerate(group):
                if index not in (2, 3):
                    checksum = (((checksum & 1) << 15) + (checksum >> 1) + value) & 0xffff
            name_bytes = b''.join(group[i+2:i+32] for i in range(64, total, 32) if group[i] == 0xc1)
            name = name_bytes[:group[35] * 2].decode('utf-16-le')
            entries.append({'name': name, 'directory': bool(struct.unpack_from('<H', group, 4)[0] & 0x10),
                            'first_cluster': struct.unpack_from('<I', group, 52)[0],
                            'data_length': struct.unpack_from('<Q', group, 56)[0],
                            'valid_length': struct.unpack_from('<Q', group, 40)[0],
                            'contiguous': bool(group[33] & 2),
                            'entry_checksum_valid': checksum == struct.unpack_from('<H', group, 2)[0],
                            'entry_directory_cluster': chain[offset // self.cluster],
                            'entry_byte_in_cluster': offset % self.cluster})
            offset += total
        return entries

    def lookup(self, path):
        entries = self.directory()
        parts = path.replace('\\', '/').strip('/').split('/')
        for index, name in enumerate(parts):
            entry = next((item for item in entries if item['name'].casefold() == name.casefold()), None)
            if entry is None:
                return None
            if index < len(parts) - 1:
                if not entry['directory']:
                    raise ValueError(f'Non-directory ancestor in {path}')
                entries = self.directory(entry)
        return entry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('volume', help='Read-only volume, e.g. Windows raw volume path')
    parser.add_argument('paths', nargs='+')
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if str(output).casefold().startswith(args.volume.replace('\\\\.\\', '').casefold()):
        parser.error('Evidence must be saved off the examined volume')
    output.mkdir(parents=True, exist_ok=True)
    reader = ExfatReader(args.volume)
    try:
        entries = {path: reader.lookup(path) for path in args.paths}
        report = {'volume_open_mode': 'read-only', 'sector_bytes': reader.sector,
                  'cluster_bytes': reader.cluster, 'cluster_count': reader.count,
                  'cluster_heap_sector': reader.heap_sector, 'root_cluster': reader.root,
                  'selected_entries': entries}
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        (output / 'volume-boot.bin').write_bytes(reader.read_at(0, reader.sector * 24))
        for cluster, data in reader.cached_clusters.items():
            (output / f'directory-cluster-{cluster}.bin').write_bytes(data)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        reader.close()


if __name__ == '__main__':
    main()
