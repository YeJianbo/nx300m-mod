"""Compare every overlapping CXO/existing look across varied scene types."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageCms, ImageDraw, ImageFont

from nx300m_lut_core import apply_lut, parse_lut_file


ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / 'scratch/lumixlab/repo/LumixLutSearch-main/app/src/main/assets/songying'
DOC_IMAGES = ROOT / 'mod/nxconnect/third_party/cxo-filmpack/docs/images'
CXO = ROOT / 'outputs/looks/cxo-import/comparison-masters'
OUT = ROOT / 'outputs/looks/cxo-import/comparison/cxo-cross-scene-existing-vs-cxo.jpg'

SAMPLE_FILES = (
    ('暖光人像', SAMPLES / '34_09_before.jpg', False),
    ('冷光人像', SAMPLES / '32_17_before.jpg', False),
    ('城市街景', SAMPLES / '113_01_before.jpg', False),
    ('自然风景', DOC_IMAGES / 'raw-landscape.jpg', True),
    ('夜景城市', DOC_IMAGES / 'raw-sony.jpg', True),
    ('室内场景', SAMPLES / '34_08_before.jpg', False),
)

PAIRS = (
    ('ColorModeArgentiqueKodakPortra400', 'metol-portra400', 'Portra 400'),
    ('ColorModeArgentiqueKodakE100GXEktachrome100', 'metol-ektachrome', 'Ektachrome 100'),
    ('ColorModeArgentiqueFujiVelvia50', 'metol-velvia50', 'Velvia 50'),
    ('ColorModeArgentiqueKodakTriX400', 'metol-trix400', 'Tri-X 400'),
    ('ColorModeArgentiqueIllfordHP5Plus400', 'metol-hp5', 'HP5 Plus 400'),
    ('ColorModeArgentiqueFujiSuperiaXtra400', 'metol-superia400', 'Superia X-Tra 400'),
    ('ColorModeDigitalFujiClassicChrome', 'nx300-fuji-classic-chrome', 'Classic Chrome'),
    ('ColorModeDigitalFujiAstiaSoft', 'nx300-fuji-astia', 'Astia'),
    ('ColorModeDigitalFujiEterna', 'nx300-fuji-eterna', 'Eterna'),
    ('ColorModeDigitalFujiClassicNeg', 'nx300-fuji-classic-negative', 'Classic Negative'),
    ('ColorModeDigitalFujiNostalgicNeg', 'nx300-fuji-nostalgic-neg', 'Nostalgic Negative'),
)

CHOSEN_CXO = {
    'ColorModeArgentiqueFujiVelvia50',
    'ColorModeArgentiqueKodakTriX400',
    'ColorModeDigitalFujiClassicChrome',
    'ColorModeDigitalFujiAstiaSoft',
    'ColorModeDigitalFujiEterna',
    'ColorModeDigitalFujiClassicNeg',
}


def load_rgb(path: Path, crop_first_panel: bool = False) -> np.ndarray:
    image = Image.open(path).convert('RGB')
    if crop_first_panel:
        image = image.crop((0, 32, image.width // 3, image.height))
    return np.asarray(image, dtype=np.float32) / 255.0


def rgb_image(array: np.ndarray, size: tuple[int, int]) -> Image.Image:
    image = Image.fromarray(np.clip(array * 255 + 0.5, 0, 255).astype(np.uint8), 'RGB')
    image.thumbnail(size, Image.Resampling.LANCZOS)
    canvas = Image.new('RGB', size, '#0d1115')
    canvas.paste(image, ((size[0] - image.width) // 2, (size[1] - image.height) // 2))
    return canvas


def save_srgb(image: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
    image.save(path, quality=95, subsampling=0, optimize=True, icc_profile=icc)


def main() -> None:
    manifest = json.loads((CXO / 'manifest.json').read_text(encoding='utf-8'))
    entries = {entry['source_id']: entry for entry in manifest['entries']}
    originals = [(label, load_rgb(path, crop)) for label, path, crop in SAMPLE_FILES]

    tile_width, image_height, row_label, gap, margin = 292, 137, 31, 5, 26
    width = margin * 2 + len(SAMPLE_FILES) * tile_width + (len(SAMPLE_FILES) - 1) * gap
    header_height = 108
    row_height = image_height + row_label + gap
    height = header_height + row_height * (len(PAIRS) + 1)
    canvas = Image.new('RGB', (width, height), '#11161b')
    draw = ImageDraw.Draw(canvas)
    title_font = ImageFont.truetype('C:/Windows/Fonts/msyhbd.ttc', 30)
    subtitle_font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 16)
    row_font = ImageFont.truetype('C:/Windows/Fonts/msyhbd.ttc', 16)
    sample_font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 13)

    draw.text((margin, 17), 'CXO 与现有版跨场景对照', font=title_font, fill='#f5f1e8')
    draw.text(
        (margin, 59),
        '每格左侧为现有版，右侧为 CXO 100%；中白线为分界。颜色、亮度和裁切完全一致。',
        font=subtitle_font,
        fill='#aeb8c2',
    )
    draw.text((margin, 84), '覆盖肤色、城市、自然、夜景和室内场景，不只用人像判断。', font=sample_font, fill='#81909e')

    top = header_height
    draw.text((margin, top + 5), '原图基准', font=row_font, fill='#e3c36b')
    for column, ((label, pixels), _) in enumerate(zip(originals, SAMPLE_FILES)):
        left = margin + column * (tile_width + gap)
        canvas.paste(rgb_image(pixels, (tile_width, image_height)), (left, top + row_label))
        draw.text((left + 8, top + image_height + row_label - 18), label, font=sample_font, fill='#f5f1e8')

    for row, (source_id, existing_id, name) in enumerate(PAIRS, 1):
        top = header_height + row * row_height
        verdict = '当前留 CXO' if source_id in CHOSEN_CXO else '当前留现有'
        accent = '#2fa681' if source_id in CHOSEN_CXO else '#d65f4b'
        draw.text((margin, top + 4), name, font=row_font, fill='#f5f1e8')
        draw.text((margin + 220, top + 6), verdict, font=sample_font, fill=accent)
        existing = parse_lut_file(ROOT / 'looks' / existing_id / 'source.cube').lut
        cxo = parse_lut_file(CXO / entries[source_id]['cube']).lut
        for column, ((sample_label, pixels), _) in enumerate(zip(originals, SAMPLE_FILES)):
            left = margin + column * (tile_width + gap)
            old_image = rgb_image(apply_lut(pixels, existing), (tile_width, image_height))
            cxo_image = rgb_image(apply_lut(pixels, cxo), (tile_width, image_height))
            split = cxo_image.copy()
            split.paste(old_image.crop((0, 0, tile_width // 2, image_height)), (0, 0))
            canvas.paste(split, (left, top + row_label))
            draw.line((left + tile_width // 2, top + row_label, left + tile_width // 2, top + row_label + image_height), fill='#f3f1ea', width=2)
            draw.text((left + 7, top + row_label + image_height - 17), sample_label, font=sample_font, fill='#f5f1e8')

    save_srgb(canvas, OUT)
    print(OUT.relative_to(ROOT))


if __name__ == '__main__':
    main()
