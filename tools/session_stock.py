"""Inspect the private runtime's actual camera process, not just its supervisor."""
import json
import os
from pathlib import Path
import subprocess
import sys
import struct
import copy
import threading
import time

PROJECT = Path(__file__).resolve().parents[1]
GUEST = '/opt/nx300m-lab/stock-1.15'
INSPECT_TIMEOUT = 4
_inspection_lock = threading.Lock()
_inspection_cache = None
_inspection_cache_key = None
_inspection_cache_until = 0
_inspection_cache_checked_at = 0
_pending_inspection = None


class BackendUnavailable(RuntimeError):
    pass


def session_file():
    return Path(os.environ.get('NX300M_SESSION_FILE') or PROJECT / 'outputs/firmware-runtime/session.json').resolve()


def session_output():
    return session_file().parent


def wsl_session_environment():
    selected = session_file()
    linux_selected = '/mnt/' + selected.drive[0].lower() + selected.as_posix()[2:]
    return ['env', 'NX300M_SESSION_FILE='+linux_selected]

def empty_state():
    return {'namespace_pid': None, 'camera_pids': [], 'metol_pids': [], 'remote_pids': [], 'running': False, 'dial_locked': False,
             'exposure_controls_ready': False,
             'capture_status_ready': False,
             'shutter_limit_ready': False,
             'color_style_ready': False, 'chroma_style_ready': False,
             'contrast_style_ready': False, 'sharpness_style_ready': False,
             'source_rgb_style_ready': False,
             'recipe_style_ready': False,
             'picture_wizard_ready': False, 'lut_hook_ready': False,
             'service_loader_ready': False, 'service_loader': None,
             'spi_instruction_mmio': False, 'sensor_register_model': None, 'mipi_register_model': None,
             'ssif_event_input_ready': False, 'pp_event_input_ready': False,
             'ipcm_cpu_resize': {'enabled': False, 'status': None},
             'd4c_register_model': None, 'd4c_burst_payload': None, 'shutter_motion': None,
             'hotshoe_input': None, 'pwm_timing': None,
             'lut': {}, 'lcd_video': None, 'sdcard': None, 'ipcm_replay': None,
             'backend_available': True, 'camera_state': 'stopped', 'backend_error': None}


def unavailable_state(message, kind):
    state = empty_state()
    # None is intentional: false would claim that the camera is stopped.
    state.update(backend_available=False, camera_state='unknown', running=None,
                 backend_error=message, backend_error_kind=kind)
    return state


def _run_windows_inspection():
    global _pending_inspection
    # A timed-out client's pipe readers may still be waiting for a descendant.
    # Do not launch more clients until that exact process tree has drained.
    if _pending_inspection is not None:
        try:
            _pending_inspection.communicate(timeout=0)
        except subprocess.TimeoutExpired:
            return unavailable_state('WSL 查询客户端尚未退出，相机状态未知；等待后重试。', 'cleanup-pending')
        _pending_inspection = None
    script = '/mnt/' + PROJECT.drive[0].lower() + PROJECT.as_posix()[2:] + '/tools/session_stock.py'
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    process = subprocess.Popen(['wsl.exe', '-u', 'root', '-e', *wsl_session_environment(), 'python3', script],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags)
    try:
        stdout, stderr = process.communicate(timeout=INSPECT_TIMEOUT)
    except subprocess.TimeoutExpired:
        # Only this Popen's Windows tree is targeted, never a distro or wslhost
        # by name. Kill the tree before the parent disappears so /T can find it.
        try:
            subprocess.run(['taskkill.exe', '/PID', str(process.pid), '/T', '/F'],
                           capture_output=True, timeout=2, creationflags=flags)
        except (OSError, subprocess.TimeoutExpired):
            pass
        if process.poll() is None:
            try: process.kill()
            except OSError: pass
        try:
            process.communicate(timeout=.5)
        except subprocess.TimeoutExpired:
            _pending_inspection = process
        return unavailable_state('WSL 后端查询超时，相机状态未知；请等待后端恢复，勿重复启动。', 'timeout')
    if process.returncode:
        detail = stderr.decode('utf-8', 'replace').strip()[-300:]
        return unavailable_state(f'WSL 后端查询失败（退出码 {process.returncode}）：{detail or "无错误输出"}；相机状态未知。', 'exit-error')
    try:
        observed = json.loads(stdout)
        if not isinstance(observed, dict) or not isinstance(observed.get('running'), (bool, type(None))):
            raise ValueError('invalid session status')
        if 'namespace_pid' not in observed or 'camera_pids' not in observed:
            raise ValueError('missing process identity fields')
    except (ValueError, UnicodeError) as exc:
        return unavailable_state(f'WSL 后端返回无效状态：{exc}；相机状态未知。', 'invalid-response')
    observed.setdefault('backend_available', True)
    observed.setdefault('backend_error', None)
    observed.setdefault('camera_state', 'running' if observed['running'] else 'stopped')
    return observed


def inspect_session(*, refresh=False):
    global _inspection_cache, _inspection_cache_key, _inspection_cache_until, _inspection_cache_checked_at
    if os.name != 'nt':
        return _inspect_local_session()
    key = str(session_file())
    requested_at = time.monotonic()
    # Join the in-flight query rather than turning healthy concurrent requests
    # into false outages. The bound includes query, tree cleanup and pipe drain.
    if not _inspection_lock.acquire(timeout=INSPECT_TIMEOUT + 3):
        return unavailable_state('WSL 状态查询等待超时，相机状态暂未知。', 'inspection-in-progress')
    try:
        if (key == _inspection_cache_key and time.monotonic() < _inspection_cache_until and
                (not refresh or not _inspection_cache.get('backend_available') or
                 _inspection_cache_checked_at >= requested_at)):
            return copy.deepcopy(_inspection_cache)
        try:
            state = _run_windows_inspection()
        except OSError as exc:
            state = unavailable_state(f'WSL 后端不可用：{exc}；相机状态未知。', 'launch-error')
        _inspection_cache = copy.deepcopy(state)
        _inspection_cache_key = key
        _inspection_cache_checked_at = time.monotonic()
        # Briefly coalesce status/frame/control requests; failures back off.
        _inspection_cache_until = _inspection_cache_checked_at + (.5 if state.get('backend_available') else 2)
        return state
    finally:
        _inspection_lock.release()


def _inspect_local_session():
    empty = empty_state()
    try:
        try: state = json.loads(session_file().read_text())
        except FileNotFoundError: return empty
        pid = int(state['namespace_pid'])
        proc = Path('/proc') / str(pid)
        try: command = (proc / 'cmdline').read_bytes()
        except FileNotFoundError: return empty
        if GUEST.encode() not in command: return empty
        namespace = (proc / 'ns/mnt').readlink()
        camera = []
        camera_namespace_pids = set()
        metol = []
        remote = []
        spi_mmio = False
        cpu_resize = False
        hotshoe_absent = False
        pwm_timing = False
        for entry in Path('/proc').iterdir():
            if not entry.name.isdecimal(): continue
            try:
                if (entry / 'ns/mnt').readlink() != namespace: continue
                argv = (entry / 'cmdline').read_bytes().split(b'\0')
                if argv[0].endswith(b'qemu-arm-static') and b'/usr/bin/di-camera-app-nx300' in argv:
                    camera.append(int(entry.name))
                    for line in (entry/'status').read_text().splitlines():
                        if line.startswith('NSpid:'):
                            camera_namespace_pids.add(int(line.split()[-1]))
                            break
                    spi_mmio |= b'NX300M_SPI_MMIO=1' in argv
                    cpu_resize |= any(b'/tmp/ipcm_resize.so' in argument for argument in argv)
                    hotshoe_absent |= b'NX300M_HOTSHOE_ABSENT=1' in argv
                    pwm_timing |= b'NX300M_PWM_TIMING=1' in argv
                elif argv[0].endswith(b'qemu-arm-static') and b'/mod/nxui.arm' in argv:
                    metol.append(int(entry.name))
                elif argv[0].endswith(b'qemu-arm-static') and b'/mod/remote/nx-remote-controller.arm' in argv:
                    remote.append(int(entry.name))
            except (FileNotFoundError, PermissionError, ProcessLookupError): pass
        lockfile = proc / 'root' / GUEST.lstrip('/') / 'var/run/nx300m-dial-lock'
        locked = lockfile.exists() and lockfile.read_text().strip() == '1'
        runtime = proc / 'root' / GUEST.lstrip('/') / 'var/run'
        exposure_ready = False
        try:
            exposure_ready = int((runtime/'nx300m-exposure-ready').read_text().strip()) in camera_namespace_pids
        except (OSError, ValueError):
            pass
        shutter_limit_ready = False
        try:
            shutter_limit_ready = int((runtime/'nx300m-shutter-limit-ready').read_text().strip()) in camera_namespace_pids
        except (OSError, ValueError):
            pass
        color_style_ready = False
        chroma_style_ready = False
        scalar_ready = {}
        for name in ('contrast', 'sharpness', 'source-rgb', 'recipe', 'capture-status'):
            try:
                scalar_ready[name] = int((runtime/f'nx300m-{name}-ready').read_text().strip()) in camera_namespace_pids
            except (OSError, ValueError):
                scalar_ready[name] = False
        try:
            chroma_style_ready = int((runtime/'nx300m-chroma-style-ready').read_text().strip()) in camera_namespace_pids
        except (OSError, ValueError):
            pass
        try:
            color_style_ready = int((runtime/'nx300m-color-style-ready').read_text().strip()) in camera_namespace_pids
        except (OSError, ValueError):
            pass
        picture_wizard_ready = False
        try:
            picture_wizard_ready = int((runtime/'nx300m-pw-ready').read_text().strip()) in camera_namespace_pids
        except (OSError, ValueError):
            pass
        lut_hook_ready = False
        try:
            hook = (runtime/'nx300m-lut-hook').read_text().split()
            lut_hook_ready = len(hook) == 2 and int(hook[0]) in camera_namespace_pids and hook[1].startswith('/')
        except (OSError, ValueError):
            pass
        motion = None
        service_loader = None
        service_loader_ready = False
        try:
            observed = json.loads((runtime/'nx300m-service-loader-status.json').read_text())
            if isinstance(observed, dict):
                service_loader = observed
                service_loader['current_camera'] = observed.get('owner_pid') in camera_namespace_pids
                service_loader_ready = service_loader['current_camera'] and observed.get('reason') != 'timer-registration-failed'
        except (OSError, ValueError):
            pass
        motion_file = runtime/'nx300m-shutter-motion.bin'
        if motion_file.exists():
            data = motion_file.read_bytes()
            if len(data) == 64 and struct.unpack_from('<II', data) == (0x4d53584e, 1):
                values = struct.unpack('<16I', data)
                motion = {'kind': 'motor-gpio-observation' if values[2] == 1 else 'ideal-8-sector',
                          'sector_us': values[3], 'sector': values[4],
                          'drive': struct.unpack_from('<i', data, 20)[0],
                          'power_mot1_mot2': values[6], 'pi1': values[7], 'pi2': values[8],
                          'position_edges': values[9], 'scheduling_stalls': values[10],
                          'sample_us': values[13], 'mechanics_calibrated': False,
                          'capture_callbacks_invoked': False, 'synchronized_snapshot': False}
        resize_file = runtime/'nx300m-ipcm-resize-status.json'
        resize_status = json.loads(resize_file.read_text()) if resize_file.exists() else None
        sdcard = None
        card_file = runtime/'nx300m-sdcard.json'
        if card_file.exists():
            sdcard = json.loads(card_file.read_text())
            card_root = proc/'root'/GUEST.lstrip('/')/'mnt/mmc'
            capacity = os.statvfs(card_root)
            sdcard.update(free_bytes=capacity.f_bavail*capacity.f_frsize,
                          dcim_exists=(card_root/'DCIM').is_dir())
        replay = None
        replay_file = runtime/'nx300m-ipcm-replay-status.json'
        if replay_file.exists():
            replay = json.loads(replay_file.read_text())
            worker = Path('/proc')/str(replay.get('worker_pid', 0))
            try:
                argv = (worker/'cmdline').read_bytes().split(b'\0')
                replay['worker_running'] = any(arg.endswith(b'/tools/replay_ipcm.py') for arg in argv) and (worker/'ns/mnt').readlink() == namespace
            except (FileNotFoundError, ProcessLookupError): replay['worker_running'] = False
        lcd = {'constructed': False, 'frames': 0, 'failures': 0, 'sensor_liveview': False}
        lcd_file = runtime/'nx300m-lcd-state.bin'
        if lcd_file.exists():
            data = lcd_file.read_bytes()
            if len(data) == 4096 and struct.unpack_from('<II', data) == (0x434c584e, 1):
                lcd['constructed'] = True
                provenance = struct.unpack_from('<I', data, 12)[0]
                lcd['source'] = {0: 'laboratory-lcd-backend', 1: 'stock-lcd-observer'}.get(provenance, 'unknown-lcd-writer')
                lcd['synchronized_snapshot'] = False
                for i in range(4):
                    lcd['frames'] += struct.unpack_from('<I', data, 16+i*92+84)[0]
                    lcd['failures'] += struct.unpack_from('<I', data, 16+i*92+88)[0]
        lut = {}
        if (runtime/'nx300m-lut-status.json').exists():
            lut = json.loads((runtime/'nx300m-lut-status.json').read_text())
        selection = (runtime/'nx300m-lut-selection').read_text().split() if (runtime/'nx300m-lut-selection').exists() else []
        if len(selection) == 2:
            if lut.get('generation') != int(selection[0]): lut = {'stage': 'staged-awaiting-stock-call', 'generation': int(selection[0]), 'image_verified': False}
            lut['selection'] = selection[1]
        elif lut.get('active'): lut = {'stage': 'stock-restore-awaiting-stock-call', 'image_verified': False}
        sensor = None
        bank = runtime/'nx300m-sensor-registers.bin'
        if bank.exists():
            data = bank.read_bytes()
            if len(data) == 0x11000:
                defined = data[0x10000:]
                sample = {}
                for address in (0x0100, 0x0342, 0x0344, 0x0346, 0x0348, 0x034a, 0x034c, 0x034e, 0x700c, 0x702c):
                    if defined[address//8] & (1 << (address%8)):
                        sample[f'0x{address:04x}'] = int.from_bytes(data[address*2:address*2+2], 'little')
                sensor = {'kind': 'aquila-written-register-bank', 'defined_registers': sum(b.bit_count() for b in defined),
                          'sampled_registers': sample, 'sensor_reset_defaults': False, 'image_verified': False}
        d4c = None
        bank = runtime/'nx300m-d4c-registers.bin'
        if bank.exists():
            data = bank.read_bytes()
            if len(data) == 0x42000:
                defined = data[0x40000:]
                sample = {}
                for address in (0x0000, 0x0004, 0x0c20, 0x0c24, 0x4000, 0x4008, 0x4020, 0x8000):
                    if defined[address//8] & (1 << (address%8)):
                        sample[f'0x{address:04x}'] = f'0x{int.from_bytes(data[address*4:address*4+4], "little"):08x}'
                d4c = {'kind': 'd4c-written-register-bank', 'defined_registers': sum(b.bit_count() for b in defined),
                       'sampled_registers': sample, 'reset_defaults': False, 'dsp_result_generated': False}
        burst = None
        packet = runtime/'nx300m-d4c-burst.bin'
        if packet.exists():
            data = packet.read_bytes()
            if len(data) >= 8:
                count = int.from_bytes(data[4:8], 'little')
                if 0 < count <= 16384 and len(data) == 8+count*4:
                    burst = {'wire_command': f'0x{int.from_bytes(data[:4], "little"):08x}',
                             'words': count, 'opaque': True, 'interpreted': False}
        mipi = None
        pp = proc / 'root' / GUEST.lstrip('/') / 'tmp/nx-isp-pp'
        if pp.exists():
            with pp.open('rb') as stream:
                stream.seek(0x8000)
                data = stream.read(0x3000)
            if len(data) == 0x3000:
                def word(offset): return int.from_bytes(data[offset:offset+4], 'little')
                resolution = word(0x202c)
                mipi = {'kind': 'pp-shared-register-ram',
                        'con_control': f'0x{word(0):08x}', 'csis_control': f'0x{word(0x2000):08x}',
                        'csis_dphy_control': f'0x{word(0x2004):08x}', 'csis_config': f'0x{word(0x2008):08x}',
                        'csis_interrupt_mask': f'0x{word(0x2010):08x}', 'csis_interrupt_source': f'0x{word(0x2014):08x}',
                        'csis_enabled_bit': bool(word(0x2000)&1),
                        'programmed_width': resolution>>16, 'programmed_height': resolution&0xffff,
                        'frame_generated': False, 'image_verified': False}
        return {'namespace_pid': pid, 'camera_pids': camera, 'metol_pids': metol, 'remote_pids': remote, 'running': bool(camera), 'dial_locked': locked,
                'firmware': state.get('firmware'),
                'backend_available': True, 'backend_error': None, 'camera_state': 'running' if camera else 'stopped',
                'exposure_controls_ready': exposure_ready,
                'capture_status_ready': scalar_ready['capture-status'],
                'shutter_limit_ready': shutter_limit_ready,
                'color_style_ready': color_style_ready, 'chroma_style_ready': chroma_style_ready,
                'contrast_style_ready': scalar_ready['contrast'], 'sharpness_style_ready': scalar_ready['sharpness'],
                'source_rgb_style_ready': scalar_ready['source-rgb'],
                'recipe_style_ready': scalar_ready['recipe'],
                'picture_wizard_ready': picture_wizard_ready,
                'service_loader_ready': service_loader_ready, 'service_loader': service_loader,
                'lut_hook_ready': lut_hook_ready, 'lut': lut,
                'spi_instruction_mmio': spi_mmio, 'sensor_register_model': sensor, 'mipi_register_model': mipi,
                'ssif_event_input_ready': (runtime/'nx300m-ssif-events').is_fifo(),
                'pp_event_input_ready': (runtime/'nx300m-pp-events').is_fifo(),
                'd4c_register_model': d4c, 'd4c_burst_payload': burst,
                'ipcm_cpu_resize': {'enabled': cpu_resize, 'status': resize_status}, 'lcd_video': lcd, 'sdcard': sdcard, 'ipcm_replay': replay,
                'shutter_motion': motion,
                'pwm_timing': {'kind': 'finite-register-counter', 'counter_tick_ns': 7.5,
                               'clock_measured': False, 'external_input': 'explicit source/level FIFO',
                               'input_ready': (runtime/'nx300m-pwm-events').is_fifo(),
                               'physical_waveform_verified': False} if pwm_timing else None,
                'hotshoe_input': {'kind': 'explicit-absent', 'gpio': 56, 'input_level': 1} if hotshoe_absent else None}
    except (OSError, ValueError, KeyError) as exc:
        return unavailable_state(f'无法核验原厂会话：{exc}；相机状态未知。', 'inspection-error')

if __name__ == '__main__': print(json.dumps(inspect_session()))
