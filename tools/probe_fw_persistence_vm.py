"""Diagnose production firmware-preference shell writes in an isolated ARM VM.

Only camera_control.sh is a finite 26-field response fixture. The stock shell,
mv, common.sh and panel-actions.sh are real. No camera, card or network is used.
Known contract failures are reported separately from diagnostic completion.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

import probe_menu_startup_vm as vm
from probe_menu_entry_vm import collect

ROOT = Path(__file__).resolve().parents[1]
CONTROL = b'#!/bin/sh\nprintf "1 0 123 2 0 0 0 0 0 0 0 0 0 1 0 1 0 63 0 0 0 0 0 0 0 0\\n"\n'
PROBE = r'''#!/bin/sh
set -eu
export NX300M_MOD_ROOT=/perf/mod NX_STATE_DIR=/tmp/fw-persist-state
mkdir -p /perf/normal /perf/target-directory/.state/fw-mode-lock /perf/temp-directory/.state
set +e
NX300M_MOD_SOURCE_ROOT=/perf/normal /bin/sh /perf/mod/mfw-panel-actions.sh fw-mode-lock-on > /perf/normal.log 2>&1
normal=$?
NX300M_MOD_SOURCE_ROOT=/perf/target-directory /bin/sh /perf/mod/mfw-panel-actions.sh fw-mode-lock-on > /perf/target-directory.log 2>&1
target=$?
# exec preserves $$, making the real action's temporary path a directory.
NX300M_MOD_SOURCE_ROOT=/perf/temp-directory /bin/sh -c 'mkdir "$NX300M_MOD_SOURCE_ROOT/.state/fw-mode-lock.tmp.$$"; exec /bin/sh /perf/mod/mfw-panel-actions.sh fw-mode-lock-on' > /perf/temp-directory.log 2>&1
temporary=$?
set -e
printf 'NORMAL_RC=%s\nTARGET_DIRECTORY_RC=%s\nTEMP_DIRECTORY_RC=%s\n' "$normal" "$target" "$temporary" > /perf/outcome.log
if [ -f /perf/normal/.state/fw-mode-lock ] && [ "$(cat /perf/normal/.state/fw-mode-lock)" = 1 ]; then echo NORMAL_FILE_VALID=1 >> /perf/outcome.log; fi
if [ -d /perf/target-directory/.state/fw-mode-lock ]; then echo TARGET_REMAINS_DIRECTORY=1 >> /perf/outcome.log; fi
ls -la /perf/target-directory/.state/fw-mode-lock >> /perf/outcome.log
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--require-clean', action='store_true')
    args = parser.parse_args()
    out = args.output_dir.resolve()
    if (out / 'report.json').exists():
        raise RuntimeError('Choose a new output directory; preserve earlier evidence')
    vm.OUT, vm.PROBE = out, PROBE
    vm.EXTRA_FILES = {'perf/mod/camera_control.sh': CONTROL}
    vm.prepare()
    report = dict(diagnostic_completed=False, contracts_passed=False,
                  hardware_verified=False, network='none',
                  scope='Stock ARM shell + production panel-actions/common; fixture firmware control response',
                  action_sha256=hashlib.sha256((ROOT / 'mod/deployment/mfw/panel-actions.sh').read_bytes()).hexdigest())
    started = False
    try:
        vm.call('start')
        started = True
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if (out / 'serial.log').exists() and 'MENU_PERF_VM_READY' in (out / 'serial.log').read_text(errors='replace'):
                break
            time.sleep(.5)
        else:
            raise TimeoutError('Private ARM VM startup')
        execution = vm.call('exec', '/bin/sh /perf/probe.sh')
        (out / 'execution.json').write_text(json.dumps(execution, ensure_ascii=False, indent=2), encoding='utf-8')
        for name in ('normal.log', 'target-directory.log', 'temp-directory.log', 'outcome.log'):
            collect(out, name)
        if execution['exit_code']:
            raise RuntimeError('Guest diagnostic exit ' + str(execution['exit_code']))
        outcome = (out / 'outcome.log').read_text()
        normal = (out / 'normal.log').read_text()
        target = (out / 'target-directory.log').read_text()
        temporary = (out / 'temp-directory.log').read_text()
        checks = [
            dict(name='normal_save_control', contract_passed='NORMAL_RC=0\n' in outcome and 'NORMAL_FILE_VALID=1' in outcome and normal.startswith('FW_MOD ')),
            dict(name='directory_target_must_not_report_saved', contract_passed='TARGET_DIRECTORY_RC=75\n' in outcome and 'PERSIST-WARNING' in target),
            dict(name='temp_write_failure_returns_applied_unsaved', contract_passed='TEMP_DIRECTORY_RC=75\n' in outcome and 'FW_MOD PERSIST-WARNING' in temporary),
        ]
        report.update(diagnostic_completed=True, checks=checks,
                      contracts_passed=all(row['contract_passed'] for row in checks),
                      observed_contract_failures=sum(not row['contract_passed'] for row in checks),
                      temp_failure_has_diagnostic_before_marker=not temporary.startswith('FW_MOD ') and 'FW_MOD PERSIST-WARNING' in temporary)
    except Exception as error:
        report['failure'] = str(error)
        raise
    finally:
        try:
            if started:
                report['stop'] = vm.call('stop')
                report['private_vm_stopped'] = 'return' in report['stop']
                if report['private_vm_stopped']:
                    (out / 'initramfs.cpio.gz').unlink(missing_ok=True)
        finally:
            (out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(args.require_clean and not report['contracts_passed'])


if __name__ == '__main__':
    sys.exit(main())
