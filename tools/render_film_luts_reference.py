"""Compare BigNerdCoding/Film-Luts against current NX300M camera looks."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageCms, ImageDraw, ImageFont

from nx300m_lut_core import apply_lut, parse_lut_file


ROOT = Path(__file__).resolve().parents[1]
REF = ROOT / 'scratch/film-luts-reference/luts'
SAMPLES = ROOT / 'scratch/lumixlab/repo/LumixLutSearch-main/app/src/main/assets/songying'
DOC = ROOT / 'mod/nxconnect/third_party/cxo-filmpack/docs/images'
OUT = ROOT / 'outputs/looks/cxo-import/comparison'

SCENES = (
    ('人像', SAMPLES / '34_09_before.jpg', False),
    ('城市', SAMPLES / '113_01_before.jpg', False),
    ('风景', DOC / 'raw-landscape.jpg', True),
    ('夜景', DOC / 'raw-sony.jpg', True),
)

MATCHED = (
    ('Portra 400', 'blend-metol-portra400-cxo60', REF / 'negative_new/kodak_portra_400.cube'),
    ('Ektachrome 100', 'blend-metol-ektachrome-cxo85-refined', REF / 'colorslide/kodak_e-100_gx_ektachrome_100.cube'),
    ('Velvia 50', 'metol-velvia50-cxo80', REF / 'colorslide/fuji_velvia_50.cube'),
    ('Tri-X 400', 'metol-trix400-cxo50', REF / 'bw/kodak_tri-x_400.cube'),
    ('HP5 Plus', 'metol-hp5', REF / 'bw/ilford_hp_5_plus_400.cube'),
    ('Superia 400', 'blend-metol-superia400-cxo35', REF / 'negative_old/fuji_superia_400.cube'),
    ('Classic Chrome', 'nx300-fuji-classic-chrome-cxo50', REF / 'fujixtransiii/fuji_xtrans_iii_classic_chrome.cube'),
    ('Astia', 'nx300-fuji-astia-cxo50', REF / 'fujixtransiii/fuji_xtrans_iii_astia.cube'),
)

UNIQUE = (
    ('Kodak Ektar 100', REF / 'negative_color/kodak_ektar_100.cube'),
    ('Kodak Portra 800', REF / 'negative_new/kodak_portra_800.cube'),
    ('Fujifilm 400H', REF / 'negative_new/fuji_400h.cube'),
    ('Fujifilm 800Z', REF / 'negative_new/fuji_800z.cube'),
    ('Kodak T-Max 400', REF / 'bw/kodak_t-max_400.cube'),
    ('Ilford Delta 400', REF / 'bw/ilford_delta_400.cube'),
    ('Kodak 2383 Cusp Clip', REF / 'print/kodak_2383_cuspclip.cube'),
    ('Fuji 3510 Cusp Clip', REF / 'print/fuji_3510_cuspclip.cube'),
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


def save(image: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
    image.save(path, quality=95, subsampling=0, optimize=True, icc_profile=icc)


def lut_delta(a, b) -> float:
    points = np.random.default_rng(0xF11A).random((50000, 1, 3), dtype=np.float32)
    return float(np.abs(apply_lut(points, a) - apply_lut(points, b)).mean() * 255.0)


def matched_sheet(samples) -> Path:
    columns, tile_width, image_height = 4, 380, 214
    row_label, gap, margin, header = 41, 10, 26, 108
    row_height = image_height + row_label + gap
    canvas = Image.new('RGB', (margin * 2 + columns * tile_width, header + len(MATCHED) * row_height), '#11161b')
    draw = ImageDraw.Draw(canvas)
    title = ImageFont.truetype('C:/Windows/Fonts/msyhbd.ttc', 30)
    body = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 16)
    row_font = ImageFont.truetype('C:/Windows/Fonts/msyhbd.ttc', 17)
    small = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 12)
    draw.text((margin, 17), '同名 Film-Luts 与当前机内版', font=title, fill='#f5f1e8')
    draw.text((margin, 59), '每格左半为当前机内，右半为 BigNerdCoding/Film-Luts；同一图、同一切换。', font=body, fill='#aeb8c2')
    draw.text((margin, 82), '该仓库 MIT 许可；数值用于选候选，实拍用途仍需自行判断。', font=small, fill='#81909e')
    for row, (name, current_id, reference_path) in enumerate(MATCHED):
        top = header + row * row_height
        current = parse_lut_file(ROOT / 'looks' / current_id / 'source.cube').lut
        reference = parse_lut_file(reference_path).lut
        draw.text((margin, top + 2), f'{name}  VS  Film-Luts', font=row_font, fill='#f5f1e8')
        draw.text((margin + 750, top + 5), f'平均 LUT 差 {lut_delta(current, reference):.1f}', font=small, fill='#8e9ba8')
        for column, (scene, pixels) in enumerate(samples):
            left = margin + column * tile_width
            current_image = render_image(apply_lut(pixels, current), (tile_width, image_height))
            reference_image = render_image(apply_lut(pixels, reference), (tile_width, image_height))
            split = reference_image.copy()
            split.paste(current_image.crop((0, 0, tile_width // 2, image_height)), (0, 0))
            canvas.paste(split, (left, top + row_label))
            draw.line((left + tile_width // 2, top + row_label, left + tile_width // 2, top + row_label + image_height), fill='#f3f1ea', width=2)
            draw.text((left + 8, top + row_label + image_height - 18), scene, font=small, fill='#f5f1e8')
    path = OUT / 'film-luts-matched-current-vs-reference.jpg'
    save(canvas, path)
    return path


def unique_sheet(samples) -> Path:
    columns, tile_width, image_height = 4, 380, 214
    row_label, gap, margin, header = 41, 10, 26, 108
    row_height = image_height + row_label + gap
    canvas = Image.new('RGB', (margin * 2 + columns * tile_width, header + len(UNIQUE) * row_height), '#11161b')
    draw = ImageDraw.Draw(canvas)
    title = ImageFont.truetype('C:/Windows/Fonts/msyhbd.ttc', 30)
    body = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 16)
    row_font = ImageFont.truetype('C:/Windows/Fonts/msyhbd.ttc', 17)
    small = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 12)
    draw.text((margin, 17), 'Film-Luts 中值得测试的不同胶卷', font=title, fill='#f5f1e8')
    draw.text((margin, 59), '从 137 个去重基底中抽取现有机内没有覆盖的负片、黑白和打印胶片。', font=body, fill='#aeb8c2')
    draw.text((margin, 82), '这些尚未入机；先看跨场景表现，再决定是否转成 NX300M 9×9×9。', font=small, fill='#81909e')
    for row, (name, reference_path) in enumerate(UNIQUE):
        top = header + row * row_height
        reference = parse_lut_file(reference_path).lut
        draw.text((margin, top + 8), name, font=row_font, fill='#f5f1e8')
        for column, (scene, pixels) in enumerate(samples):
            left = margin + column * tile_width
            canvas.paste(render_image(apply_lut(pixels, reference), (tile_width, image_height)), (left, top + row_label))
            draw.text((left + 8, top + row_label + image_height - 18), scene, font=small, fill='#f5f1e8')
    path = OUT / 'film-luts-unique-candidates.jpg'
    save(canvas, path)
    return path


def main() -> None:
    samples = [(label, load_rgb(path, crop)) for label, path, crop in SCENES]
    print(matched_sheet(samples).relative_to(ROOT))
    print(unique_sheet(samples).relative_to(ROOT))


if __name__ == '__main__':
    main()
