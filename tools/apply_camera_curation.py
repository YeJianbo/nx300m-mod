"""Apply the reviewed camera membership cleanup and rebuild the card stage."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil

from import_cxo_filmpacks import package_camera
from manage_local_lut_library import sync


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / 'looks/catalog.json'
MACHINE = ROOT / 'lut-library/machine-catalog.json'
BACKUPS = ROOT / 'lut-library/.backups'
REPORT = ROOT / 'outputs/looks/cxo-import/camera-curation.json'

APP_ONLY = {'lr-custom-10e69cdb47b694fd2929e8ce7cacde7d'}  # 日光
PENTAX = (
    'nx300-pentax-natural',
    'lr-custom-147bc4125acce76fac4c63e3d644e1bf',
    'lr-custom-4a06e6310411a69955ad13c68d018b26',
    'lr-custom-2bb5bf55a1a7c40be4731106dafee5a1',
    'lr-custom-63baf27666493ba3bd6e45b05721cda1',
)


def remove_ids(groups: list[dict], ids: set[str]) -> None:
    for group in groups:
        group['ids'] = [key for key in group.get('ids', []) if key not in ids]


def upsert_group(groups: list[dict], name: str, ids: list[str], *, camera: bool = True) -> dict:
    group = next((item for item in groups if item.get('name') == name), None)
    if group is None:
        group = {'name': name, 'ids': []}
        groups.append(group)
    group['ids'] = list(dict.fromkeys(ids))
    if camera:
        group.pop('camera', None)
    else:
        group['camera'] = False
    return group


def main() -> None:
    catalog = json.loads(CATALOG.read_text(encoding='utf-8'))
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup = BACKUPS / f'camera-curation-{stamp}'
    backup.mkdir(parents=True, exist_ok=False)
    shutil.copy2(CATALOG, backup / 'catalog.json')
    if MACHINE.is_file():
        shutil.copy2(MACHINE, backup / 'machine-catalog.json')

    groups = catalog['groups']
    all_targets = APP_ONLY | set(PENTAX)
    remove_ids(groups, all_targets)
    upsert_group(groups, 'App 专属', sorted(APP_ONLY), camera=False)
    pentax_group = upsert_group(groups, '宾得', list(PENTAX))

    # Keep the imported brand set near the other look families rather than at
    # the end of the menu.
    groups.remove(pentax_group)
    insert_at = next(
        (index for index, group in enumerate(groups) if 'panasonic-lumix-skin-tone-boost' in group.get('ids', [])),
        len(groups),
    )
    groups.insert(insert_at, pentax_group)

    temporary = CATALOG.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
    temporary.replace(CATALOG)

    build = sync(compile_camera=False)
    package = package_camera()
    camera_ids = json.loads(MACHINE.read_text(encoding='utf-8'))['ids']
    report = {
        'schema': 1,
        'generated_at': stamp,
        'backup': str(backup.relative_to(ROOT)),
        'removed_from_camera': sorted(APP_ONLY),
        'app_only': sorted(APP_ONLY),
        'restored_to_camera': list(PENTAX),
        'camera_count': len(camera_ids),
        'camera_rows_with_identity': len(camera_ids) + 1,
        'camera_verification': build['camera'],
        'camera_package': package,
    }
    if report['camera_verification']['errors']:
        raise ValueError('Camera verification failed: ' + ', '.join(report['camera_verification']['errors']))
    if not set(PENTAX).issubset(camera_ids):
        raise ValueError('Pentax set was not restored into the camera catalogue')
    if APP_ONLY & set(camera_ids):
        raise ValueError('App-only look leaked into the camera catalogue')
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
