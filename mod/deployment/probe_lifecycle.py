"""Host-native fixture checks of SD scripts, in a private PID/network namespace.

The fixture replaces remote only inside tmpfs. This is not an ARM/camera test.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

PROJECT = Path(__file__).resolve().parents[2]
OUT = PROJECT / 'outputs/deployment/sd-bundle'
FIXTURE = r'''
#include <stdio.h>
#include <stdlib.h>
#include <signal.h>
#include <unistd.h>
static volatile sig_atomic_t done;
static void stop(int sig) { (void)sig; done=1; }
int main(int argc, char **argv) {
    signal(SIGTERM,stop); signal(SIGINT,stop);
    for (int i=1;i<argc;i++) fprintf(stderr,"ARG %s\n",argv[i]);
    if (!getenv("NX_PROBE_NO_READY")) fputs("NX_REMOTE_READY fixture\n",stderr);
    while (!done) pause();
    return 0;
}
'''


def wait_until(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.05)
    raise AssertionError('Timed out waiting for fixture lifecycle')


def alive(pid):
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(') ', 1)[1].split()
        return fields[0] != 'Z'
    except FileNotFoundError:
        return False


def main():
    results = []
    with tempfile.TemporaryDirectory(prefix='nx-sd-probe-') as name:
        workspace = Path(name)
        subprocess.run(['mount', '-t', 'tmpfs', '-o', 'mode=0700,nosuid,nodev', 'tmpfs', name], check=True)
        try:
            root = workspace / 'nx300m-mod'
            shutil.copytree(OUT / 'nx300m-mod', root)
            source = workspace / 'fixture.c'
            source.write_text(FIXTURE)
            binary = root / 'bin/nx-remote-controller.arm'
            subprocess.run(['gcc', '-Wall', '-Wextra', '-Werror', str(source), '-o', str(binary)], check=True)
            os.chmod(root / 'bin/nx-quickshare-armv7', 0o755)
            state = workspace / 'state'
            state.mkdir()
            env = dict(os.environ, NX300M_MOD_ROOT=str(root), NX_STATE_DIR=str(state), NX_START_TIMEOUT='5', NX_STOP_TIMEOUT='4')
            def invoke(action, expected=0, extra=None):
                result = subprocess.run(['/bin/sh', str(root / f'{action}.sh')], env=dict(env, **(extra or {})), capture_output=True, text=True, timeout=20)
                if result.returncode != expected:
                    raise AssertionError(f'{action}: code={result.returncode} stdout={result.stdout!r} stderr={result.stderr!r}')
                return result
            def record():
                return (state / 'remote.pid').read_text().splitlines()
            def lease_released():
                return subprocess.run(['flock', '-n', str(state / 'service.lock'), 'true'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
            for path in root.glob('*.sh'):
                subprocess.run(['/bin/sh', '-n', str(path)], check=True)
            results.append({'check': 'shell_syntax', 'passed': True})
            first = invoke('start')
            owner = record()
            assert '127.0.0.1:8781' in first.stdout
            assert '--quickshare-binary' in (state / 'remote.log').read_text()
            invoke('start')
            assert record() == owner
            # Concurrent starts must all reuse the same service.
            calls = [subprocess.Popen(['/bin/sh', str(root / 'start.sh')], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(3)]
            for process in calls:
                stdout, stderr = process.communicate(timeout=20)
                assert process.returncode == 0, (stdout, stderr)
            assert record() == owner
            results.append({'check': 'ready_default_listen_and_single_instance', 'passed': True})
            invoke('stop')
            wait_until(lambda: not alive(int(owner[0])) and lease_released())
            invoke('stop')
            results.append({'check': 'owned_term_and_idempotent_stop', 'passed': True})
            # A real unrelated process with a matching PID/start tick is protected
            # by the executable identity; boot/start mismatches protect reuse.
            unrelated = subprocess.Popen(['/bin/sleep', '60'])
            try:
                start = Path(f'/proc/{unrelated.pid}/stat').read_text().rsplit(') ', 1)[1].split()[19]
                boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
                for label, tick, boot_id, inode in (
                    ('wrong_executable', start, boot, owner[4]),
                    ('stale_starttime', str(int(start) + 1), boot, owner[4]),
                    ('previous_boot', start, 'previous-boot', owner[4])):
                    (state / 'remote.pid').write_text('\n'.join([str(unrelated.pid), tick, boot_id, str(binary), inode]) + '\n')
                    invoke('stop')
                    assert unrelated.poll() is None
                    results.append({'check': label + '_ignored', 'passed': True})
            finally:
                unrelated.terminate()
                unrelated.wait()
            invoke('start')
            owner = record()
            moved = workspace / 'removed-card'
            root.rename(moved)
            root.mkdir()
            (root / 'start.sh').write_text('# replacement directory\n')
            wait_until(lambda: not alive(int(owner[0])) and lease_released())
            results.append({'check': 'root_inode_replacement_stops_original_remote', 'passed': True})
            (root / 'start.sh').unlink()
            root.rmdir()
            moved.rename(root)
            # Missing readiness is a failure and the supervisor reaps its child.
            invoke('start', expected=1, extra={'NX_PROBE_NO_READY': '1'})
            wait_until(lease_released)
            assert not (state / 'remote.pid').exists()
            results.append({'check': 'missing_ready_fails_and_cleans_owned_child', 'passed': True})
        finally:
            subprocess.run(['umount', name], check=True)
    report = {'scope': 'host-native remote fixture; fresh PID/mount/network/IPC namespace and tmpfs; no camera or existing emulator touched',
              'arm_execution_verified': False, 'checks': results}
    (OUT / 'probe-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
