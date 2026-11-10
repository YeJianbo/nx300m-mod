"""Render the complete 153-style CXO FilmPack catalogue as a review package."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
import json
from pathlib import Path
import re
import shutil
import zipfile

import numpy as np
from PIL import Image, ImageCms, ImageDraw, ImageFont

from nx300m_lut_core import apply_lut, parse_lut_file


ROOT = Path(__file__).resolve().parents[1]
MASTERS = ROOT / 'outputs/looks/cxo-import/all-masters'
SAMPLES = ROOT / 'scratch/lumixlab/repo/LumixLutSearch-main/app/src/main/assets/songying'
DOC = ROOT / 'mod/nxconnect/third_party/cxo-filmpack/docs/images'
LICENSE = ROOT / 'mod/nxconnect/third_party/cxo-filmpack/LICENSE'
STAMP = datetime.now().strftime('%Y%m%dT%H%M%S')
OUT = ROOT / 'outputs/looks/library-review' / f'{STAMP}-CXO_APP_FULL'

SCENES = (
    ('人像', SAMPLES / '34_09_before.jpg', False),
    ('城市', SAMPLES / '113_01_before.jpg', False),
    ('自然', DOC / 'raw-landscape.jpg', True),
    ('夜景', DOC / 'raw-sony.jpg', True),
)

DIRECT_CAMERA = {
    'cxo-digital-fujirealaace',
    'cxo-kodakelitechrome200',
    'cxo-kodakkodachrome25',
    'cxo-adoxsilvermax21',
    'cxo-fujieternableachbypass',
}

INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype('C:/Windows/Fonts/msyhbd.ttc' if bold else 'C:/Windows/Fonts/msyh.ttc', size)


def safe(value: str, limit: int = 64) -> str:
    return (INVALID.sub('_', ' '.join(value.split())).strip(' .') or 'item')[:limit].rstrip(' .')


def repair(value: str) -> str:
    if not any(mark in value for mark in ('鍙', '鏃', '锛', '銆', '鐨', '瀵', '榛', '鑳', '鎴', '缁', '浜')):
        return value
    try:
        return value.encode('gbk').decode('utf-8')
    except UnicodeError:
        return value


def load_scene(path: Path, crop_left: bool) -> np.ndarray:
    image = Image.open(path).convert('RGB')
    if crop_left:
        image = image.crop((0, 58, image.width // 3, image.height))
    image.thumbnail((400, 250), Image.Resampling.LANCZOS)
    canvas = Image.new('RGB', (400, 250), '#0b0f13')
    canvas.paste(image, ((400 - image.width) // 2, (250 - image.height) // 2))
    return np.asarray(canvas, dtype=np.float32) / 255.0


def save(image: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
    image.save(path, quality=93, subsampling=0, optimize=True, icc_profile=icc)


def render_preview(entry: dict, scenes, path: Path) -> None:
    lut = parse_lut_file(MASTERS / entry['cube']).lut
    canvas = Image.new('RGB', (800, 585), '#11161b')
    for index, (_, pixels) in enumerate(scenes):
        rendered = apply_lut(pixels, lut)
        image = Image.fromarray(np.clip(rendered * 255 + 0.5, 0, 255).astype(np.uint8), 'RGB')
        canvas.paste(image, ((index % 2) * 400, (index // 2) * 250))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 500, 799, 584), fill='#1c232a')
    draw.text((12, 509), entry['label'], font=font(18, True), fill='#f5f1e8')
    draw.text((12, 534), entry['camera_id'], font=font(12), fill='#8e9ba8')
    draw.text((12, 556), '当前直接入机' if entry['camera_id'] in DIRECT_CAMERA else 'App 参考 / 未直接入机',
              font=font(12, True), fill='#58b58f' if entry['camera_id'] in DIRECT_CAMERA else '#b3b9bf')
    save(canvas, path)


def contact_sheet(entries: list[dict], previews: dict[str, Path], category: str, path: Path) -> None:
    columns, width, height, gap, margin, header = 4, 400, 292, 10, 24, 104
    rows = (len(entries) + columns - 1) // columns
    canvas = Image.new('RGB', (
        columns * width + (columns - 1) * gap + margin * 2,
        header + rows * (height + gap),
    ), '#11161b')
    draw = ImageDraw.Draw(canvas)
    draw.text((margin, 16), f'CXO App 完整审片 · {category}', font=font(28, True), fill='#f5f1e8')
    draw.text((margin, 56), '四格依次为人像、城市、自然、夜景；固定曝光、白平衡和裁切。', font=font(15), fill='#aeb8c2')
    draw.text((margin, 80), f'共 {len(entries)} 款；绿色标识表示当前直接入机，绿色混合配方不计入。', font=font(13), fill='#81909e')
    for index, entry in enumerate(entries):
        column, row = index % columns, index // columns
        image = Image.open(previews[entry['camera_id']]).convert('RGB').resize((width, height), Image.Resampling.LANCZOS)
        canvas.paste(image, (margin + column * (width + gap), header + row * (height + gap)))
    save(canvas, path)


def main() -> None:
    manifest = json.loads((MASTERS / 'manifest.json').read_text(encoding='utf-8'))
    entries = []
    for entry in manifest['entries']:
        entries.append({
            **entry,
            'category': repair(entry['cxo_category']),
            'label': entry['label'],
        })
    if len(entries) != 153:
        raise ValueError(f'Expected 153 CXO styles, found {len(entries)}')
    scenes = [(label, load_scene(path, crop)) for label, path, crop in SCENES]
    previews = {}
    by_category: defaultdict[str, list[dict]] = defaultdict(list)
    for index, entry in enumerate(entries, 1):
        category = safe(entry['category'])
        target = OUT / 'previews' / category / f'{index:03d}-{safe(entry["camera_id"])}.jpg'
        render_preview(entry, scenes, target)
        previews[entry['camera_id']] = target
        by_category[entry['category']].append(entry)
        if index % 25 == 0:
            print(f'rendered {index}/{len(entries)}', flush=True)
    sheets = []
    for index, (category, category_entries) in enumerate(sorted(by_category.items()), 1):
        target = OUT / 'contact-sheets' / f'{index:02d}-{safe(category)}.jpg'
        contact_sheet(category_entries, previews, category, target)
        sheets.append(target)
    lines = ['category\tlabel\tstable_id\tsource_id\tdirect_camera\tkeep\tnote']
    for entry in entries:
        lines.append('\t'.join((
            entry['category'], entry['label'], entry['camera_id'], entry['source_id'],
            'yes' if entry['camera_id'] in DIRECT_CAMERA else 'no', '', '',
        )))
    (OUT / 'selection.tsv').write_text('\n'.join(lines) + '\n', encoding='utf-8', newline='\n')
    shutil.copy2(LICENSE, OUT / 'LICENSE')
    (OUT / 'README.md').write_text(
        '# CXO FilmPack 完整审片\n\n'
        '共 153 款；全部来自 CXO FilmPack 1.0.1 的固定 sRGB 管线，33×33×33 导出。\n\n'
        '预览使用同一张真实人像、城市、自然和夜景素材，固定曝光、白平衡与裁切。\n\n'
        '绿色标识仅表示该 CXO 原表当前直接入机；相机中的混合配方不等同于直接使用完整 CXO 表。\n\n'
        'CXO FilmPack 仅作非商业使用；完整授权文本随包附带。\n',
        encoding='utf-8', newline='\n',
    )
    archive = OUT.with_suffix('.zip')
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for path in sorted(OUT.rglob('*')):
            if path.is_file():
                bundle.write(path, Path(OUT.name) / path.relative_to(OUT))
    with zipfile.ZipFile(archive) as bundle:
        bad = bundle.testzip()
        if bad:
            raise ValueError(f'ZIP verification failed at {bad}')
    result = {
        'count': len(entries),
        'categories': dict(sorted(Counter(entry['category'] for entry in entries).items())),
        'sheets': [path.relative_to(ROOT).as_posix() for path in sheets],
        'package': {'file': archive.relative_to(ROOT).as_posix(), 'bytes': archive.stat().st_size},
    }
    (OUT / 'review.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
