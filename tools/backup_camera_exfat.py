"""Preserve exFAT metadata and allocated/referenced clusters into a local sparse image."""
import argparse
import ctypes
import hashlib
import json
import msvcrt
import os
from pathlib import Path
import struct

from inspect_camera_exfat import ExfatReader


def extend_sparse_image(stream, volume_size):
    """Extend an NTFS sparse file without the CRT's zero-filling truncate."""
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.DeviceIoControl.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p,
                                      ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32,
                                      ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
    kernel.DeviceIoControl.restype = ctypes.c_int
    kernel.SetFilePointerEx.argtypes = [ctypes.c_void_p, ctypes.c_int64,
                                       ctypes.POINTER(ctypes.c_int64), ctypes.c_uint32]
    kernel.SetFilePointerEx.restype = ctypes.c_int
    kernel.SetEndOfFile.argtypes = [ctypes.c_void_p]
    kernel.SetEndOfFile.restype = ctypes.c_int
    handle = msvcrt.get_osfhandle(stream.fileno())
    returned = ctypes.c_uint32()
    if not kernel.DeviceIoControl(handle, 0x900c4, None, 0, None, 0,
                                  ctypes.byref(returned), None):
        raise ctypes.WinError(ctypes.get_last_error())
    if not kernel.SetFilePointerEx(handle, volume_size, None, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    if not kernel.SetEndOfFile(handle):
        raise ctypes.WinError(ctypes.get_last_error())
    stream.seek(0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('volume')
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if str(output).casefold().startswith(args.volume.replace('\\\\.\\', '').casefold()):
        parser.error('Backup must be saved off the examined volume')
    output.mkdir(parents=True, exist_ok=True)
    image = output / 'allocated-volume.img'
    if image.exists():
        parser.error('Preserving existing backup; choose a new output directory')
    reader = ExfatReader(args.volume)
    report = {'status': 'incomplete', 'volume_open_mode': 'read-only',
              'image': str(image), 'scope': 'metadata and allocated/referenced clusters; free space omitted',
              'crosslinked_clusters': {}, 'directory_errors': []}
    try:
        volume_size = struct.unpack_from('<Q', reader.boot, 72)[0] * reader.sector
        root_data = b''.join(reader.read_cluster(c) for c in reader.chain(reader.root))
        bitmap_entry = next(i for i in range(0, len(root_data), 32) if root_data[i] == 0x81)
        first = struct.unpack_from('<I', root_data, bitmap_entry + 20)[0]
        bitmap_size = struct.unpack_from('<Q', root_data, bitmap_entry + 24)[0]
        bitmap_chain = reader.chain(first)
        bitmap = b''.join(reader.read_cluster(c) for c in bitmap_chain)[:bitmap_size]
        if len(bitmap) * 8 < reader.count:
            raise ValueError('Allocation bitmap is too short')
        selected = {index + 2 for index in range(reader.count) if bitmap[index // 8] & (1 << (index % 8))}
        allocated_count = len(selected)
        owners = {}
        visited = set()

        def walk(entry, path):
            chain = reader.chain(reader.root) if entry is None else reader.chain(entry['first_cluster'], entry['data_length'], entry['contiguous'])
            selected.update(chain)
            for cluster in chain:
                owners.setdefault(cluster, []).append(path)
            if entry is not None and not entry['directory']:
                return
            if not chain or chain[0] in visited:
                return
            visited.add(chain[0])
            for child in reader.directory(entry):
                child_path = path.rstrip('/') + '/' + child['name']
                if not child['data_length']:
                    continue
                try:
                    walk(child, child_path)
                except (ValueError, OSError) as exc:
                    report['directory_errors'].append({'path': child_path, 'error': str(exc)})

        walk(None, '/')
        report['crosslinked_clusters'] = {str(c): paths for c, paths in owners.items() if len(paths) > 1}
        report.update(logical_volume_bytes=volume_size, allocated_clusters=allocated_count,
                      captured_clusters=len(selected), referenced_but_bitmap_free=len(selected) - allocated_count,
                      cluster_bytes=reader.cluster, metadata_prefix_bytes=reader.heap_sector * reader.sector)
        digest = hashlib.sha256()
        ranges = []
        with image.open('xb') as stream:
            extend_sparse_image(stream, volume_size)
            prefix = reader.read_at(0, reader.heap_sector * reader.sector)
            stream.seek(0)
            stream.write(prefix)
            digest.update(prefix)
            ranges.append([0, len(prefix)])
            ordered = sorted(selected)
            runs = []
            for cluster in ordered:
                if runs and runs[-1][0] + runs[-1][1] == cluster:
                    runs[-1][1] += 1
                else:
                    runs.append([cluster, 1])
            captured = len(prefix)
            for first_cluster, count in runs:
                offset = (reader.heap_sector * reader.sector) + (first_cluster - 2) * reader.cluster
                size = count * reader.cluster
                stream.seek(offset)
                remaining = size
                position = offset
                while remaining:
                    amount = min(remaining, 4 * 1024 * 1024)
                    data = reader.read_at(position, amount)
                    stream.write(data)
                    digest.update(data)
                    remaining -= amount
                    position += amount
                ranges.append([offset, size])
                captured += size
            stream.flush()
            os.fsync(stream.fileno())
        # Compare the local image's captured ranges without rereading or modifying the card.
        local_digest = hashlib.sha256()
        with image.open('rb') as stream:
            for offset, length in ranges:
                stream.seek(offset)
                remaining = length
                while remaining:
                    data = stream.read(min(remaining, 4 * 1024 * 1024))
                    if not data:
                        raise ValueError('Truncated backup image')
                    local_digest.update(data)
                    remaining -= len(data)
        if local_digest.digest() != digest.digest():
            raise ValueError('Backup readback differs from captured volume data')
        changed = []
        for cluster, initial in reader.cached_clusters.items():
            offset = reader.heap_sector * reader.sector + (cluster - 2) * reader.cluster
            if reader.read_at(offset, reader.cluster) != initial:
                changed.append(cluster)
        prefix_stable = reader.read_at(0, len(prefix)) == prefix
        report.update(status='captured', captured_bytes=captured, captured_ranges=ranges,
                      captured_ranges_sha256=digest.hexdigest(), local_image_readback_verified=True,
                      metadata_stable_during_capture=not changed and prefix_stable,
                      changed_metadata_clusters=changed, sparse_image=True)
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        reader.close()
        (output / 'backup-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({k: v for k, v in report.items() if k not in ('captured_ranges', 'crosslinked_clusters')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
