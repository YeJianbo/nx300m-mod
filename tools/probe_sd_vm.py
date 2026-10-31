"""Prepare unchanged SD services for a real ARM Linux VM, without identity shims."""
import argparse
import base64
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import time
import uuid

PROJECT = Path(__file__).resolve().parents[1]
OUT = PROJECT / 'outputs/deployment/sd-bundle/vm-probe'
SD = '/mnt/mmc/nx300m-mod'


POST_BOOT = r'''#!/bin/sh
mkdir -p /arm-vm /tmp/.X11-unix
chmod 1777 /tmp /tmp/.X11-unix
export XKB_CONFIG_ROOT=/usr/share/X11/xkb
/usr/bin/Xvfb :97 -screen 0 480x800x24 -nolisten tcp -ac -noreset -fp built-ins > /arm-vm/xvfb.log 2>&1 &
printf '%s\n' "$!" > /arm-vm/xvfb.pid
# Keep the management shell available even if the X server fails to start.
exit 0
'''


PROBE = r'''#!/bin/sh
set -eu
ROOT=/mnt/mmc/nx300m-mod
OUT=/arm-vm/sd-results
mkdir -p "$OUT" /mnt/mmc/DCIM
export NX300M_MOD_ROOT="$ROOT" DISPLAY="${DISPLAY:-:97}"
export NX_START_TIMEOUT=30 NX_STOP_TIMEOUT=10
REMOTE=$ROOT/bin/nx-remote-controller.arm
cleanup() { /bin/sh "$ROOT/stop.sh" >>"$OUT/cleanup.log" 2>&1 || :; }
trap cleanup EXIT HUP INT TERM

waited=0
while [ ! -S /tmp/.X11-unix/X97 ]; do
 if [ "$waited" -ge 30 ]; then
  cat /arm-vm/xvfb.log >&2
  echo 'ARM X server did not become available' >&2
  exit 1
 fi
 sleep 1
 waited=$((waited + 1))
done
/bin/sh "$ROOT/start.sh" >"$OUT/start.stdout" 2>"$OUT/start.stderr"
{
 IFS= read -r pid
 IFS= read -r started
 IFS= read -r boot
 IFS= read -r executable
 IFS= read -r inode
} < "$ROOT/.state/remote.pid"
case $pid in ''|*[!0-9]*) echo 'Invalid production PID record' >&2; exit 1;; esac
[ "$pid" -gt 1 ]
observed=$(readlink "/proc/$pid/exe")
observed_inode=$(stat -L -c '%d:%i' "/proc/$pid/exe")
file_inode=$(stat -L -c '%d:%i' "$REMOTE")
[ "$executable" = "$REMOTE" ] && [ "$observed" = "$REMOTE" ]
[ "$inode" = "$observed_inode" ] && [ "$inode" = "$file_inode" ]
printf '%s\n' "$pid" "$started" "$boot" "$executable" "$inode" >"$OUT/identity"
printf 'VM_SD_IDENTITY pid=%s exe=%s file_inode=%s observed_inode=%s\n' "$pid" "$observed" "$file_inode" "$observed_inode"

# Native HTTP readiness and the two bodies come from the actual ARM service.
# No camera app is present in this first deployment-only VM check.
/rescue/busybox wget -q -T 10 -O "$OUT/info.json" http://127.0.0.1:8781/api/v1/camera/info
/rescue/busybox wget -q -T 10 -O "$OUT/looks.json" http://127.0.0.1:8781/api/v1/looks
/bin/sh "$ROOT/start.sh" >"$OUT/repeat.stdout" 2>"$OUT/repeat.stderr"
IFS= read -r repeated < "$ROOT/.state/remote.pid"
[ "$repeated" = "$pid" ]

/bin/sh "$ROOT/stop.sh" >"$OUT/stop.stdout" 2>"$OUT/stop.stderr"
[ ! -e "$ROOT/.state/remote.pid" ]
# stop.sh waits for execution to end; the separate supervisor reaps its child
# on its next polling cycle. Observe that completion before checking proc.
reap_wait=0
while kill -0 "$pid" 2>/dev/null && [ "$reap_wait" -lt 10 ]; do
 sleep 1
 reap_wait=$((reap_wait + 1))
done
if kill -0 "$pid" 2>/dev/null; then
 echo 'Remote still exists after production stop' >&2
 exit 1
fi
cp "$ROOT/.state/remote.log" "$OUT/remote.log"
cp "$ROOT/.state/supervisor.log" "$OUT/supervisor.log"
trap - EXIT HUP INT TERM
printf 'VM_SD_PROBE passed\n'
'''


DELIVERY_PROBE = r'''#!/bin/sh
set -eu
ROOT=/mnt/mmc/nx300m-mod
OUT=/arm-vm/sd-delivery
mkdir -p "$OUT" /mnt/mmc/DCIM
/rescue/busybox sha256sum -c /tmp/sd-delivery.members >"$OUT/archive-files.checks"
export NX300M_MOD_ROOT="$ROOT" DISPLAY=:97 NX_START_TIMEOUT=20 NX_STOP_TIMEOUT=6
panel_pid=0
cleanup_panel() {
 if [ "$panel_pid" -gt 1 ] && [ "$(readlink "/proc/$panel_pid/exe" 2>/dev/null || :)" = "$ROOT/bin/nxui.arm" ]; then
  kill -TERM "$panel_pid" 2>/dev/null || :
  wait "$panel_pid" || :
 fi
 panel_pid=0
}
cleanup() {
 cleanup_panel
 /bin/sh "$ROOT/stop.sh" >"$OUT/stop.stdout" 2>"$OUT/stop.stderr" || :
}
trap cleanup EXIT HUP INT TERM
[ -S /tmp/.X11-unix/X97 ] || { echo 'Existing ARM X server is unavailable' >&2; exit 1; }
[ ! -e "$ROOT/.state/remote.pid" ] || { echo 'An SD remote already exists; refusing to replace it' >&2; exit 1; }
/bin/sh "$ROOT/start.sh" >"$OUT/start.stdout" 2>"$OUT/start.stderr"
IFS= read -r pid < "$ROOT/.state/remote.pid"
[ "$(readlink "/proc/$pid/exe")" = "$ROOT/bin/nx-remote-controller.arm" ]
[ "$(stat -L -c '%d:%i' "/proc/$pid/exe")" = "$(stat -L -c '%d:%i' "$ROOT/bin/nx-remote-controller.arm")" ]
cp "$ROOT/.state/remote.pid" "$OUT/remote.identity"
for endpoint in info looks recipes; do
 case $endpoint in
  info) path=/api/v1/camera/info ;;
  looks) path=/api/v1/looks ;;
  recipes) path=/api/v1/color/recipes ;;
 esac
 /rescue/busybox wget -q -T 8 -O "$OUT/$endpoint.json" "http://127.0.0.1:8781$path"
done

# These are the production binaries, resolved by the original ARM loader.
for name in nxui.arm recipe_engine.arm lut_engine.arm; do
 [ -x "$ROOT/bin/$name" ]
 /lib/ld-linux.so.3 --list "$ROOT/bin/$name" >"$OUT/$name.dependencies" 2>&1
done
/bin/sh -n "$ROOT/panel.sh"
for mode in recipes lut; do
 /bin/sh "$ROOT/panel.sh" "$mode" --dry-run >"$OUT/panel-$mode.stdout" 2>"$OUT/panel-$mode.stderr" &
 panel_pid=$!
 waited=0
 while ! grep -q '^READY .* live=0 ' "$OUT/panel-$mode.stderr"; do
  kill -0 "$panel_pid" 2>/dev/null || { cat "$OUT/panel-$mode.stderr" >&2; exit 1; }
  [ "$waited" -lt 15 ] || { echo 'Native panel readiness deadline reached' >&2; exit 1; }
  sleep 1; waited=$((waited+1))
 done
 [ "$(readlink "/proc/$panel_pid/exe")" = "$ROOT/bin/nxui.arm" ]
 [ "$(stat -L -c '%d:%i' "/proc/$panel_pid/exe")" = "$(stat -L -c '%d:%i' "$ROOT/bin/nxui.arm")" ]
 printf '%s\n%s\n' "$panel_pid" "$(readlink "/proc/$panel_pid/exe")" >"$OUT/panel-$mode.identity"
 /rescue/busybox tr '\000' '\n' <"/proc/$panel_pid/cmdline" >"$OUT/panel-$mode.argv"
 /rescue/busybox tr '\000' '\n' <"/proc/$panel_pid/environ" | grep -E '^(FONTCONFIG_FILE|DISPLAY|NX300M_MOD_ROOT)=' >"$OUT/panel-$mode.environment"
 cleanup_panel
done
[ ! -e /var/run/nx300m-recipe-ready ]
[ ! -e /var/run/nx300m-lut-hook ]
[ ! -e /var/run/nx300m-lut-panel-backup ]
[ ! -e /var/run/nx300m-lut-selection ]
printf 'VM_SD_DELIVERY passed\n'
'''


UPDATE_PROBE = r'''#!/bin/sh
set -eu
ROOT=/mnt/mmc/nx300m-mod
OUT=/arm-vm/sd-update
export NX300M_MOD_ROOT="$ROOT" DISPLAY=:97 NX_START_TIMEOUT=20 NX_STOP_TIMEOUT=6
[ ! -e "$ROOT/.state/remote.pid" ] || { echo 'Remote already running; refusing replacement probe' >&2; exit 1; }
/rescue/busybox sha256sum -c "$OUT/members.sha256" >"$OUT/members.checks"
quickshare_exit=0
"$ROOT/bin/nx-quickshare-armv7" >"$OUT/quickshare-help.stdout" 2>"$OUT/quickshare-help.stderr" || quickshare_exit=$?
printf '%s\n' "$quickshare_exit" >"$OUT/quickshare-usage.exit"
[ "$quickshare_exit" -eq 1 ] && grep -q '^Error: usage: nx-quickshare ' "$OUT/quickshare-help.stderr"
cleanup() { /bin/sh "$ROOT/stop.sh" >"$OUT/stop.stdout" 2>"$OUT/stop.stderr" || :; }
trap cleanup EXIT HUP INT TERM
/bin/sh "$ROOT/start.sh" >"$OUT/start.stdout" 2>"$OUT/start.stderr"
IFS= read -r pid < "$ROOT/.state/remote.pid"
[ "$(readlink "/proc/$pid/exe")" = "$ROOT/bin/nx-remote-controller.arm" ]
[ "$(stat -L -c '%d:%i' "/proc/$pid/exe")" = "$(stat -L -c '%d:%i' "$ROOT/bin/nx-remote-controller.arm")" ]
cp "$ROOT/.state/remote.pid" "$OUT/remote.identity"
for endpoint in info looks recipes quickshare.js; do
 case $endpoint in
  info) path=/api/v1/camera/info ;;
  looks) path=/api/v1/looks ;;
  recipes) path=/api/v1/color/recipes ;;
  quickshare.js) path=/js/quickshare.js ;;
 esac
 /rescue/busybox wget -q -T 8 -O "$OUT/$endpoint" "http://127.0.0.1:8781$path"
done
[ ! -e /var/run/nx300m-recipe-ready ]
[ ! -e /var/run/nx300m-lut-hook ]
printf 'VM_SD_UPDATE passed\n'
'''


def upload_worker(path):
    """Serial stdin transfer avoids expanding binary data into shell commands."""
    payload = base64.encodebytes(Path(path).read_bytes())
    token = uuid.uuid4().hex
    begin, end = '__VM_UPLOAD_BEGIN_' + token + '__', '__VM_UPLOAD_END_' + token + '__'
    command = ('/rescue/busybox stty -echo; printf "\\n%s\\n" ' + begin +
               '; /rescue/busybox base64 -d > /tmp/sd-native-update.tar.gz; upload_rc=$?; '
               '/rescue/busybox stty echo; printf "\\n%s:%s\\n" ' + end + ' "$upload_rc"\n')
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(.2)
        connection.connect('/tmp/nx300m-arm-vm-serial.sock')
        try:
            while connection.recv(65536):
                pass
        except socket.timeout:
            pass
        connection.settimeout(55)
        connection.sendall(command.encode())
        output = b''
        while begin.encode() + b'\r\n' not in output and begin.encode() + b'\n' not in output:
            block = connection.recv(65536)
            if not block:
                raise RuntimeError('VM serial closed before upload')
            output += block
        connection.sendall(payload + b'\x04')
        output = b''
        while end.encode() + b':0' not in output:
            block = connection.recv(65536)
            if not block:
                raise RuntimeError('VM serial closed during upload')
            output += block
            if re.search(re.escape(end.encode()) + rb':[1-9][0-9]*', output):
                raise RuntimeError('Guest archive upload failed')
    print(json.dumps({'uploaded_bytes': Path(path).stat().st_size, 'guest_decode_succeeded': True}))


def update_delivery(resume=False):
    """Check only the concurrently changed remote/Quick Share/client payload."""
    directory = OUT / 'delivery-update'
    directory.mkdir(exist_ok=True)
    bundle = PROJECT / 'outputs/deployment/sd-bundle/nx300m-mod-sd.tar.gz'
    bundle_data = bundle.read_bytes()
    targets = ('bin/nx-remote-controller.arm', 'bin/nx-quickshare-armv7', 'web_root/js/quickshare.js')
    inputs = {}
    with tarfile.open(fileobj=io.BytesIO(bundle_data), mode='r:gz') as source:
        file_count = sum(m.isfile() for m in source.getmembers())
        for relative in targets:
            inputs[relative] = source.extractfile('nx300m-mod/' + relative).read()
    checks = ''.join(hashlib.sha256(data).hexdigest() + '  ' + SD + '/' + name + '\n'
                     for name, data in inputs.items())
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode='w') as target:
        for relative, data in [*((SD.lstrip('/') + '/' + name, data) for name, data in inputs.items()),
                               ('arm-vm/sd-update/members.sha256', checks.encode()),
                               ('arm-vm/sd-update/probe.sh', UPDATE_PROBE.encode())]:
            item = tarfile.TarInfo(relative)
            item.size = len(data)
            item.mode = 0o755 if '/bin/' in relative or relative.endswith('.sh') else 0o644
            target.addfile(item, io.BytesIO(data))
    archive = directory / 'update.tar.gz'
    if resume:
        with tarfile.open(archive) as previous:
            for name, data in inputs.items():
                if previous.extractfile(SD.lstrip('/') + '/' + name).read() != data:
                    raise ValueError('Changed payload no longer matches the previously uploaded archive')
        for suffix in ('stdout.log', 'stderr.log', '-execution.json'):
            name = 'delivery-update-run' + (suffix if suffix.startswith('-') else '.' + suffix)
            original = OUT / name
            if original.exists():
                original.replace(directory / ('initial-unsupported-help-' + name))
        command = ('cp /arm-vm/sd-update/probe.sh /arm-vm/sd-update/probe.initial.sh; printf %s ' +
                   base64.b64encode(UPDATE_PROBE.encode()).decode() +
                   ' | /rescue/busybox base64 -d > /arm-vm/sd-update/probe.sh && /bin/sh /arm-vm/sd-update/probe.sh')
    else:
        archive.write_bytes(gzip.compress(payload.getvalue(), mtime=0))
        wsl_project = '/mnt/' + PROJECT.drive[0].lower() + PROJECT.as_posix()[2:]
        result = subprocess.run(['wsl.exe', '-u', 'root', '-e', 'python3',
                                 wsl_project + '/tools/probe_sd_vm.py', 'upload-worker',
                                 wsl_project + '/' + archive.relative_to(PROJECT).as_posix()],
                                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=80)
        (directory / 'upload.stdout.log').write_text(result.stdout, encoding='utf-8')
        (directory / 'upload.stderr.log').write_text(result.stderr, encoding='utf-8')
        if result.returncode:
            raise RuntimeError('VM archive transfer failed; inspect delivery-update/upload logs')
        command = '/rescue/busybox tar -xzf /tmp/sd-native-update.tar.gz -C / && /bin/sh /arm-vm/sd-update/probe.sh'
    output = vm_exec(command, 'delivery-update-run', timeout=45)
    if not re.search(r'^VM_SD_UPDATE passed\r?$', output, re.M):
        raise RuntimeError('Changed-payload check did not complete')
    names = ['members.checks', 'quickshare-help.stdout', 'quickshare-help.stderr', 'quickshare-usage.exit', 'remote.identity',
             'start.stdout', 'start.stderr', 'stop.stdout', 'stop.stderr', 'info', 'looks', 'recipes', 'quickshare.js']
    command = ('set -e; for name in ' + ' '.join(names) + '; do '
               'printf "\\n%s%s%s\\n" __VM_SD_FILE_ "$name" __; '
               '/rescue/busybox base64 "/arm-vm/sd-update/$name"; '
               'printf "\\n%s%s%s\\n" __VM_SD_END_ "$name" __; done')
    collected = vm_exec(command, 'delivery-update-collect', timeout=30)
    for name in names:
        pattern = '^' + re.escape('__VM_SD_FILE_' + name + '__') + r'\r?\n([A-Za-z0-9+/=\s]*?)\r?\n' + \
                  re.escape('__VM_SD_END_' + name + '__') + r'\r?$'
        match = re.search(pattern, collected, re.M)
        if not match:
            raise ValueError('Missing changed-payload result: ' + name)
        (directory / name).write_bytes(base64.b64decode(re.sub(r'\s', '', match[1]), validate=True))
    info = json.loads((directory / 'info').read_bytes())
    recipes = json.loads((directory / 'recipes').read_bytes())
    looks = json.loads((directory / 'looks').read_bytes())
    if len(recipes['recipes']) != 68 or len(looks['looks']) != 17 or not info['capabilities'].get('gallery_preview'):
        raise ValueError('Changed ARM remote no longer exposes the shipped catalogues/preview capability')
    if (directory / 'quickshare.js').read_bytes() != inputs['web_root/js/quickshare.js']:
        raise ValueError('HTTP client bytes differ from the changed archive member')
    report = {'passed': True, 'bundle_sha256': hashlib.sha256(bundle_data).hexdigest(), 'archive_file_count': file_count,
              'scope': 'Three changed payload members on the existing ARM VM; one production start/stop, native Quick Share usage, catalogue/capability reads',
              'checked_members': {name: hashlib.sha256(data).hexdigest() for name, data in inputs.items()},
              'recipe_count': 68, 'look_count': 17, 'gallery_preview': True, 'native_quickshare_usage_executed': True,
              'quickshare_usage_exit': int((directory / 'quickshare-usage.exit').read_text()),
              'native_remote_identity_verified': True, 'HTTP_client_bytes_match': True, 'production_scripts_modified': False,
              'ready_file_fabricated': False, 'VM_restarted': False, 'new_quickshare_panel_dynamically_checked': False}
    (directory / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))
    return report


def prepare():
    # Keep the established ELF dependency resolver and stock provenance. Its
    # output directory is private to this probe; prior QEMU-user evidence stays.
    import probe_sd_native
    previous = probe_sd_native.OUT
    try:
        probe_sd_native.OUT = OUT
        metadata = probe_sd_native.stage()
    finally:
        probe_sd_native.OUT = previous
    stage = OUT / 'runtime-files'
    overlay = OUT / 'overlay'
    if overlay.exists():
        if overlay.is_symlink() or overlay.resolve().parent != OUT.resolve():
            raise ValueError('Refusing to replace an overlay outside this probe')
        shutil.rmtree(overlay)
    overlay.mkdir(parents=True, exist_ok=True)
    for source in stage.rglob('*'):
        relative = source.relative_to(stage)
        if relative.as_posix() == 'qemu-arm-static':
            continue
        target = overlay / relative
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    script = overlay / 'arm-vm/sd-probe.sh'
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(PROBE, encoding='utf-8', newline='\n')
    (script.parent / 'post-boot.sh').write_text(POST_BOOT, encoding='utf-8', newline='\n')
    (overlay / 'mnt/mmc/DCIM').mkdir(exist_ok=True)
    files = [p.relative_to(overlay).as_posix() for p in overlay.rglob('*') if p.is_file()]
    report = {
        'overlay': str(overlay), 'probe_script': '/arm-vm/sd-probe.sh',
        'stock_provenance': str(OUT / 'runtime-provenance.json'),
        'bundle_sha256': metadata['bundle_sha256'],
        'expected_look_ids': metadata['expected_look_ids'],
        'expected_recipe_ids': [line.split('|', 1)[0] for line in
                                (stage / SD.lstrip('/') / 'recipes.txt').read_text(encoding='utf-8').splitlines()
                                if line and not line.startswith('#')],
        'file_count': len(files),
        'production_scripts_modified': False, 'qemu_user_in_overlay': False,
        'process_identity_shim_used': False, 'camera_app_in_overlay': False,
        'scope': 'Prepared inputs only; ARM VM execution has not been performed by this command',
    }
    (OUT / 'prepare-report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))
    return report


def delivery(collect_only=False):
    """One start, catalogue reads and dry-run panels; no camera or lifecycle loop."""
    preparation = json.loads((OUT / 'prepare-report.json').read_text(encoding='utf-8'))
    bundle = PROJECT / 'outputs/deployment/sd-bundle/nx300m-mod-sd.tar.gz'
    if hashlib.sha256(bundle.read_bytes()).hexdigest() != preparation['bundle_sha256']:
        raise ValueError('Archive changed after VM preparation')
    with tarfile.open(bundle) as archive:
        members = [m for m in archive if m.isfile()]
        checks = ''.join(hashlib.sha256(archive.extractfile(m).read()).hexdigest() + '  ' +
                         '/mnt/mmc/' + m.name + '\n' for m in members)
    command = 'printf %s ' + base64.b64encode(checks.encode()).decode() + \
              ' | /rescue/busybox base64 -d > /tmp/sd-delivery.members && printf %s ' + \
              base64.b64encode(DELIVERY_PROBE.encode()).decode() + \
              ' | /rescue/busybox base64 -d > /tmp/sd-delivery.sh && /bin/sh /tmp/sd-delivery.sh'
    if collect_only:
        serial = (PROJECT / 'outputs/arm-vm/serial.log').read_text(encoding='utf-8', errors='replace')
        completed = re.search(r'VM_SD_DELIVERY passed[\r\n ]+__ARM_VM_END_[a-f0-9]+__:0(?:\r?\n|$)', serial)
        if not completed:
            raise RuntimeError('No successful completed delivery command is present in the current VM serial log')
        (OUT / 'delivery-completion.txt').write_text(completed[0], encoding='utf-8')
    else:
        output = vm_exec(command, 'delivery-run', timeout=55)
        if not re.search(r'^VM_SD_DELIVERY passed\r?$', output, re.M):
            raise RuntimeError('Delivery check did not finish; inspect delivery-run logs')
    names = ['info.json', 'looks.json', 'recipes.json', 'remote.identity', 'archive-files.checks',
             'start.stdout', 'start.stderr', 'stop.stdout', 'stop.stderr',
             *(f'{name}.dependencies' for name in ('nxui.arm', 'recipe_engine.arm', 'lut_engine.arm')),
             *(f'panel-{mode}.{suffix}' for mode in ('recipes', 'lut')
               for suffix in ('stdout', 'stderr', 'identity', 'argv', 'environment'))]
    command = ('set -e; for name in ' + ' '.join(names) + '; do '
               'printf "\\n%s%s%s\\n" __VM_SD_FILE_ "$name" __; '
               '/rescue/busybox base64 "/arm-vm/sd-delivery/$name"; '
               'printf "\\n%s%s%s\\n" __VM_SD_END_ "$name" __; done')
    collected = vm_exec(command, 'delivery-collect', timeout=30)
    evidence = OUT / 'delivery-results'
    evidence.mkdir(exist_ok=True)
    for name in names:
        pattern = '^' + re.escape('__VM_SD_FILE_' + name + '__') + r'\r?\n([A-Za-z0-9+/=\s]*?)\r?\n' + \
                  re.escape('__VM_SD_END_' + name + '__') + r'\r?$'
        match = re.search(pattern, collected, re.M)
        if not match:
            raise ValueError('Missing delivery result: ' + name)
        (evidence / name).write_bytes(base64.b64decode(re.sub(r'\s', '', match[1]), validate=True))
    info = json.loads((evidence / 'info.json').read_bytes())
    looks = json.loads((evidence / 'looks.json').read_bytes())
    recipes = json.loads((evidence / 'recipes.json').read_bytes())
    if sorted(row['id'] for row in looks['looks']) != preparation['expected_look_ids']:
        raise ValueError('Actual ARM LUT catalogue differs from the bundle')
    if sorted(row['id'] for row in recipes['recipes']) != sorted(preparation['expected_recipe_ids']):
        raise ValueError('Actual ARM recipe catalogue differs from the bundle')
    capabilities = info['capabilities']
    if not capabilities.get('recipes') or not capabilities.get('looks') or not capabilities.get('gallery_preview'):
        raise ValueError('Missing shipped catalogue/gallery-preview capability')
    if capabilities.get('recipe_style') or capabilities.get('lut_selection') or capabilities.get('sensor_liveview') or recipes['ready']:
        raise ValueError('Unexpected camera hardware capability in the service-only VM check')
    for mode, count in (('recipes', len(recipes['recipes'])), ('lut', len(looks['looks']))):
        log = (evidence / f'panel-{mode}.stderr').read_text(encoding='utf-8')
        arguments = (evidence / f'panel-{mode}.argv').read_text(encoding='utf-8').splitlines()
        engine = SD + '/bin/' + ('recipe_engine.arm' if mode == 'recipes' else 'lut_engine.arm')
        if f'live=0 recipes={count}' not in log or arguments[0] != SD + '/bin/nxui.arm' or \
                arguments[arguments.index('--engine') + 1] != engine or '--arm-runner' in arguments:
            raise ValueError('Production panel path/readiness does not match the bundle: ' + mode)
    report = {'passed': True, 'scope': 'Latest production SD archive, one real ARM start and catalogue/panel readiness check; no camera app',
              'bundle_sha256': preparation['bundle_sha256'], 'archive_files_verified': len(members),
              'recipe_count': len(recipes['recipes']), 'look_count': len(looks['looks']),
              'capabilities': {name: capabilities.get(name) for name in
                               ('recipes', 'recipe_style', 'looks', 'lut_selection', 'gallery_preview', 'sensor_liveview')},
              'native_panel_modes': ['recipes', 'lut'], 'native_loader_dependencies_resolved': True,
              'panel_font_configuration_loaded': True, 'native_proc_exe_and_inode_verified': True,
              'production_scripts_modified': False, 'ready_file_fabricated': False,
              'collected_after_host_timeout': collect_only,
              'physical_camera_verified': False, 'live_recipe_application_verified': False}
    (OUT / 'delivery-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))
    return report


def vm_exec(command, label, timeout=90):
    started = time.monotonic()
    arguments = [sys.executable, '-X', 'utf8', str(PROJECT / 'tools/arm_vm.py'),
                 'exec', command, '--timeout', str(timeout)]
    result = subprocess.run(arguments, capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=timeout + 20)
    (OUT / (label + '.stdout.log')).write_text(result.stdout, encoding='utf-8')
    (OUT / (label + '.stderr.log')).write_text(result.stderr, encoding='utf-8')
    record = {'exit_code': result.returncode, 'elapsed_seconds': round(time.monotonic() - started, 3)}
    (OUT / (label + '-execution.json')).write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    try:
        response = json.loads(result.stdout)
    except ValueError as error:
        raise RuntimeError(f'{label}: invalid VM response; inspect preserved stdout/stderr') from error
    if result.returncode or response.get('exit_code'):
        raise RuntimeError(f'{label}: host exit {result.returncode}, guest exit {response.get("exit_code")}; inspect preserved logs')
    return response['output']


def run(collect_only=False):
    if not (OUT / 'prepare-report.json').is_file():
        raise ValueError('Prepare and install the SD overlay into the ARM VM first')
    if collect_only:
        previous = json.loads((OUT / 'run.stdout.log').read_text(encoding='utf-8'))
        if previous.get('exit_code') != 0:
            raise RuntimeError('Cannot collect a previously failed guest lifecycle')
        output = previous['output']
    else:
        output = vm_exec('/bin/sh /arm-vm/sd-probe.sh', 'run')
    if not re.search(r'^VM_SD_PROBE passed\r?$', output, re.M):
        raise RuntimeError('Guest script did not report completion; inspect run.stdout.log')
    names = ['info.json', 'looks.json', 'identity', 'start.stdout', 'start.stderr',
             'repeat.stdout', 'repeat.stderr', 'stop.stdout', 'stop.stderr',
             'remote.log', 'supervisor.log']
    command = ('set -e; for name in ' + ' '.join(names) + '; do '
               'printf "\\n%s%s%s\\n" __VM_SD_FILE_ "$name" __; '
               '/rescue/busybox base64 "/arm-vm/sd-results/$name"; '
               'printf "\\n%s%s%s\\n" __VM_SD_END_ "$name" __; done; '
               'for name in start.sh stop.sh common.sh service.sh; do '
               'printf "\\n%s%s%s\\n" __VM_SD_SOURCE_ "$name" __; '
               '/rescue/busybox base64 "/mnt/mmc/nx300m-mod/$name"; '
               'printf "\\n%s%s%s\\n" __VM_SD_END_ "$name" __; done')
    collected = vm_exec(command, 'collect', timeout=30)
    evidence = OUT / 'guest-results'
    evidence.mkdir(exist_ok=True)

    def extract(prefix, name):
        pattern = '^' + re.escape(prefix + name + '__') + r'\r?\n([A-Za-z0-9+/=\s]*?)\r?\n' + re.escape('__VM_SD_END_' + name + '__') + r'\r?$'
        match = re.search(pattern, collected, re.M)
        if not match:
            raise ValueError('Missing guest result: ' + name)
        return base64.b64decode(re.sub(r'\s', '', match[1]), validate=True)

    for name in names:
        (evidence / name).write_bytes(extract('__VM_SD_FILE_', name))
    unchanged = []
    for name in ['start.sh', 'stop.sh', 'common.sh', 'service.sh']:
        observed = extract('__VM_SD_SOURCE_', name)
        expected = (OUT / 'overlay' / SD.lstrip('/') / name).read_bytes()
        if observed != expected:
            raise ValueError('Guest production script differs from prepared bundle: ' + name)
        unchanged.append(name)
    info = json.loads((evidence / 'info.json').read_bytes())
    looks = json.loads((evidence / 'looks.json').read_bytes())
    preparation = json.loads((OUT / 'prepare-report.json').read_text(encoding='utf-8'))
    if info.get('model') != 'NX300M':
        raise ValueError('Unexpected model from actual ARM service')
    ids = sorted(item['id'] for item in looks['looks'])
    if ids != preparation['expected_look_ids'] or looks['hook']['ready']:
        raise ValueError('Deployed catalog mismatch or unexpected camera hook in service-only VM')
    report = {'passed': True, 'scope': 'Unchanged production SD shell lifecycle and ARM remote under a real ARM Linux kernel; no camera app',
              'unchanged_production_scripts': unchanged, 'native_proc_exe_and_inode_verified': True,
              'start_and_HTTP_succeeded': True, 'duplicate_start_same_pid': True, 'stop_and_reap_succeeded': True,
              'model': info['model'], 'look_count': len(ids), 'camera_hook_ready': False,
              'process_identity_shim_used': False, 'ready_file_fabricated': False,
              'physical_camera_verified': False, 'stock_camera_kernel_executed': False}
    (OUT / 'probe-report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'run', 'collect', 'delivery', 'delivery-collect', 'update-delivery', 'update-resume', 'upload-worker'])
    parser.add_argument('extra', nargs='?')
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare()
    elif args.action in ('delivery', 'delivery-collect'):
        delivery(collect_only=args.action == 'delivery-collect')
    elif args.action in ('update-delivery', 'update-resume'):
        update_delivery(resume=args.action == 'update-resume')
    elif args.action == 'upload-worker':
        upload_worker(args.extra)
    else:
        run(collect_only=args.action == 'collect')


if __name__ == '__main__':
    main()
