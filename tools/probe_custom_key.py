"""Exercise the production ARM Custom helper in a private stock QEMU session.

Only the generated test context adapts process identity and the QEMU address
bias. Accessor bytes and preview/Quickview checks stay unchanged. Camera memory
is always opened read-only. No firmware-mod or Custom SO is loaded.
"""
import argparse
import ctypes
import difflib
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import struct
import subprocess
import sys
import time
import urllib.request
import zipfile

PROJECT = Path(__file__).resolve().parents[1]
GUEST = '/opt/nx300m-lab/stock-1.15'
DEFAULT_OUTPUT = PROJECT/'outputs/deployment/custom-key-test'


def linux_path(path):
    path = Path(path).resolve()
    return '/mnt/'+path.drive[0].lower()+path.as_posix()[2:]


def generate_fixture(output):
    source = output/'source/metol/remote'
    source.mkdir(parents=True, exist_ok=True)
    shutil.copytree(PROJECT/'mod/metol/remote', source, dirs_exist_ok=True)
    for name in ('mod_paths.h', 'lut_engine_shared.h'):
        shutil.copyfile(PROJECT/'mod/metol'/name, source.parent/name)
    runtime = output/'runtime'
    runtime.mkdir(exist_ok=True)
    shutil.copyfile(PROJECT/'runtime/lut_ram_blob.h', runtime/'lut_ram_blob.h')
    path = source/'custom_key_context.h'
    original = path.read_text()
    adapted = original.replace('struct custom_key_context {',
                              'static off_t lab_bias;\nstruct custom_key_context {', 1)
    adapted = adapted.replace('(off_t)address', '((off_t)address+lab_bias)')
    adapted = adapted.replace('    if(lseek(context->fd,',
                              '    if(!access("/tmp/nx-custom-lab-read-failure",F_OK))return 0;\n    if(lseek(context->fd,', 1)
    adapted = adapted.replace('static int custom_key_context_preview(struct custom_key_context *context) {',
        'static int custom_key_context_preview(struct custom_key_context *context) {\n'
        '    if(!access("/tmp/nx-custom-lab-preview-stall",F_OK))sleep(5);', 1)
    start = adapted.index('static int custom_key_context_open(')
    end = adapted.index('/* The singleton application', start)
    replacement = r'''/* TEST TRANSPORT ONLY: pin the real QEMU process and map ARM VAs. */
static int custom_key_context_open(struct custom_key_context *context) {
    memset(context,0,sizeof *context);context->fd=-1;
    const char *pid=getenv("NX_CUSTOM_LAB_PID"),*bias=getenv("NX_CUSTOM_LAB_BIAS");
    if(!pid || !bias)return 0;
    context->pid=atoi(pid);lab_bias=(off_t)strtoull(bias,NULL,0);
    char path[64];struct stat executable;
    snprintf(path,sizeof path,"/proc/%d/exe",context->pid);
    if(stat(path,&executable) || !custom_key_context_start(context->pid,context->start))return 0;
    context->device=executable.st_dev;context->inode=executable.st_ino;
    snprintf(path,sizeof path,"/proc/%d/mem",context->pid);
    context->fd=open(path,O_RDONLY|O_CLOEXEC);if(context->fd<0)return 0;
    unsigned char actual[256];
    for(unsigned i=0;i<sizeof custom_key_stock/sizeof custom_key_stock[0];i++) {
        const struct custom_key_stock_entry *entry=&custom_key_stock[i];
        if(entry->size>sizeof actual || !custom_key_context_read(context,entry->address,actual,entry->size) ||
           memcmp(actual,entry->bytes,entry->size)){custom_key_context_close(context);return 0;}
    }
    return custom_key_context_identity(context);
}
'''
    adapted = adapted[:start]+replacement+adapted[end:]
    path.write_text(adapted, newline='\n')
    (output/'context-adaptation.diff').write_text(''.join(difflib.unified_diff(
        original.splitlines(True), adapted.splitlines(True), fromfile='production', tofile='QEMU-test-transport')))
    return source


def build_fixture(output):
    source = generate_fixture(output)
    build = (PROJECT/'tools/build_remote.py').read_text(encoding='utf-8')
    # Reuse the exact production compile/link command, with output/source paths
    # redirected to this tool's private fixture. Never run the production builder.
    start = build.index('tc=/opt/nx300m-toolchain/usr')
    end = build.index("\n'''", start)
    script = build[start:end].replace('{shlex.quote(GUEST)}', shlex.quote(GUEST))
    script = script.replace('mod/metol/remote/', shlex.quote(linux_path(source))+'/')
    script = script.replace('outputs/metol/remote/nx-remote-controller.arm', shlex.quote(linux_path(output/'remote-fixture.arm')))
    script = script.replace('outputs/metol/remote/versions.txt', shlex.quote(linux_path(output/'versions.txt')))
    script = 'set -eu\ncd '+shlex.quote(linux_path(PROJECT))+'\n'+script
    result = subprocess.run(['wsl.exe', '-u', 'root', '-e', 'sh', '-c', script], capture_output=True,
                            encoding='utf-8', errors='replace')
    (output/'build.log').write_text(result.stdout+result.stderr, encoding='utf-8')
    if result.returncode:
        raise RuntimeError(f'Fixture build exit={result.returncode}: {result.stderr[-3000:]}')


def prepare_panel(guest, bundle):
    from lab_panel_runtime import arm_elf
    work = guest/'tmp/nx-custom-lab'
    if work.is_dir() and (work/'mod').stat().st_ino == (guest/'mod').stat().st_ino:
        return {'arm_exec_bridge': True, 'production_panel_script_unchanged': True,
                'reused_same_private_namespace_bridge': True}
    work.mkdir()
    panel, binaries, interpreter = work/'mod', work/'system-bin', work/'interpreter'
    if bundle.is_file():
        panel.mkdir()
        with zipfile.ZipFile(bundle) as archive:
            for entry in archive.infolist():
                relative = Path(entry.filename)
                if relative.is_absolute() or '..' in relative.parts or '\\' in entry.filename:
                    raise ValueError('Unexpected panel archive path')
                if entry.is_dir() or not entry.filename.startswith('nx300m-mod/'):
                    continue
                target = panel / entry.filename[len('nx300m-mod/'):]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(entry))
    else:
        shutil.copytree(bundle, panel, symlinks=True)
    # Only this private test copy opts in. Never enable the source/card kit.
    disabled_shortcut = panel/'NO_PANEL_SHORTCUT'
    marker_removed = disabled_shortcut.exists()
    if marker_removed: disabled_shortcut.unlink()
    for name in ('mfw-panel.sh', 'mfw-panel-actions.sh', 'gui_mfw.NX300M', 'bin/nxui.arm', 'fonts/mfw-font.charset'):
        if not (panel/name).is_file(): raise ValueError('Incomplete panel kit: '+name)
    shutil.copytree(guest/'bin', binaries, symlinks=True)
    interpreter.mkdir()
    shutil.copyfile(guest/'bin/busybox', interpreter/'busybox')
    (interpreter/'busybox').chmod(0o755)
    (interpreter/'sh').symlink_to('busybox')
    prefix = '#!/qemu-arm-static /tmp/nx-custom-lab/interpreter/sh\n'

    def wrapper(path, command):
        if path.is_symlink(): path.unlink()
        path.write_text(prefix+'exec /qemu-arm-static '+command+' "$@"\n')
        path.chmod(0o755)

    for path in list(binaries.iterdir()):
        if path.is_symlink() and os.readlink(path) in ('busybox', '/bin/busybox'):
            wrapper(path, '/tmp/nx-custom-lab/interpreter/busybox '+shlex.quote(path.name))
        elif arm_elf(path):
            saved = path.with_name(path.name+'.native')
            path.rename(saved)
            wrapper(path, '/bin/'+shlex.quote(saved.name))
    for name in ('flock', 'dirname', 'readlink', 'sed', 'stat', 'tr', 'head', 'tail'):
        if not (binaries/name).exists():
            wrapper(binaries/name, '/tmp/nx-custom-lab/interpreter/busybox '+name)
    for path in list((panel/'bin').iterdir()):
        if arm_elf(path):
            saved = path.with_name(path.name+'.native')
            path.rename(saved)
            wrapper(path, '/mod/bin/'+shlex.quote(saved.name))
    (guest/'mod').mkdir(exist_ok=True)
    for path in [panel, *panel.rglob('*')]:
        os.chown(path, 65534, 65534, follow_symlinks=False)
        if not path.is_symlink():
            path.chmod(0o755 if path.is_dir() or path.suffix == '.sh' or path.parent.name == 'bin' else 0o644)
    subprocess.run(['mount', '--bind', str(binaries), str(guest/'bin')], check=True)
    subprocess.run(['mount', '--bind', str(panel), str(guest/'mod')], check=True)
    return {'arm_exec_bridge': True, 'production_panel_script_unchanged': True,
            'firmware_ready_marker_required': False, 'global_binfmt_modified': False,
            'bundle': str(bundle), 'shortcut_marker_removed_only_in_private_copy': marker_removed}


class X11:
    def __init__(self):
        self.x = ctypes.CDLL('libX11.so.6')
        self.t = ctypes.CDLL('libXtst.so.6')
        p, w = ctypes.c_void_p, ctypes.c_ulong
        class Attributes(ctypes.Structure):
            _fields_ = [('x', ctypes.c_int), ('y', ctypes.c_int), ('width', ctypes.c_int), ('height', ctypes.c_int),
                        ('border_width', ctypes.c_int), ('depth', ctypes.c_int), ('visual', p), ('root', w),
                        ('window_class', ctypes.c_int), ('bit_gravity', ctypes.c_int), ('win_gravity', ctypes.c_int),
                        ('backing_store', ctypes.c_int), ('backing_planes', ctypes.c_ulong), ('backing_pixel', ctypes.c_ulong),
                        ('save_under', ctypes.c_int), ('colormap', w), ('map_installed', ctypes.c_int), ('map_state', ctypes.c_int),
                        ('all_event_masks', ctypes.c_long), ('your_event_mask', ctypes.c_long), ('do_not_propagate_mask', ctypes.c_long),
                        ('override_redirect', ctypes.c_int), ('screen', p)]
        self.Attributes = Attributes
        signatures = {
            'XOpenDisplay': ([ctypes.c_char_p], p), 'XDefaultRootWindow': ([p], w),
            'XStringToKeysym': ([ctypes.c_char_p], w), 'XKeysymToKeycode': ([p, w], ctypes.c_uint),
            'XSync': ([p, ctypes.c_int], ctypes.c_int),
            'XSetInputFocus': ([p, w, ctypes.c_int, w], ctypes.c_int),
            'XGetInputFocus': ([p, ctypes.POINTER(w), ctypes.POINTER(ctypes.c_int)], ctypes.c_int),
            'XQueryTree': ([p, w, ctypes.POINTER(w), ctypes.POINTER(w), ctypes.POINTER(ctypes.POINTER(w)), ctypes.POINTER(ctypes.c_uint)], ctypes.c_int),
            'XFetchName': ([p, w, ctypes.POINTER(p)], ctypes.c_int), 'XFree': ([p], ctypes.c_int),
            'XGetClassHint': ([p, w, p], ctypes.c_int),
            'XGetWindowAttributes': ([p, w, ctypes.POINTER(Attributes)], ctypes.c_int),
            'XSelectInput': ([p, w, ctypes.c_long], ctypes.c_int),
            'XPending': ([p], ctypes.c_int), 'XNextEvent': ([p, p], ctypes.c_int),
            'XChangeKeyboardMapping': ([p, ctypes.c_int, ctypes.c_int, ctypes.POINTER(w), ctypes.c_int], ctypes.c_int),
        }
        for name, (args, result) in signatures.items():
            method = getattr(self.x, name);method.argtypes = args;method.restype = result
        self.t.XTestFakeKeyEvent.argtypes = [p, ctypes.c_uint, ctypes.c_int, w]
        self.display = self.x.XOpenDisplay(b':99')
        if not self.display: raise RuntimeError('Private X server unavailable')
        self.root = self.x.XDefaultRootWindow(self.display)

    def windows(self):
        w = ctypes.c_ulong
        root, parent, children, count = w(), w(), ctypes.POINTER(w)(), ctypes.c_uint()
        self.x.XQueryTree(self.display, self.root, ctypes.byref(root), ctypes.byref(parent), ctypes.byref(children), ctypes.byref(count))
        result = []
        for i in range(count.value):
            name = ctypes.c_void_p()
            title = ''
            if self.x.XFetchName(self.display, children[i], ctypes.byref(name)) and name.value:
                title = ctypes.string_at(name).decode(errors='replace')
                self.x.XFree(name)
            hint = (ctypes.c_void_p*2)()
            classes = []
            if self.x.XGetClassHint(self.display, children[i], hint):
                for value in hint:
                    if value:
                        classes.append(ctypes.string_at(value).decode(errors='replace'));self.x.XFree(value)
            attributes = self.Attributes()
            self.x.XGetWindowAttributes(self.display, children[i], ctypes.byref(attributes))
            result.append({'id': children[i], 'title': title, 'classes': classes, 'viewable': attributes.map_state == 2})
        if children: self.x.XFree(children)
        return result

    def code(self, name):
        symbol = self.x.XStringToKeysym(name.encode())
        code = self.x.XKeysymToKeycode(self.display, symbol)
        if not code: raise RuntimeError('Missing X key: '+name)
        return code

    def edge(self, name, pressed):
        self.t.XTestFakeKeyEvent(self.display, self.code(name), int(pressed), 0)
        self.x.XSync(self.display, 0)

    def key(self, name):
        self.edge(name, True);time.sleep(.06);self.edge(name, False);time.sleep(.35)

    def focus(self, window):
        self.x.XSetInputFocus(self.display, window, 2, 0);self.x.XSync(self.display, 0)

    def focused(self):
        window, revert = ctypes.c_ulong(), ctypes.c_int()
        self.x.XGetInputFocus(self.display, ctypes.byref(window), ctypes.byref(revert))
        return window.value

    def events(self):
        # XKeyEvent uses native long/window fields; copy just the common fields.
        class Key(ctypes.Structure):
            _fields_ = [('type', ctypes.c_int), ('serial', ctypes.c_ulong), ('send_event', ctypes.c_int),
                        ('display', ctypes.c_void_p), ('window', ctypes.c_ulong), ('root', ctypes.c_ulong),
                        ('subwindow', ctypes.c_ulong), ('time', ctypes.c_ulong), ('x', ctypes.c_int),
                        ('y', ctypes.c_int), ('x_root', ctypes.c_int), ('y_root', ctypes.c_int),
                        ('state', ctypes.c_uint), ('keycode', ctypes.c_uint), ('same_screen', ctypes.c_int)]
        result = []
        while self.x.XPending(self.display):
            raw = ctypes.create_string_buffer(24*ctypes.sizeof(ctypes.c_long))
            self.x.XNextEvent(self.display, raw)
            event = Key.from_buffer(raw)
            if event.type in (2, 3):
                result.append({'type': event.type, 'code': event.keycode, 'time': event.time,
                               'synthetic_forward': bool(event.send_event)})
            elif event.type == 34:
                class Mapping(ctypes.Structure):
                    _fields_ = [('type', ctypes.c_int), ('serial', ctypes.c_ulong), ('send_event', ctypes.c_int),
                                ('display', ctypes.c_void_p), ('window', ctypes.c_ulong), ('request', ctypes.c_int),
                                ('first_keycode', ctypes.c_int), ('count', ctypes.c_int)]
                mapping = Mapping.from_buffer(raw)
                result.append({'type': 34, 'request': mapping.request,
                               'first_keycode': mapping.first_keycode, 'count': mapping.count})
        return result


def wait_for(predicate, seconds=8):
    end = time.monotonic()+seconds
    while time.monotonic() < end:
        value = predicate()
        if value: return value
        time.sleep(.1)
    raise TimeoutError('Condition did not become true')


def diagnose_mapping(output):
    """Read full server maps around another input, without changing any map."""
    x = X11()
    x.x.XDisplayKeycodes.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
    x.x.XGetKeyboardMapping.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
    x.x.XGetKeyboardMapping.restype = ctypes.POINTER(ctypes.c_ulong)
    class Modifiers(ctypes.Structure):
        _fields_ = [('max_keypermod', ctypes.c_int), ('modifiermap', ctypes.POINTER(ctypes.c_ubyte))]
    x.x.XGetModifierMapping.argtypes = [ctypes.c_void_p]
    x.x.XGetModifierMapping.restype = ctypes.POINTER(Modifiers)
    x.x.XFreeModifiermap.argtypes = [ctypes.POINTER(Modifiers)]
    def mapping():
        low, high, width = ctypes.c_int(), ctypes.c_int(), ctypes.c_int()
        x.x.XDisplayKeycodes(x.display, ctypes.byref(low), ctypes.byref(high))
        data = x.x.XGetKeyboardMapping(x.display, low.value, high.value-low.value+1, ctypes.byref(width))
        keys = {str(code): [data[(code-low.value)*width.value+level] for level in range(width.value)]
                for code in range(low.value, high.value+1)}
        x.x.XFree(data)
        mod = x.x.XGetModifierMapping(x.display)
        modifiers = [[mod.contents.modifiermap[group*mod.contents.max_keypermod+i]
                      for i in range(mod.contents.max_keypermod)] for group in range(8)]
        x.x.XFreeModifiermap(mod)
        selected = {name: {'code': (code := x.code(name)), 'level0': keys[str(code)][0],
                           'modifier_groups': [i for i, group in enumerate(modifiers) if code in group]}
                    for name in ('KP_Delete', 'Super_L', 'Super_R')}
        return {'keysyms_per_keycode': width.value, 'keys': keys, 'modifiers': modifiers, 'selected': selected}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def status():
        return json.load(opener.open('http://127.0.0.1:18781/api/v1/camera/info', timeout=1))['custom_key']
    stock = next(w['id'] for w in x.windows() if w['viewable'] and 'di-camera-app-nx300' in w['classes'])
    x.x.XSelectInput(x.display, stock, 3);x.x.XSync(x.display, 0)
    result = {'initial_failed_key_pre_map_unavailable': True, 'before': mapping(), 'before_status': status(),
              'before_events': x.events()}
    x.key('KP_Delete');time.sleep(1)
    result.update(after=mapping(), after_status=status(), after_events=x.events(), windows=x.windows())
    (Path(output)/'mapping-diagnostic.json').write_text(json.dumps(result, indent=2))
    print(json.dumps({key: value for key, value in result.items() if key not in ('before', 'after')}, indent=2))


def continue_existing(output, remaining_only=False):
    """Continue negative scenarios against the same camera and ARM remote."""
    output = Path(output)
    initial = json.loads((output/'report.json').read_text())
    proc = Path(GUEST)/'proc'/str(initial['camera']['namespace_pid'])
    bias = int(initial['camera']['guest_base'], 16)
    x = X11()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def status():
        return json.load(opener.open('http://127.0.0.1:18781/api/v1/camera/info', timeout=1))['custom_key']
    def state():
        with (proc/'mem').open('rb', buffering=0) as memory:
            memory.seek(bias+0x4c9c64);return hex(struct.unpack('<I', memory.read(4))[0])
    def panel():
        return [w for w in x.windows() if w['viewable'] and w['title'] == 'Metol-NX300M']
    stock = next(w['id'] for w in x.windows() if w['viewable'] and 'di-camera-app-nx300' in w['classes'])
    for opened in panel():
        x.focus(opened['id']);x.key('Menu');wait_for(lambda: not panel())
    x.focus(stock);x.x.XSelectInput(x.display, stock, 3);x.x.XSync(x.display, 0)
    result = {'same_stock_and_remote': True, 'initial_status': status(), 'scenarios': {},
              'quickview_verified': False, 'quickview_reason': 'No normal capture/Quickview in this no-SD session'}
    def replay(name):
        x.events();before = status();snapshot = state();x.key('KP_Delete');time.sleep(.3)
        after, edges = status(), x.events()
        custom = [e['type'] for e in edges if e.get('code') == x.code('KP_Delete')]
        passed = after['triggers'] == before['triggers'] and after['fallbacks'] > before['fallbacks'] and custom[-2:] == [2, 3] and custom.count(2) == 1 and not panel()
        result['scenarios'][name] = {'passed': passed, 'state': snapshot, 'before': before, 'after': after, 'edges': edges}
        if not passed: raise AssertionError(name)
    failure = Path(GUEST)/'tmp/nx-custom-lab-read-failure'
    try:
        if not remaining_only:
            replay('stock_menu')
            x.key('Menu');wait_for(lambda: state() == '0x4c9f04')
            x.key('XF86Tools')
            replay('stock_playback')
            x.key('XF86Tools');wait_for(lambda: state() == '0x4c9f04')
        failure.touch();replay('read_failure');failure.unlink()
        for name, key in [('non_custom', 'KP_Left'), ('S1', 'Super_L')]:
            before = status();x.events();x.edge('KP_Delete', True);time.sleep(.08)
            x.edge(key, True);time.sleep(.12);x.edge('KP_Delete', False);x.edge(key, False);time.sleep(.4)
            after, edges = status(), x.events()
            codes = [e['type'] for e in edges if e.get('code') == x.code(key)]
            passed = after['triggers'] == before['triggers'] and codes == [2, 3]
            result['scenarios'][name] = {'passed': passed, 'before': before, 'after': after, 'edges': edges}
            if not passed: raise AssertionError(name)
        before = status();helper = before['helper_pid'];os.kill(helper, signal.SIGSTOP)
        latencies = []
        for _ in range(4):
            start = time.monotonic();status();latencies.append(time.monotonic()-start);time.sleep(.15)
        wait_for(lambda: status()['timeouts'] > before['timeouts'], 4)
        wait_for(lambda: not (Path(GUEST)/'proc'/str(helper)).exists(), 4)
        result['scenarios']['hung_helper'] = {'passed': True, 'http_seconds': latencies,
                                              'stopped_helper': helper, 'after': status()}
        result['passed'] = all(s['passed'] for s in result['scenarios'].values())
    except Exception as error:
        result['error'] = repr(error);result['passed'] = False
        raise
    finally:
        if failure.exists(): failure.unlink()
        result['final_status'] = status();result['final_state'] = state()
        (output/('remaining-results.json' if remaining_only else 'continuation-results.json')).write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2))


def worker(output, bundle, hold_on_failure=False, playback_only=False):
    guest = Path(GUEST)
    report = {'passed': False, 'scenarios': {}, 'boundaries': {
        'physical_camera_tested': False, 'custom_so_loaded': False, 'camera_memory_written': False,
        'test_context_transport': 'pinned real QEMU PID plus host address bias; real accessor-byte and GUI-state reads',
        'quickview': 'not yet verified through normal camera capture',
    }}
    remote = None
    try:
        subprocess.run(['ip', 'link', 'set', 'lo', 'up'], check=True)
        def state():
            with (proc/'mem').open('rb', buffering=0) as mem:
                def word(address): mem.seek(bias+address);return struct.unpack('<I', mem.read(4))[0]
                return {'application': hex(word(0x4c9c5c)), 'preview': hex(word(0x4c9f04)),
                        'state': hex(word(0x4c9c64)), 'capture_run': hex(word(0x4de4d8))}

        end = time.monotonic()+200
        boot = []
        while time.monotonic() < end:
            candidates = []
            for candidate in (guest/'proc').iterdir():
                if candidate.name.isdigit():
                    try:
                        if b'/usr/bin/di-camera-app-nx300' in (candidate/'cmdline').read_bytes().split(b'\0'):
                            candidates.append(candidate)
                    except OSError: pass
            try:
                if len(candidates) != 1: raise RuntimeError(f'camera count={len(candidates)}')
                proc = candidates[0]
                maps = (proc/'maps').read_text()
                mapping = next(line.split() for line in maps.splitlines()
                               if line.split()[2] == '00000000' and line.endswith('/usr/bin/di-camera-app-nx300'))
                bias = int(mapping[0].split('-')[0], 16)-0x8000
                snapshot = state()
                if not boot or snapshot != boot[-1]['state']:
                    boot.append({'pid': int(proc.name), 'state': snapshot})
                if snapshot['state'] == '0x4c9f04': break
            except (OSError, RuntimeError, StopIteration): pass
            time.sleep(.5)
        else: raise TimeoutError('Stock GUI did not enter real preview state')
        report['boot_states'] = boot
        (output/'camera.maps').write_text(maps)
        report['camera'] = {'namespace_pid': int(proc.name), 'exe': os.readlink(proc/'exe'),
                            'guest_base': hex(bias), 'state_address': '0x4c9c64', 'run_state_address': '0x4de4d8',
                            'host_pid_status': (proc/'status').read_text().split('NSpid:')[-1].split('\n')[0].strip()}
        report['initial_state'] = state()
        report['panel_bridge'] = prepare_panel(guest, bundle)
        shutil.copyfile(output/'remote-fixture.arm', guest/'tmp/remote-custom-fixture.arm')
        (guest/'tmp/remote-custom-fixture.arm').chmod(0o755)
        shutil.copyfile(output/'remote-production.arm', guest/'tmp/remote-custom-production.arm')
        (guest/'tmp/remote-custom-production.arm').chmod(0o755)
        x = X11()
        def stock_windows():
            return [item for item in x.windows() if item['viewable'] and set(item['classes']) & {'di-camera-app', 'di-camera-app-nx300'}]
        windows = wait_for(stock_windows, 150)
        report['initial_windows'] = windows
        stock = next(item['id'] for item in windows if item['title'] != 'Metol-NX300M')
        x.focus(stock)
        x.x.XSelectInput(x.display, stock, 3);x.x.XSync(x.display, 0);x.events()
        def stock_return_preview():
            # Stock first-use help is a separate layer inside the MENU state.
            # MENU closes that layer before a later press leaves the menu.
            presses = 0
            while state()['state'] != '0x4c9f04' and presses < 3:
                presses += 1;x.key('Menu')
                try: wait_for(lambda: state()['state'] == '0x4c9f04', 1)
                except TimeoutError: pass
            if state()['state'] != '0x4c9f04': raise AssertionError('Stock MENU/help did not return to preview')
            return presses

        # Establish stock MENU behavior before attributing a later failure to
        # the helper. The preview singleton alone is not a hardware-ready flag.
        baseline_attempts = 0
        baseline_started = time.monotonic()
        while state()['state'] != '0x4da0dc' and baseline_attempts < 5:
            baseline_attempts += 1
            x.key('Menu')
            try: wait_for(lambda: state()['state'] == '0x4da0dc', 2)
            except TimeoutError: pass
        if state()['state'] != '0x4da0dc': raise AssertionError('Stock MENU baseline unavailable before remote launch')
        baseline_return_presses = stock_return_preview()
        report['scenarios']['stock_menu_before_remote'] = {
            'passed': True, 'open_attempts': baseline_attempts, 'return_presses': baseline_return_presses, 'seconds': time.monotonic()-baseline_started}
        environment = dict(os.environ, DISPLAY=':99', PATH='/bin:/usr/bin:/sbin:/usr/sbin',
                           NX_REMOTE_CUSTOM_KEY='1', NX300M_MOD_ROOT='/mod',
                           NX_STATE_DIR='/tmp/nx-custom-state', NX_CUSTOM_LAB_PID=proc.name,
                           NX_CUSTOM_LAB_BIAS=hex(bias))
        def launch(binary, stem):
            stdout = (output/(stem+'.stdout')).open('wb');stderr = (output/(stem+'.stderr')).open('wb')
            return subprocess.Popen(['chroot', str(guest), '/qemu-arm-static', '/tmp/'+binary,
                                     '--listen', '127.0.0.1:18781', '--web-root', '/mod/web_root'],
                                    env=environment, stdout=stdout, stderr=stderr)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def status():
            try:
                with opener.open('http://127.0.0.1:18781/api/v1/camera/info', timeout=1) as response:
                    return json.load(response)['custom_key']
            except (OSError, ValueError, KeyError): return None

        def ready():
            value = status();return value if value and value['state'] == 'ready' else None

        def panel():
            return [item for item in x.windows() if item['viewable'] and item['title'] == 'Metol-NX300M']

        remote = launch('remote-custom-production.arm', 'production')
        rejected = wait_for(lambda: (s if (s := status()) and s['failures'] > 0 else None), 10)
        report['scenarios']['production_qemu_identity_rejected'] = {
            'passed': rejected['triggers'] == 0 and rejected['state'] != 'ready', 'status': rejected,
            'explanation': 'Unmodified production context rejects qemu executable identity; HTTP remains available'}
        remote.terminate();remote.wait(timeout=4);remote = None
        remote = launch('remote-custom-fixture.arm', 'remote')
        initial = wait_for(ready, 12)
        report['ready'] = initial
        if initial.get('mode') != 'async': raise AssertionError('Expected asynchronous Custom grab')
        if playback_only:
            x.events()
            x.key('XF86Tools')
            navigation_events = x.events()
            if state()['state'] != '0x4da0f0' and any(e['type'] == 34 for e in navigation_events):
                x.key('XF86Tools')
                report['navigation_repeated_after_mapping_notify'] = True
            report['navigation_events'] = navigation_events
            wait_for(lambda: state()['state'] == '0x4da0f0', 15)
            before = status();x.events();snapshot = state();x.key('KP_Delete');time.sleep(.4)
            after, edges = status(), x.events()
            codes = [e['type'] for e in edges if e.get('code') == x.code('KP_Delete')]
            passed = after['triggers'] == before['triggers'] and after['fallbacks'] > before['fallbacks'] and codes[-2:] == [2, 3] and codes.count(2) == 1 and not panel()
            report['scenarios']['real_playback_passthrough'] = {'passed': passed, 'state': snapshot,
                'expected_playback_singleton': '0x4da0f0', 'before': before, 'after': after, 'edges': edges,
                'normal_playback_key': 'XF86Tools'}
            report['passed'] = passed
            return
        before = initial['triggers']
        x.key('KP_Delete');opened = wait_for(panel)
        wait_for(lambda: status()['triggers'] > before)
        report['first_custom'] = {'status': status(), 'events': x.events(), 'panel': opened,
                                  'helper_unchanged': status()['helper_pid'] == initial['helper_pid']}
        x.focus(opened[0]['id']);x.key('Menu');wait_for(lambda: not panel())
        x.focus(stock);x.key('KP_Delete');opened_again = wait_for(panel)
        report['scenarios']['open_close_reopen'] = {'passed': True, 'status': status(), 'state': state()}
        x.focus(opened_again[0]['id']);x.key('Menu');wait_for(lambda: not panel());x.focus(stock)
        time.sleep(.5);x.events()

        # Stop both helper and HTTP parent. A normal Custom press/release must
        # end the async grab in the X server before any watchdog can run.
        before = wait_for(ready);helper = before['helper_pid']
        os.kill(remote.pid, signal.SIGSTOP);os.kill(helper, signal.SIGSTOP)
        try:
            x.events();x.key('KP_Delete')
            custom_edges = [e['type'] for e in x.events() if e.get('code') == x.code('KP_Delete')]
            report['scenarios']['stock_input_with_both_processes_stopped'] = {
                'passed': False, 'helper_stopped': True, 'parent_stopped': True, 'custom_edges': custom_edges}
            if 2 in custom_edges:
                raise AssertionError('Custom press unexpectedly reached stock while grabbed')
            x.key('Menu')
            wait_for(lambda: state()['state'] == '0x4da0dc', 8)
            menu_state = state()
            stock_return_preview()
            report['scenarios']['stock_input_with_both_processes_stopped'] = {
                'passed': True, 'helper_stopped': True, 'parent_stopped': True,
                'custom_edges': custom_edges, 'stock_menu_state': menu_state,
                'stock_menu_opened_and_closed_before_helper_exit': True}
        finally:
            os.kill(helper, signal.SIGKILL);os.kill(remote.pid, signal.SIGCONT)
        wait_for(ready, 12)

        # Stall the preview check after Custom release and stop HTTP dispatch.
        # The asynchronous grab must already have ended before this check.
        before = wait_for(ready);helper = before['helper_pid']
        wait_for(lambda: state()['state'] == '0x4c9f04')
        stall = guest/'tmp/nx-custom-lab-preview-stall'
        stall.touch();x.events();os.kill(remote.pid, signal.SIGSTOP)
        independent_started = time.monotonic()
        try:
            x.key('KP_Delete')
            def helper_gone():
                path = guest/'proc'/str(helper)/'stat'
                return not path.exists() or path.read_text().rsplit(')', 1)[1].split()[0] == 'Z'
            wait_for(helper_gone, 3)
            elapsed = time.monotonic()-independent_started
            x.events();x.key('Menu')
            edges = x.events()
            codes = [e['type'] for e in edges if e.get('code') == x.code('Menu')]
            # The camera can move key delivery to a child as its menu opens.
            # Verify its real menu state and return, rather than requiring an
            # observer attached only to the old window to see both edges.
            wait_for(lambda: state()['state'] == '0x4da0dc', 8)
            menu_state = state()
            report['scenarios']['blocked_preview_without_http_watchdog'] = {
                'passed': False, 'parent_dispatch_stopped': True,
                'helper_exit_seconds': elapsed, 'stock_menu_edges': codes,
                'stock_menu_state': menu_state,
            }
            if menu_state['state'] != '0x4da0dc':
                raise AssertionError('Stock Menu unavailable after helper deadline')
            stock_return_preview()
            report['scenarios']['blocked_preview_without_http_watchdog'] = {
                'passed': True, 'parent_dispatch_stopped': True,
                'helper_exit_seconds': elapsed, 'stock_menu_edges': codes,
                'stock_menu_state': menu_state,
                'stock_menu_opened_and_closed': True,
            }
        finally:
            stall.unlink(missing_ok=True);os.kill(remote.pid, signal.SIGCONT)
        wait_for(ready, 12)

        def reject(name, setup=None):
            if setup: setup()
            before = status();snapshot = state();x.events();x.key('KP_Delete');time.sleep(.4)
            after = status();edges = x.events()
            codes = [e['type'] for e in edges if e.get('code') == x.code('KP_Delete')]
            result = {'passed': after['triggers'] == before['triggers'] and after['fallbacks'] > before['fallbacks'] and codes[-2:] == [2, 3] and codes.count(2) == 1 and not panel(),
                      'before': before, 'after': after, 'state': snapshot, 'observed_custom_edges': edges}
            report['scenarios'][name] = result
            if not result['passed']: raise AssertionError(name+' did not retain both stock edges')

        reject('stock_menu', lambda: x.key('Menu'))
        stock_return_preview()
        x.key('XF86Tools')
        if state()['state'] == '0x4da0f0':
            reject('stock_playback')
            x.key('XF86Tools');wait_for(lambda: state()['state'] == '0x4c9f04')
        else:
            report['playback_unverified'] = 'Normal playback key did not enter Playback; use seeded --playback-only session'
        failure = guest/'tmp/nx-custom-lab-read-failure';failure.touch()
        reject('context_read_failure');failure.unlink()
        for name, key in [('non_custom', 'KP_Left'), ('S1', 'Super_L')]:
            x.events();before = status();x.edge('KP_Delete', True);time.sleep(.08)
            x.edge(key, True);time.sleep(.1);x.edge('KP_Delete', False);x.edge(key, False);time.sleep(.5)
            after, edges = status(), x.events()
            selected = [e for e in edges if e.get('code') == x.code(key)]
            passed = after['triggers'] == before['triggers'] and [e['type'] for e in selected] == [2, 3]
            report['scenarios'][name+'_during_custom'] = {'passed': passed, 'edges': edges, 'before': before, 'after': after}
            if not passed: raise AssertionError(name+' edges swallowed')
        before = wait_for(ready, 12);helper = before['helper_pid']
        os.kill(helper, signal.SIGSTOP)
        latencies = []
        for _ in range(4):
            started = time.monotonic();response = status();latencies.append(time.monotonic()-started)
            if not response: raise AssertionError('HTTP unavailable with stopped helper')
            time.sleep(.15)
        wait_for(lambda: status() and status()['timeouts'] > before['timeouts'], 4)
        wait_for(lambda: not (guest/'proc'/str(helper)).exists(), 4)
        report['scenarios']['helper_hang'] = {'passed': True, 'stopped_namespace_pid': helper,
                                             'http_seconds': latencies, 'after': status()}
        report['final_camera_state'] = state()
        report['passed'] = all(item['passed'] for item in report['scenarios'].values())
    except Exception as error:
        report['error'] = repr(error)
        raise
    finally:
        if 'status' in locals():
            report['last_status'] = status()
        if 'x' in locals():
            report['last_windows'] = x.windows()
            report['last_focus'] = x.focused()
            report['last_observer_edges'] = x.events()
        if 'state' in locals():
            try: report['last_camera_state'] = state()
            except OSError: pass
        (output/'report.json').write_text(json.dumps(report, indent=2))
        if hold_on_failure and not report['passed']:
            end = time.monotonic()+120
            while time.monotonic() < end and not (output/'release-session').exists(): time.sleep(.2)
        if remote:
            remote.terminate()
            try: remote.wait(timeout=4)
            except subprocess.TimeoutExpired: remote.kill();remote.wait(timeout=3)
            report['remote_exit'] = remote.returncode
        (output/'report.json').write_text(json.dumps(report, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--hold-on-failure', action='store_true')
    parser.add_argument('--fixture-from', type=Path)
    parser.add_argument('--attach-session', type=Path)
    parser.add_argument('--playback-only', action='store_true')
    parser.add_argument('--bundle', type=Path, default=PROJECT/'outputs/deployment/lut-test-card/nx300m-connected-lut-test.zip')
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if args.worker:
        worker(output, args.bundle.resolve(), args.hold_on_failure, args.playback_only);return
    if os.name != 'nt': raise RuntimeError('Launch from the Windows Conda environment')
    if output.exists(): raise RuntimeError('Use a new output directory to preserve earlier evidence')
    output.mkdir(parents=True)
    if args.fixture_from:
        shutil.copytree(args.fixture_from/'source', output/'source')
        for name in ('remote-fixture.arm', 'versions.txt', 'context-adaptation.diff', 'build.log'):
            shutil.copyfile(args.fixture_from/name, output/name)
    else:
        build_fixture(output)
    shutil.copyfile(PROJECT/'outputs/metol/remote/nx-remote-controller.arm', output/'remote-production.arm')
    command = [sys.executable, str(PROJECT/'tools/run_stock.py'), '--program', 'camera', '--display', '--preferences',
               '--gpio-preview', '--device-preview', '--shutter-preview', '--shutter-park', '10', '--isp-preview',
               '--spi-mmio', '--sensor-registers', '--d4c-registers', '--capture-boot', '--mmap-script-fix',
               '--keep-awake', '--no-strace', '--qemu', 'work/qemu/qemu-arm-static', '--seconds', '0',
               '--output-dir', str(output/'stock')]
    if args.playback_only:
        command.extend(['--sdcard', '--sdcard-image', '/opt/nx300m-lab/media/custom-key-20261025-private.img',
                        '--cpu-jpeg-decode', '--cpu-playback'])
    log = (output/'session-launch.log').open('wb')
    launched = None if args.attach_session else subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
    namespace = None
    try:
        session_path = args.attach_session or output/'stock/session.json'
        wait_for(lambda: session_path.exists(), 90)
        session = json.loads(session_path.read_text())
        namespace = int(session['namespace_pid'])
        (output/'invocation.json').write_text(json.dumps({'stock_command': command, 'namespace_pid': namespace,
                                                         'no_custom_so': True, 'hardware_substitutes': True}, indent=2))
        command = ['wsl.exe', '-u', 'root', '-e', 'nsenter', '-t', str(namespace), '--mount', '--net', '--ipc', '--pid',
                   '/usr/bin/python3', linux_path(Path(__file__)), '--worker', '--output-dir', linux_path(output),
                   '--bundle', linux_path(args.bundle)]
        if args.hold_on_failure: command.append('--hold-on-failure')
        if args.playback_only: command.append('--playback-only')
        result = subprocess.run(command, capture_output=True, timeout=380)
        (output/'worker.stdout').write_bytes(result.stdout);(output/'worker.stderr').write_bytes(result.stderr)
        if result.returncode: raise RuntimeError(f'Worker exit={result.returncode}: '+result.stderr.decode(errors='replace')[-3000:])
        print((output/'report.json').read_text())
    finally:
        if namespace:
            cleanup = 'import json,os,signal,sys,time;from pathlib import Path;p=Path("/proc")/sys.argv[1];a=(p/"cmdline").read_bytes() if p.exists() else b"";owned=("NX300M_SESSION_FILE="+sys.argv[2]).encode() in a;owned and os.kill(int(sys.argv[1]),signal.SIGKILL);time.sleep(.3);print(json.dumps({"pid":int(sys.argv[1]),"owned_cmdline_verified":owned,"pid_namespace_exists":(p/"ns/pid").exists()}))'
            cleaned = subprocess.run(['wsl.exe', '-u', 'root', '-e', 'python3', '-c', cleanup,
                                      str(namespace), linux_path(session_path)], capture_output=True)
            (output/'cleanup.json').write_text(cleaned.stdout.decode(errors='replace') or json.dumps({'exit': cleaned.returncode,'stderr': cleaned.stderr.decode(errors='replace')}))
        if launched:
            try: launched.wait(timeout=15)
            except subprocess.TimeoutExpired: launched.terminate();launched.wait(timeout=5)
        log.close()


if __name__ == '__main__':
    main()
