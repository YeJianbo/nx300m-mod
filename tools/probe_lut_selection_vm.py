"""Reproduce physical LUT selection with native ARM nxui and the full catalogue."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import time
from zipfile import ZipFile

import arm_vm
import probe_menu_startup_vm as vm
from probe_menu_entry_vm import collect
from wsl_paths import wsl_path

ROOT = Path(__file__).resolve().parents[1]
TAP_DRIVER = r'''
    if(argc==6 && !strcmp(argv[1],"drag")) {
        XWindowAttributes a;if(!XGetWindowAttributes(d,w,&a))return 6;
        XEvent e={0};e.xbutton.display=d;e.xbutton.window=w;e.xbutton.button=Button1;
        e.xbutton.x=a.width-1-atoi(argv[3]);e.xbutton.y=atoi(argv[2]);
        e.type=ButtonPress;XSendEvent(d,w,False,ButtonPressMask,&e);XSync(d,False);usleep(120000);
        e.type=MotionNotify;e.xmotion.state=Button1Mask;
        e.xmotion.x=a.width-1-atoi(argv[5]);e.xmotion.y=atoi(argv[4]);
        XSendEvent(d,w,False,Button1MotionMask,&e);XSync(d,False);usleep(120000);
        e.type=ButtonRelease;e.xbutton.button=Button1;e.xbutton.state=Button1Mask;
        XSendEvent(d,w,False,ButtonReleaseMask,&e);XSync(d,False);
        XCloseDisplay(d);return 0;
    }
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
export DISPLAY=:97 NX300M_MOD_ROOT=/perf/mod FIXTURE_HOOK_READY=1 NX300M_LUT_SKIP_PREVIEW_SYNC=1
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
wait_backend() {
    attempt=0
    until grep -q '^BACKEND kind=1 ok=1' "$1"; do
        kill -0 "$panel"; attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 100000
    done
    if [ "${2:-stay}" != exit ]; then kill -0 "$panel"; fi
}
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
        tap-from-off) /perf/close.arm tap 540 166 ;;
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
    wait_backend /perf/$variant.log exit
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
# Real ARM staging engine, with only the camera-ready gate replaced. Validate
# inline key adjustments, reference pixels, saved preferences and favourites.
/perf/tune-engine.arm apply nx300-hard-mono 100 0 0 0 0 0 0 0 > /perf/tune-setup.log 2>&1
cp /var/run/nx300m-lut-selection /perf/tune-active-before.txt
NX_STATE_DIR=/tmp/select-tune /rescue/busybox timeout 20 /perf/mod/bin/nxui.arm --live --lut \
    --root /perf/mod --recipes /perf/mod/looks.txt --engine /perf/tune-engine.arm \
    --font-file /usr/share/fonts/SDIC_GP_US_20120720.ttf \
    --font-metadata /perf/mod/fonts/mfw-font.charset > /perf/tune.log 2>&1 &
panel=$!
attempt=0
until grep -q '^FIRST_FRAME ' /perf/tune.log; do
    kill -0 "$panel"; attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 100000
done
# Wrap above the first parameter, then use the wheel. It must stay in tuning.
for key in XF86HomePage Up XF86ScrollDown XF86ScrollUp Down XF86ScrollUp Down XF86ScrollDown; do /perf/close.arm key "$key"; done
# A reference-image tap must not switch wheel ownership back to LUT selection.
/perf/close.arm tap 120 180
for key in XF86ScrollUp XF86ScrollDown; do /perf/close.arm key "$key"; done
# Black-and-white has no saturation to amplify. Touch must leave it at zero.
/perf/close.arm drag 596 208 714 208
/perf/close.arm tap 90 418
/rescue/busybox usleep 300000
/perf/close.arm snapshot /perf/tune-inline.ppm
/perf/close.arm key XF86HomePage
attempt=0
until grep -q '^LUT_PRESET_SAVED key=nx300-hard-mono applied=0' /perf/tune.log; do
    kill -0 "$panel"; attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 100000
done
kill -0 "$panel"
! grep -q '^LUT_SUBMIT\|^BACKEND kind=1' /perf/tune.log
cmp /perf/tune-active-before.txt /var/run/nx300m-lut-selection
grep -q '^LUT_VIEW list=0 key=nx300-hard-mono' /perf/tune.log
grep -q '^LUT_VIEW list=1 key=nx300-hard-mono saving=0' /perf/tune.log
/perf/close.arm key Menu
wait "$panel"
grep -hE '^NXTP1 [0-9]+ [0-9a-f]{8} 1 nx300-hard-mono 1 -1 0 0 0 0 0 100$' \
    /perf/mod/.state/lut-preset-nx300-hard-mono.[01] > /perf/tune-state.txt
grep -q '^nx300-hard-mono$' /perf/mod/.state/lut-favorites
NX_STATE_DIR=/tmp/select-tune-reopen /rescue/busybox timeout 20 /perf/mod/bin/nxui.arm --live --lut \
    --root /perf/mod --recipes /perf/mod/looks.txt --engine /perf/tune-engine.arm \
    --font-file /usr/share/fonts/SDIC_GP_US_20120720.ttf \
    --font-metadata /perf/mod/fonts/mfw-font.charset > /perf/tune-reopen.log 2>&1 &
panel=$!
attempt=0
until grep -q '^FIRST_FRAME ' /perf/tune-reopen.log; do
    kill -0 "$panel"; attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 100000
done
grep -q 'LUT_TUNE key=nx300-hard-mono values=1,-1,0,0,0,0,0' /perf/tune-reopen.log
/rescue/busybox usleep 300000
/perf/close.arm snapshot /perf/tune-favorites.ppm
/perf/close.arm key XF86HomePage
/rescue/busybox usleep 300000
/perf/close.arm snapshot /perf/tune-color.ppm
/perf/close.arm key KP_Enter
wait_backend /perf/tune-reopen.log exit
wait "$panel"
grep -q '/looks/nx-tune-' /var/run/nx300m-lut-selection
gzip /perf/tune-inline.ppm /perf/tune-favorites.ppm /perf/tune-color.ppm
# Without a film style, Fn enters the identity preset; its adjustments still
# use the production ARM engine and are distinct from the red Off action.
printf '0\n' > /var/run/nx300m-lut-selection
rm -f /perf/mod/.state/lut-category
NX_STATE_DIR=/tmp/select-original /rescue/busybox timeout 20 /perf/mod/bin/nxui.arm --live --lut \
    --root /perf/mod --recipes /perf/mod/looks.txt --engine /perf/tune-engine.arm \
    --font-file /usr/share/fonts/SDIC_GP_US_20120720.ttf \
    --font-metadata /perf/mod/fonts/mfw-font.charset > /perf/original.log 2>&1 &
panel=$!
attempt=0
until grep -q '^FIRST_FRAME ' /perf/original.log; do
    kill -0 "$panel"; attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 100000
done
for key in Up XF86HomePage XF86ScrollUp XF86HomePage; do /perf/close.arm key "$key"; done
attempt=0
until grep -q '^LUT_PRESET_SAVED key=nx300-adjust-base applied=0' /perf/original.log; do
    kill -0 "$panel"; attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 100000
done
! grep -q '^LUT_SUBMIT\|^BACKEND kind=1' /perf/original.log
grep -hE '^NXTP1 [0-9]+ [0-9a-f]{8} 1 nx300-adjust-base 1 0 0 0 0 0 0 100$' \
    /perf/mod/.state/lut-preset-nx300-adjust-base.[01]
/perf/close.arm key Menu
wait "$panel"
printf 'nx300-hard-mono\nnx300-fuji-classic-chrome\n' > /perf/mod/.state/lut-favorites
/perf/tune-engine.arm apply nx300-hard-mono 100 > /perf/cycle-setup.log 2>&1
cp /perf/tune-engine.arm /perf/mod/bin/lut_engine.arm
NX300M_MOD_SOURCE_ROOT=/perf/mod /bin/sh /perf/mod/lut-cycle.sh > /perf/cycle-tuned.log 2>&1
grep -q '/looks/nx300-fuji-classic-chrome/table.d4lut' /var/run/nx300m-lut-selection
# Up inside the list must select its previous look. Only Up at its first row
# transfers focus to groups. Browsing stays local until OK confirmation.
printf '0\n' > /perf/mod/.state/lut-category
: > /perf/actions.txt
NX_STATE_DIR=/tmp/select-groups /perf/mod/bin/nxui.arm --live --lut \
    --root /perf/mod --recipes /perf/mod/looks.txt --engine /perf/engine.sh \
    --font-file /usr/share/fonts/SDIC_GP_US_20120720.ttf \
    --font-metadata /perf/mod/fonts/mfw-font.charset > /perf/groups.log 2>&1 &
panel=$!
attempt=0
until grep -q '^FIRST_FRAME ' /perf/groups.log; do
    kill -0 "$panel"; attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 100000
done
/perf/close.arm key Up
/rescue/busybox usleep 300000
! grep -q '^LUT_GROUP focus=1' /perf/groups.log
grep -q 'LUT_CHOICE event=select index=2 key=nx300-hard-mono' /perf/groups.log
attempt=0
until grep -q '^LUT_GROUP focus=1' /perf/groups.log; do
    attempt=$((attempt+1)); [ "$attempt" -lt 10 ]
    /perf/close.arm key Up
    /rescue/busybox usleep 100000
done
for key in Right XF86ScrollUp; do /perf/close.arm key "$key"; done
/rescue/busybox usleep 300000
grep -q '^LUT_GROUP focus=1 group=2' /perf/groups.log
/rescue/busybox usleep 300000
/perf/close.arm snapshot /perf/groups-focus.ppm
/perf/close.arm key Down
/rescue/busybox usleep 300000
grep -q '^LUT_GROUP focus=0 group=2' /perf/groups.log
[ ! -s /perf/actions.txt ]
/perf/close.arm key Up
/perf/close.arm key KP_Enter
/rescue/busybox usleep 300000
[ ! -s /perf/actions.txt ]
/perf/close.arm key KP_Enter
wait_backend /perf/groups.log exit
wait "$panel"
gzip /perf/groups-focus.ppm
# The real LUT header button must dispatch through RAM even when the UI root
# is the SD-style data directory and an old Fn-menu service root is inherited.
base=/tmp/lut-menu-service
mkdir -p "$base/runtime" "$base/state"
printf '1 /perf/mod/looks/nx300-fuji-classic-chrome/table.d4lut 100\n' > /perf/mod/.state/lut-choice
cat > "$base/runtime/mfw-panel-actions.sh" <<'RAM_ACTION'
#!/bin/sh
set -eu
[ "$1" = preview-s2-enable ]
[ "$NX300M_MOD_ROOT" = /tmp/lut-menu-service/runtime ]
[ "$NX_MOD_ROOT" = "$NX300M_MOD_ROOT" ]
[ "$NX_FN_SERVICE_ROOT" = "$NX300M_MOD_ROOT" ]
[ "$NX_STATE_DIR" = /tmp/lut-menu-service/state ]
[ "$NX300M_MOD_SOURCE_ROOT" = /perf/mod ]
[ "$NX300M_MOD_SOURCE_ID" = "$(stat -c '%d:%i' /perf/mod)" ]
printf 'RAM_PREVIEW_ROUTE_OK\n'
RAM_ACTION
# Fail closed if the broken SD route is accidentally used again.
printf '#!/bin/sh\necho WRONG_SD_SERVICE_ROOT\nexit 9\n' > /perf/mod/mfw-panel-actions.sh
for variant in standalone inherited-fn; do
    inherited_service=
    [ "$variant" != inherited-fn ] || inherited_service=/perf/mod/fn-panel
    NX300M_MFW_RAM_BASE="$base" NX_FN_SERVICE_ROOT="$inherited_service" \
    NX300M_MOD_SOURCE_ROOT=/perf/mod NX300M_MOD_SOURCE_ID="$(stat -c '%d:%i' /perf/mod)" \
    /perf/mod/bin/nxui.arm --live --lut --root /perf/mod --recipes /perf/mod/looks.txt \
        --engine /perf/engine.sh --font-file /usr/share/fonts/SDIC_GP_US_20120720.ttf \
        --font-metadata /perf/mod/fonts/mfw-font.charset > /perf/ram-route-$variant.log 2>&1 &
    panel=$!
    attempt=0
    until grep -q '^FIRST_FRAME ' /perf/ram-route-$variant.log; do
        kill -0 "$panel"; attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 100000
    done
    /perf/close.arm tap 310 30
    attempt=0
    until grep -q '^BACKEND kind=5 ok=1 .*RAM_PREVIEW_ROUTE_OK' /perf/ram-route-$variant.log; do
        kill -0 "$panel"; attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 100000
    done
    /perf/close.arm key Menu
    wait "$panel"
done
kill "$stock"
trap - EXIT
'''


RENDER_PROBE=r'''
# Same guest, source tables and acknowledged input sequence. No hardware work.
printf '1 /perf/mod/looks/nx300-hard-mono/table.d4lut 100\n' > /var/run/nx300m-lut-selection
rm -f /perf/mod/.state/lut-category
for trial in 1 2; do
  for variant in released current; do
    binary=/perf/released.arm
    [ "$variant" != current ] || binary=/perf/mod/bin/nxui.arm
    log=/perf/render-$variant-$trial.log
    NX_PANEL_TRACE=1 NX_STATE_DIR=/tmp/render-$variant-$trial "$binary" --live --lut \
        --root /perf/mod --recipes /perf/mod/looks.txt --engine /perf/tune-engine.arm \
        --font-file /usr/share/fonts/SDIC_GP_US_20120720.ttf \
        --font-metadata /perf/mod/fonts/mfw-font.charset > "$log" 2>&1 &
    panel=$!
    attempt=0
    until grep -q '^FIRST_FRAME ' "$log"; do
        kill -0 "$panel"; attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 50000
    done
    for key in XF86HomePage Down Up Down Up Down Up Down Up Down Up Down Up Right Left Right Left Right Left Right Left Right Left Right Left; do
        before=$(grep -c '^PANEL_FRAME .*painted=1' "$log" || :)
        /perf/close.arm key "$key"
        attempt=0
        until [ "$(grep -c '^PANEL_FRAME .*painted=1' "$log" || :)" -gt "$before" ]; do
            kill -0 "$panel"; attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 20000
        done
    done
    /perf/close.arm key Menu
    wait "$panel"
  done
done
'''

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT/'outputs/performance/menu-event-loop/lut-selection/before')
    parser.add_argument('--render-baseline', type=Path, help='用同一 ARM guest 对比已发布菜单的逐次绘制耗时')
    parser.add_argument('--navigation-only', action='store_true', help='只检查列表边界、分类导航和预览按钮路由')
    args = parser.parse_args()
    out = args.output_dir.resolve()
    if (out/'report.json').exists():
        raise RuntimeError('Preserve the recorded selection run')
    archive = ROOT/'outputs/looks/lightroom-import/on-camera-test/nx300m-lut-color-test.zip'
    with ZipFile(archive) as package:
        catalogue = package.read('nx300m-mod/looks.txt').decode().rstrip()
    if not any(row.startswith('nx300-adjust-base|') for row in catalogue.splitlines()):
        catalogue += '\nnx300-adjust-base|原图调整|0|0|0|0|0|0|0'
    vm.OUT = out
    vm.PROBE = PROBE.replace('@CATALOGUE@', catalogue)
    if args.navigation_only:
        start=vm.PROBE.index('# Up inside the list')
        prefix=vm.PROBE[:vm.PROBE.index('for variant in saved-mono')]
        setup="mkdir -p /perf/mod/.state\nprintf '1 /perf/mod/looks/nx300-fuji-classic-chrome/table.d4lut 100\\n' > /var/run/nx300m-lut-selection\n"
        vm.PROBE=prefix+setup+vm.PROBE[start:]
    if args.render_baseline:
        vm.PROBE=vm.PROBE.replace('kill "$stock"\ntrap - EXIT',RENDER_PROBE+'\nkill "$stock"\ntrap - EXIT')
    vm.DRIVER = vm.DRIVER.replace('    if(argc==2) {', TAP_DRIVER+'\n    if(argc==2) {', 1)
    vm.DRIVER = vm.DRIVER.replace('    XEvent event={0};', '    if(capture_only){XCloseDisplay(d);return 0;}\n    XEvent event={0};', 1)
    vm.DRIVER = vm.DRIVER.replace('XStringToKeysym("Menu")', 'XStringToKeysym(argc==3 ? argv[2] : "Menu")')
    launcher = ROOT/'mod/deployment/mfw/lut-panel.sh'
    vm.EXTRA_FILES = {
        'perf/mod/lut-panel.sh': launcher.read_bytes(),
        'perf/mod/lut-cycle.sh': (ROOT/'mod/deployment/mfw/lut-cycle.sh').read_bytes(),
        'perf/mod/bin/lut_engine.arm': b'#!/bin/sh\nprintf "%s\\n" "$*" >> /perf/actions.txt\nexit 0\n',
    }
    from probe_lut_choice_store import FIXTURE
    out.mkdir(parents=True,exist_ok=True)
    (out/'tune-engine.c').write_text(FIXTURE,encoding='utf-8')
    build=f'''set -eu
cd {shlex.quote(wsl_path(ROOT))}
tc=/opt/nx300m-toolchain/usr
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
rf=/opt/nx300m-lab/stock-1.15
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" -std=c99 -O1 -Wall -Wextra -Werror -D_GNU_SOURCE -include mod/metol/remote/compat.h -I. -U_TIME_BITS -D_TIME_BITS=32 -U_FILE_OFFSET_BITS -D_FILE_OFFSET_BITS=64 -mfloat-abi=soft -fno-pic -no-pie -fno-stack-protector -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 -Wl,--allow-shlib-undefined -Wl,-rpath-link,"$rf/lib" {shlex.quote(wsl_path(out/'tune-engine.c'))} mod/metol/remote/compat.c work/reference/metol/test_server/filmlab/src/crt0_arm.S "$rf/lib/libc-2.13.so" -lgcc -o {shlex.quote(wsl_path(out/'tune-engine.arm'))}
'''
    compiled=subprocess.run(['wsl.exe','-u','root','-e','sh','-c',build],capture_output=True,text=True)
    (out/'tune-engine-build.log').write_text(compiled.stdout+compiled.stderr,encoding='utf-8')
    compiled.check_returncode()
    vm.EXTRA_FILES['perf/tune-engine.arm']=(out/'tune-engine.arm').read_bytes()
    vm.EXTRA_FILES['perf/mod/looks/nx300-adjust-base/table.d4lut']=(ROOT/'outputs/review/2026-11-05-agy/catalog/looks/nx300-adjust-base/table.d4lut').read_bytes()
    if args.render_baseline:
        vm.EXTRA_FILES['perf/released.arm']=args.render_baseline.read_bytes()
    with ZipFile(archive) as package:
        vm.EXTRA_FILES.update({'perf/mod/'+name.removeprefix('nx300m-mod/'):package.read(name)
                               for name in package.namelist() if '/looks/' in name and name.endswith('.d4lut')})
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
        if execution['exit_code']:
            for name in ('tune.log','tune-state.txt','groups.log','ram-route-standalone.log','ram-route-inherited-fn.log'):
                try:collect(out,name)
                except RuntimeError:pass  # Later scenarios may not have run.
            raise RuntimeError('Selection probe failed: '+execution['output'])
        if args.navigation_only:
            for name in ('groups.log','groups-focus.ppm.gz','ram-route-standalone.log','ram-route-inherited-fn.log'):
                collect(out,name)
            report.update(passed=True,list_up_moves_previous_before_entering_groups=True,
                          first_row_up_enters_group_and_down_returns=True,
                          lut_header_preview_dispatches_ram_service_with_data_identity=True)
            return
        for variant in variants:
            for suffix in ('.log', '-actions.txt'):
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
        for name in ('tune.log','tune-reopen.log','original.log','tune-inline.ppm.gz','cycle-tuned.log',
                     'groups.log','groups-focus.ppm.gz','ram-route-standalone.log','ram-route-inherited-fn.log'):
            collect(out,name)
        report['list_up_moves_previous_before_entering_groups']=True
        report['first_row_up_enters_group_and_down_returns']=True
        report['lut_header_preview_dispatches_ram_service_with_data_identity']=True
        report['inline_tuning_persistence_favorites_and_reference_checked']=True
        report['fn_saves_without_exit_and_wheel_adjusts']=True
        report['ok_saves_and_returns_viewfinder']=True
        report['touch_browsing_does_not_submit']=True
        report['identity_preset_tuning_saved_without_film']=True
        report['tuned_zero_ui_cycle_and_favorites_checked']=True
        report['lut_cycle_sha256']=hashlib.sha256((ROOT/'mod/deployment/mfw/lut-cycle.sh').read_bytes()).hexdigest()
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
        if args.render_baseline:
            import statistics
            measurements={}
            for variant in ('released','current'):
                latency=[]
                for trial in (1,2):
                    name=f'render-{variant}-{trial}.log';collect(out,name)
                    log=(out/name).read_text()
                    latency += [int(b)-int(a) for a,b in re.findall(r'PANEL_FRAME .*dequeued_ms=(\d+) submitted_ms=(\d+).*painted=1',log)]
                if len(latency)<20:raise RuntimeError('Insufficient measured render frames: '+variant)
                latency.sort()
                measurements[variant]=dict(frames=len(latency),p50_ms=statistics.median(latency),
                    p95_ms=latency[int((len(latency)-1)*.95)])
            report['render_comparison_arm_vm']=measurements
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
