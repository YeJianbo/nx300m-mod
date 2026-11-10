"""Review the staged camera palette and proposed CXO recipes on shared photos."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from manage_local_lut_library import USER_LUT_IDS
from nx300m_lut_core import apply_lut, parse_lut_file
from review_cxo_camera_candidates import ROOT, SCENES, load_scene
from render_cxo_ratio_directions import image_of, save_srgb


OUT = ROOT / 'outputs/looks/camera-curation'
CXO = ROOT / 'outputs/looks/cxo-import'


def palette() -> tuple[list[dict], dict]:
    staged = json.loads((ROOT / 'lut-library/machine-catalog.json').read_text(encoding='utf-8'))
    catalog = json.loads((ROOT / 'looks/catalog.json').read_text(encoding='utf-8'))
    group_for = {key: group['name'] for group in catalog['groups'] for key in group['ids']}
    entries = []
    for key in staged['ids']:
        if key.startswith('cxo-'):
            continue
        directory = ROOT / 'looks' / key
        metadata = json.loads((directory / 'look.json').read_text(encoding='utf-8'))
        group = group_for.get(key, '我提供的 9 款')
        if key in USER_LUT_IDS:
            group = '我提供的 9 款'
        entries.append({'id': key, 'label': metadata['label'], 'group': group,
                        'cube': (directory / 'source.cube').relative_to(ROOT).as_posix(),
                        'input_space': metadata['input_space'],
                        'source_base_style': metadata.get('source_base_style'), 'kind': 'existing'})
    recipes = json.loads((CXO / 'recipes/manifest.json').read_text(encoding='utf-8'))
    for recipe in recipes['recipes']:
        entries.append({'id': recipe['id'], 'label': recipe['label'], 'group': '已定混合配方',
                        'cube': (CXO / 'recipes' / recipe['cube']).relative_to(ROOT).as_posix(),
                        'input_space': 'srgb', 'kind': 'chosen-recipe'})
    candidates = json.loads((CXO / 'comparison/cxo-camera-candidates.json').read_text(encoding='utf-8'))
    for key in candidates['recommended_ids']:
        directory = ROOT / 'looks' / key
        metadata = json.loads((directory / 'look.json').read_text(encoding='utf-8'))
        entries.append({'id': key, 'label': metadata['label'], 'group': 'CXO 新候选',
                        'cube': (directory / 'source.cube').relative_to(ROOT).as_posix(),
                        'input_space': metadata['input_space'], 'kind': 'cxo-candidate'})
    return entries, {'staged_ids': staged['ids'], 'source_catalog': catalog}


def scenes() -> list[tuple[str, np.ndarray]]:
    result = []
    for label, path, crop in SCENES:
        source = load_scene(path, crop)
        image = Image.fromarray(np.rint(source * 255).astype(np.uint8))
        image.thumbnail((420, 260), Image.Resampling.LANCZOS)
        result.append((label, np.asarray(image, dtype=np.float32) / 255))
    return result


def render(entries: list[dict], pictures: list, results: dict, title_text: str, path: Path) -> None:
    tile_width, image_height, row_label, gap, margin, header = 340, 200, 34, 9, 26, 115
    row_height = image_height + row_label + gap
    canvas = Image.new('RGB', (margin * 2 + tile_width * len(pictures),
                              header + row_height * (len(entries) + 1)), '#11161b')
    draw = ImageDraw.Draw(canvas)
    title = ImageFont.truetype('C:/Windows/Fonts/msyhbd.ttc', 29)
    font = ImageFont.truetype('C:/Windows/Fonts/msyhbd.ttc', 17)
    small = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 15)
    draw.text((margin, 16), title_text, font=title, fill='#f5f1e8')
    draw.text((margin, 58), '同一组正常反差样片；源 LUT 的 sRGB 桌面模拟，曝光、白平衡及裁切一致。',
              font=small, fill='#aeb8c2')
    for column, (label, _) in enumerate(pictures):
        draw.text((margin + column * tile_width + 8, 88), label, font=font, fill='#d9b86a')
    for row, entry in enumerate([None, *entries]):
        top = header + row * row_height
        name = '原图' if entry is None else entry['label']
        draw.text((margin, top + 5), name, font=font, fill='#f5f1e8')
        for column, (_, original) in enumerate(pictures):
            pixels = original if entry is None else results[entry['id']][column]
            canvas.paste(image_of(pixels, (tile_width, image_height)),
                         (margin + column * tile_width, top + row_label))
    save_srgb(canvas, path)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    entries, before = palette()
    pictures = scenes()
    results, signatures = {}, {}
    for entry in entries:
        lut = parse_lut_file(ROOT / entry['cube']).lut
        output = [apply_lut(source, lut) for _, source in pictures]
        results[entry['id']] = output
        signatures[entry['id']] = [pixels[::6, ::6] for pixels in output]
    pairs = []
    for i, a in enumerate(entries):
        for b in entries[i + 1:]:
            distances = [float(np.mean(np.abs(left - right)) * 255)
                         for left, right in zip(signatures[a['id']], signatures[b['id']])]
            pairs.append({'a': a['id'], 'b': b['id'], 'mean_abs_rgb_8bit': float(np.mean(distances)),
                          'scene_distances': dict(zip((label for label, _ in pictures), distances))})
    pairs.sort(key=lambda pair: pair['mean_abs_rgb_8bit'])
    for entry in entries:
        neighbours = [pair for pair in pairs if entry['id'] in (pair['a'], pair['b'])][:4]
        entry['nearest'] = neighbours
    groups = (
        ('brand', '品牌与基础', [e for e in entries if e['kind'] == 'existing'
                              and e['group'] in ('黑白', '富士', '理光', '奥林巴斯', '宾得')]),
        ('custom-film', '自制与胶片', [e for e in entries if e['kind'] == 'existing'
                                    and e['group'] in ('自制', '胶片模拟')]),
        ('lumix', 'Lumix Lab', [e for e in entries if e['group'] == 'Lumix Lab']),
        ('supplied', '我提供的 9 款', [e for e in entries if e['group'] == '我提供的 9 款']),
        ('cxo', '已定混合配方与新 CXO 候选', [e for e in entries if e['kind'] != 'existing']),
    )
    paths = []
    for slug, name, group in groups:
        for start in range(0, len(group), 8):
            path = OUT / f'palette-{slug}-{start // 8 + 1}.jpg'
            render(group[start:start + 8], pictures, results, name, path)
            paths.append(path.relative_to(ROOT).as_posix())
            print(path.name, flush=True)
    report = {'schema': 1, 'scope': 'camera palette curation; source sRGB appearance only',
              'hardware_verified': False, 'before': before,
              'scenes': [{'label': label, 'path': path.relative_to(ROOT).as_posix()}
                         for label, path, _ in SCENES],
              'entries': entries, 'pairs': pairs, 'images': paths}
    (OUT / 'review.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    labels = {e['id']: e['label'] for e in entries}
    print(json.dumps({'reviewed': len(entries), 'existing': sum(e['kind'] == 'existing' for e in entries),
                      'closest_pairs': [{'a': labels[p['a']], 'b': labels[p['b']],
                                         'mean_abs_rgb_8bit': p['mean_abs_rgb_8bit']} for p in pairs[:25]]},
                     ensure_ascii=False))


if __name__ == '__main__':
    main()
