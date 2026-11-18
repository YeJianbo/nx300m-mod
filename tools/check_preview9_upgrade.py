"""Exercise the shipped PowerShell updater on an isolated card for every accepted baseline."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
BASELINES = (
    ('preview.8', ROOT / 'outputs/releases/v0.1.0-preview.8/NX300M-MOD-SD-v0.1.0-preview.8.zip'),
    ('preview.9', ROOT / 'outputs/releases/v0.1.0-preview.9/NX300M-MOD-SD-v0.1.0-preview.9.zip'),
    ('preview.10', ROOT / 'outputs/releases/v0.1.0-preview.10/NX300M-MOD-SD-v0.1.0-preview.10.zip'),
)
IDENTITY = {'serial': 'fixture', 'label': 'isolated', 'filesystem': 'exFAT'}
# Windows PowerShell 5.1 cannot auto-load cmdlets (Get-FileHash) while the
# session carries PowerShell 7 module paths, so pin the 5.1 module path.
POWERSHELL_ENV = {**os.environ,
                  'PSModulePath': r'C:\Windows\System32\WindowsPowerShell\v1.0\Modules'}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', default='v0.1.0-preview.10')
    args = parser.parse_args()
    version = args.version
    release = ROOT / 'outputs/releases' / version
    with tempfile.TemporaryDirectory(prefix='nx-powershell-upgrade-') as temp_name:
        temp = Path(temp_name)
        tool = temp / 'tool'
        with ZipFile(release / f'NX300M-MOD-Upgrade-{version}.zip') as archive:
            archive.extractall(tool)
        manifest = json.loads((tool / 'manifest.json').read_text(encoding='utf-8'))
        runs = []
        fixture_card = None
        baselines = [(name, archive) for name, archive in BASELINES
                     if f'v0.1.0-{name}' in manifest['accepted_baselines']]
        for baseline_name, previous_zip in baselines:
            card = temp / ('card-' + baseline_name)
            card.mkdir()
            with ZipFile(previous_zip) as archive:
                for name in archive.namelist():
                    if name == 'autoexec.sh' or name.startswith(('nx300m-mod/', 'nx300m-lens-debug/')):
                        target = card / name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(archive.read(name))
            personal = {
                'nx300m-mod/looks.txt': (card / 'nx300m-mod/looks.txt').read_bytes(),
                'nx300m-mod/looks-cycle.txt': (card / 'nx300m-mod/looks-cycle.txt').read_bytes(),
                'nx300m-mod/lenses.txt': b'personal lens\n',
                'nx300m-mod/looks/personal/table.d4lut': b'personal lut',
                'nx300m-mod/.state/lut-preview': b'1\n',
                'nx300m-mod/.state/lut-choice': b'0\n',
                'DCIM/100PHOTO/SAM_0001.JPG': b'preserved photo',
            }
            for name, data in personal.items():
                target = card / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            extra = card / 'nx300m-mod/bin/optional-module.arm'
            extra.write_bytes(b'optional runtime module')
            runtime = card / 'nx300m-mod/runtime-files.list'
            runtime.write_bytes(runtime.read_bytes() + b'bin/optional-module.arm\n')
            card_command = [
                'C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe',
                '-NoProfile', '-ExecutionPolicy', 'Bypass',
                '-File', str(tool / 'update-card.ps1'), '-CardPath', str(card),
            ]
            for iteration in (1, 2):
                completed = subprocess.run(card_command, capture_output=True, timeout=120, env=POWERSHELL_ENV)
                assert completed.returncode == 0, (completed.returncode, completed.stderr)
                for name, record in manifest['files'].items():
                    if name == 'nx300m-mod/RESTART_SERVICE':
                        assert (card / name).read_bytes().startswith(b'release-' + version.encode() + b'-')
                    elif name == 'nx300m-mod/runtime-files.list':
                        pass
                    else:
                        assert digest((card / name).read_bytes()) == record['sha256'], name
                for name, data in personal.items():
                    assert (card / name).read_bytes() == data, name
                inventory = runtime.read_text(encoding='ascii').splitlines()
                assert 'bin/optional-module.arm' in inventory
                assert len(inventory) == len(set(inventory))
                assert all((card / 'nx300m-mod' / name).is_file() for name in inventory)
                runs.append({
                    'baseline': baseline_name,
                    'iteration': iteration,
                    'exit_code': completed.returncode,
                    'runtime_files': len(inventory),
                    'payload_files_checked': len(manifest['files']),
                    'preserved_files_checked': len(personal),
                })
            backups = list((tool / 'backups').iterdir())
            assert len(backups) == 2 * (len(runs) // 2)
            assert digest((backups[-1] / 'nx300m-mod/bin/nxui.arm').read_bytes()) in manifest['accepted_menu_sha256']
            if fixture_card is None:
                fixture_card = (card, card_command, runtime, extra)
        backups_total = 2 * len(baselines)

        card, card_command, runtime, extra = fixture_card
        assert digest((card / 'nx300m-mod/bin/nxui.arm').read_bytes()) == manifest['files']['nx300m-mod/bin/nxui.arm']['sha256']
        before = {path.relative_to(card).as_posix(): digest(path.read_bytes())
                  for path in card.rglob('*') if path.is_file()}
        damaged = tool / 'payload' / next(iter(manifest['files']))
        damaged.write_bytes(b'damaged upgrade')
        completed = subprocess.run(card_command, capture_output=True, timeout=120, env=POWERSHELL_ENV)
        assert completed.returncode != 0
        after = {path.relative_to(card).as_posix(): digest(path.read_bytes())
                 for path in card.rglob('*') if path.is_file()}
        assert before == after

        report = {
            'passed': True,
            'environment': 'Windows PowerShell; isolated directory fixtures; no physical card write',
            'archive_sha256': digest((release / f'NX300M-MOD-Upgrade-{version}.zip').read_bytes()),
            'accepted_baselines': manifest.get('accepted_baselines', []),
            'runs': runs,
            'backups_created': backups_total,
            'bad_payload_rejected_before_writes': True,
        }
        (release / 'upgrade-fixture-result.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
