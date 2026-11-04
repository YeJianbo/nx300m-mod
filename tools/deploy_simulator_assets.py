"""Publish changed simulator LUT assets under the native engine's existing lock."""
import base64
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import tempfile
import time


def relative(name):
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or not path.parts:
        raise ValueError('Invalid simulator asset path')
    return path


def deploy(root, payload):
    import fcntl
    root = Path(root)
    lock_path = root/'var/run/nx300m-lut-engine.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDONLY|os.O_CREAT, 0o600)
    copied = unchanged = removed = 0
    try:
        # Same owner and flock used by the ARM LUT engine.
        os.fchown(fd, 65534, 65534)
        deadline = time.monotonic()+10
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('Simulator LUT engine is busy')
                time.sleep(.025)
        for name in payload.get('remove', []):
            target = root/'mod'/relative(name)
            if target.exists():
                target.unlink(); removed += 1
        for name, encoded in payload['files'].items():
            target = root/'mod'/relative(name)
            data = base64.b64decode(encoded, validate=True)
            mode = 0o755 if name.endswith('.arm') else 0o644
            if target.is_file() and target.read_bytes() == data:
                if stat.S_IMODE(target.stat().st_mode) != mode:
                    target.chmod(mode)
                unchanged += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            staged_fd, staged = tempfile.mkstemp(prefix=target.name+'.', dir=target.parent)
            try:
                with os.fdopen(staged_fd, 'wb') as stream:
                    stream.write(data)
                    os.fchmod(stream.fileno(), mode)
                os.replace(staged, target)
            finally:
                Path(staged).unlink(missing_ok=True)
            copied += 1
    finally:
        os.close(fd)
    return dict(copied=copied, unchanged=unchanged, removed=removed)


if __name__ == '__main__':
    print(json.dumps(deploy(Path(sys.argv[1]), json.load(sys.stdin))))
