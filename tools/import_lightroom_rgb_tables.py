"""Read local Lightroom creative RGB tables into the existing camera library.

DNG SDK big-table serialization, modified base85, delta uint16 nodes and
tetrahedral interpolation are explicit. Only AdobeRGB/gamma2.2/clip tables
are currently baked; other native encodings are reported, not guessed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import xml.etree.ElementTree as ET
import zlib

import numpy as np

from import_fuji_rgb_tables import TO_ADOBE, FROM_ADOBE, extract_tables
from look_library import LookLibrary

ROOT = Path(__file__).resolve().parents[1]
PROFILES = Path('C:/ProgramData/Adobe/CameraRaw/Settings/Adobe/Profiles')
DLL = Path('./local/lightroom/CameraRaw.dll')
CRS = '{http://ns.adobe.com/camera-raw-settings/1.0/}'
ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ.-:+=^!/*?`'|()[]{}@%$#"
OUT = ROOT/'outputs/looks/lightroom-import'


def decode_payload(text, fingerprint):
    """DNG ASCIItoBinary uses little-endian radix-85 digits, unlike Z85."""
    lookup = np.full(256, -1, dtype=np.int64)
    lookup[list(ALPHABET.encode('ascii'))] = np.arange(85)
    characters = np.frombuffer(text.encode('ascii'), dtype=np.uint8)
    digits = lookup[characters]
    invalid = (digits < 0) & ~np.isin(characters, [9, 10, 13, 32])
    if np.any(invalid):
        raise ValueError('Invalid DNG base85 character')
    digits = digits[digits >= 0]
    if len(digits) % 5 == 1:
        raise ValueError('Truncated DNG base85 group')
    count = len(digits)//5*4 + max(0, len(digits)%5-1)
    groups = np.pad(digits, (0, (-len(digits))%5)).reshape(-1, 5).astype(np.uint64)
    words = groups@(85**np.arange(5, dtype=np.uint64))
    if np.any(words > 0xffffffff):
        raise ValueError('DNG base85 word overflow')
    data = words.astype('<u4').tobytes()[:count]
    expected = struct.unpack_from('<I', data)[0]
    if not 16 <= expected <= 16*1024*1024:
        raise ValueError('Unexpected big-table size')
    decoder = zlib.decompressobj()
    raw = decoder.decompress(data[4:], expected+1)
    if not decoder.eof or len(raw) != expected or decoder.unused_data:
        raise ValueError('DNG table compressed size mismatch')
    if hashlib.md5(raw).hexdigest().upper() != fingerprint.upper():
        raise ValueError('DNG table fingerprint mismatch')
    return raw


def gamma_encode(x):
    x = np.clip(x, 0, 1)
    x1, y1, slope = .0034800731, .0763027458, 9.9661890075
    t = x/x1
    toe = (t**3-2*t*t+t)*x1*32 + (-2*t**3+3*t*t)*y1 + (t**3-t*t)*x1*slope
    return np.where(x <= x1, toe, x**(1/2.2))


def gamma_decode(x):
    result = x**2.2
    mask = (x > 0) & (x < .0763027458)
    if np.any(mask):
        low, high = np.zeros(np.count_nonzero(mask)), np.full(np.count_nonzero(mask), .0034800731)
        for _ in range(36):
            mid = (low+high)/2
            below = gamma_encode(mid) < x[mask]
            low, high = np.where(below, mid, low), np.where(below, high, mid)
        result[mask] = (low+high)/2
    return result


class CreativeRgbTable:
    def __init__(self, raw):
        kind, version, dimension, self.size = struct.unpack_from('<4I', raw)
        if (kind, version, dimension) != (1, 1, 3) or not 2 <= self.size <= 129:
            raise ValueError(f'Unsupported RGB table header: {(kind, version, dimension, self.size)}')
        end = 16+self.size**3*6
        if len(raw) not in (end+28, end+32):
            raise ValueError('Invalid RGB table data length')
        self.primaries, self.gamma, self.gamut, self.minimum, self.maximum = struct.unpack_from('<IIIdd', raw, end)
        self.flags = struct.unpack_from('<I', raw, end+28)[0] if len(raw) == end+32 else 0
        if (self.primaries, self.gamma, self.gamut) != (1, 3, 0) or self.flags not in (0, 1):
            raise ValueError(f'Unsupported RGB table colour model: {(self.primaries, self.gamma, self.gamut, self.flags)}')
        if not np.isfinite([self.minimum, self.maximum]).all() or not 0 <= self.minimum <= 1 <= self.maximum:
            raise ValueError('Invalid RGB table amount range')
        deltas = np.frombuffer(raw, dtype='<u2', count=self.size**3*3, offset=16).reshape(self.size, self.size, self.size, 3)
        nop = (np.arange(self.size, dtype=np.uint32)*65535+(self.size >> 1))//(self.size-1)
        coordinates = np.stack(np.meshgrid(nop, nop, nop, indexing='ij'), -1)
        self.values = ((deltas.astype(np.uint32)+coordinates) & 65535).reshape(-1, 3)/65535
        self.monochrome = bool(np.all(self.values[:, 0] == self.values[:, 1]) and
                               np.all(self.values[:, 0] == self.values[:, 2]))

    def sample(self, rgb):
        x = np.clip(rgb, 0, 1)*(self.size-1)
        lo = np.minimum(x.astype(np.int64), self.size-2)
        f = x-lo
        axes = np.argsort(-f, axis=1, kind='stable')
        t = np.take_along_axis(f, axes, 1)
        weights = np.column_stack((1-t[:, 0], t[:, 0]-t[:, 1], t[:, 1]-t[:, 2], t[:, 2]))
        result = np.zeros_like(x)
        for i in range(4):
            if i:
                lo[np.arange(len(lo)), axes[:, i-1]] += 1
            result += weights[:, i, None]*self.values[(lo[:, 0]*self.size+lo[:, 1])*self.size+lo[:, 2]]
        return result

    def apply(self, rgb):
        rgb = np.asarray(rgb, dtype=float)
        shape = rgb.shape
        x = rgb.reshape(-1, 3)
        linear = np.where(x <= .040449936, x/12.92, ((x+.055)/1.055)**2.4)
        encoded = gamma_encode(linear@np.asarray(TO_ADOBE).T)
        output = gamma_decode(np.clip(self.sample(encoded), 0, 1))@np.asarray(FROM_ADOBE).T
        output = np.clip(output, 0, 1)
        return np.where(output <= .0031308, output*12.92, 1.055*output**(1/2.4)-.055).reshape(shape)

    def write_cube(self, path, size):
        axis = np.linspace(0, 1, size)
        b, g, r = np.meshgrid(axis, axis, axis, indexing='ij')
        points = np.stack((r, g, b), -1).reshape(-1, 3)
        with Path(path).open('w', encoding='utf-8', newline='\n') as out:
            out.write('# Local Lightroom creative RGB table, amount 1.0, sRGB in/out.\n')
            out.write(f'LUT_3D_SIZE {size}\nDOMAIN_MIN 0 0 0\nDOMAIN_MAX 1 1 1\n')
            for start in range(0, len(points), 65536):
                np.savetxt(out, self.apply(points[start:start+65536]), fmt='%.9f')


def fields(path):
    return {key[len(CRS):]: value for node in ET.parse(path).iter()
            for key, value in node.attrib.items() if key.startswith(CRS)}


def selected_profiles(root):
    result = []
    for folder, prefix, pattern in (
        ('Camera/Nikon/Nikon Z fc', 'nikon', 'Nikon Z fc Camera '),
        ('Camera/Sigma/Sigma fp', 'sigma', 'SIGMA fp Camera '),
        ('Artistic', 'lr-artistic', ''), ('Modern', 'lr-modern', ''), ('Vintage', 'lr-vintage', ''),
    ):
        for path in sorted((root/folder).glob('*.xmp')):
            name = path.stem.removeprefix(pattern)
            slug = re.sub('[^a-z0-9]+', '-', name.lower()).strip('-')
            key = prefix+'-'+slug if not prefix.startswith('lr-') else 'lr-'+slug
            label = ('尼康 ' if prefix == 'nikon' else '适马 ' if prefix == 'sigma' else 'LR ')+name
            result.append((path, key, label))
    for name, slug in (('BLEACH BYPASS', 'bleach-bypass'), ('Sepia', 'sepia')):
        result.append((root/f'Camera/Fujifilm/Fujifilm X-T5/Fujifilm X-T5 Camera {name}.xmp',
                       'nx300-fuji-'+slug, '富士 '+('EBB' if slug == 'bleach-bypass' else name)))
    return result


def run(root=PROFILES, dll=DLL, library=None):
    library = library or LookLibrary()
    OUT.mkdir(parents=True, exist_ok=True)
    initial = {meta['id']: {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                            for path in library.directory(meta['id']).iterdir() if path.is_file()}
               for meta in library.list()}
    selected = [(path, key, label, fields(path)) for path, key, label in selected_profiles(root)]
    missing = {a['RGBTable'] for _, _, _, a in selected if a.get('RGBTable') and 'Table_'+a['RGBTable'] not in a}
    external = extract_tables(dll, missing) if missing else {}
    report = {'source_root': 'Adobe/Profiles', 'baked_amount': 1., 'entries': [], 'skipped': [],
              'preexisting_entries': len(initial), 'preexisting_preserved': False,
              'scope': 'creative RGB table; no donor calibration or Lightroom RAW development',
              'profiles_sha256': {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                                  for path, _, _, _ in selected}}
    seen = {}
    for path, key, label, attributes in selected:
        resource = path.relative_to(root).as_posix()
        fingerprint = attributes.get('RGBTable')
        if not fingerprint:
            report['skipped'].append({'profile': resource, 'reason': 'No independent RGBTable; slider/HSV profile'})
            continue
        if fingerprint in seen:
            report['skipped'].append({'profile': resource, 'reason': 'Same RGB table', 'same_as': seen[fingerprint]})
            continue
        try:
            raw = (decode_payload(attributes['Table_'+fingerprint], fingerprint)
                   if 'Table_'+fingerprint in attributes else external[fingerprint][0])
            table = CreativeRgbTable(raw)
        except ValueError as error:
            report['skipped'].append({'profile': resource, 'reason': str(error)})
            continue
        if attributes.get('ConvertToGrayscale') == 'True' and not table.monochrome:
            report['skipped'].append({'profile': resource, 'reason': 'Grayscale conversion is outside RGB table'})
            continue
        directory = library.directory(key)
        if directory.exists():
            metadata = library.get(key)
            if metadata.get('source_rgb_table_id') != fingerprint:
                raise FileExistsError('Existing look has a different source: '+key)
        else:
            # Retain a source inside this one import run; add_cube publishes the
            # normal library entry only after its existing compiler completes.
            pending = OUT/'sources'
            pending.mkdir(exist_ok=True)
            cube = pending/(key+'.cube')
            table.write_cube(cube, 65 if table.size > 16 else 33)
            metadata = library.add_cube(cube, key, label=label, input_space='srgb')
            cube.unlink()
        metadata.pop('source_path', None)
        metadata.update(source_kind='adobe-rgb-table', source_resource=resource,
                        source_rgb_table_id=fingerprint, source_serialized_bytes=len(raw),
                        source_grid=table.size, source_primaries='Adobe RGB',
                        source_gamma='SDK gamma_2_2 with cubic toe', source_gamut='clip',
                        source_interpolation='tetrahedral', source_baked_amount=1.,
                        source_profile_default_amount=float(attributes.get('RGBTableAmount', '1')),
                        source_monochrome=table.monochrome,
                        scope='creative RGBTable only, converted to sRGB',
                        excluded_components=['donor camera calibration', 'base LookTable / CameraProfile',
                                             'RAW sliders, spatial effects, grain'])
        library._save(key, metadata)
        seen[fingerprint] = key
        entry = {'id': key, 'label': label, 'resource': resource,
                 'rgb_table_id': fingerprint, 'original_grid': table.size,
                 'source_grid': 65 if table.size > 16 else 33, 'monochrome': table.monochrome}
        report['entries'].append(entry)
        (OUT/'inventory.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        print(json.dumps(entry, ensure_ascii=False), flush=True)
    report['preexisting_preserved'] = all(
        hashlib.sha256((library.directory(key)/filename).read_bytes()).hexdigest() == digest
        for key, files in initial.items() for filename, digest in files.items())
    if not report['preexisting_preserved']:
        raise AssertionError('Preexisting source changed')
    (OUT/'inventory.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profiles', type=Path, default=PROFILES)
    parser.add_argument('--dll', type=Path, default=DLL)
    args = parser.parse_args()
    result = run(args.profiles, args.dll)
    print(json.dumps({'imported': len(result['entries']), 'skipped': result['skipped'],
                      'preexisting_preserved': result['preexisting_preserved']}, ensure_ascii=False))
