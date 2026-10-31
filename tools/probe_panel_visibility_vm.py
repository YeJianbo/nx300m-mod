"""Check menu occlusion/focus handoff in the existing private ARM Xvfb VM.

The stock window/dialog are test clients, not the camera program or LCD driver.
Only regressions implicated by the user's shutter-recovery report are tested.
"""
import argparse
import base64
import json
from pathlib import Path
import re
import time

import arm_vm
import probe_menu_startup_vm as vm


EXTRA_DRIVER = r'''
    if(argc==2 && (!strcmp(argv[1],"stock") || !strcmp(argv[1],"dialog"))) {
        const char *name=!strcmp(argv[1],"stock") ? "NX-TEST-STOCK" : "NX-TEST-DIALOG";
        Window created=XCreateSimpleWindow(d,DefaultRootWindow(d),0,0,480,800,0,0,0);
        if(!strcmp(argv[1],"stock")) {
            XClassHint hint={"di-camera-app-nx300","di-camera-app-nx300"};XSetClassHint(d,created,&hint);
        }
        XStoreName(d,created,name);XSelectInput(d,created,KeyPressMask|KeyReleaseMask);
        XMapRaised(d,created);XSetInputFocus(d,created,RevertToPointerRoot,CurrentTime);XSync(d,False);
        FILE *file=fopen(!strcmp(argv[1],"stock") ? "/perf/stock.id" : "/perf/dialog.id","w");
        if(!file)return 20;
        fprintf(file,"%lu\n",created);fclose(file);
        Window cover=None;
        for(;;){XEvent event;XNextEvent(d,&event);
            if(event.type==ClientMessage && event.xclient.data.l[0]==1) {
                if(cover==None)cover=XCreateSimpleWindow(d,created,0,0,480,800,0,0,0);
                XMapRaised(d,cover);XSync(d,False);
            }
        }
    }
    if(argc>=2 && !strcmp(argv[1],"cover")) {
        Window stock;FILE *file=fopen("/perf/stock.id","r");
        if(!file || fscanf(file,"%lu",&stock)!=1)return 21;
        fclose(file);
        if(argc==3)XSetInputFocus(d,stock,RevertToPointerRoot,CurrentTime);
        else {
            XEvent event={0};event.xclient.type=ClientMessage;event.xclient.window=stock;
            event.xclient.format=32;event.xclient.data.l[0]=1;
            XSendEvent(d,stock,False,NoEventMask,&event);
        }
        XSync(d,False);XCloseDisplay(d);return 0;
    }
    if(argc==3 && !strcmp(argv[1],"focused")) {
        Window current;int revert;char *name=NULL;
        XGetInputFocus(d,&current,&revert);XFetchName(d,current,&name);
        printf("focus=0x%lx name=%s\n",current,name ? name : "");
        int match=name && !strcmp(name,argv[2]);
        if(name)XFree(name);
        XCloseDisplay(d);return match ? 0 : 22;
    }
'''

PROBE = r'''#!/bin/sh
set -eu
export DISPLAY=:97 NX300M_MOD_ROOT=/perf/mod NX_STATE_DIR=/perf/state
mkdir -p /perf/state
attempt=0
until /perf/close.arm ping; do attempt=$((attempt+1)); [ "$attempt" -lt 20 ]; sleep 1; done
/perf/close.arm stock > /perf/stock.log 2>&1 &
stock=$!
trap 'kill "$stock" 2>/dev/null || :' EXIT
attempt=0
until [ -s /perf/stock.id ]; do attempt=$((attempt+1)); [ "$attempt" -lt 10 ]; sleep 1; done
ready() {
    attempt=0
    until grep -q '^READY .*visibility_guard=1 ' "$1"; do
        kill -0 "$panel"; attempt=$((attempt+1)); [ "$attempt" -lt 15 ]; sleep 1
    done
}
exited() {
    attempt=0
    while kill -0 "$panel" 2>/dev/null; do attempt=$((attempt+1)); [ "$attempt" -lt 8 ]; sleep 1; done
    wait "$panel"
}
/bin/sh /perf/mod/mfw-panel.sh > /perf/occlusion.log 2>&1 &
panel=$!
ready /perf/occlusion.log
/perf/close.arm focused Metol-NX300M
/perf/close.arm cover
attempt=0
until grep -q 'name=occlusion-single-raise' /perf/occlusion.log; do
    kill -0 "$panel";attempt=$((attempt+1)); [ "$attempt" -lt 8 ];sleep 1
done
/perf/close.arm focused Metol-NX300M
/perf/close.arm cover
exited
grep -q 'name=yield-obscured' /perf/occlusion.log
/perf/close.arm focused NX-TEST-STOCK

/bin/sh /perf/mod/mfw-panel.sh > /perf/focus.log 2>&1 &
panel=$!
ready /perf/focus.log
/perf/close.arm dialog > /perf/dialog.log 2>&1 &
dialog=$!
exited
/perf/close.arm focused NX-TEST-DIALOG
grep -q 'PANEL_FOCUS_RETURN retained=' /perf/focus.log
kill "$dialog"
/perf/close.arm cover focus

/bin/sh /perf/mod/mfw-panel.sh > /perf/close.log 2>&1 &
panel=$!
ready /perf/close.log
/perf/close.arm
exited
/perf/close.arm focused NX-TEST-STOCK

# No process cleanup handler can run after SIGKILL. X11 itself must restore
# the stock parent when its focused menu child disappears.
/bin/sh /perf/mod/mfw-panel.sh > /perf/abrupt.log 2>&1 &
panel=$!
ready /perf/abrupt.log
grep -q 'PANEL_PARENT mode=stock-child .*focus_revert=2' /perf/abrupt.log
kill -KILL "$panel"
set +e
wait "$panel"
abrupt_exit=$?
set -e
[ "$abrupt_exit" -eq 137 ]
attempt=0
until /perf/close.arm focused NX-TEST-STOCK; do attempt=$((attempt+1)); [ "$attempt" -lt 8 ];sleep 1;done
kill -0 "$stock"
printf '{"map_visible_before_focus":true,"one_occlusion_retry":true,"second_occlusion_yields":true,"stock_dialog_keeps_focus":true,"menu_exit_returns_focus":true,"sigkill_restores_stock_parent":true}\n' > /perf/results.json
kill "$stock"
trap - EXIT
'''


def collect(out, name):
    result = vm.call('exec', 'printf "DATA_START\\n"; /rescue/busybox base64 /perf/'+name+'; printf "DATA_END\\n"')
    match = re.search(r'\r?\nDATA_START\r?\n([A-Za-z0-9+/=\r\n]*)DATA_END', result['output'])
    if result['exit_code'] or not match:
        raise RuntimeError('Cannot collect '+name)
    (out/name).write_bytes(base64.b64decode(match[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    out = parser.parse_args().output_dir.resolve()
    if out.exists():
        raise RuntimeError('Preserve previous evidence; select a new output directory')
    vm.OUT = out
    vm.DRIVER = vm.DRIVER.replace('    Window w; int revert;', EXTRA_DRIVER+'\n    Window w; int revert;')
    vm.PROBE = PROBE
    vm.prepare()
    report = {'passed': False, 'stock_camera_program_running': False,
              'physical_display_driver_verified': False, 'main_emulator_touched': False}
    started = False
    try:
        vm.call('start'); started = True
        deadline = time.monotonic()+180
        while time.monotonic() < deadline:
            if (out/'serial.log').exists() and 'MENU_PERF_VM_READY' in (out/'serial.log').read_text(errors='replace'):
                break
            time.sleep(1)
        else:
            raise TimeoutError('ARM VM startup')
        result = vm.call('exec', '/bin/sh /perf/probe.sh')
        arm_vm.write_json(out/'execution.json', result)
        for name in ('occlusion.log', 'focus.log', 'close.log', 'abrupt.log'):
            collect(out, name)
        if result['exit_code']:
            raise RuntimeError('Visibility probe exit '+str(result['exit_code']))
        collect(out, 'results.json')
        scenarios = json.loads((out/'results.json').read_text())
        # Check observed logs too; shell success alone does not establish order.
        for name in ('occlusion.log', 'focus.log', 'close.log'):
            log = (out/name).read_text(encoding='utf-8')
            if not re.search(r'PANEL_WINDOW stage=before-focus .*visibility=0 ', log):
                raise RuntimeError('No visible-before-focus evidence: '+name)
            if not re.search(r'^EXIT ', log, re.M):
                raise RuntimeError('No clean client exit: '+name)
        report.update(passed=all(scenarios.values()), scenarios=scenarios, guest_exit_code=result['exit_code'])
    except BaseException as error:
        report['failure'] = str(error)
        raise
    finally:
        if started:
            report['stop'] = vm.call('stop')
            if 'return' in report['stop']:
                (out/'initramfs.cpio.gz').unlink(missing_ok=True)
        arm_vm.write_json(out/'report.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
