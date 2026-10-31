/* Shared panel launcher. The passive evdev helper recognizes Fn hold/release;
 * no key grab, synthetic stock input or periodic X keymap query is needed. */
#include "../mod_paths.h"
#include <sys/prctl.h>
#include "fn_log.h"
static struct {
    char launcher[PATH_MAX],disabled[PATH_MAX],no_shortcut[PATH_MAX];
    pid_t pid;
    int enabled;
} panel_shortcut;

static void panel_shortcut_init(void) {
    const char *opt_in=getenv("NX_REMOTE_PANEL_SHORTCUT");
    const char *root=getenv("NX300M_MOD_ROOT");
    struct stat state;
    fn_log("panel shortcut init root=%s opt_in=%s", root?root:"(unset)", opt_in?opt_in:"(unset)");
    if (!nx_mod_root_valid(root)) { fn_log("panel shortcut disabled: invalid mod root"); return; }
    snprintf(panel_shortcut.launcher,sizeof panel_shortcut.launcher,"%s/mfw-panel.sh",root);
    snprintf(panel_shortcut.disabled,sizeof panel_shortcut.disabled,"%s/DISABLED",root);
    snprintf(panel_shortcut.no_shortcut,sizeof panel_shortcut.no_shortcut,"%s/NO_PANEL_SHORTCUT",root);
    if (stat(panel_shortcut.launcher,&state) || !S_ISREG(state.st_mode) || access(panel_shortcut.launcher,R_OK)) {
        fn_log("panel shortcut disabled: launcher missing path=%s",panel_shortcut.launcher);
        return;
    }
    panel_shortcut.enabled=opt_in && !strcmp(opt_in,"1");
    if (panel_shortcut.enabled)
        fputs("NX_PANEL_SHORTCUT passive Fn hold 1200ms, launch on release\n",stderr);
    fn_log("panel shortcut enabled=%d launcher=%s",panel_shortcut.enabled,panel_shortcut.launcher);
}
static void fn_log_exit(const char *stage) {
    fn_log("daemon exit stage=%s",stage);
}
static int panel_shortcut_allowed(void) {
    return panel_shortcut.enabled && !media_paused && !stopping &&
        access(panel_shortcut.disabled,F_OK) && access(panel_shortcut.no_shortcut,F_OK);
}
static void custom_key_launch_panel(long long triggered_at) {
    if (media_paused || stopping) { fn_log("panel launch skipped: media_paused=%d stopping=%d",media_paused,stopping); return; }
    if (panel_shortcut.pid) { fn_log("panel launch skipped: previous panel pid=%ld still running",(long)panel_shortcut.pid); return; }
    if (!panel_shortcut.launcher[0] || !access(panel_shortcut.disabled,F_OK) || !access(panel_shortcut.no_shortcut,F_OK)) {
        fn_log("panel launch skipped: launcher=%s disabled=%d no_shortcut=%d",
            panel_shortcut.launcher[0]?"set":"empty",
            panel_shortcut.disabled[0]?!access(panel_shortcut.disabled,F_OK):-1,
            panel_shortcut.no_shortcut[0]?!access(panel_shortcut.no_shortcut,F_OK):-1);
        return;
    }
    long long fork_started=now_ms();
    pid_t parent=getpid(),pid=fork();
    if (pid<0) { fprintf(stderr,"NX_PANEL_START failed errno=%d\n",errno);fn_log("panel fork failed errno=%d",errno);return; }
    if (!pid) {
        /* The shell execs nxui, so the death signal also covers an open panel.
         * Check PPID after prctl to close the fork/parent-exit race. */
        signal(SIGINT,SIG_DFL);signal(SIGTERM,SIG_DFL);signal(SIGCHLD,SIG_DFL);
        if (prctl(PR_SET_PDEATHSIG,SIGTERM) || getppid()!=parent) _exit(125);
        close_inherited_fds();
        char trigger_value[32],fork_value[32];
        snprintf(trigger_value,sizeof trigger_value,"%lld",triggered_at);
        snprintf(fork_value,sizeof fork_value,"%lld",fork_started);
        if (setenv("NX_PANEL_TRIGGER_MS",trigger_value,1) || setenv("NX_PANEL_FORK_MS",fork_value,1))
            fprintf(stderr,"NX_PANEL_TIMING_ENV failed errno=%d\n",errno);
        long long exec_at=now_ms();
        fprintf(stderr,"NX_PANEL_EXEC pid=%ld trigger_ms=%lld fork_ms=%lld exec_ms=%lld elapsed_ms=%lld script=%s\n",
                (long)getpid(),triggered_at,fork_started,exec_at,exec_at-triggered_at,panel_shortcut.launcher);
        fn_log("panel exec pid=%ld elapsed_ms=%lld script=%s",(long)getpid(),exec_at-triggered_at,panel_shortcut.launcher);
        execl("/bin/sh","sh",panel_shortcut.launcher,(char *)NULL);
        fprintf(stderr,"NX_PANEL_EXEC failed errno=%d\n",errno);
        fn_log("panel exec failed errno=%d script=%s",errno,panel_shortcut.launcher);
        _exit(127);
    }
    panel_shortcut.pid=pid;
    long long fork_finished=now_ms();
    fprintf(stderr,"NX_PANEL_START pid=%ld trigger_ms=%lld fork_ms=%lld fork_elapsed_ms=%lld elapsed_ms=%lld\n",
            (long)pid,triggered_at,fork_started,fork_finished-fork_started,fork_finished-triggered_at);
    fn_log("panel forked pid=%ld elapsed_ms=%lld",(long)pid,fork_finished-triggered_at);
}
static void panel_shortcut_poll(void) {
    if (panel_shortcut.pid) {
        int status;
        pid_t result=waitpid(panel_shortcut.pid,&status,WNOHANG);
        if (result==panel_shortcut.pid || (result<0 && errno==ECHILD)) {
            if (result>0) {
                fprintf(stderr,"NX_PANEL_EXIT pid=%ld status=%d\n",(long)result,status);
                fn_log("panel exited pid=%ld status=%d",(long)result,status);
            } else fn_log("panel pid vanished without exit status");
            panel_shortcut.pid=0;
        }
    }
}
