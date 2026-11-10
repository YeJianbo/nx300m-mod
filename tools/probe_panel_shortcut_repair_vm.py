"""Bounded regression for the next-boot switch and physical settings entry."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

import probe_menu_startup_vm as vm
from probe_menu_entry_vm import collect
from probe_lut_selection_vm import TAP_DRIVER

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/review/2026-11-07-zcode/panel-shortcut-repair-vm'
PROBE = r'''#!/bin/sh
set -eu
export DISPLAY=:97 NX300M_MOD_ROOT=/perf/mod NX_STATE_DIR=/tmp/panel-repair
export NX300M_MOD_SOURCE_ROOT=/perf/source
mkdir -p /perf/source/.state
export NX300M_MOD_SOURCE_ID=$(stat -c '%d:%i' /perf/source)
attempt=0
until /perf/close.arm ping; do
    attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 100000
done
/perf/close.arm stock-parent > /perf/stock-parent.log 2>&1 &
stock=$!
trap 'kill "$stock" "${panel:-}" 2>/dev/null || :' EXIT
attempt=0
until [ -s /perf/stock-parent.ready ]; do
    attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 100000
done
/rescue/busybox timeout 45 /perf/mod/bin/nxui.arm --mfw-services --root /perf/mod \
    --font-file /usr/share/fonts/SDIC_GP_US_20120720.ttf \
    --font-metadata /perf/mod/fonts/mfw-font.charset > /perf/panel.log 2>&1 &
panel=$!
attempt=0
until grep -q '^FIRST_FRAME ' /perf/panel.log; do
    kill -0 "$panel"; attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 100000
done
key() { /perf/close.arm key "$1"; /rescue/busybox usleep 300000; }
# Leave the touch position on the future settings autostart row, outside
# the home cards. Enter must navigate once rather than activate that row.
/perf/close.arm tap 400 270
for k in KP_Right KP_Right KP_Right KP_Right KP_Enter; do key "$k"; done
[ ! -e /perf/source/DISABLED ] && [ ! -e /perf/mod/DISABLED ]
! grep -q '^BACKEND kind=5' /perf/panel.log
echo SETTINGS_SINGLE_ENTER_OK
key Menu; key Menu
wait "$panel"
# Exercise the actual shell against separate SD and RAM roots.
/bin/sh /perf/mod/mfw-panel-actions.sh disable-autostart > /perf/disable.log
[ -f /perf/source/DISABLED ] && [ ! -e /perf/mod/DISABLED ]
grep -q '本次服务继续运行' /perf/disable.log
echo NEXT_BOOT_ONLY_OK
/bin/sh /perf/mod/mfw-panel-actions.sh enable-autostart > /perf/enable.log
[ ! -e /perf/source/DISABLED ] && [ ! -e /perf/mod/DISABLED ]
echo ENABLE_RECOVERY_OK
printf '0\n' > /perf/source/.state/lut-preview
/bin/sh /perf/mod/mfw-panel-actions.sh preview-preference > /perf/preview.log
grep -q '^1$' /perf/source/.state/lut-preview
grep -q '预览偏好已开' /perf/preview.log
echo PREVIEW_PREFERENCE_OK
kill "$stock"
trap - EXIT
'''


def main():
    global OUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=OUT)
    parser.add_argument('--preview-only', action='store_true')
    args = parser.parse_args()
    OUT = args.output_dir.resolve()
    vm.OUT = OUT
    if args.preview_only:
        previous = json.loads((OUT/'report.json').read_text(encoding='utf-8'))
        if previous['menu_sha256'] != hashlib.sha256((ROOT/'outputs/metol/nxui.arm').read_bytes()).hexdigest():
            raise ValueError('Earlier menu evidence has a different binary')
        started = False
        try:
            vm.call('start'); started = True
            deadline = time.monotonic()+150
            while time.monotonic() < deadline:
                if 'MENU_PERF_VM_READY' in (OUT/'serial.log').read_text(errors='replace'):
                    break
                time.sleep(.5)
            else:
                raise TimeoutError('Private VM startup')
            result = vm.call('exec', '''set -eu
export NX300M_MOD_ROOT=/perf/mod NX_STATE_DIR=/tmp/preview-retry NX300M_MOD_SOURCE_ROOT=/perf/source
mkdir -p /perf/source/.state
export NX300M_MOD_SOURCE_ID=$(stat -c '%d:%i' /perf/source)
printf '0\\n' > /perf/source/.state/lut-preview
/bin/sh /perf/mod/mfw-panel-actions.sh preview-preference
grep -q '^1$' /perf/source/.state/lut-preview
echo PREVIEW_PREFERENCE_OK
''')
            (OUT/'preview-retry.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
            previous['checks']['PREVIEW_PREFERENCE_OK'] = result['exit_code'] == 0 and 'PREVIEW_PREFERENCE_OK' in result['output']
            previous['passed'] = all(previous['checks'].values())
            previous.pop('error', None)
            previous.pop('exit_code', None)
            previous['evidence'] = ['report.json (three passed checks)', 'preview-retry.json (source identity fixture supplied)']
            (OUT/'acceptance.json').write_text(json.dumps(previous, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
            print(json.dumps(previous, ensure_ascii=False))
            if not previous['passed']:
                raise RuntimeError('Preview retry failed')
        finally:
            if started:
                vm.call('stop')
        return
    vm.PROBE = PROBE
    vm.DRIVER = vm.DRIVER.replace('    if(argc==2) {', TAP_DRIVER+'\n    if(argc==2) {', 1)
    vm.DRIVER = vm.DRIVER.replace('    XEvent event={0};', '    if(capture_only){XCloseDisplay(d);return 0;}\n    XEvent event={0};', 1)
    vm.DRIVER = vm.DRIVER.replace('XStringToKeysym("Menu")', 'XStringToKeysym(argc==3 ? argv[2] : "Menu")')
    vm.EXTRA_FILES = {'perf/mod/bin/lut_engine.arm': (ROOT/'outputs/metol/lut_engine.arm').read_bytes()}
    vm.prepare()
    report = {'passed': False, 'physical_camera_verified': False,
              'menu_sha256': hashlib.sha256((ROOT/'outputs/metol/nxui.arm').read_bytes()).hexdigest(),
              'scope': 'Real ARM menu physical-key events and actual panel-actions in private VM; shortcut device transport not exercised'}
    started = False
    try:
        vm.call('start'); started = True
        deadline = time.monotonic()+150
        while time.monotonic() < deadline:
            if (OUT/'serial.log').exists() and 'MENU_PERF_VM_READY' in (OUT/'serial.log').read_text(errors='replace'):
                break
            time.sleep(.5)
        else:
            raise TimeoutError('Private VM startup')
        result = vm.call('exec', '/bin/sh /perf/probe.sh')
        (OUT/'execution.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        collect(OUT, 'panel.log')
        report['exit_code'] = result['exit_code']
        report['checks'] = {name: name in result['output'] for name in (
            'SETTINGS_SINGLE_ENTER_OK', 'NEXT_BOOT_ONLY_OK', 'ENABLE_RECOVERY_OK', 'PREVIEW_PREFERENCE_OK')}
        report['passed'] = result['exit_code'] == 0 and all(report['checks'].values())
        if not report['passed']:
            raise RuntimeError(result['output'][-3000:])
    except Exception as exc:
        report['error'] = str(exc)
        raise
    finally:
        if started:
            report['stop'] = vm.call('stop')
        (OUT/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
