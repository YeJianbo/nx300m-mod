"""Build review cubes and scene comparisons from the user's marked ratios."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from nx300m_lut_core import (
    BLEND_ALGORITHM, BLEND_WORKING_SPACE, apply_lut, blend_luts,
    cube_bytes, parse_lut_file, resample_lut,
)
from render_cxo_ratio_directions import (
    CXO, OUT, PAIRS, ROOT, SCENES, image_of, load_rgb, save_srgb,
)


RECIPES = ROOT / 'outputs/looks/cxo-import/recipes'
# Same order as the six styles in the marked comparison sheets.
CXO_WEIGHTS = (80, 50, 50, 50, 40, 60)
EVIDENCE = (
    {'portrait': 100, 'landscape': 70},
    {'portrait': 50},
    {'portrait': 50, 'landscape': 50},
    {'portrait': 50},
    {'portrait': 30, 'city': 30, 'landscape': 50},
    {'portrait': [50, 70], 'city': 50, 'landscape': 50},
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_recipe(entry: dict, existing_id: str, name: str, percent: int) -> tuple[dict, object]:
    old_path = ROOT / 'looks' / existing_id / 'source.cube'
    cxo_path = CXO / entry['cube']
    asset_path = (ROOT / 'mod/nxconnect/third_party/cxo-filmpack/core/local-data'
                  / entry['source_asset'].removeprefix('films/'))
    if sha(cxo_path) != entry['cube_sha256']:
        raise ValueError(f'CXO comparison cube changed: {cxo_path}')
    if sha(asset_path) != entry['source_asset_sha256']:
        raise ValueError(f'CXO source asset changed: {asset_path}')
    existing = parse_lut_file(old_path).lut
    cxo = parse_lut_file(cxo_path).lut
    label = f'{name} · CXO {percent}%'
    candidate = blend_luts(existing, cxo, percent / 100, title=label)
    key = f'{existing_id}-cxo{percent}'
    path = RECIPES / f'{key}.cube'
    # Preserve the original table exactly for the 100% endpoint.
    path.write_bytes(cxo_path.read_bytes() if percent == 100 else cube_bytes(candidate))
    reloaded = parse_lut_file(path).lut
    expected = (resample_lut(existing).table * (1 - percent / 100)
                + resample_lut(cxo).table * (percent / 100))
    error = float(np.max(np.abs(reloaded.table - expected)))
    if not np.isfinite(reloaded.table).all() or error > 2e-7:
        raise ValueError(f'Cube round-trip mismatch: {key}: {error}')
    return {
        'id': key, 'label': label, 'cube': path.name, 'cube_sha256': sha(path),
        'existing_percent': 100 - percent, 'cxo_percent': percent,
        'existing_cube': old_path.relative_to(ROOT).as_posix(),
        'existing_sha256': sha(old_path),
        'cxo_cube': cxo_path.relative_to(ROOT).as_posix(),
        'cxo_cube_sha256': sha(cxo_path),
        'cxo_source_id': entry['source_id'],
        'cxo_source_asset': asset_path.relative_to(ROOT).as_posix(),
        'cxo_source_asset_sha256': sha(asset_path),
        'round_trip_max_error': error,
    }, (existing, reloaded, cxo)


def render(key: str, scene_label: str, source: np.ndarray, luts: list) -> Path:
    tile_width, image_height, label_height, gap, margin = 420, 236, 54, 10, 26
    top_height = 105
    row_height = image_height + label_height + gap
    canvas = Image.new('RGB', (margin * 2 + tile_width * 4,
                              top_height + row_height * len(PAIRS)), '#11161b')
    draw = ImageDraw.Draw(canvas)
    title = ImageFont.truetype('C:/Windows/Fonts/msyhbd.ttc', 30)
    font = ImageFont.truetype('C:/Windows/Fonts/msyhbd.ttc', 19)
    small = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 16)
    draw.text((margin, 18), f'胶片配方对比 · {scene_label}', font=title, fill='#f5f1e8')
    draw.text((margin, 65), '原图 / 原机内风格 / CXO 100% / 微调配方；各列曝光、白平衡和裁切一致。',
              font=small, fill='#aeb8c2')
    for row, ((_, _, name), percent, (existing, selected, cxo)) in enumerate(
            zip(PAIRS, CXO_WEIGHTS, luts)):
        top = top_height + row * row_height
        variants = (
            ('原图', source), ('原机内 · ' + name, apply_lut(source, existing)),
            ('CXO 100% · ' + name, apply_lut(source, cxo)),
            (f'微调 {percent}% CXO · {name}', apply_lut(source, selected)),
        )
        for column, (label, pixels) in enumerate(variants):
            left = margin + column * tile_width
            draw.rectangle((left, top, left + tile_width - 1, top + image_height + label_height),
                           fill='#1c232a')
            canvas.paste(image_of(pixels, (tile_width, image_height)), (left, top))
            draw.text((left + 10, top + image_height + 15), label, font=font, fill='#f5f1e8')
            if column == 3:
                draw.rectangle((left, top, left + tile_width - 2, top + image_height + label_height),
                               outline='#d9b86a', width=3)
    path = OUT / f'cxo-selected-{key}.jpg'
    save_srgb(canvas, path)
    return path


def main() -> None:
    RECIPES.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((CXO / 'manifest.json').read_text(encoding='utf-8'))
    entries = {entry['source_id']: entry for entry in manifest['entries']}
    records, luts = [], []
    for index, ((source_id, existing_id, name), percent) in enumerate(zip(PAIRS, CXO_WEIGHTS)):
        record, tables = write_recipe(entries[source_id], existing_id, name, percent)
        record['marked_cxo_percent_by_scene'] = EVIDENCE[index]
        records.append(record)
        luts.append(tables)
    scenes = [(key, label, load_rgb(path, crop)) for key, label, path, crop in SCENES]
    images = [render(key, label, source, luts) for key, label, source in scenes]
    result = {
        'schema': 1, 'status': 'user-directed-compromise',
        'algorithm': BLEND_ALGORITHM, 'working_space': BLEND_WORKING_SPACE,
        'upstream_commit': manifest['upstream_commit'],
        'upstream_license': 'CXO FilmPack Non-Commercial License',
        'style_scope': 'Derived digital look recipes; physical film/Fujifilm camera matching unmeasured',
        'camera_validation': 'sRGB desktop comparison only; native camera table not compiled or measured',
        'recipes': records,
        'images': [path.relative_to(ROOT).as_posix() for path in images],
    }
    (RECIPES / 'manifest.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n',
                                          encoding='utf-8')
    print(json.dumps({'recipes': len(records),
                      'images': result['images'],
                      'max_cube_round_trip_error': max(r['round_trip_max_error']
                                                      for r in records)},
                     ensure_ascii=False))


if __name__ == '__main__':
    main()
