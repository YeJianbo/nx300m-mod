"""Render 30/50/70 percent CXO blend directions on several scene types."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageCms, ImageDraw, ImageFont

from nx300m_lut_core import apply_lut, blend_luts, parse_lut_file


ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / 'scratch/lumixlab/repo/LumixLutSearch-main/app/src/main/assets/songying'
DOC_IMAGES = ROOT / 'mod/nxconnect/third_party/cxo-filmpack/docs/images'
CXO = ROOT / 'outputs/looks/cxo-import/comparison-masters'
OUT = ROOT / 'outputs/looks/cxo-import/comparison'

SCENES = (
    ('portrait', '暖光人像', SAMPLES / '34_09_before.jpg', False),
    ('city', '城市街景', SAMPLES / '113_01_before.jpg', False),
    ('landscape', '自然风景', DOC_IMAGES / 'raw-landscape.jpg', True),
    ('night', '城市夜景', DOC_IMAGES / 'raw-sony.jpg', True),
)

PAIRS = (
    ('ColorModeArgentiqueFujiVelvia50', 'metol-velvia50', 'Velvia 50'),
    ('ColorModeArgentiqueKodakTriX400', 'metol-trix400', 'Tri-X 400'),
    ('ColorModeDigitalFujiClassicChrome', 'nx300-fuji-classic-chrome', 'Classic Chrome'),
    ('ColorModeDigitalFujiAstiaSoft', 'nx300-fuji-astia', 'Astia'),
    ('ColorModeDigitalFujiEterna', 'nx300-fuji-eterna', 'Eterna'),
    ('ColorModeDigitalFujiClassicNeg', 'nx300-fuji-classic-negative', 'Classic Negative'),
)


def load_rgb(path: Path, crop_first_panel: bool = False) -> np.ndarray:
    image = Image.open(path).convert('RGB')
    if crop_first_panel:
        image = image.crop((0, 58, image.width // 3, image.height))
    return np.asarray(image, dtype=np.float32) / 255.0


def image_of(array: np.ndarray, size: tuple[int, int]) -> Image.Image:
    image = Image.fromarray(np.clip(array * 255 + 0.5, 0, 255).astype(np.uint8), 'RGB')
    image.thumbnail(size, Image.Resampling.LANCZOS)
    canvas = Image.new('RGB', size, '#0d1115')
    canvas.paste(image, ((size[0] - image.width) // 2, (size[1] - image.height) // 2))
    return canvas


def save_srgb(image: Image.Image, path: Path) -> None:
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
    image.save(path, quality=95, subsampling=0, optimize=True, icc_profile=icc)


def render_scene(
    key: str,
    scene_label: str,
    source: np.ndarray,
    entries: dict[str, dict],
) -> Path:
    columns = 6
    tile_width, image_height, label_height, row_gap, margin = 292, 220, 52, 10, 26
    header_height = 105
    row_height = image_height + label_height + row_gap
    width = margin * 2 + columns * tile_width
    height = header_height + row_height * len(PAIRS)
    canvas = Image.new('RGB', (width, height), '#11161b')
    draw = ImageDraw.Draw(canvas)
    title_font = ImageFont.truetype('C:/Windows/Fonts/msyhbd.ttc', 30)
    subtitle_font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 16)
    label_font = ImageFont.truetype('C:/Windows/Fonts/msyhbd.ttc', 16)
    detail_font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 13)
    draw.text((margin, 18), f'CXO 混合比例方向 · {scene_label}', font=title_font, fill='#f5f1e8')
    draw.text(
        (margin, 62),
        '现有版为基础依次加入 CXO：30% / 50% / 70%；最右为 CXO 100%。亮度、白平衡和裁切一致。',
        font=subtitle_font,
        fill='#aeb8c2',
    )

    for row, (source_id, existing_id, name) in enumerate(PAIRS):
        top = header_height + row * row_height
        existing = parse_lut_file(ROOT / 'looks' / existing_id / 'source.cube').lut
        cxo = parse_lut_file(CXO / entries[source_id]['cube']).lut
        blend30 = blend_luts(existing, cxo, 0.30, title=name + ' 30')
        blend50 = blend_luts(existing, cxo, 0.50, title=name + ' 50')
        blend70 = blend_luts(existing, cxo, 0.70, title=name + ' 70')
        variants = (
            ('原图', source, '#e3c36b'),
            ('现有 · ' + name, apply_lut(source, existing), '#81909e'),
            ('30% CXO · ' + name, apply_lut(source, blend30), '#d89a43'),
            ('50% CXO · ' + name, apply_lut(source, blend50), '#c48655'),
            ('70% CXO · ' + name, apply_lut(source, blend70), '#4f9d8a'),
            ('CXO 100% · ' + name, apply_lut(source, cxo), '#2fa681'),
        )
        for column, (label, pixels, accent) in enumerate(variants):
            left = margin + column * tile_width
            draw.rectangle((left, top, left + tile_width - 1, top + image_height + label_height), fill='#1c232a')
            draw.rectangle((left, top, left + tile_width - 1, top + 4), fill=accent)
            canvas.paste(image_of(pixels, (tile_width, image_height)), (left, top + 4))
            draw.text((left + 9, top + image_height + 15), scene_label, font=detail_font, fill='#8e9ba8')
            draw.text((left + 9, top + image_height + 32), label, font=label_font, fill='#f5f1e8')

    path = OUT / f'cxo-ratio-{key}.jpg'
    save_srgb(canvas, path)
    return path


def main() -> None:
    manifest = json.loads((CXO / 'manifest.json').read_text(encoding='utf-8'))
    entries = {entry['source_id']: entry for entry in manifest['entries']}
    paths = []
    for key, label, source_path, crop in SCENES:
        paths.append(render_scene(key, label, load_rgb(source_path, crop), entries))
    print(json.dumps([str(path.relative_to(ROOT)) for path in paths], ensure_ascii=False))


if __name__ == '__main__':
    main()
