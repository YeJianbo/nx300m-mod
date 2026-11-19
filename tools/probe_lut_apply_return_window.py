"""Exercise the apply/disable return transaction in an isolated user space.

Real ARM code runs unchanged under QEMU user mode: `mod/metol/lut_engine.c`
service_control(), nx_lut_stage_with_strength()/nx_lut_stock() and
lut_engine_shared.h's nx_lut_sync_preview()/nx_lut_lock()/preference writer,
plus the real `lut-load.sh` and continuous-preview wrapper gates. The data root is
/perf on a private tmpfs; mounts and workers live in private mount/PID
namespaces. No ptrace, camera, IPCS or card. Budgets use the existing
NX_LUT_*_BUDGET_MS overrides; no production timeout is relaxed.

Cases (labels match docs/PI_WORKLOG_2026-11-15-apply-return.md):
  W6/W6b staged selection survives a failed preview sync; same-value retry applies
  W8     disable reports the failed preview sync instead of a successful off
  W7     preference write failure is reported, not silently saved
  W1     real lut-load.sh refuses on the uncertainty guard
  W2     real lut-load.sh refuses while a live worker holds the preview lock and
         the guard does not exist yet (the 20 s outer / 30 s+ inner window)
  W3     with both gates clear the same script reaches the target check, so W1/W2
         are attributable to the gate and not to an earlier failure
  W5/W5b the outer deadline abandons without killing; the engine layer then admits
         a second control, so the scripts own the exclusion
  W4     the real preview wrapper refuses on the guard
"""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys
from wsl_paths import wsl_path

ROOT = Path(__file__).resolve().parents[1]

HARNESS_HEAD = r'''
#define main nx_engine_program_main
#include "mod/metol/lut_engine.c"
#undef main
static void need(int ok,const char *name) {if(!ok){fprintf(stderr,"HARNESS_ERROR %s\n",name);exit(2);}}
static int exists(const char *p) {return access(p,F_OK)==0;}
static int ram_dirs(void) {
    /* Private lut-load RAM runtimes left behind by a refused attempt. */
    DIR *d=opendir("/tmp"); if(!d) return -1; int n=0; struct dirent *e;
    while((e=readdir(d))) if(!strncmp(e->d_name,"nx300m-lut-load.",16)) n++;
    closedir(d); return n;
}
static void result(const char *name,int ok,const char *detail) {
    printf("WINDOW_RESULT {\"name\":\"%s\",\"contract_passed\":%s,\"detail\":\"%s\"}\n",name,ok?"true":"false",detail);
    fflush(stdout);
}
static int run_script(const char *path,const char *operation) {
    pid_t child=fork();
    if(child<0) return -1;
    if(!child) { char *const args[]={"sh",(char*)path,(char*)operation,NULL};
        extern char **environ; execve("/bin/sh",args,environ); _exit(127); }
    int status=0; if(waitpid(child,&status,0)!=child) return -1;
    return WIFEXITED(status)?WEXITSTATUS(status):128;
}
'''

HARNESS_BODY = r'''
int main(void) {
    const char *guard="/tmp/nx300m-smartfilter-preview.uncertain";
    const char *look="/perf/looks/ricoh-negative-exact/table.d4lut";
    const char *sync="/tmp/w-runtime/smartfilter-preview.sh";
    int before=ram_dirs();

    /* The preview backend marker routes sync into the synchronous chain. */
    int fd=open("/var/run/nx300m-preview-backend",O_WRONLY|O_CREAT|O_TRUNC,0600);
    need(fd>=0,"backend_marker");ssize_t w=write(fd,"smartfilter\n",12);(void)w;close(fd);

    /* W6: staged selection survives a failed preview sync. */
    fd=open("/tmp/w-sync-fail",O_WRONLY|O_CREAT|O_TRUNC,0600);need(fd>=0,"sync_fail");close(fd);
    unsigned int generation=0;
    int staged=nx_lut_stage_with_strength(look,100,&generation);
    char selection[512]="";{FILE *s=fopen(NX_LUT_SELECTION,"r");need(s!=NULL,"selection_read");
        need(fgets(selection,sizeof selection,s)!=NULL,"selection_line");fclose(s);}
    char expected[600];snprintf(expected,sizeof expected,"%u %s 100\n",generation,look);
    result("W6_staged_selection_kept_after_sync_failure",
        staged==6&&!strcmp(selection,expected),"rc 6, staged generation and path unchanged");
    unlink("/tmp/w-sync-fail");
    unsigned int retried=0;
    int again=nx_lut_stage_with_strength(look,100,&retried);
    result("W6b_same_value_retry_applies",
        again==0&&retried>generation,"retry reaches the chain and stages a newer generation");

    /* W8: disable must not claim a successful off when the sync failed. */
    fd=open("/tmp/w-sync-fail",O_WRONLY|O_CREAT|O_TRUNC,0600);need(fd>=0,"sync_fail2");close(fd);
    int off=nx_lut_stock();
    unlink("/tmp/w-sync-fail");
    result("W8_disable_reports_failed_sync",off==6,"nx_lut_stock propagates the failed preview sync");

    /* W7: preference write failure must be reported, not silently saved. */
    /* Independent empty data root so this case cannot inherit leftovers. */
    struct stat st2;need(!stat("/perf2",&st2)&&S_ISDIR(st2.st_mode),"perf2_present");
    char id2[64],id0[64];snprintf(id2,sizeof id2,"%llu:%llu",(unsigned long long)st2.st_dev,(unsigned long long)st2.st_ino);
    const char *saved_root=getenv("NX300M_MOD_SOURCE_ROOT"),*saved_id=getenv("NX300M_MOD_SOURCE_ID");
    snprintf(id0,sizeof id0,"%s",saved_id?saved_id:"");
    setenv("NX300M_MOD_SOURCE_ROOT","/perf2",1);setenv("NX300M_MOD_SOURCE_ID",id2,1);
    need(!symlink("/tmp","/perf2/.state"),"state_block");
    int blocked=nx_lut_preview_preference(1);
    unlink("/perf2/.state");
    need(!mkdir("/perf2/.state",0700),"state_restore");
    int recovered=nx_lut_preview_preference(1);
    setenv("NX300M_MOD_SOURCE_ROOT",saved_root?saved_root:"/perf",1);
    setenv("NX300M_MOD_SOURCE_ID",id0,1);
    result("W7_preference_write_failure_reported",blocked==0&&recovered==1,
        "blocked state file yields failure; restored directory yields success");

    /* Swap the stub for the real wrapper before the gate cases. */
    need(!rename("/tmp/w-real-preview.sh",sync),"real_wrapper_swap");

    /* W1: uncertainty guard blocks the real loader script before any work. */
    FILE *g=fopen(guard,"w");need(g!=NULL,"guard_create");fprintf(g,"timeout child=1\n");fclose(g);
    int rc=service_control("lut-load.sh","apply",0);
    result("W1_guard_refuses_real_lut_load",rc==1&&exists(guard)&&ram_dirs()==before,
        "real lut-load.sh exit 1 on the guard, no RAM runtime created");
    unlink(guard);

    /* W2: the 20 s/30 s+ window - engine lock gone, guard not published yet,
     * live worker holds the preview lock. The real script must refuse. */
    unlink("/tmp/w-lockready");
    pid_t holder=fork();need(holder>=0,"holder_fork");
    if(!holder) {
        int lock=open("/tmp/nx300m-smartfilter-preview.lock",O_RDWR|O_CREAT,0600);
        if(lock<0||flock(lock,LOCK_EX|LOCK_NB))_exit(30);
        int ready=open("/tmp/w-lockready",O_WRONLY|O_CREAT|O_TRUNC,0600);
        if(ready>=0){ssize_t n=write(ready,"1\n",2);(void)n;close(ready);}
        struct timespec nap={4,0};nanosleep(&nap,NULL);
        _exit(0);
    }
    for(int i=0;i<150&&!exists("/tmp/w-lockready");i++){struct timespec s={0,20*1000*1000};nanosleep(&s,NULL);}
    need(exists("/tmp/w-lockready"),"holder_ready");
    rc=service_control("lut-load.sh","apply",0);
    result("W2_live_worker_window_refuses_real_lut_load",
        rc==1&&!exists(guard)&&ram_dirs()==before,
        "worker alive, no guard yet: real lut-load.sh refused via the preview lock");
    int status=0;need(waitpid(holder,&status,0)==holder,"holder_wait");

    /* W3: both gates clear - the same call proceeds to the target check. */
    rc=service_control("lut-load.sh","apply",0);
    result("W3_gates_clear_reaches_target_check",rc==1&&ram_dirs()==before,
        "no gate message; script reached the camera-process check and stopped there");

    /* W5: the outer deadline abandons the chain without killing it. */
    unlink("/tmp/w-slow-marker");
    unsigned started=nx_lut_control_ms();
    rc=service_control("slow.sh","apply",0);
    unsigned elapsed=nx_lut_control_ms()-started;
    result("W5_outer_deadline_abandons_without_killing",
        rc==4&&!exists("/tmp/w-slow-marker")&&elapsed<3000,
        "CONTROL-UNCERTAIN returned before the worker finished");
    unlink("/tmp/w-probe-entry");
    int second=service_control("probe.sh","apply",0);
    result("W5b_second_control_admitted_while_worker_alive",
        second==0&&exists("/tmp/w-probe-entry")&&!exists("/tmp/w-slow-marker"),
        "engine layer admits the second control; scripts own the exclusion");
    for(int i=0;i<200&&!exists("/tmp/w-slow-marker");i++){struct timespec s={0,20*1000*1000};nanosleep(&s,NULL);}
    need(exists("/tmp/w-slow-marker"),"slow_marker");

    /* W4: the real preview wrapper also refuses on the guard. */
    g=fopen(guard,"w");need(g!=NULL,"guard_create2");fprintf(g,"timeout child=1\n");fclose(g);
    int wrapper=run_script(sync,"sync");
    result("W4_real_wrapper_refuses_guard",wrapper!=0,"continuous wrapper refused; analyzer requires the guard diagnostic");
    unlink(guard);
    return 0;
}
'''

SETUP = r'''
set -eu
runtime=/tmp/w-runtime
mkdir -p "$runtime" /tmp/w-state
mkdir -p /perf
mount -t tmpfs -o rw,mode=1777,size=16m w-perf /perf
mkdir -p /perf/looks/ricoh-negative-exact /perf/.state
mkdir -p /perf2
mount -t tmpfs -o rw,mode=1777,size=4m w-perf2 /perf2
cp /mnt/d/codex/project/2026-10-19/nx300m-mod/looks/ricoh-negative-exact/table.d4lut \
   /perf/looks/ricoh-negative-exact/table.d4lut
'''

REAL_SCRIPTS = {
    'lut-load.sh': 'mod/deployment/mfw/lut-load.sh',
    # RAM staging installs the continuous wrapper under this engine-facing
    # name. The probe-only script rejects sync before consulting the guard.
    'smartfilter-preview.sh': 'mod/deployment/mfw/smartfilter-live-preview.sh',
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    out = args.output_dir.resolve()
    if (out / 'report.json').exists():
        raise RuntimeError('Choose a new output directory; preserve previous evidence')
    out.mkdir(parents=True, exist_ok=True)
    (out / 'harness.c').write_text(HARNESS_HEAD + HARNESS_BODY, encoding='utf-8', newline='\n')
    stubs = {
        'slow.sh': '#!/bin/sh\nsleep 2\ntouch /tmp/w-slow-marker\nexit 0\n',
        'probe.sh': '#!/bin/sh\ntouch /tmp/w-probe-entry\nexit 0\n',
        'smartfilter-preview.sh': '#!/bin/sh\n[ ! -e /tmp/w-sync-fail ] || exit 1\nexit 0\n',
    }
    for name, body in stubs.items():
        (out / f'stub-{name}').write_text(body, encoding='utf-8', newline='\n')
    real = {name: (ROOT / path).read_bytes() for name, path in REAL_SCRIPTS.items()}
    stage = ''.join(
        f'cp {shlex.quote(wsl_path(out / ("stub-" + name)))} "$runtime"/{name}\n'
        f'chmod 700 "$runtime"/{name}\n' for name in stubs)
    stage += (f'cp {shlex.quote(wsl_path(out / "stub-smartfilter-preview.sh"))} '
              f'"$runtime"/smartfilter-preview.sh\n')
    stage += f'cp {shlex.quote(wsl_path(ROOT / REAL_SCRIPTS["smartfilter-preview.sh"]))} /tmp/w-real-preview.sh\n'
    stage += (f'cp {shlex.quote(wsl_path(ROOT / REAL_SCRIPTS["lut-load.sh"]))} "$runtime"/lut-load.sh\n'
              f'chmod 700 "$runtime"/lut-load.sh\n')
    shell = f'''set -eu
cd {shlex.quote(wsl_path(ROOT))}
tc=/opt/nx300m-toolchain/usr
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
rf=/opt/nx300m-lab/stock-1.15
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=c99 -O1 -Wall -Wextra -Werror -Wno-unused-function -D_GNU_SOURCE -include mod/metol/remote/compat.h -I. \\
 -U_TIME_BITS -D_TIME_BITS=32 -U_FILE_OFFSET_BITS -D_FILE_OFFSET_BITS=64 \\
 -mfloat-abi=soft -fno-pic -no-pie -fno-stack-protector \\
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 \\
 -Wl,-rpath-link,"$rf/lib" \\
 {shlex.quote(wsl_path(out / 'harness.c'))} mod/metol/remote/compat.c work/reference/metol/test_server/filmlab/src/crt0_arm.S \\
 "$rf/lib/libc-2.13.so" -lgcc -o {shlex.quote(wsl_path(out / 'harness.arm'))}
mount --make-rprivate /
mount -t tmpfs -o rw,mode=1777,size=32m w-tmp /tmp
mount -t tmpfs -o rw,mode=1777,size=8m w-var /var/run
{SETUP}
{stage}
printf '#!/bin/sh\\nprintf "armv7l\\\\n"\\n' > /tmp/w-uname
chmod 700 /tmp/w-uname
mount --bind /tmp/w-uname "$(readlink -f /usr/bin/uname)"
export NX_LUT_SYNC_BUDGET_MS=600 NX_LUT_CONTROL_BUDGET_MS=600
export NX300M_MOD_ROOT="$runtime"
export NX300M_MOD_SOURCE_ROOT=/perf
export NX300M_MOD_SOURCE_ID="$(stat -c '%d:%i' /perf)"
export NX_STATE_DIR=/tmp/w-state
export NX_SF_PROBE_FIXTURE=1
set +e
work/qemu/qemu-arm-static -L "$rf" {shlex.quote(wsl_path(out / 'harness.arm'))}
harness_rc=$?
set -e
sleep 3
echo "$harness_rc" > {shlex.quote(wsl_path(out / 'harness.rc'))}
exit 0
'''
    (out / 'run.sh').write_text(shell, encoding='utf-8', newline='\n')
    done = subprocess.run(['wsl.exe', '-d', 'Debian', '-u', 'root', '-e',
                           'unshare', '--mount', '--pid', '--fork', '--mount-proc', 'sh', '-c', shell],
                          capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=150)
    (out / 'stdout.log').write_text(done.stdout, encoding='utf-8')
    (out / 'stderr.log').write_text(done.stderr, encoding='utf-8')
    if done.returncode or not (out / 'harness.rc').exists():
        report = dict(diagnostic_completed=False, exit_code=done.returncode,
                      failure=(done.stderr or done.stdout)[-3000:])
        (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2
    harness_rc = int((out / 'harness.rc').read_text().strip())
    checks = [json.loads(line.removeprefix('WINDOW_RESULT '))
              for line in (out / 'stdout.log').read_text().splitlines()
              if line.startswith('WINDOW_RESULT ')]
    stderr_text = (out / 'stderr.log').read_text()
    report = dict(
        diagnostic_completed=harness_rc == 0,
        exit_code=harness_rc,
        scope=('Real ARM service_control/nx_lut_stage_with_strength/nx_lut_stock/nx_lut_sync_preview and the real '
               'lut-load.sh + smartfilter-live-preview.sh gates against a /perf fixture data root on private tmpfs; '
               'no ptrace, camera, IPCS or card'),
        engine_sha256=hashlib.sha256((ROOT / 'mod/metol/lut_engine.c').read_bytes()).hexdigest(),
        shared_sha256=hashlib.sha256((ROOT / 'mod/metol/lut_engine_shared.h').read_bytes()).hexdigest(),
        lut_load_sha256=hashlib.sha256(real['lut-load.sh']).hexdigest(),
        preview_wrapper_sha256=hashlib.sha256(real['smartfilter-preview.sh']).hexdigest(),
        private_mount_and_pid_namespaces=True,
        budgets=dict(control_ms=600, sync_ms=600),
        messages=dict(guard_gate=stderr_text.count('preview recovery is still active'),
                      wrapper_guard_gate=stderr_text.count('上次操作未解决；保留 RAM 现场，禁止重武装'),
                      lock_gate=stderr_text.count('preview control is running'),
                      target_check=stderr_text.count('expected exactly one running original camera process'),
                      control_uncertain=stderr_text.count('CONTROL-UNCERTAIN'),
                      preview_warning=stderr_text.count('PREVIEW-WARNING'),
                      persist_warning=stderr_text.count('PERSIST-WARNING')),
        checks=checks,
        cases_passed=sum(1 for row in checks if row['contract_passed']),
        cases_failed=sum(1 for row in checks if not row['contract_passed']),
    )
    expected = {
        'W6_staged_selection_kept_after_sync_failure', 'W6b_same_value_retry_applies',
        'W8_disable_reports_failed_sync', 'W7_preference_write_failure_reported',
        'W1_guard_refuses_real_lut_load', 'W2_live_worker_window_refuses_real_lut_load',
        'W3_gates_clear_reaches_target_check', 'W5_outer_deadline_abandons_without_killing',
        'W5b_second_control_admitted_while_worker_alive', 'W4_real_wrapper_refuses_guard',
    }
    report['gate_causes_verified'] = all(report['messages'][name] == 1 for name in
                                        ('guard_gate', 'wrapper_guard_gate', 'lock_gate', 'target_check'))
    report['contracts_passed'] = (harness_rc == 0 and report['cases_failed'] == 0 and
                                  len(checks) == len(expected) and
                                  {row['name'] for row in checks} == expected and
                                  report['gate_causes_verified'])
    (out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['contracts_passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
