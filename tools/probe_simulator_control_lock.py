"""Bounded slow-backend and session-switch checks; no live simulator is used."""
import io
import json
from pathlib import Path
from types import SimpleNamespace
import threading
from unittest.mock import patch

from PIL import Image
import simulator
import remote_proxy
import remote_stock

OUT = Path(__file__).resolve().parents[1] / 'outputs/review/runtime-locks'


def slow_control(change=None):
    session = simulator.Session()
    session.software_playback = True
    state = {'running': True, 'namespace_pid': 123, 'backend_available': True}
    session.status = lambda: dict(state)
    entered, release = threading.Event(), threading.Event()
    replies, failures, frame_replies, requests = [], [], [], []

    class Connection:
        def __init__(self, *_args, **_kwargs):
            self.closed = False

        def request(self, *args, **kwargs):
            requests.append((args, kwargs))

        def getresponse(self):
            entered.set()
            if not release.wait(3):
                raise TimeoutError('probe backend gate was not released')
            return SimpleNamespace(status=200, read=lambda: b'{"native_service":true}')

        def close(self):
            self.closed = True

    handler = object.__new__(simulator.Handler)
    handler.reply = lambda *args, **kwargs: replies.append((args, kwargs))

    def control():
        try:
            handler.native_control('color/picture-wizard', {'red': 120})
        except Exception as exc:
            failures.append(str(exc))

    frame = object.__new__(simulator.Handler)
    frame.path = '/frame.png'
    frame.reply = lambda *args, **kwargs: frame_replies.append((args, kwargs))
    with (patch.object(simulator, 'SESSION', session),
          patch.object(simulator, 'start_remote', return_value=123),
          patch.object(simulator, 'read_osd_frame', return_value=Image.new('RGB', (2, 2), 'red')),
          patch.object(simulator.http.client, 'HTTPConnection', Connection)):
        worker = threading.Thread(target=control)
        worker.start()
        try:
            assert entered.wait(2), 'control never reached backend'
            reader = threading.Thread(target=frame.do_GET)
            reader.start()
            reader.join(1)
            assert not reader.is_alive() and frame_replies, 'frame blocked behind slow control'
            if change:
                with session.lock:
                    if change == 'namespace':
                        state['namespace_pid'] = 456
                    else:
                        session.generation += 1
        finally:
            release.set()
            worker.join(2)
        assert not worker.is_alive(), 'control thread did not finish'
    assert requests[0][1]['headers']['X-NX-Expected-Namespace'] == '123'
    if change:
        assert not replies and failures == ['相机会话在操作期间已变更'], (replies, failures)
    else:
        assert not failures and replies[0][0][0]['namespace_pid'] == 123


def proxy_rejects_replacement():
    handler = object.__new__(remote_proxy.Handler)
    handler.path = '/api/v1/color/picture-wizard'
    handler.headers = {'X-NX-Expected-Namespace': '123'}
    handler.server = SimpleNamespace(namespace_pid=456)
    handler.wfile = io.BytesIO()
    statuses = []
    handler.send_response = statuses.append
    handler.send_header = lambda *_: None
    handler.end_headers = lambda: None
    with patch.object(remote_proxy.http.client, 'HTTPConnection') as connection:
        handler.forward()
        assert not connection.called, 'stale request reached replacement backend'
    assert statuses == [409] and handler.close_connection


def proxy_reports_guard():
    handler = object.__new__(remote_proxy.Handler)
    handler.path = '/__nx_proxy_status'
    handler.server = SimpleNamespace(namespace_pid=123)
    handler.wfile = io.BytesIO()
    handler.send_response = lambda *_: None
    handler.send_header = lambda *_: None
    handler.end_headers = lambda: None
    handler.forward()
    status = json.loads(handler.wfile.getvalue())
    assert status['namespace_pid'] == 123 and status['expected_namespace'] is True


def proxy_upgrades_legacy():
    legacy = {'pid': 7, 'namespace_pid': 123}
    guarded = {**legacy, 'expected_namespace': True}
    process = SimpleNamespace(poll=lambda: None)
    with (patch.object(remote_stock, 'proxy_health', side_effect=[legacy, OSError(), guarded]),
          patch.object(remote_stock, 'stop_proxy') as stop,
          patch.object(remote_stock.subprocess, 'Popen', return_value=process) as spawn):
        remote_stock.start_proxy(123)
        stop.assert_called_once_with(123)
        spawn.assert_called_once()
    with (patch.object(remote_stock, 'proxy_health', return_value=guarded),
          patch.object(remote_stock, 'stop_proxy') as stop,
          patch.object(remote_stock.subprocess, 'Popen') as spawn):
        remote_stock.start_proxy(123)
        assert not stop.called and not spawn.called, 'guarded proxy was unnecessarily restarted'


def input_transport():
    handler = object.__new__(simulator.Handler)
    replies = []
    handler.reply = lambda *args: replies.append(args)
    with (patch.object(handler, 'native_request', return_value=({'ok': True}, 200)) as request,
          patch.object(simulator.subprocess, 'run') as process):
        handler.native_input({'action': 'key', 'key': 'Menu'})
        assert request.call_args.args == ('input/inject?key=Menu&bind=1', {})
        handler.native_input({'action': 'click', 'x': 123, 'y': 74})
        assert request.call_args.args == ('input/inject?click=123-74', {})
        assert not process.called, 'ordinary input launched a process'
        assert all(reply[0]['input']['transport'] == 'native-x11-worker' for reply in replies)
        request.reset_mock()
        request.return_value = ({'error': 'X11 unavailable'}, 503)
        handler.native_input({'action': 'key', 'key': 'Menu'})
        assert request.call_count == 1 and replies[-1] == ({'error': 'X11 unavailable'}, 503)
        assert not process.called, 'failed input was replayed through the legacy transport'


def shutter_transport():
    session = simulator.Session()
    session.status = lambda: {'running': True, 'namespace_pid': 123, 'backend_available': True}
    handler = object.__new__(simulator.Handler)
    replies = []
    handler.reply = lambda *args: replies.append(args)

    def run(command, **kwargs):
        assert command[0] == 'wsl.exe' and '123' in command and command[-2:] == ['shutter', 'full']
        assert session.lock.acquire(blocking=False), 'shutter held the viewer lock while waiting'
        session.lock.release()
        return SimpleNamespace(returncode=0, stderr='', stdout='{"action":"shutter","phase":"full"}')

    with patch.object(simulator, 'SESSION', session), patch.object(simulator.subprocess, 'run', side_effect=run):
        handler.native_input({'action': 'shutter', 'phase': 'full'})
    assert replies[0][0]['input']['phase'] == 'full'


def main():
    cases = []
    for name, run in (
        ('slow_control_allows_frame', lambda: slow_control()),
        ('changed_namespace_discards_response', lambda: slow_control('namespace')),
        ('same_namespace_new_generation_discards_response', lambda: slow_control('generation')),
        ('replacement_proxy_rejects_before_forward', proxy_rejects_replacement),
        ('proxy_reports_namespace_guard', proxy_reports_guard),
        ('legacy_proxy_upgraded_guarded_proxy_reused', proxy_upgrades_legacy),
        ('ordinary_input_uses_worker_without_process_or_retry', input_transport),
        ('shutter_skips_windows_python_without_holding_session_lock', shutter_transport),
    ):
        try:
            run()
            cases.append({'case': name, 'passed': True})
        except Exception as exc:
            cases.append({'case': name, 'passed': False, 'error': str(exc)})
    report = {'passed': all(case['passed'] for case in cases), 'cases': cases,
              'scope': 'Production viewer handlers with a gated HTTP fixture; no camera, WSL or live server operation.'}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'simulator-lock.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
