"""Short test for the panel-asset pin contract only.

Runs the same shell block the session suite runs (PANEL_PIN_CONTRACT) in a light
VM, so a contract change can be validated in about a minute instead of a full
session suite. Evidence: the launcher log, the argv the stub was exec'd with, and
the log of the run made while the firmware font is masked.

Offline, software only: no camera, no card, no flash.
"""
import argparse
import json
from pathlib import Path
import time

import arm_vm
import probe_menu_startup_vm as vm
from probe_menu_entry_vm import collect
from probe_ipcs_session_vm import PANEL_PIN_CONTRACT

ROOT = Path(__file__).resolve().parents[1]

PROBE = r'''#!/bin/sh
set -eu
cd /perf
mkdir -p /mnt/mmc
cp /perf/lut-panel.sh /mnt/mmc/lut-panel.sh
. /perf/pin-contract.sh
printf 'PANEL_PIN_CONTRACT_PASSED\n'
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    out = args.output_dir.resolve()
    if (out / 'report.json').exists():
        raise SystemExit('Preserve earlier evidence; choose a new output directory')

    vm.OUT = out
    vm.PROBE = PROBE
    vm.EXTRA_FILES = {
        'perf/lut-panel.sh': (ROOT / 'mod/deployment/mfw/lut-panel.sh').read_bytes(),
        'perf/pin-contract.sh': PANEL_PIN_CONTRACT.encode(),
    }
    vm.prepare()

    report = {
        'passed': False,
        'scope': 'Panel launcher asset pin contract only; no camera, display or timing claim',
        'contract_source': 'tools/probe_ipcs_session_vm.py PANEL_PIN_CONTRACT (shared with the session suite)',
        'checks': [
            'both staged binaries are hard links to the private runtime copies',
            'the pinned generation stays usable after the resident pathname is replaced',
            'the launcher passes --engine <runtime>/lut_engine.arm and --font-file <stock font>',
            'with the firmware font masked the launcher refuses to start',
        ],
    }
    started = False
    try:
        vm.call('start')
        started = True
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            log = out / 'serial.log'
            if log.exists() and 'MENU_PERF_VM_READY' in log.read_text(errors='replace'):
                break
            time.sleep(.5)
        else:
            raise TimeoutError('panel pin VM startup')
        execution = vm.call('exec', '/bin/sh /perf/probe.sh')
        arm_vm.write_json(out / 'execution.json', execution)
        report['exit_code'] = execution['exit_code']
        report['passed'] = execution['exit_code'] == 0 and 'PANEL_PIN_CONTRACT_PASSED' in execution['output']
        for name in ('panel-asset-pin.log', 'panel-asset-font-required.log', 'panel-asset-argv.txt'):
            try:
                collect(out, name)
            except RuntimeError:
                # A failing contract may legitimately never create a file; the
                # absence is itself evidence and is recorded instead of raised.
                (out / name).write_text('<not created>\n', encoding='utf-8')
        report['launcher_log'] = (out / 'panel-asset-pin.log').read_text(errors='replace').splitlines()
        report['masked_font_run'] = (out / 'panel-asset-font-required.log').read_text(errors='replace').splitlines()
        report['argv_recorded_by_stub'] = (out / 'panel-asset-argv.txt').read_text(errors='replace').splitlines()
    finally:
        if started:
            try:
                vm.call('stop')
            except Exception as error:  # keep the evidence even if shutdown complains
                report['stop_error'] = repr(error)[:400]
            # The image is regenerable; keep the measurement, not 300 MB per run.
            (out / 'initramfs.cpio.gz').unlink(missing_ok=True)
    arm_vm.write_json(out / 'report.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
