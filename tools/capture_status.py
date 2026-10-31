"""Request a read-only stock capture snapshot on the camera's Ecore thread."""
import argparse
import json
import os
from pathlib import Path
import subprocess

from session_stock import GUEST, inspect_session


REQUEST = r'''
import fcntl,json,os,pathlib,secrets,sys,time
root=pathlib.Path(sys.argv[1]); camera=int(sys.argv[2])
status=(pathlib.Path('/proc')/str(camera)/'status').read_text()
guest_pid=int(status.split('NSpid:')[1].splitlines()[0].split()[-1])
run=root/'var/run'; prefix=run/'nx300m-capture-status'
if int(pathlib.Path(str(prefix)+'-ready').read_text().strip())!=guest_pid:
    raise RuntimeError('Capture status bridge is not ready for this camera process')
with open(str(prefix)+'-lock','a') as lock:
    deadline=time.monotonic()+2
    while True:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);break
        except BlockingIOError:
            if time.monotonic()>=deadline:raise TimeoutError('Capture status request is busy')
            time.sleep(.025)
    request_id=secrets.randbelow(0x7ffffffe)+1
    temporary=pathlib.Path(str(prefix)+'-request.tmp')
    request=pathlib.Path(str(prefix)+'-request')
    response=pathlib.Path(str(prefix)+'-response')
    deadline=time.monotonic()+4
    temporary.write_text(f'{request_id} {int(deadline*1000)}\n')
    temporary.replace(request)
    while time.monotonic()<deadline:
        try:
            value=json.loads(response.read_text())
            if value.get('request_id')==request_id:
                if value.get('guest_pid')!=guest_pid:
                    raise RuntimeError('Capture snapshot belongs to another camera process')
                if value.get('request_expired'):
                    raise TimeoutError('Camera processed the snapshot request after its deadline')
                print(json.dumps(value));break
        except FileNotFoundError:pass
        time.sleep(.025)
    else:raise TimeoutError('Capture status did not respond; load --capture-observer')
'''


def read_status():
    state = inspect_session(refresh=True)
    if not state.get('running') or len(state.get('camera_pids', [])) != 1:
        raise RuntimeError('需要一个正在运行的原厂相机会话')
    result = subprocess.run([
        'wsl.exe', '-u', 'root', '-e', 'nsenter', '-t', str(state['namespace_pid']),
        '--mount', 'python3', '-c', REQUEST, GUEST, str(state['camera_pids'][0]),
    ], capture_output=True, timeout=12)
    if result.returncode:
        raise RuntimeError(f'拍摄状态读取退出 {result.returncode}: '
                           + result.stderr.decode('utf-8', 'replace').strip())
    value = json.loads(result.stdout)
    value['namespace_pid'] = state['namespace_pid']
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session-file', type=Path, help='Select an independent session.json')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.session_file:
        os.environ['NX300M_SESSION_FILE'] = str(args.session_file.resolve())
    value = read_status()
    text = json.dumps(value, ensure_ascii=False, indent=2)+'\n'
    if args.output:
        args.output.write_text(text, encoding='utf-8')
    print(text, end='')


if __name__ == '__main__':
    main()
