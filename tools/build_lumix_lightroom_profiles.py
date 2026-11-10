"""Build adjustable Lightroom Classic presets from the supplied Lumix LUTs.

Each output embeds the LUT as an Adobe RGBTable creative layer. Lightroom can
then vary the profile amount and continue to use its ordinary Develop sliders.
The serialized table follows the MIT licensed OwenWhw/CUBE-to-XMP format.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import struct
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape
import zipfile
import zlib

import numpy as np
from PIL import Image, ImageCms, ImageDraw, ImageFont

from import_lightroom_rgb_tables import CreativeRgbTable, decode_payload
from nx300m_lut_core import Lut3D, apply_lut, expand_lut_paths, synthetic_reference


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / 'outputs/looks/lightroom-luts'
GROUP = 'NX300M Lumix Lab 可调版'
XMP_LIMIT = 32
CRS = 'http://ns.adobe.com/camera-raw-settings/1.0/'
RDF = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#'
ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ.-:+=^!/*?`'|()[]{}@%$#"

SOURCES = (
    ('01', 'Teal Flat-S', 'STD', Path(r'./local/private-reference')),
    ('02', 'Smoky Color-S', 'STD', Path(r'./local/private-reference')),
    ('03', 'Clear-S', 'STD', Path(r'./local/private-reference')),
    ('04', 'Backlight-N', 'NAT', Path(r'./local/private-reference')),
    ('05', 'Filmlike-V2', 'CNEV2', Path(r'./local/private-reference')),
    ('06', 'Summer Freshness', 'PORT', Path(r'./local/private-reference')),
    ('07', 'Fresh Bright', 'STD', Path(r'./local/private-reference')),
    ('08', 'Color Harmony', 'STD', Path(r'./local/private-reference')),
    ('09', 'TXD Grainy Film LUT', 'LCLASN', Path(r'./local/private-reference')),
)


@dataclass(frozen=True)
class ProfileSource:
    order: str
    label: str
    expected_style: str
    archive: Path
    resource: str
    source_sha256: str
    lut: Lut3D


def _base85(data: bytes) -> str:
    """Encode bytes with Adobe/DNG's little-endian radix-85 alphabet."""
    padded = data + b'\0\0\0'
    output = []
    for start in range(0, len(data), 4):
        value = struct.unpack('<I', padded[start:start + 4])[0]
        remaining = min(4, len(data) - start)
        for digit in range(remaining + 1):
            output.append(ALPHABET[value % 85])
            value //= 85
            if digit > 0 and len(output) % 5 == 0:
                break
    return ''.join(output)


def _resample(lut: Lut3D, size: int) -> np.ndarray:
    axis = np.linspace(0.0, 1.0, size, dtype=np.float32)
    r, g, b = np.meshgrid(axis, axis, axis, indexing='ij')
    points = np.stack((r, g, b), axis=-1).reshape(-1, 1, 3)
    return apply_lut(points, lut, strength=1.0).reshape(size, size, size, 3)


def _encode_table(table: np.ndarray) -> tuple[str, str, int]:
    size = table.shape[0]
    if table.shape != (size, size, size, 3):
        raise ValueError(f'Invalid RGB table shape: {table.shape}')
    nop = (np.arange(size, dtype=np.uint32) * 65535 + size // 2) // (size - 1)
    coordinates = np.stack(np.meshgrid(nop, nop, nop, indexing='ij'), axis=-1)
    values = np.rint(np.clip(table, 0.0, 1.0).astype(np.float64) * 65535).astype(np.uint32)
    deltas = ((values - coordinates) & 0xFFFF).astype('<u2')
    block = bytearray(struct.pack('<4I', 1, 1, 3, size))
    block.extend(deltas.tobytes())
    block.extend(struct.pack('<3I', 0, 1, 0))
    block.extend(struct.pack('<2d', 0.0, 2.0))
    raw = bytes(block)
    fingerprint = hashlib.md5(raw).hexdigest().upper()
    compressed = struct.pack('<I', len(raw)) + zlib.compress(raw, level=zlib.Z_DEFAULT_COMPRESSION)
    return fingerprint, _base85(compressed), len(raw)


def _description(style: str) -> str:
    common = '三维 RGBTable 保留原 LUT 的颜色映射；Lightroom 其他显影滑杆仍可继续调整。'
    if style == 'STD':
        return '原 LUT 输入域：STD / Standard。' + common
    return (f'原 LUT 输入域：{style}（非 Standard）；此处仅保留 LUT 的色彩映射，'
            '不包含该源风格的完整基础显影。' + common)


def _xmp(name: str, description: str, fingerprint: str, encoded: str,
         group_name: str = GROUP) -> str:
    uuid = hashlib.md5(('nx300m-lumixlab:' + fingerprint).encode('ascii')).hexdigest().upper()
    title = escape(name)
    desc = escape(description)
    group = escape(group_name)
    return f'''<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Adobe XMP Core 7.0-c000 1.000000, 0000/00/00-00:00:00        ">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"
   crs:PresetType="Look"
   crs:Cluster=""
   crs:UUID="{uuid}"
   crs:SupportsAmount="True"
   crs:SupportsColor="True"
   crs:SupportsMonochrome="False"
   crs:SupportsHighDynamicRange="True"
   crs:SupportsNormalDynamicRange="True"
   crs:SupportsSceneReferred="True"
   crs:SupportsOutputReferred="True"
   crs:RequiresRGBTables="False"
   crs:ShowInPresets="True"
   crs:ShowInQuickActions="False"
   crs:CameraModelRestriction=""
   crs:Copyright=""
   crs:ContactInfo=""
   crs:Version="18.1.1"
   crs:ProcessVersion="15.4"
   crs:ConvertToGrayscale="False"
   crs:RGBTable="{fingerprint}"
   crs:Table_{fingerprint}="{encoded}"
   crs:HasSettings="True">
   <crs:Name>
    <rdf:Alt>
     <rdf:li xml:lang="x-default">{title}</rdf:li>
    </rdf:Alt>
   </crs:Name>
   <crs:ShortName>
    <rdf:Alt>
     <rdf:li xml:lang="x-default"/>
    </rdf:Alt>
   </crs:ShortName>
   <crs:SortName>
    <rdf:Alt>
     <rdf:li xml:lang="x-default">{title}</rdf:li>
    </rdf:Alt>
   </crs:SortName>
   <crs:Group>
    <rdf:Alt>
     <rdf:li xml:lang="x-default">{group}</rdf:li>
    </rdf:Alt>
   </crs:Group>
   <crs:Description>
    <rdf:Alt>
     <rdf:li xml:lang="x-default">{desc}</rdf:li>
    </rdf:Alt>
   </crs:Description>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
'''


def _load_sources(paths: list[Path]) -> list[ProfileSource]:
    configured = {str(path).casefold(): (order, label, style) for order, label, style, path in SOURCES}
    result = []
    for path in paths:
        try:
            order, label, expected_style = configured[str(path).casefold()]
        except KeyError as error:
            raise ValueError(f'Unexpected source archive: {path}') from error
        files = expand_lut_paths([path])
        if len(files) != 1:
            raise ValueError(f'{path.name} must contain exactly one LUT; found {len(files)}')
        item = files[0]
        actual_style = item.lut.style or 'UNKNOWN'
        if actual_style != expected_style:
            raise ValueError(f'{item.name}: expected style {expected_style}, found {actual_style}')
        if any(abs(a) > 1e-7 or abs(b - 1.0) > 1e-7 for a, b in zip(item.lut.domain_min, item.lut.domain_max)):
            raise ValueError(f'{item.name}: Lightroom RGBTable requires a 0-1 input domain')
        result.append(ProfileSource(
            order, label, actual_style, path, item.name,
            hashlib.sha256(item.source_bytes).hexdigest(), item.lut,
        ))
    return sorted(result, key=lambda source: source.order)


def _local_name(value: str) -> str:
    return value.rsplit('}', 1)[-1]


def _verify_xmp(path: Path, source: ProfileSource, expected: np.ndarray,
                fingerprint: str, raw_size: int, group_name: str = GROUP) -> dict:
    root = ET.parse(path).getroot()
    description = next(root.iter(f'{{{RDF}}}Description'))
    attributes = {_local_name(key): value for key, value in description.attrib.items()}
    name = root.find(f'.//{{{CRS}}}Name/{{{RDF}}}Alt/{{{RDF}}}li')
    group = root.find(f'.//{{{CRS}}}Group/{{{RDF}}}Alt/{{{RDF}}}li')
    if attributes.get('RGBTable') != fingerprint:
        raise AssertionError(f'{path.name}: RGBTable fingerprint mismatch')
    if attributes.get('SupportsAmount') != 'True':
        raise AssertionError(f'{path.name}: adjustable Amount is not enabled')
    if not name.text or group.text != group_name:
        raise AssertionError(f'{path.name}: missing preset name or group')
    forbidden = [
        key for key in attributes
        if key.startswith(('Mask', 'Gradient', 'IncrementalTemperature', 'IncrementalTint'))
        or key in ('Exposure2012', 'Temperature', 'Tint')
    ]
    if forbidden:
        raise AssertionError(f'{path.name}: unintended fixed controls: {forbidden}')
    raw = decode_payload(attributes['Table_' + fingerprint], fingerprint)
    if len(raw) != raw_size:
        raise AssertionError(f'{path.name}: decoded table size mismatch')
    table = CreativeRgbTable(raw)
    encoded = table.values.reshape(expected.shape)
    quantization_max = float(np.max(np.abs(encoded - expected)))
    if quantization_max > 2.0 / 65535:
        raise AssertionError(f'{path.name}: RGBTable values differ after encoding')

    rng = np.random.default_rng(0x300 + int(source.order))
    points = rng.random((20000, 3), dtype=np.float32)
    source_values = apply_lut(points[:, None, :], source.lut)[:, 0, :]
    encoded_values = table.sample(points.astype(np.float64))
    absolute = np.abs(source_values - encoded_values)
    return {
        'file': path.name,
        'grid': int(table.size),
        'table_id': fingerprint,
        'quantization_max_abs': quantization_max,
        'heldout_rgb_rmse': float(np.sqrt(np.mean(np.square(absolute)))),
        'heldout_rgb_max_abs': float(absolute.max()),
        'forbidden_fixed_controls': forbidden,
    }


def _contact_sheet(sources: list[ProfileSource], path: Path) -> None:
    original = synthetic_reference(560, 360)
    tiles = [('Original', original, 'SRC')]
    tiles.extend((source.label, apply_lut(original, source.lut), source.expected_style) for source in sources)
    columns, width, image_height, label_height = 2, 560, 360, 48
    rows = (len(tiles) + columns - 1) // columns
    sheet = Image.new('RGB', (columns * width, rows * (image_height + label_height)), '#17191c')
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype('C:/Windows/Fonts/msyhbd.ttc', 21)
        small = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 17)
    except OSError:
        font = small = ImageFont.load_default()
    for index, (label, image, style) in enumerate(tiles):
        row, column = divmod(index, columns)
        left, top = column * width, row * (image_height + label_height)
        tile = Image.fromarray(np.clip(image * 255.0 + 0.5, 0, 255).astype(np.uint8))
        sheet.paste(tile, (left, top))
        draw.rectangle((left, top + image_height, left + width - 1, top + image_height + label_height - 1),
                       fill='#202328')
        draw.text((left + 14, top + image_height + 11), label, font=font, fill='#f4f2ee')
        draw.text((left + width - 104, top + image_height + 15), style, font=small, fill='#aab1ba')
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path, quality=94, icc_profile=profile)


def _readme(entries: list[dict]) -> str:
    lines = [
        '# NX300M × Lumix Lab Lightroom 预设',
        '',
        '这些 `.xmp` 将原 LUT 嵌入为 Adobe 三维 RGBTable。应用后仍可继续调整 Lightroom 的曝光、白平衡、基础面板、曲线、HSL/颜色和分级；配置已声明支持强度调节。',
        '',
        '安装：把 `NX300M Lumix Lab 可调版` 文件夹放到',
        '',
        '`%APPDATA%\\Adobe\\CameraRaw\\Settings\\`',
        '',
        '再重启 Lightroom Classic。预设分组名为 `NX300M Lumix Lab 可调版`。',
        '',
        '| 顺序 | 配置 | 原 LUT 输入域 | 输出网格 |',
        '|---:|---|---:|---:|',
    ]
    lines.extend(f"| {item['order']} | {item['name']} | {item['source_style']} | {item['grid']}³ |"
                 for item in entries)
    lines.extend([
        '',
        'STD 条目对应 Lumix Standard 输入域。NAT、CNEV2、PORT、LCLASN 条目也保留原始 LUT 映射，但这些 LUT 依赖各自的基础风格；Lightroom 里只应用 LUT 色彩层，不能仅凭它还原该基础显影的全部关系。',
        '',
        '验证覆盖 XML 结构、RGBTable 指纹、双向解码、20,000 个留出颜色采样及固定曝光/白平衡/蒙版字段检查。这里验证的是文件与颜色表，不等于在 Lightroom 实际渲染后的逐像素证明。',
        '',
    ])
    return '\n'.join(lines)


def run(sources: list[Path], output: Path, install: Path | None = None) -> dict:
    loaded = _load_sources(sources)
    profile_dir = output / GROUP
    profile_dir.mkdir(parents=True, exist_ok=True)
    entries, verification = [], []
    for source in loaded:
        size = min(XMP_LIMIT, source.lut.size)
        table = _resample(source.lut, size)
        fingerprint, encoded, raw_size = _encode_table(table)
        name = f'{source.order} {source.label} [{source.expected_style}]'
        path = profile_dir / f'{name}.xmp'
        path.write_text(_xmp(name, _description(source.expected_style), fingerprint, encoded),
                        encoding='utf-8', newline='\n')
        verification.append(_verify_xmp(path, source, table, fingerprint, raw_size))
        entries.append({
            'order': source.order,
            'name': name,
            'source_zip': source.archive.name,
            'source_resource': source.resource,
            'source_sha256': source.source_sha256,
            'source_style': source.expected_style,
            'source_grid': source.lut.size,
            'grid': size,
            'rgb_table_id': fingerprint,
            'file': path.name,
            'bytes': path.stat().st_size,
        })

    _contact_sheet(loaded, output / 'previews' / 'lumix-look-contact-sheet.jpg')
    manifest = {
        'schema': 1,
        'group': GROUP,
        'scope': 'Adobe RGBTable creative layer; all ordinary Lightroom develop sliders remain editable',
        'complete': True,
        'source_count': len(loaded),
        'profile_count': len(entries),
        'entries': entries,
    }
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    report = {
        'schema': 1,
        'checks': ['xml_well_formed', 'table_fingerprint', 'table_decode', 'supports_amount',
                   'no_fixed_exposure_wb_mask', 'heldout_sampling'],
        'runtime_lightroom_render_verified': False,
        'entries': verification,
    }
    (output / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (output / 'README.md').write_text(_readme(entries), encoding='utf-8', newline='\n')

    bundle = output / 'NX300M-LumixLab-Lightroom.zip'
    with zipfile.ZipFile(bundle, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(profile_dir.glob('*.xmp')):
            archive.write(path, Path(GROUP) / path.name)
        for name in ('README.md', 'manifest.json', 'verification.json'):
            archive.write(output / name, name)

    if install is not None:
        install.mkdir(parents=True, exist_ok=True)
        expected_hashes = {}
        for path in sorted(profile_dir.glob('*.xmp')):
            destination = install / path.name
            destination.write_bytes(path.read_bytes())
            expected_hashes[destination.name] = hashlib.sha256(destination.read_bytes()).hexdigest()
        installed = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in sorted(install.glob('*.xmp'))}
        missing = sorted(set(expected_hashes) - set(installed))
        changed = sorted(name for name, digest in expected_hashes.items() if installed.get(name) != digest)
        if missing or changed:
            raise AssertionError(f'Install verification failed: missing={missing}, changed={changed}')
        manifest['install'] = {'path': str(install), 'files': len(expected_hashes), 'verified': True}
        (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    manifest['bundle'] = bundle.name
    manifest['bundle_sha256'] = hashlib.sha256(bundle.read_bytes()).hexdigest()
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return {
        'profiles': len(entries),
        'output': str(output),
        'bundle': str(bundle),
        'bundle_sha256': manifest['bundle_sha256'],
        'verification': verification,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', action='append', type=Path, help='input ZIP; defaults to the nine supplied archives')
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--install-dir', type=Path,
                        help='also install into this Lightroom Settings subdirectory')
    args = parser.parse_args()
    sources = args.source or [item[3] for item in SOURCES]
    result = run(sources, args.output.resolve(), args.install_dir.resolve() if args.install_dir else None)
    print(json.dumps({key: value for key, value in result.items() if key != 'verification'},
                     ensure_ascii=False, indent=2))
    print('maximum held-out RGB error:',
          max(item['heldout_rgb_max_abs'] for item in result['verification']))


if __name__ == '__main__':
    main()
