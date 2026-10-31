"""Isolated regressions for the simulator upload proxy; no WSL or media writes."""
import io
import threading
import unittest
from unittest.mock import Mock, patch

import simulator


class UploadProxyTests(unittest.TestCase):
    def handler(self, origin=None, content=b'jpeg'):
        handler = simulator.Handler.__new__(simulator.Handler)
        handler.path = '/api/v1/gallery/upload?p=100PHOTO/%24%28printf%20PROBE%29.JPG'
        handler.command = 'POST'
        handler.headers = {'Content-Length': str(len(content)), 'Host': '127.0.0.1:8765'}
        if origin: handler.headers['Origin'] = origin
        handler.rfile = io.BytesIO(content)
        handler.wfile = io.BytesIO()
        handler.reply = Mock()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        return handler

    def session(self, running=True):
        state = {'running': running, 'namespace_pid': 123 if running else None}
        return Mock(lock=threading.Lock(), status=Mock(return_value=state),
                    require_backend=Mock(return_value=state))

    def test_cross_origin_rejected_before_backend(self):
        handler = self.handler('http://unrelated.invalid')
        with patch.object(simulator, 'SESSION', self.session()) as session:
            handler.do_POST()
            session.status.assert_not_called()
        self.assertEqual(handler.reply.call_args.args[1], 403)

    def test_missing_session_is_failure(self):
        handler = self.handler()
        with patch.object(simulator, 'SESSION', self.session(False)), patch.object(simulator, 'start_remote') as start:
            handler.do_POST()
            start.assert_not_called()
        self.assertEqual(handler.reply.call_args.args[1], 503)

    def test_filename_is_http_data(self):
        handler = self.handler()
        response = Mock(status=200, getheaders=Mock(return_value=[]))
        response.read.side_effect = [b'{"ok":true}', b'']
        connection = Mock(getresponse=Mock(return_value=response))
        with patch.object(simulator, 'SESSION', self.session()), patch.object(simulator, 'start_remote'), \
             patch.object(simulator.http.client, 'HTTPConnection', return_value=connection), \
             patch.object(simulator.subprocess, 'Popen') as shell:
            handler.do_POST()
            shell.assert_not_called()
        self.assertEqual(connection.request.call_args.args, ('POST', handler.path))
        self.assertEqual(connection.request.call_args.kwargs['body'], b'jpeg')
        handler.send_response.assert_called_once_with(200)

    def test_short_body_rejected(self):
        handler = self.handler()
        handler.headers['Content-Length'] = '5'
        with patch.object(simulator, 'SESSION', self.session()), patch.object(simulator, 'start_remote'):
            handler.do_POST()
        self.assertEqual(handler.reply.call_args.args[1], 400)


if __name__ == '__main__': unittest.main()
