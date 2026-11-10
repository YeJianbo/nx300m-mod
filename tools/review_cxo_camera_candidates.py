"""Compare remaining CXO looks across normal images and existing 9-cube tables."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from lut_resample import sample_table
from lut_table_data import read_tables
from nx300m_lut_core import apply_lut, parse_lut_file
from render_cxo_ratio_directions import DOC_IMAGES, ROOT, SAMPLES, image_of, save_srgb


BASE = ROOT / 'outputs/looks/cxo-import'
OUT = BASE / 'comparison'
CATEGORIES = (
    ('数码胶片', 'digital'), ('负片', 'negative'), ('正片', 'positive'),
    ('黑白胶片', 'mono'), ('电影胶片', 'cinema'), ('交叉冲印', 'cross'),
)
SCENES = (
    ('暖光人像', SAMPLES / '34_09_before.jpg', False),
    ('自然光人像', SAMPLES / '32_19_before.jpg', False),
    ('城市街景', SAMPLES / '113_01_before.jpg', False),
    ('自然风景', DOC_IMAGES / 'raw-landscape.jpg', True),
    ('城市夜景', DOC_IMAGES / 'raw-sony.jpg', True),
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_scene(path: Path, crop: bool) -> np.ndarray:
    image = Image.open(path).convert('RGB')
    if crop:
        image = image.crop((0, 58, image.width // 3, image.height))
    image.thumbnail((600, 420), Image.Resampling.LANCZOS)
    return np.asarray(image, dtype=np.float32) / 255


def render_group(category: str, slug: str, entries: list, scenes: list, rendered: dict) -> Path:
    width, image_height, row_label, gap, margin = 340, 200, 34, 9, 26
    row_height, header = image_height + row_label + gap, 115
    canvas = Image.new('RGB', (margin * 2 + len(scenes) * width,
                              header + row_height * (len(entries) + 1)), '#11161b')
    draw = ImageDraw.Draw(canvas)
    title = ImageFont.truetype('C:/Windows/Fonts/msyhbd.ttc', 29)
    font = ImageFont.truetype('C:/Windows/Fonts/msyhbd.ttc', 17)
    small = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 15)
    draw.text((margin, 16), f'CXO 其他入机候选 · {category}', font=title, fill='#f5f1e8')
    draw.text((margin, 58), '每格左半：33³ 母版；右半：现有 9³ 表的 RGB 三线性模拟。均为 CXO 100%。',
              font=small, fill='#aeb8c2')
    for col, (label, _) in enumerate(scenes):
        draw.text((margin + col * width + 8, 88), label, font=font, fill='#d9b86a')
    for row, entry in enumerate([None, *entries]):
        top = header + row * row_height
        name = '原图' if entry is None else entry['label']
        draw.text((margin, top + 5), name, font=font, fill='#f5f1e8')
        for col, (_, source) in enumerate(scenes):
            left = margin + col * width
            if entry is None:
                tile = image_of(source, (width, image_height))
            else:
                master, native = rendered[entry['camera_id']][col]
                tile = image_of(native, (width, image_height))
                reference = image_of(master, (width, image_height))
                tile.paste(reference.crop((0, 0, width // 2, image_height)), (0, 0))
            canvas.paste(tile, (left, top + row_label))
            if entry is not None:
                draw.line((left + width // 2, top + row_label,
                           left + width // 2, top + row_label + image_height), fill='#e0e4e7')
    path = OUT / f'cxo-camera-candidates-{slug}.jpg'
    save_srgb(canvas, path)
    return path


def main() -> None:
    manifest = json.loads((BASE / 'masters/manifest.json').read_text(encoding='utf-8'))
    selected = json.loads((BASE / 'recipes/manifest.json').read_text(encoding='utf-8'))
    selected_ids = {r['cxo_source_id'] for r in selected['recipes']}
    entries = [e for e in manifest['entries'] if e['source_id'] not in selected_ids]
    scenes = [(label, load_scene(path, crop)) for label, path, crop in SCENES]
    points = np.random.default_rng(0xC05E9).random((20000, 3), dtype=np.float32)
    rendered, records, signatures = {}, [], {}
    for entry in entries:
        key = entry['camera_id']
        cube = BASE / 'masters' / entry['cube']
        directory = ROOT / 'looks' / key
        metadata = json.loads((directory / 'look.json').read_text(encoding='utf-8'))
        if sha(cube) != entry['cube_sha256'] or sha(directory / 'source.cube') != entry['cube_sha256']:
            raise ValueError(f'CXO master identity mismatch: {key}')
        if metadata['compiler']['source_sha256'] != entry['cube_sha256']:
            raise ValueError(f'Native table compiled from a different source: {key}')
        native = read_tables(directory, metadata)['table.d4lut']
        lut = parse_lut_file(cube).lut
        target = apply_lut(points[:, None, :], lut)[:, 0, :]
        native_values = sample_table(native, points, interpolation='trilinear')
        absolute = np.abs(target - native_values) * 255
        record = {
            'id': key, 'label': entry['label'], 'category': entry['cxo_category'],
            'master_sha256': entry['cube_sha256'],
            'native_table_sha256': hashlib.sha256(native).hexdigest(),
            'native_method': metadata['compiler']['method'],
            'native_rgb_rmse_8bit': float(np.sqrt(np.mean(absolute ** 2))),
            'native_rgb_p99_abs_8bit': float(np.percentile(absolute, 99)),
            'native_rgb_max_abs_8bit': float(absolute.max()), 'scenes': [],
        }
        rendered[key] = []
        signature = []
        for label, source in scenes:
            master_image = apply_lut(source, lut)
            native_image = sample_table(native, source.reshape(-1, 3),
                                        interpolation='trilinear').reshape(source.shape)
            rendered[key].append((master_image, native_image))
            error = (master_image - native_image) * 255
            record['scenes'].append({'scene': label,
                                     'native_rgb_rmse_8bit': float(np.sqrt(np.mean(error ** 2)))})
            signature.append(native_image[::8, ::8].reshape(-1, 3).astype(np.float32))
        signatures[key] = np.concatenate(signature)
        records.append(record)
    by_id = {r['id']: r for r in records}
    for key, signature in signatures.items():
        neighbours = sorted((float(np.mean(np.abs(signature - other)) * 255), other_key)
                            for other_key, other in signatures.items() if other_key != key)
        by_id[key]['closest_other_cxo'] = [
            {'id': other_key, 'mean_abs_rgb_difference_8bit': difference}
            for difference, other_key in neighbours[:3]
        ]
    paths = []
    for category, slug in CATEGORIES:
        subset = [e for e in entries if e['cxo_category'] == category]
        paths.append(render_group(category, slug, subset, scenes, rendered))
    result = {
        'schema': 1, 'scope': 'remaining CXO 100% masters and existing fitted 9-cube tables',
        'hardware_verified': False, 'model': 'RGB-code trilinear standalone creative table',
        'random_points': len(points), 'seed': '0xC05E9',
        'scene_paths': [{'label': label, 'path': path.relative_to(ROOT).as_posix(),
                         'crop_first_panel_y': 58 if crop else None}
                        for label, path, crop in SCENES],
        'entries': records, 'images': [p.relative_to(ROOT).as_posix() for p in paths],
    }
    (OUT / 'cxo-camera-candidates.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n',
                                                  encoding='utf-8')
    print(json.dumps({'reviewed': len(entries), 'scene_count': len(scenes), 'images': result['images']},
                     ensure_ascii=False))


if __name__ == '__main__':
    main()
