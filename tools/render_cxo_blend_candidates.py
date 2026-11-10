"""Render blended NX300M candidates for overlapping existing/CXO looks."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageCms, ImageDraw, ImageFont

from nx300m_lut_core import apply_lut, blend_luts, parse_lut_file


ROOT = Path(__file__).resolve().parents[1]
CXO = ROOT / 'outputs/looks/cxo-import/comparison-masters'
SOURCE = ROOT / 'scratch/lumixlab/repo/LumixLutSearch-main/app/src/main/assets/songying/34_09_before.jpg'
OUT = ROOT / 'outputs/looks/cxo-import/comparison/cxo-blend-candidates.jpg'

PAIRS = (
    ('ColorModeArgentiqueFujiVelvia50', 'metol-velvia50', 'Velvia 50'),
    ('ColorModeArgentiqueKodakTriX400', 'metol-trix400', 'Tri-X 400'),
    ('ColorModeDigitalFujiClassicChrome', 'nx300-fuji-classic-chrome', 'Classic Chrome'),
    ('ColorModeDigitalFujiAstiaSoft', 'nx300-fuji-astia', 'Astia'),
    ('ColorModeDigitalFujiEterna', 'nx300-fuji-eterna', 'Eterna'),
    ('ColorModeDigitalFujiClassicNeg', 'nx300-fuji-classic-negative', 'Classic Negative'),
)


def srgb_save(image: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
    image.save(path, quality=95, subsampling=0, optimize=True, icc_profile=icc)


def image_of(array: np.ndarray, size: tuple[int, int]) -> Image.Image:
    image = Image.fromarray(np.clip(array * 255 + 0.5, 0, 255).astype(np.uint8), 'RGB')
    image.thumbnail(size, Image.Resampling.LANCZOS)
    canvas = Image.new('RGB', size, '#11161b')
    canvas.paste(image, ((size[0] - image.width) // 2, (size[1] - image.height) // 2))
    return canvas


def main() -> None:
    manifest = json.loads((CXO / 'manifest.json').read_text(encoding='utf-8'))
    entries = {entry['source_id']: entry for entry in manifest['entries']}
    source = Image.open(SOURCE).convert('RGB')
    original = np.asarray(source, dtype=np.float32) / 255.0

    columns = 5
    tile_width, image_height = 350, 164
    label_height, row_gap, margin = 52, 12, 28
    header_height = 118
    row_height = image_height + label_height + row_gap
    size = (columns * tile_width + 2 * margin, header_height + row_gap + len(PAIRS) * row_height)
    canvas = Image.new('RGB', size, '#11161b')
    draw = ImageDraw.Draw(canvas)
    title_font = ImageFont.truetype('C:/Windows/Fonts/msyhbd.ttc', 30)
    subtitle_font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 16)
    label_font = ImageFont.truetype('C:/Windows/Fonts/msyhbd.ttc', 16)
    detail_font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 12)
    draw.text((margin, 20), 'CXO 同名风格中和测试', font=title_font, fill='#f5f1e8')
    draw.text(
        (margin, 62),
        '同一人物样本：原图 / 现有 / 50% CXO / 70% CXO / 100% CXO。百分比是和现有表混合后的 CXO 权重。',
        font=subtitle_font,
        fill='#aeb8c2',
    )

    for row, (source_id, existing_id, name) in enumerate(PAIRS):
        top = header_height + row * row_height
        cxo = parse_lut_file(CXO / entries[source_id]['cube']).lut
        existing = parse_lut_file(ROOT / 'looks' / existing_id / 'source.cube').lut
        blend50 = blend_luts(existing, cxo, 0.50, title=name + ' 50')
        blend70 = blend_luts(existing, cxo, 0.70, title=name + ' 70')
        cells = (
            ('原图', original, '#e3c36b'),
            ('现有 · ' + name, apply_lut(original, existing), '#81909e'),
            ('中和 50% · ' + name, apply_lut(original, blend50), '#d89a43'),
            ('中和 70% · ' + name, apply_lut(original, blend70), '#4f9d8a'),
            ('CXO 100% · ' + name, apply_lut(original, cxo), '#2fa681'),
        )
        for column, (label, pixels, accent) in enumerate(cells):
            left = margin + column * tile_width
            draw.rectangle((left, top, left + tile_width - 1, top + image_height + label_height), fill='#1c232a')
            draw.rectangle((left, top, left + tile_width - 1, top + 4), fill=accent)
            canvas.paste(image_of(pixels, (tile_width, image_height)), (left, top + 4))
            draw.text((left + 10, top + image_height + 13), label, font=label_font, fill='#f5f1e8')
            detail = '无 LUT' if column == 0 else (
                '旧版 100%' if column == 1 else f'CXO {50 if column == 2 else 70 if column == 3 else 100}%'
            )
            draw.text((left + 10, top + image_height + 36), detail, font=detail_font, fill='#8e9ba8')

    srgb_save(canvas, OUT)
    print(OUT.relative_to(ROOT))


if __name__ == '__main__':
    main()
