"""Render rainy-day botanical-garden comparison sheet for the current in-machine looks.

Usage:
    python tools/render_rainy_botanical_garden.py <photo.jpg> [--out outputs/...]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))

from nx300m_lut_core import apply_lut, parse_lut_file  # noqa: E402

PICKS = [
    ('nx300-fuji-classic-chrome-cxo50', 'CC'),
    ('nx300-fuji-astia-cxo50', 'Astia'),
    ('panasonic-lumix-dusk-moss', '薄暮苔痕'),
    ('cxo-cinedesaturatedgreen', '退绿'),
    ('nx300-fuji-pro-neg-std', 'Pro Neg Std'),
    ('lr-custom-8320bd2bc7f0b8e1026b94f7bf5f2754', '奥林巴斯 柔和'),
    ('blend-metol-portra400-cxo50', 'Portra 400'),
    ('nx300-fuji-eterna-cxo40', 'Eterna'),
    ('nx300-fuji-nostalgic-neg', 'NN'),
    ('lr-custom-63baf27666493ba3bd6e45b05721cda1', 'SATOBI'),
    ('lr-custom-bb2eed1b68c4d7183ac6bb60020fba2b', '九秋'),
    ('cxo-cineautumn', '秋色 Autumn'),
]

FONT = 'C:/Windows/Fonts/msyh.ttc'
BOLD = 'C:/Windows/Fonts/msyhbd.ttc'


def fit(array: np.ndarray, width: int, height: int) -> np.ndarray:
    """Resize to cover the cell and center-crop."""
    im = Image.fromarray(np.rint(np.clip(array, 0, 1) * 255).astype('uint8'))
    scale = max(width / im.width, height / im.height)
    im = im.resize((round(im.width * scale), round(im.height * scale)), Image.Resampling.LANCZOS)
    left = (im.width - width) // 2
    top = (im.height - height) // 2
    return np.asarray(im.crop((left, top, left + width, top + height)), dtype=np.float32) / 255


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('photo')
    parser.add_argument('--out', default='outputs/review/rainy-botanical-garden-comparison.jpg')
    parser.add_argument('--cell', type=int, default=480)
    parser.add_argument('--label', type=int, default=40)
    args = parser.parse_args()

    photo = np.asarray(Image.open(args.photo).convert('RGB'), dtype=np.float32) / 255
    rows = []
    for look_id, label in PICKS:
        cube = ROOT / 'looks' / look_id / 'source.cube'
        lut = parse_lut_file(cube).lut
        out = apply_lut(photo, lut)
        rows.append((f'{label} / {look_id}', out))
        print('rendered', label, flush=True)

    cell = args.cell
    label_h = args.label
    header = 120
    cols = 3
    grid_rows = (len(rows) + cols - 1) // cols
    canvas = Image.new('RGB', (cols * cell, header + grid_rows * (cell + label_h)), (24, 24, 26))
    draw = ImageDraw.Draw(canvas)
    draw.text((24, 24), '中山植物园 · 阴雨天机内 LUT 对比', font=ImageFont.truetype(BOLD, 34), fill=(240, 240, 240))
    draw.text((24, 72), f'原图 {photo.shape[1]}×{photo.shape[0]} · 共 {len(rows)} 款 · 100% 强度',
              font=ImageFont.truetype(FONT, 20), fill=(170, 170, 175))

    original = fit(photo, cell, cell)
    canvas.paste(Image.fromarray(np.rint(original * 255).astype('uint8')), (0, header))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, header, cell, header + label_h), fill=(16, 16, 18))
    draw.text((10, header + 8), '原图', font=ImageFont.truetype(BOLD, 22), fill=(240, 240, 240))

    for index, (label, out) in enumerate(rows):
        row = index // cols
        col = index % cols
        # first cell already used by the original
        row = (index + 1) // cols
        col = (index + 1) % cols
        x = col * cell
        y = header + row * (cell + label_h)
        canvas.paste(Image.fromarray(np.rint(fit(out, cell, cell) * 255).astype('uint8')), (x, y))
        draw.rectangle((x, y + cell, x + cell, y + cell + label_h), fill=(16, 16, 18))
        draw.text((x + 10, y + cell + 8), label, font=ImageFont.truetype(FONT, 20), fill=(235, 235, 235))

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, quality=92)
    print(str(out_path))


if __name__ == '__main__':
    main()
