#!/usr/bin/env python3
"""Offline Linux host tests only: no ARM build, VM, camera, card or real loader.

Run with WSL Debian python3 -I. Production launcher/collector copies differ only
in fixture paths: /tmp job prefix -> executable /dev/shm, /perf -> private source,
camera executable -> this Python executable, and lock/guard paths -> private ones.
The launcher is built by host gcc in a private temporary directory.
"""
import ctypes
try:
    import fcntl
except ModuleNotFoundError:
    import unittest
    raise unittest.SkipTest('Linux process/lease tests run separately under WSL')
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import time
import unittest

BASE = Path(__file__).resolve().parents[1]
MFW = BASE / 'mod/deployment/mfw'
FAKE_LOADER = r'''#!/usr/bin/python3
import json, os, pathlib, sys, time
args=sys.argv[1:]; command=args[0]; job=pathlib.Path.cwd()
def val(name): return args[args.index(name)+1]
pid=val('--pid'); fields=pathlib.Path('/proc/'+pid+'/stat').read_text().rsplit(') ',1)[1].split()
token=pid+':'+fields[19]+':11:2:3'
case=os.environ.get('SF_TEST_CASE','ok')
source=pathlib.Path(os.environ['SF_TEST_SOURCE'])
state=pathlib.Path(os.environ['SF_TEST_STATE'])
# No UI descriptors survive; verified job media/preview leases do survive.
for fd in (6,9):
    try: os.fstat(fd)
    except OSError: sys.exit(91)
try: os.fstat(77)
except OSError: pass
else: sys.exit(91)
with (job/'calls').open('a') as f: f.write(json.dumps(args)+'\n')
if command=='apply':
    targets=list(source.glob('sf-probe-*.ndjson'))
    if len(targets)!=1 or targets[0].read_bytes()!=b'': sys.exit(92)
    if case=='timeout':
        time.sleep(2); (job/'loader-survived').write_text('not killed'); sys.exit(7)
    if case=='slow': time.sleep(1)
    if case=='apply_error': sys.exit(8)
    if case=='bad_token': token+=':'
    print(json.dumps({'command':command,'token':token}))
elif command=='diag-export':
    if case=='no_data':
        print(json.dumps({'status':'no_data','snapshot_created':False,'persisted':False})); sys.exit(3)
    if case=='pause_after_diag': (state/'media-paused').touch()
    if case=='replace_target': list(source.glob('sf-probe-*.ndjson'))[0].write_text('foreign')
    if case=='source_after_diag': source.rename(str(source)+'.old'); source.mkdir(mode=0o700)
    if case=='mismatch': token=token.rsplit(':',1)[0]+':4'
    (job/'frozen.ndjson').write_text('{"raw":true}\n')
    print(json.dumps({'command':command,'token':token,'status':'ram_only','snapshot_created':True,'persisted':False,'safe_to_persist':False}))
elif command=='probe-save':
    if case=='capture_changed':
        print(json.dumps({'command':command,'status':'capture_observed','snapshot_created':True,'persisted':False})); sys.exit(3)
    if case.startswith('save_error'):
        data={'command':command,'status':'persistence_failed','snapshot_created':True,'persisted':False,
              'error_stage':'append','error_operation':'write','error_reason':'syscall_failed',
              'error_errno':28,'error_errno_valid':True,'error_errno_text':'No space left on device',
              'write_attempted':True,'destination_may_have_changed':True,'destination_bytes_verified':False}
        if case=='save_error_marker': data.update(error_stage='marker',error_operation='fsync',error_errno=5,error_errno_text='Input/output error',destination_bytes_verified=True)
        if case=='save_error_logical': data.update(error_stage='readback',error_operation='compare',error_reason='bytes_mismatch',error_errno=0,error_errno_valid=False,error_errno_text='not applicable')
        if case=='save_error_missing': del data['destination_bytes_verified']
        if case=='save_error_enum': data['error_stage']='append\\nphase=persisted'
        if case=='save_error_errno': data['error_errno_valid']=False
        if case=='save_error_persisted': data['persisted']=True
        if case=='save_error_bool': data['write_attempted']='perhaps'
        if case=='save_error_quoted_bool': data['write_attempted']='true'
        if case=='save_error_quoted_errno': data['error_errno']='28'
        if case=='save_error_numeric_stage': data['error_stage']=123
        pathlib.Path(val('--probe-file')).write_bytes(b'partial')
        raw=json.dumps(data)
        if case=='save_error_duplicate': raw=raw[:-1]+',"error_stage":"marker"}'
        print(raw)
        print('file error stage='+str(data['error_stage'])+' errno='+str(data['error_errno']),file=sys.stderr)
        sys.exit(3)
    if args.count('--expect')!=1 or val('--expect')!=token or '--ack-idle-no-capture' not in args: sys.exit(93)
    if val('--source-id')!=str(source.stat().st_dev)+':'+str(source.stat().st_ino): sys.exit(94)
    pathlib.Path(val('--probe-file')).write_bytes((job/'frozen.ndjson').read_bytes())
    print(json.dumps({'command':command,'token':token,'persisted':True,'observed_capture_unchanged':True,'probe_contract':'idle-no-capture','backend_pending':True}))
else: sys.exit(95)
'''


def start(pid):
    return Path(f'/proc/{pid}/stat').read_text().rsplit(') ', 1)[1].split()[19]


def wait_file(path, timeout=6):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if path.is_file():
            return path.read_text()
        time.sleep(.025)
    raise AssertionError('deadline waiting for ' + str(path))


class CollectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = Path(tempfile.mkdtemp(prefix='sf-collector-host.', dir='/dev/shm'))
        cls.fixture = cls.workspace / 'source'
        code = (MFW / 'smartfilter-probe-launcher.c').read_text()
        # Fixture path substitution only; no weakened RAM, session or FD checks.
        code = code.replace('"/tmp/nx300m-sf-job."', '"/dev/shm/nx300m-sf-job."')
        code = code.replace(',19)', ',23)').replace('p+19', 'p+23').replace('p[19]', 'p[23]')
        code = code.replace('/tmp/nx300m-smartfilter-preview.lock', str(cls.workspace / 'preview.lock'))
        (cls.workspace / 'launcher.c').write_text(code)
        cls.binary = cls.workspace / 'launcher'
        subprocess.run(['gcc', '-std=gnu99', '-O2', '-Wall', '-Wextra', '-Werror',
                        '-I', str(BASE / 'runtime'),
                        '-DNX_SF_FIXTURE_SOURCE=' + json.dumps(str(cls.fixture)),
                        str(cls.workspace / 'launcher.c'), '-o', str(cls.binary)], check=True)
        # Compile-only libc wrapping in test copies; production has no fault
        # switch. A target fstatfs fault happens after successful O_EXCL, so its
        # empty-file change fact must survive collector capture_error.
        shim = '''#define _GNU_SOURCE
#include <sys/types.h>
#include <sys/stat.h>
#include <sys/statfs.h>
#include <errno.h>
static int test_target_fstatfs(int fd,struct statfs *fs) {
    struct stat st;
    if(fstat(fd,&st)) return -1;
    if(S_ISREG(st.st_mode)) { errno=EIO; return -1; }
    return fstatfs(fd,fs);
}
#define fstatfs test_target_fstatfs
'''
        (cls.workspace / 'launcher-target-fault.c').write_text(shim + code)
        cls.target_fault_binary = cls.workspace / 'launcher-target-fault'
        subprocess.run(['gcc', '-std=gnu99', '-O2', '-Wall', '-Wextra', '-Werror',
                        '-I', str(BASE / 'runtime'),
                        '-DNX_SF_FIXTURE_SOURCE=' + json.dumps(str(cls.fixture)),
                        str(cls.workspace / 'launcher-target-fault.c'), '-o', str(cls.target_fault_binary)], check=True)
        # Also compile the unmodified source with warnings as errors.
        subprocess.run(['gcc', '-std=gnu99', '-Wall', '-Wextra', '-Werror', '-fsyntax-only',
                        '-I', str(BASE / 'runtime'), str(MFW / 'smartfilter-probe-launcher.c')], check=True)
        for name in ('smartfilter-preview.sh', 'smartfilter-probe-collector.sh', 'panel-actions.sh'):
            subprocess.run(['/bin/sh', '-n', str(MFW / name)], check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.workspace)

    def setUp(self):
        self.source = self.fixture
        self.source.mkdir(mode=0o700)
        self.state = self.workspace / 'state'
        self.state.mkdir(mode=0o700)
        self.guard = self.workspace / 'uncertain'
        self.lock = self.workspace / 'preview.lock'
        self.job = Path(tempfile.mkdtemp(prefix='nx300m-sf-job.', dir='/dev/shm'))
        os.chmod(self.job, 0o700)
        shutil.copy2(self.binary, self.job / 'launcher')
        (self.job / 'loader.arm').write_text(FAKE_LOADER)
        os.chmod(self.job / 'loader.arm', 0o500)
        (self.job / 'blob.bin').write_bytes(b'fixture')
        script = (MFW / 'smartfilter-probe-collector.sh').read_text()
        script = script.replace('/tmp/nx300m-sf-job.', '/dev/shm/nx300m-sf-job.')
        script = script.replace('/perf', str(self.source))
        script = script.replace('/usr/bin/di-camera-app-nx300', os.readlink('/proc/self/exe'))
        script = script.replace('/mnt/mmc/bin/smartfilter_fixture.arm', os.readlink('/proc/self/exe'))
        script = script.replace('/tmp/nx300m-smartfilter-preview.uncertain', str(self.guard))
        script = script.replace('/tmp/nx300m-smartfilter-preview.lock', str(self.lock))
        (self.job / 'collector.sh').write_text(script)
        self.nonce = os.urandom(16).hex()
        self.env = dict(os.environ, NX_SF_PROBE_DELAY='0', NX_SF_PROBE_TIMEOUT='3', NX_SF_PROBE_FIXTURE='1',
                        SF_TEST_SOURCE=str(self.source), SF_TEST_STATE=str(self.state))

    def tearDown(self):
        shutil.rmtree(self.job)
        for path in (self.source, Path(str(self.source) + '.old'), self.state):
            if path.exists(): shutil.rmtree(path)
        for path in (self.guard, self.lock):
            path.unlink(missing_ok=True)

    def launch(self, case='ok', source_id=None, extra_env=None):
        env = dict(self.env, SF_TEST_CASE=case)
        env.update(extra_env or {})
        identity = source_id or f'{self.source.stat().st_dev}:{self.source.stat().st_ino}'
        begin = time.monotonic()
        p = subprocess.run([str(self.job / 'launcher'), '--launch', str(self.job), self.nonce,
                            str(self.source), identity, str(self.state), str(os.getpid()), start(os.getpid())],
                           env=env, capture_output=True, text=True, timeout=5)
        elapsed = time.monotonic() - begin
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn('accepted nonce=' + self.nonce, p.stdout)
        return elapsed

    def result(self):
        data = wait_file(self.job / 'result')
        return dict(line.split('=', 1) for line in data.splitlines())

    def calls(self):
        path = self.job / 'calls'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_success_one_attempt_frozen_raw_pending_not_safe(self):
        self.launch()
        result = self.result()
        self.assertEqual(result['phase'], 'persisted')
        calls = self.calls()
        self.assertEqual([x[0] for x in calls], ['apply', 'diag-export', 'probe-save'])
        self.assertIn('--probe-once', calls[0])
        self.assertNotIn('--diag-log', calls[1])
        self.assertEqual(Path(result['probe_file']).read_bytes(), b'{"raw":true}\n')
        self.assertTrue(json.loads((self.job / 'save.json').read_text())['backend_pending'])
        self.assertEqual(os.stat(result['probe_file']).st_mode & 0o777, 0o600)

    def test_ui_returns_before_apply(self):
        elapsed = self.launch('slow')
        self.assertLess(elapsed, .8)
        self.assertFalse((self.job / 'result').exists())
        self.assertEqual(self.result()['phase'], 'persisted')

    def test_apply_failure_no_retry(self):
        self.launch('apply_error')
        self.assertEqual(self.result()['reason'], 'apply_failed_8')
        self.assertEqual([x[0] for x in self.calls()], ['apply'])

    def test_no_data_exit3_is_not_ram_snapshot_success(self):
        self.launch('no_data')
        self.assertEqual(self.result()['phase'], 'no_data')
        self.assertEqual([x[0] for x in self.calls()], ['apply', 'diag-export'])
        self.assertEqual(list(self.source.iterdir())[0].stat().st_size, 0)

    def test_malformed_token_not_exported(self):
        self.launch('bad_token')
        self.assertEqual(self.result()['reason'], 'apply_token_invalid')
        self.assertEqual(len(self.calls()), 1)

    def test_export_token_mismatch_not_saved(self):
        self.launch('mismatch')
        self.assertEqual(self.result()['reason'], 'export_token_mismatch')
        self.assertEqual(len(self.calls()), 2)

    def test_pause_before_job_prevents_precreate_and_apply(self):
        (self.state / 'media-paused').touch()
        self.launch()
        self.assertEqual(self.result()['phase'], 'cancelled')
        self.assertEqual(self.calls(), [])
        self.assertEqual(list(self.source.iterdir()), [])

    def test_source_identity_mismatch_prevents_apply(self):
        self.launch(source_id='1:2')
        self.assertEqual(self.result()['phase'], 'cancelled')
        self.assertEqual(self.calls(), [])

    def test_pause_after_freeze_retains_ram_no_save(self):
        self.launch('pause_after_diag')
        self.assertEqual(self.result()['phase'], 'cancelled')
        self.assertTrue((self.job / 'frozen.ndjson').exists())
        self.assertEqual(len(self.calls()), 2)

    def test_source_replacement_after_freeze_no_save(self):
        self.launch('source_after_diag')
        self.assertEqual(self.result()['phase'], 'cancelled')
        self.assertEqual(len(self.calls()), 2)

    def test_target_replacement_no_save(self):
        self.launch('replace_target')
        self.assertEqual(self.result()['reason'], 'precreated_target_changed')
        self.assertEqual(len(self.calls()), 2)

    def test_capture_change_rejection_not_rearmed(self):
        self.launch('capture_changed')
        result = self.result()
        self.assertEqual(result['phase'], 'cancelled')
        self.assertEqual(result['reason'], 'capture_observed')
        self.assertTrue((self.job / 'frozen.ndjson').exists())
        self.assertEqual([x[0] for x in self.calls()], ['apply', 'diag-export', 'probe-save'])

    def save_error_result(self, case):
        self.launch(case)
        result = self.result()
        self.assertEqual(result['phase'], 'failed')
        self.assertEqual(result['reason'], 'save_failed_3')
        self.assertEqual([x[0] for x in self.calls()], ['apply', 'diag-export', 'probe-save'])
        self.assertTrue((self.job / 'frozen.ndjson').exists())
        self.assertEqual(Path(result['probe_file']).read_bytes(), b'partial')
        return result

    def test_save_io_error_is_retained_without_retry(self):
        result = self.save_error_result('save_error')
        self.assertEqual(result['error_stage'], 'append')
        self.assertEqual(result['error_operation'], 'write')
        self.assertEqual(result['error_reason'], 'syscall_failed')
        self.assertEqual(result['error_errno'], '28')
        self.assertEqual(result['error_errno_valid'], 'true')
        self.assertEqual(result['snapshot_created'], 'true')
        self.assertEqual(result['persisted'], 'false')
        self.assertEqual(result['write_attempted'], 'true')
        self.assertEqual(result['destination_may_have_changed'], 'true')
        self.assertEqual(result['destination_bytes_verified'], 'false')
        self.assertEqual(Path(result['error_receipt']), self.job / 'save.json')
        self.assertIn('errno=28', Path(result['error_stderr']).read_text())

    def test_marker_error_does_not_discard_verified_bytes_fact(self):
        result = self.save_error_result('save_error_marker')
        self.assertEqual(result['error_stage'], 'marker')
        self.assertEqual(result['error_errno'], '5')
        self.assertEqual(result['destination_bytes_verified'], 'true')
        self.assertEqual(result['persisted'], 'false')

    def test_logical_error_has_no_stale_errno(self):
        result = self.save_error_result('save_error_logical')
        self.assertEqual(result['error_reason'], 'bytes_mismatch')
        self.assertEqual(result['error_errno'], '0')
        self.assertEqual(result['error_errno_valid'], 'false')

    def test_missing_error_fact_falls_back_without_guessing(self):
        self.assertNotIn('error_stage', self.save_error_result('save_error_missing'))

    def test_duplicate_error_field_falls_back_without_guessing(self):
        self.assertNotIn('error_stage', self.save_error_result('save_error_duplicate'))

    def test_invalid_error_enum_cannot_inject_ram_result(self):
        self.assertNotIn('error_stage', self.save_error_result('save_error_enum'))

    def test_inconsistent_error_errno_falls_back_without_guessing(self):
        self.assertNotIn('error_stage', self.save_error_result('save_error_errno'))

    def test_failed_command_cannot_claim_persisted(self):
        self.assertNotIn('error_stage', self.save_error_result('save_error_persisted'))

    def test_invalid_error_boolean_falls_back_without_guessing(self):
        self.assertNotIn('error_stage', self.save_error_result('save_error_bool'))

    def test_quoted_error_boolean_falls_back_without_guessing(self):
        self.assertNotIn('error_stage', self.save_error_result('save_error_quoted_bool'))

    def test_quoted_error_errno_falls_back_without_guessing(self):
        self.assertNotIn('error_stage', self.save_error_result('save_error_quoted_errno'))

    def test_numeric_error_enum_falls_back_without_guessing(self):
        self.assertNotIn('error_stage', self.save_error_result('save_error_numeric_stage'))

    def test_preview_lock_contention_no_apply(self):
        with self.lock.open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.launch()
            self.assertEqual(self.result()['reason'], 'preview_busy')
            self.assertEqual(self.calls(), [])

    def test_media_lock_contention_no_apply(self):
        with (self.state / 'media-io.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.launch()
            self.assertEqual(self.result()['reason'], 'media_paused_or_draining')
            self.assertEqual(self.calls(), [])

    def test_timeout_retains_media_until_writer_really_exits(self):
        self.launch('timeout', extra_env={'NX_SF_PROBE_TIMEOUT': '1'})
        result = self.result()
        self.assertEqual(result['phase'], 'uncertain')
        self.assertTrue(self.guard.exists())
        for path in (self.lock, self.state / 'media-io.lock'):
            with path.open('w') as lock:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.assertEqual(wait_file(self.job / 'loader-survived', timeout=4), 'not killed')
        time.sleep(.1)
        for path in (self.lock, self.state / 'media-io.lock'):
            with path.open('w') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.assertEqual([x[0] for x in self.calls()], ['apply'])

    def test_prepare_rejects_duplicate_and_symlink(self):
        identity = f'{self.source.stat().st_dev}:{self.source.stat().st_ino}'
        command = [str(self.binary), '--prepare', str(self.source), identity, self.nonce]
        target = self.source / f'sf-probe-{self.nonce}.ndjson'
        target.symlink_to(self.workspace / 'nonexistent')
        self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
        target.unlink()
        self.assertEqual(subprocess.run(command, capture_output=True).returncode, 0)
        self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)

    def test_prepare_failure_retains_first_error_without_apply(self):
        os.chmod(self.source, 0o770)
        self.launch()
        result = self.result()
        self.assertEqual((result['phase'], result['reason']), ('failed', 'prepare_failed_1'))
        self.assertEqual((result['error_stage'], result['error_operation'], result['error_reason']),
                         ('destination_parent', 'validate', 'unsafe_permissions'))
        self.assertEqual(result['error_errno'], '0')
        self.assertEqual(result['error_errno_valid'], 'false')
        for fact in ('snapshot_created', 'persisted', 'write_attempted',
                     'destination_may_have_changed', 'destination_bytes_verified'):
            self.assertEqual(result[fact], 'false')
        self.assertEqual(self.calls(), [])
        self.assertEqual(list(self.source.iterdir()), [])
        receipt = json.loads((self.job / 'prepare.json').read_text())
        self.assertEqual(receipt['media_owner_policy'], 'posix_owner')
        self.assertIsNone(receipt['media_target_uid'])
        self.assertEqual(Path(result['error_stderr']), self.job / 'prepare.stderr')

    def test_prepare_target_fs_failure_reports_created_empty_not_saved(self):
        shutil.copy2(self.target_fault_binary, self.job / 'launcher')
        self.launch()
        result = self.result()
        self.assertEqual((result['phase'], result['reason']), ('failed', 'prepare_failed_1'))
        self.assertEqual((result['error_stage'], result['error_operation'], result['error_errno']),
                         ('destination', 'fstatfs', '5'))
        self.assertEqual(result['error_errno_valid'], 'true')
        self.assertEqual(result['destination_may_have_changed'], 'true')
        for fact in ('snapshot_created', 'persisted', 'write_attempted', 'destination_bytes_verified'):
            self.assertEqual(result[fact], 'false')
        self.assertEqual(self.calls(), [])
        target = self.source / f'sf-probe-{self.nonce}.ndjson'
        self.assertEqual(target.read_bytes(), b'')
        receipt = json.loads((self.job / 'prepare.json').read_text())
        self.assertIsInstance(receipt['media_target_uid'], int)
        self.assertIsNone(receipt['media_target_fs_type'])
        self.assertEqual(receipt['media_owner_policy'], 'posix_owner')
        self.assertIn('op=fstatfs', (self.job / 'prepare.stderr').read_text())

    def test_prepare_duplicate_errno_and_success_two_field_protocol(self):
        identity = f'{self.source.stat().st_dev}:{self.source.stat().st_ino}'
        command = [str(self.binary), '--prepare', str(self.source), identity, self.nonce]
        first = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(first.returncode, 0, first.stderr)
        target = self.source / f'sf-probe-{self.nonce}.ndjson'
        self.assertEqual(first.stdout.split(), [str(target), f'{target.stat().st_dev}:{target.stat().st_ino}'])
        self.assertEqual(first.stderr, '')
        duplicate = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(duplicate.returncode, 1)
        receipt = json.loads(duplicate.stdout)
        self.assertEqual((receipt['error_stage'], receipt['error_operation'], receipt['error_errno']),
                         ('destination', 'openat', 17))
        self.assertFalse(receipt['destination_may_have_changed'])
        self.assertFalse(receipt['write_attempted'])
        self.assertEqual(len(duplicate.stderr.splitlines()), 1)

    def test_launcher_drops_session_fds_and_parent_death_signal(self):
        # An extra UI FD and PDEATHSIG are installed before native exec. The
        # job reports its own shell state after the launcher has returned.
        script = '''#!/bin/sh
"$PWD/launcher" --ready "$PWD" "$1" || exit 1
/usr/bin/python3 -c 'import ctypes,json,os,pathlib; v=ctypes.c_int(); ctypes.CDLL(None).prctl(2,ctypes.byref(v),0,0,0); pathlib.Path("isolation").write_text(json.dumps({"pid":os.getpid(),"sid":os.getsid(0),"pg":os.getpgrp(),"pdeath":v.value,"cwd":os.getcwd(),"fds":{x:os.readlink("/proc/self/fd/"+x) for x in os.listdir("/proc/self/fd") if os.path.exists("/proc/self/fd/"+x)}}))'
'''
        (self.job / 'collector.sh').write_text(script)
        fd = os.open(self.workspace / 'ui-held', os.O_RDWR | os.O_CREAT, 0o600)
        os.dup2(fd, 77); os.close(fd)
        def preexec():
            ctypes.CDLL(None).prctl(1, signal.SIGTERM, 0, 0, 0)
        try:
            identity = f'{self.source.stat().st_dev}:{self.source.stat().st_ino}'
            p = subprocess.run([str(self.job / 'launcher'), '--launch', str(self.job), self.nonce,
                                str(self.source), identity, str(self.state), str(os.getpid()), start(os.getpid())],
                               pass_fds=(77,), preexec_fn=preexec, capture_output=True, text=True, timeout=5)
            self.assertEqual(p.returncode, 0, p.stderr)
            info = json.loads(wait_file(self.job / 'isolation'))
            self.assertNotEqual(info['sid'], os.getsid(0))
            self.assertNotEqual(info['pg'], os.getpgrp())
            self.assertEqual(info['pdeath'], 0)
            self.assertNotIn('77', info['fds'])
            self.assertEqual(info['fds']['0'], '/dev/null')
            self.assertTrue(info['fds']['1'].endswith('/collector.log'))
            self.assertTrue(info['fds']['2'].endswith('/collector.log'))
            self.assertEqual(info['cwd'], str(self.job))
        finally:
            os.close(77)

    def test_missing_ready_does_not_report_accepted(self):
        (self.job / 'collector.sh').write_text('#!/bin/sh\nexit 0\n')
        identity = f'{self.source.stat().st_dev}:{self.source.stat().st_ino}'
        p = subprocess.run([str(self.binary), '--launch', str(self.job), self.nonce,
                            str(self.source), identity, str(self.state), str(os.getpid()), start(os.getpid())],
                           capture_output=True, text=True, timeout=5)
        self.assertNotEqual(p.returncode, 0)
        self.assertNotIn('accepted', p.stdout)

    def test_job_survives_terminated_ui_process_group(self):
        # The UI has its own session and stays alive after receiving ACCEPTED.
        # Terminate that entire UI process group while apply is still running.
        identity = f'{self.source.stat().st_dev}:{self.source.stat().st_ino}'
        args = [str(self.job / 'launcher'), '--launch', str(self.job), self.nonce,
                str(self.source), identity, str(self.state), str(os.getpid()), start(os.getpid())]
        helper = '''import ctypes, signal, subprocess, sys, time
ctypes.CDLL(None).prctl(1, signal.SIGTERM, 0, 0, 0)
p=subprocess.run(sys.argv[1:],capture_output=True,text=True)
print(p.stdout,flush=True)
sys.stdout.flush()
time.sleep(10)
'''
        env = dict(self.env, SF_TEST_CASE='slow')
        ui = subprocess.Popen(['/usr/bin/python3', '-I', '-c', helper, *args],
                              env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, start_new_session=True)
        try:
            self.assertIn('accepted nonce=', ui.stdout.readline())
            self.assertFalse((self.job / 'result').exists())
            os.killpg(ui.pid, signal.SIGTERM)
            ui.wait(timeout=3)
            self.assertEqual(self.result()['phase'], 'persisted')
        finally:
            if ui.poll() is None:
                os.killpg(ui.pid, signal.SIGTERM)
                ui.wait(timeout=3)
            ui.stdout.close(); ui.stderr.close()

    def test_panel_no_data_does_not_claim_ram_evidence(self):
        runtime = self.workspace / 'panel-runtime'
        runtime.mkdir(mode=0o700)
        try:
            common = 'ROOT=' + str(runtime) + '\nSTATE=' + str(self.state) + '\nfail() { printf "%s\\n" "$*" >&2; exit 1; }\n'
            (runtime / 'common.sh').write_text(common)
            (runtime / 'smartfilter-preview.sh').write_text(
                '#!/bin/sh\nprintf \'%s\\n\' \'{"status":"no_data","snapshot_created":false,"persisted":false}\'\nexit 3\n')
            env = dict(self.env, NX300M_MOD_ROOT=str(runtime), NX300M_MOD_SOURCE_ROOT=str(self.source),
                       NX300M_MOD_SOURCE_ID=f'{self.source.stat().st_dev}:{self.source.stat().st_ino}')
            p = subprocess.run(['/bin/sh', str(MFW / 'panel-actions.sh'), 'preview-diag-export'],
                               env=env, capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn('未创建 RAM 快照', p.stdout)
            self.assertNotIn('RAM 快照已保留', p.stdout)
            self.assertNotIn('已创建 RAM 快照', p.stdout)
        finally:
            shutil.rmtree(runtime)

    def test_panel_export_failure_preserves_io_receipt(self):
        runtime = self.workspace / 'panel-runtime'
        runtime.mkdir(mode=0o700)
        receipt = {'command': 'diag-export', 'status': 'export_failed',
                   'snapshot_created': True, 'persisted': False,
                   'error_stage': 'append', 'error_operation': 'write',
                   'error_reason': 'syscall_failed', 'error_errno': 28,
                   'error_errno_valid': True, 'error_errno_text': 'No space left on device',
                   'write_attempted': True, 'destination_may_have_changed': True,
                   'destination_bytes_verified': False}
        try:
            (runtime / 'common.sh').write_text(
                'ROOT=' + str(runtime) + '\nSTATE=' + str(self.state) +
                '\nfail() { printf "%s\\n" "$*" >&2; exit 1; }\n')
            (runtime / 'smartfilter-preview.sh').write_text(
                '#!/bin/sh\nprintf \'%s\\n\' \'' + json.dumps(receipt) + "'\nexit 3\n")
            env = dict(self.env, NX300M_MOD_ROOT=str(runtime), NX300M_MOD_SOURCE_ROOT=str(self.source),
                       NX300M_MOD_SOURCE_ID=f'{self.source.stat().st_dev}:{self.source.stat().st_ino}')
            p = subprocess.run(['/bin/sh', str(MFW / 'panel-actions.sh'), 'preview-diag-export'],
                               env=env, capture_output=True, text=True)
            self.assertNotEqual(p.returncode, 0)
            self.assertIn(json.dumps(receipt), p.stderr)
            self.assertNotIn('诊断已保存', p.stdout)
            self.assertNotIn('RAM 快照已保留', p.stdout + p.stderr)
        finally:
            shutil.rmtree(runtime)

    def test_menu_and_legacy_wrapper_fail_closed(self):
        menu = (MFW / 'gui_preview.NX300M').read_text()
        self.assertIn('preview-probe-once', menu)
        for legacy in ('preview-enable\n', 'preview-monochrome', 'preview-sync'):
            self.assertNotIn(legacy, menu)
        for action in ('enable', 'enable-stock', 'enable-s2', 'sync', 'test-monochrome', 'apply'):
            p = subprocess.run(['/bin/sh', str(MFW / 'smartfilter-preview.sh'), action], capture_output=True, text=True)
            self.assertNotEqual(p.returncode, 0)
            self.assertIn('探针包拒绝', p.stderr)
        panel = (MFW / 'panel-actions.sh').read_text()
        self.assertLess(panel.index('exec /bin/sh "$script" probe-once'), panel.index('exec 6>'))
        self.assertIn('snapshot_created', panel)
        self.assertIn('没有驻留诊断数据', panel)


if __name__ == '__main__':
    unittest.main(verbosity=2)
