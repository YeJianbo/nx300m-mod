"""Render the closest current camera LUT pairs across representative scenes."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageCms, ImageDraw, ImageFont

from nx300m_lut_core import apply_lut, parse_lut_file


ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / 'scratch/lumixlab/repo/LumixLutSearch-main/app/src/main/assets/songying'
DOC = ROOT / 'mod/nxconnect/third_party/cxo-filmpack/docs/images'
OVERLAP = ROOT / 'outputs/looks/cxo-import/comparison/camera-lut-overlap.json'
OUT = ROOT / 'outputs/looks/cxo-import/comparison/camera-overlap-candidates.jpg'

SCENES = (
    ('人像', SAMPLES / '34_09_before.jpg', False),
    ('城市', SAMPLES / '113_01_before.jpg', False),
    ('风景', DOC / 'raw-landscape.jpg', True),
    ('夜景', DOC / 'raw-sony.jpg', True),
)

PAIRS = (
    ('panasonic-lumix-skin-tone-boost', '肤色优化', 'lr-custom-10e69cdb47b694fd2929e8ce7cacde7d', '日光'),
    ('panasonic-lumix-skin-tone-boost', '肤色优化', 'custom-soft-bright', '柔和明亮'),
    ('lr-custom-10e69cdb47b694fd2929e8ce7cacde7d', '日光', 'custom-soft-bright', '柔和明亮'),
    ('nx300-fuji-classic-chrome-cxo50', 'Classic Chrome · CXO 50%', 'custom-soft-bright', '柔和明亮'),
    ('blend-metol-superia400-cxo35', 'Superia 400 · CXO 35%', 'custom-soft-bright', '柔和明亮'),
    ('cxo-digital-fujirealaace', 'CXO Reala Ace', 'lr-custom-0a5d551f2e54e746337ef3cf6da1c1f1', '日出淡影'),
    ('blend-metol-ektachrome-cxo85-mild', 'Ektachrome 100 · CXO 85%', 'cxo-kodakelitechrome200', 'CXO Elite Chrome 200'),
    ('blend-metol-portra400-cxo60', 'Portra 400 · CXO 60%', 'blend-metol-superia400-cxo35', 'Superia 400 · CXO 35%'),
    ('nx300-fuji-eterna-cxo40', 'Eterna · CXO 40%', 'lr-custom-63baf27666493ba3bd6e45b05721cda1', '宾得 SATOBI'),
    ('nx300-fuji-classic-chrome-cxo50', 'Classic Chrome · CXO 50%', 'nx300-fuji-nostalgic-neg', '富士 NN'),
)


def load_rgb(path: Path, crop_left: bool) -> np.ndarray:
    image = Image.open(path).convert('RGB')
    if crop_left:
        image = image.crop((0, 58, image.width // 3, image.height))
    return np.asarray(image, dtype=np.float32) / 255.0


def render_image(array: np.ndarray, size: tuple[int, int]) -> Image.Image:
    image = Image.fromarray(np.clip(array * 255 + 0.5, 0, 255).astype(np.uint8), 'RGB')
    image.thumbnail(size, Image.Resampling.LANCZOS)
    canvas = Image.new('RGB', size, '#0d1115')
    canvas.paste(image, ((size[0] - image.width) // 2, (size[1] - image.height) // 2))
    return canvas


def main() -> None:
    samples = [(label, load_rgb(path, crop)) for label, path, crop in SCENES]
    pair_metrics = {
        frozenset((row['a'], row['b'])): row
        for row in json.loads(OVERLAP.read_text(encoding='utf-8'))['pairs']
    }
    lut_cache = {}

    def lut(key: str):
        if key not in lut_cache:
            lut_cache[key] = parse_lut_file(ROOT / 'looks' / key / 'source.cube').lut
        return lut_cache[key]

    columns = len(samples)
    tile_width, image_height, row_label, row_gap, margin = 360, 203, 43, 9, 26
    header_height = 104
    row_height = image_height + row_label + row_gap
    width = margin * 2 + columns * tile_width
    height = header_height + row_height * len(PAIRS)
    canvas = Image.new('RGB', (width, height), '#11161b')
    draw = ImageDraw.Draw(canvas)
    title_font = ImageFont.truetype('C:/Windows/Fonts/msyhbd.ttc', 30)
    body_font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 15)
    row_font = ImageFont.truetype('C:/Windows/Fonts/msyhbd.ttc', 17)
    small_font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 12)
    draw.text((margin, 17), '当前入机 LUT 近似项对照', font=title_font, fill='#f5f1e8')
    draw.text(
        (margin, 59),
        '每格左半为 A、右半为 B；中间白线分界。先看人像与城市，再看风景和夜景。',
        font=body_font,
        fill='#aeb8c2',
    )
    draw.text((margin, 82), '数值只用于挑候选，最终是否合并看不同场景是否仍有独立用途。', font=small_font, fill='#81909e')

    for row, (a, a_label, b, b_label) in enumerate(PAIRS):
        top = header_height + row * row_height
        metric = pair_metrics[frozenset((a, b))]
        draw.text((margin, top + 3), f'{a_label}  VS  {b_label}', font=row_font, fill='#f5f1e8')
        draw.text(
            (margin + 720, top + 5),
            f"平均差 {metric['mean_abs_8bit']:.1f} / P95 {metric['p95_pixel_abs_8bit']:.1f}（8-bit）",
            font=small_font,
            fill='#8e9ba8',
        )
        for column, (scene_label, pixels) in enumerate(samples):
            left = margin + column * tile_width
            image_a = render_image(apply_lut(pixels, lut(a)), (tile_width, image_height))
            image_b = render_image(apply_lut(pixels, lut(b)), (tile_width, image_height))
            split = image_b.copy()
            split.paste(image_a.crop((0, 0, tile_width // 2, image_height)), (0, 0))
            canvas.paste(split, (left, top + row_label))
            draw.line((left + tile_width // 2, top + row_label, left + tile_width // 2, top + row_label + image_height), fill='#f3f1ea', width=2)
            draw.text((left + 8, top + row_label + image_height - 19), scene_label, font=small_font, fill='#f5f1e8')

    OUT.parent.mkdir(parents=True, exist_ok=True)
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
    canvas.save(OUT, quality=95, subsampling=0, optimize=True, icc_profile=icc)
    print(OUT.relative_to(ROOT))


if __name__ == '__main__':
    main()
