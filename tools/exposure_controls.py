"""Read or set verified NX300M exposure UserData controls."""
import argparse
import json
import secrets
import subprocess
import threading
import time

from session_stock import inspect_session

USER_DATA_IDS = {
    'iso_step': 0x37,
    'iso_expansion': 0x38,
    'auto_iso_max': 0x3b,
}
ISO_STEP_VALUES = {'one-third': 0x00370001, 'one-stop': 0x00370000}
ISO_EXPANSION_VALUES = {False: 0x00380000, True: 0x00380001}
AUTO_ISO_MAX_VALUES = {
    125: 0x003b0000,
    160: 0x003b0001,
    200: 0x003b0002,
    250: 0x003b0003,
    320: 0x003b0004,
    400: 0x003b0005,
    500: 0x003b0006,
    640: 0x003b0007,
    800: 0x003b0008,
    1000: 0x003b0009,
    1250: 0x003b000a,
    1600: 0x003b000b,
    2000: 0x003b000c,
    2500: 0x003b000d,
    3200: 0x003b000e,
    6400: 0x003b000f,
    12800: 0x003b0010,
    25600: 0x003b0011,
}
_AUTO_ISO_MAX_BY_RAW = {raw: iso for iso, raw in AUTO_ISO_MAX_VALUES.items()}
LOCK = threading.Lock()
_SV16_ISO = dict(zip(
    (64,69,75,80,85,91,96,101,107,112,117,123,128,133,139,144,149,155,160,165,171,176,181,187,192,197,203,208),
    (50,64,80,100,125,160,200,250,320,400,500,640,800,1000,1250,1600,2000,2500,3200,4000,5000,6400,8000,10000,12800,16000,20000,25600)))


def _ae_policy(fields, requested_max=None):
    mask, setting, maximum, mode, max_sv, current_sv = (
        tuple(map(int, fields[6:12])) if len(fields) >= 12 else (0,0,0,0,0,0))
    if mask < 0 or mask & ~31:
        raise RuntimeError('原厂曝光桥返回了无效AE状态掩码')
    max_iso = _SV16_ISO.get(max_sv) if mask & 8 else None
    matches = (maximum == requested_max and max_iso == requested_max
               if requested_max is not None and mask & 10 == 10 else None)
    return {'available': bool(mask), 'available_mask': mask,
        'iso_mode': ('auto' if setting == 0 else 'manual') if mask & 1 and setting >= 0 else 'unknown',
        'iso_setting': setting if mask & 1 else None,
        'current_iso': _SV16_ISO.get(current_sv) if mask & 16 else None,
        'max_iso': max_iso, 'iq_max_iso': maximum if mask & 2 else None,
        'requested_max_iso': requested_max, 'shooting_mode': mode if mask & 4 else None,
        'iq_drive_mode': mode if mask & 4 else None,
        'ae_capture_mode': int(fields[14]) if len(fields) == 15 and int(fields[14]) >= 0 else None,
        'current_sv16': current_sv if mask & 16 else None,
        'max_sv16': max_sv if mask & 8 else None, 'policy_matches_requested': matches,
        'ae_solver_verified': False, 'physical_exposure_verified': False,
        'current_iso_source': 'original AE current Svalue, not a sensor measurement'}


def _decode_value(name, raw):
    if name == 'iso_step':
        return next((step for step, encoded in ISO_STEP_VALUES.items()
                     if encoded == raw), None)
    if name == 'auto_iso_max':
        return _AUTO_ISO_MAX_BY_RAW.get(raw)
    if name == 'iso_expansion':
        return next((enabled for enabled, encoded in ISO_EXPANSION_VALUES.items()
                     if encoded == raw), None)
    return None


def _format_raw(value):
    return f'0x{value & 0xffffffff:08x}'


def _request_many(requests):
    state = inspect_session()
    if not state['running']:
        raise RuntimeError('请先启动原厂相机程序')
    if not state.get('exposure_controls_ready'):
        raise RuntimeError('请使用 --exposure-controls 启动查看器并重启原厂程序')

    pid = state['namespace_pid']
    results = {}; ae_fields = []
    with LOCK:
        for name, requested_value in requests:
            data_id = USER_DATA_IDS[name]
            operation = 0 if requested_value is None else 1
            requested_raw = 0 if requested_value is None else requested_value
            request_id = secrets.randbelow(0x7ffffffe) + 1
            request_text = (
                f'{request_id} {int(time.time()) + 6} {operation} '
                f'{data_id:08x} {requested_raw:08x}'
            )
            command = [
                'wsl.exe', '-u', 'root', '-e', 'nsenter', '-t', str(pid), '--mount',
                'sh', '-c', '''set -eu
cd /opt/nx300m-lab/stock-1.15/var/run
exec 9> nx300m-exposure-lock
flock -w 2 9
printf '%s\\n' "$1" > nx300m-exposure-request.tmp
mv nx300m-exposure-request.tmp nx300m-exposure-request
for attempt in $(seq 1 160); do
 if [ -f nx300m-exposure-response ]; then
  read -r response_id rest < nx300m-exposure-response
  if [ "$response_id" = "$2" ]; then cat nx300m-exposure-response; exit 0; fi
 fi
 sleep 0.05
done
exit 124''',
                'sh', request_text, str(request_id),
            ]
            result = subprocess.run(command, capture_output=True, timeout=15)
            if result.returncode:
                detail = result.stderr.decode('utf-8', 'replace').strip()
                if result.returncode == 124:
                    raise RuntimeError(
                        '原厂桥未响应；请确认启动器已加载 exposure_controls.so'
                    )
                raise RuntimeError(
                    f'曝光参数请求失败（退出码 {result.returncode}）：{detail}'
                )

            fields = result.stdout.decode('ascii').split()
            if len(fields) not in (6, 12, 14, 15) or int(fields[0]) != request_id:
                raise RuntimeError('原厂曝光桥返回格式无效')
            code = int(fields[1])
            if code:
                raise RuntimeError(f'原厂 UserData 服务返回 {code}')

            before, actual = int(fields[2], 16), int(fields[3], 16)
            applied, notified = bool(int(fields[4])), bool(int(fields[5]))
            if requested_value is not None and actual != requested_raw:
                raise RuntimeError(f'{name} 的原厂 UserData 读回与请求不一致')
            decoded = _decode_value(name, actual)
            ae_fields = fields
            results[name] = {
                'user_data_id': f'0x{data_id:02x}',
                'before_raw': _format_raw(before),
                'actual_raw': _format_raw(actual),
                'value': decoded,
                'set_accepted': applied if requested_value is not None else None,
                'notify_path': notified if requested_value is not None else None,
                'value_mapping_verified': decoded is not None,
            }
            if name == 'auto_iso_max':
                ready = len(fields) >= 14 and fields[13] == '1'
                results[name].update({
                    'stock_actual_raw': _format_raw(int(fields[12], 16)) if len(fields) >= 14 else _format_raw(actual),
                    'supported_values': list(AUTO_ISO_MAX_VALUES)[:18 if ready else 16],
                    'extended_ready': ready,
                    'value_source': 'mod_iq_parameter' if actual in (0x003b0010, 0x003b0011) else 'stock_userdata',
                })

    policy = _ae_policy(ae_fields, results.get('auto_iso_max', {}).get('value'))
    return {
        'namespace_pid': pid,
        'operation': 'set' if any(value is not None for _, value in requests) else 'read',
        'controls': results,
        'ae_policy_consumption_verified': policy['policy_matches_requested'] is True,
        'ae_policy': policy,
        'image_verified': False,
        'persistent': False,
    }


def read_controls():
    """Read all supported UserData values through the stock service."""
    return _request_many([(name, None) for name in USER_DATA_IDS])


def set_controls(*, iso_step=None, iso_expansion=None, auto_iso_max=None):
    """Set verified controls; each non-None argument is strictly validated."""
    requested = {}
    if iso_step is not None:
        if type(iso_step) is not str or iso_step not in ISO_STEP_VALUES:
            raise ValueError(f'iso_step 必须是 {tuple(ISO_STEP_VALUES)} 之一')
        requested['iso_step'] = ISO_STEP_VALUES[iso_step]
    if iso_expansion is not None:
        if type(iso_expansion) is not bool:
            raise ValueError('iso_expansion 必须是 JSON 布尔值')
        requested['iso_expansion'] = ISO_EXPANSION_VALUES[iso_expansion]
    if auto_iso_max is not None:
        if type(auto_iso_max) is not int or auto_iso_max not in AUTO_ISO_MAX_VALUES:
            raise ValueError(
                f'auto_iso_max 必须是 {tuple(AUTO_ISO_MAX_VALUES)} 中的 ISO 数值'
            )
        requested['auto_iso_max'] = AUTO_ISO_MAX_VALUES[auto_iso_max]
    if not requested:
        raise ValueError('至少提供一个可设置的曝光控制项')
    return _request_many(list(requested.items()))


def _legacy_iso_step_response(result, step):
    item = result['controls']['iso_step']
    return {
        'namespace_pid': result['namespace_pid'],
        'user_data_id': item['user_data_id'],
        'operation': 'set',
        'requested_step': step,
        'before_raw': item['before_raw'],
        'actual_raw': item['actual_raw'],
        'actual_step': item['value'],
        'stock_set_user_data_called': True,
        'user_data_set_accepted': item['set_accepted'],
        'stock_set_data_on_notify_path': item['notify_path'],
        'ae_policy_consumption_verified': False,
        'image_verified': False,
        'persistent': False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iso-step', choices=tuple(ISO_STEP_VALUES),
                        help='设置 ISO 步长：one-third（1/3 EV）或 one-stop（1 EV）')
    parser.add_argument('--iso-expansion', choices=('enabled', 'disabled'),
                        help='设置 ISO 扩展开关')
    parser.add_argument('--auto-iso-max', type=int, choices=tuple(AUTO_ISO_MAX_VALUES),
                        help='设置 Auto ISO 上限（ISO 数值）')
    args = parser.parse_args()

    requested = {
        key: value for key, value in {
            'iso_step': args.iso_step,
            'auto_iso_max': args.auto_iso_max,
        }.items() if value is not None
    }
    if args.iso_expansion is not None:
        requested['iso_expansion'] = args.iso_expansion == 'enabled'
    if not requested:
        result = read_controls()
    else:
        result = set_controls(**requested)
        if set(requested) == {'iso_step'}:
            result = _legacy_iso_step_response(result, args.iso_step)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
