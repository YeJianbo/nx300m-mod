"""Build a public release from the currently working card without personal data."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
PREFIX = 'nx300m-mod/'
USER_FILES = {PREFIX + name for name in ('looks.txt', 'looks-cycle.txt', 'lenses.txt')}
STATE_PREFIX = PREFIX + '.state/'
LOG_PREFIX = PREFIX + 'logs/'
PERSONAL_LOOK_PREFIX = PREFIX + 'looks/'
PREVIOUS_VERSION = 'v0.1.0-preview.8'
PREVIOUS_RELEASE = ROOT / 'outputs/releases' / PREVIOUS_VERSION
ACCEPTED_BASELINES = ('v0.1.0-preview.8', 'v0.1.0-preview.9', 'v0.1.0-preview.10')


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def previous_runtime_hashes() -> dict[str, dict]:
    """Merge the SD-archive file hashes of every accepted baseline release.

    A file absent from at least one baseline cannot be required on every
    supported upgrade path, so its record carries which baselines lack it.
    """
    merged: dict[str, dict] = {}
    name_sets: dict[str, set] = {}
    for version in ACCEPTED_BASELINES:
        with ZipFile(ROOT / 'outputs/releases' / version / f'NX300M-MOD-SD-{version}.zip') as previous:
            present = set()
            for name in previous.namelist():
                if name.startswith(PREFIX) and not name.endswith('/'):
                    present.add(name)
                    record = merged.setdefault(name, {'hashes': [], 'missing_from': []})
                    record['hashes'].append(digest(previous.read(name)))
            name_sets[version] = present
    for version in ACCEPTED_BASELINES:
        for name, record in merged.items():
            if name not in name_sets[version]:
                record['missing_from'].append(version)
    return merged


def safe_rel(name: str) -> Path:
    path = Path(name)
    if path.is_absolute() or '..' in path.parts or '\\' in name:
        raise ValueError('invalid release path: ' + name)
    return path


def read_card(card: Path, name: str) -> bytes:
    return (card / safe_rel(name)).read_bytes()


def make_archive(path: Path, files: dict[str, bytes]) -> None:
    with ZipFile(path, 'w', ZIP_DEFLATED, compresslevel=6) as archive:
        for name, data in sorted(files.items()):
            archive.writestr(name, data)
    with ZipFile(path) as archive:
        if archive.testzip() is not None or set(archive.namelist()) != set(files):
            raise RuntimeError('archive verification failed: ' + str(path))
        for name, data in files.items():
            if archive.read(name) != data:
                raise RuntimeError('archive readback differs: ' + name)


def runtime_files(card: Path) -> dict[str, bytes]:
    runtime = read_card(card, PREFIX + 'runtime-files.list').decode('ascii').splitlines()
    if not runtime or len(runtime) != len(set(runtime)):
        raise ValueError('runtime-files.list is empty or duplicated')
    files = {
        'autoexec.sh': read_card(card, 'autoexec.sh'),
        PREFIX + 'mfw-service-entry.sh': read_card(card, PREFIX + 'mfw-service-entry.sh'),
        PREFIX + 'runtime-files.list': ('\n'.join(runtime) + '\n').encode('ascii'),
        PREFIX + 'RESTART_SERVICE': b'card-release-build\n',
    }
    for relative in runtime:
        files[PREFIX + relative] = read_card(card, PREFIX + relative)
    for directory in ('nx300m-lens-debug/',):
        for path in sorted((card / directory).rglob('*')):
            if path.is_file() and not any(part == 'logs' for part in path.relative_to(card).parts):
                relative = path.relative_to(card).as_posix()
                if relative in ('nx300m-lens-debug/wifi-mem-entry.log',):
                    continue
                files[relative] = path.read_bytes()
    if any('/.state/' in '/' + name or '/logs/' in '/' + name for name in files):
        raise ValueError('personal state or logs entered release')
    if files[PREFIX + 'bin/nxui.arm'] != files[PREFIX + 'fn-panel/bin/nxui.arm']:
        raise ValueError('Main and alternate panel binaries differ on the release card')
    return files


def public_looks() -> dict[str, bytes]:
    source = ROOT / 'outputs/looks/original-release'
    manifest = json.loads((source / 'manifest.json').read_text(encoding='utf-8'))
    files: dict[str, bytes] = {}
    for item in manifest.get('looks', []):
        name = item.get('id') if isinstance(item, dict) else item
        if not name or not (source / name).is_dir():
            raise ValueError('original look missing: ' + str(name))
        for filename in ('look.json', 'table.d4lut'):
            files[PREFIX + 'looks/' + name + '/' + filename] = (source / name / filename).read_bytes()
    files[PREFIX + 'looks.txt'] = (source / 'looks.txt').read_bytes()
    files[PREFIX + 'looks-cycle.txt'] = files[PREFIX + 'looks.txt']
    files[PREFIX + 'lenses.txt'] = b''
    files['LUT-LICENSE.txt'] = (source / 'LICENSE.txt').read_bytes()
    return files


def write_upgrade(runtime: dict[str, bytes], install_note: bytes, output: Path, version: str,
                  source_commit: str, freeze_date: str) -> Path:
    upgrade_files = {name: data for name, data in runtime.items()
                     if name.startswith(PREFIX) and name not in USER_FILES}
    current_menu = upgrade_files[PREFIX + 'bin/nxui.arm']
    current_runtime = runtime[PREFIX + 'runtime-files.list'].decode('ascii').splitlines()
    runtime_additions = [name for name in current_runtime
                         if PREFIX + name not in USER_FILES]
    previous_hashes = previous_runtime_hashes()
    previous_menus = set(previous_hashes.get(PREFIX + 'bin/nxui.arm', {}).get('hashes', ()))
    manifest = {
        'schema': 1,
        'kind': 'nx300m-checked-card-runtime',
        'version': version,
        'source_commit': source_commit,
        'frozen_card_runtime_at': freeze_date,
        'accepted_baselines': list(ACCEPTED_BASELINES),
        'accepted_menu_sha256': sorted(previous_menus | {digest(current_menu)}),
        'runtime_additions': runtime_additions,
        'files': {name: {
            'bytes': len(data), 'sha256': digest(data),
            'previous_sha256': previous_hashes.get(name, {}).get('hashes', [None])[0],
        } for name, data in sorted(upgrade_files.items())},
    }
    archive_files = {'payload/' + name: data for name, data in upgrade_files.items()}
    archive_files.update({
        'manifest.json': (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode('utf-8'),
        'update-card.ps1': b'\xef\xbb\xbf' + (PREVIOUS_RELEASE / 'update-card.ps1').read_bytes(),
        'INSTALL.txt': install_note,
    })
    with ZipFile(PREVIOUS_RELEASE / f'NX300M-MOD-Upgrade-{PREVIOUS_VERSION}.zip') as previous:
        for name in previous.namelist():
            if name == 'NOTICE.md' or name == 'RISKS_AND_RIGHTS.md' or name.startswith('LICENSES/'):
                archive_files[name] = previous.read(name)
    if any(name in USER_FILES or name.startswith((PERSONAL_LOOK_PREFIX, STATE_PREFIX, LOG_PREFIX))
           for name in upgrade_files):
        raise ValueError('user data entered standalone upgrade')
    path = output / f'NX300M-MOD-Upgrade-{version}.zip'
    make_archive(path, archive_files)
    return path


def write_windows_staging(files: dict[str, bytes], output: Path, version: str,
                  source_commit: str, freeze_date: str) -> Path:
    package = output / 'windows-package'
    if package.exists():
        archived = output / ('windows-package-before-' + version + '-rebuild')
        if archived.exists():
            import time
            archived = output / ('windows-package-before-' + version + '-rebuild-' + time.strftime('%H%M%S'))
        package.rename(archived)
    package.mkdir(parents=True)
    # Reuse only release metadata and licenses. Extracting the prior program
    # would put its executable, _internal runtime and source back into a build
    # presented as the current installer.
    with ZipFile(PREVIOUS_RELEASE / f'NX300M-MOD-Windows-{PREVIOUS_VERSION}.zip') as archive:
        for name in archive.namelist():
            if name == 'NOTICE.md' or name.startswith('LICENSES/'):
                target = safe_rel(name)
                path = package / target
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(archive.read(name))
    package_docs = {
        'USER_GUIDE.md': ROOT / 'docs/USER_GUIDE.md',
        'RISKS_AND_RIGHTS.md': ROOT / 'docs/RISKS_AND_RIGHTS.md',
        '使用说明.md': ROOT / 'docs/WINDOWS_INSTALLER_PUBLIC.md',
        '首次安装说明.md': ROOT / 'docs/WINDOWS_INSTALLER_PUBLIC.md',
        'WINDOWS-SIGNING.txt': None,
    }
    for name, source_doc in package_docs.items():
        if source_doc is not None and source_doc.is_file():
            shutil.copy2(source_doc, package / name)
    (package / 'WINDOWS-SIGNING.txt').write_text(
        'NX300M-MOD.exe 未进行商业代码签名，Windows 可能显示 SmartScreen 提示。\n'
        '请从本 Release 下载并先用 SHA256SUMS.txt 核对压缩包。\n', encoding='utf-8')
    shutil.copy2(ROOT / 'outputs/looks/original-release/LICENSE.txt', package / 'LUT-LICENSE.txt')
    first = package / 'first-install'
    payload = {name: data for name, data in files.items()
               if name == 'autoexec.sh' or name.startswith(('nx300m-mod/', 'nx300m-lens-debug/'))}
    entries = {}
    for name, data in payload.items():
        target = first / 'payload' / safe_rel(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        entries[name] = {'bytes': len(data), 'sha256': digest(data)}
    first_manifest = {
        'schema': 1,
        'kind': 'nx300m-first-install-local-candidate',
        'distribution': 'public-original-looks',
        'paragraph': 1,
        'card_manifest': True,
        'source_commit': source_commit,
        'frozen_card_runtime_at': freeze_date,
        'files': entries,
    }
    (first / 'manifest.json').write_text(json.dumps(first_manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    # Upgrade only replaces the frozen runtime. Public original LUTs are copied by
    # first install, while an existing card's looks and state remain user-owned.
    runtime = files[PREFIX + 'runtime-files.list'].decode('ascii').splitlines()
    upgrade_names = {
        PREFIX + 'mfw-service-entry.sh', PREFIX + 'runtime-files.list',
        PREFIX + 'RESTART_SERVICE',
        *(PREFIX + name for name in runtime),
    }
    upgrade_names.difference_update(USER_FILES)
    upgrade_payload = {name: files[name] for name in upgrade_names}
    previous_hashes = previous_runtime_hashes()
    accepted_menu = sorted({*previous_hashes.get(PREFIX + 'bin/nxui.arm', {}).get('hashes', ()),
                            digest(payload[PREFIX + 'bin/nxui.arm'])})
    manifest = {
        'schema': 1,
        'kind': 'nx300m-checked-card-runtime',
        'version': version,
        'source_commit': source_commit,
        'frozen_card_runtime_at': freeze_date,
        'accepted_baselines': list(ACCEPTED_BASELINES),
        'accepted_menu_sha256': accepted_menu,
        'backend_files': {},
        'payload': {},
        'files': {},
    }
    for name, data in upgrade_payload.items():
        asset = 'payload/' + name
        target = package / asset
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        previous = previous_hashes.get(name, {'hashes': [], 'missing_from': list(ACCEPTED_BASELINES)})
        manifest['files'][name] = {
            'bytes': len(data), 'sha256': digest(data),
            'accepted': sorted(set(previous['hashes']) | {digest(data)}),
            'allow_missing': bool(previous['missing_from']),
        }
        manifest['payload'][name] = {'asset': asset, 'sha256': digest(data),
                                     'accepted': [None, digest(data)]}
    if any(name in USER_FILES or name.startswith((PERSONAL_LOOK_PREFIX, STATE_PREFIX, LOG_PREFIX))
           for name in upgrade_payload):
        raise ValueError('user data entered Windows upgrade')
    (package / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    source = package / 'source'
    source.mkdir()
    source_names = (
        'nx300m_installer.py', 'nx300m_client_ui.py', 'nx300m_lut_core.py',
        'installer_backend.py', 'manual_lens_wifi_payload.py', 'release_payload.py',
        'build_card_release.py', 'build_first_install_candidate.py',
        'build_windows_installer.py', 'build_original_looks.py',
        'build_public_first_install.py', 'cube_to_d4.py', 'lut_resample.py',
        'lut_integral.py', 'import_lumix_lab_luts.py', 'look_catalog.py',
        'look_library.py', 'lut_table_data.py',
    )
    for name in source_names:
        path = TOOLS / name
        if not path.is_file():
            raise ValueError('installer source missing: ' + name)
        shutil.copy2(path, source / name)
    (package / 'SOURCE.md').write_text(
        '# 对应源码\n\n'
        f'Source commit: {source_commit}\n\n'
        f'{version} snapshot: {freeze_date}. Release file identities are listed in VALIDATION.json.\n\n'
        f'对应源码快照：https://github.com/YeJianbo/nx300m-mod/tree/{version}\n\n'
        'Windows 工具及 LUT 转换源码随包保存在 source/，同一 Release 的源码包记录卡文件身份、冻结源码与本版 Windows 工具。\n',
        encoding='utf-8')
    return package


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--card', type=Path, default=Path('H:/'))
    parser.add_argument('--version', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--freeze-date', required=True)
    parser.add_argument('--skip-exe', action='store_true')
    parser.add_argument('--overwrite', action='store_true')
    args = parser.parse_args()
    card = args.card.resolve()
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        if not args.overwrite:
            raise FileExistsError('refusing to replace prior release evidence: ' + str(output))
        import time
        archived = output.with_name(output.name + '-before-' + time.strftime('%Y%m%d-%H%M%S'))
        if archived.exists():
            raise FileExistsError('release archive target already exists')
        output.rename(archived)
    output.mkdir(parents=True, exist_ok=True)
    version = args.version
    source_commit = args.source_commit
    freeze_date = args.freeze_date
    runtime = runtime_files(card)
    looks = public_looks()
    sd_files = dict(runtime)
    sd_files.update(looks)
    install_note = ('NX300M MOD 已安装 MFW 后使用。此包只含运行程序与原创 LUT；\n'
                    '已有 MOD 建议使用 Windows 图形安装器升级以保留个人 LUT、收藏和参数。\n')
    sd_files['INSTALL.txt'] = install_note.encode('utf-8')
    sd = output / f'NX300M-MOD-SD-{version}.zip'
    make_archive(sd, sd_files)
    upgrade_inputs = dict(runtime)
    upgrade = write_upgrade(upgrade_inputs, install_note.encode('utf-8'), output, version, source_commit, freeze_date)
    validation = {
        'version': version,
        'source': 'frozen NX300M test-card snapshot',
        'source_commit': source_commit,
        'frozen_card_runtime_at': freeze_date,
        'accepted_baselines': list(ACCEPTED_BASELINES),
        'runtime_files': len(runtime),
        'runtime_sha256': {name: digest(sd_files[name]) for name in sorted(runtime)},
        'menu_sha256': digest(runtime[PREFIX + 'bin/nxui.arm']),
        'public_luts': sorted(name for name in looks if name.startswith(PREFIX + 'looks/')),
        'excludes': ['photos', 'personal LUTs', 'favorites', 'tunes', 'logs', '.state', 'table-stock3d.d4lut'],
        'executable_signed': False,
        'sd_archive': {'name': sd.name, 'bytes': sd.stat().st_size, 'sha256': digest(sd.read_bytes())},
        'upgrade_archive': {'name': upgrade.name, 'bytes': upgrade.stat().st_size, 'sha256': digest(upgrade.read_bytes())},
        'windows_builder': 'PyInstaller 6.22.3, current conda Python, source tools/nx300m_installer.py',
    }
    (output / 'VALIDATION.json').write_text(json.dumps(validation, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    staging = write_windows_staging(sd_files, output, version, source_commit, freeze_date)
    if not args.skip_exe:
        package = output / 'windows-dist'
        if package.exists():
            archived = output / ('windows-dist-before-' + version + '-rebuild')
            if archived.exists():
                import time
                archived = output / ('windows-dist-before-' + version + '-rebuild-' + time.strftime('%H%M%S'))
            package.rename(archived)
        command = [os.sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir',
                   '--windowed', '--name', 'NX300M-MOD', '--distpath', str(package),
                   '--workpath', str(output / 'windows-build'),
                   '--specpath', str(output), str(TOOLS / 'nx300m_installer.py')]
        env = os.environ.copy()
        env['PATH'] = os.pathsep.join((str(Path(os.sys.base_prefix) / 'Library/bin'),
                                       str(Path(os.sys.base_prefix)),
                                       str(Path(os.sys.base_prefix) / 'Scripts'),
                                       env.get('PATH', '')))
        subprocess.run(command, cwd=ROOT, check=True, env=env)
        exe_dir = package / 'NX300M-MOD'
        for item in staging.iterdir():
            target = exe_dir / item.name
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            else:
                shutil.copy2(item, target)
        forbidden = ('NX300M-MOD.exe', '_internal')
        if any((staging / name).exists() for name in forbidden):
            raise RuntimeError('Windows staging contains program runtime')
        windows_zip = output / f'NX300M-MOD-Windows-{version}.zip'
        with ZipFile(windows_zip, 'w', ZIP_DEFLATED, compresslevel=6) as archive:
            for path in sorted(exe_dir.rglob('*')):
                if path.is_file():
                    archive.write(path, path.relative_to(exe_dir).as_posix())
        with ZipFile(windows_zip) as archive:
            if archive.testzip() is not None:
                raise RuntimeError('Windows archive CRC failed')
        validation['windows_archive'] = {
            'name': windows_zip.name, 'bytes': windows_zip.stat().st_size,
            'sha256': digest(windows_zip.read_bytes()),
        }
        (output / 'VALIDATION.json').write_text(json.dumps(validation, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'sd': str(sd), 'upgrade': str(upgrade), 'validation': validation}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
