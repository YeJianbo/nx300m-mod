/* NX300M 1.15 bounded external Wi-Fi condition-byte tool.
 * No ptrace, target calls, signals, SO loading, firmware or snapshot writes.
 * A power cycle or removing the card is NOT a guaranteed rollback.
 * SPDX-License-Identifier: AGPL-3.0-or-later
 */
#define NX300M_LENS_PROBE_NO_START 1
#define probe_main unused_observer_main
#include "manual_lens_probe.c"
#undef probe_main
#include "manual_lens_wifi_stock.h"
extern int geteuid(void);
extern int pwrite64(int, const void *, unsigned int, long long);

static const uint32_t wifi_sites[2] = {
    NX_WIFI_GATE_ADDRESS + NX_WIFI_GATE_OFFSET + 3,
    NX_WIFI_GATE_ADDRESS + NX_WIFI_KMOUNT_OFFSET + 3
};
static const unsigned char wifi_new[2] = {0x9a, 0xea};
static unsigned char before_wifi[2] = {255,255}, after_wifi[2] = {255,255};
static unsigned int writes_attempted, rollback_attempted, rollback_verified;
static unsigned int after_verified;
static int failure_errno, failed_site=-1;
static const char *failure_reason="none";
static char initial_mapping[512];

/* Accept exactly the four original/known-patched combinations. Every other
 * byte in this complete function remains part of the stock comparison. */
static int normalize_wifi(unsigned char *bytes) {
    for (unsigned int i=0;i<2;i++) {
        unsigned int at=wifi_sites[i]-NX_WIFI_GATE_ADDRESS;
        if(bytes[at]!=0x0a && bytes[at]!=wifi_new[i]) return 0;
        bytes[at]=0x0a;
    }
    return !memcmp(bytes,nx_wifi_gate_stock,sizeof nx_wifi_gate_stock);
}

static int verify_wifi_stock(unsigned char state[2]) {
    char path[96]; unsigned char bytes[4096];
    if(!path_for(path,"exe")) return 0;
    int fd=open(path,0);
    if(fd<0) return 0;
    int n=read(fd,bytes,sizeof probe_exe_header); close(fd);
    if(n!=(int)sizeof probe_exe_header || memcmp(bytes,probe_exe_header,sizeof probe_exe_header)) return 0;
    verified_checks=0;
    for(unsigned int i=0;i<ARRAY_SIZE(probe_checks);i++) {
        const struct stock_check *c=probe_checks+i;
        if(c->size>sizeof bytes || biases[c->module]>UINT32_MAX-c->address ||
           !memory(biases[c->module]+c->address,bytes,c->size,(int)c->module)) return 0;
        if(c->module==0 && c->address==NX_WIFI_GATE_ADDRESS) {
            if(c->size!=sizeof nx_wifi_gate_stock) return 0;
            for(unsigned int k=0;k<2;k++) state[k]=bytes[wifi_sites[k]-NX_WIFI_GATE_ADDRESS];
            if(!normalize_wifi(bytes)) return 0;
        } else if(memcmp(bytes,c->bytes,c->size)) return 0;
        verified_checks++;
    }
    return verified_checks==ARRAY_SIZE(probe_checks);
}

static int mapping_identity(char out[512]) {
    static char text[MAP_TEXT_CAP]; char path[96];
    if(!path_for(path,"maps") || text_file(path,text,sizeof text)<0) return 0;
    char *line=text;
    while(*line) {
        char *next=strchr(line,'\n'); if(next) *next++=0;
        unsigned int begin,end,offset,major,minor; unsigned long long inode;
        char perms[5]; int used=0;
        if(sscanf(line,"%x-%x %4s %x %x:%x %llu %n",&begin,&end,perms,&offset,&major,&minor,&inode,&used)==7 &&
           begin<=NX_WIFI_GATE_ADDRESS && NX_WIFI_GATE_ADDRESS+sizeof nx_wifi_gate_stock<=end) {
            const char *name=line+used; while(*name==' ') name++;
            unsigned int n=strlen(line);
            if(!inode || strcmp(perms,"r-xp") || strcmp(name,probe_module_paths[0]) || n>=512) return 0;
            memcpy(out,line,n+1); return 1;
        }
        if(!next) break; line=next;
    }
    return 0;
}

static int current_identity(void) {
    unsigned long long ticks; char mapping[512]; unsigned int original_pid=pid;
    if(!executable_matches(pid) || !process_start(&ticks) || ticks!=start_ticks ||
       !mapping_identity(mapping) || strcmp(mapping,initial_mapping)) return 0;
    process_matches=0; pid=0;
    return find_process() && pid==original_pid;
}

/* The only write syscall in the production tool. Count and addresses cannot
 * be supplied by CLI, metadata, a file, or a target-provided pointer. */
static int write_site(unsigned int site,unsigned char value) {
    if(site>=2 || (value!=0x0a && value!=wifi_new[site])) { *__errno_location()=22; return 0; }
    writes_attempted++;
    int count=pwrite64(mem_fd,&value,1,(long long)wifi_sites[site]);
    if(count!=1 && count>=0) *__errno_location()=5;
    return count==1;
}

struct wifi_ops { int (*verify)(unsigned char state[2]); int (*identity)(void); };
static int wifi_transaction(unsigned int mode,const struct wifi_ops *ops) {
    unsigned char current[2]; unsigned int attempted=0;
    if(!ops->identity() || !ops->verify(before_wifi)) {
        failure_reason="preflight_identity_or_stock_check_failed"; failure_errno=*__errno_location(); return 0;
    }
    memcpy(after_wifi,before_wifi,2);
    after_verified=1;
    if(!mode) {
        if(ops->identity()) return 1;
        after_verified=0; failure_reason="identity_changed"; return 0;
    }
    for(unsigned int i=0;i<2;i++) {
        unsigned char wanted=mode==1?wifi_new[i]:0x0a;
        if(before_wifi[i]==wanted) continue;
        failed_site=(int)i;
        if(!ops->identity() || !ops->verify(current) || memcmp(current,after_wifi,2)) goto failed;
        attempted|=1u<<i;
        if(!write_site(i,wanted)) {
            failure_reason="byte_write_failed_or_short"; failure_errno=*__errno_location(); goto failed;
        }
        after_wifi[i]=wanted;
        if(!ops->verify(current) || memcmp(current,after_wifi,2)) goto failed;
    }
    if(!ops->identity() || !ops->verify(current) || memcmp(current,after_wifi,2)) goto failed;
    failed_site=-1;
    return 1;
failed:
    after_verified=0;
    if(!strcmp(failure_reason,"none")) {
        failure_reason="identity_or_stock_state_changed"; failure_errno=*__errno_location();
    }
    if(!attempted) return 0;
    rollback_attempted=1;
    int restored=1;
    for(unsigned int i=0;i<2;i++) if(attempted&(1u<<i)) {
        if(!ops->identity() || !ops->verify(current) || !write_site(i,before_wifi[i])) restored=0;
    }
    if(ops->identity() && ops->verify(current)) {
        memcpy(after_wifi,current,2);
        after_verified=1;
        rollback_verified=restored && !memcmp(current,before_wifi,2);
    }
    return 0;
}

#ifndef NX_WIFI_MEM_FIXTURE
int wifi_mem_main(unsigned int *initial) {
    unsigned int argc=initial[0], mode=0; char **argv=(char **)(initial+1);
    const char *command="status";
    if(argc>2) { problem(2,"arguments","expected_status_apply_or_restore"); goto output; }
    if(argc==2) {
        command=argv[1];
        if(!strcmp(command,"apply")) mode=1;
        else if(!strcmp(command,"restore")) mode=2;
        else if(strcmp(command,"status")) { command="invalid"; problem(2,"arguments","expected_status_apply_or_restore"); goto output; }
    }
    if(geteuid()!=0) { problem(2,"identity","root_required"); goto output; }
    if(!find_process()) goto output;
    if(!process_start(&start_ticks) || !read_maps() || !mapping_identity(initial_mapping)) {
        problem(2,"identity","unverified_process_or_mapping"); goto output;
    }
    char path[96];
    if(!path_for(path,"mem")) { problem(2,"memory","invalid_process_path"); goto output; }
    mem_fd=open(path,mode?2:0);
    if(mem_fd<0) { problem(3,"memory","process_memory_open_failed"); goto output; }
    const struct wifi_ops ops={verify_wifi_stock,current_identity};
    if(!wifi_transaction(mode,&ops)) {
        *__errno_location()=failure_errno;
        problem(5,"verification_or_write",failure_reason);
    }
output:
    if(mem_fd>=0) close(mem_fd);
    printf("{\"schema\":1,\"command\":\"%s\",\"ok\":%s,\"exit_code\":%d,\"stage\":\"%s\",\"reason\":\"%s\",\"errno\":%d,"
           "\"pid\":%u,\"process_start_ticks\":%llu,\"verified_symbols\":%u,"
           "\"before\":[%u,%u],\"after\":[%u,%u],\"after_verified\":%s,\"failed_site\":%d,\"byte_writes_attempted\":%u,"
           "\"rollback_attempted\":%s,\"rollback_verified\":%s,\"transaction_atomic\":false,"
           "\"power_cycle_reversion_guaranteed\":false,\"snapshot_persistence_verified\":false,\"physical_rvf_verified\":false}\n",
           command,result_code?"false":"true",result_code,stage,reason,saved_errno,pid,start_ticks,verified_checks,
           before_wifi[0],before_wifi[1],after_wifi[0],after_wifi[1],after_verified?"true":"false",failed_site,writes_attempted,
           rollback_attempted?"true":"false",rollback_verified?"true":"false");
    fflush((void *)0); return result_code;
}
__asm__(".text\n.arm\n.global _start\n.type _start,%function\n_start:\nmov r0,sp\nbl wifi_mem_main\nmov r7,#1\nsvc #0\n");
#endif
