"""Build review sheets for all local LUTs, split by current camera membership."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
import json
from pathlib import Path
import re
import zipfile

import numpy as np
from PIL import Image, ImageCms, ImageDraw, ImageFont

from nx300m_lut_core import apply_lut, parse_lut_file


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / 'lut-library/catalog.json'
MACHINE = ROOT / 'lut-library/machine-catalog.json'
SAMPLES = ROOT / 'scratch/lumixlab/repo/LumixLutSearch-main/app/src/main/assets/songying'
DOC = ROOT / 'mod/nxconnect/third_party/cxo-filmpack/docs/images'
STAMP = datetime.now().strftime('%Y%m%dT%H%M%S')
OUT = ROOT / 'outputs/looks/library-review' / STAMP

SCENES = (
    ('人像', SAMPLES / '34_09_before.jpg', False),
    ('城市', SAMPLES / '113_01_before.jpg', False),
    ('自然', DOC / 'raw-landscape.jpg', True),
    ('夜景', DOC / 'raw-sony.jpg', True),
)

FONT_REGULAR = 'C:/Windows/Fonts/msyh.ttc'
FONT_BOLD = 'C:/Windows/Fonts/msyhbd.ttc'
INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_BOLD if bold else FONT_REGULAR, size)


def repair_text(value: str) -> str:
    """Recover text created through the project's historical GBK/UTF-8 mix-up."""
    if not any(mark in value for mark in ('鍙', '鏃', '锛', '銆', '鐨', '瀵', '榛', '鑳', '鎴', '缁', '浜')):
        return value
    try:
        return value.encode('gbk').decode('utf-8')
    except UnicodeError:
        return value


def safe_name(value: str, limit: int = 54) -> str:
    value = INVALID.sub('_', value).strip(' .')
    value = ' '.join(value.split())
    return value[:limit].rstrip(' .') or 'LUT'


def load_sample(path: Path, crop_left: bool) -> np.ndarray:
    image = Image.open(path).convert('RGB')
    if crop_left:
        image = image.crop((0, 58, image.width // 3, image.height))
    return np.asarray(image, dtype=np.float32) / 255.0


def fit(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    image = image.copy()
    image.thumbnail(size, Image.Resampling.LANCZOS)
    canvas = Image.new('RGB', size, '#0b0f13')
    canvas.paste(image, ((size[0] - image.width) // 2, (size[1] - image.height) // 2))
    return canvas


def rgb_image(values: np.ndarray, size: tuple[int, int]) -> Image.Image:
    image = Image.fromarray(np.clip(values * 255 + 0.5, 0, 255).astype(np.uint8), 'RGB')
    return fit(image, size)


def save_srgb(image: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
    image.save(path, quality=93, subsampling=0, optimize=True, icc_profile=icc)


def make_preview(entry: dict, original: np.ndarray, path: Path) -> Path:
    key = entry['id']
    lut = parse_lut_file(ROOT / 'looks' / key / 'source.cube').lut
    rendered = apply_lut(original, lut)
    panel_size = (400, 250)
    canvas = Image.new('RGB', (800, 555), '#11161b')
    canvas.paste(rgb_image(original, panel_size), (0, 0))
    canvas.paste(rgb_image(rendered, panel_size), (400, 0))
    canvas.paste(Image.new('RGB', panel_size, '#11161b'), (0, 250))
    canvas.paste(Image.new('RGB', panel_size, '#11161b'), (400, 250))

    # The four quadrants use the same source family at different scenes.
    # Re-render the list of inputs passed by the caller in the next pass.
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 500, 799, 554), fill='#1c232a')
    label = repair_text(entry.get('label', key))
    draw.text((12, 510), label, font=font(18, True), fill='#f5f1e8')
    draw.text((12, 535), key, font=font(12), fill='#8e9ba8')
    save_srgb(canvas, path)
    return path


def build_preview(entry: dict, scene_pixels: list[tuple[str, np.ndarray]], path: Path) -> Path:
    lut = parse_lut_file(ROOT / 'looks' / entry['id'] / 'source.cube').lut
    panel_size = (400, 250)
    canvas = Image.new('RGB', (800, 555), '#11161b')
    for index, (scene, pixels) in enumerate(scene_pixels):
        x, y = (index % 2) * 400, (index // 2) * 250
        canvas.paste(rgb_image(apply_lut(pixels, lut), panel_size), (x, y))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 500, 799, 554), fill='#1c232a')
    label = repair_text(entry.get('label', entry['id']))
    draw.text((12, 510), label, font=font(18, True), fill='#f5f1e8')
    draw.text((12, 535), entry['id'], font=font(12), fill='#8e9ba8')
    save_srgb(canvas, path)
    return path


def make_contact_sheet(entries: list[dict], previews: dict[str, Path], title: str, path: Path) -> Path:
    if not entries:
        raise ValueError('Cannot create an empty contact sheet')
    columns, tile_width, tile_height, gap, margin, header = 4, 400, 278, 10, 24, 104
    rows = (len(entries) + columns - 1) // columns
    canvas = Image.new('RGB', (
        columns * tile_width + (columns - 1) * gap + margin * 2,
        header + rows * (tile_height + gap),
    ), '#11161b')
    draw = ImageDraw.Draw(canvas)
    draw.text((margin, 16), title, font=font(28, True), fill='#f5f1e8')
    draw.text(
        (margin, 56),
        '每款四个窗格依次为：人像、城市、自然、夜景；标签下方的稳定 ID 用于回填选择清单。',
        font=font(15), fill='#aeb8c2',
    )
    draw.text((margin, 80), f'共 {len(entries)} 款', font=font(13), fill='#81909e')
    for index, entry in enumerate(entries):
        column, row = index % columns, index // columns
        left = margin + column * (tile_width + gap)
        top = header + row * (tile_height + gap)
        preview = Image.open(previews[entry['id']]).convert('RGB').resize(
            (tile_width, tile_height), Image.Resampling.LANCZOS
        )
        canvas.paste(preview, (left, top))
    save_srgb(canvas, path)
    return path


def write_readme(directory: Path, title: str, entries: list[dict]) -> None:
    groups = Counter(repair_text(entry['group']) for entry in entries)
    lines = [
        f'# {title}', '',
        f'共 {len(entries)} 款。每款预览使用人像、城市、自然、夜景四个场景，固定曝光、白平衡和裁切。', '',
        '`previews/` 是逐款大图；`contact-sheets/` 是按分类汇总；`selection.tsv` 可直接填写保留/淘汰。', '',
        '## 分类', '',
        *[f'- {name}: {count}' for name, count in sorted(groups.items())], '',
        '这些图用于视觉筛选；相机端实际 ISP 与 9×9×9 量化仍需实机验收。', '',
    ]
    (directory / 'README.md').write_text('\n'.join(lines), encoding='utf-8', newline='\n')


def write_inventory(directory: Path, entries: list[dict], camera_ids: set[str]) -> None:
    lines = ['in_camera\tgroup\tlabel\tstable_id\tkeep\tnote']
    for entry in entries:
        lines.append('\t'.join([
            'yes' if entry['id'] in camera_ids else 'no',
            repair_text(entry['group']),
            repair_text(entry.get('label', entry['id'])),
            entry['id'], '', '',
        ]))
    (directory / 'selection.tsv').write_text('\n'.join(lines) + '\n', encoding='utf-8', newline='\n')


def zip_directory(source: Path, archive: Path) -> dict:
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for path in sorted(source.rglob('*')):
            if path.is_file():
                bundle.write(path, Path(source.name) / path.relative_to(source))
    with zipfile.ZipFile(archive) as bundle:
        bad = bundle.testzip()
        if bad:
            raise ValueError(f'ZIP verification failed at {bad}')
        count = len(bundle.namelist())
    return {'file': archive.relative_to(ROOT).as_posix(), 'bytes': archive.stat().st_size, 'members': count}


def build_package(name: str, title: str, entries: list[dict], scene_pixels, camera_ids: set[str]) -> dict:
    root = OUT / name
    preview_root = root / 'previews'
    sheet_root = root / 'contact-sheets'
    by_group: defaultdict[str, list[dict]] = defaultdict(list)
    previews = {}
    for index, entry in enumerate(entries, 1):
        group = safe_name(repair_text(entry['group']))
        destination = preview_root / group / f'{index:03d}-{entry["id"]}.jpg'
        build_preview(entry, scene_pixels, destination)
        previews[entry['id']] = destination
        by_group[group].append(entry)
    sheets = []
    for index, (group, group_entries) in enumerate(sorted(by_group.items()), 1):
        sheet = sheet_root / f'{index:02d}-{safe_name(group)}.jpg'
        make_contact_sheet(group_entries, previews, f'{title} · {group}', sheet)
        sheets.append(sheet)
    write_readme(root, title, entries)
    write_inventory(root, entries, camera_ids)
    archive = OUT / f'{name}.zip'
    packaged = zip_directory(root, archive)
    return {
        'count': len(entries),
        'groups': {group: len(items) for group, items in sorted(by_group.items())},
        'sheets': [path.relative_to(ROOT).as_posix() for path in sheets],
        'package': packaged,
    }


def main() -> None:
    catalog = json.loads(CATALOG.read_text(encoding='utf-8'))
    camera_ids = set(json.loads(MACHINE.read_text(encoding='utf-8'))['ids'])
    entries = sorted(
        catalog['entries'],
        key=lambda entry: (entry['group'], repair_text(entry.get('label', entry['id'])), entry['id']),
    )
    missing = [entry['id'] for entry in entries if not (ROOT / 'looks' / entry['id'] / 'source.cube').is_file()]
    if missing:
        raise FileNotFoundError('Missing LUT sources: ' + ', '.join(missing[:20]))
    scene_pixels = [(label, load_sample(path, crop)) for label, path, crop in SCENES]
    camera_entries = [entry for entry in entries if entry['id'] in camera_ids]
    other_entries = [entry for entry in entries if entry['id'] not in camera_ids]
    OUT.mkdir(parents=True, exist_ok=False)

    # Save the exact display samples in each package.
    in_camera = build_package('IN_CAMERA', '当前入机 LUT 审片', camera_entries, scene_pixels, camera_ids)
    not_in_camera = build_package('NOT_IN_CAMERA', '未入机 LUT 审片', other_entries, scene_pixels, camera_ids)
    summary = {
        'schema': 1,
        'generated_at': STAMP,
        'library_count': len(entries),
        'camera_count': len(camera_entries),
        'not_in_camera_count': len(other_entries),
        'scenes': [label for label, _, _ in SCENES],
        'in_camera': in_camera,
        'not_in_camera': not_in_camera,
    }
    (OUT / 'review.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
