"""Find local NX300(M) RAW/JPEG candidates and extract original SRW previews.

Only metadata is read during inventory. Originals and Lightroom catalogs are
never changed; a candidate match is not a measured camera-color calibration.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime
import io
import json
import math
from pathlib import Path
import re
import struct
import subprocess

from PIL import Image

PROJECT = Path(__file__).resolve().parents[1]
OUTPUT = PROJECT / 'outputs/looks'
FIELDS = {271: 'make', 272: 'model', 305: 'software', 306: 'modified',
          36867: 'captured', 33434: 'exposure', 33437: 'aperture', 34855: 'iso',
          37386: 'focal_length', 40962: 'width', 40963: 'height'}


def srw_metadata(path):
    path = Path(path)
    length = path.stat().st_size
    result = {'path': str(path)}
    previews = []
    with path.open('rb') as stream:
        header = stream.read(8)
        if header[:4] not in (b'II*\0', b'MM\0*'):
            raise ValueError('Expected TIFF-based SRW')
        endian = '<' if header[:2] == b'II' else '>'
        queue = [struct.unpack(endian+'I', header[4:])[0]]
        visited = set()
        while queue:
            offset = queue.pop(0)
            if not offset or offset in visited: continue
            if len(visited) >= 32 or offset+2 > length: raise ValueError('Invalid SRW IFD chain')
            visited.add(offset)
            stream.seek(offset)
            count = struct.unpack(endian+'H', stream.read(2))[0]
            if count > 1024 or offset+2+count*12+4 > length: raise ValueError('Invalid SRW directory')
            entries = stream.read(count*12)
            queue.append(struct.unpack(endian+'I', stream.read(4))[0])
            selected = {}
            for i in range(count):
                tag, kind, number, pointer = struct.unpack_from(endian+'HHII', entries, i*12)
                if tag not in FIELDS and tag not in (330, 34665, 513, 514): continue
                unit = {2: 1, 3: 2, 4: 4, 5: 8}.get(kind)
                if not unit or number < 1 or number*unit > 4096: continue
                size = number*unit
                if size <= 4: data = entries[i*12+8:i*12+8+size]
                else:
                    if pointer+size > length: raise ValueError('SRW metadata outside file')
                    stream.seek(pointer); data = stream.read(size)
                if kind == 2: value = data.split(b'\0', 1)[0].decode('utf-8', 'replace').strip()
                elif kind == 5:
                    a, b = struct.unpack(endian+'II', data[:8])
                    value = a/b if b else None
                else:
                    values = list(struct.unpack(endian+('H' if kind == 3 else 'I')*number, data))
                    if tag in (330, 34665): queue.extend(values)
                    value = values[0] if number == 1 else values
                selected[tag] = value
                if tag in FIELDS: result[FIELDS[tag]] = value
            if 513 in selected and 514 in selected:
                start, size = selected[513], selected[514]
                if not isinstance(start, int) or not isinstance(size, int) or start+size > length:
                    raise ValueError('SRW JPEG outside file')
                previews.append({'offset': start, 'bytes': size})
    if previews: result['embedded_jpeg'] = max(previews, key=lambda p: p['bytes'])
    return result


def jpeg_metadata(path):
    with Image.open(path) as image:
        exif = image.getexif()
        sub = exif.get_ifd(34665)
        result = {'path': str(path), 'width': image.width, 'height': image.height}
        for tag, name in FIELDS.items():
            value = sub.get(tag, exif.get(tag))
            if value is None: continue
            if isinstance(value, str): value = value.strip(' \0')
            elif not isinstance(value, (int, float)): value = float(value)
            if isinstance(value, float) and not math.isfinite(value): value = None
            result[name] = value
        return result


def candidate(raw, jpeg):
    if raw.get('model') != jpeg.get('model') or raw.get('model') not in ('NX300', 'NX300M'):
        return None
    try:
        a, b = (datetime.strptime(item['captured'], '%Y:%m:%d %H:%M:%S') for item in (raw, jpeg))
    except (KeyError, ValueError): return None
    delta = abs((a-b).total_seconds())
    if delta > 3: return None
    for field in ('iso', 'exposure', 'aperture', 'focal_length'):
        if field in raw and field in jpeg and raw[field] is not None and jpeg[field] is not None:
            if abs(raw[field]-jpeg[field]) > max(1e-6, abs(raw[field])*1e-4): return None
    return {'raw': raw, 'jpeg': jpeg, 'capture_time_difference_seconds': delta,
            'status': 'metadata-matched-candidate',
            'camera_jpeg_software_candidate': bool(re.fullmatch(r'\d+\.\d+', jpeg.get('software', '')))}


def inventory(roots):
    command = ['rg', '--files', *map(str, roots)]
    for glob in ('*.SRW', '*.srw', '*.JPG', '*.jpg', '*.JPEG', '*.jpeg'):
        command.extend(['-g', glob])
    process = subprocess.run(command, capture_output=True, encoding='utf-8')
    if process.returncode not in (0, 1): raise RuntimeError(process.stderr.strip())
    paths = [Path(line) for line in process.stdout.splitlines()]
    raws = [p for p in paths if p.suffix.lower() == '.srw']
    jpegs = defaultdict(list)
    for path in paths:
        if path.suffix.lower() != '.srw': jpegs[path.stem.lower()].append(path)
    cache, errors, models, pairs = {}, [], Counter(), []
    for path in raws:
        try:
            raw = srw_metadata(path)
            models[raw.get('model', 'unknown')] += 1
            for jp in jpegs[path.stem.lower()]:
                if jp not in cache: cache[jp] = jpeg_metadata(jp)
                match = candidate(raw, cache[jp])
                if match: pairs.append(match)
        except (OSError, ValueError, struct.error) as exc:
            errors.append({'path': str(path), 'error': str(exc)})
    return {'roots': list(map(str, roots)), 'counts': dict(Counter(p.suffix.lower() for p in paths)),
            'raw_models': dict(models), 'pairs': pairs, 'errors': errors,
            'scope': 'Metadata candidates, not confirmed simultaneous captures or color measurements'}


def extract_preview(raw, target):
    raw, target = Path(raw), Path(target)
    if target.suffix.lower() not in ('.jpg', '.jpeg'): raise ValueError('Preview target must be JPEG')
    if target.resolve() == raw.resolve(): raise ValueError('Preview must use a separate output file')
    if target.exists(): raise FileExistsError('Preview target already exists')
    info = srw_metadata(raw)
    preview = info.get('embedded_jpeg')
    if not preview: raise ValueError('SRW has no embedded JPEG')
    with raw.open('rb') as stream:
        stream.seek(preview['offset']); data = stream.read(preview['bytes'])
    with Image.open(io.BytesIO(data)) as image:
        size = image.size; image.verify()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('xb') as stream: stream.write(data)
    return {'raw': str(raw), 'output': str(target), 'size': size, 'bytes': len(data),
            'source': 'original SRW embedded JPEG, bytes copied without re-encoding'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    scan = commands.add_parser('inventory')
    scan.add_argument('--root', type=Path, action='append')
    scan.add_argument('--output', type=Path, default=OUTPUT/'photo-pairs.json')
    preview = commands.add_parser('preview')
    preview.add_argument('raw', type=Path); preview.add_argument('output', type=Path)
    args = parser.parse_args()
    if args.command == 'inventory':
        report = inventory(args.root or [Path('F:/Pictures')])
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
        result = {key: report[key] for key in ('counts', 'raw_models')}
        result.update(output=str(args.output), candidate_pairs=len(report['pairs']), errors=len(report['errors']))
    else: result = extract_preview(args.raw, args.output)
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == '__main__': main()
