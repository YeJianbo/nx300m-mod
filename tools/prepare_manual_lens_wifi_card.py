"""Prepare a bounded Wi-Fi action or boot profile on the identified SD card."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import struct

from prepare_manual_lens_debug_card import PROJECT, volume, inventory, save


def shell_bytes(path):
    return path.read_bytes().replace(b'\r\n', b'\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--action', required=True, choices=('status', 'apply', 'restore'))
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--serial', default='LOCAL_REDACTED',
                        help='Expected volume serial of the explicitly selected H: card')
    parser.add_argument('--initialize', action='store_true',
                        help='Prepare the lens directory and replace a known older service entry')
    parser.add_argument('--repeat-on-boot', action='store_true',
                        help='Enable bounded apply at each fresh RAM session; no resident watcher')
    parser.add_argument('--expected-entry', type=Path,
                        help='Previously verified entry when migrating between template versions')
    args = parser.parse_args()
    if args.repeat_on_boot and args.action != 'apply':
        raise RuntimeError('Repeated boot application requires action=apply')
    if not re.fullmatch(r'[0-9A-Za-z-]{1,64}', args.run_id):
        raise RuntimeError('Invalid run identifier')
    serial = args.serial.upper()
    if not re.fullmatch(r'[0-9A-F]{8}', serial):
        raise RuntimeError('Expected eight-digit hexadecimal volume serial')
    root = Path('H:/')
    identity = volume(root, serial)
    prefix = 'nx300m-lens-debug/'
    if (not (root/prefix).is_dir() and not args.initialize) or (root/prefix/'DISABLED').exists():
        raise RuntimeError('Expected enabled lens diagnostic directory')
    old_entry = (root/'autoexec.sh').read_bytes()
    template = PROJECT/'mod/deployment/mfw/autoexec-lens-wifi-mem.sh'
    allowed = (shell_bytes(template), shell_bytes(PROJECT/'mod/deployment/mfw/autoexec-lens-debug.sh'))
    if args.initialize:
        allowed += (shell_bytes(PROJECT/'mod/deployment/mfw/autoexec-services.sh'),)
    if args.expected_entry is not None:
        allowed += (args.expected_entry.read_bytes(),)
    if old_entry not in allowed:
        raise RuntimeError('Unknown current autoexec; will not overwrite it')
    if (root/prefix/'WIFI_MEM_REQUEST').exists():
        raise RuntimeError('An unconsumed Wi-Fi request already exists')
    if (root/prefix/'logs'/args.run_id).exists():
        raise RuntimeError('Run identifier already used on card')
    build = PROJECT/'outputs/controls/manual-lens'
    evidence = json.loads((build/'wifi-mem-build-probe.json').read_text(encoding='utf-8'))
    binary = (build/'manual-lens-wifi-mem.arm').read_bytes()
    if (evidence['fixture_returncode'] != 0 or evidence['stderr'] or evidence['bytes'] != len(binary)
            or binary[:6] != b'\x7fELF\x01\x01' or struct.unpack_from('<H', binary, 18)[0] != 40):
        raise RuntimeError('ARM artifact or build evidence does not match')
    package = {
        prefix+'manual-lens-wifi-mem.arm': binary,
        prefix+'WIFI_MEM_RUN_ID': (args.run_id+'\n').encode('ascii'),
        prefix+'WIFI_MEM_PROFILE': b'manual-lens-wifi\n' if args.repeat_on_boot else b'stock\n',
    }
    if args.action != 'status':
        package[prefix+'WIFI_MEM_REQUEST'] = (args.run_id+' '+args.action+'\n').encode('ascii')
    package['autoexec.sh'] = shell_bytes(template)
    output = PROJECT/'outputs/deployment/manual-lens-debug'/('card-'+serial)/'updates'/args.run_id
    output.mkdir(parents=True, exist_ok=False)
    report = {
        'status': 'preparing', 'volume': identity, 'run_id': args.run_id, 'action': args.action,
        'boot_profile': 'manual-lens-wifi' if args.repeat_on_boot else 'stock',
        'time_utc': datetime.now(timezone.utc).isoformat(), 'files': [],
        'firmware_written': False, 'internal_storage_written': False, 'snapshot_modified': False,
        'camera_program_executed': False, 'physical_wifi_verified': False, 'color_patch_loaded': False,
        'restore': 'Explicit tool restore and verified [10,10]; power-cycle reversion not guaranteed',
    }
    report_path = output/'report.json'
    save(report_path, report)
    try:
        original = inventory(root)
        staged = {}
        for name, data in package.items():
            target = root/name
            if target.is_symlink() or not target.resolve().is_relative_to(root.resolve()):
                raise RuntimeError(f'Unexpected target: {target}')
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                previous = target.read_bytes()
                backup = output/'before'/name
                backup.parent.mkdir(parents=True, exist_ok=True)
                backup.write_bytes(previous)
                if backup.read_bytes() != previous:
                    raise RuntimeError(f'Backup differs: {name}')
            temporary = target.with_name(target.name+'.wifi-mem-next')
            with temporary.open('xb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if temporary.read_bytes() != data:
                raise RuntimeError(f'Staged readback differs: {name}')
            staged[name] = temporary
        volume(root, serial)
        if (root/'autoexec.sh').read_bytes() != old_entry:
            raise RuntimeError('Entry changed while staging')
        # Publish the boot entry last, after the tool and request are ready.
        for name, data in package.items():
            staged[name].replace(root/name)
            if (root/name).read_bytes() != data:
                raise RuntimeError(f'Published readback differs: {name}')
            report['files'].append({'path': name, 'bytes': len(data), 'readback_verified': True})
            save(report_path, report)
        untouched_before = [item for item in original if item['path'] not in package]
        untouched_after = [item for item in inventory(root) if item['path'] not in package]
        if untouched_after != untouched_before:
            raise RuntimeError('Unrelated card file metadata changed')
        volume(root, serial)
        report.update(status='sd_test_prepared_not_executed',
                      unrelated_file_metadata_unchanged=True,
                      unrelated_files=len(untouched_before),
                      finished_utc=datetime.now(timezone.utc).isoformat())
        save(report_path, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    except BaseException as error:
        report.update(status='failed', error=str(error))
        save(report_path, report)
        raise


if __name__ == '__main__':
    main()
