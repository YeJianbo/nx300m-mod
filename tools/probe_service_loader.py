"""Separate camera-guard rejection from original-Ecore timer state tests."""
import argparse
import json
import shlex
import subprocess
from build_service_loader import ROOT, OUT, wsl, stage, compile_arm
from probe_persistent_hooks import CRT

FIXTURE = r'''
/* Honest fixture directly registers the internal timer, without camera identity. */
#define ecore_main_loop_begin fixture_hook_begin
#include "SOURCE"
#undef ecore_main_loop_begin
extern int ecore_init(void);
extern int ecore_shutdown(void);
extern double ecore_time_get(void);
extern void *ecore_timer_add(double, int (*)(void *), void *);
extern void ecore_main_loop_begin(void);
extern void ecore_main_loop_quit(void);
static double started, changed, exhausted;
static int phase, result, invalid_stage, last_attempts = -1;
static unsigned int last_epoch;
static const char *script =
 "#!/bin/sh\n"
 "[ \"$#\" -eq 1 ] && [ \"$1\" = --service ] || exit 80\n"
 "[ -z \"${LD_PRELOAD-}\" ] || exit 81\n"
 "[ \"$NX300M_MOD_ROOT\" = /mod ] || exit 82\n"
 "[ ! -e /proc/self/fd/47 ] || exit 83\n"
 "[ /proc/self/fd/0 -ef /dev/null ] && [ /proc/self/fd/1 -ef /dev/null ] && [ /proc/self/fd/2 -ef /dev/null ] || exit 84\n"
 "count=0; [ ! -r /tmp/count ] || read count < /tmp/count\n"
 "count=$((count+1)); printf '%s\n' \"$count\" > /tmp/count\n"
 "mode=; read mode < /tmp/mode\n"
 "printf '%s %s clean_preload clean_fd null_stdio\n' \"$count\" \"$mode\" >> /tmp/script-events\n"
 "printf 'THIS_MUST_GO_TO_DEV_NULL\n'\n"
 "[ \"$mode\" != fail ] || exit 7\n"
 "/bin/sleep 1\n"
 "exit 0\n";
static void write_file(const char *path, const char *text) {
 FILE *f = fopen(path, "w");
 if (!f || fputs(text, f) < 0 || fclose(f)) { result=91; ecore_main_loop_quit(); }
}
static int count(void) {
 int n=0; FILE *f=fopen("/tmp/count", "r");
 if(f) { char text[32]; if(!fgets(text,sizeof text,f)) n=-1;
  else for(char *p=text; *p>='0' && *p<='9'; p++) n=n*10+(*p-'0');
  fclose(f); } return n;
}
static int check(int ok, const char *name) {
 printf("{\"event\":\"check\",\"name\":\"%s\",\"passed\":%s,\"time\":%.3f}\n", name, ok?"true":"false", ecore_time_get()-started);
 fflush(stdout); if(!ok) { result=92; ecore_main_loop_quit(); } return ok;
}
static int drive(void *unused) {
 (void)unused; double elapsed=ecore_time_get()-started;
 if(last_attempts != (int)loader.attempts || last_epoch != loader.epoch) {
  printf("{\"event\":\"attempt\",\"attempt\":%u,\"phase\":%d,\"time\":%.3f,\"child\":%d}\n",loader.attempts,phase,elapsed,loader.child);
  fflush(stdout); last_attempts=(int)loader.attempts; last_epoch=loader.epoch;
 }
 if(phase==0 && invalid_stage==0 && elapsed>1.1) {
  if(!check(count()==0 && !loader.child && !loader.present,"absent_no_execution")) return 0;
  mkdir("/mod",0755); write_file("/mod/ram-start.sh",script);
  write_file("/mod/DISABLED", ""); invalid_stage=1;
 } else if(phase==0 && invalid_stage==1 && elapsed>2.2) {
  if(!check(count()==0 && !loader.child && loader.disabled,"disabled_resource_not_started")) return 0;
  unlink("/mod/DISABLED"); unlink("/mod/ram-start.sh"); mkdir("/mod/ram-start.sh",0755); invalid_stage=2;
 } else if(phase==0 && invalid_stage==2 && elapsed>3.3) {
  if(!check(count()==0 && !loader.child && !loader.present,"directory_script_not_executed")) return 0;
  rmdir("/mod/ram-start.sh"); write_file("/mod/ram-start.sh",script); chmod("/mod/ram-start.sh",0000); invalid_stage=3;
 } else if(phase==0 && invalid_stage==3 && elapsed>4.4) {
  if(!check(count()==0 && !loader.child && !loader.present,"unreadable_script_not_executed")) return 0;
  chmod("/mod/ram-start.sh",0644); write_file("/tmp/mode","ok\n"); phase=1;
 } else if(phase==1 && elapsed>7.5) {
  int status; errno=0; pid_t p=waitpid(-1,&status,WNOHANG);
  if(!check(count()==1 && loader.succeeded && !loader.child && p==-1 && errno==ECHILD,"success_once_child_reaped")) return 0;
  rename("/mod","/removed"); phase=2;
 } else if(phase==2 && elapsed>8.7) {
  if(!check(!loader.present && count()==1,"removal_observed")) return 0;
  rename("/removed","/mod"); phase=3;
 } else if(phase==3 && elapsed>11.9) {
  if(!check(count()==2 && loader.succeeded && !loader.child,"same_inode_reinsertion_retriggers")) return 0;
  write_file("/tmp/mode","fail\n"); write_file("/mod/new.sh",script);
  rename("/mod/new.sh","/mod/ram-start.sh"); phase=4;
 } else if(phase==4 && loader.attempts==5 && !loader.child) {
  exhausted=elapsed; phase=5;
 } else if(phase==5 && elapsed-exhausted>2.2) {
  int status; errno=0; pid_t p=waitpid(-1,&status,WNOHANG);
  if(!check(count()==7 && loader.attempts==5 && !loader.succeeded && p==-1 && errno==ECHILD,"five_failures_bounded_and_reaped")) return 0;
  rename("/mod","/removed"); changed=elapsed; phase=6;
 } else if(phase==6 && elapsed-changed>1.1) {
  if(!check(!loader.present,"exhausted_resource_removed")) return 0;
  write_file("/tmp/mode","ok\n"); rename("/removed","/mod"); phase=7;
 } else if(phase==7 && loader.succeeded && !loader.child) {
  if(!check(count()==8,"reinsertion_resets_retry_budget")) return 0;
  write_file("/mod/DISABLED", ""); write_file("/mod/new.sh",script);
  rename("/mod/new.sh","/mod/ram-start.sh"); changed=elapsed; phase=8;
 } else if(phase==8 && elapsed-changed>1.6) {
  if(!check(count()==8 && !loader.child && loader.disabled && !loader.succeeded,
            "disabled_changed_resource_not_restarted")) return 0;
  unlink("/mod/DISABLED"); phase=9;
 } else if(phase==9 && loader.succeeded && !loader.child) {
  if(!check(count()==9 && !loader.disabled,"removing_disable_allows_pending_start")) return 0;
  ecore_main_loop_quit(); return 0;
 }
 return 1;
}
int main(void) {
 if(ecore_init()<=0) return 93;
 started=ecore_time_get();
 if(!check(!camera_process(),"real_guard_rejects_honest_fixture") ||
    !check(camera_name("/usr/bin/di-camera-app-nx300") && camera_name("di-camera-app") &&
       !camera_name("di-camera-app-fake") && !camera_name("fixture -- di-camera-app"),"exact_camera_basename_cases")) return 94;
 int sentinel=open("/dev/null",O_RDONLY); if(sentinel<0 || dup2(sentinel,47)!=47) return 95;
 close(sentinel);
 /* Exercise /dev/null reusing closed fd0: dup2(0,0) retains CLOEXEC. */
 close(0);
 register_timer(); register_timer();
 if(!check(loader.timer!=NULL,"real_ecore_timer_registered")) return 96;
 ecore_timer_add(.1,drive,NULL);
 ecore_main_loop_begin();
 close(47); int shutdown=ecore_shutdown();
 printf("{\"event\":\"finished\",\"result\":%d,\"phase\":%d,\"shutdown\":%d}\n",result,phase,shutdown);
 return result || phase!=9;
}
'''

GUARD = r'''
extern int printf(const char *, ...);
extern int access(const char *, int);
extern int ecore_init(void);
extern int ecore_shutdown(void);
extern void *ecore_timer_add(double, int (*)(void *), void *);
extern void ecore_main_loop_begin(void);
extern void ecore_main_loop_quit(void);
static int stop(void *unused) { (void)unused; ecore_main_loop_quit(); return 0; }
int main(void) {
 if(ecore_init()<=0) return 1;
 ecore_timer_add(1.6,stop,(void *)0); ecore_main_loop_begin();
 int no_execution=access("/tmp/count",0)!=0;
 printf("{\"event\":\"production_guard\",\"passed\":%s,\"ecore_shutdown\":%d}\n",no_execution?"true":"false",ecore_shutdown());
 return no_execution?0:2;
}
'''

STDIO = r'''
/* Direct launch-only boundary fixture; no Ecore loop or camera impersonation. */
#define ecore_main_loop_begin fixture_hook_begin
#include "SOURCE"
#undef ecore_main_loop_begin
int main(int argc, char **argv) {
 if(argc!=2 || argv[1][0]<'0' || argv[1][0]>'2' || argv[1][1]) return 90;
 memset(&loader,0,sizeof loader); loader.owner=getpid();
 strcpy(loader.root,"/mod"); strcpy(loader.script,"/mod/ram-start.sh");
 strcpy(loader.root_env,"NX300M_MOD_ROOT=/mod");
 if(!resource(&loader.current)) return 91;
 loader.present=1;
 int fd=open("/dev/null",O_RDONLY);
 if(fd<0 || dup2(fd,47)!=47) return 92;
 close(fd); close(argv[1][0]-'0');
 launch_child(); if(!loader.child) return 93;
 int status; pid_t child;
 do { child=waitpid(loader.child,&status,0); } while(child<0 && errno==EINTR);
 return child>0 && WIFEXITED(status) ? WEXITSTATUS(status) : 94;
}
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    selected = parser.add_mutually_exclusive_group()
    selected.add_argument('--stdio-only', action='store_true', help='Only rerun closed-fd0/1/2 boundaries; retain existing full-loop reports')
    selected.add_argument('--timer-only', action='store_true', help='Only rerun the original timer/backoff regression; reuse existing guard report')
    args=parser.parse_args()
    stage()
    compile_arm(ROOT / 'runtime/service_loader.c', OUT / 'service-loader.so', shared=True)
    (OUT / 'crt0.S').write_text(CRT, encoding='utf-8', newline='\n')
    fixture = OUT / 'service-timer-fixture.c'
    fixture.write_text(FIXTURE.replace('SOURCE', wsl(ROOT / 'runtime/service_loader.c')), encoding='utf-8', newline='\n')
    guard = OUT / 'service-guard-probe.c'
    guard.write_text(GUARD, encoding='utf-8', newline='\n')
    compile_arm(fixture, OUT / 'service-timer-fixture.arm', extra=(OUT / 'crt0.S',))
    if not args.timer_only:
        compile_arm(guard, OUT / 'service-guard-probe.arm', extra=(OUT / 'crt0.S',))
    stdio=OUT / 'service-stdio-fixture.c'
    stdio.write_text(STDIO.replace('SOURCE',wsl(ROOT / 'runtime/service_loader.c')),encoding='utf-8',newline='\n')
    if not args.timer_only:
        compile_arm(stdio, OUT / 'service-stdio-fixture.arm', extra=(OUT / 'crt0.S',))
    reused = ('guard','timer') if args.stdio_only else ('guard',) if args.timer_only else ()
    reports=[json.loads((OUT/(label+'-report.json')).read_text(encoding='utf-8')) for label in reused]
    labels=('stdio0','stdio1','stdio2') if args.stdio_only else ('timer',) if args.timer_only else ('guard','timer','stdio0','stdio1','stdio2')
    for label in labels:
        root = OUT / (label + '-root')
        root.mkdir(exist_ok=True)
        evidence = OUT / (label + '-evidence')
        evidence.mkdir(exist_ok=True)
        shell = '#!/qemu-arm-static /bin/arm/sh\nexec /qemu-arm-static /bin/arm/sh "$@"\n'
        sleep = '#!/qemu-arm-static /bin/arm/sh\nexec /qemu-arm-static /bin/arm/busybox sleep "$@"\n'
        (OUT / 'shell-bridge.sh').write_text(shell, encoding='utf-8', newline='\n')
        (OUT / 'sleep-bridge.sh').write_text(sleep, encoding='utf-8', newline='\n')
        target = '/service-guard-probe.arm' if label=='guard' else '/service-timer-fixture.arm' if label=='timer' else '/service-stdio-fixture.arm '+label[-1]
        setup = '''mkdir "$root/mod"
printf 'printf "unexpected\\n" > /tmp/count\\n' > "$root/mod/ram-start.sh"
''' if label=='guard' else ''
        if label.startswith('stdio'):
            setup='''mkdir "$root/mod"
cat > "$root/mod/ram-start.sh" <<'NX_STDIO_SCRIPT'
#!/bin/sh
[ "$#" -eq 1 ] && [ "$1" = --service ] || exit 80
[ -z "${LD_PRELOAD-}" ] || exit 81
[ ! -e /proc/self/fd/47 ] || exit 82
[ /proc/self/fd/0 -ef /dev/null ] && [ /proc/self/fd/1 -ef /dev/null ] && [ /proc/self/fd/2 -ef /dev/null ] || exit 83
printf 'clean_stdio\n' > /tmp/count
NX_STDIO_SCRIPT
'''
        script=f'''set -eu
mount --make-rprivate /
root={shlex.quote(wsl(root))}
mount -t tmpfs -o mode=0755,nosuid,nodev tmpfs "$root"
cp -a {shlex.quote(wsl(OUT / 'stock-dependencies'))}/. "$root"/
mkdir -p "$root/proc" "$root/dev" "$root/tmp" "$root/var/run" "$root/evidence"
chmod 777 "$root/tmp" "$root/var/run" "$root"
cp {shlex.quote(wsl(OUT))}/service-*.arm "$root/"
cp {shlex.quote(wsl(OUT / 'service-loader.so'))} "$root/"
cp {shlex.quote(wsl(ROOT / 'work/qemu/qemu-arm-static'))} "$root/qemu-arm-static"
cp {shlex.quote(wsl(OUT / 'shell-bridge.sh'))} "$root/bin/sh"
cp {shlex.quote(wsl(OUT / 'sleep-bridge.sh'))} "$root/bin/sleep"
ln -s busybox "$root/bin/arm/sh"
chmod -R a+rX "$root"
chmod 755 "$root"/service-*.arm "$root/qemu-arm-static" "$root/bin/sh" "$root/bin/sleep" "$root/bin/arm/busybox"
mount -t proc proc "$root/proc"
touch "$root/dev/null" "$root/dev/urandom"
mount --bind /dev/null "$root/dev/null"
mount --bind /dev/urandom "$root/dev/urandom"
{setup}
set +e
timeout 45 chroot --userspec=65534:65534 "$root" /qemu-arm-static -cpu cortex-a9 -L / \\
 -E NX300M_MOD_ROOT=/mod -E LD_PRELOAD=/service-loader.so {target}
status=$?
set -e
for name in count script-events; do
 if [ -f "$root/tmp/$name" ]; then cp "$root/tmp/$name" {shlex.quote(wsl(evidence))}/"$name"; fi
done
if [ -f "$root/var/run/nx300m-service-loader-status.json" ]; then
 cp "$root/var/run/nx300m-service-loader-status.json" {shlex.quote(wsl(evidence))}/status.json
fi
exit "$status"
'''
        (OUT / (label + '-host.sh')).write_text(script, encoding='utf-8', newline='\n')
        result=subprocess.run(['wsl.exe','-d','Debian','-u','root','-e','unshare','--mount','--net','--ipc','--pid','--fork','--mount-proc','sh','-s'],input=script.encode(),capture_output=True,timeout=55)
        (OUT / (label+'.stdout.log')).write_bytes(result.stdout)
        (OUT / (label+'.stderr.log')).write_bytes(result.stderr)
        events=[json.loads(line) for line in result.stdout.decode('utf-8','replace').splitlines() if line.startswith('{"event":')]
        report={'case':label,'exit_code':result.returncode,'events':events,
                'production_camera_guard_bypassed': label!='guard',
                'fixture_camera_identity_spoofed':False,'camera_app_executed':False,
                'scope':'production SO guard rejection' if label=='guard' else 'internal timer directly registered by source-inclusion fixture; production SO also preloaded but guard rejects fixture' if label=='timer' else 'internal launch directly called after closing fd'+label[-1]+'; no Ecore loop',
                'stock_loader_libc_ecore':label in ('guard','timer'),'stock_busybox_via_explicit_qemu_interpreter_bridge':True,
                'global_binfmt_changed':False,'physical_camera_verified':False,
                'isolation':'fresh mount/net/IPC/PID namespace, private tmpfs root, uid65534, only null/urandom devices',
                'main_rootfs_changed':False,'candidate_changed':False}
        if label.startswith('stdio'):
            report['stdio_survived_exec']= (evidence/'count').is_file() and (evidence/'count').read_text().strip()=='clean_stdio'
            if not report['stdio_survived_exec']: report['exit_code']=result.returncode or 95
        (OUT / (label+'-report.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        reports.append(report)
        print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
        if report['exit_code']:
            print(result.stderr.decode('utf-8','replace'))
            raise SystemExit(report['exit_code'])
    retries=[e['time'] for e in reports[1]['events'] if e['event']=='attempt' and e['phase']==4 and e['attempt']>0]
    gaps=[round(b-a,3) for a,b in zip(retries,retries[1:])]
    verified=len(retries)==5 and all(g>=minimum for g,minimum in zip(gaps,[1,2,4,8]))
    summary={'exit_code':0 if verified else 1,'guard_passed':reports[0]['exit_code']==0,
             'reused_reports':list(reused),
             'timer_passed':reports[1]['exit_code']==0,'failure_attempt_times_seconds':retries,
             'closed_stdio_cases':[{k:r[k] for k in ('case','exit_code','stdio_survived_exec')} for r in reports if r['case'].startswith('stdio')],
             'failure_retry_gaps_seconds':gaps,'bounded_exponential_backoff_verified':verified,
             'physical_camera_verified':False,'snapshot_restore_verified':False,
             'production_integration_pending':True}
    (OUT / 'verification-summary.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(summary,indent=2))
    raise SystemExit(summary['exit_code'])


if __name__=='__main__':
    main()
