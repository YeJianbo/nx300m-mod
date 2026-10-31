"""Control NX300M Picture Wizard and read its original IQ parameter consumers."""
import argparse
import json
import secrets
import subprocess
import threading
import time

from session_stock import inspect_session

GUEST = '/opt/nx300m-lab/stock-1.15'
SCALARS = ('saturation', 'sharpness', 'contrast')
FIELDS = (*SCALARS, 'color_tone')
LOCK = threading.Lock()


def pack(value):
    if type(value) is not int or not -4 <= value <= 4:
        raise ValueError('原厂饱和度、锐度和对比度必须是 -4 至 +4 的整数')
    return ((value << 20) | 0xff004) & 0xffffffff


def unpack(raw):
    value = (raw if raw < 0x80000000 else raw - 0x100000000) >> 20
    return value if -4 <= value <= 4 and pack(value) == raw else None


def pack_color(value):
    if type(value) is not int or not -1 <= value <= 359:
        raise ValueError('原厂色彩角度必须是 0 至 359 的整数；-1 关闭色彩调整')
    return value & 0xffffffff


def control(changes=None):
    changes = {} if changes is None else changes
    if not isinstance(changes, dict) or changes.keys() - set(FIELDS):
        raise ValueError('仅支持 saturation、sharpness、contrast、color_tone')
    values = [pack(changes.get(field, 0)) for field in SCALARS]
    values.append(pack_color(changes.get('color_tone', -1)))
    mask = sum(1 << i for i, field in enumerate(FIELDS) if field in changes)
    with LOCK:
        state = inspect_session()
        if not state['running']: raise RuntimeError('请先启动原厂程序')
        if not state.get('picture_wizard_ready'):
            raise RuntimeError('当前相机会话未检测到匹配的 Picture Wizard 桥，请重新加载 ui_mod.so')
        pid = state['namespace_pid']
        request = secrets.randbelow(0x7ffffffe)+1
        command = ['wsl.exe', '-u', 'root', '-e', 'nsenter', '-t', str(pid), '--mount',
                   'sh', '-c', '''set -eu
cd /opt/nx300m-lab/stock-1.15/var/run
ready=$(cat nx300m-pw-ready)
exec 9> nx300m-pw-lock
flock -w 2 9
[ "$(cat nx300m-pw-ready)" = "$ready" ] || { echo "Picture Wizard ready PID changed" >&2; exit 4; }
printf '%s\\n' "$1" > nx300m-pw-request.tmp
mv nx300m-pw-request.tmp nx300m-pw-request
for attempt in $(seq 1 160); do
 if [ -f nx300m-pw-response ]; then
  read -r response_id rest < nx300m-pw-response
  if [ "$response_id" = "$2" ]; then
   [ "$(cat nx300m-pw-ready)" = "$ready" ] || { echo "Picture Wizard ready PID changed" >&2; exit 4; }
   cat nx300m-pw-response; exit 0
  fi
 fi
 sleep 0.05
done
exit 124''', 'sh', f'{request} {mask} {int(time.time())+6} '+ ' '.join(f'{v:08x}' for v in values), str(request)]
        result = subprocess.run(command, capture_output=True, timeout=15)
        if result.returncode:
            raise RuntimeError(f'原厂参数桥未响应（退出码 {result.returncode}）：'+result.stderr.decode('utf-8', 'replace').strip())
        parts = result.stdout.decode('ascii').split()
        if len(parts) != 15 or int(parts[0]) != request: raise RuntimeError('参数桥返回格式无效，请确认相机已载入当前 ui_mod.so')
        if int(parts[1]): raise RuntimeError(f'原厂参数服务返回 {parts[1]}')
        data = [int(v, 16) for v in parts[2:6]]
        iq = [int(v, 16) for v in parts[6:10]]
        actual = {field: unpack(raw) for field, raw in zip(SCALARS, data[1:])}
        actual['color_tone'] = -1 if data[0] == 0xffffffff else (data[0] if data[0] <= 359 else None)
        decoded = {field: int(value) for field, value in zip(('color_tone', *SCALARS), parts[10:14])}
        mode = int(parts[14])
        current = inspect_session()
        if (not current.get('running') or not current.get('picture_wizard_ready')
                or current.get('namespace_pid') != pid):
            raise RuntimeError('Picture Wizard 请求期间相机会话或 ready 状态已变化')
        if any(actual[field] != value for field, value in changes.items()):
            raise RuntimeError('原厂参数读回与请求不一致')
        return {'namespace_pid': pid, 'values': actual, 'raw': [f'0x{v:08x}' for v in data],
                'iq_raw': [f'0x{v:08x}' for v in iq], 'iq_matches': data == iq,
                'decoded_indices': decoded, 'iq_mode': mode,
                'scalar_mode_enabled': mode in (1, 2, 3, 4, 8),
                'image_verified': False, 'persistent': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in SCALARS: parser.add_argument('--'+field, type=int, choices=range(-4, 5))
    parser.add_argument('--color-tone', type=int, choices=range(-1, 360))
    args = parser.parse_args()
    print(json.dumps(control({f: getattr(args, f) for f in FIELDS if getattr(args, f) is not None}), indent=2))


if __name__ == '__main__': main()
