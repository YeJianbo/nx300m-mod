"""Render enlarged existing-vs-CXO skin-tone comparisons."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageCms, ImageDraw, ImageFont

from nx300m_lut_core import apply_lut, parse_lut_file


ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / 'scratch/lumixlab/repo/LumixLutSearch-main/app/src/main/assets/songying'
CXO = ROOT / 'outputs/looks/cxo-import/comparison-masters'
OUT = ROOT / 'outputs/looks/cxo-import/comparison/cxo-skin-matrix.jpg'

SAMPLE_FILES = (
    ('暖光近景', '34_09_before.jpg'),
    ('室内侧脸', '32_17_before.jpg'),
    ('室外自然光', '32_19_before.jpg'),
    ('花田强光', '33_22_before.jpg'),
)

PAIRS = (
    ('ColorModeArgentiqueFujiVelvia50', 'metol-velvia50', 'Velvia 50'),
    ('ColorModeArgentiqueKodakTriX400', 'metol-trix400', 'Tri-X 400'),
    ('ColorModeDigitalFujiClassicChrome', 'nx300-fuji-classic-chrome', 'Classic Chrome'),
    ('ColorModeDigitalFujiAstiaSoft', 'nx300-fuji-astia', 'Astia'),
    ('ColorModeDigitalFujiEterna', 'nx300-fuji-eterna', 'Eterna'),
    ('ColorModeDigitalFujiClassicNeg', 'nx300-fuji-classic-negative', 'Classic Negative'),
)


def load_rgb(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert('RGB'), dtype=np.float32) / 255.0


def image_of(array: np.ndarray, size: tuple[int, int]) -> Image.Image:
    image = Image.fromarray(np.clip(array * 255 + 0.5, 0, 255).astype(np.uint8), 'RGB')
    return image.resize(size, Image.Resampling.LANCZOS)


def save_srgb(image: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
    image.save(path, quality=95, subsampling=0, optimize=True, icc_profile=icc)


def main() -> None:
    manifest = json.loads((CXO / 'manifest.json').read_text(encoding='utf-8'))
    entries = {entry['source_id']: entry for entry in manifest['entries']}
    originals = [(label, load_rgb(SAMPLES / filename)) for label, filename in SAMPLE_FILES]

    tile_width, image_height, row_label = 430, 202, 33
    row_gap, margin, header_height = 7, 26, 108
    width = margin * 2 + tile_width * len(SAMPLE_FILES)
    row_height = image_height + row_label + row_gap
    height = header_height + row_height * (len(PAIRS) + 1)
    canvas = Image.new('RGB', (width, height), '#11161b')
    draw = ImageDraw.Draw(canvas)
    title = ImageFont.truetype('C:/Windows/Fonts/msyhbd.ttc', 30)
    subtitle = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 16)
    row_font = ImageFont.truetype('C:/Windows/Fonts/msyhbd.ttc', 18)
    small = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 14)

    draw.text((margin, 17), 'CXO 肤色跨样片对照', font=title, fill='#f5f1e8')
    draw.text(
        (margin, 60),
        '每格左半为现有版，右半为 CXO 100%；四张人像使用相同裁切、亮度与白平衡。',
        font=subtitle,
        fill='#aeb8c2',
    )
    draw.text((margin, 84), '重点观察肤色、白衣服、眼睛高光与暗部过渡。', font=small, fill='#81909e')

    top = header_height
    draw.text((margin, top + 5), '原图基准', font=row_font, fill='#e3c36b')
    for column, (label, pixels) in enumerate(originals):
        left = margin + column * tile_width
        canvas.paste(image_of(pixels, (tile_width, image_height)), (left, top + row_label))
        draw.text((left + 9, top + row_label + image_height - 20), label, font=small, fill='#f5f1e8')

    for row, (source_id, existing_id, name) in enumerate(PAIRS, 1):
        top = header_height + row * row_height
        draw.text((margin, top + 4), name, font=row_font, fill='#f0eee8')
        existing = parse_lut_file(ROOT / 'looks' / existing_id / 'source.cube').lut
        cxo = parse_lut_file(CXO / entries[source_id]['cube']).lut
        for column, (label, pixels) in enumerate(originals):
            left = margin + column * tile_width
            old_image = image_of(apply_lut(pixels, existing), (tile_width, image_height))
            cxo_image = image_of(apply_lut(pixels, cxo), (tile_width, image_height))
            split = cxo_image.copy()
            split.paste(old_image.crop((0, 0, tile_width // 2, image_height)), (0, 0))
            canvas.paste(split, (left, top + row_label))
            draw.line((left + tile_width // 2, top + row_label, left + tile_width // 2, top + row_label + image_height), fill='#f3f1ea', width=2)
            draw.text((left + 9, top + row_label + image_height - 20), label, font=small, fill='#f5f1e8')

    save_srgb(canvas, OUT)
    print(OUT.relative_to(ROOT))


if __name__ == '__main__':
    main()
