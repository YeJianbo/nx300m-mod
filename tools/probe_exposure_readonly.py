"""Exercise the read-only bridge in an independent genuine stock camera session."""
import argparse
import http.client
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
GUEST = Path('/opt/nx300m-lab/stock-1.15')
OUT = ROOT / 'outputs/deployment/mfw-exposure-test/runtime'


def linux(path):
    return '/mnt/' + path.drive[0].lower() + path.as_posix()[2:]


def worker(output):
    import fcntl
    run = GUEST / 'var/run'
    report = {'status': 'failed', 'scope': 'genuine stock app with synthetic hardware backends',
              'physical_camera_verified': False, 'hardware_restart_verified': False,
              'requests': []}
    remote = None
    try:
        deadline = time.monotonic() + 22
        ready = run / 'nx300m-exposure-ready'
        while not ready.exists():
            if time.monotonic() > deadline:
                raise TimeoutError('read-only Ecore bridge readiness')
            time.sleep(.1)
        camera_pid = int(ready.read_text().strip())
        if int((run / 'nx300m-exposure-readonly').read_text().strip()) != camera_pid:
            raise RuntimeError('read-only marker does not identify the ready process')
        # The guest procfs is mounted in the camera PID namespace; the worker's
        # host /proc retains outer PID numbers even after nsenter --pid.
        camera_proc = GUEST / 'proc' / str(camera_pid)
        command = (camera_proc / 'cmdline').read_bytes().split(b'\0')
        if b'/usr/bin/di-camera-app-nx300' not in command:
            raise RuntimeError(f'not the genuine stock camera command: {command!r}')
        maps = (camera_proc / 'maps').read_text()
        if '/tmp/exposure_status.so' not in maps or '/tmp/exposure_controls.so' in maps:
            raise RuntimeError('unexpected exposure module mappings')
        report['camera_pid'] = camera_pid
        report['camera_cmdline'] = [part.decode() for part in command if part]
        (output / 'camera.maps').write_text(maps)
        # Stock initialization continues across its first Ecore timer.
        time.sleep(12)

        def rpc(data_id, operation=0, requested=0):
            with (run / 'nx300m-exposure-lock').open('a') as lock:
                os.fchown(lock.fileno(), ready.stat().st_uid, ready.stat().st_gid)
                fcntl.flock(lock, fcntl.LOCK_EX)
                request_id = time.time_ns() % 0x7ffffffe + 1
                temporary = run / 'nx300m-exposure-request.tmp'
                temporary.write_text(f'{request_id} {int(time.time())+6} {operation} {data_id:x} {requested:x}\n')
                temporary.replace(run / 'nx300m-exposure-request')
                until = time.monotonic() + 7
                while time.monotonic() < until:
                    try:
                        fields = (run / 'nx300m-exposure-response').read_text().split()
                    except FileNotFoundError:
                        fields = []
                    if fields and fields[0] == str(request_id):
                        if len(fields) != 15:
                            raise RuntimeError(f'invalid bridge response: {fields}')
                        report['requests'].append({'data_id': data_id, 'operation': operation, 'fields': fields})
                        return fields
                    time.sleep(.05)
                raise TimeoutError(f'bridge request {request_id}')

        before = {}
        for data_id in (0x37, 0x38, 0x3b):
            fields = rpc(data_id)
            if int(fields[1]) != 0 or fields[4:6] != ['0', '0'] or fields[13] != '0':
                raise RuntimeError(f'read failed or reported a write: {fields}')
            before[data_id] = int(fields[3], 16)
        rejected = rpc(0x3b, 1, 0x003b0011)
        if int(rejected[1]) != -120 or rejected[4:6] != ['0', '0']:
            raise RuntimeError(f'direct write was not rejected: {rejected}')
        report['direct_write_rejected'] = True

        binary = GUEST / 'tmp/nx-remote-readonly.arm'
        shutil.copyfile(ROOT / 'outputs/metol/remote/nx-remote-controller.arm', binary)
        binary.chmod(0o755)
        subprocess.run(['ip', 'link', 'set', 'lo', 'up'], check=True)
        env = dict(os.environ, DISPLAY=':99')
        with (output / 'remote.log').open('wb') as log:
            remote = subprocess.Popen(['chroot', '--userspec=65534:65534', str(GUEST),
                                       '/qemu-arm-static', '-cpu', 'cortex-a9', '-L', '/',
                                       '/tmp/nx-remote-readonly.arm'], env=env,
                                      stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)

        def request_json(path, method='GET', body=None):
            connection = http.client.HTTPConnection('127.0.0.1', 8781, timeout=10)
            try:
                connection.request(method, path, body, {'Content-Type': 'application/json'})
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()

        until = time.monotonic() + 6
        while True:
            try:
                status, info = request_json('/api/v1/camera/info')
                break
            except OSError:
                if remote.poll() is not None or time.monotonic() > until:
                    raise RuntimeError('native remote did not become available; see remote.log')
                time.sleep(.1)
        report['camera_info'] = info
        if status != 200 or info['capabilities']['exposure_status'] is not True or info['capabilities']['iso_controls'] is not False:
            raise RuntimeError('native remote misreported the read-only capability')
        status, state = request_json('/api/v1/exposure/iso')
        report['http_read'] = {'status': status, 'body': state}
        if status != 200 or state.get('read_only') is not True:
            raise RuntimeError('native exposure GET did not report read-only state')
        for name, data_id in (('iso_step', 0x37), ('iso_expansion', 0x38), ('auto_iso_max', 0x3b)):
            if int(state['controls'][name]['actual_raw'], 16) != before[data_id]:
                raise RuntimeError(f'HTTP and direct readback differ: {name}')
        status, error = request_json('/api/v1/exposure/iso', 'POST', '{"auto_iso_max":25600}')
        report['http_write'] = {'status': status, 'body': error}
        if status != 403 or error.get('read_only') is not True:
            raise RuntimeError('native remote did not refuse the write')
        after = {data_id: rpc(data_id) for data_id in before}
        if any(int(fields[1]) or int(fields[3], 16) != before[data_id] for data_id, fields in after.items()):
            raise RuntimeError('stock values changed after refused writes')
        report.update(status='passed', stock_values_unchanged=True)
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        if remote is not None and remote.poll() is None:
            remote.terminate()
            try:
                remote.wait(timeout=3)
            except subprocess.TimeoutExpired:
                remote.kill()
                remote.wait(timeout=3)
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return int(report['status'] != 'passed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--output-dir', type=Path, default=OUT)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if args.worker:
        raise SystemExit(worker(output))
    if os.name != 'nt':
        parser.error('Launch with project Conda on Windows')
    if output == ROOT / 'outputs/firmware-runtime':
        parser.error('An independent output directory is required')
    output.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, '-X', 'utf8', str(ROOT / 'tools/run_stock.py'),
               '--program', 'camera', '--display', '--preferences', '--gpio-preview',
               '--device-preview', '--shutter-preview', '--shutter-park', '10',
               '--isp-preview', '--spi-mmio', '--sensor-registers', '--d4c-registers',
               '--capture-boot', '--mmap-script-fix', '--keep-awake', '--exposure-read-only',
               '--no-strace', '--qemu', str(ROOT / 'work/qemu/qemu-arm-static'),
               '--seconds', '55', '--output-dir', str(output)]
    with (output / 'launcher.log').open('wb') as log:
        started = time.time()
        process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        try:
            selected = output / 'session.json'
            deadline = time.monotonic() + 25
            while not selected.exists() or selected.stat().st_mtime < started:
                if process.poll() is not None:
                    raise RuntimeError('camera launcher exited; see launcher.log')
                if time.monotonic() > deadline:
                    raise TimeoutError('new private session identity')
                time.sleep(.1)
            session = json.loads(selected.read_text())
            result = subprocess.run(['wsl.exe', '-u', 'root', '-e', 'nsenter', '-t',
                                     str(session['namespace_pid']), '--mount', '--pid', '--net',
                                     'python3', linux(ROOT / 'tools/probe_exposure_readonly.py'),
                                     '--worker', '--output-dir', linux(output)],
                                    capture_output=True, timeout=48)
            (output / 'worker.stdout.log').write_bytes(result.stdout)
            (output / 'worker.stderr.log').write_bytes(result.stderr)
            print(result.stdout.decode('utf-8', 'replace'), flush=True)
            if result.returncode:
                raise RuntimeError(f'worker exit {result.returncode}: {result.stderr.decode("utf-8", "replace")}')
        finally:
            # This invocation owns the bounded namespace; never terminate another session.
            process.wait(timeout=65)


if __name__ == '__main__':
    main()
