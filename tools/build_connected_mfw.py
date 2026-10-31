"""Combine the verified RAM service kit with the physical-tested Wi-Fi tool."""
import json
from pathlib import Path
import zipfile

PROJECT = Path(__file__).resolve().parents[1]
OUT = PROJECT/'outputs/deployment/mfw-connected'
RUN_ID = 'connected-20261027-01'


def shell(path):
    return path.read_bytes().replace(b'\r\n', b'\n')


def main():
    service = PROJECT/'outputs/deployment/mfw-services'
    verification = json.loads((service/'ram-runtime/arm-vm/report.json').read_text(encoding='utf-8'))
    if not verification['passed']:
        raise RuntimeError('The base ARM service integration did not pass')
    snapshot = Path(verification['candidate']['snapshot_directory'])
    package = {}
    with zipfile.ZipFile(service/'nx300m-mfw-services.zip') as base:
        if base.testzip() is not None:
            raise RuntimeError('Base service archive is corrupt')
        for entry in base.infolist():
            name = entry.filename
            if name not in verification['candidate']['inventory']:
                raise RuntimeError('Unexpected base package member: '+name)
            data = base.read(name)
            if data != (snapshot/name).read_bytes():
                raise RuntimeError('Base service differs from ARM-verified bytes: '+name)
            if name.startswith('nx300m-mod/'):
                package[name] = data
            elif name == 'autoexec.sh':
                package['nx300m-mod/mfw-service-entry.sh'] = data
    lens = PROJECT/'outputs/controls/manual-lens'
    evidence = json.loads((lens/'wifi-mem-build-probe.json').read_text(encoding='utf-8'))
    binary = (lens/'manual-lens-wifi-mem.arm').read_bytes()
    if evidence['fixture_returncode'] != 0 or evidence['bytes'] != len(binary):
        raise RuntimeError('Wi-Fi tool build evidence mismatch')
    package.update({
        'autoexec.sh': shell(PROJECT/'mod/deployment/mfw/autoexec-connected.sh'),
        'nx300m-lens-debug/autoexec-wifi.sh': shell(PROJECT/'mod/deployment/mfw/autoexec-lens-wifi-mem.sh'),
        'nx300m-lens-debug/manual-lens-wifi-mem.arm': binary,
        'nx300m-lens-debug/WIFI_MEM_RUN_ID': (RUN_ID+'\n').encode('ascii'),
        'nx300m-lens-debug/WIFI_MEM_PROFILE': b'manual-lens-wifi\n',
        'nx300m-mod/NO_PANEL_SHORTCUT': b'connected boot: no key grabbers\n',
    })
    OUT.mkdir(parents=True, exist_ok=True)
    archive = OUT/'nx300m-connected.zip'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as target:
        for name, data in sorted(package.items()):
            path = OUT/'sd'/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = (0o100755 if name.endswith('.sh') or '/bin/' in name or name.endswith('.arm') else 0o100644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            target.writestr(info, data)
    report = {
        'archive': str(archive), 'run_id': RUN_ID,
        'files': [{'path': name, 'bytes': len(data)} for name, data in sorted(package.items())],
        'base_service_bytes_match_arm_verified_snapshot': True,
        'http_listen': '0.0.0.0:8781', 'discovery_udp_port': 5681,
        'custom_shortcut': False, 'legacy_fn_shortcut': False,
        'color_patch_loaded': False, 'stock_camera_restart': False,
        'physical_phone_connection_verified': False,
    }
    (OUT/'build-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(f'Built {len(package)} files: {archive}')


if __name__ == '__main__':
    main()
