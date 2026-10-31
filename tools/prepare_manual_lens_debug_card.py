"""Back up the explicitly identified debug card; prepare its bounded lens probe.

All original user files are copied and verified before any card mutation.
No formatting, camera execution, firmware flashing or process injection occurs.
"""
import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import time

PROJECT = Path(__file__).resolve().parents[1]
BASE = PROJECT / 'outputs/deployment/manual-lens-debug'
OS_METADATA = {'System Volume Information', '$RECYCLE.BIN'}


def volume(root, serial):
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    api.GetDriveTypeW.restype = wintypes.UINT
    api.GetVolumeInformationW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
        wintypes.LPWSTR, wintypes.DWORD]
    api.GetVolumeInformationW.restype = wintypes.BOOL
    label, filesystem = ctypes.create_unicode_buffer(261), ctypes.create_unicode_buffer(261)
    number, length, flags = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD()
    if not api.GetVolumeInformationW(str(root), label, len(label), ctypes.byref(number),
            ctypes.byref(length), ctypes.byref(flags), filesystem, len(filesystem)):
        raise ctypes.WinError(ctypes.get_last_error())
    actual = f'{number.value:08X}'
    if api.GetDriveTypeW(str(root)) != 2 or filesystem.value.upper() not in ('FAT32', 'EXFAT') or actual != serial:
        raise RuntimeError(f'Unexpected card: {actual}, {filesystem.value}, {label.value}')
    return {'root': str(root), 'serial': actual, 'filesystem': filesystem.value, 'label': label.value}


def inventory(root):
    files = []
    for directory, directories, names in os.walk(root, onerror=lambda error: (_ for _ in ()).throw(error)):
        if Path(directory) == root:
            directories[:] = [name for name in directories if name not in OS_METADATA]
        for name in directories + names:
            if (Path(directory)/name).is_symlink():
                raise RuntimeError(f'Unexpected card link: {Path(directory)/name}')
        for name in names:
            path = Path(directory)/name
            stat = path.stat()
            files.append({'path': path.relative_to(root).as_posix(), 'bytes': stat.st_size,
                          'mtime_ns': stat.st_mtime_ns})
    return sorted(files, key=lambda item: item['path'])


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def backup_card(root, serial, output):
    identity = volume(root, serial)
    report_path = output/'backup-report.json'
    before = inventory(root)
    report = {'status': 'backing-up', 'volume': identity, 'files': [],
              'total_bytes': sum(item['bytes'] for item in before),
              'skipped_os_metadata': sorted(OS_METADATA), 'card_written': False}
    save(report_path, report)
    total = 0
    announce = time.monotonic()
    for item in before:
        source, target = root/item['path'], output/'backup'/item['path']
        target.parent.mkdir(parents=True, exist_ok=True)
        checksum = hashlib.sha256()
        if target.exists():
            expected = digest(source)
            if target.stat().st_size != item['bytes'] or digest(target) != expected:
                raise RuntimeError(f'Existing backup differs; will not overwrite: {target}')
        else:
            stage = target.with_name(target.name+'.copying')
            with source.open('rb') as incoming, stage.open('xb') as outgoing:
                while block := incoming.read(1024*1024):
                    checksum.update(block)
                    outgoing.write(block)
                    if time.monotonic()-announce >= 8:
                        print(f'Backing up {item["path"]}: {total+incoming.tell():,}/{report["total_bytes"]:,} bytes', flush=True)
                        announce = time.monotonic()
                outgoing.flush()
                os.fsync(outgoing.fileno())
            expected = checksum.hexdigest()
            if stage.stat().st_size != item['bytes'] or digest(stage) != expected:
                raise RuntimeError(f'Backup verification failed: {item["path"]}')
            stage.rename(target)
            shutil.copystat(source, target)
        report['files'].append({**item, 'sha256': expected})
        total += item['bytes']
    if volume(root, serial) != identity or inventory(root) != before:
        raise RuntimeError('Source card contents or identity changed during backup')
    report.update(status='verified', finished_utc=datetime.now(timezone.utc).isoformat(),
                  verified_files=len(before), verified_bytes=total)
    save(report_path, report)
    print(f'Backup verified: {len(before)} files, {total:,} bytes; {report_path}', flush=True)


def deploy_card(root, serial, output):
    identity = volume(root, serial)
    backup = json.loads((output/'backup-report.json').read_text(encoding='utf-8'))
    if backup['status'] != 'verified' or backup['volume'] != identity:
        raise RuntimeError('A verified full backup of this card is required')
    original = [{key: item[key] for key in ('path', 'bytes', 'mtime_ns')} for item in backup['files']]
    if inventory(root) != original:
        raise RuntimeError('Card contents changed since the backup; nothing was deployed')
    probe = PROJECT/'outputs/controls/manual-lens/manual-lens-probe.arm'
    binary = probe.read_bytes()
    if binary[:6] != b'\x7fELF\x01\x01' or struct.unpack_from('<H', binary, 18)[0] != 40:
        raise RuntimeError('Expected a little-endian ELF32 ARM diagnostic')
    prefix = 'nx300m-lens-debug/'
    sources = {
        'autoexec.sh': PROJECT/'mod/deployment/mfw/autoexec-lens-debug.sh',
        prefix+'manual-lens-probe.arm': probe,
        prefix+'README.md': PROJECT/'docs/MANUAL_LENS_DEBUG_CARD.md',
        prefix+'candidates/manual-lens-cs.so': PROJECT/'outputs/controls/manual-lens/manual-lens-cs.so',
        prefix+'candidates/manual_lens_release.sh': PROJECT/'mod/deployment/manual_lens_release.sh',
    }
    package = {}
    for name, source in sources.items():
        data = source.read_bytes()
        if source.suffix in ('.sh', '.md'):
            data = data.decode('utf-8').replace('\r\n', '\n').encode('utf-8')
        package[name] = data
        target = root/name
        if target.is_symlink() or not target.resolve().is_relative_to(root.resolve()):
            raise RuntimeError(f'Unexpected card target: {target}')
        if target.exists() and name != 'autoexec.sh':
            raise RuntimeError(f'Will not overwrite an existing diagnostic: {target}')
    report = {'status': 'preparing', 'volume': identity,
              'backup_report': str(output/'backup-report.json'), 'files': [], 'retained': [],
              'firmware_flashed': False, 'camera_code_executed': False,
              'camera_boot_verified': False, 'color_patch_loaded': False,
              'startup': 'three bounded read-only snapshots; no service, Custom or SO injection'}
    report_path = output/'deployment-report.json'
    save(report_path, report)
    try:
        # Prepare and read back every file before publishing the boot entry.
        staged = {}
        for name, data in package.items():
            target = root/name
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name+'.lens-debug-next')
            with temporary.open('xb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if temporary.read_bytes() != data:
                raise RuntimeError(f'Stage readback differs: {name}')
            staged[name] = temporary
        volume(root, serial)
        # Remove the old upgrade filename from the root without deleting it.
        # Backup checksums establish the identity of each file being renamed.
        by_name = {item['path']: item for item in backup['files']}
        for name in ('nx300m.bin', 'autoexec.sh'):
            source = root/name
            if source.exists():
                if name not in by_name or digest(source) != by_name[name]['sha256']:
                    raise RuntimeError(f'Original changed before retention: {name}')
                target = root/'.nx300m-debug-originals'/name
                if target.exists() or not target.resolve().is_relative_to(root.resolve()):
                    raise RuntimeError(f'Unexpected original-file destination: {target}')
                target.parent.mkdir(parents=True, exist_ok=True)
                source.rename(target)
                report['retained'].append({'source': name, 'destination': target.relative_to(root).as_posix()})
        for name in sorted(package, key=lambda value: value == 'autoexec.sh'):
            staged[name].rename(root/name)
            if (root/name).read_bytes() != package[name]:
                raise RuntimeError(f'Published readback differs: {name}')
            report['files'].append({'path': name, 'bytes': len(package[name]), 'readback_verified': True})
            save(report_path, report)
        changed_paths = {item['source']: item['destination'] for item in report['retained']}
        for item in original:
            path = root/changed_paths.get(item['path'], item['path'])
            stat = path.stat()
            if stat.st_size != item['bytes'] or stat.st_mtime_ns != item['mtime_ns']:
                raise RuntimeError(f'Original metadata changed: {item["path"]}')
        check = subprocess.run(['chkdsk', root.drive], capture_output=True, timeout=60)
        (output/'chkdsk-after.stdout.txt').write_bytes(check.stdout)
        (output/'chkdsk-after.stderr.txt').write_bytes(check.stderr)
        report['chkdsk_after_exit'] = check.returncode
        if check.returncode:
            raise RuntimeError(f'Read-only chkdsk after deployment exit {check.returncode}')
        volume(root, serial)
        report.update(status='deployed', finished_utc=datetime.now(timezone.utc).isoformat(),
                      original_files_retained=len(original),
                      original_metadata_unchanged=True,
                      original_content_evidence='checksums verified during full backup; not rehashed after deployment',
                      dcim_files=sum(item['path'].startswith('DCIM/') for item in original))
        save(report_path, report)
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    except BaseException as error:
        report.update(status='failed', error=str(error))
        save(report_path, report)
        # This tool never executes the card. Preserve partial results and make
        # sure a failed new deployment does not leave its diagnostic enabled.
        entry = root/'autoexec.sh'
        if entry.is_file() and entry.read_bytes() == package['autoexec.sh']:
            disabled = root/'autoexec.sh.lens-debug-disabled'
            if not disabled.exists():
                entry.rename(disabled)
                report['failed_entry_disabled'] = True
                save(report_path, report)
        raise


def update_card(root, serial, output, run_id, expected_entry, expected_probe=None, enable_release=False):
    """Update only the bounded diagnostic, run identifier and instructions."""
    identity = volume(root, serial)
    backup = json.loads((output/'backup-report.json').read_text(encoding='utf-8'))
    if backup['status'] != 'verified' or backup['volume'] != identity:
        raise RuntimeError('A verified backup of this card is required')
    expected = expected_entry.read_bytes()
    if (root/'autoexec.sh').read_bytes() != expected:
        raise RuntimeError('Current entry differs from the captured entry; nothing updated')
    probe = PROJECT/'outputs/controls/manual-lens/manual-lens-probe.arm'
    if (root/'nx300m-lens-debug/manual-lens-probe.arm').read_bytes() != (expected_probe or probe).read_bytes():
        raise RuntimeError('Diagnostic probe differs; nothing updated')
    binary = probe.read_bytes()
    if binary[:6] != b'\x7fELF\x01\x01' or struct.unpack_from('<H', binary, 18)[0] != 40:
        raise RuntimeError('Expected a little-endian ELF32 ARM diagnostic')
    package = {
        'nx300m-lens-debug/manual-lens-probe.arm': binary,
        'nx300m-lens-debug/RUN_ID': (run_id+'\n').encode('ascii'),
        'nx300m-lens-debug/README.md': (PROJECT/'docs/MANUAL_LENS_DEBUG_CARD.md').read_bytes(),
        'autoexec.sh': (PROJECT/'mod/deployment/mfw/autoexec-lens-debug.sh').read_bytes(),
    }
    action_path = root/'nx300m-lens-debug/ENABLE_NO_LENS_RELEASE'
    if action_path.exists() or action_path.is_symlink():
        raise RuntimeError('A no-lens preference request is already pending; inspect it before replacing')
    if enable_release:
        package['nx300m-lens-debug/ENABLE_NO_LENS_RELEASE'] = (run_id+'\n').encode('ascii')
    # Publish the entry only after all of its inputs and optional request exist.
    package['autoexec.sh'] = package.pop('autoexec.sh')
    package = {name: data if name.endswith('.arm') else data.replace(b'\r\n', b'\n') for name, data in package.items()}
    before = inventory(root)
    evidence = output/'updates'/run_id
    evidence.mkdir(parents=True, exist_ok=False)
    report = {'status': 'preparing', 'volume': identity, 'run_id': run_id,
              'files': [], 'card_written': False, 'firmware_flashed': False,
              'camera_preference_action_requested': 'enable_no_lens_release' if enable_release else None,
              'color_patch_loaded': False, 'camera_execution_verified': False}
    report_path = evidence/'report.json'
    save(report_path, report)
    staged = {}
    try:
        for name, data in package.items():
            target = root/name
            if target.is_symlink() or not target.resolve().is_relative_to(root.resolve()):
                raise RuntimeError(f'Unexpected card path: {target}')
            if target.exists():
                previous = evidence/'before'/name
                previous.parent.mkdir(parents=True, exist_ok=True)
                previous.write_bytes(target.read_bytes())
                if previous.read_bytes() != target.read_bytes():
                    raise RuntimeError(f'Previous file backup differs: {name}')
            temporary = target.with_name(target.name+'.lens-debug-next')
            with temporary.open('xb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            report['card_written'] = True
            if temporary.read_bytes() != data:
                raise RuntimeError(f'Stage readback differs: {name}')
            staged[name] = temporary
        volume(root, serial)
        if (root/'autoexec.sh').read_bytes() != expected:
            raise RuntimeError('Entry changed during update')
        # Publish the boot entry after its run identifier and instructions.
        for name, data in package.items():
            staged[name].replace(root/name)
            if (root/name).read_bytes() != data:
                raise RuntimeError(f'Published readback differs: {name}')
            report['files'].append({'path': name, 'bytes': len(data), 'readback_verified': True})
            save(report_path, report)
        unchanged_before = [item for item in before if item['path'] not in package]
        unchanged_after = [item for item in inventory(root) if item['path'] not in package]
        if unchanged_before != unchanged_after or volume(root, serial) != identity:
            raise RuntimeError('Other card metadata or identity changed during update')
        report.update(status='updated', other_file_metadata_unchanged=True,
                      finished_utc=datetime.now(timezone.utc).isoformat())
        save(report_path, report)
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    except BaseException as error:
        report.update(status='failed', error=str(error))
        save(report_path, report)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--drive', choices=['H:'], required=True)
    parser.add_argument('--serial', required=True)
    parser.add_argument('--phase', choices=['backup', 'deploy', 'update'], default='backup')
    parser.add_argument('--run-id')
    parser.add_argument('--expected-entry', type=Path)
    parser.add_argument('--expected-probe', type=Path)
    parser.add_argument('--enable-no-lens-release', action='store_true')
    args = parser.parse_args()
    serial = args.serial.replace('-', '').upper()
    if len(serial) != 8 or any(c not in '0123456789ABCDEF' for c in serial):
        parser.error('serial must be an eight-digit volume serial')
    if args.phase == 'update' and (not args.run_id or len(args.run_id) > 64 or
            any(c not in '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-' for c in args.run_id)
            or not args.expected_entry):
        parser.error('update requires a safe --run-id and a captured --expected-entry')
    if args.enable_no_lens_release and args.phase != 'update':
        parser.error('--enable-no-lens-release requires --phase update')
    output = BASE/('card-'+serial)
    output.mkdir(parents=True, exist_ok=True)
    if args.phase == 'backup':
        backup_card(Path(args.drive+'/'), serial, output)
    elif args.phase == 'deploy':
        deploy_card(Path(args.drive+'/'), serial, output)
    else:
        update_card(Path(args.drive+'/'), serial, output, args.run_id, args.expected_entry, args.expected_probe,
                    args.enable_no_lens_release)


if __name__ == '__main__':
    main()
