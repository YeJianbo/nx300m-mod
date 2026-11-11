"""Focused Off/re-enable regression, using the existing isolated ABI7 ARM VM."""
import base64
import gzip
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

import arm_vm
import probe_mfw_ram_vm as vm
import probe_smartfilter_ram_vm as ram
import probe_smartfilter_vm as boundary

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/review/2026-11-06-first-install-card-stall/lut-off/vm'
BASE = ROOT / 'outputs/deployment/smartfilter-session/preview-navigation-vm'
SOCKET = '/tmp/nx300m-off-private'
PROBE = ram.PROBE[:ram.PROBE.index('request original-mode 2')] + r'''
NX300M_MFW_RAM_BASE=/tmp/nx300m-off
RUNTIME=$NX300M_MFW_RAM_BASE/runtime
STATE=$NX300M_MFW_RAM_BASE/state
mkdir -p "$RUNTIME/bin" "$RUNTIME/lib" "$RUNTIME/ipcs-diagnostic/bin" "$RUNTIME/ipcs-diagnostic/lib" "$STATE" /perf/.state /perf/looks/current-mono
cp /mnt/mmc/smartfilter-live-preview.sh "$RUNTIME/smartfilter-preview.sh"
cp /mnt/mmc/lut-load.sh "$RUNTIME/lut-load.sh"
cp /mnt/mmc/smartfilter-probe-collector.sh "$RUNTIME/"
cp /mnt/mmc/bin/smartfilter_loader.arm "$RUNTIME/ipcs-diagnostic/bin/"
cp /mnt/mmc/bin/smartfilter_probe_launcher.arm "$RUNTIME/ipcs-diagnostic/bin/"
cp /mnt/mmc/bin/smartfilter_probe_launcher.arm "$RUNTIME/bin/"
cp /mnt/mmc/bin/ram_asset_verify.arm "$RUNTIME/bin/"
cp /mnt/mmc/bin/lut_engine_switch_fixture.arm "$RUNTIME/bin/lut_engine.arm"
cp /mnt/mmc/bin/lut_live_loader.arm "$RUNTIME/bin/"
cp /mnt/mmc/lut_ram_blob.bin "$RUNTIME/lib/"
cp /mnt/mmc/smartfilter_ram.bin "$RUNTIME/ipcs-diagnostic/lib/"
chmod 500 "$RUNTIME/bin/"*
printf '1\n' > "$RUNTIME/IPCS_OPEN_DIAGNOSTIC"
printf '1\n' > "$RUNTIME/IPCS_SESSION_PREVIEW"
cp /mnt/mmc/monochrome.d4lut /perf/looks/current-mono/table.d4lut
printf 'current-mono|Mono|0|0|0|0|0|0|0\n' > /perf/looks.txt
printf '1 /perf/looks/current-mono/table.d4lut 100\n' > /perf/.state/lut-selection
export NX300M_MFW_RAM_BASE NX300M_MOD_ROOT="$RUNTIME" NX_STATE_DIR="$STATE" NX_SF_PROBE_FIXTURE=1
export NX300M_MOD_SOURCE_ROOT=/perf NX300M_MOD_SOURCE_ID=$(stat -c '%d:%i' /perf) NX_FN_KEY_LOG=/perf/fn-key.log
export NX_LUT_VM_PID="$pid"
ENGINE=$RUNTIME/bin/lut_engine.arm
for cycle in 1 2; do
    # Preview is explicitly enabled below; automatic boot restoration is
    # exercised separately by probe_lut_lifecycle_vm.py.
    NX300M_LUT_SKIP_PREVIEW_SYNC=1 NX300M_MOD_ROOT=/perf "$ENGINE" apply current-mono 100 > "on-$cycle.json" 2> "on-$cycle.stderr"
    /bin/sh "$RUNTIME/smartfilter-preview.sh" enable-s2 > "preview-$cycle.json" 2> "preview-$cycle.stderr"
    request "render-$cycle" 100
    request "stream-$cycle" 116
    NX300M_MOD_ROOT=/perf "$ENGINE" disable > "off-$cycle.json" 2> "off-$cycle.stderr"
    request "stream-stopped-$cycle" 117
    [ ! -e /var/run/nx300m-preview-backend ]
    [ ! -e /var/run/nx300m-lut-selection ]
    NX300M_MOD_ROOT=/perf "$ENGINE" choice-status > "choice-$cycle.txt"
    grep -q '^CHOICE valid=1 recorded=1 off=1 .* slot$' "choice-$cycle.txt"
    [ "$(cat /perf/.state/lut-preview)" = 1 ]
    [ ! -e /tmp/nx300m-smartfilter-preview.uncertain ]
    grep -q 'LUT_DISABLED native_still_restored=true preview_processing=false' "off-$cycle.json"
    ! grep -q '"command":"open-export"' "off-$cycle.json"
    set +e
    "$RUNTIME/bin/lut_live_loader.arm" status --pid "$pid" > "native-$cycle.json"
    rc=$?
    set -e
    [ "$rc" = 2 ]
    grep -q '"rw_slots_original":true' "native-$cycle.json"
    operation "stopped-$cycle" open-export
    request "responsive-$cycle" 2
    cat /proc/$pid/status > "target-$cycle.status"
done
echo LUT_OFF_ARM_PASSED
'''


def call(action, command='', vm_timeout=180):
    args = ['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'python3',
            arm_vm.wsl_path(__file__), '--output-dir', arm_vm.wsl_path(OUT),
            '--linux', action, base64.b64encode(command.encode()).decode()]
    r = subprocess.run(args, capture_output=True, text=True, encoding='utf-8', timeout=vm_timeout+30)
    if r.returncode:
        raise RuntimeError(r.stderr or r.stdout)
    return json.loads(r.stdout)


def prepare():
    OUT.mkdir(parents=True, exist_ok=True)
    # Rebuild only the engine fixture; its identity gate names the VM target.
    script = r'''set -eu
cd /mnt/d/codex/project/2026-10-19/nx300m-mod
tc=/opt/nx300m-toolchain/usr
rf=/opt/nx300m-lab/stock-1.15
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \
 -std=gnu99 -O1 -Wall -Wextra -Werror -Wno-unused-function -march=armv7-a -mfloat-abi=soft \
 -D_GNU_SOURCE -D_TIME_BITS=32 -D_FILE_OFFSET_BITS=64 -fno-pic -no-pie -fno-stack-protector \
 -include mod/metol/remote/compat.h -nostdlib -nostartfiles -Wl,-e,_start \
 -Wl,--dynamic-linker=/lib/ld-linux.so.3 -Wl,-rpath-link,"$rf/lib" \
 runtime/lut_engine_switch_fixture.c mod/metol/remote/compat.c \
 work/reference/metol/test_server/filmlab/src/crt0_arm.S "$rf/lib/libc-2.13.so" -lgcc -o OFF_FIXTURE
'''.replace('/mnt/d/codex/project/2026-10-19/nx300m-mod', arm_vm.wsl_path(ROOT)).replace(
        'OFF_FIXTURE', arm_vm.wsl_path(OUT/'lut_engine_switch_fixture.arm'))
    r = subprocess.run(['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'sh', '-s'],
                       input=script.encode(), capture_output=True)
    (OUT/'build.stderr').write_bytes(r.stderr)
    if r.returncode:
        raise RuntimeError(r.stderr.decode(errors='replace'))
    overlay = io.BytesIO()
    updates = {'arm-vm/mfw-probe.sh': PROBE.encode(),
               'arm-vm/mfw-source/smartfilter-live-preview.sh': (ROOT/'mod/deployment/mfw/smartfilter-live-preview.sh').read_bytes(),
               'arm-vm/mfw-source/lut-load.sh': (ROOT/'mod/deployment/mfw/lut-load.sh').read_bytes(),
               'arm-vm/mfw-source/bin/ram_asset_verify.arm': (ROOT/'outputs/controls/ram-asset-verify/ram_asset_verify.arm').read_bytes(),
               'arm-vm/mfw-source/bin/lut_engine_switch_fixture.arm': (OUT/'lut_engine_switch_fixture.arm').read_bytes(),
               'arm-vm/mfw-source/bin/lut_live_loader.arm': (ROOT/'outputs/deployment/lut-live-loader/lut_live_loader.arm').read_bytes(),
               'arm-vm/mfw-source/lut_ram_blob.bin': (ROOT/'outputs/deployment/lut-live-loader/lut_ram_blob.bin').read_bytes(),
               'arm-vm/mfw-source/bin/smartfilter_loader.arm': (ROOT/'outputs/deployment/smartfilter-session/smartfilter_loader.arm').read_bytes(),
               'arm-vm/mfw-source/bin/smartfilter_probe_launcher.arm': (ROOT/'outputs/deployment/smartfilter-session/smartfilter_probe_launcher.arm').read_bytes(),
               'arm-vm/mfw-source/smartfilter_ram.bin': (ROOT/'outputs/deployment/smartfilter-session/smartfilter_ram.bin').read_bytes()}
    for inode, (name, data) in enumerate(updates.items(), 9000):
        arm_vm.cpio_add(overlay, name, 0o100755, data, inode)
    arm_vm.cpio_add(overlay, 'TRAILER!!!', 0)
    with (BASE/'initramfs.cpio.gz').open('rb') as source, (OUT/'initramfs.cpio.gz').open('wb') as dest:
        shutil.copyfileobj(source, dest)
        dest.write(gzip.compress(overlay.getvalue(), mtime=0))


def run():
    prepare()
    report = {'passed': False, 'physical_camera_verified': False}
    try:
        print(call('start'), flush=True)
        deadline = time.monotonic()+120
        while time.monotonic() < deadline:
            if 'PRIVATE_VM_READY' in (OUT/'serial.log').read_text(errors='replace'):
                break
            time.sleep(.5)
        else:
            raise TimeoutError('ARM boot timeout')
        result = call('exec', '/bin/sh /arm-vm/mfw-probe.sh')
        arm_vm.write_json(OUT/'execution.json', result)
        boundary.OUT=OUT
        boundary.call=call
        guest=boundary.collect()
        if result['exit_code'] or 'LUT_OFF_ARM_PASSED' not in result['output']:
            raise RuntimeError('Off probe failed; see execution.json and guest evidence')
        windows=[]
        for cycle in (1, 2):
            text=(guest/f'off-{cycle}.json').read_text()+(guest/f'off-{cycle}.stderr').read_text()
            assert 'operation=pause phase=end rc=0' in text
            assert 'operation=restore phase=end rc=0' in text
            assert 'Still control admission:' in text and 'camera running' in text
            windows += [int(v) for v in re.findall(r'Still target stop window: (\d+) ms', text)]
            assert re.search(r'TracerPid:\s+0\b', (guest/f'target-{cycle}.status').read_text())
            session=next(json.loads(line) for line in (guest/f'stopped-{cycle}.json').read_text().splitlines() if '"type":"ipcs_session"' in line)
            assert session['held']==0 and session['opens']==session['closes']
        assert len(windows)==2
        report.update(passed=True, cycles=2, original_still_slots_restored=True,
                      session_references_balanced=True, target_responsive_and_detached=True,
                      ordinary_off_has_no_diagnostic_attach=True, still_stop_windows_vm_ms=windows)
    except Exception as exc:
        report['error']=str(exc)
        raise
    finally:
        arm_vm.write_json(OUT/'shutdown.json', call('stop'))
        arm_vm.write_json(OUT/'report.json', report)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser(add_help=False)
    parser.add_argument('--output-dir',type=Path,default=OUT)
    args,remaining=parser.parse_known_args()
    OUT=args.output_dir.resolve()
    sys.argv=[sys.argv[0],*remaining]
    vm.OUT=OUT
    vm.SOCKET=SOCKET
    if len(sys.argv)>1 and sys.argv[1]=='--linux':
        print(json.dumps(vm.linux_worker(sys.argv[2], sys.argv[3], timeout=180)))
    else:
        run()
