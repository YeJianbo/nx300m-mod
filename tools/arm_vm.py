"""Workspace-local ARM Linux VM; no binfmt or active camera-root changes.

Run with the project's Conda Python on Windows. The Linux worker uses only the
standard library. The serial command channel is intended for a trusted developer.
"""
import argparse
import base64
import concurrent.futures
import gzip
import hashlib
import io
import json
import lzma
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import tarfile
import time
import urllib.request
import uuid

PROJECT = Path(__file__).resolve().parents[1]
WORK = PROJECT / 'work/arm-vm'
OUT = PROJECT / 'outputs/arm-vm'
SERIAL = '/tmp/nx300m-arm-vm-serial.sock'
QMP = '/tmp/nx300m-arm-vm-qmp.sock'
DEBIAN = 'https://deb.debian.org/debian/'
KERNEL = DEBIAN + 'dists/trixie/main/installer-armhf/current/images/netboot/vmlinuz'


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def wsl_path(path):
    path = str(Path(path).resolve())
    return '/mnt/' + path[0].lower() + path[2:].replace('\\', '/')


def linux_call(arguments, capture=False, check=True):
    command = ['wsl', '-d', 'Debian', '--', 'python3', wsl_path(__file__), '--linux'] + arguments
    return subprocess.run(command, check=check, capture_output=capture, text=True, encoding='utf-8')


def fetch(url, path, sha256=None):
    if path.exists() and path.stat().st_size and (not sha256 or hashlib.sha256(path.read_bytes()).hexdigest() == sha256):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.part')
    print('Downloading ' + url, flush=True)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for attempt in range(5):
        try:
            request = urllib.request.Request(url + '?nx-arm-vm-range=metadata', headers={'Range': 'bytes=0-0'})
            with opener.open(request, timeout=20) as response:
                size = int(response.headers['Content-Range'].split('/')[-1]) if response.status == 206 else int(response.headers['Content-Length'])
            break
        except (OSError, TimeoutError):
            if attempt == 4:
                raise
            time.sleep(.5 * (2 ** attempt))
    block = 65536
    def part(start):
        end = min(start + block, size) - 1
        for attempt in range(5):
            try:
                request = urllib.request.Request(url + '?nx-arm-vm-range=' + str(start), headers={'Range': f'bytes={start}-{end}'})
                with opener.open(request, timeout=20) as response:
                    content_range = response.headers.get('Content-Range')
                    if response.status != 206 or content_range != f'bytes {start}-{end}/{size}':
                        raise ValueError('Unexpected Debian range response: ' + repr(content_range))
                    data = response.read()
                if len(data) != end - start + 1:
                    raise ValueError('Truncated Debian download range')
                return start, data
            except (OSError, TimeoutError):
                if attempt == 4:
                    raise
                time.sleep(.5 * (2 ** attempt))
    with temporary.open('w+b') as target, concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        target.truncate(size)
        futures = [executor.submit(part, start) for start in range(0, size, block)]
        for future in concurrent.futures.as_completed(futures):
            start, data = future.result()
            target.seek(start)
            target.write(data)
    if sha256 and hashlib.sha256(temporary.read_bytes()).hexdigest() != sha256:
        raise ValueError('Debian package SHA256 mismatch: ' + url)
    temporary.replace(path)


def package_index():
    path = WORK / 'downloads/Packages-armhf.xz'
    fetch(DEBIAN + 'dists/trixie/main/binary-armhf/Packages.xz', path)
    result = {}
    for stanza in lzma.decompress(path.read_bytes()).decode().split('\n\n'):
        item = {}
        for line in stanza.splitlines():
            if line and not line[0].isspace() and ': ' in line:
                key, value = line.split(': ', 1)
                item[key] = value
        if 'Package' in item:
            result[item['Package']] = item
    return result


def download_arm_packages(names, dependencies=False):
    index = package_index()
    pending, selected = list(names), {}
    while pending:
        name = pending.pop(0)
        if name in selected:
            continue
        item = index[name]
        selected[name] = item
        if dependencies:
            for field in ('Pre-Depends', 'Depends'):
                for group in item.get(field, '').split(','):
                    alternatives = [re.split(r'[\s(:]', part.strip())[0] for part in group.split('|')]
                    dependency = next((p for p in alternatives if p in index), None)
                    if dependency:
                        pending.append(dependency)
        fetch(DEBIAN + item['Filename'], WORK / 'downloads' / Path(item['Filename']).name, item['SHA256'])
    write_json(OUT / 'arm-package-sources.json', selected)
    return selected


def cpio_add(stream, name, mode, data=b'', inode=1):
    encoded = name.encode() + b'\0'
    fields = [inode, mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(encoded), 0]
    header = b'070701' + ''.join(f'{value:08x}' for value in fields).encode()
    stream.write(header + encoded)
    stream.write(b'\0' * (-(len(header) + len(encoded)) % 4))
    stream.write(data)
    stream.write(b'\0' * (-len(data) % 4))


INIT = '''#!/rescue/busybox sh
export PATH=/bin:/sbin:/usr/bin:/usr/sbin
/rescue/busybox mount -t devtmpfs devtmpfs /dev
/rescue/busybox mount -t proc proc /proc
/rescue/busybox mount -t sysfs sysfs /sys
/rescue/busybox mkdir -p /dev/pts /tmp/.X11-unix
/rescue/busybox mount -t devpts devpts /dev/pts
/rescue/busybox chmod 1777 /tmp /tmp/.X11-unix
/rescue/busybox hostname nx300m-arm-linux-vm
/rescue/busybox ifconfig lo 127.0.0.1 up
echo ARM_VM_KERNEL_READY
/bin/sh /arm-vm/native-probe.sh
if [ -f /arm-vm/post-boot.sh ]; then /bin/sh /arm-vm/post-boot.sh; fi
echo ARM_VM_SHELL_READY
while true; do /rescue/busybox setsid /bin/sh -i < /dev/ttyAMA0 > /dev/ttyAMA0 2>&1; done
'''

PROBE = '''#!/bin/sh
set -eu
echo ARM_VM_NATIVE_PROBE_BEGIN
uname -a
/bin/busybox sleep 600 &
child=$!
observed=$(readlink /proc/$child/exe)
expected=$(stat -L -c '%d:%i' /bin/busybox)
actual=$(stat -L -c '%d:%i' /proc/$child/exe)
printf 'ARM_VM_NATIVE_CHILD pid=%s exe=%s expected=%s actual=%s\\n' "$child" "$observed" "$expected" "$actual"
[ "$observed" = /bin/busybox ]
[ "$actual" = "$expected" ]
kill "$child"
wait "$child" || :
/bin/sh -c '/bin/busybox echo ARM_VM_NATIVE_CROSS_EXEC_PASSED'
echo ARM_VM_NATIVE_PROBE_PASSED
'''


def prepare(overlay=None, deb_overlays=(), stock_root=False):
    from elftools.elf.elffile import ELFFile
    WORK.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    fetch(KERNEL, WORK / 'downloads/vmlinuz')
    packages = download_arm_packages(['busybox-static'])
    package = WORK / 'downloads' / Path(packages['busybox-static']['Filename']).name
    linux_call(['extract', wsl_path(package), wsl_path(WORK / 'rescue')])
    root = WORK / 'root'
    if root.exists():
        assert root.resolve().parent == WORK.resolve() and not root.is_symlink()
        shutil.rmtree(root)
    root.mkdir()
    entries = {}
    directory_modes = {}
    def put(path, data, mode=0o100755):
        entries[path] = (mode, data)
    source = json.loads((PROJECT / 'work/stock-1.15/source.json').read_text(encoding='utf-8'))
    with (PROJECT / 'work/stock-1.15/nx300m.bin').open('rb') as firmware:
        assert hashlib.file_digest(firmware, 'sha256').hexdigest() == source['local_binary_sha256']
    provenance = []
    with tarfile.open(PROJECT / 'work/stock-1.15/rootfs.tar') as archive:
        members = {m.name: m for m in archive}
        def resolve(name):
            for _ in range(30):
                member = members[name]
                if member.isfile():
                    return name, archive.extractfile(member).read()
                if member.issym():
                    import posixpath
                    name = posixpath.normpath(member.linkname.lstrip('/') if member.linkname.startswith('/') else posixpath.join(posixpath.dirname(name), member.linkname))
                elif member.islnk():
                    name = member.linkname.lstrip('/')
                else:
                    raise ValueError(name)
            raise ValueError('stock symlink cycle')
        if stock_root:
            # Carry original dynamic resources and links directly from TAR;
            # Windows extraction would lose their Unix filesystem semantics.
            for name, member in members.items():
                if not name or name == '.':
                    continue
                if name.startswith('/') or '..' in Path(name).parts:
                    raise ValueError('Invalid stock archive path: ' + name)
                if member.isdir():
                    directory_modes[name.rstrip('/')] = stat.S_IFDIR | member.mode
                elif member.isfile():
                    put(name, archive.extractfile(member).read(), stat.S_IFREG | member.mode)
                elif member.issym():
                    put(name, member.linkname.encode(), stat.S_IFLNK | member.mode)
                elif member.islnk():
                    origin, data = resolve(name)
                    put(name, data, stat.S_IFREG | members[origin].mode)
                else:
                    raise ValueError('Unsupported stock archive member: ' + name)
        pending = [('bin/busybox', 'bin/busybox'), ('lib/ld-linux.so.3', 'lib/ld-linux.so.3')]
        seen = set()
        while pending:
            destination, name = pending.pop()
            if destination in seen:
                continue
            seen.add(destination)
            origin, data = resolve(name)
            put(destination, data)
            provenance.append({'destination': destination, 'stock_member': origin, 'sha256': hashlib.sha256(data).hexdigest()})
            dynamic = ELFFile(io.BytesIO(data)).get_section_by_name('.dynamic')
            for tag in dynamic.iter_tags() if dynamic else []:
                if tag.entry.d_tag == 'DT_NEEDED':
                    target = next(p + tag.needed for p in ('lib/', 'usr/lib/') if p + tag.needed in members)
                    pending.append((target, target))
    for applet in ('sh', 'ash', 'echo', 'sleep', 'mkdir', 'rm', 'stat', 'readlink', 'flock', 'mv', 'grep', 'cat', 'kill', 'uname', 'ls', 'pwd', 'env', 'chmod', 'cp', 'touch', 'test', 'head', 'tail', 'wc', 'sed', 'awk', 'wget', 'ps', 'date', 'sync'):
        put('bin/' + applet, b'busybox', 0o120777)
    rescue = next((WORK / 'rescue').rglob('busybox'))
    put('rescue/busybox', rescue.read_bytes())
    put('init', INIT.encode())
    put('arm-vm/native-probe.sh', PROBE.encode())
    for package_path in deb_overlays:
        raw = subprocess.check_output(['wsl', '-d', 'Debian', '--', 'dpkg-deb', '--fsys-tarfile', wsl_path(package_path)])
        with tarfile.open(fileobj=io.BytesIO(raw)) as package:
            hardlinks = []
            for member in package:
                name = member.name.removeprefix('./').rstrip('/')
                if not name:
                    continue
                assert not name.startswith('/') and '..' not in Path(name).parts
                if member.isfile():
                    put(name, package.extractfile(member).read(), stat.S_IFREG | member.mode)
                elif member.issym():
                    put(name, member.linkname.encode(), stat.S_IFLNK | member.mode)
                elif member.islnk():
                    hardlinks.append((name, member.linkname.removeprefix('./')))
            for name, target in hardlinks:
                entries[name] = entries[target]
    if 'usr/lib/ld-linux-armhf.so.3' in entries:
        put('lib/ld-linux-armhf.so.3', b'../usr/lib/ld-linux-armhf.so.3', 0o120777)
    if overlay:
        overlay = Path(overlay).resolve()
        assert overlay.is_dir()
        for path in overlay.rglob('*'):
            relative = path.relative_to(overlay).as_posix()
            if path.is_symlink():
                put(relative, os.readlink(path).encode(), 0o120777)
            elif path.is_file():
                data = path.read_bytes()
                executable = data.startswith(b'\x7fELF') or data.startswith(b'#!')
                put(relative, data, 0o100755 if executable else 0o100644)
    directories = {'dev', 'dev/pts', 'proc', 'sys', 'tmp', 'mnt', 'mnt/mmc', 'run', 'var', 'var/run'} | set(directory_modes)
    for name in entries:
        directories.update(p.as_posix() for p in Path(name).parents if str(p) != '.')
    buffer = io.BytesIO()
    for inode, name in enumerate(sorted(directories), 1):
        if name not in entries:
            cpio_add(buffer, name, directory_modes.get(name, 0o40755), inode=inode)
    for inode, (name, (mode, data)) in enumerate(sorted(entries.items()), len(directories) + 1):
        cpio_add(buffer, name, mode, data, inode)
    cpio_add(buffer, 'TRAILER!!!', 0)
    image = WORK / 'initramfs.cpio.gz'
    temporary_image = WORK / 'initramfs.cpio.gz.build'
    # A cancelled compression must not truncate the last bootable image.
    with temporary_image.open('wb') as stream:
        stream.write(gzip.compress(buffer.getvalue(), mtime=0))
    temporary_image.replace(image)
    write_json(OUT / 'runtime-provenance.json', {'kernel_url': KERNEL, 'kernel_sha256': hashlib.sha256((WORK / 'downloads/vmlinuz').read_bytes()).hexdigest(), 'stock_firmware_sha256': source['local_binary_sha256'], 'stock_members': provenance, 'stock_root_included': stock_root, 'stock_root_tar': str(PROJECT / 'work/stock-1.15/rootfs.tar') if stock_root else None, 'deb_overlays': [str(Path(p).resolve()) for p in deb_overlays], 'overlay': str(overlay) if overlay else None, 'file_entries': len(entries), 'directory_entries': len(directories), 'camera_application_included_by_base': stock_root, 'process_identity_shim_used': False, 'global_binfmt_changed': False})
    print('Prepared ' + str(WORK / 'initramfs.cpio.gz'), flush=True)


def host_sources():
    pending, selected = ['qemu-system-arm', 'qemu-system-common', 'qemu-system-data'], {}
    while pending:
        name = pending.pop(0)
        if name in selected:
            continue
        raw = subprocess.check_output(['apt-cache', '--no-all-versions', 'show', name], text=True)
        fields = dict(line.split(': ', 1) for line in raw.split('\n\n')[0].splitlines() if ': ' in line and not line[0].isspace())
        selected[name] = fields
        for field in ('Pre-Depends', 'Depends'):
            for group in fields.get(field, '').split(','):
                dependency = re.split(r'[\s(:]', group.split('|')[0].strip())[0]
                if not dependency:
                    continue
                status = subprocess.run(['dpkg-query', '-W', '-f=${db:Status-Status}', dependency], capture_output=True, text=True)
                if status.stdout != 'installed':
                    pending.append(dependency)
    return selected


def prepare_host():
    sources = json.loads(linux_call(['host-sources'], capture=True).stdout)
    for item in sources.values():
        package = WORK / 'downloads/host' / Path(item['Filename']).name
        fetch(DEBIAN + item['Filename'], package, item['SHA256'])
        linux_call(['extract', wsl_path(package), wsl_path(WORK / 'host')])
    write_json(OUT / 'host-package-sources.json', sources)
    print('Prepared workspace-local QEMU host packages', flush=True)


def qmp(command):
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(5)
        connection.connect(QMP)
        stream = connection.makefile('rwb', buffering=0)
        greeting = json.loads(stream.readline())
        def execute(name):
            stream.write(json.dumps({'execute': name}).encode() + b'\r\n')
            while True:
                response = json.loads(stream.readline())
                if 'return' in response or 'error' in response:
                    return response
        execute('qmp_capabilities')
        return execute(command)


def native_exec(command, timeout=30):
    token = uuid.uuid4().hex
    end = '__ARM_VM_END_' + token + '__'
    # Build the marker at runtime so serial terminal echo cannot match it.
    payload = '/bin/sh -c ' + shlex.quote(command) + "; arm_vm_rc=$?; printf '\\n%s%s:%s\\n' '__ARM_VM_END_' '" + token + "__' \"$arm_vm_rc\"\n"
    if len(payload.encode()) > 512:
        # The original BusyBox interactive editor has a short line buffer.
        # Stage long source in bounded lines, then pass it intact to sh -c.
        encoded = base64.b64encode(command.encode()).decode('ascii')
        source = '/tmp/arm-vm-command-' + token
        lines = ['arm_vm_stage_ok=1']
        for offset in range(0, len(encoded), 256):
            redirect = '>' if offset == 0 else '>>'
            lines.append("printf '%s' " + encoded[offset:offset + 256] + ' ' + redirect + ' ' + source + '.b64 || arm_vm_stage_ok=0')
        lines.extend([
            '/rescue/busybox base64 -d ' + source + '.b64 > ' + source + ' || arm_vm_stage_ok=0',
            'if [ "$arm_vm_stage_ok" = 1 ]; then /bin/sh -c "$(/rescue/busybox cat ' + source + ')"; arm_vm_rc=$?; else arm_vm_rc=125; fi; '
            '/rescue/busybox rm -f ' + source + ' ' + source + '.b64; '
            "printf '\\n%s%s:%s\\n' '__ARM_VM_END_' '" + token + "__' \"$arm_vm_rc\"",
        ])
        payload = '\n'.join(lines) + '\n'
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(timeout)
        connection.connect(SERIAL)
        # Drop a pending prompt before sending; one serial client at a time.
        connection.settimeout(.2)
        try:
            while connection.recv(65536):
                pass
        except socket.timeout:
            pass
        connection.settimeout(timeout)
        connection.sendall(payload.encode())
        output = b''
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            data = connection.recv(65536)
            if not data:
                raise RuntimeError('VM serial connection closed')
            output += data
            match = re.search(re.escape(end.encode()) + rb':(\d+)\r?\n', output)
            if match:
                return {'exit_code': int(match[1]), 'output': output[:match.start()].decode('utf-8', 'replace')}
        raise TimeoutError('VM command timeout')


def linux_main(args):
    if args.action == 'host-sources':
        print(json.dumps(host_sources()))
    elif args.action == 'extract':
        subprocess.run(['dpkg-deb', '-x', args.extra[0], args.extra[1]], check=True)
    elif args.action == 'start':
        try:
            print(json.dumps(qmp('query-status')))
            raise RuntimeError('VM already running')
        except (FileNotFoundError, ConnectionRefusedError):
            pass
        for path in (SERIAL, QMP):
            Path(path).unlink(missing_ok=True)
        binary = WORK / 'host/usr/bin/qemu-system-arm'
        env = dict(os.environ, LD_LIBRARY_PATH=str(WORK / 'host/usr/lib/x86_64-linux-gnu'))
        os.umask(0o077)
        command = [str(binary), '-M', 'virt', '-cpu', 'cortex-a15', '-m', str(args.memory_mib) + 'M', '-smp', '2', '-kernel', str(WORK / 'downloads/vmlinuz'), '-initrd', str(WORK / 'initramfs.cpio.gz'), '-append', 'console=ttyAMA0 rdinit=/init panic=-1', '-display', 'none', '-monitor', 'none', '-serial', 'unix:' + SERIAL + ',server=on,wait=off,logfile=' + str(OUT / 'serial.log'), '-qmp', 'unix:' + QMP + ',server=on,wait=off', '-nic', 'none', '-no-reboot']
        logfile = (OUT / 'qemu.log').open('ab', buffering=0)
        process = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL, stdout=logfile, stderr=subprocess.STDOUT, start_new_session=True)
        write_json(OUT / 'handle.json', {'pid': process.pid, 'command': command, 'serial_socket': SERIAL, 'qmp_socket': QMP, 'boot_started_epoch': time.time()})
        print(json.dumps({'pid': process.pid, 'log': str(OUT / 'serial.log')}))
    elif args.action == 'status':
        print(json.dumps(qmp('query-status')))
    elif args.action == 'stop':
        print(json.dumps(qmp('quit')))
    elif args.action == 'reset-console':
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(2)
            connection.connect(SERIAL)
            # This is explicit recovery of an unfinished interactive input,
            # not an automatic response to an observation timeout.
            connection.sendall(b'\x03\x04')
            try:
                output = connection.recv(65536).decode('utf-8', 'replace')
            except socket.timeout:
                output = ''
        print(json.dumps({'interrupt_and_eof_sent': True, 'output': output}))
    elif args.action == 'exec':
        command = base64.b64decode(args.extra[0], validate=True).decode('utf-8') if args.command_base64 else args.extra[0]
        result = native_exec(command, args.timeout)
        print(json.dumps(result, ensure_ascii=False))
        return result['exit_code']
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--linux', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--command-base64', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('action', choices=['prepare', 'host', 'host-sources', 'start', 'status', 'stop', 'reset-console', 'exec', 'probe', 'extract', 'packages'])
    parser.add_argument('extra', nargs='*')
    parser.add_argument('--overlay')
    parser.add_argument('--stock-root', action='store_true', help='Include the complete original stock TAR in prepare; requires additional guest RAM')
    parser.add_argument('--deb-overlay', action='append', default=[], help='ARM Debian package(s), glob patterns supported')
    parser.add_argument('--timeout', type=int, default=30)
    parser.add_argument('--memory-mib', type=int, default=512, help='Guest RAM for start; use 1024 or more for Xvfb package overlays')
    parser.add_argument('--dependencies', action='store_true')
    args = parser.parse_args()
    if args.memory_mib <= 0:
        parser.error('--memory-mib must be positive')
    if args.linux:
        return linux_main(args)
    if args.action == 'prepare':
        import glob
        packages = [path for pattern in args.deb_overlay for path in glob.glob(pattern)]
        prepare(args.overlay, packages, stock_root=args.stock_root)
    elif args.action == 'host':
        prepare_host()
    elif args.action == 'packages':
        download_arm_packages(args.extra, args.dependencies)
    elif args.action == 'probe':
        result = linux_call(['exec', '/bin/sh /arm-vm/native-probe.sh', '--timeout', str(args.timeout)], capture=True, check=False)
        if not result.stdout.strip():
            print(result.stderr, file=sys.stderr)
            return result.returncode or 1
        report = json.loads(result.stdout)
        report.update({'arm_kernel_native_exec': report['exit_code'] == 0 and 'ARM_VM_NATIVE_PROBE_PASSED' in report['output'], 'scope': 'Generic ARM Linux kernel and original stock BusyBox execution; no camera identity assertion', 'process_identity_shim_used': False, 'global_binfmt_changed': False})
        write_json(OUT / 'base-probe.json', report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return result.returncode
    else:
        # WSL's Windows command-line transport can expand dollar expressions in
        # shell source. Carry source as opaque bytes until the Linux worker.
        extra = args.extra
        flags = []
        if args.action == 'exec':
            extra = [base64.b64encode(args.extra[0].encode('utf-8')).decode('ascii')]
            flags = ['--command-base64']
        result = linux_call([args.action] + extra + flags + ['--timeout', str(args.timeout), '--memory-mib', str(args.memory_mib)], capture=True, check=False)
        print(result.stdout, end='')
        if result.stderr:
            print(result.stderr, end='', file=sys.stderr)
        return result.returncode
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
