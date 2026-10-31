"""Private ARM check: complete SD RAM entry and gallery media-release barrier.

Honest source-inclusion fixtures directly call internal helpers; no camera app
or guard spoof, no main/MFW VM, physical camera or snapshot operation.
"""
import argparse
import base64
import io
import json
from pathlib import Path
import re
import shlex
import subprocess
import tarfile
import time
import arm_vm
import probe_mfw_ram_vm as vm

PROJECT=Path(__file__).resolve().parents[1]
OUT=PROJECT/'outputs/deployment/service-loader/ram-service-vm'
SOCKET='/tmp/nx300m-full-service-private'
LOADER=r'''
#define ecore_main_loop_begin fixture_loader_hook
#include "SOURCE"
#undef ecore_main_loop_begin
int main(void) {
 const char *root=getenv("NX300M_MOD_ROOT");
 if(!root || strcmp(root,"/mnt/mmc/nx300m-mod"))return 80;
 memset(&loader,0,sizeof loader);loader.owner=getpid();loader.mount_watch=-1;
 snprintf(loader.root,sizeof loader.root,"%s",root);
 snprintf(loader.script,sizeof loader.script,"%s/ram-start.sh",root);
 snprintf(loader.root_env,sizeof loader.root_env,"NX300M_MOD_ROOT=%s",root);
 if(!resource(&loader.current))return 81;loader.present=1;
 int fd=open("/mnt/mmc/nx300m-mod/README.md",O_RDONLY);
 if(fd<0 || dup2(fd,47)!=47)return 82;close(fd);
 launch_child();if(!loader.child)return 83;
 int status;pid_t child;
 do {child=waitpid(loader.child,&status,0);}while(child<0 && errno==EINTR);
 close(47);
 if(strcmp(getenv("NX300M_MOD_ROOT"),"/mnt/mmc/nx300m-mod"))return 84;
 return child>0 && WIFEXITED(status)?WEXITSTATUS(status):85;
}
'''
GALLERY=r'''
#define ecore_main_loop_begin fixture_gallery_hook
#include "SOURCE"
#undef ecore_main_loop_begin
#include <sys/wait.h>
static int reads,fallbacks;
static int fake_index(void) {reads++;return 0;}
static const char *fake_filename(int index) {(void)index;reads++;return "private.jpg";}
static int fake_state(int state) {return state==0xce?0xa:2;}
static int fake_database(void) {reads++;return 0;}
static int fake_count(void) {reads++;return 1;}
static void fallback(void) {fallbacks++;}
int main(int argc,char **argv) {
 char root[PATH_MAX],state[PATH_MAX];
 if(argc!=2 || !service_location(root,state))return 90;
 if(strcmp(getenv("NX300M_MOD_ROOT"),"/mnt/mmc/nx300m-mod"))return 91;
 int fd=gallery_media_lock(state);
 if(!strcmp(argv[1],"paused")) {
  if(fd>=0)return 92;
  current_index=fake_index;filename=fake_filename;get_state=fake_state;
  database_mode=fake_database;selected_count=fake_count;stock_multi=fallback;
  launch(fallback);upload_multi();if(reads || fallbacks)return 93;
  puts("paused_hook_no_asl_or_fallback");return 0;
 }
 if(fd<0)return 94;
 if(!strcmp(argv[1],"hold")) {
  FILE *f=fopen("/arm-vm/mfw-results/gallery-lock-held","w");
  if(!f)return 95;fputs("held\n",f);fclose(f);sleep(4);close(fd);return 0;
 }
 if(strcmp(argv[1],"active"))return 96;
 int sentinel=open("/mnt/mmc/nx300m-mod/README.md",O_RDONLY);
 if(sentinel<0 || dup2(sentinel,47)!=47)return 97;close(sentinel);
 char *args[]={"/bin/sh","-c",
  "[ \"$NX300M_MOD_ROOT\" = /tmp/nx300m-mfw/runtime ] && "
  "[ \"$NX_MOD_ROOT\" = /tmp/nx300m-mfw/runtime ] && "
  "[ \"$NX_STATE_DIR\" = /tmp/nx300m-mfw/state ] && "
  "[ \"$(pwd -P)\" = /tmp/nx300m-mfw/runtime ] && "
  "[ ! -e /proc/self/fd/47 ] && [ -z \"${LD_PRELOAD-}\" ] && "
  "[ /proc/self/fd/0 -ef /dev/null ] && "
  "[ /proc/self/fd/1 -ef /dev/null ] && [ /proc/self/fd/2 -ef /dev/null ]",NULL};
 pid_t pid=spawn_panel(args,root,state);close(fd);close(47);
 if(pid<0)return 98;int status;
 if(waitpid(pid,&status,0)!=pid || !WIFEXITED(status) || WEXITSTATUS(status))return 99;
 if(strcmp(getenv("NX300M_MOD_ROOT"),"/mnt/mmc/nx300m-mod"))return 100;
 puts("active_ram_identity_child_environment_and_fd_isolation");return 0;
}
'''

PROBE=vm.PROBE.replace('/bin/sh /mnt/mmc/autoexec.sh','NX300M_MOD_ROOT="$SOURCE" /mnt/mmc/bin/service-loader-entry.arm')
PROBE=PROBE.replace('mfw-entry.log','service-entry.log').replace('MFW_FULL_ARM_','FULL_SERVICE_ARM_')
PROBE='\n'.join(line for line in PROBE.split('\n') if not any(x in line for x in ('diagnostics.json','bootstrap-http.log')))
PROBE=PROBE.replace('wget -T 5 -Y off -O "$OUT/media-before.json"',r'''NX300M_MOD_ROOT="$SOURCE" /mnt/mmc/bin/gallery-runtime-entry.arm active > "$OUT/gallery-active.log"
NX300M_MOD_ROOT="$SOURCE" /mnt/mmc/bin/gallery-runtime-entry.arm hold > "$OUT/gallery-hold.log" &
gallery_holder=$!
attempt=0
while [ ! -f "$OUT/gallery-lock-held" ]; do
    attempt=$((attempt+1)); [ "$attempt" -lt 10 ]; sleep 1
done
wget -T 5 -Y off -O "$OUT/media-before.json"''')
PROBE=PROBE.replace('wait_media_released "$OUT/media-paused.json"',r'''grep -q '"service_media_released":false' "$OUT/media-pause.json"
wait "$gallery_holder"
wait_media_released "$OUT/media-paused.json"
NX300M_MOD_ROOT="$SOURCE" /mnt/mmc/bin/gallery-runtime-entry.arm paused > "$OUT/gallery-paused.log"''',1)
PROBE=PROBE.replace('[ -f "$RAM/state/media-paused" ]\n/bin/sh "$RAM/runtime/stop.sh"',r'''[ -f "$RAM/state/media-paused" ]
NX300M_MOD_ROOT="$SOURCE" /mnt/mmc/bin/gallery-runtime-entry.arm paused > "$OUT/gallery-restarted-paused.log"
/bin/sh "$RAM/runtime/stop.sh"''')

def configure():
    vm.OUT=OUT;vm.SOCKET=SOCKET;vm.PROBE=PROBE

def prepare():
    OUT.mkdir(parents=True,exist_ok=True)
    stage=OUT/'source-files';stage.mkdir(exist_ok=True)
    bundle=PROJECT/'outputs/deployment/sd-bundle/nx300m-mod-sd.tar.gz'
    with tarfile.open(bundle) as package:package.extractall(stage,filter='data')
    (stage/'DCIM').mkdir(exist_ok=True);(stage/'bin').mkdir(exist_ok=True)
    for name,text,source in [('service-loader-entry',LOADER,'runtime/service_loader.c'),('gallery-runtime-entry',GALLERY,'runtime/gallery_share.c')]:
        (OUT/(name+'.c')).write_text(text.replace('SOURCE',arm_vm.wsl_path(PROJECT/source)))
    script=f'''set -eu
cd {shlex.quote(arm_vm.wsl_path(PROJECT))}
tc=/opt/nx300m-toolchain/usr
rf=/opt/nx300m-lab/stock-1.15
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
for name in service-loader-entry gallery-runtime-entry; do
 "$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=gnu99 -O2 -Wall -Wextra -Wno-unused-function -Wno-unused-parameter -Wno-misleading-indentation \\
 -march=armv7-a -mfloat-abi=soft -fno-stack-protector -D_GNU_SOURCE -D_TIME_BITS=32 -D_FILE_OFFSET_BITS=64 \\
 -include mod/metol/remote/compat.h -Ioutputs/quickshare/gallery -fno-pic -no-pie -nostdlib -nostartfiles \\
 -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 -Wl,-rpath-link,"$rf/usr/lib" -Wl,-rpath-link,"$rf/lib" \\
 {shlex.quote(arm_vm.wsl_path(OUT))}/$name.c work/reference/metol/test_server/filmlab/src/crt0_arm.S \\
 "$rf/usr/lib/libecore.so.1" "$rf/lib/libc-2.13.so" "$rf/lib/libdl.so.2" -lgcc \\
 -o {shlex.quote(arm_vm.wsl_path(stage/'bin'))}/$name.arm
done
'''
    result=subprocess.run(['wsl.exe','-d','Debian','-u','root','-e','sh','-s'],input=script.encode(),capture_output=True)
    (OUT/'fixture-build.stdout.log').write_bytes(result.stdout);(OUT/'fixture-build.stderr.log').write_bytes(result.stderr)
    if result.returncode:raise RuntimeError(f'Fixture build exit {result.returncode}: {result.stderr.decode(errors="replace")}')
    vm.initramfs()
    arm_vm.write_json(OUT/'candidate.json',{'archive':str(bundle),'archive_bytes':bundle.stat().st_size,
        'files':len((stage/'nx300m-mod/runtime-files.list').read_text().splitlines()),'camera_app_executed':False,'source_inclusion_fixtures':True})

def call(action,command=''):
    result=subprocess.run(['wsl.exe','-d','Debian','-u','root','-e','python3',arm_vm.wsl_path(__file__),
        '--linux',action,base64.b64encode(command.encode()).decode()],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=195)
    if result.returncode:raise RuntimeError(result.stderr or result.stdout)
    return json.loads(result.stdout)

def run():
    (OUT/'report.json').unlink(missing_ok=True);call('start')
    try:
        deadline=time.monotonic()+90
        while time.monotonic()<deadline:
            if 'MFW_PRIVATE_VM_SHELL_READY' in (OUT/'serial.log').read_text(errors='replace'):break
            time.sleep(1)
        else:raise RuntimeError('Private ARM boot timed out')
        result=call('exec','/bin/sh /arm-vm/mfw-probe.sh');arm_vm.write_json(OUT/'execution.json',result)
        collected=call('exec','/rescue/busybox tar -czf /tmp/full-service-evidence.tar.gz -C /arm-vm mfw-results; /rescue/busybox base64 /tmp/full-service-evidence.tar.gz')
        encoded=''.join(line.strip() for line in collected['output'].splitlines() if re.fullmatch('[A-Za-z0-9+/=]+',line.strip()))
        raw=base64.b64decode(encoded);(OUT/'guest-evidence.tar.gz').write_bytes(raw)
        with tarfile.open(fileobj=io.BytesIO(raw)) as package:package.extractall(OUT/'guest',filter='data')
        if result['exit_code'] or 'FULL_SERVICE_ARM_PASSED' not in result['output']:raise RuntimeError('Complete service ARM check failed; see execution.json and guest evidence')
        guest=OUT/'guest/mfw-results';expected=json.loads((OUT/'candidate.json').read_text())['files']
        if int((guest/'compared-files.txt').read_text())!=expected:raise ValueError('Incomplete runtime files')
        before=json.loads((guest/'media-before.json').read_text());paused=json.loads((guest/'media-paused.json').read_text());restarted=json.loads((guest/'media-restarted.json').read_text())
        if not before['available'] or before['paused']:raise ValueError('Fresh media state failed')
        if not all(x['paused'] and x['service_media_released'] for x in (paused,restarted)):raise ValueError('Pause/restart release failed')
        report={'exit_code':0,'runtime_files_compared':expected,'loader_real_service_argument':True,
            'resident_sd_references_absent':True,'duplicate_entry_same_pid':True,'manual_stop_preserved':True,
            'gallery_ram_identity_child_environment_and_fd_isolation':True,'gallery_lock_blocks_release':True,
            'paused_gallery_no_asl_or_fallback':True,'pause_survives_restart':True,'source_mount':'read-only bind',
            'media_before':before,'media_paused':paused,'media_restarted':restarted,
            'scope':'source-inclusion launch/gallery fixtures; production ARM service and stock libraries',
            'camera_app_executed':False,'physical_camera_verified':False,'main_simulator_changed':False}
        arm_vm.write_json(OUT/'report.json',report);print(json.dumps(report,ensure_ascii=False,indent=2))
    finally:call('stop')

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--linux',choices=['start','stop','exec'])
    parser.add_argument('payload',nargs='?',default='');parser.add_argument('--prepare-only',action='store_true')
    args=parser.parse_args();configure()
    if args.linux:print(json.dumps(vm.linux_worker(args.linux,args.payload)));return
    prepare()
    if not args.prepare_only:run()

if __name__=='__main__':main()
