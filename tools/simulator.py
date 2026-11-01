"""Local viewer and X11 input bridge for the genuine stock NX300M process."""
import argparse
import io
import http.client
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import socket
import sys
import threading
import time

from render_runtime_frame import read_frame
from session_stock import inspect_session, BackendUnavailable
from metol_stock import open_panel, engine_action, sync_looks
from look_library import LookLibrary
from mfw_runtime import firmware_profile
from remote_stock import start as start_remote, stop_proxy, read_osd_frame

PROJECT = Path(__file__).resolve().parents[1]
OUTPUT = PROJECT / 'outputs/firmware-runtime'
INPUT = PROJECT / 'tools/input_stock.py'
GUEST = '/opt/nx300m-lab/stock-1.15'
PAGE = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>NX300M 原厂程序</title><style>
body{margin:0;background:#151515;color:#e7e7e7;font:15px system-ui,sans-serif}main{max-width:960px;margin:36px auto;padding:0 20px}header{display:flex;align-items:center;justify-content:space-between;gap:20px}h1{font-size:21px;font-weight:550}#state{color:#bdbdbd}#screen{display:block;width:100%;max-width:800px;aspect-ratio:5/3;background:#000;margin:22px auto;object-fit:contain}nav{display:flex;gap:8px;flex-wrap:wrap;justify-content:center}button,select,input{font:inherit;color:inherit;border:1px solid #555;border-radius:5px;background:#262626;padding:8px 12px}button{cursor:pointer}button:hover{background:#383838}#message{min-height:24px;text-align:center;color:#bdbdbd;margin-top:18px}</style>
<main><header><h1>NX300M 原厂程序</h1><span id="state">正在连接</span></header>
<nav>
<button id="start-button" onclick="action('start')">启动</button><button id="stop-button" onclick="action('stop')">停止</button>
<button onclick="sendKey('XF86Tools')">回放</button>
<button onclick="sendKey('Menu')">MENU / 返回</button><button onclick="sendKey('KP_Up')">↑</button><button onclick="sendKey('KP_Down')">↓</button><button onclick="sendKey('KP_Left')">←</button><button onclick="sendKey('KP_Right')">→</button><button onclick="sendKey('KP_Enter')">OK</button><button onclick="sendKey('XF86ScrollUp')">拨盘 −</button><button onclick="sendKey('XF86ScrollDown')">拨盘 ＋</button>
<button onclick="action('metol')">Metol 配方面板</button>
<button onclick="action('lut')">Metol LUT 面板</button>
<button onclick="action('input',{action:'shutter',phase:'half'})">半按快门</button>
<button onclick="action('input',{action:'shutter',phase:'full'})">拍摄</button>
<button onclick="action('input',{action:'shutter',phase:'release'})">松开快门</button>
<a href="/remote/" target="_blank" rel="noopener" style="color:inherit;padding:8px 12px">机身设置 / LUT / 遥控相册</a>
</nav><div id="message"></div>
<p style="max-width:800px;margin:20px auto;color:#bdbdbd">ISO、快门上限、拨盘锁、RGB 和原厂画面参数统一在遥控页的“设置 → 机身设置…”中操作；LUT 选择位于“设置 → LUT 风格…”。</p></main>
<script>
const screen=document.querySelector('#screen'),state=document.querySelector('#state'),message=document.querySelector('#message');
async function action(path,body={}){try{let r=await fetch('/'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});let v=await r.json();if(!r.ok)throw Error(v.error);message.textContent=path==='input'?'':v.message||'';}catch(e){message.textContent=e.message}}
function sendKey(key){return action('input',{action:'key',key})}
screen.onclick=e=>{let b=screen.getBoundingClientRect();action('input',{action:'click',x:Math.floor((e.clientX-b.left)*800/b.width),y:Math.floor((e.clientY-b.top)*480/b.height)})};
let old,frameError='';async function refresh(){let statusKnown=false;
try{const response=await fetch('/status'),s=await response.json();
if(s.backend_available===false){state.textContent='WSL 后端不可用 · 相机状态未知';screen.removeAttribute('src');document.querySelector('#start-button').disabled=true;document.querySelector('#stop-button').disabled=true;throw Error(s.backend_error||'后端暂不可用，请等待恢复。');}
if(!response.ok)throw Error(s.error||'会话状态读取失败');statusKnown=true;
const firmware=s.firmware?.name||s.requested_firmware;
const title=firmware==='mfw'?'NX300M MFW 调试':'NX300M 原厂程序';
document.title=title;document.querySelector('h1').textContent=title;
document.querySelector('#start-button').disabled=s.running||s.starting;document.querySelector('#stop-button').disabled=!s.running&&!s.starting;
const labReplay=!!s.ipcm_replay||s.lcd_video?.source==='laboratory-lcd-backend';
state.textContent=s.running?(s.ipcm_replay?.stage==='failed'?'运行中 · 实验室回放中断':s.ipcm_replay?.stage==='submitted'?'运行中 · 实验室回放已结束':labReplay&&s.lcd_video?.frames?'运行中 · 实验室照片回放':'运行中'):s.starting?'正在启动':'原厂程序已停止';
if(s.running){let r=await fetch('/frame.png?'+Date.now());if(!r.ok)throw Error((await r.json()).error);let url=URL.createObjectURL(await r.blob());screen.src=url;if(old)URL.revokeObjectURL(old);old=url;if(r.headers.get('X-Frame-Blank')==='1')state.textContent='进程存活，当前无显示内容';}else{screen.removeAttribute('src');}
if(frameError&&message.textContent===frameError)message.textContent='';frameError='';
}catch(e){if(statusKnown)state.textContent='画面读取失败';else if(!state.textContent.startsWith('WSL 后端不可用')){state.textContent='查看器连接中断 · 相机状态未知';screen.removeAttribute('src');document.querySelector('#start-button').disabled=true;document.querySelector('#stop-button').disabled=true;}frameError=e.message;message.textContent=frameError;}
setTimeout(refresh,700)}refresh();
document.addEventListener('keydown',e=>{if(e.target.matches('input,select,a')||e.repeat||(e.key==='Enter'&&e.target.matches('button')))return;const keys={ArrowUp:'KP_Up',ArrowDown:'KP_Down',ArrowLeft:'KP_Left',ArrowRight:'KP_Right',Enter:'KP_Enter',Escape:'Menu'};if(keys[e.key]){e.preventDefault();sendKey(keys[e.key])}});
</script></html>'''

class Session:
    def __init__(self):
        self.process = None
        self.lock = threading.Lock()
        self.started_at = 0
        self.replay_packet = None
        self.sdcard = False
        self.replay_continuous = False
        self.shutter_motion = None
        self.pwm_timing = False
        self.bayer_replay = None
        self.exposure_controls = False
        self.shutter_limit = False
        self.color_style = False
        self.service_loader = False
        self.software_playback = False
        self.capture_observer = False
        self.firmware_mod = False
        self.qemu = None
        self.shutter_step_us = 10000
        self.firmware = 'mfw'

    def namespace(self):
        return self.require_backend(inspect_session())['namespace_pid']

    @staticmethod
    def require_backend(state):
        if state.get('backend_available') is False:
            raise BackendUnavailable(state.get('backend_error') or 'WSL 后端不可用，相机状态未知。')
        return state

    def status(self):
        state = inspect_session()
        state['viewer_pid'] = os.getpid()
        state['requested_firmware'] = self.firmware
        state['starting'] = None if state.get('backend_available') is False else (not state['running'] and self.process is not None
                            and self.process.poll() in (None, 0) and 0 < self.started_at
                            and time.monotonic() - self.started_at < 35)
        return state

    def start(self):
        with self.lock:
            state = self.require_backend(self.status())
            if state['running']:
                active_firmware = (state.get('firmware') or {}).get('name', 'stock')
                if active_firmware != self.firmware:
                    raise RuntimeError('当前运行的是 ' + active_firmware + '，请先停止再启动 ' + self.firmware + '。')
                return
            if state['starting']: return
            if state['namespace_pid']:
                self._stop_namespace(state['namespace_pid'])
            if self.process is not None and self.process.poll() is None:
                self.process.wait(timeout=15)
            log = (OUTPUT / 'viewer-launch.log').open('wb')
            try:
                command = [sys.executable, '-u', str(PROJECT / 'tools/run_stock.py'), '--program', 'camera', '--display', '--preferences', '--gpio-preview', '--device-preview', '--shutter-preview', '--shutter-park', '10', '--isp-preview', '--cpu-ycc-resize', '--spi-mmio', '--sensor-registers', '--d4c-registers', '--capture-boot', '--mmap-script-fix', '--ui-mod', '--lut-port', '--keep-awake', '--no-strace', '--seconds', '0', '--tag', 'viewer']
                command.extend(('--firmware', self.firmware))
                if self.replay_packet: command.extend(('--replay-packet', str(self.replay_packet)))
                if self.qemu: command.extend(('--qemu', self.qemu))
                if self.replay_continuous: command.append('--replay-continuous')
                if self.sdcard: command.append('--sdcard')
                if self.exposure_controls: command.append('--exposure-controls')
                if self.shutter_limit: command.append('--shutter-limit')
                if self.color_style: command.append('--color-style')
                if self.service_loader: command.append('--service-loader')
                if self.capture_observer: command.append('--capture-observer')
                if self.firmware_mod: command.append('--firmware-mod')
                if self.software_playback: command.extend(('--cpu-jpeg-decode','--cpu-playback','--gallery-share'))
                if self.shutter_motion:
                    command.extend(('--shutter-motion', self.shutter_motion, '--shutter-step-us', str(self.shutter_step_us)))
                if self.pwm_timing: command.append('--pwm-timing')
                if self.bayer_replay: command.extend(('--bayer-replay', str(self.bayer_replay)))
                self.process = subprocess.Popen(command, cwd=PROJECT, stdout=log, stderr=subprocess.STDOUT)
                self.started_at = time.monotonic()
            finally: log.close()

    def stop(self):
        with self.lock:
            pid = self.namespace()
            # A start request may still be preparing its namespace.
            if pid is None and self.process is not None and self.process.poll() in (None, 0) and time.monotonic() - self.started_at < 35:
                deadline = time.monotonic() + 15
                while pid is None and self.process.poll() in (None, 0) and time.monotonic() < deadline:
                    time.sleep(.2)
                    pid = self.namespace()
            if pid is not None:
                self._stop_namespace(pid)
            if self.process is not None and self.process.poll() is None:
                self.process.wait(timeout=15)
            self.started_at = 0

    def _stop_namespace(self, pid):
        # Auxiliary programs join the mount/network namespaces from outside
        # the PID namespace, so its supervisor cannot reap them automatically.
        state = self.require_backend(inspect_session(refresh=True))
        if state['namespace_pid'] != pid:
            if state['namespace_pid'] is None: return
            raise RuntimeError('原厂会话已变化，未停止其他会话；请重新读取状态。')
        stop_proxy(pid)
        if state['namespace_pid'] == pid:
            auxiliary = state.get('metol_pids', []) + state.get('remote_pids', [])
            if auxiliary:
                subprocess.run(['wsl.exe', '-u', 'root', '-e', 'kill', '-TERM', *map(str, auxiliary)], capture_output=True, timeout=10)
        subprocess.run(['wsl.exe', '-u', 'root', '-e', 'kill', '-KILL', str(pid)], capture_output=True, timeout=10, check=True)
        deadline = time.monotonic() + 10
        while self.namespace() == pid:
            if time.monotonic() > deadline: raise TimeoutError('原厂会话未能停止')
            time.sleep(.1)

SESSION = Session()

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_): pass

    def is_remote(self):
        return self.path == '/remote' or self.path.startswith(('/remote/', '/api/v1/', '/DCIM', '/cgi-bin/'))

    def proxy_remote(self):
        connection = None
        started = False
        try:
            length = int(self.headers.get('Content-Length', 0))
            path = self.path[len('/remote'):] if self.path.startswith('/remote') else self.path
            upload = path.split('?', 1)[0] == '/api/v1/gallery/upload'
            limit = 120 * 1024 * 1024 if upload else 1048576
            if length < 0 or length > limit or (upload and not length):
                raise ValueError('请求为空或过大')
            if self.headers.get('Transfer-Encoding'):
                raise ValueError('请使用 Content-Length 上传')
            with SESSION.lock:
                state = SESSION.require_backend(SESSION.status())
                if not state.get('running') or not state.get('namespace_pid'):
                    raise RuntimeError('请先启动原厂程序')
                pid = start_remote()
            body = self.rfile.read(length)
            if len(body) != length: raise ValueError('请求内容不完整')
            if not path: path = '/'
            headers = {name: self.headers[name] for name in ('Content-Type', 'Range', 'If-Modified-Since') if name in self.headers}
            editing = path.split('?', 1)[0] in ('/api/v1/gallery/upload', '/api/v1/gallery/restore')
            connection = http.client.HTTPConnection('127.0.0.1', 8782, timeout=95 if editing else 20)
            connection.request(self.command, path, body=body, headers=headers)
            response = connection.getresponse()
            self.send_response(response.status)
            for name, value in response.getheaders():
                if name.lower() not in ('transfer-encoding', 'connection', 'server', 'date', 'keep-alive'):
                    self.send_header(name, value)
            self.send_header('Connection', 'close')
            self.end_headers(); started = True; self.close_connection = True
            while chunk := response.read(65536): self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError): pass
        except ValueError as exc:
            if not started:
                self.close_connection = True
                self.reply({'error': str(exc)}, 400)
        except Exception as exc:
            if not started: self.reply({'error': str(exc)}, 503)
        finally:
            if connection: connection.close()

    def reply(self, data, status=200, mime='application/json; charset=utf-8', headers=None):
        if isinstance(data, dict): data = json.dumps(data, ensure_ascii=False).encode('utf-8')
        if isinstance(data, str): data = data.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        for key, value in (headers or {}).items(): self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def native_control(self, path, values=None):
        # Both lab pages and native clients use the same ARM service. Only the
        # lab supervisor handle is added here for this page's session checks.
        with SESSION.lock:
            state = SESSION.require_backend(SESSION.status())
            if not state['running']:
                raise RuntimeError('请先启动原厂程序')
            start_remote()
            connection = http.client.HTTPConnection('127.0.0.1', 8782, timeout=16)
            try:
                body = None if values is None else json.dumps(values).encode('utf-8')
                connection.request('GET' if values is None else 'POST',
                                   '/api/v1/'+path, body=body,
                                   headers={'Content-Type': 'application/json'})
                response = connection.getresponse()
                result = json.loads(response.read())
                status = response.status
            finally:
                connection.close()
            if isinstance(result, dict) and result.get('native_service'):
                result['namespace_pid'] = state['namespace_pid']
        self.reply(result, status)

    def do_GET(self):
        if self.path == '/remote':
            self.send_response(302); self.send_header('Location', '/remote/'); self.end_headers(); return
        if self.is_remote(): self.proxy_remote(); return
        try:
            if self.path == '/': self.reply(PAGE, mime='text/html; charset=utf-8')
            elif self.path == '/downloads/nxconnect.apk':
                apk = PROJECT / 'outputs/nxconnect/NXConnect-NX300M-debug.apk'
                if not apk.is_file():
                    self.reply({'error': 'APK 尚未构建'}, 404)
                else:
                    self.reply(apk.read_bytes(), mime='application/vnd.android.package-archive',
                               headers={'Content-Disposition': 'attachment; filename="NXConnect-NX300M.apk"'})
            elif self.path == '/status':
                state = SESSION.status()
                self.reply(state, 503 if state.get('backend_available') is False else 200)
            elif self.path == '/looks': self.reply({'looks': LookLibrary().list()})
            elif self.path.split('?')[0] == '/simulator/info':
                state = SESSION.status()
                self.reply({'simulator': True, 'requested_firmware': SESSION.firmware,
                            'firmware': state.get('firmware'),
                            'capabilities': {'look_library_sync': True}})
            elif self.path == '/picture-wizard':
                self.native_control('color/picture-wizard')
            elif self.path == '/exposure-controls':
                self.native_control('exposure/iso')
            elif self.path == '/shutter-limit':
                self.native_control('exposure/shutter')
            elif self.path == '/dial-lock':
                self.native_control('controls/dial-lock')
            elif self.path.split('?')[0] == '/frame.png':
                SESSION.require_backend(SESSION.status())
                if SESSION.software_playback or (PROJECT/'outputs/metol/remote/nx-remote-controller.arm').is_file():
                    with SESSION.lock: start_remote()
                    image = read_osd_frame()
                else:
                    image, _ = read_frame(live=True)
                out = io.BytesIO(); image.save(out, format='PNG')
                blank = image.getbbox() is None
                self.reply(out.getvalue(), mime='image/png', headers={'X-Frame-Blank': str(int(blank))})
            else: self.reply({'error':'页面不存在'}, 404)
        except Exception as exc: self.reply({'error':str(exc)}, 503)

    def handle_gallery_upload(self):
        # Use the same native media-root validation and atomic replacement as
        # physical clients. File names never become shell source here.
        self.proxy_remote()

    def do_PUT(self):
        if self.is_remote():
            self.do_POST()
        else:
            self.reply({'error': '此接口不接受 PUT'}, 405)

    def do_POST(self):
        origin = self.headers.get('Origin')
        if origin and origin != f'http://{self.headers.get("Host")}':
            self.close_connection = True
            self.reply({'error':'请求来源不匹配'}, 403); return
        if self.path.startswith('/api/v1/gallery/upload'):
            self.handle_gallery_upload(); return
        if self.is_remote(): self.proxy_remote(); return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 <= length <= 4096: raise ValueError('请求过大')
            body = json.loads(self.rfile.read(length) or b'{}')
            SESSION.require_backend(SESSION.status())
            if self.path == '/start': SESSION.start(); self.reply({'message':'正在启动原厂程序'})
            elif self.path == '/stop': SESSION.stop(); self.reply({'message':'已停止'})
            elif self.path == '/metol':
                with SESSION.lock: open_panel()
                self.reply({'message':'正在打开 Metol 配方面板'})
            elif self.path == '/lut':
                with SESSION.lock: open_panel('lut')
                self.reply({'message':'正在打开 LUT 面板'})
            elif self.path == '/looks/sync':
                if body != {}: raise ValueError('同步接口只接受空对象')
                with SESSION.lock: result = sync_looks()
                self.reply(result)
            elif self.path == '/look-select':
                with SESSION.lock: result = engine_action('apply', str(body['id']))
                self.reply({'message':'LUT 已暂存，等待原厂重载', 'backend': result})
            elif self.path == '/look-off':
                with SESSION.lock: result = engine_action('off')
                self.reply({'message':'已请求恢复先前 LUT，等待原厂重载', 'backend': result})
            elif self.path == '/picture-wizard':
                self.native_control('color/picture-wizard', body)
            elif self.path == '/exposure-controls':
                if not isinstance(body, dict) or not body or set(body) - {'iso_step', 'iso_expansion', 'auto_iso_max'}:
                    raise ValueError('请提供 ISO 步长、ISO 扩展或 Auto ISO 上限')
                self.native_control('exposure/iso', body)
            elif self.path == '/shutter-limit':
                if not isinstance(body, dict) or set(body) != {'fastest'}:
                    raise ValueError('请提供最快快门目标')
                self.native_control('exposure/shutter', body)
            elif self.path == '/dial-lock':
                self.native_control('controls/dial-lock', body)
            elif self.path == '/input':
                if SESSION.namespace() is None: raise ValueError('请先启动原厂程序')
                action = body.get('action')
                if action == 'click': args = ['click', str(int(body['x'])), str(int(body['y']))]
                elif action == 'key': args = ['key', str(body['key'])]
                elif action == 'shutter':
                    phase = body.get('phase')
                    if phase not in ('half', 'full', 'release'): raise ValueError('未知快门输入')
                    args = ['shutter', phase]
                else: raise ValueError('未知输入动作')
                with SESSION.lock:
                    if SESSION.software_playback: start_remote()
                    result = subprocess.run([sys.executable, str(INPUT), *args], capture_output=True, text=True, timeout=20)
                if result.returncode: raise ValueError(result.stderr.strip())
                self.reply({'message':'已发送', 'input':json.loads(result.stdout)})
            else: self.reply({'error':'接口不存在'}, 404)
        except BackendUnavailable as exc:
            self.reply({'error':str(exc), 'backend_available':False, 'camera_state':'unknown'}, 503)
        except Exception as exc: self.reply({'error':str(exc)}, 400)

class ViewerHTTPServer(ThreadingHTTPServer):
    # Windows SO_REUSEADDR permits concurrent listeners with nondeterministic
    # request routing. An old viewer must not receive a new build's requests.
    allow_reuse_address = os.name != 'nt'

    def server_bind(self):
        if os.name == 'nt':
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--host', default='127.0.0.1', help='HTTP bind address; use 0.0.0.0 for LAN access')
    parser.add_argument('--profile', choices=('minimal', 'development'), default='minimal',
                        help='development: enable SD, exposure/shutter/RGB, capture observation, native gallery software playback and continuous laboratory photo replay; remote uses its development supervisor')
    parser.add_argument('--no-start', action='store_true')
    parser.add_argument('--firmware', choices=('stock', 'mfw'), default='mfw',
                        help='Select stock user space or the camera-exported MFW script library (default)')
    parser.add_argument('--qemu', help='WSL absolute path to a QEMU runner, privately mounted for new camera sessions')
    parser.add_argument('--replay-packet', type=Path, help='Replay a prepared JSON/YUV input when starting the stock camera')
    parser.add_argument('--sdcard', action='store_true', help='Attach the persistent lab FAT32 card when starting the stock camera')
    parser.add_argument('--replay-continuous', action='store_true', help='Continue recorded frame input until the stock session stops')
    parser.add_argument('--shutter-motion', choices=('observe', 'ideal-8-sector'), help='Optional motor observation or uncalibrated rotor scenario')
    parser.add_argument('--pwm-timing', action='store_true', help='Optional finite PWM timing with explicit external input levels')
    parser.add_argument('--bayer-replay', type=Path, help='Explicit recorded Bayer packet at the PP WDMA boundary')
    parser.add_argument('--exposure-controls', action='store_true', help='Enable the original UserData ISO preference bridge')
    parser.add_argument('--shutter-limit', action='store_true', help='Enable the capture AE fastest-shutter preference bridge')
    parser.add_argument('--color-style', action='store_true', help='Enable independent RGB matrix gains; neutral until configured')
    parser.add_argument('--service-loader', action='store_true', help='Load the native fixed-path SD service trigger')
    parser.add_argument('--capture-observer', action='store_true', help='Enable passive capture observations and on-demand stock status reads')
    parser.add_argument('--firmware-mod', action='store_true', help='Load firmware controls; verified entries enable Custom panel and instant mode animation; mode and ISO locks remain off')
    parser.add_argument('--software-playback',action='store_true',help='Enable software JPEG/playback tasks and the original gallery Quick Share entry on the lab SD card')
    parser.add_argument('--shutter-step-us', type=int, default=10000)
    args = parser.parse_args()
    firmware_profile(args.firmware, prepare=True)
    SESSION.firmware = args.firmware
    if args.profile == 'development':
        args.sdcard = True
        args.exposure_controls = True
        args.shutter_limit = True
        args.color_style = True
        args.software_playback = True
        args.capture_observer = True
        args.replay_packet = args.replay_packet or OUTPUT / 'ipcm-replay-input.json'
        args.replay_continuous = True
    if args.software_playback:args.sdcard=True
    SESSION.sdcard = args.sdcard
    SESSION.qemu = args.qemu
    SESSION.software_playback=args.software_playback
    SESSION.capture_observer=args.capture_observer
    SESSION.firmware_mod=args.firmware_mod
    if not 3000 <= args.shutter_step_us <= 1000000: parser.error('--shutter-step-us must be 3000..1000000')
    SESSION.shutter_motion = args.shutter_motion
    SESSION.pwm_timing = args.pwm_timing
    SESSION.bayer_replay = args.bayer_replay
    SESSION.exposure_controls = args.exposure_controls
    SESSION.shutter_limit = args.shutter_limit
    SESSION.color_style = args.color_style
    SESSION.service_loader = args.service_loader
    SESSION.shutter_step_us = args.shutter_step_us
    if args.replay_continuous and not args.replay_packet: parser.error('--replay-continuous requires --replay-packet')
    SESSION.replay_continuous = args.replay_continuous
    if args.replay_packet:
        packet = args.replay_packet.resolve()
        if not packet.is_file() or not packet.with_suffix('.yuv').is_file(): parser.error('Replay JSON/YUV input pair is missing')
        SESSION.replay_packet = packet
    OUTPUT.mkdir(parents=True, exist_ok=True)
    server = ViewerHTTPServer((args.host, args.port), Handler)
    if args.profile == 'development':
        print(f'开发预设：模拟 SD 卡、曝光／快门／RGB、拍摄观察、原厂相册软件回放与分享；遥控由开发服务管理；实验室录制照片持续回放：{SESSION.replay_packet}', flush=True)
    if not args.no_start:
        try: SESSION.start()
        except BackendUnavailable as exc: print(str(exc), file=sys.stderr, flush=True)
    print(f'原厂程序查看器：http://{args.host}:{args.port} (PID {os.getpid()})', flush=True)
    try: server.serve_forever()
    finally: server.server_close()

if __name__ == '__main__': main()
