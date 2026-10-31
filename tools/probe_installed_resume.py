"""Continue the existing ARM VM app after the binary test-client was corrected."""
import gzip
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

import probe_installed_candidate as candidate
import probe_app_vm as lifecycle


def main():
    candidate.prepare()
    out = candidate.OUT
    prior = json.loads((out / 'last-run.json').read_text(encoding='utf-8'))
    run_id = prior['run_id'] + '-binary-resume'
    directory = out / 'runs' / run_id
    directory.mkdir(parents=True, exist_ok=True)
    original = (out / 'overlay/arm-vm/app-probe.sh').read_text(encoding='utf-8')
    header = original.split('phase preflight\n', 1)[0]
    edit = original.split('phase generated-JPEG-backup-restore\n', 1)[1].split('phase manual-stop\n', 1)[0]
    truncated = r'''
{ printf 'POST /api/v1/gallery/upload?p=%s&require_backup=1 HTTP/1.0\r\nHost: 127.0.0.1:8781\r\nContent-Length: 4\r\nConnection: close\r\n\r\n' "${fixture##*/}";
  printf '\377\330\377\340'; } | "$R" nc -w 8 127.0.0.1 8781 > "$OUT/truncated.http"
grep -q '^HTTP/1.[01] 400' "$OUT/truncated.http"
"$R" cmp "$fixture" /arm-vm/install-fixture/original.jpg
[ ! -e "$backup" ]
'''
    edit = edit.replace('for version in first second; do\n', truncated + '\nfor version in first second; do\n', 1)
    script = header + '''phase resume-existing-app
{
 IFS= read -r app_pid
 IFS= read -r app_start
 IFS= read -r observed_app
 IFS= read -r app_inode
} < /arm-vm/app-results/@PRIOR@/app.identity
[ "$observed_app" = "$APP" ]
assert_app
app_started=1
cp /arm-vm/app-results/@PRIOR@/app.identity "$OUT/app.identity"
/bin/sh "$ROOT/start.sh" > "$OUT/resume-start.stdout" 2> "$OUT/resume-start.stderr"
remote_identity resumed
first_pid=$remote_pid
phase generated-JPEG-backup-restore
'''.replace('@PRIOR@', prior['run_id']) + edit + 'phase manual-stop\n' + original.split('phase manual-stop\n', 1)[1]
    body = io.BytesIO()
    payload = {'arm-vm/candidate-resume.sh': script.encode(),
               'mnt/mmc/nx300m-mod/bin/nx-remote-controller.arm':
                   (candidate.PROJECT / 'outputs/deployment/sd-bundle/nx300m-mod/bin/nx-remote-controller.arm').read_bytes()}
    with tarfile.open(fileobj=body, mode='w') as archive:
        for name, data in payload.items():
            member = tarfile.TarInfo(name)
            member.mode, member.size = 0o755, len(data)
            archive.addfile(member, io.BytesIO(data))
    archive_path = directory / 'resume-update.tar.gz'
    archive_path.write_bytes(gzip.compress(body.getvalue(), mtime=0))
    guest = '/mnt/' + candidate.PROJECT.drive[0].lower() + candidate.PROJECT.as_posix()[2:]
    upload = subprocess.run(['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'python3',
                             guest + '/tools/probe_sd_vm.py', 'upload-worker',
                             guest + '/' + archive_path.relative_to(candidate.PROJECT).as_posix()],
                            capture_output=True, timeout=80)
    (directory / 'upload.stdout.log').write_bytes(upload.stdout)
    (directory / 'upload.stderr.log').write_bytes(upload.stderr)
    if upload.returncode:
        raise RuntimeError('Partial VM update transfer failed')
    command = ('set -e; for entry in /proc/[0-9]*/exe; do observed=$(readlink "$entry" 2>/dev/null || :); '
               '[ "$observed" != /mnt/mmc/nx300m-mod/bin/nx-remote-controller.arm ] || exit 1; done; '
               '/rescue/busybox tar -xzf /tmp/sd-native-update.tar.gz -C /; '
               'APP_VM_RUN_ID=' + run_id + ' /rescue/busybox timeout -s TERM -k 8 65 /bin/sh /arm-vm/candidate-resume.sh; '
               'result=$?; printf "RESUME_EXIT=%s\\n" "$result"; exit "$result"')
    execution, response = lifecycle.vm_exec(command, directory, 80, 'resume')
    names = ('phase', 'probe.exit', 'app.identity', 'identity.resumed', 'identity.restarted',
             'loader.after-stop.json', 'loader.final.json', 'truncated.http', 'upload-first.json',
             'upload-second.json', 'restore.json', 'edit-check.txt', 'resume-start.stderr')
    lifecycle.RESULT_FILES = names
    lifecycle.SCRIPTS = ()
    command = lifecycle.collect_command(run_id)
    collect_execution, collected = lifecycle.vm_exec(command, directory, 65, 'collect')
    found = lifecycle.extract_files(collected.get('output', '') if collected else '', directory)
    if not response or response.get('exit_code') or execution['host_exit_code'] or execution['timed_out']:
        raise RuntimeError('Continued lifecycle failed; see preserved resume logs')
    if not collected or collect_execution['host_exit_code'] or found.get('probe.exit', b'').strip() != b'0':
        raise RuntimeError('Continued lifecycle evidence was not collected completely')
    if found.get('phase', b'').strip() != b'completed' or b'400' not in found.get('truncated.http', b''):
        raise RuntimeError('JPEG rejection / continued lifecycle not completed')
    for name in ('upload-first.json', 'upload-second.json', 'restore.json'):
        row = json.loads(found[name])
        if not row.get('ok') or not row.get('backup_available') or row.get('bytes', 0) < 600:
            raise RuntimeError('Binary JPEG upload/restore was not complete')
    initial = Path(prior['run_directory']) / 'guest-results'
    # Preserve the preceding failed run. Only its completed automatic-start
    # and repeated-start evidence is reused for the unchanged app lifetime.
    preparation = json.loads((out / 'prepare-report.json').read_text(encoding='utf-8'))
    if (initial / 'app.sha256').read_text().split()[0] != preparation['original_app_sha256']:
        raise RuntimeError('Original app identity from the preceding run differs')
    if (initial / 'identity.initial').read_bytes() != (initial / 'identity.repeated').read_bytes():
        raise RuntimeError('The preceding automatic/repeated startup identities differ')
    if found['app.identity'] != (initial / 'app.identity').read_bytes():
        raise RuntimeError('The app identity changed between the two bounded probes')
    if int((initial / 'firmware.ready').read_text()) != int(found['app.identity'].splitlines()[0]):
        raise RuntimeError('The firmware bridge marker owner differs from the actual app')
    for name in ('start.sh', 'stop.sh', 'common.sh', 'service.sh'):
        if (initial / ('source-' + name)).read_bytes() != (out / 'overlay/mnt/mmc/nx300m-mod' / name).read_bytes():
            raise RuntimeError('Production SD scripts differ from the observed automatic-start inputs')
    looks = json.loads((initial / 'looks.json').read_text())
    if sorted(row['id'] for row in looks['looks']) != preparation['expected_look_ids']:
        raise RuntimeError('Observed look catalog differs from the final package')
    maps = (initial / 'candidate.maps').read_text()
    if any('/usr/lib/nx300m-mod/' + name not in maps for name in candidate.PERSISTENT_HOOKS):
        raise RuntimeError('Nine-module mapping evidence is incomplete')
    loader = json.loads(found['loader.final.json'])
    first = json.loads((initial / 'loader.initial.json').read_text())
    if any(not row['succeeded'] or row['child_pid'] != 0 or row['last_exit'] or row['attempts'] != 1
           for row in (loader, first)):
        raise RuntimeError('Service-loader actual state is inconsistent')
    report = {'passed': True, 'scope': 'Original ARM app, complete candidate and explicit VM hardware shims',
              'initial_evidence_run': prior['run_id'], 'continuation_run': run_id,
              'initial_run_failure': 'Binary-unsafe BusyBox wget truncated the JPEG; failed run retained',
              'same_app_identity_across_both_runs': True, 'app_pid': loader['owner_pid'],
              'all_candidate_modules_mapped': True, 'persistent_candidate_hook_count': 9,
              'firmware_bridge_owner_verified': True, 'look_count': len(looks['looks']),
              'automatic_start_succeeded': True, 'duplicate_start_same_pid': True,
              'manual_stop_did_not_auto_restart': True, 'production_manual_restart_and_stop_succeeded': True,
              'single_remote_at_each_identity_check': True, 'service_loader': loader,
              'truncated_JPEG_rejected_HTTP_400': True,
              'generated_JPEG_two_uploads_restore_bytes_verified': True, 'original_backup_retained': True,
              'media_directory_fsync_succeeded': True, 'media_filesystem_scope': 'ARM VM rootfs media directory',
              'remote_bytes': len(payload['mnt/mmc/nx300m-mod/bin/nx-remote-controller.arm']),
              'remote_sha256': hashlib.sha256(payload['mnt/mmc/nx300m-mod/bin/nx-remote-controller.arm']).hexdigest(),
              'physical_camera_verified': False, 'main_simulator_modified': False}
    candidate.save('resume-report.json', report)
    candidate.save('probe-report.json', report)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
