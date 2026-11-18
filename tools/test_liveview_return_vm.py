"""Exercise the native-playback return gate in a private ARM Xvfb VM.

The fixture maps the stock camera data at its real addresses and supplies the
named threads used by the readiness gate. It never runs or modifies the stock
camera executable, and it does not touch a physical card or the main emulator.
"""
from __future__ import annotations

import argparse
import base64
import gzip
import io
import json
import os
from pathlib import Path
import re
import shutil
import shlex
import subprocess
import sys
import time
import zipfile

import arm_vm
from wsl_paths import wsl_path


PROJECT = Path(__file__).resolve().parents[1]
OUT = PROJECT / "outputs/performance/liveview-return-vm"
SOCKET = "/tmp/nx300m-liveview-return-private"

STATE_IDLE = 0x4C9C64
STATE_PLAYBACK = 0x4DA0F0
STATE_LIVEVIEW = 0x4C9F04
APPLICATION = 0x4C9C5C
UI_STATE = 0x4DE4D4
PREVIEW_POINTER = 0x4C9F04
PREVIEW_NATIVE = 0x24C838

STOCK_ACCESSORS = {
    0xF58C8: bytes.fromhex(
        "18482de90cb08de2543c09e34c3040e3003093e5013003e2000053e31800001a"
        "540c09e34c0040e300d6fdeb0030a0e1000053e30030a0030130a0137330efe6"
        "000053e30e00000a0040a0e35c0c09e34c0040e37bffffeb540c09e34c0040e3"
        "bdd7fdeb203805e30f3040e35c0c09e34c0040e30310a0e1ac2209e3292040e3"
        "28dafdeb5c3c09e34c3040e30300a0e11888bde8000054e30200001a540c09e3"
        "4c0040e36bd6fdebf2dbfdeb"
    ),
    0xF8FEC: bytes.fromhex(
        "18482de90cb08de2003f09e34c3040e3003093e5013003e2000053e31800001a"
        "000f09e34c0040e337c8fdeb0030a0e1000053e30030a0030130a0137330efe6"
        "000053e30e00000a0040a0e3040f09e34c0040e3fd0600eb000f09e34c0040e3"
        "f4c9fdeb783c0ae30f3040e3040f09e34c0040e30310a0e1ac2209e3292040e3"
        "5fccfdeb043f09e34c3040e30300a0e11888bde8000054e30200001a000f09e3"
        "4c0040e3a2c8fdeb29cefdeb"
    ),
    0x85E04: bytes.fromhex(
        "04b02de500b08de20cd04de208000be508301be5083093e50300a0e100d08be2"
        "0008bde81eff2fe1"
    ),
    0x1BBC54: bytes.fromhex(
        "00482de904b08de20300a0e30c0200eb0030a0e1003093e50300a0e10088bde8"
    ),
    0x107454: bytes.fromhex(
        "18482de90cb08de2ec300ae34d3040e3003093e5013003e2000053e31800001a"
        "ec000ae34d0040e31d8ffdeb0030a0e1000053e30030a0030130a0137330efe6"
        "000053e30e00000a0040a0e3f0000ae34d0040e3ca0400ebec000ae34d0040e3"
        "da90fdeb143808e3103040e3f0000ae34d0040e30310a0e1ac2209e3292040e3"
        "4593fdebf0300ae34d3040e30300a0e11888bde8000054e30200001aec000ae3"
        "4d0040e3888ffdeb0f95fdeb"
    ),
}

FIXTURE = r'''
#define _GNU_SOURCE
#include <stddef.h>
#include <stdint.h>
#include "fixture_accessors.h"
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <X11/keysym.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

extern int prctl(int option, ...);
#define PR_SET_NAME 15

enum { MODE_PLAYBACK, MODE_LIVEVIEW, MODE_MENU, MODE_CMM, MODE_HDMI };
static volatile sig_atomic_t stopping;
static volatile unsigned commands;
static volatile uint32_t page=MODE_PLAYBACK;
static volatile uint32_t application_state=0x4da0f0;
static volatile uint32_t native_state=2;
static volatile uint32_t preview_pointer=0x24c838;

static void stop(int signal){(void)signal;stopping=1;}
static void *videosrc(void *unused){(void)unused;prctl(PR_SET_NAME,"videosrc");while(!stopping)usleep(10000);return NULL;}
static void *udd(void *unused){(void)unused;prctl(PR_SET_NAME,"udd_intr_handle");while(!stopping)usleep(10000);return NULL;}
static void *command(void *unused){(void)unused;prctl(PR_SET_NAME,"command_input");
    char line[64];
    while(!stopping&&fgets(line,sizeof line,stdin)){
        unsigned state;
        unsigned parsed=0;char *cursor=line+5;
        while(*cursor>='0'&&*cursor<='9'){parsed=parsed*10+(unsigned)(*cursor-'0');++cursor;}
        if(!strncmp(line,"page ",5)&&cursor!=line+5){
            state=parsed;
            page=state;
        application_state=state==1?0x4c9f04:0x4da0f0;
            native_state=2;
            fprintf(stderr,"FIXTURE page=%u app=%08x native=%u\n",page,application_state,native_state);
        } else if(!strcmp(line,"command\n")) ++commands;
        else if(!strcmp(line,"quit\n")) break;
        fflush(stderr);
    }
    stopping=1;return NULL;
}

static int map_page(uint32_t address,size_t size) {
    long pagesize=sysconf(_SC_PAGESIZE);
    uintptr_t start=(uintptr_t)address&~((uintptr_t)pagesize-1);
    uintptr_t end=((uintptr_t)address+size+pagesize-1)&~((uintptr_t)pagesize-1);
    void *mapped=mmap((void *)start,end-start,PROT_READ|PROT_WRITE,
        MAP_PRIVATE|MAP_ANONYMOUS|MAP_FIXED_NOREPLACE,-1,0);
    return mapped==(void *)start;
}

int main(void){
    signal(SIGTERM,stop);signal(SIGINT,stop);
    if(!map_page(0x4c9000,0x6000)||!map_page(0x4de000,0x2000)||!map_page(0x240000,0xd000)||
       !map_page(0xf0000,0x10000)||!map_page(0x80000,0x6000)||!map_page(0x107000,0x1000)||
       !map_page(0x1bb000,0x1000)){
        perror("mmap");return 2;
    }
    if(prctl(15,"di-camera-app-")!=0)return 3;
    for(unsigned i=0;i<sizeof accessors/sizeof accessors[0];i++)
        memcpy((void *)(uintptr_t)accessors[i].address,accessors[i].bytes,accessors[i].size);
    #define PUT(addr,value) (*(volatile uint32_t *)(uintptr_t)(addr)=(uint32_t)(value))
    PUT(0x4c9c5c,0x24c0b8);PUT(0x4c9c64,application_state);PUT(0x4de4d4,page);
    PUT(0x4c9f04,preview_pointer);PUT(0x24c838+0x34,native_state);
    Display *display=XOpenDisplay(NULL);if(!display){fprintf(stderr,"XOpenDisplay failed\n");return 6;}
    Window stock=XCreateSimpleWindow(display,DefaultRootWindow(display),0,0,480,800,0,0,0);
    XClassHint hint={"di-camera-app-nx300","di-camera-app-nx300"};
    XSetClassHint(display,stock,&hint);XStoreName(display,stock,"NX-FIXTURE-STOCK-PARENT");
    XSelectInput(display,stock,KeyPressMask|KeyReleaseMask);
    XMapRaised(display,stock);XSetInputFocus(display,stock,RevertToPointerRoot,CurrentTime);XSync(display,False);
    pthread_t a,b,d,c;
    if(pthread_create(&a,NULL,videosrc,NULL)||pthread_create(&b,NULL,udd,NULL)||
       pthread_create(&d,NULL,udd,NULL)||pthread_create(&c,NULL,command,NULL))return 5;
    fprintf(stderr,"FIXTURE_READY pid=%d\n",getpid());
    while(!stopping){
        PUT(0x4c9c64,application_state);PUT(0x4de4d4,page);PUT(0x4c9f04,preview_pointer);
        PUT(0x24c838+0x34,native_state);
        while(XPending(display)){
            XEvent event;XNextEvent(display,&event);
    if(event.type==KeyPress&&XLookupKeysym(&event.xkey,0)==XStringToKeysym("XF86Tools")){
        ++commands;application_state=0x4c9f04;native_state=7;
    }
        }
        usleep(1000);
    }
    fflush(stderr);
    pthread_join(a,NULL);pthread_join(b,NULL);pthread_join(d,NULL);pthread_join(c,NULL);
    XDestroyWindow(display,stock);XCloseDisplay(display);
    fprintf(stderr,"FIXTURE_RESULT commands=%u\n",commands);
    return commands?0:9;
}
'''

PROBE = r'''#!/bin/sh
set -ux
on_error() { printf '\nLIVEVIEW_RETURN_FIXTURE_FAILED\n'; }
trap on_error EXIT
echo PROBE_BEGIN
export DISPLAY=:97
while [ ! -S /tmp/.X11-unix/X97 ]; do sleep 1; done
echo PROBE_X_READY
/perf/panel-driver.arm ping
echo PROBE_PING_OK
trap 'kill "$fixture" 2>/dev/null || :' EXIT
mkfifo /perf/fixture.in
/bin/sh -x /perf/fixture-launch.sh > /perf/fixture.stdout.log 2> /perf/fixture.log < /perf/fixture.in &
fixture=$!
exec 3> /perf/fixture.in
ready_wait=0
while ! grep -q FIXTURE_READY /perf/fixture.log; do
    kill -0 "$fixture" || { cat /perf/fixture.log /perf/fixture.stdout.log 2>/dev/null; exit 1; }
    ready_wait=$((ready_wait+1)); [ "$ready_wait" -lt 60 ] || { cat /perf/fixture.log /perf/fixture.stdout.log 2>/dev/null; exit 1; }
    sleep 1
done
run_case() {
    case_page=$1; expected=$2; name=$3
        printf 'page %s\n' "$case_page" > /perf/fixture.in
    sleep 1
    : > /perf/case.log
    NX300M_MOD_ROOT=/perf/mod NX_STATE_DIR=/tmp/lut-$name NXUI_LOCK_TAG=-$name \
/perf/panel-driver.arm launch /perf/mod/bin/nxui.arm --live --lut --root /perf/mod \
        --recipes /perf/mod/looks.txt --engine /perf/mod/lut_engine.arm \
        --font-file /usr/share/fonts/SDIC_GP_US_20120720.ttf --lifetime-ms 1200 > /perf/case.log 2>&1 &
    panel=$!
    attempt=0
    while kill -0 "$panel" 2>/dev/null; do
        attempt=$((attempt+1)); [ "$attempt" -lt 40 ] || { cat /perf/case.log; exit 1; }; sleep 1
    done
    wait "$panel" || :
    cp /perf/case.log "/perf/$name.log"
    count=$(grep -c '^PANEL_LIVEVIEW key=XF86Tools' "/perf/$name.log" || true)
    [ "$count" -eq "$expected" ] || { cat "/perf/$name.log"; exit 1; }
    case "$case_page" in
        18|19|20|21|22|23|24|25|26|28) grep -q '^PANEL_LIVEVIEW ready .* returned=1' "/perf/$name.log" || { echo "case $name: liveview not ready"; exit 1; } ;;
        1|5|27|30|31|32|33|34|35|36|37) ! grep -q '^PANEL_LIVEVIEW ready ' "/perf/$name.log" || { echo "case $name: unexpected ready"; exit 1; } ;;
    esac
}
for page in 18 19 20 21 22 23 24 25 26 28; do run_case "$page" 1 "page-$page" || exit 1; done
for page in 1 5 27 30 31 32 33 34 35 36 37; do run_case "$page" 0 "page-$page" || exit 1; done
printf 'quit\n' > /perf/fixture.in
fixture_wait=0
while kill -0 "$fixture" 2>/dev/null; do fixture_wait=$((fixture_wait+1)); [ "$fixture_wait" -lt 15 ] || exit 1; sleep 1; done
wait "$fixture"
grep -q 'FIXTURE_RESULT commands=10' /perf/fixture.log
trap - EXIT
echo LIVEVIEW_RETURN_FIXTURE_PASSED
'''


DRIVER = r'''
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <unistd.h>

extern int XTestFakeKeyEvent(Display *display, unsigned int keycode, int is_press, unsigned long delay);

static Display *open_display(void) {
    Display *d=XOpenDisplay(NULL);if(!d){fprintf(stderr,"XOpenDisplay failed\n");exit(2);}return d;
}

int main(int argc,char **argv) {
    Display *d=open_display();
    const char *extra_names[]={"XF86Tools","XF86HomePage","XF86ScrollUp","XF86ScrollDown"};
    int minimum,maximum;XDisplayKeycodes(d,&minimum,&maximum);
    for(int i=0;i<4;i++){
        KeySym symbol=XStringToKeysym(extra_names[i]);
        if(XKeysymToKeycode(d,symbol))continue;
        int code;
        for(code=maximum;code>=minimum;code--){
            int per_code=0,unused=1;KeySym *symbols=XGetKeyboardMapping(d,code,1,&per_code);
            if(!symbols)return 22;
            for(int j=0;j<per_code;j++)if(symbols[j]!=NoSymbol)unused=0;
            XFree(symbols);
            if(unused){XChangeKeyboardMapping(d,code,1,&symbol,1);XSync(d,False);break;}
        }
        if(code<minimum)return 22;
    }
    if(argc>2&&!strcmp(argv[1],"launch")){
        if(setpriority(PRIO_PROCESS,0,19))return 4;
        execv(argv[2],argv+2);return 5;
    }
    if(argc==2&&!strcmp(argv[1],"ping")){XCloseDisplay(d);return 0;}
    if(argc==2&&!strcmp(argv[1],"reset")){XCloseDisplay(d);return 0;}
    fprintf(stderr,"usage: panel-driver ping|launch program args...\n");return 2;
}
'''


def prepare() -> None:
    if (OUT / "report.json").exists():
        raise FileExistsError("preserve the existing ARM evidence: " + str(OUT))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "fixture.c").write_text(FIXTURE, encoding="utf-8")
    header = ["struct fixture_accessor { uint32_t address; size_t size; const unsigned char *bytes; };"]
    table = []
    for address, data in STOCK_ACCESSORS.items():
        values = ",".join(f"0x{value:02x}" for value in data)
        header.append(f"static const unsigned char accessor_{address:x}[]={{{values}}};")
        table.append(f"{{0x{address:x},sizeof accessor_{address:x},accessor_{address:x}}}")
    header.append("static const struct fixture_accessor accessors[]={" + ",".join(table) + "};")
    (OUT / "fixture_accessors.h").write_text("\n".join(header) + "\n", encoding="utf-8")
    (OUT / "panel-driver.c").write_text(DRIVER, encoding="utf-8")
    mod = OUT / "guest/mod"
    for name in ("bin", "fonts", "looks"):
        (mod / name).mkdir(parents=True, exist_ok=True)
    shutil.copy2(PROJECT / "outputs/metol/nxui.arm", mod / "bin/nxui.arm")
    with zipfile.ZipFile(PROJECT / "outputs/releases/v0.1.0-preview.9/NX300M-MOD-SD-v0.1.0-preview.9.zip") as archive:
        (mod / "lut_engine.arm").write_bytes(archive.read("nx300m-mod/bin/lut_engine.arm"))
    font_metadata = PROJECT / "outputs/metol/mfw-font.charset"
    if font_metadata.is_file():
        shutil.copy2(font_metadata, mod / "mfw-font.charset")
    for path in sorted((PROJECT / "outputs/looks/original-release").glob("*")):
        if path.is_dir():
            shutil.copytree(path, mod / "looks" / path.name, dirs_exist_ok=True)
    for name in ("looks.txt", "looks-cycle.txt", "LUT-LICENSE.txt"):
        source = PROJECT / "outputs/looks/original-release" / name
        if not source.is_file():
            source = PROJECT / "outputs/looks/original-release/LICENSE.txt"
        shutil.copy2(source, mod / name)
    shutil.copy2(PROJECT / "docs/USER_GUIDE.md", mod / "gui_mfw.NX300M")
    for name, data in STOCK_ACCESSORS.items():
        (OUT / f"accessor-{name:x}.bin").write_bytes(data)
    command = f'''set -eu
tc=/opt/nx300m-toolchain/usr
rf=/opt/nx300m-lab/stock-1.15
out={shlex_quote(wsl_path(OUT))}
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
cc="$tc/bin/arm-linux-gnueabi-gcc"
flags="-B$tc/lib/gcc-cross/arm-linux-gnueabi/14/ -O2 -Wall -Wextra -I$tc/include -mfloat-abi=soft -fno-pic -no-pie -fno-stack-protector -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 -Wl,--allow-shlib-undefined -Wl,-rpath-link,$rf/usr/lib -Wl,-rpath-link,$rf/lib"
cr={shlex_quote(wsl_path(PROJECT))}/work/reference/metol/test_server/filmlab/src/crt0_arm.S
$cc $flags "$out/fixture.c" "$cr" "$rf/usr/lib/libX11.so.6" "$rf/lib/libpthread.so.0" "$rf/lib/libc-2.13.so" -lgcc -o "$out/fixture.arm"
$cc $flags "$out/panel-driver.c" "$cr" "$rf/usr/lib/libX11.so.6" "$rf/usr/lib/libXtst.so.6" "$rf/lib/libc-2.13.so" -lgcc -o "$out/panel-driver.arm"
'''
    build = subprocess.run(["C:\\Windows\\System32\\wsl.exe", "-d", "Debian", "-u", "root", "-e", "sh", "-c", command], capture_output=True, text=True, encoding="utf-8", errors="replace")
    (OUT / "build.log").write_text(build.stdout + build.stderr, encoding="utf-8")
    if build.returncode:
        raise RuntimeError(build.stderr)


def shlex_quote(value: str) -> str:
    import shlex
    return shlex.quote(value)


def build_overlay() -> None:
    """Build one newc archive from the cached root and the fixture overlay."""
    files = {
        "usr/bin/di-camera-app-nx300": (OUT / "fixture.arm").read_bytes(),
        "perf/panel-driver.arm": (OUT / "panel-driver.arm").read_bytes(),
        "perf/probe.sh": PROBE.encode(),
        "perf/fixture-launch.sh": b"#!/bin/sh\nexport LD_LIBRARY_PATH=/lib:/usr/lib\nexec /usr/bin/di-camera-app-nx300\n",
    }
    for path in (OUT / "guest/mod").rglob("*"):
        if path.is_file():
            files["perf/mod/" + path.relative_to(OUT / "guest/mod").as_posix()] = path.read_bytes()
    overlay = io.BytesIO()
    all_names = list(files)
    dirs = {str(parent) for name in all_names for parent in Path(name).parents if str(parent) != "."}
    for name in sorted(dirs):
        arm_vm.cpio_add(overlay, name, 0o40755)
    for name, data in sorted(files.items()):
        arm_vm.cpio_add(overlay, name, 0o100755 if name.endswith((".arm", ".sh")) or name == "usr/bin/di-camera-app-nx300" else 0o100644, data)
    arm_vm.cpio_add(overlay, "TRAILER!!!", 0)
    (OUT / "overlay.cpio.gz").write_bytes(gzip.compress(overlay.getvalue(), mtime=0))
    init = r'''#!/rescue/busybox sh
export PATH=/bin:/sbin:/usr/bin:/usr/sbin
/rescue/busybox mount -t devtmpfs devtmpfs /dev
exec </dev/ttyAMA0 >/dev/ttyAMA0 2>&1
/rescue/busybox mount -t proc proc /proc
/rescue/busybox mount -t sysfs sysfs /sys
    /rescue/busybox mkdir -p /tmp /dev/pts /perf /mnt/mmc
/rescue/busybox mount -t devpts devpts /dev/pts
/rescue/busybox mount -t tmpfs -o rw,exec,mode=1777 tmp /tmp
/rescue/busybox mkdir -p /tmp/.X11-unix
/rescue/busybox chmod 1777 /tmp/.X11-unix
/rescue/busybox ifconfig lo 127.0.0.1 up
export XKB_CONFIG_ROOT=/usr/share/X11/xkb
/rescue/busybox mv /usr/bin/xkbcomp /perf/xkbcomp.debian
/rescue/busybox printf '%s\n' '#!/rescue/busybox sh' 'exec /usr/lib/arm-linux-gnueabihf/ld-linux-armhf.so.3 --library-path /usr/lib/arm-linux-gnueabihf /perf/xkbcomp.debian "$@"' > /usr/bin/xkbcomp
/rescue/busybox chmod 755 /usr/bin/xkbcomp
/usr/lib/arm-linux-gnueabihf/ld-linux-armhf.so.3 --library-path /usr/lib/arm-linux-gnueabihf /usr/bin/Xvfb :97 -screen 0 480x800x24 -nolisten tcp -ac -noreset -fp built-ins > /perf/xvfb.log 2>&1 &
while [ ! -S /tmp/.X11-unix/X97 ]; do sleep 1; done
echo LIVEVIEW_RETURN_VM_READY
/bin/sh /perf/probe.sh 2>&1 | tee /perf/probe.stdout.log
if ! grep -q LIVEVIEW_RETURN_FIXTURE_PASSED /perf/probe.stdout.log; then echo LIVEVIEW_RETURN_FIXTURE_DONE; fi
while true; do /rescue/busybox setsid /bin/sh -i < /dev/ttyAMA0 > /dev/ttyAMA0 2>&1; done
'''
    (OUT / "base-initramfs.cpio.gz").write_bytes((arm_vm.WORK / "initramfs.cpio.gz").read_bytes())
    (OUT / "init.override").write_text(init, encoding="utf-8")

    def parse(data: bytes) -> list[tuple[str, int, bytes]]:
        entries = []
        offset = 0
        while offset < len(data):
            header = data[offset:offset + 110]
            offset += 110
            if header[:6] != b"070701":
                raise ValueError("invalid newc cpio archive")
            fields = [int(header[6 + i * 8:14 + i * 8], 16) for i in range(13)]
            namesize, size, mode = fields[11], fields[6], fields[1]
            name = data[offset:offset + namesize - 1].decode()
            offset = (offset + namesize + 3) & ~3
            payload = data[offset:offset + size]
            offset = (offset + size + 3) & ~3
            if name == "TRAILER!!!":
                break
            entries.append((name, mode, payload))
        return entries

    merged: dict[str, tuple[int, bytes]] = {}
    for name, mode, payload in parse(gzip.decompress((arm_vm.WORK / "initramfs.cpio.gz").read_bytes())):
        merged[name] = (mode, payload)
    for name, mode, payload in parse(overlay.getvalue()):
        merged[name] = (mode, payload)
    merged["init"] = (0o100755, init.encode())
    for name in list(merged):
        for parent in Path(name).parents:
            parent_name = parent.as_posix()
            if parent_name != ".":
                merged.setdefault(parent_name, (0o40755, b""))
    stream = io.BytesIO()
    for inode, name in enumerate(sorted(merged), 1):
        mode, payload = merged[name]
        arm_vm.cpio_add(stream, name, mode, payload, inode)
    arm_vm.cpio_add(stream, "TRAILER!!!", 0)
    (OUT / "initramfs.cpio.gz").write_bytes(gzip.compress(stream.getvalue(), mtime=0))


def linux_worker(action: str, payload: str = "", timeout: int = 240) -> dict:
    arm_vm.SERIAL, arm_vm.QMP = SOCKET + "-serial.sock", SOCKET + "-qmp.sock"
    if action == "start":
        (OUT / "serial.log").write_bytes(b"")
        for path in (arm_vm.SERIAL, arm_vm.QMP):
            Path(path).unlink(missing_ok=True)
        command = [str(arm_vm.WORK / "host/usr/bin/qemu-system-arm"), "-M", "virt", "-cpu", "cortex-a15", "-m", "2048M", "-smp", "2", "-kernel", str(arm_vm.WORK / "downloads/vmlinuz"), "-initrd", str(OUT / "initramfs.cpio.gz"), "-append", "console=ttyAMA0 rdinit=/init panic=-1", "-display", "none", "-monitor", "none", "-serial", f"unix:{arm_vm.SERIAL},server=on,wait=off,logfile={OUT / 'serial.log'}", "-qmp", f"unix:{arm_vm.QMP},server=on,wait=off", "-nic", "none", "-no-reboot"]
        env = dict(os.environ, LD_LIBRARY_PATH=str(arm_vm.WORK / "host/usr/lib/x86_64-linux-gnu"))
        log = (OUT / "qemu.log").open("ab", buffering=0)
        process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        arm_vm.write_json(OUT / "handle.json", {"pid": process.pid, "command": command})
        return {"pid": process.pid}
    if action == "stop":
        return arm_vm.qmp("quit")
    return arm_vm.native_exec(base64.b64decode(payload).decode(), timeout=timeout)


def call(action: str, payload: str = "", timeout: int = 240) -> dict:
    command = "C:\\Windows\\System32\\wsl.exe -d Debian -u root -e python3 " + shlex.quote(wsl_path(__file__)) + " --linux --output-dir " + shlex.quote(wsl_path(OUT)) + " --timeout " + str(timeout) + " " + action + " " + base64.b64encode(payload.encode()).decode()
    result = subprocess.run(command, shell=True, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout + 20)
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    return json.loads(result.stdout)


def main() -> int:
    global OUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--linux", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=OUT)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("action", nargs="?", default="run")
    parser.add_argument("payload", nargs="?", default="")
    args = parser.parse_args()
    OUT = args.output_dir.resolve()
    if args.action == "run" and not args.linux:
        report = OUT / "report.json"
        if report.is_file():
            report.rename(OUT / ("report-before-" + time.strftime("%Y%m%d-%H%M%S") + ".json"))
    if args.linux:
        print(json.dumps(linux_worker(args.action, args.payload, args.timeout)))
        return 0
    if args.action != "run":
        print(json.dumps(call(args.action, args.payload, args.timeout)))
        return 0
    prepare()
    build_overlay()
    started = False
    report = {"passed": False, "scope": "ARM Xvfb fixture; isolated process memory and named threads; no stock camera execution", "physical_camera_verified": False}
    try:
        call("start")
        started = True
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            serial = (OUT / "serial.log").read_text(errors="replace")
            if "LIVEVIEW_RETURN_FIXTURE_PASSED" in serial:
                result = {"exit_code": 0, "output": serial}
                break
            if "LIVEVIEW_RETURN_FIXTURE_FAILED" in serial or "LIVEVIEW_RETURN_FIXTURE_DONE" in serial:
                result = {"exit_code": 1, "output": serial}
                break
            time.sleep(1)
        else:
            raise TimeoutError("private VM did not boot")
        report.update(passed=result["exit_code"] == 0, guest_result=result)
        if not report["passed"]:
            raise RuntimeError(result["output"])
    finally:
        if started:
            report["stop"] = call("stop")
        arm_vm.write_json(OUT / "report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
