"""Real bind-remount regression in an isolated stock-Ecore source fixture."""
import json
import shlex
import subprocess
from build_service_loader import ROOT, OUT, wsl, compile_arm, stage
from probe_persistent_hooks import CRT

FIXTURE = r'''
#define _GNU_SOURCE
#define _LARGEFILE64_SOURCE
#include <stdio.h>
static int unavailable;
static FILE *probe_fopen(const char *,const char *);
#define fopen probe_fopen
#define ecore_main_loop_begin fixture_hook_begin
#include "SOURCE"
#undef ecore_main_loop_begin
#undef fopen
static FILE *probe_fopen(const char *path,const char *mode) {
 if(unavailable && !strcmp(path,"/proc/self/mountinfo")) { errno=EIO; return NULL; }
 return fopen(path,mode);
}
extern int ecore_init(void);
extern int ecore_shutdown(void);
extern double ecore_time_get(void);
extern void *ecore_timer_add(double,int (*)(void *),void *);
extern void ecore_main_loop_begin(void);
extern void ecore_main_loop_quit(void);
static double started,changed;
static int phase,failed,cycle,seen_pending;
static unsigned int epoch;
static unsigned long mount_id;
static pid_t old_child;
static void write_file(const char *path,const char *text) {
 FILE *file=fopen(path,"w");
 if(!file || fputs(text,file)<0 || fclose(file)) { failed=90; ecore_main_loop_quit(); }
}
static int check(int ok,const char *name) {
 printf("{\"event\":\"check\",\"name\":\"%s\",\"passed\":%s,\"phase\":%d,\"time\":%.3f,\"epoch\":%u,\"attempts\":%u,\"mount_id\":%lu}\n",
  name,ok?"true":"false",phase,ecore_time_get()-started,loader.epoch,loader.attempts,loader.current.mount_id);
 fflush(stdout); if(!ok) { failed=91; ecore_main_loop_quit(); } return ok;
}
static int count(void) {
 char text[32]; int n=0; FILE *file=fopen("/tmp/count","r");
 if(file) {
  if(!fgets(text,sizeof text,file)) n=-1;
  else for(char *p=text; *p>='0' && *p<='9'; p++) n=n*10+(*p-'0');
  fclose(file);
 }
 return n;
}
static int invocations(void) {
 char text[32]; int n=0; FILE *file=fopen("/tmp/invocations","r");
 if(file) {
  if(!fgets(text,sizeof text,file)) n=-1;
  else for(char *p=text; *p>='0' && *p<='9'; p++) n=n*10+(*p-'0');
  fclose(file);
 }
 return n;
}
static int parse(const char *text,const char *root,unsigned long expected,int known) {
 write_file("/tmp/mountinfo-fixture",text);
 FILE *file=fopen("/tmp/mountinfo-fixture","r"); unsigned long id=0;
 int valid=mount_identity(file,root,&id); fclose(file);
 return valid==known && (!valid || id==expected);
}
static int parsers(void) {
 return check(parse("1 2 0:1 / / rw - tmpfs tmpfs rw\n"
   "3 1 0:2 / /mod-other rw - tmpfs tmpfs rw\n"
   "4 1 0:3 / /mod rw shared:4 - tmpfs tmpfs rw\n"
   "5 4 0:4 / /mod/deeper rw - tmpfs tmpfs rw\n","/mod/deeper/child",5,1),"longest_boundary_prefix") &&
  check(parse("1 2 0:1 / / rw - tmpfs tmpfs rw\n"
   "3 1 0:2 / /mod-other rw - tmpfs tmpfs rw\n","/mod",1,1),"sibling_not_prefix") &&
  check(parse("4 1 0:3 / /space\\040tab\\011line\\012back\\134 rw - tmpfs tmpfs rw\n",
   "/space tab\tline\nback\\/child",4,1),"mountinfo_escape_decode") &&
  check(parse("4 1 0:3 / /mod\\04 rw - tmpfs tmpfs rw\n","/mod",0,0) &&
   parse("4 1 0:3 / /mod rw - tmpfs tmpfs","/mod",0,0) &&
   parse("4294967296 1 0:3 / /mod rw - tmpfs tmpfs rw\n","/mod",0,0) &&
   parse("4 1 broken:3 / /mod rw - tmpfs tmpfs rw\n","/mod",0,0),"malformed_partial_overflow_rejected") &&
  check(parse("4 1 0:3 / /mod rw - tmpfs tmpfs rw\n"
   "5 4 0:4 / /mod rw - tmpfs tmpfs rw\n","/mod",0,0),"stacked_same_path_unknown");
}
static int mount_operation(const char *kind) {
 struct resource_id before,after;
 if(!resource(&before) || !before.mount_known) return check(0,"before_real_mount_known");
 unlink("/tmp/remount.ack"); write_file("/tmp/remount.request",kind);
 double deadline=ecore_time_get()+3;
 /* Fixture only: hold its event loop so host remount is between observations. */
 while(access("/tmp/remount.ack",R_OK) && ecore_time_get()<deadline) usleep(1000);
 if(!check(access("/tmp/remount.ack",R_OK)==0,"host_remount_ack")) return 0;
 int present=resource(&after);
 printf("{\"event\":\"remount_identity\",\"before\":%lu,\"after\":%lu,\"known\":%d,\"present\":%d,\"same_root_inode\":%s,\"same_script_inode\":%s}\n",
  before.mount_id,after.mount_id,after.mount_known,present,
  before.root_dev==after.root_dev && before.root_ino==after.root_ino?"true":"false",
  before.script_dev==after.script_dev && before.script_ino==after.script_ino?"true":"false");
 fflush(stdout);
 if(!check(present && before.root_dev==after.root_dev && before.root_ino==after.root_ino &&
    before.script_dev==after.script_dev && before.script_ino==after.script_ino && after.mount_known &&
    (!strcmp(kind,"unrelated") ? before.mount_id==after.mount_id : 1),
    "real_mount_operation_same_resource_inode")) return 0;
 if(strcmp(kind,"unrelated")) cycle++;
 return 1;
}
static int drive(void *unused) {
 (void)unused; double now=ecore_time_get();
 if(now-started>22) { check(0,"fixture_deadline"); return 0; }
 if(phase==0 && loader.succeeded && !loader.child) {
  if(!check(count()==1 && !loader.current.mount_known,"unavailable_mountinfo_stat_fallback")) return 0;
  epoch=loader.epoch; unavailable=0; changed=now; phase=1;
 } else if(phase==1 && now-changed>1.1) {
  if(!check(count()==1 && loader.succeeded && loader.current.mount_known && loader.epoch==epoch,
    "first_mountinfo_read_enriches_without_restart")) return 0;
  mount_id=loader.current.mount_id; unlink("/tmp/service-running");
  /* Fixture invalidates only the watch fd: reopen must not create an epoch. */
  close(loader.mount_watch);
  write_file("/tmp/manual-stop","stop\n"); changed=now; phase=2;
 } else if(phase==2 && now-changed>1.1) {
  if(!check(count()==1 && !loader.child && loader.succeeded && loader.epoch==epoch &&
    loader.mount_watch>=0 && access("/tmp/service-running",F_OK),
    "watch_reopen_and_manual_stop_same_mount_not_restarted")) return 0;
  if(!mount_operation("unrelated")) return 0;
  changed=now; phase=3;
 } else if(phase==3 && now-changed>1.1) {
  if(!check(count()==1 && invocations()==2 && loader.succeeded && loader.epoch==epoch+1 &&
    loader.current.mount_id==mount_id && access("/tmp/service-running",F_OK),
    "namespace_event_reconciles_but_manual_stop_blocks_service")) return 0;
  epoch=loader.epoch; unlink("/tmp/manual-stop"); unavailable=1; changed=now; phase=4;
 } else if(phase==4 && now-changed>1.1) {
  if(!check(count()==1 && loader.epoch==epoch && loader.current.mount_known && loader.current.mount_id==mount_id,
    "transient_read_failure_preserves_epoch_and_identity")) return 0;
  unavailable=0; changed=now; phase=5;
 } else if(phase==5 && now-changed>0.6) {
  if(!check(count()==1 && loader.epoch==epoch,"read_recovery_not_restart") || !mount_operation("remount")) return 0;
  phase=6;
 } else if(phase==6 && count()==2 && loader.child) {
  if(!check(loader.epoch==epoch+1 && loader.attempts==1 && loader.mount_changes>=2,
    "fast_remount_restarts_once_without_absent_tick")) return 0;
  epoch=loader.epoch; old_child=loader.child; if(!mount_operation("lazy-remount")) return 0; phase=7;
 } else if(phase==7) {
  if(loader.epoch==epoch+1 && loader.child==old_child) seen_pending=1;
  if(count()==3 && loader.succeeded && !loader.child) {
   if(!check(seen_pending && loader.epoch==epoch+1 && loader.attempts==1 && access("/tmp/overlap",F_OK),
     "remount_while_child_active_serialized")) return 0;
   changed=now; phase=8;
  }
 } else if(phase==8 && now-changed>1.1) {
  if(!check(count()==3 && invocations()==4 && loader.succeeded && cycle==2,"success_once_per_mount_event")) return 0;
  ecore_main_loop_quit(); return 0;
 }
 return 1;
}
int main(void) {
 if(ecore_init()<=0) return 92;
 started=ecore_time_get();
 if(!parsers() || !check(!camera_process(),"honest_fixture_guard_rejected")) return 93;
 char resolved[4096]; strcpy(loader.root,"/mod-link");
 unsigned long linked=0,direct=0;
 if(!check(realpath(loader.root,resolved) && !strcmp(resolved,"/mod") && resource_mount(&linked),
   "symlink_resolves_real_mount")) return 94;
 strcpy(loader.root,"/mod");
 if(!check(resource_mount(&direct) && direct==linked,"symlink_and_direct_mount_match")) return 95;
 unavailable=1; register_timer(); ecore_timer_add(.05,drive,NULL); ecore_main_loop_begin();
 int shutdown=ecore_shutdown();
 printf("{\"event\":\"finished\",\"failed\":%d,\"phase\":%d,\"shutdown\":%d}\n",failed,phase,shutdown);
 return failed || phase!=8;
}
'''


def main():
    label = 'remount'
    stage()
    compile_arm(ROOT / 'runtime/service_loader.c', OUT / 'service-loader.so', shared=True)
    source = OUT / 'service-remount-fixture.c'
    source.write_text(FIXTURE.replace('"SOURCE"', '"' + wsl(ROOT / 'runtime/service_loader.c') + '"'), encoding='utf-8', newline='\n')
    (OUT / 'crt0.S').write_text(CRT, encoding='utf-8', newline='\n')
    compile_arm(source, OUT / 'service-remount-fixture.arm', extra=(OUT / 'crt0.S',))
    root, evidence = OUT / (label+'-root'), OUT / (label+'-evidence')
    root.mkdir(exist_ok=True)
    evidence.mkdir(exist_ok=True)
    script = f'''set -eu
mount --make-rprivate /
root={shlex.quote(wsl(root))}
mount -t tmpfs -o mode=0755,nosuid,nodev tmpfs "$root"
cp -a {shlex.quote(wsl(OUT / 'stock-dependencies'))}/. "$root"/
mkdir -p "$root/proc" "$root/dev" "$root/tmp" "$root/var/run" "$root/card" "$root/mod"
chmod 777 "$root/tmp" "$root/var/run"
cp {shlex.quote(wsl(OUT / 'service-remount-fixture.arm'))} "$root/"
cp {shlex.quote(wsl(OUT / 'service-loader.so'))} "$root/"
cp {shlex.quote(wsl(ROOT / 'work/qemu/qemu-arm-static'))} "$root/qemu-arm-static"
cat > "$root/bin/sh" <<'BRIDGE'
#!/qemu-arm-static /bin/arm/sh
exec /qemu-arm-static /bin/arm/sh "$@"
BRIDGE
cat > "$root/bin/sleep" <<'BRIDGE'
#!/qemu-arm-static /bin/arm/sh
exec /qemu-arm-static /bin/arm/busybox sleep "$@"
BRIDGE
ln -s busybox "$root/bin/arm/sh"
chmod 755 "$root"/service-remount-fixture.arm "$root/qemu-arm-static" "$root/bin/sh" "$root/bin/sleep" "$root/bin/arm/busybox"
mount -t proc proc "$root/proc"
touch "$root/dev/null" "$root/dev/urandom"
mount --bind /dev/null "$root/dev/null"
mount --bind /dev/urandom "$root/dev/urandom"
cat > "$root/card/start.sh" <<'START'
#!/bin/sh
[ "$NX300M_AUTO_START" = 1 ] || exit 87
invocations=0; [ ! -r /tmp/invocations ] || read invocations < /tmp/invocations
invocations=$((invocations+1)); printf '%s\n' "$invocations" > /tmp/invocations
[ ! -e /tmp/manual-stop ] || exit 0
if [ -d /tmp/active ]; then printf overlap > /tmp/overlap; exit 88; fi
/qemu-arm-static /bin/arm/busybox mkdir /tmp/active || exit 89
count=0; [ ! -r /tmp/count ] || read count < /tmp/count
count=$((count+1)); printf '%s\n' "$count" > /tmp/count
printf '%s\n' "$count" >> /tmp/script-events
printf running > /tmp/service-running
/bin/sleep 1
/qemu-arm-static /bin/arm/busybox rmdir /tmp/active
START
ln -s /mod "$root/mod-link"
mount --bind "$root/card" "$root/mod"
set +e
timeout 27 chroot --userspec=65534:65534 "$root" /qemu-arm-static -cpu cortex-a9 -L / \\
 -E NX300M_MOD_ROOT=/mod -E LD_PRELOAD=/service-loader.so /service-remount-fixture.arm &
fixture=$!
while kill -0 "$fixture" 2>/dev/null; do
 if [ -f "$root/tmp/remount.request" ]; then
  operation=$(cat "$root/tmp/remount.request")
  rm "$root/tmp/remount.request"
  before=$(date +%s%N)
  if [ "$operation" = unrelated ]; then
   mkdir "$root/unrelated"
   mount --bind "$root/card" "$root/unrelated"
   action=$?
  else
   if [ "$operation" = lazy-remount ]; then umount -l "$root/mod"; else umount "$root/mod"; fi
   action=$?
   if [ "$action" -eq 0 ]; then mount --bind "$root/card" "$root/mod"; action=$?; fi
  fi
  after=$(date +%s%N)
  printf '%s %s %s %s\n' "$before" "$after" "$action" "$operation" >> "$root/tmp/remount-times"
  if [ "$action" -eq 0 ]; then
   printf 'done\n' > "$root/tmp/remount.ack.tmp"
   mv "$root/tmp/remount.ack.tmp" "$root/tmp/remount.ack"
  fi
 fi
 sleep .005
done
wait "$fixture"
status=$?
set -e
for name in count invocations script-events remount-times; do
 if [ -f "$root/tmp/$name" ]; then cp "$root/tmp/$name" {shlex.quote(wsl(evidence))}/"$name"; fi
done
if [ -f "$root/var/run/nx300m-service-loader-status.json" ]; then
 cp "$root/var/run/nx300m-service-loader-status.json" {shlex.quote(wsl(evidence))}/status.json
fi
exit "$status"
'''
    (OUT / (label+'-host.sh')).write_text(script, encoding='utf-8', newline='\n')
    result = subprocess.run(['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'unshare', '--mount', '--net', '--ipc', '--pid', '--fork', '--mount-proc', 'sh', '-s'], input=script.encode(), capture_output=True, timeout=35)
    (OUT / (label+'.stdout.log')).write_bytes(result.stdout)
    (OUT / (label+'.stderr.log')).write_bytes(result.stderr)
    events = [json.loads(line) for line in result.stdout.decode('utf-8', 'replace').splitlines() if line.startswith('{"event":')]
    timing = []
    if (evidence / 'remount-times').is_file():
        for line in (evidence / 'remount-times').read_text().splitlines():
            before, after, code, operation = line.split()
            timing.append({'operation': operation, 'duration_ms': round((int(after)-int(before))/1000000, 3), 'exit_code': int(code)})
    passed = result.returncode == 0 and len(timing) == 3 and all(t['exit_code'] == 0 and t['duration_ms'] < 500 for t in timing)
    report = {'exit_code': result.returncode, 'passed': passed, 'events': events, 'real_bind_remounts': timing,
              'fixture_occupies_released_mount_id': False,
              'scope': 'source-inclusion fixture directly registers stock Ecore timer; real mountinfo and bind mounts; mountinfo EIO and parser inputs are fixture injections',
              'fixture_camera_identity_spoofed': False, 'camera_app_executed': False,
              'physical_sleep_or_sd_verified': False, 'main_namespace_changed': False,
              'isolation': 'fresh mount/net/IPC/PID namespace, private tmpfs chroot, uid65534; host controller alone performs bind mounts'}
    (OUT / (label+'-report.json')).write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if result.stderr:
        print(result.stderr.decode('utf-8', 'replace'))
    raise SystemExit(0 if passed else result.returncode or 1)


if __name__ == '__main__':
    main()
