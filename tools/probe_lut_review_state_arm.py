"""Run the production ARM UI completion logic against finite child-result fixtures.

No X server, camera, SD card, firmware write, network or ISP transaction is used.
Contracts deliberately fail on known review findings; JSON separates completion
of the diagnostic from correctness of the current implementation.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys
from wsl_paths import wsl_path

ROOT=Path(__file__).resolve().parents[1]
SOURCE=r'''
#define fsync nx_probe_fsync
#define main nxui_program_main
#include "@NXUI@"
#undef main
#undef fsync
extern int fsync(int);
static int fail_directory_sync;
int nx_probe_fsync(int fd) {
    if(fail_directory_sync&&(fcntl(fd,F_GETFL)&O_DIRECTORY)){errno=EIO;return -1;}
    return fsync(fd);
}
static void need(int ok,const char *name) {if(!ok){fprintf(stderr,"HARNESS_ERROR %s\n",name);exit(2);}}
static void init_case(void) {
    char path[]="/tmp/nx-review-state-XXXXXX";
    char *fresh=mkdtemp(path);need(fresh!=NULL,"mkdtemp");root=strdup(fresh);
    memset(lut_look_drafts,0,sizeof lut_look_drafts);lut_look_draft_count=0;
    memset(&lut_tune,0,sizeof lut_tune);memset(&lut_base_tune,0,sizeof lut_base_tune);
    memset(mfw_fw_retry,0,sizeof mfw_fw_retry);
    lut_strength=lut_base_strength=100;selected=1;lut_mode=1;count=3;
    snprintf(recipes[1].key,sizeof recipes[1].key,"look-a");
    snprintf(recipes[2].key,sizeof recipes[2].key,"look-b");
    snprintf(recipes[1].label,sizeof recipes[1].label,"A");
    snprintf(recipes[2].label,sizeof recipes[2].label,"B");
    nx_queue_init(&queue);lut_request_pending_valid=lut_request_inflight_valid=0;
    lut_submit_failed=quit=queue.close_requested=0;child=0;child_fd=-1;
    fail_directory_sync=0;panel_input.unmapped=0;panel_yielded=0;
    mfw_retired_child=0;mfw_menu_root=NULL;
}
static void complete_child(int kind,int rc,const char *payload) {
    int pipefd[2];need(!pipe(pipefd),"pipe");
    child=fork();need(child>=0,"fork");
    if(!child){close(pipefd[0]);need(write(pipefd[1],payload,strlen(payload))==(ssize_t)strlen(payload),"write");close(pipefd[1]);_exit(rc);}
    close(pipefd[1]);child_fd=pipefd[0];fcntl(child_fd,F_SETFL,O_NONBLOCK);
    busy_kind=kind;busy_recipe=selected;child_timed_out=output_len=0;output[0]=0;
    child_started=now_ms();mfw_child_readonly=mfw_child_preview=0;
    for(int n=0;child&&n<1000;n++){poll_child();if(child){struct timespec pause={0,1000000};nanosleep(&pause,NULL);}}
    need(!child,"child_completed");
}
static void prepare_request(int target) {
    confirm_lut(target);need(lut_request_pending_valid,"request_created");
    lut_request_inflight=lut_request_pending;lut_request_inflight_valid=1;
    lut_request_pending_valid=0;queue.commit_requested=0;queue.committing=1;
    queue.running=selected;queue.desired=-1;
}
static struct nx_lut_look_draft engine_draft(void) {
    struct nx_lut_look_draft draft={0};
    snprintf(draft.key,sizeof draft.key,"%s",lut_request_inflight.key);
    draft.tune=lut_request_inflight.tune;draft.strength=lut_request_inflight.strength;
    return draft;
}
static void result(const char *name,int ok) {
    printf("REVIEW_RESULT {\"name\":\"%s\",\"contract_passed\":%s,\"quit\":%d,\"drafts_remaining\":%d,\"submit_failed\":%d,\"retry_cached\":%d}\n",
        name,ok?"true":"false",quit,lut_look_draft_count,lut_submit_failed,mfw_fw_retry[0][0]!=0);
}
int main(void) {
    init_case();lut_tune.value[0]=1;need(lut_draft_stash(),"stash_A");
    need(lut_select_look(2),"browse_B");lut_tune.value[0]=2;prepare_request(1);
    struct nx_lut_look_draft persisted=engine_draft();
    need(lut_draft_persist_one(&persisted),"persist_B");
    complete_child(1,0,"STAGED fixture-current-only\n");
    struct nx_lut_tune a_saved={{0}};unsigned a_strength;
    result("direct_save_must_not_exit_with_unsaved_other_look",!quit||
        (nx_tune_load(root,"look-a",&a_saved,&a_strength)&&a_saved.value[0]==1));

    init_case();lut_tune.value[0]=3;prepare_request(1);
    fail_directory_sync=1;persisted=engine_draft();
    need(!lut_draft_persist_one(&persisted)&&persisted.durability_pending,"unconfirmed_directory_sync");
    fail_directory_sync=0;
    complete_child(1,0,"PERSIST-UNCONFIRMED per-look tuning written but directory sync failed\n");
    result("unconfirmed_persistence_must_keep_draft_and_stay",!quit&&lut_draft_slot("look-a")>=0);

    init_case();lut_tune.value[0]=3;prepare_request(1);
    complete_child(1,0,"PERSIST-WARNING per-look tuning was not saved\n");
    result("warning_control_keeps_draft_and_stays",!quit&&lut_draft_slot("look-a")>=0);

    init_case();lut_tune.value[0]=3;prepare_request(1);
    complete_child(1,1,"REJECT simulated engine failure\n");
    result("failed_child_control_keeps_draft_and_stays",!quit&&lut_draft_slot("look-a")>=0);

    init_case();lut_mode=0;snprintf(mfw_fw_action,sizeof mfw_fw_action,"fw-mode-lock-on");
    complete_child(5,75,"filesystem diagnostic before result\nFW_MOD PERSIST-WARNING: applied, not saved\n");
    result("applied_unsaved_marker_survives_prior_stderr",mfw_fw_retry[0][0]!=0);

    /* H2 acceptance: the REAL production engine, built with a wrapper that
     * fails only /.state directory fsyncs, produced the payload and exit code
     * below; the UI completion logic must keep the draft and stay. */
    init_case();lut_tune.value[0]=3;prepare_request(1);
    complete_child(1,@ENGINE_RC@,@ENGINE_OUTPUT@);
    result("real_engine_dirsync_failure_keeps_draft",!quit&&lut_draft_slot("look-a")>=0&&lut_submit_failed!=0);

    /* H5: an unconfirmed favorites write records its expected state; a second
     * tap retries the SAME value instead of toggling back, and a confirmed
     * retry clears the pending record. */
    init_case();live=1;lut_favorite[1]=0;
    fail_directory_sync=1;
    lut_toggle_favorite();
    int pending_set=!strcmp(lut_favorite_pending_key,"look-a")&&lut_favorite_pending_add==1&&lut_favorite[1]==1;
    lut_toggle_favorite();
    int same_value_retried=!strcmp(lut_favorite_pending_key,"look-a")&&lut_favorite_pending_add==1&&lut_favorite[1]==1;
    fail_directory_sync=0;
    lut_toggle_favorite();
    result("favorite_unconfirmed_same_value_retry",pending_set&&same_value_retried&&
        lut_favorite_pending_add<0&&lut_favorite[1]==1);
    return 0;
}
'''
ENGINE_WRAP=r'''
/* Fail only /.state directory fsyncs so table publishing still succeeds and
 * the engine reaches its real per-look persistence code path. The fsync macro
 * must precede every header whose inlines call it; the hook override must
 * follow the shared header that defines the real function. */
#define fsync nx_engine_probe_fsync
#include "mod/metol/lut_engine_shared.h"
#define nx_lut_hook_ready() 1
#define main nx_engine_program_main
#include "mod/metol/lut_engine.c"
#undef main
#undef fsync
extern int fsync(int);
extern int fcntl(int,int,...);
extern ssize_t readlink(const char *,char *,size_t);
int nx_engine_probe_fsync(int fd) {
    char link[64],path[512];
    if(fcntl(fd,F_GETFL)&O_DIRECTORY){
        snprintf(link,sizeof link,"/proc/self/fd/%d",fd);
        ssize_t n=readlink(link,path,sizeof path-1);
        if(n>0){path[n]=0;if(strstr(path,"/.state")){errno=EIO;return -1;}}
    }
    return fsync(fd);
}
int main(int argc,char **argv){return nx_engine_program_main(argc,argv);}
'''

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--require-clean',action='store_true')
    args=parser.parse_args();out=args.output_dir.resolve()
    if (out/'report.json').exists():raise RuntimeError('Choose a new output directory; preserve previous evidence')
    out.mkdir(parents=True,exist_ok=True)
    # One real look's tables feed the real engine's apply path.
    from zipfile import ZipFile
    archive=ROOT/'outputs/looks/lightroom-import/on-camera-test/nx300m-lut-color-test.zip'
    engine_root=out/'engine-root'
    (engine_root/'looks/nx300-hard-mono').mkdir(parents=True,exist_ok=True)
    (engine_root/'.state').mkdir(exist_ok=True)
    with ZipFile(archive) as package:
        (engine_root/'looks/nx300-hard-mono'/'table.d4lut').write_bytes(
            package.read('nx300m-mod/looks/nx300-hard-mono/table.d4lut'))
        try:
            (engine_root/'looks/nx300-hard-mono'/'table-stock3d.d4lut').write_bytes(
                package.read('nx300m-mod/looks/nx300-hard-mono/table-stock3d.d4lut'))
        except KeyError:
            pass  # single-table look; the engine treats composed as absent
    (engine_root/'looks.txt').write_text('nx300-hard-mono|nx300-hard-mono|0|0|0|0|0|0|0\n',encoding='utf-8')
    (out/'engine_wrap.c').write_text(ENGINE_WRAP,encoding='utf-8')
    engine_shell=f'''set -eu
cd {shlex.quote(wsl_path(ROOT))}
tc=/opt/nx300m-toolchain/usr
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
rf=/opt/nx300m-lab/stock-1.15
# Real engine with a /.state-only directory-fsync failure wrapper.
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=c99 -O1 -Wall -Wextra -Werror -D_GNU_SOURCE -include mod/metol/remote/compat.h -I. \\
 -U_TIME_BITS -D_TIME_BITS=32 -U_FILE_OFFSET_BITS -D_FILE_OFFSET_BITS=64 \\
 -mfloat-abi=soft -fno-pic -no-pie -fno-stack-protector \\
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 \\
 -Wl,-rpath-link,"$rf/lib" \\
 {shlex.quote(wsl_path(out/'engine_wrap.c'))} mod/metol/remote/compat.c work/reference/metol/test_server/filmlab/src/crt0_arm.S \\
 "$rf/lib/libc-2.13.so" -lgcc -o {shlex.quote(wsl_path(out/'engine-eio.arm'))}
mount -t tmpfs nx-review-eio /var/run
trap 'umount /var/run 2>/dev/null || :' EXIT
set +e
NX300M_MOD_ROOT={shlex.quote(wsl_path(engine_root))} work/qemu/qemu-arm-static -L "$rf" {shlex.quote(wsl_path(out/'engine-eio.arm'))} apply nx300-hard-mono 100 3 0 0 0 0 0 0 > {shlex.quote(wsl_path(out/'engine-real.out'))} 2> {shlex.quote(wsl_path(out/'engine-real.err'))}
engine_rc=$?
set -e
echo "$engine_rc" > {shlex.quote(wsl_path(out/'engine-real.rc'))}
umount /var/run
trap - EXIT
exit 0
'''
    engine_run=subprocess.run(['wsl.exe','-u','root','-e','sh','-c',engine_shell],capture_output=True,text=True,encoding='utf-8',errors='replace')
    (out/'engine-build.log').write_text(engine_run.stdout+engine_run.stderr,encoding='utf-8')
    if engine_run.returncode or not (out/'engine-real.rc').exists():
        raise RuntimeError('Real engine failure-injection run failed: '+(engine_run.stderr or engine_run.stdout)[-2000:])
    engine_rc=int((out/'engine-real.rc').read_text().strip())
    payload=(out/'engine-real.out').read_text(errors='replace')+'\n'+(out/'engine-real.err').read_text(errors='replace')
    if 'PERSIST-UNCONFIRMED' not in payload:
        raise RuntimeError('Real engine did not emit PERSIST-UNCONFIRMED under /.state dirsync failure: '+payload[-800:])
    import json as _json
    literal=_json.dumps(payload)
    (out/'probe.c').write_text(SOURCE.replace('@NXUI@',wsl_path(ROOT/'mod/metol/nxui.c'))
                               .replace('@ENGINE_RC@',str(engine_rc)).replace('@ENGINE_OUTPUT@',literal),encoding='utf-8')
    shell=f'''set -eu
cd {shlex.quote(wsl_path(ROOT))}
tc=/opt/nx300m-toolchain/usr
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
rf=/opt/nx300m-lab/stock-1.15
ref=work/reference/metol/test_server/nxui
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" -std=c99 -O2 -Wall -Wextra -Wno-unused-function -Wno-unused-parameter -I"$tc/include" -I"$tc/include/freetype2" -I"$ref" -U_TIME_BITS -D_TIME_BITS=32 -U_FILE_OFFSET_BITS -D_FILE_OFFSET_BITS=32 -march=armv7-a -mfpu=vfpv3-d16 -mfloat-abi=softfp -fno-pic -no-pie -fno-stack-protector -ffunction-sections -fdata-sections -Wl,--gc-sections -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 -Wl,--allow-shlib-undefined -Wl,-rpath-link,"$rf/usr/lib" -Wl,-rpath-link,"$rf/lib" {shlex.quote(wsl_path(out/'probe.c'))} "$ref/nxui_model.c" "$ref/nxui_services.c" work/reference/metol/test_server/filmlab/src/crt0_arm.S "$rf/usr/lib/libX11.so.6" "$rf/usr/lib/libXtst.so.6" "$rf/usr/lib/libfreetype.so.6" "$rf/lib/libc-2.13.so" "$rf/lib/librt.so.1" -lgcc -o {shlex.quote(wsl_path(out/'probe.arm'))}
work/qemu/qemu-arm-static -L "$rf" {shlex.quote(wsl_path(out/'probe.arm'))}
'''
    (out/'run.sh').write_text(shell,encoding='utf-8',newline='\n')
    done=subprocess.run(['wsl.exe','-u','root','-e','sh','-c',shell],capture_output=True,text=True,encoding='utf-8',errors='replace')
    (out/'stdout.log').write_text(done.stdout,encoding='utf-8');(out/'stderr.log').write_text(done.stderr,encoding='utf-8')
    checks=[json.loads(line.removeprefix('REVIEW_RESULT ')) for line in done.stdout.splitlines() if line.startswith('REVIEW_RESULT ')]
    expected_checks=7
    report=dict(diagnostic_completed=done.returncode==0 and len(checks)==expected_checks,exit_code=done.returncode,
        scope='Real production ARM nxui completion logic; sixth check feeds a REAL engine dirsync-failure run into the UI; seventh covers favorites unconfirmed retry; no X11 or ISP',
        source_sha256=hashlib.sha256((ROOT/'mod/metol/nxui.c').read_bytes()).hexdigest(),checks=checks,
        contracts_passed=all(row['contract_passed'] for row in checks) and len(checks)==expected_checks,
        real_engine_exit_code=engine_rc,
        observed_contract_failures=sum(not row['contract_passed'] for row in checks))
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    if not report['diagnostic_completed']:print(done.stderr[-4000:]);return done.returncode or 2
    return int(args.require_clean and not report['contracts_passed'])

if __name__=='__main__':sys.exit(main())
