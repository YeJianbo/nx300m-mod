"""Windows SD-card updater for a checked NX300M deployment candidate."""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
import shutil
import struct
import sys
import tempfile
import uuid
import zipfile
from datetime import datetime
from installer_backend import verify_backend
from manual_lens_wifi_payload import PREFIX as WIFI_PREFIX, verify_wifi_payload
from nx300m_lut_core import expand_lut_paths
from release_payload import verify_complete

BASE = Path(sys.executable if getattr(sys, 'frozen', False) else __file__).resolve().parent
MANIFEST = BASE / 'manifest.json'
RESTART = 'nx300m-mod/RESTART_SERVICE'
FIRST = BASE / 'first-install'
LUT_GROUPS = 'nx300m-mod/.state/lut-groups.json'
LUT_GROUPS_CAMERA = 'nx300m-mod/.state/lut-groups.txt'
LUT_GROUP_LIMIT = 32
STANDARD_LUT_STYLES = {'STD', 'STANDARD', 'STANDARD COLOR', 'STANDARD COLOUR',
                       'STANDARD TONE'}


def standard_lut_style(style):
    if not style:
        return True
    return ' '.join(str(style).upper().split()) in STANDARD_LUT_STYLES


def prepare_first(card, identity):
    if identity['filesystem'].upper() not in ('FAT32', 'EXFAT'):
        raise ValueError('请选择 FAT32 或 exFAT 存储卡。')
    for name in ('autoexec.sh', 'nx300m-mod', 'nx300m-lens-debug'):
        path = safe_path(card, name)
        if path.exists() or path.is_symlink():
            raise ValueError('卡上已有 ' + name + '，为保护现有配置，请使用升级流程或另一张卡。')
    info = json.loads((FIRST / 'manifest.json').read_text(encoding='utf-8'))
    if info.get('schema') != 1 or info.get('kind') != 'nx300m-first-install-local-candidate':
        raise ValueError('首次安装文件格式不受支持。')
    files = info['files']
    if 'autoexec.sh' not in files or 'nx300m-mod/mfw-service-entry.sh' not in files:
        raise ValueError('首次安装文件不完整。')
    size = 0
    for name, rec in files.items():
        if name != 'autoexec.sh' and not name.startswith(('nx300m-mod/', WIFI_PREFIX)):
            raise ValueError('安装包含不支持的路径：' + name)
        data = safe_path(FIRST / 'payload', name).read_bytes()
        if len(data) != rec['bytes'] or checksum(data) != rec['sha256']:
            raise ValueError('首次安装文件校验失败：' + name)
        size += len(data)
    verify_complete(lambda name: safe_path(FIRST / 'payload', name).read_bytes())
    if shutil.disk_usage(card).free < size + 4 * 1024 * 1024:
        raise ValueError('存储卡剩余空间不足。')
    return files


def install_first(card, identity):
    files = prepare_first(card, identity)
    if volume(str(card)) != identity:
        raise ValueError('存储卡信息已变化，请重新检查。')
    stage = card / ('.nx300m-install-' + uuid.uuid4().hex)
    stage.mkdir()
    try:
        for name, rec in files.items():
            if volume(str(card)) != identity:
                raise OSError('写入期间存储卡已变化。')
            data = safe_path(FIRST / 'payload', name).read_bytes()
            if checksum(data) != rec['sha256']:
                raise ValueError('安装文件发生变化：' + name)
            target = safe_path(stage, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            write_checked(target, data)
        if volume(str(card)) != identity:
            raise OSError('写入期间存储卡已变化。')
        verify_complete(lambda name: safe_path(stage, name).read_bytes())
        # Both runtime directories must exist before publishing the boot entry.
        for name in ('nx300m-lens-debug', 'nx300m-mod', 'autoexec.sh'):
            target = safe_path(card, name)
            if target.exists() or target.is_symlink():
                raise OSError('目标已出现，已停止安装：' + name)
            os.rename(stage / name, target)
        stage.rmdir()
    except (OSError, ValueError) as exc:
        raise OSError(f'{exc}\n安装中断；请保留存储卡。临时目录：{stage}。如已出现 nx300m-mod，请先检查后再重试。') from exc


def uninstall_mod(card, identity):
    """Move the recognized boot entry and MOD into a reversible on-card backup."""
    if volume(str(card)) != identity:
        raise ValueError('存储卡信息已变化，请重新选择。')
    entry = safe_path(card, 'autoexec.sh')
    mod = safe_path(card, 'nx300m-mod')
    info = json.loads((FIRST / 'manifest.json').read_text(encoding='utf-8'))
    expected = info['files']['autoexec.sh']['sha256']
    if not entry.is_file() or checksum(entry.read_bytes()) != expected:
        raise ValueError('启动脚本与本工具不匹配，无法自动卸载；请保留现有脚本。')
    if not mod.is_dir() or not safe_path(card, 'nx300m-mod/mfw-service-entry.sh').is_file():
        raise ValueError('未找到可识别的 MOD 目录。')
    lens = safe_path(card, 'nx300m-lens-debug')
    lens_owned = False
    if lens.is_dir():
        try:
            verify_wifi_payload(lambda name: safe_path(card, name).read_bytes(), allow_stock=True)
            lens_owned = True
        except (OSError, ValueError):
            pass  # Preserve an unrecognized directory; the root entry is removed.
    backup = safe_path(card, 'nx300m-mod-backup-' + uuid.uuid4().hex[:12])
    backup.mkdir()
    try:
        if volume(str(card)) != identity:
            raise OSError('存储卡信息已变化。')
        os.rename(entry, backup / 'autoexec.sh')
        if volume(str(card)) != identity:
            raise OSError('存储卡信息已变化。')
        os.rename(mod, backup / 'nx300m-mod')
        if lens_owned:
            if volume(str(card)) != identity:
                raise OSError('存储卡信息已变化。')
            os.rename(lens, backup / 'nx300m-lens-debug')
    except OSError as exc:
        raise OSError(f'{exc}\n卸载未完成；请保留卡上文件，检查备份目录 {backup}。') from exc
    return backup


def checksum(data):
    return hashlib.sha256(data).hexdigest()


def safe_path(root, name):
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name:
        raise ValueError('路径不安全：' + name)
    target = root.joinpath(*path.parts)
    for item in (target, *target.parents):
        if item == root:
            break
        if item.is_symlink() or (item.exists() and getattr(item.stat(), 'st_file_attributes', 0) & 0x400):
            raise ValueError('发现链接或重解析点：' + name)
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError('路径超出目标目录：' + name)
    return target


def volume(root):
    root = str(root)
    if os.name != 'nt' or len(root) != 3 or root[1:] != ':\\':
        raise ValueError('请选择 Windows 可移动磁盘根目录。')
    kernel = ctypes.windll.kernel32
    if kernel.GetDriveTypeW(root) != 2:
        raise ValueError('请选择读卡器识别出的可移动磁盘。')
    label = ctypes.create_unicode_buffer(256)
    fs = ctypes.create_unicode_buffer(256)
    serial = ctypes.c_ulong()
    if not kernel.GetVolumeInformationW(root, label, 256, ctypes.byref(serial), None, None, fs, 256):
        raise OSError('无法读取磁盘信息，确认已插入存储卡。')
    return {'serial': f'{serial.value:08X}', 'label': label.value, 'filesystem': fs.value}


def prepare(card):
    info = json.loads(MANIFEST.read_text(encoding='utf-8'))
    if info.get('kind') != 'nx300m-cold-boot-update' or info.get('schema') != 1:
        raise ValueError('升级包格式不受支持。')
    complete_runtime = bool(info.get('complete_runtime'))
    if not complete_runtime:
        for name, expected in info['requires'].items():
            file = safe_path(card, name)
            if not file.is_file() or checksum(file.read_bytes()) != expected:
                raise ValueError('后端版本不匹配或文件缺失：' + name)
    writes = {}
    for name, rec in info['payload'].items():
        dest = safe_path(card, name)
        current = checksum(dest.read_bytes()) if dest.is_file() else None
        if dest.exists() and not dest.is_file():
            raise ValueError('目标不是普通文件：' + name)
        if rec.get('preserve_existing') and current is not None:
            data = dest.read_bytes()
            valid = ((name == WIFI_PREFIX + 'WIFI_MEM_RUN_ID' and re.fullmatch(rb'[0-9A-Za-z-]{1,64}\n', data)) or
                     (name == WIFI_PREFIX + 'WIFI_MEM_PROFILE' and data in (b'stock\n', b'manual-lens-wifi\n')))
            if not valid:
                raise ValueError('已有无线配置无效，请保留并检查：' + name)
            continue
        if current not in rec['accepted']:
            raise ValueError('目标版本不匹配：' + name)
        data = safe_path(BASE, rec['asset']).read_bytes()
        if checksum(data) != rec['sha256']:
            raise ValueError('升级包校验失败：' + name)
        writes[name] = data
    read = lambda name: writes[name] if name in writes else safe_path(card, name).read_bytes()
    if complete_runtime:
        verify_complete(read, allow_stock_wifi=True)
    else:
        verify_backend(read, info.get('backend_files'))
    return writes


def write_checked(path, data):
    fd, name = tempfile.mkstemp(prefix='.nx-update-', dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        if temp.read_bytes() != data:
            raise OSError('临时文件回读失败：' + str(path))
        os.replace(temp, path)
        if path.read_bytes() != data:
            raise OSError('写入回读失败：' + str(path))
    finally:
        temp.unlink(missing_ok=True)


def apply(card, identity):
    writes = prepare(card)
    if volume(str(card))['serial'] != identity['serial']:
        raise ValueError('磁盘已更换，请重新检查。')
    backups = BASE / 'backups'
    if backups.resolve().is_relative_to(card.resolve()):
        raise ValueError('请先把程序完整解压到电脑。')
    backup = backups / (datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
    backup.mkdir(parents=True)
    records = {}
    restart = safe_path(card, RESTART)
    all_writes = dict(writes)
    all_writes[RESTART] = ('gui-update-' + uuid.uuid4().hex + '\n').encode('ascii')
    for name in all_writes:
        source = safe_path(card, name)
        old = source.read_bytes() if source.is_file() else None
        if old is not None:
            dst = safe_path(backup, 'before/' + name)
            dst.parent.mkdir(parents=True, exist_ok=True)
            write_checked(dst, old)
        records[name] = {'before_sha256': checksum(old) if old is not None else None,
                         'installed_sha256': checksum(all_writes[name])}
    write_checked(backup / 'backup.json', json.dumps({'files': records}, indent=2).encode())
    try:
        names = [name for name in all_writes if name not in (RESTART, 'autoexec.sh')]
        if 'autoexec.sh' in all_writes:
            names.append('autoexec.sh')
        for name in names:
            data = all_writes[name]
            if volume(str(card))['serial'] != identity['serial']:
                raise OSError('写入期间磁盘已更换')
            target = safe_path(card, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            write_checked(target, data)
        backend_files = json.loads(MANIFEST.read_text(encoding='utf-8')).get('backend_files')
        verify_backend(lambda name: safe_path(card, name).read_bytes(), backend_files)
        if volume(str(card))['serial'] != identity['serial']:
            raise OSError('写入期间磁盘已更换')
        write_checked(safe_path(card, RESTART), all_writes[RESTART])
    except (OSError, ValueError) as exc:
        raise OSError(f'{exc}\n请保留存储卡，备份位于：{backup}') from exc
    return backup


def export_logs(card, destination, identity):
    logs = safe_path(card, 'nx300m-mod/logs')
    selected = []
    if logs.is_dir():
        for path in logs.iterdir():
            if path.is_file() and not path.is_symlink() and path.suffix.lower() in ('.log', '.txt'):
                if not (getattr(path.stat(), 'st_file_attributes', 0) & 0x400):
                    selected.append((path, 'logs/' + path.name))
    lens_logs = safe_path(card, 'nx300m-lens-debug/logs')
    if lens_logs.is_dir():
        for path in lens_logs.rglob('*'):
            if not path.is_file() or path.suffix.lower() not in ('.log', '.txt', '.json'):
                continue
            try:
                checked = safe_path(card, path.relative_to(card).as_posix())
            except ValueError:
                continue
            selected.append((checked, 'lens-wifi/' + path.relative_to(lens_logs).as_posix()))
    entry_log = safe_path(card, 'nx300m-lens-debug/wifi-mem-entry.log')
    if entry_log.is_file():
        selected.append((entry_log, 'lens-wifi/wifi-mem-entry.log'))
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('说明.txt', '日志可能包含相机、网络和电脑路径信息，请检查后再分享。\n未包含照片或完整配置。\n')
        archive.writestr('设备信息.json', json.dumps(identity, ensure_ascii=False, indent=2))
        local_log = BASE / 'installer.log'
        if local_log.is_file() and not local_log.is_symlink():
            archive.write(local_log, 'installer.log')
        for path, name in selected:
            archive.write(path, name)
    return len(selected)


def catalog(card):
    path = safe_path(card, 'nx300m-mod/looks.txt')
    raw = path.read_bytes()
    lines = raw.decode('utf-8-sig').splitlines()
    rows = [line.split('|') for line in lines if line.strip()]
    if not rows or any(len(r) != 9 or not r[0] or not r[1] for r in rows):
        raise ValueError('LUT 目录格式不受支持。')
    if len({r[0] for r in rows}) != len(rows):
        raise ValueError('LUT 目录存在重复 ID。')
    return raw, rows


def rename_look(card, identity, key, label):
    label = label.strip()
    if not label or len(label.encode('utf-8')) > 120 or any(c in '|\r\n\0' or ord(c) < 32 for c in label):
        raise ValueError('名称不能为空，也不能包含竖线或换行。')
    original, rows = catalog(card)
    match = next((r for r in rows if r[0] == key), None)
    if match is None:
        raise ValueError('风格已不存在，请刷新列表。')
    match[1] = label
    backup = BASE / 'backups' / (datetime.now().strftime('%Y%m%d-%H%M%S') + '-lut-' + uuid.uuid4().hex[:6])
    backup.mkdir(parents=True)
    write_checked(backup / 'looks.txt', original)
    metadata_path = safe_path(card, 'nx300m-mod/looks/' + key + '/look.json')
    metadata_bytes = metadata_path.read_bytes() if metadata_path.is_file() else None
    if metadata_bytes is not None:
        write_checked(backup / 'look.json', metadata_bytes)
    if volume(str(card)) != identity or catalog(card)[0] != original:
        raise ValueError('存储卡或风格目录发生变化，请重新操作。')
    metadata = None
    if metadata_bytes is not None:
        metadata = json.loads(metadata_bytes.decode('utf-8'))
        metadata['label'] = label
    try:
        if metadata is not None:
            write_checked(metadata_path,
                          (json.dumps(metadata, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
        write_checked(safe_path(card, 'nx300m-mod/looks.txt'),
                      ('\n'.join('|'.join(row) for row in rows) + '\n').encode('utf-8'))
    except Exception:
        if metadata_bytes is not None:
            write_checked(metadata_path, metadata_bytes)
        write_checked(safe_path(card, 'nx300m-mod/looks.txt'), original)
        raise
    return backup


def add_builtin_looks(card, identity):
    original, rows = catalog(card)
    _, builtins = catalog(FIRST/'payload')
    known={row[0] for row in rows}
    additions=[row for row in builtins if row[0] not in known]
    if not additions: return 0, None
    if len(rows)+len(additions)>255:
        raise ValueError('风格列表已满。')
    manifest=json.loads((FIRST/'manifest.json').read_text(encoding='utf-8'))
    pending=[]
    for row in additions:
        relative='nx300m-mod/looks/'+row[0]
        destination=safe_path(card,relative)
        if destination.exists(): raise ValueError('目录已存在但未列入风格列表：'+row[0])
        metadata=json.loads(safe_path(FIRST/'payload',relative+'/look.json').read_text(encoding='utf-8'))
        if metadata.get('source_kind')!='original-procedural' or metadata.get('license')!='CC0-1.0':
            raise ValueError('此安装包不包含可添加的原创风格。')
        for name in ('table.d4lut','look.json'):
            path=relative+'/'+name
            data=safe_path(FIRST/'payload',path).read_bytes()
            if checksum(data)!=manifest['files'][path]['sha256']: raise ValueError('内置风格校验失败。')
            pending.append((path,data))
    backup=BASE/'backups'/(datetime.now().strftime('%Y%m%d-%H%M%S')+'-builtin-'+uuid.uuid4().hex[:6])
    backup.mkdir(parents=True);write_checked(backup/'looks.txt',original)
    for path,data in pending:
        if volume(str(card))!=identity: raise ValueError('存储卡信息已变化。')
        target=safe_path(card,path);target.parent.mkdir(parents=True,exist_ok=True);write_checked(target,data)
    if volume(str(card))!=identity or catalog(card)[0]!=original:
        raise ValueError('存储卡或风格列表发生变化，新风格尚未加入列表。')
    addition=(''.join('|'.join(row)+'\n' for row in additions)).encode('utf-8')
    write_checked(safe_path(card,'nx300m-mod/looks.txt'),original.rstrip(b'\r\n')+b'\n'+addition)
    return len(additions),backup


def import_cube(card, identity, source, label):
    from cube_to_d4 import compile_cube, load_cube
    if BASE.resolve().is_relative_to(card.resolve()):
        raise ValueError('请先把程序完整解压到电脑。')
    label = label.strip()
    if not label or len(label.encode('utf-8')) > 120 or any(c in '|\r\n\0' or ord(c) < 32 for c in label):
        raise ValueError('名称不能为空，也不能包含竖线或换行。')
    original, rows = catalog(card)
    if len(rows) >= 255:
        raise ValueError('风格列表已满，无法继续导入。')
    baseline_path = safe_path(card, 'nx300m-mod/looks/nx300-adjust-base/table-stock3d.d4lut')
    stock = None
    if baseline_path.exists():
        baseline = baseline_path.read_bytes()
        if len(baseline) != 4380 or checksum(baseline) != '8e90e5a7528928585c86e4fe7e74d689ed96abd6cbb37f94bce234ddca09a07a':
            raise ValueError('原厂组合表不匹配。')
        stock = struct.unpack('<2190H', baseline)
        if any(x > 1023 for x in stock) or any(stock[i] for i in (729, 1459, 2189)):
            raise ValueError('原厂组合表格式错误。')
    source_data = source.read_bytes()
    cube = load_cube(source)
    direct = compile_cube(cube, method='fit')
    still = compile_cube(cube, stock, method='fit') if stock is not None else None
    if volume(str(card))['serial'] != identity['serial'] or catalog(card)[0] != original:
        raise ValueError('存储卡或风格目录发生变化，请重新操作。')
    key = 'import-' + uuid.uuid4().hex[:16]
    lookdir = safe_path(card, 'nx300m-mod/looks/' + key)
    lookdir.mkdir()
    backup = BASE / 'backups' / (datetime.now().strftime('%Y%m%d-%H%M%S') + '-lut-' + uuid.uuid4().hex[:6])
    backup.mkdir(parents=True)
    write_checked(backup / 'looks.txt', original)
    write_checked(backup / 'source.cube', source_data)
    metadata = {'id': key, 'label': label, 'source_kind': 'user-cube', 'input_space': 'srgb',
                'output_space': 'srgb', 'native_table': 'table.d4lut',
                'native_format': {'grid': 9, 'channels': 3, 'bits': 10, 'bytes': 4380},
                'calibration': {'camera': 'NX300M', 'status': 'unmeasured'},
                'compiler': {'method': 'fit', 'tables': {'table.d4lut': checksum(direct)}}}
    tables = [('table.d4lut', direct)]
    if still is not None:
        tables.append(('table-stock3d.d4lut', still))
        metadata['native_stock_3d_table'] = 'table-stock3d.d4lut'
        metadata['compiler']['tables']['table-stock3d.d4lut'] = checksum(still)
    tables.append(('look.json', (json.dumps(metadata, ensure_ascii=False, indent=2)+'\n').encode('utf-8')))
    for name, data in tables:
        write_checked(lookdir / name, data)
    if volume(str(card))['serial'] != identity['serial'] or catalog(card)[0] != original:
        raise ValueError('存储卡或风格目录发生变化；新风格尚未加入目录。')
    line = (key + '|' + label + '|0|0|0|0|0|0|0\n').encode('utf-8')
    write_checked(safe_path(card, 'nx300m-mod/looks.txt'), original.rstrip(b'\r\n') + b'\n' + line)
    return key, backup


def look_metadata(card, key):
    path = safe_path(card, 'nx300m-mod/looks/' + key + '/look.json')
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding='utf-8'))


def favorite_keys(card):
    path = safe_path(card, 'nx300m-mod/.state/lut-favorites')
    if not path.is_file():
        return set()
    return {token for token in path.read_text(encoding='utf-8').split() if token}


def set_favorite(card, identity, key, favorite):
    set_favorites(card, identity, [key], favorite)
    return favorite


def set_favorites(card, identity, keys, favorite):
    _, rows = catalog(card)
    requested = list(dict.fromkeys(keys))
    if not requested or any(key not in {row[0] for row in rows} for key in requested):
        raise ValueError('风格已不存在，请刷新列表。')
    path = safe_path(card, 'nx300m-mod/.state/lut-favorites')
    selected = favorite_keys(card)
    if favorite:
        selected.update(requested)
    else:
        selected.difference_update(requested)
    ordered = [row[0] for row in rows if row[0] in selected]
    data = ''.join(item + '\n' for item in ordered).encode('ascii')
    if volume(str(card)) != identity:
        raise ValueError('存储卡信息已变化。')
    path.parent.mkdir(parents=True, exist_ok=True)
    write_checked(path, data)
    actual = favorite_keys(card)
    if any((key not in actual) if favorite else (key in actual) for key in requested):
        raise OSError('收藏状态回读不一致。')
    return ordered


def _group_label(value):
    if not isinstance(value, str):
        raise ValueError('分组名称必须是文本。')
    label = str(value).strip()
    if not label or len(label.encode('utf-8')) > 80 or any(
            ord(char) < 32 or char in '|\r\n\0' for char in label):
        raise ValueError('分组名称不能为空，不能含控制字符或竖线，且最多 80 字节。')
    return label


def _group_state(raw, rows):
    if raw is None:
        return {'schema': 1, 'groups': [], 'members': {}}
    try:
        value = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError('LUT 分组文件不是有效 JSON。') from error
    if not isinstance(value, dict) or value.get('schema') != 1:
        raise ValueError('LUT 分组文件版本不受支持。')
    groups = []
    group_ids = set()
    labels = set()
    items = value.get('groups')
    members = value.get('members')
    if not isinstance(items, list) or not isinstance(members, dict):
        raise ValueError('LUT 分组文件结构不正确。')
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('LUT 分组条目结构不正确。')
        group_id = item.get('id')
        if not isinstance(group_id, str) or not re.fullmatch(r'group-[0-9a-f]{12}', group_id):
            raise ValueError('LUT 分组 ID 不正确。')
        label = _group_label(item.get('label'))
        if group_id in group_ids or label.casefold() in labels:
            raise ValueError('LUT 分组 ID 或名称重复。')
        group_ids.add(group_id)
        labels.add(label.casefold())
        groups.append({'id': group_id, 'label': label})
    look_ids = {row[0] for row in rows}
    normalized = {}
    for look_id, selected in members.items():
        if look_id not in look_ids:
            continue
        if not isinstance(selected, list) or any(
                not isinstance(group_id, str) or group_id not in group_ids for group_id in selected):
            raise ValueError('LUT 分组归属结构不正确。')
        ordered = [item['id'] for item in groups if item['id'] in set(selected)]
        if ordered:
            normalized[look_id] = ordered
    return {'schema': 1, 'groups': groups, 'members': normalized}


def _read_lut_groups(card, rows):
    path = safe_path(card, LUT_GROUPS)
    raw = path.read_bytes() if path.is_file() else None
    return raw, _group_state(raw, rows)


def lut_groups(card):
    _, rows = catalog(card)
    return _read_lut_groups(card, rows)[1]


def _lut_group_camera_data(state):
    lines = ['NXUTG1']
    lines.extend(
        'G|{}|{}'.format(group['id'], group['label']) for group in state['groups'])
    lines.extend(
        'L|{}|{}'.format(look_id, '|'.join(group_ids))
        for look_id, group_ids in state['members'].items())
    return ('\n'.join(lines) + '\n').encode('utf-8')


def _write_lut_groups(card, identity, original, state, action):
    if volume(str(card)) != identity:
        raise ValueError('存储卡信息已变化。')
    backup = BASE / 'backups' / (
        datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + action + '-' +
        uuid.uuid4().hex[:6])
    backup.mkdir(parents=True)
    if original is None:
        write_checked(backup / 'previous-missing.txt', b'no lut-groups.json\n')
    else:
        write_checked(backup / 'lut-groups.json', original)
    path = safe_path(card, LUT_GROUPS)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(state, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    camera_path = safe_path(card, LUT_GROUPS_CAMERA)
    camera_original = camera_path.read_bytes() if camera_path.is_file() else None
    camera_data = _lut_group_camera_data(state)
    try:
        write_checked(path, data)
        write_checked(camera_path, camera_data)
        if lut_groups(card) != state:
            raise OSError('LUT 分组回读不一致。')
        if camera_path.read_bytes() != camera_data:
            raise OSError('相机端 LUT 分组回读不一致。')
    except Exception:
        if original is None:
            path.unlink(missing_ok=True)
        else:
            write_checked(path, original)
        if camera_original is None:
            camera_path.unlink(missing_ok=True)
        else:
            write_checked(camera_path, camera_original)
        raise
    return backup


def create_lut_group(card, identity, label):
    _, rows = catalog(card)
    original, state = _read_lut_groups(card, rows)
    label = _group_label(label)
    if any(item['label'].casefold() == label.casefold() for item in state['groups']):
        raise ValueError('已有同名 LUT 分组。')
    if len(state['groups']) >= LUT_GROUP_LIMIT:
        raise ValueError(f'相机端最多支持 {LUT_GROUP_LIMIT} 个自定义分组。')
    state['groups'].append({'id': 'group-' + uuid.uuid4().hex[:12], 'label': label})
    backup = _write_lut_groups(card, identity, original, state, 'lut-group-create')
    return state['groups'][-1], backup


def rename_lut_group(card, identity, group_id, label):
    _, rows = catalog(card)
    original, state = _read_lut_groups(card, rows)
    item = next((group for group in state['groups'] if group['id'] == group_id), None)
    if item is None:
        raise ValueError('LUT 分组已不存在，请刷新。')
    label = _group_label(label)
    if any(group['id'] != group_id and group['label'].casefold() == label.casefold()
           for group in state['groups']):
        raise ValueError('已有同名 LUT 分组。')
    item['label'] = label
    backup = _write_lut_groups(card, identity, original, state, 'lut-group-rename')
    return backup


def delete_lut_group(card, identity, group_id):
    _, rows = catalog(card)
    original, state = _read_lut_groups(card, rows)
    if not any(group['id'] == group_id for group in state['groups']):
        raise ValueError('LUT 分组已不存在，请刷新。')
    state['groups'] = [group for group in state['groups'] if group['id'] != group_id]
    for look_id in list(state['members']):
        state['members'][look_id] = [
            selected for selected in state['members'][look_id] if selected != group_id]
        if not state['members'][look_id]:
            del state['members'][look_id]
    backup = _write_lut_groups(card, identity, original, state, 'lut-group-delete')
    return backup


def set_looks_groups(card, identity, assignments):
    """Apply one group transaction for multiple LUTs."""
    _, rows = catalog(card)
    known = {row[0] for row in rows}
    original, state = _read_lut_groups(card, rows)
    available = {item['id'] for item in state['groups']}
    if not assignments or any(key not in known for key in assignments):
        raise ValueError('风格已不存在，请刷新。')
    changed = False
    for key, group_ids in assignments.items():
        requested = set(group_ids)
        if not requested <= available:
            raise ValueError('所选 LUT 分组已不存在，请刷新。')
        ordered = [item['id'] for item in state['groups'] if item['id'] in requested]
        if ordered:
            if state['members'].get(key, []) != ordered:
                state['members'][key] = ordered
                changed = True
        elif key in state['members']:
            state['members'].pop(key, None)
            changed = True
    if not changed:
        return None
    return _write_lut_groups(card, identity, original, state, 'lut-group-assign')


def set_look_groups(card, identity, key, group_ids):
    return set_looks_groups(card, identity, {key: group_ids})


def import_lut_sources(card, identity, paths):
    """Import .cube/.vlt files or ZIPs as one checked catalogue transaction."""
    from cube_to_d4 import compile_cube, load_cube
    paths = [Path(path) for path in paths]
    if not paths:
        raise ValueError('没有选择 LUT 文件。')
    if BASE.resolve().is_relative_to(card.resolve()):
        raise ValueError('请先把程序完整解压到电脑。')
    original, rows = catalog(card)
    if len(rows) >= 255:
        raise ValueError('风格列表已满，无法继续导入。')
    existing_hashes = {}
    known_labels = {row[1].casefold() for row in rows}
    for row in rows:
        metadata = look_metadata(card, row[0])
        if metadata.get('source_sha256'):
            existing_hashes[metadata['source_sha256']] = row[0]
        else:
            legacy_source = safe_path(card, 'nx300m-mod/looks/' + row[0] + '/source.cube')
            if legacy_source.is_file():
                existing_hashes[checksum(legacy_source.read_bytes())] = row[0]
    expanded = expand_lut_paths(paths)
    baseline_path = safe_path(card, 'nx300m-mod/looks/nx300-adjust-base/table-stock3d.d4lut')
    stock = None
    if baseline_path.is_file():
        baseline = baseline_path.read_bytes()
        if len(baseline) != 4380 or checksum(baseline) != '8e90e5a7528928585c86e4fe7e74d689ed96abd6cbb37f94bce234ddca09a07a':
            raise ValueError('原厂组合表不匹配。')
        stock = struct.unpack('<2190H', baseline)
        if any(value > 1023 for value in stock) or any(stock[i] for i in (729, 1459, 2189)):
            raise ValueError('原厂组合表格式错误。')
    pending = []
    skipped = []
    reserved = set()
    for item in expanded:
        source_hash = checksum(item.source_bytes)
        if source_hash in existing_hashes:
            skipped.append({'name': item.name, 'reason': 'duplicate',
                            'existing': existing_hashes[source_hash]})
            continue
        style = (item.lut.style or '').upper()
        if not standard_lut_style(style):
            skipped.append({'name': item.name, 'reason': f'非 Standard 输入域：{item.lut.style}'})
            continue
        if len(rows) + len(pending) >= 255:
            skipped.append({'name': item.name, 'reason': '风格列表已满'})
            continue
        label = item.lut.title or Path(item.name).stem
        label = label.strip()
        base_label = label
        suffix = 2
        while label.casefold() in known_labels or label.casefold() in reserved:
            label = f'{base_label} ({suffix})'
            suffix += 1
        if not label or len(label.encode('utf-8')) > 120 or any(c in '|\r\n\0' or ord(c) < 32 for c in label):
            skipped.append({'name': item.name, 'reason': '名称不可用于相机目录'})
            continue
        with tempfile.TemporaryDirectory(prefix='nx-lut-import-') as temporary:
            master = Path(temporary) / 'source.cube'
            master.write_bytes(item.cube_bytes)
            cube = load_cube(master)
        direct = compile_cube(cube, method='fit')
        still = compile_cube(cube, stock, method='fit') if stock is not None else None
        key = 'import-' + uuid.uuid4().hex[:16]
        metadata = {
            'id': key, 'label': label, 'source_kind': 'user-lumix' if item.lut.style else 'user-cube',
            'source_name': item.name, 'source_format': item.lut.source_format,
            'source_sha256': source_hash, 'source_style': item.lut.style,
            'source_grid': item.lut.size, 'input_space': 'srgb', 'output_space': 'srgb',
            'native_table': 'table.d4lut',
            'native_format': {'grid': 9, 'channels': 3, 'bits': 10, 'bytes': 4380},
            'calibration': {'camera': 'NX300M', 'status': 'unmeasured'},
            'compiler': {'method': 'fit', 'tables': {'table.d4lut': checksum(direct)}},
            'created_at': datetime.now().isoformat(timespec='seconds'),
        }
        tables = [('source.cube', item.cube_bytes), ('table.d4lut', direct)]
        if still is not None:
            tables.append(('table-stock3d.d4lut', still))
            metadata['native_stock_3d_table'] = 'table-stock3d.d4lut'
            metadata['compiler']['tables']['table-stock3d.d4lut'] = checksum(still)
        tables.append(('look.json', (json.dumps(metadata, ensure_ascii=False, indent=2) + '\n').encode('utf-8')))
        pending.append((key, label, tables, metadata, source_hash))
        reserved.add(label.casefold())
        existing_hashes[source_hash] = key
    if not pending:
        return {'imported': [], 'skipped': skipped, 'backup': None, 'rows': len(rows)}
    if volume(str(card)) != identity or catalog(card)[0] != original:
        raise ValueError('存储卡或风格目录发生变化，请重新操作。')
    backup = BASE / 'backups' / (datetime.now().strftime('%Y%m%d-%H%M%S') + '-lut-batch-' + uuid.uuid4().hex[:6])
    backup.mkdir(parents=True)
    write_checked(backup / 'looks.txt', original)
    created = []
    try:
        for key, label, tables, metadata, source_hash in pending:
            if volume(str(card)) != identity:
                raise OSError('存储卡信息已变化。')
            lookdir = safe_path(card, 'nx300m-mod/looks/' + key)
            if lookdir.exists():
                raise FileExistsError('导入目标已存在：' + key)
            lookdir.mkdir()
            created.append(lookdir)
            for name, data in tables:
                write_checked(lookdir / name, data)
            backup_look = backup / key
            backup_look.mkdir()
            for name, data in tables:
                write_checked(backup_look / name, data)
        addition = ''.join(key + '|' + label + '|0|0|0|0|0|0|0\n'
                           for key, label, _, _, _ in pending).encode('utf-8')
        if volume(str(card)) != identity or catalog(card)[0] != original:
            raise ValueError('存储卡或风格目录发生变化；新风格尚未加入目录。')
        write_checked(safe_path(card, 'nx300m-mod/looks.txt'),
                      original.rstrip(b'\r\n') + b'\n' + addition)
        updated, updated_rows = catalog(card)
        if updated_rows[-len(pending):] != [
                [key, label, *(['0'] * 7)] for key, label, _, _, _ in pending]:
            raise OSError('LUT 目录回读不一致。')
    except Exception:
        write_checked(safe_path(card, 'nx300m-mod/looks.txt'), original)
        for lookdir in reversed(created):
            shutil.rmtree(lookdir, ignore_errors=True)
        raise
    imported = [{'id': key, 'label': label, 'source_sha256': source_hash,
                 'grid': metadata['source_grid'], 'style': metadata.get('source_style')}
                for key, label, _, metadata, source_hash in pending]
    return {'imported': imported, 'skipped': skipped, 'backup': backup,
            'rows': len(updated_rows)}


def local_lut_root():
    configured = os.environ.get('NX300M_LUT_LIBRARY')
    if configured and Path(configured).is_dir():
        return Path(configured).resolve()
    current = BASE.resolve()
    for parent in (current, *current.parents):
        candidate = parent / 'lut-library'
        if (candidate / 'catalog.json').is_file() and (candidate / 'LUTs').is_dir():
            return candidate
    return None


def local_lut_roots(root):
    root = Path(root)
    return root.parent / 'looks', root / 'LUTs'


def blend_sources(card):
    """Return compatible local and card masters for two-source blending."""
    _, rows = catalog(card)
    result = [{'id': '__original__', 'label': '原图', 'grid': 0, 'valid': True,
               'reason': '', 'original': True, 'origin': 'original', 'path': None}]
    local_root = local_lut_root()
    if local_root is not None:
        looks_root, cubes_root = local_lut_roots(local_root)
        catalog_path = local_root / 'catalog.json'
        catalog_data = json.loads(catalog_path.read_text(encoding='utf-8'))
        catalog_entries = {entry['id']: entry for entry in catalog_data.get('entries', [])}
        for path in sorted(cubes_root.rglob('*.cube'), key=lambda item: item.name.casefold()):
            match = re.search(r'\[([a-z0-9][a-z0-9_-]{0,62})\]\.cube$', path.name, re.I)
            if not match:
                continue
            key = match.group(1).lower()
            metadata_path = looks_root / key / 'look.json'
            try:
                metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            if metadata.get('source_kind') == 'blend-linear-srgb':
                continue
            if metadata.get('input_space') != 'srgb' or metadata.get('output_space') != 'srgb':
                continue
            catalog_entry = catalog_entries.get(key, {})
            label = catalog_entry.get('label', key)
            group = catalog_entry.get('group', '')
            result.append({
                'id': 'local:' + key,
                'label': (str(group) + ' · ' if group else '') + str(label),
                'grid': metadata.get('source_grid') or 0, 'valid': True,
                'reason': '', 'original': False, 'origin': 'local', 'path': str(path.resolve()),
            })
    for row in rows:
        key, label = row[0], row[1]
        metadata = look_metadata(card, key)
        source = safe_path(card, 'nx300m-mod/looks/' + key + '/source.cube')
        reason = ''
        if not source.is_file():
            reason = '没有保留 source.cube 原文件'
        elif metadata.get('source_kind') == 'blend-linear-srgb':
            reason = '首版不允许把已混合风格继续当作输入源'
        elif metadata.get('input_space') != 'srgb' or metadata.get('output_space') != 'srgb':
            reason = '未明确声明 sRGB 输入和输出域'
        result.append({
            'id': 'card:' + key, 'label': label,
            'grid': metadata.get('source_grid') or 0,
            'valid': not reason, 'reason': reason, 'original': False,
            'origin': 'card', 'path': str(source.resolve()) if not reason else None,
        })
    return result


def _blend_source(card, key):
    if key in (None, '', '__original__'):
        return None, {'id': '__original__', 'label': '原图', 'source_grid': 0}
    if str(key).startswith('local:'):
        local_id = str(key)[6:]
        root = local_lut_root()
        if root is None:
            raise ValueError('本地 LUT 仓库已断开，请重新选择仓库。')
        looks_root, cubes_root = local_lut_roots(root)
        metadata_path = looks_root / local_id / 'look.json'
        source = next((path for path in cubes_root.rglob('*.cube')
                       if path.name.casefold().endswith(f'[{local_id}].cube')), None)
        if source is None or not source.is_file():
            raise ValueError('本地 LUT 源已不存在，请刷新列表。')
        metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
        if metadata.get('source_kind') == 'blend-linear-srgb':
            raise ValueError('首版不允许把已混合风格继续当作输入源。')
        if metadata.get('input_space') != 'srgb' or metadata.get('output_space') != 'srgb':
            raise ValueError('本地 LUT 未明确声明 sRGB 输入和输出域，不能参与混合。')
        metadata = dict(metadata, id=str(key), label=metadata.get('label', local_id))
        return source, metadata
    if str(key).startswith('card:'):
        key = str(key)[5:]
    _, rows = catalog(card)
    row = next((item for item in rows if item[0] == key), None)
    if row is None:
        raise ValueError('混合源已不存在，请刷新 LUT 列表。')
    metadata = look_metadata(card, key)
    source = safe_path(card, 'nx300m-mod/looks/' + key + '/source.cube')
    if not source.is_file():
        raise ValueError(f'“{row[1]}”没有保留 source.cube，不能用于混合。')
    if metadata.get('source_kind') == 'blend-linear-srgb':
        raise ValueError('首版不允许把已混合风格继续当作输入源。')
    if metadata.get('input_space') != 'srgb' or metadata.get('output_space') != 'srgb':
        raise ValueError(f'“{row[1]}”未明确声明 sRGB 输入和输出域，不能参与混合。')
    metadata = dict(metadata, id=str(key), label=row[1])
    return source, metadata


def blended_lut(card, source_a, source_b, amount_b):
    from nx300m_lut_core import blend_luts, identity_lut, parse_lut_file
    a_path, a_metadata = _blend_source(card, source_a)
    b_path, b_metadata = _blend_source(card, source_b)
    a = identity_lut(title='原图') if a_path is None else parse_lut_file(a_path).lut
    b = identity_lut(title='原图') if b_path is None else parse_lut_file(b_path).lut
    return blend_luts(a, b, float(amount_b), title='混合预览')


def snapshot_blended_lut(card, key, amount_b):
    from nx300m_lut_core import blend_luts, parse_lut_file
    directory = safe_path(card, 'nx300m-mod/looks/' + key)
    recipe_path = directory / 'blend.json'
    if not recipe_path.is_file():
        raise ValueError('该 LUT 没有可编辑的混合配方。')
    recipe = json.loads(recipe_path.read_text(encoding='utf-8'))
    if recipe.get('algorithm') != 'linear-srgb-output-v1':
        raise ValueError('混合算法版本不受支持。')
    a = parse_lut_file(directory / 'sources/a.cube').lut
    b = parse_lut_file(directory / 'sources/b.cube').lut
    return blend_luts(a, b, float(amount_b), title='混合预览')


def _valid_blend_label(label):
    label = str(label).strip()
    if not label or len(label.encode('utf-8')) > 120 or any(
            c in '|\r\n\0' or ord(c) < 32 for c in label):
        raise ValueError('名称不能为空，也不能包含竖线或换行。')
    return label


def _build_blend_package(source_a, source_b, key, label, amount_b, source_a_id,
                         source_b_id, reference_image=None):
    from look_library import LookLibrary
    with tempfile.TemporaryDirectory(prefix='nx-blend-build-') as temporary:
        root = Path(temporary) / 'looks'
        library = LookLibrary(root=root)
        metadata = library.add_blend(
            source_a, source_b, key, label=label, amount_b=amount_b,
            source_a_id=source_a_id, source_b_id=source_b_id,
            reference_image=Path(reference_image) if reference_image else None,
            description='两款 sRGB LUT 按比例生成的独立风格', method='round')
        metadata['created_at'] = datetime.now().isoformat(timespec='seconds')
        metadata['derived_from_card_sources'] = True
        (root/key/'look.json').write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        files = {}
        for path in (root/key).rglob('*'):
            if path.is_file():
                files[path.relative_to(root/key).as_posix()] = path.read_bytes()
        return files, metadata


def _blend_file_label(label):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(label))
    return ' '.join(value.split()).strip(' .')[:88].rstrip(' .') or 'LUT'


def _write_local_catalog_index(catalog, path):
    lines = ['分组\t状态\t名称\t稳定ID\t源网格\t文件']
    for entry in catalog.get('entries', []):
        lines.append('\t'.join(str(entry.get(name, '')) for name in
                                ('group', 'status', 'label', 'id', 'source_grid', 'file')))
    write_checked(Path(path), ('\n'.join(lines) + '\n').encode('utf-8'))


def _save_blend_to_local_repository(files, metadata, key, label):
    root = local_lut_root()
    if root is None:
        return None
    looks_root, cubes_root = local_lut_roots(root)
    catalog_path = root / 'catalog.json'
    index_path = root / 'catalog.tsv'
    source_catalog_path = looks_root / 'catalog.json'
    if not catalog_path.is_file():
        raise ValueError('本地 LUT 仓库缺少 catalog.json。')
    catalog_raw = catalog_path.read_bytes()
    index_raw = index_path.read_bytes() if index_path.is_file() else None
    source_catalog_raw = source_catalog_path.read_bytes() if source_catalog_path.is_file() else None
    catalog = json.loads(catalog_raw.decode('utf-8'))
    if any(entry.get('id') == key for entry in catalog.get('entries', [])):
        return {'id': key, 'already_present': True}
    group_dir = cubes_root / '自制'
    group_dir.mkdir(parents=True, exist_ok=True)
    number = len([path for path in group_dir.glob('*.cube') if path.is_file()]) + 1
    target_cube = group_dir / f'{number:03d} {_blend_file_label(label)} [{key}].cube'
    lookdir = looks_root / key
    if lookdir.exists() or target_cube.exists():
        raise FileExistsError('本地 LUT 仓库已有同名混合条目：' + key)
    backup = BASE / 'backups' / (
        datetime.now().strftime('%Y%m%d-%H%M%S') + '-blend-local-' + uuid.uuid4().hex[:6])
    backup.mkdir(parents=True)
    write_checked(backup / 'catalog.json', catalog_raw)
    if index_raw is not None:
        write_checked(backup / 'catalog.tsv', index_raw)
    if source_catalog_raw is not None:
        write_checked(backup / 'looks-catalog.json', source_catalog_raw)
    try:
        lookdir.mkdir(parents=True)
        for relative, data in files.items():
            target = lookdir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            write_checked(target, data)
        output_cube = files['source.cube']
        write_checked(target_cube, output_cube)
        output_hash = checksum(output_cube)
        entry = {
            'id': key, 'label': label, 'group': '自制', 'groups': ['自制'],
            'file': target_cube.relative_to(root).as_posix(),
            'source_sha256': output_hash, 'local_sha256': output_hash,
            'source_grid': 33, 'source_kind': 'blend-linear-srgb',
            'source_style': None, 'status': 'source-only', 'camera_ready': False,
        }
        catalog.setdefault('entries', []).append(entry)
        catalog['count'] = len(catalog['entries'])
        groups = catalog.setdefault('groups', [])
        if '自制' not in groups:
            groups.append('自制')
        catalog['generated_at'] = datetime.now().astimezone().isoformat(timespec='seconds')
        write_checked(catalog_path, (json.dumps(catalog, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
        _write_local_catalog_index(catalog, index_path)
        source_catalog = (json.loads(source_catalog_raw.decode('utf-8'))
                          if source_catalog_raw is not None else {'version': 1, 'groups': []})
        group = next((item for item in source_catalog.setdefault('groups', [])
                      if item.get('name') == '自制'), None)
        if group is None:
            group = {'name': '自制', 'ids': []}
            source_catalog['groups'].append(group)
        if key not in group.setdefault('ids', []):
            group['ids'].append(key)
        write_checked(source_catalog_path,
                      (json.dumps(source_catalog, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
    except Exception:
        shutil.rmtree(lookdir, ignore_errors=True)
        target_cube.unlink(missing_ok=True)
        write_checked(catalog_path, catalog_raw)
        if index_raw is None:
            index_path.unlink(missing_ok=True)
        else:
            write_checked(index_path, index_raw)
        if source_catalog_raw is None:
            source_catalog_path.unlink(missing_ok=True)
        else:
            write_checked(source_catalog_path, source_catalog_raw)
        raise
    return {'id': key, 'path': target_cube, 'backup': backup}


def _update_local_blend_repository(files, metadata, key, label):
    root = local_lut_root()
    if root is None:
        return None
    looks_root, cubes_root = local_lut_roots(root)
    catalog_path = root / 'catalog.json'
    index_path = root / 'catalog.tsv'
    source_catalog_path = looks_root / 'catalog.json'
    catalog_raw = catalog_path.read_bytes()
    catalog = json.loads(catalog_raw.decode('utf-8'))
    entry = next((item for item in catalog.get('entries', []) if item.get('id') == key), None)
    if entry is None:
        return {'id': key, 'missing': True}
    old_cube = root / entry['file']
    old_cube_bytes = old_cube.read_bytes() if old_cube.is_file() else None
    prefix = re.match(r'^(\d+)\s+', old_cube.name)
    target_cube = old_cube.parent / f'{(prefix.group(1) if prefix else "999")} {_blend_file_label(label)} [{key}].cube'
    lookdir = looks_root / key
    index_raw = index_path.read_bytes() if index_path.is_file() else None
    source_catalog_raw = source_catalog_path.read_bytes() if source_catalog_path.is_file() else None
    backup = BASE / 'backups' / (
        datetime.now().strftime('%Y%m%d-%H%M%S') + '-blend-local-edit-' + uuid.uuid4().hex[:6])
    backup.mkdir(parents=True)
    write_checked(backup / 'catalog.json', catalog_raw)
    if index_raw is not None:
        write_checked(backup / 'catalog.tsv', index_raw)
    if source_catalog_raw is not None:
        write_checked(backup / 'looks-catalog.json', source_catalog_raw)
    for path in lookdir.rglob('*'):
        if path.is_file():
            target = backup / key / path.relative_to(lookdir)
            target.parent.mkdir(parents=True, exist_ok=True)
            write_checked(target, path.read_bytes())
    try:
        for relative, data in files.items():
            target = lookdir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            write_checked(target, data)
        write_checked(target_cube, files['source.cube'])
        if target_cube != old_cube:
            old_cube.unlink()
        output_hash = checksum(files['source.cube'])
        entry.update(label=label, file=target_cube.relative_to(root).as_posix(),
                     source_sha256=output_hash, local_sha256=output_hash)
        catalog['generated_at'] = datetime.now().astimezone().isoformat(timespec='seconds')
        write_checked(catalog_path, (json.dumps(catalog, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
        _write_local_catalog_index(catalog, index_path)
    except Exception:
        if lookdir.is_dir():
            for path in list(lookdir.rglob('*'))[::-1]:
                if path.is_file():
                    path.unlink(missing_ok=True)
                elif path.is_dir():
                    try:
                        path.rmdir()
                    except OSError:
                        pass
            try:
                lookdir.rmdir()
            except OSError:
                pass
        for path in (backup / key).rglob('*') if (backup / key).is_dir() else ():
            if path.is_file():
                target = lookdir / path.relative_to(backup / key)
                target.parent.mkdir(parents=True, exist_ok=True)
                write_checked(target, path.read_bytes())
        if old_cube_bytes is not None:
            write_checked(old_cube, old_cube_bytes)
        if target_cube != old_cube:
            target_cube.unlink(missing_ok=True)
        write_checked(catalog_path, catalog_raw)
        if index_raw is None:
            index_path.unlink(missing_ok=True)
        else:
            write_checked(index_path, index_raw)
        raise
    return {'id': key, 'path': target_cube, 'backup': backup}


def create_blended_look(card, identity, source_a, source_b, amount_b, label,
                        reference_image=None):
    """Atomically add a camera-ready blend with editable recipe and snapshots."""
    label = _valid_blend_label(label)
    a_path, a_metadata = _blend_source(card, source_a)
    b_path, b_metadata = _blend_source(card, source_b)
    baseline_path = safe_path(card, 'nx300m-mod/looks/nx300-adjust-base/table-stock3d.d4lut')
    if not baseline_path.is_file():
        raise ValueError('卡内缺少原厂 3D 基准表，不能生成可用的 Still 组合表。')
    baseline = baseline_path.read_bytes()
    if len(baseline) != 4380 or checksum(baseline) != '8e90e5a7528928585c86e4fe7e74d689ed96abd6cbb37f94bce234ddca09a07a':
        raise ValueError('原厂组合表不匹配，不能生成混合风格。')
    words = struct.unpack('<2190H', baseline)
    if any(value > 1023 for value in words) or any(words[i] for i in (729, 1459, 2189)):
        raise ValueError('原厂组合表格式错误。')
    original, rows = catalog(card)
    if len(rows) >= 255:
        raise ValueError('风格列表已满，无法继续创建混合风格。')
    key = 'blend-' + uuid.uuid4().hex[:16]
    files, metadata = _build_blend_package(
        a_path, b_path, key, label, float(amount_b),
        a_metadata.get('id', '__original__'), b_metadata.get('id', '__original__'),
        reference_image)
    if volume(str(card)) != identity or catalog(card)[0] != original:
        raise ValueError('存储卡或风格目录发生变化，请重新操作。')
    backup = BASE / 'backups' / (
        datetime.now().strftime('%Y%m%d-%H%M%S') + '-blend-' + uuid.uuid4().hex[:6])
    backup.mkdir(parents=True)
    write_checked(backup / 'looks.txt', original)
    lookdir = safe_path(card, 'nx300m-mod/looks/' + key)
    if lookdir.exists():
        raise FileExistsError('混合风格目标已存在：' + key)
    group_raw, group_state = _read_lut_groups(card, rows)
    camera_groups_path = safe_path(card, LUT_GROUPS_CAMERA)
    camera_groups_raw = camera_groups_path.read_bytes() if camera_groups_path.is_file() else None
    group = next((item for item in group_state['groups'] if item['label'] == '自制'), None)
    groups_changed = False
    try:
        lookdir.mkdir()
        for relative, data in files.items():
            target = lookdir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            write_checked(target, data)
            (backup / key / relative).parent.mkdir(parents=True, exist_ok=True)
            write_checked(backup / key / relative, data)
        line = (key + '|' + label + '|0|0|0|0|0|0|0\n').encode('utf-8')
        if volume(str(card)) != identity or catalog(card)[0] != original:
            raise ValueError('存储卡或风格目录发生变化；混合风格尚未加入目录。')
        write_checked(safe_path(card, 'nx300m-mod/looks.txt'),
                      original.rstrip(b'\r\n') + b'\n' + line)
        if group is None:
            group = {'id': 'group-' + uuid.uuid4().hex[:12], 'label': '自制'}
            group_state['groups'].append(group)
        group_state.setdefault('members', {})[key] = [group['id']]
        _write_lut_groups(card, identity, group_raw, group_state, 'lut-blend-group')
        groups_changed = True
        updated, updated_rows = catalog(card)
        if updated_rows[-1][:2] != [key, label]:
            raise OSError('LUT 目录回读不一致。')
    except Exception:
        write_checked(safe_path(card, 'nx300m-mod/looks.txt'), original)
        if groups_changed or group_raw is not None or camera_groups_raw is not None:
            groups_path = safe_path(card, LUT_GROUPS)
            if group_raw is None:
                groups_path.unlink(missing_ok=True)
            else:
                write_checked(groups_path, group_raw)
            if camera_groups_raw is None:
                camera_groups_path.unlink(missing_ok=True)
            else:
                write_checked(camera_groups_path, camera_groups_raw)
        shutil.rmtree(lookdir, ignore_errors=True)
        raise
    try:
        local_repository = _save_blend_to_local_repository(files, metadata, key, label)
    except Exception as error:
        local_repository = {'error': str(error)}
    return {'id': key, 'label': label, 'backup': backup, 'recipe': metadata['blend_recipe'],
            'group': group, 'local_repository': local_repository}


def update_blended_look(card, identity, key, amount_b, label, reference_image=None):
    """Rebuild a saved blend from its snapshots and replace its runtime files."""
    label = _valid_blend_label(label)
    directory = safe_path(card, 'nx300m-mod/looks/' + key)
    recipe_path = directory / 'blend.json'
    metadata_path = directory / 'look.json'
    if not recipe_path.is_file() or not metadata_path.is_file():
        raise ValueError('该 LUT 没有可编辑的混合配方。')
    recipe = json.loads(recipe_path.read_text(encoding='utf-8'))
    if recipe.get('algorithm') != 'linear-srgb-output-v1':
        raise ValueError('混合算法版本不受支持。')
    a_snapshot = directory / recipe.get('a', {}).get('snapshot', 'sources/a.cube')
    b_snapshot = directory / recipe.get('b', {}).get('snapshot', 'sources/b.cube')
    files, metadata = _build_blend_package(
        a_snapshot, b_snapshot, key, label, float(amount_b),
        recipe.get('a', {}).get('id', 'original'),
        recipe.get('b', {}).get('id', 'original'), reference_image)
    metadata['created_at'] = json.loads(metadata_path.read_text(encoding='utf-8')).get(
        'created_at', datetime.now().isoformat(timespec='seconds'))
    metadata['updated_at'] = datetime.now().isoformat(timespec='seconds')
    files['look.json'] = (json.dumps(metadata, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    original, rows = catalog(card)
    index = next((i for i, row in enumerate(rows) if row[0] == key), None)
    if index is None:
        raise ValueError('混合风格已不在 LUT 目录中。')
    if volume(str(card)) != identity:
        raise ValueError('存储卡信息已变化。')
    backup = BASE / 'backups' / (
        datetime.now().strftime('%Y%m%d-%H%M%S') + '-blend-edit-' + uuid.uuid4().hex[:6])
    backup.mkdir(parents=True)
    write_checked(backup / 'looks.txt', original)
    for path in directory.rglob('*'):
        if path.is_file():
            target = backup / key / path.relative_to(directory)
            target.parent.mkdir(parents=True, exist_ok=True)
            write_checked(target, path.read_bytes())
    old_files = {path.relative_to(directory).as_posix(): path.read_bytes()
                 for path in directory.rglob('*') if path.is_file()}
    try:
        for relative, data in files.items():
            target = directory / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            write_checked(target, data)
        rows[index][1] = label
        write_checked(safe_path(card, 'nx300m-mod/looks.txt'),
                      ('\n'.join('|'.join(row) for row in rows) + '\n').encode('utf-8'))
    except Exception:
        for relative, data in old_files.items():
            write_checked(directory / relative, data)
        write_checked(safe_path(card, 'nx300m-mod/looks.txt'), original)
        raise
    try:
        local_repository = _update_local_blend_repository(files, metadata, key, label)
    except Exception as error:
        local_repository = {'error': str(error)}
    return {'id': key, 'label': label, 'backup': backup, 'recipe': metadata['blend_recipe'],
            'local_repository': local_repository}


def delete_look(card, identity, key):
    original, rows = catalog(card)
    if key == 'nx300-adjust-base':
        raise ValueError('原图微调是基准项，不能删除。')
    match = next((row for row in rows if row[0] == key), None)
    if match is None:
        raise ValueError('风格已不存在，请刷新列表。')
    lookdir = safe_path(card, 'nx300m-mod/looks/' + key)
    if not lookdir.is_dir():
        raise ValueError('风格目录缺失，已停止修改目录。')
    groups_original, groups_state = _read_lut_groups(card, rows)
    camera_group_path = safe_path(card, LUT_GROUPS_CAMERA)
    camera_groups_original = (
        camera_group_path.read_bytes() if camera_group_path.is_file() else None)
    groups_replacement = None
    if key in groups_state['members']:
        del groups_state['members'][key]
        groups_replacement = groups_state
    backup = BASE / 'backups' / (datetime.now().strftime('%Y%m%d-%H%M%S') + '-lut-delete-' + uuid.uuid4().hex[:6])
    backup.mkdir(parents=True)
    shutil.copytree(lookdir, backup / key)
    write_checked(backup / 'looks.txt', original)
    if groups_original is not None:
        write_checked(backup / 'lut-groups.json', groups_original)
        if camera_groups_original is not None:
            write_checked(backup / 'lut-groups.txt', camera_groups_original)
    remaining = [row for row in rows if row[0] != key]
    replacement = ('\n'.join('|'.join(row) for row in remaining) + '\n').encode('utf-8')
    if volume(str(card)) != identity or catalog(card)[0] != original:
        raise ValueError('存储卡或风格目录发生变化，请重新操作。')
    favorites = favorite_keys(card)
    favorite_path = safe_path(card, 'nx300m-mod/.state/lut-favorites')
    favorite_original = favorite_path.read_bytes() if favorite_path.is_file() else None
    favorite_written = False
    catalog_written = False
    groups_written = False
    camera_groups_written = False
    try:
        write_checked(safe_path(card, 'nx300m-mod/looks.txt'), replacement)
        catalog_written = True
        if catalog(card)[1] != remaining:
            raise OSError('LUT 目录回读不一致。')
        if groups_replacement is not None:
            group_path = safe_path(card, LUT_GROUPS)
            group_path.parent.mkdir(parents=True, exist_ok=True)
            write_checked(
                group_path,
                (json.dumps(groups_replacement, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
            groups_written = True
            write_checked(camera_group_path, _lut_group_camera_data(groups_replacement))
            camera_groups_written = True
            if lut_groups(card) != groups_replacement:
                raise OSError('LUT 分组回读不一致。')
            if camera_group_path.read_bytes() != _lut_group_camera_data(groups_replacement):
                raise OSError('相机端 LUT 分组回读不一致。')
        if key in favorites:
            favorites.discard(key)
            favorite_path.parent.mkdir(parents=True, exist_ok=True)
            write_checked(favorite_path,
                          ''.join(row[0] + '\n' for row in remaining
                                  if row[0] in favorites).encode('ascii'))
            favorite_written = True
            if key in favorite_keys(card):
                raise OSError('收藏状态回读不一致。')
    except Exception:
        if catalog_written:
            write_checked(safe_path(card, 'nx300m-mod/looks.txt'), original)
        if groups_written:
            write_checked(safe_path(card, LUT_GROUPS), groups_original or b'')
        if camera_groups_written:
            if camera_groups_original is None:
                camera_group_path.unlink(missing_ok=True)
            else:
                write_checked(camera_group_path, camera_groups_original)
        if favorite_written:
            write_checked(favorite_path, favorite_original or b'')
        raise
    try:
        shutil.rmtree(lookdir)
    except OSError as error:
        raise OSError(
            f'已从相机列表移除，但旧目录未能完整清理；完整备份保存在 {backup}。{error}') from error
    return backup


def main():
    if len(sys.argv) == 3 and sys.argv[1] == '--check-converter':
        from cube_to_d4 import compile_cube
        values = [(r, g, b) for b in range(2) for g in range(2) for r in range(2)]
        result = compile_cube((2, (0.,)*3, (1.,)*3, values), method='fit')
        Path(sys.argv[2]).write_text(str(len(result)), encoding='ascii')
        return 0
    if len(sys.argv) == 3 and sys.argv[1] == '--ui-smoke-test':
        from PySide6.QtCore import QTimer
        from PySide6.QtGui import QFontDatabase
        from nx300m_client_ui import create_app
        app, window = create_app(sys.modules[__name__])
        font = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Fonts/msyh.ttc'
        if font.is_file():
            QFontDatabase.addApplicationFont(str(font))
        window.show()
        def finish():
            if not window.grab().save(sys.argv[2]):
                app.exit(2)
                return
            app.quit()
        QTimer.singleShot(1200, finish)
        return app.exec()
    from nx300m_client_ui import create_app
    app, window = create_app(sys.modules[__name__])
    window.show()
    return app.exec()
if __name__ == '__main__':
    raise SystemExit(main())
