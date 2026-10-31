"""Deploy/run the native remote module in the existing isolated stock session."""
import json
import http.client
from pathlib import Path
import shlex
import struct
import subprocess
import sys
import time
import urllib.request

from session_stock import inspect_session, session_file

PROJECT = Path(__file__).resolve().parents[1]
GUEST = '/opt/nx300m-lab/stock-1.15'
OUTPUT = PROJECT/'outputs/metol/remote'
_known_namespace = None
_checked_at = 0


def proxy_health():
    with urllib.request.urlopen('http://127.0.0.1:8782/__nx_proxy_status', timeout=2) as response:
        return json.load(response)


def stop_proxy(namespace):
    global _known_namespace, _checked_at
    state_file = OUTPUT/'proxy.json'
    if not state_file.exists(): return
    state = json.loads(state_file.read_text(encoding='utf-8'))
    if state.get('namespace_pid') != namespace: return
    expected = '/mnt/' + PROJECT.drive[0].lower() + PROJECT.as_posix()[2:] + '/tools/remote_proxy.py'
    code = '''import os,pathlib,signal,sys
pid=int(sys.argv[1]); target=pathlib.Path('/proc')/str(pid)
try:
 argv=(target/'cmdline').read_bytes().split(b'\\0')
 if sys.argv[2].encode() not in argv: raise RuntimeError('Proxy process identity changed')
 os.kill(pid,signal.SIGTERM)
except FileNotFoundError: pass
'''
    subprocess.run(['wsl.exe', '-u', 'root', '-e', 'python3', '-c', code, str(state['pid']), expected], check=True, capture_output=True, timeout=10)
    state_file.unlink(missing_ok=True); _known_namespace = None; _checked_at = 0


def start_proxy(namespace):
    try: health = proxy_health()
    except OSError: health = None
    if health:
        if health.get('namespace_pid') == namespace: return
        stop_proxy(health['namespace_pid'])
        deadline = time.monotonic()+5
        while time.monotonic() < deadline:
            try: proxy_health()
            except OSError: break
            time.sleep(.1)
        else: raise RuntimeError('Previous loopback proxy is still running')
    elif (OUTPUT/'proxy.json').exists():
        state = json.loads((OUTPUT/'proxy.json').read_text(encoding='utf-8'))
        expected = '/mnt/' + PROJECT.drive[0].lower() + PROJECT.as_posix()[2:] + '/tools/remote_proxy.py'
        code = '''import pathlib,sys
p=pathlib.Path('/proc')/sys.argv[1]
try:
 argv=(p/'cmdline').read_bytes().split(b'\\0')
 print(1 if sys.argv[2].encode() in argv else 0)
except FileNotFoundError: print(0)
'''
        result = subprocess.run(['wsl.exe', '-u', 'root', '-e', 'python3', '-c', code, str(state['pid']), expected], capture_output=True, text=True, check=True, timeout=10)
        if result.stdout.strip() == '1': raise RuntimeError('现有本机代理仍在运行，健康检查暂未返回；保留当前进程')
        (OUTPUT/'proxy.json').unlink(missing_ok=True)
    base = '/mnt/' + PROJECT.drive[0].lower() + PROJECT.as_posix()[2:]
    script = f'exec >{shlex.quote(base+"/outputs/metol/remote/proxy.log")} 2>&1\nexec python3 {shlex.quote(base+"/tools/remote_proxy.py")} --namespace {namespace} --state {shlex.quote(base+"/outputs/metol/remote/proxy.json")}'
    process = subprocess.Popen(['wsl.exe', '-u', 'root', '-e', 'sh', '-c', script], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic()+15
    while time.monotonic() < deadline:
        if process.poll() is not None: break
        try:
            if proxy_health().get('namespace_pid') == namespace: return
        except OSError: pass
        time.sleep(.2)
    raise RuntimeError('本机遥控代理未就绪，请查看 outputs/metol/remote/proxy.log')


def request_command(pid, method, path, headers=None):
    script = '/mnt/' + PROJECT.drive[0].lower() + PROJECT.as_posix()[2:] + '/tools/remote_request.py'
    return ['wsl.exe', '-u', 'root', '-e', 'nsenter', '-t', str(pid), '--net',
            '/usr/bin/python3', script, method, path, json.dumps(headers or {})]


def request(pid, path, method='GET', body=b'', headers=None):
    result = subprocess.run(request_command(pid, method, path, headers), input=body,
                            capture_output=True, timeout=25)
    if result.returncode: raise RuntimeError(result.stderr.decode('utf-8', 'replace').strip())
    size = struct.unpack_from('<I', result.stdout)[0]
    metadata = json.loads(result.stdout[4:4+size])
    return metadata, result.stdout[4+size:]


def read_osd_frame():
    """Decode a full native OSD packet without starting a WSL client per frame.

    The caller ensures start() has completed under the viewer's session lock.
    All clients consume the same LCD video + X11 OSD composition from ARM.
    """
    from PIL import Image

    connection = http.client.HTTPConnection('127.0.0.1', 8782, timeout=10)
    try:
        connection.request('POST', '/api/v1/osd/get', body=b'')
        response = connection.getresponse()
        data = response.read()
        if response.status != 200:
            raise RuntimeError('原生屏幕读取失败: '+data.decode('utf-8', 'replace'))
    finally:
        connection.close()
    if len(data) < 12 or struct.unpack_from('>4H', data) != (800, 480, 80, 40):
        raise ValueError('原生 OSD 帧头无效')
    stride = 8+80*40*4
    if len(data) != 12+120*stride or data[-4:] != b'\xff'*4:
        raise ValueError('原生 OSD 完整帧长度无效')
    image = Image.new('RGB', (800, 480))
    seen = set()
    for offset in range(8, len(data)-4, stride):
        x, y, _ = struct.unpack_from('>HHI', data, offset)
        if x % 80 or y % 40 or x >= 800 or y >= 480 or (x, y) in seen:
            raise ValueError('原生 OSD 分块坐标无效')
        seen.add((x, y))
        block = Image.frombytes('RGBA', (80, 40), data[offset+8:offset+stride])
        image.paste(block.convert('RGB'), (x, y))
    return image


def start():
    global _known_namespace, _checked_at
    session = json.loads(session_file().read_text(encoding='utf-8'))
    if _known_namespace == session.get('namespace_pid') and time.monotonic()-_checked_at < 2:
        return _known_namespace
    state = inspect_session()
    if not state['running']: raise RuntimeError('原厂相机程序尚未运行')
    if state.get('remote_pids'):
        start_proxy(state['namespace_pid'])
        _known_namespace = state['namespace_pid']; _checked_at = time.monotonic()
        return _known_namespace
    gallery = bool(session.get('gallery_share'))
    binary = PROJECT/'outputs/quickshare/gallery/nx-remote-controller.arm' if gallery else OUTPUT/'nx-remote-controller.arm'
    if not binary.is_file():
        raise RuntimeError('请先运行 tools/build_gallery_share.py' if gallery else '请先运行 tools/build_remote.py')
    base = '/mnt/' + PROJECT.drive[0].lower() + PROJECT.as_posix()[2:]
    binary_source = base+'/'+binary.relative_to(PROJECT).as_posix()
    gallery_setup = ''
    if gallery:
        # Bootstrap normally runs before the camera scans the SD card. This
        # also prepares older development sessions without adding a service.
        gallery_setup = f'''if [ ! -f {shlex.quote(GUEST+'/var/run/nx300m-gallery-runtime.json')} ]; then
python3 {shlex.quote(base+'/tools/stock_gallery_runtime.py')} --bootstrap {shlex.quote(GUEST)}
fi
'''
    quickshare = PROJECT/'outputs/quickshare/nx-quickshare-armv7'
    copy_quickshare = ''
    if quickshare.is_file():
        copy_quickshare = f'''mkdir -p {shlex.quote(GUEST+'/mod/quickshare')}
cp {shlex.quote(base+'/outputs/quickshare/nx-quickshare-armv7')} {shlex.quote(GUEST+'/mod/quickshare/nx-quickshare-armv7.tmp')}
chmod 755 {shlex.quote(GUEST+'/mod/quickshare/nx-quickshare-armv7.tmp')}
mv {shlex.quote(GUEST+'/mod/quickshare/nx-quickshare-armv7.tmp')} {shlex.quote(GUEST+'/mod/quickshare/nx-quickshare-armv7')}
'''
    media = '/mnt/mmc/DCIM'
    reference = PROJECT/'outputs/looks/photo-reference.jpg'
    copy_media = ''
    if reference.exists() and not (session.get('sdcard') or state.get('sdcard') or gallery):
        # Simulator-only card contents, copied from the already extracted local
        # reference. The actual camera and the photo library remain untouched.
        media = '/mod/remote/media/DCIM'
        copy_media = f'''mkdir -p {shlex.quote(GUEST+media+'/100PHOTO')}
cp {shlex.quote(base+'/outputs/looks/photo-reference.jpg')} {shlex.quote(GUEST+media+'/100PHOTO/SAM_4262.JPG')}
'''
        source_info = PROJECT/'outputs/looks/photo-reference.json'
        fixture = {'jpeg': {'source': str(reference), 'kind': 'original SRW embedded JPEG'},
                   'scope': 'private simulator copies of existing local photos; not new captures'}
        if source_info.is_file():
            metadata = json.loads(source_info.read_text(encoding='utf-8'))
            original_jpeg = Path(metadata.get('external_jpeg', {}).get('path', ''))
            if original_jpeg.is_file():
                source = '/mnt/'+original_jpeg.drive[0].lower()+original_jpeg.as_posix()[2:]
                copy_media += f'cp {shlex.quote(source)} {shlex.quote(GUEST+media+"/100PHOTO/SAM_4262.JPG")}\n'
                fixture['jpeg'] = {'source': str(original_jpeg), 'kind': 'original camera JPEG with EXIF',
                                   'bytes': original_jpeg.stat().st_size}
            raw = Path(metadata['raw']['path'])
            if raw.is_file() and raw.suffix.lower() == '.srw':
                source = '/mnt/'+raw.drive[0].lower()+raw.as_posix()[2:]
                copy_media += f'cp {shlex.quote(source)} {shlex.quote(GUEST+media+"/100PHOTO/SAM_4262.SRW")}\n'
                fixture['raw'] = {'source': str(raw), 'bytes': raw.stat().st_size,
                                  'destination': media+'/100PHOTO/SAM_4262.SRW'}
        (OUTPUT/'media-fixture.json').write_text(json.dumps(fixture, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    setup = f'''set -eu
ip link set lo up
{gallery_setup}
mkdir -p {shlex.quote(GUEST+'/mod/remote/web_root')}
cp {shlex.quote(binary_source)} {shlex.quote(GUEST+'/mod/remote/nx-remote-controller.arm.tmp')}
mv {shlex.quote(GUEST+'/mod/remote/nx-remote-controller.arm.tmp')} {shlex.quote(GUEST+'/mod/remote/nx-remote-controller.arm')}
chmod 755 {shlex.quote(GUEST+'/mod/remote/nx-remote-controller.arm')}
cp -R {shlex.quote(base+'/outputs/metol/remote/web_root/.')} {shlex.quote(GUEST+'/mod/remote/web_root/')}
cp {shlex.quote(base+'/outputs/metol/recipes.txt')} {shlex.quote(GUEST+'/mod/recipes.txt.tmp')}
mv {shlex.quote(GUEST+'/mod/recipes.txt.tmp')} {shlex.quote(GUEST+'/mod/recipes.txt')}
{copy_quickshare}
{copy_media}'''
    subprocess.run(['wsl.exe', '-u', 'root', '-e', 'nsenter', '-t', str(state['namespace_pid']),
                    '--mount', '--net', 'sh', '-c', setup], check=True, capture_output=True, timeout=25)
    script = f'''exec >{shlex.quote(base+'/outputs/metol/remote/server.log')} 2>&1
exec chroot --userspec=65534:65534 {shlex.quote(GUEST)} /qemu-arm-static -cpu cortex-a9 -L / /mod/remote/nx-remote-controller.arm --media-root {shlex.quote(media)} --quickshare-binary /mod/quickshare/nx-quickshare-armv7 --quickshare-runner /qemu-arm-static
'''
    process = subprocess.Popen(['wsl.exe', '-u', 'root', '-e', 'nsenter', '-t', str(state['namespace_pid']),
                                '--mount', '--net', '--ipc', 'env', 'DISPLAY=:99', 'sh', '-c', script],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic()+20
    error = None
    while time.monotonic() < deadline:
        # The viewer can start this same endpoint while a remote-only update
        # replaces it. A losing bind process may exit before the winner is
        # ready; observe the namespace's endpoint for the bounded grace period
        # instead of declaring failure from this launcher process alone.
        try:
            metadata, body = request(state['namespace_pid'], '/api/v1/camera/info')
            if metadata['status'] == 200 and json.loads(body)['model'] == 'NX300M':
                start_proxy(state['namespace_pid'])
                _known_namespace = state['namespace_pid']; _checked_at = time.monotonic()
                return _known_namespace
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc: error = exc
        time.sleep(.2)
    log = (OUTPUT/'server.log').read_text(encoding='utf-8', errors='replace') if (OUTPUT/'server.log').exists() else ''
    raise RuntimeError('原生遥控模块启动失败: '+log[-2000:]+' '+str(error or ''))


if __name__ == '__main__':
    pid = start()
    metadata, body = request(pid, sys.argv[1] if len(sys.argv)>1 else '/api/v1/camera/info')
    print(json.dumps(metadata))
    print(body.decode('utf-8', 'replace'))
