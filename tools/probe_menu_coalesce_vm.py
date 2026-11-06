"""Measure drag/wheel rasterization coalescing on real ARM nxui in one VM.

The same wheel burst sequence drives two binaries in the same guest: the
recorded before-binary and the current build. NX_PANEL_TRACE frame logs give
painted-frame counts, inter-paint intervals and final-input latency. Software
rasterization counts only; no physical FPS, LCD or camera claim.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import time

import arm_vm
import probe_menu_startup_vm as vm
from probe_menu_entry_vm import collect
from probe_lut_selection_vm import TAP_DRIVER

ROOT = Path(__file__).resolve().parents[1]

BURST_KEYS = ' '.join(['XF86ScrollDown'] * 800)

# The base driver's key mode handles a single keysym (argc==3); volleys need
# an explicit multi-key branch so they do not fall through to the Menu probe.
BURST_DRIVER = r'''
    if(argc>3 && !strcmp(argv[1],"key")) {
        for(int i=2;i<argc;i++) {
            XEvent e={0};e.xkey.display=d;e.xkey.window=w;
            e.xkey.keycode=XKeysymToKeycode(d,XStringToKeysym(argv[i]));
            if(!e.xkey.keycode)return 22;
            e.type=KeyPress;XSendEvent(d,w,False,KeyPressMask,&e);
            e.type=KeyRelease;XSendEvent(d,w,False,KeyReleaseMask,&e);
        }
        XSync(d,False);XCloseDisplay(d);return 0;
    }
'''

PROBE = r'''#!/bin/sh
set -eu
export DISPLAY=:97 NX300M_MOD_ROOT=/perf/mod
mkdir -p /perf/mod/.state
cat > /perf/mod/looks.txt <<'CATALOGUE'
@CATALOGUE@
CATALOGUE
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
last_choice() { grep 'LUT_CHOICE event=select' "$1" | tail -n 1; }
run_variant() { # label binary
    rm -f /var/run/nx300m-lut-selection /var/run/nx300m-lut-selection-session
    rm -rf /perf/mod/.state; mkdir -p /perf/mod/.state
    NX_PANEL_TRACE=1 NX_STATE_DIR=/tmp/coalesce-$1 /rescue/busybox timeout 25 "$2" --live --lut \
        --root /perf/mod --recipes /perf/mod/looks.txt --engine /perf/recorder.sh \
        --font-file /usr/share/fonts/SDIC_GP_US_20120720.ttf \
        --font-metadata /perf/mod/fonts/mfw-font.charset --lifetime-ms 9000 > /perf/coalesce-$1.log 2>&1 &
    panel=$!
    attempt=0
    until grep -q '^FIRST_FRAME ' /perf/coalesce-$1.log; do
        kill -0 "$panel"; attempt=$((attempt+1)); [ "$attempt" -lt 150 ]; /rescue/busybox usleep 100000
    done
    # Reach a real look and open its tuning view.
    attempt=0
    until last_choice /perf/coalesce-$1.log | grep -q "key=nx300-hard-mono"; do
        /perf/close.arm key KP_Down
        attempt=$((attempt+1)); [ "$attempt" -lt 30 ]; /rescue/busybox usleep 80000
    done
    /perf/close.arm key XF86HomePage
    /rescue/busybox usleep 300000
    # One sustained backlog spans many 64-event batches. The final 13 reverse
    # steps leave +3, so a saturated endpoint cannot hide missing tail input.
    volley=0
    while [ "$volley" -lt 1 ]; do
        /perf/close.arm key @BURST@
        /rescue/busybox usleep 100000
        volley=$((volley+1))
    done
    /perf/close.arm key XF86ScrollUp XF86ScrollUp XF86ScrollUp XF86ScrollUp XF86ScrollUp XF86ScrollUp XF86ScrollUp XF86ScrollUp XF86ScrollUp XF86ScrollUp XF86ScrollUp XF86ScrollUp XF86ScrollUp
    # A trailing discrete key must still paint promptly after the burst.
    /perf/close.arm key KP_Down
    /rescue/busybox usleep 250000
    wait "$panel" || :
}
run_variant before /perf/before.arm
run_variant current /perf/mod/bin/nxui.arm
kill "$stock"
trap - EXIT
'''


def analyze(text):
    frames = [dict(dequeued=int(m[0]), submitted=int(m[1]), painted=int(m[2]))
              for m in re.findall(r'PANEL_FRAME pid=\d+ dequeued_ms=(-?\d+) submitted_ms=(-?\d+) events=\d+ painted=(\d+) exposed=\d+', text)]
    painted = [f for f in frames if f['painted'] == 1]
    if len(painted) < 2:
        return dict(frames=len(frames), painted=len(painted), interpaint_p50_ms=None,
                    interpaint_p95_ms=None, final_latency_ms=None)
    gaps = sorted(b['submitted'] - a['submitted'] for a, b in zip(painted, painted[1:]))
    last_dequeued = max((f['dequeued'] for f in frames if f['dequeued'] >= 0), default=-1)
    last_painted = painted[-1]['submitted']
    return dict(frames=len(frames), painted=len(painted),
                interpaint_p50_ms=gaps[len(gaps)//2],
                interpaint_p95_ms=gaps[int((len(gaps)-1)*0.95)],
                final_latency_ms=last_painted - last_dequeued if last_dequeued >= 0 else None)


def details(text):
    final = re.search(r'PANEL_FINAL selected=(\d+) field=(\d+) strength=(\d+) tune=([-\d,]+)', text)
    resources = re.findall(r'PANEL_RESOURCE .*stage=(first-frame|exit) user_us=(\d+) system_us=(\d+)', text)
    cpu = {stage: int(user)+int(system) for stage,user,system in resources}
    wake = re.search(r'EXIT renders=\d+ elapsed_ms=(\d+) wakeups=(\d+)',text)
    full_batches = re.findall(r'PANEL_FRAME .*submitted_ms=(\d+) events=64 painted=(\d+)',text)
    return dict(final=(dict(selected=int(final[1]),field=int(final[2]),strength=int(final[3]),tune=list(map(int,final[4].split(',')))) if final else None),
                cpu_after_first_frame_ms=(cpu['exit']-cpu['first-frame'])/1000 if len(cpu)==2 else None,
                wakeups=int(wake[2]) if wake else None,
                full_batches=len(full_batches),
                painted_during_full_batches=sum(int(paint) for _,paint in full_batches),
                wheel_presses=len(re.findall(r'PANEL_KEY type=2 .*symbol=0x1008ff7[89]\b',text)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--before-binary', type=Path,
                        default=ROOT/'outputs/review/2026-11-05-handoff/nxui-before-perf.arm')
    args = parser.parse_args()
    out = args.output_dir.resolve()
    if (out/'report.json').exists():
        raise RuntimeError('Preserve earlier evidence; choose a new output directory')
    from zipfile import ZipFile
    archive = ROOT/'outputs/looks/lightroom-import/on-camera-test/nx300m-lut-color-test.zip'
    with ZipFile(archive) as package:
        catalogue = package.read('nx300m-mod/looks.txt').decode().rstrip()
        looks = {name: package.read(name) for name in package.namelist()
                 if '/looks/' in name and name.endswith('.d4lut')}
    vm.OUT = out
    vm.PROBE = PROBE.replace('@CATALOGUE@', catalogue).replace('@BURST@', BURST_KEYS)
    vm.DRIVER = vm.DRIVER.replace('    if(argc==2) {', TAP_DRIVER+BURST_DRIVER+'\n    if(argc==2) {', 1)
    vm.DRIVER = vm.DRIVER.replace('    XEvent event={0};', '    if(capture_only){XCloseDisplay(d);return 0;}\n    XEvent event={0};', 1)
    vm.DRIVER = vm.DRIVER.replace('XStringToKeysym("Menu")', 'XStringToKeysym(argc==3 ? argv[2] : "Menu")')
    vm.EXTRA_FILES = {
        'perf/recorder.sh': b'#!/bin/sh\nprintf \'%s\\n\' "$*" >> /perf/actions.txt\nprintf \'STAGED %s\\n\' "${2:-off}"\n',
        'perf/before.arm': args.before_binary.read_bytes(),
        'perf/mod/fonts/mfw-font.charset': (ROOT/'outputs/metol/mfw-font.charset').read_bytes(),
    }
    vm.EXTRA_FILES.update({'perf/mod/'+name.removeprefix('nx300m-mod/'): data for name, data in looks.items()})
    vm.prepare()
    report = dict(passed=False, hardware_fps=False,
                  scope='Same wheel burst on two binaries in one ARM VM; NX_PANEL_TRACE software frame logs',
                  before_sha256=hashlib.sha256(args.before_binary.read_bytes()).hexdigest(),
                  current_sha256=hashlib.sha256((ROOT/'outputs/metol/nxui.arm').read_bytes()).hexdigest())
    started = False
    try:
        vm.call('start'); started = True
        deadline = time.monotonic()+180
        while time.monotonic() < deadline:
            if (out/'serial.log').exists() and 'MENU_PERF_VM_READY' in (out/'serial.log').read_text(errors='replace'):
                break
            time.sleep(.5)
        else:
            raise TimeoutError('Coalesce VM startup')
        execution = vm.call('exec', '/bin/sh /perf/probe.sh')
        arm_vm.write_json(out/'execution.json', execution)
        for name in ('coalesce-before.log', 'coalesce-current.log'):
            collect(out, name)
        if execution['exit_code']:
            raise RuntimeError('Coalesce probe failed: '+execution['output'][-3000:])
        before = analyze((out/'coalesce-before.log').read_text(errors='replace'))
        current = analyze((out/'coalesce-current.log').read_text(errors='replace'))
        report.update(before=before, current=current)
        report['before_details']=details((out/'coalesce-before.log').read_text(errors='replace'))
        report['current_details']=details((out/'coalesce-current.log').read_text(errors='replace'))
        report['painted_frames_reduced'] = (current['painted'] < before['painted'])
        # The trailing discrete key must still paint quickly after the burst.
        report['final_input_latency_ok'] = (current['final_latency_ms'] is not None and
                                            0 <= current['final_latency_ms'] <= 150)
        d=report['current_details']
        report['sustained_input_paints']=d['full_batches']>=20 and d['painted_during_full_batches']>=5
        report['final_value_correct']=bool(d['final'] and d['final']['field']==1 and d['final']['tune']==[3,0,0,0,0,0,0])
        report['all_wheel_presses_consumed']=d['wheel_presses']==813
        report['passed'] = report['final_input_latency_ok'] and report['sustained_input_paints'] and report['final_value_correct'] and report['all_wheel_presses_consumed']
    except BaseException as error:
        report['failure'] = str(error)
        raise
    finally:
        if started:
            report['stop'] = vm.call('stop')
            report['private_vm_stopped'] = 'return' in report['stop']
            if report['private_vm_stopped']:
                (out/'initramfs.cpio.gz').unlink(missing_ok=True)
        arm_vm.write_json(out/'report.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
