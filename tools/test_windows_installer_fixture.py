"""Exercise update and LUT import on an isolated directory, never on the card."""
from pathlib import Path
import json
import shutil
import tempfile
import zipfile

import nx300m_installer as app

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'outputs/releases/windows-updater-20261106/package'
BEFORE = ROOT / 'outputs/review/2026-11-06-device-feedback/cold-boot-candidate/backups/20261106T022107873262Z/before'


def main():
    manifest = json.loads((PACKAGE / 'manifest.json').read_text(encoding='utf-8'))
    with tempfile.TemporaryDirectory(prefix='nx-installer-fixture-') as temp:
        root = Path(temp); card = root / 'card'; card.mkdir()
        package = root / 'package'; shutil.copytree(PACKAGE / 'payload', package / 'payload')
        shutil.copy2(PACKAGE / 'manifest.json', package / 'manifest.json')
        shutil.copytree(PACKAGE / 'first-install', package / 'first-install')
        app.BASE = package; app.MANIFEST = package / 'manifest.json'
        app.FIRST = package / 'first-install'
        identity = {'serial': '01234567', 'label': 'fixture', 'filesystem': 'FAT32'}
        app.volume = lambda _: identity
        shutil.copytree(app.FIRST / 'payload', card, dirs_exist_ok=True)
        for name in manifest['payload']:
            dest = card / name; dest.parent.mkdir(parents=True, exist_ok=True)
            if (BEFORE / name).is_file():
                shutil.copyfile(BEFORE / name, dest)
        # Reproduce the exact first-install regression: ABI7 selectors absent.
        for marker in ('IPCS_OPEN_DIAGNOSTIC', 'IPCS_SESSION_PREVIEW'):
            (card / 'nx300m-mod' / marker).unlink(missing_ok=True)
        runtime = card / 'nx300m-mod/runtime-files.list'
        runtime.write_bytes(b''.join(line for line in runtime.read_bytes().splitlines(keepends=True)
                                    if line.strip() not in (b'IPCS_OPEN_DIAGNOSTIC', b'IPCS_SESSION_PREVIEW')))
        assert app.checksum(runtime.read_bytes()) == 'f2b66a197fe42afeae32e7c4266f6fcb9cd571acdc95e7a02e6cb2db92806894'
        photo = card / 'photo.jpg'; photo.write_bytes(b'keep photo')
        backup = app.apply(card, identity)
        assert (backup / 'backup.json').is_file()
        for name, rec in manifest['payload'].items(): assert app.checksum((card / name).read_bytes()) == rec['sha256']
        assert app.verify_backend(lambda name: (card / name).read_bytes())['abi'] == 7
        assert photo.read_bytes() == b'keep photo'
        assert json.loads((backup / 'backup.json').read_text())['files']['nx300m-mod/IPCS_SESSION_PREVIEW']['before_sha256'] is None
        target = card / next(iter(manifest['payload']))
        target.write_bytes(b'unknown')
        try: app.prepare(card); raise AssertionError('unknown target accepted')
        except ValueError: pass
        (card / 'nx300m-mod/looks').mkdir(exist_ok=True)
        (card / 'nx300m-mod/looks.txt').write_text('nx300-adjust-base|原图微调|0|0|0|0|0|0|0\n', encoding='utf-8')
        baseline = card / 'nx300m-mod/looks/nx300-adjust-base/table-stock3d.d4lut'
        baseline.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(app.FIRST / 'payload/nx300m-mod/looks/nx300-adjust-base/table-stock3d.d4lut', baseline)
        cube = root / 'identity.cube'
        cube.write_text('LUT_3D_SIZE 2\n' + ''.join(f'{r} {g} {b}\n' for b in range(2) for g in range(2) for r in range(2)))
        key, history = app.import_cube(card, identity, cube, '测试风格')
        assert (history / 'source.cube').read_bytes() == cube.read_bytes()
        assert len((card / 'nx300m-mod/looks' / key / 'table.d4lut').read_bytes()) == 4380
        assert len((card / 'nx300m-mod/looks' / key / 'table-stock3d.d4lut').read_bytes()) == 4380
        assert app.catalog(card)[1][-1][1] == '测试风格'
        logs = card / 'nx300m-mod/logs'; logs.mkdir()
        (logs / 'fn-key.log').write_text('fixture', encoding='utf-8')
        (logs / 'photo.jpg').write_bytes(b'not included')
        dest = root / 'logs.zip'; assert app.export_logs(card, dest, identity) == 1
        with zipfile.ZipFile(dest) as z: assert 'logs/photo.jpg' not in z.namelist()
        print('fixture passed: update, backup, mismatch, LUT fit/import, log selection')


if __name__ == '__main__': main()
