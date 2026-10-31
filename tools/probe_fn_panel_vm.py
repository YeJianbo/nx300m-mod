"""Exercise restored Fn entry + original ARM box in isolated Xvfb, not camera hardware."""
import argparse
import difflib
import gzip
import io
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import time
import zipfile

import arm_vm
from build_stock_app_loader import read_cpio
import probe_menu_startup_vm as vm
from probe_menu_entry_vm import collect

PROJECT = Path(__file__).resolve().parents[1]

KEY_DRIVER = r'''
#include <fcntl.h>
#include <linux/input.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
int main(int argc,char **argv) {
    if(argc!=3)return 2;
    int fd=open("/perf/fn-input",O_WRONLY|O_NONBLOCK);if(fd<0)return 3;
    struct input_event event={0};event.type=EV_KEY;event.code=atoi(argv[1]);event.value=1;
    if(write(fd,&event,sizeof event)!=sizeof event)return 5;
    usleep(atoi(argv[2])*1000);event.value=0;
    if(write(fd,&event,sizeof event)!=sizeof event)return 6;
    close(fd);return 0;
}
'''

PROBE = r'''#!/bin/sh
set -eu
export DISPLAY=:97 NX300M_MOD_ROOT=/perf/mod NX_STATE_DIR=/perf/state
export NX_REMOTE_CUSTOM_KEY=0 NX_REMOTE_MENU_KEY=0 NX_REMOTE_PANEL_SHORTCUT=1
# Exercise the SD evidence path too: the daemon must append chain events here.
export NX_FN_KEY_LOG=/perf/mod/logs/fn-key.log
export NX300M_MOD_SOURCE_ROOT=/perf/mod
export NX_REMOTE_PANEL_KEY=custom
mkdir -p /perf/state /perf/photos /perf/mod/logs
mkfifo /perf/fn-input
until /perf/close.arm ping; do sleep 1; done
/perf/close.arm stock-parent > /perf/stock-parent.log 2>&1 &
stock=$!
/perf/remote-fixture.arm --listen 127.0.0.1:8781 --web-root /perf/mod/web_root --media-root /perf/photos > /perf/remote.log 2>&1 &
remote=$!
trap 'kill "$remote" "$stock" 2>/dev/null || :' EXIT
attempt=0
until wget -T 2 -Y off -q -O /perf/health.json http://127.0.0.1:8781/api/v1/health; do
    attempt=$((attempt+1)); [ "$attempt" -lt 20 ]; sleep 1
done
attempt=0
until wget -T 2 -Y off -q -O /perf/fn-entry.json http://127.0.0.1:8781/api/v1/panel/entry && grep -q '"state":"ready"' /perf/fn-entry.json; do
    attempt=$((attempt+1)); [ "$attempt" -lt 20 ]; sleep 1
done
code=$(sed -n 's/.*"linux_keycode":\([0-9][0-9]*\).*/\1/p' /perf/fn-entry.json)
[ -n "$code" ]
/perf/fn.arm "$code" 150
sleep 1
! grep -q '^FIRST_FRAME ' /perf/remote.log
for pass in first second; do
    /perf/fn.arm "$code" 1900
    expected=1; [ "$pass" != second ] || expected=2
    attempt=0
    until [ "$(grep -c '^FIRST_FRAME ' /perf/mod/logs/fn-key.log || :)" -eq "$expected" ]; do
        attempt=$((attempt+1)); [ "$attempt" -lt 15 ]; sleep 1
    done
    sleep 2
    /perf/close.arm /perf/$pass.ppm
    attempt=0
    until [ "$(grep -c '^EXIT ' /perf/mod/logs/fn-key.log || :)" -eq "$expected" ]; do
        attempt=$((attempt+1)); [ "$attempt" -lt 10 ]; sleep 1
    done
    sleep 1
done
# Custom-button gestures: a short tap dispatches the zero-UI look cycle;
# the long press must still open the panel; the Fn key is never touched.
/perf/fn.arm "$code" 150
sleep 1
grep -q 'custom tap dispatched' /perf/mod/logs/fn-key.log
grep -q 'lut cycle: stock ->' /perf/mod/logs/fn-key.log
grep -q 'lut cycle flash:' /perf/mod/logs/fn-key.log
/perf/fn.arm "$code" 1900
attempt=0
until [ "$(grep -c '^FIRST_FRAME ' /perf/mod/logs/fn-key.log || :)" -eq 3 ]; do
    attempt=$((attempt+1)); [ "$attempt" -lt 15 ]; sleep 1
done
sleep 1
/perf/fn.arm "$code" 150
sleep 1
grep -q 'lut cycle: stock -> ' /perf/mod/logs/fn-key.log
wget -T 2 -Y off -q -O /perf/health.json http://127.0.0.1:8781/api/v1/health
grep -q 'READY backend=nuklear-cpu-ximage' /perf/mod/logs/fn-key.log
grep -q 'NX_CUSTOM_KEY_READY' /perf/remote.log
! grep -q 'NX_X11_WORKER' /perf/remote.log
for marker in 'daemon start' 'menu key init' 'helper spawned' 'helper ready' 'key press' \
    'trigger held_ms' 'panel forked' 'fn panel launcher start' 'panel exec pid=' 'panel exited pid='; do
    grep -q "$marker" /perf/mod/logs/fn-key.log
done
[ "$(grep -c 'helper spawned' /perf/mod/logs/fn-key.log)" -eq 1 ]
printf '{"short_fn_no_launch":true,"long_fn_opens_original_box":true,"menu_close_and_fn_reopen":true,"http_alive":true}\n' > /perf/results.json
kill "$remote"; wait "$remote"
kill "$stock"
trap - EXIT
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    out = parser.parse_args().output_dir.resolve()
    if out.exists():
        raise RuntimeError('Preserve earlier evidence; choose a new output directory')
    vm.OUT = out
    vm.SOCKET = '/tmp/nx300m-fn-restore-private'
    vm.PROBE = PROBE
    vm.prepare()
    (out/'fn.c').write_text(KEY_DRIVER)
    shell = f'''set -eu
tc=/opt/nx300m-toolchain/usr
rf=/opt/nx300m-lab/stock-1.15
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" -O2 -I"$tc/include" \\
 -mfloat-abi=soft -fno-pic -no-pie -fno-stack-protector -nostdlib -nostartfiles \\
 -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 -Wl,--allow-shlib-undefined \\
 -Wl,-rpath-link,"$rf/usr/lib" -Wl,-rpath-link,"$rf/lib" \\
 {shlex.quote(vm.linux_path(out/'fn.c'))} \\
 {shlex.quote(vm.linux_path(PROJECT/'work/reference/metol/test_server/filmlab/src/crt0_arm.S'))} \\
 "$rf/usr/lib/libX11.so.6" "$rf/usr/lib/libXtst.so.6" "$rf/lib/libc-2.13.so" -lgcc \\
 -o {shlex.quote(vm.linux_path(out/'fn.arm'))}
'''
    built = subprocess.run(['wsl.exe','-d','Debian','-u','root','-e','sh','-c',shell],capture_output=True)
    (out/'fn-build.log').write_bytes(built.stdout+built.stderr)
    if built.returncode:
        raise RuntimeError('Fn test driver build exit '+str(built.returncode))
    # Only replace evdev open/ioctl with a FIFO. Keep the actual event loop,
    # Fn mapping, IPC, launch and original UI unchanged in the ARM guest.
    source = out/'source/metol/remote'
    shutil.copytree(PROJECT/'mod/metol/remote',source)
    for name in ('mod_paths.h','lut_engine_shared.h'):
        shutil.copyfile(PROJECT/'mod/metol'/name,source.parent/name)
    (out/'runtime').mkdir()
    shutil.copyfile(PROJECT/'runtime/lut_ram_blob.h',out/'runtime/lut_ram_blob.h')
    shutil.copyfile(PROJECT/'runtime/lut_ram_blob_build.h',out/'runtime/lut_ram_blob_build.h')
    header=source/'menu_key.h'
    original=header.read_text(encoding='utf-8')
    start,end=original.index('static int menu_key_device('),original.index('static void menu_key_child(')
    replacement='''static int menu_key_device(const char *path,unsigned code,unsigned char *held,size_t size) {
    (void)code;memset(held,0,size);
    if(strcmp(path,"/dev/input/event0"))return -1;
    return open("/perf/fn-input",O_RDWR|O_NONBLOCK|O_CLOEXEC);
}
'''
    adapted=original[:start]+replacement+original[end:]
    header.write_text(adapted,newline='\n')
    (out/'evdev-transport.diff').write_text(''.join(difflib.unified_diff(original.splitlines(True),adapted.splitlines(True),fromfile='production',tofile='VM-FIFO')))
    builder=(PROJECT/'tools/build_remote.py').read_text(encoding='utf-8')
    start=builder.index('tc=/opt/nx300m-toolchain/usr')
    end=builder.index("\n'''",start)
    command=builder[start:end].replace('{shlex.quote(GUEST)}',shlex.quote('/opt/nx300m-lab/stock-1.15'))
    command=command.replace('mod/metol/remote/',shlex.quote(vm.linux_path(source))+'/')
    command=command.replace('outputs/metol/remote/nx-remote-controller.arm',shlex.quote(vm.linux_path(out/'remote-fixture.arm')))
    command=command.replace('outputs/metol/remote/versions.txt',shlex.quote(vm.linux_path(out/'versions.txt')))
    fixture=subprocess.run(['wsl.exe','-d','Debian','-u','root','-e','sh','-c','set -eu\ncd '+shlex.quote(vm.linux_path(PROJECT))+'\n'+command],capture_output=True)
    (out/'remote-build.log').write_bytes(fixture.stdout+fixture.stderr)
    if fixture.returncode:raise RuntimeError('Fn fixture build exit '+str(fixture.returncode))
    entries = read_cpio(gzip.decompress((out/'overlay.cpio.gz').read_bytes()))
    with zipfile.ZipFile(PROJECT/'outputs/deployment/lut-test-card/nx300m-connected-lut-test.zip') as archive:
        for name in archive.namelist():
            if not name.startswith('nx300m-mod/'):
                continue
            relative = name.removeprefix('nx300m-mod/')
            if relative.startswith(('bin/', 'fn-panel/')) or relative in ('mfw-panel-actions.sh','common.sh'):
                entries['perf/mod/'+relative] = (0o100755, archive.read(name))
    # The zip's binaries lag the source tree; the lock-tag work only exists in
    # the current build, so the maintained UI must be staged from outputs.
    entries['perf/mod/bin/nxui.arm'] = (0o100755,(PROJECT/'outputs/metol/nxui.arm').read_bytes())
    # Launcher scripts move with the source tree; the deployment zip can lag.
    entries['perf/mod/mfw-panel.sh'] = (0o100755,(PROJECT/'mod/deployment/mfw/fn-panel.sh').read_bytes().replace(b'\r\n',b'\n'))
    entries['perf/mod/fn-panel/mfw-panel-actions.sh'] = (0o100755,(PROJECT/'mod/deployment/mfw/fn-panel-actions.sh').read_bytes().replace(b'\r\n',b'\n'))
    entries['perf/mod/fn-panel/gui_mfw.NX300M'] = (0o100644,(PROJECT/'mod/deployment/mfw/fn-gui_mfw.NX300M').read_bytes().replace(b'\r\n',b'\n'))
    # Zero-UI cycling fixture: two catalogue looks with dummy tables. The
    # engine rejects staging without a camera hook, which still proves the
    # double-tap dispatch and the catalogue walk.
    entries['perf/mod/lut-cycle.sh'] = (0o100755,(PROJECT/'mod/deployment/mfw/lut-cycle.sh').read_bytes().replace(b'\r\n',b'\n'))
    # The cycle now self-loads the adapter; the guest has no camera process,
    # so the loader script itself is a success stub. The real engine still
    # rejects staging (no hook), which proves the failure logging path.
    entries['perf/mod/lut-load.sh'] = (0o100755,
        b'#!/bin/sh\nprintf \'%s lut-load(stub) apply ok\\n\' "$(date -u +%FT%TZ)" >>"${NX_FN_KEY_LOG:-/dev/null}" 2>/dev/null || :\nexit 0\n')
    entries['perf/mod/lut-panel.sh'] = (0o100755,(PROJECT/'mod/deployment/mfw/lut-panel.sh').read_bytes().replace(b'\r\n',b'\n'))
    entries['perf/mod/looks.txt'] = (0o100644,b'metol-ektachrome|Metol Ektachrome E100|0|0|0|0|0|0|0\n'
                                            b'nx300-fuji-classic-chrome|Fuji Classic Chrome|0|0|0|0|0|0|0\n')
    for key in ('metol-ektachrome','nx300-fuji-classic-chrome'):
        entries['perf/mod/looks/'+key+'/table.d4lut'] = (0o100644,bytes(4096))
    entries['perf/fn.arm'] = (0o100755,(out/'fn.arm').read_bytes())
    entries['perf/remote-fixture.arm'] = (0o100755,(out/'remote-fixture.arm').read_bytes())
    inputs = json.loads((out/'inputs.json').read_text())
    inputs.update(scope='Fn evdev event loop and original ARM Xft box; FIFO device transport only',
                  fn_box_bytes=len(entries['perf/mod/fn-panel/bin/nxui.arm'][1]),
                  launcher_nice='inherited from production remote',
                  physical_key_mapping_verified=False, physical_display_driver_verified=False)
    inputs.pop('x11_first_client_warmed_before_both_variants', None)
    arm_vm.write_json(out/'inputs.json', inputs)
    directories = {parent.as_posix() for name in entries for parent in Path(name).parents if str(parent) != '.'}
    overlay = io.BytesIO()
    for name in sorted(directories):
        arm_vm.cpio_add(overlay,name,0o40755)
    for name,(mode,data) in entries.items():
        if name != 'TRAILER!!!' and mode & 0o170000 != 0o040000:
            arm_vm.cpio_add(overlay,name,mode,data)
    arm_vm.cpio_add(overlay,'TRAILER!!!',0)
    compressed = gzip.compress(overlay.getvalue(),mtime=0)
    (out/'overlay.cpio.gz').write_bytes(compressed)
    with (arm_vm.WORK/'initramfs.cpio.gz').open('rb') as source,(out/'initramfs.cpio.gz').open('wb') as target:
        shutil.copyfileobj(source,target); target.write(compressed)
    started = False
    report = {'passed':False,'original_box_bytes':461588,'evdev_transport':'FIFO fixture; see evdev-transport.diff',
              'physical_camera_verified':False,'camera_program_running':False}
    try:
        vm.call('start'); started = True
        deadline = time.monotonic()+180
        while time.monotonic()<deadline:
            if (out/'serial.log').exists() and 'MENU_PERF_VM_READY' in (out/'serial.log').read_text(errors='replace'):
                break
            time.sleep(1)
        else:
            raise TimeoutError('Private VM startup')
        execution = vm.call('exec','/bin/sh -x /perf/probe.sh 2>/perf/probe-trace.log')
        arm_vm.write_json(out/'execution.json',execution)
        collect(out,'remote.log')
        (out/'mod/logs').mkdir(parents=True,exist_ok=True)
        collect(out,'mod/logs/fn-key.log')
        collect(out,'probe-trace.log')
        report['guest_exit_code'] = execution['exit_code']
        if execution['exit_code']:
            raise RuntimeError('Fn exercise exit '+str(execution['exit_code']))
        collect(out,'results.json')
        collect(out,'fn-entry.json')
        for name in ('first','second'):
            packed = vm.call('exec','gzip -c /perf/'+name+'.ppm > /perf/'+name+'.ppm.gz')
            if packed['exit_code']:
                raise RuntimeError('Screenshot compression failed')
            collect(out,name+'.ppm.gz')
            from PIL import Image
            Image.open(io.BytesIO(gzip.decompress((out/(name+'.ppm.gz')).read_bytes()))).save(out/(name+'.png'))
        report.update(passed=True,scenarios=json.loads((out/'results.json').read_text()))
    except BaseException as error:
        report['failure'] = str(error)
        raise
    finally:
        if started:
            report['stop'] = vm.call('stop')
            if 'return' in report['stop']:
                (out/'initramfs.cpio.gz').unlink(missing_ok=True)
        arm_vm.write_json(out/'report.json',report)
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
