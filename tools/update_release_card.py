"""Reader-side menu update; no camera shell, firmware flash or library changes."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
import uuid

BASE = Path(__file__).resolve().parent
MENU = 'nx300m-mod/bin/nxui.arm'
RESTART = 'nx300m-mod/RESTART_SERVICE'
TARGETS = {MENU, RESTART}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def card_file(card: Path, name: str) -> Path:
    parts = PurePosixPath(name)
    if parts.is_absolute() or '..' in parts.parts or '\\' in name or ':' in name:
        raise ValueError('Invalid package path: ' + name)
    target = card.joinpath(*parts.parts)
    if not target.resolve().is_relative_to(card):
        raise ValueError('Path leaves card: ' + name)
    for parent in (target, *target.parents):
        if parent == card:
            break
        if parent.is_symlink():
            raise ValueError('Linked card path: ' + name)
    return target


def replace_verified(target: Path, data: bytes) -> None:
    fd, temporary = tempfile.mkstemp(prefix=target.name+'.', suffix='.next', dir=target.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if Path(temporary).read_bytes() != data:
            raise OSError('Temporary readback differs: ' + str(target))
        os.replace(temporary, target)
        if target.read_bytes() != data:
            raise OSError('Final readback differs: ' + str(target))
    finally:
        Path(temporary).unlink(missing_ok=True)


def load_update(card: Path) -> dict[str, bytes]:
    manifest = json.loads((BASE/'manifest.json').read_text(encoding='utf-8'))
    if manifest['schema'] != 1 or manifest['kind'] != 'nx300m-menu-upgrade':
        raise ValueError('Unsupported package')
    for name, expected in manifest['requires'].items():
        asset = card_file(card, name)
        if not asset.is_file() or digest(asset.read_bytes()) != expected:
            raise ValueError('版本不匹配或文件缺失：'+name+'；没有写卡。')
    if not card_file(card, MENU).is_file():
        raise ValueError('Existing menu is missing')
    binary = (BASE/'payload/nxui.arm').read_bytes()
    if digest(binary) != manifest['menu_sha256']:
        raise ValueError('Package menu checksum differs')
    return {MENU: binary, RESTART: ('release-'+manifest['version']+'-'+uuid.uuid4().hex+'\n').encode('ascii')}


def load_restore(card: Path, backup: Path) -> tuple[dict[str, bytes], set[str]]:
    info = json.loads((backup/'backup.json').read_text(encoding='utf-8'))
    if info.get('schema') != 1 or set(info['files']) != TARGETS:
        raise ValueError('Unsupported backup')
    writes: dict[str, bytes] = {}
    removes: set[str] = set()
    for name, record in info['files'].items():
        target = card_file(card, name)
        actual = digest(target.read_bytes()) if target.is_file() else None
        if actual != record['installed_sha256']:
            raise ValueError('更新后文件已改变，不覆盖新的修改：'+name)
        if record['existed']:
            source = backup/'before'/name
            if source.is_symlink() or not source.resolve().is_relative_to(backup.resolve()):
                raise ValueError('Invalid backup source')
            data = source.read_bytes()
            if digest(data) != record['before_sha256']:
                raise ValueError('Backup checksum differs: '+name)
            writes[name] = data
        else:
            removes.add(name)
    # A fresh marker makes the resident RAM launcher reload the restored menu
    # even when an older restart marker had already been consumed before backup.
    writes[RESTART] = ('rollback-'+uuid.uuid4().hex+'\n').encode('ascii')
    removes.discard(RESTART)
    return writes, removes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--card', type=Path, required=True, help='关机后接入读卡器的卡根目录，如 H:/')
    parser.add_argument('--apply', action='store_true', help='实际写入；默认只检查')
    parser.add_argument('--restore', type=Path, help='本工具生成的备份目录')
    args = parser.parse_args()
    try:
        if args.card.is_symlink():
            raise ValueError('Card root must not be a link')
        card = args.card.resolve(strict=True)
        autoexec = card_file(card, 'autoexec.sh')
        startup = autoexec.read_bytes()
        writes, removes = load_restore(card, args.restore.resolve(strict=True)) if args.restore else (load_update(card), set())
        if not args.apply:
            print('检查通过，未写卡。确认相机已关机，再加 --apply 执行。')
            return 0
        backup_root = BASE/'backups'
        if backup_root.resolve().is_relative_to(card):
            raise ValueError('请将升级包解压到电脑，备份不能放在目标卡上。')
        backup = backup_root/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8])
        backup.mkdir(parents=True)
        info = {'schema': 1, 'files': {}}
        for name in sorted(writes.keys() | removes):
            target = card_file(card, name)
            old = target.read_bytes() if target.is_file() else None
            if old is not None:
                saved = backup/'before'/name
                saved.parent.mkdir(parents=True, exist_ok=True)
                replace_verified(saved, old)
            info['files'][name] = {'existed': old is not None,
                                   'before_sha256': digest(old) if old is not None else None,
                                   'installed_sha256': digest(writes[name]) if name in writes else None}
        replace_verified(backup/'backup.json', (json.dumps(info, indent=2)+'\n').encode())
        print('备份：'+str(backup), flush=True)
        # Publish the restart request last. No boot entry or user state is touched.
        for name in sorted(writes, key=lambda name: name == RESTART):
            replace_verified(card_file(card, name), writes[name])
        for name in removes:
            card_file(card, name).unlink()
        if autoexec.read_bytes() != startup:
            raise OSError('Startup file changed during update')
        print('写入及回读通过。安全弹出卡后装回相机。')
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print('失败：'+str(exc))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
