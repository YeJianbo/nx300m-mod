"""Exercise stock SmartFilter ARM control flow with a named software pixel boundary.

Reuses the private full-system MFW user-space fixture. No camera, card, ISP,
physical DMA, concurrent capture, or production RAM hooks are accessed.
"""
import argparse
import base64
import gzip
import io
import json
from pathlib import Path
import re
import shlex
import subprocess
import tarfile
import time

import arm_vm
import probe_lut_preview_vm as preview
import probe_mfw_ram_vm as vm
from cube_to_d4 import compile_cube
from mfw_runtime import LIBRARY, firmware_profile
from build_smartfilter_lut_bridge import build as build_bridge, LIBRARY as BRIDGE_LIBRARY

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'outputs/deployment/smartfilter-preview/arm-vm'
SOCKET = '/tmp/nx300m-smartfilter-private'
MARKER = 'SMARTFILTER_PRIVATE_VM_READY'
BOOT_TIMEOUT = 240
PROBE = r'''#!/bin/sh
set -eu
ulimit -c 0
cd /arm-vm/mfw-results
uname -a > uname.txt
sha256sum /usr/lib/libmisc.so > mfw-library.sha256
LD_LIBRARY_PATH=/mnt/mmc/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH} /rescue/busybox timeout 45 /mnt/mmc/bin/smartfilter_fixture.arm > fixture.stdout 2> fixture.stderr
echo SMARTFILTER_ARM_BOUNDARY_PASSED
'''


def configure():
    vm.OUT = OUT
    vm.SOCKET = SOCKET
    vm.INIT = preview.INIT.replace('LUT_PREVIEW_PRIVATE_VM_READY', MARKER)
    vm.PROBE = PROBE


def call(action, command=''):
    result = subprocess.run(['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'python3',
                             arm_vm.wsl_path(__file__), '--linux', action,
                             base64.b64encode(command.encode()).decode()],
                            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    return json.loads(result.stdout)


def prepare(initramfs=True):
    import numpy as np

    OUT.mkdir(parents=True, exist_ok=True)
    firmware_profile('mfw', prepare=True)
    bridge = build_bridge()
    fixture = OUT/'smartfilter_fixture.arm'
    script = f'''set -eu
tc=/opt/nx300m-toolchain/usr
rf=/opt/nx300m-lab/stock-1.15
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \
 -std=gnu99 -O1 -Wall -Wextra -Werror -march=armv7-a -mfloat-abi=soft -fno-pic -no-pie -fno-stack-protector \
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 -Wl,--export-dynamic \
 -Wl,--allow-shlib-undefined -Wl,-rpath-link,"$rf/usr/lib" -Wl,-rpath-link,"$rf/lib" \
 {shlex.quote(arm_vm.wsl_path(ROOT/'runtime/smartfilter_fixture.c'))} \
 {shlex.quote(arm_vm.wsl_path(ROOT/'work/reference/metol/test_server/filmlab/src/crt0_arm.S'))} \
 {shlex.quote(arm_vm.wsl_path(BRIDGE_LIBRARY))} \
 "$rf/lib/libpthread.so.0" "$rf/lib/libc-2.13.so" "$rf/lib/libdl.so.2" -lgcc \
 -o {shlex.quote(arm_vm.wsl_path(fixture))}
'''
    built = subprocess.run(['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'sh', '-s'],
                           input=script.encode(), capture_output=True)
    (OUT/'fixture-build.stderr.log').write_bytes(built.stderr)
    if built.returncode:
        raise RuntimeError(f'Fixture build exit {built.returncode}: {built.stderr.decode(errors="replace")}')
    stage = OUT/'source-files'
    for name, path in {'bin/smartfilter_fixture.arm': fixture, 'lib/libmisc.so': LIBRARY,
                       'lib/libnx-smartfilter-lut.so': BRIDGE_LIBRARY}.items():
        dest = stage/name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(path.read_bytes())
    axis = (0., 1.)
    identity = [(r, g, b) for b in axis for g in axis for r in axis]
    monochrome = [(v,)*3 for r, g, b in identity for v in [.299*r+.587*g+.114*b]]
    for name, values in [('identity', identity), ('monochrome', monochrome)]:
        (stage/(name+'.d4lut')).write_bytes(compile_cube((2, (0.,)*3, (1.,)*3, values)))
    # Constant 2x2 RGB blocks avoid a second chroma resampling variable.
    yy, xx = np.indices((24, 32))
    rgb = np.stack((32+xx*6, 32+yy*8, 40+(xx+yy)%20*8), -1).astype(float)
    y = .299*rgb[..., 0]+.587*rgb[..., 1]+.114*rgb[..., 2]
    cb = -.168736*rgb[..., 0]-.331264*rgb[..., 1]+.5*rgb[..., 2]+128
    cr = .5*rgb[..., 0]-.418688*rgb[..., 1]-.081312*rgb[..., 2]+128
    y = np.repeat(np.repeat(y, 2, 0), 2, 1)
    chroma = np.stack((cb, cr), -1).reshape(24, 64)
    (stage/'input.yuv').write_bytes(np.floor(np.r_[y.ravel(), chroma.ravel()]+.5).astype('uint8').tobytes())
    inventory = {p.relative_to(stage).as_posix(): p.stat().st_size for p in stage.rglob('*') if p.is_file()}
    arm_vm.write_json(OUT/'candidate.json', {'inventory': inventory, 'mfw': firmware_profile('mfw'), 'bridge_build': bridge,
        'stock_execution': ['SmartFilter Prepare/Execute/t_Main/execute/smartFilter',
                            'CCapImage getters, ADE_Image constructor and ADE YCC wrapper',
                            'ADE initialization, IQ processor and parameter-list Reset/Update'],
        'test_doubles': ['single-message queue', 'environment and work RAM',
                         'legacy effect to ADE YCC bridge', 'UDD software-reference pixels',
                         'reference-backend lease and saved-table restoration in controller checks',
                         'native-adapter UDD calls and IQ colour-node Update implementations',
                         'stock-effect delegate for routing checks', 'cache-flush recording callback'],
        'hardware_pixels': False})
    if initramfs:
        vm.initramfs()


def collect():
    result = call('exec', '/rescue/busybox tar -czf /tmp/smartfilter-evidence.tar.gz -C /arm-vm mfw-results; '
                         '/rescue/busybox base64 /tmp/smartfilter-evidence.tar.gz')
    if result['exit_code']:
        raise RuntimeError('Evidence collection failed')
    raw = base64.b64decode(''.join(line.strip() for line in result['output'].splitlines()
                                  if re.fullmatch('[A-Za-z0-9+/=]+', line.strip())), validate=True)
    (OUT/'guest-evidence.tar.gz').write_bytes(raw)
    unpacked = gzip.decompress(raw)
    with tarfile.open(fileobj=io.BytesIO(unpacked)) as package:
        package.extractall(OUT/'guest', filter='data')
    return OUT/'guest/mfw-results'


def verify(guest):
    import numpy as np

    # The original ADE error printf has no trailing newline.
    checks = [json.loads(line[line.index('{"check":'):])
              for line in (guest/'fixture.stdout').read_text().splitlines() if '{"check":' in line]
    expected = {'no_work_returns_true_without_queue', 'busy_skip_and_bring_down_does_not_drain',
                'passthrough', 'identity', 'monochrome', 'stock_filter_off_skips_callback', 'stock_effect_failure_asserts',
                'single_slot_ram_selection_strength_and_generation',
                'other_slot_and_out_of_preview_effect_delegation',
                'backend_busy_and_error_complete_with_passthrough',
                'capture_priority_drains_inflight_before_admission',
                'capture_during_begin_and_pause_resume',
                'restore_failure_stops_backend_and_reports_recovery',
                'native_api_upload_cache_and_iq_reset_reload',
                'native_api_lock_contention_and_error_recovery',
                's2_filter_off_managed_work_runs_bridge',
                's2_lr_filter_keeps_other_callers_stock',
                's2_forged_mode_without_work_does_not_queue',
                's2_filter_on_uses_stock_work'}
    if {c['check'] for c in checks} != expected or not all(c['passed'] for c in checks):
        raise ValueError('Incomplete stock control-flow results')
    source = np.frombuffer((OUT/'source-files/input.yuv').read_bytes(), dtype='uint8').astype(int)
    passing = np.frombuffer((guest/'passthrough.yuv').read_bytes(), dtype='uint8').astype(int)
    identity = np.frombuffer((guest/'identity.yuv').read_bytes(), dtype='uint8').astype(int)
    mono = np.frombuffer((guest/'monochrome.yuv').read_bytes(), dtype='uint8').astype(int)
    if not np.array_equal(source, passing):
        raise ValueError('Reference passthrough changed input pixels')
    identity_error = int(np.abs(source-identity).max())
    if identity_error > 1 or not np.all(mono[64*48:] == 128) or np.abs(mono[:64*48]-source[:64*48]).max() > 1:
        raise ValueError('Reference identity/monochrome pixels differ from expected values')
    if (guest/'mfw-library.sha256').read_text().split()[0] != firmware_profile('mfw')['libmisc_sha256']:
        raise ValueError('MFW overlay mismatch')
    def frame(name):
        return np.frombuffer((guest/(name+'.yuv')).read_bytes(), dtype='uint8').astype(int)
    if not np.array_equal(frame('slot-identity'), identity) or not np.array_equal(frame('slot-return'), identity):
        raise ValueError('Single-slot identity differs from the reference or accumulates changes')
    if not np.array_equal(frame('slot-monochrome'), mono):
        raise ValueError('Single-slot monochrome differs from the reference')
    if not np.array_equal(frame('native-identity'),identity) or not np.array_equal(frame('native-monochrome'),mono):
        raise ValueError('Native API adapter pixels differ from the reference backend')
    half_chroma = frame('slot-half')[64*48:]-128
    source_chroma = source[64*48:]-128
    half_error = int(np.abs(half_chroma-source_chroma*.5).max())
    if half_error > 2 or not np.any(half_chroma):
        raise ValueError(f'50% monochrome strength differs from the reference, error={half_error}')
    return {'passed': True, 'checks': checks, 'reference_pixels': {
        'passthrough_byte_identical': True, 'identity_max_ycc_error_8bit': identity_error,
        'monochrome_chroma': 128, 'input_unmodified': True,
        'half_strength_max_chroma_deviation_8bit': half_error,
        'single_slot_switch_returns_identical_identity': True,
        'model': 'BT.601 full-range, CbCr-packed 420, RGB trilinear 9^3; reference assumptions'},
        'stock_arm_control_flow': True, 'mfw_library_verified': True,
        'single_slot_controller_verified': True, 'controller_capture_handoff_verified': True,
        'native_ipcs_api_adapter_verified': True,
        'stock_iq_parameter_reset_and_list_update_executed': True,
        'hardware_isp_pixels_verified': False, 'cache_coherency_verified': False,
        'capture_arbitration_verified': False, 'physical_camera_or_card_accessed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--linux', choices=('start', 'exec', 'stop'))
    parser.add_argument('payload', nargs='?', default='')
    args = parser.parse_args()
    configure()
    if args.linux:
        print(json.dumps(vm.linux_worker(args.linux, args.payload)))
        return
    OUT.mkdir(parents=True, exist_ok=True)
    report = {'passed': False, 'phase': 'prepare'}
    arm_vm.write_json(OUT/'report.json', report)
    start_attempted = False
    try:
        prepare()
        report['phase'] = 'boot'
        start_attempted = True
        print(json.dumps(call('start')), flush=True)
        deadline = time.monotonic()+BOOT_TIMEOUT
        while time.monotonic() < deadline:
            serial=OUT/'serial.log'
            if serial.exists() and MARKER in serial.read_text(errors='replace'):
                break
            time.sleep(.5)
        else:
            raise TimeoutError('Private SmartFilter VM boot timed out')
        report['phase'] = 'execute'
        execution = call('exec', '/bin/sh /arm-vm/mfw-probe.sh')
        arm_vm.write_json(OUT/'execution.json', execution)
        guest = collect()
        if execution['exit_code'] or 'SMARTFILTER_ARM_BOUNDARY_PASSED' not in execution['output']:
            raise RuntimeError('ARM boundary probe failed; see execution.json and guest/fixture.stderr')
        report = verify(guest)
    except Exception as exc:
        report['error'] = str(exc)
        raise
    finally:
        if start_attempted:
            try:
                arm_vm.write_json(OUT/'shutdown.json', call('stop'))
                report['private_vm_stopped'] = True
            except Exception as exc:
                report['private_vm_stopped'] = False
                report['shutdown_error'] = str(exc)
        arm_vm.write_json(OUT/'report.json', report)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
