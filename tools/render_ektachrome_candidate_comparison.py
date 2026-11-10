"""Compare current and refined Ektachrome camera recipes across scenes."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageCms, ImageDraw, ImageFont

from nx300m_lut_core import apply_lut, parse_lut_file


ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / 'scratch/lumixlab/repo/LumixLutSearch-main/app/src/main/assets/songying'
DOC = ROOT / 'mod/nxconnect/third_party/cxo-filmpack/docs/images'
OUT = ROOT / 'outputs/looks/cxo-import/comparison/ektachrome-candidate-comparison.jpg'

SCENES = (
    ('暖光人像', SAMPLES / '34_09_before.jpg', False),
    ('城市街景', SAMPLES / '113_01_before.jpg', False),
    ('自然风景', DOC / 'raw-landscape.jpg', True),
    ('城市夜景', DOC / 'raw-sony.jpg', True),
)

VARIANTS = (
    ('原图', None, '#e3c36b'),
    ('旧版 Ektachrome', 'metol-ektachrome', '#81909e'),
    ('当前入机 85% mild', 'blend-metol-ektachrome-cxo85-mild', '#d89a43'),
    ('细修候选 85% refined', 'blend-metol-ektachrome-cxo85-refined', '#4f9d8a'),
    ('CXO 100%', 'cxo-kodake100gxektachrome100', '#2fa681'),
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
    columns = len(VARIANTS)
    tile_width, image_height, label_height, row_gap, margin = 320, 240, 58, 10, 26
    header_height = 110
    row_height = image_height + label_height + row_gap
    width = margin * 2 + columns * tile_width
    height = header_height + row_height * len(SCENES)
    canvas = Image.new('RGB', (width, height), '#11161b')
    draw = ImageDraw.Draw(canvas)
    title = ImageFont.truetype('C:/Windows/Fonts/msyhbd.ttc', 30)
    subtitle = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 16)
    label = ImageFont.truetype('C:/Windows/Fonts/msyhbd.ttc', 16)
    small = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 13)

    draw.text((margin, 17), 'Ektachrome 85% 当前版与细修候选', font=title, fill='#f5f1e8')
    draw.text(
        (margin, 60),
        '固定曝光、白平衡和裁切；重点看洋红是否减少、暗部是否更开、青色是否更自然。',
        font=subtitle,
        fill='#aeb8c2',
    )
    draw.text((margin, 84), 'refined 尚未入机，仅用于本轮比较。', font=small, fill='#81909e')

    luts = {}
    for _, key, _ in VARIANTS:
        if key:
            source = (ROOT / 'looks' / key / 'source.cube') if key.startswith(('blend-', 'metol-')) else (
                ROOT / 'outputs/looks/cxo-import/comparison-masters' / 'cxo-kodake100gxektachrome100.cube'
            )
            luts[key] = parse_lut_file(source).lut

    for row, (scene_label, path, crop) in enumerate(SCENES):
        source = load_rgb(path, crop)
        top = header_height + row * row_height
        for column, (variant_label, key, accent) in enumerate(VARIANTS):
            left = margin + column * tile_width
            pixels = source if key is None else apply_lut(source, luts[key])
            draw.rectangle((left, top, left + tile_width - 1, top + image_height + label_height), fill='#1c232a')
            draw.rectangle((left, top, left + tile_width - 1, top + 4), fill=accent)
            canvas.paste(render_image(pixels, (tile_width, image_height)), (left, top + 4))
            draw.text((left + 9, top + image_height + 14), scene_label, font=small, fill='#8e9ba8')
            draw.text((left + 9, top + image_height + 32), variant_label, font=label, fill='#f5f1e8')

    OUT.parent.mkdir(parents=True, exist_ok=True)
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
    canvas.save(OUT, quality=95, subsampling=0, optimize=True, icc_profile=icc)
    print(OUT.relative_to(ROOT))


if __name__ == '__main__':
    main()
