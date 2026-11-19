"""Settings-page LUT liveview fps row on real ARM nxui in a private VM.

Row index 4 on the system settings page (after software mode switch). Covers
the old-card default display state, 30/45/60 cycling with physical Down/OK
keys and a touch tap on the row, invalid saved values falling back to 30,
distinguished save failure (card write blocked, value untouched) and the
running-preview sync dispatch through the unchanged panel-actions chain with
a recording stub. The real camera panel and SD card stay named boundaries.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import arm_vm
import probe_menu_startup_vm as vm
import probe_fw_controls_vm as fw
from probe_lut_selection_vm import TAP_DRIVER
from probe_menu_entry_vm import collect

ROOT = Path(__file__).resolve().parents[1]

PROBE = fw.PROBE.split('# --- Pass 1:', 1)[0] + r'''
# A recording stub replaces the preview wrapper so the panel-actions chain
# runs unchanged while the sensor-rate switch itself is covered by the S2
# engine probe (probe_preview_fps_vm.py).
cat > /perf/mod/smartfilter-preview.sh <<'EOF'
#!/bin/sh
printf 'FPS_STUB %s\n' "$*" >> /perf/fps-sync.log
[ "$1" = capabilities ] && printf 'continuous-s2\n'
exit 0
EOF
chmod 500 /perf/mod/smartfilter-preview.sh
key() { /perf/close.arm key "$1"; /rescue/busybox usleep 200000; }
tap() { /perf/close.arm tap "$1" "$2"; /rescue/busybox usleep 300000; }
fps_row() { key KP_Down; key KP_Down; key KP_Down; key KP_Down; }

# --- Pass A: old card without the preference; Down/OK cycles 30->45->60->30.
NX_STATE_DIR=/tmp/fps-passA /rescue/busybox timeout 60 /perf/mod/bin/nxui.arm --mfw-services \
    --root /perf/mod --font-file /usr/share/fonts/SDIC_GP_US_20120720.ttf \
    --font-metadata /perf/mod/fonts/mfw-font.charset > /perf/fps-a.log 2>&1 &
panel=$!
attempt=0
until grep -q '^FIRST_FRAME ' /perf/fps-a.log; do
    kill -0 "$panel"; attempt=$((attempt+1)); [ "$attempt" -lt 150 ]; /rescue/busybox usleep 100000
done
[ ! -e /perf/mod/.state/lut-fps ]
for key_name in KP_Right KP_Right KP_Right KP_Right KP_Enter; do key "$key_name"; done
# KP_Down moves the feature-page selection directly (no per-step log line);
# row identity is proven by the pref value that only the fps row writes.
fps_row
key KP_Enter
[ "$(cat /perf/mod/.state/lut-fps)" = 45 ]
fps_row
key KP_Enter
[ "$(cat /perf/mod/.state/lut-fps)" = 60 ]
fps_row
key KP_Enter
[ "$(cat /perf/mod/.state/lut-fps)" = 30 ]
! grep -q '^BACKEND kind=5' /perf/fps-a.log
key Menu; key Menu
wait "$panel"

# --- Pass B: invalid saved values fall back to 30, then cycle from there.
printf 'abc\n' > /perf/mod/.state/lut-fps
NX_STATE_DIR=/tmp/fps-passB /rescue/busybox timeout 60 /perf/mod/bin/nxui.arm --mfw-services \
    --root /perf/mod --font-file /usr/share/fonts/SDIC_GP_US_20120720.ttf \
    --font-metadata /perf/mod/fonts/mfw-font.charset > /perf/fps-b.log 2>&1 &
panel=$!
attempt=0
until grep -q '^FIRST_FRAME ' /perf/fps-b.log; do
    kill -0 "$panel"; attempt=$((attempt+1)); [ "$attempt" -lt 150 ]; /rescue/busybox usleep 100000
done
for key_name in KP_Right KP_Right KP_Right KP_Right KP_Enter; do key "$key_name"; done
fps_row
key KP_Enter
[ "$(cat /perf/mod/.state/lut-fps)" = 45 ]
key Menu; key Menu
wait "$panel"

# --- Pass B2: the whole file is one enum value and a single LF — the same
# grammar the control script reads (probe_preview_fps_vm B5). Every
# malformed byte sequence displays the fallback, so OK cycles to 45.
gidx=0
for variant in '45xyz\n' ' 45\n' '45' '45\n60\n' '45\n\n'; do
    gidx=$((gidx+1))
    printf "$variant" > /perf/mod/.state/lut-fps
    NX_STATE_DIR=/tmp/fps-passG-$gidx /rescue/busybox timeout 60 /perf/mod/bin/nxui.arm --mfw-services \
        --root /perf/mod --font-file /usr/share/fonts/SDIC_GP_US_20120720.ttf \
        --font-metadata /perf/mod/fonts/mfw-font.charset > /perf/fps-g-$gidx.log 2>&1 &
    panel=$!
    attempt=0
    until grep -q '^FIRST_FRAME ' /perf/fps-g-$gidx.log; do
        kill -0 "$panel"; attempt=$((attempt+1)); [ "$attempt" -lt 150 ]; /rescue/busybox usleep 100000
    done
    for key_name in KP_Right KP_Right KP_Right KP_Right KP_Enter; do key "$key_name"; done
    fps_row
    key KP_Enter
    [ "$(cat /perf/mod/.state/lut-fps)" = 45 ]
    key Menu; key Menu
    wait "$panel"
done
rm -f /perf/mod/.state/lut-fps

# --- Pass C: a blocked card write is a save failure, never a silent selection.
rm -f /perf/mod/.state/lut-fps
mv /perf/mod/.state /perf/mod/.state-keep
printf blocked > /perf/mod/.state
NX_STATE_DIR=/tmp/fps-passC /rescue/busybox timeout 60 /perf/mod/bin/nxui.arm --mfw-services \
    --root /perf/mod --font-file /usr/share/fonts/SDIC_GP_US_20120720.ttf \
    --font-metadata /perf/mod/fonts/mfw-font.charset > /perf/fps-c.log 2>&1 &
panel=$!
attempt=0
until grep -q '^FIRST_FRAME ' /perf/fps-c.log; do
    kill -0 "$panel"; attempt=$((attempt+1)); [ "$attempt" -lt 150 ]; /rescue/busybox usleep 100000
done
for key_name in KP_Right KP_Right KP_Right KP_Right KP_Enter; do key "$key_name"; done
fps_row
key KP_Enter
[ ! -e /perf/mod/.state/lut-fps ]
rm /perf/mod/.state
mv /perf/mod/.state-keep /perf/mod/.state
key KP_Enter
[ "$(cat /perf/mod/.state/lut-fps)" = 45 ]
key Menu; key Menu
wait "$panel"

# --- Pass D: touch on the row cycles too.
NX_STATE_DIR=/tmp/fps-passD /rescue/busybox timeout 60 /perf/mod/bin/nxui.arm --mfw-services \
    --root /perf/mod --font-file /usr/share/fonts/SDIC_GP_US_20120720.ttf \
    --font-metadata /perf/mod/fonts/mfw-font.charset > /perf/fps-d.log 2>&1 &
panel=$!
attempt=0
until grep -q '^FIRST_FRAME ' /perf/fps-d.log; do
    kill -0 "$panel"; attempt=$((attempt+1)); [ "$attempt" -lt 150 ]; /rescue/busybox usleep 100000
done
for key_name in KP_Right KP_Right KP_Right KP_Right KP_Enter; do key "$key_name"; done
tap 240 274
[ "$(cat /perf/mod/.state/lut-fps)" = 60 ]
key Menu; key Menu
wait "$panel"

# --- Pass E: preview running with a saved selection dispatches preview-sync.
printf 'smartfilter\n' > /var/run/nx300m-preview-backend
printf '3 /perf/mod/looks/x/table.d4lut 90\n' > /var/run/nx300m-lut-selection
mkdir -p /tmp/nx300m-mfw/state
rm -f /perf/fps-sync.log
NX_STATE_DIR=/tmp/fps-passE /rescue/busybox timeout 60 /perf/mod/bin/nxui.arm --mfw-services \
    --root /perf/mod --font-file /usr/share/fonts/SDIC_GP_US_20120720.ttf \
    --font-metadata /perf/mod/fonts/mfw-font.charset > /perf/fps-e.log 2>&1 &
panel=$!
attempt=0
until grep -q '^FIRST_FRAME ' /perf/fps-e.log; do
    kill -0 "$panel"; attempt=$((attempt+1)); [ "$attempt" -lt 150 ]; /rescue/busybox usleep 100000
done
for key_name in KP_Right KP_Right KP_Right KP_Right KP_Enter; do key "$key_name"; done
fps_row
key KP_Enter
[ "$(cat /perf/mod/.state/lut-fps)" = 30 ]
attempt=0
until grep -q 'FPS_STUB sync' /perf/fps-sync.log; do
    kill -0 "$panel"; attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 100000
done
wait "$panel"
rm -f /var/run/nx300m-preview-backend /var/run/nx300m-lut-selection
kill "$bridge" 2>/dev/null || :
kill "$stock" 2>/dev/null || :
trap - EXIT
echo FPS_SETTINGS_PROBE_DONE
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    out = args.output_dir.resolve()
    if (out / 'report.json').exists():
        raise RuntimeError('Preserve recorded evidence; use a new output directory')
    vm.OUT = out
    vm.PROBE = PROBE
    vm.DRIVER = vm.DRIVER.replace('    if(argc==2) {', TAP_DRIVER + '\n    if(argc==2) {', 1)
    vm.DRIVER = vm.DRIVER.replace('    XEvent event={0};', '    if(capture_only){XCloseDisplay(d);return 0;}\n    XEvent event={0};', 1)
    vm.DRIVER = vm.DRIVER.replace('XStringToKeysym("Menu")', 'XStringToKeysym(argc==3 ? argv[2] : "Menu")')
    vm.EXTRA_FILES = {
        'perf/mod/mfw-panel-actions.sh': (ROOT/'mod/deployment/mfw/panel-actions.sh').read_bytes(),
        'perf/mod/camera_control.sh': (ROOT/'mod/deployment/camera_control.sh').read_bytes(),
        'perf/mod/gui_mfw.NX300M': (ROOT/'mod/deployment/mfw/gui_mfw.NX300M').read_bytes(),
        'perf/mod/fonts/mfw-font.charset': (ROOT/'outputs/metol/mfw-font.charset').read_bytes(),
        'perf/mod/fonts/fonts.conf.in': (ROOT/'mod/deployment/mfw/fonts.conf.in').read_bytes(),
        'perf/fake-module': b'NX300M fixture camera_firmware_mod.so payload\n',
        'perf/exposure-module': b'NX300M fixture exposure_controls.so payload\n',
    }
    vm.prepare()
    (out/'bridge.c').write_text(fw.BRIDGE, encoding='utf-8')
    shell = f'''set -eu
tc=/opt/nx300m-toolchain/usr
rf=/opt/nx300m-lab/stock-1.15
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" -O2 -Wall \\
 -I"$tc/include" -mfloat-abi=soft -fno-pic -no-pie -fno-stack-protector \\
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 \\
 -Wl,--allow-shlib-undefined -Wl,-rpath-link,"$rf/usr/lib" -Wl,-rpath-link,"$rf/lib" \\
 {vm.linux_path(out/'bridge.c')} \\
 {vm.linux_path(ROOT)}/work/reference/metol/test_server/filmlab/src/crt0_arm.S \\
 "$rf/lib/libc-2.13.so" -lgcc -o {vm.linux_path(out/'bridge.arm')}
'''
    built = subprocess.run(['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'sh', '-c', shell],
                           capture_output=True, text=True, encoding='utf-8', errors='replace')
    (out/'bridge-build.log').write_text(built.stdout+built.stderr, encoding='utf-8')
    if built.returncode:
        raise RuntimeError('Fixture bridge build exit '+str(built.returncode))
    import gzip
    import io
    import shutil
    from build_stock_app_loader import read_cpio
    entries = read_cpio(gzip.decompress((out/'overlay.cpio.gz').read_bytes()))
    entries['usr/bin/di-camera-app-nx300'] = (0o100755, (out/'bridge.arm').read_bytes())
    overlay = io.BytesIO()
    for name, (mode, data) in entries.items():
        if name != 'TRAILER!!!':
            arm_vm.cpio_add(overlay, name, mode, data)
    arm_vm.cpio_add(overlay, 'TRAILER!!!', 0)
    (out/'overlay.cpio.gz').write_bytes(gzip.compress(overlay.getvalue(), mtime=0))
    with (out/'initramfs.cpio.gz').open('wb') as target:
        shutil.copyfileobj((arm_vm.WORK/'initramfs.cpio.gz').open('rb'), target)
        target.write(gzip.compress(overlay.getvalue(), mtime=0))
    report = dict(passed=False, hardware_verified=False,
                  scope='Real ARM nxui settings page; key/tap input, card prefs and panel-actions chain; no camera, no SD',
                  candidate_sha256=hashlib.sha256((ROOT/'outputs/metol/nxui.arm').read_bytes()).hexdigest())
    started = False
    try:
        vm.call('start'); started = True
        import time
        deadline = time.monotonic()+180
        while time.monotonic() < deadline:
            if (out/'serial.log').exists() and 'MENU_PERF_VM_READY' in (out/'serial.log').read_text(errors='replace'):
                break
            time.sleep(.5)
        else:
            raise TimeoutError('Fps settings VM startup')
        execution = vm.call('exec', '/bin/sh /perf/probe.sh')
        arm_vm.write_json(out/'execution.json', execution)
        for name in ('fps-a.log', 'fps-b.log', 'fps-c.log', 'fps-d.log', 'fps-e.log', 'fps-sync.log'):
            try:
                collect(out, name)
            except RuntimeError:
                pass
        if execution['exit_code'] or 'FPS_SETTINGS_PROBE_DONE' not in execution['output']:
            raise RuntimeError('Fps settings probe failed: '+execution['output'][-4000:])
        def log(name):
            path = out/name
            return path.read_text(errors='replace') if path.exists() else ''
        a, b, c, d, e = log('fps-a.log'), log('fps-b.log'), log('fps-c.log'), log('fps-d.log'), log('fps-e.log')
        report['checks'] = dict(
            # The pref-value progression is asserted inside the guest with
            # set -e: only the fps row writes .state/lut-fps, so reaching
            # FPS_SETTINGS_PROBE_DONE proves the key/tap row hits and cycles.
            guest_assertions_passed='FPS_SETTINGS_PROBE_DONE' in execution['output'],
            preview_sync_dispatched='FPS_STUB sync' in log('fps-sync.log'),
            keys_no_backend_actions=all('BACKEND kind=5' not in log(name)
                                        for name in ('fps-a.log', 'fps-b.log', 'fps-c.log', 'fps-d.log')),
        )
        report['passed'] = all(report['checks'].values())
        if not report['passed']:
            raise AssertionError(report['checks'])
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
