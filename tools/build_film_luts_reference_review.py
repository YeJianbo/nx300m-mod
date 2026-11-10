"""Render all BigNerdCoding/Film-Luts cubes as a separate review package."""
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
SOURCE = ROOT / 'scratch/film-luts-reference'
LUTS = SOURCE / 'luts'
SAMPLES = ROOT / 'scratch/lumixlab/repo/LumixLutSearch-main/app/src/main/assets/songying'
DOC = ROOT / 'mod/nxconnect/third_party/cxo-filmpack/docs/images'
STAMP = datetime.now().strftime('%Y%m%dT%H%M%S')
OUT = ROOT / 'outputs/looks/library-review' / f'{STAMP}-FILM_LUTS_REFERENCE'

SCENES = (
    ('人像', SAMPLES / '34_09_before.jpg', False),
    ('城市', SAMPLES / '113_01_before.jpg', False),
    ('自然', DOC / 'raw-landscape.jpg', True),
    ('夜景', DOC / 'raw-sony.jpg', True),
)
CATEGORY_NAMES = {
    'bw': '黑白',
    'colorslide': '彩色正片',
    'fujixtransiii': '富士 X-Trans III',
    'instant_consumer': '即时成像消费级',
    'instant_pro': '即时成像专业级',
    'negative_color': '彩色负片',
    'negative_new': '新版负片',
    'negative_old': '旧版负片',
    'print': '电影打印',
}
INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype('C:/Windows/Fonts/msyhbd.ttc' if bold else 'C:/Windows/Fonts/msyh.ttc', size)


def safe(value: str, limit: int = 64) -> str:
    return (INVALID.sub('_', ' '.join(value.split())).strip(' .') or 'item')[:limit].rstrip(' .')


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


def preview(entry: dict, scenes: list[tuple[str, np.ndarray]], path: Path) -> None:
    lut = parse_lut_file(entry['path']).lut
    canvas = Image.new('RGB', (800, 555), '#11161b')
    for index, (_, pixels) in enumerate(scenes):
        rendered = apply_lut(pixels, lut)
        image = Image.fromarray(np.clip(rendered * 255 + 0.5, 0, 255).astype(np.uint8), 'RGB')
        canvas.paste(image, ((index % 2) * 400, (index // 2) * 250))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 500, 799, 554), fill='#1c232a')
    draw.text((12, 510), entry['label'], font=font(18, True), fill='#f5f1e8')
    draw.text((12, 535), entry['source_file'], font=font(12), fill='#8e9ba8')
    save(canvas, path)


def contact_sheet(entries: list[dict], previews: dict[str, Path], category: str, path: Path) -> None:
    columns, width, height, gap, margin, header = 4, 400, 278, 10, 24, 104
    rows = (len(entries) + columns - 1) // columns
    canvas = Image.new('RGB', (
        columns * width + (columns - 1) * gap + margin * 2,
        header + rows * (height + gap),
    ), '#11161b')
    draw = ImageDraw.Draw(canvas)
    draw.text((margin, 16), f'Film-Luts 参考审片 · {category}', font=font(28, True), fill='#f5f1e8')
    draw.text((margin, 56), '四格依次为人像、城市、自然、夜景；固定曝光、白平衡和裁切。', font=font(15), fill='#aeb8c2')
    draw.text((margin, 80), f'共 {len(entries)} 款；这些尚未入机。', font=font(13), fill='#81909e')
    for index, entry in enumerate(entries):
        column, row = index % columns, index // columns
        image = Image.open(previews[entry['id']]).convert('RGB').resize((width, height), Image.Resampling.LANCZOS)
        canvas.paste(image, (margin + column * (width + gap), header + row * (height + gap)))
    save(canvas, path)


def main() -> None:
    entries = []
    for path in sorted(LUTS.rglob('*.cube')):
        category = path.parent.name
        stem = path.stem
        identifier = f'film-luts-{category}-{stem}'.lower().replace('_', '-').replace('+', 'plus')
        entries.append({
            'id': identifier,
            'label': stem.replace('_', ' ').title(),
            'category': CATEGORY_NAMES.get(category, category),
            'path': path,
            'source_file': f'luts/{category}/{path.name}',
        })
    if len(entries) != 296:
        raise ValueError(f'Expected 296 Film-Luts cubes, found {len(entries)}')
    scenes = [(label, load_scene(path, crop)) for label, path, crop in SCENES]
    previews = {}
    by_category: defaultdict[str, list[dict]] = defaultdict(list)
    for index, entry in enumerate(entries, 1):
        target = OUT / 'previews' / safe(entry['category']) / f'{index:03d}-{safe(entry["id"])}.jpg'
        preview(entry, scenes, target)
        previews[entry['id']] = target
        by_category[entry['category']].append(entry)
        if index % 25 == 0:
            print(f'rendered {index}/{len(entries)}', flush=True)
    sheets = []
    for index, (category, category_entries) in enumerate(sorted(by_category.items()), 1):
        target = OUT / 'contact-sheets' / f'{index:02d}-{safe(category)}.jpg'
        contact_sheet(category_entries, previews, category, target)
        sheets.append(target)
    inventory = ['category\tlabel\tstable_id\tsource_file\tkeep\tnote']
    for entry in entries:
        inventory.append('\t'.join((entry['category'], entry['label'], entry['id'], entry['source_file'], '', '')))
    (OUT / 'selection.tsv').write_text('\n'.join(inventory) + '\n', encoding='utf-8', newline='\n')
    shutil.copy2(SOURCE / 'LICENSE', OUT / 'LICENSE')
    (OUT / 'README.md').write_text(
        '# Film-Luts 参考审片\n\n'
        '来源：https://github.com/BigNerdCoding/Film-Luts\n\n'
        f'提交：af957b631a304e6be94778b96cb3395de3438e9f\n\n'
        f'共 {len(entries)} 个 `.cube`，按原仓库分类生成人像、城市、自然、夜景四场景预览。\n\n'
        '这些素材尚未进入相机目录；MIT 许可随包附带。README 同时声明部分底片名称和资产可能涉及第三方权利，发布前需复核。\n',
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
