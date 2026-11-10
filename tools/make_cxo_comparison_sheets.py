"""Build readable CXO comparison sheets from a real NX300M RAW frame."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageCms, ImageDraw, ImageFont

from compare_cxo_looks import decode_raw
from nx300m_lut_core import apply_lut, parse_lut_file


ROOT = Path(__file__).resolve().parents[1]
CXO_DIR = ROOT / 'outputs/looks/cxo-import'
MASTERS = CXO_DIR / 'masters'
COMPARISON_MASTERS = CXO_DIR / 'comparison-masters'
OUT = CXO_DIR / 'comparison'

CATEGORIES = (
    ('正片', '#d89a43'),
    ('负片', '#b96554'),
    ('交叉冲印', '#a66bb2'),
    ('电影胶片', '#5a88b8'),
    ('黑白胶片', '#8d969e'),
    ('数码胶片', '#4f9d8a'),
)

POSITIVE = {
    'ColorModeArgentiqueFujiSensia100',
    'ColorModeArgentiqueKodakEliteExtraColour100',
    'ColorModeArgentiqueKodakElitechrome200',
    'ColorModeArgentiqueKodakElitechrome400',
    'ColorModeArgentiqueKodakKodachrome25',
    'ColorModeArgentiqueKodakKodachrome200',
    'ColorModeArgentiquePolaroidPolachrome',
    'ColorModeArgentiqueFujiVelvia50',
}
NEGATIVE = {
    'ColorModeArgentiqueAgfaUltraColor100',
    'ColorModeArgentiqueFujiSuperiaReala100',
    'ColorModeArgentiqueFujiSuperiaXtra800',
    'ColorModeArgentiqueFujiSuperiaHG1600',
    'ColorModeArgentiqueKodakEliteColor200',
    'ColorModeArgentiqueKodakEliteColor400',
    'ColorModeArgentiqueLomoRedScale100',
    'ColorModeNegativeAmberT200',
    'ColorModeNegativeCinestillT800',
}
CROSS = {
    'ColorModeArgentiqueKodakElite100C41',
    'ColorModeArgentiqueFujiSuperia200E6',
}
CINEMA = {
    'ColorModeCineTealAndOrange',
    'ColorModeCineDesaturatedGreen',
    'ColorModeCineAutumn',
    'ColorModeCineMoodyDarkGreen',
    'ColorModeCinePastel',
    'ColorModeCineTealAndPink',
}
BW = {
    'ColorModeArgentiqueAgfaScala200x',
    'ColorModeArgentiqueFomaPan100',
    'ColorModeArgentiqueFomaPan200Creative',
    'ColorModeArgentiqueAdoxCMS20',
    'ColorModeArgentiqueAdoxSilvermax21',
    'ColorModeArgentiquePolaroid667',
    'ColorModeArgentiqueRolleiOrtho25',
    'ColorModeArgentiqueKodakTriX400',
}
DIGITAL = {
    'ColorModeDigitalFujiClassicChromePlus',
    'ColorModeDigitalFujiProNegativeHigh',
    'ColorModeDigitalFujiProNegativeStd',
    'ColorModeDigitalFujiEternaBleachBypass',
    'ColorModeDigital_FujiRealaAce',
    'ColorModeDigital_NikonZ_Bleached',
    'ColorModeDigitalFujiClassicChrome',
    'ColorModeDigitalFujiAstiaSoft',
    'ColorModeDigitalFujiEterna',
    'ColorModeDigitalFujiClassicNeg',
}

REPLACED = {
    'ColorModeArgentiqueFujiVelvia50',
    'ColorModeArgentiqueKodakTriX400',
    'ColorModeDigitalFujiClassicChrome',
    'ColorModeDigitalFujiAstiaSoft',
    'ColorModeDigitalFujiEterna',
    'ColorModeDigitalFujiClassicNeg',
}

PAIR_VERDICTS = {
    'ColorModeArgentiqueKodakPortra400': ('保留现有', '#d65f4b'),
    'ColorModeArgentiqueKodakE100GXEktachrome100': ('保留现有', '#d65f4b'),
    'ColorModeArgentiqueFujiVelvia50': ('换用 CXO', '#2fa681'),
    'ColorModeArgentiqueKodakTriX400': ('换用 CXO', '#2fa681'),
    'ColorModeArgentiqueIllfordHP5Plus400': ('保留现有', '#d65f4b'),
    'ColorModeArgentiqueFujiSuperiaXtra400': ('保留现有', '#d65f4b'),
    'ColorModeDigitalFujiClassicChrome': ('换用 CXO', '#2fa681'),
    'ColorModeDigitalFujiAstiaSoft': ('换用 CXO', '#2fa681'),
    'ColorModeDigitalFujiEterna': ('换用 CXO', '#2fa681'),
    'ColorModeDigitalFujiClassicNeg': ('换用 CXO', '#2fa681'),
    'ColorModeDigitalFujiNostalgicNeg': ('保留现有', '#d65f4b'),
}

EXISTING_IDS = {
    'ColorModeArgentiqueKodakPortra400': ('metol-portra400', 'Portra 400'),
    'ColorModeArgentiqueKodakE100GXEktachrome100': ('metol-ektachrome', 'Ektachrome 100'),
    'ColorModeArgentiqueFujiVelvia50': ('metol-velvia50', 'Velvia 50'),
    'ColorModeArgentiqueKodakTriX400': ('metol-trix400', 'Tri-X 400'),
    'ColorModeArgentiqueIllfordHP5Plus400': ('metol-hp5', 'HP5 Plus 400'),
    'ColorModeArgentiqueFujiSuperiaXtra400': ('metol-superia400', 'Superia X-Tra 400'),
    'ColorModeDigitalFujiClassicChrome': ('nx300-fuji-classic-chrome', 'Classic Chrome'),
    'ColorModeDigitalFujiAstiaSoft': ('nx300-fuji-astia', 'Astia'),
    'ColorModeDigitalFujiEterna': ('nx300-fuji-eterna', 'Eterna'),
    'ColorModeDigitalFujiClassicNeg': ('nx300-fuji-classic-negative', 'Classic Negative'),
    'ColorModeDigitalFujiNostalgicNeg': ('nx300-fuji-nostalgic-neg', 'Nostalgic Negative'),
}

FONT_REGULAR = 'C:/Windows/Fonts/msyh.ttc'
FONT_BOLD = 'C:/Windows/Fonts/msyhbd.ttc'


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_BOLD if bold else FONT_REGULAR, size)


def category_for(source_id: str) -> tuple[str, str]:
    if source_id in POSITIVE:
        return CATEGORIES[0]
    if source_id in NEGATIVE:
        return CATEGORIES[1]
    if source_id in CROSS:
        return CATEGORIES[2]
    if source_id in CINEMA:
        return CATEGORIES[3]
    if source_id in BW:
        return CATEGORIES[4]
    if source_id in DIGITAL:
        return CATEGORIES[5]
    raise KeyError(source_id)


def as_image(rgb: np.ndarray) -> Image.Image:
    return Image.fromarray(np.clip(rgb * 255 + 0.5, 0, 255).astype(np.uint8), 'RGB')


def fit(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    copy = image.copy()
    copy.thumbnail(size, Image.Resampling.LANCZOS)
    return copy


def contain(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    copy = image.copy()
    copy.thumbnail(size, Image.Resampling.LANCZOS)
    canvas = Image.new('RGB', size, '#11161b')
    canvas.paste(copy, ((size[0] - copy.width) // 2, (size[1] - copy.height) // 2))
    return canvas


def paste_centered(canvas: Image.Image, image: Image.Image, box: tuple[int, int, int, int]) -> None:
    left, top, right, bottom = box
    x = left + (right - left - image.width) // 2
    y = top + (bottom - top - image.height) // 2
    canvas.paste(image, (x, y))


def save_srgb(image: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
    image.save(path, quality=95, subsampling=0, optimize=True, icc_profile=icc)


def load_manifest(directory: Path = MASTERS, *, sorted_by_category: bool = True) -> list[dict]:
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    if not sorted_by_category:
        return manifest['entries']
    category_rank = {name: index for index, (name, _) in enumerate(CATEGORIES)}
    return sorted(
        manifest['entries'],
        key=lambda entry: (category_rank[category_for(entry['source_id'])[0]], entry['label'].lower()),
    )


def build_overview(
    original: np.ndarray,
    entries: list[dict],
    *,
    title: str,
    subtitle: str,
    output_name: str,
) -> Path:
    columns = 5
    tile_width = 340
    image_height = 227
    label_height = 66
    row_height = image_height + label_height + 10
    margin = 34
    header_height = 154
    rows = (len(entries) + 1 + columns - 1) // columns
    width = margin * 2 + columns * tile_width + (columns - 1) * 12
    height = header_height + margin + rows * row_height
    canvas = Image.new('RGB', (width, height), '#11161b')
    draw = ImageDraw.Draw(canvas)

    draw.text((margin, 28), title, font=font(34, True), fill='#f5f1e8')
    draw.text(
        (margin, 76),
        subtitle,
        font=font(18), fill='#aeb8c2',
    )
    draw.text(
        (margin, 110),
        '蓝色标题条为分类；橙色“替换”表示该 CXO 版本已替代相机目录里的旧同名风格',
        font=font(16), fill='#81909e',
    )
    x0 = margin
    y0 = header_height
    base = contain(as_image(original), (tile_width, image_height))

    items: list[dict | None] = [None, *entries]
    for index, entry in enumerate(items):
        col = index % columns
        row = index // columns
        left = x0 + col * (tile_width + 12)
        top = y0 + row * row_height
        if entry is None:
            image = base
            category, accent = '原图', '#e3c36b'
            title = '原图基准'
            replaced = False
        else:
            cube = MASTERS / entry['cube']
            image = contain(
                as_image(apply_lut(original, parse_lut_file(cube).lut)),
                (tile_width, image_height),
            )
            category, accent = category_for(entry['source_id'])
            title = entry['label']
            replaced = entry['source_id'] in REPLACED
        draw.rectangle((left - 2, top - 2, left + tile_width + 1, top + image_height + 1), fill=accent)
        canvas.paste(image, (left, top))
        draw.rectangle((left, top + image_height, left + tile_width, top + image_height + label_height), fill='#1c232a')
        draw.rectangle((left, top + image_height, left + tile_width, top + image_height + 5), fill=accent)
        draw.text((left + 11, top + image_height + 14), title, font=font(17, True), fill='#f5f1e8')
        draw.text((left + 11, top + image_height + 40), category, font=font(14), fill='#9eabb7')
        if replaced:
            pill = '替换'
            pill_font = font(13, True)
            pill_box = draw.textbbox((0, 0), pill, font=pill_font)
            pill_width = pill_box[2] - pill_box[0] + 14
            draw.rounded_rectangle(
                (left + tile_width - pill_width - 9, top + image_height + 33, left + tile_width - 9, top + image_height + 56),
                radius=8,
                fill='#d0783f',
            )
            draw.text((left + tile_width - pill_width - 2, top + image_height + 36), pill, font=pill_font, fill='#11161b')

    path = OUT / output_name
    save_srgb(canvas, path)
    return path


def build_pairs(
    original: np.ndarray,
    entries_by_source: dict[str, dict],
    *,
    title: str,
    subtitle: str,
    source_note: str,
    output_name: str,
) -> Path:
    columns = 3
    tile_width = 500
    image_height = 334
    label_height = 76
    row_gap = 14
    row_height = image_height + label_height + row_gap
    margin = 34
    header_height = 145
    count = len(EXISTING_IDS)
    width = margin * 2 + columns * tile_width
    height = header_height + margin + count * row_height
    canvas = Image.new('RGB', (width, height), '#11161b')
    draw = ImageDraw.Draw(canvas)
    draw.text((margin, 25), title, font=font(34, True), fill='#f5f1e8')
    draw.text(
        (margin, 73),
        subtitle,
        font=font(18), fill='#aeb8c2',
    )
    draw.text(
        (margin, 107),
        '红色：保留现有版本    绿色：相机目录已换用 CXO 版本',
        font=font(16), fill='#81909e',
    )
    original_image = contain(as_image(original), (tile_width, image_height))

    for row_index, (source_id, (existing_id, name)) in enumerate(EXISTING_IDS.items()):
        entry = entries_by_source[source_id]
        top = header_height + row_index * row_height
        existing_image = as_image(apply_lut(original, parse_lut_file(ROOT / 'looks' / existing_id / 'source.cube').lut))
        cxo_image = as_image(apply_lut(original, parse_lut_file(COMPARISON_MASTERS / entry['cube']).lut))
        cells = (
            ('原图', original_image, '#e3c36b'),
            ('现有 · ' + name, contain(existing_image, (tile_width, image_height)), '#81909e'),
            ('CXO · ' + name, contain(cxo_image, (tile_width, image_height)), '#2fa681'),
        )
        verdict, verdict_color = PAIR_VERDICTS[source_id]
        for col, (label, image, accent) in enumerate(cells):
            left = margin + col * tile_width
            draw.rectangle((left, top, left + tile_width - 1, top + image_height + label_height), fill='#1c232a')
            draw.rectangle((left, top, left + tile_width - 1, top + 4), fill=accent)
            canvas.paste(image, (left, top + 4))
            draw.text((left + 13, top + image_height + 17), label, font=font(20, True), fill='#f5f1e8')
            detail = source_note if col == 0 else (
                existing_id if col == 1 else entry['camera_id']
            )
            draw.text((left + 13, top + image_height + 47), detail, font=font(14), fill='#8e9ba8')
        pill_font = font(15, True)
        pill_box = draw.textbbox((0, 0), verdict, font=pill_font)
        pill_width = pill_box[2] - pill_box[0] + 24
        draw.rounded_rectangle(
            (width - margin - pill_width, top + 13, width - margin, top + 47),
            radius=10,
            fill=verdict_color,
        )
        draw.text((width - margin - pill_width + 12, top + 19), verdict, font=pill_font, fill='#0f1418')

    path = OUT / output_name
    save_srgb(canvas, path)
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-image', type=Path)
    parser.add_argument('--crop-left-third', action='store_true')
    parser.add_argument('--alt', action='store_true')
    args = parser.parse_args()
    entries = load_manifest()
    comparison_entries = load_manifest(COMPARISON_MASTERS, sorted_by_category=False)
    entries_by_source = {entry['source_id']: entry for entry in comparison_entries}
    missing = set(EXISTING_IDS) - set(entries_by_source)
    if missing:
        raise ValueError('Missing comparison entries: ' + ', '.join(sorted(missing)))
    if args.source_image:
        source = Image.open(args.source_image).convert('RGB')
        if args.crop_left_third:
            source = source.crop((0, 0, source.width // 3, source.height))
        original = np.asarray(source, dtype=np.float32) / 255.0
        source_note = '统一对照样本 · 同一冲印参数'
        title = 'NX300M LUT 对照 · 人物、肤色与暖光'
        subtitle = '人物实拍样本；CXO 100% 强度；相机端仍以 NX300M 原生 9×9×9 LUT 为主'
        overview_name = 'cxo-all-43-portrait-overview.jpg'
        pairs_name = 'cxo-overlap-portrait-existing-cxo.jpg'
        if args.alt:
            source_note = '统一风景样本 · 同一冲印参数'
            title = 'NX300M LUT 对照 · 天空、绿地与阴影'
            subtitle = '风景实拍样本；CXO 100% 强度；相机端仍以 NX300M 原生 9×9×9 LUT 为主'
            overview_name = 'cxo-all-43-landscape-overview.jpg'
            pairs_name = 'cxo-overlap-landscape-existing-cxo.jpg'
    else:
        original = decode_raw()
        source_note = '真实 NX300M SRW · 同一冲印参数'
        title = 'NX300M 实拍 SRW · 原图与 43 个 CXO 风格'
        subtitle = '同一张 SRW、同一曝光与白平衡；CXO 100% 强度；相机端仍以 NX300M 原生 9×9×9 LUT 为主'
        overview_name = 'cxo-all-43-real-srw-overview.jpg'
        pairs_name = 'cxo-overlap-original-existing-cxo.jpg'
    overview = build_overview(
        original,
        entries,
        title=title,
        subtitle=subtitle,
        output_name=overview_name,
    )
    pairs = build_pairs(
        original,
        entries_by_source,
        title=title.replace('原图与 43 个', '同名风格对照 · 原图 / 现有 /'),
        subtitle='建议依据画面判断，9×9×9 投影误差只作参考；本图展示全部 11 个重叠项。',
        source_note=source_note,
        output_name=pairs_name,
    )
    print(json.dumps({
        'overview': str(overview.relative_to(ROOT)),
        'pairs': str(pairs.relative_to(ROOT)),
    }, ensure_ascii=False))


if __name__ == '__main__':
    main()
