"""Probe the packaged ARM remote in a disposable, isolated stock-library chroot.

Windows/Conda stages dependencies from the verified stock TAR. Linux workers use
only the standard library. No running camera root, binfmt registration or device
other than /dev/null is used. The production scripts are not altered.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import posixpath
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request

PROJECT = Path(__file__).resolve().parents[1]
OUT = PROJECT / 'outputs/deployment/sd-bundle/native-probe'
SD = '/mnt/mmc/nx300m-mod'
REMOTE = SD + '/bin/nx-remote-controller.arm'


def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def stage():
    from elftools.elf.elffile import ELFFile
    OUT.mkdir(parents=True, exist_ok=True)
    source = json.loads((PROJECT / 'work/stock-1.15/source.json').read_text(encoding='utf-8'))
    assert (source['model'], source['version'], source['zip_entry_crc_verified_on_complete_read']) == ('NX300M', '1.15', True)
    with (PROJECT / 'work/stock-1.15/nx300m.bin').open('rb') as stream:
        assert hashlib.file_digest(stream, 'sha256').hexdigest() == source['local_binary_sha256']
    # This subtree belongs only to this probe, never the active firmware root.
    staged = OUT / 'runtime-files'
    if staged.exists():
        assert not staged.is_symlink() and staged.resolve().parent == OUT.resolve()
        shutil.rmtree(staged)
    staged.mkdir()
    provenance = []
    with tarfile.open(PROJECT / 'work/stock-1.15/rootfs.tar') as archive:
        members = {m.name: m for m in archive.getmembers()}

        def resolve(name):
            for _ in range(30):
                item = members[name]
                if item.isfile():
                    return name, archive.extractfile(item).read()
                if item.issym():
                    name = posixpath.normpath(item.linkname.lstrip('/') if item.linkname.startswith('/') else posixpath.join(posixpath.dirname(name), item.linkname))
                elif item.islnk():
                    name = item.linkname.lstrip('/')
                else:
                    raise ValueError('Unexpected stock member: ' + name)
            raise ValueError('Stock link cycle')

        pending = [('lib/ld-linux.so.3', 'lib/ld-linux.so.3'), ('bin/arm/busybox', 'bin/busybox')]
        seen = set()

        def dependencies(data):
            dynamic = ELFFile(io.BytesIO(data)).get_section_by_name('.dynamic')
            for tag in dynamic.iter_tags() if dynamic else []:
                if tag.entry.d_tag == 'DT_NEEDED':
                    name = next((folder + tag.needed for folder in ('lib/', 'usr/lib/') if folder + tag.needed in members), None)
                    if not name:
                        raise ValueError('Stock dependency missing: ' + tag.needed)
                    pending.append((name, name))

        bundle = PROJECT / 'outputs/deployment/sd-bundle/nx300m-mod-sd.tar.gz'
        with tarfile.open(bundle) as package:
            for member in package:
                path = Path(member.name)
                assert path.parts[0] == 'nx300m-mod' and '..' not in path.parts and not path.is_absolute()
                target = staged / 'mnt/mmc' / path
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    data = package.extractfile(member).read()
                    target.write_bytes(data)
                    if member.name == 'nx300m-mod/bin/nx-remote-controller.arm':
                        assert ELFFile(io.BytesIO(data))['e_machine'] == 'EM_ARM'
                        dependencies(data)
                else:
                    raise ValueError('Unexpected SD archive link')
        while pending:
            destination, name = pending.pop()
            if destination in seen:
                continue
            seen.add(destination)
            origin, data = resolve(name)
            target = staged / destination
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            provenance.append({'destination': destination, 'stock_member': origin, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
            dependencies(data)
        origin, data = resolve('etc/version.info')
        (staged / 'etc').mkdir()
        (staged / 'etc/version.info').write_bytes(data)
        provenance.append({'destination': 'etc/version.info', 'stock_member': origin, 'bytes': len(data)})
    shutil.copyfile(PROJECT / 'work/qemu/qemu-arm-static', staged / 'qemu-arm-static')
    metadata = {'stock_firmware_sha256': source['local_binary_sha256'], 'bundle_sha256': hashlib.sha256(bundle.read_bytes()).hexdigest(),
                'remote_sha256': hashlib.sha256((staged / REMOTE.lstrip('/')).read_bytes()).hexdigest(),
                'expected_look_ids': sorted(p.name for p in (staged / SD.lstrip('/') / 'looks').iterdir()),
                'stock_members': provenance}
    save('runtime-provenance.json', metadata)
    return metadata


def namespaces(pid='self'):
    return {name: os.readlink(f'/proc/{pid}/ns/{name}') for name in ('mnt', 'net', 'ipc', 'pid')}


def launch():
    env = dict(os.environ, NX_PROBE_PARENT_NS=json.dumps(namespaces()))
    return subprocess.run(['unshare', '--mount', '--net', '--ipc', '--pid', '--fork', '--mount-proc', sys.executable, str(Path(__file__).resolve()), '--worker'], env=env).returncode


def worker():
    report = {'scope': 'Packaged ARM remote under QEMU with original stock loader, libc and X libraries; no camera application',
              'namespace_parent': json.loads(os.environ['NX_PROBE_PARENT_NS']), 'namespace_probe': namespaces(),
              'camera_app_started': False, 'production_scripts_modified': False, 'process_identity_shim_used': False,
              'physical_camera_verified': False, 'global_binfmt_changed': False, 'checks': []}
    assert all(report['namespace_parent'][k] != report['namespace_probe'][k] for k in namespaces())
    processes, root = [], None
    try:
        subprocess.run(['mount', '--make-rprivate', '/'], check=True)
        # Xvfb lock/socket names and the chroot are private even to other probes.
        subprocess.run(['mount', '-t', 'tmpfs', '-o', 'mode=1777,nosuid,nodev', 'tmpfs', '/tmp'], check=True)
        subprocess.run(['ip', 'link', 'set', 'lo', 'up'], check=True)
        root = Path(tempfile.mkdtemp(prefix='nx-sd-native-'))
        os.chmod(root, 0o755)
        shutil.copytree(OUT / 'runtime-files', root, dirs_exist_ok=True)
        for p in root.rglob('*'):
            os.chmod(p, 0o755 if p.is_dir() or p.name.endswith('.sh') or p.parent.name == 'bin' or p.name in ('busybox', 'qemu-arm-static') else 0o644)
        for folder in ('proc', 'dev', 'tmp', 'tmp/.X11-unix', 'var/run', 'mnt/mmc/DCIM'):
            (root / folder).mkdir(parents=True, exist_ok=True)
        os.chmod(root / 'tmp', 0o1777)
        os.chmod(root / 'var/run', 0o777)
        subprocess.run(['mount', '--bind', '/proc', str(root / 'proc')], check=True)
        (root / 'dev/null').touch()
        subprocess.run(['mount', '--bind', '/dev/null', str(root / 'dev/null')], check=True)
        # Only interpreter/applet dispatch is bridged. ARM remote remains an
        # unchanged ELF, and stat/readlink still inspect the real kernel /proc.
        shell = '#!/qemu-arm-static /bin/arm/sh\nexec /qemu-arm-static /bin/arm/sh "$@"\n'
        (root / 'bin/arm/sh').symlink_to('busybox')
        (root / 'bin/sh').write_text(shell)
        os.chmod(root / 'bin/sh', 0o755)
        for command in ('mkdir', 'flock', 'sleep', 'rm', 'stat', 'readlink', 'mv', 'grep'):
            path = root / 'bin' / command
            path.write_text('#!/qemu-arm-static /bin/arm/sh\nexec /qemu-arm-static /bin/arm/busybox ' + command + ' "$@"\n')
            os.chmod(path, 0o755)
        save('interpreter-bridge.json', {'bin_sh': shell, 'applets': ['mkdir', 'flock', 'sleep', 'rm', 'stat', 'readlink', 'mv', 'grep'],
                                         'purpose': 'Dispatch original ARM BusyBox explicitly without global binfmt; no /proc identity translation'})
        xlog = (OUT / 'xvfb.log').open('w')
        xserver = subprocess.Popen(['Xvfb', ':97', '-screen', '0', '480x800x24', '-s', '0', '-nolisten', 'tcp', '-ac'], stdout=xlog, stderr=subprocess.STDOUT)
        processes.append(xserver)
        deadline = time.monotonic() + 8
        while not Path('/tmp/.X11-unix/X97').exists():
            assert xserver.poll() is None, 'Xvfb exited'
            if time.monotonic() > deadline:
                raise TimeoutError('Xvfb socket unavailable')
            time.sleep(.05)
        subprocess.run(['mount', '--bind', '/tmp/.X11-unix', str(root / 'tmp/.X11-unix')], check=True)
        env = dict(os.environ, PATH='/bin', DISPLAY=':97', NX300M_MOD_ROOT=SD, NX_STATE_DIR='/tmp/service-state', NX_START_TIMEOUT='2', NX_STOP_TIMEOUT='2')
        base = ['/usr/sbin/chroot', '--userspec=65534:65534', str(root), '/qemu-arm-static', '-cpu', 'cortex-a9']
        result = subprocess.run(base + ['/bin/arm/busybox', 'sh', SD + '/start.sh'], env=env, capture_output=True, text=True, timeout=18)
        (OUT / 'script-start.stdout.log').write_text(result.stdout)
        (OUT / 'script-start.stderr.log').write_text(result.stderr)
        state = root / 'tmp/service-state'
        for filename in ('remote.log', 'supervisor.log', 'service.state', 'remote.pid'):
            if (state / filename).exists():
                shutil.copyfile(state / filename, OUT / ('script-' + filename))
        report['script_start'] = {'exit_code': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr,
                                  'end_to_end_verified': result.returncode == 0,
                                  'boundary': 'Kernel cannot execute ARM ELF via QEMU without binfmt; /proc/PID/exe also names QEMU. Production ownership checks remain unchanged.'}
        # No replacement ready marker or replacement remote: execute the same
        # packaged ARM ELF explicitly, independent of the failed script chain.
        logfile = (OUT / 'remote.log').open('w')
        command = base + [REMOTE, '--listen', '127.0.0.1:8781', '--web-root', SD + '/web_root', '--media-root', '/mnt/mmc/DCIM', '--quickshare-binary', SD + '/bin/nx-quickshare-armv7']
        remote = subprocess.Popen(command, env=env, stdout=logfile, stderr=subprocess.STDOUT)
        processes.append(remote)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def request(path, data=None):
            payload = None if data is None else json.dumps(data).encode()
            req = urllib.request.Request('http://127.0.0.1:8781' + path, data=payload, headers={'Content-Type': 'application/json'})
            try:
                response = opener.open(req, timeout=3)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                return {'status': response.status, 'body': json.loads(response.read())}

        deadline = time.monotonic() + 12
        while True:
            assert remote.poll() is None, 'ARM remote exited before HTTP ready'
            try:
                info = request('/api/v1/camera/info')
                break
            except (OSError, TimeoutError):
                if time.monotonic() > deadline:
                    raise
                time.sleep(.1)
        report['camera_info'] = info
        assert info['status'] == 200
        assert 'NX_REMOTE_READY ' in (OUT / 'remote.log').read_text()
        assert info['body']['model'] == 'NX300M' and info['body']['fw_ver'] == '1.15'
        assert info['body']['capabilities']['looks'] is True and info['body']['capabilities']['lut_selection'] is False
        report['checks'].append('actual_ARM_remote_HTTP_ready_and_stock_model_info')
        catalog = request('/api/v1/looks')
        report['catalog'] = catalog
        expected = json.loads((OUT / 'runtime-provenance.json').read_text())['expected_look_ids']
        assert catalog['status'] == 200 and len(expected) == 17
        assert sorted(item['id'] for item in catalog['body']['looks']) == expected
        assert all(item['table_valid'] and not item['selectable'] for item in catalog['body']['looks'])
        assert catalog['body']['hook']['ready'] is False
        report['checks'].append('17_valid_LUT_entries_readable_without_camera_hook')
        before = {p.name: p.read_bytes() for p in (root / 'var/run').iterdir() if p.is_file()}
        writes = [request('/api/v1/looks/select', {'id': expected[0]}), request('/api/v1/looks/stock', {})]
        after = {p.name: p.read_bytes() for p in (root / 'var/run').iterdir() if p.is_file()}
        report['write_attempts'] = writes
        report['lut_runtime_files_before_after'] = {'before': sorted(before), 'after': sorted(after), 'bytes_unchanged': before == after}
        assert all(response['status'] == 503 for response in writes) and before == after
        assert not any((root / ('var/run/' + name)).exists() for name in ('nx300m-lut-selection', 'nx300m-lut-generation', 'nx300m-lut-hook'))
        report['checks'].append('select_and_stock_refused_503_without_selection_mutation')
        snapshot = []
        for directory in Path('/proc').iterdir():
            if directory.name.isdigit():
                try:
                    raw = (directory / 'cmdline').read_bytes()
                    snapshot.append({'pid': int(directory.name), 'argv': [arg.decode(errors='replace') for arg in raw.split(b'\0') if arg]})
                except FileNotFoundError:
                    pass
        report['process_snapshot'] = snapshot
        assert not (root / 'usr/bin/di-camera-app-nx300').exists()
        assert not any(any('di-camera-app' in arg for arg in row['argv']) for row in snapshot)
        report['checks'].append('camera_application_absent_from_chroot_and_private_PID_namespace')
        # PID is a live Popen child; verify exact command line and QEMU inode
        # before sending TERM, without adapting the production script's checks.
        actual = Path(f'/proc/{remote.pid}/cmdline').read_bytes().split(b'\0')[:-1]
        expected_cmd = ['/qemu-arm-static', '-cpu', 'cortex-a9'] + command[6:]
        assert actual == [a.encode() for a in expected_cmd], (actual, expected_cmd)
        executable = Path(f'/proc/{remote.pid}/exe').stat()
        qemu = (root / 'qemu-arm-static').stat()
        assert (executable.st_dev, executable.st_ino) == (qemu.st_dev, qemu.st_ino)
        report['stop_identity'] = {'pid': remote.pid, 'cmdline': expected_cmd, 'exe': os.readlink(f'/proc/{remote.pid}/exe'), 'exact_cmdline_and_qemu_inode_verified': True}
        remote.terminate()
        code = remote.wait(timeout=8)
        report['stop'] = {'method': 'TERM to verified explicit-QEMU child, not stop.sh', 'exit_code': code}
        assert code == 0
        try:
            request('/api/v1/camera/info')
        except OSError:
            report['stop']['HTTP_connection_closed'] = True
        else:
            raise AssertionError('HTTP still available after ARM service exit')
        report['checks'].append('verified_ARM_service_TERM_exit_0_and_HTTP_closed')
        report['passed'] = True
    except Exception as error:
        report['passed'] = False
        report['error'] = repr(error)
        raise
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        if root:
            subprocess.run(['umount', '-R', str(root)], check=False, capture_output=True)
        save('probe-report.json', report)
        print(json.dumps(report, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--launch', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        worker()
    elif args.launch:
        raise SystemExit(launch())
    else:
        stage()
        guest = '/mnt/' + PROJECT.drive[0].lower() + PROJECT.as_posix()[2:]
        script = 'cd ' + shlex.quote(guest) + ' && exec python3 tools/probe_sd_native.py --launch'
        result = subprocess.run(['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'sh', '-c', script], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=70)
        (OUT / 'probe.stdout.log').write_text(result.stdout, encoding='utf-8')
        (OUT / 'probe.stderr.log').write_text(result.stderr, encoding='utf-8')
        print(result.stdout)
        if result.returncode:
            print(result.stderr[-6000:], file=sys.stderr)
        raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
