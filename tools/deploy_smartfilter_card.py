"""Write the checked preview overlay with the existing backed-up SD deployer."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import zipfile

import prepare_lut_test_card as card


BASE = card.PROJECT / 'outputs/deployment/smartfilter-preview'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial', required=True)
    args = parser.parse_args()
    serial = args.serial.replace('-', '').upper()
    identity = card.volume(Path('H:/'), serial)
    report = json.loads((BASE / 'package-report.json').read_text(encoding='utf-8'))
    if not report.get('camera_button_preview_menu'):
        raise RuntimeError('Candidate package lacks the camera-button preview menu')
    for name in ('arm-vm', 'ram-vm'):
        checked = json.loads((BASE / name / 'report.json').read_text(encoding='utf-8'))
        if not checked.get('passed') or not checked.get('private_vm_stopped'):
            raise RuntimeError(f'{name} verification has not passed')
    expected = {item['path']: item for item in report['files']}
    package = {}
    with zipfile.ZipFile(BASE / 'nx300m-smartfilter-preview.zip') as source:
        names = source.namelist()
        if len(names) != len(set(names)) or set(names) != set(expected):
            raise RuntimeError('Candidate archive inventory differs from its report')
        for name in names:
            parts = PurePosixPath(name)
            if parts.is_absolute() or '..' in parts.parts or '\\' in name or ':' in name:
                raise RuntimeError('Unexpected candidate path')
            data = source.read(name)
            item = expected[name]
            if len(data) != item['bytes'] or hashlib.sha256(data).hexdigest() != item['sha256']:
                raise RuntimeError('Candidate bytes differ: ' + name)
            if name != 'README.md' and not name.startswith('nx300m-mod/'):
                raise RuntimeError('Candidate is outside the MOD directory')
            if name in ('nx300m-mod/bin/smartfilter_loader.arm', 'nx300m-mod/lib/smartfilter_ram.bin'):
                tested = 'bin/smartfilter_loader.arm' if name.endswith('.arm') else 'smartfilter_ram.bin'
                if data != (BASE / 'ram-vm/source-files' / tested).read_bytes():
                    raise RuntimeError('Candidate differs from the RAM-tested build')
            target = 'nx300m-mod/SMARTFILTER-TEST-START.md' if name == 'README.md' else name
            package[target] = data
    # Existing MFW staging replaces only its owned service on the next entry.
    # This does not inject the preview, alter autoexec or restart the camera app.
    stamp = 'smartfilter-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    package['nx300m-mod/RESTART_SERVICE'] = (stamp + '\n').encode('ascii')
    # Preserve the current service's manifest and stage the new menu with it.
    manifest = Path('H:/nx300m-mod/runtime-files.list').read_text(encoding='ascii').splitlines()
    required = {'gui_preview.NX300M', 'fn-panel/gui_preview.NX300M'}
    for name in set(manifest) | required:
        parts = PurePosixPath(name)
        if not name or parts.is_absolute() or '..' in parts.parts or '\\' in name or ':' in name:
            raise RuntimeError('Unexpected current RAM runtime entry')
        if 'nx300m-mod/' + name not in package and not (Path('H:/nx300m-mod') / name).is_file():
            raise RuntimeError('Missing current RAM runtime asset: ' + name)
    package['nx300m-mod/runtime-files.list'] = ('\n'.join(sorted(set(manifest) | required)) + '\n').encode('ascii')
    card.OUT = BASE
    card.deploy(package, stamp, serial, preserve_autoexec=True)
    output = BASE / 'reader' / ('card-' + serial) / stamp
    deployed = json.loads((output / 'report.json').read_text(encoding='utf-8'))
    deployed.update(candidate_files=len(expected), candidate_build=report['build'],
                    owned_ram_service_restart_requested=True, preview_automatically_enabled=False,
                    autoexec_changed=False, original_lut_library_changed=False)
    deployed['camera_button_preview_menu'] = True
    card.save(output / 'report.json', deployed)
    card.save(BASE / 'latest-card-deployment.json', {
        'report': str(output / 'report.json'), 'backup': str(output / 'before'),
        'status': deployed['status'], 'volume': identity,
        'readback_verified': deployed['readback_verified'],
        'preview_automatically_enabled': False})
    print('Deployment report: ' + str(output / 'report.json'))


if __name__ == '__main__':
    main()
