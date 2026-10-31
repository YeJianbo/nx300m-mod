"""Control an explicitly selected stock session through acknowledged firmware RPC.

Auto ISO and shutter controls reuse the existing exposure and shutter bridges.
Camera UI controls require run_stock.py --firmware-mod.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import threading
import time

from exposure_controls import AUTO_ISO_MAX_VALUES, read_controls, set_controls
from session_stock import inspect_session
from shutter_limit import OPTIONS, request as shutter_request

MODE_MAP = {'SMARTAUTO': 1, 'AUTO': 1, 'P': 2, 'A': 3, 'S': 4, 'M': 5,
            'BULB': 6, 'IFN': 7, 'I': 7, 'SMARTPRO': 8, 'WIFI': 30}
MODE_NAMES = {1: 'SMARTAUTO', 2: 'P', 3: 'A', 4: 'S', 5: 'M', 6: 'BULB',
              7: 'IFN', 8: 'SMARTPRO', 30: 'WIFI'}
LOCK = threading.Lock()


def firmware_request(operation=0, requested=0):
    state = inspect_session()
    if not state.get('running') or not state.get('namespace_pid'):
        raise RuntimeError('选定原厂会话没有运行')
    request_id = secrets.randbelow(0x7ffffffe) + 1
    # Owner and IPC are checked in the selected mount namespace; no local
    # work/var_run fallback and no readiness based only on file existence.
    command = [
        'wsl.exe', '-u', 'root', '-e', 'nsenter', '-t', str(state['namespace_pid']), '--mount', '--pid',
        'sh', '-c', '''set -eu
cd /opt/nx300m-lab/stock-1.15/var/run
test -f nx300m-firmware-mod-ready || { echo 'firmware bridge is not ready' >&2; exit 69; }
read -r owner < nx300m-firmware-mod-ready
case "$owner" in ''|*[!0-9]*) exit 70;; esac
test "$owner" -gt 1
kill -0 "$owner"
exec 9> nx300m-firmware-mod-lock
chown --reference=nx300m-firmware-mod-ready nx300m-firmware-mod-lock
flock -w 2 9
printf '%s\n' "$1" > nx300m-firmware-mod-request.tmp
mv nx300m-firmware-mod-request.tmp nx300m-firmware-mod-request
for attempt in $(seq 1 180); do
 if [ -f nx300m-firmware-mod-response ]; then
  read -r response_id response_code response_owner rest < nx300m-firmware-mod-response
  if [ "$response_id" = "$2" ] && [ "$response_owner" = "$owner" ]; then
   cat nx300m-firmware-mod-response; exit 0
  fi
 fi
 kill -0 "$owner" || exit 71
 sleep 0.05
done
exit 124''', 'sh',
        f'{request_id} {int(time.time()) + 6} {operation} {requested}', str(request_id),
    ]
    with LOCK:
        result = subprocess.run(command, capture_output=True, timeout=15)
    if result.returncode:
        detail = result.stderr.decode('utf-8', 'replace').strip()
        raise RuntimeError(f'固件桥未响应（退出码 {result.returncode}）：{detail}')
    try:
        fields = [int(value) for value in result.stdout.decode('ascii').split()]
    except ValueError as exc:
        raise RuntimeError('固件桥返回格式无效') from exc
    if len(fields) != 26 or fields[0] != request_id:
        raise RuntimeError('固件桥返回格式或请求编号无效')
    (_, code, owner, mode, metering, choice, tv16, calls, capped, stock, actual,
     ae_calls, adjusted, mode_lock, animation, custom, iso_lock, patches,
     mode_events, mode_blocked, animation_calls, animation_suppressed,
     custom_calls, custom_launches, iso_blocks, iso_actual) = fields
    if code:
        raise RuntimeError(f'固件请求失败：{code}（-109=原厂模式未在时限内完成切换）')
    if choice not in range(len(OPTIONS)) or metering not in (0, 1):
        raise RuntimeError('固件桥返回无效配置')
    if operation == 1 and mode != requested:
        raise RuntimeError('原厂模式读回与请求不一致')
    if operation == 2 and metering != requested:
        raise RuntimeError('测光开关读回与请求不一致')
    if operation == 3 and choice != requested:
        raise RuntimeError('实验快门配置读回与请求不一致')
    if operation in (4, 5, 6, 7) and {4: mode_lock, 5: animation, 6: custom, 7: iso_lock}[operation] != requested:
        raise RuntimeError('固件开关读回与请求不一致')
    return {'namespace_pid': state['namespace_pid'], 'owner_pid': owner,
            'ui_mode': mode, 'mode': MODE_NAMES.get(mode),
            'metering_plus_enabled': bool(metering),
            'experimental_fastest_shutter': OPTIONS[choice], 'limit_tv16': tv16 or None,
            'shutter_getter_calls': calls, 'shutter_capped_calls': capped,
            'last_stock_tv16': stock if calls else None,
            'last_result_tv16': actual if calls else None,
            'metering_calls': ae_calls, 'metering_adjusted_calls': adjusted,
            'physical_mode_dial_locked': bool(mode_lock), 'animation_mode': animation,
            'custom_settings_enabled': bool(custom), 'auto_iso_lock': iso_lock or None,
            'verified_entry_patch_mask': patches, 'mode_events': mode_events,
            'manual_lens_wifi_admission_enabled': bool(patches & 32),
            'mode_events_blocked': mode_blocked, 'animation_calls': animation_calls,
            'animation_suppressed': animation_suppressed, 'custom_calls': custom_calls,
            'custom_panel_launches': custom_launches, 'iso_rebound_blocked': iso_blocks,
            'actual_auto_iso_raw': f'0x{iso_actual & 0xffffffff:08x}',
            'physical_exposure_verified': False, 'image_verified': False}


def switch_mode(name):
    mode = MODE_MAP.get(name.upper())
    if mode is None:
        raise ValueError(f'模式必须是 {tuple(MODE_MAP)} 之一')
    return firmware_request(1, mode)


def set_auto_iso_max(iso):
    return set_controls(auto_iso_max=iso)


def set_fastest_shutter(speed):
    return shutter_request(speed)


def set_metering_plus(enabled):
    if enabled:
        raise ValueError('实验测光补偿已移除；使用原厂测光')
    return firmware_request(2, int(enabled))


def read_status():
    state = inspect_session()
    status = {'session': state, 'firmware_bridge': None,
              'exposure': None, 'shutter': None, 'errors': {}}
    if not state.get('running'):
        return status
    for key, read in [('firmware_bridge', firmware_request), ('exposure', read_controls),
                      ('shutter', shutter_request)]:
        try:
            status[key] = read()
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            status['errors'][key] = str(exc)
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', type=Path, help='选定隔离会话 JSON')
    parser.add_argument('--switch-mode', choices=tuple(MODE_MAP))
    parser.add_argument('--auto-iso-max', type=int, choices=tuple(AUTO_ISO_MAX_VALUES))
    parser.add_argument('--fastest-shutter', choices=OPTIONS)
    parser.add_argument('--metering-plus', type=int, choices=(0, 1))
    parser.add_argument('--lock-mode-dial', type=int, choices=(0, 1))
    parser.add_argument('--dial-animation', type=int, choices=(0, 1, 2), help='0原厂、1瞬切、2隐藏轮盘')
    parser.add_argument('--custom-settings', type=int, choices=(0, 1))
    parser.add_argument('--auto-iso-lock', type=int, choices=(0, 6400), help='6400启用上限防回弹，0解除锁定')
    parser.add_argument('--status', action='store_true')
    args = parser.parse_args()
    if args.session:
        os.environ['NX300M_SESSION_FILE'] = str(args.session.resolve())
    results = {}
    try:
        if args.switch_mode is not None:
            results['mode'] = switch_mode(args.switch_mode)
        if args.auto_iso_max is not None:
            results['auto_iso_max'] = set_auto_iso_max(args.auto_iso_max)
        if args.fastest_shutter is not None:
            results['shutter'] = set_fastest_shutter(args.fastest_shutter)
        if args.metering_plus is not None:
            results['metering'] = set_metering_plus(bool(args.metering_plus))
        for key, operation in [('lock_mode_dial', 4), ('dial_animation', 5),
                               ('custom_settings', 6), ('auto_iso_lock', 7)]:
            value = getattr(args, key)
            if value is not None:
                results[key] = firmware_request(operation, value)
        if args.status or not results:
            results['status'] = read_status()
    except (RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        parser.exit(1, f'{exc}\n')
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
