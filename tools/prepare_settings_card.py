"""Prepare an existing MOD card for the two-module settings firmware test.

Only stages files on a removable card; never invokes a firmware updater.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import zipfile

from build_settings_candidate import OUT, ROOT, SD_FILES
from build_slp_candidate import layout
from nx300m_installer import safe_path, volume, write_checked
import probe_stock_slp as probe


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def check_parser(firmware):
    probe.OUT = OUT/'parser'
    stage, _, metadata = probe.prepare(firmware)
    result = probe.run_case('candidate-parser', firmware, 'NX300M', stage)
    if (result['exit_code'] != 0 or not result['crc_helper_reached']
            or not result['crc_update_reached']):
        raise ValueError('Original read-only parser rejected the settings candidate')
    if (digest(probe.STOCK) != metadata['stock_sha256_verified']
            or digest(ROOT/'outputs/deployment/stock/usr/sbin/fw_upgrade') != metadata['original_elf_sha256']
            or digest(firmware) != metadata['selected_firmware_sha256']):
        raise ValueError('Parser input identity changed')
    report = {'candidate_parser_passed': True, 'candidate_sha256': digest(firmware),
              'case': result, 'stock_input_and_original_elf_unchanged': True,
              'scope': 'Read-only original ARM SLP parser and CRC; no upgrade main or flash writer',
              'controls': 'Existing stock/model/CRC controls reused, not rerun',
              'updater_executed': False, 'physical_camera_verified': False}
    save(probe.OUT/'report.json', report)
    return report


def card_inventory(card):
    entries = {}
    for directory, dirs, files in os.walk(card, followlinks=False):
        dirs[:] = [name for name in dirs if name != 'System Volume Information']
        for name in files:
            path = Path(directory)/name
            relative = path.relative_to(card).as_posix()
            safe_path(card, relative)
            stat = path.stat()
            entries[relative] = [stat.st_size, stat.st_mtime_ns]
    return entries


def deploy(card, serial, firmware, parser_report):
    identity = volume(str(card))
    if identity['serial'] != serial or identity['filesystem'].upper() not in ('FAT32', 'EXFAT'):
        raise ValueError('Card identity or filesystem differs')
    if not safe_path(card, 'nx300m-mod/mfw-service-entry.sh').is_file():
        raise ValueError('This companion requires an existing full MOD installation')
    disabled = safe_path(card, 'nx300m-mod/DISABLED')
    if disabled.exists():
        raise ValueError('MOD autostart is disabled; preserve the marker and check before deploying')
    build = json.loads((OUT/'report.json').read_text(encoding='utf-8'))
    comparison = json.loads((OUT/'ubifs/comparison.json').read_text(encoding='utf-8'))
    if not build.get('rootfs_readback_passed') or not comparison.get('passed'):
        raise ValueError('Rootfs verification is missing')
    for source, expected in build['source_identity'].items():
        if digest(ROOT/source) != expected:
            raise ValueError('Built source changed: '+source)
    if parser_report['candidate_sha256'] != digest(firmware):
        raise ValueError('Firmware no longer matches parser input')
    parsed = layout(firmware)
    if parsed['version'] != '1.15' or parsed['size'] != build['firmware_bytes']:
        raise ValueError('Unexpected firmware version or length')
    with zipfile.ZipFile(OUT/'nx300m-settings-sd-update.zip') as archive:
        if sorted(archive.namelist()) != sorted(SD_FILES):
            raise ValueError('Unexpected companion file list')
        writes = {name: archive.read(name) for name in SD_FILES}
    for name, source in SD_FILES.items():
        if writes[name] != (ROOT/source).read_bytes():
            raise ValueError('Companion differs from source: '+name)
    # Stock initramfs invokes fw_upgrade nx300m.bin; the local .slp suffix
    # describes its format, rather than the on-card update filename.
    writes['nx300m.bin'] = firmware.read_bytes()
    writes['nx300m-mod/RESTART_SERVICE'] = b'settings-firmware-test\n'
    required = sum(len(data) for data in writes.values())
    if shutil.disk_usage(card).free < required + 16*1024*1024:
        raise ValueError('Insufficient card free space')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup = OUT/('card-'+stamp)
    backup.mkdir()
    before = card_inventory(card)
    save(backup/'inventory-before.json', before)
    records = {}
    for name, data in writes.items():
        target = safe_path(card, name)
        if target.exists() and not target.is_file():
            raise ValueError('Target is not a regular file: '+name)
        old = target.read_bytes() if target.is_file() else None
        if old is not None:
            dest = safe_path(backup/'before', name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            write_checked(dest, old)
        records[name] = {'before_sha256': hashlib.sha256(old).hexdigest() if old is not None else None,
                         'installed_sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
    for prefix in ('nx300m-mod/.state', 'nx300m-mod/logs'):
        for name in before:
            if name.startswith(prefix+'/'):
                source = safe_path(card, name)
                target = safe_path(backup/'before', name)
                target.parent.mkdir(parents=True, exist_ok=True)
                write_checked(target, source.read_bytes())
    save(backup/'backup.json', {'card': identity, 'files': records})
    report = {'status': 'prepared', 'checked_at': datetime.now(timezone.utc).isoformat(),
              'card': identity, 'backup': str(backup), 'files': records,
              'candidate_parser_report': str(OUT/'parser/report.json'),
              'updater_executed': False, 'physical_camera_verified': False,
              'release_ready': False, 'written': []}
    try:
        for name, data in writes.items():
            if volume(str(card)) != identity:
                raise ValueError('Card identity changed during deployment')
            target = safe_path(card, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            write_checked(target, data)
            report['written'].append(name)
        for name, record in records.items():
            if digest(safe_path(card, name)) != record['installed_sha256']:
                raise ValueError('Card readback mismatch: '+name)
        after = card_inventory(card)
        unrelated = sorted(name for name in before.keys() | after.keys()
                           if name not in writes and before.get(name) != after.get(name))
        save(backup/'inventory-after.json', after)
        report['unrelated_file_changes'] = unrelated
        if unrelated:
            raise ValueError('Unexpected unrelated card changes: '+repr(unrelated))
        report.update(status='card_staged_readback_passed', card_readback_passed=True,
                      firmware_card_path=str(card/'nx300m.bin'),
                      card_file_count_before=len(before), card_file_count_after=len(after))
    except Exception as error:
        report.update(status='incomplete', error=str(error))
        raise
    finally:
        save(backup/'result.json', report)
        save(OUT/'card-deployment.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-stock-parser', action='store_true')
    parser.add_argument('--card', type=Path)
    parser.add_argument('--serial')
    args = parser.parse_args()
    if bool(args.card) != bool(args.serial):
        parser.error('--card and --serial must be supplied together')
    firmware = OUT/'firmware/nx300m-settings-1.15-candidate.slp'
    if args.check_stock_parser:
        checked = check_parser(firmware)
    else:
        checked = json.loads((OUT/'parser/report.json').read_text(encoding='utf-8'))
    if not checked.get('candidate_parser_passed'):
        raise ValueError('Read-only parser verification missing')
    if args.card:
        result = deploy(args.card, args.serial, firmware, checked)
        print(json.dumps({key: result[key] for key in ('status', 'backup', 'firmware_card_path',
                                                      'card_readback_passed', 'unrelated_file_changes')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
