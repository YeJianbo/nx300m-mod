"""Exercise verified firmware entry hooks in a bounded, independent stock camera.

The custom-panel probe uses explicit QEMU interpreter bridges only inside that
camera's mount namespace. It does not alter production scripts or global binfmt.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
GUEST = Path('/opt/nx300m-lab/stock-1.15')
DEFAULT = ROOT/'outputs/firmware-runtime/firmware-mod-runtime'


def linux(path):
    return '/mnt/'+path.drive[0].lower()+path.as_posix()[2:]


def worker(output, bundle):
    import fcntl
    import ctypes
    run = GUEST/'var/run'
    results = []
    report = {'kind': 'genuine-stock-entry-exercises', 'results': results,
              'physical_camera': 'not_run', 'global_binfmt_modified': False,
              'firmware_module_source': str(ROOT/'outputs/controls/camera_firmware_mod.so'),
              'panel_bundle': str(bundle)}

    def save():
        (output/'advanced-control-results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')

    def rpc(name, operation=0, value=0):
        with (run/'nx300m-firmware-mod-lock').open('w') as lock:
            owner = (run/'nx300m-firmware-mod-ready').stat()
            os.fchown(lock.fileno(), owner.st_uid, owner.st_gid)
            fcntl.flock(lock, fcntl.LOCK_EX)
            request_id = int(time.time_ns() % 0x7ffffffe)+1
            temporary = run/'nx300m-firmware-mod-request.tmp'
            temporary.write_text(f'{request_id} {int(time.time())+6} {operation} {value}\n')
            temporary.replace(run/'nx300m-firmware-mod-request')
            deadline = time.monotonic()+8
            while time.monotonic() < deadline:
                try:
                    fields = [int(v) for v in (run/'nx300m-firmware-mod-response').read_text().split()]
                except FileNotFoundError:
                    fields = []
                if fields and fields[0] == request_id:
                    if len(fields) != 26 or fields[1]:
                        raise RuntimeError(f'{name}: response {fields}')
                    record = {'name': name, 'fields': fields}
                    results.append(record); save()
                    return fields
                time.sleep(.03)
            raise TimeoutError(name)

    try:
        deadline = time.monotonic()+14
        while not (run/'nx300m-firmware-mod-ready').exists():
            if time.monotonic() > deadline: raise TimeoutError('firmware ready')
            time.sleep(.1)
        # The first timer can run before the original capture framework finishes
        # its startup wait. Avoid issuing a six-second control across that wait.
        time.sleep(12)
        initial = rpc('initial')
        assert initial[17] == 31, initial
        assert initial[15] == 1, initial
        rpc('physical-lock-on', 4, 1)
        blocked = rpc('physical-manager-entry', 8, 1)
        assert blocked[18:20] == [1, 1], blocked
        assert rpc('software-A-while-locked', 1, 3)[3] == 3
        rpc('animation-instant', 5, 1)
        instant = rpc('instant-original-draw-entry', 8, 2)
        assert instant[21] > blocked[21]
        rpc('animation-hidden', 5, 2)
        hidden = rpc('hidden-original-draw-entry', 8, 2)
        assert hidden[21] > instant[21]
        rpc('animation-stock', 5, 0)
        rpc('software-P', 1, 2)
        rpc('physical-lock-off', 4, 0)
        locked = rpc('iso-lock6400', 7, 6400)
        assert locked[25] == 0x003b000f
        rebound = rpc('original-iso-set3200-attempt', 8, 4)
        assert rebound[24] > locked[24] and rebound[25] == 0x003b000f
        rpc('iso-lock-off', 7, 0)

        # This is the same native panel and script as the package. Only kernel
        # ARM executable dispatch is bridged, since global binfmt is absent.
        from lab_panel_runtime import prepare
        report['interpreter_bridge'] = prepare(GUEST, bundle)
        before = rpc('before-custom')
        custom = rpc('original-custom-key-entry', 8, 3)
        assert custom[22] > before[22] and custom[23] > before[23]
        processes = []
        deadline = time.monotonic()+5
        while time.monotonic() < deadline:
            for process in (GUEST/'proc').iterdir():
                if not process.name.isdecimal(): continue
                try: command = (process/'cmdline').read_bytes().replace(b'\0', b' ')
                except (FileNotFoundError, ProcessLookupError): continue
                if b'/mod/bin/nxui.arm.native ' in command:
                    processes = [{'pid': int(process.name), 'argv': command.decode('utf-8', 'replace')}]
                    break
            if processes: break
            time.sleep(.1)
        report['custom_panel_processes'] = processes
        x11 = ctypes.CDLL('libX11.so.6')
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x11.XDefaultRootWindow.restype = ctypes.c_ulong
        x11.XQueryTree.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                                  ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong),
                                  ctypes.POINTER(ctypes.POINTER(ctypes.c_ulong)), ctypes.POINTER(ctypes.c_uint)]
        x11.XFetchName.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_char_p)]
        x11.XFree.argtypes = [ctypes.c_void_p]
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        display = x11.XOpenDisplay(b':99')
        assert display, 'Native panel X display unavailable'
        windows = []
        deadline = time.monotonic()+5
        while time.monotonic() < deadline:
            children = ctypes.POINTER(ctypes.c_ulong)()
            parent, root, count = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_uint()
            assert x11.XQueryTree(display, x11.XDefaultRootWindow(display), ctypes.byref(root),
                                  ctypes.byref(parent), ctypes.byref(children), ctypes.byref(count))
            windows = []
            for index in range(count.value):
                name = ctypes.c_char_p()
                x11.XFetchName(display, children[index], ctypes.byref(name))
                windows.append({'id': children[index], 'name': name.value.decode('utf-8', 'replace') if name.value else None})
                if name: x11.XFree(name)
            if children: x11.XFree(children)
            if any(window['name'] == 'Metol-NX300M' for window in windows):
                break
            time.sleep(.1)
        report['custom_panel_windows'] = windows
        assert processes, 'Native settings panel did not stay running'
        assert any(window['name'] == 'Metol-NX300M' for window in windows), 'Native settings panel window was not created'
        assert '--live ' in processes[0]['argv'], 'Custom panel is only browsing'
        window = next(window['id'] for window in windows if window['name'] == 'Metol-NX300M')
        xtst = ctypes.CDLL('libXtst.so.6')
        xtst.XTestFakeMotionEvent.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
        xtst.XTestFakeButtonEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
        xtst.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
        x11.XTranslateCoordinates.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong,
            ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_ulong)]
        x11.XFlush.argtypes = [ctypes.c_void_p]
        x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        x11.XKeysymToKeycode.restype = ctypes.c_uint

        def tap(column):
            # UI logical first-row center (x=180/540,y=70), rotated by NXUI.
            x, y, child = ctypes.c_int(), ctypes.c_int(), ctypes.c_ulong()
            assert x11.XTranslateCoordinates(display, window, x11.XDefaultRootWindow(display),
                335-70, 180 if column == 0 else 540, ctypes.byref(x), ctypes.byref(y), ctypes.byref(child))
            xtst.XTestFakeMotionEvent(display, -1, x.value, y.value, 0)
            xtst.XTestFakeButtonEvent(display, 1, 1, 0)
            x11.XFlush(display); time.sleep(.12)
            xtst.XTestFakeButtonEvent(display, 1, 0, 0)
            x11.XFlush(display); time.sleep(.25)

        def wait_lock(name, expected):
            deadline = time.monotonic()+3
            while True:
                fields = rpc(name)
                if fields[13] == expected: return fields
                if time.monotonic() > deadline: raise AssertionError(f'{name}: menu did not change real bridge state')
                time.sleep(.15)

        # XStoreName precedes font/backend setup; wait for the actual rendered
        # frame rather than a guessed delay before sending menu input.
        session = json.loads((output/'session.json').read_text())
        panel_log = output/session['stderr_log']
        deadline = time.monotonic()+5
        while b'FIRST_FRAME process_ms=' not in panel_log.read_bytes():
            if time.monotonic() > deadline: raise AssertionError('Custom panel never rendered its first frame')
            time.sleep(.1)
        tap(1)  # Main settings -> dial and shortcut submenu.
        tap(0)  # Physical mode lock on.
        wait_lock('custom-menu-physical-lock-on', 1)
        tap(1)  # Physical mode lock off.
        wait_lock('custom-menu-physical-lock-off', 0)
        code = x11.XKeysymToKeycode(display, 0xffeb)  # Super_L: original simulator S1.
        assert code
        xtst.XTestFakeKeyEvent(display, code, 1, 0)
        x11.XFlush(display); time.sleep(.1)
        xtst.XTestFakeKeyEvent(display, code, 0, 0)
        x11.XFlush(display)
        deadline = time.monotonic()+4
        while (GUEST/'proc'/str(processes[0]['pid'])/'cmdline').exists():
            if not (GUEST/'proc'/str(processes[0]['pid'])/'cmdline').read_bytes(): break
            if time.monotonic() > deadline: raise AssertionError('Custom settings did not exit on half press')
            time.sleep(.1)
        report['custom_panel_half_press_exit'] = True
        x11.XCloseDisplay(display)
        rpc('custom-off', 6, 0)
        report['status'] = 'passed'
    except Exception as exc:
        report['status'] = 'failed'
        report['error'] = f'{type(exc).__name__}: {exc}'
    save()
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return int(report['status'] != 'passed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=DEFAULT)
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--bundle', type=Path, default=ROOT/'outputs/deployment/sd-bundle/nx300m-mod')
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if args.worker: raise SystemExit(worker(output, args.bundle.resolve()))
    if os.name != 'nt': parser.error('Launch using Windows project Conda')
    command = [sys.executable, '-X', 'utf8', str(ROOT/'tools/run_stock.py'),
               '--program', 'camera', '--display', '--preferences', '--gpio-preview',
               '--device-preview', '--shutter-preview', '--shutter-park', '10',
               '--isp-preview', '--spi-mmio', '--sensor-registers', '--d4c-registers',
               '--capture-boot', '--mmap-script-fix', '--ui-mod', '--keep-awake',
               '--firmware-mod', '--firmware-entry-diagnostics', '--color-style',
               '--exposure-controls', '--shutter-limit', '--no-strace',
               '--seconds', '55', '--tag', 'firmware-advanced', '--output-dir', str(output)]
    output.mkdir(parents=True, exist_ok=True)
    with (output/'advanced-launcher.log').open('wb') as log:
        process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        try:
            old_time = time.time()
            selected = output/'session.json'
            deadline = time.monotonic()+25
            while not selected.exists() or selected.stat().st_mtime < old_time:
                if process.poll() is not None: raise RuntimeError('camera launcher exited')
                if time.monotonic() > deadline: raise TimeoutError('new private session identity')
                time.sleep(.1)
            session = json.loads(selected.read_text())
            result = subprocess.run(['wsl.exe', '-u', 'root', '-e', 'nsenter', '-t',
                                     str(session['namespace_pid']), '--mount', '--pid',
                                     'python3', linux(ROOT/'tools/probe_firmware_mod_runtime.py'),
                                     '--worker', '--output-dir', linux(output),
                                     '--bundle', linux(args.bundle.resolve())],
                                    capture_output=True, timeout=42)
            (output/'advanced-worker.stdout.log').write_bytes(result.stdout)
            (output/'advanced-worker.stderr.log').write_bytes(result.stderr)
            print(result.stdout.decode('utf-8', 'replace'))
            if result.returncode: raise RuntimeError(f'worker exit {result.returncode}: {result.stderr.decode("utf-8", "replace")}')
        finally:
            # run_stock owns its 55-second namespace timeout. Never terminate a
            # distro, another session or a PID saved by an earlier invocation.
            process.wait(timeout=65)


if __name__ == '__main__':
    main()
