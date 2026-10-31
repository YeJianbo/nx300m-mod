"""Exercise the shipped APK in an Android emulator against a gated RVF fixture.

The HTTP/AVI peer is synthetic. This checks actual Activity, HTTP and decoder
integration; it does not emulate the camera's WLAN driver or native pairing UI.
"""
import argparse
import io
import json
from pathlib import Path
import re
import struct
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

from PIL import Image, ImageDraw

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = PROJECT / 'outputs/nxconnect/rvf-emulator-0.20.28'
ADB = Path.home() / 'AppData/Local/Android/Sdk/platform-tools/adb.exe'
APK = PROJECT / 'outputs/nxconnect/NXConnect-NX300M-release.apk'
SERVICE = 'urn:schemas-upnp-org:service:ContentDirectory:1'
STOCK_RVF = PROJECT / 'work/validation/rvf-investigation/libdi-network-dlna-rvf.so'
STOCK_BODY = PROJECT / 'outputs/nxconnect/stock-rvf/serialization/stock-generated-response-body.xml'
STREAM_PATHS = ('/livestream.avi', '/qvga_livestream.avi')


def stock_fixture_xml():
    # Exact firmware 1.15 embedded SCPD; do not invent a keepalive or exit action.
    scpd = STOCK_RVF.read_bytes()[0x5a304:0x5c75c].decode('utf-8')
    root = ET.fromstring(scpd)
    ns = {'s': 'urn:schemas-upnp-org:service-1-0'}
    actions = {a.findtext('s:name', namespaces=ns): a for a in root.findall('s:actionList/s:action', ns)}
    info_args = actions['GetInfomation'].findall('s:argumentList/s:argument', ns)
    assert not any(a.findtext('s:direction', namespaces=ns) == 'in' for a in info_args)
    assert 'HeartBeat' not in actions
    # This is the stock formatter's controlled-input output, not a captured
    # complete camera response. Only substitute the fixture's reachable host.
    body = STOCK_BODY.read_text(encoding='utf-8').replace('192.0.2.10:7677', '127.0.0.1:7677')
    parsed = ET.fromstring(body)
    stream = parsed.find('StreamUrl')
    assert stream is not None and stream.find('QualityHighUrl') is not None
    return scpd, body, set(actions)


def chunk(name, data):
    return name.encode('ascii') + struct.pack('<I', len(data)) + data + b'\0' * (len(data) % 2)


def avi_header():
    stream = chunk('strh', b'vidsMJPG' + bytes(48)) + chunk('strf',
        struct.pack('<IiiHH', 40, 640, 360, 1, 24) + b'MJPG' + bytes(20))
    return b'RIFF' + bytes(4) + b'AVI ' + chunk('LIST', b'hdrl' + chunk('LIST', b'strl' + stream)) + b'LIST' + bytes(4) + b'movi'


class Peer:
    def __init__(self, scpd, information_body):
        self.gate = threading.Event()
        self.lock = threading.Lock()
        self.records = []
        self.reject = False
        self.scpd = scpd
        self.information_body = information_body
        self.break_stream = threading.Event()
        self.frames = 0
        self.capturing = threading.Event()
        self.capture_paused = threading.Event()
        self.resume_stream = threading.Event()
        self.shot_fault = False
        self.busy_info_requests = 0

    def action_count(self, action):
        with self.lock:
            return sum(r['action'] == action for r in self.records)

    def record(self, handler, action=None, body=None):
        with self.lock:
            self.records.append({'time': time.time(), 'path': handler.path,
                'method': handler.command, 'action': action,
                'user_agent': handler.headers.get('User-Agent'),
                'access_method': handler.headers.get('Access-Method'),
                'body': body})

    def count(self, path):
        with self.lock:
            return sum(urlsplit(r['path']).path == path for r in self.records)

    def raw_input_count(self):
        with self.lock:
            return sum(urlsplit(r['path']).path.startswith('/api/v1/input/') for r in self.records)

    def wait_count(self, path, count, timeout=15):
        deadline = time.monotonic() + timeout
        while self.count(path) < count and time.monotonic() < deadline:
            time.sleep(.05)
        assert self.count(path) == count, (path, self.count(path), count)


def serve(peer):
    image = Image.new('RGB', (640, 360), '#16232d')
    draw = ImageDraw.Draw(image)
    for i, color in enumerate(['#e65757', '#57c775', '#588fe0', '#e8c559', '#c87ac7', '#5bc8c8']):
        draw.rectangle((20 + i * 100, 55, 110 + i * 100, 270), fill=color)
    draw.text((25, 20), 'RVF PROTOCOL FIXTURE - NOT CAMERA SENSOR OUTPUT', fill='white')
    buffer = io.BytesIO()
    image.save(buffer, 'JPEG', quality=90)
    frame = chunk('00dc', buffer.getvalue())

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, value, status=200, content_type='application/json'):
            if not isinstance(value, (str, bytes)):
                value = json.dumps(value)
            if isinstance(value, str):
                value = value.encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(value)))
            self.end_headers()
            self.wfile.write(value)

        def envelope(self, action, value='', status=200):
            self.reply(f'<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body><u:{action}Response xmlns:u="{SERVICE}">{value}</u:{action}Response></s:Body></s:Envelope>', status, 'text/xml')

        def soap_fault(self, message):
            self.reply('<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
                '<s:Body><s:Fault><faultcode>s:Client</faultcode><faultstring>' + message +
                '</faultstring></s:Fault></s:Body></s:Envelope>', 500, 'text/xml')

        def do_GET(self):
            peer.record(self)
            path = urlsplit(self.path).path
            try:
                if path == '/api/v1/camera/info':
                    if peer.capturing.is_set():
                        peer.busy_info_requests += 1
                        self.reply({'error': 'capture busy'}, 503)
                        return
                    self.reply({'model': 'NX300M', 'fw_ver': '1.15', 'instance_id': 'rvf-emulator-fixture',
                        'capabilities': {'sensor_liveview': False, 'screen_mirroring': True,
                            'key_input': True, 'keep_alive': False, 'shell': False,
                            'lcd_control': False, 'silent_shutter': False, 'time_sync': False}})
                elif path == '/api/v1/camera/status':
                    self.reply({'battery_level': 3, 'battery_charging': False, 'mode': 'WIFI', 'drive': 'SINGLE'})
                elif path == '/cgi-bin/dirlist':
                    self.reply({'ok': True, 'dirs': [], 'files': []})
                elif path.startswith('/api/v1/input/'):
                    self.reply({'ok': True})
                elif path == '/smp_2_':
                    if not peer.gate.wait(90):
                        self.reply('confirmation timeout', 408, 'text/plain')
                    elif peer.reject:
                        self.reply('permission denied by simulated camera', 403, 'text/plain')
                    else:
                        self.reply(f'<root><device><serviceList><service><serviceType>{SERVICE}</serviceType><controlURL>/rvf/control</controlURL><SCPDURL>/rvf/actions.xml</SCPDURL></service></serviceList></device></root>', content_type='text/xml')
                elif path == '/rvf/actions.xml':
                    self.reply(peer.scpd, content_type='text/xml')
                elif path in STREAM_PATHS:
                    self.send_response(200)
                    self.send_header('Content-Type', 'video/x-msvideo')
                    self.end_headers()
                    self.wfile.write(avi_header())
                    for _ in range(1800):
                        if peer.break_stream.is_set():
                            self.close_connection = True
                            return
                        if peer.capturing.is_set():
                            # Interrupt inside a chunk header, not only between frames: retrying
                            # nextFrame() after a timeout would lose these two consumed bytes.
                            self.wfile.write(frame[:2])
                            self.wfile.flush()
                            peer.capture_paused.set()
                            if not peer.resume_stream.wait(30):
                                return
                            self.wfile.write(frame[2:])
                            self.wfile.flush()
                            peer.frames += 1
                            continue
                        self.wfile.write(frame)
                        self.wfile.flush()
                        peer.frames += 1
                        time.sleep(.1)
                else:
                    self.reply({'error': 'fixture endpoint not provided'}, 404)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get('Content-Length', '0'))).decode('utf-8', errors='replace')
            action = self.headers.get('SOAPACTION', '').strip('"').split('#')[-1]
            peer.record(self, action, body)
            try:
                if urlsplit(self.path).path == '/rvf/control':
                    if action == 'GetInfomation':
                        request = ET.fromstring(body).find('.//{' + SERVICE + '}GetInfomation')
                        if request is None or list(request):
                            self.soap_fault('GetInfomation has no input arguments')
                        else:
                            self.reply('<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
                                '<s:Body>' + peer.information_body + '</s:Body></s:Envelope>', content_type='text/xml')
                    elif action == 'MULTIAF':
                        self.envelope(action, '<AFSTATUS>focused</AFSTATUS>')
                    elif action == 'Shot':
                        if peer.shot_fault:
                            self.soap_fault('controlled Shot rejection')
                        else:
                            peer.capture_paused.clear()
                            peer.resume_stream.clear()
                            peer.capturing.set()
                            if not peer.capture_paused.wait(5):
                                raise AssertionError('Shot did not reach the active video reader')
                            time.sleep(18)  # Exceeds the old 8s video and two service-probe limits.
                            peer.capturing.clear()
                            peer.resume_stream.set()
                            self.envelope(action, '<AFSHOTRESULT>/photos/test.jpg</AFSHOTRESULT>')
                    else:
                        self.soap_fault('Action not implemented by this fixture')
                else:
                    # OSD is deliberately unavailable: AVI must decode independently.
                    self.reply({'error': 'fixture has no native camera OSD'}, 503)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

    servers = [ThreadingHTTPServer(('127.0.0.1', port), Handler) for port in (18781, 18766, 18767)]
    for server in servers:
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
    return servers


class Android:
    def __init__(self, serial, output):
        self.serial = serial
        self.output = output

    def adb(self, *args, binary=False):
        result = subprocess.run([str(ADB), '-s', self.serial, *args], capture_output=True, timeout=45)
        if result.returncode:
            raise RuntimeError(f'adb {args}: exit {result.returncode}: {result.stderr.decode(errors="replace")} {result.stdout.decode(errors="replace")}')
        return result.stdout if binary else result.stdout.decode('utf-8', errors='replace')

    def ui(self):
        self.adb('shell', 'uiautomator', 'dump', '/sdcard/nx-rvf-ui.xml')
        return self.adb('shell', 'cat', '/sdcard/nx-rvf-ui.xml')

    def wait(self, text, timeout=25):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            xml = self.ui()
            if text in xml:
                return xml
            time.sleep(.3)
        (self.output / 'last-ui.xml').write_text(xml, encoding='utf-8')
        raise AssertionError(f'UI did not contain {text!r}; saved last-ui.xml')

    def tap(self, value):
        xml = self.ui()
        (self.output / 'last-ui.xml').write_text(xml, encoding='utf-8')
        for node in ET.fromstring(xml).iter('node'):
            if node.get('resource-id', '').endswith(':id/' + value) or node.get('text') == value or node.get('content-desc') == value:
                x1, y1, x2, y2 = map(int, re.findall(r'\d+', node.get('bounds')))
                if x2 > x1 and y2 > y1:
                    self.adb('shell', 'input', 'tap', str((x1+x2)//2), str((y1+y2)//2))
                    return
        raise AssertionError(f'Visible UI node missing: {value}')

    def reveal(self, value, direction='down'):
        for _ in range(8):
            root = ET.fromstring(self.ui())
            for node in root.iter('node'):
                if node.get('resource-id', '').endswith(':id/' + value) or node.get('text') == value:
                    x1, y1, x2, y2 = map(int, re.findall(r'\d+', node.get('bounds')))
                    if x2 > x1 and y2 > y1:
                        return
            scroll = next(n for n in root.iter('node') if n.get('resource-id', '').endswith(':id/controlScroll'))
            x1, y1, x2, y2 = map(int, re.findall(r'\d+', scroll.get('bounds')))
            top, bottom = int(y1 + (y2-y1)*.2), int(y1 + (y2-y1)*.8)
            start, end = (bottom, top) if direction == 'down' else (top, bottom)
            self.adb('shell', 'input', 'swipe', str((x1+x2)//2), str(start), str((x1+x2)//2), str(end), '350')
        raise AssertionError(f'Could not reveal UI node: {value}')

    def edit(self, value, text):
        self.tap(value)
        self.adb('shell', 'input', 'keyevent', 'KEYCODE_MOVE_END')
        self.adb('shell', 'input', 'keyevent', *(['KEYCODE_DEL'] * 35))
        self.adb('shell', 'input', 'text', text)

    def capture(self, name):
        (self.output / f'{name}.xml').write_text(self.ui(), encoding='utf-8')
        (self.output / f'{name}.png').write_bytes(self.adb('exec-out', 'screencap', '-p', binary=True))

    def wait_enabled(self, resource, enabled=True, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            xml = self.ui()
            for node in ET.fromstring(xml).iter('node'):
                if node.get('resource-id', '').endswith(':id/' + resource) and node.get('enabled') == str(enabled).lower():
                    return
            time.sleep(.2)
        (self.output / 'last-ui.xml').write_text(xml, encoding='utf-8')
        raise AssertionError(f'{resource} did not become enabled={enabled}')


def verify_shot(android, peer, checks):
    before_raw = peer.raw_input_count()
    stream_count = sum(peer.count(path) for path in STREAM_PATHS)
    pairing_count = peer.count('/smp_2_')
    frames_before_shot = peer.frames
    android.tap('btnShutter')
    assert peer.capture_paused.wait(5), 'Shot must pause inside the existing stream'
    android.wait('正在拍摄')
    android.wait_enabled('btnShutter', True, timeout=35)
    android.wait('已解码画面')
    assert peer.action_count('Shot') == 1
    assert peer.frames > frames_before_shot
    assert sum(peer.count(path) for path in STREAM_PATHS) == stream_count
    assert peer.count('/smp_2_') == pairing_count
    assert peer.raw_input_count() == before_raw
    assert peer.busy_info_requests >= 2, peer.busy_info_requests
    android.capture('05a-shot-resumed-on-same-stream')
    checks['shot_uses_soap_without_raw_shutter_keys'] = True
    checks['shot_18s_mid_chunk_pause_preserves_stream_and_control'] = True
    checks['busy_mod_http_during_shot_does_not_disconnect_camera'] = True
    peer.shot_fault = True
    android.tap('btnShutter')
    android.wait_enabled('btnShutter', True)
    assert peer.action_count('Shot') == 2
    assert peer.count('/smp_2_') == pairing_count
    assert sum(peer.count(path) for path in STREAM_PATHS) == stream_count
    checks['shot_fault_releases_busy_state_and_keeps_session'] = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial', default='emulator-5562')
    parser.add_argument('--capture-only', action='store_true', help='Run only the Shot/stream-loss regression.')
    parser.add_argument('--disconnect-only', action='store_true', help='Check stream-loss input routing without repeating Shot.')
    parser.add_argument('--output', type=Path, default=DEFAULT_OUT,
        help='Evidence directory; an existing report is never overwritten.')
    args = parser.parse_args()
    if args.serial != 'emulator-5562':
        parser.error('This checkout is authorized to test only emulator-5562')
    output = args.output.resolve()
    if (output / 'report.json').exists():
        parser.error(f'{output} already contains report.json; choose a new --output to preserve it')
    output.mkdir(parents=True, exist_ok=True)
    scpd, information_body, declared_actions = stock_fixture_xml()
    (output / 'fixture-scpd.xml').write_text(scpd, encoding='utf-8')
    (output / 'fixture-information-body.xml').write_text(information_body, encoding='utf-8')
    peer = Peer(scpd, information_body)
    servers = serve(peer)
    android = Android(args.serial, output)
    checks = {}
    report = {'passed': False, 'actual_release_apk': str(APK), 'apk_bytes': APK.stat().st_size,
        'android_serial': args.serial,
        'peer': 'synthetic gated RVF HTTP and MJPG AVI', 'stock_firmware_executed': False,
        'fixture_sources': {'scpd_elf': str(STOCK_RVF), 'scpd_file_offset': '0x5a304..0x5c75c',
            'information_body': str(STOCK_BODY), 'information_body_scope':
            'Stock pure formatter output with controlled IP/port, only StreamUrl; fixture host substituted; not a full device response'},
        'physical_camera_tested': False, 'checks': checks}
    report['capture_only'] = args.capture_only
    report['disconnect_only'] = args.disconnect_only
    try:
        package = android.adb('shell', 'dumpsys', 'package', 'com.nxwa.nxconnect')
        report['initial_package'] = package
        gradle = (PROJECT / 'mod/nxconnect/NXConnect/app/build.gradle.kts').read_text(encoding='utf-8')
        version = re.search(r'versionName\s*=\s*"([^"]+)"', gradle).group(1)
        assert re.search(r'\bversionName=' + re.escape(version) + r'(?:\s|$)', package), f'Install the {version} APK before running this probe'
        report['expected_version'] = version
        android.adb('reverse', 'tcp:8781', 'tcp:18781')
        android.adb('reverse', 'tcp:7676', 'tcp:18766')
        android.adb('reverse', 'tcp:7677', 'tcp:18767')
        android.adb('shell', 'am', 'force-stop', 'com.nxwa.nxconnect')
        android.adb('shell', 'am', 'start', '-W', '-n', 'com.nxwa.nxconnect/.ui.MainActivity')
        report['app_pid'] = android.adb('shell', 'pidof', 'com.nxwa.nxconnect').strip()
        startup_ui = android.ui()
        if '上次运行时崩溃了' in startup_ui:
            android.capture('00-previous-crash-dialog')
            report['previous_crash_dialog'] = startup_ui
            android.tap('知道了')
        android.tap('更多')
        android.wait('手动连接')
        android.tap('手动连接')
        for key, value in [('ipInput', '127.0.0.1'), ('portInput', '8781'), ('cgiPortInput', '8781')]:
            android.edit(key, value)
        android.adb('shell', 'input', 'keyevent', 'KEYCODE_BACK')
        android.tap('btnConnect')
        android.wait('已连接')
        peer.wait_count('/smp_2_', 1)
        if args.capture_only or args.disconnect_only:
            peer.gate.set()
            android.wait('原厂已连接')
            android.tap('btnRemote')
            android.wait('已解码画面')
            if not args.disconnect_only:
                verify_shot(android, peer, checks)
            peer.break_stream.set()
            android.wait('原厂取景未就绪', timeout=20)
            android.wait_enabled('btnShutter', False)
            before_raw = peer.raw_input_count()
            shot_count = peer.action_count('Shot')
            android.tap('btnShutter')
            android.tap('More options')
            android.tap('拍摄')
            android.tap('More options')
            android.tap('长曝光与屏幕诊断')
            android.reveal('btnBulb')
            android.wait_enabled('btnBulb', False)
            android.tap('btnBulb')
            android.capture('08-stream-ended-shutter-blocked')
            assert peer.raw_input_count() == before_raw
            assert peer.action_count('Shot') == shot_count
            checks['stream_end_blocks_button_bulb_and_toolbar_shutter'] = True
            time.sleep(3.5)
            assert peer.count('/smp_2_') == 1
            assert sum(peer.count(path) for path in STREAM_PATHS) == 1
            checks['stream_end_does_not_automatically_reconnect'] = True
            assert all(r['action'] in ('GetInfomation', 'Shot') for r in peer.records if r['action'])
            checks['only_stock_information_and_shot_actions_sent'] = True
            report['passed'] = all(checks.values())
            return
        assert peer.count('/rvf/actions.xml') == 0
        assert sum(peer.count(path) for path in STREAM_PATHS) == 0
        checks['home_connection_starts_one_pairing_without_video'] = True
        android.capture('01-home-awaiting-camera-confirmation')
        android.tap('btnRemote')
        android.wait('btnStockRvf')
        time.sleep(3.5)
        assert peer.count('/smp_2_') == 1
        android.capture('02-remote-reuses-pending-home-pairing')
        checks['remote_reuses_pending_home_pairing'] = True
        android.wait('原厂待允许')
        peer.wait_count('/smp_2_', 1)
        android.capture('03-await-camera-confirmation')
        assert peer.count('/rvf/actions.xml') == 0
        checks['manual_pairing_waits_for_camera_confirmation'] = True
        before_raw = peer.raw_input_count()
        android.tap('btnShutter')
        android.reveal('MENU / 返回')
        android.tap('MENU / 返回')
        time.sleep(.5)
        assert peer.raw_input_count() == before_raw
        checks['pending_pairing_blocks_shutter_and_menu_raw_input'] = True
        android.reveal('btnStockRvf', direction='up')
        android.adb('shell', 'input', 'keyevent', 'KEYCODE_BACK')
        android.wait('cameraSettingsSummary')
        assert peer.count('/smp_2_') == 1
        assert peer.count('/rvf/actions.xml') == 0
        checks['leaving_remote_without_video_preserves_pending_control'] = True
        peer.gate.set()
        peer.wait_count('/rvf/control', 1)
        android.wait('原厂已连接')
        assert sum(peer.count(path) for path in STREAM_PATHS) == 0
        status_nodes = {n.get('resource-id', '').split('/')[-1]: n
            for n in ET.fromstring(android.ui()).iter('node')}
        camera_bounds = list(map(int, re.findall(r'\d+', status_nodes['cameraStateText'].get('bounds'))))
        pairing_bounds = list(map(int, re.findall(r'\d+', status_nodes['autoStatus'].get('bounds'))))
        assert camera_bounds[3] <= pairing_bounds[1], (camera_bounds, pairing_bounds)
        checks['shooting_and_pairing_status_do_not_overlap'] = True
        android.capture('04-home-paired-without-video')
        checks['home_authorization_completes_information_without_video'] = True
        android.tap('btnCameraSettings')
        android.wait('原厂菜单')
        android.tap('原厂菜单')
        android.wait('btnStockRvf')
        time.sleep(1)
        assert peer.count('/smp_2_') == 1
        assert peer.count('/rvf/control') == 1
        assert sum(peer.count(path) for path in STREAM_PATHS) == 0
        checks['settings_entry_does_not_open_video_on_existing_control_session'] = True
        android.capture('04-settings-preserves-video-free-control')
        android.adb('shell', 'input', 'keyevent', 'KEYCODE_BACK')
        android.wait('原厂菜单')
        android.adb('shell', 'input', 'keyevent', 'KEYCODE_BACK')
        android.wait('cameraSettingsSummary')
        android.tap('btnRemote')
        android.wait('已解码画面')
        assert peer.count('/smp_2_') == 1
        assert peer.count('/rvf/control') == 1
        android.capture('05-remote-reuses-home-session')
        checks['remote_reuses_connected_home_session_and_decodes_mjpg'] = peer.frames > 0
        assert not any(r['action'] == 'Shot' for r in peer.records)
        checks['pairing_does_not_trigger_shutter'] = True
        time.sleep(3.5)
        assert not any(r['action'] == 'HeartBeat' for r in peer.records)
        checks['session_without_declared_heartbeat_sends_none'] = True
        verify_shot(android, peer, checks)
        android.tap('btnStockRvf')
        android.wait('原厂遥控已断开')
        peer.reject = True
        android.tap('btnStockRvf')
        android.wait('原厂遥控连接失败')
        android.capture('06-camera-rejects')
        count = peer.count('/smp_2_')
        time.sleep(3.5)
        assert peer.count('/smp_2_') == count == 2
        checks['camera_rejection_is_visible_without_retry_loop'] = True
        android.wait_enabled('btnShutter', False)
        before_raw = peer.raw_input_count()
        android.tap('btnShutter')
        android.tap('More options')
        android.tap('长曝光与屏幕诊断')
        android.reveal('btnBulb')
        android.wait_enabled('btnBulb', False)
        android.tap('btnBulb')
        assert peer.raw_input_count() == before_raw
        checks['failed_pairing_cannot_fall_back_to_raw_shutter_or_bulb'] = True
        android.reveal('btnStockRvf', direction='up')
        peer.reject = False
        peer.gate.clear()
        android.tap('btnStockRvf')
        android.wait('原厂待允许')
        peer.wait_count('/smp_2_', 3)
        android.tap('btnStockRvf')
        android.wait('原厂遥控已断开')
        peer.gate.set()  # Release the abandoned request after client cancellation.
        time.sleep(3.5)
        assert peer.count('/smp_2_') == 3
        assert peer.count('/rvf/actions.xml') == 1
        android.capture('07-cancelled-late-acceptance')
        checks['cancel_does_not_retry_or_accept_late_authorization'] = True
        android.tap('btnStockRvf')
        android.wait('已解码画面')
        assert peer.count('/smp_2_') == 4
        checks['explicit_retry_after_failure_and_cancel_decodes_mjpg'] = True
        peer.break_stream.set()
        android.wait('原厂取景未就绪', timeout=20)
        android.capture('08-stream-ended')
        count = peer.count('/smp_2_')
        stream_count = sum(peer.count(path) for path in STREAM_PATHS)
        time.sleep(3.5)
        assert peer.count('/smp_2_') == count
        assert sum(peer.count(path) for path in STREAM_PATHS) == stream_count
        checks['stream_end_is_visible_without_automatic_repair_or_repairing'] = True
        android.wait_enabled('btnShutter', False)
        before_raw = peer.raw_input_count()
        android.tap('btnShutter')
        assert peer.raw_input_count() == before_raw
        checks['ended_stream_cannot_fall_back_to_raw_shutter'] = True
        android.adb('shell', 'input', 'keyevent', 'KEYCODE_BACK')
        android.wait('cameraSettingsSummary')
        android.tap('btnCameraSettings')
        android.wait('原厂菜单')
        android.tap('原厂菜单')
        android.wait('btnStockRvf')
        time.sleep(3.5)
        assert peer.count('/smp_2_') == count
        android.capture('09-settings-no-pairing')
        checks['stock_settings_entry_does_not_auto_pair'] = True
        requests = [r for r in peer.records if urlsplit(r['path']).path in ('/smp_2_', '/rvf/actions.xml', '/rvf/control')]
        assert requests and all((r['user_agent'] or '').startswith('SEC_RVF_') and r['access_method'] == 'manual'
            for r in requests)
        checks['rvf_headers_match_manual_pairing'] = True
        soap_requests = [r for r in peer.records if urlsplit(r['path']).path == '/rvf/control']
        assert soap_requests and all(r['action'] in ('GetInfomation', 'Shot') and r['action'] in declared_actions for r in soap_requests)
        assert all(not list(ET.fromstring(r['body']).find('.//{' + SERVICE + '}' + r['action'])) for r in soap_requests)
        checks['only_declared_soap_without_invented_heartbeat_or_exit'] = True
        checks['get_information_has_no_input_arguments'] = True
        report['passed'] = all(checks.values())
    except BaseException as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        peer.gate.set()
        peer.capturing.clear()
        peer.resume_stream.set()
        report['fixture_frames_sent'] = peer.frames
        report['requests'] = peer.records
        cleanup_errors = []
        for name, call in [('package', lambda: android.adb('shell', 'dumpsys', 'package', 'com.nxwa.nxconnect')),
                ('logcat', lambda: android.adb('logcat', '-d', '-s', 'NxApp', 'AndroidRuntime'))]:
            try:
                value = call()
                if name == 'package':
                    report['package'] = value
                else:
                    (output / 'logcat.txt').write_text(value, encoding='utf-8')
                    pid = report.get('app_pid')
                    if pid:
                        current = [line for line in value.splitlines()
                            if re.match(r'\S+\s+\S+\s+' + re.escape(pid) + r'\s+', line)]
                        (output / 'logcat-current-app.txt').write_text('\n'.join(current) + '\n', encoding='utf-8')
            except Exception as exc:
                cleanup_errors.append(f'{name}: {exc}')
        for port in ('8781', '7676', '7677'):
            try:
                android.adb('reverse', '--remove', 'tcp:' + port)
            except Exception as exc:
                cleanup_errors.append(f'reverse {port}: {exc}')
        for server in servers:
            server.shutdown()
            server.server_close()
        report['cleanup_errors'] = cleanup_errors
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({k: v for k, v in report.items() if k not in (
            'requests', 'package', 'initial_package', 'previous_crash_dialog')}, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
