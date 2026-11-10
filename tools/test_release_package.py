"""Check final release archives and the real installer on isolated directories."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from unittest.mock import patch
import zipfile

import nx300m_installer as app
from release_payload import is_core, verify_complete

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT/'outputs/releases/windows-updater-20261106'
IDENTITY = {'serial': 'FIXTURE', 'label': 'isolated', 'filesystem': 'exFAT'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', default='v0.1.0-preview.6')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    checks = []
    def passed(name):
        checks.append(name)
    windows = BASE/('NX300M-MOD-Windows-'+args.version+'.zip')
    sd = BASE/('NX300M-MOD-SD-'+args.version+'.zip')
    old = BASE/'NX300M-MOD-Windows-v0.1.0-preview.3.zip'
    with tempfile.TemporaryDirectory(prefix='nx-release-') as temp:
        temp = Path(temp)
        program = temp/'program'
        with zipfile.ZipFile(windows) as archive, zipfile.ZipFile(sd) as standalone:
            assert archive.testzip() is None and standalone.testzip() is None
            for name in archive.namelist():
                target = app.safe_path(program, name)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(name))
            first = json.loads(archive.read('first-install/manifest.json'))
            upgrade = json.loads(archive.read('manifest.json'))
            verify_complete(lambda name: archive.read('first-install/payload/'+name))
            verify_complete(standalone.read)
            for name, item in first['files'].items():
                data = archive.read('first-install/payload/'+name)
                assert len(data) == item['bytes'] and app.checksum(data) == item['sha256']
                assert data == standalone.read(name)
                if is_core(name):
                    assert name in upgrade['payload']
                    assert archive.read(upgrade['payload'][name]['asset']) == data
            assert not any('/.state/' in n or '/logs/' in n or 'table-stock3d' in n for n in archive.namelist())
            passed('final_zip_crc_manifests_complete_runtime_and_sd_windows_upgrade_parity')
        output = temp/'converter.txt'
        result = subprocess.run([str(program/'NX300M-MOD.exe'), '--check-converter', str(output)], timeout=45)
        actual = output.read_text(encoding='ascii') if output.is_file() else None
        assert result.returncode == 0 and actual == '4380', {'exit_code': result.returncode, 'output': actual}
        passed('packaged_exe_imports_and_actual_lut_converter')
        with patch.object(app, 'BASE', program), patch.object(app, 'FIRST', program/'first-install'), \
             patch.object(app, 'MANIFEST', program/'manifest.json'), patch.object(app, 'volume', return_value=IDENTITY):
            fresh = temp/'fresh';fresh.mkdir()
            (fresh/'photo.jpg').write_bytes(b'preserve photo')
            app.install_first(fresh, IDENTITY)
            for name, item in first['files'].items():
                assert app.checksum((fresh/name).read_bytes()) == item['sha256']
            assert (fresh/'photo.jpg').read_bytes() == b'preserve photo'
            passed('actual_first_install_all_files_and_photo_preserved')
            card = temp/'old';card.mkdir()
            with zipfile.ZipFile(old) as previous:
                for name in previous.namelist():
                    prefix = 'first-install/payload/'
                    if name.startswith(prefix):
                        target = app.safe_path(card, name[len(prefix):])
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(previous.read(name))
            assert not (card/'nx300m-lens-debug').exists()
            state = card/'nx300m-mod/.state';state.mkdir()
            (state/'lut-preview').write_bytes(b'1\n')
            (state/'lut-choice').write_bytes(b'0\n')
            (card/'photo.srw').write_bytes(b'preserve raw')
            (card/'nx300m-mod/lenses.txt').write_bytes(b'personal lens list\n')
            preserved = {n:(card/n).read_bytes() for n in
                         ('nx300m-mod/looks.txt', 'nx300m-mod/looks-cycle.txt', 'nx300m-mod/lenses.txt',
                          'nx300m-mod/.state/lut-preview', 'nx300m-mod/.state/lut-choice', 'photo.srw')}
            # Reproduce the earlier ABI7-selector omission as well.
            for marker in ('IPCS_OPEN_DIAGNOSTIC', 'IPCS_SESSION_PREVIEW'):
                (card/'nx300m-mod'/marker).unlink()
            runtime = card/'nx300m-mod/runtime-files.list'
            runtime.write_bytes(b''.join(line for line in runtime.read_bytes().splitlines(keepends=True)
                                        if line.strip() not in (b'IPCS_OPEN_DIAGNOSTIC', b'IPCS_SESSION_PREVIEW')))
            backup = app.apply(card, IDENTITY)
            for name, item in upgrade['payload'].items():
                assert app.checksum((card/name).read_bytes()) == item['sha256']
            for name, data in preserved.items():
                assert (card/name).read_bytes() == data
            assert (backup/'backup.json').is_file()
            verify_complete(lambda name:(card/name).read_bytes())
            passed('released_old_package_upgrades_all_programs_repairs_wifi_and_session_without_losing_user_data')
            (card/'nx300m-lens-debug/WIFI_MEM_PROFILE').write_bytes(b'stock\n')
            (card/'nx300m-lens-debug/WIFI_MEM_RUN_ID').write_bytes(b'personal-run\n')
            app.apply(card, IDENTITY)
            assert (card/'nx300m-lens-debug/WIFI_MEM_PROFILE').read_bytes() == b'stock\n'
            assert (card/'nx300m-lens-debug/WIFI_MEM_RUN_ID').read_bytes() == b'personal-run\n'
            passed('repeat_upgrade_preserves_explicit_wireless_profile_and_run_id')
            target = card/'nx300m-mod/bin/nxui.arm';saved = target.read_bytes();target.write_bytes(b'unknown version')
            try:
                app.prepare(card)
                raise AssertionError('unknown executable was accepted')
            except ValueError:
                pass
            assert target.read_bytes() == b'unknown version'
            target.write_bytes(saved)
            passed('unknown_program_refused_before_any_upgrade_write')
            logs = card/'nx300m-lens-debug/logs/personal-run';logs.mkdir(parents=True)
            (logs/'after.json').write_text('{"after_verified":true}', encoding='utf-8')
            exported = temp/'logs.zip'
            assert app.export_logs(card, exported, IDENTITY) == 1
            with zipfile.ZipFile(exported) as archive:
                assert 'lens-wifi/personal-run/after.json' in archive.namelist()
                assert not any(n.endswith('.srw') for n in archive.namelist())
            passed('wireless_readback_diagnostics_exported_without_photos')
            backup = app.uninstall_mod(card, IDENTITY)
            assert not (card/'autoexec.sh').exists() and not (card/'nx300m-mod').exists()
            assert not (card/'nx300m-lens-debug').exists()
            assert (backup/'nx300m-lens-debug/WIFI_MEM_PROFILE').read_bytes() == b'stock\n'
            assert (card/'photo.srw').read_bytes() == b'preserve raw'
            passed('uninstall_moves_main_and_wireless_files_to_backup_even_with_user_config')
    report = {'passed': True, 'scope': 'Final ZIPs, packaged EXE and real installer; isolated directories, no camera',
              'physical_camera_verified': False, 'checks': checks,
              'archives': {p.name:{'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
                           for p in (windows, sd)}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
