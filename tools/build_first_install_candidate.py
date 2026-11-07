"""Prepare a local, limited first-install candidate from the checked test card.

This does not copy firmware, photographs, preferences, personal looks, or logs.
The stock-composed baseline comes from the card and is for local testing only.
"""
from pathlib import Path
import hashlib
import json
import shutil

from nx300m_installer import safe_path
from installer_backend import verify_backend
from manual_lens_wifi_payload import build_wifi_payload, verify_wifi_payload
from release_payload import deployed_overlay, verify_complete

ROOT = Path(__file__).resolve().parents[1]
CARD = Path('H:/')
OUT = ROOT / 'outputs/releases/windows-updater-20261106/first-install'
EVIDENCE = ROOT / 'outputs/review/2026-11-06-device-feedback/cold-boot-candidate/manifest.json'


def main():
    record = json.loads(EVIDENCE.read_text(encoding='utf-8'))
    overlay = deployed_overlay(ROOT)
    prior_path = OUT / 'manifest.json'
    prior = json.loads(prior_path.read_text(encoding='utf-8')) if prior_path.is_file() else {}
    source_root = OUT / 'payload' if prior else CARD
    if prior:
        for name, item in prior['files'].items():
            data = safe_path(source_root, name).read_bytes()
            if len(data) != item['bytes'] or hashlib.sha256(data).hexdigest() != item['sha256']:
                raise ValueError('Local source snapshot differs: ' + name)
    for name, expected in record['requires'].items():
        if '/.state/' in name: continue
        actual = hashlib.sha256(safe_path(source_root, name).read_bytes()).hexdigest()
        accepted = {expected}
        if name in overlay:
            accepted.add(hashlib.sha256(overlay[name]).hexdigest())
        if name == 'nx300m-mod/runtime-files.list':
            accepted.add(prior.get('files', {}).get(name, {}).get('sha256'))
        if actual not in accepted:
            raise ValueError('Card differs from tested backend: ' + name)
    for name, item in record['payload'].items():
        if name.endswith('RESTART_SERVICE'): continue
        accepted = {item['sha256']}
        if name in overlay:
            accepted.add(hashlib.sha256(overlay[name]).hexdigest())
        if hashlib.sha256(safe_path(source_root, name).read_bytes()).hexdigest() not in accepted:
            raise ValueError('Card differs from candidate: ' + name)
    runtime = safe_path(source_root, 'nx300m-mod/runtime-files.list').read_text(encoding='ascii').splitlines()
    if len(runtime) != len(set(runtime)): raise ValueError('Duplicate runtime paths')
    # These two markers select ABI7 Session preview; they do not enable
    # continuous timing collection during normal preview.
    markers = {'IPCS_OPEN_DIAGNOSTIC', 'IPCS_SESSION_PREVIEW'}
    runtime = sorted(set(runtime) | markers)
    files = {'autoexec.sh': safe_path(source_root, 'autoexec.sh').read_bytes(),
             'nx300m-mod/mfw-service-entry.sh': safe_path(source_root, 'nx300m-mod/mfw-service-entry.sh').read_bytes()}
    for name in runtime:
        if name in markers:
            data = b'1\n'
        elif name == 'runtime-files.list':
            data = ('\n'.join(runtime) + '\n').encode('ascii')
        elif name == 'lenses.txt':
            data = b''
        elif name == 'looks-cycle.txt':
            data = 'nx300-hard-mono|硬调黑白|0|0|0|0|0|0|0\n'.encode('utf-8')
        elif name in ('LUT_MOD.md', 'REFERENCE_IMAGES.txt'):
            data = b'NX300M MOD local installation candidate.\n'
        else:
            data = safe_path(source_root, 'nx300m-mod/' + name).read_bytes()
        files['nx300m-mod/' + name] = data
    rows = 'nx300-adjust-base|原图微调|0|0|0|0|0|0|0\nnx300-hard-mono|硬调黑白|0|0|0|0|0|0|0\n'
    files['nx300m-mod/looks.txt'] = rows.encode('utf-8')
    for key in ('nx300-adjust-base', 'nx300-hard-mono'):
        for name in ('table.d4lut', 'table-stock3d.d4lut', 'look.json'):
            relative = 'nx300m-mod/looks/' + key + '/' + name
            source = safe_path(source_root, relative)
            if source.is_file(): files[relative] = source.read_bytes()
    if len(files['nx300m-mod/looks/nx300-adjust-base/table-stock3d.d4lut']) != 4380:
        raise ValueError('Factory baseline missing')
    files.update(overlay)
    files.update(build_wifi_payload(ROOT))
    wifi = verify_wifi_payload(files.__getitem__)
    backend = verify_backend(files.__getitem__)
    complete = verify_complete(files.__getitem__)
    OUT.mkdir(parents=True, exist_ok=True)
    entries = {}
    for name, data in files.items():
        destination = OUT / 'payload' / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        entries[name] = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    manifest = {'schema': 1, 'kind': 'nx300m-first-install-local-candidate',
                'files': entries, 'backend': backend, 'manual_lens_wifi': wifi,
                'complete_runtime': complete,
                'physical_first_install_verified': False}
    (OUT / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('FIRST_INSTALL_FILES=', len(files), 'directory=', OUT)


if __name__ == '__main__': main()
