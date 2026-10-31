"""Read or request transient source-indexed chroma controls in the stock session."""
import argparse
import json
import secrets
import subprocess
import time

from session_stock import inspect_session

FIELDS = (
    'request_id', 'bridge_result', 'hue', 'saturation', 'active',
    'capture_calls', 'preview_calls', 'capture_range_bypasses',
    'preview_range_bypasses', 'capture_setter_result', 'preview_setter_result',
    'capture_base_available', 'preview_base_available', 'capture_modified',
    'preview_modified', 'guest_pid',
)

TRANSPORT = '''set -eu
cd /opt/nx300m-lab/stock-1.15/var/run
ready=$(cat nx300m-chroma-style-ready 2>/dev/null || true)
[ -n "$ready" ] || { echo "Chroma bridge is not ready" >&2; exit 3; }
exec 9> nx300m-chroma-style-lock
flock -w 2 9
[ "$(cat nx300m-chroma-style-ready 2>/dev/null)" = "$ready" ] || exit 4
printf '%s\\n' "$1" > nx300m-chroma-style-request.tmp
mv nx300m-chroma-style-request.tmp nx300m-chroma-style-request
for attempt in $(seq 1 160); do
 if [ -f nx300m-chroma-style-response ]; then
  read -r RESPONSE_FIELDS < nx300m-chroma-style-response
  if [ "$request_id" = "$2" ]; then
   [ "$guest_pid" = "$ready" ] || exit 4
   [ "$(cat nx300m-chroma-style-ready 2>/dev/null)" = "$ready" ] || exit 4
   cat nx300m-chroma-style-response
   if [ "$3" = context ]; then cat nx300m-chroma-style-context.json; fi
   exit 0
  fi
 fi
 sleep 0.05
done
exit 124'''.replace('RESPONSE_FIELDS', ' '.join(FIELDS))


def request(operation=2, *, hue=10, saturation=10, include_context=False):
    """Indices refer to source controls, not degrees or a measured image match."""
    if type(operation) is not int or operation not in (0, 1, 2):
        raise ValueError('未知色度操作')
    for name, value in (('hue', hue), ('saturation', saturation)):
        if type(value) is not int or not 0 <= value <= 20:
            raise ValueError(f'{name} 必须是 0–20 的整数档位')
    state = inspect_session(refresh=True)
    if not state.get('running') or not state.get('chroma_style_ready'):
        raise RuntimeError('当前原厂会话未加载色度桥，或其 Ecore 回调尚未就绪')
    namespace = state['namespace_pid']
    request_id = secrets.randbelow(0x7ffffffe) + 1
    wire = f'{request_id} {int(time.time()) + 6} {operation} {hue} {saturation}'
    result = subprocess.run(
        ['wsl.exe', '-u', 'root', '-e', 'nsenter', '-t', str(namespace), '--mount',
         'sh', '-c', TRANSPORT, 'sh', wire, str(request_id), 'context' if include_context else ''],
        capture_output=True, timeout=15,
    )
    if result.returncode:
        detail = result.stderr.decode('utf-8', 'replace').strip()
        raise RuntimeError(f'色度桥请求失败（退出码 {result.returncode}）：{detail or "未取得匹配的实时响应"}')
    lines = result.stdout.decode('ascii').splitlines()
    if len(lines) != (2 if include_context else 1): raise RuntimeError('色度桥响应行数无效')
    values = lines[0].split()
    if len(values) != len(FIELDS): raise RuntimeError('色度桥响应字段数量无效')
    response = dict(zip(FIELDS, map(int, values)))
    if response['request_id'] != request_id or response['guest_pid'] <= 0:
        raise RuntimeError('色度桥响应身份无效')
    current = inspect_session(refresh=True)
    if (not current.get('running') or not current.get('chroma_style_ready')
            or current.get('namespace_pid') != namespace):
        raise RuntimeError('色度请求期间相机会话发生变化')
    if response['bridge_result']:
        raise RuntimeError(f'色度桥拒绝请求：{response["bridge_result"]}；读回={response}')
    if response['capture_setter_result'] or response['preview_setter_result']:
        raise RuntimeError(f'原厂色度 setter 返回错误；读回={response}')
    expected = (10, 10) if operation == 0 else (hue, saturation)
    if operation != 2 and (response['hue'], response['saturation']) != expected:
        raise RuntimeError(f'色度档位读回与请求不符；读回={response}')
    response.update(namespace_pid=namespace, persistent=False, image_verified=False,
                    supported_hue=True, supported_saturation=True,
                    saturation_scope='P/A/S/M, Standard/Custom1 Picture Wizard, 2D, neutral native CHM',
                    applied_to='NX500 SAT/HUE on compatible NX300M YCC; HUE-only CHM fallback when full mode/bases are unavailable')
    if include_context:
        context = json.loads(lines[1])
        if context.get('request_id') != request_id or context.get('guest_pid') != response['guest_pid']:
            raise RuntimeError('模式上下文与色度响应身份不一致')
        response['context'] = context
    return response


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--set', action='store_true')
    action.add_argument('--reset', action='store_true')
    action.add_argument('--status', action='store_true')
    parser.add_argument('--hue', type=int, help='原厂 HUE 表档位 0–20，10 为中性')
    parser.add_argument('--saturation', type=int, help='0–20；非中性档位要求 P/A/S/M、标准／自定义1 Picture Wizard 及原厂中性色度')
    parser.add_argument('--context', action='store_true', help='附带本次请求读取的原厂模式原始值')
    args = parser.parse_args()
    if args.set:
        if args.hue is None or args.saturation is None:
            parser.error('--set 要求同时提供 --hue 与 --saturation')
        result = request(1, hue=args.hue, saturation=args.saturation, include_context=args.context)
    else:
        if args.hue is not None or args.saturation is not None:
            parser.error('档位参数只能与 --set 一起使用')
        result = request(0 if args.reset else 2, include_context=args.context)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
