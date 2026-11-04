"""Check real ARM dirlist and XTest input in private namespaces and Xvfb."""
from wsl_paths import wsl_path as linux
import argparse
import ctypes as C
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/review/runtime-locks'
GUEST = '/opt/nx300m-lab/stock-1.15'


class InputEvent(C.Structure):
    _fields_ = [('type', C.c_int), ('serial', C.c_ulong), ('send_event', C.c_int), ('display', C.c_void_p),
                ('window', C.c_ulong), ('root', C.c_ulong), ('subwindow', C.c_ulong), ('time', C.c_ulong),
                ('x', C.c_int), ('y', C.c_int), ('x_root', C.c_int), ('y_root', C.c_int),
                ('state', C.c_uint), ('detail', C.c_uint), ('same_screen', C.c_int)]


class Event(C.Union):
    _fields_ = [('type', C.c_int), ('input', InputEvent), ('pad', C.c_long * 24)]


def request(path):
    try:
        with urllib.request.urlopen('http://127.0.0.1:18781' + path, timeout=4) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


def worker(args):
    subprocess.run(['mount', '--make-rprivate', '/'], check=True)
    for target in ('/var/run', '/tmp'):
        subprocess.run(['mount', '-t', 'tmpfs', '-o', 'mode=1777,nosuid,nodev', 'tmpfs', target], check=True)
    subprocess.run(['ip', 'link', 'set', 'lo', 'up'], check=True)
    report = {'scope': 'Real ARM remote HTTP and XTest on private Xvfb; generated files only.',
              'camera_started': False, 'main_simulator_changed': False, 'physical_camera_verified': False}
    with tempfile.TemporaryDirectory(prefix='nx-remote-interaction-') as temporary:
        base = Path(temporary)
        media, web = base / 'DCIM', base / 'web'
        media.mkdir(); web.mkdir(); (media / 'Album').mkdir()
        expected = {}
        for name in [*(f'SAM_{i:04d}.JPG' for i in range(128)), 'quote".JPG', 'line\nbreak.JPG', '风格测试.JPG']:
            data = name.encode('utf-8')
            (media / name).write_bytes(data); expected[name] = len(data)
        (media / '.hidden.JPG').write_bytes(b'hidden')
        (base / 'outside.JPG').write_bytes(b'outside')
        (media / 'inside.JPG').symlink_to(media / 'SAM_0000.JPG')
        expected['inside.JPG'] = expected['SAM_0000.JPG']
        (media / 'escape.JPG').symlink_to(base / 'outside.JPG')
        os.mkfifo(media / 'pipe')
        desired = {'ok': True, 'path': '', 'dirs': ['Album'], 'files': sorted(expected), 'sizes': expected}

        def directory(binary, label, check_input=False):
            log_path = base / (label + '-remote.log')
            with log_path.open('wb') as log:
                command = [GUEST + '/qemu-arm-static', '-strace', '-cpu', 'cortex-a9', '-L', GUEST,
                           '-E', 'LD_LIBRARY_PATH=' + GUEST + '/usr/lib:' + GUEST + '/lib', str(binary),
                           '--listen', '127.0.0.1:18781', '--web-root', str(web), '--media-root', str(media)]
                environment = {**os.environ, 'DISPLAY': ':99', 'LC_ALL': 'C', 'NX_REMOTE_LAB_INPUT': '1'}
                process = subprocess.Popen(command, stdout=log, stderr=log, env=environment)
                try:
                    deadline = time.monotonic() + 6
                    while True:
                        try:
                            assert request('/api/v1/camera/info')[0] == 200
                            break
                        except OSError:
                            if process.poll() is not None or time.monotonic() > deadline:
                                raise RuntimeError('Remote startup failed: ' + log_path.read_text(errors='replace')[-2000:])
                            time.sleep(.05)
                    started = time.perf_counter()
                    code, listing = request('/cgi-bin/dirlist')
                    elapsed = (time.perf_counter() - started) * 1000
                    assert code == 200 and listing == desired, (code, listing)
                    assert request('/cgi-bin/dirlist?p=..')[0] == 404
                    assert request('/cgi-bin/dirlist?p=escape.JPG')[0] == 404
                    code, empty = request('/cgi-bin/dirlist?p=Album')
                    assert code == 200 and empty == {'ok': True, 'path': 'Album', 'dirs': [], 'files': [], 'sizes': {}}
                    if check_input:
                        report['input'] = inputs()
                finally:
                    process.terminate()
                    try: process.wait(timeout=4)
                    except subprocess.TimeoutExpired:
                        process.kill(); process.wait()
                    (args.output / log_path.name).write_bytes(log_path.read_bytes())
            trace = log_path.read_text(errors='replace')
            count = len(re.findall(r'(?<![a-z])stat64\("' + re.escape(str(media / 'SAM_0001.JPG')) + r'"', trace))
            return {'listing_matches': True, 'sample_file_stat_calls': count,
                    'single_dirlist_ms_under_qemu_strace': round(elapsed, 3),
                    'worker_starts': len(re.findall('NX_X11_WORKER pid=', trace))}

        xvfb_log = (args.output / 'input-xvfb.log').open('wb')
        xvfb = subprocess.Popen(['Xvfb', ':99', '-screen', '0', '480x800x24', '-nolisten', 'tcp', '-ac'],
                                stdout=xvfb_log, stderr=xvfb_log)
        x = C.CDLL('libX11.so.6')
        x.XOpenDisplay.argtypes = [C.c_char_p]; x.XOpenDisplay.restype = C.c_void_p
        x.XDefaultRootWindow.argtypes = [C.c_void_p]; x.XDefaultRootWindow.restype = C.c_ulong
        x.XCreateSimpleWindow.argtypes = [C.c_void_p, C.c_ulong, C.c_int, C.c_int, C.c_uint, C.c_uint,
                                         C.c_uint, C.c_ulong, C.c_ulong]
        x.XCreateSimpleWindow.restype = C.c_ulong
        for name in ('XSelectInput', 'XMapWindow', 'XDestroyWindow'):
            getattr(x, name).argtypes = [C.c_void_p, C.c_ulong] + ([C.c_long] if name == 'XSelectInput' else [])
        x.XSync.argtypes = [C.c_void_p, C.c_int]
        x.XPending.argtypes = [C.c_void_p]
        x.XNextEvent.argtypes = [C.c_void_p, C.POINTER(Event)]
        x.XCloseDisplay.argtypes = [C.c_void_p]
        display = None
        try:
            deadline = time.monotonic() + 4
            while not display:
                display = x.XOpenDisplay(b':99')
                if not display and time.monotonic() > deadline: raise RuntimeError('Private Xvfb unavailable')
                if not display: time.sleep(.05)
            window = x.XCreateSimpleWindow(display, x.XDefaultRootWindow(display), 0, 0, 480, 800, 0, 0, 0)
            x.XSelectInput(display, window, 1 | 2 | 4 | 8 | 64)
            x.XMapWindow(display, window); x.XSync(display, 0)

            def drain():
                events = []
                while x.XPending(display):
                    event = Event(); x.XNextEvent(display, C.byref(event))
                    events.append((event.type, event.input.detail, event.input.x, event.input.y))
                return events

            def inputs():
                drain()
                assert request('/api/v1/input/inject?key=Menu&bind=1') == (200, {'ok': True})
                keys = drain()
                assert [event[0] for event in keys if event[0] in (2, 3)] == [2, 3], keys
                for name in ('XF86ScrollUp', 'XF86ScrollDown', 'XF86ScrollUp'):
                    assert request('/api/v1/input/inject?key=' + name + '&bind=1') == (200, {'ok': True})
                    assert [e[0] for e in drain() if e[0] in (2, 3)] == [2, 3]
                assert request('/api/v1/input/inject?click=123-74') == (200, {'ok': True})
                clicks = drain()
                assert [(t, detail, px, py) for t, detail, px, py in clicks if t in (4, 5)] == [
                    (4, 1, 405, 123), (5, 1, 405, 123)], clicks
                assert request('/api/v1/input/inject?click=800-0')[0] == 400
                assert not [e for e in drain() if e[0] in (2, 3, 4, 5)], 'invalid click produced input'
                return {'key_press_release': True, 'dial_key_rebinding': True, 'click_order_and_portrait_rotation': True,
                        'invalid_click_not_injected': True}

            if args.baseline: report['baseline'] = directory(args.baseline, 'baseline')
            report['candidate'] = directory(args.binary, 'candidate', check_input=True)
            assert report['candidate']['sample_file_stat_calls'] == 1, report
            assert report['candidate']['worker_starts'] == 1, report
            if args.baseline: assert report['baseline']['sample_file_stat_calls'] == 3, report
            report['passed'] = True
        finally:
            if display: x.XCloseDisplay(display)
            xvfb.terminate()
            try: xvfb.wait(timeout=3)
            except subprocess.TimeoutExpired: xvfb.kill(); xvfb.wait()
            xvfb_log.close()
    (args.output / 'interaction.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--binary', type=Path, default=ROOT / 'outputs/metol/remote/nx-remote-controller.arm')
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--output', type=Path, default=OUT)
    args = parser.parse_args()
    if args.worker: worker(args); return
    args.output.mkdir(parents=True, exist_ok=True)
    
    command = ['wsl.exe', '-u', 'root', '-e', 'unshare', '--mount', '--net', '--ipc', '--pid', '--fork',
               '--mount-proc', '--kill-child', 'python3', linux(Path(__file__).resolve()), '--worker',
               '--binary', linux(args.binary.resolve()), '--output', linux(args.output.resolve())]
    if args.baseline: command.extend(['--baseline', linux(args.baseline.resolve())])
    run = subprocess.run(command, capture_output=True, timeout=40)
    (args.output / 'interaction.stdout.log').write_bytes(run.stdout)
    (args.output / 'interaction.stderr.log').write_bytes(run.stderr)
    print(run.stdout.decode('utf-8', 'replace'))
    if run.returncode:
        raise RuntimeError(f'Interaction probe exit {run.returncode}: ' + run.stderr.decode('utf-8', 'replace'))


if __name__ == '__main__': main()
