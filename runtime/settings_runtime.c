/* RAM-only runtime-settings installer (2026-11-08 rev2). Fixes B1-B4:
 * B1: main_thread selected from attach list; libc opened and verified.
 * B2: region+symbol addresses; full config copy; RX/RW mprotect; cacheflush.
 * B4: existing install detected by slot value; remove restores both slots;
 *     patch_attempted set before first write; keyed recovery. */
#define _GNU_SOURCE
#define _LARGEFILE64_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <dirent.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <sys/file.h>
#include <sys/vfs.h>
#include <elf.h>
#include <errno.h>
#include <sys/ptrace.h>
#include <sys/wait.h>
#include <signal.h>
#include <time.h>
#include "settings_runtime_stock.h"
#include "settings_runtime_blob.h"
extern int __fxstat64(int,int,struct stat64 *);
extern unsigned long long old_strtoull(const char *,char **,int) __asm__("strtoull");
extern unsigned long old_strtoul(const char *,char **,int) __asm__("strtoul");
extern long old_strtol(const char *,char **,int) __asm__("strtol");
extern int old_sscanf(const char *,const char *,...) __asm__("sscanf");
#define strtoull old_strtoull
#define strtoul old_strtoul
#define strtol old_strtol
#define sscanf old_sscanf
#include "live_loader_core.h"

struct runtime_config {uint32_t magic,abi;volatile uint32_t bootstrapped;
    uint32_t select_slot,orig_select,wrapper_addr,pt_func;
    volatile uint32_t pt_state;uint32_t pt_deadline;
    uint32_t key_entry,orig_key_handler,custom_enabled,launch_action,last_launch;
    char launch_cmd[128];char request_path[96];char bootstrap_marker[96];char seen[128];};
#define CFG(field) (region + BLOB_NX_SETTINGS_CONFIG + offsetof(struct runtime_config,field))
struct admitted {int mem;struct image libc,ecore;struct stat64 executable;
    unsigned long long start;uint32_t region;struct runtime_config config;int fixture;
    int installed;
    char fixture_exe[256];unsigned fixture_slot,fixture_pt,fixture_key_entry,fixture_key_orig;};
static int match(int mem,uint32_t at,const void *data,unsigned size) {
    unsigned char buf[4096];const unsigned char *p=data;
    while(size){unsigned n=size>sizeof buf?sizeof buf:size;if(!read_exact(mem,buf,n,at)||memcmp(buf,p,n))return 0;at+=n;p+=n;size-=n;}return 1;
}
static void close_target(struct admitted *t) {if(t->mem>=0)close(t->mem);if(t->libc.fd>=0)close(t->libc.fd);if(t->ecore.fd>=0)close(t->ecore.fd);}
static int executable(unsigned pid,struct stat64 *st,int auth,const struct admitted *t) {
    char path[64],exe[512];snprintf(path,sizeof path,"/proc/%u/exe",pid);
    ssize_t n=readlink(path,exe,sizeof exe-1);if(n<0)return 0;exe[n]=0;
    const char *want=t->fixture?t->fixture_exe:"/usr/bin/di-camera-app-nx300";
    if(strcmp(exe,want))return 0;
    int fd=open(path,O_RDONLY|O_CLOEXEC);if(fd<0)return 0;
    int ok=!__fxstat64(3,fd,st)&&S_ISREG(st->st_mode);
    if(ok&&auth&&!t->fixture)ok=file_hash(fd,STOCK_APP_SIZE,STOCK_APP_SHA);
    close(fd);return ok;
}
static int same_file(const struct stat64 *a,const struct stat64 *b) {
    return a->st_dev==b->st_dev&&a->st_ino==b->st_ino&&a->st_size==b->st_size&&
        a->st_mtim.tv_sec==b->st_mtim.tv_sec&&a->st_mtim.tv_nsec==b->st_mtim.tv_nsec;
}
static int app_bytes(const struct admitted *t,uint32_t at,unsigned n) {
    if(t->fixture)return 1;
    struct mapping *m=cover(at,n,0,1);
    return m&&m->inode==(unsigned long long)t->executable.st_ino&&
        m->devmajor==major(t->executable.st_dev)&&m->devminor==minor(t->executable.st_dev);
}
static int fixture_manifest(struct admitted *t) {
    const char *flag=getenv("NX_SETTINGS_RUNTIME_FIXTURE");
    t->fixture=flag&&!strcmp(flag,"1");
    if(!t->fixture)return 1;
    const char *path="/perf/settings-fixture.json";
    int fd=open(path,O_RDONLY);if(fd<0)return 0;
    char body[1024];ssize_t n=read(fd,body,(sizeof body)-1);close(fd);
    if(n<=0)return 0;
    body[n]=0;
    char exe[256]={0};unsigned slot=0,pt=0,entry=0,orig=0;
    /* Every numeric field is written as "key":"0x........". Locate the
     * needle, then start the number at the needle's trailing "0x". */
    static const char *const keys[]={"\"exe\":\"","\"slot\":\"0x","\"pt\":\"0x",
        "\"key_entry\":\"0x","\"key_orig\":\"0x"};
    char *p=strstr(body,keys[0]);
    if(!p)return 0;
    p+=strlen(keys[0]);
    for(int i=0;*p&&*p!='"'&&i<255;i++)exe[i]=*p++;
    unsigned *dst[4]={&slot,&pt,&entry,&orig};
    for(unsigned k=0;k<4;k++){
        p=strstr(body,keys[k+1]);
        if(!p)return 0;
        *dst[k]=strtoul(p+strlen(keys[k+1])-2,0,16);
    }
    snprintf(t->fixture_exe,sizeof t->fixture_exe,"%s",exe);
    t->fixture_slot=slot;t->fixture_pt=pt;t->fixture_key_entry=entry;t->fixture_key_orig=orig;
    return exe[0]&&slot&&pt&&entry&&orig;
}
static unsigned key_entry_addr(const struct admitted *t){return t->fixture?t->fixture_key_entry:NX_KEY_ENTRY;}
static int verify_stock_bytes(const struct admitted *t) {
    if(t->fixture)return 1;
    for(unsigned i=0;i<sizeof settings_checks/sizeof settings_checks[0];i++){
        const struct settings_check *c=&settings_checks[i];
        unsigned skip=0;
        if(!app_bytes(t,c->address,c->size)||!match(t->mem,c->address+skip,c->bytes+skip,c->size-skip))return 0;
    }
    return 1;
}
static int inspect(unsigned pid,struct admitted *t,int auth) {
    memset(t,0,sizeof *t);t->mem=t->libc.fd=t->ecore.fd=-1;
    if(!fixture_manifest(t))return 0;
    struct thread id;char path[64];
    if(!task_identity(pid,pid,&id)||!executable(pid,&t->executable,auth,t)||!load_maps(pid))return 0;
    t->start=id.start;
    snprintf(path,sizeof path,"/proc/%u/mem",pid);t->mem=open(path,O_RDONLY|O_CLOEXEC);if(t->mem<0)return 0;
    /* B1: libc must be opened and verified for remote_syscall's svc. The
     * fixture links the same stock libc, so this applies to both modes. */
    if(!image_open(pid,"libc-2.13.so",STOCK_LIBC_SIZE,STOCK_LIBC_SHA,&t->libc)&&
       !image_open(pid,"libc.so.6",STOCK_LIBC_SIZE,STOCK_LIBC_SHA,&t->libc))return 0;
    if(!code_check(t->mem,t->libc.bias+STOCK_SVC,stock_svc))return 0;
    unsigned slot_addr,pt_func,key_entry;
    if(t->fixture){slot_addr=t->fixture_slot;pt_func=t->fixture_pt;key_entry=t->fixture_key_entry;}
    else{
        if(!image_open(pid,"libecore.so.1.2.0",ECORE_SIZE,ECORE_SHA,&t->ecore))return 0;
        slot_addr=t->ecore.bias+NX_SELECT_SLOT_OFF;pt_func=NX_PT_FUNC;key_entry=NX_KEY_ENTRY;
    }
    t->config.magic=0x4e585352;t->config.abi=1;
    t->config.select_slot=slot_addr;t->config.pt_func=pt_func;t->config.key_entry=key_entry;
    t->config.wrapper_addr=BLOB_NX_ECORE_SELECT_WRAPPER;
    if(!read_exact(t->mem,&t->config.orig_select,4,slot_addr))return 0;
    if(!read_exact(t->mem,&t->config.orig_key_handler,4,key_entry))return 0;
    /* B4: an existing install is only "installed" if the live slots still
     * point at the blob. After a successful uninstall the page stays mapped
     * with a valid config, so matching the config page alone would report a
     * stale install and silently skip the real work. */
    t->region=0;t->installed=0;
    {unsigned found=0;
    for(unsigned i=0;i<nmaps;i++){
        if(maps[i].inode||strcmp(maps[i].perm,"rw-p"))continue;
        if(maps[i].hi-maps[i].lo<sizeof(struct runtime_config))continue;
        struct runtime_config probe;
        /* The mapping may be merged with neighbours, so try every page. */
        for(uint32_t base=maps[i].lo;base+sizeof probe<=maps[i].hi;base+=4096){
            if(!read_exact(t->mem,&probe,sizeof probe,base))continue;
            if(probe.magic==0x4e585352&&probe.abi==1&&probe.select_slot==slot_addr&&
               base>=BLOB_NX_SETTINGS_CONFIG){
                found=base-BLOB_NX_SETTINGS_CONFIG;break;}
        }
        if(found)break;
    }
    if(found){
        t->region=found;
        if(!read_exact(t->mem,&t->config,sizeof t->config,t->region+BLOB_NX_SETTINGS_CONFIG))return 0;
        if(t->config.magic!=0x4e585352||t->config.abi!=1)return 0;
        uint32_t sel=0,key=0;
        if(read_exact(t->mem,&sel,4,t->config.select_slot)&&
           read_exact(t->mem,&key,4,t->config.key_entry))
            t->installed=(sel==t->config.wrapper_addr);
        if(!t->installed){t->region=0;t->installed=0;}
    }}
    if(!verify_stock_bytes(t))return 0;
    return 1;
}
static int cacheflush(struct stopped *thread,uint32_t svc,uint32_t address,unsigned size) {
    uint32_t args[6]={address,address+size,0,0,0,0},rc;
    return remote_syscall(thread,svc,0xf0002,args,&rc)&&!kernel_error(rc);
}
static int mutate(unsigned pid,unsigned mode) {
    struct admitted t;memset(&t,0,sizeof t);t.mem=t.libc.fd=t.ecore.fd=-1;
    int rc=3,patch_attempted=0;uint32_t region=0;struct stopped *main_thread=NULL;
    const char *stage="running-file-admission";
    if(!inspect(pid,&t,1))goto done;
    stage="all-thread-attach";if(!attach_all(pid))goto done;
    {struct thread id;struct stat64 st;
    if(!task_identity(pid,pid,&id)||id.start!=t.start||!executable(pid,&st,0,&t)||!same_file(&st,&t.executable)||!load_maps(pid))goto done;}
    for(unsigned i=0;i<nstops;i++)if(stops[i].tid==pid)main_thread=&stops[i];
    if(!main_thread){fprintf(stderr,"Main thread not in attach list.\n");goto done;}
    if(!t.fixture&&!code_check(t.mem,t.libc.bias+STOCK_SVC,stock_svc))goto done;
    struct admitted stopped;
    if(!inspect(pid,&stopped,0)){close_target(&stopped);goto done;}
    if(stopped.start!=t.start||!same_file(&stopped.executable,&t.executable)){close_target(&stopped);goto done;}
    t.region=stopped.region;t.config=stopped.config;close_target(&stopped);
    /* CFG() addresses are relative to the installed region; the local
     * `region` is only filled on the fresh-install path, so seed it here or
     * the reinstall branch writes page 0. */
    region=t.region;
    if(t.region){
        stage="existing-install";
        if(!mode){
            unsigned orig_sel=t.config.orig_select,orig_key=t.config.orig_key_handler;
            if(!poke(pid,t.config.select_slot,&orig_sel,4)||!match(t.mem,t.config.select_slot,&orig_sel,4)){
                fprintf(stderr,"FATAL: select slot restore failed.\n");rc=5;goto done;}
            if(!poke(pid,t.config.key_entry,&orig_key,4)||!match(t.mem,t.config.key_entry,&orig_key,4)){
                fprintf(stderr,"FATAL: key entry restore failed.\n");rc=5;goto done;}
            /* Read both slots back through the live mapping, not the local
             * copy, so the restore is proven against target memory. */
            unsigned back_sel=0,back_key=0;
            if(!read_exact(t.mem,&back_sel,4,t.config.select_slot)||back_sel!=orig_sel){
                fprintf(stderr,"FATAL: select slot readback %08x != %08x.\n",back_sel,orig_sel);rc=5;goto done;}
            if(!read_exact(t.mem,&back_key,4,t.config.key_entry)||back_key!=orig_key){
                fprintf(stderr,"FATAL: key entry readback %08x != %08x.\n",back_key,orig_key);rc=5;goto done;}
            printf("restored select=%08x key=%08x\n",back_sel,back_key);
            rc=0;goto done;
        }
        unsigned one=1;
        if(!poke(pid,CFG(custom_enabled),&one,4))goto done;
        rc=0;goto done;
    }
    if(!mode){rc=0;goto done;}
    {uint32_t args[6]={0,sizeof settings_blob,3,0x22,0xffffffff,0};
    stage="private-mmap";
    if(!remote_syscall(main_thread,t.libc.bias+STOCK_SVC,192,args,&region)||kernel_error(region)||region%4096)goto done;}
    if(!load_maps(pid)||!cover(region,sizeof settings_blob,1,0))goto done;
    if(!poke(pid,region,settings_blob,sizeof settings_blob))goto done;
    stage="config-init";
    {struct runtime_config cfg=t.config;
        cfg.magic=0x4e585352;cfg.abi=1;cfg.bootstrapped=0;
        cfg.wrapper_addr=region+BLOB_NX_ECORE_SELECT_WRAPPER;
        cfg.custom_enabled=1;cfg.launch_action=1;cfg.last_launch=0;cfg.pt_state=0;cfg.pt_deadline=0;
        if(t.fixture)snprintf(cfg.launch_cmd,sizeof cfg.launch_cmd,"echo ok >> /perf/custom-launch");
        else snprintf(cfg.launch_cmd,sizeof cfg.launch_cmd,"/mnt/mmc/nx300m-mod/panel.sh settings");
        snprintf(cfg.request_path,sizeof cfg.request_path,"/var/run/nx300m-settings-request");
        snprintf(cfg.bootstrap_marker,sizeof cfg.bootstrap_marker,"/var/run/nx300m-settings-bootstrap");
        memset(cfg.seen,0,sizeof cfg.seen);
        if(!poke(pid,region+BLOB_NX_SETTINGS_CONFIG,&cfg,sizeof cfg))goto done;}
    stage="cacheflush-rx";
    {if(!cacheflush(main_thread,t.libc.bias+STOCK_SVC,region,BLOB___DATA_START))goto done;
        {uint32_t rx[6]={region,BLOB___DATA_START,5,0,0,0},r0;
        if(!remote_syscall(main_thread,t.libc.bias+STOCK_SVC,125,rx,&r0)||kernel_error(r0))goto done;}}
    stage="key-table-patch";
    {patch_attempted=1;
        unsigned addr=key_entry_addr(&t);
        {uint32_t protect[6]={addr&~4095u,4096,3,0,0,0},r0;
        if(!remote_syscall(main_thread,t.libc.bias+STOCK_SVC,125,protect,&r0)||kernel_error(r0))goto done;}
        unsigned wrapper=region+BLOB_NX_SETTINGS_CUSTOM_HANDLER;
        if(!poke(pid,addr,&wrapper,4))goto done;
        {uint32_t restore[6]={addr&~4095u,4096,5,0,0,0},r0;
        if(!remote_syscall(main_thread,t.libc.bias+STOCK_SVC,125,restore,&r0)||kernel_error(r0))goto done;}
        if(!match(t.mem,addr,&wrapper,4))goto done;}
    stage="select-slot-patch";
    {unsigned wrapper=region+BLOB_NX_ECORE_SELECT_WRAPPER;
        /* Fixture-only fault injection: refuse the final slot write after the
         * key slot was already changed. That is the real failure shape B4 is
         * about, and it must roll the key slot back and report failure. */
        if(t.fixture&&getenv("NX_SETTINGS_RUNTIME_FAIL_SELECT"))
            {fprintf(stderr,"Injected fault before select-slot patch.\n");goto done;}
        if(!poke(pid,t.config.select_slot,&wrapper,4))goto done;
        if(!match(t.mem,t.config.select_slot,&wrapper,4))goto done;}
    t.region=region;rc=0;
done:
    if(rc&&patch_attempted){
        unsigned orig_sel=t.config.orig_select,orig_key=t.config.orig_key_handler;
        if(!poke(pid,t.config.select_slot,&orig_sel,4)||!match(t.mem,t.config.select_slot,&orig_sel,4))
            transaction_poisoned=1;
        if(!poke(pid,key_entry_addr(&t),&orig_key,4)||!match(t.mem,key_entry_addr(&t),&orig_key,4))
            transaction_poisoned=1;
    }
    close_target(&t);
    if(transaction_poisoned){fprintf(stderr,"FATAL: recovery incomplete; no success claimed.\n");return 5;}
    if(!detach_all())return 5;
    if(rc)fprintf(stderr,"Runtime settings refused at %s (errno=%d), exit=%d.\n",stage,errno,rc);
    else printf("%u %u %u region=%08x key=%08x\n",pid,mode,region?1u:0u,region,key_entry_addr(&t));
    return rc;
}
static int supervised(unsigned pid,unsigned mode) {
    struct statfs fs;if(statfs("/var/run",&fs)||((unsigned long)fs.f_type!=0x01021994UL&&(unsigned long)fs.f_type!=0x858458f6UL))return 69;
    int guard=open("/var/run/nx300m-settings-runtime.lock",O_RDWR|O_CREAT|O_CLOEXEC|O_NOFOLLOW,0600);
    struct stat64 st;
    if(guard<0||__fxstat64(3,guard,&st)||!S_ISREG(st.st_mode)||flock(guard,LOCK_EX|LOCK_NB)){if(guard>=0)close(guard);return 4;}
    pid_t child=fork();if(child<0){close(guard);return 3;}
    if(!child){
        signal(SIGUSR1,request_abort);signal(SIGTERM,request_abort);signal(SIGINT,request_abort);signal(SIGPIPE,SIG_IGN);
        exit(mutate(pid,mode));
    }
    int status;unsigned long long deadline=now_ms()+20000;
    while(now_ms()<deadline){pid_t n=waitpid(child,&status,WNOHANG);if(n==child){close(guard);return WIFEXITED(status)?WEXITSTATUS(status):5;}
        if(n<0&&errno!=EINTR)break;
        struct timespec pause={0,10000000};nanosleep(&pause,NULL);}
    kill(child,SIGUSR1);close(guard);
    fprintf(stderr,"Runtime-settings worker %u still owns guard; do not kill it.\n",(unsigned)child);return 5;
}
int main(int argc,char **argv) {
    unsigned pid=0,mode;
    /* Fixture mode must still select exactly one process: the manifest's exe.
     * A NULL filter (the earlier revision) counted every numeric /proc entry. */
    struct admitted fixture;memset(&fixture,0,sizeof fixture);
    fixture.mem=fixture.libc.fd=fixture.ecore.fd=-1;
    if(!fixture_manifest(&fixture)){fprintf(stderr,"No fixture manifest (/perf/settings-fixture.json).\n");return 69;}
    const char *want_exe=fixture.fixture?fixture.fixture_exe:"/usr/bin/di-camera-app-nx300";
    fprintf(stderr,"scan: fixture=%d want=%s\n",fixture.fixture,want_exe);
    if(argc==2){
        DIR *d=opendir("/proc");if(!d)return 69;
        struct dirent *e;unsigned found=0;
        while((e=readdir(d))){unsigned candidate;if(!decimal(e->d_name,&candidate))continue;
            char path[64],exe[512];snprintf(path,sizeof path,"/proc/%u/exe",candidate);
            ssize_t n=readlink(path,exe,sizeof exe-1);if(n<0)continue;exe[n]=0;
            if(strcmp(exe,want_exe)){fprintf(stderr,"  skip %u %s\n",candidate,exe);continue;}
            fprintf(stderr,"  match %u %s\n",candidate,exe);
            pid=candidate;found++;}
        closedir(d);if(found!=1){fprintf(stderr,"scan: found=%u\n",found);return 69;}
    }else if(argc!=3||!decimal(argv[2],&pid))return 64;
    if(!strcmp(argv[1],"status")){
        struct admitted t;int ok=inspect(pid,&t,0);
        if(ok)printf("%u %u %u\n",pid,t.region?1u:0u,t.config.custom_enabled);
        close_target(&t);return ok?0:69;
    }
    if(strlen(argv[1])!=1||argv[1][0]<'0'||argv[1][0]>'1')return 64;
    mode=(unsigned)(argv[1][0]-'0');return supervised(pid,mode);
}
