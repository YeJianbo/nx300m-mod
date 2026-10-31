"""Install the ARM-verified MFW SD service kit on the identified removable card.

This only writes ordinary SD files. It never starts camera code or modifies
firmware, photos, camera preferences, or hibernation images.
"""
import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import zipfile

PROJECT = Path(__file__).resolve().parents[1]
BASE = PROJECT / 'outputs/deployment/mfw-services'
ARCHIVE = BASE / 'nx300m-mfw-services.zip'
PHOTO_REFERENCE = PROJECT / 'outputs/deployment/mfw-exposure-test/camera/sd-backup/photo-export-report.json'


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def volume(root, serial):
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    api.GetDriveTypeW.restype = wintypes.UINT
    api.GetVolumeInformationW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
        wintypes.LPWSTR, wintypes.DWORD]
    api.GetVolumeInformationW.restype = wintypes.BOOL
    label, fs = ctypes.create_unicode_buffer(261), ctypes.create_unicode_buffer(261)
    number, length, flags = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD()
    if not api.GetVolumeInformationW(str(root), label, len(label), ctypes.byref(number),
            ctypes.byref(length), ctypes.byref(flags), fs, len(fs)):
        raise ctypes.WinError(ctypes.get_last_error())
    observed = f'{number.value:08X}'
    if api.GetDriveTypeW(str(root)) != 2 or fs.value.lower() != 'exfat' or observed != serial.replace('-', '').upper():
        raise RuntimeError(f'Unexpected reader volume: serial={observed}, filesystem={fs.value}')
    return {'drive': str(root), 'serial': observed, 'filesystem': fs.value, 'removable': True}


def check_filesystem(root, output, phase):
    result = subprocess.run(['chkdsk', root.drive], capture_output=True, timeout=60)
    (output / f'chkdsk-{phase}.stdout.txt').write_bytes(result.stdout)
    (output / f'chkdsk-{phase}.stderr.txt').write_bytes(result.stderr)
    if result.returncode:
        raise RuntimeError(f'Read-only chkdsk {phase} exit {result.returncode}; see saved logs')
    return result.returncode


def photos(root, reference, *, allow_new=False):
    result = []
    for item in reference['files']:
        relative = item['path'].lstrip('/')
        path = root / relative
        actual = digest(path)
        if path.stat().st_size != item['bytes'] or actual != item['sha256']:
            raise RuntimeError(f'Photo differs from repaired-card backup: {relative}')
        result.append({'path': relative, 'bytes': item['bytes'], 'sha256': actual})
    present = {p.relative_to(root).as_posix() for p in (root / 'DCIM').rglob('*') if p.is_file()}
    known = {p['path'] for p in result}
    added = present - known
    if known - present or (added and not allow_new):
        raise RuntimeError(f'Photo inventory changed: added={sorted(added)}, missing={sorted(known-present)}')
    for relative in sorted(added):
        path = root / relative
        result.append({'path': relative, 'bytes': path.stat().st_size, 'sha256': digest(path)})
    return result


def write_flushed(path, data):
    with path.open('xb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    if path.read_bytes() != data:
        raise RuntimeError(f'SD readback differs: {path}')


def request_owned_restart(root, serial, output, report):
    volume(root, serial)
    marker = root / 'nx300m-mod/RESTART_SERVICE'
    if marker.exists():
        previous = output / 'restart-marker.before'
        shutil.copy2(marker, previous)
        if previous.read_bytes() != marker.read_bytes():
            raise RuntimeError('Restart marker backup differs')
    data = ('owned-ram-update=' + datetime.now(timezone.utc).isoformat() + '\n').encode('ascii')
    staged = marker.with_name('RESTART_SERVICE.next-' + output.name)
    write_flushed(staged, data)
    staged.replace(marker)
    if marker.read_bytes() != data:
        raise RuntimeError('Restart request readback differs')
    report['owned_ram_restart_requested'] = True
    report['restart_scope'] = 'next MFW entry; exact owned RAM service only; old SD service guard retained'
    report['active_control_markers'] = [name for name in
        ('DISABLED', 'NO_PANEL_SHORTCUT', 'RESTART_SERVICE', 'TRACE_HTTP')
        if (root / 'nx300m-mod' / name).exists()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--drive', required=True, choices=['H:'])
    parser.add_argument('--serial', required=True)
    parser.add_argument('--restart-owned-service', action='store_true',
        help='Request the next MFW entry to replace its verified RAM service after updating files')
    profile = parser.add_mutually_exclusive_group()
    profile.add_argument('--service-only', action='store_true',
        help='Enable the updated service with NO_PANEL_SHORTCUT; keep Custom and Fn listeners off for the first device check')
    profile.add_argument('--custom-shortcut', action='store_true',
        help='Remove NO_PANEL_SHORTCUT from an already deployed matching kit; Custom on, legacy Fn still off by default')
    args = parser.parse_args()
    root = Path(args.drive + '/')
    if root.resolve() != Path('H:/').resolve():
        raise RuntimeError('Expected the explicitly selected reader root')
    identity = volume(root, args.serial)
    verification = json.loads((BASE / 'ram-runtime/arm-vm/report.json').read_text(encoding='utf-8'))
    if not verification['passed']:
        raise RuntimeError('ARM runtime verification did not pass')
    snapshot = Path(verification['candidate']['snapshot_directory'])
    package = {}
    with zipfile.ZipFile(ARCHIVE) as archive:
        if archive.testzip() is not None:
            raise RuntimeError('Service archive is corrupt')
        for item in archive.infolist():
            relative = PurePosixPath(item.filename)
            if relative.is_absolute() or '..' in relative.parts or '\\' in item.filename:
                raise RuntimeError('Unexpected archive path')
            if item.filename not in ('autoexec.sh', 'README.md') and not item.filename.startswith('nx300m-mod/'):
                raise RuntimeError('Unexpected archive scope')
            data = archive.read(item)
            if (snapshot / item.filename).read_bytes() != data:
                raise RuntimeError(f'Archive differs from ARM-tested bytes: {item.filename}')
            target = root / item.filename
            if not target.resolve().is_relative_to(root.resolve()) or target.is_symlink():
                raise RuntimeError('Unexpected target path')
            package[item.filename] = data
    if args.custom_shortcut:
        if (root / 'nx300m-mod/DISABLED').exists():
            raise RuntimeError('Autostart is disabled; shortcut activation does not enable the service')
        for name, data in package.items():
            if not (root / name).is_file() or (root / name).read_bytes() != data:
                raise RuntimeError(f'Shortcut activation requires the already deployed kit: {name}')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    output = BASE / 'reader-deployment' / stamp
    output.mkdir(parents=True, exist_ok=False)
    backup = output / 'backup'
    report = {'status': 'preparing', 'started_utc': datetime.now(timezone.utc).isoformat(),
        'volume': identity, 'archive': str(ARCHIVE), 'package_files': len(package),
        'package_matches_arm_tested_bytes': True, 'backup': str(backup), 'files': [],
        'firmware_written': False, 'camera_code_executed': False, 'camera_boot_verified': False,
        'photos_written': False, 'snapshot_modified': False}
    report_path = output / 'report.json'
    custom_marker_removed = False

    def save():
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    save()
    try:
        report['chkdsk_before_exit'] = check_filesystem(root, output, 'before')
        reference = json.loads(PHOTO_REFERENCE.read_text(encoding='utf-8'))
        report['photos_before'] = photos(root, reference, allow_new=True)
        reference_names = {item['path'].lstrip('/') for item in reference['files']}
        report['new_photos'] = [item for item in report['photos_before'] if item['path'] not in reference_names]
        # New captures are valid card contents. Back them up without weakening
        # the existing reference check, then compare all files after deployment.
        for item in report['new_photos']:
            target = output / 'new-photos' / item['path']
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / item['path'], target)
            if target.stat().st_size != item['bytes'] or digest(target) != item['sha256']:
                raise RuntimeError(f'New photo backup differs: {item["path"]}')
        print(f'Identified {identity}; all {len(report["photos_before"])} photos match backup', flush=True)
        # Preserve the old scripts, program bytes and all historical diagnostics.
        for name in ('autoexec.sh', 'README.md', 'nx300m-mod'):
            source = root / name
            if source.is_dir():
                shutil.copytree(source, backup / name, copy_function=shutil.copy2)
            elif source.is_file():
                (backup / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, backup / name)
        for path in backup.rglob('*'):
            if path.is_file() and path.read_bytes() != (root / path.relative_to(backup)).read_bytes():
                raise RuntimeError(f'Backup verification failed: {path}')
        print(f'Old Mod and diagnostics backed up and verified: {backup}', flush=True)
        if args.service_only:
            # Publish the existing shortcut policy before the bootstrap. The
            # DISABLED marker remains in place until the complete kit is read
            # back, so a failed update cannot activate a partial deployment.
            policy = root / 'nx300m-mod/NO_PANEL_SHORTCUT'
            if not policy.exists():
                write_flushed(policy, b'startup-fix: service-only device check\n')
            elif not policy.is_file() or policy.is_symlink():
                raise RuntimeError('Shortcut policy is not a regular file')
            disabled = root / 'nx300m-mod/DISABLED'
            if not disabled.exists():
                write_flushed(disabled, b'update in progress\n')
            elif not disabled.is_file() or disabled.is_symlink():
                raise RuntimeError('Disable marker is not a regular file')
            report['requested_startup_profile'] = 'service-only'
            save()
        changed = []
        for name, data in package.items():
            target = root / name
            same = target.is_file() and target.read_bytes() == data
            item = {'path': name, 'bytes': len(data), 'changed': not same}
            report['files'].append(item)
            if not same:
                target.parent.mkdir(parents=True, exist_ok=True)
                stage = target.with_name(target.name + '.next-' + stamp)
                write_flushed(stage, data)
                changed.append((name, target, stage, item))
        report['status'] = 'staged'
        save()
        volume(root, args.serial)
        # Publish the new bootstrap last. Keep old SD allocations available by
        # renaming old files instead of truncating their potentially mapped bytes.
        changed.sort(key=lambda item: item[0] == 'autoexec.sh')
        for name, target, stage, item in changed:
            if target.exists():
                # Preserve the executable basename so the RAM launcher's
                # old-service process scan still recognizes a mapped old ELF.
                retained = root / ('.nx300m-before-ram-' + stamp) / name
                if retained.exists():
                    raise RuntimeError(f'Previous-generation target already exists: {retained}')
                retained.parent.mkdir(parents=True, exist_ok=True)
                target.rename(retained)
                item['retained_old_file'] = retained.relative_to(root).as_posix()
            stage.rename(target)
            if target.read_bytes() != package[name]:
                raise RuntimeError(f'Published file readback differs: {name}')
            item['published'] = True
            save()
        for name, data in package.items():
            if (root / name).read_bytes() != data:
                raise RuntimeError(f'Final package readback differs: {name}')
        report['photos_after'] = photos(root, {'files': report['photos_before']})
        if args.restart_owned_service:
            request_owned_restart(root, args.serial, output, report)
        report['chkdsk_after_exit'] = check_filesystem(root, output, 'after')
        if args.service_only:
            volume(root, args.serial)
            # Retain the stop marker with the other old files rather than
            # deleting its allocation. This is the final activation step.
            disabled = root / 'nx300m-mod/DISABLED'
            retained = root / ('.nx300m-before-ram-' + stamp) / 'nx300m-mod/DISABLED'
            retained.parent.mkdir(parents=True, exist_ok=True)
            if retained.exists():
                raise RuntimeError('Retained disable marker already exists')
            disabled.rename(retained)
            if disabled.exists() or not (root / 'nx300m-mod/NO_PANEL_SHORTCUT').is_file():
                raise RuntimeError('Service-only startup policy readback failed')
            report['startup_profile'] = 'service-only; Custom and Fn listeners disabled'
            save()
        elif args.custom_shortcut:
            volume(root, args.serial)
            policy = root / 'nx300m-mod/NO_PANEL_SHORTCUT'
            if policy.exists() or policy.is_symlink():
                if policy.is_symlink() or not policy.is_file():
                    raise RuntimeError('Shortcut policy is not a regular file')
                retained = root / ('.nx300m-before-ram-' + stamp) / 'nx300m-mod/NO_PANEL_SHORTCUT'
                retained.parent.mkdir(parents=True, exist_ok=True)
                if retained.exists():
                    raise RuntimeError('Retained shortcut policy already exists')
                policy.rename(retained)
                custom_marker_removed = True
            if policy.exists() or policy.is_symlink():
                raise RuntimeError('Shortcut startup policy readback failed')
            report['startup_profile'] = 'Custom shortcut enabled; legacy Fn polling remains opt-in'
            save()
        report.update(status='deployed', changed_files=len(changed), readback_verified=len(package),
            photos_unchanged=len(report['photos_after']), finished_utc=datetime.now(timezone.utc).isoformat(),
            startup='MFW autoexec -> verified RAM stage; refuses to replace an old SD-backed service',
            active_control_markers=[name for name in ('DISABLED', 'NO_PANEL_SHORTCUT', 'RESTART_SERVICE', 'TRACE_HTTP')
                if (root / 'nx300m-mod' / name).exists()])
        save()
        (BASE / 'reader-deployment/latest.json').write_text(json.dumps({'report': str(report_path)}, indent=2), encoding='utf-8')
        print(json.dumps({k: report[k] for k in ('status', 'changed_files', 'readback_verified',
            'photos_unchanged', 'chkdsk_after_exit', 'camera_boot_verified', 'active_control_markers')}, indent=2), flush=True)
        print(f'Report: {report_path}', flush=True)
    except BaseException as exc:
        report.update(status='failed', error=str(exc))
        if args.service_only:
            try:
                volume(root, args.serial)
                disabled = root / 'nx300m-mod/DISABLED'
                if not disabled.exists():
                    write_flushed(disabled, b'service-only update failed\n')
                report['failed_update_left_disabled'] = disabled.is_file() and not disabled.is_symlink()
            except Exception as disable_error:
                report['failure_disable_error'] = str(disable_error)
        elif custom_marker_removed:
            try:
                volume(root, args.serial)
                policy = root / 'nx300m-mod/NO_PANEL_SHORTCUT'
                if not policy.exists():
                    write_flushed(policy, b'shortcut activation failed\n')
                report['failed_update_left_shortcut_disabled'] = policy.is_file() and not policy.is_symlink()
            except Exception as disable_error:
                report['failure_disable_error'] = str(disable_error)
        save()
        raise


if __name__ == '__main__':
    main()
