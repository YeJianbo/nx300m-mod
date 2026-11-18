"""Verify the quick log export chain in a private stock-rootfs ARM VM.

Covers the LUT control journal (lut-load.sh and lut_engine.arm writers), the
extended /api/v1/diagnostics listing, the one-tap ?bundle=1 attachment, the
card-side logs-export.sh and the web UI export entry. No camera process, no
physical card, no stock camera execution: lut-load exits at the camera scan
by design and the failure itself is the evidence being asserted.
"""
from __future__ import annotations

import argparse
import base64
import gzip
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import time

import arm_vm
from wsl_paths import wsl_path


PROJECT = Path(__file__).resolve().parents[1]
OUT = PROJECT / "outputs/deployment/diagnostics-bundle-vm"
SOCKET = "/tmp/nx300m-diagnostics-bundle-private"

PROBE = r'''#!/bin/sh
set -ux
on_error() { printf '\nDIAGNOSTICS_BUNDLE_PROBE_FAILED\n'; }
trap on_error EXIT
must() { "$@" || { printf '\nASSERT FAILED: %s\n' "$*"; exit 1; }; }
echo PROBE_BEGIN
export PATH=/bin:/usr/bin:/sbin:/usr/sbin
mkdir -p /tmp/state/logs /tmp/card
printf 'panel stderr line PANEL_STAGE pid=1 name=script-entry\n' > /tmp/state/logs/fn-key.log
printf 'remote starting\n' > /tmp/state/remote.log
printf 'rev=diagnostics-probe\n' > /tmp/state/service.state
# 1) lut-load.sh control journal: no camera process is running, so the scan
#    fails by design and the failure must land in the RAM journal.
sh /perf/lut-load.sh apply > /tmp/lut-load.out 2>&1 || :
must grep -q 'NX300M LUT: expected exactly one running original camera process' /tmp/lut-load.out
must test -f /var/run/nx300m-lut-journal.log
must grep -q 'FAIL expected exactly one running original camera process' /var/run/nx300m-lut-journal.log
# 2) lut_engine control journal: the catalog is missing in this fixture, so
#    the apply fails before staging and must be journaled as well.
NX300M_MOD_ROOT=/perf/mod /perf/lut_engine.arm apply bad-look 100 > /tmp/engine.out 2>&1 || :
must grep -q 'apply-failed catalog unavailable' /var/run/nx300m-lut-journal.log
# 3) the remote serves the extended diagnostics over HTTP. The VM's stock
#    ld.so.cache points at firmware paths that do not exist here, so pin the
#    library search like every other stock-rootfs fixture does.
export DISPLAY=:97
NX_STATE_DIR=/tmp/state LD_LIBRARY_PATH=/lib:/usr/lib /perf/remote-nx300.arm \
    --listen 127.0.0.1:8781 --web-root /perf/web > /tmp/state/remote.log 2>&1 &
remote=$!
n=0
until wget -T 2 -Y off -q -O /tmp/health.out http://127.0.0.1:8781/api/v1/health 2>/dev/null; do
    n=$((n+1)); [ "$n" -lt 20 ] || { cat /tmp/state/remote.log; exit 1; }; sleep 1
done
# 4) the listing includes the panel stderr, the LUT journal and the status file.
wget -T 5 -Y off -O /tmp/diag.json http://127.0.0.1:8781/api/v1/diagnostics
must grep -q '"name":"logs/fn-key.log"' /tmp/diag.json
must grep -q '"name":"lut-journal.log"' /tmp/diag.json
must grep -q '"name":"lut-status.json"' /tmp/diag.json
# 5) single-file streaming reaches subdirectory RAM logs.
wget -T 5 -Y off -O /tmp/fnkey.txt 'http://127.0.0.1:8781/api/v1/diagnostics?file=logs/fn-key.log'
must grep -q 'PANEL_STAGE' /tmp/fnkey.txt
# 6) the bundle is one bounded attachment containing every section.
wget -T 10 -Y off -O /tmp/bundle.txt 'http://127.0.0.1:8781/api/v1/diagnostics?bundle=1'
cat /tmp/bundle.txt
must grep -q '===== lut-journal.log (available) =====' /tmp/bundle.txt
must grep -q 'FAIL expected exactly one running original camera process' /tmp/bundle.txt
must grep -q 'apply-failed catalog unavailable' /tmp/bundle.txt
must grep -q '===== logs/fn-key.log (available) =====' /tmp/bundle.txt
must grep -q 'PANEL_STAGE' /tmp/bundle.txt
must grep -q '===== lut-status.json (unavailable) =====' /tmp/bundle.txt
must grep -q '===== end =====' /tmp/bundle.txt
# 7) the web UI carries the one-tap export entry.
wget -T 5 -Y off -O /tmp/index.html http://127.0.0.1:8781/
must grep -q '导出日志' /tmp/index.html
must grep -q "/api/v1/diagnostics?bundle=1" /tmp/index.html
# 8) the card-side export writes the same bundle to a user directory.
NX_STATE_DIR=/tmp/state sh /perf/logs-export.sh /tmp/card
exported=$(ls /tmp/card/nx300m-logs-*.txt 2>/dev/null | head -1)
must test -n "$exported"
must test -s "$exported"
must grep -q 'lut-journal.log' "$exported"
must grep -q 'FAIL expected exactly one running original camera process' "$exported"
must grep -q 'apply-failed catalog unavailable' "$exported"
must grep -q 'uptime:' "$exported"
trap - EXIT
kill "$remote" 2>/dev/null || :
echo DIAGNOSTICS_BUNDLE_PROBE_PASSED
'''

INIT = r'''#!/rescue/busybox sh
export PATH=/bin:/sbin:/usr/bin:/usr/sbin
/rescue/busybox mount -t devtmpfs devtmpfs /dev
exec </dev/ttyAMA0 >/dev/ttyAMA0 2>&1
/rescue/busybox mount -t proc proc /proc
/rescue/busybox mount -t sysfs sysfs /sys
/rescue/busybox mkdir -p /tmp /dev/pts /perf
/rescue/busybox mount -t devpts devpts /dev/pts
/rescue/busybox mount -t tmpfs -o rw,exec,mode=1777,size=32m tmp /tmp
/rescue/busybox mkdir -p /tmp/.X11-unix
/rescue/busybox chmod 1777 /tmp/.X11-unix
/rescue/busybox ifconfig lo 127.0.0.1 up
export XKB_CONFIG_ROOT=/usr/share/X11/xkb
/rescue/busybox mv /usr/bin/xkbcomp /perf/xkbcomp.debian
/rescue/busybox printf '%s\n' '#!/rescue/busybox sh' 'exec /usr/lib/arm-linux-gnueabihf/ld-linux-armhf.so.3 --library-path /usr/lib/arm-linux-gnueabihf /perf/xkbcomp.debian "$@"' > /usr/bin/xkbcomp
/rescue/busybox chmod 755 /usr/bin/xkbcomp
echo INIT_STEP_XVFB_START
/usr/lib/arm-linux-gnueabihf/ld-linux-armhf.so.3 --library-path /usr/lib/arm-linux-gnueabihf /usr/bin/Xvfb :97 -screen 0 480x800x24 -nolisten tcp -ac -noreset -fp built-ins > /perf/xvfb.log 2>&1 &
n=0
while [ ! -S /tmp/.X11-unix/X97 ]; do
    n=$((n+1))
    if [ "$n" -ge 30 ]; then
        echo XVFB_WAIT_FAILED
        cat /perf/xvfb.log 2>/dev/null || echo no-xvfb-log
        ls -la /tmp/.X11-unix 2>/dev/null
        free -m 2>/dev/null | head -3
        break
    fi
    sleep 1
done
echo DIAGNOSTICS_BUNDLE_VM_READY
/bin/sh /perf/probe.sh 2>&1 | tee /perf/probe.stdout.log
if ! grep -q DIAGNOSTICS_BUNDLE_PROBE_PASSED /perf/probe.stdout.log; then echo DIAGNOSTICS_BUNDLE_PROBE_DONE; fi
while true; do /rescue/busybox setsid /bin/sh -i < /dev/ttyAMA0 > /dev/ttyAMA0 2>&1; done
'''


def build_overlay() -> None:
    """Assemble the initramfs with the rebuilt artifacts and the probe."""
    files = {
        "perf/probe.sh": PROBE.encode(),
        "perf/remote-nx300.arm": (PROJECT / "outputs/metol/remote/nx-remote-controller.arm").read_bytes(),
        "perf/lut_engine.arm": (PROJECT / "outputs/metol/lut_engine.arm").read_bytes(),
        "perf/lut_ram_blob.bin": (PROJECT / "outputs/deployment/lut-live-loader/lut_ram_blob.bin").read_bytes(),
        "perf/lut_live_loader.arm": (PROJECT / "outputs/deployment/lut-live-loader/lut_live_loader.arm").read_bytes(),
        "perf/lut-load.sh": (PROJECT / "mod/deployment/mfw/lut-load.sh").read_bytes(),
        "perf/lut-panel.sh": (PROJECT / "mod/deployment/mfw/lut-panel.sh").read_bytes(),
        "perf/logs-export.sh": (PROJECT / "mod/deployment/mfw/logs-export.sh").read_bytes(),
    }
    web = PROJECT / "outputs/metol/remote/web_root"
    for path in sorted(web.rglob("*")):
        if path.is_file():
            files["perf/web/" + path.relative_to(web).as_posix()] = path.read_bytes()
    # lut-load.sh verifies its own resources before the camera scan.
    files["perf/mod/bin/lut_engine.arm"] = files["perf/lut_engine.arm"]
    files["perf/mod/bin/lut_live_loader.arm"] = files["perf/lut_live_loader.arm"]
    files["perf/mod/lib/lut_ram_blob.bin"] = files["perf/lut_ram_blob.bin"]
    overlay = io.BytesIO()
    all_names = list(files)
    dirs = {str(parent) for name in all_names for parent in Path(name).parents if str(parent) != "."}
    for name in sorted(dirs):
        arm_vm.cpio_add(overlay, name, 0o40755)
    for name, data in sorted(files.items()):
        arm_vm.cpio_add(overlay, name, 0o100755 if name.endswith((".arm", ".sh")) else 0o100644, data)
    arm_vm.cpio_add(overlay, "TRAILER!!!", 0)
    (OUT / "overlay.cpio.gz").write_bytes(gzip.compress(overlay.getvalue(), mtime=0))

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
    merged["init"] = (0o100755, INIT.encode())
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
    OUT.mkdir(parents=True, exist_ok=True)
    build_overlay()
    started = False
    report = {"passed": False, "scope": "stock-rootfs ARM VM; fixed RAM diagnostic paths; no camera process, no card write",
              "physical_camera_verified": False}
    try:
        call("start")
        started = True
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            serial = (OUT / "serial.log").read_text(errors="replace")
            if "DIAGNOSTICS_BUNDLE_PROBE_PASSED" in serial:
                result = {"exit_code": 0, "output": serial}
                break
            if "DIAGNOSTICS_BUNDLE_PROBE_FAILED" in serial or "DIAGNOSTICS_BUNDLE_PROBE_DONE" in serial:
                result = {"exit_code": 1, "output": serial}
                break
            time.sleep(1)
        else:
            raise TimeoutError("private VM did not finish")
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
