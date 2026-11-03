/* Production engine actions and live RAM validation, with an explicit VM
 * process identity. This helper is never included in an SD runtime package. */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#define nx_lut_hook_ready nx_lut_production_identity_gate
#include "../mod/metol/lut_engine_shared.h"
#undef nx_lut_hook_ready
static int nx_lut_hook_ready(void) {
    const char *text=getenv("NX_LUT_VM_PID");unsigned pid;
    char proc[64],exe[256],root[PATH_MAX];
    if(!text||!nx_lut_uint(text,&pid)||!pid||pid>INT_MAX||!nx_lut_root(root)) return 0;
    snprintf(proc,sizeof proc,"/proc/%u/exe",pid);
    ssize_t n=readlink(proc,exe,sizeof exe-1);if(n<0) return 0;exe[n]=0;
    if(strcmp(exe,"/mnt/mmc/bin/smartfilter_fixture.arm")) return 0;
    return nx_lut_ram_hook_ready((int)pid,(int)pid,root);
}
#define main nx_lut_engine_main
#include "../mod/metol/lut_engine.c"
#undef main
int main(int argc,char **argv) {return nx_lut_engine_main(argc,argv);}
