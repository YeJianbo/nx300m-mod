"""Deploy the verified local camera LUT catalogue to an NX300M card."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid

from nx300m_installer import (_group_state, _lut_group_camera_data, catalog,
                              lut_groups, safe_path, write_checked)


ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / 'lut-library'
STAGE = LOCAL / 'camera'
REPORTS = ROOT / 'outputs/deployment/lut-library-card'
MODULE = 'nx300m-mod'


def _card_paths(card: Path) -> dict[str, Path]:
    module = safe_path(card, MODULE)
    return {
        'module': module,
        'looks': safe_path(card, MODULE + '/looks'),
        'looks_txt': safe_path(card, MODULE + '/looks.txt'),
        'cycle': safe_path(card, MODULE + '/looks-cycle.txt'),
        'state': safe_path(card, MODULE + '/.state'),
        'groups': safe_path(card, MODULE + '/.state/lut-groups.json'),
        'groups_txt': safe_path(card, MODULE + '/.state/lut-groups.txt'),
        'favorites': safe_path(card, MODULE + '/.state/lut-favorites'),
    }


def _backup(card: Path, stamp: str) -> Path:
    paths = _card_paths(card)
    target = REPORTS / stamp / 'backup'
    target.mkdir(parents=True, exist_ok=False)
    if paths['looks'].is_dir():
        shutil.copytree(paths['looks'], target / 'looks')
    for key, name in (('looks_txt', 'looks.txt'), ('cycle', 'looks-cycle.txt'),
                      ('groups', 'lut-groups.json'), ('groups_txt', 'lut-groups.txt'),
                      ('favorites', 'lut-favorites')):
        if paths[key].is_file():
            shutil.copy2(paths[key], target / name)
    return target


def _stable_group_id(label: str) -> str:
    return 'group-' + hashlib.sha256(label.casefold().encode('utf-8')).hexdigest()[:12]


def _seed_groups(card: Path, local_catalog: dict, *, replace_existing: bool = False) -> dict:
    paths = _card_paths(card)
    _, rows = catalog(card)
    raw = (None if replace_existing else
           paths['groups'].read_bytes() if paths['groups'].is_file() else None)
    state = _group_state(raw, rows)
    by_label = {group['label'].casefold(): group['id'] for group in state['groups']}
    group_labels = list(local_catalog['groups'])
    if '基础与黑白' not in group_labels:
        group_labels.append('基础与黑白')
    for label in group_labels:
        if label.casefold() not in by_label:
            group_id = _stable_group_id(label)
            if any(group['id'] == group_id for group in state['groups']):
                raise ValueError(f'LUT 分组 ID 冲突：{label}')
            state['groups'].append({'id': group_id, 'label': label})
            by_label[label.casefold()] = group_id
    if len(state['groups']) > 32:
        raise ValueError('LUT 分组超过相机端上限 32')
    look_groups = {entry['id']: entry['group'] for entry in local_catalog['entries']}
    look_groups['nx300-adjust-base'] = '基础与黑白'
    known = {row[0] for row in rows}
    for key in sorted(known):
        if key in state['members']:
            continue
        label = look_groups.get(key)
        if label:
            state['members'][key] = [by_label[label.casefold()]]
    return state


def _checked_remove(path: Path, parent: Path) -> None:
    resolved = path.resolve(strict=False)
    if resolved.parent != parent.resolve(strict=False):
        raise ValueError(f'拒绝删除范围外路径：{resolved}')
    if resolved.is_dir():
        shutil.rmtree(resolved)
    elif resolved.exists():
        resolved.unlink()


def _write_state(card: Path, state: dict) -> None:
    paths = _card_paths(card)
    paths['state'].mkdir(parents=True, exist_ok=True)
    write_checked(paths['groups'],
                  (json.dumps(state, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
    write_checked(paths['groups_txt'], _lut_group_camera_data(state))
    if lut_groups(card) != state:
        raise OSError('卡内 LUT 分组回读不一致')


def _verify_card(card: Path, source_catalog: bytes, group_count: int) -> dict:
    paths = _card_paths(card)
    raw, rows = catalog(card)
    if raw != source_catalog:
        raise ValueError('卡内 looks.txt 与本地相机目录不一致')
    expected_rows = len([line for line in source_catalog.decode('utf-8').splitlines() if line])
    if len(rows) != expected_rows:
        raise ValueError(f'卡内 LUT 数量异常：{len(rows)}，预期 {expected_rows}')
    if len({row[0] for row in rows}) != len(rows):
        raise ValueError('卡内 LUT ID 重复')
    tables = composed = 0
    for row in rows:
        key = row[0]
        directory = paths['looks'] / key
        source_directory = STAGE / 'looks' / key
        if not directory.is_dir() or not source_directory.is_dir():
            raise ValueError(f'缺少 LUT 目录：{key}')
        source_tables = sorted(source_directory.glob('table*.d4lut'))
        if not source_tables:
            raise ValueError(f'LUT 没有原生表：{key}')
        for source in source_tables:
            target = directory / source.name
            if not target.is_file() or target.read_bytes() != source.read_bytes():
                raise ValueError(f'卡内表与本地不一致：{key}/{source.name}')
            if target.stat().st_size != 4380:
                raise ValueError(f'卡内表尺寸错误：{key}/{source.name}')
            tables += int(source.name == 'table.d4lut')
            composed += int(source.name == 'table-stock3d.d4lut')
        source_metadata = (source_directory / 'look.json').read_bytes()
        if (directory / 'look.json').read_bytes() != source_metadata:
            raise ValueError(f'卡内元数据与本地不一致：{key}')
    state = lut_groups(card)
    if len(state['groups']) != group_count:
        raise ValueError('卡内 LUT 分组数量不一致')
    categorized = len(state['members'])
    return {'rows': len(rows), 'native_tables': tables, 'composed_tables': composed,
            'groups': len(state['groups']), 'categorized': categorized,
            'catalog_sha256': hashlib.sha256(raw).hexdigest()}


def deploy(card: Path, *, reset_groups: bool = False) -> dict:
    verification = json.loads((LOCAL / 'camera-verification.json').read_text(encoding='utf-8'))
    if not verification.get('camera_ready'):
        raise ValueError('本地相机 LUT 目录尚未通过校验')
    local_catalog = json.loads((LOCAL / 'catalog.json').read_text(encoding='utf-8'))
    source_looks_txt = (STAGE / 'looks.txt').read_bytes()
    source_cycle = (STAGE / 'looks-cycle.txt').read_bytes()
    if not STAGE.joinpath('looks').is_dir():
        raise ValueError('缺少本地 camera/looks 目录')
    card = card.resolve()
    paths = _card_paths(card)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup = _backup(card, stamp)
    token = uuid.uuid4().hex[:10]
    stage = safe_path(paths['module'], '.lut-stage-' + token)
    old_looks = safe_path(paths['module'], '.lut-old-' + token)
    old_txt = safe_path(paths['module'], '.looks-old-' + token)
    old_cycle = safe_path(paths['module'], '.cycle-old-' + token)
    swapped = False
    try:
        stage.mkdir(parents=True)
        shutil.copytree(STAGE / 'looks', stage / 'looks')
        shutil.copy2(STAGE / 'looks.txt', stage / 'looks.txt')
        shutil.copy2(STAGE / 'looks-cycle.txt', stage / 'looks-cycle.txt')
        # Keep unlisted diagnostic directories instead of silently deleting them.
        listed = {line.split('|', 1)[0] for line in source_looks_txt.decode('utf-8').splitlines() if line}
        if paths['looks'].is_dir():
            for old in paths['looks'].iterdir():
                if old.is_dir() and old.name not in listed and not (stage / 'looks' / old.name).exists():
                    shutil.copytree(old, stage / 'looks' / old.name)
        swapped = True
        if paths['looks'].exists():
            os.replace(paths['looks'], old_looks)
        os.replace(stage / 'looks', paths['looks'])
        if paths['looks_txt'].exists():
            os.replace(paths['looks_txt'], old_txt)
        os.replace(stage / 'looks.txt', paths['looks_txt'])
        if paths['cycle'].exists():
            os.replace(paths['cycle'], old_cycle)
        os.replace(stage / 'looks-cycle.txt', paths['cycle'])
        state = _seed_groups(card, local_catalog, replace_existing=reset_groups)
        _write_state(card, state)
        verified = _verify_card(card, source_looks_txt, len(state['groups']))
    except Exception:
        if swapped:
            _checked_remove(paths['looks'], paths['module'])
            if old_looks.exists():
                os.replace(old_looks, paths['looks'])
            _checked_remove(paths['looks_txt'], paths['module'])
            if old_txt.exists():
                os.replace(old_txt, paths['looks_txt'])
            _checked_remove(paths['cycle'], paths['module'])
            if old_cycle.exists():
                os.replace(old_cycle, paths['cycle'])
        for key, name in (('groups', 'lut-groups.json'), ('groups_txt', 'lut-groups.txt')):
            previous = backup / name
            if previous.is_file():
                paths['state'].mkdir(parents=True, exist_ok=True)
                shutil.copy2(previous, paths[key])
            else:
                paths[key].unlink(missing_ok=True)
        raise
    finally:
        if stage.exists():
            _checked_remove(stage, paths['module'])
    for old in (old_looks, old_txt, old_cycle):
        if old.exists():
            _checked_remove(old, paths['module'])
    report = {'schema': 1, 'card': str(card), 'backup': str(backup),
              'deployed_at': stamp, 'verified': verified,
              'groups_replaced': reset_groups,
              'preserved_unlisted_directories': True}
    report_path = REPORTS / stamp / 'deployment.json'
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--card', type=Path, default=Path('H:/'))
    parser.add_argument('--reset-groups', action='store_true',
                        help='replace card categories with the local broad-category map')
    args = parser.parse_args()
    print(json.dumps(deploy(args.card, reset_groups=args.reset_groups), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
