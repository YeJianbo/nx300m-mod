"""Directed lifecycle regression: LUT switch, preview preference, real backend.

Adapted from probe_lut_off_vm.py (isolated ABI7 ARM VM, fixture camera). Covers
the 2026-11-07 rules: LUT Off suspends preview but preserves the user's saved
preference; a stock boot never writes the preference to 0; re-applying a LUT
restores preview per that preference; preference off applies the look without
starting hardware. VM data only; no camera hardware claim.
"""
import base64
import gzip
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

import arm_vm
import probe_mfw_ram_vm as vm
import probe_smartfilter_ram_vm as ram
import probe_smartfilter_vm as boundary

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/review/2026-11-07-zcode/lut-lifecycle-vm-v12'
BASE = ROOT / 'outputs/deployment/smartfilter-session/preview-navigation-vm'
SOCKET = '/tmp/nx300m-lifecycle-private'
PROBE = ram.PROBE[:ram.PROBE.index('request original-mode 2')] + r'''
NX300M_MFW_RAM_BASE=/tmp/nx300m-lifecycle
RUNTIME=$NX300M_MFW_RAM_BASE/runtime
STATE=$NX300M_MFW_RAM_BASE/state
mkdir -p "$RUNTIME/bin" "$RUNTIME/lib" "$RUNTIME/ipcs-diagnostic/bin" "$RUNTIME/ipcs-diagnostic/lib" "$STATE" /perf/.state /perf/looks/current-mono
cp /mnt/mmc/smartfilter-live-preview.sh "$RUNTIME/smartfilter-preview.sh"
cp /mnt/mmc/lut-load.sh "$RUNTIME/lut-load.sh"
cp /mnt/mmc/lut-boot.sh "$RUNTIME/lut-boot-real.sh"
printf '#!/bin/sh
exec /bin/sh -x "%s/lut-boot-real.sh" >>/arm-vm/mfw-results/boot-trace.log 2>&1
' "$RUNTIME" > "$RUNTIME/lut-boot.sh"
chmod 700 "$RUNTIME/lut-boot.sh"
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
export NX_LUT_SYNC_BUDGET_MS=40000 NX_LUT_CONTROL_BUDGET_MS=40000
ENGINE=$RUNTIME/bin/lut_engine.arm
# The user starts with preview preference OFF.
printf '0\n' > /perf/.state/lut-preview

# --- Round 1: user turns preview on, then turns the LUT off.
NX300M_MOD_ROOT=/perf "$ENGINE" apply current-mono 100 > on-1.json 2> on-1.stderr
/bin/sh "$RUNTIME/smartfilter-preview.sh" enable-s2 > preview-1.json 2> preview-1.stderr
grep -q smartfilter /var/run/nx300m-preview-backend
[ "$(cat /perf/.state/lut-preview)" = 1 ]
NX300M_MOD_ROOT=/perf "$ENGINE" disable > off-1.json 2> off-1.stderr
[ ! -e /var/run/nx300m-preview-backend ]
NX300M_MOD_ROOT=/perf "$ENGINE" choice-status > off-choice.txt
grep -q '^CHOICE valid=1 recorded=1 off=1 .* slot$' off-choice.txt
# Rule: suspending with the LUT must NOT clear the user's preview preference.
[ "$(cat /perf/.state/lut-preview)" = 1 ]
grep -q 'operation=pause phase=end rc=0' off-1.json off-1.stderr
grep -q 'LUT_DISABLED native_still_restored=true preview_processing=false' off-1.json
! grep -q '"command":"open-export"' off-1.json

# --- Stock boot: the preference must survive a boot with the LUT off.
NX300M_MOD_ROOT=/perf "$ENGINE" boot > boot-stock.json 2> boot-stock.stderr
[ "$(cat /perf/.state/lut-preview)" = 1 ]
[ ! -e /var/run/nx300m-lut-selection ]

# --- Re-apply with preference on: preview restores via the saved preference.
# rc 6 (outer budget) is acceptable: the abandoned chain still completes.
set +e
NX300M_MOD_ROOT=/perf "$ENGINE" apply current-mono 100 > on-restore.json 2> on-restore.stderr
apply_rc=$?
set -e
[ "$apply_rc" = 0 ] || [ "$apply_rc" = 6 ]
restored=0
i=0
while [ $i -lt 300 ]; do
    if grep -q smartfilter /var/run/nx300m-preview-backend 2>/dev/null; then restored=1; break; fi
    sleep 0.2; i=$((i+1))
done
if [ "$restored" != 1 ]; then
    cp /perf/logs/fn-key.log worker-fnkey.log 2>/dev/null || :
    cat "$STATE/lut-boot.status" > worker-status.txt 2>/dev/null || :
    ls -la "$STATE" >> worker-status.txt 2>/dev/null || :
    ps > worker-ps.txt 2>/dev/null || :
fi
[ "$restored" = 1 ]
[ "$(cat /perf/.state/lut-preview)" = 1 ]
grep -q smartfilter /var/run/nx300m-preview-backend

# --- User turns preview off alone, then re-applies: no hardware start.
/bin/sh "$RUNTIME/smartfilter-preview.sh" stop > user-off.json 2> user-off.stderr
[ ! -e /var/run/nx300m-preview-backend ]
[ "$(cat /perf/.state/lut-preview)" = 0 ]
NX300M_MOD_ROOT=/perf "$ENGINE" disable > off-2.json 2> off-2.stderr
# Native/responsive checks belong right after a full Off: the later
# preference-off apply intentionally re-installs the native hook.
set +e
"$RUNTIME/bin/lut_live_loader.arm" status --pid "$pid" > native-final.json
rc=$?
set -e
[ "$rc" = 2 ]
grep -q '"rw_slots_original":true' native-final.json
operation "stopped-final" open-export
request "responsive-final" 2
cat /proc/$pid/status > target-final.status
set +e
NX300M_MOD_ROOT=/perf "$ENGINE" apply current-mono 100 > on-pref0.json 2> on-pref0.stderr
pref0_rc=$?
set -e
[ "$pref0_rc" = 0 ]
NX300M_MOD_ROOT=/perf "$ENGINE" choice-status > on-choice.txt
grep -q '^CHOICE valid=1 recorded=1 off=0 .*current-mono/table.d4lut slot$' on-choice.txt
i=0
while [ $i -lt 100 ]; do
    grep -q smartfilter /var/run/nx300m-preview-backend 2>/dev/null && exit 91
    sleep 0.2; i=$((i+1))
done
[ ! -e /var/run/nx300m-preview-backend ]
[ "$(cat /perf/.state/lut-preview)" = 0 ]

echo LUT_LIFECYCLE_ARM_PASSED
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
               'arm-vm/mfw-source/lut-load.sh': (ROOT/'mod/deployment/mfw/lut-load.sh').read_bytes(),
               'arm-vm/mfw-source/lut-boot.sh': (ROOT/'mod/deployment/mfw/lut-boot.sh').read_bytes(),
               'arm-vm/mfw-source/bin/ram_asset_verify.arm': (ROOT/'outputs/controls/ram-asset-verify/ram_asset_verify.arm').read_bytes(),
               'arm-vm/mfw-source/smartfilter-live-preview.sh': (ROOT/'mod/deployment/mfw/smartfilter-live-preview.sh').read_bytes(),
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


def collect_extra(out,guest,name):
    import shutil as _sh
    src=guest/name
    if src.exists():
        out.parent.mkdir(parents=True,exist_ok=True)
        _sh.copyfile(src,out/Path(name).name)


def run():
    prepare()
    report = {'passed': False, 'physical_camera_verified': False,
              'scope': 'Isolated ABI7 ARM VM, fixture camera; LUT/preview/preference lifecycle per 2026-11-07 rules'}
    try:
        print(call('start'), flush=True)
        deadline = time.monotonic()+120
        while time.monotonic() < deadline:
            if 'PRIVATE_VM_READY' in (OUT/'serial.log').read_text(errors='replace'):
                break
            time.sleep(.5)
        else:
            raise TimeoutError('ARM boot timeout')
        result = call('exec', '/bin/sh /arm-vm/mfw-probe.sh', vm_timeout=300)
        arm_vm.write_json(OUT/'execution.json', result)
        boundary.OUT = OUT
        boundary.call = call
        guest = boundary.collect()
        for extra in ('worker-fnkey.log','worker-status.txt','worker-ps.txt','logs/boot-trace.log'):
            try: collect_extra(out,guest,extra)
            except Exception: pass
        if result['exit_code'] or 'LUT_LIFECYCLE_ARM_PASSED' not in result['output']:
            raise RuntimeError('Lifecycle probe failed; see execution.json and guest evidence')
        off1 = (guest/'off-1.json').read_text()+(guest/'off-1.stderr').read_text()
        assert 'operation=pause phase=end rc=0' in off1
        assert re.search(r'TracerPid:\s+0\b', (guest/'target-final.status').read_text())
        report.update(passed=True,
                      pause_preserves_preference=True,
                      stock_boot_preserves_preference=True,
                      reapply_restores_preview_per_preference=True,
                      preference_off_applies_without_hardware=True,
                      native_still_slots_restored=True,
                      target_responsive_after_final_off=True)
    except Exception as exc:
        report['error'] = str(exc)
        raise
    finally:
        arm_vm.write_json(OUT/'shutdown.json', call('stop'))
        arm_vm.write_json(OUT/'report.json', report)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    import re
    import argparse
    parser=argparse.ArgumentParser(add_help=False)
    parser.add_argument('--output-dir',type=Path,default=OUT)
    args,remaining=parser.parse_known_args()
    OUT=args.output_dir.resolve()
    sys.argv=[sys.argv[0],*remaining]
    vm.OUT = OUT
    vm.SOCKET = SOCKET
    if len(sys.argv) > 1 and sys.argv[1] == '--linux':
        print(json.dumps(vm.linux_worker(sys.argv[2], sys.argv[3], timeout=300)))
    else:
        run()
