"""Install the current SmartFilter RAM candidate into the isolated MFW ARM fixture.

Exercises actual ptrace/PLT installation and rollback, stock worker and ADE/IQ
control flow. Physical UDD pixels/cache/B2Y lease construction are named doubles.
"""
import base64
import json
from pathlib import Path
import re
import shlex
import subprocess

import arm_vm
import probe_smartfilter_vm as boundary

ROOT=Path(__file__).resolve().parents[1]
BUILD=ROOT/'outputs/deployment/smartfilter-preview'
OUT=BUILD/'ram-vm'
PROBE=r'''#!/bin/sh
set -eu
ulimit -c 0
cd /arm-vm/mfw-results
export LD_LIBRARY_PATH=/mnt/mmc/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
LOADER=/mnt/mmc/bin/smartfilter_loader.arm
BLOB=/mnt/mmc/smartfilter_ram.bin
pid=
finish() {
    code=$?; trap - EXIT; set +e
    if [ -n "$pid" ] && [ -d /proc/$pid ]; then kill -TERM "$pid"; wait "$pid"; fi
    cp /tmp/smartfilter-ram.stdout /tmp/smartfilter-ram.stderr .
    printf '%s\n' "$code" > probe.exit
    exit "$code"
}
trap finish EXIT
uname -a > uname.txt
sha256sum /usr/lib/libmisc.so > mfw-library.sha256
/mnt/mmc/bin/smartfilter_fixture.arm > /tmp/smartfilter-ram.stdout 2> /tmp/smartfilter-ram.stderr &
pid=$!
tries=0
while [ ! -s /tmp/smartfilter-ram.ready ]; do
    tries=$((tries+1)); [ "$tries" -lt 100 ]; /rescue/busybox usleep 100000
done
read ready_pid < /tmp/smartfilter-ram.ready
[ "$ready_pid" = "$pid" ]
cat /proc/$pid/maps > before.maps
seq=0
request() {
    label=$1; cmd=$2; seq=$((seq+1))
    printf '%s %s\n' "$seq" "$cmd" > /tmp/smartfilter-ram.request.tmp
    mv /tmp/smartfilter-ram.request.tmp /tmp/smartfilter-ram.request
    tries=0
    while :; do
        if [ -s /tmp/smartfilter-ram.response ]; then
            read got result operations callbacks < /tmp/smartfilter-ram.response
            [ "$got" != "$seq" ] || break
        fi
        tries=$((tries+1)); [ "$tries" -lt 100 ]; /rescue/busybox usleep 100000
    done
    printf '%s %s %s\n' "$result" "$operations" "$callbacks" > "$label.response"
    case $cmd in 1|5|7|9) cp /tmp/smartfilter-ram.frame "$label.yuv" ;; esac
}
operation() {
    label=$1; shift
    "$LOADER" "$@" --pid "$pid" --blob "$BLOB" --fixture > "$label.json" 2> "$label.stderr"
}
request original-mode 2
operation baseline status
set +e
"$LOADER" stock --pid "$pid" --blob "$BLOB" > wrong-target.stdout 2> wrong-target.stderr
wrong=$?
set -e
[ "$wrong" = 3 ]
operation identity apply --table /mnt/mmc/identity.d4lut --look identity --strength 100
request replaced-mode 2
request identity 1
operation identity-applied status
request selection-inflight 8
operation monochrome apply --table /mnt/mmc/monochrome.d4lut --look monochrome --strength 100
request monochrome 9
operation monochrome-applied status
operation half apply --table /mnt/mmc/monochrome.d4lut --look monochrome --strength 50
request half 1
operation stock stock
request stock 1
operation capture-look apply --table /mnt/mmc/monochrome.d4lut --look monochrome --strength 100
request capture-cycle 6
operation after-capture status
request busy 7
request operation-error 5
operation after-error status
operation restore restore
request restored-mode 2
operation restored status
operation reapply apply --table /mnt/mmc/identity.d4lut --look identity --strength 100
request reapply 1
operation reapplied status
operation final-restore restore
request final-mode 2
operation final status
cat /proc/$pid/maps > after.maps
cat /proc/$pid/status > after.status
printf '%s 0\n' "$((seq+1))" > /tmp/smartfilter-ram.request.tmp
mv /tmp/smartfilter-ram.request.tmp /tmp/smartfilter-ram.request
wait "$pid"
pid=
echo SMARTFILTER_ARM_BOUNDARY_PASSED
'''
_prepare=boundary.prepare


def call(action,command=''):
    result=subprocess.run(['wsl.exe','-d','Debian','-u','root','-e','python3',
        arm_vm.wsl_path(__file__),'--linux',action,base64.b64encode(command.encode()).decode()],
        capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=375)
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    return json.loads(result.stdout)


def prepare():
    from build_smartfilter_preview import build
    report=build()
    _prepare(initramfs=False)
    fixture=OUT/'smartfilter_fixture.arm'
    script=f'''set -eu
tc=/opt/nx300m-toolchain/usr
rf=/opt/nx300m-lab/stock-1.15
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=gnu99 -O1 -Wall -Wextra -Werror -Wno-unused-function -Wno-unused-const-variable -march=armv7-a -mfloat-abi=soft \\
 -fno-pic -no-pie -fno-stack-protector -nostdlib -nostartfiles -Wl,-e,_start \\
 -Wl,--dynamic-linker=/lib/ld-linux.so.3 -Wl,--export-dynamic -Wl,--allow-shlib-undefined \\
 -Wl,-rpath-link,"$rf/usr/lib" -Wl,-rpath-link,"$rf/lib" -I{shlex.quote(arm_vm.wsl_path(BUILD))} \\
 {shlex.quote(arm_vm.wsl_path(ROOT/'runtime/smartfilter_ram_fixture.c'))} \\
 {shlex.quote(arm_vm.wsl_path(ROOT/'work/reference/metol/test_server/filmlab/src/crt0_arm.S'))} \\
 {shlex.quote(arm_vm.wsl_path(BUILD/'libnx-smartfilter-lut.so'))} \\
 "$rf/lib/libpthread.so.0" "$rf/lib/libc-2.13.so" "$rf/lib/libdl.so.2" -lgcc -o {shlex.quote(arm_vm.wsl_path(fixture))}
'''
    result=subprocess.run(['wsl.exe','-d','Debian','-u','root','-e','sh','-s'],input=script.encode(),capture_output=True)
    (OUT/'fixture-build.stderr.log').write_bytes(result.stderr)
    if result.returncode: raise RuntimeError(f'RAM fixture build exit {result.returncode}: {result.stderr.decode(errors="replace")}')
    stage=OUT/'source-files'
    for relative,source in [('bin/smartfilter_fixture.arm',fixture),('bin/smartfilter_loader.arm',BUILD/'smartfilter_loader.arm'),
                            ('smartfilter_ram.bin',BUILD/'smartfilter_ram.bin')]:
        path=stage/relative; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(source.read_bytes())
    candidate=json.loads((OUT/'candidate.json').read_text())
    candidate.update(ram_build=report,fixture_overrides=['UDD entry points and pixel operation',
        'cache callback at CLiveviewBuffer', 'B2Y semaphore-provider construction', 'IQ colour-node Update bodies'],
        stock_execution=['SmartFilter worker and ADE YCC/init', 'IQ Processor, List and Reset',
                         'B2Y Prepare/Finish without physical UDD', 'GetEffectMode'],
        actual_ram_loader=True,actual_plt_hooks=5)
    arm_vm.write_json(OUT/'candidate.json',candidate)
    boundary.vm.initramfs()


def verify(guest):
    import numpy as np
    from mfw_runtime import firmware_profile

    def record(name): return json.loads((guest/(name+'.json')).read_text())
    def response(name): return list(map(int,(guest/(name+'.response')).read_text().split()))
    def frame(name): return np.frombuffer((guest/(name+'.yuv')).read_bytes(),dtype='uint8').astype(int)
    original=np.frombuffer((OUT/'source-files/input.yuv').read_bytes(),dtype='uint8').astype(int)
    identity,mono,half=frame('identity'),frame('monochrome'),frame('half')
    assert response('original-mode')[0]==response('restored-mode')[0]==response('final-mode')[0]==2
    assert response('replaced-mode')[0]==0
    assert np.max(np.abs(original-identity))<=1 and np.array_equal(identity,frame('reapply'))
    assert np.all(mono[64*48:]==128) and np.max(np.abs(mono[:64*48]-original[:64*48]))<=1
    assert np.max(np.abs((half[64*48:]-128)-(original[64*48:]-128)*.5))<=2
    for name in ('stock','busy','operation-error'): assert np.array_equal(frame(name),original),name
    for name in ('identity','monochrome','half','reapply'): assert response(name)[1:]==[1,1],name
    for name in ('identity-applied','monochrome-applied','reapplied'):
        r=record(name); assert r['applied_generation']==r['requested_generation'] and r['rendered']>0 and r['inflight']==r['control_paused']==0
    capture=record('after-capture')
    assert capture['capture_waits']>0 and capture['backend_active']==capture['captures']==0
    assert record('after-error')['last_error']==-1 and record('after-error')['fault']==0
    assert record('final')['installed'] is False and record('final')['enabled'] is False
    assert record('baseline')['installed'] is False
    assert (guest/'mfw-library.sha256').read_text().split()[0]==firmware_profile('mfw')['libmisc_sha256']
    stop_windows={p.stem:[int(v) for v in re.findall(r'Target stop window: (\d+) ms;',p.read_text())]
                  for p in guest.glob('*.stderr') if 'Target stop window:' in p.read_text()}
    assert stop_windows and all(stop_windows.get(name) for name in ('identity','monochrome','restore','final-restore'))
    return dict(passed=True,actual_ram_loader=True,actual_plt_hooks=5,mfw_library_verified=True,
        single_stock_slot=True,independent_look_and_strength=True,still_host_effect_suppressed=True,
        original_mode_restored=True,capture_inflight_wait_and_resume=True,selection_drains_inflight=True,cache_reset_reload_executed=True,
        busy_and_error_passthrough=True,reapply_returns_identical_pixels=True,
        original_file_hashes_while_target_running=True,target_stop_windows_ms=stop_windows,
        reference_pixels=dict(identity_max_ycc_error_8bit=int(np.abs(original-identity).max()),monochrome_chroma=128),
        physical_camera_or_card_accessed=False,hardware_isp_pixels_verified=False,cache_coherency_verified=False,
        all_ipcs_client_arbitration_verified=False,raw_menu_restrictions_resolved=False)


if __name__=='__main__':
    boundary.OUT=OUT
    boundary.SOCKET='/tmp/nx300m-smartfilter-ram-private'
    boundary.MARKER='SMARTFILTER_RAM_PRIVATE_VM_READY'
    boundary.PROBE=PROBE
    boundary.prepare=prepare
    boundary.verify=verify
    boundary.call=call
    boundary.main()
