"""Run the unchanged manual exposure launcher in a private ARM Linux VM.

The original camera ELF, stock shell and read-only module are real. An explicit
test execve bridge adds existing synthetic hardware libraries only when the shell
execs the original ELF. The launcher still selects its unique RAM module.
"""
import argparse
import base64
import hashlib
import io
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tarfile
import time
import arm_vm
import probe_mfw_ram_vm as vm

PROJECT=Path(__file__).resolve().parents[1]
OUT=PROJECT/'outputs/deployment/mfw-exposure-test/launcher-vm'
SOCKET='/tmp/nx300m-exposure-launch-private'
BRIDGE=r'''
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
int execve(const char *path,char *const argv[],char *const envp[]) {
 int (*original)(const char *,char *const [],char *const [])=dlsym(RTLD_NEXT,"execve");
 if(!original){errno=ENOSYS;return -1;}
 if(strcmp(path,"/usr/bin/di-camera-app-nx300"))return original(path,argv,envp);
 size_t count=0;const char *preload=NULL;
 FILE *log=fopen("/arm-vm/mfw-results/exec-bridge-original.env","a");
 if(!log){errno=EACCES;return -1;}
 fprintf(log,"EXEC_APP pid=%ld\n",(long)getpid());
 for(;envp[count];count++) {
  fprintf(log,"%s\n",envp[count]);
  if(!strncmp(envp[count],"LD_PRELOAD=",11))preload=envp[count]+11;
  if(!strncmp(envp[count],"HIB=",4) || !strncmp(envp[count],"LD_AUDIT=",9) ||
     !strncmp(envp[count],"LD_LIBRARY_PATH=",16)){fclose(log);errno=EINVAL;return -1;}
 }
 fclose(log);
 const char *prefix="/tmp/nx300m-exposure.";const char *suffix="/exposure_status.so";
 if(!preload || strncmp(preload,prefix,strlen(prefix)) || strchr(preload,':') ||
    strlen(preload)<strlen(suffix) || strcmp(preload+strlen(preload)-strlen(suffix),suffix)) {
  errno=EINVAL;return -1;
 }
 const char *shims="LD_PRELOAD=/arm-vm/mfw-source/lab/gpio_preview.so:/arm-vm/mfw-source/lab/device_preview.so:/arm-vm/mfw-source/lab/capture_boot.so:";
 char *patched=malloc(strlen(shims)+strlen(preload)+1);
 char **environment=calloc(count+1,sizeof *environment);
 if(!patched || !environment){free(patched);free(environment);errno=ENOMEM;return -1;}
 strcpy(patched,shims);strcat(patched,preload);
 for(size_t i=0;i<count;i++)environment[i]=strncmp(envp[i],"LD_PRELOAD=",11)?envp[i]:patched;
 int result=original(path,argv,environment),error=errno;
 free(patched);free(environment);errno=error;return result;
}
'''
PROBE=r'''#!/bin/sh
set -eu
OUT=/arm-vm/mfw-results
SOURCE=/mnt/mmc/nx300m-exposure-test
APP=/usr/bin/di-camera-app-nx300
READY=/var/run/nx300m-exposure-ready
READONLY=/var/run/nx300m-exposure-readonly
finish() {
    result=$?
    trap - EXIT
    for dir in /tmp/nx300m-exposure.*; do
        [ -d "$dir" ] || continue
        name=$(basename "$dir")
        mkdir -p "$OUT/runtime/$name"
        cp "$dir"/*.log "$OUT/runtime/$name/" 2>/dev/null || :
    done
    printf '%s\n' "$result" > "$OUT/probe.exit"
    exit "$result"
}
trap finish EXIT
echo EXPOSURE_LAUNCH_ARM_BEGIN
grep ' /mnt/mmc .* ro,' /proc/mounts > "$OUT/source-mount.txt"
/rescue/busybox sha256sum "$APP" > "$OUT/original-app.sha256"
/bin/sh "$SOURCE/exposure-launch.sh" inspect > "$OUT/inspect-before.log"
grep -q '^camera_pids=none$' "$OUT/inspect-before.log"
grep -q '^module_present=yes$' "$OUT/inspect-before.log"
[ ! -e /tmp/nx300m-exposure-launch.lock ]
attempt=0
while [ ! -S /tmp/.X11-unix/X97 ]; do
    attempt=$((attempt+1)); [ "$attempt" -lt 20 ]; sleep 1
done
umask 000
/usr/bin/prefman init > "$OUT/pref-init.log" 2>&1
/usr/bin/prefman reset > "$OUT/pref-reset.log" 2>&1
! grep -qi failed "$OUT/pref-reset.log"
/usr/bin/prefman set 1 0x300 l 1 > "$OUT/pref-display.log" 2>&1
/usr/bin/prefman set 1 0x304 l 1 > "$OUT/pref-power.log" 2>&1
umask 077
# Old markers cannot establish the new app's readiness.
printf '1\n' > "$READY"
printf '1\n' > "$READONLY"
stat -c '%d:%i' "$READONLY" > "$OUT/readonly-old.inode"
export DISPLAY=:97 NX300M_SHUTTER_SIM=1 NX300M_SHUTTER_PARK=10 NX300M_ISP_SIM=1
export NX300M_SPI_MMIO=1 NX300M_SPI_PEER=aquila-registers NX300M_D4C_REGISTERS=1 NX300M_HOTSHOE_ABSENT=1
# Test bridge is mapped by the shell, and absent from the camera preload.
HIB=fixture-must-be-cleared LD_LIBRARY_PATH=/fixture-invalid-search \
LD_PRELOAD=/mnt/mmc/lab/launcher-exec-bridge.so \
    /bin/sh "$SOURCE/exposure-launch.sh" launch > "$OUT/launch.stdout" 2> "$OUT/launch.stderr" &
monitor=$!
printf '%s\n' "$monitor" > "$OUT/monitor.pid"
attempt=0
while [ ! -e /proc/$monitor/maps ]; do
    attempt=$((attempt+1)); [ "$attempt" -lt 10 ]; sleep 1
done
cat /proc/$monitor/maps > "$OUT/monitor.maps"
wait "$monitor"
grep -q '^read_only_ready=yes camera_pid=' "$OUT/launch.stdout"
grep -q '^launcher_exit=0 (not readiness)$' "$OUT/launch.stdout"
camera_pid=$(cat "$READY")
[ "$camera_pid" = "$(cat "$READONLY")" ]
[ "$camera_pid" != "$monitor" ]
[ "$(readlink /proc/$camera_pid/exe)" = "$APP" ]
[ "$(stat -L -c '%d:%i' /proc/$camera_pid/exe)" = "$(stat -c '%d:%i' "$APP")" ]
[ "$(stat -c '%d:%i' "$READONLY")" != "$(cat "$OUT/readonly-old.inode")" ]
runtime=$(sed -n 's/^temporary_runtime=//p' "$OUT/launch.stdout")
[ -n "$runtime" ]
cmp "$SOURCE/lib/exposure_status.so" "$runtime/exposure_status.so"
grep -q '^LD_PRELOAD='"$runtime/exposure_status.so"'$' "$OUT/exec-bridge-original.env"
! grep -Eq '^(HIB|LD_AUDIT|LD_LIBRARY_PATH)=' "$OUT/exec-bridge-original.env"
! grep -q '/exposure_status.so' "$OUT/monitor.maps"
cat /proc/$camera_pid/maps > "$OUT/camera.maps"
cat /proc/$camera_pid/stat > "$OUT/camera.stat"
cat /proc/$camera_pid/status > "$OUT/camera.status"
ls -l /proc/$camera_pid/fd > "$OUT/camera.fd"
tr '\000' '\n' < /proc/$camera_pid/environ > "$OUT/camera.environment"
! grep -q 'nx300m-exposure-launch.lock\|nx300m-exposure-readonly' "$OUT/camera.fd"
! grep -q 'launcher-exec-bridge.so' "$OUT/camera.maps"
count=0
for entry in /proc/[0-9]*/exe; do
    target=$(readlink "$entry" 2>/dev/null) || continue
    [ "$target" != "$APP" ] || count=$((count+1))
done
[ "$count" -eq 1 ]
printf '%s\n' "$count" > "$OUT/camera-count.txt"
printf '%s\n' "$camera_pid" > "$OUT/camera.pid"
# Existing real camera must be retained without a second launch.
/bin/sh "$SOURCE/exposure-launch.sh" inspect > "$OUT/inspect-existing.log"
grep -q '^launch_allowed=no (original app is running)$' "$OUT/inspect-existing.log"
set +e
/bin/sh "$SOURCE/exposure-launch.sh" launch > "$OUT/reject.stdout" 2> "$OUT/reject.stderr"
rejected=$?
set -e
[ "$rejected" -eq 1 ]
printf '%s\n' "$rejected" > "$OUT/reject.exit"
grep -q 'refusing a second camera instance; no signal was sent' "$OUT/reject.stderr"
[ "$(cat "$READY")" = "$camera_pid" ]
kill -0 "$camera_pid"
[ "$(grep -c '^EXEC_APP ' "$OUT/exec-bridge-original.env")" -eq 1 ]
# Only read the native object after stock initialization has settled.
sleep 12
rpc() {
    id=$1; operation=$2
    expires=$(($(date +%s)+8))
    printf '%s %s %s 3b 003b0011\n' "$id" "$expires" "$operation" > /var/run/nx300m-exposure-request.tmp
    mv /var/run/nx300m-exposure-request.tmp /var/run/nx300m-exposure-request
    attempt=0
    while :; do
        if [ -r /var/run/nx300m-exposure-response ]; then
            read response_id rest < /var/run/nx300m-exposure-response
            [ "$response_id" != "$id" ] || break
        fi
        attempt=$((attempt+1)); [ "$attempt" -lt 10 ]; sleep 1
    done
    cp /var/run/nx300m-exposure-response "$OUT/response-$id.txt"
}
rpc 101 0
rpc 102 1
rpc 103 0
awk 'NF==15 && $2==0 && $5==0 && $6==0 {ok=1} END {exit !ok}' "$OUT/response-101.txt"
awk 'NF==15 && $2==-120 && $5==0 && $6==0 {ok=1} END {exit !ok}' "$OUT/response-102.txt"
awk 'NF==15 && $2==0 && $5==0 && $6==0 {ok=1} END {exit !ok}' "$OUT/response-103.txt"
[ "$(awk '{print $4}' "$OUT/response-101.txt")" = "$(awk '{print $4}' "$OUT/response-103.txt")" ]
[ "$(awk '{print $13}' "$OUT/response-101.txt")" = "$(awk '{print $13}' "$OUT/response-103.txt")" ]
cat /proc/$camera_pid/stat > "$OUT/camera-after.stat"
echo EXPOSURE_LAUNCH_ARM_PASSED
'''

def configure():
    vm.OUT=OUT;vm.SOCKET=SOCKET;vm.PROBE=PROBE
    vm.INIT=vm.INIT.replace('echo MFW_PRIVATE_VM_SHELL_READY',r'''
/rescue/busybox mkdir -p /dev/shm /pref /adj /var/run
/rescue/busybox chmod 1777 /dev/shm
/rescue/busybox chmod 777 /pref /adj /var/run
/rescue/busybox mount -t tmpfs -o mode=1777,nosuid,nodev tmpfs /dev/shm
/rescue/busybox touch /adj/AdjLog.txt
/rescue/busybox chmod 666 /adj/AdjLog.txt
echo MFW_PRIVATE_VM_SHELL_READY''')

def prepare():
    OUT.mkdir(parents=True,exist_ok=True)
    stage=OUT/'source-files'
    if stage.exists():
        if stage.is_symlink() or stage.resolve().parent!=OUT.resolve():raise ValueError('Unexpected private staging path')
        shutil.rmtree(stage)
    source=stage/'nx300m-exposure-test';(source/'lib').mkdir(parents=True)
    script=PROJECT/'mod/deployment/mfw/exposure-launch.sh'
    module=PROJECT/'outputs/controls/exposure_status.so'
    (source/'exposure-launch.sh').write_bytes(script.read_bytes().replace(b'\r\n',b'\n'))
    (source/'lib/exposure_status.so').write_bytes(module.read_bytes())
    lab=stage/'lab';lab.mkdir()
    for name in ('gpio_preview','device_preview','capture_boot'):
        shutil.copyfile(PROJECT/f'outputs/firmware-runtime/{name}.so',lab/f'{name}.so')
    fixture=OUT/'launcher-exec-bridge.c';fixture.write_text(BRIDGE,encoding='utf-8',newline='\n')
    build=f'''set -eu
tc=/opt/nx300m-toolchain/usr
rf=/opt/nx300m-lab/stock-1.15
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" -std=gnu99 -O2 -Wall -Wextra -Werror \\
 -march=armv7-a -mfloat-abi=soft -fno-stack-protector -D_TIME_BITS=32 -D_FILE_OFFSET_BITS=64 \\
 -nostdlib -shared -fPIC -Wl,-z,defs -Wl,-rpath-link,"$rf/lib" \\
 {shlex.quote(arm_vm.wsl_path(fixture))} "$rf/lib/libc-2.13.so" "$rf/lib/libdl.so.2" -lgcc \\
 -o {shlex.quote(arm_vm.wsl_path(lab/'launcher-exec-bridge.so'))}
'''
    result=subprocess.run(['wsl.exe','-d','Debian','-u','root','-e','sh','-s'],input=build.encode(),capture_output=True)
    (OUT/'fixture-build.stdout.log').write_bytes(result.stdout);(OUT/'fixture-build.stderr.log').write_bytes(result.stderr)
    if result.returncode:raise RuntimeError(f'Fixture build exit {result.returncode}: {result.stderr.decode(errors="replace")}')
    with tarfile.open(PROJECT/'work/stock-1.15/rootfs.tar') as stock:
        app=stock.extractfile('usr/bin/di-camera-app-nx300').read()
    arm_vm.write_json(OUT/'candidate.json',{'launcher_source':str(script),'launcher_bytes':script.stat().st_size,
        'module_bytes':module.stat().st_size,'original_app_sha256':hashlib.sha256(app).hexdigest(),
        'test_execve_bridge':True,'hardware_shims':['gpio_preview','device_preview','capture_boot'],
        'launcher_script_replaced':False,'camera_app_replaced':False,'ready_or_process_identity_stubbed':False})
    vm.initramfs()

def call(action,command=''):
    result=subprocess.run(['wsl.exe','-d','Debian','-u','root','-e','python3',arm_vm.wsl_path(__file__),
        '--linux',action,base64.b64encode(command.encode()).decode()],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=195)
    if result.returncode:raise RuntimeError(result.stderr or result.stdout)
    return json.loads(result.stdout)

def run():
    (OUT/'report.json').unlink(missing_ok=True);call('start')
    try:
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            if 'MFW_PRIVATE_VM_SHELL_READY' in (OUT/'serial.log').read_text(errors='replace'):break
            time.sleep(1)
        else:raise RuntimeError('Private ARM boot timed out')
        result=call('exec','/bin/sh /arm-vm/mfw-probe.sh');arm_vm.write_json(OUT/'execution.json',result)
        collected=call('exec','/rescue/busybox tar -czf /tmp/exposure-launch-evidence.tar.gz -C /arm-vm mfw-results; /rescue/busybox base64 /tmp/exposure-launch-evidence.tar.gz')
        encoded=''.join(line.strip() for line in collected['output'].splitlines() if re.fullmatch('[A-Za-z0-9+/=]+',line.strip()))
        raw=base64.b64decode(encoded);(OUT/'guest-evidence.tar.gz').write_bytes(raw)
        with tarfile.open(fileobj=io.BytesIO(raw)) as package:package.extractall(OUT/'guest',filter='data')
        if result['exit_code'] or 'EXPOSURE_LAUNCH_ARM_PASSED' not in result['output']:raise RuntimeError('Launcher ARM check failed; see execution.json and guest evidence')
        guest=OUT/'guest/mfw-results';candidate=json.loads((OUT/'candidate.json').read_text())
        if (guest/'original-app.sha256').read_text().split()[0]!=candidate['original_app_sha256']:raise ValueError('Original camera ELF differs')
        def stat_identity(path):
            text=path.read_text();fields=text[text.rfind(')')+2:].split();return fields[19]
        if stat_identity(guest/'camera.stat')!=stat_identity(guest/'camera-after.stat'):raise ValueError('Camera identity changed during rejection/read checks')
        report={'passed':True,'scope':'real manual launcher, genuine stock ARM app, explicit test execve bridge and synthetic hardware libraries',
            'native_arm_kernel':True,'original_app_bytes_verified':True,'unchanged_launcher_script':True,
            'inspect_without_start':True,'daemon_pid_independently_discovered':True,'unique_camera_pid':True,
            'fresh_readonly_marker_required':True,'ram_module_matches_source':True,'camera_exe_inode_starttime_verified':True,
            'inherited_hib_and_library_search_cleared':True,'monitor_does_not_map_readonly_module':True,
            'launch_lock_not_inherited_by_camera':True,'existing_app_inspect_and_launch_rejection':True,
            'existing_camera_identity_preserved':True,'readonly_write_rejected_and_preference_unchanged':True,
            'test_execve_bridge':True,'hardware_shims':candidate['hardware_shims'],'ready_or_process_identity_stubbed':False,
            'physical_camera_verified':False,'hardware_restart_verified':False,'main_simulator_modified':False,
            'sd_card_accessed':False,'original_app_exit_or_restart_tested':False}
        arm_vm.write_json(OUT/'report.json',report);print(json.dumps(report,ensure_ascii=False,indent=2))
    finally:arm_vm.write_json(OUT/'shutdown.json',call('stop'))

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--linux',choices=['start','stop','exec'])
    parser.add_argument('payload',nargs='?',default='');parser.add_argument('--prepare-only',action='store_true')
    args=parser.parse_args();configure()
    if args.linux:print(json.dumps(vm.linux_worker(args.linux,args.payload)));return
    prepare()
    if not args.prepare_only:run()

if __name__=='__main__':main()

