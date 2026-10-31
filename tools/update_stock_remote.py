"""Update only the native remote in the existing stock app session."""
import json
from pathlib import Path
import subprocess
import time
import urllib.request

import remote_stock
from session_stock import inspect_session, session_file

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/metol/remote/update-report.json'


def observed(state, stop=False):
    code = r'''import json,os,pathlib,signal,sys,time
state=json.loads(sys.argv[1]); stop=sys.argv[2]=='1'
root=pathlib.Path('/proc'); owner=root/str(state['namespace_pid'])
namespace=(owner/'ns/mnt').readlink()
guest=owner/'root/opt/nx300m-lab/stock-1.15'
if state.get('gallery') and not (guest/'var/run/nx300m-gallery-runtime.json').is_file():
 raise RuntimeError('Existing gallery runtime marker missing; refusing bootstrap during remote-only update')
def identity(pid,target):
 p=root/str(pid); argv=(p/'cmdline').read_bytes().split(b'\0')
 if (p/'ns/mnt').readlink()!=namespace or not argv[0].endswith(b'qemu-arm-static') or target not in argv:
  raise RuntimeError('Process identity changed: '+str(pid))
 return (p/'stat').read_text().rsplit(')',1)[1].split()[19]
camera={str(pid):identity(pid,b'/usr/bin/di-camera-app-nx300') for pid in state['camera_pids']}
remote={str(pid):identity(pid,b'/mod/remote/nx-remote-controller.arm') for pid in state['remote_pids']}
if stop:
 for key,stamp in remote.items():
  if identity(int(key),b'/mod/remote/nx-remote-controller.arm')!=stamp: raise RuntimeError('Remote reused')
  os.kill(int(key),signal.SIGTERM)
 def alive(key,stamp):
  try:
   fields=(root/key/'stat').read_text().rsplit(')',1)[1].split()
   return fields[19]==stamp and fields[0]!='Z'
  except FileNotFoundError: return False
 deadline=time.monotonic()+8
 while any(alive(key,stamp) for key,stamp in remote.items()):
  if time.monotonic()>deadline: raise RuntimeError('Remote did not exit after TERM; camera preserved')
  time.sleep(.1)
source=pathlib.Path(sys.argv[3])
destination=guest/'mod/remote/nx-remote-controller.arm'
print(json.dumps({'namespace_pid':state['namespace_pid'],'camera':camera,'remote':remote,
 'deployed_binary_matches':source.read_bytes()==destination.read_bytes()}))
'''
    gallery = json.loads(session_file().read_text(encoding='utf-8')).get('gallery_share', False)
    source = ROOT / ('outputs/quickshare/gallery/nx-remote-controller.arm' if gallery else 'outputs/metol/remote/nx-remote-controller.arm')
    path = '/mnt/' + source.drive[0].lower() + source.as_posix()[2:]
    result = subprocess.run(['wsl.exe', '-u', 'root', '-e', 'python3', '-c', code,
                             json.dumps(dict(state, gallery=gallery)), '1' if stop else '0', path],
                            capture_output=True, timeout=15)
    if result.returncode:
        raise RuntimeError('Remote update identity check exit ' + str(result.returncode) + ': ' +
                           result.stderr.decode('utf-8', 'replace')[-1500:])
    return json.loads(result.stdout)


def main():
    state = inspect_session(refresh=True)
    if not state.get('running') or not state['camera_pids']:
        raise RuntimeError('Existing stock camera is not ready')
    before = observed(state, stop=True)
    # Let the shared inspection cache expire after the exact remote exits.
    time.sleep(.6)
    remote_stock.start()
    after = observed(inspect_session(refresh=True))
    if before['namespace_pid'] != after['namespace_pid'] or before['camera'] != after['camera']:
        raise RuntimeError('Camera identity changed during remote update')
    if not after['deployed_binary_matches'] or len(after['remote']) != 1:
        raise RuntimeError('Updated remote identity or copied binary mismatch')
    responses = {}
    for path in ('mods/status', 'mods/install-plan', 'exposure/iso', 'camera/info'):
        with urllib.request.urlopen('http://127.0.0.1:8765/api/v1/' + path, timeout=15) as response:
            responses[path] = {'http_status': response.status, 'body': json.load(response)}
    report = {'passed': True, 'before': before, 'after': after, 'api': responses,
              'camera_app_restarted': False, 'main_namespace_restarted': False,
              'photos_written': False, 'physical_camera_verified': False}
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'passed': True, 'report': str(OUT), 'namespace_pid': after['namespace_pid'],
                      'camera_pids': list(after['camera']), 'remote_pids': list(after['remote']),
                      'camera_app_restarted': False, 'deployed_binary_matches': True}))


if __name__ == '__main__':
    main()
