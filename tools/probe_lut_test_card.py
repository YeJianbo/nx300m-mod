"""Reproduce the observed Wi-Fi request mismatch using the production shell worker and a fake tool."""
import json
from pathlib import Path
import subprocess

from prepare_lut_test_card import build, OUT


GUEST = r'''
import json, os, pathlib, subprocess, sys, tempfile
source = pathlib.Path(sys.argv[1]).read_text()
worker = source.split("worker='", 1)[1].split("'\nnice -n", 1)[0]
rows = []
with tempfile.TemporaryDirectory(prefix='nx300m-wifi-request-') as folder:
    base = pathlib.Path(folder)
    commands = base / 'commands'
    commands.mkdir()
    timeout = commands / 'timeout'
    timeout.write_text('#!/bin/sh\n[ "$1" != -t ] || shift\nexec /usr/bin/timeout "$@"\n')
    timeout.chmod(0o700)
    for request_id, expected in [('old-run', 1), ('new-run', 0)]:
        root = base / request_id
        root.mkdir()
        (root / 'WIFI_MEM_PROFILE').write_text('manual-lens-wifi\n')
        (root / 'WIFI_MEM_REQUEST').write_text(request_id + ' apply\n')
        tool = root / 'manual-lens-wifi-mem.arm'
        tool.write_text('#!/bin/sh\nprintf "%s\\n" "$1" >> "' + str(root / 'calls') + '"\nprintf \'{"ok":true}\\n\'\n')
        tool.chmod(0o700)
        identity = subprocess.check_output(['stat', '-c', '%d:%i', str(root)], text=True).strip()
        ram = base / ('ram-' + request_id)
        env = dict(os.environ, PATH=str(commands) + ':' + os.environ['PATH'])
        result = subprocess.run(['/bin/sh', '-c', worker, 'sh', str(root), str(ram), identity, 'new-run'], env=env, capture_output=True, text=True)
        session = (ram / 'session.txt').read_text()
        calls = (root / 'calls').read_text().splitlines()
        assert result.returncode == expected, (result.returncode, result.stderr)
        if expected:
            assert 'failure=request_mismatch' in session and calls == ['status']
            assert (root / 'WIFI_MEM_REQUEST').exists()
        else:
            assert calls == ['status', 'apply', 'status'] and 'action_exit=0' in session
            assert not (root / 'WIFI_MEM_REQUEST').exists()
        rows.append({'request_id': request_id, 'worker_exit': result.returncode, 'tool_calls': calls})
print(json.dumps({'passed': True, 'production_shell_worker': True, 'fake_camera_tool': True, 'checks': rows}))
'''


def main():
    project = Path(__file__).resolve().parents[1]
    source = project / 'mod/deployment/mfw/autoexec-lens-wifi-mem.sh'
    guest_source = '/mnt/' + source.drive[0].lower() + source.as_posix()[2:]
    run = subprocess.run(['wsl.exe', '-d', 'Debian', '-e', 'python3', '-c', GUEST, guest_source], capture_output=True, text=True)
    if run.returncode:
        raise RuntimeError(f'Wi-Fi request worker probe exit {run.returncode}: {run.stderr}')
    report = json.loads(run.stdout)
    package, _ = build(custom_shortcut=True, pending_wifi='apply')
    run_id = package['nx300m-lens-debug/WIFI_MEM_RUN_ID'].decode().strip()
    assert package['nx300m-lens-debug/WIFI_MEM_REQUEST'] == (run_id + ' apply\n').encode()
    assert 'nx300m-mod/NO_PANEL_SHORTCUT' not in package
    assert b'export NX_REMOTE_CUSTOM_KEY=1 NX_REMOTE_PANEL_SHORTCUT=0' in package['autoexec.sh']
    report.update(pending_request_rebound=True, custom_enabled=True, legacy_fn_disabled=True,
                  real_camera_verified=False, sd_card_accessed=False)
    (OUT / 'request-migration-report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
