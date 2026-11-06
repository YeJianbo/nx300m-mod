"""Finite stand-in validation of the LUT control recovery protocol.

Real ARM code: lut_engine.c service_control() and lut_engine_shared.h
nx_lut_sync_preview()/nx_lut_lock() run unchanged under QEMU user mode against
fixture scripts on a tmpfs runtime. No ptrace, IPCS, camera or preview lease is
executed. Mounts and workers live in private mount/PID namespaces. A finite
worker publishes uncertainty; the real continuous wrapper must refuse re-entry
both while that worker lives and after it exits. Exit alone never clears guard.
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

HARNESS = r'''
#define main nx_engine_program_main
#include "mod/metol/lut_engine.c"
#undef main
static void need(int ok,const char *name) {if(!ok){fprintf(stderr,"HARNESS_ERROR %s\n",name);exit(2);}}
static int marker_seen(const char *path) {return access(path,F_OK)==0;}
static void wait_marker(const char *path,const char *name) {
    for(int i=0;i<80;i++){if(marker_seen(path))return;struct timespec p={0,50*1000*1000};nanosleep(&p,NULL);}
    need(0,name);
}
static void result(const char *name,int ok,const char *detail) {
    printf("PROTOCOL_RESULT {\"name\":\"%s\",\"contract_passed\":%s,\"detail\":\"%s\"}\n",name,ok?"true":"false",detail);
}
int main(void) {
    /* 1. The engine lock fd must be CLOEXEC so abandoned chains cannot hold
     * the flock after this engine exits. */
    int lock=nx_lut_lock(0,1);
    need(lock>=0,"lock_acquire");
    int flags=fcntl(lock,F_GETFD);
    result("engine_lock_is_cloexec",flags>=0&&(flags&FD_CLOEXEC),flags>=0?"flags set":"fcntl failed");
    /* 2. Duplicate request: a second nonblocking exclusive taker is refused
     * while the first owner holds the lock. */
    pid_t probe=fork();
    need(probe>=0,"fork_dup");
    if(!probe){_exit(nx_lut_lock(1,1)>=0?10:20);}
    int dup_status=0;
    need(waitpid(probe,&dup_status,0)==probe,"wait_dup");
    result("duplicate_request_refused_while_held",WIFEXITED(dup_status)&&WEXITSTATUS(dup_status)==20,
        WIFEXITED(dup_status)?"second taker refused":"second taker crashed");
    close(lock);

    /* 3. sync_preview normal completion. */
    int rc=nx_lut_sync_preview();
    result("sync_normal_completes",rc==0,"rc");
    rc=nx_lut_sync_preview();
    result("retry_after_confirmed_completion",rc==0,"normal completed operation permits next request");
    /* Hold the real engine lock until the outer call returns, then release
     * it as a terminating engine would. Re-entry must still be rejected. */
    lock=nx_lut_lock(0,1);need(lock>=0,"slow_lock");
    rc=nx_lut_sync_preview();
    result("sync_slow_chain_abandoned_not_killed",rc==6&&!marker_seen("/tmp/h6-slow-marker"),"outer returned before worker completed");
    close(lock);
    lock=nx_lut_lock(1,1);
    result("engine_lock_released_while_worker_active",lock>=0&&!marker_seen("/tmp/h6-slow-marker"),"new engine may enter; real wrapper guard must still refuse");
    if(lock>=0)close(lock);
    need(marker_seen("/tmp/nx300m-smartfilter-preview.uncertain"),"guard_exists");
    rc=nx_lut_sync_preview();
    result("live_worker_reentry_refused_by_real_wrapper",rc==6&&!marker_seen("/tmp/h6-slow-marker"),"production guard rejection before scanner or IPCS");
    char saved_id[64];snprintf(saved_id,sizeof saved_id,"%s",getenv("NX300M_MOD_SOURCE_ID"));
    setenv("NX300M_MOD_SOURCE_ID","0:0",1);
    rc=nx_lut_sync_preview();
    result("changed_source_identity_refuses_sync_and_control",rc==6&&service_control("lut-load.sh","apply",0)==4,"identity changed; no worker may start");
    setenv("NX300M_MOD_SOURCE_ID",saved_id,1);
    wait_marker("/tmp/h6-slow-marker","slow_chain_marker");
    rc=nx_lut_sync_preview();
    result("uncertain_guard_retained_after_worker_exit",rc==6&&marker_seen("/tmp/nx300m-smartfilter-preview.uncertain"),"worker completion is not confirmed hardware recovery");
    rc=service_control("lut-load.sh","apply",0);
    result("control_normal_completes",rc==0,"rc");
    rc=service_control("lut-load.sh","apply",0);
    result("control_slow_chain_abandoned_not_killed",rc==4,"CONTROL-UNCERTAIN expected");
    wait_marker("/tmp/h6-control-marker","control_chain_marker");
    return 0;
}
'''

SETUP = r'''
set -eu
runtime=/tmp/h6-runtime
data=/tmp/h6-data
mkdir -p "$runtime" "$data"
mkdir -p /tmp/h6-state
'''

# The sync target script is swapped between scenarios by touching marker files;
# the harness calls run in sequence and the script content decides the phase.
SCRIPTS = {
    'smartfilter-preview.sh': r'''#!/bin/sh
step=/tmp/h6-step
phase=0
[ -f "$step" ] && phase=$(cat "$step")
case $phase in
0) printf '1\n' > "$step"; exit 0 ;;
1) printf '2\n' > "$step"; exit 0 ;;
2) printf '3\n' > "$step"
   printf 'uncertain owner=%s; finite worker is not hardware recovery\n' "$$" > /tmp/nx300m-smartfilter-preview.uncertain
   sleep 2; touch /tmp/h6-slow-marker; exit 0 ;;
*) NX300M_MOD_SOURCE_ROOT=/perf NX300M_MOD_SOURCE_ID=1:1 NX_SF_PROBE_FIXTURE=1 NX_STATE_DIR=/tmp/h6-state \
       /bin/sh /tmp/h6-runtime/production-preview.sh sync ;;
esac
''',
    'lut-load.sh': r'''#!/bin/sh
count=/tmp/h6-control-count
n=0
[ -f "$count" ] && n=$(cat "$count")
n=$((n+1)); printf '%s\n' "$n" > "$count"
if [ "$n" -ge 2 ]; then
    sleep 2; touch /tmp/h6-control-marker; exit 0   # scenario 7b: slow then finish
fi
exit 0                                              # scenario 7a: normal
''',
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    out = args.output_dir.resolve()
    if (out / 'report.json').exists():
        raise RuntimeError('Choose a new output directory; preserve previous evidence')
    out.mkdir(parents=True, exist_ok=True)
    (out / 'harness.c').write_text(HARNESS, encoding='utf-8')
    shell_pre = SETUP
    for name, body in SCRIPTS.items():
        (out / name).write_text(body, encoding='utf-8', newline='\n')
        shell_pre += f'cp {shlex.quote(wsl_path(out / name))} "$runtime"/{name}\nchmod 700 "$runtime"/{name}\n'
    wrapper=ROOT/'mod/deployment/mfw/smartfilter-live-preview.sh'
    shell_pre += f'cp {shlex.quote(wsl_path(wrapper))} "$runtime"/production-preview.sh\n'
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
# Private mount propagation BEFORE any mount. PID namespace teardown owns all
# finite children, including the outer-deadline orphans.
mount --make-rprivate /
mount -t tmpfs nx-h6-tmp /tmp
mount -t tmpfs nx-h6-var /var/run
{shell_pre}
printf '#!/bin/sh\\nprintf "armv7l\\\\n"\\n' > /tmp/h6-uname
chmod 700 /tmp/h6-uname
mount --bind /tmp/h6-uname "$(readlink -f /usr/bin/uname)"
printf 'smartfilter\\n' > /var/run/nx300m-preview-backend
rm -f /tmp/h6-step /tmp/h6-control-count
export NX_LUT_SYNC_BUDGET_MS=600 NX_LUT_CONTROL_BUDGET_MS=600
export NX300M_MOD_ROOT=/tmp/h6-runtime
export NX300M_MOD_SOURCE_ROOT=/tmp/h6-data
export NX300M_MOD_SOURCE_ID="$(stat -c '%d:%i' /tmp/h6-data)"
set +e
work/qemu/qemu-arm-static -L "$rf" {shlex.quote(wsl_path(out / 'harness.arm'))}
harness_rc=$?
set -e
# Give abandoned orphans a moment to finish before the namespace tears down.
sleep 1
echo "$harness_rc" > {shlex.quote(wsl_path(out / 'harness.rc'))}
exit 0
'''
    (out / 'run.sh').write_text(shell, encoding='utf-8', newline='\n')
    done = subprocess.run(['wsl.exe', '-d', 'Debian', '-u', 'root', '-e',
                           'unshare', '--mount', '--pid', '--fork', '--mount-proc', 'sh', '-c', shell],
                          capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=90)
    (out / 'stdout.log').write_text(done.stdout, encoding='utf-8')
    (out / 'stderr.log').write_text(done.stderr, encoding='utf-8')
    if done.returncode or not (out / 'harness.rc').exists():
        report = dict(diagnostic_completed=False, exit_code=done.returncode,
                      failure=(done.stderr or done.stdout)[-2000:])
        (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(report, indent=2))
        return 2
    harness_rc = int((out / 'harness.rc').read_text().strip())
    checks = [json.loads(line.removeprefix('PROTOCOL_RESULT '))
              for line in (out / 'stdout.log').read_text().splitlines()
              if line.startswith('PROTOCOL_RESULT ')]
    stderr_text = (out / 'stderr.log').read_text()
    evidence = dict(
        uncertain_logged='PREVIEW-UNCERTAIN' in stderr_text,
        control_uncertain_logged='CONTROL-UNCERTAIN' in stderr_text,
        real_guard_refusals=stderr_text.count('上次操作未解决；保留 RAM 现场，禁止重武装'),
    )
    report = dict(diagnostic_completed=harness_rc == 0 and len(checks) == 11,
                  exit_code=harness_rc,
                  scope='Real ARM service_control/nx_lut_sync_preview/nx_lut_lock against fixture scripts on tmpfs; no ptrace/IPCS/camera',
                  engine_sha256=hashlib.sha256((ROOT / 'mod/metol/lut_engine.c').read_bytes()).hexdigest(),
                  shared_sha256=hashlib.sha256((ROOT / 'mod/metol/lut_engine_shared.h').read_bytes()).hexdigest(),
                  production_wrapper_sha256=hashlib.sha256(wrapper.read_bytes()).hexdigest(),
                  private_mount_and_pid_namespaces=True,
                  checks=checks, evidence=evidence,
                  contracts_passed=(harness_rc == 0 and len(checks) == 11 and
                                   all(row['contract_passed'] for row in checks) and
                                   evidence['uncertain_logged'] and evidence['control_uncertain_logged'] and
                                   evidence['real_guard_refusals']==2),
                  observed_contract_failures=sum(not row['contract_passed'] for row in checks))
    (out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(not report['contracts_passed'])


if __name__ == '__main__':
    sys.exit(main())
