"""Package native remote, panels, catalogues and Quick Share for an NX300M SD card.

This does not install the start hook, modify firmware, or contact a camera.
"""
import argparse
import gzip
import io
import json
from pathlib import Path
import shlex
import shutil
import struct
import subprocess
import tarfile
from look_library import LookLibrary
from recipe_style import catalogue as recipe_catalogue
from elftools.elf.elffile import ELFFile

PROJECT = Path(__file__).resolve().parents[1]
OUTPUT = PROJECT / 'outputs/deployment/sd-bundle'
SCRIPTS = ('common.sh', 'start.sh', 'stop.sh', 'service.sh', 'panel.sh', 'quickshare-panel.sh',
           'ram-start.sh', 'mfw-ram-stage.sh')
RAM_SCRIPTS = {'ram-start.sh': 'mfw/autoexec-services.sh', 'mfw-ram-stage.sh': 'mfw/ram-stage.sh'}
SWITCH_SCRIPTS = (
    'toggle_dial_lock.sh', 'off_toggle_dial_lock.sh',
    'toggle_auto_iso.sh', 'off_toggle_auto_iso.sh',
    'camera_control.sh', 'set_control.sh', 'cycle_shutter_limit.sh', 'switch_mode.sh', 'apply_look.sh', 'gui_exit.sh'
)
MENUS = ('gui_ini.NX300M', 'gui_modes.NX300M', 'gui_looks.NX300M', 'gui_controls.NX300M', 'gui_exposure.NX300M')
PANEL_BINARIES = ('nxui.arm', 'recipe_engine.arm', 'lut_engine.arm')
PANEL_FONT = 'usr/share/fonts/SDIC_GP_US_20120720.ttf'


def bundle_looks(root):
    library = LookLibrary()
    looks = library.list()
    if len(looks) > 256:
        raise ValueError('Native LUT catalog supports at most 256 entries')
    rows, ids = [], set()
    for look in looks:
        key = look['id']
        source = library.directory(key)
        if key in ids or source.name != key or library.get(key) != look:
            raise ValueError(f'Ambiguous LUT entry: {key}')
        ids.add(key)
        label = library.label(look['label'])
        if any(ord(c) < 32 or ord(c) == 127 for c in label):
            raise ValueError(f'Control character in LUT label: {key}')
        target = root / 'looks' / key
        target.mkdir(parents=True)
        for name in ('table.d4lut', 'table-stock3d.d4lut'):
            path = source / name
            if name == 'table-stock3d.d4lut' and not path.exists():
                if look.get('native_stock_3d_table') == name:
                    raise ValueError(f'Missing composed table: {key}')
                continue
            data = path.read_bytes()
            if len(data) != 4380:
                raise ValueError(f'Invalid LUT table length: {path}')
            words = struct.unpack('<2190H', data)
            if any(v > 1023 or (i % 730 == 729 and v) for i, v in enumerate(words)):
                raise ValueError(f'Invalid 10-bit LUT table or padding: {path}')
            (target / name).write_bytes(data)
        # Keep portable metadata; local LR profile paths are not runtime inputs.
        metadata = {k: v for k, v in look.items() if k != 'source_path'}
        (target / 'look.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        rows.append('|'.join([key, label, *['0'] * 7]) + '\n')
    (root / 'looks.txt').write_text(''.join(rows), encoding='utf-8', newline='\n')
    return sorted(ids)


def arm_elf(path):
    header = path.read_bytes()[:20]
    if len(header) != 20 or header[:6] != b'\x7fELF\x01\x01' or struct.unpack_from('<H', header, 18)[0] != 40:
        raise ValueError(f'Expected little-endian ELF32 ARM binary: {path}')
    with path.open('rb') as stream:
        dynamic = ELFFile(stream).get_section_by_name('.dynamic')
        return [tag.needed for tag in dynamic.iter_tags() if tag.entry.d_tag == 'DT_NEEDED'] if dynamic else []


def bundle_panel_assets(root):
    records = recipe_catalogue()
    if not records:
        raise ValueError('Build a nonempty recipe catalogue with tools/build_metol.py')
    shutil.copyfile(PROJECT / 'outputs/metol/recipes.txt', root / 'recipes.txt')
    for menu in MENUS:
        target = root / menu
        target.write_text((PROJECT / 'mod/deployment' / menu).read_text(encoding='utf-8'), encoding='utf-8', newline='\n')
    for script in SWITCH_SCRIPTS:
        target = root / script
        target.write_text((PROJECT / 'mod/deployment' / script).read_text(encoding='utf-8'), encoding='utf-8', newline='\n')
    target_font_conf = root / 'fonts/fonts.conf.in'
    target_font_conf.parent.mkdir(parents=True, exist_ok=True)
    target_font_conf.write_text((PROJECT / 'mod/deployment/fonts.conf.in').read_text(encoding='utf-8'), encoding='utf-8', newline='\n')
    with tarfile.open(PROJECT / 'work/stock-1.15/rootfs.tar') as stock:
        member = stock.getmember(PANEL_FONT)
        if not member.isfile() or not member.size:
            raise ValueError('Original NX300M panel font is unavailable')
        (root / 'fonts' / Path(PANEL_FONT).name).write_bytes(stock.extractfile(member).read())
    (root / 'fonts/SOURCE.txt').write_text(
        'Original NX300M firmware 1.15 font, copied from work/stock-1.15/rootfs.tar:\n' + PANEL_FONT + '\n',
        encoding='utf-8', newline='\n')
    (root / 'licenses').mkdir()
    for source, name in (('mod/metol/LICENSE', 'metol-AGPL-3.0.txt'),
                         ('mod/metol/ATTRIBUTION.md', 'metol-ATTRIBUTION.md'),
                         ('work/reference/metol/test_server/nxui/vendor/LICENSE', 'Nuklear-MIT.txt'),
                         ('mod/metol/vendor/jsmn-LICENSE', 'jsmn-MIT.txt')):
        shutil.copyfile(PROJECT / source, root / 'licenses' / name)
    return {'recipes': list(records), 'font': {'stock_source': PANEL_FONT, 'bytes': member.size},
            'font_family': 'SDIC_GP_US', 'native_default': True}


def build(remote=None):
    remote = remote or PROJECT / 'outputs/metol/remote/nx-remote-controller.arm'
    quickshare = PROJECT / 'outputs/quickshare/nx-quickshare-armv7'
    web = PROJECT / 'outputs/metol/remote/web_root'
    binaries = (remote, quickshare, PROJECT / 'outputs/quickshare/gallery/quickshare-panel.arm',
                *(PROJECT / 'outputs/metol' / name for name in PANEL_BINARIES))
    native_dependencies = {binary.name: arm_elf(binary) for binary in binaries}
    if not (web / 'index.html').is_file():
        raise ValueError('Build the remote web client first with tools/build_remote.py')
    OUTPUT.mkdir(parents=True, exist_ok=True)
    root = OUTPUT / 'nx300m-mod'
    # Only replace this builder's own staging tree; no camera/runtime directory.
    if root.exists():
        if root.is_symlink() or root.resolve().parent != OUTPUT.resolve():
            raise ValueError(f'Unexpected staging path: {root}')
        shutil.rmtree(root)
    (root / 'bin').mkdir(parents=True)
    for name in SCRIPTS:
        source = PROJECT / 'mod/deployment' / RAM_SCRIPTS.get(name, name)
        (root / name).write_text(source.read_text(encoding='utf-8'), encoding='utf-8', newline='\n')
    (root / 'README.md').write_text((PROJECT / 'mod/deployment/README.md').read_text(encoding='utf-8'),
                                   encoding='utf-8', newline='\n')
    for binary in binaries:
        shutil.copyfile(binary, root / 'bin' / binary.name)
    lib_dir = root / 'lib'
    lib_dir.mkdir(parents=True, exist_ok=True)
    fw_mod_so = PROJECT / 'outputs/controls/camera_firmware_mod.so'
    if fw_mod_so.exists():
        shutil.copyfile(fw_mod_so, lib_dir / fw_mod_so.name)
    shutil.copytree(web, root / 'web_root')
    look_ids = bundle_looks(root)
    panels = bundle_panel_assets(root)
    runtime_files = sorted(path.relative_to(root).as_posix() for path in root.rglob('*') if path.is_file())
    runtime_files.append('runtime-files.list')
    (root / 'runtime-files.list').write_text(''.join(name + '\n' for name in sorted(runtime_files)),
                                            encoding='ascii', newline='\n')
    inventory = []
    archive = OUTPUT / 'nx300m-mod-sd.tar.gz'
    with archive.open('wb') as stream, gzip.GzipFile(filename='', mode='wb', fileobj=stream, mtime=0) as zipped, tarfile.open(fileobj=zipped, mode='w') as tar:
        for path in sorted(root.rglob('*')):
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                raise ValueError(f'Unexpected bundle symlink: {path}')
            is_dir = path.is_dir()
            mode = 0o755 if is_dir or relative in SCRIPTS or relative in SWITCH_SCRIPTS or relative.startswith('bin/') else 0o644
            info = tarfile.TarInfo('nx300m-mod/' + relative)
            info.mode = mode
            if is_dir:
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            else:
                data = path.read_bytes()
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
                inventory.append({'path': relative, 'bytes': len(data), 'mode': oct(mode)})
    report = {'bundle': archive.relative_to(PROJECT).as_posix(), 'root': '/mnt/mmc/nx300m-mod',
              'listen_default': '127.0.0.1:8781', 'services': ['remote (owns on-demand Quick Share children)'],
              'looks': look_ids, 'panels': panels, 'native_dependencies': native_dependencies,
              'runtime': {'migrated': True, 'entry': 'ram-start.sh --service',
                          'stage_source': 'mod/deployment/mfw/ram-stage.sh',
                          'root': 'resolved /tmp/nx300m-mfw/runtime on writable executable tmpfs',
                          'state': 'resolved /tmp/nx300m-mfw/state',
                          'parent_app_root': 'unchanged; source/LUT root remains the app launch root',
                          'old_sd_service': 'refuse; no SD fallback or automatic old-process migration'},
              'firmware_modified': False, 'files': inventory}
    (OUTPUT / 'build-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report


def probe():
    guest_project = '/mnt/' + PROJECT.drive[0].lower() + PROJECT.as_posix()[2:]
    script = 'cd ' + shlex.quote(guest_project) + ' && exec unshare --mount --net --ipc --pid --fork --mount-proc python3 mod/deployment/probe_lifecycle.py'
    result = subprocess.run(['wsl.exe', '-u', 'root', '-e', 'sh', '-c', script], capture_output=True, encoding='utf-8', errors='replace')
    (OUTPUT / 'probe.stdout.log').write_text(result.stdout, encoding='utf-8')
    (OUTPUT / 'probe.stderr.log').write_text(result.stderr, encoding='utf-8')
    if result.returncode:
        raise RuntimeError(f'Isolated lifecycle probe exited {result.returncode}:\n{result.stderr[-6000:]}')


def main():
    global OUTPUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe', action='store_true', help='Run shell lifecycle probes in a fresh WSL PID/mount/network namespace')
    parser.add_argument('--output', type=Path, help='Separate staging directory under outputs/ for concurrent integration work')
    args = parser.parse_args()
    if args.output:
        candidate=args.output.resolve()
        if not candidate.is_relative_to((PROJECT/'outputs').resolve()) or candidate==(PROJECT/'outputs').resolve():
            parser.error('--output must be a subdirectory of project outputs/')
        if args.probe:
            parser.error('--output cannot be combined with the existing fixed-path lifecycle probe')
        OUTPUT=candidate
    report = build()
    if args.probe:
        probe()
    print(f"SD bundle built: {report['bundle']} ({len(report['files'])} files)")


if __name__ == '__main__':
    main()
