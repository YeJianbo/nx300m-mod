"""Check read-only Mod APIs in the existing disposable ARM VM, without reboot."""
import gzip
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile

import probe_app_vm as lifecycle

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/deployment/mod-management-vm'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    binary = (ROOT / 'outputs/metol/remote/nx-remote-controller.arm').read_bytes()
    body = io.BytesIO()
    with tarfile.open(fileobj=body, mode='w') as package:
        member = tarfile.TarInfo('mnt/mmc/nx300m-mod/bin/nx-remote-controller.arm')
        member.size, member.mode = len(binary), 0o755
        package.addfile(member, io.BytesIO(binary))
    archive = OUT / 'remote-update.tar.gz'
    archive.write_bytes(gzip.compress(body.getvalue(), mtime=0))
    guest = '/mnt/' + ROOT.drive[0].lower() + ROOT.as_posix()[2:]
    transfer = subprocess.run(['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'python3',
                               guest + '/tools/probe_sd_vm.py', 'upload-worker',
                               guest + '/' + archive.relative_to(ROOT).as_posix()], capture_output=True, timeout=80)
    (OUT / 'upload.stdout.log').write_bytes(transfer.stdout)
    (OUT / 'upload.stderr.log').write_bytes(transfer.stderr)
    if transfer.returncode:
        raise RuntimeError('VM remote update transfer failed')
    command = r'''set -eu
R=/rescue/busybox
ROOT=/mnt/mmc/nx300m-mod
for entry in /proc/[0-9]*/exe; do
 observed=$(readlink "$entry" 2>/dev/null || :)
 [ "$observed" != "$ROOT/bin/nx-remote-controller.arm" ] || exit 1
done
"$R" tar -xzf /tmp/sd-native-update.tar.gz -C /
trap '/bin/sh "$ROOT/stop.sh" >/tmp/mod-manager-stop.log 2>&1' EXIT
export DISPLAY=:97 NX300M_MOD_ROOT="$ROOT"
/bin/sh "$ROOT/start.sh"
"$R" wget -q -T 8 -O /tmp/mod-manager-status.json http://127.0.0.1:8781/api/v1/mods/status
"$R" wget -q -T 8 -O /tmp/mod-manager-plan.json http://127.0.0.1:8781/api/v1/mods/install-plan
printf 'POST /api/v1/mods/status HTTP/1.0\r\nHost: 127.0.0.1:8781\r\nContent-Length: 0\r\nConnection: close\r\n\r\n' |
 "$R" nc -w 8 127.0.0.1 8781 > /tmp/mod-manager-post.http
grep -q '^HTTP/1.[01] 405' /tmp/mod-manager-post.http
printf '\nMM_STATUS='; "$R" cat /tmp/mod-manager-status.json
printf '\nMM_PLAN='; "$R" cat /tmp/mod-manager-plan.json
printf '\nMM_POST_REJECTED=405\n'
'''
    execution, response = lifecycle.vm_exec(command, OUT, 65, 'run')
    if execution['host_exit_code'] or not response or response.get('exit_code'):
        raise RuntimeError('VM management API check failed; inspect preserved run logs')
    output = response['output']
    status = json.loads(re.search(r'^MM_STATUS=(.+)$', output, re.M).group(1))
    plan = json.loads(re.search(r'^MM_PLAN=(.+)$', output, re.M).group(1))
    assert status['model'] == 'NX300M' and status['firmware'] == '1.15'
    assert status['camera_process_verified'] and status['sd_service_complete']
    assert len(status['modules']) == 9 and all(row['mapped'] and row['installed_file'] for row in status['modules'])
    assert not plan['remote_install_supported'] and len(plan['mod_steps']) == 3 and len(plan['stock_steps']) == 3
    assert 'MM_POST_REJECTED=405' in output
    report = {'passed': True, 'module_count': 9, 'all_modules_actually_mapped': True,
              'ready_pid_identity_verified': True, 'sd_service_complete': True,
              'GET_status_and_plan_passed': True, 'POST_rejected_HTTP_405': True,
              'remote_bytes': len(binary), 'firmware_or_rootfs_written': False,
              'main_simulator_modified': False, 'physical_camera_verified': False}
    for name, data in (('status.json', status), ('plan.json', plan), ('report.json', report)):
        (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
