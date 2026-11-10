"""Evaluate a 50/50 merge of the two closest camera everyday looks."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageCms, ImageDraw, ImageFont

from nx300m_lut_core import apply_lut, blend_luts, parse_lut_file


ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / 'scratch/lumixlab/repo/LumixLutSearch-main/app/src/main/assets/songying'
DOC = ROOT / 'mod/nxconnect/third_party/cxo-filmpack/docs/images'
OUT = ROOT / 'outputs/looks/cxo-import/comparison/skin-daylight-merge.jpg'

SCENES = (
    ('暖光人像', SAMPLES / '34_09_before.jpg', False),
    ('城市街景', SAMPLES / '113_01_before.jpg', False),
    ('自然风景', DOC / 'raw-landscape.jpg', True),
    ('城市夜景', DOC / 'raw-sony.jpg', True),
)

SKIN = 'panasonic-lumix-skin-tone-boost'
DAYLIGHT = 'lr-custom-10e69cdb47b694fd2929e8ce7cacde7d'


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
    skin = parse_lut_file(ROOT / 'looks' / SKIN / 'source.cube').lut
    daylight = parse_lut_file(ROOT / 'looks' / DAYLIGHT / 'source.cube').lut
    merged = blend_luts(skin, daylight, 0.5, title='肤色优化 + 日光 50%')
    variants = (
        ('原图', None, '#e3c36b'),
        ('肤色优化', skin, '#d89a43'),
        ('合并候选 50% + 50%', merged, '#4f9d8a'),
        ('日光', daylight, '#2fa681'),
    )

    columns = len(variants)
    tile_width, image_height, label_height, gap, margin = 360, 270, 59, 8, 26
    header_height = 110
    row_height = image_height + label_height + gap
    canvas = Image.new('RGB', (
        margin * 2 + columns * tile_width,
        header_height + len(SCENES) * row_height,
    ), '#11161b')
    draw = ImageDraw.Draw(canvas)
    title = ImageFont.truetype('C:/Windows/Fonts/msyhbd.ttc', 30)
    subtitle = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 16)
    label = ImageFont.truetype('C:/Windows/Fonts/msyhbd.ttc', 16)
    small = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 13)
    draw.text((margin, 17), '肤色优化 + 日光：混合候选', font=title, fill='#f5f1e8')
    draw.text(
        (margin, 60),
        '两者 50% + 50% 输出混合；固定曝光、白平衡和裁切，观察是否值得合并成一条。',
        font=subtitle,
        fill='#aeb8c2',
    )
    draw.text((margin, 84), '重点：肤色是否更稳、白墙是否仍中性、夜景高光是否多出偏色。', font=small, fill='#81909e')

    for row, (scene, path, crop) in enumerate(SCENES):
        source = load_rgb(path, crop)
        top = header_height + row * row_height
        for column, (name, lut, accent) in enumerate(variants):
            left = margin + column * tile_width
            pixels = source if lut is None else apply_lut(source, lut)
            draw.rectangle((left, top, left + tile_width - 1, top + image_height + label_height), fill='#1c232a')
            draw.rectangle((left, top, left + tile_width - 1, top + 4), fill=accent)
            canvas.paste(render_image(pixels, (tile_width, image_height)), (left, top + 4))
            draw.text((left + 9, top + image_height + 14), scene, font=small, fill='#8e9ba8')
            draw.text((left + 9, top + image_height + 32), name, font=label, fill='#f5f1e8')

    OUT.parent.mkdir(parents=True, exist_ok=True)
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
    canvas.save(OUT, quality=95, subsampling=0, optimize=True, icc_profile=icc)
    print(OUT.relative_to(ROOT))


if __name__ == '__main__':
    main()
