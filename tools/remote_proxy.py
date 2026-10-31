"""Persistent WSL loopback gateway into the private camera network namespace.

The listener stays in WSL's initial network namespace. Only each request thread
joins the target namespace, so Windows localhost forwarding remains available.
"""
import argparse
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_): pass

    def forward(self):
        if self.path == '/__nx_proxy_status':
            data = json.dumps({'pid': os.getpid(), 'namespace_pid': self.server.namespace_pid}).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data); return
        connection = None
        started = False
        try:
            origin = self.headers.get('Origin')
            if self.command in ('POST', 'PUT') and origin and origin != f'http://{self.headers.get("Host")}':
                raise ValueError('Origin mismatch')
            size = int(self.headers.get('Content-Length', 0))
            upload = self.path.split('?', 1)[0] == '/api/v1/gallery/upload'
            limit = 120 * 1024 * 1024 if upload else 1048576
            if size < 0 or size > limit or (upload and not size): raise ValueError('Empty or oversized request')
            if self.headers.get('Transfer-Encoding'): raise ValueError('Content-Length required')
            body = self.rfile.read(size)
            if len(body) != size: raise ValueError('Incomplete request body')
            os.setns(self.server.netns_fd, os.CLONE_NEWNET)
            headers = {key: self.headers[key] for key in ('Content-Type', 'Range', 'If-Modified-Since') if key in self.headers}
            # Original backup plus SD fsync can outlast ordinary control calls.
            editing = self.path.split('?', 1)[0] in ('/api/v1/gallery/upload', '/api/v1/gallery/restore')
            connection = http.client.HTTPConnection('127.0.0.1', 8781, timeout=90 if editing else 15)
            connection.request(self.command, self.path, body=body, headers=headers)
            response = connection.getresponse()
            self.send_response(response.status)
            for key, value in response.getheaders():
                if key.lower() not in ('transfer-encoding', 'connection', 'server', 'date', 'keep-alive'):
                    self.send_header(key, value)
            self.send_header('Connection', 'close'); self.end_headers(); started = True; self.close_connection = True
            while chunk := response.read(65536): self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError): pass
        except ValueError as exc:
            if not started:
                data = json.dumps({'error': str(exc)}).encode()
                self.send_response(400); self.send_header('Content-Length', str(len(data)))
                self.send_header('Connection', 'close'); self.end_headers(); self.wfile.write(data)
                self.close_connection = True
        except Exception as exc:
            if not started:
                data = json.dumps({'error': str(exc)}).encode()
                self.send_response(503); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
        finally:
            if connection: connection.close()

    do_GET = forward
    do_POST = forward
    do_PUT = forward
    do_HEAD = forward


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--namespace', type=int, required=True)
    parser.add_argument('--state', type=Path, required=True)
    args = parser.parse_args()
    if args.namespace < 1: raise ValueError('Invalid namespace')
    server = ThreadingHTTPServer(('127.0.0.1', 8782), Handler)
    server.daemon_threads = True; server.timeout = .5
    server.netns_fd = os.open(f'/proc/{args.namespace}/ns/net', os.O_RDONLY)
    server.namespace_pid = args.namespace
    args.state.write_text(json.dumps({'pid': os.getpid(), 'namespace_pid': args.namespace}), encoding='utf-8')
    stopped = False
    def stop(*_):
        nonlocal stopped
        stopped = True
    signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
    try:
        while not stopped: server.handle_request()
    finally:
        server.server_close(); os.close(server.netns_fd)


if __name__ == '__main__': main()
