"""Package the current single-slot preview overlay after both ARM probes pass."""
import hashlib
import json
from pathlib import Path
import struct
import zipfile


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/deployment/smartfilter-preview'
FILES = {
    'nx300m-mod/bin/smartfilter_loader.arm': OUT / 'smartfilter_loader.arm',
    'nx300m-mod/lib/smartfilter_ram.bin': OUT / 'smartfilter_ram.bin',
    'nx300m-mod/bin/lut_engine.arm': ROOT / 'outputs/metol/lut_engine.arm',
    'nx300m-mod/bin/nxui.arm': ROOT / 'outputs/metol/nxui.arm',
    'nx300m-mod/bin/nx-remote-controller.arm': ROOT / 'outputs/metol/remote/nx-remote-controller.arm',
    'nx300m-mod/smartfilter-preview.sh': ROOT / 'mod/deployment/mfw/smartfilter-preview.sh',
    'nx300m-mod/lut-with-preview.sh': ROOT / 'mod/deployment/mfw/lut-with-preview.sh',
    'nx300m-mod/mfw-panel-actions.sh': ROOT / 'mod/deployment/mfw/panel-actions.sh',
    'nx300m-mod/gui_mfw.NX300M': ROOT / 'mod/deployment/mfw/gui_mfw.NX300M',
    'nx300m-mod/gui_preview.NX300M': ROOT / 'mod/deployment/mfw/gui_preview.NX300M',
    'nx300m-mod/fn-panel/gui_mfw.NX300M': ROOT / 'mod/deployment/mfw/fn-gui_mfw.NX300M',
    'nx300m-mod/fn-panel/gui_preview.NX300M': ROOT / 'mod/deployment/mfw/gui_preview.NX300M',
    'nx300m-mod/preview-tests/identity.d4lut': OUT / 'ram-vm/source-files/identity.d4lut',
    'nx300m-mod/preview-tests/monochrome.d4lut': OUT / 'ram-vm/source-files/monochrome.d4lut',
    'README.md': ROOT / 'docs/SMARTFILTER_CANDIDATE.md',
}


def main():
    reports = {}
    for name in ('arm-vm', 'ram-vm'):
        report = json.loads((OUT / name / 'report.json').read_text(encoding='utf-8'))
        if not report.get('passed') or not report.get('private_vm_stopped'):
            raise ValueError(f'{name} has not completed successfully')
        reports[name] = report
    build = json.loads((OUT / 'ram-build-report.json').read_text(encoding='utf-8'))
    tested = json.loads((OUT / 'ram-vm/candidate.json').read_text(encoding='utf-8'))['ram_build']
    if build['build'] != tested['build']:
        raise ValueError('RAM probe used another module build')
    for relative, source in (('bin/smartfilter_loader.arm', OUT / 'smartfilter_loader.arm'),
                             ('smartfilter_ram.bin', OUT / 'smartfilter_ram.bin')):
        if source.read_bytes() != (OUT / 'ram-vm/source-files' / relative).read_bytes():
            raise ValueError(f'Current artifact differs from the RAM probe: {source.name}')
    header = struct.unpack('<16I', (OUT / 'smartfilter_ram.bin').read_bytes()[:64])
    if header[15] != build['build']:
        raise ValueError('Blob and build report differ')
    controls_path = json.loads((OUT / 'controls-menu-latest.json').read_text(encoding='utf-8'))['report']
    controls = json.loads(Path(controls_path).read_text(encoding='utf-8'))
    if not controls.get('passed') or not controls.get('private_vm_stopped'):
        raise ValueError('Physical-control menu probe has not completed')
    for name, item in controls['candidate_files'].items():
        if hashlib.sha256(FILES[name].read_bytes().replace(b'\r\n', b'\n') if name.endswith('.sh') else FILES[name].read_bytes()).hexdigest() != item:
            raise ValueError('Menu candidate differs from its controls probe: ' + name)
    archive_path = OUT / 'nx300m-smartfilter-preview.zip'
    inventory = []
    with zipfile.ZipFile(archive_path, 'w', compression=zipfile.ZIP_DEFLATED) as package:
        for name, source in FILES.items():
            data = source.read_bytes()
            if source.suffix in ('.sh', '.md'):
                data = data.replace(b'\r\n', b'\n')
            target = OUT / 'sd' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = (0o100755 if '/bin/' in name or name.endswith('.sh') else 0o100644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            package.writestr(info, data)
            inventory.append(dict(path=name, bytes=len(data), sha256=hashlib.sha256(data).hexdigest()))
    with zipfile.ZipFile(archive_path) as package:
        for item in inventory:
            if package.read(item['path']) != (OUT / 'sd' / item['path']).read_bytes():
                raise ValueError(f'Archive differs from SD overlay: {item["path"]}')
    report = dict(archive=str(archive_path), build=build['build'], files=inventory,
                  arm_boundary_checks=len(reports['arm-vm']['checks']),
                  ram_plt_hooks=reports['ram-vm']['actual_plt_hooks'],
                  physical_camera_or_card_accessed=False, hardware_isp_pixels_verified=False,
                  raw_menu_restrictions_resolved=False, automatic_startup_changed=False)
    report['camera_button_preview_menu'] = True
    report['controls_menu_report'] = controls_path
    (OUT / 'package-report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(f'Packaged {len(inventory)} files: {archive_path}')


if __name__ == '__main__':
    main()
