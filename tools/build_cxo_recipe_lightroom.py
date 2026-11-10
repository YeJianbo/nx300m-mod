"""Package the selected mixed cubes as adjustable Lightroom XMP looks."""
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
OUTPUT = ROOT / 'outputs/looks/cxo-import'
RECIPES = OUTPUT / 'recipes'
GROUP = 'NX300M × CXO 混合配方'
DESTINATION = RECIPES / 'lightroom' / GROUP


def main() -> None:
    recipe_manifest = json.loads((RECIPES / 'manifest.json').read_text(encoding='utf-8'))
    DESTINATION.mkdir(parents=True, exist_ok=True)
    entries, checks, paths = [], [], []
    for index, recipe in enumerate(recipe_manifest['recipes'], 1):
        cube_path = RECIPES / recipe['cube']
        actual_sha = hashlib.sha256(cube_path.read_bytes()).hexdigest()
        if actual_sha != recipe['cube_sha256']:
            raise ValueError(f'Recipe cube changed: {cube_path}')
        item = parse_lut_file(cube_path)
        source = ProfileSource(str(index), recipe['label'], 'sRGB', cube_path,
                               item.name, actual_sha, item.lut)
        table = _resample(item.lut, 32)
        fingerprint, encoded, raw_size = _encode_table(table)
        name = f"{index:02d} {recipe['label']}"
        path = DESTINATION / f'{name}.xmp'
        description = (
            f"{recipe['existing_percent']}% 现有版 + {recipe['cxo_percent']}% CXO；"
            '混合比例已烘焙，Amount 100 为本配方。sRGB 输出色值混合。'
            '曝光、白平衡、曲线和颜色滑杆可继续调整。'
            '源自 CXO FilmPack 数字风格及现有 LUT 的衍生配方。'
        )
        path.write_text(_xmp(name, description, fingerprint, encoded, group_name=GROUP),
                        encoding='utf-8', newline='\n')
        check = _verify_xmp(path, source, table, fingerprint, raw_size, group_name=GROUP)
        checks.append(check)
        entries.append({
            'name': name, 'file': f'{GROUP}/{path.name}',
            'source_cube': recipe['cube'], 'source_cube_sha256': actual_sha,
            'existing_percent': recipe['existing_percent'],
            'cxo_percent': recipe['cxo_percent'], 'grid': 32,
            'rgb_table_id': fingerprint,
            'xmp_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        })
        paths.append(path)
    fingerprints = [entry['rgb_table_id'] for entry in entries]
    if len(set(fingerprints)) != len(fingerprints):
        raise ValueError('Selected recipes contain duplicate Lightroom table IDs')
    manifest = {
        'schema': 1, 'group': GROUP, 'count': len(entries), 'default_amount': 100,
        'scope': 'Adobe RGBTable creative XMP look; mixed ratio baked into the color table',
        'entries': entries, 'recipe_sources': recipe_manifest,
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
        path = RECIPES / filename
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        metadata_paths.append(path)
    bundle = OUTPUT / 'NX300M-CXO-Blended-Lightroom.zip'
    with zipfile.ZipFile(bundle, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in paths:
            archive.write(path, f'{GROUP}/{path.name}')
        for path in metadata_paths:
            archive.write(path, path.name)
        archive.write(ROOT / 'mod/nxconnect/third_party/cxo-filmpack/LICENSE',
                      'licenses/CXO-FilmPack-LICENSE.txt')
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
