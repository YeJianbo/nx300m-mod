/* RAM-only animation entry installation. Uses the existing pure-syscall tracer;
 * never calls C/dlopen on a borrowed thread or changes an on-disk executable. */
#define _GNU_SOURCE
#define _LARGEFILE64_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
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
#include "settings_animation_stock.h"
#include "settings_animation_blob.h"
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

struct animation_config {uint32_t magic,abi,mode,active,active_mode;};
struct admitted {int mem;struct image libc;struct stat64 executable;unsigned long long start;uint32_t region;struct animation_config config;};
static int match(int mem,uint32_t at,const void *data,unsigned size) {
    unsigned char buf[4096];const unsigned char *p=data;
    while(size){unsigned n=size>sizeof buf?sizeof buf:size;if(!read_exact(mem,buf,n,at)||memcmp(buf,p,n))return 0;at+=n;p+=n;size-=n;}return 1;
}
static void close_target(struct admitted *t) {if(t->mem>=0)close(t->mem);if(t->libc.fd>=0)close(t->libc.fd);}
static int executable(unsigned pid,struct stat64 *st,int authenticate) {
    char path[64],exe[512];snprintf(path,sizeof path,"/proc/%u/exe",pid);
    ssize_t n=readlink(path,exe,sizeof exe-1);if(n<0)return 0;exe[n]=0;
    if(strcmp(exe,"/usr/bin/di-camera-app-nx300"))return 0;
    int fd=open(path,O_RDONLY|O_CLOEXEC);if(fd<0)return 0;
    int ok=!__fxstat64(3,fd,st)&&S_ISREG(st->st_mode)&&st->st_size==STOCK_APP_SIZE;
    if(ok&&authenticate)ok=file_hash(fd,STOCK_APP_SIZE,STOCK_APP_SHA);
    close(fd);return ok;
}
static int same_file(const struct stat64 *a,const struct stat64 *b) {
    return a->st_dev==b->st_dev&&a->st_ino==b->st_ino&&a->st_size==b->st_size&&
        a->st_mtim.tv_sec==b->st_mtim.tv_sec&&a->st_mtim.tv_nsec==b->st_mtim.tv_nsec;
}
static int app_bytes(const struct admitted *t,uint32_t at,unsigned n) {
    struct mapping *m=cover(at,n,0,1);
    return m&&m->inode==(unsigned long long)t->executable.st_ino&&
        m->devmajor==major(t->executable.st_dev)&&m->devminor==minor(t->executable.st_dev);
}
static int inspect(unsigned pid,struct admitted *t,int authenticate) {
    memset(t,0,sizeof *t);t->mem=t->libc.fd=-1;
    struct thread id;char path[64];
    if(!task_identity(pid,pid,&id)||!executable(pid,&t->executable,authenticate)||!load_maps(pid))return 0;
    t->start=id.start;
    snprintf(path,sizeof path,"/proc/%u/mem",pid);t->mem=open(path,O_RDONLY|O_CLOEXEC);if(t->mem<0)return 0;
    uint32_t draw[2],start[2];
    if(!app_bytes(t,NX_DRAW,sizeof stock_draw)||!app_bytes(t,NX_START,sizeof stock_start)||
       !read_exact(t->mem,draw,8,NX_DRAW)||!read_exact(t->mem,start,8,NX_START))return 0;
    int original=!memcmp(draw,stock_draw,8)&&!memcmp(start,stock_start,8);
    if(!original){
        if(draw[0]!=0xe51ff004U||start[0]!=0xe51ff004U||draw[1]<BLOB_NX_ANIMATION_DRAW)return 0;
        t->region=draw[1]-BLOB_NX_ANIMATION_DRAW;
        if(!t->region||t->region%4096||t->region>UINT32_MAX-sizeof animation_blob||
           start[1]!=t->region+BLOB_NX_ANIMATION_START)return 0;
        struct mapping *rx=cover(t->region,BLOB___DATA_START,0,1);
        struct mapping *rw=cover(t->region+BLOB___DATA_START,sizeof animation_blob-BLOB___DATA_START,1,0);
        if(!rx||!rw||rx->inode||rw->inode||strcmp(rx->perm,"r-xp")||strcmp(rw->perm,"rw-p")||
           !match(t->mem,t->region,animation_blob,BLOB___DATA_START)||
           !read_exact(t->mem,&t->config,sizeof t->config,t->region+BLOB_NX_ANIMATION_CONFIG)||
           t->config.magic!=0x4e58414e||t->config.abi!=1||t->config.mode>2||t->config.active>1||t->config.active_mode>2)return 0;
    }
    for(unsigned i=0;i<sizeof animation_checks/sizeof animation_checks[0];i++){
        const struct animation_check *c=&animation_checks[i];unsigned skip=(c->address==NX_DRAW||c->address==NX_START)?8:0;
        if(!app_bytes(t,c->address,c->size)||!match(t->mem,c->address+skip,c->bytes+skip,c->size-skip))return 0;
    }
    if(authenticate){
        if(!image_open(pid,"libc-2.13.so",STOCK_LIBC_SIZE,STOCK_LIBC_SHA,&t->libc)&&
           !image_open(pid,"libc.so.6",STOCK_LIBC_SIZE,STOCK_LIBC_SHA,&t->libc))return 0;
        if(!code_check(t->mem,t->libc.bias+STOCK_SVC,stock_svc))return 0;
    }
    return 1;
}
static int cacheflush(struct stopped *thread,uint32_t svc,uint32_t address,unsigned size) {
    uint32_t args[6]={address,address+size,0,0,0,0},rc;
    return remote_syscall(thread,svc,0xf0002,args,&rc)&&!kernel_error(rc);
}
static int mutate(unsigned pid,unsigned mode) {
    struct admitted t;memset(&t,0,sizeof t);t.mem=t.libc.fd=-1;
    int rc=3,patch_attempted=0;uint32_t region=0;struct stopped *main_thread=NULL;
    const char *stage="running-file-admission";
    if(!inspect(pid,&t,1))goto done;
    /* Large immutable files are checked while the camera is running. */
    stage="all-thread-attach";if(!attach_all(pid))goto done;
    struct thread id;struct stat64 st;
    if(!task_identity(pid,pid,&id)||id.start!=t.start||!executable(pid,&st,0)||!same_file(&st,&t.executable)||!load_maps(pid))goto done;
    for(unsigned i=0;i<nstops;i++){
        if(stops[i].tid==pid)main_thread=&stops[i];
        uint32_t pc=stops[i].regs.r[15];
        if((pc>=NX_DRAW&&pc<NX_DRAW+8)||(pc>=NX_START&&pc<NX_START+8)){
            fprintf(stderr,"Entry prologue in flight; retry without changing target.\n");rc=4;goto done;}
    }
    if(!main_thread||!code_check(t.mem,t.libc.bias+STOCK_SVC,stock_svc))goto done;
    /* Re-read mutable hooks/config after the stop, not the earlier snapshot. */
    struct admitted stopped;
    if(!inspect(pid,&stopped,0)){close_target(&stopped);goto done;}
    if(stopped.start!=t.start||!same_file(&stopped.executable,&t.executable)){close_target(&stopped);goto done;}
    t.region=stopped.region;t.config=stopped.config;close_target(&stopped);
    if(t.region){
        stage="existing-mode-update";uint32_t actual;
        if(!poke(pid,t.region+BLOB_NX_ANIMATION_CONFIG+8,&mode,4)||
           !read_exact(t.mem,&actual,4,t.region+BLOB_NX_ANIMATION_CONFIG+8)||actual!=mode){
            if(!poke(pid,t.region+BLOB_NX_ANIMATION_CONFIG+8,&t.config.mode,4)||
               !read_exact(t.mem,&actual,4,t.region+BLOB_NX_ANIMATION_CONFIG+8)||actual!=t.config.mode)transaction_poisoned=1;
            goto done;
        }
        rc=0;goto done;
    }
    if(!mode){rc=0;goto done;}
    uint32_t args[6]={0,sizeof animation_blob,3,0x22,0xffffffff,0},result;
    stage="private-mmap";
    if(!remote_syscall(main_thread,t.libc.bias+STOCK_SVC,192,args,&region)||kernel_error(region)||region%4096)goto done;
    if(!load_maps(pid)||!cover(region,sizeof animation_blob,1,0)||!poke(pid,region,animation_blob,sizeof animation_blob)||
       !poke(pid,region+BLOB_NX_ANIMATION_CONFIG+8,&mode,4))goto done;
    if(!cacheflush(main_thread,t.libc.bias+STOCK_SVC,region,BLOB___DATA_START))goto done;
    uint32_t protect[6]={region,BLOB___DATA_START,5,0,0,0};
    if(!remote_syscall(main_thread,t.libc.bias+STOCK_SVC,125,protect,&result)||kernel_error(result))goto done;
    stage="entry-commit";
    uint32_t draw[2]={0xe51ff004U,region+BLOB_NX_ANIMATION_DRAW},start[2]={0xe51ff004U,region+BLOB_NX_ANIMATION_START};
    patch_attempted=1;
    if(!poke(pid,NX_DRAW,draw,8)||!poke(pid,NX_START,start,8)||
       !match(t.mem,NX_DRAW,draw,8)||!match(t.mem,NX_START,start,8)||
       !cacheflush(main_thread,t.libc.bias+STOCK_SVC,NX_DRAW,8)||
       !cacheflush(main_thread,t.libc.bias+STOCK_SVC,NX_START,8))goto done;
    t.region=region;rc=0;
done:
    if(rc&&patch_attempted){
        if(!poke(pid,NX_DRAW,stock_draw,8)||!poke(pid,NX_START,stock_start,8)||
           !match(t.mem,NX_DRAW,stock_draw,8)||!match(t.mem,NX_START,stock_start,8)||
           !cacheflush(main_thread,t.libc.bias+STOCK_SVC,NX_DRAW,8)||
           !cacheflush(main_thread,t.libc.bias+STOCK_SVC,NX_START,8))transaction_poisoned=1;
    }
    /* Keep the small private mapping: a wrapper/trampoline may be on a stack.
     * Mode zero restores original behavior without unsafe unmapping. */
    close_target(&t);
    if(transaction_poisoned){fprintf(stderr,"FATAL: recovery incomplete; no success claimed.\n");return 5;}
    if(!detach_all())return 5;
    if(rc)fprintf(stderr,"Animation refused at %s (errno=%d), exit=%d.\n",stage,errno,rc);
    else printf("%u %u %u\n",pid,mode,t.region?1:0);
    return rc;
}
static int supervised(unsigned pid,unsigned mode) {
    struct statfs fs;if(statfs("/var/run",&fs)||((unsigned long)fs.f_type!=0x01021994UL&&(unsigned long)fs.f_type!=0x858458f6UL))return 69;
    int guard=open("/var/run/nx300m-animation.lock",O_RDWR|O_CREAT|O_CLOEXEC|O_NOFOLLOW,0600);
    struct stat64 st;
    if(guard<0||__fxstat64(3,guard,&st)||!S_ISREG(st.st_mode)||flock(guard,LOCK_EX|LOCK_NB)){if(guard>=0)close(guard);return 4;}
    pid_t child=fork();if(child<0){close(guard);return 3;}
    if(!child){
        signal(SIGUSR1,request_abort);signal(SIGTERM,request_abort);signal(SIGINT,request_abort);signal(SIGPIPE,SIG_IGN);
        exit(mutate(pid,mode));
    }
    int status;unsigned long long deadline=now_ms()+15000;
    while(now_ms()<deadline){pid_t n=waitpid(child,&status,WNOHANG);if(n==child){close(guard);return WIFEXITED(status)?WEXITSTATUS(status):5;}
        if(n<0&&errno!=EINTR)break;
        struct timespec pause={0,10000000};nanosleep(&pause,NULL);}
    kill(child,SIGUSR1);close(guard);
    fprintf(stderr,"Animation recovery worker %u still owns guard; do not kill it.\n",(unsigned)child);return 5;
}
int main(int argc,char **argv) {
    unsigned pid=0,mode;
    if(argc==2){
        DIR *d=opendir("/proc");if(!d)return 69;struct dirent *e;unsigned found=0;
        while((e=readdir(d))){unsigned candidate;if(!decimal(e->d_name,&candidate))continue;
            char path[64],exe[512];snprintf(path,sizeof path,"/proc/%u/exe",candidate);
            ssize_t n=readlink(path,exe,sizeof exe-1);if(n<0)continue;exe[n]=0;
            if(!strcmp(exe,"/usr/bin/di-camera-app-nx300")){pid=candidate;found++;}}
        closedir(d);if(found!=1)return 69;
    }else if(argc!=3||!decimal(argv[2],&pid))return 64;
    if(!strcmp(argv[1],"status")){
        struct admitted t;int ok=inspect(pid,&t,0);
        if(ok)printf("%u %u %u\n",pid,t.region?t.config.mode:0,t.region?1:0);
        close_target(&t);return ok?0:69;
    }
    if(strlen(argv[1])!=1||argv[1][0]<'0'||argv[1][0]>'2')return 64;
    mode=(unsigned)(argv[1][0]-'0');return supervised(pid,mode);
}
