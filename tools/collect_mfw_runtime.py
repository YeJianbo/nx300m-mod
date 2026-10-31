"""Collect current MFW service status and RAM logs through GET requests only."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit

PROJECT = Path(__file__).resolve().parents[1]
LOGS = ('bootstrap.log', 'mfw-entry.log', 'remote.log', 'supervisor.log',
        'service.state', 'remote.pid', 'latest-diagnostic')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True, help='Camera service origin, e.g. http://192.168.0.1:8781')
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    base = args.base.rstrip('/')
    parts = urlsplit(base)
    if parts.scheme != 'http' or not parts.hostname or parts.username or parts.password or parts.path or parts.query or parts.fragment:
        parser.error('--base must be an HTTP origin without credentials, path or query')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    output = args.output_dir or PROJECT / 'outputs/deployment/mfw-services/camera/runtime' / stamp
    output.mkdir(parents=True, exist_ok=False)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    report = {'base': base, 'started_utc': datetime.now(timezone.utc).isoformat(),
              'method': 'GET', 'camera_writes': False, 'requests': []}

    def get(path, name):
        item = {'path': path, 'file': name}
        report['requests'].append(item)
        try:
            with opener.open(base + path, timeout=4) as response:
                data = response.read(1048577)
                if len(data) > 1048576:
                    raise ValueError('Response exceeds 1 MiB')
                item.update(status=response.status, bytes=len(data),
                            log_offset=response.headers.get('X-Log-Offset'))
                (output / name).write_bytes(data)
                return data
        except (OSError, ValueError) as exc:
            item['error'] = str(exc)
            if isinstance(exc, urllib.error.HTTPError):
                item['status'] = exc.code
            return None

    try:
        raw = get('/api/v1/camera/info', 'camera-info.json')
        if raw is None:
            report['status'] = 'unreachable'
            return 1
        try:
            info = json.loads(raw)
        except (ValueError, UnicodeError):
            report['status'] = 'invalid-camera-info'
            return 1
        if not isinstance(info, dict) or not info.get('model'):
            report['status'] = 'invalid-camera-info'
            return 1
        report.update(service_revision=info.get('service_revision'), model=info.get('model'),
                      instance_id=info.get('instance_id'), custom_key=info.get('custom_key'))
        report['media_paused'] = info.get('media_paused')
        get('/api/v1/media/status', 'media-status.json')
        get('/api/v1/storage/status', 'storage-status.json')
        get('/api/v1/diagnostics', 'diagnostics.json')
        for name in LOGS:
            get('/api/v1/diagnostics?file=' + name, name)
        raw_after = get('/api/v1/camera/info', 'camera-info-after.json')
        try:
            after = json.loads(raw_after) if raw_after else {}
            report['same_instance_after_collection'] = bool(info.get('instance_id')) and after.get('instance_id') == info['instance_id']
            report['custom_key_after'] = after.get('custom_key')
        except (ValueError, UnicodeError, AttributeError):
            report['same_instance_after_collection'] = False
        # Missing individual logs are recorded; they never imply a healthy camera.
        report['status'] = 'collected' if all('error' not in item for item in report['requests']) else 'partial'
        return 0 if report['status'] == 'collected' else 2
    finally:
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print(f'Report: {output / "report.json"}')


if __name__ == '__main__':
    sys.exit(main())
