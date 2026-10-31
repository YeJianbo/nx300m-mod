"""Set, reset, or read the transient NX300M RGB color-style bridge."""
import argparse
import json
import secrets
import subprocess
import threading
import time

from session_stock import inspect_session

GUEST = '/opt/nx300m-lab/stock-1.15'
LOCK = threading.Lock()
RESPONSE_FIELDS = (
    'request_id', 'bridge_result', 'red', 'green', 'blue', 'active',
    'capture_calls', 'preview_calls', 'capture_range_bypasses',
    'preview_range_bypasses', 'capture_setter_result', 'preview_setter_result',
    'capture_base_available', 'preview_base_available', 'capture_modified',
    'preview_modified', 'guest_pid',
)

_TRANSPORT = '''set -eu
cd /opt/nx300m-lab/stock-1.15/var/run
ready=$(cat nx300m-color-style-ready 2>/dev/null || true)
[ -n "$ready" ] || { echo "RGB bridge is not ready" >&2; exit 3; }
exec 9> nx300m-color-style-lock
flock -w 2 9
[ "$(cat nx300m-color-style-ready 2>/dev/null)" = "$ready" ] || { echo "RGB ready PID changed" >&2; exit 4; }
printf '%s\\n' "$1" > nx300m-color-style-request.tmp
mv nx300m-color-style-request.tmp nx300m-color-style-request
for attempt in $(seq 1 160); do
 if [ -f nx300m-color-style-response ]; then
  read -r response_id bridge_result red green blue active capture_calls preview_calls cap_bypass prev_bypass cap_rc prev_rc cap_valid prev_valid cap_modified prev_modified response_pid < nx300m-color-style-response
  if [ "$response_id" = "$2" ]; then
   [ "$response_pid" = "$ready" ] || { echo "RGB response PID does not match ready PID" >&2; exit 4; }
   [ "$(cat nx300m-color-style-ready 2>/dev/null)" = "$ready" ] || { echo "RGB ready PID changed" >&2; exit 4; }
   cat nx300m-color-style-response
   exit 0
  fi
 fi
 sleep 0.05
done
exit 124'''


def _validate_percent(value, name):
    if type(value) is not int or not 0 <= value <= 200:
        raise ValueError(f'{name} 必须是 0–200 的整数，100 为中性')
    return value


def _request(operation, rgb=(100, 100, 100)):
    state = inspect_session()
    if not state.get('running'):
        raise RuntimeError('请先启动已加载 color-style.so 的原厂 NX300M 会话')
    if not state.get('color_style_ready'):
        raise RuntimeError('当前相机会话未检测到与相机进程匹配的 RGB 桥 ready PID')
    pid = state.get('namespace_pid')
    if not isinstance(pid, int) or pid <= 0:
        raise RuntimeError('当前原厂相机会话没有有效的命名空间 PID')

    request_id = secrets.randbelow(0x7ffffffe) + 1
    request_text = ' '.join(map(str, (
        request_id, int(time.time()) + 6, operation, *rgb,
    )))
    command = [
        'wsl.exe', '-u', 'root', '-e', 'nsenter', '-t', str(pid), '--mount',
        'sh', '-c', _TRANSPORT, 'sh', request_text, str(request_id),
    ]
    with LOCK:
        result = subprocess.run(command, capture_output=True, timeout=15)
    if result.returncode:
        detail = result.stderr.decode('utf-8', 'replace').strip()
        if result.returncode == 124:
            raise RuntimeError('原厂 RGB 桥未响应；请确认 color-style.so 已预加载并达到 Ecore ready')
        if result.returncode == 3:
            raise RuntimeError('当前会话未加载 RGB 风格桥，或 Ecore 定时器尚未就绪')
        if result.returncode == 4:
            raise RuntimeError('RGB 桥响应 PID 与 ready PID 不匹配，已拒绝使用该响应')
        raise RuntimeError(f'RGB 风格请求失败（退出码 {result.returncode}）：{detail}')

    values = result.stdout.decode('ascii').split()
    if len(values) != len(RESPONSE_FIELDS):
        raise RuntimeError('原厂 RGB 桥返回格式无效')
    parsed = [int(value) for value in values]
    if parsed[0] != request_id:
        raise RuntimeError('原厂 RGB 桥响应 ID 与请求不符')
    response = dict(zip(RESPONSE_FIELDS, parsed))
    if response['bridge_result']:
        raise RuntimeError(f'原厂 RGB 桥拒绝请求：{response["bridge_result"]}')
    if response['guest_pid'] <= 0:
        raise RuntimeError('原厂 RGB 桥没有返回有效 guest PID')
    current = inspect_session()
    if (not current.get('running') or not current.get('color_style_ready')
            or current.get('namespace_pid') != pid):
        raise RuntimeError('RGB 请求期间相机会话或 ready 状态已变化，已拒绝使用该响应')
    response.update(
        namespace_pid=pid,
        image_verified=False,
        persistent=False,
        applied_to='row gains on the latest native color matrix',
    )
    return response


def set_rgb_gain(*, red, green, blue):
    """Set all three output-channel percentages; 100/100/100 passes stock data."""
    rgb = (
        _validate_percent(red, 'red'),
        _validate_percent(green, 'green'),
        _validate_percent(blue, 'blue'),
    )
    response = _request(1, rgb)
    if (response['red'], response['green'], response['blue']) != rgb:
        raise RuntimeError('原厂 RGB 风格读回与请求不一致')
    return response


def reset_rgb_gain():
    """Restore the latest original matrix and return all channel gains to 100%."""
    return _request(0)


def read_rgb_gain():
    """Read current transient channel gains and setter status."""
    return _request(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--set', action='store_true', help='设置 R/G/B 通道百分比')
    group.add_argument('--reset', action='store_true', help='恢复原厂矩阵并回到中性')
    group.add_argument('--status', action='store_true', help='读取当前桥接状态')
    parser.add_argument('--red', type=int)
    parser.add_argument('--green', type=int)
    parser.add_argument('--blue', type=int)
    args = parser.parse_args()

    rgb_values = (args.red, args.green, args.blue)
    if args.set:
        if any(value is None for value in rgb_values):
            parser.error('--set 要求同时提供 --red、--green、--blue')
        response = set_rgb_gain(red=args.red, green=args.green, blue=args.blue)
    elif args.reset:
        if any(value is not None for value in rgb_values):
            parser.error('--reset 不接受 RGB 数值')
        response = reset_rgb_gain()
    else:
        if any(value is not None for value in rgb_values):
            parser.error('只有 --set 可使用 RGB 数值')
        response = read_rgb_gain()
    print(json.dumps(response, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
