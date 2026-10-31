"""Derive review-only app launch scripts; never install or run stock boot scripts."""
import difflib
import json
from pathlib import Path
import re
import struct
import subprocess
import tarfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/deployment/app-loader'
FRAGMENT = ROOT / 'mod/deployment/app-loader/preload-launch.sh'
HOOKS = {
    'lut_port.so': 'outputs/firmware-runtime/lut_port.so',
    'ui_mod.so': 'outputs/firmware-runtime/ui_mod.so',
    'exposure_controls.so': 'outputs/controls/exposure_controls.so',
    'shutter_limit.so': 'outputs/controls/shutter_limit.so',
    'color-style.so': 'outputs/metol/color-style.so',
    'lcd-capture.so': 'outputs/firmware-runtime/lcd-capture.so',
}
PERSISTENT_HOOKS = {
    **HOOKS,
    'service-loader.so': 'outputs/deployment/service-loader/service-loader.so',
    'gallery-share.so': 'outputs/quickshare/gallery/gallery-share.so',
    'camera_firmware_mod.so': 'outputs/controls/camera_firmware_mod.so',
}
LAUNCH_PROFILES = {
    'stock': [],
    'services': ['service-loader.so'],
    'controls': ['exposure_controls.so', 'shutter_limit.so', 'lcd-capture.so',
                 'service-loader.so', 'camera_firmware_mod.so'],
    'color': ['lut_port.so', 'ui_mod.so', 'color-style.so', 'service-loader.so'],
    'full': list(PERSISTENT_HOOKS),
}
PERSISTENT_TEMPLATES = ROOT / 'mod/deployment/app-loader'
CAMERA_DESKTOP = 'opt/share/applications/com.samsung.di-camera-app-nx300.desktop'
EVIDENCE = (
    'etc/init.d/rcS', 'etc/~inittab', 'etc/fstab',
    'etc/rc.d/rc.entersleep', 'etc/rc.d/rc.leavesleep',
    'usr/bin/go.sh', 'usr/bin/hib2.sh', 'usr/sbin/pivot_rootfs_ubi.sh',
)


def write(relative, data):
    path = OUT / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def read_cpio(data):
    """Read the embedded newc archive without extracting paths to the filesystem."""
    position = 0
    files = {}
    while data[position:position + 6] in (b'070701', b'070702'):
        header = data[position:position + 110]
        fields = [int(header[i:i + 8], 16) for i in range(6, 110, 8)]
        size, namesize = fields[6], fields[11]
        name = data[position + 110:position + 110 + namesize - 1].decode('ascii')
        start = (position + 110 + namesize + 3) & ~3
        body = data[start:start + size]
        if len(body) != size:
            raise ValueError('Truncated embedded newc archive')
        position = (start + size + 3) & ~3
        if name == 'TRAILER!!!':
            return files
        files[name] = (fields[1], body)
    raise ValueError('Embedded archive is not complete newc')


def kernel_evidence():
    results = []
    with (ROOT / 'work/stock-1.15/nx300m.bin').open('rb') as stream:
        stream.seek(64)
        tables = [struct.unpack('<4I', stream.read(16)) for _ in range(4)]
        for name, index in (('vImage', 0), ('uImage', 3)):
            size, _, offset, _ = tables[index]
            stream.seek(offset)
            image = stream.read(size)
            if image[:4] != bytes.fromhex('27051956'):
                raise ValueError(f'{name} lacks the original uImage magic')
            outer = image.find(b'\x1f\x8b\x08')
            if outer < 0:
                raise ValueError(f'{name}: compressed kernel not found')
            kernel = zlib.decompress(image[outer:], 31)
            entry = {'image': name, 'firmware_offset': offset, 'image_bytes': size,
                     'kernel_gzip_image_offset': outer, 'kernel_bytes': len(kernel),
                     'embedded_newc': []}
            for match in re.finditer(b'\x1f\x8b\x08', kernel):
                try:
                    unpacked = zlib.decompress(kernel[match.start():], 31)
                except zlib.error:
                    continue
                if not unpacked.startswith(b'070701'):
                    continue
                files = read_cpio(unpacked)
                entry['embedded_newc'].append({'gzip_kernel_offset': match.start(),
                                              'bytes': len(unpacked), 'members': len(files)})
                for member in ('init', 'usr/bin/bind_dir.sh'):
                    if member in files:
                        write(f'evidence/{name}-initramfs/{member}', files[member][1])
            entry['relevant_strings'] = [
                {'kernel_offset': m.start(), 'text': m.group().decode('ascii')}
                for m in re.finditer(rb'[ -~]{6,}', kernel)
                if any(s in m.group() for s in (b'console=ttyAMA0', b'/sbin/init',
                                                b'Failed to load hibernation image'))
            ]
            results.append(entry)
    return results


def verify_native(path):
    data = path.read_bytes()
    if len(data) < 52 or data[:6] != b'\x7fELF\x01\x01':
        raise ValueError(f'Expected ELF32 little-endian: {path}')
    if struct.unpack_from('<HH', data, 16) != (3, 40):
        raise ValueError(f'Expected ARM ET_DYN hook: {path}')
    return data


def persistent_launch_files():
    """Derive only the two established entry points and their common helper."""
    helper_source = PERSISTENT_TEMPLATES / 'preload-persistent.sh'
    wrapper_source = PERSISTENT_TEMPLATES / 'nx300m-camera-launch'
    call_source = PERSISTENT_TEMPLATES / 'hib2-app-call.sh'
    helper, wrapper, app_call = (p.read_bytes() for p in (helper_source, wrapper_source, call_source))
    if any(b'\r' in data for data in (helper, wrapper, app_call)):
        raise ValueError('Persistent templates must use LF line endings')
    with tarfile.open(ROOT / 'work/stock-1.15/rootfs.tar') as archive:
        files = []
        for name, old, new, template in (
            ('usr/bin/hib2.sh', b'\n$app &\n', b'\n' + app_call, call_source),
            (CAMERA_DESKTOP, b'Exec=/usr/bin/di-camera-app\n',
             b'Exec=/usr/bin/nx300m-camera-launch\n', wrapper_source),
        ):
            member = archive.getmember(name)
            original = archive.extractfile(member).read()
            if original.count(old) != 1:
                raise ValueError('Expected one original launch command: ' + name)
            candidate = original.replace(old, new, 1)
            if candidate.replace(new, old, 1) != original:
                raise ValueError('Unintended launch-file change: ' + name)
            files.append({'destination': '/' + name, 'content': candidate,
                          'original': original, 'change': 'modified',
                          'source': 'work/stock-1.15/rootfs.tar:' + name,
                          'template': template.relative_to(ROOT).as_posix(),
                          'generator': 'tools/build_stock_app_loader.py:persistent_launch_files',
                          'metadata': {'mode': oct(member.mode), 'uid': member.uid, 'gid': member.gid,
                                       'mtime': member.mtime, 'uname': member.uname, 'gname': member.gname},
                          'other_original_bytes_preserved': True})
        stamp = archive.getmember('usr/bin/hib2.sh').mtime
    for name, content, source, mode in (
        ('/usr/lib/nx300m-mod/preload.sh', helper, helper_source, 0o644),
        ('/usr/bin/nx300m-camera-launch', wrapper, wrapper_source, 0o755),
        ('/usr/lib/nx300m-mod/launch-profile', (PERSISTENT_TEMPLATES / 'launch-profile').read_bytes(),
         PERSISTENT_TEMPLATES / 'launch-profile', 0o644),
    ):
        files.append({'destination': name, 'content': content, 'change': 'new',
                      'source': source.relative_to(ROOT).as_posix(),
                      'metadata': {'mode': oct(mode), 'uid': 0, 'gid': 0, 'mtime': stamp,
                                   'uname': 'root', 'gname': 'root'}})
    return files


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    report = {
        'status': 'review-only candidates; no camera or boot script execution',
        'source': 'work/stock-1.15/rootfs.tar',
        'automatic_cold_start_confirmed': False,
        'normal_fast_resume_confirmed': False,
        'snapshot_creation_app_command_confirmed_static': True,
        'sd_root': '/mnt/mmc/nx300m-mod',
        'required_native_hooks': list(HOOKS),
        'hooks': [], 'original_members': [], 'patches': [],
        'limits': [
            'go.sh caller and normal bootargs are unknown',
            'hib2.sh is the snapshot creation path, not a demonstrated per-boot launcher',
            'SD is noauto in fstab; no SD mount or waiting was added',
            'SD absent or any required hook missing: original app command and environment',
            'SD checks apply at exec only, not to an already restored app or card removal',
            'Never run hib2.sh as a verification command: it erases mtd9 and creates a snapshot',
            'ELF header checks establish artifact type, not runtime dependency compatibility',
        ],
    }
    fragment = FRAGMENT.read_bytes()
    if b'\r' in fragment:
        raise ValueError('The shell fragment must have LF line endings')
    with tarfile.open(ROOT / report['source']) as archive:
        names = set(archive.getnames())
        report['rootfs_has_etc_inittab'] = 'etc/inittab' in names
        report['rootfs_has_rc_sysinit'] = 'etc/rc.d/rc.sysinit' in names
        for name in ('sbin/init', 'bin/sh', 'usr/bin/di-camera-app'):
            member = archive.getmember(name)
            report['original_members'].append({'path': name, 'link': member.linkname,
                                               'mode': oct(member.mode)})
        for name in EVIDENCE:
            member = archive.getmember(name)
            data = archive.extractfile(member).read()
            write('evidence/rootfs/' + name, data)
            report['original_members'].append({'path': name, 'bytes': len(data),
                                               'mode': oct(member.mode)})
        for name, old, new in (
            ('usr/bin/go.sh', b'\ndi-camera-app\n', b'\nnx300m_launch_app di-camera-app\n'),
            ('usr/bin/hib2.sh', b'\n$app &\n', b'\nnx300m_launch_app $app &\n'),
        ):
            original = archive.extractfile(name).read()
            if original.count(old) != 1:
                raise ValueError(f'Original app command is not unique: {name}')
            header_end = original.index(b'\n') + 1
            patched = original[:header_end] + b'\n' + fragment + b'\n' + original[header_end:]
            patched = patched.replace(old, new, 1)
            # All original bytes survive except the one explicit command change.
            restored = patched.replace(b'\n' + fragment + b'\n', b'', 1).replace(new, old, 1)
            if restored != original:
                raise ValueError(f'Unexpected change outside the helper/app call: {name}')
            output = write('candidate-rootfs/' + name, patched)
            patch = ''.join(difflib.unified_diff(original.decode().splitlines(True),
                                                patched.decode().splitlines(True),
                                                fromfile='a/' + name, tofile='b/' + name))
            write('patches/' + Path(name).name + '.patch', patch.encode())
            report['patches'].append({'original': name,
                                     'candidate': output.relative_to(ROOT).as_posix(),
                                     'original_bytes': len(original), 'candidate_bytes': len(patched),
                                     'original_command': old.decode().strip(),
                                     'new_command': new.decode().strip(),
                                     'other_original_bytes_preserved': True})
    for name, source in HOOKS.items():
        data = verify_native(ROOT / source)
        target = write('sd-fragment/nx300m-mod/lib/' + name, data)
        report['hooks'].append({'name': name, 'source': source, 'bytes': len(data),
                                'output': target.relative_to(ROOT).as_posix(),
                                'elf': 'ELF32 little-endian ARM ET_DYN'})
    report['kernel_evidence'] = kernel_evidence()
    # Syntax only: no boot commands, app, guest rootfs or existing namespace run.
    checks = []
    for script in [FRAGMENT] + [OUT / 'candidate-rootfs' / p for p in ('usr/bin/go.sh', 'usr/bin/hib2.sh')]:
        wsl_path = '/mnt/' + script.drive[0].lower() + script.as_posix()[2:]
        result = subprocess.run(['wsl.exe', '-d', 'Debian', '-e', '/bin/sh', '-n', wsl_path],
                                capture_output=True, encoding='utf-8', errors='replace')
        checks.append({'file': script.relative_to(ROOT).as_posix(), 'exit_code': result.returncode,
                       'stderr': result.stderr.strip()})
    report['shell_syntax'] = checks
    write('build-report.json', (json.dumps(report, ensure_ascii=False, indent=2) + '\n').encode())
    if any(c['exit_code'] for c in checks):
        raise RuntimeError('Shell syntax check failed; see build-report.json')
    print(json.dumps({'status': report['status'], 'patches': len(report['patches']),
                      'native_hooks': len(report['hooks']), 'shell_syntax': checks},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
