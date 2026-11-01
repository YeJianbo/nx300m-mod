"""Verified camera-exported MFW script library for private simulator overlays."""
import hashlib
import json
from pathlib import Path
import shlex

PROJECT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT / ('outputs/deployment/mfw-exposure-test/camera/'
                    'diagnostics-7e8d8cd5-d0c5-4105-a840-b9ec813dfb9a-2305/'
                    'camera-loading/libmisc.so')
LIBRARY = PROJECT / 'outputs/mfw-runtime/libmisc.so'
MFW_SHA256 = '188d1c695b06703d2cd52be3d81425dd8978a6934fc2ad8bb94d1d212cee9b59'
STOCK_SHA256 = 'fe98d99729da83ae425cfb07dcbfc8ae27b0c99186a58a2d6c9c0503932d2f14'


def firmware_profile(name, *, prepare=False):
    profile = {
        'name': name, 'base_version': '1.15',
        'scope': 'camera user space; synthetic hardware; no camera kernel or hibernation',
        'libmisc_sha256': MFW_SHA256 if name == 'mfw' else STOCK_SHA256,
    }
    if name not in ('stock', 'mfw'):
        raise ValueError('Unknown firmware profile: ' + name)
    if name == 'mfw':
        profile['source'] = 'camera-exported MFW script-entry libmisc.so (2026-10-24)'
        profile['autoexec'] = '/mnt/mmc/autoexec.sh'
        profile['user_qemu_exec'] = 'ARM child exec requires full-system VM; no global binfmt installed'
        if prepare:
            data = (LIBRARY if LIBRARY.is_file() else SOURCE).read_bytes()
            if hashlib.sha256(data).hexdigest() != MFW_SHA256:
                raise ValueError('MFW libmisc.so differs from the verified camera export')
            LIBRARY.parent.mkdir(parents=True, exist_ok=True)
            if not LIBRARY.is_file():
                LIBRARY.write_bytes(data)
            (LIBRARY.parent / 'profile.json').write_text(
                json.dumps(profile, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    else:
        profile['source'] = 'stock NX300M 1.15 rootfs'
    return profile


def overlay_setup(guest, linux_source):
    """Bind in the private mount namespace; keep the extracted stock root intact."""
    target = shlex.quote(guest + '/usr/lib/libmisc.so')
    staged = shlex.quote(guest + '/tmp/mfw-libmisc.so')
    return (
        'mount --make-rprivate / || exit 8;\n'
        f'[ "$(sha256sum {target} | cut -d " " -f 1)" = {STOCK_SHA256} ] || exit 8;\n'
        f'cp {shlex.quote(linux_source)} {staged} && chmod 644 {staged} || exit 8;\n'
        f'[ "$(sha256sum {staged} | cut -d " " -f 1)" = {MFW_SHA256} ] || exit 8;\n'
        f'mount --bind {staged} {target} && mount -o remount,bind,ro {target} || exit 8;\n'
        'echo MFW_PRIVATE_LIBMISC=1 >&2;\n'
    )
