"""Package the supplemental-pack cubes as adjustable Lightroom XMP looks."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

from build_lumix_lightroom_profiles import (
    ProfileSource, _encode_table, _resample, _verify_xmp, _xmp,
)
from nx300m_lut_core import parse_lut_file


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'outputs/looks/supplement-lightroom'
GROUP = 'NX300M \u8865\u5145\u5305\u53ef\u8c03\u7248'
DESTINATION = OUTPUT / GROUP

IDS = (
    'kodak-5219',
    'kodak-ultramax-400',
    'fuji-pro-neg-hi',
    'ricoh-gr-positive',
    'ricoh-gr-negative',
    'sony-fl3',
    'fuji-bleach-bypass-hz',
    'fuji-velvia-hz',
    'fuji-acros-hz',
    'sigma-warm-gold-hz',
    'fuji-eterna-hz',
)


def main() -> None:
    DESTINATION.mkdir(parents=True, exist_ok=True)
    entries, checks, paths = [], [], []
    for index, look_id in enumerate(IDS, 1):
        look = json.loads((ROOT / 'looks' / look_id / 'look.json').read_text(encoding='utf-8'))
        cube_path = ROOT / 'looks' / look_id / 'source.cube'
        actual_sha = hashlib.sha256(cube_path.read_bytes()).hexdigest()
        item = parse_lut_file(cube_path)
        label = look['label']
        source = ProfileSource(str(index), label, 'sRGB', cube_path,
                               item.name, actual_sha, item.lut)
        table = _resample(item.lut, 32)
        fingerprint, encoded, raw_size = _encode_table(table)
        name = f"{index:02d} {label}"
        path = DESTINATION / f'{name}.xmp'
        description = (
            f"\u8865\u5145\u5305{label}\uff0cAmount 100 \u4e3a\u914d\u65b9sRGB\u8f93\u51fa\u503c\u3002"
            "\u66dd\u5149\u3001\u767d\u5e73\u8861\u3001\u66f2\u7ebf\u548c\u8272\u5f69\u6ed1\u5757\u53ef\u7ee7\u7eed\u8c03\u8282\u3002"
            f"\u6e90\uff1alooks/{look_id}/source.cube\u3002"
        )
        path.write_text(_xmp(name, description, fingerprint, encoded, group_name=GROUP),
                        encoding='utf-8', newline='\n')
        check = _verify_xmp(path, source, table, fingerprint, raw_size, group_name=GROUP)
        checks.append(check)
        entries.append({
            'name': name, 'file': f'{GROUP}/{path.name}',
            'look_id': look_id, 'source_cube_sha256': actual_sha,
            'grid': 32,
            'rgb_table_id': fingerprint,
            'xmp_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        })
        paths.append(path)
    fingerprints = [entry['rgb_table_id'] for entry in entries]
    if len(set(fingerprints)) != len(fingerprints):
        raise ValueError('Supplement cubes contain duplicate Lightroom table IDs')
    manifest = {
        'schema': 1, 'group': GROUP, 'count': len(entries), 'default_amount': 100,
        'scope': 'Adobe RGBTable creative XMP look; 1:1 from looks/<id>/source.cube',
        'entries': entries,
    }
    verification = {
        'schema': 1, 'runtime_lightroom_render_verified': False,
        'checks': ['source_cube_identity', 'xml_well_formed', 'unique_table_ids',
                   'table_fingerprint', 'table_decode', 'supports_amount',
                   'no_fixed_exposure_wb_mask', '20000_heldout_rgb_samples_per_recipe'],
        'entries': checks,
    }
    metadata_paths = []
    for filename, data in (('lightroom-manifest.json', manifest),
                           ('lightroom-verification.json', verification)):
        path = OUTPUT / filename
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        metadata_paths.append(path)
    bundle = OUTPUT / 'NX300M-Supplement-Lightroom.zip'
    with zipfile.ZipFile(bundle, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in paths:
            archive.write(path, f'{GROUP}/{path.name}')
        for path in metadata_paths:
            archive.write(path, path.name)
    with zipfile.ZipFile(bundle) as archive:
        if archive.testzip() is not None:
            raise ValueError('Lightroom bundle CRC validation failed')
        if sum(name.endswith('.xmp') for name in archive.namelist()) != len(entries):
            raise ValueError('Lightroom bundle XMP count mismatch')
        for entry in entries:
            if hashlib.sha256(archive.read(entry['file'])).hexdigest() != entry['xmp_sha256']:
                raise ValueError(f"Packaged XMP differs: {entry['file']}")
    print(json.dumps({
        'profiles': len(entries), 'bundle': str(bundle), 'bytes': bundle.stat().st_size,
        'maximum_quantization_error': max(c['quantization_max_abs'] for c in checks),
        'maximum_heldout_rgb_rmse': max(c['heldout_rgb_rmse'] for c in checks),
        'maximum_heldout_rgb_error': max(c['heldout_rgb_max_abs'] for c in checks),
        'runtime_lightroom_render_verified': False,
    }, ensure_ascii=False))


if __name__ == '__main__':
    main()
