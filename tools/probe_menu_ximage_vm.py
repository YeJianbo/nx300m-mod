"""Exercise the CPU menu, raw alpha, power handoff and X11 timeout in ARM VM.

Reuses the passive MENU probe, including its FIFO-only evdev transport. A test
window observes forwarded power events; it is not the original camera program.
"""
import argparse
import gzip
import json
from pathlib import Path
import time

import arm_vm
import probe_menu_entry_vm as entry

EXTRA_DRIVER = r'''
    if(argc==2 && !strcmp(argv[1],"stock")) {
        Window stock=XCreateSimpleWindow(d,DefaultRootWindow(d),0,0,480,800,0,0,0);
        XClassHint hint={"di-camera-app-nx300","di-camera-app-nx300"};XSetClassHint(d,stock,&hint);
        XStoreName(d,stock,"NX-TEST-STOCK");XSelectInput(d,stock,KeyPressMask|KeyReleaseMask);
        XMapRaised(d,stock);XSetInputFocus(d,stock,RevertToPointerRoot,CurrentTime);XSync(d,False);
        puts("STOCK_READY");fflush(stdout);
        for(;;){XEvent e;XNextEvent(d,&e);
            if((e.type==KeyPress || e.type==KeyRelease) && XLookupKeysym(&e.xkey,0)==0x1008ff2a) {
                printf("STOCK_POWER type=%d\n",e.type);fflush(stdout);
            }
        }
    }
'''
FOCUSED_DRIVER = r'''
    if(argc==2 && !strcmp(argv[1],"alpha")) {
        XWindowAttributes a;if(!XGetWindowAttributes(d,w,&a))return 20;
        XImage *im=XGetImage(d,w,0,0,a.width,a.height,AllPlanes,ZPixmap);
        if(!im || im->bits_per_pixel!=32 || im->byte_order!=LSBFirst)return 21;
        unsigned bad=0,dark=0,light=0;
        for(int y=0;y<a.height;y++)for(int x=0;x<a.width;x++) {
            unsigned p=((unsigned *)(im->data+y*im->bytes_per_line))[x];
            if((p>>24)!=255)bad++;
            if((p&0xffffff)==0x181d21)dark++;
            if(((p>>16)&255)>190 && ((p>>8)&255)>190 && (p&255)>140)light++;
        }
        printf("{\"nonopaque_pixels\":%u,\"background_pixels\":%u,\"text_pixels\":%u}\n",bad,dark,light);
        XDestroyImage(im);XCloseDisplay(d);return bad || !dark || !light ? 22:0;
    }
    if(argc==2 && !strcmp(argv[1],"power")) {
        KeySym power=0x1008ff2a;KeyCode code=XKeysymToKeycode(d,power);
        if(!code) {code=222;XChangeKeyboardMapping(d,code,1,&power,1);XSync(d,False);usleep(200000);}
        XEvent e={0};e.xkey.display=d;e.xkey.window=w;e.xkey.keycode=code;
        e.type=KeyPress;XSendEvent(d,w,False,KeyPressMask,&e);
        e.type=KeyRelease;XSendEvent(d,w,False,KeyReleaseMask,&e);
        XSync(d,False);XCloseDisplay(d);return 0;
    }
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    out = parser.parse_args().output_dir.resolve()
    if out.exists():
        raise RuntimeError('Select a new output directory; preserve existing evidence')
    vm = entry.vm
    vm.DRIVER = vm.DRIVER.replace('    Window w; int revert;', EXTRA_DRIVER+'\n    Window w; int revert;')
    vm.DRIVER = vm.DRIVER.replace('    if(argc==2) {', FOCUSED_DRIVER+'\n    if(argc==2) {')
    entry.PROBE = entry.PROBE.replace('sleep 3\n/perf/close.arm lut', '''attempt=0
until awk '/^NX_PANEL_START / {ready=0} /^BACKEND kind=5 ok=1 / {ready=1} END {exit !ready}' /perf/remote.log; do
    attempt=$((attempt+1)); [ "$attempt" -lt 20 ]; sleep 1
done
/perf/close.arm lut''', 1)
    entry.PROBE = entry.PROBE.replace('/perf/remote-fixture.arm --listen',
        '/perf/close.arm stock > /perf/stock.log 2>&1 &\nstock=$!\nsleep 1\n/perf/remote-fixture.arm --listen',1)
    entry.PROBE = entry.PROBE.replace('/perf/close.arm /perf/first.ppm',
        '/perf/close.arm alpha > /perf/alpha.json\n/perf/close.arm /perf/first.ppm',1)
    entry.PROBE = entry.PROBE.replace('kill "$remote"\nwait "$remote"', r'''
/perf/close.arm raw "$code" 1450
attempt=0
until [ "$(grep -c '^FIRST_FRAME ' /perf/remote.log)" -ge 4 ]; do attempt=$((attempt+1)); [ "$attempt" -lt 15 ]; sleep 1; done
/perf/close.arm power
attempt=0
until grep -q 'STOCK_POWER type=3' /perf/stock.log; do attempt=$((attempt+1)); [ "$attempt" -lt 10 ]; sleep 1; done
grep -q 'STOCK_POWER type=2' /perf/stock.log
sleep 1
# The next panel must abandon a blocked XOpenDisplay, without taking focus.
xvfb=$(cat /perf/xvfb.pid)
kill -STOP "$xvfb"
set +e
/bin/sh /perf/mod/mfw-panel.sh > /perf/timeout.log 2>&1
timeout_exit=$?
set -e
kill -CONT "$xvfb"
[ "$timeout_exit" -eq 124 ]
grep -q 'PANEL_DISPLAY_TIMEOUT' /perf/timeout.log
wget -T 2 -Y off -q -O /perf/health-after.json http://127.0.0.1:8781/api/v1/health
kill "$stock"
kill "$remote"
wait "$remote"''',1)
    entry.prepare(out)
    report = {'passed': False, 'camera_program_running': False,
        'physical_display_driver_verified': False, 'physical_evdev_verified': False,
        'stock_window': 'test observer only', 'evdev_transport': 'FIFO fixture'}
    started = False
    try:
        vm.call('start'); started = True
        deadline = time.monotonic()+180
        while time.monotonic()<deadline:
            if (out/'serial.log').exists() and 'MENU_PERF_VM_READY' in (out/'serial.log').read_text(errors='replace'):
                break
            time.sleep(1)
        else:
            raise TimeoutError('VM startup')
        result = vm.call('exec', '/bin/sh /perf/probe.sh')
        arm_vm.write_json(out/'execution.json', result)
        # Always retrieve the main log first, including on a guest failure.
        for name in ('remote.log', 'stock.log'):
            entry.collect(out, name)
        if result['exit_code']:
            raise RuntimeError('CPU menu probe exit '+str(result['exit_code']))
        for name in ('alpha.json', 'timeout.log', 'health-after.json', 'results.json'):
            entry.collect(out, name)
        compressed = vm.call('exec', 'gzip -c /perf/first.ppm > /perf/first.ppm.gz; gzip -c /perf/lut.ppm > /perf/lut.ppm.gz')
        if compressed['exit_code']:
            raise RuntimeError('Snapshot compression exit '+str(compressed['exit_code']))
        for name in ('first', 'lut'):
            entry.collect(out, name+'.ppm.gz')
            (out/(name+'.ppm')).write_bytes(gzip.decompress((out/(name+'.ppm.gz')).read_bytes()))
        from PIL import Image
        for name in ('first', 'lut'):
            Image.open(out/(name+'.ppm')).transpose(Image.Transpose.ROTATE_90).save(out/(name+'.png'))
        scenarios = json.loads((out/'results.json').read_text())
        scenarios.update(raw_alpha_opaque=True, power_edges_forwarded=True, blocked_x11_exit_124=True,
            http_alive_after_timeout=True)
        report.update(passed=all(scenarios.values()), scenarios=scenarios,
            alpha=json.loads((out/'alpha.json').read_text()), guest_exit_code=result['exit_code'])
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
