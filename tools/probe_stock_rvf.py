"""Observe the genuine stock Wi-Fi app in a disposable, read-only-root namespace."""
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time

PROJECT = Path(__file__).resolve().parents[1]
GUEST = '/opt/nx300m-lab/stock-1.15'
OUTPUT = PROJECT / 'outputs/nxconnect/stock-rvf'


def linux_path(path):
    path = path.resolve()
    return '/mnt/' + path.drive[0].lower() + path.as_posix()[2:]


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def worker(output, seconds, device_substitutes):
    output = Path(output)
    report = {'stock_program': '/usr/bin/smart-wifi-app-nx300',
              'stock_root_read_only': True, 'separate_mount_net_ipc_pid_namespaces': True,
              'physical_devices_operated': False, 'pairing_confirmation_injected': False,
              'hardware_preloads': [], 'listener_samples': []}
    report['interfaces'] = subprocess.run(['ip', '-brief', 'link'], capture_output=True, text=True).stdout
    xvfb_log = (output / 'xvfb.log').open('wb')
    xvfb = subprocess.Popen(['Xvfb', ':99', '-screen', '0', '480x800x24', '-nolisten', 'tcp',
                             '-ac', '-fbdir', str(output / 'framebuffer')],
                            stdout=xvfb_log, stderr=subprocess.STDOUT)
    app = None
    try:
        for _ in range(30):
            if Path('/tmp/.X11-unix/X99').is_socket():
                break
            if xvfb.poll() is not None:
                raise RuntimeError('Xvfb exited with code ' + str(xvfb.returncode))
            time.sleep(.1)
        else:
            raise RuntimeError('Xvfb did not create its socket')
        guest_socket = Path(GUEST + '/tmp/.X11-unix/X99')
        guest_socket.parent.mkdir(exist_ok=True)
        guest_socket.touch()
        subprocess.run(['mount', '--bind', '/tmp/.X11-unix/X99', str(guest_socket)], check=True)
        environment = dict(os.environ, DISPLAY=':99', LC_ALL='C')
        with (output / 'preferences.stdout.log').open('wb') as out, (output / 'preferences.stderr.log').open('wb') as err:
            pref = subprocess.run(['sh', str(PROJECT / 'runtime/bootstrap_preferences.sh'), GUEST],
                                  stdout=out, stderr=err, env=environment, timeout=15)
        report['preferences_exit_code'] = pref.returncode
        if pref.returncode:
            raise RuntimeError('Private stock preference initialization failed')
        qemu_arguments = []
        if device_substitutes:
            for name in ('gpio_preview', 'device_preview'):
                shutil.copyfile(PROJECT / ('outputs/firmware-runtime/' + name + '.so'),
                                GUEST + '/tmp/' + name + '.so')
                report['hardware_preloads'].append('/tmp/' + name + '.so')
            qemu_arguments = ['-E', 'LD_PRELOAD=' + ':'.join(report['hardware_preloads'])]
        command = ['chroot', '--userspec=65534:65534', GUEST, '/qemu-arm-static', '-cpu', 'cortex-a9',
                   '-strace', '-L', '/', *qemu_arguments, '/usr/bin/smart-wifi-app-nx300']
        report['app_command'] = command
        with (output / 'app.stdout.log').open('wb') as out, (output / 'app.stderr.log').open('wb') as err:
            app = subprocess.Popen(command, stdout=out, stderr=err, env=environment)
            started = time.monotonic()
            while time.monotonic() - started < seconds:
                listeners = []
                for table in ('/proc/net/tcp', '/proc/net/tcp6'):
                    for line in Path(table).read_text().splitlines()[1:]:
                        fields = line.split()
                        if fields[3] == '0A':
                            listeners.append({'table': table, 'local': fields[1],
                                              'port': int(fields[1].split(':')[1], 16)})
                report['listener_samples'].append({'elapsed_s': round(time.monotonic() - started, 3),
                                                   'app_exit_code': app.poll(), 'listeners': listeners})
                if app.poll() is not None:
                    break
                time.sleep(.25)
            report['app_exit_before_cleanup'] = app.poll()
            report['observation_seconds'] = round(time.monotonic() - started, 3)
            if app.poll() is None:
                app.terminate()
                try:
                    app.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    app.kill()
                    app.wait()
            report['app_exit_after_cleanup'] = app.returncode
        report['port_7676_observed'] = any(item['port'] == 7676 for sample in report['listener_samples']
                                         for item in sample['listeners'])
    except Exception as exc:
        report['probe_error'] = repr(exc)
    finally:
        if app is not None and app.poll() is None:
            app.kill()
            app.wait()
        xvfb.terminate()
        try:
            xvfb.wait(timeout=2)
        except subprocess.TimeoutExpired:
            xvfb.kill()
            xvfb.wait()
        xvfb_log.close()
        save(output / 'report.json', report)
    return 2 if 'probe_error' in report else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', type=Path)
    parser.add_argument('--seconds', type=int, default=10)
    parser.add_argument('--device-substitutes', action='store_true',
                        help='Retry with existing synthetic GPIO/device backends; no Wi-Fi emulation')
    args = parser.parse_args()
    if not 1 <= args.seconds <= 20:
        parser.error('--seconds must be 1..20')
    if args.worker:
        return worker(args.worker, args.seconds, args.device_substitutes)
    selected_output = OUTPUT / 'device-substitutes' if args.device_substitutes else OUTPUT
    selected_output.mkdir(parents=True, exist_ok=True)
    (selected_output / 'framebuffer').mkdir(exist_ok=True)
    output = linux_path(selected_output)
    inner = '\n'.join([
        'set -eu', 'mount --make-rprivate /',
        'mount --bind ' + shlex.quote(GUEST) + ' ' + shlex.quote(GUEST),
        'mount -o remount,bind,ro ' + shlex.quote(GUEST),
        'mount -t tmpfs -o mode=1777,nosuid,nodev tmpfs /tmp',
        'mount -t tmpfs -o mode=1777,nosuid,nodev tmpfs ' + shlex.quote(GUEST + '/tmp'),
        'mount -t tmpfs -o mode=0777,nosuid,nodev tmpfs ' + shlex.quote(GUEST + '/var/run'),
        'mount -t proc -o nosuid,nodev,noexec proc ' + shlex.quote(GUEST + '/proc'),
        'ip link set lo up',
        'exec python3 ' + shlex.quote(linux_path(Path(__file__))) + ' --worker ' + shlex.quote(output) +
        ' --seconds ' + str(args.seconds) + (' --device-substitutes' if args.device_substitutes else ''),
    ])
    command = ['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'timeout', '--kill-after=2s', '40s',
               'unshare', '--mount', '--net', '--ipc', '--pid', '--fork', '--kill-child', 'sh', '-c', inner]
    save(selected_output / 'command.json', command)
    with (selected_output / 'probe.stdout.log').open('wb') as out, (selected_output / 'probe.stderr.log').open('wb') as err:
        result = subprocess.run(command, stdout=out, stderr=err, timeout=48)
    save(selected_output / 'execution.json', {'exit_code': result.returncode})
    print(json.dumps({'exit_code': result.returncode, 'report': str(selected_output / 'report.json')}))
    return result.returncode


if __name__ == '__main__':
    sys.exit(main())
