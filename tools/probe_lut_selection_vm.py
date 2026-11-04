"""Reproduce physical LUT selection with native ARM nxui and the full catalogue."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import time
from zipfile import ZipFile

import arm_vm
import probe_menu_startup_vm as vm
from probe_menu_entry_vm import collect

ROOT = Path(__file__).resolve().parents[1]
TAP_DRIVER = r'''
    if(argc==4 && !strcmp(argv[1],"tap")) {
        XWindowAttributes a;if(!XGetWindowAttributes(d,w,&a))return 6;
        XEvent e={0};e.xbutton.display=d;e.xbutton.window=w;e.xbutton.button=Button1;
        e.xbutton.x=a.width-1-atoi(argv[3]);e.xbutton.y=atoi(argv[2]);
        e.type=ButtonPress;XSendEvent(d,w,False,ButtonPressMask,&e);XSync(d,False);
        usleep(120000);
        e.type=ButtonRelease;XSendEvent(d,w,False,ButtonReleaseMask,&e);XSync(d,False);
        XCloseDisplay(d);return 0;
    }
    int capture_only=argc==3 && !strcmp(argv[1],"snapshot");
    if(capture_only){argv[1]=argv[2];argc=2;}
'''
PROBE = r'''#!/bin/sh
set -eu
set -x
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
cat > /perf/mod/looks.txt <<'CATALOGUE'
@CATALOGUE@
CATALOGUE
cat > /perf/engine.sh <<'ENGINE'
#!/bin/sh
printf '%s %s\n' "$1" "${2:-}" >> /perf/actions.txt
[ "$1" != disable ] || sleep 2
printf 'STAGED %s\n' "${2:-off}"
ENGINE
chmod 700 /perf/engine.sh
for variant in saved-mono keys-from-off tap-from-off browse-mono; do
    rm -f /var/run/nx300m-lut-selection
    case $variant in saved-mono|browse-mono)
        printf '2 /perf/mod/looks/nx300-hard-mono/table.d4lut 100\n' > /var/run/nx300m-lut-selection ;; esac
    : > /perf/actions.txt
    NX_STATE_DIR=/tmp/select-$variant /rescue/busybox timeout 15 /perf/mod/bin/nxui.arm --live --lut \
        --root /perf/mod --recipes /perf/mod/looks.txt --engine /perf/engine.sh \
        --font-file /usr/share/fonts/SDIC_GP_US_20120720.ttf \
        --font-metadata /perf/mod/fonts/mfw-font.charset > /perf/$variant.log 2>&1 &
    panel=$!
    attempt=0
    until grep -q '^FIRST_FRAME ' /perf/$variant.log; do
        kill -0 "$panel"; attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 100000
    done
    /perf/close.arm snapshot /perf/$variant-before.ppm
    case $variant in
        saved-mono)
            /perf/close.arm key KP_Left
            /perf/close.arm key KP_Right ;;
        keys-from-off) /perf/close.arm key KP_Right ;;
        tap-from-off) /perf/close.arm tap 215 80 ;;
        browse-mono)
            /perf/close.arm key KP_Left
            /rescue/busybox usleep 500000
            [ ! -s /perf/actions.txt ]
            /perf/close.arm key KP_Right ;;
    esac
    /rescue/busybox usleep 300000
    [ ! -s /perf/actions.txt ]
    /perf/close.arm snapshot /perf/$variant-picked.ppm
    /perf/close.arm key KP_Enter
    if [ "$variant" = browse-mono ]; then
        /rescue/busybox usleep 150000
        /perf/close.arm key Menu
    fi
    wait "$panel"
    cp /perf/actions.txt /perf/$variant-actions.txt
    cat /perf/$variant-actions.txt
    gzip /perf/$variant-before.ppm /perf/$variant-picked.ppm
done
# Direct launcher must display both saved-On and saved-Off without executing
# ready/apply/restore-selection. The engine admits hardware only on confirmation.
mkdir -p /perf/mod/.state
for variant in direct-saved direct-off; do
    rm -f /var/run/nx300m-lut-selection /var/run/nx300m-lut-selection-session
    if [ "$variant" = direct-saved ]; then
        printf '2 /perf/mod/looks/nx300-hard-mono/table.d4lut 75\n' > /perf/mod/.state/lut-choice
    else
        printf '0\n' > /perf/mod/.state/lut-choice
    fi
    : > /perf/actions.txt
    /rescue/busybox timeout 15 /bin/sh /perf/mod/lut-panel.sh > /perf/$variant.log 2>&1 &
    panel=$!
    attempt=0
    until grep -q '^FIRST_FRAME ' /perf/$variant.log; do
        kill -0 "$panel"; attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 100000
    done
    [ ! -s /perf/actions.txt ]
    /perf/close.arm key Menu
    wait "$panel"
    cp /perf/actions.txt /perf/$variant-actions.txt
done
printf 'button|开启流畅预览 · 当前 LUT|preview-s2-enable\nbutton|选择 LUT|lut\n' > /perf/mod/gui_preview_quick.NX300M
cat > /perf/mod/mfw-panel-actions.sh <<'ACTIONS'
#!/bin/sh
printf 'LUT_OFF select a look before enabling its preview\n'
exit 1
ACTIONS
NX_STATE_DIR=/tmp/select-off-preview /rescue/busybox timeout 15 /perf/mod/bin/nxui.arm --mfw-quick-preview \
    --root /perf/mod --font-file /usr/share/fonts/SDIC_GP_US_20120720.ttf \
    --font-metadata /perf/mod/fonts/mfw-font.charset > /perf/off-preview.log 2>&1 &
panel=$!
attempt=0
until grep -q '^FIRST_FRAME ' /perf/off-preview.log; do
    kill -0 "$panel"; attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 100000
done
/perf/close.arm key KP_Enter
attempt=0
until grep -q '^BACKEND ' /perf/off-preview.log; do
    kill -0 "$panel"; attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 100000
done
/perf/close.arm key Menu
wait "$panel"
kill "$stock"
trap - EXIT
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT/'outputs/performance/menu-event-loop/lut-selection/before')
    args = parser.parse_args()
    out = args.output_dir.resolve()
    if (out/'report.json').exists():
        raise RuntimeError('Preserve the recorded selection run')
    archive = ROOT/'outputs/deployment/smartfilter-session/diagnostic-card/nx300m-ipcs-open-diagnostic.zip'
    with ZipFile(archive) as package:
        catalogue = package.read('nx300m-mod/looks.txt').decode().rstrip()
    vm.OUT = out
    vm.PROBE = PROBE.replace('@CATALOGUE@', catalogue)
    vm.DRIVER = vm.DRIVER.replace('    if(argc==2) {', TAP_DRIVER+'\n    if(argc==2) {', 1)
    vm.DRIVER = vm.DRIVER.replace('    XEvent event={0};', '    if(capture_only){XCloseDisplay(d);return 0;}\n    XEvent event={0};', 1)
    vm.DRIVER = vm.DRIVER.replace('XStringToKeysym("Menu")', 'XStringToKeysym(argc==3 ? argv[2] : "Menu")')
    launcher = ROOT/'mod/deployment/mfw/lut-panel.sh'
    vm.EXTRA_FILES = {
        'perf/mod/lut-panel.sh': launcher.read_bytes(),
        'perf/mod/bin/lut_engine.arm': b'#!/bin/sh\nprintf "%s\\n" "$*" >> /perf/actions.txt\nexit 0\n',
    }
    vm.prepare()
    report = dict(passed=False, hardware_pixels_verified=False,
                  scope='Native ARM nxui, real catalogue and X11 key/tap input; engine action recorder',
                  candidate_sha256=hashlib.sha256((ROOT/'outputs/metol/nxui.arm').read_bytes()).hexdigest(),
                  lut_panel_sha256=hashlib.sha256(launcher.read_bytes()).hexdigest())
    started = False
    try:
        vm.call('start'); started = True
        deadline = time.monotonic()+180
        while time.monotonic()<deadline:
            if (out/'serial.log').exists() and 'MENU_PERF_VM_READY' in (out/'serial.log').read_text(errors='replace'):
                break
            time.sleep(.5)
        else:
            raise TimeoutError('Selection VM startup')
        execution = vm.call('exec', '/bin/sh /perf/probe.sh')
        arm_vm.write_json(out/'execution.json', execution)
        variants = ('saved-mono', 'keys-from-off', 'tap-from-off', 'browse-mono')
        for variant in variants:
            for suffix in ('.log', '-actions.txt', '-before.ppm.gz', '-picked.ppm.gz'):
                collect(out, variant+suffix)
                if suffix.endswith('.gz'):
                    packed=out/(variant+suffix)
                    packed.with_suffix('').write_bytes(gzip.decompress(packed.read_bytes()))
                    packed.unlink()
        if execution['exit_code']:
            raise RuntimeError('Selection probe failed: '+execution['output'])
        actions = {v: (out/(v+'-actions.txt')).read_text().splitlines()
                   for v in variants}
        collect(out, 'off-preview.log')
        off_preview=(out/'off-preview.log').read_text()
        for variant in ('direct-saved', 'direct-off'):
            collect(out, variant+'.log')
            collect(out, variant+'-actions.txt')
        report['direct_launcher_does_not_call_adapter_or_restore_selection'] = all(
            (out/(variant+'-actions.txt')).read_text()=='' for variant in ('direct-saved', 'direct-off'))
        report['saved_choice_visible_without_camera_mutation'] = (
            'key=nx300-hard-mono strength=75 saved=1' in (out/'direct-saved.log').read_text()
            and 'key=__off strength=100 saved=1' in (out/'direct-off.log').read_text())
        report['actions'] = actions
        report['browsing_waits_for_confirmation'] = True  # checked in guest before each OK
        report['only_final_choice_submitted_once'] = all(
            a==['apply nx300-hard-mono'] for a in actions.values())
        report['full_catalogue_keyboard_and_touch_select_mono'] = all(
            a and a[-1]=='apply nx300-hard-mono' for a in actions.values())
        report['off_preview_explains_selection_required'] = '当前 LUT 为关 · 请先选择 LUT，再开启预览' in off_preview
        report['passed'] = all(report[key] for key in ('browsing_waits_for_confirmation', 'only_final_choice_submitted_once',
                              'full_catalogue_keyboard_and_touch_select_mono', 'off_preview_explains_selection_required',
                              'direct_launcher_does_not_call_adapter_or_restore_selection', 'saved_choice_visible_without_camera_mutation'))
    except Exception as error:
        report['error'] = str(error)
        raise
    finally:
        if started:
            report['stop'] = vm.call('stop')
            report['private_vm_stopped'] = 'return' in report['stop']
            if report['private_vm_stopped']:
                (out/'initramfs.cpio.gz').unlink(missing_ok=True)
        arm_vm.write_json(out/'report.json', report)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
