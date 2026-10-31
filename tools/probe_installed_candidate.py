"""Install the current candidate only into the existing disposable ARM VM.

Reuses the established original-app lifecycle probe. No host camera namespace,
physical device, stock hibernation script or firmware updater is executed.
"""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import shutil
import stat
import uuid

import probe_app_vm as lifecycle
from build_stock_app_loader import PERSISTENT_HOOKS

PROJECT = Path(__file__).resolve().parents[1]
OUT = PROJECT / 'outputs/deployment/app-loader/installed-vm'
CANDIDATE = PROJECT / 'outputs/deployment/app-loader/boot-chain/persistent-candidate'
SD = '/mnt/mmc/nx300m-mod'


def save(name, data):
    path = OUT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def prepare():
    from PIL import Image
    OUT.mkdir(parents=True, exist_ok=True)
    overlay = OUT / 'overlay'
    overlay.mkdir(exist_ok=True)
    candidate = json.loads((CANDIDATE / 'report.json').read_text(encoding='utf-8'))
    hooks = candidate['hooks']
    if {Path(row['destination']).name for row in hooks} != set(PERSISTENT_HOOKS):
        raise ValueError('Persistent candidate module list is stale')
    payload = {}
    for row in candidate['rootfs_changes']:
        relative = row['destination'].lstrip('/')
        data = (CANDIDATE / 'rootfs' / relative).read_bytes()
        payload[relative] = (data, int(row['metadata']['mode'], 8))
    for row in hooks:
        if payload[row['destination'].lstrip('/')][0] != (PROJECT / row['source']).read_bytes():
            raise ValueError('Persistent candidate differs from current module: ' + row['destination'])
    bundle = PROJECT / 'outputs/deployment/sd-bundle/nx300m-mod-sd.tar.gz'
    with tarfile.open(bundle) as package:
        for member in package:
            name = Path(member.name)
            if name.is_absolute() or '..' in name.parts or name.parts[0] != 'nx300m-mod':
                raise ValueError('Unexpected SD archive path')
            if member.isfile():
                payload['mnt/mmc/' + member.name] = (package.extractfile(member).read(), member.mode)
            elif not member.isdir():
                raise ValueError('SD archive must not include links')
    for name in (*lifecycle.SCRIPTS, 'bin/nx-remote-controller.arm', 'bin/nx-quickshare-armv7', 'web_root/index.html'):
        if SD.lstrip('/') + '/' + name not in payload:
            raise ValueError('Incomplete SD service bundle: ' + name)
    old_launch = ('LD_PRELOAD=/arm-vm/lib/gpio_preview.so:/arm-vm/lib/device_preview.so:/arm-vm/lib/capture_boot.so:/usr/lib/nx300m-mod/service-loader.so \\\n'
                  ' "$APP" > "$OUT/app.stdout" 2> "$OUT/app.stderr" &')
    # This probe explicitly opts into all modules; shipping defaults stay stock.
    new_launch = ('printf \'full\\n\' > /mnt/mmc/nx300m-mod/launch-profile\n'
                  'unset HIB\n'
                  'export LD_PRELOAD=/arm-vm/lib/gpio_preview.so:/arm-vm/lib/device_preview.so:/arm-vm/lib/capture_boot.so\n'
                  '/usr/bin/nx300m-camera-launch > "$OUT/app.stdout" 2> "$OUT/app.stderr" &')
    if lifecycle.PROBE.count(old_launch) != 1:
        raise ValueError('Original lifecycle launch changed; update the bounded replacement')
    script = lifecycle.PROBE.replace(old_launch, new_launch, 1)
    identity_note = " printf 'VM_APP_REMOTE %s pid=%s exe=%s inode=%s\\n'"
    if script.count(identity_note) != 1:
        raise ValueError('Original lifecycle identity check changed')
    script = script.replace(identity_note,
                            ' remote_count=0\n'
                            ' for remote_entry in /proc/[0-9]*/exe; do\n'
                            '  observed=$(readlink "$remote_entry" 2>/dev/null || :)\n'
                            '  [ "$observed" != "$REMOTE" ] || remote_count=$((remote_count+1))\n'
                            ' done\n'
                            ' [ "$remote_count" -eq 1 ]\n' + identity_note, 1)
    script = script.replace('remote_identity initial\n',
                            'cat "/proc/$app_pid/maps" > "$OUT/candidate.maps"\n'
                            'cat /var/run/nx300m-firmware-mod-ready > "$OUT/firmware.ready"\n'
                            'remote_identity initial\n', 1)
    fixture = 'NXMOD_TEST_' + uuid.uuid4().hex[:12] + '.JPG'
    edit_probe = r'''
phase generated-JPEG-backup-restore
fixture=/mnt/mmc/DCIM/@FIXTURE@
backup=/mnt/mmc/DCIM/.@FIXTURE@.nxoriginal
[ ! -e "$backup" ]
(set -C; cat /arm-vm/install-fixture/original.jpg > "$fixture")
for version in first second; do
 size=$("$R" wc -c < "/arm-vm/install-fixture/$version.jpg")
 { printf 'POST /api/v1/gallery/upload?p=@FIXTURE@&require_backup=1 HTTP/1.0\r\nHost: 127.0.0.1:8781\r\nContent-Type: image/jpeg\r\nContent-Length: %s\r\nConnection: close\r\n\r\n' "$size";
   "$R" cat "/arm-vm/install-fixture/$version.jpg"; } |
  "$R" nc -w 8 127.0.0.1 8781 > "$OUT/upload-$version.http"
 "$R" sed '1,/^\r$/d' "$OUT/upload-$version.http" > "$OUT/upload-$version.json"
 grep -q '"ok":true' "$OUT/upload-$version.json"
 "$R" cmp "$fixture" "/arm-vm/install-fixture/$version.jpg"
 "$R" cmp "$backup" /arm-vm/install-fixture/original.jpg
done
"$R" wget -q -T 8 --post-data '' -O "$OUT/restore.json" \
 'http://127.0.0.1:8781/api/v1/gallery/restore?p=@FIXTURE@'
grep -q '"ok":true' "$OUT/restore.json"
"$R" cmp "$fixture" /arm-vm/install-fixture/original.jpg
"$R" cmp "$backup" /arm-vm/install-fixture/original.jpg
printf 'original backup retained through two uploads; restored bytes equal original\n' > "$OUT/edit-check.txt"
"$R" rm "$fixture" "$backup"
'''.replace('@FIXTURE@', fixture)
    if script.count('phase manual-stop\n') != 1:
        raise ValueError('Original lifecycle stop phase changed')
    script = script.replace('phase manual-stop\n', edit_probe + '\nphase manual-stop\n', 1)
    payload['arm-vm/app-probe.sh'] = (script.encode(), 0o755)
    for name, color in (('original', (25, 70, 180)), ('first', (160, 60, 30)), ('second', (20, 140, 70))):
        jpeg = io.BytesIO()
        Image.new('RGB', (16, 16), color).save(jpeg, format='JPEG', quality=90)
        payload['arm-vm/install-fixture/' + name + '.jpg'] = (jpeg.getvalue(), 0o644)
    for name in ('gpio_preview', 'device_preview', 'capture_boot'):
        payload['arm-vm/lib/' + name + '.so'] = ((PROJECT / 'outputs/firmware-runtime' / (name + '.so')).read_bytes(), 0o644)
    body = io.BytesIO()
    with tarfile.open(fileobj=body, mode='w') as archive:
        for relative, (data, mode) in payload.items():
            target = overlay / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            info = tarfile.TarInfo(relative)
            info.mode, info.size = mode, len(data)
            archive.addfile(info, io.BytesIO(data))
    archive_path = OUT / 'candidate-vm.tar.gz'
    archive_path.write_bytes(gzip.compress(body.getvalue(), mtime=0))
    look_ids = sorted({name.split('/')[4] for name in payload if name.startswith('mnt/mmc/nx300m-mod/looks/')})
    metadata = {'scope': 'Current persistent candidate and complete SD bundle, existing ARM VM only',
                'hook_names': list(PERSISTENT_HOOKS), 'expected_look_ids': look_ids}
    with tarfile.open(PROJECT / 'work/stock-1.15/rootfs.tar') as stock:
        metadata['original_app_sha256'] = hashlib.sha256(stock.extractfile('usr/bin/di-camera-app-nx300').read()).hexdigest()
    metadata['bundle_bytes'] = bundle.stat().st_size
    metadata['file_count'] = len(payload)
    save('prepare-report.json', metadata)
    return archive_path


def boot_candidate(archive):
    """Explicitly restart only this disposable VM with a preloaded CPIO overlay."""
    from arm_vm import WORK, cpio_add
    image = WORK / 'initramfs.cpio.gz'
    base = WORK / 'initramfs-before-installed-candidate.cpio.gz'
    overlay = io.BytesIO()
    with tarfile.open(archive) as source:
        members = source.getmembers()
        directories = {parent.as_posix() for member in members for parent in Path(member.name).parents
                       if str(parent) != '.'}
        for index, name in enumerate(sorted(directories), 1):
            cpio_add(overlay, name, stat.S_IFDIR | 0o755, inode=index)
        for index, member in enumerate(members, len(directories) + 1):
            cpio_add(overlay, member.name, stat.S_IFREG | member.mode, source.extractfile(member).read(), index)
        cpio_add(overlay, 'TRAILER!!!', 0)
    subprocess.run([sys.executable, '-X', 'utf8', str(PROJECT / 'tools/arm_vm.py'), 'stop'], check=True)
    if not base.exists():
        image.rename(base)
    staged = image.with_suffix('.candidate-build')
    with base.open('rb') as source, staged.open('wb') as target:
        shutil.copyfileobj(source, target)
        target.write(gzip.compress(overlay.getvalue(), mtime=0))
    staged.replace(image)
    subprocess.run([sys.executable, '-X', 'utf8', str(PROJECT / 'tools/arm_vm.py'), 'start',
                    '--memory-mib', '2048'], check=True)
    save('boot-input.json', {'scope': 'Only the disposable ARM VM restarted',
                             'base_initramfs_backup': str(base), 'candidate_archive': str(archive),
                             'main_simulator_modified': False})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'install', 'boot', 'run', 'collect'))
    args = parser.parse_args()
    lifecycle.OUT = OUT
    original_collect = lifecycle.collect_command
    def bounded_collect(run_id):
        return original_collect(run_id).replace('tail -c 65536', 'tail -c 4096').replace(
            '$R base64 "$OUT/$name";',
            'if [ "$name" = candidate.maps ]; then grep /usr/lib/nx300m-mod/ "$OUT/$name" | $R base64; '
            'else $R base64 "$OUT/$name"; fi;')
    lifecycle.collect_command = bounded_collect
    original_exec = lifecycle.vm_exec
    lifecycle.vm_exec = lambda command, directory, timeout, label: original_exec(
        command, directory, max(timeout, 90) if label == 'collect' else timeout, label)
    lifecycle.RESULT_FILES += ('candidate.maps', 'firmware.ready', 'upload-first.json', 'upload-second.json',
                               'restore.json', 'edit-check.txt')
    if args.action in ('prepare', 'install', 'boot'):
        archive = prepare()
        if args.action == 'prepare':
            print(json.dumps({'prepared': str(archive)})); return
        if args.action == 'boot':
            boot_candidate(archive)
            return
        # The serial bridge transfers binary stdin; no photos enter this archive.
        guest = '/mnt/' + PROJECT.drive[0].lower() + PROJECT.as_posix()[2:]
        result = subprocess.run(['wsl.exe', '-u', 'root', '-e', 'python3', guest + '/tools/probe_sd_vm.py',
                                 'upload-worker', guest + '/' + archive.relative_to(PROJECT).as_posix()],
                                capture_output=True, timeout=80)
        (OUT / 'upload.stdout.log').write_bytes(result.stdout)
        (OUT / 'upload.stderr.log').write_bytes(result.stderr)
        if result.returncode:
            raise RuntimeError('VM archive transfer failed')
        command = ('set -e; for exe in /proc/[0-9]*/exe; do target=$(readlink "$exe" 2>/dev/null || :); '
                   'case "$target" in /usr/bin/di-camera-app-nx300|*/nx-remote-controller.arm) '
                   'echo "Owned VM must be idle before installation" >&2; exit 1;; esac; done; '
                   '/rescue/busybox tar -xzf /tmp/sd-native-update.tar.gz -C /; '
                   'printf "VM_CANDIDATE_INSTALLED\\n"')
        run = subprocess.run([sys.executable, '-X', 'utf8', str(PROJECT / 'tools/arm_vm.py'), 'exec', command, '--timeout', '25'],
                             capture_output=True, text=True, encoding='utf-8', timeout=35)
        (OUT / 'install.stdout.log').write_text(run.stdout, encoding='utf-8')
        (OUT / 'install.stderr.log').write_text(run.stderr, encoding='utf-8')
        response = json.loads(run.stdout)
        if run.returncode or response.get('exit_code'):
            raise RuntimeError('VM candidate installation failed')
        print(json.dumps({'installed_in_ARM_VM': True, 'physical_camera_modified': False})); return
    code = lifecycle.run(collect_only=args.action == 'collect')
    if code == 0:
        latest = json.loads((OUT / 'last-run.json').read_text(encoding='utf-8'))
        evidence = Path(latest['run_directory']) / 'guest-results'
        maps = (evidence / 'candidate.maps').read_text()
        absent = [name for name in PERSISTENT_HOOKS if '/usr/lib/nx300m-mod/' + name not in maps]
        report = json.loads((OUT / 'probe-report.json').read_text(encoding='utf-8'))
        if absent or int((evidence / 'firmware.ready').read_text()) != report['app_pid']:
            raise RuntimeError('Candidate module mapping/readiness differs from actual camera identity')
        uploads = [json.loads((evidence / name).read_text()) for name in ('upload-first.json', 'upload-second.json', 'restore.json')]
        if not all(row.get('ok') and row.get('backup_available') for row in uploads) or not (evidence / 'edit-check.txt').is_file():
            raise RuntimeError('Generated-JPEG backup/upload/restore was not confirmed')
        report.update({'persistent_candidate_hook_count': len(PERSISTENT_HOOKS),
                       'all_candidate_modules_mapped': True, 'firmware_bridge_owner_verified': True,
                       'single_remote_at_each_identity_check': True,
                       'generated_JPEG_two_uploads_restore_bytes_verified': True,
                       'original_backup_retained': True, 'media_directory_fsync_succeeded': True,
                       'media_filesystem_scope': 'Existing ARM VM media directory; physical SD/vfat not established',
                       'persistent_wrapper_used': True, 'main_simulator_modified': False})
        save('probe-report.json', report)
        print(json.dumps(report, ensure_ascii=False))
    raise SystemExit(code)


if __name__ == '__main__':
    main()
