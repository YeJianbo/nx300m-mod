"""Call original stock tools inside the live camera IPC/mount namespace.

The stock st client sends a queue message and exits 1 even for help. Its reply
is printed by the original camera process, so keep client and server output
separate instead of treating the client status as proof of success/failure.
"""
import argparse
import json
from pathlib import Path
import subprocess
import time

from session_stock import inspect_session

PROJECT = Path(__file__).resolve().parents[1]
GUEST = '/opt/nx300m-lab/stock-1.15'


def call(tool, arguments, *, wait=2):
    if tool not in ('st', 'prefman'): raise ValueError('Unsupported stock tool')
    if tool == 'st' and arguments == ['cap', 'capdtm']:
        raise ValueError('NX300M 1.15 的裸 capdtm 调用会使原厂进程崩溃；请指定 getusr、getvar 等具体子命令')
    if tool == 'st' and arguments[:2] == ['cap', 'd4c']:
        if len(arguments) < 3:
            raise ValueError('NX300M 1.15 的 D4C 调试入口缺少参数检查；请指定 regr 地址，或 regw 地址 数值')
        expected = {'regr': 4, 'regw': 5}.get(arguments[2])
        if expected is not None and len(arguments) != expected:
            raise ValueError('D4C regr 需要地址，regw 需要地址及数值')
    state = inspect_session()
    if not state['running']: raise RuntimeError('Original camera is not running')
    session = json.loads((PROJECT/'outputs/firmware-runtime/session.json').read_text(encoding='utf-8'))
    log = PROJECT/'outputs/firmware-runtime'/session['stdout_log']
    offset = log.stat().st_size
    command = ['wsl.exe', '-u', 'root', '-e', 'nsenter', '-t', str(state['namespace_pid']), '--mount', '--net', '--ipc',
               'timeout', '8', 'chroot', '--userspec=65534:65534', GUEST, '/qemu-arm-static', '-cpu', 'cortex-a9', '-L', '/', '/usr/bin/'+tool, *arguments]
    result = subprocess.run(command, capture_output=True, timeout=15)
    server = b''
    previous = b''
    last_change = time.monotonic()
    deadline = time.monotonic()+wait
    while time.monotonic() < deadline:
        with log.open('rb') as stream:
            stream.seek(offset); server = stream.read()
        relevant = b'\n'.join(line for line in server.splitlines()
                              if line.strip() != b'CaptureConsole' and not line.startswith(b'[Lens]'))
        if relevant != previous: previous = relevant; last_change = time.monotonic()
        if relevant and time.monotonic()-last_change >= .25: break
        time.sleep(.05)
    # Finish the current server write without waiting on unrelated lens logs.
    if server:
        time.sleep(.05)
        with log.open('rb') as stream:
            stream.seek(offset); server = stream.read()
    return {'tool': tool, 'arguments': arguments, 'namespace_pid': state['namespace_pid'],
            'client_exit_code': result.returncode, 'client_stdout': result.stdout.decode('utf-8', 'replace'),
            'client_stderr': result.stderr.decode('utf-8', 'replace'), 'server_stdout': server.decode('utf-8', 'replace')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('tool', choices=('st', 'prefman'))
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    print(json.dumps(call(args.tool, args.arguments), ensure_ascii=False, indent=2))


if __name__ == '__main__': main()
