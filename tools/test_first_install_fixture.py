"""Exercise first install on a temporary directory, never on a physical card."""
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

import nx300m_installer as installer
from installer_backend import verify_backend

ROOT = Path(__file__).resolve().parents[1]
FIRST = ROOT / 'outputs/releases/windows-updater-20261106/first-install'
IDENTITY = {'serial': 'TEST', 'label': 'fixture', 'filesystem': 'FAT32'}

checked = ROOT / 'outputs/deployment/smartfilter-session/preview-navigation-vm'
report = json.loads((checked / 'report.json').read_text(encoding='utf-8'))
assert report['passed'] and report['abi'] == 7
assert report['default_preview_enables_session_with_diagnostics_off']
payload = FIRST / 'payload/nx300m-mod'
assert (payload / 'smartfilter-preview.sh').read_bytes() == (checked / 'source-files/smartfilter-live-preview.sh').read_bytes()
assert (payload / 'ipcs-diagnostic/lib/smartfilter_ram.bin').read_bytes() == (checked / 'source-files/smartfilter_ram.bin').read_bytes()
broken = {name: (FIRST / 'payload' / name).read_bytes()
          for name in json.loads((FIRST / 'manifest.json').read_text(encoding='utf-8'))['files']}
broken['nx300m-mod/runtime-files.list'] = b''.join(
    line for line in broken['nx300m-mod/runtime-files.list'].splitlines(keepends=True)
    if line.strip() not in (b'IPCS_OPEN_DIAGNOSTIC', b'IPCS_SESSION_PREVIEW'))
try:
    verify_backend(broken.__getitem__)
    raise AssertionError('ABI4 regression was accepted')
except ValueError:
    pass

with tempfile.TemporaryDirectory() as tmp:
    card = Path(tmp)
    photo = card / 'photo.jpg'
    photo.write_bytes(b'photo')
    with patch.object(installer, 'FIRST', FIRST), patch.object(installer, 'volume', return_value=IDENTITY):
        installer.install_first(card, IDENTITY)
        manifest = json.loads((FIRST / 'manifest.json').read_text(encoding='utf-8'))
        for name, rec in manifest['files'].items():
            assert installer.checksum((card / name).read_bytes()) == rec['sha256']
        assert photo.read_bytes() == b'photo'
        try:
            installer.install_first(card, IDENTITY)
            raise AssertionError('existing install was not rejected')
        except ValueError:
            pass
        (card / 'autoexec.sh').write_bytes(b'other boot script')
        try:
            installer.uninstall_mod(card, IDENTITY)
            raise AssertionError('unknown boot script was accepted')
        except ValueError:
            pass
        (card / 'autoexec.sh').write_bytes((FIRST / 'payload/autoexec.sh').read_bytes())
        backup = installer.uninstall_mod(card, IDENTITY)
        assert not (card / 'autoexec.sh').exists() and not (card / 'nx300m-mod').exists()
        assert (backup / 'autoexec.sh').is_file() and (backup / 'nx300m-mod/looks.txt').is_file()
        assert photo.read_bytes() == b'photo'
print('first-install fixture: install, conflict refusal, reversible uninstall and photo preservation passed')
