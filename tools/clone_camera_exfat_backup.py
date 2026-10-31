"""Create a local sparse working copy of captured exFAT ranges, never a device."""
import argparse
import hashlib
import json
import os
from pathlib import Path

from backup_camera_exfat import extend_sparse_image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('backup_dir', type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args()
    backup = args.backup_dir.resolve()
    output = args.output_dir.resolve()
    original = backup / 'allocated-volume.img'
    source = json.loads((backup / 'backup-report.json').read_text(encoding='utf-8'))
    if (source['status'] != 'captured' or not source['local_image_readback_verified']
            or not source['metadata_stable_during_capture'] or not original.is_file()):
        parser.error('A verified, stable local backup is required')
    output.mkdir(parents=True, exist_ok=True)
    image = output / 'working-volume.img'
    digest = hashlib.sha256()
    with original.open('rb') as src, image.open('xb') as dst:
        extend_sparse_image(dst, source['logical_volume_bytes'])
        for start, length in source['captured_ranges']:
            if start < 0 or length < 0 or start + length > source['logical_volume_bytes']:
                raise ValueError('Invalid captured range')
            src.seek(start)
            dst.seek(start)
            remaining = length
            while remaining:
                data = src.read(min(remaining, 4 * 1024 * 1024))
                if not data:
                    raise ValueError('Truncated source image')
                dst.write(data)
                digest.update(data)
                remaining -= len(data)
        dst.flush()
        os.fsync(dst.fileno())
    if digest.hexdigest() != source['captured_ranges_sha256']:
        raise ValueError('Backup no longer matches its capture report')
    readback = hashlib.sha256()
    with image.open('rb') as dst:
        for start, length in source['captured_ranges']:
            dst.seek(start)
            remaining = length
            while remaining:
                data = dst.read(min(remaining, 4 * 1024 * 1024))
                if not data:
                    raise ValueError('Truncated working image')
                readback.update(data)
                remaining -= len(data)
    if readback.digest() != digest.digest():
        raise ValueError('Working image readback differs')
    report = {'status': 'cloned', 'source': str(original), 'image': str(image),
              'scope': source['scope'], 'physical_camera_accessed': False,
              'captured_bytes': source['captured_bytes'],
              'captured_ranges_sha256': digest.hexdigest(),
              'local_image_readback_verified': True,
              'intended_use': 'Offline repair experiment only; do not write this image to a card'}
    (output / 'clone-report.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
