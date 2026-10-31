"""Exercise the native remote endpoints; optionally verify one session restart."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import struct
import subprocess
import time
import urllib.parse
import urllib.request

from session_stock import inspect_session

PROJECT = Path(__file__).resolve().parents[1]
BASE = 'http://127.0.0.1:8765'


def request(path, data=None, headers=None):
    with urllib.request.urlopen(urllib.request.Request(BASE+path, data=data, headers=headers or {}), timeout=30) as response:
        return response.status, dict(response.headers), response.read()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--restart', action='store_true')
    args = parser.parse_args()
    report = {'timestamp_utc': datetime.now(timezone.utc).isoformat()}
    if args.restart:
        before = inspect_session()
        proxy_file = PROJECT/'outputs/metol/remote/proxy.json'
        proxy = json.loads(proxy_file.read_text()) if proxy_file.exists() else {}
        old = [before['namespace_pid'], *before['camera_pids'], *before['metol_pids'], *before['remote_pids'], proxy.get('pid')]
        old = [pid for pid in old if pid]
        request('/stop', b'{}', {'Content-Type': 'application/json'})
        code = 'import pathlib,sys,json;print(json.dumps([int(p) for p in sys.argv[1:] if (pathlib.Path("/proc")/p).exists()]))'
        deadline = time.monotonic()+10
        while True:
            result = subprocess.run(['wsl.exe', '-u', 'root', '-e', 'python3', '-c', code, *map(str, old)], capture_output=True, check=True, timeout=10)
            remaining = json.loads(result.stdout)
            if not remaining or time.monotonic() >= deadline: break
            time.sleep(.2)
        report['restart'] = {'before': before, 'old_proxy_pid': proxy.get('pid'), 'remaining_old_pids': remaining}
        # Restore a usable session even when cleanup reveals a failure.
        request('/start', b'{}', {'Content-Type': 'application/json'})
        deadline = time.monotonic()+45
        while not inspect_session()['running']:
            if time.monotonic() >= deadline: raise TimeoutError('Stock camera did not start')
            time.sleep(.5)
        report['restart']['after'] = inspect_session()
    for key, path in [('info', '/api/v1/camera/info'), ('root', '/cgi-bin/dirlist'), ('files', '/cgi-bin/dirlist?p=100PHOTO')]:
        status, _, body = request(path)
        report[key] = {'status': status, 'body': json.loads(body)}
    status, _, body = request('/DCIM/100PHOTO/SAM_4262.JPG')
    fixture = json.loads((PROJECT/'outputs/metol/remote/media-fixture.json').read_text(encoding='utf-8'))
    report['photo'] = {'status': status, 'bytes': len(body), 'source_bytes_match': body == Path(fixture['jpeg']['source']).read_bytes()}
    status, headers, body = request('/api/v1/osd/get', b'')
    header = struct.unpack_from('>4H', body)
    hashes = ['00000000']*120
    stride = 8+80*40*4
    for offset in range(8, len(body)-4, stride):
        x, y, value = struct.unpack_from('>HHI', body, offset)
        hashes[y//40*10+x//80] = f'{value:08x}'
    report['osd'] = {'status': status, 'content_type': headers['Content-Type'], 'header': header, 'bytes': len(body), 'blocks': (len(body)-12)//stride, 'end': hex(struct.unpack_from('>I', body, len(body)-4)[0])}
    _, _, second = request('/api/v1/osd/get', urllib.parse.urlencode({'hashs': ''.join(hashes)}).encode(), {'Content-Type': 'application/x-www-form-urlencoded'})
    report['osd']['next_bytes'] = len(second)
    report['osd']['next_changed_blocks'] = (len(second)-12)//stride
    report['session'] = inspect_session()
    path = PROJECT/'outputs/metol/remote/integration.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report.get('restart', {}).get('remaining_old_pids'): raise RuntimeError('Old session processes remain; see integration.json')
    if (not report['photo']['source_bytes_match'] or header != (800, 480, 80, 40)
            or 'SAM_4262.JPG' not in report['files']['body']['files']):
        raise RuntimeError('Remote integration mismatch')


if __name__ == '__main__': main()
