"""Private full-system ARM IQ Update/GOT submission test; no pixel ISP claim."""
import argparse
import base64
import hashlib
import io
import json
from pathlib import Path
import re
import shlex
import shutil
import struct
import subprocess
import tarfile
import time
import zlib

import arm_vm
import probe_mfw_ram_vm as vm
from mfw_runtime import LIBRARY, firmware_profile

PROJECT = Path(__file__).resolve().parents[1]
OUT = PROJECT / 'outputs/deployment/ipcm-preview/arm-vm'
SOCKET = '/tmp/nx300m-ipcm-preview-private'
INIT = r'''#!/rescue/busybox sh
export PATH=/bin:/sbin:/usr/bin:/usr/sbin
/rescue/busybox mount -t devtmpfs devtmpfs /dev
exec </dev/ttyAMA0 >/dev/ttyAMA0 2>&1
/rescue/busybox mount -t proc proc /proc
/rescue/busybox mount -t sysfs sysfs /sys
/rescue/busybox mkdir -p /tmp /dev/pts /mnt/mmc /var/run /dev/shm /arm-vm/mfw-results
/rescue/busybox mount -t devpts devpts /dev/pts
/rescue/busybox mount -t tmpfs -o rw,exec,mode=1777,size=32m ipcm-runtime /tmp
/rescue/busybox mount -t tmpfs -o mode=1777 tmpfs /dev/shm
/rescue/busybox mount --bind /arm-vm/mfw-source /mnt/mmc
/rescue/busybox mount -o remount,bind,ro /mnt/mmc
/rescue/busybox mount --bind /mnt/mmc/lib/libmisc.so /usr/lib/libmisc.so
echo IPCM_PREVIEW_PRIVATE_VM_READY
while true; do /rescue/busybox setsid /bin/sh -i </dev/ttyAMA0 >/dev/ttyAMA0 2>&1; done
'''
PROBE = r'''#!/bin/sh
set -eu
OUT=/arm-vm/mfw-results
LOADER=/mnt/mmc/bin/ipcm_preview_loader.arm
BLOB=/mnt/mmc/ipcm_preview_blob.bin
PROFILE=/mnt/mmc/profile.bin
LEGACY=/mnt/mmc/legacy-profile.bin
PCSE=/mnt/mmc/pcse-profile.bin
pid=
finish() {
    code=$?; trap - EXIT; set +e
    if [ -n "$pid" ] && [ -d /proc/$pid ]; then
        cat /proc/$pid/status > "$OUT/failure.status"
        cat /proc/$pid/maps > "$OUT/failure.maps"
        kill -TERM "$pid"; wait "$pid"
    fi
    cp /tmp/ipcm-fixture.* "$OUT/"
    printf '%s\n' "$code" > "$OUT/probe.exit"
    exit "$code"
}
trap finish EXIT
uname -a > "$OUT/uname.txt"
sha256sum /usr/lib/libmisc.so /usr/lib/libiq-fw-slpcam.so /usr/lib/libudd.so.3.1 > "$OUT/library.sha256"
/mnt/mmc/bin/ipcm_preview_fixture.arm > /tmp/ipcm-fixture.stdout 2> /tmp/ipcm-fixture.stderr &
pid=$!; attempt=0
while [ ! -s /tmp/ipcm-fixture.ready ]; do
    attempt=$((attempt+1)); [ "$attempt" -lt 60 ]; /rescue/busybox usleep 100000
done
read ready_pid ccm tone chm pcse color gamma pcse_node < /tmp/ipcm-fixture.ready
[ "$ready_pid" = "$pid" ]
cat /proc/$pid/maps > "$OUT/before.maps"
cat /proc/$pid/stat > "$OUT/before.stat"
ls /proc/$pid/task > "$OUT/before.tasks"
seq=0
frame() {
    tag=$1; variant=$2; strength=$3; force=$4; mask=${5:-3}; profile=${6:-v2}
    seq=$((seq+1))
    printf '%s %s %s %s %s %s-v%s-s%s\n' "$seq" "$variant" "$force" "$mask" "$tag" "$profile" "$variant" "$strength" > /tmp/ipcm-request.tmp
    mv /tmp/ipcm-request.tmp /tmp/ipcm-request
    attempt=0
    while :; do
        if [ -s /tmp/ipcm-response ]; then
            read got got_tag < /tmp/ipcm-response
            [ "$got" != "$seq" ] || break
        fi
        attempt=$((attempt+1)); [ "$attempt" -lt 80 ]; /rescue/busybox usleep 100000
    done
    [ "$got_tag" = "$tag" ]
    "$LOADER" status --pid "$pid" --blob "$BLOB" --fixture > "$OUT/$tag.json" 2> "$OUT/$tag.stderr"
}
operation() {
    op=$1; label=$2; shift 2
    "$LOADER" "$op" --pid "$pid" --blob "$BLOB" --fixture "$@" > "$OUT/$label.json" 2> "$OUT/$label.stderr"
}
frame baseline 0 0 1
operation apply apply0 --profile "$PROFILE" --strength 0
frame passthrough 0 0 0
operation apply apply100 --profile "$PROFILE" --strength 100
frame full100 0 100 0
cat /proc/$pid/maps > "$OUT/installed.maps"
set +e
"$LOADER" apply --pid "$pid" --blob "$BLOB" --fixture --profile /mnt/mmc/malformed.bin --strength 100 > "$OUT/malformed.stdout" 2> "$OUT/malformed.stderr"
malformed=$?
set -e
printf '%s\n' "$malformed" > "$OUT/malformed.exit"
[ "$malformed" != 0 ]
operation status malformed-after
for case in range tone; do
    set +e
    "$LOADER" apply --pid "$pid" --blob "$BLOB" --fixture --profile "/mnt/mmc/invalid-$case.bin" --strength 100 > "$OUT/invalid-$case.stdout" 2> "$OUT/invalid-$case.stderr"
    rejected=$?
    set -e
    printf '%s\n' "$rejected" > "$OUT/invalid-$case.exit"
    [ "$rejected" != 0 ]
    operation status "invalid-$case-after"
done
frame repeat100 0 100 1
operation apply apply50 --profile "$PROFILE" --strength 50
frame half50 0 50 0
frame wb50 1 50 0
operation stock stock
frame stock-latest 1 0 0
operation restore restore
operation apply apply-v1 --profile "$LEGACY" --strength 100
frame v1compat 1 100 0 3 v1
operation apply switch-v2 --profile "$PROFILE" --strength 100
frame switch-ycc 1 100 0 2 v2
frame switch-ycc-retry 1 100 2 2 v2
operation apply switch-v1 --profile "$LEGACY" --strength 100
frame switch-chm 1 100 0 1 v1
frame switch-chm-retry 1 100 2 1 v1
operation stock independent-stock
frame stock-chm 1 0 0 1 v1
set +e
operation restore restore-pending
pending=$?
set -e
printf '%s\n' "$pending" > "$OUT/restore-pending.exit"
[ "$pending" = 4 ]
frame restore-forward 1 0 0 2 v1
operation restore final-restore
frame final 1 0 1
operation apply pcse-apply0 --profile "$PCSE" --strength 0
frame pcse-zero 1 0 0 15 v3
operation apply pcse-apply100 --profile "$PCSE" --strength 100
frame pcse-full 1 100 0 15 v3
frame pcse-repeat 1 100 1 15 v3
operation apply pcse-apply50 --profile "$PCSE" --strength 50
frame pcse-half 1 50 0 15 v3
frame pcse-fresh 0 50 0 15 v3
operation apply pcse-overflow-apply --profile /mnt/mmc/pcse-overflow.bin --strength 100
frame pcse-overflow 0 100 0 15 overflow
operation apply pcse-before-retire --profile "$PCSE" --strength 100
frame pcse-styled 0 100 0 15 v3
operation apply pcse-retire-v2 --profile "$PROFILE" --strength 100
frame pcse-retire-hue 0 100 0 7 v2
frame pcse-retire-sat 0 100 1 11 v2
operation apply pcse-before-stock --profile "$PCSE" --strength 100
frame pcse-before-stock-frame 0 100 0 15 v3
operation stock pcse-stock
frame pcse-stock-hue 0 0 0 7 v3
set +e
operation restore pcse-restore-pending
pcse_pending=$?
set -e
[ "$pcse_pending" = 4 ]
frame pcse-stock-sat 0 0 1 11 v3
operation restore pcse-final-restore
frame pcse-final 0 0 1 15 v3
cat /proc/$pid/maps > "$OUT/after.maps"
cat /proc/$pid/stat > "$OUT/after.stat"
cat /proc/$pid/status > "$OUT/after.status"
ls /proc/$pid/task > "$OUT/after.tasks"
printf '%s 99 0 3 exit v2-v0-s0\n' "$((seq+1))" > /tmp/ipcm-request.tmp
mv /tmp/ipcm-request.tmp /tmp/ipcm-request
wait "$pid"; pid=
echo IPCM_PREVIEW_FULL_ARM_PASSED
'''


def configure():
    vm.OUT = OUT; vm.SOCKET = SOCKET; vm.INIT = INIT; vm.PROBE = PROBE


def call(action, command=''):
    result = subprocess.run(['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'python3',
        arm_vm.wsl_path(__file__), '--linux', action, base64.b64encode(command.encode()).decode()],
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=90)
    if result.returncode:
        raise RuntimeError(f'WSL exit {result.returncode}: {result.stderr or result.stdout}')
    return json.loads(result.stdout)


def rounded(n, d):
    return -( (-n + d//2)//d) if n < 0 else (n + d//2)//d


def expected(profile, variant, strength):
    """Independent integer composition, then the genuine UDD packs its MMIO."""
    ccm = [145,-13,-4,-7,139,-4,-3,-9,140]
    ccm = [x + (variant*7 if i%4 == 0 else 0) for i,x in enumerate(ccm)]
    chm = [0]*19; chm[0] = 1; chm[6:10] = [128+variant*4,7,-5,127+variant*3]
    ycc = [0]*19; ycc[0] = 3
    ycc[10:19] = [v+(variant if i%4==0 else 0) for i,v in enumerate([77,150,29,-43,-85,128,128,-107,-21])]
    tone = bytes(min(255, i*255//2047+variant*2) for i in range(2048))
    version=struct.unpack_from('<I',profile,4)[0]
    c=struct.unpack_from('<9i',profile,32)
    h=struct.unpack_from('<4i' if version==1 else '<9i',profile,68)
    lut=profile[84:340] if version==1 else profile[104:360]
    def compose(base, coefficients, n):
        mix = [rounded((4096 if i//n == i%n else 0)*(100-strength)+v*strength,100)
               for i,v in enumerate(coefficients)]
        return [rounded(sum(mix[r*n+k]*base[k*n+col] for k in range(n)),4096)
                for r in range(n) for col in range(n)]
    ccm = compose(ccm, c, 3)
    if version==1:chm[6:10]=compose(chm[6:10],h,2)
    else:ycc[10:19]=compose(ycc[10:19],h,3)
    tone = bytes(rounded(v*(100-strength)+lut[v]*strength,100) for v in tone)
    pcse=[]
    for selector in range(2):
        base=[i*3-16+variant*7+selector*40 for i in range(12)]
        delta=struct.unpack_from('<12i',profile,360+selector*48) if version==3 else [0]*12
        styled=[v+rounded(d*strength,100) for v,d in zip(base,delta)]
        if any(abs(v)>4095 for v in styled):styled=base
        pcse.extend([selector,*styled])
    return struct.pack('<9i19i19i', *ccm, *chm, *ycc) + tone + struct.pack('<26i',*pcse)


def build_fixture():
    OUT.mkdir(parents=True, exist_ok=True)
    fixture = OUT / 'ipcm_preview_fixture.arm'
    script = f'''set -eu
tc=/opt/nx300m-toolchain/usr
rf=/opt/nx300m-lab/stock-1.15
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \
 -std=gnu99 -O1 -Wall -Wextra -Werror -Wno-misleading-indentation -march=armv7-a -mfloat-abi=soft -fno-pic -no-pie -fno-stack-protector \
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 -Wl,--export-dynamic \
 -Wl,--allow-shlib-undefined -Wl,-rpath-link,"$rf/usr/lib" -Wl,-rpath-link,"$rf/lib" \
 {shlex.quote(arm_vm.wsl_path(PROJECT / 'runtime/ipcm_preview_fixture.c'))} \
 {shlex.quote(arm_vm.wsl_path(PROJECT / 'work/reference/metol/test_server/filmlab/src/crt0_arm.S'))} \
 -L"$rf/usr/lib" -Wl,--no-as-needed -Wl,-l:libiq-fw-slpcam.so \
 -Wl,-l:libdiosal.so -Wl,-l:libproduction-mode.so "$rf/usr/lib/libudd.so.3.1" \
 "$rf/lib/libpthread.so.0" "$rf/lib/libc-2.13.so" "$rf/lib/libdl.so.2" -lgcc \
 -o {shlex.quote(arm_vm.wsl_path(fixture))}
'''
    built = subprocess.run(['wsl.exe','-d','Debian','-u','root','-e','sh','-s'], input=script.encode(), capture_output=True)
    (OUT / 'fixture-build.stderr.log').write_bytes(built.stderr)
    if built.returncode:
        raise RuntimeError(f'Fixture build exit {built.returncode}: {built.stderr.decode(errors="replace")}')
    return fixture


def prepare(profile_path,legacy_path):
    fixture = build_fixture(); firmware_profile('mfw', prepare=True)
    profile = profile_path.read_bytes()
    legacy=legacy_path.read_bytes()
    pcse=bytearray(profile+struct.pack('<24i', *([-7,7]*6+[-11,11]*6)))
    struct.pack_into('<II',pcse,4,3,456);struct.pack_into('<I',pcse,16,zlib.crc32(pcse[32:]))
    overflow=bytearray(pcse);struct.pack_into('<24i',overflow,360,*([4095]*24))
    struct.pack_into('<I',overflow,16,zlib.crc32(overflow[32:]))
    for data,version,size in ((profile,2,360),(legacy,1,340)):
        if len(data)!=size or struct.unpack_from('<I',data,4)[0]!=version or zlib.crc32(data[32:])!=struct.unpack_from('<I',data,16)[0]:
            raise ValueError('Profile version/size/CRC differs')
    inputs = {'bin/ipcm_preview_fixture.arm':fixture,
        'bin/ipcm_preview_loader.arm':OUT.parent/'ipcm_preview_loader.arm',
        'ipcm_preview_blob.bin':OUT.parent/'ipcm_preview_blob.bin','profile.bin':profile_path,
        'legacy-profile.bin':legacy_path,'lib/libmisc.so':LIBRARY}
    stage = OUT / 'source-files'; inventory = {}
    if stage.exists():
        if stage.resolve().parent!=OUT.resolve() or stage.is_symlink():raise ValueError('Unexpected fixture staging path')
        shutil.rmtree(stage)
    for name, source in inputs.items():
        target = stage / name; target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(source.read_bytes())
        inventory[name] = target.stat().st_size
    for name,data in (('pcse-profile.bin',pcse),('pcse-overflow.bin',overflow)):
        (stage/name).write_bytes(data);inventory[name]=len(data)
    for version,data in ((2,profile),(1,legacy),(3,pcse),('overflow',overflow)):
        for variant in (0,1):
            for strength in (0,50,100):
                prefix=f'v{version}' if isinstance(version,int) else version
                name=f'expected/{prefix}-v{variant}-s{strength}.bin'; target=stage/name
                target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(expected(data,variant,strength))
                inventory[name]=target.stat().st_size
    broken=bytearray(profile);broken[32]^=1;(stage/'malformed.bin').write_bytes(broken);inventory['malformed.bin']=len(broken)
    for case in ('range','tone'):
        broken=bytearray(profile)
        if case=='range':struct.pack_into('<i',broken,32,16385)
        else:broken[-256:-254]=bytes((255,0))
        struct.pack_into('<I',broken,16,zlib.crc32(broken[32:]))
        name=f'invalid-{case}.bin';(stage/name).write_bytes(broken);inventory[name]=len(broken)
    arm_vm.write_json(OUT/'candidate.json',{'inventory':inventory,'source':'current local build; no ZIP extraction',
        'profile':str(profile_path),'profile_sha256':hashlib.sha256(profile).hexdigest(),
        'legacy_profile':str(legacy_path),'legacy_profile_sha256':hashlib.sha256(legacy).hexdigest(),
        'scope':'genuine stock IQ Update/Reset and UDD, fixture GetIndex/SetParam, synthetic RAM MMIO',
        'mfw':firmware_profile('mfw')})
    vm.initramfs()


def collect():
    result=call('exec','/rescue/busybox tar -czf /tmp/ipcm-evidence.tar.gz -C /arm-vm mfw-results; /rescue/busybox base64 /tmp/ipcm-evidence.tar.gz')
    if result['exit_code']: raise RuntimeError('Evidence collection failed')
    raw=base64.b64decode(''.join(x.strip() for x in result['output'].splitlines() if re.fullmatch('[A-Za-z0-9+/=]+',x.strip())),validate=True)
    (OUT/'guest-evidence.tar.gz').write_bytes(raw)
    extracted=OUT/'guest'
    if extracted.exists():
        if extracted.resolve().parent!=OUT.resolve() or extracted.is_symlink():raise ValueError('Unexpected guest extraction path')
        shutil.rmtree(extracted)
    with tarfile.open(fileobj=io.BytesIO(raw)) as package: package.extractall(extracted,filter='data')
    return OUT/'guest/mfw-results'


def verify(guest):
    def status(label):
        # The loader supervisor intentionally combines worker stderr/stdout.
        lines=[line for line in (guest/(label+'.json')).read_text().splitlines() if line.startswith('{')]
        if len(lines)!=1:raise ValueError('Expected one status object: '+label)
        return json.loads(lines[0])
    labels=('baseline','passthrough','full100','repeat100','half50','wb50','stock-latest',
            'v1compat','switch-ycc','switch-ycc-retry','switch-chm','switch-chm-retry','stock-chm','restore-forward','final')
    for label in labels:
        f=json.loads((guest/(label+'.fixture.json')).read_text())
        if not f['source_unchanged'] or f['setter_result'] != [0]*5 or f['expected_setter_result'] != [0]*5:
            raise ValueError('Source preservation or real UDD result differs: '+label)
        if f['setparam_calls'][:2] != [x+1 for x in f['calls_before'][:2]]:
            raise ValueError('Original Update did not invoke both fixture SetParam functions: '+label)
        if (guest/(label+'.actual.bin')).read_bytes() != (guest/(label+'.expected.bin')).read_bytes():
            raise ValueError('Real UDD MMIO differs from independent integer composition: '+label)
        if (guest/(label+'.ipcs.bin')).read_bytes() != bytes([0x5a])*0xb000:
            raise ValueError('IPCS RAM changed: '+label)
        if f['update_tid'] != int((guest/'ipcm-fixture.ready').read_text().split()[0]):
            raise ValueError('Update moved off the original fixture thread')
    if (guest/'baseline.actual.bin').read_bytes() != (guest/'passthrough.actual.bin').read_bytes():
        raise ValueError('Zero-strength MMIO differs')
    if (guest/'full100.actual.bin').read_bytes() != (guest/'repeat100.actual.bin').read_bytes():
        raise ValueError('Repeated update accumulated the transform')
    zero=status('passthrough'); fixture=json.loads((guest/'passthrough.fixture.json').read_text())
    if zero['last_source'][:4] != fixture['source_pointer'][:4] or zero['last_forwarded'][:4] != fixture['source_pointer'][:4]:
        raise ValueError('Zero strength changed the original UDD pointer')
    for label in ('full100','half50','wb50'):
        s=status(label)
        if s['pending'] != [1,1,0,1,0,0] or s['inflight'] or s['cache_valid'][:4] != [1]*4 or s['profile_version']!=2:
            raise ValueError('Styled submissions not tracked: '+label)
        f=json.loads((guest/(label+'.fixture.json')).read_text())
        if s['last_forwarded'][2]!=f['source_pointer'][2] or s['last_forwarded'][3]==f['source_pointer'][3]:
            raise ValueError('v2 selector-specific pointer forwarding differs: '+label)
    legacy=status('v1compat');f=json.loads((guest/'v1compat.fixture.json').read_text())
    if legacy['profile_version']!=1 or legacy['pending']!=[1,1,1,0,0,0] or legacy['last_forwarded'][3]!=f['source_pointer'][3] or legacy['last_forwarded'][2]==f['source_pointer'][2]:
        raise ValueError('v1 CHM behavior or original YCC pointer compatibility differs')
    if status('switch-ycc')['pending']!=[1,1,1,1,0,0] or status('switch-chm')['pending']!=[1,1,1,1,0,0]:
        raise ValueError('Profile switch erased the other selector pending state')
    for label in ('switch-ycc','switch-chm'):
        first=status(label);retry=status(label+'-retry')
        if retry['pending']!=[1,1,1,1,0,0] or retry['node_resets'][0]!=first['node_resets'][0]+1:
            raise ValueError('Retired selector pending did not force the next genuine Color Reset: '+label)
    if status('stock-chm')['pending']!=[0,0,0,1,0,0]:
        raise ValueError('CHM stock forwarding cleared the independent YCC pending state')
    if status('malformed-after') != status('full100'): raise ValueError('Malformed profile changed target state')
    for case in ('range','tone'):
        if (guest/f'invalid-{case}.exit').exists() and status(f'invalid-{case}-after')!=status('full100'):
            raise ValueError('Rejected profile changed target state: '+case)
    if (guest/'restore-pending.exit').read_text().strip() != '4' or not status('restore-pending')['installed']:
        raise ValueError('Restore removed hooks before native restoration')
    if status('restore-forward')['pending'] != [0]*6 or status('final')['installed']:
        raise ValueError('Latest stock restoration/removal failed')
    pcse_labels=('pcse-zero','pcse-full','pcse-repeat','pcse-half','pcse-fresh','pcse-overflow','pcse-styled',
                 'pcse-retire-hue','pcse-retire-sat','pcse-before-stock-frame','pcse-stock-hue','pcse-stock-sat','pcse-final')
    for label in pcse_labels:
        f=json.loads((guest/(label+'.fixture.json')).read_text())
        if not f['source_unchanged'] or f['setter_result']!=[0]*5 or f['expected_setter_result']!=[0]*5:
            raise ValueError('PCSE source or UDD result differs: '+label)
        if (guest/(label+'.actual.bin')).read_bytes()!=(guest/(label+'.expected.bin')).read_bytes():
            raise ValueError('PCSE native MMIO differs: '+label)
        if (guest/(label+'.ipcs.bin')).read_bytes()!=bytes([0x5a])*0xb000:
            raise ValueError('PCSE changed IPCS RAM: '+label)
    s=status('pcse-zero')
    if s['last_source']!=s['last_forwarded'] or s['pending']!=[0]*6:
        raise ValueError('v3 zero strength is not original-pointer passthrough')
    if (guest/'pcse-full.actual.bin').read_bytes()!=(guest/'pcse-repeat.actual.bin').read_bytes():
        raise ValueError('PCSE accumulated on repeated native updates')
    if status('pcse-full')['pending']!=[1,1,0,1,1,1] or status('pcse-full')['cache_valid']!=[1]*6:
        raise ValueError('PCSE selector state not independently tracked')
    if status('pcse-overflow')['fallbacks'][4:]!=[1,1]:
        raise ValueError('PCSE native range overflow not rejected per entire selector')
    if status('pcse-retire-hue')['pending'][4:]!=[0,1] or status('pcse-retire-sat')['pending'][4:]!=[0,0]:
        raise ValueError('v3-to-v2 retired PCSE selector recovery differs')
    if status('pcse-stock-hue')['pending']!=[0,0,0,0,0,1] or not status('pcse-restore-pending')['installed']:
        raise ValueError('PCSE restore completed before both selectors forwarded stock')
    if status('pcse-final')['pending']!=[0]*6 or status('pcse-final')['installed']:
        raise ValueError('PCSE native stock restoration/unhook failed')
    if (guest/'before.tasks').read_bytes() != (guest/'after.tasks').read_bytes(): raise ValueError('Thread set changed')
    if not re.search(r'^TracerPid:\s+0$',(guest/'after.status').read_text(),re.M): raise ValueError('Tracer remained attached')
    before=(guest/'before.stat').read_text();after=(guest/'after.stat').read_text()
    if before[before.rfind(')')+2:].split()[19] != after[after.rfind(')')+2:].split()[19]: raise ValueError('Target restarted')
    addresses=[int(x) for x in (guest/'ipcm-fixture.ready').read_text().split()[1:]]
    for filename in ('before.maps','installed.maps','after.maps'):
        maps=(guest/filename).read_text().splitlines()
        for address in addresses:
            matches=[l.split()[1] for l in maps if int(l.split()[0].split('-')[0],16)<=address<int(l.split()[0].split('-')[1],16)]
            if matches != ['r--p']: raise ValueError('Hook slot page permissions differ: '+filename)
    if (guest/'library.sha256').read_text().split()[0] != firmware_profile('mfw')['libmisc_sha256']:
        raise ValueError('Camera-exported MFW library overlay differs')
    stock=json.loads((OUT.parent/'build-report.json').read_text())['stock']
    library_hashes={line.split()[1]:line.split()[0] for line in (guest/'library.sha256').read_text().splitlines()}
    if library_hashes['/usr/lib/libiq-fw-slpcam.so']!=stock['iq']['sha256'] or library_hashes['/usr/lib/libudd.so.3.1']!=stock['udd']['sha256']:
        raise ValueError('Genuine IQ/UDD library identity differs')
    return {'passed':True,'genuine_iq_update_reset':True,'genuine_udd_setters':True,
        'fixture_functions':['Color/Gamma/PCSE GetIndex','Color/Gamma/PCSE SetParam'],
        'integer_math_and_ram_mmio':True,'strengths':[0,50,100],'fresh_base_and_no_accumulation':True,
        'v2_ycc_3x3_and_original_chm_pointer':True,'v1_chm_2x2_and_original_ycc_pointer':True,
        'independent_chm_ycc_pending_across_both_profile_switches':True,'profile_versions':[1,2,3],
        'pcse_native_hue_saturation_24_deltas':True,'pcse_no_accumulation':True,
        'pcse_entire_selector_range_fallback':True,'pcse_independent_selector_restore':True,
        'latest_stock_restore_and_reapply':True,'pending_restore_retains_hooks':True,
        'read_only_seven_slots_preserved':True,'malformed_profile_no_target_change':True,
        'profile_rejections':['CRC mismatch','coefficient above 16384 with valid CRC','nonmonotonic tone with valid CRC'],
        'stock_iq_udd_sha256_verified':True,'mfw_libmisc_overlay_sha256_verified':True,
        'fixture_threads_preserved':2,'tracer_detached':True,'ipcs_ram_unchanged':True,
        'tone_status':'void API; forwarded/submitted only, hardware acceptance unavailable',
        'color_gamma_frame_atomic':False,'pixel_processing':False,'isp_dma_tested':False,
        'busy_concurrency_tested':False,'camera_or_card_accessed':False,'complete_camera_emulation':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--linux',choices=('start','exec','stop'));parser.add_argument('payload',nargs='?',default='')
    parser.add_argument('--profile',type=Path,default=OUT.parent/'profiles/nx300-fuji-classic-chrome/profile.bin')
    parser.add_argument('--legacy-profile',type=Path)
    parser.add_argument('--prepare-only',action='store_true');parser.add_argument('--build-fixture-only',action='store_true')
    args=parser.parse_args();configure()
    if args.linux: print(json.dumps(vm.linux_worker(args.linux,args.payload)));return
    if args.build_fixture_only: print(build_fixture());return
    if args.legacy_profile is None:parser.error('--legacy-profile is required for the v1 compatibility check')
    prepare(args.profile,args.legacy_profile)
    if args.prepare_only:return
    print(json.dumps(call('start')),flush=True);report={'passed':False}
    try:
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            if 'IPCM_PREVIEW_PRIVATE_VM_READY' in (OUT/'serial.log').read_text(errors='replace'):break
            time.sleep(.5)
        else:raise TimeoutError('Private IPCM VM boot timed out')
        execution=call('exec','/bin/sh /arm-vm/mfw-probe.sh');arm_vm.write_json(OUT/'execution.json',execution)
        guest=collect()
        if execution['exit_code'] or 'IPCM_PREVIEW_FULL_ARM_PASSED' not in execution['output']:
            raise RuntimeError('IPCM ARM probe failed; see execution.json and guest evidence')
        report=verify(guest)
    except Exception as exc:report['error']=str(exc);raise
    finally:
        arm_vm.write_json(OUT/'shutdown.json',call('stop'));report['private_vm_stopped']=True
        arm_vm.write_json(OUT/'report.json',report)
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
