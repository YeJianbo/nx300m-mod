"""Package the manual RAM LUT candidate without changing a camera."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import struct
import zipfile

from build_sd_bundle import arm_elf, bundle_looks

PROJECT = Path(__file__).resolve().parents[1]
OUTPUT = PROJECT / 'outputs/deployment/lut-mod'


def build(include_diagnostics=False):
    output = PROJECT / 'outputs/deployment/lut-mod-diagnostics' if include_diagnostics else OUTPUT
    module = PROJECT / 'outputs/firmware-runtime/lut_port.so'
    engine = PROJECT / 'outputs/metol/lut_engine.arm'
    ui = PROJECT / 'outputs/metol/nxui.arm'
    remote = PROJECT / 'outputs/metol/remote/nx-remote-controller.arm'
    live_loader = PROJECT / 'outputs/deployment/lut-live-loader/lut_live_loader.arm'
    blob = PROJECT / 'outputs/deployment/lut-live-loader/lut_ram_blob.bin'
    dependencies = {path.name: arm_elf(path) for path in (module, engine, ui, remote, live_loader)}
    # A packaged old binary must not silently lag the current adapter/backend.
    for binary, sources in (
        (module, ('runtime/lut_port.c', 'mod/metol/mod_paths.h')),
        (engine, ('mod/metol/lut_engine.c', 'mod/metol/lut_engine_shared.h', 'runtime/lut_ram_blob.h', 'runtime/lut_ram_blob_build.h', 'mod/metol/mod_paths.h')),
        (ui, ('mod/metol/nxui.c', 'mod/metol/nxui_ximage.h', 'mod/metol/panel_input.h')),
        (live_loader, ('runtime/lut_live_loader.c', 'runtime/lut_ram_blob.h', 'runtime/lut_ram_blob_build.h', 'runtime/live_loader_core.h', 'tools/build_lut_live_loader.py')),
        (blob, ('runtime/lut_ram_blob.c', 'runtime/lut_ram_blob.h', 'runtime/lut_ram_blob_build.h', 'runtime/lut_ram_blob.ld', 'tools/build_lut_ram_blob.py')),
        (remote, ('mod/metol/remote/remote.c', 'mod/metol/lut_engine_shared.h', 'runtime/lut_ram_blob.h', 'runtime/lut_ram_blob_build.h')),
    ):
        if any((PROJECT / source).stat().st_mtime_ns > binary.stat().st_mtime_ns for source in sources):
            raise ValueError(f'Rebuild current sources before packaging: {binary.relative_to(PROJECT)}')
    tested = PROJECT / 'outputs/deployment/lut-live-loader/arm-vm'
    result = json.loads((tested / 'report.json').read_text('utf-8'))
    if not result.get('passed') or not result.get('readiness_independent_of_shot_telemetry') or not result.get('known_image_recovery_checked') or not result.get('lazy_writer_admission_checked'):
        raise ValueError('Current RAM loader/readiness VM checks must pass before packaging')
    for binary, staged in ((live_loader, 'bin/lut_live_loader.arm'), (blob, 'lut_ram_blob.bin')):
        if binary.read_bytes() != (tested / 'source-files' / staged).read_bytes():
            raise ValueError(f'Build differs from the tested VM candidate: {binary.name}')
    blob_header = struct.unpack('<16I', blob.read_bytes()[:64])
    output.mkdir(parents=True, exist_ok=True)
    root = output / 'sd/nx300m-mod'
    if root.exists():
        if root.is_symlink() or root.resolve() != (output.resolve() / 'sd/nx300m-mod'):
            raise ValueError(f'Unexpected staging path: {root}')
        shutil.rmtree(root)
    (root / 'lib').mkdir(parents=True)
    (root / 'bin').mkdir()
    shutil.copyfile(module, root / 'lib/lut_port.so')
    shutil.copyfile(engine, root / 'bin/lut_engine.arm')
    shutil.copyfile(ui, root / 'bin/nxui.arm')
    shutil.copyfile(remote, root / 'bin/nx-remote-controller.arm')
    shutil.copyfile(live_loader, root / 'bin/lut_live_loader.arm')
    shutil.copyfile(blob, root / 'lib/lut_ram_blob.bin')
    compat = PROJECT / 'outputs/deployment/lut-live-loader/compat'
    compatibility_images = []
    for saved in sorted(compat.glob('blob-*.bin')):
        target = root / 'lib/lut-compat' / saved.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(saved, target)
        compatibility_images.append(target.relative_to(root).as_posix())
    (root / 'fonts').mkdir()
    shutil.copyfile(PROJECT / 'outputs/metol/mfw-font.charset', root / 'fonts/mfw-font.charset')
    for source, name in (('mod/deployment/mfw/lut-load.sh', 'lut-load.sh'),
                         ('mod/deployment/mfw/lut-cycle.sh', 'lut-cycle.sh'),
                         ('mod/deployment/mfw/lut-panel.sh', 'lut-panel.sh'),
                         ('mod/deployment/mfw/panel.sh', 'mfw-panel.sh'),
                         ('mod/deployment/mfw/fonts.conf.in', 'fonts/fonts.conf.in'),
                         ('mod/deployment/gui_looks.NX300M', 'gui_looks.NX300M'),
                         ('docs/LUT_MOD.md', 'LUT_MOD.md'),
                         ('docs/LUT_CALIBRATION.md', 'LUT_CALIBRATION.md')):
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text((PROJECT / source).read_text(encoding='utf-8'), encoding='utf-8', newline='\n')
    look_ids = bundle_looks(root)
    # Keep the diagnostic monochrome first, matching the existing Custom cycle.
    catalog = root / 'looks.txt'
    rows = catalog.read_text('utf-8').splitlines(True)
    rows.sort(key=lambda row: 0 if row.startswith('nx300-hard-mono|') else 1)
    catalog.write_text(''.join(rows), encoding='utf-8', newline='\n')
    if include_diagnostics:
        from build_lut_diagnostics import build as build_diagnostics
        build_diagnostics()
        diagnostic_root = PROJECT / 'outputs/deployment/lut-diagnostics/sd/nx300m-mod'
        rows = (diagnostic_root / 'looks-diagnostics.txt').read_text(encoding='utf-8')
        diagnostic_ids = [row.split('|', 1)[0] for row in rows.splitlines() if row.strip()]
        if len(diagnostic_ids) != 5 or set(look_ids).intersection(diagnostic_ids):
            raise ValueError('Diagnostic catalogue is incomplete or overlaps user looks')
        shutil.copytree(diagnostic_root / 'looks', root / 'looks', dirs_exist_ok=True)
        with (root / 'looks.txt').open('a', encoding='utf-8', newline='\n') as file:
            file.write(rows)
        shutil.copyfile(diagnostic_root / 'looks-diagnostics.txt', root / 'looks-diagnostics.txt')
        shutil.copyfile(PROJECT / 'outputs/deployment/lut-diagnostics/manifest.json', root / 'lut-diagnostics.json')
        look_ids.extend(diagnostic_ids)
    web = PROJECT / 'outputs/metol/remote/web_root'
    if not (web / 'index.html').is_file():
        raise ValueError('Build the current remote web client first')
    shutil.copytree(web, root / 'web_root', symlinks=True)
    (root / 'licenses').mkdir()
    for source, name in (('mod/metol/LICENSE', 'metol-AGPL-3.0.txt'),
                         ('mod/metol/ATTRIBUTION.md', 'metol-ATTRIBUTION.md'),
                         ('work/reference/metol/test_server/nxui/vendor/LICENSE', 'Nuklear-MIT.txt')):
        shutil.copyfile(PROJECT / source, root / 'licenses' / name)
    # Preserve every file expected by the user's existing connected-service kit.
    # This archive is an increment: unchanged baseline files remain on the card.
    baseline_root = PROJECT / 'outputs/deployment/mfw-connected/sd/nx300m-mod'
    baseline = (baseline_root / 'runtime-files.list').read_text(encoding='ascii').splitlines()
    if len(baseline) != len(set(baseline)) or not baseline:
        raise ValueError('Invalid connected-service runtime baseline')
    for name in baseline:
        if name.startswith('/') or '..' in name.split('/') or not (baseline_root / name).is_file():
            raise ValueError(f'Invalid or missing connected-service asset: {name}')
    runtime_files = set(baseline) | {'lut-panel.sh', 'lut-load.sh', 'lut-cycle.sh', 'gui_looks.NX300M', 'bin/lut_engine.arm', 'bin/lut_live_loader.arm', 'lib/lut_ram_blob.bin', 'fonts/mfw-font.charset'}
    runtime_files.update(compatibility_images)
    runtime_files.update(path.relative_to(root).as_posix() for path in (root / 'web_root').rglob('*') if path.is_file())
    (root / 'runtime-files.list').write_text(''.join(name + '\n' for name in sorted(runtime_files)), encoding='ascii', newline='\n')
    syntax_checks = []
    for name in ('lut-load.sh', 'lut-panel.sh', 'lut-cycle.sh', 'mfw-panel.sh'):
        guest_script = '/mnt/' + root.drive[0].lower() + (root / name).as_posix()[2:]
        syntax = subprocess.run(['wsl.exe', '-d', 'Debian', '-e', '/bin/sh', '-n', guest_script], capture_output=True)
        if syntax.returncode:
            raise RuntimeError(f'{name} shell syntax exited {syntax.returncode}: {syntax.stderr.decode("utf-8", "replace")}')
        syntax_checks.append({'script': name, 'exit_code': syntax.returncode})
    inventory = []
    archive = output / 'nx300m-lut-mod.zip'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as zipped:
        for path in sorted(root.rglob('*')):
            if path.is_symlink():
                raise ValueError(f'Unexpected package symlink: {path}')
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            executable = relative.endswith('.sh') or relative.startswith('bin/')
            mode = 0o100755 if executable else 0o100644
            info = zipfile.ZipInfo('nx300m-mod/' + relative)
            info.create_system = 3
            info.external_attr = mode << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            contents = path.read_bytes()
            zipped.writestr(info, contents)
            inventory.append({'path': relative, 'bytes': len(contents), 'mode': oct(mode)})
    report = {
        'bundle': archive.relative_to(PROJECT).as_posix(),
        'entry': 'nx300m-mod/lut-load.sh status (default); explicit apply attaches RAM callbacks to existing camera',
        'look_count': len(look_ids), 'looks': look_ids, 'native_dependencies': dependencies,
        'runtime': {'code': 'unique writable executable tmpfs directory; copied and cmp checked',
                    'data_root': 'canonical package directory, normally /mnt/mmc/nx300m-mod'},
        'root_autoexec_included': False, 'service_scripts_included': False,
        'remote_backend_included': True, 'native_panel_included': True,
        'runtime_manifest': {'baseline': (baseline_root / 'runtime-files.list').relative_to(PROJECT).as_posix(),
                             'baseline_count': len(baseline), 'merged_count': len(runtime_files),
                             'removed_baseline_entries': sorted(set(baseline) - runtime_files),
                             'candidate_adapter_automatically_loaded': False,
                             'retained_files_require_existing_connected_service_kit': True},
        'rootfs_modified': False, 'snapshot_modified': False,
        'live_loader_included': True, 'live_loader_automatically_run': False,
        'ram_build': blob_header[13], 'config_abi_bytes': blob_header[9],
        'ram_vm_verified': True, 'viewfinder_register_experiment': False,
        'compatibility_images': compatibility_images,
        'diagnostics_included': include_diagnostics,
        'shell_syntax': syntax_checks, 'files': inventory,
        'validation_scope': 'offline package and shell syntax; no camera start or ISP image verification',
    }
    (output / 'build-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--include-diagnostics', action='store_true', help='Separate development bundle with five native-coordinate probes')
    result = build(parser.parse_args().include_diagnostics)
    print(f"LUT mod built: {result['bundle']} ({result['look_count']} looks, {len(result['files'])} files)")
