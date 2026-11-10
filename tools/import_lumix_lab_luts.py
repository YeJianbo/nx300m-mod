"""Import standard-based Panasonic LUMIX Lab LUTs into the local camera library.

The official Panasonic China LUT library is scraped only to obtain its current
time-limited ZIP URLs. LUT files are kept locally and are never copied into a
public release package by this tool.

Only LUTs whose source Photo Style is Standard/standard colour/standard tone
are selected. V-Log and other camera-style LUTs are intentionally excluded.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import io
from pathlib import Path
import re
import tempfile
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen
import zipfile

from cube_to_d4 import load_cube
from look_catalog import camera_looks
from look_library import LookLibrary
from lut_table_data import read_tables

PROJECT = Path(__file__).resolve().parents[1]
OUTPUT = PROJECT / 'outputs/looks/lumixlab-import'
SITE = 'https://www.panasonicshuma.cn'
PAGE = SITE + '/RealtimeLUTLibrary/list.aspx?lcid={}'
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) NX300M-LUT-Importer/1.0'

# The Panasonic page contains 58 entries across four categories. These are the
# 20 logical entries (21 files) that use a standard-family source style. The
# duplicate page entry for the two-variant "千禧回声" package is represented by
# 113_02 once. Each tuple is (asset id, category, preview, title, slug, files).
# A file tuple is (expected name, variant, optional ZIP basename).
STANDARD_ITEMS = (
    ('113_01', '25周年专属', '/uploadfiles/2026/03/微信图片_20260325164720.png',
     'AgfaPhoto LeBox', 'agfaphoto-lebox',
     (('agfaphoto_lebox.cube', None, None),)),
    ('113_02', '25周年专属', '/uploadfiles/2026/03/微信图片_20260325164723.png',
     '千禧回声', 'millennium-echo',
     (('millennium_echo_cool.cube', 'COOL',
       'c5903c4c33ba980117f5ed7804e28b0f_d6a53ab957237e7ab2560511637a42ce_8.cube'),
      ('millennium_echo_warm.cube', 'WARM',
       '73ec0931880d84bd8a73754ec6acb212_1f57406f3aa403dcec575ad7a39d635f_8.cube'))),
    ('113_04', '25周年专属', '/uploadfiles/2026/03/组 1.png',
     'Y2K', 'y2k',
     (('y2k.cube', None, None),)),
    ('32_12', '人像', '/uploadfiles/2024/07/20240709162253876.jpg',
     '藏区暖光', 'tibet-warm-light',
     (('tibet_warm_light.cube', None, None),)),
    ('32_14', '人像', '/uploadfiles/2024/07/20240709162328568.jpg',
     '肤色优化', 'skin-tone-boost',
     (('skin_tone_boost.vlt', None, None),)),
    ('32_16', '人像', '/uploadfiles/2024/09/20240912163752647.jpg',
     '轻日系-蓝', 'light-japanese-blue',
     (('light_japanese_blue.cube', None, None),)),
    ('32_17', '人像', '/uploadfiles/2025/01/8fb1db26f85d6aef12c32a015e191e2.png',
     '暖白肤色', 'warm-white-skin',
     (('warm_white_skin.cube', None, None),)),
    ('32_19', '人像', '/uploadfiles/2025/11/复古夏日.png',
     '复古夏日', 'retro-summer',
     (('retro_summer.cube', None, None),)),
    ('32_20', '人像', '/uploadfiles/2025/11/薄暮苔痕.png',
     '薄暮苔痕', 'dusk-moss',
     (('dusk_moss.cube', None, None),)),
    ('32_21', '人像', '/uploadfiles/2025/11/TXD Eternal.png',
     'Eterna', 'eterna',
     (('eterna.cube', None, None),)),
    ('33_05', '户外', '/uploadfiles/2024/07/20240709163637435.jpg',
     '夏日暖风', 'summer-warm-wind',
     (('summer_warm_wind.cube', None, None),)),
    ('33_06', '户外', '/uploadfiles/2024/07/20240709163625338.jpg',
     '浓厚格调', 'rich-tone',
     (('rich_tone.cube', None, None),)),
    ('33_07', '户外', '/uploadfiles/2024/07/20240709163607910.jpg',
     '灰度电影', 'grayscale-film',
     (('grayscale_film.cube', None, None),)),
    ('33_17', '户外', '/uploadfiles/2024/07/20240709163340475.jpg',
     '正片', 'reversal-film',
     (('reversal_film.cube', None, None),)),
    ('33_20', '户外', '/uploadfiles/2024/11/92befc277b62be21e1c4e6dccfbc122.png',
     '糖果色', 'candy-color',
     (('candy_color.cube', None, None),)),
    ('33_21', '户外', '/uploadfiles/2025/11/蓝漾.png',
     '蓝漾', 'blue-ripple',
     (('blue_ripple.cube', None, None),)),
    ('33_23', '户外', '/uploadfiles/2025/11/微信图片_20251120164310.png',
     '苔痕雨季', 'mossy-rain',
     (('mossy_rain.cube', None, None),)),
    ('34_01', '室内', '/uploadfiles/2024/07/20240709164112701.jpg',
     '黑白旧胶片', 'mono-old-film',
     (('mono_old_film.cube', None, None),)),
    ('34_06', '室内', '/uploadfiles/2024/07/20240709164020786.jpg',
     '复古旧胶片', 'retro-old-film',
     (('retro_old_film.cube', None, None),)),
    ('34_07', '室内', '/uploadfiles/2024/07/20240709164007940.jpg',
     '洋红旧胶片', 'magenta-old-film',
     (('magenta_old_film.cube', None, None),)),
)

ZIP_LINK = re.compile(r'href=["\'](https://oss\.panasonicshuma\.cn/[^"\']+\.zip)["\']', re.I)
TAG = re.compile(r'<[^>]+>')
STYLE = re.compile(r'#\s*LUMIXPHOTOSTYLE\s+([^\r\n]+)', re.I)
TITLE = re.compile(r'^\s*TITLE\s+(.+?)\s*$', re.I | re.M)
STANDARD_STYLES = {'STD', 'STANDARD', 'STANDARD COLOR', 'STANDARD COLOUR',
                   'STANDARD TONE', '标准', '标准模式', '标准色彩', '标准色调'}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + f'.{next(tempfile._get_candidate_names())}.tmp')
    try:
        pending.write_bytes(data)
        pending.replace(path)
    except BaseException:
        pending.unlink(missing_ok=True)
        raise


def fetch(url: str, *, referer: str | None = None, timeout: int = 90) -> bytes:
    headers = {'User-Agent': USER_AGENT}
    if referer:
        headers['Referer'] = referer
    request = Request(url, headers=headers)
    with urlopen(request, timeout=timeout) as response:
        data = response.read()
    if not data:
        raise ValueError('empty HTTP response: ' + url)
    return data


def text_content(fragment: str) -> str:
    return ' '.join(html.unescape(TAG.sub(' ', fragment)).split())


def item_block(page_html: str, preview: str) -> tuple[str, str, str]:
    preview_name = unquote(preview.rsplit('/', 1)[-1])
    at = page_html.find(preview_name)
    if at < 0:
        raise ValueError('preview not found on current Panasonic page: ' + preview_name)
    start = page_html.rfind('<li', 0, at)
    end = page_html.find('</li>', at)
    if start < 0 or end < 0:
        raise ValueError('Panasonic page item structure changed for: ' + preview_name)
    block = page_html[start:end]
    match = ZIP_LINK.search(block)
    if not match:
        raise ValueError('no current ZIP link for: ' + preview_name)
    heading = re.search(r'<h2[^>]*>(.*?)</h2>', block, re.I | re.S)
    description = re.search(r'<p\s+class=["\']dot["\'][^>]*>(.*?)</p>', block, re.I | re.S)
    return match.group(1), text_content(heading.group(1)) if heading else '', \
        text_content(description.group(1)) if description else ''


def lut_entries(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    found = []
    for entry in archive.infolist():
        name = entry.filename.replace('\\', '/')
        base = unquote(name.rsplit('/', 1)[-1])
        if entry.is_dir() or base.startswith('._') or '__MACOSX/' in name:
            continue
        if base.lower().endswith(('.cube', '.vlt')) and entry.file_size >= 1000:
            found.append(entry)
    return found


def choose_entry(archive: zipfile.ZipFile, expected: tuple[str, str | None, str | None]) -> tuple[zipfile.ZipInfo, bytes]:
    expected_name, variant, zip_name = expected
    candidates = lut_entries(archive)
    if zip_name:
        selected = [e for e in candidates
                    if unquote(e.filename.replace('\\', '/').rsplit('/', 1)[-1]).casefold()
                    == zip_name.casefold()]
        if len(selected) == 1:
            entry = selected[0]
            return entry, archive.read(entry)
    if variant:
        selected = [e for e in candidates if variant.casefold() in e.filename.casefold()]
        if len(selected) == 1:
            entry = selected[0]
            return entry, archive.read(entry)
    named = [e for e in candidates
             if unquote(e.filename.replace('\\', '/').rsplit('/', 1)[-1]).casefold()
             == expected_name.casefold()]
    if len(named) == 1:
        entry = named[0]
        return entry, archive.read(entry)
    if len(candidates) == 1:
        entry = candidates[0]
        return entry, archive.read(entry)
    names = ', '.join(e.filename for e in candidates)
    raise ValueError(f'cannot identify {expected_name!r} in ZIP; candidates: {names}')


def parse_vlt(data: bytes) -> tuple[bytes, tuple[int, tuple[float, float, float], tuple[float, float, float], list[tuple[float, float, float]]], dict]:
    text = data.decode('utf-8-sig')
    size = None
    values = []
    for line_number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        tokens = stripped.split()
        if tokens[0].upper() == 'LUT_3D_SIZE':
            if size is not None or len(tokens) != 2:
                raise ValueError('duplicate or invalid VLT LUT_3D_SIZE')
            size = int(tokens[1])
            if not 2 <= size <= 129:
                raise ValueError('VLT LUT_3D_SIZE must be 2..129')
            continue
        if tokens[0].upper().startswith('LUT_'):
            continue
        if len(tokens) != 3:
            raise ValueError(f'invalid VLT row at line {line_number}')
        values.append(tuple(float(v) for v in tokens))
    if size is None or len(values) != size ** 3:
        raise ValueError('incomplete VLT 3D table')
    maximum = max(abs(value) for row in values for value in row)
    if maximum <= 1.0001:
        divisor = 1.0
    elif maximum <= 255.001:
        divisor = 255.0
    elif maximum <= 4095.001:
        divisor = 4095.0
    else:
        raise ValueError(f'unsupported VLT numeric range: max={maximum}')
    normalized = [tuple(max(0.0, min(1.0, value / divisor)) for value in row) for row in values]
    lines = ['TITLE "Converted from Panasonic VLT"', f'LUT_3D_SIZE {size}',
             'DOMAIN_MIN 0 0 0', 'DOMAIN_MAX 1 1 1']
    lines.extend(f'{r:.10f} {g:.10f} {b:.10f}' for r, g, b in normalized)
    cube_bytes = ('\n'.join(lines) + '\n').encode('utf-8')
    cube = (size, (0.0, 0.0, 0.0), (1.0, 1.0, 1.0), normalized)
    return cube_bytes, cube, {'vlt_numeric_divisor': int(divisor), 'converted_from_vlt': True}


def style_from_header(data: bytes) -> str | None:
    match = STYLE.search(data[:4096].decode('utf-8-sig', errors='replace'))
    return match.group(1).strip() if match else None


def source_style(data: bytes, source_name: str) -> str | None:
    style = style_from_header(data)
    if style is not None:
        return style
    if source_name.casefold().endswith('.vlt'):
        comments = '\n'.join(line for line in data[:4096].decode('utf-8-sig', errors='replace').splitlines()
                             if line.lstrip().startswith('#'))
        if re.search(r'(?i)(?:^|[^a-z])std(?:[^a-z]|$)|standard', comments):
            return 'STANDARD'
    return None


def title_from_cube(data: bytes) -> str | None:
    match = TITLE.search(data[:4096].decode('utf-8-sig', errors='replace'))
    if not match:
        return None
    value = match.group(1).strip().strip('"\'')
    return None if re.match(r'(?i)^generated by\b', value) else value


def ensure_standard(style: str | None, *, context: str) -> None:
    if style is None:
        raise ValueError('LUT has no LUMIXPHOTOSTYLE marker: ' + context)
    normalized = ' '.join(style.upper().split())
    if normalized not in {s.upper() for s in STANDARD_STYLES}:
        raise ValueError(f'LUT source style is {style!r}, not standard-family: {context}')


def slugify(value: str) -> str:
    slug = re.sub(r'[^a-z0-9]+', '-', value.casefold()).strip('-')
    return slug or 'lut-' + digest(value.encode('utf-8'))[:8]


def look_id(slug: str) -> str:
    key = 'panasonic-lumix-' + slug
    if len(key) > 63:
        key = key[:54].rstrip('-') + '-' + digest(slug.encode('utf-8'))[:8]
    return key


def existing_sources(library: LookLibrary) -> dict[str, str]:
    result = {}
    for metadata in library.list():
        source = library.directory(metadata['id']) / 'source.cube'
        if source.is_file():
            result.setdefault(digest(source.read_bytes()), metadata['id'])
    return result


def save_zip(cache: Path, asset_id: str, data: bytes, *, refresh: bool) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / f'{asset_id}.zip'
    if path.is_file() and not refresh:
        try:
            with zipfile.ZipFile(path) as archive:
                if archive.testzip() is None and lut_entries(archive):
                    return path
        except (OSError, zipfile.BadZipFile):
            pass
    atomic_write(path, data)
    return path


def import_file(library: LookLibrary, *, key: str, label: str, source_name: str,
                source_bytes: bytes, cube_bytes: bytes, cube, style: str | None,
                metadata_extra: dict, source_hashes: dict[str, str]) -> tuple[str, dict]:
    source_hash = digest(source_bytes)
    cube_hash = digest(cube_bytes)
    if source_hash in source_hashes and source_hashes[source_hash] != key:
        return 'duplicate', {'existing': source_hashes[source_hash], 'source_sha256': source_hash}
    directory = library.directory(key)
    if directory.exists():
        existing = library.get(key)
        if existing.get('source_sha256') != source_hash:
            raise ValueError(f'existing look differs from remote source: {key}')
        return 'existing', existing
    with tempfile.TemporaryDirectory(prefix='lumix-lut-', dir=OUTPUT) as temporary:
        master = Path(temporary) / 'master.cube'
        master.write_bytes(cube_bytes)
        parsed = load_cube(master)
        if parsed[0] != cube[0] or len(parsed[3]) != cube[0] ** 3:
            raise AssertionError('canonical cube changed while staging: ' + key)
        metadata = library.add_cube(master, key, label=label, input_space='srgb',
                                    description=metadata_extra.get('source_description', ''),
                                    method='fit')
    metadata.pop('source_path', None)
    metadata.update(metadata_extra)
    metadata.update(source_kind='lumix-lab-standard-cube', source_resource=source_name,
                    source_style=style, source_sha256=source_hash,
                    source_cube_sha256=cube_hash, source_grid=cube[0],
                    input_space='srgb', output_space='srgb',
                    scope='creative LUT applied after NX300M stock Standard processing',
                    calibration={'camera': 'NX300M', 'status': 'unmeasured'},
                    runtime_status='compiled-not-loaded')
    library._save(key, metadata)
    tables = read_tables(directory, metadata)
    if set(tables) != {'table.d4lut', 'table-stock3d.d4lut'} or any(len(data) != 4380 for data in tables.values()):
        raise AssertionError('compiled native table set is invalid: ' + key)
    source_hashes[source_hash] = key
    return 'imported', metadata


def process_remote(library: LookLibrary, output: Path, source_hashes: dict[str, str], *, refresh: bool):
    entries, skipped = [], []
    pages = {}
    for asset_id, category, preview, title, slug, files in STANDARD_ITEMS:
        lcid = asset_id.split('_', 1)[0]
        page_url = PAGE.format(lcid)
        if lcid not in pages:
            pages[lcid] = fetch(page_url, referer=SITE + '/').decode('utf-8', errors='replace')
        try:
            zip_url, heading, description = item_block(pages[lcid], preview)
            zip_bytes = fetch(zip_url, referer=page_url)
            archive_path = save_zip(output / 'source-zips', asset_id, zip_bytes, refresh=refresh)
            with zipfile.ZipFile(archive_path) as archive:
                if archive.testzip() is not None:
                    raise ValueError('remote ZIP CRC failed: ' + asset_id)
                for expected in files:
                    source_name, variant, _ = expected
                    zip_entry, source_bytes = choose_entry(archive, expected)
                    header_style = source_style(source_bytes, zip_entry.filename)
                    style = header_style or 'STANDARD'
                    ensure_standard(style, context=f'{asset_id}/{zip_entry.filename}')
                    if zip_entry.filename.lower().endswith('.vlt'):
                        cube_bytes, cube, conversion = parse_vlt(source_bytes)
                    else:
                        cube_bytes, conversion = source_bytes, {'converted_from_vlt': False}
                        with tempfile.TemporaryDirectory(prefix='lumix-cube-', dir=OUTPUT) as temporary:
                            path = Path(temporary) / 'source.cube'
                            path.write_bytes(cube_bytes)
                            cube = load_cube(path)
                    label = title + (' 冷' if variant == 'COOL' else ' 暖' if variant == 'WARM' else '')
                    file_slug = slug + ('-cool' if variant == 'COOL' else '-warm' if variant == 'WARM' else '')
                    key = look_id(file_slug)
                    extra = {
                        'source_collection': 'panasonic-china-realtime-lut-library',
                        'source_asset_id': asset_id,
                        'source_page': page_url,
                        'source_category': category,
                        'source_creator_heading': heading,
                        'source_description': description,
                        'source_base_style': 'standard-family',
                        'source_style_evidence': 'cube-header' if header_style else 'panasonic-page-whitelist',
                        'source_variant': variant,
                        'source_archive_sha256': digest(zip_bytes),
                        **conversion,
                    }
                    status, detail = import_file(
                        library, key=key, label=label, source_name=source_name,
                        source_bytes=source_bytes, cube_bytes=cube_bytes, cube=cube,
                        style=style, metadata_extra=extra, source_hashes=source_hashes)
                    record = {'id': key, 'label': label, 'status': status,
                              'source': 'panasonic-china', 'asset_id': asset_id,
                              'source_resource': source_name, 'source_style': style,
                              'source_style_evidence': 'cube-header' if header_style else 'panasonic-page-whitelist',
                              'source_sha256': digest(source_bytes), **conversion}
                    entries.append(record)
                    print(f"{status:8} {key}  {label}", flush=True)
        except Exception as error:
            skipped.append({'asset_id': asset_id, 'title': title, 'reason': str(error)})
            print(f"SKIP     {asset_id} {title}: {error}", flush=True)
    return entries, skipped


def process_local_zip(library: LookLibrary, path: Path, source_hashes: dict[str, str]):
    entries, skipped = [], []
    if not path.is_file():
        return entries, [{'resource': str(path), 'reason': 'file not found'}]
    try:
        with zipfile.ZipFile(path) as archive:
            if archive.testzip() is not None:
                raise ValueError('local ZIP CRC failed')
            candidates = lut_entries(archive)
            if not candidates:
                raise ValueError('local ZIP contains no .cube/.vlt LUT')
            for entry in candidates:
                source_bytes = archive.read(entry)
                style = source_style(source_bytes, entry.filename)
                try:
                    ensure_standard(style, context=f'{path.name}/{entry.filename}')
                except ValueError as error:
                    skipped.append({'resource': f'{path.name}/{entry.filename}',
                                    'reason': str(error), 'expected_rejection': True})
                    print(f"SKIP     {path.name}/{entry.filename}: {error}", flush=True)
                    continue
                if entry.filename.lower().endswith('.vlt'):
                    cube_bytes, cube, conversion = parse_vlt(source_bytes)
                else:
                    cube_bytes, conversion = source_bytes, {'converted_from_vlt': False}
                    with tempfile.TemporaryDirectory(prefix='lumix-cube-', dir=OUTPUT) as temporary:
                        master = Path(temporary) / 'source.cube'
                        master.write_bytes(cube_bytes)
                        cube = load_cube(master)
                title = title_from_cube(source_bytes) or Path(entry.filename).stem
                key = look_id(slugify(title))
                extra = {
                    'source_collection': 'local-lumix-lab-export',
                    'source_archive': path.name,
                    'source_archive_sha256': digest(path.read_bytes()),
                    'source_base_style': 'standard-family',
                    'source_variant': None,
                    **conversion,
                }
                status, _ = import_file(
                    library, key=key, label=title, source_name=entry.filename,
                    source_bytes=source_bytes, cube_bytes=cube_bytes, cube=cube,
                    style=style, metadata_extra=extra, source_hashes=source_hashes)
                record = {'id': key, 'label': title, 'status': status,
                          'source': 'local-zip', 'source_resource': entry.filename,
                          'source_style': style, 'source_sha256': digest(source_bytes), **conversion}
                entries.append(record)
                print(f"{status:8} {key}  {title}", flush=True)
    except Exception as error:
        skipped.append({'resource': str(path), 'reason': str(error)})
        print(f"SKIP     {path.name}: {error}", flush=True)
    return entries, skipped


def update_catalog(library: LookLibrary, keys: list[str]) -> bool:
    path = library.root / 'catalog.json'
    catalog = __import__('json').loads(path.read_text(encoding='utf-8'))
    if catalog.get('version') != 1:
        raise ValueError('unsupported LUT catalog version')
    groups = catalog.get('groups')
    if not isinstance(groups, list):
        raise ValueError('invalid LUT catalog groups')
    group = next((g for g in groups if g.get('name') == 'Lumix Lab'), None)
    if group is None:
        group = {'name': 'Lumix Lab', 'ids': []}
        groups.append(group)
    for key in keys:
        library.get(key)
        if key not in group['ids']:
            group['ids'].append(key)
    all_ids = [key for item in groups for key in item['ids']]
    if len(all_ids) != len(set(all_ids)):
        raise ValueError('duplicate LUT in camera catalog')
    if len(all_ids) > 256:
        raise ValueError('native LUT catalog supports at most 256 entries')
    data = (__import__('json').dumps(catalog, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    if path.read_bytes() == data:
        return False
    atomic_write(path, data)
    return True


def build_package(library: LookLibrary, output: Path) -> dict:
    from build_sd_bundle import bundle_looks
    with tempfile.TemporaryDirectory(prefix='lumix-package-', dir=output) as temporary:
        stage = Path(temporary) / 'root'
        ids = bundle_looks(stage, method=None, complete=True)
        rows = (stage / 'looks.txt').read_text(encoding='utf-8').splitlines()
        if len(rows) != len(ids):
            raise AssertionError('packaged catalogue count differs from look set')
        archive_path = output / 'nx300m-lumixlab-looks.zip'
        with zipfile.ZipFile(archive_path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(stage / 'looks.txt', 'nx300m-mod/looks.txt')
            for path in sorted((stage / 'looks').rglob('*')):
                if path.is_file():
                    archive.write(path, 'nx300m-mod/' + path.relative_to(stage).as_posix())
            archive.writestr('README.txt', (
                'NX300M LUMIX Lab local LUT catalogue\n\n'
                'Extract at the card root. This archive contains looks.txt and runtime\n'
                'table.d4lut/table-stock3d.d4lut files only. It does not modify binaries,\n'
                'selection state, firmware, or the root autoexec hook.\n\n'
                f'Catalogue entries: {len(rows)}\n'
                'Source scope: standard-based Panasonic LUTs plus one local LUMIX Lab export.\n'
                'V-Log and other Photo Style inputs are excluded.\n'
                'Keep this package local; creator redistribution rights are not asserted.\n'
            ).encode('utf-8'))
        with zipfile.ZipFile(archive_path) as archive:
            if archive.testzip() is not None:
                raise AssertionError('packaged LUT ZIP CRC failed')
            names = archive.namelist()
            if 'nx300m-mod/looks.txt' not in names or 'nx300m-mod/looks/nx300-adjust-base/table.d4lut' not in names:
                raise AssertionError('packaged LUT ZIP is incomplete')
        return {'path': archive_path.relative_to(PROJECT).as_posix(), 'looks': len(rows),
                'bytes': archive_path.stat().st_size}


def write_readme(output: Path, report: dict) -> None:
    lines = [
        '# LUMIX Lab 本地 LUT 导入', '',
        f'当前源库 {report["total_looks"]} 项；默认目录 {report["catalog_looks"]} 项；完整卡包 {report["package_looks"]} 项。', '',
        '只接收 Standard／标准色彩／标准色调基底。V-Log、CNED2、PORT、Classic Neo 等输入域已排除。', '',
        '来源：',
        '- 松下中国实时 LUT 库：https://www.panasonicshuma.cn/RealtimeLUTLibrary/list.aspx?lcid=113',
        '- 用户提供的 `City Film.zip` 本地官方导出。', '',
        '部署包只更新 `nx300m-mod/looks.txt` 和 `nx300m-mod/looks/`，不改二进制、启动钩子或选择状态。',
        '这批创作者 LUT 仅作本机集成；公开分发前需要逐条确认再分发授权。', '',
        '| 状态 | ID | 名称 | 来源 |', '| --- | --- | --- | --- |',
    ]
    lines.extend(f'| {e["status"]} | `{e["id"]}` | {e["label"]} | {e["source"]} |'
                 for e in report['entries'])
    if report['skipped']:
        lines.extend(['', '## 未加入', ''])
        lines.extend(f'- `{s.get("asset_id", s.get("resource", "?"))}`：{s["reason"]}'
                     for s in report['skipped'])
    (output / 'README.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=OUTPUT)
    parser.add_argument('--local-zip', action='append', type=Path, default=[])
    parser.add_argument('--skip-remote', action='store_true')
    parser.add_argument('--no-package', action='store_true')
    parser.add_argument('--refresh', action='store_true')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    library = LookLibrary()
    source_hashes = existing_sources(library)
    entries, skipped = [], []
    if not args.skip_remote:
        remote_entries, remote_skipped = process_remote(
            library, output, source_hashes, refresh=args.refresh)
        entries.extend(remote_entries)
        skipped.extend(remote_skipped)
    for path in args.local_zip:
        local_entries, local_skipped = process_local_zip(
            library, path.resolve(), source_hashes)
        entries.extend(local_entries)
        skipped.extend(local_skipped)
    successful = [e['id'] for e in entries if e['status'] != 'duplicate']
    catalog_updated = update_catalog(library, successful)
    all_looks = library.list()
    catalog = camera_looks(library)
    if len(all_looks) > 256:
        raise ValueError('source library exceeds native 256-entry catalogue limit')
    package = None if args.no_package else build_package(library, output)
    report = {
        'schema': 1,
        'source_policy': 'standard-family LUMIX source styles only; V-Log excluded',
        'entries': entries,
        'skipped': skipped,
        'catalog_updated': catalog_updated,
        'total_looks': len(all_looks),
        'catalog_looks': len(catalog),
        'package_looks': package['looks'] if package else None,
        'package': package,
        'complete': bool(entries) and not any(
            not item.get('expected_rejection') for item in skipped),
        'camera_pixels_verified': False,
    }
    atomic_write(output / 'inventory.json',
                 (__import__('json').dumps(report, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
    write_readme(output, report)
    print(__import__('json').dumps({k: report[k] for k in (
        'complete', 'total_looks', 'catalog_looks', 'package_looks', 'package', 'skipped')},
        ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
