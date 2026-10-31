"""Deploy the ARM-checked combined boot entry to the explicitly identified card."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import zipfile

from prepare_manual_lens_debug_card import PROJECT, volume, inventory, save

BASE = PROJECT/'outputs/deployment/mfw-connected'


def flushed(path, data):
    with path.open('xb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    if path.read_bytes() != data:
        raise RuntimeError('Readback differs: '+str(path))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial', required=True)
    args = parser.parse_args()
    root = Path('H:/')
    serial = args.serial.upper()
    identity = volume(root, serial)
    verification = json.loads((BASE/'arm-vm/report.json').read_text(encoding='utf-8'))
    archive = BASE/'nx300m-connected.zip'
    archive_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
    if not verification['passed'] or verification['archive_sha256'] != archive_hash:
        raise RuntimeError('Combined package differs from the passing ARM boot probe')
    current = (root/'autoexec.sh').read_bytes()
    known = [
        (PROJECT/'mod/deployment/mfw'/name).read_bytes().replace(b'\r\n', b'\n')
        for name in ('autoexec-lens-wifi-mem.sh', 'autoexec-connected.sh', 'autoexec-services.sh')
    ]
    if current not in known:
        raise RuntimeError('Unknown current autoexec; will not replace it')
    package = {}
    with zipfile.ZipFile(archive) as source:
        if source.testzip() is not None:
            raise RuntimeError('Combined archive is corrupt')
        for entry in source.infolist():
            name = entry.filename
            parts = PurePosixPath(name)
            if (entry.is_dir() or name in package or parts.is_absolute() or '..' in parts.parts
                    or '\\' in name or ':' in name or
                    not (name == 'autoexec.sh' or name.startswith(('nx300m-mod/', 'nx300m-lens-debug/')))):
                raise RuntimeError('Unexpected package path: '+name)
            package[name] = source.read(entry)
    run = package['nx300m-lens-debug/WIFI_MEM_RUN_ID'].decode('ascii').strip()
    # Reconcile only the exact owned RAM daemon. The production staging helper
    # records this marker in RAM, so repeated MFW entry does not restart again.
    package['nx300m-mod/RESTART_SERVICE'] = (run+'\n').encode('ascii')
    output = BASE/'reader-deployment'/('card-'+serial)/run
    output.mkdir(parents=True, exist_ok=False)
    retained_prefix = '.nx300m-before-'+run+'/'
    report = {
        'status': 'preparing', 'volume': identity, 'archive_sha256': archive_hash,
        'files': [], 'time_utc': datetime.now(timezone.utc).isoformat(),
        'firmware_written': False, 'internal_storage_written': False, 'snapshot_modified': False,
        'stock_camera_restart': False, 'camera_code_executed_by_deployment': False,
        'physical_phone_connection_verified': False,
        'owned_ram_service_restart_requested': True,
    }
    report_path = output/'report.json'
    save(report_path, report)
    try:
        original = inventory(root)
        # Preserve the most recent physical Wi-Fi result before changing run ID.
        logs = root/'nx300m-lens-debug/logs'
        for path in logs.glob('wifi-mem-20261027-03/*'):
            if path.is_file():
                target = output/'camera-before'/path.name
                target.parent.mkdir(parents=True, exist_ok=True)
                data = path.read_bytes()
                target.write_bytes(data)
                if target.read_bytes() != data:
                    raise RuntimeError('Camera log backup differs')
        retired = []
        for name in ('nx300m-lens-debug/WIFI_MEM_REQUEST', 'nx300m-lens-debug/DISABLED', 'nx300m-mod/DISABLED'):
            if (root/name).exists():
                retired.append(name)
        changed = []
        for name, data in package.items():
            target = root/name
            if target.is_symlink() or not target.resolve().is_relative_to(root.resolve()):
                raise RuntimeError('Unexpected card target: '+name)
            same = target.is_file() and target.read_bytes() == data
            report['files'].append({'path': name, 'bytes': len(data), 'changed': not same})
            if same:
                continue
            if target.exists():
                old = target.read_bytes()
                backup = output/'before'/name
                backup.parent.mkdir(parents=True, exist_ok=True)
                backup.write_bytes(old)
                if backup.read_bytes() != old:
                    raise RuntimeError('Backup differs: '+name)
            target.parent.mkdir(parents=True, exist_ok=True)
            stage = target.with_name(target.name+'.connected-next')
            flushed(stage, data)
            changed.append((name, target, stage))
        volume(root, serial)
        if (root/'autoexec.sh').read_bytes() != current:
            raise RuntimeError('Startup entry changed during staging')
        for name in retired:
            target = root/name
            retained = root/(retained_prefix+name)
            retained.parent.mkdir(parents=True, exist_ok=True)
            if retained.exists():
                raise RuntimeError('Retained marker already exists')
            target.rename(retained)
        for name, target, stage in sorted(changed, key=lambda item: item[0] == 'autoexec.sh'):
            if target.exists():
                retained = root/(retained_prefix+name)
                retained.parent.mkdir(parents=True, exist_ok=True)
                if retained.exists():
                    raise RuntimeError('Retained file already exists')
                target.rename(retained)
            stage.rename(target)
            if target.read_bytes() != package[name]:
                raise RuntimeError('Published readback differs: '+name)
        for name, data in package.items():
            if (root/name).read_bytes() != data:
                raise RuntimeError('Final readback differs: '+name)
        unaffected_before = [p for p in original if p['path'] not in package and p['path'] not in retired]
        unaffected_after = [p for p in inventory(root) if p['path'] not in package and p['path'] not in retired
                            and not p['path'].startswith(retained_prefix)]
        if unaffected_before != unaffected_after:
            raise RuntimeError('Unrelated file metadata changed')
        volume(root, serial)
        report.update(status='deployed_not_executed', readback_verified=len(package),
                      changed_files=len(changed), retired_markers=retired,
                      unrelated_files=len(unaffected_before), unrelated_file_metadata_unchanged=True,
                      retained_previous_files=retained_prefix,
                      finished_utc=datetime.now(timezone.utc).isoformat())
        save(report_path, report)
        print(json.dumps({k:v for k,v in report.items() if k != 'files'}, ensure_ascii=False, indent=2))
    except BaseException as error:
        report.update(status='failed', error=str(error))
        save(report_path, report)
        raise


if __name__ == '__main__':
    main()
