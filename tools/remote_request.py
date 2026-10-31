"""WSL-side loopback HTTP transport for the private ARM remote daemon."""
import http.client
import json
import struct
import sys


def main():
    method, path, headers = sys.argv[1:4]
    headers = json.loads(headers)
    if method not in ('GET', 'POST', 'HEAD') or not path.startswith('/'):
        raise ValueError('Invalid local remote request')
    body = sys.stdin.buffer.read(1048577)
    if len(body) > 1048576: raise ValueError('Request body too large')
    connection = http.client.HTTPConnection('127.0.0.1', 8781, timeout=15)
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        metadata = json.dumps({'status': response.status, 'headers': response.getheaders()}).encode('utf-8')
        sys.stdout.buffer.write(struct.pack('<I', len(metadata))+metadata)
        sys.stdout.buffer.flush()
        while chunk := response.read(65536):
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()
    finally: connection.close()


if __name__ == '__main__': main()
