"""Send XTEST mouse/keyboard input to the actual stock UI's private X server."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import subprocess
import struct
import sys
import time
from session_stock import session_file

PROJECT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--screen', choices=('480x800', '800x480'), default='480x800')
    actions = parser.add_subparsers(dest='action', required=True)
    click = actions.add_parser('click')
    click.add_argument('x', type=int)
    click.add_argument('y', type=int)
    key = actions.add_parser('key')
    key.add_argument('name')
    shutter = actions.add_parser('shutter')
    shutter.add_argument('phase', choices=('half', 'full', 'release'))
    actions.add_parser('info')
    actions.add_parser('frame')
    actions.add_parser('lcd-frame')
    args = parser.parse_args()
    if os.name == 'nt':
        state = json.loads(session_file().read_text(encoding='utf-8'))
        script = '/mnt/' + PROJECT.drive[0].lower() + PROJECT.as_posix()[2:] + '/tools/input_stock.py'
        command = ['wsl.exe', '-u', 'root', '-e', 'nsenter', '-t', str(state['namespace_pid']), '--mount', '--net', '/usr/bin/python3', script, '--screen', state['raw_screen'], args.action]
        if args.action == 'click': command.extend([str(args.x), str(args.y)])
        elif args.action == 'key': command.append(args.name)
        elif args.action == 'shutter': command.append(args.phase)
        result = subprocess.run(command, check=True, stdout=subprocess.PIPE if args.action in ('frame', 'lcd-frame') else None)
        if args.action in ('frame', 'lcd-frame'): sys.stdout.buffer.write(result.stdout)
        return
    xlib = ctypes.CDLL('libX11.so.6')
    xtest = ctypes.CDLL('libXtst.so.6')
    display_type = ctypes.c_void_p
    xlib.XOpenDisplay.argtypes = [ctypes.c_char_p]
    xlib.XOpenDisplay.restype = display_type
    xlib.XSync.argtypes = [display_type, ctypes.c_int]
    xlib.XCloseDisplay.argtypes = [display_type]
    window_type = ctypes.c_ulong
    xlib.XDefaultRootWindow.argtypes = [display_type]
    xlib.XDefaultRootWindow.restype = window_type
    xlib.XQueryTree.argtypes = [display_type, window_type, ctypes.POINTER(window_type), ctypes.POINTER(window_type), ctypes.POINTER(ctypes.POINTER(window_type)), ctypes.POINTER(ctypes.c_uint)]
    xlib.XGetGeometry.argtypes = [display_type, window_type, ctypes.POINTER(window_type), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)]
    xlib.XSetInputFocus.argtypes = [display_type, window_type, ctypes.c_int, ctypes.c_ulong]
    xlib.XGetInputFocus.argtypes = [display_type, ctypes.POINTER(window_type), ctypes.POINTER(ctypes.c_int)]
    xlib.XFree.argtypes = [ctypes.c_void_p]
    xlib.XFetchName.argtypes = [display_type, window_type, ctypes.POINTER(ctypes.c_void_p)]
    class XWindowAttributes(ctypes.Structure):
        _fields_ = [('x', ctypes.c_int), ('y', ctypes.c_int), ('width', ctypes.c_int), ('height', ctypes.c_int),
                    ('border_width', ctypes.c_int), ('depth', ctypes.c_int), ('visual', ctypes.c_void_p),
                    ('root', window_type), ('window_class', ctypes.c_int), ('bit_gravity', ctypes.c_int),
                    ('win_gravity', ctypes.c_int), ('backing_store', ctypes.c_int), ('backing_planes', ctypes.c_ulong),
                    ('backing_pixel', ctypes.c_ulong), ('save_under', ctypes.c_int), ('colormap', ctypes.c_ulong),
                    ('map_installed', ctypes.c_int), ('map_state', ctypes.c_int), ('all_event_masks', ctypes.c_long),
                    ('your_event_mask', ctypes.c_long), ('do_not_propagate_mask', ctypes.c_long),
                    ('override_redirect', ctypes.c_int), ('screen', ctypes.c_void_p)]
    xlib.XGetWindowAttributes.argtypes = [display_type, window_type, ctypes.POINTER(XWindowAttributes)]
    display = xlib.XOpenDisplay(b':99')
    if not display: raise SystemExit('The simulator display is no longer running')
    try:
        root = xlib.XDefaultRootWindow(display)
        root_out, parent = window_type(), window_type()
        children, count = ctypes.POINTER(window_type)(), ctypes.c_uint()
        xlib.XQueryTree(display, root, ctypes.byref(root_out), ctypes.byref(parent), ctypes.byref(children), ctypes.byref(count))
        windows = []
        for i in range(count.value):
            x, y, w, h, border, depth = ctypes.c_int(), ctypes.c_int(), ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint()
            if xlib.XGetGeometry(display, children[i], ctypes.byref(root_out), ctypes.byref(x), ctypes.byref(y), ctypes.byref(w), ctypes.byref(h), ctypes.byref(border), ctypes.byref(depth)):
                name, attributes = ctypes.c_void_p(), XWindowAttributes()
                title = ''
                if xlib.XFetchName(display, children[i], ctypes.byref(name)) and name.value:
                    title = ctypes.string_at(name).decode('utf-8', 'replace'); xlib.XFree(name)
                viewable = bool(xlib.XGetWindowAttributes(display, children[i], ctypes.byref(attributes)) and attributes.map_state == 2)
                windows.append({'window':children[i], 'x':x.value, 'y':y.value, 'width':w.value, 'height':h.value,
                                'depth':depth.value, 'title':title, 'viewable':viewable})
        xlib.XFree(children)
        if args.action in ('frame', 'lcd-frame'):
            class XImage(ctypes.Structure):
                _fields_ = [('width', ctypes.c_int), ('height', ctypes.c_int), ('xoffset', ctypes.c_int), ('format', ctypes.c_int), ('data', ctypes.c_void_p), ('byte_order', ctypes.c_int), ('bitmap_unit', ctypes.c_int), ('bitmap_bit_order', ctypes.c_int), ('bitmap_pad', ctypes.c_int), ('depth', ctypes.c_int), ('bytes_per_line', ctypes.c_int), ('bits_per_pixel', ctypes.c_int), ('red_mask', ctypes.c_ulong), ('green_mask', ctypes.c_ulong), ('blue_mask', ctypes.c_ulong)]
            x, y, w, h, border, depth = ctypes.c_int(), ctypes.c_int(), ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint()
            xlib.XGetGeometry(display, root, ctypes.byref(root_out), ctypes.byref(x), ctypes.byref(y), ctypes.byref(w), ctypes.byref(h), ctypes.byref(border), ctypes.byref(depth))
            xlib.XGetImage.argtypes = [display_type, window_type, ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_ulong, ctypes.c_int]
            xlib.XGetImage.restype = ctypes.POINTER(XImage)
            xlib.XDestroyImage.argtypes = [ctypes.POINTER(XImage)]
            captured = xlib.XGetImage(display, root, 0, 0, w.value, h.value, ctypes.c_ulong(-1), 2)
            if not captured: raise SystemExit('XGetImage failed')
            try:
                frame = captured.contents
                if frame.bits_per_pixel != 32 or frame.byte_order != 0 or (frame.red_mask, frame.green_mask, frame.blue_mask) != (0xff0000, 0xff00, 0xff):
                    raise SystemExit('Unsupported XImage format')
                sys.stdout.buffer.write(struct.pack('<III', frame.width, frame.height, frame.bytes_per_line))
                sys.stdout.buffer.write(ctypes.string_at(frame.data, frame.bytes_per_line * frame.height))
            finally:
                xlib.XDestroyImage(captured)
            if args.action == 'lcd-frame':
                from lcd_plane import snapshot, pack_snapshot
                report, frames = snapshot()
                # Legacy Quick Share still uses the RGB Xft backend. nxui now
                # supplies alpha itself; do not mask regressions in its pixels.
                report['opaque_osd_windows'] = [item for item in windows if item['title'] == 'NX300M Quick Share' and item['viewable']]
                sys.stdout.buffer.write(pack_snapshot(report, frames))
            return
        if args.action == 'info':
            print(json.dumps({'root':root, 'windows':windows})); return
        if windows:
            focused, revert = window_type(), ctypes.c_int()
            xlib.XGetInputFocus(display, ctypes.byref(focused), ctypes.byref(revert))
            if focused.value not in {item['window'] for item in windows}:
                target = max(windows, key=lambda item: item['width'] * item['height'])
                xlib.XSetInputFocus(display, target['window'], 2, 0)
        if args.action == 'shutter':
            xlib.XStringToKeysym.argtypes = [ctypes.c_char_p]
            xlib.XStringToKeysym.restype = ctypes.c_ulong
            xlib.XKeysymToKeycode.argtypes = [display_type, ctypes.c_ulong]
            xlib.XKeysymToKeycode.restype = ctypes.c_uint
            xtest.XTestFakeKeyEvent.argtypes = [display_type, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
            half = xlib.XKeysymToKeycode(display, xlib.XStringToKeysym(b'Super_L'))
            full = xlib.XKeysymToKeycode(display, xlib.XStringToKeysym(b'Super_R'))
            if not half or not full or half == full: raise RuntimeError('Distinct stock shutter key bindings are unavailable')
            guest = Path('/opt/nx300m-lab/stock-1.15')
            value = guest/'sys/devices/platform/d4keys-polled/shutter'
            # Only update the private board input installed by this lab. Never
            # write the host's sysfs or alter the camera's internal state.
            if not (guest/'var/run/nx300m-sdcard.json').is_file():
                raise RuntimeError('Start with --sdcard to provide private shutter input')
            value.parent.mkdir(parents=True, exist_ok=True)
            def level(number): value.write_text(str(number)+'\n')
            def edge(code, pressed):
                xtest.XTestFakeKeyEvent(display, code, int(pressed), 0)
                xlib.XSync(display, 0)
            if args.phase == 'half':
                level(1); edge(half, True)
            elif args.phase == 'release':
                level(0); edge(full, False); edge(half, False)
            else:
                try:
                    level(1); edge(half, True); time.sleep(.08)
                    level(2); edge(full, True); time.sleep(.12)
                finally:
                    level(0); edge(full, False); edge(half, False)
            print(json.dumps({'action': 'shutter', 'phase': args.phase, 'half_key': 'Super_L', 'full_key': 'Super_R',
                              'input_level': 1 if args.phase == 'half' else 0}))
            return
        if args.action == 'click':
            if not (0 <= args.x < 800 and 0 <= args.y < 480): parser.error('Click coordinates must be inside the 800x480 view')
            raw_x, raw_y = (479 - args.y, args.x) if args.screen == '480x800' else (args.x, args.y)
            xtest.XTestFakeMotionEvent.argtypes = [display_type, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
            xtest.XTestFakeButtonEvent.argtypes = [display_type, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
            xtest.XTestFakeMotionEvent(display, -1, raw_x, raw_y, 0)
            xtest.XTestFakeButtonEvent(display, 1, 1, 15)
            xtest.XTestFakeButtonEvent(display, 1, 0, 50)
            report = {'action':'click', 'view':[args.x, args.y], 'raw':[raw_x, raw_y]}
        else:
            xlib.XStringToKeysym.argtypes = [ctypes.c_char_p]
            xlib.XStringToKeysym.restype = ctypes.c_ulong
            xlib.XKeysymToKeycode.argtypes = [display_type, ctypes.c_ulong]
            xlib.XKeysymToKeycode.restype = ctypes.c_uint
            xtest.XTestFakeKeyEvent.argtypes = [display_type, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
            symbol = xlib.XStringToKeysym(args.name.encode('ascii'))
            if not symbol: parser.error('Unknown X11 key name')
            code = xlib.XKeysymToKeycode(display, symbol)
            if not code:
                xlib.XChangeKeyboardMapping.argtypes = [display_type, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_ulong), ctypes.c_int]
                binding = ctypes.c_ulong(symbol)
                code = 255
                xlib.XChangeKeyboardMapping(display, code, 1, ctypes.byref(binding), 1)
                xlib.XSync(display, 0)
            xtest.XTestFakeKeyEvent(display, code, 1, 15)
            xtest.XTestFakeKeyEvent(display, code, 0, 50)
            report = {'action':'key', 'name':args.name, 'keycode':code}
        xlib.XSync(display, 0)
        print(json.dumps(report))
    finally:
        xlib.XCloseDisplay(display)

if __name__ == '__main__': main()
