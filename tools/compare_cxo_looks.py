"""Compare overlapping CXO and existing NX300M looks on one real NX300M RAW."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageCms, ImageDraw, ImageFont
import rawpy

from cube_to_d4 import compile_cube, load_cube
from lut_resample import sample_table
from nx300m_lut_core import apply_lut, parse_lut_file


ROOT = Path(__file__).resolve().parents[1]
SRW = ROOT / 'scratch/raw-e2e/Samsung-NX300M.SRW'
CXO = ROOT / 'outputs/looks/cxo-import/comparison-masters'
OUTPUT = ROOT / 'outputs/looks/cxo-import/comparison'
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


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def decode_raw() -> np.ndarray:
    if not SRW.is_file():
        raise FileNotFoundError(SRW)
    with rawpy.imread(str(SRW)) as raw:
        image = raw.postprocess(
            use_camera_wb=True,
            no_auto_bright=True,
            auto_bright_thr=0.01,
            output_color=rawpy.ColorSpace.sRGB,
            output_bps=8,
            half_size=True,
            user_flip=0,
        )
    picture = Image.fromarray(image, 'RGB')
    picture.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
    return np.asarray(picture, dtype=np.float32) / 255.0


def native_error(path: Path) -> dict:
    cube = load_cube(path)
    native = compile_cube(cube, method='round')
    rng = np.random.default_rng(0xC0F1)
    points = rng.random((16000, 3), dtype=np.float32)
    source = parse_lut_file(path).lut
    target = apply_lut(points[:, None, :], source)[:, 0, :]
    sampled = sample_table(native, points, interpolation='trilinear')
    error = np.abs(target - sampled)
    return {
        'native_round_rgb_rmse': float(np.sqrt(np.mean(error ** 2)) * 255.0),
        'native_round_rgb_max': float(np.max(error) * 255.0),
        'source_grid': int(cube[0]),
    }


def image_metrics(original: np.ndarray, rendered: np.ndarray, monochrome: bool) -> dict:
    delta = np.abs(rendered - original)
    channel_spread = rendered.max(axis=2) - rendered.min(axis=2)
    gray = np.repeat(np.linspace(0, 1, 257, dtype=np.float32)[:, None, None], 3, axis=2)
    gray_rendered = apply_lut(gray, parse_lut_file_from_cache.current)
    gray_spread = gray_rendered.max(axis=2) - gray_rendered.min(axis=2)
    gray_step = np.diff(gray_rendered.mean(axis=2), axis=0)
    return {
        'mean_abs_delta_8bit': float(delta.mean() * 255.0),
        'mean_chroma_8bit': float(channel_spread.mean() * 255.0),
        'gray_axis_chroma_8bit': float(gray_spread.mean() * 255.0),
        'gray_axis_reversals': int(np.count_nonzero(gray_step < -0.002)),
        'clip_black_percent': float(np.mean(np.max(rendered, axis=2) < 1 / 255) * 100),
        'clip_white_percent': float(np.mean(np.min(rendered, axis=2) > 254 / 255) * 100),
        'expected_monochrome': monochrome,
    }


class _CurrentLook:
    current = None


parse_lut_file_from_cache = _CurrentLook()


def contact_sheet(rows: list[dict], original: np.ndarray) -> None:
    tile_width, tile_height, label_height = 420, 280, 48
    canvas = Image.new('RGB', (tile_width * 3, (tile_height + label_height) * len(rows)), '#17191c')
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype('C:/Windows/Fonts/msyhbd.ttc', 18)
        small = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 14)
    except OSError:
        font = small = ImageFont.load_default()
    base_image = Image.fromarray(np.clip(original * 255 + 0.5, 0, 255).astype(np.uint8))
    base_image.thumbnail((tile_width, tile_height), Image.Resampling.LANCZOS)
    for row_index, row in enumerate(rows):
        top = row_index * (tile_height + label_height)
        values = [
            ('原图', base_image),
            (f"现有 · {row['name']}", Image.fromarray(np.clip(row['existing_image'] * 255 + 0.5, 0, 255).astype(np.uint8))),
            (f"CXO · {row['name']}", Image.fromarray(np.clip(row['cxo_image'] * 255 + 0.5, 0, 255).astype(np.uint8))),
        ]
        for column, (label, image) in enumerate(values):
            left = column * tile_width
            image.thumbnail((tile_width, tile_height), Image.Resampling.LANCZOS)
            canvas.paste(image, (left, top))
            draw.rectangle((left, top + tile_height, left + tile_width - 1, top + tile_height + label_height - 1), fill='#202328')
            draw.text((left + 12, top + tile_height + 13), label, font=font, fill='#f4f2ee')
            if column:
                detail = row['existing'] if column == 1 else row['cxo']
                draw.text((left + 12, top + tile_height + 34), detail['profile_id'], font=small, fill='#aab1ba')
    OUTPUT.mkdir(parents=True, exist_ok=True)
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
    canvas.save(OUTPUT / 'cxo-vs-existing-contact-sheet.jpg', quality=94, icc_profile=icc)


def main() -> None:
    original = decode_raw()
    rows = []
    for source_id, existing_id, name in PAIRS:
        cxo_path = CXO / (source_id.replace('ColorMode', 'cxo-').lower() + '.cube')
        # The exporter normalizes tokens rather than stripping the ColorMode prefix.
        if not cxo_path.is_file():
            manifest = json.loads((CXO / 'manifest.json').read_text(encoding='utf-8'))
            cxo_entry = next(item for item in manifest['entries'] if item['source_id'] == source_id)
            cxo_path = CXO / cxo_entry['cube']
        existing_path = ROOT / 'looks' / existing_id / 'source.cube'
        cxo_lut = parse_lut_file(cxo_path).lut
        existing_lut = parse_lut_file(existing_path).lut
        cxo_image = apply_lut(original, cxo_lut)
        existing_image = apply_lut(original, existing_lut)
        parse_lut_file_from_cache.current = cxo_lut
        cxo_metrics = image_metrics(original, cxo_image, '黑白' in next(
            entry['cxo_category'] for entry in json.loads((CXO / 'manifest.json').read_text(encoding='utf-8'))['entries']
            if entry['source_id'] == source_id))
        parse_lut_file_from_cache.current = existing_lut
        existing_metrics = image_metrics(original, existing_image, 'mono' in existing_id or 'trix' in existing_id or 'hp5' in existing_id)
        row = {
            'source_id': source_id,
            'name': name,
            'existing': {
                'profile_id': existing_id,
                'sha256': sha256(existing_path),
                **image_metrics(original, existing_image, existing_metrics['expected_monochrome']),
                **native_error(existing_path),
            },
            'cxo': {
                'profile_id': cxo_path.stem,
                'sha256': sha256(cxo_path),
                **cxo_metrics,
                **native_error(cxo_path),
            },
            'existing_image': existing_image,
            'cxo_image': cxo_image,
        }
        rows.append(row)
    contact_sheet(rows, original)
    report_rows = [{key: value for key, value in row.items() if not key.endswith('_image')} for row in rows]
    (OUTPUT / 'report.json').write_text(json.dumps({
        'source_raw': str(SRW.relative_to(ROOT)),
        'source_raw_sha256': sha256(SRW),
        'decoder': 'rawpy 0.27.1 / LibRaw, camera white balance, sRGB, half size',
        'native_evaluation': 'RGB-code trilinear model with 9^3 round projection; not measured camera ISP output',
        'entries': report_rows,
    }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    Image.fromarray(np.clip(original * 255 + 0.5, 0, 255).astype(np.uint8)).save(OUTPUT / 'original.jpg', quality=94)
    print(json.dumps({'pairs': len(rows), 'contact_sheet': str((OUTPUT / 'cxo-vs-existing-contact-sheet.jpg').relative_to(ROOT))}, ensure_ascii=False))


if __name__ == '__main__':
    main()
