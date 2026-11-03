"""Actual FAT32 persistence in a disposable private mount namespace/chroot.

Never accepts a physical device/image argument; never enters a stock session.
Native production persistence and launcher are tested, not ARM ptrace/ISP.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import traceback
import uuid

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/deployment/smartfilter-preview/media-fs'
SOURCES = (
    'runtime/smartfilter_media.h', 'runtime/smartfilter_persistence.h',
    'runtime/smartfilter_probe_control.h', 'runtime/smartfilter_loader.c',
    'runtime/live_loader_core.h',
    'mod/deployment/mfw/smartfilter-probe-launcher.c',
    'mod/deployment/mfw/smartfilter-probe-collector.sh',
    'tools/smartfilter_media_fs_host.c', 'tools/probe_smartfilter_media_fs.py',
)
CANDIDATES = ('smartfilter_loader.arm', 'smartfilter_probe_launcher.arm', 'smartfilter_ram.bin')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory():
    base = OUT.parent
    return {
        'sources': {name: sha(ROOT / name) for name in SOURCES},
        'candidates': {name: sha(base / name) for name in CANDIDATES},
    }


def command(args, *, check=True):
    result = subprocess.run([str(arg) for arg in args], capture_output=True, text=True,
                            encoding='utf-8', errors='strict', timeout=60)
    if check and result.returncode:
        raise RuntimeError(f'{args!r}: rc={result.returncode}\n{result.stdout}\n{result.stderr}')
    return result


def namespace(workspace, token):
    import fcntl
    work = Path(workspace)
    assert str(work).startswith('/tmp/nx300m-media-fs-') and not work.is_symlink()
    owner = json.loads((work / 'owner.json').read_text())
    assert owner['token'] == token and work.stat().st_uid == os.geteuid() == 0
    assert os.stat('/proc/self/ns/mnt').st_ino != owner['parent_mount_ns']
    command(['mount', '--make-rprivate', '/'])
    root = work / 'root'
    image = work / 'fat32.img'
    mounted = []
    loopfd = None
    loop = None
    result = {'passed': False, 'private_mount_namespace': True, 'checks': {},
              'physical_card_accessed': False, 'image_created_exclusively': False,
              'fat32': {'tested': False}, 'exfat': {'tested': False, 'status': 'not_tested',
              'reason': 'No installed mkfs.exfat; no dependency installation attempted'},
              'native_production_code': True, 'arm_ptrace_end_to_end': False}

    def checked(name, truth):
        result['checks'][name] = bool(truth)
        if not truth:
            raise AssertionError(name)

    def mount(target, fs, source='tmpfs', options='mode=0755'):
        target.mkdir(parents=True, exist_ok=True)
        command(['mount', '-t', fs, '-o', options, source, target])
        mounted.append(target)

    def unmount(target):
        command(['umount', target])
        mounted.remove(target)

    def inside(args):
        return command(['chroot', root, *args], check=False)

    serial = 0

    def snapshot():
        nonlocal serial
        serial += 1
        name = f'/var/run/nx300m-sf-diag-fs-{serial}.ndjson'
        path = root / name.lstrip('/')
        data = ('{"native_real_fs_case":' + str(serial) + '}\n').encode()
        path.write_bytes(data)
        path.chmod(0o600)
        return name, data

    def persist(label, target, *, probe=False, source=None, identity='-', success=True):
        if source is None:
            source, data = snapshot()
        else:
            data = (root / source.lstrip('/')).read_bytes()
        call = inside(['/bin/persist', source, target, 'probe' if probe else 'ordinary', identity])
        receipt = json.loads(call.stdout)
        checked(label, call.returncode == (0 if success else 3) and receipt['persisted'] is success)
        if not success:
            checked(label + '_no_append', receipt['write_attempted'] is False)
        return source, data, receipt

    def source_id(source):
        s = source.stat()
        return f'{s.st_dev}:{s.st_ino}'

    def prepare(label, nonce, *, identity=None, source='/mnt/mmc/nx300m-mod', success=True):
        if identity is None:
            identity = source_id(root / source.lstrip('/'))
        call = inside(['/bin/launcher', '--prepare', source, identity, nonce])
        checked(label, (call.returncode == 0) is success)
        if success:
            path, observed = call.stdout.strip().split()
            actual = root / path.lstrip('/')
            checked(label + '_exact_identity', observed == source_id(actual) and actual.stat().st_size == 0)
            return path, actual
        # Failures must retain parseable first-error evidence, not just perror.
        receipt = json.loads(call.stdout)
        checked(label + '_truthful_receipt', receipt['persisted'] is False and
                receipt['snapshot_created'] is False and receipt['write_attempted'] is False and
                bool(receipt.get('error_stage')))
        return receipt

    try:
        for name in ('bin', 'mnt/mmc', 'var/run', 'perf'):
            (root / name).mkdir(parents=True, exist_ok=True)
        shutil.copy2(work / 'build/persist', root / 'bin/persist')
        shutil.copy2(work / 'build/launcher', root / 'bin/launcher')
        mount(root / 'var/run', 'tmpfs', options='mode=0700,nosuid,nodev')
        with image.open('xb') as stream:
            stream.truncate(64 * 1024 * 1024)
        result['image_created_exclusively'] = True
        command(['mkfs.fat', '-F', '32', '-n', 'NX300MTEST', image])
        with image.open('rb') as stream:
            boot = stream.read(512)
        checked('real_fat32_boot_record', boot[82:90] == b'FAT32   ' and boot[510:512] == b'\x55\xaa')
        loop = command(['losetup', '--find', '--show', image]).stdout.strip()
        checked('private_loop_device_only', bool(re.fullmatch(r'/dev/loop[0-9]+', loop)) and stat.S_ISBLK(os.stat(loop).st_mode))
        checked('loop_backing_is_only_new_image', command(['losetup', '--noheadings', '--output', 'BACK-FILE', loop]).stdout.strip() == str(image))
        loopfd = os.open(loop, os.O_RDWR | os.O_CLOEXEC)
        info = bytearray(232)
        fcntl.ioctl(loopfd, 0x4c05, info, True)
        import struct
        struct.pack_into('<I', info, 52, struct.unpack_from('<I', info, 52)[0] | 4)
        fcntl.ioctl(loopfd, 0x4c04, info)
        observations = []
        for index, mask in enumerate(('0022', '0000')):
            tag = f'fat32_mask_{mask}'
            mount(root / 'mnt/mmc', 'vfat', loop, f'uid=65534,gid=65534,umask={mask},nosuid,nodev')
            source = root / 'mnt/mmc/nx300m-mod'
            source.mkdir(exist_ok=True)
            magic = int(command(['stat', '-f', '-c', '%t', source]).stdout.strip(), 16)
            checked(tag + '_actual_magic', magic == 0x4d44)
            checked(tag + '_actual_uid_differs', source.stat().st_uid == 65534 != os.geteuid())
            observations.append({'umask': mask, 'uid': source.stat().st_uid,
                                 'euid': os.geteuid(), 'fs_type': magic, 'source_id': source_id(source)})
            nonce = f'{index + 1:032x}'
            probe_path, probe_actual = prepare(tag + '_prepare', nonce)
            normal_path = '/mnt/mmc/nx300m-mod/logs/fn-key.log'
            normal = root / normal_path.lstrip('/')
            before = normal.read_bytes() if normal.exists() else b''
            snap, data, receipt = persist(tag + '_ordinary', normal_path)
            checked(tag + '_ordinary_exact_bytes', normal.read_bytes() == before + data and receipt['destination_bytes_verified'] is True)
            stable = normal.read_bytes()
            persist(tag + '_ordinary_idempotent', normal_path, source=snap)
            checked(tag + '_ordinary_not_duplicated', normal.read_bytes() == stable)
            snap, data, receipt = persist(tag + '_probe', probe_path, probe=True, identity=source_id(source))
            checked(tag + '_probe_exact_bytes', probe_actual.read_bytes() == data and receipt['destination_bytes_verified'] is True)
            persist(tag + '_probe_idempotent', probe_path, probe=True, identity=source_id(source), source=snap)
            checked(tag + '_probe_not_duplicated', probe_actual.read_bytes() == data)
            prepare(tag + '_duplicate_refused', nonce, success=False)
            empty_path, empty_actual = prepare(tag + '_negative_target', f'{index + 11:032x}')
            wrong_id = f'{source.stat().st_dev}:{source.stat().st_ino + 1}'
            persist(tag + '_wrong_source_id', empty_path, probe=True, identity=wrong_id, success=False)
            checked(tag + '_wrong_id_target_untouched', empty_actual.read_bytes() == b'')
            prepare(tag + '_prepare_wrong_id', f'{index + 21:032x}', identity=wrong_id, success=False)
            empty_actual.write_bytes(b'keep')
            persist(tag + '_nonempty_refused', empty_path, probe=True, identity=source_id(source), success=False)
            checked(tag + '_nonempty_kept', empty_actual.read_bytes() == b'keep')
            bad, _ = snapshot()
            os.chown(root / bad.lstrip('/'), 65534, 65534)
            persist(tag + '_ram_owner_strict', normal_path, source=bad, success=False)
            # A FAT filesystem at /perf must not gain the production-path exception.
            mount(root / 'perf', 'vfat', loop, f'uid=65534,gid=65534,umask={mask},nosuid,nodev')
            (root / 'perf/fn-key.log').touch(exist_ok=True)
            perf_before = (root / 'perf/fn-key.log').read_bytes()
            persist(tag + '_fixture_no_fat_exception', '/perf/fn-key.log', success=False)
            checked(tag + '_fixture_untouched', (root / 'perf/fn-key.log').read_bytes() == perf_before)
            unmount(root / 'perf')
            # An independently mounted logs directory must fail the media binding.
            mount(source / 'logs', 'tmpfs', options='mode=0755')
            (source / 'logs/fn-key.log').write_bytes(b'child-mount')
            persist(tag + '_cross_device_logs_refused', normal_path, success=False)
            checked(tag + '_child_mount_not_written', (source / 'logs/fn-key.log').read_bytes() == b'child-mount')
            unmount(source / 'logs')
            unmount(root / 'mnt/mmc')
        result['fat32'] = {'tested': True, 'passed': True, 'observations': observations,
                           'native_only': True, 'mocked_fs_or_uid': False}
        # Real POSIX target under the exact production path: root is not a bypass.
        mount(root / 'mnt/mmc', 'tmpfs', options='mode=0755')
        source = root / 'mnt/mmc/nx300m-mod'
        source.mkdir()
        logs = source / 'logs'
        logs.mkdir()
        target = logs / 'fn-key.log'
        target.write_bytes(b'posix')
        os.chown(target, 65534, 65534)
        persist('posix_target_owner_refused_even_root', '/mnt/mmc/nx300m-mod/logs/fn-key.log', success=False)
        checked('posix_bad_owner_unchanged', target.read_bytes() == b'posix')
        os.chown(target, 0, 0)
        snap, data, _ = persist('posix_ordinary_unchanged', '/mnt/mmc/nx300m-mod/logs/fn-key.log')
        checked('posix_exact_bytes', target.read_bytes() == b'posix' + data)
        for obj, label in ((source, 'source'), (logs, 'logs')):
            os.chown(obj, 65534, 65534)
            prepare('posix_' + label + '_owner_refused', f'{31 if label == "source" else 32:032x}', success=False)
            os.chown(obj, 0, 0)
        logs.chmod(0o777)
        prepare('posix_logs_permissions_refused', f'{33:032x}', success=False)
        logs.chmod(0o755)
        link = logs / 'sf-probe-'  # The actual symlink uses a valid leaf contract.
        link = logs / ('sf-probe-' + f'{34:032x}' + '.ndjson')
        link.symlink_to('fn-key.log')
        prepare('posix_symlink_refused', f'{34:032x}', success=False)
        # Marker remains private on RAM even when its destination is valid.
        marker = root / (snap + '.persisted').lstrip('/')
        os.chown(marker, 65534, 65534)
        stable = target.read_bytes()
        persist('ram_marker_owner_refused', '/mnt/mmc/nx300m-mod/logs/fn-key.log', source=snap, success=False)
        checked('ram_marker_refusal_no_duplicate', target.read_bytes() == stable)
        result['passed'] = True
    except BaseException as error:
        result.update(passed=False, error=str(error), traceback=traceback.format_exc())
    finally:
        cleanup_errors = []
        for target in reversed(mounted[:]):
            try:
                unmount(target)
            except BaseException as error:
                cleanup_errors.append(str(error))
        if loopfd is not None:
            os.close(loopfd)
        associated = command(['losetup', '-j', image], check=False)
        if (loop is not None and re.fullmatch(r'/dev/loop[0-9]+', loop)
                and associated.stdout.strip() and not mounted):
            backing = command(['losetup', '--noheadings', '--output', 'BACK-FILE', loop], check=False)
            if backing.returncode == 0 and backing.stdout.strip() == str(image):
                detached = command(['losetup', '--detach', loop], check=False)
                if detached.returncode:
                    cleanup_errors.append(detached.stderr)
                associated = command(['losetup', '-j', image], check=False)
        result['all_mounts_removed'] = not mounted
        result['loop_released'] = associated.returncode == 0 and not associated.stdout.strip()
        result['cleanup_errors'] = cleanup_errors
        result['passed'] = bool(result['passed'] and not cleanup_errors and
                                result['all_mounts_removed'] and result['loop_released'])
    print(json.dumps(result, ensure_ascii=True), flush=True)
    return 0 if result['passed'] else 1


def linux():
    OUT.mkdir(parents=True, exist_ok=True)
    before = inventory()
    report = {'passed': False, 'date': datetime.now(timezone.utc).isoformat(), **before,
              'physical_card_accessed': False, 'scope': 'Native production filesystem code, not ARM ptrace/ISP'}
    try:
        with tempfile.TemporaryDirectory(prefix='nx300m-media-fs-', dir='/tmp') as directory:
            work = Path(directory)
            build = work / 'build'
            build.mkdir()
            token = uuid.uuid4().hex
            (work / 'owner.json').write_text(json.dumps({'token': token, 'parent_mount_ns': os.stat('/proc/self/ns/mnt').st_ino}))
            original = (ROOT / 'runtime/live_loader_core.h').read_text(encoding='utf-8')
            (build / 'smartfilter_media_fs_sha.h').write_text(original[original.index('struct sha {'):original.index('static int file_hash(')])
            flags = ['gcc', '-std=gnu99', '-O2', '-g', '-Wall', '-Wextra', '-Werror',
                     '-Wno-unused-function', '-D_FILE_OFFSET_BITS=64', '-static',
                     '-I' + str(ROOT / 'runtime'), '-I' + str(build)]
            commands = [flags + [str(ROOT / 'tools/smartfilter_media_fs_host.c'), '-o', str(build / 'persist')],
                        flags + [str(ROOT / 'mod/deployment/mfw/smartfilter-probe-launcher.c'), '-o', str(build / 'launcher')]]
            logs = []
            for argv in commands:
                compiled = command(argv, check=False)
                logs.append({'argv': argv, 'exit': compiled.returncode, 'stdout': compiled.stdout, 'stderr': compiled.stderr})
                if compiled.returncode:
                    raise RuntimeError('Native real-FS driver build failed: ' + compiled.stderr)
            report['builds'] = logs
            report['native_binaries'] = {name: sha(build / name) for name in ('persist', 'launcher')}
            run = command(['unshare', '--mount', '--fork', '--', sys.executable, '-I', '-B',
                           Path(__file__).resolve(), '--namespace', work, token], check=False)
            (OUT / 'namespace.stdout.log').write_text(run.stdout, encoding='utf-8')
            (OUT / 'namespace.stderr.log').write_text(run.stderr, encoding='utf-8')
            report['namespace_exit'] = run.returncode
            if run.stdout.strip():
                report['filesystem_run'] = json.loads(run.stdout)
            report['passed'] = run.returncode == 0 and report.get('filesystem_run', {}).get('passed') is True
            if not report['passed']:
                report['error'] = report.get('filesystem_run', {}).get('error', run.stderr.strip())
        after = inventory()
        report['source_and_candidate_bytes_stable'] = before == after
        report['passed'] = report['passed'] and before == after
    except BaseException as error:
        report.update(passed=False, error=str(error), traceback=traceback.format_exc())
    (OUT / 'report.json').write_text(json.dumps(report, indent=2, ensure_ascii=True) + '\n', encoding='utf-8')
    print(json.dumps({'passed': report['passed'], 'report': str(OUT / 'report.json'),
                      'error': report.get('error')}, indent=2), flush=True)
    return 0 if report['passed'] else 1


def main():
    if len(sys.argv) == 4 and sys.argv[1] == '--namespace':
        return namespace(sys.argv[2], sys.argv[3])
    if sys.argv[1:] == ['--linux'] and os.name != 'nt':
        return linux()
    if sys.argv[1:]:
        raise SystemExit('Run without arguments (Windows) or --linux (WSL). No device/image arguments are accepted.')
    if os.name != 'nt':
        return linux()
    if OUT.exists():
        history = OUT.parent / ('media-fs-before-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
        OUT.rename(history)
    OUT.mkdir()
    script = '/mnt/' + ROOT.drive[0].lower() + ROOT.as_posix()[2:] + '/tools/probe_smartfilter_media_fs.py'
    return subprocess.run(['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'python3', '-I', '-B', script, '--linux']).returncode


if __name__ == '__main__':
    raise SystemExit(main())
