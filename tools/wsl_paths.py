"""Host paths used by the project's WSL commands; returns unquoted paths."""
from functools import lru_cache
import ntpath
import os
from pathlib import Path
import re
import subprocess


@lru_cache(maxsize=64)
def _network_path(value: str, distro: str | None) -> str:
    command = ['wsl.exe'] if os.name == 'nt' else []
    if command:
        if distro: command.extend(['--distribution', distro])
        command.append('--exec')
    command.extend(['wslpath', '-a', '-u', value])
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                                errors='replace', timeout=5)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f'WSL cannot convert the network path: {value!r}: {exc}') from exc
    converted = result.stdout.strip()
    if result.returncode or not converted.startswith('/'):
        raise ValueError(f'wslpath exit {result.returncode}: {result.stderr.strip() or "no absolute Linux path"}')
    return converted


def wsl_path(path: str | os.PathLike[str], *, distro: str | None = None) -> str:
    """Map drive/WSL UNC/Linux paths without starting WSL for common inputs.

    Relative inputs resolve against the host working directory. Generic network
    UNC paths use wslpath; this converts a name and does not mount a share.
    Callers still quote the returned string when constructing shell commands.
    """
    raw = os.fspath(path)
    if not isinstance(raw, str) or not raw or '\0' in raw:
        raise ValueError('A nonempty text path is required')
    # Backslashes can be real filename characters on Linux.
    if raw.startswith('/') and not raw.startswith('//'):
        return raw
    value = raw.replace('\\', '/')
    if value.startswith('//?/'):
        value = value[4:]
        if value[:4].lower() == 'unc/': value = '//' + value[4:]
    if value.startswith('//'):
        value = ntpath.normpath(value).replace('\\', '/')
        parts = value[2:].split('/', 2)
        if len(parts) < 2 or not parts[1]:
            raise ValueError(f'UNC path is missing its share: {raw!r}')
        if parts[0].lower() in ('wsl.localhost', 'wsl$'):
            if distro and parts[1].casefold() != distro.casefold():
                raise ValueError(f'WSL path belongs to {parts[1]}, requested {distro}')
            return '/' + (parts[2] if len(parts) == 3 else '')
        return _network_path(ntpath.normpath(value), distro)
    if re.match(r'^[A-Za-z]:/', value):
        value = ntpath.normpath(value).replace('\\', '/')
        return '/mnt/' + value[0].lower() + value[2:]
    if re.match(r'^[A-Za-z]:', value):
        raise ValueError(f'Drive-relative path needs an absolute location: {raw!r}')
    if value.startswith('/'):
        return value
    return wsl_path(Path(raw).resolve(), distro=distro)
