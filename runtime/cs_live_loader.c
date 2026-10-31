/* NX300M CS RAM loader. Production mutations remain blocked pending PP serialization evidence.
 * status is read-only. All admitted mutation targets are isolated fixtures. */
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
#include <elf.h>
#include <errno.h>
#include <sys/ptrace.h>
#include <sys/wait.h>
#include <sys/vfs.h>
#include <signal.h>
#include <time.h>
#include <stddef.h>
#include "manual_lens_cs_ram.h"
#include "cs_live_stock.h"

extern int __fxstat64(int, int, struct stat64 *);
extern unsigned long long old_strtoull(const char *,char **,int) __asm__("strtoull");
extern unsigned long old_strtoul(const char *,char **,int) __asm__("strtoul");
extern long old_strtol(const char *,char **,int) __asm__("strtol");
extern int old_sscanf(const char *,const char *,...) __asm__("sscanf");
extern int old_fscanf(FILE *,const char *,...) __asm__("fscanf");
#define strtoull old_strtoull
#define strtoul old_strtoul
#define strtol old_strtol
#define sscanf old_sscanf
#define fscanf old_fscanf

#include "live_loader_core.h"

struct live_state {
    uint32_t magic,abi,pid;unsigned long long start;
    uint32_t iq,udd,lens,libc,region,length,entry[4],config;
};
struct target {struct image iq,udd,lens,libc;int mem;unsigned long long start;uint32_t slots[4];};
static const uint32_t got[4]={STOCK_UPDATE_GOT,STOCK_COMMON_GOT,STOCK_INFO_GOT,STOCK_ONOFF_GOT};
static int bytes_match(int mem,uint32_t at,const void *bytes,unsigned n) {
    unsigned char buf[4096];const unsigned char *p=bytes;
    while(n){unsigned take=n>sizeof buf?sizeof buf:n;if(!read_exact(mem,buf,take,at)||memcmp(buf,p,take))return 0;
        at+=take;p+=take;n-=take;}return 1;
}
static int dynamic_abi(struct image *iq) {
    uint32_t rel=0,nrel=0,sym=0,str=0;int ended=0;
    for(unsigned i=0;i<iq->eh.e_phnum;i++)if(iq->ph[i].p_type==PT_DYNAMIC){
        unsigned count=iq->ph[i].p_filesz/sizeof(Elf32_Dyn);if(count>4096)return 0;
        for(unsigned j=0;j<count;j++){Elf32_Dyn d;if(!read_exact(iq->fd,&d,sizeof d,iq->ph[i].p_offset+j*sizeof d))return 0;
            if(d.d_tag==DT_NULL){ended=1;break;}
            if(d.d_tag==DT_JMPREL)rel=d.d_un.d_ptr;
            if(d.d_tag==DT_PLTRELSZ)nrel=d.d_un.d_val;
            if(d.d_tag==DT_SYMTAB)sym=d.d_un.d_ptr;
            if(d.d_tag==DT_STRTAB)str=d.d_un.d_ptr;}}
    if(!ended||!rel||!nrel||nrel%sizeof(Elf32_Rel)||nrel>65536||!sym||!str)return 0;
    unsigned seen[4]={0};
    for(unsigned i=0;i<nrel/sizeof(Elf32_Rel);i++){Elf32_Rel r;if(!file_vaddr(iq,&r,sizeof r,rel+i*sizeof r))return 0;
        for(unsigned k=0;k<4;k++)if(r.r_offset==got[k]){Elf32_Sym s;char name[128];unsigned n=strlen(stock_slot_names[k])+1;
            if(ELF32_R_TYPE(r.r_info)!=R_ARM_JUMP_SLOT||n>sizeof name||
               !file_vaddr(iq,&s,sizeof s,sym+ELF32_R_SYM(r.r_info)*sizeof s)||
               !file_vaddr(iq,name,n,str+s.st_name)||memcmp(name,stock_slot_names[k],n))return 0;
            seen[k]++;}}
    for(unsigned i=0;i<4;i++)if(seen[i]!=1)return 0;
    return 1;
}
static void originals(const struct target *t,uint32_t out[4]) {
    out[0]=t->iq.bias+STOCK_UPDATE;out[1]=t->udd.bias+STOCK_COMMON;
    out[2]=t->udd.bias+STOCK_INFO;out[3]=t->udd.bias+STOCK_ONOFF;
}
static void config_init(const struct target *t,struct nx_cs_ram_config *c) {
    memset(c,0,sizeof *c);c->magic=NX_CS_RAM_MAGIC;c->abi=NX_CS_RAM_ABI;c->enabled=1;
    c->original_update=t->iq.bias+STOCK_UPDATE;c->original_init=t->iq.bias+STOCK_INIT;
    c->original_common=t->udd.bias+STOCK_COMMON;c->original_info=t->udd.bias+STOCK_INFO;
    c->original_onoff=t->udd.bias+STOCK_ONOFF;c->original_ready=t->lens.bias+STOCK_READY;
    c->iq_slot=t->iq.bias+STOCK_IQ_SLOT;c->pp_slot=t->iq.bias+STOCK_PP_SLOT;
    c->operation_slot=t->lens.bias+STOCK_OPERATION_SLOT;c->detached_state=0xffffffffU;
}
static void target_close(struct target *t) {
    if(t->mem>=0)close(t->mem);
    if(t->iq.fd>=0)close(t->iq.fd);
    if(t->udd.fd>=0)close(t->udd.fd);
    if(t->lens.fd>=0)close(t->lens.fd);
    if(t->libc.fd>=0)close(t->libc.fd);
}
static int target_open(unsigned pid,int fixture,struct target *t) {
    memset(t,0,sizeof *t);t->mem=t->iq.fd=t->udd.fd=t->lens.fd=t->libc.fd=-1;
    unsigned n=0;if(!threads(pid,after,&n)||!load_maps(pid))return 0;
    for(unsigned i=0;i<n;i++)if(after[i].tid==pid)t->start=after[i].start;
    char path[96],exe[512];snprintf(path,sizeof path,"/proc/%u/exe",pid);
    ssize_t size=readlink(path,exe,sizeof exe-1);if(size<0)return 0;exe[size]=0;
    if(strcmp(base(exe),fixture?"cs_live_fixture.arm":"di-camera-app-nx300"))return 0;
    if(!fixture){int fd=open(path,O_RDONLY|O_CLOEXEC);int ok=fd>=0&&file_hash(fd,STOCK_APP_SIZE,STOCK_APP_SHA);
        if(fd>=0)close(fd);
        if(!ok)return 0;}
    if(!t->start||!image_open(pid,"libiq-fw-slpcam.so",STOCK_IQ_SIZE,STOCK_IQ_SHA,&t->iq)||
       !image_open(pid,"libudd.so.3.1",STOCK_UDD_SIZE,STOCK_UDD_SHA,&t->udd)||
       !image_open(pid,"liblens-fw-prod.so",STOCK_LENS_SIZE,STOCK_LENS_SHA,&t->lens))return 0;
    if(!image_open(pid,"libc-2.13.so",STOCK_LIBC_SIZE,STOCK_LIBC_SHA,&t->libc)&&
       !image_open(pid,"libc.so.6",STOCK_LIBC_SIZE,STOCK_LIBC_SHA,&t->libc))return 0;
    snprintf(path,sizeof path,"/proc/%u/mem",pid);t->mem=open(path,O_RDONLY|O_CLOEXEC);
    if(t->mem<0||!dynamic_abi(&t->iq)||!code_check(t->mem,t->libc.bias+STOCK_SVC,stock_svc))return 0;
    const uint32_t bias[3]={t->iq.bias,t->udd.bias,t->lens.bias};
    for(unsigned i=0;i<sizeof stock_checks/sizeof stock_checks[0];i++){
        const struct stock_check *c=&stock_checks[i];uint32_t at=bias[c->image]+c->address;
        if(!cover(at,c->size,0,c->executable)||!bytes_match(t->mem,at,c->bytes,c->size))return 0;}
    for(unsigned i=0;i<4;i++){struct mapping *m=cover(t->iq.bias+got[i],4,1,0);
        if(!m||strcmp(m->perm,"rw-p")||m->inode!=t->iq.first->inode||
           !read_exact(t->mem,&t->slots[i],4,t->iq.bias+got[i]))return 0;}
    if(!cover(t->iq.bias+STOCK_IQ_SLOT,4,1,0)||!cover(t->iq.bias+STOCK_PP_SLOT,4,1,0)||
       !cover(t->lens.bias+STOCK_OPERATION_SLOT,4,1,0))return 0;
    return 1;
}
static int state_path(unsigned pid,char *path,unsigned size) {
    struct statfs fs;if(statfs("/var/run",&fs))return 0;
    if((unsigned long)fs.f_type!=0x01021994UL&&(unsigned long)fs.f_type!=0x858458f6UL)return 0;
    return snprintf(path,size,"/var/run/nx300m-cs-live-%u.state",pid)>0;
}
static int read_state(const char *path,unsigned pid,struct live_state *s) {
    int fd=open(path,O_RDONLY|O_CLOEXEC|O_NOFOLLOW);if(fd<0)return 0;struct stat64 st;
    int ok=!__fxstat64(3,fd,&st)&&S_ISREG(st.st_mode)&&st.st_size==sizeof *s&&read(fd,s,sizeof *s)==sizeof *s;
    close(fd);return ok&&s->magic==NX_CS_RAM_MAGIC&&s->abi==NX_CS_RAM_ABI&&s->pid==pid;
}
static int write_state(const char *path,const struct live_state *s) {
    int fd=open(path,O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC,0600);if(fd<0)return 0;
    int ok=write(fd,s,sizeof *s)==sizeof *s;int rc=close(fd);return ok&&!rc;
}
static int header_valid(const struct nx_cs_ram_header *h,unsigned size) {
    if(h->magic!=NX_CS_RAM_MAGIC||h->abi!=NX_CS_RAM_ABI||h->blob_size!=size||
       size<8192||size>1024*1024||h->code_size!=h->data_offset||h->data_offset<4096||h->data_offset%4096||
       h->data_offset>size||!h->data_size||h->data_size!=size-h->data_offset||
       h->config_size!=sizeof(struct nx_cs_ram_config)||h->config_offset%4||
       h->config_offset<h->data_offset||h->config_offset>size-h->config_size)return 0;
    const uint32_t entries[4]={h->update_entry,h->common_entry,h->info_entry,h->onoff_entry};
    for(unsigned i=0;i<4;i++)if(entries[i]<64||entries[i]>=h->code_size||entries[i]%4)return 0;
    for(unsigned i=0;i<4;i++)if(h->reserved[i])return 0;
    return 1;
}
static int read_blob(const char *path,unsigned char **bytes,struct nx_cs_ram_header *h) {
    int fd=open(path,O_RDONLY|O_CLOEXEC);if(fd<0)return 0;struct stat64 st;
    int ok=!__fxstat64(3,fd,&st)&&S_ISREG(st.st_mode)&&st.st_size<=1024*1024&&st.st_size>=sizeof *h;
    if(ok)ok=read_exact(fd,h,sizeof *h,0)&&header_valid(h,(unsigned)st.st_size);
    unsigned char *p=NULL;if(ok){p=malloc(h->blob_size);ok=p&&read_exact(fd,p,h->blob_size,0);}
    close(fd);if(!ok){free(p);return 0;}*bytes=p;return 1;
}
static int state_admit(const struct target *t,const struct live_state *s,const struct nx_cs_ram_header *h,
                       const unsigned char *blob,struct nx_cs_ram_config *c) {
    unsigned length=(h->blob_size+4095)&~4095U;
    if(s->start!=t->start||s->iq!=t->iq.bias||s->udd!=t->udd.bias||s->lens!=t->lens.bias||s->libc!=t->libc.bias||
       !s->region||s->region%4096||s->region>UINT32_MAX-length||s->length!=length||
       s->config!=s->region+h->config_offset)return 0;
    const uint32_t entries[4]={h->update_entry,h->common_entry,h->info_entry,h->onoff_entry};
    for(unsigned i=0;i<4;i++)if(s->entry[i]!=s->region+entries[i])return 0;
    struct mapping *code=cover(s->region,h->code_size,0,1),*data=cover(s->region+h->data_offset,h->data_size,1,0);
    if(!code||!data||code->inode||data->inode||strcmp(code->perm,"r-xp")||strcmp(data->perm,"rw-p")||
       !bytes_match(t->mem,s->region,blob,h->code_size)||!read_exact(t->mem,c,sizeof *c,s->config)||
       c->magic!=NX_CS_RAM_MAGIC||c->abi!=NX_CS_RAM_ABI||c->enabled>1)return 0;
    struct nx_cs_ram_config expected;config_init(t,&expected);
    return !memcmp(&c->original_update,&expected.original_update,10*4);
}
static int put_word(unsigned pid,int mem,uint32_t at,uint32_t value) {
    uint32_t actual;return poke(pid,at,&value,4)&&read_exact(mem,&actual,4,at)&&actual==value;
}
static int mutate(unsigned pid,const char *command,const char *blob_path) {
    int rc=3,attached=0,changed=0,new_state=0;uint32_t owned=0,owned_size=0;
    char path[128];struct live_state s;memset(&s,0,sizeof s);
    struct target t;memset(&t,0,sizeof t);t.mem=t.iq.fd=t.udd.fd=t.lens.fd=t.libc.fd=-1;
    unsigned char *blob=NULL;struct nx_cs_ram_header h;struct nx_cs_ram_config c;uint32_t orig[4]={0};
    const char *stage="blob/state-admission";
    if(!state_path(pid,path,sizeof path)||!read_blob(blob_path,&blob,&h))goto done;
    int existing=access(path,F_OK)==0;
    if(existing&&!read_state(path,pid,&s))goto done;
    if(!existing&&strcmp(command,"apply"))goto done;
    stage="all-thread-attach";attached=1;if(!attach_all(pid))goto done;
    stage="stock-admission";if(!target_open(pid,1,&t))goto done;originals(&t,orig);
    struct stopped *main_thread=NULL;for(unsigned i=0;i<nstops;i++)if(stops[i].tid==pid)main_thread=&stops[i];
    if(!main_thread)goto done;
    if(existing){
        stage="existing-image-admission";if(!state_admit(&t,&s,&h,blob,&c))goto done;
        int installed=1,restored=1;for(unsigned i=0;i<4;i++){if(t.slots[i]!=s.entry[i])installed=0;if(t.slots[i]!=orig[i])restored=0;}
        if(!installed&&!restored)goto done;
        if(!strcmp(command,"restore")){
            stage="disable-and-drain";
            if(!put_word(pid,t.mem,s.config+offsetof(struct nx_cs_ram_config,enabled),0))goto done;
            if(c.inflight||c.busy){fprintf(stderr,"CS restore draining; hooks disabled, retry after wrappers return.\n");rc=4;goto done;}
            if(c.request_seq!=c.done_seq){
                if(!put_word(pid,t.mem,s.config+offsetof(struct nx_cs_ram_config,request_action),NX_CS_NONE)||
                   !put_word(pid,t.mem,s.config+offsetof(struct nx_cs_ram_config,phase),NX_CS_REFUSED)||
                   !put_word(pid,t.mem,s.config+offsetof(struct nx_cs_ram_config,reason),NX_CS_REQUEST_CANCELLED)||
                   !put_word(pid,t.mem,s.config+offsetof(struct nx_cs_ram_config,done_seq),c.request_seq))goto done;}
            changed=1;for(unsigned i=0;i<4;i++)if(!put_word(pid,t.mem,t.iq.bias+got[i],orig[i]))goto done;
            rc=0;goto done;
        }
        if(!strcmp(command,"apply")){
            if(installed&&c.enabled){rc=0;goto done;}
            if(!restored||c.enabled||c.inflight||c.busy||c.request_seq!=c.done_seq)goto done;
            if(!put_word(pid,t.mem,s.config+offsetof(struct nx_cs_ram_config,enabled),1))goto done;
        }else{
            if(!installed||!c.enabled||c.inflight||c.busy||c.request_seq!=c.done_seq||c.request_seq==UINT32_MAX)goto done;
            uint32_t action=!strcmp(command,"request-default")?NX_CS_DEFAULT:NX_CS_STOCK;
            stage="request-publication";
            if(!put_word(pid,t.mem,s.config+offsetof(struct nx_cs_ram_config,request_action),action)||
               !put_word(pid,t.mem,s.config+offsetof(struct nx_cs_ram_config,phase),NX_CS_PENDING)||
               !put_word(pid,t.mem,s.config+offsetof(struct nx_cs_ram_config,reason),NX_CS_OK)||abort_requested||
               !put_word(pid,t.mem,s.config+offsetof(struct nx_cs_ram_config,request_seq),c.request_seq+1))goto done;
            rc=0;goto done;
        }
    }else{
        for(unsigned i=0;i<4;i++)if(t.slots[i]!=orig[i])goto done;
        config_init(&t,&c);memcpy(blob+h.config_offset,&c,sizeof c);
        owned_size=(h.blob_size+4095)&~4095U;uint32_t args[6]={0,owned_size,3,0x22,0xffffffff,0},result;
        stage="mmap2";if(!remote_syscall(main_thread,t.libc.bias+STOCK_SVC,192,args,&owned)||kernel_error(owned)||owned%4096){owned=0;goto done;}
        stage="private-blob-copy";if(!load_maps(pid)||!cover(owned,owned_size,1,0)||!poke(pid,owned,blob,h.blob_size))goto done;
        uint32_t flush[6]={owned,owned+h.code_size,0,0,0,0},protect[6]={owned,h.code_size,5,0,0,0};
        stage="cacheflush/mprotect";
        if(!remote_syscall(main_thread,t.libc.bias+STOCK_SVC,0xf0002,flush,&result)||kernel_error(result)||
           !remote_syscall(main_thread,t.libc.bias+STOCK_SVC,125,protect,&result)||kernel_error(result)||!load_maps(pid))goto done;
        s.magic=NX_CS_RAM_MAGIC;s.abi=NX_CS_RAM_ABI;s.pid=pid;s.start=t.start;
        s.iq=t.iq.bias;s.udd=t.udd.bias;s.lens=t.lens.bias;s.libc=t.libc.bias;s.region=owned;s.length=owned_size;
        s.entry[0]=owned+h.update_entry;s.entry[1]=owned+h.common_entry;s.entry[2]=owned+h.info_entry;s.entry[3]=owned+h.onoff_entry;
        s.config=owned+h.config_offset;
        if(!state_admit(&t,&s,&h,blob,&c)||abort_requested||!write_state(path,&s))goto done;
        new_state=1;
    }
    stage="four-GOT-slot-commit";changed=1;
    for(unsigned i=0;i<4;i++)if(abort_requested||!put_word(pid,t.mem,t.iq.bias+got[i],s.entry[i]))goto done;
    rc=0;
done:
    if(rc&&changed){for(unsigned i=0;i<4;i++)if(!put_word(pid,t.mem,t.iq.bias+got[i],t.slots[i]))transaction_poisoned=1;
        if(!put_word(pid,t.mem,s.config+offsetof(struct nx_cs_ram_config,enabled),0))transaction_poisoned=1;}
    if(rc&&owned&&!changed&&!transaction_poisoned){struct stopped *thread=NULL;
        for(unsigned i=0;i<nstops;i++)if(stops[i].tid==pid)thread=&stops[i];
        uint32_t args[6]={owned,owned_size,0,0,0,0},result;
        if(thread&&remote_syscall(thread,t.libc.bias+STOCK_SVC,91,args,&result)&&!kernel_error(result)){
            if(new_state)unlink(path);
        }else fprintf(stderr,"Unused CS mapping retained after failed admission.\n");}
    free(blob);target_close(&t);
    if(attached&&!detach_all()){fprintf(stderr,"FATAL: not every target thread detached.\n");return 5;}
    if(transaction_poisoned){fprintf(stderr,"FATAL: transaction recovery incomplete.\n");return 5;}
    if(rc)fprintf(stderr,"CS mutation failed/pending at %s (errno=%d), exit=%d.\n",stage,errno,rc);
    else printf("{\"command\":\"%s\",\"pid\":%u,\"fixture\":true,\"accepted\":true,\"region\":%u,\"mapping_retained\":true,\"physical_color_verified\":false}\n",command,pid,s.region);
    return rc;
}
static int supervised_mutate(unsigned pid,const char *command,const char *blob) {
    char path[128];if(!state_path(pid,path,sizeof path))return 3;
    snprintf(path,sizeof path,"/var/run/nx300m-cs-worker-%u.log",(unsigned)getpid());
    int log=open(path,O_RDWR|O_CREAT|O_EXCL|O_CLOEXEC,0600);if(log<0)return 3;
    pid_t child=fork();if(child<0){close(log);unlink(path);return 3;}
    if(!child){dup2(log,1);dup2(log,2);close(log);signal(SIGUSR1,request_abort);signal(SIGTERM,request_abort);
        signal(SIGINT,request_abort);signal(SIGPIPE,SIG_IGN);exit(mutate(pid,command,blob));}
    unsigned long long deadline=now_ms()+15000;int status=0,finished=0;
    while(now_ms()<deadline){pid_t n=waitpid(child,&status,WNOHANG);if(n==child){finished=1;break;}if(n<0&&errno!=EINTR)break;
        struct timespec pause={0,10000000};nanosleep(&pause,NULL);}
    if(!finished){kill(child,SIGUSR1);printf("{\"accepted\":false,\"recovery_worker\":%u,\"recovery_log\":\"%s\"}\n",(unsigned)child,path);
        fprintf(stderr,"Recovery tracer retained until original registers and detach are verified.\n");close(log);return 5;}
    lseek(log,0,SEEK_SET);char buf[4096];ssize_t n;while((n=read(log,buf,sizeof buf))>0)fwrite(buf,1,n,stdout);
    close(log);unlink(path);return WIFEXITED(status)?WEXITSTATUS(status):5;
}
static int inspect(unsigned pid,int fixture,const char *blob_path) {
    struct target t;int valid=target_open(pid,fixture,&t),installed=0,restored=0,state_ok=0;struct nx_cs_ram_config c;memset(&c,0,sizeof c);
    uint32_t orig[4];if(valid){originals(&t,orig);restored=1;for(unsigned i=0;i<4;i++)if(t.slots[i]!=orig[i])restored=0;
        char path[128];struct live_state s;unsigned char *blob=NULL;struct nx_cs_ram_header h;
        if(blob_path&&state_path(pid,path,sizeof path)&&read_state(path,pid,&s)&&read_blob(blob_path,&blob,&h)&&state_admit(&t,&s,&h,blob,&c)){
            state_ok=1;installed=1;for(unsigned i=0;i<4;i++)if(t.slots[i]!=s.entry[i])installed=0;}free(blob);}
    if(!state_ok)memset(&c,0,sizeof c);
    printf("{\"command\":\"status\",\"pid\":%u,\"atomic\":false,\"target_writes\":0,\"stock_admission\":%s,\"rw_slots_original\":%s,\"ram_image_verified\":%s,\"ram_hooks_installed\":%s,\"enabled\":%u,\"request_seq\":%u,\"done_seq\":%u,\"phase\":%u,\"reason\":%u,\"inflight\":%u,\"busy\":%u",
        pid,valid?"true":"false",restored?"true":"false",state_ok?"true":"false",installed?"true":"false",c.enabled,c.request_seq,c.done_seq,c.phase,c.reason,c.inflight,c.busy);
    printf(",\"update_tid\":%u,\"raw_lens_state\":%d,\"ready_result\":%u,\"iq_ready\":%u,\"iq_shading\":%u,\"flags_before\":%u,\"flags_after\":%u,\"common_calls\":%u,\"common_rc\":",
        c.update_tid,(int32_t)c.operation_state,c.ready_result,c.iq_ready,c.iq_shading,c.flags_before,c.flags_after,c.common_calls);
    if(state_ok&&c.common_calls)printf("%d",(int32_t)c.common_rc);else printf("null");
    printf(",\"info_calls\":%u,\"info_rc\":null,\"onoff_calls\":%u,\"onoff_rc\":null,\"onoff_arg\":%u,\"order\":%u,\"production_mutations_enabled\":false,\"physical_color_verified\":false}\n",
        c.info_calls,c.onoff_calls,c.onoff_arg,c.order);
    target_close(&t);return valid&&(restored||installed)?0:2;
}
int main(int argc,char **argv) {
    const char *command="status",*blob=NULL;unsigned pid=0;int fixture=0,next=1;
    if(argc>1&&argv[1][0]!='-'){command=argv[1];next=2;}
    if(strcmp(command,"status")&&strcmp(command,"apply")&&strcmp(command,"restore")&&strcmp(command,"request-default")&&strcmp(command,"request-stock"))goto usage;
    for(int i=next;i<argc;i++){
        if(!strcmp(argv[i],"--fixture")){fixture=1;continue;}if(i+1>=argc)goto usage;const char *key=argv[i++];
        if(!strcmp(key,"--pid")){if(pid||!decimal(argv[i],&pid))goto usage;}
        else if(!strcmp(key,"--blob")){if(blob)goto usage;blob=argv[i];}else goto usage;}
    if(!pid)goto usage;
    if(!strcmp(command,"status"))return inspect(pid,fixture,blob);
    if(!fixture){fprintf(stderr,"Production CS mutations disabled: PP caller serialization investigation is incomplete. Read-only status remains available.\n");return 69;}
    if(!blob)goto usage;
    return supervised_mutate(pid,command,blob);
usage:
    fprintf(stderr,"usage: cs_live_loader.arm [status|apply|restore|request-default|request-stock] --pid PID [--blob FILE] [--fixture]\n");return 64;
}
