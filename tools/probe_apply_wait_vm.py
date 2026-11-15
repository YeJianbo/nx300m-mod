"""Focused reproduction of the hidden apply wait: one variant, one backend child.

The lut-menu-entry wait-vm gate requires the panel to sleep, not spin, while a
control backend runs (baseline spun at ~5000 wakeups, the fixed panel at ~22).
This probe runs only that scenario so a wakeup regression can be attributed
without the whole gate, and prints the panel's own EXIT counters including
zero_delay (select calls that could not sleep at all).

Offline, software only: no camera, no card, no flash.
"""
import argparse
import json
from pathlib import Path
import re
import time

import arm_vm
import probe_menu_startup_vm as vm
from probe_menu_entry_vm import collect

ROOT = Path(__file__).resolve().parents[1]

PROBE = r'''#!/bin/sh
set -eu
export DISPLAY=:97 NX300M_MOD_ROOT=/perf/mod
attempt=0
until /perf/close.arm ping; do
    attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 100000
done
/perf/close.arm stock-parent > /perf/stock-parent.log 2>&1 &
stock=$!
trap 'kill "$stock" 2>/dev/null || :' EXIT
attempt=0
until [ -s /perf/stock-parent.ready ]; do
    attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 100000
done
mkdir -p /perf/mod/looks/test-mono
printf 'test-mono|Mono|0|0|0|0|0|0|0\n' > /perf/mod/looks.txt
printf '1 /perf/mod/looks/test-mono/table.d4lut 100\n' > /var/run/nx300m-lut-selection
cat > /perf/engine.sh <<'ENGINE'
#!/bin/sh
if [ "$1" = apply ]; then
    trap 'exit 88' TERM INT
    sleep 2
    printf '%s\n' "$2" > /perf/chosen.txt
fi
exit 0
ENGINE
chmod 700 /perf/engine.sh
for variant in @VARIANTS@; do
    binary=/perf/$variant.arm
    NX_STATE_DIR=/tmp/panel-idle-$variant /rescue/busybox timeout 12 "$binary" \
        @FLAG@ --lut --root /perf/mod --recipes /perf/mod/looks.txt --engine /perf/engine.sh \
        --font-file /usr/share/fonts/SDIC_GP_US_20120720.ttf > /perf/$variant.log 2>&1 &
    panel=$!
    attempt=0
    until grep -q '^FIRST_FRAME ' /perf/$variant.log; do
        kill -0 "$panel" || { cat /perf/$variant.log; exit 1; }
        attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 100000
    done
    /perf/close.arm key Return
    wait "$panel"
    grep '^EXIT ' /perf/$variant.log
done
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--variant', action='append', default=[],
                        metavar='LABEL=PATH', help='binary to run; repeat for several')
    parser.add_argument('--flag', default='--live')
    args = parser.parse_args()
    out = args.output_dir.resolve()
    if (out / 'report.json').exists():
        raise SystemExit('Preserve earlier evidence; choose a new output directory')
    variants = []
    for item in args.variant:
        label, _, path = item.partition('=')
        if not label or not re.fullmatch(r'[a-z0-9-]+', label):
            raise SystemExit('--variant needs LABEL=PATH with a [a-z0-9-] label')
        variants.append((label, Path(path)))
    if not variants:
        raise SystemExit('at least one --variant is required')

    binaries = {label: path.read_bytes() for label, path in variants}
    vm.OUT = out
    probe = (PROBE.replace('@VARIANTS@', ' '.join(label for label, _ in variants))
                  .replace('@FLAG@', args.flag))
    vm.PROBE = probe
    vm.EXTRA_FILES = {f'perf/{label}.arm': data for label, data in binaries.items()}
    vm.prepare()

    report = {'passed': False, 'variants': {},
              'scope': 'Hidden apply wait only: panel must sleep while a 2 s backend child runs',
              'binaries': {label: __import__('hashlib').sha256(data).hexdigest()
                           for label, data in binaries.items()}}
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
            raise TimeoutError('apply wait VM startup')
        execution = vm.call('exec', '/bin/sh /perf/probe.sh')
        arm_vm.write_json(out / 'execution.json', execution)
        report['exit_code'] = execution['exit_code']
        for label, _ in variants:
            collect(out, f'{label}.log')
            text = (out / f'{label}.log').read_text(errors='replace')
            exit_line = re.search(r'^EXIT (.+)$', text, re.M)
            detail = dict(re.findall(r'(\w+)=(-?\d+)', exit_line[1])) if exit_line else {}
            report['variants'][label] = {k: int(v) for k, v in detail.items()}
        report['passed'] = execution['exit_code'] == 0
    finally:
        if started:
            try:
                vm.call('stop')
            except Exception as error:
                report['stop_error'] = repr(error)[:300]
            (out / 'initramfs.cpio.gz').unlink(missing_ok=True)
    arm_vm.write_json(out / 'report.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
