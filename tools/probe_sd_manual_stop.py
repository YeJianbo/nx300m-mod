"""Exercise production SD scripts with an isolated native daemon fixture.

This checks manual-stop/automatic-start coordination, not ARM, HTTP or suspend.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def worker():
    with tempfile.TemporaryDirectory(prefix='nx300m-manual-stop-') as directory:
        root = Path(directory)
        for name in ('common.sh', 'start.sh', 'stop.sh', 'service.sh'):
            (root/name).write_text((ROOT/'mod/deployment'/name).read_text(), newline='\n')
        (root/'bin').mkdir()
        (root/'web_root').mkdir()
        (root/'web_root/index.html').write_text('fixture')
        source = root/'fixture.c'
        source.write_text('#include <stdio.h>\n#include <unistd.h>\n'
                          'int main(void){puts("NX_REMOTE_READY fixture");'
                          'fflush(stdout);for(;;)pause();}\n')
        subprocess.run(['gcc', str(source), '-o', str(root/'bin/nx-remote-controller.arm')], check=True)
        shutil.copyfile('/bin/true', root/'bin/nx-quickshare-armv7')
        os.chmod(root/'bin/nx-quickshare-armv7', 0o700)
        env = dict(os.environ, NX300M_MOD_ROOT=str(root), NX_START_TIMEOUT='8', NX_STOP_TIMEOUT='4')
        env.pop('NX300M_AUTO_START', None)
        env.pop('NX_STATE_DIR', None)
        records = []

        def run(script, automatic=False):
            settings = dict(env)
            if automatic:
                settings['NX300M_AUTO_START'] = '1'
            result = subprocess.run(['/bin/sh', str(root/script)], env=settings,
                                    capture_output=True, text=True, timeout=20)
            records.append({'script': script, 'automatic': automatic,
                            'exit': result.returncode, 'stdout': result.stdout.strip(),
                            'stderr': result.stderr.strip()})
            if result.returncode:
                raise RuntimeError(records[-1])

        state = root/'.state'
        try:
            run('stop.sh')
            assert (state/'manual-stop').is_file()
            run('start.sh', automatic=True)
            assert not (state/'remote.pid').exists()
            run('start.sh')
            assert not (state/'manual-stop').exists()
            first = (state/'remote.pid').read_text()
            run('start.sh', automatic=True)
            assert (state/'remote.pid').read_text() == first
            run('stop.sh')
            run('start.sh', automatic=True)
            assert (state/'manual-stop').is_file() and not (state/'remote.pid').exists()
            run('start.sh')
            second = (state/'remote.pid').read_text()
            assert second != first and not (state/'manual-stop').exists()
            run('stop.sh')
            # An old boot's explicit stop must not permanently disable startup.
            (state/'manual-stop').write_text('previous-boot-fixture\n')
            run('start.sh', automatic=True)
            assert not (state/'manual-stop').exists() and (state/'remote.pid').exists()
            run('stop.sh')
            # The production supervisor polls once per second before releasing
            # its own record/lock. Leave its temporary filesystem until exit.
            time.sleep(1.2)
        finally:
            if (state/'remote.pid').exists():
                run('stop.sh')
                time.sleep(1.2)
        return {'passed': True, 'production_scripts_unmodified': True,
                'native_daemon_fixture': True, 'arm_execution': False,
                'http_verified': False, 'suspend_verified': False,
                'stop_before_first_start_respected': True,
                'automatic_start_preserved_running_identity': True,
                'automatic_start_respected_manual_stop': True,
                'manual_start_cleared_stop_and_restarted': True,
                'old_boot_stop_did_not_disable_automatic_start': True, 'operations': records}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', action='store_true')
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(worker()))
        return
    path = '/mnt/'+ROOT.drive[0].lower()+ROOT.as_posix()[2:]+'/tools/probe_sd_manual_stop.py'
    result = subprocess.run(['wsl.exe', '-e', 'python3', path, '--worker'],
                            capture_output=True, text=True, encoding='utf-8', timeout=90)
    if result.returncode:
        raise RuntimeError(f'Probe exited {result.returncode}: {result.stderr}')
    report = json.loads(result.stdout)
    output = ROOT/'outputs/deployment/service-loader/manual-stop-report.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'report': str(output), 'passed': report['passed']}))


if __name__ == '__main__':
    main()
