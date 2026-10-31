"""Check the production Custom launcher without a camera or camera app.

Compile only its source-included resolver, launch and entry-check functions on
WSL. Access, fork and exec are fixtures: this checks path selection, argv,
environment and stock fallback, not installation or physical key delivery.
"""
import subprocess
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]


def main():
    source = (PROJECT / 'runtime/camera_firmware_mod.c').read_text(encoding='utf-8')
    launcher = source[source.index('struct settings_launcher {'):source.index('static int iso_set(')]
    exercise = source[source.index('static int exercise_entry('):source.index('/* Constructors run')]
    install = source[source.index('__attribute__((constructor)) static void install_entries('):source.index('/* eUI_MODE values')]
    install = install.replace('__attribute__((constructor)) ', '')
    harness = r'''
#include <assert.h>
#include <setjmp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static char **fixture_environment;
static const char *readable_panel, *executable_ui;
static int child_result=19, execs, fallbacks, entries;
static unsigned int custom_enabled=1, custom_calls, custom_launches;
static unsigned int patch_mask=8, mode_locked, animation_mode, iso_lock;
static unsigned int entry_diagnostics;
static jmp_buf executed;
static char *fixture_getenv(const char *name) {
    size_t n=strlen(name);
    for(char **p=fixture_environment;*p;p++)
        if(!strncmp(*p,name,n) && (*p)[n]=='=') return *p+n+1;
    return NULL;
}
static int fixture_access(const char *path,int mode) {
    const char *expected=mode==4?readable_panel:executable_ui;
    return !expected || strcmp(path,expected);
}
static void stock(void *event) {(void)event;fallbacks++;}
static void (*stock_custom)(void *)=stock;
static void entry(void *event) {(void)event;entries++;}
static void *dlsym(void *handle,const char *name) {
    (void)handle;assert(!strcmp(name,"custom"));return entry;
}
static int fixture_fork(void) {return child_result;}
static int fixture_open(const char *path,int flags) {
    assert(!strcmp(path,"/dev/null") && flags==0);return 7;
}
static int fixture_dup2(int old,int newfd) {assert(old==7 && newfd==0);return 0;}
static int fixture_close(int fd) {(void)fd;return 0;}
static int fixture_execve(const char *path,char *const args[],char *const env[]) {
    assert(!strcmp(path,"/bin/sh") && !strcmp(args[0],"/bin/sh"));
    assert(!strcmp(args[1],readable_panel) && !strcmp(args[2],"settings") && !args[3]);
    int root=0,search=0,state=0,source=0,identity=0;
    for(unsigned int i=0;env[i];i++) {
        assert(strncmp(env[i],"LD_PRELOAD=",11) && strncmp(env[i],"LD_AUDIT=",9));
        if(!strncmp(env[i],"NX300M_MOD_ROOT=",16)) {root++;assert(!strcmp(env[i],"NX300M_MOD_ROOT=/explicit"));}
        if(!strncmp(env[i],"PATH=",5)) {search++;assert(!strcmp(env[i],"PATH=/bin:/usr/bin:/sbin:/usr/sbin"));}
        state+=!strcmp(env[i],"NX_STATE_DIR=/tmp/existing/state");
        source+=!strcmp(env[i],"NX300M_MOD_SOURCE_ROOT=/mnt/mmc/existing");
        identity+=!strcmp(env[i],"NX300M_MOD_SOURCE_ID=12:34");
    }
    assert(root==1 && search==1 && state==1 && source==1 && identity==1);
    execs++;longjmp(executed,1);return -1;
}
static void fixture_exit(int code) {assert(code!=127);abort();}
#define getenv fixture_getenv
#define access fixture_access
#define environ fixture_environment
#define fork fixture_fork
#define open fixture_open
#define dup2 fixture_dup2
#define close fixture_close
#define execve fixture_execve
#define _exit fixture_exit
#define NX_MODE_EVENT_SYMBOL "mode"
#define NX_MODE_DRAW_SYMBOL "draw"
#define NX_CUSTOM_SYMBOL "custom"
#define NX_ISO_SET_SYMBOL "iso"
'''
    harness += launcher + exercise
    harness += r'''
#define NX_MODE_EVENT_ADDRESS 1
#define NX_ANIM_START_ADDRESS 2
#define NX_MODE_DRAW_ADDRESS 3
#define NX_CUSTOM_ADDRESS 4
#define NX_ISO_SET_ADDRESS 5
#define NX_ANIM_START_SYMBOL "animation"
static unsigned char nx_mode_event_stock[8],nx_anim_start_stock[8],nx_mode_draw_stock[8];
static unsigned char nx_custom_stock[8],nx_iso_set_stock[8];
static void (*stock_mode_event)(void *),(*stock_mode_draw)(void *);
static void (*stock_animation_start)(void *,double);
static int (*stock_iso_set)(void *,int,int);
static void physical_mode_event(void *p) {(void)p;}
static void mode_draw(void *p) {(void)p;}
static void animation_start(void *p,double n) {(void)p;(void)n;}
static int iso_set(void *p,int a,int b) {(void)p;(void)a;(void)b;return 0;}
static int custom_matches=1;
static unsigned int patch_entry(const char *symbol,unsigned int address,
    const unsigned char *expected,unsigned int bytes,void *replacement,void **fallback) {
    (void)address;(void)expected;(void)bytes;(void)replacement;(void)fallback;
    /* Only the Custom patch result varies; installation byte checks remain in
     * the production ARM build, outside this launcher fixture. */
    return strcmp(symbol,NX_CUSTOM_SYMBOL) || custom_matches;
}
'''
    harness += install
    harness += r'''
static void expect(int code,const char *root) {
    struct settings_launcher result;
    assert(resolve_settings_launcher(&result)==code);
    if(!code) assert(!strcmp(result.root,root));
    int before=entries;
    assert(exercise_entry(3)==code);
    assert(entries==before+(!code));
}
int main(void) {
    char *empty[]={NULL};fixture_environment=empty;
    custom_enabled=0;patch_mask=0;install_entries();
    assert(custom_enabled==1 && (patch_mask&8));
    custom_matches=0;custom_enabled=0;patch_mask=0;install_entries();
    assert(!custom_enabled && !(patch_mask&8));
    custom_key(NULL);assert(fallbacks==1);fallbacks=0;
    custom_matches=1;custom_enabled=1;patch_mask=8;
    readable_panel="/mnt/mmc/nx300m-mod/panel.sh";
    executable_ui="/mnt/mmc/nx300m-mod/bin/nxui.arm";
    expect(0,"/mnt/mmc/nx300m-mod");
    executable_ui="/mod/bin/nxui.arm";expect(-111,NULL);
    readable_panel="/mod/panel.sh";expect(0,"/mod");
    char *configured[]={"NX300M_MOD_ROOT=/explicit","PATH=/untrusted",
        "LD_PRELOAD=/old.so","LD_AUDIT=/audit.so","NX_STATE_DIR=/tmp/existing/state",
        "NX300M_MOD_SOURCE_ROOT=/mnt/mmc/existing","NX300M_MOD_SOURCE_ID=12:34",NULL};
    fixture_environment=configured;expect(-111,NULL);
    readable_panel="/explicit/panel.sh";executable_ui="/explicit/bin/nxui.arm";
    expect(0,"/explicit");assert(launch_settings()==19 && custom_launches==1);
    child_result=-1;assert(launch_settings()==-112 && custom_launches==1);
    child_result=0;if(!setjmp(executed)) launch_settings();assert(execs==1);
    executable_ui=NULL;expect(-111,NULL);custom_key(NULL);assert(fallbacks==1);
    readable_panel=NULL;executable_ui="/explicit/bin/nxui.arm";expect(-111,NULL);
    char long_root[600];memset(long_root,'a',sizeof long_root);long_root[599]=0;
    char environment[640];snprintf(environment,sizeof environment,"NX300M_MOD_ROOT=/%s",long_root);
    char *oversized[]={environment,NULL};fixture_environment=oversized;expect(-111,NULL);
    char *mfw[]={"NX300M_MOD_ROOT=/tmp/nx300m-mfw/runtime",
        "NX_STATE_DIR=/tmp/nx300m-mfw/state","NX300M_MOD_SOURCE_ROOT=/mnt/mmc/source",
        "NX300M_MOD_SOURCE_ID=12:34",NULL};fixture_environment=mfw;
    readable_panel="/tmp/nx300m-mfw/runtime/mfw-panel.sh";
    executable_ui="/tmp/nx300m-mfw/runtime/bin/nxui.arm";expect(-111,NULL);
    puts("PASS: Custom defaults only after successful patch; rejected patch falls back; shared preflight, explicit/SD/mod paths, missing assets, bounds, argv, environment, stock fallback; MFW remains unsupported");
    return 0;
}
'''
    output = PROJECT / 'outputs/controls'
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='custom-launcher-', dir=output) as work:
        linux = subprocess.check_output(['wsl', '-d', 'Debian', '--exec', 'wslpath', '-a', Path(work).as_posix()], text=True).strip()
        binary = linux + '/probe'
        subprocess.run(['wsl', '-d', 'Debian', '--', 'gcc', '-std=c99', '-Wall', '-Wextra',
                        '-Werror', '-x', 'c', '-', '-o', binary], input=harness, text=True, check=True)
        subprocess.run(['wsl', '-d', 'Debian', '--', binary], check=True)


if __name__ == '__main__':
    main()
