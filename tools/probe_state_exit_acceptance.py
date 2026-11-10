"""Bounded acceptance: Fn save followed by MENU while queued/in flight."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from wsl_paths import wsl_path

spec = importlib.util.spec_from_file_location('state_probe', ROOT / 'tools/probe_lut_review_state_arm.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
out = ROOT / 'outputs/review/2026-11-07-zcode/state-exit'
out.mkdir(exist_ok=True)
if (out / 'report.json').exists():
    raise RuntimeError('Preserve earlier evidence; move state-exit dir first')
prefix = module.SOURCE.split('int main(void) {', 1)[0]
prefix = prefix.replace('static int fail_directory_sync;', 'static int fail_directory_sync;\nstatic int inject_menu;')
prefix = prefix.replace('    for(int n=0;child&&n<1000;n++)', '    if(inject_menu==1)lut_key(XK_Menu);\n    if(inject_menu==2){mfw_menu_root="mfw";lut_key(XK_Menu);mfw_menu_root=NULL;}\n    for(int n=0;child&&n<1000;n++)')
main = r'''
/* Persistence moved to the dual-slot NXTP1 preset store (lut-preset-<id>.0/.1);
 * the old lut-tune-<id> filename no longer exists. Durability of the other
 * look's draft = its preset loads back from either slot. */
static int other_look_safe(void) {
    struct nx_lut_tune t;unsigned s;
    return (!quit&&!mfw_return_requested)||nx_tune_preset_load(root,"look-a",&t,&s);
}
int main(void) {
    init_case();mfw_return_requested=0;mfw_preview=0;
    lut_tune.value[0]=1;need(lut_draft_stash(),"A_saved_as_draft");
    need(lut_select_look(2),"browse_B");lut_tune.value[0]=2;prepare_request(0);
    struct nx_lut_look_draft persisted=engine_draft();
    need(lut_draft_persist_one(&persisted),"engine_persists_B");
    inject_menu=1;
    complete_child(1,0,"STAGED B saved during Fn task\n");
    result("menu_during_fn_inflight_must_not_drop_A",other_look_safe());

    init_case();mfw_return_requested=0;mfw_preview=0;inject_menu=0;
    lut_tune.value[0]=1;need(lut_draft_stash(),"A_saved_as_draft");
    need(lut_select_look(2),"browse_B");lut_tune.value[0]=2;
    confirm_lut(0);need(queue.commit_requested&&lut_request_pending_valid,"Fn_queued");
    lut_key(XK_Menu);
    lut_request_inflight=lut_request_pending;lut_request_inflight_valid=1;
    lut_request_pending_valid=0;queue.commit_requested=0;queue.committing=1;
    queue.running=selected;queue.desired=-1;
    persisted=engine_draft();need(lut_draft_persist_one(&persisted),"engine_persists_B");
    complete_child(1,0,"STAGED B saved after queued Fn task\n");
    result("menu_during_fn_queued_must_not_drop_A",other_look_safe());

    init_case();mfw_return_requested=0;mfw_preview=0;inject_menu=0;
    lut_tune.value[0]=1;need(lut_draft_stash(),"A_saved_as_draft");
    need(lut_select_look(2),"browse_B");lut_tune.value[0]=2;prepare_request(0);
    persisted=engine_draft();need(lut_draft_persist_one(&persisted),"engine_persists_B");
    inject_menu=2; /* MENU with a menu-root parent: leave target 2 */
    complete_child(1,0,"STAGED B saved before target2 leave\n");
    result("menu_target2_flushes_then_returns",other_look_safe()&&mfw_return_requested);

    init_case();mfw_return_requested=0;mfw_preview=0;inject_menu=0;
    lut_tune.value[0]=1;need(lut_draft_stash(),"A_saved_as_draft");
    need(lut_select_look(2),"browse_B");lut_tune.value[0]=2;confirm_lut(0);
    need(queue.commit_requested&&lut_request_pending_valid,"Fn_queued_plain");
    lut_request_inflight=lut_request_pending;lut_request_inflight_valid=1;
    lut_request_pending_valid=0;queue.commit_requested=0;queue.committing=1;
    queue.running=selected;queue.desired=-1;
    persisted=engine_draft();need(lut_draft_persist_one(&persisted),"engine_persists_B");
    complete_child(1,0,"STAGED B saved, no MENU\n");
    result("fn_without_menu_keeps_panel_and_other_draft",!quit&&!mfw_return_requested&&lut_draft_slot("look-a")>=0);
    return 0;
}
'''
(out / 'probe.c').write_text((prefix + main).replace('@NXUI@', wsl_path(ROOT / 'mod/metol/nxui.c')), encoding='utf-8')
old_dir = ROOT / 'outputs/review/2026-11-05-handoff/state-baseline-v3'
shell = (old_dir / 'run.sh').read_text().replace(wsl_path(old_dir), wsl_path(out))
(out / 'run.sh').write_text(shell, encoding='utf-8', newline='\n')
done = subprocess.run(['wsl.exe','-u','root','-e','sh','-c',shell], capture_output=True, text=True, encoding='utf-8', errors='replace')
(out / 'stdout.log').write_text(done.stdout, encoding='utf-8')
(out / 'stderr.log').write_text(done.stderr, encoding='utf-8')
checks = [json.loads(line.removeprefix('REVIEW_RESULT ')) for line in done.stdout.splitlines() if line.startswith('REVIEW_RESULT ')]
report = dict(diagnostic_completed=done.returncode == 0 and len(checks) == 4,
              exit_code=done.returncode, checks=checks,
              contracts_passed=len(checks) == 4 and all(row['contract_passed'] for row in checks),
              scope='Production ARM UI: MENU target1/2 during queued/inflight Fn task flush-then-exit; Fn-alone control; no ISP',
              source_sha256=hashlib.sha256((ROOT / 'mod/metol/nxui.c').read_bytes()).hexdigest())
(out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps(report, ensure_ascii=False, indent=2))
if done.returncode:
    print(done.stderr[-2500:])
sys.exit(done.returncode or (0 if report['diagnostic_completed'] else 2))
