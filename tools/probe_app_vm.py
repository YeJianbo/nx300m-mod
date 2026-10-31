"""Stage and inspect the original camera -> production SD service chain in ARM VM.

Preparation is local only. run/collect use the existing arm_vm command channel;
they do not boot, stop or replace a VM. Camera, SD scripts and process identities
are never substituted. The hardware shims remain explicit laboratory inputs.
"""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import time
import uuid

PROJECT = Path(__file__).resolve().parents[1]
OUT = PROJECT / 'outputs/deployment/app-vm'
SD_OUT = PROJECT / 'outputs/deployment/sd-bundle/vm-probe'
SD = '/mnt/mmc/nx300m-mod'
SCRIPTS = ('start.sh', 'stop.sh', 'common.sh', 'service.sh')
SHIMS = {
    'arm-vm/lib/gpio_preview.so': 'outputs/firmware-runtime/gpio_preview.so',
    'arm-vm/lib/device_preview.so': 'outputs/firmware-runtime/device_preview.so',
    'arm-vm/lib/capture_boot.so': 'outputs/firmware-runtime/capture_boot.so',
    'usr/lib/nx300m-mod/service-loader.so': 'outputs/deployment/service-loader/service-loader.so',
}

POST_BOOT = r'''#!/bin/sh
R=/rescue/busybox
$R mkdir -p /arm-vm /tmp/.X11-unix /dev/shm /pref /adj /var/run /mnt/mmc/DCIM
$R chmod 1777 /tmp /tmp/.X11-unix /dev/shm
$R chmod 777 /pref /adj /var/run
$R mount -t tmpfs -o mode=1777,nosuid,nodev tmpfs /dev/shm > /arm-vm/shm-mount.log 2>&1 || :
$R touch /adj/AdjLog.txt
$R chmod 666 /adj/AdjLog.txt
# The stock cache contains softfloat paths for shared SONAMEs. Both loaders
# have distinct default search directories; using those also keeps Xvfb's
# stock-shell helper processes free of a hardfloat LD_LIBRARY_PATH.
if [ -f /etc/ld.so.cache ]; then $R mv /etc/ld.so.cache /arm-vm/stock-ld.so.cache; fi
$R env -u LD_PRELOAD -u LD_AUDIT -u LD_LIBRARY_PATH XKB_CONFIG_ROOT=/usr/share/X11/xkb \
 /usr/bin/Xvfb :97 -screen 0 480x800x24 -nolisten tcp -ac -noreset -fp built-ins \
 > /arm-vm/xvfb.log 2>&1 &
printf '%s\n' "$!" > /arm-vm/xvfb.pid
exit 0
'''

PROBE = r'''#!/bin/sh
set -eu
ID=${APP_VM_RUN_ID:?APP_VM_RUN_ID is required}
case $ID in ''|*[!A-Za-z0-9_-]*) exit 2;; esac
OUT=/arm-vm/app-results/$ID
ROOT=/mnt/mmc/nx300m-mod
APP=/usr/bin/di-camera-app-nx300
REMOTE=$ROOT/bin/nx-remote-controller.arm
STATUS=/var/run/nx300m-service-loader-status.json
R=/rescue/busybox
mkdir -p "$OUT"
printf '%s\n' "$ID" > /arm-vm/app-current-run
export NX300M_MOD_ROOT="$ROOT" DISPLAY=:97 NX_START_TIMEOUT=15 NX_STOP_TIMEOUT=6
app_started=0
phase() { printf '%s\n' "$1" > "$OUT/phase"; printf 'VM_APP_PHASE %s\n' "$1"; }
finish() {
 result=$?
 trap - EXIT HUP INT TERM
 if [ "$app_started" = 1 ]; then
  /bin/sh "$ROOT/stop.sh" > "$OUT/cleanup.stdout" 2> "$OUT/cleanup.stderr" || :
 fi
 if [ -f "$STATUS" ]; then cp "$STATUS" "$OUT/loader.final.json"; fi
 printf '%s\n' "$result" > "$OUT/probe.exit"
 exit "$result"
}
trap finish EXIT
trap 'exit 124' HUP INT TERM
process_start() {
 IFS= read -r line < "/proc/$1/stat" || return 1
 fields=${line##*) }
 set -- $fields
 [ "$#" -ge 20 ] && [ "$1" != Z ] || return 1
 shift 19
 printf '%s\n' "$1"
}
assert_app() {
 [ "$(readlink "/proc/$app_pid/exe")" = "$APP" ]
 [ "$(stat -L -c '%d:%i' "/proc/$app_pid/exe")" = "$app_inode" ]
 [ "$(process_start "$app_pid")" = "$app_start" ]
 kill -0 "$app_pid"
}
remote_identity() {
 tag=$1
 {
  IFS= read -r remote_pid
  IFS= read -r remote_start
  IFS= read -r remote_boot
  IFS= read -r remote_exe
  IFS= read -r remote_inode
 } < "$ROOT/.state/remote.pid"
 case $remote_pid in ''|*[!0-9]*) return 1;; esac
 [ "$remote_pid" -gt 1 ]
 IFS= read -r current_boot < /proc/sys/kernel/random/boot_id
 [ "$remote_boot" = "$current_boot" ]
 [ "$remote_exe" = "$REMOTE" ]
 [ "$(readlink "/proc/$remote_pid/exe")" = "$REMOTE" ]
 [ "$remote_inode" = "$(stat -L -c '%d:%i' "$REMOTE")" ]
 [ "$remote_inode" = "$(stat -L -c '%d:%i' "/proc/$remote_pid/exe")" ]
 [ "$remote_start" = "$(process_start "$remote_pid")" ]
 kill -0 "$remote_pid"
 cp "$ROOT/.state/remote.pid" "$OUT/identity.$tag"
 printf 'VM_APP_REMOTE %s pid=%s exe=%s inode=%s\n' "$tag" "$remote_pid" "$remote_exe" "$remote_inode"
}
wait_reaped() {
 target=$1
 waited=0
 while kill -0 "$target" 2>/dev/null && [ "$waited" -lt 6 ]; do sleep 1; waited=$((waited+1)); done
 ! kill -0 "$target" 2>/dev/null
}

phase preflight
for entry in /proc/[0-9]*/exe; do
 observed=$(readlink "$entry" 2>/dev/null || :)
 [ "$observed" != "$APP" ] || { echo 'An original camera process already exists; refusing another run' >&2; exit 1; }
done
[ ! -e "$ROOT/.state/remote.pid" ] || { echo 'An SD PID record already exists before automatic startup' >&2; exit 1; }
[ ! -e "$STATUS" ] || { echo 'A service-loader status already exists before app startup' >&2; exit 1; }
waited=0
while [ ! -S /tmp/.X11-unix/X97 ]; do
 [ "$waited" -lt 15 ] || { cat /arm-vm/xvfb.log >&2; exit 1; }
 sleep 1; waited=$((waited+1))
done
$R sha256sum "$APP" > "$OUT/app.sha256"

phase preferences
umask 000
/usr/bin/prefman init > "$OUT/pref-init.stdout" 2> "$OUT/pref-init.stderr"
/usr/bin/prefman reset > "$OUT/pref-reset.stdout" 2> "$OUT/pref-reset.stderr"
if grep -qi failed "$OUT/pref-reset.stdout"; then echo 'Original preference reset failed' >&2; exit 1; fi
/usr/bin/prefman get 1 0xe0 l > "$OUT/pref-read.stdout" 2> "$OUT/pref-read.stderr"
if grep -Eqi 'Semaphore open Fail|pref_get_shm.*failed|failed' "$OUT/pref-read.stderr" "$OUT/pref-read.stdout"; then
 echo 'Original preference state unavailable' >&2; exit 1
fi
grep -q 'value = -1' "$OUT/pref-read.stdout"
/usr/bin/prefman set 1 0x300 l 1 > "$OUT/pref-display.stdout" 2> "$OUT/pref-display.stderr"
/usr/bin/prefman set 1 0x304 l 1 > "$OUT/pref-power.stdout" 2> "$OUT/pref-power.stderr"
umask 077

phase app-start
unset LD_AUDIT LD_LIBRARY_PATH
export NX300M_SHUTTER_SIM=1 NX300M_SHUTTER_PARK=10 NX300M_ISP_SIM=1
export NX300M_SPI_MMIO=1 NX300M_SPI_PEER=aquila-registers NX300M_D4C_REGISTERS=1 NX300M_HOTSHOE_ABSENT=1
LD_PRELOAD=/arm-vm/lib/gpio_preview.so:/arm-vm/lib/device_preview.so:/arm-vm/lib/capture_boot.so:/usr/lib/nx300m-mod/service-loader.so \
 "$APP" > "$OUT/app.stdout" 2> "$OUT/app.stderr" &
printf '%s\n' "$!" > "$OUT/app-launcher.pid"
app_started=1

phase automatic-service
waited=0
while :; do
 if [ -r "$STATUS" ] && grep -q '"succeeded":true' "$STATUS" &&
    grep -q '"child_pid":0' "$STATUS" && [ -r "$ROOT/.state/remote.pid" ]; then break; fi
 [ "$waited" -lt 50 ] || { echo 'App automatic SD startup did not finish' >&2; exit 1; }
 sleep 1; waited=$((waited+1))
done
cp "$STATUS" "$OUT/loader.initial.json"
app_pid=$(sed -n 's/.*"owner_pid":\([0-9][0-9]*\).*/\1/p' "$STATUS")
case $app_pid in ''|*[!0-9]*) echo 'Invalid loader owner PID' >&2; exit 1;; esac
[ "$app_pid" -gt 1 ]
app_inode=$(stat -L -c '%d:%i' "$APP")
app_start=$(process_start "$app_pid")
assert_app
printf '%s\n%s\n%s\n%s\n' "$app_pid" "$app_start" "$APP" "$app_inode" > "$OUT/app.identity"
remote_identity initial
first_pid=$remote_pid

phase HTTP
$R wget -q -T 8 -O "$OUT/info.json" http://127.0.0.1:8781/api/v1/camera/info
$R wget -q -T 8 -O "$OUT/looks.json" http://127.0.0.1:8781/api/v1/looks
/bin/sh "$ROOT/start.sh" > "$OUT/repeat.stdout" 2> "$OUT/repeat.stderr"
remote_identity repeated
[ "$remote_pid" = "$first_pid" ]

phase manual-stop
/bin/sh "$ROOT/stop.sh" > "$OUT/stop.stdout" 2> "$OUT/stop.stderr"
/bin/sh "$ROOT/stop.sh" > "$OUT/repeat-stop.stdout" 2> "$OUT/repeat-stop.stderr"
wait_reaped "$first_pid"
[ ! -e "$ROOT/.state/remote.pid" ]
sleep 2
[ ! -e "$ROOT/.state/remote.pid" ]
cp "$STATUS" "$OUT/loader.after-stop.json"
assert_app

phase manual-restart
/bin/sh "$ROOT/start.sh" > "$OUT/restart.stdout" 2> "$OUT/restart.stderr"
remote_identity restarted
[ "$remote_pid" != "$first_pid" ]
second_pid=$remote_pid
$R wget -q -T 8 -O "$OUT/restart-info.json" http://127.0.0.1:8781/api/v1/camera/info
/bin/sh "$ROOT/stop.sh" > "$OUT/final-stop.stdout" 2> "$OUT/final-stop.stderr"
wait_reaped "$second_pid"
[ ! -e "$ROOT/.state/remote.pid" ]
assert_app
phase completed
printf 'VM_APP_PROBE passed app_pid=%s automatic_remote_pid=%s restarted_remote_pid=%s\n' "$app_pid" "$first_pid" "$second_pid"
'''

RESULT_FILES = (
    'phase', 'probe.exit', 'app.sha256', 'app.identity', 'app-launcher.pid',
    'loader.initial.json', 'loader.after-stop.json', 'loader.final.json',
    'identity.initial', 'identity.repeated', 'identity.restarted',
    'info.json', 'looks.json', 'restart-info.json',
    'pref-init.stdout', 'pref-init.stderr', 'pref-reset.stdout', 'pref-reset.stderr',
    'pref-read.stdout', 'pref-read.stderr', 'pref-display.stdout', 'pref-display.stderr',
    'pref-power.stdout', 'pref-power.stderr', 'repeat.stdout', 'repeat.stderr',
    'stop.stdout', 'stop.stderr', 'repeat-stop.stdout', 'repeat-stop.stderr',
    'restart.stdout', 'restart.stderr', 'final-stop.stdout', 'final-stop.stderr',
    'cleanup.stdout', 'cleanup.stderr', 'app.stdout.tail', 'app.stderr.tail',
    'remote.log', 'supervisor.log', 'xvfb.log', 'log-sizes',
)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def prepare():
    source = SD_OUT / 'overlay'
    previous = json.loads((SD_OUT / 'prepare-report.json').read_text(encoding='utf-8'))
    if not source.is_dir():
        raise ValueError('Prepare the established SD VM overlay first')
    overlay = OUT / 'overlay'
    OUT.mkdir(parents=True, exist_ok=True)
    if overlay.exists():
        if overlay.is_symlink() or overlay.resolve().parent != OUT.resolve():
            raise ValueError('App overlay is outside its dedicated output directory')
        shutil.rmtree(overlay)
    shutil.copytree(source, overlay, symlinks=True, ignore=shutil.ignore_patterns('arm-vm'))
    if any('qemu' in p.name.lower() for p in overlay.rglob('*')):
        raise ValueError('QEMU-user must not enter the app overlay')
    bundle = PROJECT / 'outputs/deployment/sd-bundle/nx300m-mod-sd.tar.gz'
    with tarfile.open(bundle) as package:
        for name in SCRIPTS:
            expected = package.extractfile('nx300m-mod/' + name).read()
            if (overlay / SD.lstrip('/') / name).read_bytes() != expected:
                raise ValueError('Established SD overlay differs from the production bundle: ' + name)
    inputs = []
    for destination, relative in SHIMS.items():
        original = PROJECT / relative
        data = original.read_bytes()
        if data[:4] != b'\x7fELF' or int.from_bytes(data[18:20], 'little') != 40:
            raise ValueError('Expected a real ARM ELF: ' + str(original))
        target = overlay / destination
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        inputs.append({'source': relative, 'destination': '/' + destination, 'bytes': len(data)})
    with tarfile.open(PROJECT / 'work/stock-1.15/rootfs.tar') as stock:
        app = stock.extractfile('usr/bin/di-camera-app-nx300').read()
        original = stock.extractfile('usr/lib/sys-mmap-script/sysmmap-script-NX300M').read()
    corrected, count = re.subn(rb'(//Define Playback Edit Map\r?\nCLIENT srp )playback( n 0 1)', rb'\g<1>pbedit\2', original)
    if count != 1:
        raise ValueError('Expected the single known stock Playback Edit client typo')
    target = overlay / 'usr/lib/sys-mmap-script/sysmmap-script-NX300M'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(corrected)
    folder = overlay / 'arm-vm'
    folder.mkdir(exist_ok=True)
    (folder / 'post-boot.sh').write_text(POST_BOOT, encoding='utf-8', newline='\n')
    (folder / 'app-probe.sh').write_text(PROBE, encoding='utf-8', newline='\n')
    report = {'overlay': str(overlay), 'guest_probe': '/arm-vm/app-probe.sh',
              'requires_arm_vm_prepare_stock_root': True,
              'source_SD_overlay': str(source), 'expected_look_ids': previous['expected_look_ids'],
              'original_app_sha256': hashlib.sha256(app).hexdigest(), 'shim_inputs': inputs,
              'stock_sysmmap_change': 'Playback Edit CLIENT srp playback -> pbedit; exactly one substitution',
              'production_scripts_modified': False, 'process_identity_shim_used': False,
              'lcd_capture_loaded': False, 'hardware_backend': 'Existing explicit GPIO/device/capture-boot laboratory shims',
              'scope': 'Prepared inputs only; camera is supplied by the separate original stock-root image'}
    save(OUT / 'prepare-report.json', report)
    print(json.dumps(report, ensure_ascii=False))
    return report


def collect_command(run_id):
    folder = '/arm-vm/app-results/' + run_id
    # Keep complete raw camera logs in the guest. Bound their serial export so
    # a hardware retry loop cannot consume the probe's wall-time allowance.
    return ('OUT=' + shlex.quote(folder) + '; R=/rescue/busybox; mkdir -p "$OUT"; '
            'for name in app.stdout app.stderr; do if [ -f "$OUT/$name" ]; then '
            '$R wc -c "$OUT/$name" >> "$OUT/log-sizes"; $R tail -c 65536 "$OUT/$name" > "$OUT/$name.tail"; fi; done; '
            'for name in remote.log supervisor.log; do if [ -f ' + SD + '/.state/$name ]; then '
            'cp ' + SD + '/.state/$name "$OUT/$name"; fi; done; '
            'if [ -f /arm-vm/xvfb.log ]; then $R tail -c 32768 /arm-vm/xvfb.log > "$OUT/xvfb.log"; fi; '
            'for name in ' + ' '.join(RESULT_FILES) + '; do if [ -f "$OUT/$name" ]; then '
            'printf "\\n%s%s%s\\n" __VM_APP_FILE_ "$name" __; $R base64 "$OUT/$name"; '
            'printf "\\n%s%s%s\\n" __VM_APP_END_ "$name" __; fi; done; '
            'for name in ' + ' '.join(SCRIPTS) + '; do '
            'printf "\\n%s%s%s\\n" __VM_APP_SOURCE_ "$name" __; $R base64 "' + SD + '/$name"; '
            'printf "\\n%s%s%s\\n" __VM_APP_END_ "$name" __; done')


def vm_exec(command, run_dir, timeout, label):
    arguments = [sys.executable, '-X', 'utf8', str(PROJECT / 'tools/arm_vm.py'), 'exec', command, '--timeout', str(timeout)]
    started = time.monotonic()
    try:
        result = subprocess.run(arguments, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=min(timeout + 10, 120))
        stdout, stderr, code, timed_out = result.stdout, result.stderr, result.returncode, False
    except subprocess.TimeoutExpired as error:
        decode = lambda v: v.decode('utf-8', 'replace') if isinstance(v, bytes) else v or ''
        stdout, stderr, code, timed_out = decode(error.stdout), decode(error.stderr), None, True
    (run_dir / (label + '.stdout.log')).write_text(stdout, encoding='utf-8')
    (run_dir / (label + '.stderr.log')).write_text(stderr, encoding='utf-8')
    execution = {'host_exit_code': code, 'timed_out': timed_out, 'elapsed_seconds': round(time.monotonic() - started, 3)}
    save(run_dir / (label + '-execution.json'), execution)
    try:
        response = json.loads(stdout)
    except ValueError:
        return execution, None
    return execution, response


def extract_files(output, run_dir):
    evidence = run_dir / 'guest-results'
    evidence.mkdir(exist_ok=True)
    found = {}
    for prefix, names in (('__VM_APP_FILE_', RESULT_FILES), ('__VM_APP_SOURCE_', SCRIPTS)):
        for name in names:
            pattern = '^' + re.escape(prefix + name + '__') + r'\r?\n([A-Za-z0-9+/=\s]*?)\r?\n' + re.escape('__VM_APP_END_' + name + '__') + r'\r?$'
            match = re.search(pattern, output, re.M)
            if match:
                data = base64.b64decode(re.sub(r'\s', '', match[1]), validate=True)
                destination = evidence / (('source-' if prefix.endswith('SOURCE_') else '') + name)
                destination.write_bytes(data)
                found[destination.name] = data
    return found


def evaluate(found, preparation, response, execution):
    if response is None:
        raise ValueError('No complete VM JSON response; preserved logs and guest run remain available for collect')
    if execution['host_exit_code'] != 0 or response.get('exit_code') != 0 or execution['timed_out']:
        raise ValueError('Guest app lifecycle failed; inspect preserved phase, logs and exit codes')
    if found.get('probe.exit', b'').strip() != b'0' or found.get('phase', b'').strip() != b'completed':
        raise ValueError('Guest lifecycle did not finish')
    if found['app.sha256'].decode().split()[0] != preparation['original_app_sha256']:
        raise ValueError('Running app file differs from original stock app')
    for name in SCRIPTS:
        if found['source-' + name] != (OUT / 'overlay' / SD.lstrip('/') / name).read_bytes():
            raise ValueError('Production SD script changed in the guest: ' + name)
    info = json.loads(found['info.json'])
    looks = json.loads(found['looks.json'])
    if (info.get('model'), info.get('fw_ver')) != ('NX300M', '1.15'):
        raise ValueError('Unexpected actual remote camera metadata')
    if sorted(item['id'] for item in looks['looks']) != preparation['expected_look_ids']:
        raise ValueError('Actual remote look catalog differs from prepared SD bundle')
    first = json.loads(found['loader.initial.json'])
    after = json.loads(found['loader.after-stop.json'])
    final = json.loads(found['loader.final.json'])
    app_identity = found['app.identity'].decode().splitlines()
    app_pid = int(app_identity[0])
    if any(item['owner_pid'] != app_pid or item['root'] != SD or not item['succeeded'] or item['child_pid'] != 0 or item['last_exit'] != 0 for item in (first, after, final)):
        raise ValueError('Actual production service-loader state is inconsistent')
    if first['attempts'] != after['attempts'] or after['attempts'] != final['attempts']:
        raise ValueError('Service-loader retriggered after manual stop')
    initial = found['identity.initial'].decode().splitlines()
    repeated = found['identity.repeated'].decode().splitlines()
    restarted = found['identity.restarted'].decode().splitlines()
    if initial != repeated or initial[0] == restarted[0]:
        raise ValueError('Production duplicate-start/restart identity mismatch')
    return {'passed': True, 'scope': 'Original stock app with explicit hardware shims -> production service-loader -> unchanged production SD services in generic ARM Linux VM',
            'original_app_identity_verified': True, 'app_pid': app_pid, 'automatic_remote_pid': int(initial[0]),
            'restarted_remote_pid': int(restarted[0]), 'native_proc_exe_and_inode_verified': True,
            'service_loader': final, 'unchanged_production_scripts': list(SCRIPTS), 'look_count': len(looks['looks']),
            'automatic_start_succeeded': True, 'duplicate_start_same_pid': True, 'manual_stop_did_not_auto_restart': True,
            'production_manual_restart_and_stop_succeeded': True, 'app_remains_alive_after_probe': True,
            'process_identity_shim_used': False, 'ready_file_fabricated': False,
            'camera_hardware_emulated_by_virt': False, 'physical_camera_verified': False,
            'camera_log_export': 'Last 65536 bytes per stream; full raw logs remain under guest run directory'}


def run(collect_only=False):
    preparation = json.loads((OUT / 'prepare-report.json').read_text(encoding='utf-8'))
    if collect_only:
        latest = json.loads((OUT / 'last-run.json').read_text(encoding='utf-8'))
        run_id, run_dir = latest['run_id'], Path(latest['run_directory'])
        command, timeout = collect_command(run_id), 30
    else:
        run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
        run_dir = OUT / 'runs' / run_id
        run_dir.mkdir(parents=True)
        save(OUT / 'last-run.json', {'run_id': run_id, 'run_directory': str(run_dir), 'guest_directory': '/arm-vm/app-results/' + run_id})
        command = ('APP_VM_RUN_ID=' + run_id + ' /rescue/busybox timeout -s TERM -k 8 95 /bin/sh /arm-vm/app-probe.sh; app_vm_rc=$?; '
                   + collect_command(run_id) + '; exit "$app_vm_rc"')
        timeout = 110
    execution, response = vm_exec(command, run_dir, timeout, 'collect' if collect_only else 'run')
    extract_error = None
    try:
        found = extract_files(response.get('output', ''), run_dir) if response else {}
    except ValueError as error:
        found, extract_error = {}, str(error)
    if collect_only:
        # collect exports existing evidence; it never turns a failed lifecycle
        # into a successful run merely because the retrieval command returned 0.
        lifecycle = run_dir / 'lifecycle-execution.json'
        if lifecycle.exists():
            saved = json.loads(lifecycle.read_text(encoding='utf-8'))
            execution, lifecycle_code = saved['execution'], saved['guest_exit_code']
            if response:
                response['exit_code'] = lifecycle_code
    else:
        save(run_dir / 'lifecycle-execution.json', {'execution': execution, 'guest_exit_code': response.get('exit_code') if response else None})
    try:
        if extract_error:
            raise ValueError('Guest evidence export could not be decoded: ' + extract_error)
        report = evaluate(found, preparation, response, execution)
    except (ValueError, KeyError, TypeError) as error:
        report = {'passed': False, 'reason': str(error), 'execution': execution,
                  'guest_exit_code': response.get('exit_code') if response else None,
                  'evidence_directory': str(run_dir), 'guest_directory': '/arm-vm/app-results/' + run_id}
    save(run_dir / 'probe-report.json', report)
    save(OUT / 'probe-report.json', report)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report['passed'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'run', 'collect'])
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare()
        return 0
    return run(collect_only=args.action == 'collect')


if __name__ == '__main__':
    raise SystemExit(main())
