"""Import curated CXO FilmPack cubes into the camera library and Lightroom presets."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import zipfile

import numpy as np

from build_lumix_lightroom_profiles import (
    ProfileSource,
    _contact_sheet,
    _description,
    _encode_table,
    _resample,
    _verify_xmp,
    _xmp,
)
from cube_to_d4 import load_cube
from look_library import LookLibrary
from manage_local_lut_library import CAMERA, GROUP_ORDER, sync
from nx300m_lut_core import parse_lut_file


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'outputs/looks/cxo-import'
MASTERS = OUTPUT / 'masters'
LR_GROUP = 'NX300M × CXO FilmPack 可调版'
LICENSE_RELATIVE = Path('mod/nxconnect/third_party/cxo-filmpack/LICENSE')
REPLACED_IDS = {
    'ColorModeArgentiqueFujiVelvia50': 'metol-velvia50',
    'ColorModeArgentiqueKodakTriX400': 'metol-trix400',
    'ColorModeDigitalFujiClassicChrome': 'nx300-fuji-classic-chrome',
    'ColorModeDigitalFujiAstiaSoft': 'nx300-fuji-astia',
    'ColorModeDigitalFujiEterna': 'nx300-fuji-eterna',
    'ColorModeDigitalFujiClassicNeg': 'nx300-fuji-classic-negative',
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_manifest() -> dict:
    path = MASTERS / 'manifest.json'
    if not path.is_file():
        raise FileNotFoundError('Run the CXO cube exporter first: ' + str(path))
    manifest = json.loads(path.read_text(encoding='utf-8'))
    if manifest.get('schema') != 1 or manifest.get('count') != len(manifest.get('entries', [])):
        raise ValueError('Invalid CXO export manifest')
    return manifest


def import_camera_looks(manifest: dict) -> list[dict]:
    library = LookLibrary()
    imported = []
    for entry in manifest['entries']:
        key = entry['camera_id']
        cube_path = MASTERS / entry['cube']
        if not cube_path.is_file() or sha256(cube_path) != entry['cube_sha256']:
            raise ValueError(f'CXO cube identity mismatch: {cube_path}')
        if load_cube(cube_path)[0] != entry['cube_grid']:
            raise ValueError(f'CXO cube grid mismatch: {cube_path}')
        directory = library.directory(key)
        if directory.exists():
            source = directory / 'source.cube'
            if not source.is_file() or sha256(source) != entry['cube_sha256']:
                raise ValueError(f'Existing look differs from CXO export: {key}')
        else:
            library.add_cube(
                cube_path,
                key,
                label=entry['label'],
                input_space='srgb',
                description=f"CXO FilmPack 1.0.1 · {entry['cxo_category']} · {entry['cxo_name']}",
                method='fit',
            )
        metadata = library.get(key)
        metadata.update({
            'source_kind': 'cxo-filmpack',
            'source_grid': entry['cube_grid'],
            'source_style': 'CXO FilmPack 1.0.1',
            'scope': 'creative sRGB film transform after NXConnect camera RAW development and phone LUT',
            'upstream': manifest['upstream'],
            'upstream_commit': manifest['upstream_commit'],
            'upstream_license': 'CXO FilmPack Non-Commercial License',
            'cxo_source_id': entry['source_id'],
            'cxo_source_asset': entry['source_asset'],
            'cxo_source_asset_sha256': entry['source_asset_sha256'],
            'cxo_category': entry['cxo_category'],
            'cxo_model': entry['cxo_model'],
            'cxo_intensity': entry['intensity'],
        })
        metadata.pop('source_path', None)
        library._save(key, metadata)
        imported.append({
            'id': key,
            'label': entry['label'],
            'category': entry['cxo_category'],
            'source_id': entry['source_id'],
            'cube_sha256': entry['cube_sha256'],
        })

    catalog_path = library.root / 'catalog.json'
    catalog = json.loads(catalog_path.read_text(encoding='utf-8'))
    groups = catalog.setdefault('groups', [])
    by_name = {group['name']: group for group in groups}
    wanted = {
        '正片': 'CXO 正片',
        '负片': 'CXO 负片',
        '黑白胶片': 'CXO 黑白',
        '电影胶片': 'CXO 电影',
        '数码胶片': 'CXO 数码',
        '交叉冲印': 'CXO 交叉冲印',
    }
    for target_name in wanted.values():
        if target_name not in by_name:
            group = {'name': target_name, 'ids': []}
            groups.append(group)
            by_name[target_name] = group
    existing_ids = {key for group in groups for key in group['ids']}
    for entry in imported:
        target = by_name[wanted[entry['category']]]
        if entry['id'] not in existing_ids:
            target['ids'].append(entry['id'])
            existing_ids.add(entry['id'])
    demoted = {REPLACED_IDS[entry['source_id']] for entry in imported if entry['source_id'] in REPLACED_IDS}
    for group in groups:
        group['ids'] = [key for key in group['ids'] if key not in demoted]
    catalog_path.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8', newline='\n',
    )
    # The source library is already compiled when each CXO cube is added. Reusing
    # those tables avoids rebuilding unrelated looks after even a one-line tool edit.
    result = sync(compile_camera=False)
    if result['camera']['errors']:
        raise ValueError('Camera library verification failed: ' + ', '.join(result['camera']['errors']))
    return imported


def build_lightroom(manifest: dict, install: Path | None) -> dict:
    profile_dir = OUTPUT / LR_GROUP
    profile_dir.mkdir(parents=True, exist_ok=True)
    profiles, entries, verification = [], [], []
    for index, entry in enumerate(manifest['entries'], 1):
        master = MASTERS / entry['cube']
        item = parse_lut_file(master)
        source = ProfileSource(
            str(index),
            entry['label'],
            'CXO',
            master,
            item.name,
            sha256(master),
            item.lut,
        )
        size = min(32, item.lut.size)
        table = _resample(item.lut, size)
        fingerprint, encoded, raw_size = _encode_table(table)
        name = f"{index:02d} {entry['label']}"
        path = profile_dir / f'{name}.xmp'
        description = (
            f"CXO FilmPack 1.0.1 · {entry['cxo_category']} · {entry['cxo_name']}。"
            + _description('CXO')
        )
        path.write_text(
            _xmp(name, description, fingerprint, encoded, group_name=LR_GROUP),
            encoding='utf-8', newline='\n',
        )
        verification.append(_verify_xmp(
            path, source, table, fingerprint, raw_size, group_name=LR_GROUP,
        ))
        profiles.append(source)
        entries.append({
            'order': index,
            'name': name,
            'camera_id': entry['camera_id'],
            'source_id': entry['source_id'],
            'source_cube_sha256': source.source_sha256,
            'grid': size,
            'rgb_table_id': fingerprint,
            'file': path.name,
            'bytes': path.stat().st_size,
        })

    _contact_sheet(profiles, OUTPUT / 'previews' / 'cxo-film-contact-sheet.jpg')
    manifest_path = OUTPUT / 'lightroom-manifest.json'
    manifest_path.write_text(json.dumps({
        'schema': 1,
        'group': LR_GROUP,
        'scope': 'adjustable Adobe RGBTable creative layer; develop sliders remain editable',
        'count': len(entries),
        'entries': entries,
    }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    verify_path = OUTPUT / 'lightroom-verification.json'
    verify_path.write_text(json.dumps({
        'schema': 1,
        'runtime_lightroom_render_verified': False,
        'checks': ['xml_well_formed', 'table_fingerprint', 'table_decode', 'supports_amount',
                   'no_fixed_exposure_wb_mask', 'heldout_sampling'],
        'entries': verification,
    }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    readme = f'''# NX300M × CXO FilmPack

本包含 {len(entries)} 个新增 CXO FilmPack 风格、对应 NX300M 原生 9×9×9 表和可调 Lightroom Classic 预设。

## Lightroom

把 `{LR_GROUP}` 文件夹放入 `%APPDATA%\\Adobe\\CameraRaw\\Settings\\`，重启 Lightroom Classic。预设支持 Amount，也保留曝光、白平衡、曲线、HSL/颜色和分级滑杆。

这些 `.xmp` 是 Camera Raw RGBTable 预设，不是 DCP 相机配置文件。

## 相机

相机目录位于 `lut-library/camera`。`looks.txt` 与 `looks/` 一起覆盖到卡内 `nx300m-mod/`；本批新增项的稳定 ID 以 `cxo-` 开头。

本包输入和输出均为 sRGB，胶片强度按 CXO 100% 烘焙。相机端的 9×9×9 表经过离线减少；实际取景、拍摄和 Lightroom 渲染需要在设备上验收。

CXO FilmPack 仅按非商业许可使用，来源与许可证文本随包保留。
'''
    (OUTPUT / 'README.md').write_text(readme, encoding='utf-8', newline='\n')

    lr_bundle = OUTPUT / 'NX300M-CXO-FilmPack-Lightroom.zip'
    with zipfile.ZipFile(lr_bundle, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(profile_dir.glob('*.xmp')):
            archive.write(path, Path(LR_GROUP) / path.name)
        for name in ('README.md', 'lightroom-manifest.json', 'lightroom-verification.json'):
            archive.write(OUTPUT / name, name)
        archive.write(ROOT / LICENSE_RELATIVE, 'licenses/CXO-FilmPack-LICENSE.txt')

    if install is not None:
        target = install / LR_GROUP
        target.mkdir(parents=True, exist_ok=True)
        for path in sorted(profile_dir.glob('*.xmp')):
            shutil.copy2(path, target / path.name)
    return {
        'profiles': len(entries),
        'bundle': str(lr_bundle.relative_to(ROOT)),
        'installed': str(install / LR_GROUP) if install else None,
        'verification': verify_path.relative_to(ROOT).as_posix(),
    }


def package_camera() -> dict:
    camera_bundle = OUTPUT / 'NX300M-LUT-Library-with-CXO.zip'
    with zipfile.ZipFile(camera_bundle, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(CAMERA.rglob('*')):
            if path.is_file():
                archive.write(path, Path('nx300m-mod') / path.relative_to(CAMERA))
        archive.write(
            ROOT / LICENSE_RELATIVE,
            'nx300m-mod/licenses/CXO-FilmPack-LICENSE.txt',
        )
        archive.write(OUTPUT / 'README.md', 'nx300m-mod/CXO-README.md')
    return {
        'bundle': camera_bundle.relative_to(ROOT).as_posix(),
        'bytes': camera_bundle.stat().st_size,
        'sha256': sha256(camera_bundle),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', type=Path, help='Lightroom CameraRaw Settings directory')
    args = parser.parse_args()
    manifest = load_manifest()
    imported = import_camera_looks(manifest)
    lightroom = build_lightroom(manifest, args.install)
    camera = package_camera()
    result = {
        'imported': len(imported),
        'camera_rows': len(list((CAMERA / 'looks').iterdir())),
        'lightroom': lightroom,
        'camera_package': camera,
    }
    (OUTPUT / 'result.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8',
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
