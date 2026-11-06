"""Finite ARM regression probe for Fn preset-only save and actual LUT entry.

Includes the production UI. Only the two RAM selection paths are redirected
to private fixture files; no shared /var/run mounts or camera access.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
from wsl_paths import wsl_path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = r'''
#define fopen nx_probe_fopen
#define fsync nx_probe_fsync
#define main nxui_program_main
#include "@NXUI@"
#undef main
#undef fsync
#undef fopen
extern FILE *fopen(const char *,const char *);
extern int fsync(int);
static int fail_sync;
FILE *nx_probe_fopen(const char *path,const char *mode) {
    char fixture[1024];
    if(!strncmp(path,"/var/run/nx300m-lut-selection",28)) {
        snprintf(fixture,sizeof fixture,"%s/%s",root,strrchr(path,'/')+1);path=fixture;
    }
    return fopen(path,mode);
}
int nx_probe_fsync(int fd) {
    if(fail_sync&&(fcntl(fd,F_GETFL)&O_DIRECTORY)){errno=EIO;return -1;}
    return fsync(fd);
}
static void need(int ok,const char *why){if(!ok){fprintf(stderr,"FIXTURE %s\n",why);exit(2);}}
static void put(const char *name,const char *text){
    char p[1024];snprintf(p,sizeof p,"%s/%s",root,name);FILE *f=fopen(p,"w");
    need(f!=NULL,"create");need(fputs(text,f)>=0&&!fclose(f),"write");
}
static void init_case(void){
    char path[]="/tmp/nx-entry-save-XXXXXX";root=strdup(mkdtemp(path));need(root!=NULL,"mkdtemp");
    char d[1024];snprintf(d,sizeof d,"%s/.state",root);need(!mkdir(d,0700),"mkdir");
    memset(recipes,0,sizeof recipes);count=3;selected=1;lut_mode=1;
    strcpy(recipes[0].key,"__off");strcpy(recipes[1].key,"nx300-fuji-test");strcpy(recipes[2].key,"nx300-hard-mono");
    strcpy(recipes[1].label,"Fuji");strcpy(recipes[2].label,"Mono");
    memset(&lut_tune,0,sizeof lut_tune);memset(&lut_base_tune,0,sizeof lut_base_tune);
    lut_strength=lut_base_strength=100;lut_origin_selected=1;lut_origin_strength=100;lut_origin_tune=lut_tune;
    lut_look_draft_count=0;lut_draft=0;lut_leave_dialog=lut_submit_failed=0;lut_list_open=0;
    lut_toolbar_focus=-1;lut_category_focus=0;live=0;
    lut_request_pending_valid=lut_request_inflight_valid=0;child=0;quit=0;fail_sync=0;
    nx_queue_init(&queue);
    put("looks.txt","nx300-fuji-test|Fuji|0|0|0|0|0|0|0\nnx300-hard-mono|Mono|0|0|0|0|0|0|0\n");
}
static void result(const char *name,int ok){printf("CHECK {\"name\":\"%s\",\"passed\":%s}\n",name,ok?"true":"false");}
static void enter(void){char path[1024];snprintf(path,sizeof path,"%s/looks.txt",root);need(load_lut_choices(path),"entry");}
static void current(const char *id){
    char text[1024];snprintf(text,sizeof text,"7 %s/looks/%s/table.d4lut 100\n",root,id);
    put("nx300m-lut-selection",text);snprintf(text,sizeof text,"%s\n",root);put("nx300m-lut-selection-session",text);
}
int main(void){
    init_case();lut_tune.value[0]=3;lut_toggle_view();
    struct nx_lut_tune disk={{0}};unsigned strength=0;
    result("fn_saves_current_without_apply_or_exit",lut_list_open&&!quit&&!child&&!queue.commit_requested&&
        !lut_request_pending_valid&&!queue.close_requested&&lut_origin_tune.value[0]==0&&
        nx_tune_load(root,recipes[1].key,&disk,&strength)&&disk.value[0]==3&&!lut_current_dirty()&&!lut_matches_applied());
    lut_look_draft_count=0;memset(&lut_tune,0,sizeof lut_tune);current("nx300-fuji-test");enter();
    result("fn_saved_preset_survives_fresh_entry",selected==1&&lut_tune.value[0]==3&&
        nx_tune_zero(&lut_origin_tune)&&!lut_current_dirty()&&!lut_matches_applied());

    init_case();lut_tune.value[0]=2;need(lut_draft_stash(),"stash_A");need(lut_select_look(2),"select_B");
    lut_tune.value[0]=4;lut_list_open=0;lut_toggle_view();
    result("fn_preserves_other_look_draft",lut_look_draft_count==1&&lut_draft_slot("nx300-fuji-test")>=0&&
        nx_tune_load(root,recipes[2].key,&disk,&strength)&&disk.value[0]==4&&!queue.commit_requested);

    init_case();lut_tune.value[0]=5;fail_sync=1;lut_toggle_view();
    result("fn_unconfirmed_stays_for_retry",!lut_list_open&&lut_submit_failed&&lut_draft_slot(recipes[1].key)>=0&&!queue.commit_requested);
    fail_sync=0;lut_toggle_view();
    result("fn_identical_retry_is_confirmed",lut_list_open&&!lut_submit_failed&&!lut_current_dirty()&&!queue.commit_requested);

    init_case();current("nx300-fuji-test");put(".state/lut-category","8\n");
    put(".state/lut-tune-nx300-fuji-test","1 nx300-fuji-test 5 0 0 0 0 0 0 80\n");enter();
    result("saved_preset_visible_active_zero_separate",selected==1&&lut_strength==80&&lut_tune.value[0]==5&&
        lut_origin_selected==1&&lut_origin_strength==100&&nx_tune_zero(&lut_origin_tune)&&!lut_matches_applied());
    result("active_look_visible_despite_stale_group",lut_category==2&&lut_in_group(selected)&&lut_tab_start==-1);

    init_case();current("nx300-hard-mono");put(".state/lut-category","0\n");enter();
    result("compatible_group_and_cursor_restored",selected==2&&lut_category==0&&lut_list_cursor==1);

    init_case();put(".state/lut-category","2\n");put(".state/lut-selection","8 invalid 100\n");
    put("nx300m-lut-selection-session",root);enter();
    result("explicit_runtime_off_never_revives_legacy",selected==0&&lut_origin_selected==0&&lut_category==0);

    init_case();current("nx300-hard-mono");put("nx300m-lut-selection-session","/different-card\n");
    char saved[1024];snprintf(saved,sizeof saved,"9 %s/looks/nx300-fuji-test/table.d4lut 100\n",root);
    put(".state/lut-choice",saved);enter();
    result("other_session_not_claimed_as_active",selected==1&&lut_saved_config&&lut_origin_selected==-1&&!lut_matches_applied());

    init_case();struct nx_lut_tune first={{3,0,0,0,0,0,0}},second={{6,0,0,0,0,0,0}};
    need(nx_tune_preset_save(root,recipes[1].key,&first,70)==1,"first_slot");
    need(nx_tune_preset_save(root,recipes[1].key,&second,60)==1,"second_slot");
    result("fixed_slots_latest_generation_restored",nx_tune_load(root,recipes[1].key,&disk,&strength)&&disk.value[0]==6&&strength==60);
    put(".state/lut-preset-nx300-fuji-test.1","NXTP1 2 00000000 1 nx300-fuji-test 6 0 0 0 0 0 0 60\n");
    result("torn_newest_slot_recovers_previous",nx_tune_load(root,recipes[1].key,&disk,&strength)&&disk.value[0]==3&&strength==70);
    need(nx_tune_preset_save(root,recipes[1].key,&second,60)==1,"retry_corrupt_slot");
    result("corrupt_slot_retry_restores_latest",nx_tune_load(root,recipes[1].key,&disk,&strength)&&disk.value[0]==6&&strength==60);

    init_case();put("protected","original\n");char target[1024],link[1024];
    snprintf(target,sizeof target,"%s/protected",root);snprintf(link,sizeof link,"%s/.state/lut-preset-nx300-fuji-test.0",root);
    need(!symlink(target,link),"symlink_fixture");
    result("slot_symlink_rejected",nx_tune_preset_save(root,recipes[1].key,&first,70)==0);

    init_case();lut_init_groups();lut_category=0;lut_list_open=1;lut_list_cursor=0;
    lut_key(XK_Up);lut_key(XK_Up);
    result("buttons_reach_preview_toolbar",lut_toolbar_focus==1&&!lut_category_focus&&!queue.commit_requested);
    lut_key(XF86XK_ScrollDown);lut_key(XK_Return);
    result("wheel_and_ok_disable_lut",lut_request_pending_valid&&lut_request_pending.is_off&&lut_request_pending.exit_after==1);
    return 0;
}
'''

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args();out=args.output_dir.resolve();out.mkdir(parents=True,exist_ok=True)
    if (out/'report.json').exists():raise RuntimeError('Preserve existing results; choose a new output')
    (out/'probe.c').write_text(SOURCE.replace('@NXUI@',wsl_path(ROOT/'mod/metol/nxui.c')),encoding='utf-8')
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
    p=subprocess.run(['wsl.exe','-u','root','-e','sh','-c',shell],capture_output=True,text=True,encoding='utf-8',errors='replace')
    (out/'stdout.log').write_text(p.stdout,encoding='utf-8');(out/'stderr.log').write_text(p.stderr,encoding='utf-8')
    checks=[json.loads(s[6:]) for s in p.stdout.splitlines() if s.startswith('CHECK ')]
    report=dict(scope='Production ARM UI logic; redirected selection files; no X server/ISP/card',exit_code=p.returncode,
        checks=checks,passed=p.returncode==0 and len(checks)==16 and all(c['passed'] for c in checks),
        source_sha256={s:hashlib.sha256((ROOT/s).read_bytes()).hexdigest() for s in ('mod/metol/nxui.c','mod/metol/nxui_lut_panel.h','mod/metol/lut_tune.h','mod/metol/lut_tune_store.h')})
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    if not report['passed']:print(p.stderr[-2500:])
    return 0 if report['passed'] else 1

if __name__=='__main__':raise SystemExit(main())
