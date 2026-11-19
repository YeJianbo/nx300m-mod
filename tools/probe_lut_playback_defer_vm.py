"""Playback LUT apply: saved vs pending-sync separation, in isolated user space.

Real ARM code runs unchanged under QEMU user mode: mod/metol/lut_engine.c's
nx_lut_stage_with_strength()/nx_lut_stock() and lut_engine_shared.h's
nx_lut_sync_preview_ex() with the 2026-11-18 playback deferral. The data root
is /perf on a private tmpfs; mounts and workers live in private mount/PID
namespaces. A native state process exposes the stock liveview state addresses
and the named camera threads, so the deferral decision polls a real snapshot
instead of trusting a flag. The sync chain is a recording stub except for the
deadline case; no ptrace, camera, IPCS or card.

Cases (docs/TASK_2026-11-18-preview-fps-playback-save.md P1 acceptance 1-2):
  D1 playback apply returns promptly, records preview-pending, no 12 s wait
  D2 liveview restored: the detached worker syncs the latest saved request
  D3 rapid re-selection while the worker waits: exactly one sync of the newest
  D4 preview closed while waiting: cancelled, chain never invoked
  D5 liveview never returns: bounded wait, expired (not failed), chain untouched
  D6 real save failure: card record unchanged, pending still recorded
  D7 liveview already ready: inline sync unchanged, pending records synced
  D8 inline chain past its budget: uncertain recorded, never "failed"
  D9 deferred chain past its budget: uncertain recorded by the worker
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
static void result(const char *name,int ok,const char *detail) {
    printf("DEFER_RESULT {\"name\":\"%s\",\"contract_passed\":%s,\"detail\":\"%s\"}\n",name,ok?"true":"false",detail);
    fflush(stdout);
}
static void set_cam(char state) {
    FILE *f=fopen("/tmp/w-cam-state","w");need(f!=NULL,"cam_state_write");
    fputc(state,f);fclose(f);
    struct timespec settle={0,150*1000*1000};nanosleep(&settle,0);
}
static int wait_pending(const char *want,int seconds) {
    for(int i=0;i<seconds*20;i++) {
        unsigned staged=0,synced=0;char state[24]={0};
        if(nx_lut_preview_pending_read(&staged,state,sizeof state,&synced)&&!strcmp(state,want))return 1;
        struct timespec pause={0,50*1000*1000};nanosleep(&pause,0);
    }
    return 0;
}
static int pending_is(const char *want,unsigned *staged,unsigned *synced) {
    char state[24]={0};
    int ok=nx_lut_preview_pending_read(staged,state,sizeof state,synced);
    return ok&&!strcmp(state,want);
}
static int chain_syncs(void) {
    FILE *in=fopen("/tmp/w-chain.log","r");if(!in)return 0;
    int n=0;char line[512];
    while(fgets(line,sizeof line,in))if(!strncmp(line,"SYNC env-root=",14))n++;
    fclose(in);return n;
}
static int chain_contains(const char *needle) {
    FILE *in=fopen("/tmp/w-chain.log","r");if(!in)return 0;
    char line[512];int found=0;
    while(fgets(line,sizeof line,in))if(strstr(line,needle))found=1;
    fclose(in);return found;
}
static int journal_has(const char *needle) {
    FILE *in=fopen(NX_LUT_JOURNAL,"r");if(!in)return 0;
    char line[512];int found=0;
    while(fgets(line,sizeof line,in))if(strstr(line,needle))found=1;
    fclose(in);return found;
}
static int journal_count(const char *needle) {
    FILE *in=fopen(NX_LUT_JOURNAL,"r");if(!in)return 0;
    char line[512];int found=0;
    while(fgets(line,sizeof line,in))if(strstr(line,needle))found++;
    fclose(in);return found;
}
static void clear_state(void) {
    unlink(NX_LUT_PREVIEW_PENDING);
    unlink("/tmp/w-chain.log");
}
/* Replace the chain stub without consuming the source: hard links on the
 * same tmpfs keep both stub bodies available for later cases. */
static void swap_stub(const char *src) {
    unlink("/tmp/w-runtime/smartfilter-preview.sh");
    need(!link(src,"/tmp/w-runtime/smartfilter-preview.sh"),"stub_swap");
}
'''

HARNESS_BODY = r'''
int main(void) {
    const char *look_a="/perf/looks/ricoh-negative-exact/table.d4lut";
    const char *look_b="/perf/looks/playback-second/table.d4lut";
    char expected_a[256],expected_b[256];

    /* The preview backend marker routes sync decisions; the user preference
     * starts on, exactly like a live SmartFilter session before playback. */
    int fd=open("/var/run/nx300m-preview-backend",O_WRONLY|O_CREAT|O_TRUNC,0600);
    need(fd>=0,"backend_marker");ssize_t w=write(fd,"smartfilter\n",12);(void)w;close(fd);
    need(nx_lut_preview_preference(1),"preview_preference");
    snprintf(expected_a,sizeof expected_a,"%s/looks/ricoh-negative-exact/table.d4lut",nx_lut_data_root());
    snprintf(expected_b,sizeof expected_b,"%s/looks/playback-second/table.d4lut",nx_lut_data_root());

    /* Native state camera: wait for its readiness marker, start in playback. */
    for(int i=0;i<100&&!access("/tmp/w-cam-ready",F_OK);i++){struct timespec s={0,100*1000*1000};nanosleep(&s,NULL);}
    need(!access("/tmp/w-cam-ready",F_OK),"camera_state_ready");
    need(nx_lut_camera_pid_scan()>0,"camera_identity_found");
    set_cam('P');

    /* D1: playback apply returns promptly and records the pending sync. */
    clear_state();
    unsigned long long started=nx_lut_control_ms();
    unsigned g1=0;int rc=nx_lut_stage_with_strength(look_a,100,&g1);
    unsigned long long elapsed=nx_lut_control_ms()-started;
    unsigned staged=0,synced=0;
    int d1=rc==0&&elapsed<2000&&pending_is("pending",&staged,&synced)&&staged==g1&&synced==0&&
        journal_has("preview-pending");
    result("D1_playback_apply_prompt_and_pending",d1,
        "rc 0 well under the 12 s admission wait; pending record and journal written");
    need(g1,"d1_generation");

    /* D2: the stock viewfinder returns; the worker syncs the latest saved
     * request through the real chain entry with the service environment. */
    set_cam('L');
    int d2=wait_pending("synced",30)&&pending_is("synced",&staged,&synced)&&synced==g1&&
        chain_syncs()==1&&chain_contains(expected_a)&&
        chain_contains("env-root=/tmp/w-runtime")&&chain_contains("env-state=/tmp/w-state")&&
        journal_has("preview-sync-complete");
    result("D2_liveview_restore_syncs_saved_request",d2,
        "worker synced the saved generation once, with runtime root and state dir");

    /* D3: rapid re-selection while the first worker still waits: exactly one
     * sync, of the newest saved request. */
    set_cam('P');
    clear_state();
    int completed_before=journal_count("preview-sync-complete");
    unsigned ga=0,gb=0;
    int rc_a=nx_lut_stage_with_strength(look_a,100,&ga);
    int rc_b=nx_lut_stage_with_strength(look_b,100,&gb);
    set_cam('L');
    int d3=rc_a==0&&rc_b==0&&wait_pending("synced",30)&&pending_is("synced",&staged,&synced)&&
        synced==gb&&chain_syncs()==1&&chain_contains(expected_b)&&
        journal_count("preview-sync-complete")==completed_before+1&&journal_has("preview-sync-skipped");
    result("D3_rapid_reselect_converges_on_latest",d3,
        "two deferred applies produced one sync of the newer generation; older worker skipped");
    need(gb>ga,"d3_generations");

    /* D4: the user closes preview while a worker waits: cancelled, no chain. */
    set_cam('P');
    clear_state();
    unsigned g4=0;
    need(nx_lut_stage_with_strength(look_a,100,&g4)==0,"d4_stage");
    need(unlink("/var/run/nx300m-preview-backend")==0,"d4_backend_clear");
    need(nx_lut_preview_preference(0),"d4_preference_off");
    set_cam('L');
    int d4=wait_pending("cancelled",30)&&chain_syncs()==0&&journal_has("preview-cancelled");
    result("D4_preview_closed_while_waiting_cancels",d4,
        "worker cancelled without invoking the chain; preference stays off");
    fd=open("/var/run/nx300m-preview-backend",O_WRONLY|O_CREAT|O_TRUNC,0600);
    need(fd>=0,"backend_restore");w=write(fd,"smartfilter\n",12);(void)w;close(fd);
    need(nx_lut_preview_preference(1),"preference_restore");

    /* D5: the viewfinder never returns: bounded wait, expired - honest RAM
     * state, no failure claim, no chain invocation, no runaway worker. */
    setenv("NX_LUT_DEFERRED_SYNC_WAIT_MS","1200",1);
    set_cam('P');
    clear_state();
    started=nx_lut_control_ms();
    unsigned g5=0;rc=nx_lut_stage_with_strength(look_a,100,&g5);
    elapsed=nx_lut_control_ms()-started;
    int d5=rc==0&&elapsed<2000&&wait_pending("expired",30)&&chain_syncs()==0&&
        journal_has("preview-expired");
    result("D5_never_ready_expires_bounded",d5,
        "apply stayed prompt; worker expired inside its budget without failing the control");
    unsetenv("NX_LUT_DEFERRED_SYNC_WAIT_MS");

    /* D6: a real card save failure: the staged selection and the deferred
     * sync proceed in RAM, the card record stays at its previous value, and
     * the two facts are recorded separately. The store's own lock path is
     * blocked by a directory, which fails even for root (a chmod would not:
     * this namespace runs everything as uid 0). */
    set_cam('P');
    clear_state();
    struct nx_lut_choice before;
    need(nx_lut_choice_read_root(nx_lut_data_root(),&before),"choice_before");
    need(!unlink("/perf/.state/lut-choice.lock"),"store_lock_removed");
    need(!mkdir("/perf/.state/lut-choice.lock",0700),"store_lock_blocked");
    unsigned g6=0;rc=nx_lut_stage_with_strength(look_b,100,&g6);
    need(!rmdir("/perf/.state/lut-choice.lock"),"store_lock_restored");
    struct nx_lut_choice after;
    int recorded=nx_lut_choice_read_root(nx_lut_data_root(),&after);
    int d6=rc==0&&g6>after.generation&&pending_is("pending",&staged,&synced)&&staged==g6&&
        after.slot_generation==before.slot_generation&&recorded;
    result("D6_real_save_failure_separates_facts",d6,
        "rc 0 with an unchanged card record: staged+pending in RAM, save failure not hidden");

    /* D7: liveview already ready: the inline sync is unchanged and the
     * pending record ends synced with the applied generation. */
    set_cam('L');
    clear_state();
    int pending_before=journal_count("preview-pending");
    started=nx_lut_control_ms();
    unsigned g7=0;rc=nx_lut_stage_with_strength(look_a,100,&g7);
    elapsed=nx_lut_control_ms()-started;
    int d7=rc==0&&chain_syncs()==1&&pending_is("synced",&staged,&synced)&&synced==g7&&
        elapsed<2000&&journal_count("preview-pending")==pending_before;
    result("D7_ready_apply_stays_inline",d7,
        "liveview apply still runs the chain synchronously and records synced");

    /* D8: inline chain past its budget: uncertain, never failed. */
    swap_stub("/tmp/w-slow-preview.sh");
    set_cam('L');
    clear_state();
    unsigned g8=0;rc=nx_lut_stage_with_strength(look_a,100,&g8);
    int d8=rc==6&&pending_is("uncertain",&staged,&synced)&&staged==g8;
    result("D8_inline_deadline_records_uncertain",d8,
        "outer deadline leaves the chain to its recovery owner and records uncertainty");
    swap_stub("/tmp/w-fast-preview.sh");

    /* D9: deferred chain past its budget: the worker records uncertainty. */
    swap_stub("/tmp/w-slow-preview.sh");
    set_cam('P');
    clear_state();
    unsigned g9=0;rc=nx_lut_stage_with_strength(look_a,100,&g9);
    set_cam('L');
    int d9=rc==0&&wait_pending("uncertain",30)&&journal_has("preview-uncertain");
    result("D9_deferred_deadline_records_uncertain",d9,
        "worker abandoned the slow chain without killing it and recorded uncertain");
    swap_stub("/tmp/w-fast-preview.sh");

    /* The disable path keeps its synchronous semantics: unchanged rc. */
    clear_state();
    fd=open("/tmp/w-sync-fail",O_WRONLY|O_CREAT|O_TRUNC,0600);need(fd>=0,"sync_fail");close(fd);
    int off=nx_lut_stock();
    unlink("/tmp/w-sync-fail");
    result("D10_disable_keeps_synchronous_failure",off==6,
        "nx_lut_stock still reports the failed sync instead of deferring it");
    return 0;
}
'''

CAMERA_HELPER = r'''
/* State double of the stock camera application for the deferral snapshot:
 * maps the stock state addresses, keeps the named threads, and follows a
 * control file (P=playback, L=liveview). Native host process. */
#define _GNU_SOURCE
#include <fcntl.h>
#include <pthread.h>
#include <stdio.h>
#include <sys/mman.h>
#include <sys/prctl.h>
#include <unistd.h>
static volatile int liveview;
static void *udd_thread(void *arg) {(void)arg;prctl(PR_SET_NAME,"udd_intr_handle");for(;;)usleep(20000);}
static void *videosrc_thread(void *arg) {(void)arg;
    for(;;) {prctl(PR_SET_NAME,liveview?"videosrc":"videosrc-idle");usleep(20000);}}
int main(void) {
    if(mmap((void *)0x4c9000,0x6000,PROT_READ|PROT_WRITE,
            MAP_PRIVATE|MAP_ANONYMOUS|MAP_FIXED,-1,0)==MAP_FAILED)return 2;
    if(mmap((void *)0x240000,0xd000,PROT_READ|PROT_WRITE,
            MAP_PRIVATE|MAP_ANONYMOUS|MAP_FIXED,-1,0)==MAP_FAILED)return 2;
    *(volatile unsigned *)0x4c9c5c=0x24c0b8;
    *(volatile unsigned *)0x4c9f04=0x24c838;
    pthread_t a,b,c;
    if(pthread_create(&a,0,videosrc_thread,0)||pthread_create(&b,0,udd_thread,0)||
       pthread_create(&c,0,udd_thread,0))return 3;
    int fd=open("/tmp/w-cam-ready",O_WRONLY|O_CREAT|O_TRUNC,0600);
    if(fd>=0){dprintf(fd,"%d\n",(int)getpid());close(fd);}
    for(;;) {
        char state='P';
        FILE *f=fopen("/tmp/w-cam-state","r");
        if(f){int got=fgetc(f);if(got=='L'||got=='P')state=(char)got;fclose(f);}
        liveview=state=='L';
        *(volatile unsigned *)0x4c9c64=liveview?0x4c9f04u:0x4da0f0u;
        *(volatile unsigned *)(0x24c838+0x34)=liveview?7u:2u;
        usleep(20000);
    }
}
'''

FAST_STUB = r'''#!/bin/sh
{
  printf 'SYNC env-root=%s env-state=%s source-root=%s source-id=%s\n' \
    "$NX300M_MOD_ROOT" "$NX_STATE_DIR" "$NX300M_MOD_SOURCE_ROOT" "$NX300M_MOD_SOURCE_ID"
  printf 'SYNC selection=%s\n' "$(cat /var/run/nx300m-lut-selection 2>/dev/null || echo none)"
} >> /tmp/w-chain.log
[ -e /tmp/w-sync-fail ] && exit 1
exit 0
'''

SLOW_STUB = r'''#!/bin/sh
{
  printf 'SYNC env-root=%s env-state=%s source-root=%s source-id=%s\n' \
    "$NX300M_MOD_ROOT" "$NX_STATE_DIR" "$NX300M_MOD_SOURCE_ROOT" "$NX300M_MOD_SOURCE_ID"
} >> /tmp/w-chain.log
sleep 2
exit 0
'''

SETUP = r'''
set -eu
runtime=/tmp/w-runtime
mkdir -p "$runtime" /tmp/w-state /tmp/w-cam
mkdir -p /perf
mount -t tmpfs -o rw,mode=1777,size=16m w-perf /perf
mkdir -p /perf/looks/ricoh-negative-exact /perf/looks/playback-second /perf/.state
cp /mnt/d/codex/project/2026-10-19/nx300m-mod/looks/ricoh-negative-exact/table.d4lut \
   /perf/looks/ricoh-negative-exact/table.d4lut
cp /mnt/d/codex/project/2026-10-19/nx300m-mod/looks/ricoh-negative-exact/table.d4lut \
   /perf/looks/playback-second/table.d4lut
cp "$OUTDIR/stub-fast-preview.sh" "$runtime/smartfilter-preview.sh"
chmod 700 "$runtime"/smartfilter-preview.sh
cp "$OUTDIR/stub-fast-preview.sh" /tmp/w-fast-preview.sh
cp "$OUTDIR/stub-slow-preview.sh" /tmp/w-slow-preview.sh
chmod 700 /tmp/w-fast-preview.sh /tmp/w-slow-preview.sh
cp "$OUTDIR/camera-helper" /tmp/w-cam/di-camera-app-nx300
chmod 700 /tmp/w-cam/di-camera-app-nx300
: > /tmp/w-cam-state
/tmp/w-cam/di-camera-app-nx300 >/tmp/w-cam.log 2>&1 &
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    out = args.output_dir.resolve()
    if (out / 'report.json').exists():
        raise RuntimeError('Choose a new output directory; preserve previous evidence')
    out.mkdir(parents=True, exist_ok=True)
    (out / 'harness.c').write_text(HARNESS_HEAD + HARNESS_BODY, encoding='utf-8', newline='\n')
    (out / 'camera-helper.c').write_text(CAMERA_HELPER, encoding='utf-8', newline='\n')
    (out / 'stub-fast-preview.sh').write_text(FAST_STUB, encoding='utf-8', newline='\n')
    (out / 'stub-slow-preview.sh').write_text(SLOW_STUB, encoding='utf-8', newline='\n')
    shell = f'''set -eu
cd {shlex.quote(wsl_path(ROOT))}
tc=/opt/nx300m-toolchain/usr
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
rf=/opt/nx300m-lab/stock-1.15
OUTDIR={shlex.quote(wsl_path(out))}
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=c99 -O1 -Wall -Wextra -Werror -Wno-unused-function -D_GNU_SOURCE -include mod/metol/remote/compat.h -I. \\
 -U_TIME_BITS -D_TIME_BITS=32 -U_FILE_OFFSET_BITS -D_FILE_OFFSET_BITS=64 \\
 -mfloat-abi=soft -fno-pic -no-pie -fno-stack-protector \\
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 \\
 -Wl,-rpath-link,"$rf/lib" \\
 {shlex.quote(wsl_path(out / 'harness.c'))} mod/metol/remote/compat.c work/reference/metol/test_server/filmlab/src/crt0_arm.S \\
 "$rf/lib/libc-2.13.so" -lgcc -o {shlex.quote(wsl_path(out / 'harness.arm'))}
gcc -O2 -Wall {shlex.quote(wsl_path(out / 'camera-helper.c'))} -pthread -o {shlex.quote(wsl_path(out / 'camera-helper'))}
mount --make-rprivate /
mount -t tmpfs -o rw,mode=1777,size=32m w-tmp /tmp
mount -t tmpfs -o rw,mode=1777,size=8m w-var /var/run
{SETUP}
printf '#!/bin/sh\\nprintf "armv7l\\\\n"\\n' > /tmp/w-uname
chmod 700 /tmp/w-uname
mount --bind /tmp/w-uname "$(readlink -f /usr/bin/uname)"
export NX_LUT_SYNC_BUDGET_MS=600 NX_LUT_CONTROL_BUDGET_MS=600
export NX300M_MOD_ROOT="$runtime"
export NX300M_MOD_SOURCE_ROOT=/perf
export NX300M_MOD_SOURCE_ID="$(stat -c '%d:%i' /perf)"
export NX_STATE_DIR=/tmp/w-state
export NX_SF_PROBE_FIXTURE=1
export NX_LUT_CAMERA_PID="$(cat /tmp/w-cam-ready)"
set +e
work/qemu/qemu-arm-static -L "$rf" {shlex.quote(wsl_path(out / 'harness.arm'))}
harness_rc=$?
set -e
echo "$harness_rc" > {shlex.quote(wsl_path(out / 'harness.rc'))}
exit 0
'''
    (out / 'run.sh').write_text(shell, encoding='utf-8', newline='\n')
    done = subprocess.run(['wsl.exe', '-d', 'Debian', '-u', 'root', '-e',
                           'unshare', '--mount', '--pid', '--fork', '--mount-proc', 'sh', '-c', shell],
                          capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=240)
    (out / 'stdout.log').write_text(done.stdout, encoding='utf-8')
    (out / 'stderr.log').write_text(done.stderr, encoding='utf-8')
    if done.returncode or not (out / 'harness.rc').exists():
        report = dict(diagnostic_completed=False, exit_code=done.returncode,
                      failure=(done.stderr or done.stdout)[-3000:])
        (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2
    harness_rc = int((out / 'harness.rc').read_text().strip())
    checks = [json.loads(line.removeprefix('DEFER_RESULT '))
              for line in (out / 'stdout.log').read_text().splitlines()
              if line.startswith('DEFER_RESULT ')]
    stderr_text = (out / 'stderr.log').read_text()
    expected = {
        'D1_playback_apply_prompt_and_pending', 'D2_liveview_restore_syncs_saved_request',
        'D3_rapid_reselect_converges_on_latest', 'D4_preview_closed_while_waiting_cancels',
        'D5_never_ready_expires_bounded', 'D6_real_save_failure_separates_facts',
        'D7_ready_apply_stays_inline', 'D8_inline_deadline_records_uncertain',
        'D9_deferred_deadline_records_uncertain', 'D10_disable_keeps_synchronous_failure',
    }
    report = dict(
        diagnostic_completed=harness_rc == 0,
        exit_code=harness_rc,
        scope=('Real ARM nx_lut_stage_with_strength/nx_lut_sync_preview_ex deferral plus a native state double of the '
               'stock camera snapshot against a /perf fixture data root on private tmpfs; recording stub sync chain; '
               'no ptrace, camera, IPCS or card'),
        engine_sha256=hashlib.sha256((ROOT / 'mod/metol/lut_engine.c').read_bytes()).hexdigest(),
        shared_sha256=hashlib.sha256((ROOT / 'mod/metol/lut_engine_shared.h').read_bytes()).hexdigest(),
        harness_stderr_markers=dict(
            deferred_lines=stderr_text.count('LUT_PREVIEW_DEFERRED'),
            preview_warnings=stderr_text.count('PREVIEW-WARNING'),
            persist_warnings=stderr_text.count('PERSIST-WARNING'),
            preview_uncertain=stderr_text.count('PREVIEW-UNCERTAIN')),
        private_mount_and_pid_namespaces=True,
        budgets=dict(control_ms=600, sync_ms=600, deferred_wait_ms='case D5 uses 1200'),
        checks=checks,
        cases_passed=sum(1 for row in checks if row['contract_passed']),
        cases_failed=sum(1 for row in checks if not row['contract_passed']),
    )
    report['contracts_passed'] = (harness_rc == 0 and report['cases_failed'] == 0 and
                                  len(checks) == len(expected) and
                                  {row['name'] for row in checks} == expected)
    (out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['contracts_passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
