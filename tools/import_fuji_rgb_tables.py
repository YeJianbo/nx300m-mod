"""Import the user's NC/NN XMP RGBTable references from local Camera Raw data.

Only the creative RGB table is baked. Donor camera calibration and Adobe's
RAW rendering are excluded. Original XMPs and CameraRaw.dll remain read-only.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import tempfile
import zlib

from adobe_profiles import (_inverse, _matrix, _multiply, _normalize_rows,
                            _SRGB, srgb_decode, srgb_encode, xmp_metadata)
from look_library import LookLibrary

PROJECT = Path(__file__).resolve().parents[1]
LIGHTROOM = Path('./local/lightroom')
PROFILES = (
    ('XT5_CLASSIC_Neg_NX300.xmp', 'FF464EAD00BC484FF7242A5615F1FE4A',
     'nx300-fuji-classic-negative', '富士 NC'),
    ('XT5_NOSTALGIC_Neg_NX300.xmp', 'D2586BEC87DD7D2E326BE4EE7F1D91AB',
     'nx300-fuji-nostalgic-neg', '富士 NN'),
)
# DNG SDK dng_space_AdobeRGB::MatrixToPCS, with SDK white normalization.
ADOBE = _normalize_rows(((.6097, .2053, .1492), (.3111, .6257, .0632),
                         (.0195, .0609, .7446)))
TO_ADOBE = _multiply(_inverse(ADOBE), _SRGB)
FROM_ADOBE = _inverse(TO_ADOBE)


def gamma_encode(value):
    """SDK gamma_2_2 includes a cubic toe, rather than a pure power law."""
    x1, y1, slope1 = .0034800731, .0763027458, 9.9661890075
    if value > x1:
        return value**(1./2.2)
    t = value/x1
    return ((t**3-2*t*t+t)*x1*32. + (-2*t**3+3*t*t)*y1
            + (t**3-t*t)*x1*slope1)


def gamma_decode(value):
    if not 0. < value < .0763027458:
        return value**2.2
    low, high = 0., .0034800731
    for _ in range(36):
        middle = (low+high)*.5
        if gamma_encode(middle) < value:
            low = middle
        else:
            high = middle
    return (low+high)*.5


def extract_tables(dll, fingerprints):
    """Locate bounded zlib records by their serialized DNG table fingerprint.

    DLL layout offsets change between releases; adjacent registration strings
    do not establish payload identity. Require MD5 of the actual decoded record
    to match the XMP RGBTable ID, per dng_big_table::ComputeFingerprint.
    """
    data = Path(dll).read_bytes()
    expected_size = 16 + 16**3*6 + 32
    signature = struct.pack('<I', expected_size)
    found, start = {}, 0
    while len(found) < len(fingerprints):
        offset = data.find(signature, start)
        if offset < 0:
            break
        start = offset+4
        if data[offset+4:offset+5] != b'\x78':
            continue
        try:
            decoder = zlib.decompressobj()
            raw = decoder.decompress(data[offset+4:offset+4+65536], expected_size+1)
        except zlib.error:
            continue
        if not decoder.eof or len(raw) != expected_size:
            continue
        fingerprint = hashlib.md5(raw).hexdigest().upper()
        if fingerprint in fingerprints:
            found[fingerprint] = (raw, offset)
    missing = set(fingerprints)-found.keys()
    if missing:
        raise ValueError('RGBTable payload not found for XMP IDs: '+', '.join(sorted(missing)))
    return found


class RgbTable:
    def __init__(self, raw):
        if struct.unpack_from('<4I', raw) != (1, 1, 3, 16):
            raise ValueError('Expected DNG RGB table v1, three dimensions, 16 divisions')
        self.size = 16
        footer = struct.unpack_from('<IIIddI', raw, 16+self.size**3*6)
        # Do not silently interpret another color space, gamut policy or flag.
        if footer != (1, 3, 0, 0., 2., 1):
            raise ValueError(f'Unsupported RGBTable color encoding or flags: {footer}')
        self.encoding = {'primaries': 'Adobe RGB', 'gamma': 'SDK gamma_2_2 (cubic toe)',
                         'gamut': 'clip', 'min_amount': 0., 'max_amount': 2.,
                         'flags': 1, 'flags_meaning': 'embed-never; not a color operation',
                         'baked_amount': 1.}
        deltas = struct.unpack_from('<'+str(self.size**3*3)+'H', raw, 16)
        nop = [(i*65535+(self.size >> 1))//(self.size-1) for i in range(self.size)]
        self.values = []
        for r in range(self.size):
            for g in range(self.size):
                for b in range(self.size):
                    index = ((r*self.size+g)*self.size+b)*3
                    self.values.append(tuple(((deltas[index+c]+nop[v]) & 65535)/65535.
                                             for c, v in enumerate((r, g, b))))

    def sample(self, rgb):
        coords = [max(0., min(1., v))*(self.size-1) for v in rgb]
        base = [min(int(v), self.size-2) for v in coords]
        fract = [v-i for v, i in zip(coords, base)]
        axes = sorted(range(3), key=lambda c: fract[c], reverse=True)
        f1, f2, f3 = [fract[c] for c in axes]
        vertices = [tuple(base)]
        for axis in axes:
            base[axis] += 1
            vertices.append(tuple(base))
        weights = (1.-f1, f1-f2, f2-f3, f3)
        return tuple(sum(w*self.values[(r*self.size+g)*self.size+b][c]
                         for w, (r, g, b) in zip(weights, vertices)) for c in range(3))

    def apply_srgb(self, rgb):
        linear = _matrix(TO_ADOBE, tuple(srgb_decode(v) for v in rgb))
        encoded = tuple(gamma_encode(max(0., min(1., v))) for v in linear)
        output = tuple(gamma_decode(v) for v in self.sample(encoded))
        return tuple(srgb_encode(max(0., min(1., v))) for v in _matrix(FROM_ADOBE, output))

    def write_cube(self, path, size=33):
        with Path(path).open('w', encoding='utf-8', newline='\n') as stream:
            stream.write('# Local Adobe RGBTable creative stage; converted to sRGB; R fastest.\n')
            stream.write(f'LUT_3D_SIZE {size}\nDOMAIN_MIN 0 0 0\nDOMAIN_MAX 1 1 1\n')
            for b in range(size):
                for g in range(size):
                    for r in range(size):
                        result = self.apply_srgb((r/(size-1), g/(size-1), b/(size-1)))
                        stream.write(' '.join(f'{v:.9f}' for v in result)+'\n')


def import_profiles(dll=LIGHTROOM/'CameraRaw.dll', xmp_root=None, library=None):
    xmp_root = Path(xmp_root or Path(os.environ['APPDATA'])/'Adobe/CameraRaw/ImportedSettings/NX300_Fuji_XMP_Import')
    library = library or LookLibrary()
    sources = []
    for filename, fingerprint, key, label in PROFILES:
        profile = xmp_metadata(xmp_root/filename)
        if (profile['camera_model'] != 'Samsung NX300'
                or profile['settings'].get('RGBTable') != fingerprint
                or not re.fullmatch('[A-F0-9]{32}', fingerprint)):
            raise ValueError(f'XMP identity differs: {filename}')
        sources.append((profile, fingerprint, key, label))
    payloads = extract_tables(dll, {row[1] for row in sources})
    imported = []
    for profile, fingerprint, key, label in sources:
        raw, offset = payloads[fingerprint]
        table = RgbTable(raw)
        directory = library.directory(key)
        if directory.exists():
            metadata = library.get(key)
            if (metadata.get('source_kind') != 'adobe-rgb-table'
                    or metadata.get('source_rgb_table_id') != fingerprint):
                raise FileExistsError(f'Preserve existing look with different source: {key}')
            metadata['label'] = label
        else:
            with tempfile.TemporaryDirectory(prefix='nx300m-rgbtable-') as temporary:
                cube = Path(temporary)/'source.cube'
                table.write_cube(cube)
                metadata = library.add_cube(cube, key, label=label, input_space='srgb')
        metadata.update({
            'source_kind': 'adobe-rgb-table', 'source_path': profile['path'],
            'source_profile': profile['profile_name'], 'source_base_profile': profile['base_profile'],
            'source_rgb_table_id': fingerprint, 'source_dll': str(Path(dll).resolve()),
            'source_dll_offset': hex(offset), 'source_serialized_bytes': len(raw),
            'source_encoding': table.encoding, 'source_axis_order': 'R outer, G middle, B inner',
            'master_axis_order': 'B outer, G middle, R inner (standard cube)',
            'scope': 'creative RGBTable only; sRGB bake using Adobe RGB primaries and SDK gamma_2_2',
            'source_interpolation': 'tetrahedral; float64 analytic gamma, not SDK float32 1D tables',
            'excluded_components': ['donor camera calibration', 'base CameraProfile',
                                    'Adobe raw-development process'],
        })
        library._save(key, metadata)
        imported.append({'id': key, 'label': label, 'rgb_table_id': fingerprint,
                         'dll_offset': hex(offset), 'native_bytes': (directory/'table.d4lut').stat().st_size})
    return {'entries': imported, 'scope': 'local creative color adaptation; no on-camera color verification'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dll', type=Path, default=LIGHTROOM/'CameraRaw.dll')
    parser.add_argument('--xmp-root', type=Path)
    args = parser.parse_args()
    result = import_profiles(args.dll, args.xmp_root)
    destination = PROJECT/'outputs/looks/fuji-rgb-table-import.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
