"""Deploy the existing verified connected package without LUT modules."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import zipfile

from prepare_manual_lens_debug_card import PROJECT, volume, inventory, save

BASE = PROJECT/'outputs/deployment/mfw-connected'
OUT = PROJECT/'outputs/deployment/outing-stable'


def package():
    archive = BASE/'nx300m-connected.zip'
    proof = json.loads((BASE/'arm-vm/report.json').read_text(encoding='utf-8'))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if not proof.get('passed') or proof.get('archive_sha256') != digest:
        raise RuntimeError('Stable archive differs from its passing ARM evidence')
    files = {}
    with zipfile.ZipFile(archive) as source:
        if source.testzip() is not None:
            raise RuntimeError('Stable archive CRC failed')
        for entry in source.infolist():
            name = entry.filename
            parts = PurePosixPath(name)
            if (entry.is_dir() or name in files or parts.is_absolute() or '..' in parts.parts or
                    '\\' in name or ':' in name or not
                    (name == 'autoexec.sh' or name.startswith(('nx300m-mod/', 'nx300m-lens-debug/')))):
                raise RuntimeError('Unexpected stable package path: '+name)
            files[name] = source.read(entry)
    if any('lut' in name.lower() or 'smartfilter' in name.lower() for name in files):
        raise RuntimeError('Stable package contains LUT assets')
    if b'NX_REMOTE_CUSTOM_KEY=0 NX_REMOTE_PANEL_SHORTCUT=0' not in files['autoexec.sh']:
        raise RuntimeError('Stable package must keep MOD key listeners disabled')
    return files, dict(archive=str(archive), archive_sha256=digest,
                       source_arm_verified=True, lut_modules=False, mod_key_listeners=False,
                       firmware_written=False)


def deploy(root, serial):
    files, report = package()
    identity = volume(root, serial)
    stamp = 'outing-no-lut-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    output = OUT/('card-'+serial)/stamp
    output.mkdir(parents=True, exist_ok=False)
    files['nx300m-mod/RESTART_SERVICE'] = (stamp+'\n').encode('ascii')
    original = inventory(root)
    report.update(status='preparing', volume=identity, replaced_files=[])
    save(output/'report.json', report)
    staged = []
    try:
        for name, data in files.items():
            target = root/name
            if target.is_symlink() or not target.resolve().is_relative_to(root.resolve()):
                raise RuntimeError('Unexpected card target: '+name)
            if target.exists():
                if not target.is_file():
                    raise RuntimeError('Card target is not a file: '+name)
                previous = target.read_bytes()
                if previous == data:
                    continue
                backup = output/'before'/name
                backup.parent.mkdir(parents=True, exist_ok=True)
                backup.write_bytes(previous)
                if backup.read_bytes() != previous:
                    raise RuntimeError('Backup differs: '+name)
            target.parent.mkdir(parents=True, exist_ok=True)
            pending = target.with_name(target.name+'.outing-next')
            with pending.open('xb') as stream:
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
            if pending.read_bytes() != data:
                raise RuntimeError('Staged readback differs: '+name)
            staged.append((name, pending, target))
        volume(root, serial)
        # Publish startup last, after all of its dependencies are present.
        for name, pending, target in sorted(staged, key=lambda row: row[0] == 'autoexec.sh'):
            os.replace(pending, target)
            report['replaced_files'].append(name)
        for name, data in files.items():
            if (root/name).read_bytes() != data:
                raise RuntimeError('Final card readback differs: '+name)
        unchanged = [row for row in original if row['path'] not in files]
        after = [row for row in inventory(root) if row['path'] not in files]
        if unchanged != after:
            raise RuntimeError('Unrelated card file metadata changed')
        volume(root, serial)
        report.update(status='deployed_not_executed', readback_verified=len(files),
                      unrelated_files=len(unchanged), unrelated_file_metadata_unchanged=True)
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        save(output/'report.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print('DEPLOYMENT_REPORT='+str(output/'report.json'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial')
    parser.add_argument('--drive', default='H')
    args = parser.parse_args()
    if args.serial:
        if len(args.drive) != 1 or not args.drive.isalpha():
            parser.error('drive must be one letter')
        deploy(Path(args.drive.upper()+':/'), args.serial.upper().replace('-', ''))
    else:
        files, proof = package()
        print(json.dumps(dict(**proof, prepared_files=len(files)), ensure_ascii=False, indent=2))
