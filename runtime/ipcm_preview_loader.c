/* Independent IPCM preview RAM loader. The still-LUT ABI is not included. */
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
#include <elf.h>
#include <errno.h>
#include <sys/ptrace.h>
#include <sys/wait.h>
#include <sys/vfs.h>
#include <signal.h>
#include <time.h>
#include "ipcm_preview_blob.h"
#include "ipcm_preview_stock.h"
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
struct preview_state {
    uint32_t magic,abi,pid,build;
    unsigned long long start;
    uint32_t iq,udd,region,length,config,stats;
};
struct target {struct image iq,udd,libc; int mem; unsigned long long start; unsigned slot[NX_IPCM_SLOTS],original[NX_IPCM_SLOTS],value[NX_IPCM_SLOTS];};
static int state_path(unsigned pid,char *p,unsigned size) {
    struct statfs fs;
    if(statfs("/var/run",&fs) || ((unsigned long)fs.f_type!=0x01021994UL && (unsigned long)fs.f_type!=0x858458f6UL)) return 0;
    return snprintf(p,size,"/var/run/nx300m-ipcm-preview-%u.state",pid)>0;
}
static int state_read(const char *p,unsigned pid,struct preview_state *s) {
    int fd=open(p,O_RDONLY|O_CLOEXEC|O_NOFOLLOW);if(fd<0)return 0;
    struct stat64 st; int ok=!__fxstat64(3,fd,&st)&&S_ISREG(st.st_mode)&&st.st_size==sizeof *s&&read(fd,s,sizeof *s)==sizeof *s;
    close(fd);return ok&&s->magic==NX_IPCM_MAGIC&&s->abi==NX_IPCM_ABI&&s->pid==pid;
}
static int state_write(const char *p,const struct preview_state *s) {
    int fd=open(p,O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC|O_NOFOLLOW,0600);if(fd<0)return 0;
    int ok=write(fd,s,sizeof *s)==sizeof *s;int rc=close(fd);if(!ok||rc){unlink(p);return 0;}return 1;
}
static void target_init(struct target *t) {memset(t,0,sizeof *t);t->mem=t->iq.fd=t->udd.fd=t->libc.fd=-1;}
static void target_close(struct target *t) {
    if(t->mem>=0)close(t->mem);
    if(t->iq.fd>=0)close(t->iq.fd);
    if(t->udd.fd>=0)close(t->udd.fd);
    if(t->libc.fd>=0)close(t->libc.fd);
}
static int target_open(unsigned pid,int fixture,struct target *t) {
    char path[96],exe[512];snprintf(path,sizeof path,"/proc/%u/exe",pid);
    ssize_t n=readlink(path,exe,sizeof exe-1);if(n<0)return 0;exe[n]=0;
    if(strcmp(base(exe),fixture?"ipcm_preview_fixture.arm":"di-camera-app-nx300"))return 0;
    if(!fixture){int fd=open(path,O_RDONLY|O_CLOEXEC);int ok=fd>=0&&file_hash(fd,STOCK_APP_SIZE,STOCK_APP_SHA);if(fd>=0)close(fd);if(!ok)return 0;}
    struct thread thread;if(!task_identity(pid,pid,&thread)||!load_maps(pid))return 0;t->start=thread.start;
    if(!image_open(pid,"libiq-fw-slpcam.so",STOCK_IQ_SIZE,STOCK_IQ_SHA,&t->iq)||
       !image_open(pid,"libudd.so.3.1",STOCK_UDD_SIZE,STOCK_UDD_SHA,&t->udd))return 0;
    if(!image_open(pid,"libc-2.13.so",STOCK_LIBC_SIZE,STOCK_LIBC_SHA,&t->libc)&&
       !image_open(pid,"libc.so.6",STOCK_LIBC_SIZE,STOCK_LIBC_SHA,&t->libc))return 0;
    snprintf(path,sizeof path,"/proc/%u/mem",pid);t->mem=open(path,O_RDONLY|O_CLOEXEC);if(t->mem<0)return 0;
    unsigned char actual[4096];
    for(unsigned i=0;i<sizeof stock_checks/sizeof stock_checks[0];i++) {
        const struct stock_check *c=&stock_checks[i];unsigned bias=c->image?t->udd.bias:t->iq.bias;
        for(unsigned offset=0;offset<c->size;) {
            unsigned count=c->size-offset;if(count>sizeof actual)count=sizeof actual;
            if(!cover(bias+c->address+offset,count,0,1)||!read_exact(t->mem,actual,count,bias+c->address+offset)||memcmp(actual,c->bytes+offset,count))return 0;
            offset+=count;
        }
    }
    if(!code_check(t->mem,t->libc.bias+STOCK_SVC,stock_svc))return 0;
    for(unsigned i=0;i<NX_IPCM_SLOTS;i++) {
        t->slot[i]=t->iq.bias+stock_slots[i];t->original[i]=(i<NX_IPCM_SETTERS?t->udd.bias:t->iq.bias)+stock_originals[i];
        struct mapping *m=cover(t->slot[i],4,0,0);
        if(!m||m->inode!=t->iq.first->inode||m->perm[2]=='x'||!read_exact(t->mem,&t->value[i],4,t->slot[i]))return 0;
    }
    return 1;
}
static int read_blob(const char *path,unsigned char **blob,struct nx_ipcm_header *h) {
    int fd=open(path,O_RDONLY|O_CLOEXEC|O_NOFOLLOW);if(fd<0)return 0;struct stat64 st;
    int ok=!__fxstat64(3,fd,&st)&&S_ISREG(st.st_mode)&&st.st_size>=8192&&st.st_size<=65536&&read_exact(fd,h,sizeof *h,0);
    if(ok)ok=h->magic==NX_IPCM_MAGIC&&h->abi==NX_IPCM_ABI&&h->build==NX_IPCM_BUILD&&
        h->blob_size==(unsigned)st.st_size&&h->code_size==h->data_offset&&h->code_size>=4096&&h->code_size%4096==0&&
        h->code_size<h->blob_size&&h->blob_size%4096==0&&h->data_size==h->blob_size-h->data_offset&&
        h->config_size==sizeof(struct nx_ipcm_config)&&h->stats_size==sizeof(struct nx_ipcm_stats)&&
        h->config_offset>=h->data_offset&&h->config_offset%4==0&&h->config_offset<=h->blob_size-h->config_size&&
        h->stats_offset>=h->data_offset&&h->stats_offset%4==0&&h->stats_offset<=h->blob_size-h->stats_size&&
        (h->config_offset+h->config_size<=h->stats_offset||h->stats_offset+h->stats_size<=h->config_offset);
    for(unsigned i=0;ok&&i<NX_IPCM_SLOTS;i++)ok=h->entry[i]>=sizeof *h&&h->entry[i]<h->code_size&&h->entry[i]%4==0;
    unsigned char *p=ok?malloc(h->blob_size):NULL;if(ok)ok=p&&read_exact(fd,p,h->blob_size,0);close(fd);
    if(!ok){free(p);return 0;}*blob=p;return 1;
}
static unsigned crc32(const unsigned char *p,unsigned n) {
    unsigned crc=~0U;while(n--){crc^=*p++;for(unsigned i=0;i<8;i++)crc=crc>>1^(0xedb88320U&(0U-(crc&1)));}return ~crc;
}
static int profile_valid(const struct nx_ipcm_profile *p) {
    unsigned count=p->version==1?4:9;
    unsigned disk_size=p->version==1?340:p->version==2?360:456;
    if(p->magic!=NX_IPCM_PROFILE_MAGIC||(p->version<1||p->version>3)||p->size!=disk_size||p->flags||
       p->reserved[0]||p->reserved[1]||p->reserved[2])return 0;
    for(unsigned i=0;i<9;i++)if(p->ccm[i]<-16384||p->ccm[i]>16384)return 0;
    for(unsigned i=0;i<count;i++)if(p->post[i]<-16384||p->post[i]>16384)return 0;
    for(unsigned i=count;i<9;i++)if(p->post[i])return 0;
    for(unsigned i=1;i<256;i++)if(p->tone[i]<p->tone[i-1])return 0;
    for(unsigned sel=0;sel<2;sel++)for(unsigned i=0;i<12;i++)
        if(p->pcse[sel][i]<-4095||p->pcse[sel][i]>4095||(p->version<3&&p->pcse[sel][i]))return 0;
    /* Authenticate the original disk representation, including v1's shorter
     * post matrix. Internal padding never changes its on-disk CRC contract. */
    unsigned char raw[456];
    memcpy(raw,p,68+count*4);memcpy(raw+68+count*4,p->tone,256);
    if(p->version==3)memcpy(raw+360,p->pcse,96);
    if(p->payload_crc32!=crc32(raw+32,disk_size-32))return 0;
    return 1;
}
static int profile_read(const char *path,struct nx_ipcm_profile *p) {
    int fd=open(path,O_RDONLY|O_CLOEXEC|O_NOFOLLOW);if(fd<0)return 0;struct stat64 st;
    unsigned char raw[456];
    int ok=!__fxstat64(3,fd,&st)&&S_ISREG(st.st_mode)&&(st.st_size==340||st.st_size==360||st.st_size==456)&&read_exact(fd,raw,(unsigned)st.st_size,0);close(fd);
    if(ok) {
        memset(p,0,sizeof *p);memcpy(p,raw,32);
        if((p->version==1&&st.st_size==340)||(p->version==2&&st.st_size==360)||(p->version==3&&st.st_size==456)) {
            unsigned count=p->version==1?4:9;
            memcpy(p->ccm,raw+32,36);memcpy(p->post,raw+68,count*4);memcpy(p->tone,raw+68+count*4,256);
            if(p->version==3)memcpy(p->pcse,raw+360,96);
        } else ok=0;
    }
    return ok&&profile_valid(p);
}
static int slots_match(const struct target *t,const struct preview_state *s,const struct nx_ipcm_header *h,int installed) {
    for(unsigned i=0;i<NX_IPCM_SLOTS;i++)if(t->value[i]!=(installed?s->region+h->entry[i]:t->original[i]))return 0;
    return 1;
}
static int resident(const struct target *t,const struct preview_state *s,const struct nx_ipcm_header *h,
                    const unsigned char *blob,struct nx_ipcm_config *cfg,struct nx_ipcm_stats *stats) {
    if(s->start!=t->start||s->iq!=t->iq.bias||s->udd!=t->udd.bias||s->build!=h->build||!s->region||s->region%4096||
       s->region>UINT32_MAX-h->blob_size||s->length!=h->blob_size||s->config!=s->region+h->config_offset||s->stats!=s->region+h->stats_offset)return 0;
    struct mapping *rx=cover(s->region,h->code_size,0,1),*rw=cover(s->region+h->data_offset,h->data_size,1,0);
    if(!rx||!rw||rx->inode||rw->inode||strcmp(rx->perm,"r-xp")||strcmp(rw->perm,"rw-p"))return 0;
    unsigned char actual[4096];for(unsigned off=0;off<h->code_size;off+=sizeof actual)
        if(!read_exact(t->mem,actual,sizeof actual,s->region+off)||memcmp(actual,blob+off,sizeof actual))return 0;
    if(!read_exact(t->mem,cfg,sizeof *cfg,s->config)||!read_exact(t->mem,stats,sizeof *stats,s->stats)||
       cfg->abi!=NX_IPCM_ABI||cfg->size!=sizeof *cfg||cfg->enabled>1||cfg->strength>100||!cfg->generation||
       cfg->update!=t->iq.bias+STOCK_UPDATE||cfg->reset!=t->iq.bias+STOCK_RESET||
       cfg->vptr[0]!=t->iq.bias+STOCK_COLOR_VPTR||cfg->vptr[1]!=t->iq.bias+STOCK_GAMMA_VPTR||cfg->vptr[2]!=t->iq.bias+STOCK_PCSE_VPTR)return 0;
    for(unsigned i=0;i<NX_IPCM_SETTERS;i++)if(cfg->original[i]!=t->original[i])return 0;
    if(cfg->enabled&&!profile_valid(&cfg->profile))return 0;
    return slots_match(t,s,h,0)||slots_match(t,s,h,1);
}
static void array(const char *key,const unsigned *p,unsigned n) {printf(",\"%s\":[",key);for(unsigned i=0;i<n;i++)printf("%s%u",i?",":"",p[i]);putchar(']');}
static void print_status(unsigned pid,const char *command,int installed,const struct nx_ipcm_config *cfg,const struct nx_ipcm_stats *s) {
    printf("{\"command\":\"%s\",\"pid\":%u,\"runtime_abi\":%u,\"profile_version\":%u,\"installed\":%s,\"enabled\":%s,\"generation\":%u,\"strength\":%u,\"inflight\":%u",
        command,pid,NX_IPCM_ABI,cfg->profile.version,installed?"true":"false",cfg->enabled?"true":"false",cfg->generation,cfg->strength,s->inflight);
    array("pending",s->pending,NX_IPCM_KINDS);array("cache_valid",s->cache_valid,NX_IPCM_KINDS);array("setter_calls",s->setter_calls,NX_IPCM_KINDS);array("setter_forwarded",s->setter_forwarded,NX_IPCM_KINDS);
    array("fallbacks",s->fallbacks,NX_IPCM_KINDS);array("last_source",s->last_source,NX_IPCM_KINDS);array("last_forwarded",s->last_forwarded,NX_IPCM_KINDS);
    printf(",\"last_result\":[%d,null,%d,%d,%d,%d]",s->last_result[0],s->last_result[2],s->last_result[3],s->last_result[4],s->last_result[5]);
    array("node_updates",s->node_updates,NX_IPCM_NODES);array("node_resets",s->node_resets,NX_IPCM_NODES);
    printf(",\"mapping_retained\":%s,\"pixel_verified\":false,\"tone_hardware_acceptance\":\"unobservable_void_setter\"}\n",
           cfg->abi==NX_IPCM_ABI?"true":"false");
}
static int execute(unsigned pid,const char *command,const char *blob_path,const char *profile,unsigned strength,int fixture) {
    struct target t;target_init(&t);struct preview_state s={0};struct nx_ipcm_header h;
    struct nx_ipcm_config cfg={0},prior_cfg={0};struct nx_ipcm_stats stats={0};struct nx_ipcm_profile requested;
    unsigned char *blob=NULL;char path[128];int rc=3,has_state=0,attempted=0,config_attempted=0,installed=0;
    if(!state_path(pid,path,sizeof path)||!read_blob(blob_path,&blob,&h)||
       (!strcmp(command,"apply")&&!profile_read(profile,&requested))) {fprintf(stderr,"Invalid blob/profile or non-RAM state directory; no target changed.\n");free(blob);return 3;}
    if(!attach_all(pid)){int detached=detach_all();free(blob);fprintf(stderr,"Thread admission failed; detached=%d.\n",detached);return 3;}
    if(!target_open(pid,fixture,&t)){fprintf(stderr,"Original ELF/code/slot admission failed.\n");goto done;}
    has_state=access(path,F_OK)==0;
    if(has_state) {
        if(!state_read(path,pid,&s)||!resident(&t,&s,&h,blob,&cfg,&stats)){
            fprintf(stderr,"Resident ownership/code/ABI differs. For an older build, restore with its matching loader, then restart the target process; do not remove state to bypass admission.\n");goto done;
        }
        installed=slots_match(&t,&s,&h,1);
    } else {
        for(unsigned i=0;i<NX_IPCM_SLOTS;i++)if(t.value[i]!=t.original[i]){fprintf(stderr,"Original slots not resolved; no target changed.\n");goto done;}
    }
    if(!strcmp(command,"status")){print_status(pid,command,installed,&cfg,&stats);rc=0;goto done;}
    if(stats.inflight){fprintf(stderr,"Blob in flight; retry after native call returns.\n");rc=4;goto done;}
    for(unsigned i=0;i<NX_IPCM_KINDS;i++)if(stats.lock[i])goto done;
    for(unsigned i=0;i<NX_IPCM_NODES;i++)if(stats.node_lock[i])goto done;
    if(!has_state) {
        if(strcmp(command,"apply")){rc=0;print_status(pid,command,0,&cfg,&stats);goto done;}
        struct stopped *leader=NULL;for(unsigned i=0;i<nstops;i++)if(stops[i].tid==pid)leader=&stops[i];if(!leader)goto done;
        cfg.abi=NX_IPCM_ABI;cfg.size=sizeof cfg;cfg.generation=1;cfg.update=t.iq.bias+STOCK_UPDATE;cfg.reset=t.iq.bias+STOCK_RESET;
        cfg.vptr[0]=t.iq.bias+STOCK_COLOR_VPTR;cfg.vptr[1]=t.iq.bias+STOCK_GAMMA_VPTR;cfg.vptr[2]=t.iq.bias+STOCK_PCSE_VPTR;
        for(unsigned i=0;i<NX_IPCM_SETTERS;i++)cfg.original[i]=t.original[i];
        memcpy(blob+h.config_offset,&cfg,sizeof cfg);
        uint32_t args[6]={0,h.blob_size,3,0x22,0xffffffff,0},region=0,result;
        if(!remote_syscall(leader,t.libc.bias+STOCK_SVC,192,args,&region)||kernel_error(region)||region%4096)goto done;
        /* Anonymous code is retained even on failure; no stale entry can ever
         * point at reused/unmapped code. No original page permission changes. */
        if(!load_maps(pid)||!cover(region,h.blob_size,1,0)||!poke(pid,region,blob,h.blob_size))goto done;
        uint32_t flush[6]={region,region+h.code_size,0,0,0,0},protect[6]={region,h.code_size,5,0,0,0};
        if(!remote_syscall(leader,t.libc.bias+STOCK_SVC,0xf0002,flush,&result)||kernel_error(result)||
           !remote_syscall(leader,t.libc.bias+STOCK_SVC,125,protect,&result)||kernel_error(result)||!load_maps(pid))goto done;
        s.magic=NX_IPCM_MAGIC;s.abi=NX_IPCM_ABI;s.pid=pid;s.build=h.build;s.start=t.start;s.iq=t.iq.bias;s.udd=t.udd.bias;
        s.region=region;s.length=h.blob_size;s.config=region+h.config_offset;s.stats=region+h.stats_offset;
        if(!state_write(path,&s))goto done;
        has_state=1;
    }
    prior_cfg=cfg;
    int applying=!strcmp(command,"apply"),restoring=!strcmp(command,"restore");
    if(cfg.generation==UINT32_MAX){fprintf(stderr,"Generation exhausted.\n");goto done;}
    /* All threads are stopped and the blob has no in-flight users. */
    ++cfg.generation;cfg.enabled=applying?1:0;cfg.strength=applying?strength:0;
    if(applying)cfg.profile=requested;
    if(abort_requested)goto done;
    config_attempted=1;if(!poke(pid,s.config,&cfg,sizeof cfg))goto done;
    unsigned pending=0;for(unsigned i=0;i<NX_IPCM_KINDS;i++)pending|=stats.pending[i];
    int desired_installed=!(restoring&&!pending);
    for(unsigned i=0;i<NX_IPCM_SLOTS;i++) {
        unsigned desired=desired_installed?s.region+h.entry[i]:t.original[i],actual;
        attempted=(int)i+1;
        /* ptrace POKEDATA performs private COW on RELRO vtable data. */
        if(!poke(pid,t.slot[i],&desired,4)||!read_exact(t.mem,&actual,4,t.slot[i])||actual!=desired)goto done;
    }
    {struct nx_ipcm_config verify;if(!read_exact(t.mem,&verify,sizeof verify,s.config)||memcmp(&verify,&cfg,sizeof cfg))goto done;}
    installed=desired_installed;rc=restoring&&pending?4:0;
    print_status(pid,command,installed,&cfg,&stats);
    if(rc==4)fprintf(stderr,"Restore pending; hooks retained until genuine native setters forward stock inputs. Run restore again after refresh.\n");
done:
    if(rc!=0&&!(rc==4&&attempted==NX_IPCM_SLOTS)) {
        for(int i=attempted-1;i>=0;i--){unsigned actual;if(!poke(pid,t.slot[i],&t.value[i],4)||!read_exact(t.mem,&actual,4,t.slot[i])||actual!=t.value[i])transaction_poisoned=1;}
        if(config_attempted){struct nx_ipcm_config actual;if(!poke(pid,s.config,&prior_cfg,sizeof prior_cfg)||!read_exact(t.mem,&actual,sizeof actual,s.config)||memcmp(&actual,&prior_cfg,sizeof actual))transaction_poisoned=1;}
    }
    target_close(&t);free(blob);
    if(transaction_poisoned){fprintf(stderr,"Context/rollback recovery incomplete; tracer retained.\n");return 5;}
    if(!detach_all())return 5;
    if(rc==3)fprintf(stderr,"IPCM admission/transaction failed; exit=3.\n");
    return rc;
}
static int supervise(unsigned pid,const char *command,const char *blob,const char *profile,unsigned strength,int fixture) {
    char path[128];if(!state_path(pid,path,sizeof path))return 3;
    snprintf(path,sizeof path,"/var/run/nx300m-ipcm-worker-%u.log",(unsigned)getpid());
    int fd=open(path,O_RDWR|O_CREAT|O_EXCL|O_CLOEXEC|O_NOFOLLOW,0600);if(fd<0)return 3;
    pid_t child=fork();if(child<0){close(fd);unlink(path);return 3;}
    if(!child){dup2(fd,1);dup2(fd,2);close(fd);signal(SIGUSR1,request_abort);signal(SIGTERM,request_abort);signal(SIGINT,request_abort);signal(SIGPIPE,SIG_IGN);exit(execute(pid,command,blob,profile,strength,fixture));}
    unsigned long long deadline=now_ms()+15000;int status=0,finished=0;
    while(now_ms()<deadline){pid_t n=waitpid(child,&status,WNOHANG);if(n==child){finished=1;break;}if(n<0&&errno!=EINTR)break;struct timespec pause={0,10000000};nanosleep(&pause,NULL);}
    if(!finished){kill(child,SIGUSR1);close(fd);fprintf(stderr,"Recovery tracer %u remains active; log %s; do not terminate it.\n",(unsigned)child,path);return 5;}
    lseek(fd,0,SEEK_SET);char text[4096];ssize_t n;while((n=read(fd,text,sizeof text))>0)fwrite(text,1,n,stdout);close(fd);unlink(path);
    return WIFEXITED(status)?WEXITSTATUS(status):5;
}
int main(int argc,char **argv) {
    const char *command="status",*blob=NULL,*profile=NULL;unsigned pid=0,strength=100;int fixture=0,first=1;
    if(argc>1&&argv[1][0]!='-'){command=argv[1];first=2;}
    if(strcmp(command,"status")&&strcmp(command,"apply")&&strcmp(command,"stock")&&strcmp(command,"restore"))goto usage;
    for(int i=first;i<argc;i++) {
        if(!strcmp(argv[i],"--fixture")){fixture=1;continue;}if(i+1>=argc)goto usage;
        if(!strcmp(argv[i],"--pid")){if(!decimal(argv[++i],&pid))goto usage;}
        else if(!strcmp(argv[i],"--blob"))blob=argv[++i];
        else if(!strcmp(argv[i],"--profile"))profile=argv[++i];
        else if(!strcmp(argv[i],"--strength")){const char *v=argv[++i];if(!strcmp(v,"0"))strength=0;else if(!decimal(v,&strength)||strength>100)goto usage;}
        else goto usage;
    }
    if(!pid||!blob||(!strcmp(command,"apply")&&!profile))goto usage;
    return supervise(pid,command,blob,profile,strength,fixture);
usage:
    fprintf(stderr,"usage: ipcm_preview_loader.arm [status|apply|stock|restore] --pid PID --blob FILE [--profile FILE --strength 0..100] [--fixture]\n");return 64;
}
