"""Maintain a human-readable, directly editable local LUT repository.

`looks/` remains the compile/runtime source used by the camera bundlers. This
tool exposes the same masters in `lut-library/LUTs/<group>/`, detects manual
cube edits, and applies them back with a full per-entry backup and rebuild.
"""
from __future__ import annotations

import argparse
from collections import OrderedDict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil

from build_sd_bundle import bundle_looks
from cube_to_d4 import load_cube
from look_library import LookLibrary
from lut_table_data import read_tables


ROOT = Path(__file__).resolve().parents[1]
LOOKS = ROOT / 'looks'
LOCAL = ROOT / 'lut-library'
CUBES = LOCAL / 'LUTs'
CAMERA = LOCAL / 'camera'
CATALOG = LOCAL / 'catalog.json'
INDEX = LOCAL / 'catalog.tsv'
MACHINE_CATALOG = LOCAL / 'machine-catalog.json'
ID_SUFFIX = re.compile(r'\[([a-z0-9][a-z0-9_-]{0,62})\]\.cube$', re.IGNORECASE)
INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
GROUP_ORDER = (
    '松下', '富士', '宾得', '奥林巴斯', '尼康', '柯达', '适马', '创意与其他', 'App 专属',
    '基础与黑白', '自制', '日常色彩', '电影胶片',
    '品牌相机', 'Lumix Lab', '我提供的 9 款',
    'CXO 正片', 'CXO 负片', 'CXO 黑白', 'CXO 电影', 'CXO 数码', 'CXO 交叉冲印',
)
USER_LUT_ORDER = (
    'panasonic-lumix-teal-flat-s', 'panasonic-lumix-smoky-color-s',
    'panasonic-lumix-clear-s', 'panasonic-lumix-backlight-n',
    'panasonic-lumix-filmlike-v2', 'panasonic-lumix-summer-freshness',
    'panasonic-lumix-fresh-bright', 'panasonic-lumix-color-harmony',
    'panasonic-lumix-txd-grainy-film',
)
USER_LUT_IDS = set(USER_LUT_ORDER)
BRAND_PREFIXES = (
    'nx300-fuji-', 'ricoh-', 'nx300-pentax', 'pentax-', 'olympus-',
    'nikon-', 'sigma-', 'sony-', 'panasonic-',
)
BRAND_CATALOG_GROUPS = {'富士', '理光', '奥林巴斯', '宾得'}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_part(value: str, limit: int = 88) -> str:
    value = INVALID_FILENAME.sub('_', value)
    value = ' '.join(value.split()).strip(' .')
    return value[:limit].rstrip(' .') or 'LUT'


def infer_group(metadata: dict, catalog_group: str = '') -> str:
    key = metadata['id']
    if catalog_group in ('松下','富士','宾得','奥林巴斯','尼康','柯达','适马','创意与其他','App 专属'):
        return catalog_group
    if key.startswith('panasonic-'):
        return '松下'
    for prefix,group in [('nx300-fuji-','富士'),('nikon-','尼康'),('sigma-','适马')]:
        if key.startswith(prefix):
            return group
    if key.startswith('cxo-'):
        return {
            '正片': 'CXO 正片',
            '负片': 'CXO 负片',
            '黑白胶片': 'CXO 黑白',
            '电影胶片': 'CXO 电影',
            '数码胶片': 'CXO 数码',
            '交叉冲印': 'CXO 交叉冲印',
        }.get(metadata.get('cxo_category'), 'CXO 电影')
    if key in USER_LUT_IDS:
        return '我提供的 9 款'
    if metadata.get('source_kind') == 'blend-linear-srgb' or key.startswith('blend-'):
        return '自制'
    text = (str(metadata.get('label', '')) + ' ' + key).casefold()
    if key == 'nx300-adjust-base' or key == 'nx300-hard-mono' or any(
            word in text for word in ('黑白', '硬调', 'mono', 'black', 'b&w')):
        return '基础与黑白'
    if (catalog_group in BRAND_CATALOG_GROUPS or key.startswith(BRAND_PREFIXES)
            or key.startswith('nx-original-')):
        return '品牌相机'
    if catalog_group == '胶片模拟' or any(word in text for word in (
            '电影', '胶片', '反转', '负片', '正片', '漂白', '复', '旧', '港',
            '暗', '棕', '褪', '青橙', '金属', '雾', 'film', 'cine', 'vintage',
            'retro', 'fade')):
        return '电影胶片'
    return '日常色彩'


def machine_selection(library: LookLibrary) -> list[str]:
    source_catalog = library.root / 'catalog.json'
    data = json.loads(source_catalog.read_text(encoding='utf-8'))
    keys = [key for group in data['groups'] if group.get('camera', True) for key in group['ids']]
    keys = list(dict.fromkeys(keys))
    if len(keys) > 255:
        raise ValueError(f'入机 LUT 数量 {len(keys)} 超过 255 上限')
    missing = [key for key in keys if not (library.directory(key) / 'look.json').is_file()]
    if missing:
        raise ValueError('入机目录包含缺失 LUT：' + ', '.join(missing))
    return keys


def source_catalog_groups(library: LookLibrary) -> tuple[OrderedDict[str, list[str]], dict[str, list[str]]]:
    path = library.root / 'catalog.json'
    data = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {'groups': []}
    groups = OrderedDict((group['name'], []) for group in data.get('groups', []))
    membership = {}
    for group in data.get('groups', []):
        for key in group['ids']:
            membership.setdefault(key, []).append(group['name'])
    return groups, membership


def desired_filename(index: int, metadata: dict) -> str:
    label = safe_part(metadata.get('label') or metadata['id'])
    return f'{index:03d} {label} [{metadata["id"]}].cube'


def entry_record(path: Path, group: str, source_sha: str, local_sha: str) -> dict:
    return {
        'id': path.name.rsplit('[', 1)[1][:-5],
        'group': group,
        'file': path.relative_to(LOCAL).as_posix(),
        'source_sha256': source_sha,
        'local_sha256': local_sha,
    }


def scan(paths: list[Path] | None = None) -> dict:
    files = sorted((paths if paths is not None else CUBES.rglob('*.cube')), key=lambda path: path.as_posix().casefold())
    found = {}
    errors = []
    for path in files:
        match = ID_SUFFIX.search(path.name)
        if not match:
            errors.append({'file': str(path), 'reason': '文件名末尾必须包含 [稳定ID].cube'})
            continue
        key = match.group(1).lower()
        if key in found:
            errors.append({'file': str(path), 'reason': f'重复 ID：{key}'})
            continue
        found[key] = path
    catalog = json.loads(CATALOG.read_text(encoding='utf-8')) if CATALOG.is_file() else {'entries': []}
    saved = {entry['id']: entry for entry in catalog.get('entries', [])}
    changed, moved, renamed, unknown, missing, invalid = [], [], [], [], [], []
    for key, path in found.items():
        group = path.parent.name
        entry = saved.get(key)
        if entry is None:
            unknown.append({
                'id': key, 'file': path.relative_to(LOCAL).as_posix(), 'group': group,
                'label': path.name.rsplit('[', 1)[0].strip().split(' ', 1)[-1],
            })
            continue
        current = digest(path.read_bytes())
        if current != entry['local_sha256']:
            changed.append({'id': key, 'file': path.relative_to(LOCAL).as_posix()})
            try:
                load_cube(path)
            except Exception as error:
                invalid.append({'id': key, 'file': path.relative_to(LOCAL).as_posix(), 'error': str(error)})
        if entry['group'] != group:
            moved.append({'id': key, 'from': entry['group'], 'to': group})
        saved_label = path.name.rsplit('[', 1)[0].strip().split(' ', 1)[-1]
        if entry.get('label') and saved_label != entry['label']:
            renamed.append({'id': key, 'from': entry['label'], 'to': saved_label})
    for key, entry in saved.items():
        if key not in found:
            missing.append({'id': key, 'file': entry['file']})
    return {'entries': found, 'saved': saved, 'errors': errors, 'changed': changed,
            'moved': moved, 'renamed': renamed, 'unknown': unknown,
            'missing': missing, 'invalid': invalid}


def _write_index(catalog: dict) -> None:
    lines = ['分组\t状态\t名称\t稳定ID\t源网格\t文件']
    for entry in catalog['entries']:
        lines.append('\t'.join(str(entry.get(name, '')) for name in
                                ('group', 'status', 'label', 'id', 'source_grid', 'file')))
    INDEX.write_text('\n'.join(lines) + '\n', encoding='utf-8', newline='\n')


def _readme(catalog: dict) -> str:
    group_counts = OrderedDict()
    for entry in catalog['entries']:
        group_counts[entry['group']] = group_counts.get(entry['group'], 0) + 1
    lines = [
        '# NX300M 本地 LUT 仓库',
        '',
        f'当前 {len(catalog["entries"])} 个相机 LUT。直接编辑 `LUTs/` 里的 `.cube` 文件；文件名末尾的 `[稳定ID]` 用于同步，不要删除。',
        '',
        '工作流：',
        '',
        '1. 在 `LUTs/` 中按分类打开并修改 `.cube`。',
        '2. 双击 `检查修改.cmd` 查看变化、语法和数量。',
        '3. 双击 `应用修改并重编译.cmd` 写回 `looks/`、更新标签并重建相机原生表。',
        '4. 双击 `部署到相机卡.cmd` 校验后写入默认 `H:` 卡；卡内条目和现有分组会先备份。',
        '',
        '`camera/` 是完整的相机运行时目录：`looks.txt` 和 `looks/<ID>/` 中的 `table.d4lut`、sRGB LUT 的 `table-stock3d.d4lut`、`look.json`。本次只在本地生成和验证，不读取或写入相机卡。',
        '',
        f'相机目录只启用经过筛选的 {catalog.get("camera", {}).get("source_entries", 0)} 款 LUT；完整 {len(catalog["entries"])} 款仍保留在 `LUTs/` 供本地管理和后续替换。',
        '',
        '移动 Cube 到另一个分类目录会改变它的主分组；修改文件名中 `[ID]` 前面的名称会改变 Lightroom/相机中使用的显示名称。`.backups/` 保存每次应用前的完整 LUT 条目。',
        '',
        '## 分类',
        '',
    ]
    lines.extend(f'- {group}: {count}' for group, count in group_counts.items())
    lines.extend([
        '',
        '`Lumix Lab · 非 Standard` 中的 LUT 原始 Photo Style 分别是 NAT、CNEV2、PORT、LCLASN。本地库保留其颜色表，但相机只消费标准化 sRGB 表，不包含这些 Photo Style 的完整基础显影。',
        '',
    ])
    return '\n'.join(lines)


def _verify_camera_stage(library: LookLibrary) -> dict:
    errors = []
    catalog_path = CAMERA / 'looks.txt'
    looks_root = CAMERA / 'looks'
    if not catalog_path.is_file() or not looks_root.is_dir():
        return {'camera_ready': False, 'rows': 0, 'native_tables': 0,
                'composed_tables': 0, 'errors': ['camera/looks.txt 或 camera/looks 不存在']}
    rows = [line.split('|') for line in catalog_path.read_text(encoding='utf-8').splitlines() if line]
    keys = [row[0] for row in rows]
    directories = {path.name for path in looks_root.iterdir() if path.is_dir()}
    # The importer's factory composition asset is not a selectable style.
    if 'nx300-adjust-base' not in keys:
        directories.discard('nx300-adjust-base')
    if any(len(row) != 9 for row in rows):
        errors.append('looks.txt 存在字段数不为 9 的行')
    if len(keys) != len(set(keys)):
        errors.append('looks.txt 存在重复 ID')
    if len(keys) > 256:
        errors.append(f'looks.txt 有 {len(keys)} 项，超过相机 256 项上限')
    if set(keys) != directories:
        errors.append('looks.txt 与 camera/looks 目录集合不一致')
    native_tables = composed_tables = 0
    for key in keys:
        if key == 'nx300-adjust-base':
            continue
        if key not in directories:
            continue
        try:
            metadata = library.get(key)
            expected = read_tables(library.directory(key), metadata)
            for name, data in expected.items():
                packaged = looks_root / key / name
                if not packaged.is_file() or packaged.read_bytes() != data:
                    errors.append(f'{key}/{name} 与源库不一致')
                elif name == 'table.d4lut':
                    native_tables += 1
                else:
                    composed_tables += 1
        except Exception as error:
            errors.append(f'{key}: {error}')
    verification = {
        'camera_ready': not errors,
        'rows': len(keys),
        'source_entries': len(keys) - (1 if 'nx300-adjust-base' in keys else 0),
        'native_tables': native_tables,
        'composed_tables': composed_tables,
        'catalog_limit': 256,
        'errors': errors,
        'camera_runtime_verified': False,
    }
    (LOCAL / 'camera-verification.json').write_text(
        json.dumps(verification, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8', newline='\n')
    return verification


def _refresh_camera_stage(library: LookLibrary, include_ids: list[str], *, method: str | None = 'fit') -> tuple[set[str], dict]:
    if CAMERA.exists():
        if CAMERA.is_symlink() or CAMERA.resolve().parent != LOCAL.resolve():
            raise ValueError(f'Unexpected camera staging path: {CAMERA}')
        shutil.rmtree(CAMERA)
    keys = set(bundle_looks(CAMERA, method=method, complete=True, include_ids=include_ids))
    return keys, _verify_camera_stage(library)


def sync(*, compile_camera: bool = True) -> dict:
    library = LookLibrary()
    rows = library.list()
    _, membership = source_catalog_groups(library)
    previous = json.loads(CATALOG.read_text(encoding='utf-8')) if CATALOG.is_file() else {'entries': []}
    previous_by_id = {entry['id']: entry for entry in previous.get('entries', [])}
    CUBES.mkdir(parents=True, exist_ok=True)
    entries = []
    group_counters = {}
    created = updated = preserved = 0
    for metadata in sorted(rows, key=lambda item: (item['id'])):
        key = metadata['id']
        group = infer_group(metadata, (membership.get(key) or [''])[0])
        groups = [group]
        group = safe_part(group, 64)
        group_dir = CUBES / group
        group_dir.mkdir(parents=True, exist_ok=True)
        group_counters[group] = group_counters.get(group, 0) + 1
        old = previous_by_id.get(key)
        target = group_dir / desired_filename(group_counters[group], metadata)
        source = library.directory(key) / 'source.cube'
        source_hash = digest(source.read_bytes())
        if old and (LOCAL / old['file']).is_file():
            local_path = LOCAL / old['file']
            local_hash = digest(local_path.read_bytes())
            if local_hash != old['local_sha256']:
                preserved += 1
            elif source_hash != old['source_sha256']:
                local_path.write_bytes(source.read_bytes())
                local_hash = source_hash
                updated += 1
            if local_path != target:
                if target.exists():
                    raise FileExistsError(f'目标文件已存在：{target}')
                local_path.rename(target)
                local_path = target
        else:
            target.write_bytes(source.read_bytes())
            local_path = target
            created += 1
        local_hash = digest(local_path.read_bytes())
        entries.append({
            'id': key,
            'label': metadata.get('label', key),
            'group': group,
            'groups': groups,
            'file': local_path.relative_to(LOCAL).as_posix(),
            'source_sha256': source_hash,
            'local_sha256': local_hash,
            'source_grid': metadata.get('source_grid') or _grid_from_cube(local_path),
            'source_kind': metadata.get('source_kind', ''),
            'source_style': metadata.get('source_base_style') or metadata.get('source_style'),
            'status': 'ready',
        })
    for path in sorted(CUBES.rglob('*'), reverse=True):
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    machine_ids = machine_selection(library)
    machine_data = {
        'schema': 1,
        'policy': 'explicit selection from looks/catalog.json; full source library preserved',
        'count': len(machine_ids),
        'ids': machine_ids,
    }
    MACHINE_CATALOG.write_text(
        json.dumps(machine_data, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8', newline='\n')
    camera_ids, camera_verification = _refresh_camera_stage(
        library, machine_ids, method='fit' if compile_camera else None,
    )
    for entry in entries:
        ready = entry['id'] in camera_ids and not camera_verification['errors']
        entry['camera_ready'] = ready
        entry['status'] = 'camera-ready' if ready else 'source-only'
    catalog = {
        'schema': 1,
        'root': 'looks',
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'count': len(entries),
        'groups': [group for group in GROUP_ORDER if any(entry['group'] == group for entry in entries)],
        'entries': entries,
        'camera': camera_verification,
        'machine_catalog': {'count': len(machine_ids), 'file': MACHINE_CATALOG.name},
    }
    CATALOG.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
    _write_index(catalog)
    (LOCAL / 'README.md').write_text(_readme(catalog), encoding='utf-8', newline='\n')
    _write_wrappers()
    return {'count': len(entries), 'created': created, 'updated': updated,
            'preserved_edits': preserved, 'camera': camera_verification}


def _grid_from_cube(path: Path) -> int:
    return int(load_cube(path)[0])


def _write_wrappers() -> None:
    python = Path(r'D:\conda\envs\nx300m-mod\python.exe')
    script = Path(__file__).resolve()
    status = (
        '@echo off\r\n'
        f'"{python}" "{script}" status\r\n'
        'echo.\r\npause\r\n'
    )
    apply = (
        '@echo off\r\n'
        f'"{python}" "{script}" apply\r\n'
        'echo.\r\npause\r\n'
    )
    deploy_script = Path(__file__).resolve().parents[1] / 'tools/deploy_local_lut_library.py'
    deploy = (
        '@echo off\r\n'
        f'"{python}" "{script}" apply\r\n'
        f'"{python}" "{deploy_script}" --card H:\\\r\n'
        'echo.\r\npause\r\n'
    )
    (LOCAL / '检查修改.cmd').write_text(status, encoding='gbk', newline='')
    (LOCAL / '应用修改并重编译.cmd').write_text(apply, encoding='gbk', newline='')
    (LOCAL / '部署到相机卡.cmd').write_text(deploy, encoding='gbk', newline='')


def status() -> dict:
    result = scan()
    summary = {name: len(result[name]) for name in
               ('changed', 'moved', 'renamed', 'unknown', 'missing', 'invalid', 'errors')}
    camera = _verify_camera_stage(LookLibrary())
    return {**summary, 'camera': camera, **{name: result[name] for name in summary}}


def _snapshot_entry(directory: Path, backup: Path) -> None:
    backup.mkdir(parents=True, exist_ok=False)
    for path in directory.iterdir():
        if path.is_file():
            shutil.copy2(path, backup / path.name)


def _restore_entry(directory: Path, backup: Path) -> None:
    expected = {path.name for path in backup.iterdir() if path.is_file()}
    for path in list(directory.iterdir()):
        if path.is_file() and path.name not in expected:
            path.unlink()
    for path in backup.iterdir():
        if path.is_file():
            shutil.copy2(path, directory / path.name)


def apply_changes() -> dict:
    result = scan()
    if result['invalid'] or result['errors']:
        raise ValueError(f'本地 Cube 校验失败：{result["invalid"] + result["errors"]}')
    if result['unknown']:
        raise ValueError('发现未知 ID；请使用稳定 ID 后缀或先导入到 looks/：' +
                         ', '.join(item['id'] for item in result['unknown']))
    catalog = json.loads(CATALOG.read_text(encoding='utf-8'))
    saved = {entry['id']: entry for entry in catalog['entries']}
    library = LookLibrary()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup_root = LOCAL / '.backups' / stamp
    touched = set(item['id'] for item in result['changed'] + result['renamed'])
    snapshots = {}
    try:
        for key in sorted(touched):
            path = result['entries'][key]
            directory = library.directory(key)
            backup = backup_root / key
            _snapshot_entry(directory, backup)
            snapshots[key] = (directory, backup)
            metadata = library.get(key)
            label = path.name.rsplit('[', 1)[0].strip().split(' ', 1)[-1]
            if key in {item['id'] for item in result['changed']}:
                (directory / 'source.cube').write_bytes(path.read_bytes())
                metadata['source_cube_sha256'] = digest(path.read_bytes())
            metadata['label'] = library.label(label)
            library._save(key, metadata)
            if key in {item['id'] for item in result['changed']}:
                library.build(key, method='fit')
    except Exception:
        for directory, backup in snapshots.values():
            _restore_entry(directory, backup)
        raise
    groups_by_id = {}
    for key, path in result['entries'].items():
        groups_by_id[key] = [path.parent.name]
    for entry in catalog['entries']:
        path = result['entries'][entry['id']]
        entry.update(
            label=path.name.rsplit('[', 1)[0].strip().split(' ', 1)[-1],
            group=path.parent.name,
            groups=groups_by_id[entry['id']],
            local_sha256=digest(path.read_bytes()),
            source_sha256=digest((library.directory(entry['id']) / 'source.cube').read_bytes()),
            source_grid=_grid_from_cube(path),
            status='ready',
        )
    machine_ids = machine_selection(library)
    camera_ids, camera_verification = _refresh_camera_stage(library, machine_ids)
    for entry in catalog['entries']:
        entry['camera_ready'] = entry['id'] in camera_ids and not camera_verification['errors']
        entry['status'] = 'camera-ready' if entry['camera_ready'] else 'source-only'
    catalog['camera'] = camera_verification
    catalog['generated_at'] = datetime.now(timezone.utc).isoformat()
    CATALOG.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
    _write_index(catalog)
    (LOCAL / 'README.md').write_text(_readme(catalog), encoding='utf-8', newline='\n')
    return {'applied': sorted(touched), 'backup': str(backup_root) if touched else None,
            'camera': camera_verification}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('sync', 'status', 'apply'))
    args = parser.parse_args()
    if args.command == 'sync':
        result = sync()
    elif args.command == 'status':
        result = status()
    else:
        result = apply_changes()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
