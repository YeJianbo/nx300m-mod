/* Explicit RAM installation. Uses the existing stop/restore syscall machinery;
 * no target dlopen, instruction patch, permanent file or original page chmod. */
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
#include <sys/msg.h>
#include <signal.h>
#include <time.h>
#include "smartfilter_ram.h"
#include "smartfilter_stock.h"
extern int __fxstat64(int,int,struct stat64 *);
extern int __lxstat64(int,const char *,struct stat64 *);
extern unsigned long long old_strtoull(const char *,char **,int) __asm__("strtoull");
extern unsigned long old_strtoul(const char *,char **,int) __asm__("strtoul");
extern long old_strtol(const char *,char **,int) __asm__("strtol");
extern int old_sscanf(const char *,const char *,...) __asm__("sscanf");
#define strtoull old_strtoull
#define strtoul old_strtoul
#define strtol old_strtol
#define sscanf old_sscanf
static int admission_file_hash(int,unsigned,const char *);
#define NX_LOADER_FILE_HASH admission_file_hash
#include "live_loader_core.h"
#include "smartfilter_admission_cache.h"

/* Fault injection is admitted only for the named isolated fixture process. */
static int fixture_fail_slot=-1;
static int timing_requested=-1;
#ifdef NX_SFR_SESSION
static int session_requested=-1,calltrace_requested=-1;
#endif
static int keep_timing,fixture_keep_failure;
struct kept_timing {
    struct nx_sfr_config config;
    struct nx_sfr_runtime runtime;
};

struct ram_state {
    unsigned magic,abi,pid,build;
    unsigned long long start;
    unsigned bias[STOCK_IMAGES],region,length,config,runtime;
    unsigned profile,work_base,work_size;
};
struct target {
    struct image images[STOCK_IMAGES];
    struct stat64 app_file;
    struct stat64 image_file[STOCK_IMAGES];
    struct mapping image_mapping[STOCK_IMAGES];
    int mem;
    unsigned long long start;
    unsigned slot[NX_SFR_SLOTS],original[NX_SFR_SLOTS],resolver[NX_SFR_SLOTS],value[NX_SFR_SLOTS];
    int fixture;
    struct nx_sfr_fixture_bindings fixture_api;
};
#include "smartfilter_timing_export.h"
#include "smartfilter_probe_control.h"
static struct nx_sfr_expect pause_owner;

static int state_path(unsigned pid,char *path,unsigned size) {
    struct statfs fs;
    if(statfs("/var/run",&fs)||((unsigned long)fs.f_type!=0x01021994UL&&(unsigned long)fs.f_type!=0x858458f6UL)) return 0;
    return snprintf(path,size,"/var/run/nx300m-smartfilter-%u.state",pid)>0;
}
static int state_read(const char *path,unsigned pid,struct ram_state *s) {
    int fd=open(path,O_RDONLY|O_CLOEXEC|O_NOFOLLOW); if(fd<0) return 0;
    struct stat64 st;
    int ok=!__fxstat64(3,fd,&st)&&S_ISREG(st.st_mode)&&st.st_size==sizeof *s&&read(fd,s,sizeof *s)==sizeof *s;
    close(fd);
    if(!ok) { fprintf(stderr,"Resident state is unreadable or has an unexpected layout: %s expected_bytes=%u\n",path,(unsigned)sizeof *s); return 0; }
    if(s->magic!=NX_SFR_MAGIC||s->abi!=NX_SFR_ABI||s->pid!=pid) {
        fprintf(stderr,"Resident state identity differs: stored_abi=%u expected_abi=%u stored_pid=%u expected_pid=%u stored_build=%u\n",
                s->abi,NX_SFR_ABI,s->pid,pid,s->build); return 0;
    }
    return 1;
}
static int state_write(const char *path,const struct ram_state *s) {
    int fd=open(path,O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC|O_NOFOLLOW,0600); if(fd<0) return 0;
    int ok=write(fd,s,sizeof *s)==sizeof *s; int rc=close(fd);
    if(!ok||rc) { unlink(path); return 0; } return 1;
}
static void target_init(struct target *t) {
    memset(t,0,sizeof *t); t->mem=-1;
    for(unsigned i=0;i<STOCK_IMAGES;++i) t->images[i].fd=-1;
}
static void target_close(struct target *t) {
    if(t->mem>=0) close(t->mem);
    for(unsigned i=0;i<STOCK_IMAGES;++i) if(t->images[i].fd>=0) close(t->images[i].fd);
}
static int fixture_bindings(unsigned pid,struct target *t) {
    char path[64]; snprintf(path,sizeof path,"/proc/%u/exe",pid);
    int fd=open(path,O_RDONLY|O_CLOEXEC); if(fd<0) return 0;
    Elf32_Ehdr eh; int ok=read_exact(fd,&eh,sizeof eh,0)&&eh.e_type==ET_EXEC&&eh.e_machine==EM_ARM&&
        eh.e_shentsize==sizeof(Elf32_Shdr)&&eh.e_shnum>0&&eh.e_shnum<=256;
    unsigned address=0;
    for(unsigned i=0;ok&&i<eh.e_shnum;++i) {
        Elf32_Shdr sh,strings;
        ok=read_exact(fd,&sh,sizeof sh,eh.e_shoff+i*sizeof sh);
        if(!ok||sh.sh_type!=SHT_DYNSYM) continue;
        if(sh.sh_entsize!=sizeof(Elf32_Sym)||sh.sh_link>=eh.e_shnum||
           !read_exact(fd,&strings,sizeof strings,eh.e_shoff+sh.sh_link*sizeof strings)||strings.sh_size>1048576) { ok=0; break; }
        char *names=malloc(strings.sh_size);
        if(!names||!read_exact(fd,names,strings.sh_size,strings.sh_offset)) { free(names); ok=0; break; }
        for(unsigned j=0;j<sh.sh_size/sizeof(Elf32_Sym);++j) {
            Elf32_Sym sym;
            if(!read_exact(fd,&sym,sizeof sym,sh.sh_offset+j*sizeof sym)) { ok=0; break; }
            if(sym.st_name<strings.sh_size&&memchr(names+sym.st_name,0,strings.sh_size-sym.st_name)&&
               !strcmp(names+sym.st_name,"nx_sfr_fixture_bindings")&&sym.st_shndx!=SHN_UNDEF&&
               ELF32_ST_TYPE(sym.st_info)==STT_OBJECT&&sym.st_size==sizeof t->fixture_api) address=sym.st_value;
        }
        free(names);
    }
    close(fd);
    return ok&&address&&cover(address,sizeof t->fixture_api,1,0)&&read_exact(t->mem,&t->fixture_api,sizeof t->fixture_api,address)&&
        t->fixture_api.magic==NX_SFR_MAGIC&&t->fixture_api.size==sizeof t->fixture_api;
}
static int original_allowed(const struct target *t,unsigned slot,unsigned address) {
    if(address==t->images[stock_slots[slot].image].bias+stock_slots[slot].address) return 1;
#ifdef NX_SFR_SESSION
    for(unsigned i=0;i<sizeof stock_slot_alternatives/sizeof stock_slot_alternatives[0];++i) {
        const struct stock_slot_alternative *a=&stock_slot_alternatives[i];
        if(a->slot==slot&&address==t->images[a->image].bias+a->address) return 1;
    }
#endif
    return 0;
}
static int target_sites(unsigned pid,int fixture,struct target *t) {
    for(unsigned i=0;i<sizeof stock_checks/sizeof stock_checks[0];++i) {
        const struct stock_check *c=&stock_checks[i]; unsigned address=t->images[c->image].bias+c->address;
        unsigned char actual[32]={0};
        if(!c->size||!cover(address,c->size,0,1)||!read_exact(t->mem,actual,c->size,address)||memcmp(actual,c->bytes,c->size)) {
            fprintf(stderr,"Original code differs: image=%u file=%s offset=%x size=%u actual=",c->image,stock_images[c->image].name,c->address,c->size);
            for(unsigned j=0;j<c->size&&j<32;++j) fprintf(stderr,"%02x",actual[j]);
            fputs(" expected=",stderr);
            for(unsigned j=0;j<c->size&&j<32;++j) fprintf(stderr,"%02x",c->bytes[j]);
            fputc('\n',stderr); return 0;
        }
    }
    for(unsigned i=0;i<NX_SFR_SLOTS;++i) {
        unsigned patch=stock_slots[i].patch_image;
        t->slot[i]=t->images[patch].bias+stock_slots[i].slot;
        t->original[i]=t->images[stock_slots[i].image].bias+stock_slots[i].address;
        t->resolver[i]=t->images[patch].bias+stock_slots[i].resolver;
        struct mapping *m=cover(t->slot[i],4,1,0);
        if(!m||m->inode!=t->images[patch].first->inode||m->perm[2]=='x'||!read_exact(t->mem,&t->value[i],4,t->slot[i])) {
            fprintf(stderr,"Original slot mapping/read failed: slot=%u file=%s address=%08x errno=%d\n",
                    i,stock_images[patch].name,t->slot[i],errno); return 0;
        }
        if(original_allowed(t,i,t->value[i])) t->original[i]=t->value[i];
    }
    t->fixture=fixture;
    return !fixture||fixture_bindings(pid,t);
}
static int target_open(unsigned pid,int fixture,struct target *t) {
    char path[96],exe[512]; snprintf(path,sizeof path,"/proc/%u/exe",pid);
    ssize_t n=readlink(path,exe,sizeof exe-1); if(n<0) return 0; exe[n]=0;
    if(strcmp(base(exe),fixture?"smartfilter_fixture.arm":"di-camera-app-nx300")) {
        fprintf(stderr,"Original executable name differs: %s\n",exe); return 0;
    }
    struct thread thread;
    if(!task_identity(pid,pid,&thread)||!load_maps(pid)) return 0;
    t->start=thread.start;
    admission_cache_begin(pid,t->start,fixture);
    int fd=open(path,O_RDONLY|O_CLOEXEC);
    int ok=fd>=0&&!__fxstat64(3,fd,&t->app_file)&&
        (fixture||admission_file_hash(fd,STOCK_APP_SIZE,STOCK_APP_SHA));
    if(fd>=0) close(fd);
    if(!ok) { fprintf(stderr,"Original executable identity/hash failed: %s expected_bytes=%u\n",exe,STOCK_APP_SIZE); return 0; }
    for(unsigned i=0;i<STOCK_IMAGES;++i) {
        int ok=image_open(pid,stock_images[i].name,stock_images[i].size,stock_images[i].sha,&t->images[i]);
        const char *alias=i==4?"libc.so.6":i==5?"libpthread.so.0":NULL;
        if(!ok&&alias) ok=image_open(pid,alias,stock_images[i].size,stock_images[i].sha,&t->images[i]);
        if(!ok||__fxstat64(3,t->images[i].fd,&t->image_file[i])) { fprintf(stderr,"Original image admission failed: %s expected_bytes=%u errno=%d\n",stock_images[i].name,stock_images[i].size,errno); return 0; }
        t->image_mapping[i]=*t->images[i].first;
    }
    snprintf(path,sizeof path,"/proc/%u/mem",pid); t->mem=open(path,O_RDONLY|O_CLOEXEC); if(t->mem<0) return 0;
    if(!target_sites(pid,fixture,t)) return 0;
    admission_cache_save(); return 1;
}
static int same_file(const struct stat64 *a,const struct stat64 *b) {
    return a->st_dev==b->st_dev&&a->st_ino==b->st_ino&&a->st_size==b->st_size&&
        a->st_mtim.tv_sec==b->st_mtim.tv_sec&&a->st_mtim.tv_nsec==b->st_mtim.tv_nsec&&
        a->st_ctim.tv_sec==b->st_ctim.tv_sec&&a->st_ctim.tv_nsec==b->st_ctim.tv_nsec;
}
static int target_refresh(unsigned pid,int fixture,struct target *t) {
    /* Hash large original files while liveview is running. After stopping the
     * threads, recheck process lifetime, file identity, mappings, native code
     * and all mutable slots before any write. No hash runs in the stop window. */
    struct thread thread;
    if(!task_identity(pid,pid,&thread)||thread.start!=t->start||!load_maps(pid)) return 0;
    char path[64];snprintf(path,sizeof path,"/proc/%u/exe",pid);
    int fd=open(path,O_RDONLY|O_CLOEXEC);struct stat64 st;
    int ok=fd>=0&&!__fxstat64(3,fd,&st)&&same_file(&st,&t->app_file);
    if(fd>=0) close(fd);
    if(!ok) return 0;
    for(unsigned i=0;i<STOCK_IMAGES;++i) {
        struct mapping *found=NULL;
        for(unsigned j=0;j<nmaps;++j) {
            struct mapping *m=&maps[j],*e=&t->image_mapping[i];
            if(m->lo==e->lo&&m->hi==e->hi&&m->offset==e->offset&&m->inode==e->inode&&
               m->devmajor==e->devmajor&&m->devminor==e->devminor&&!strcmp(m->perm,e->perm)&&!strcmp(m->path,e->path)) found=m;
        }
        if(!found||__fxstat64(3,t->images[i].fd,&st)||!same_file(&st,&t->image_file[i])||st.st_size!=stock_images[i].size||
           st.st_ino!=found->inode||major(st.st_dev)!=found->devmajor||minor(st.st_dev)!=found->devminor) return 0;
        t->images[i].first=found;
    }
    return target_sites(pid,fixture,t);
}
static void bind_config(const struct target *t,struct nx_sfr_config *cfg) {
    memset(cfg,0,sizeof *cfg); cfg->abi=NX_SFR_ABI; cfg->size=sizeof *cfg; cfg->api.rgb3d_index=3;
    for(unsigned i=0;i<NX_SFR_SLOTS;++i) cfg->original[i]=t->original[i];
    for(unsigned i=0;i<sizeof stock_bindings/sizeof stock_bindings[0];++i) {
        const struct stock_binding *b=&stock_bindings[i]; unsigned address=t->images[b->image].bias+b->address;
        memcpy((unsigned char *)cfg+b->offset,&address,4);
    }
    for(unsigned i=0;i<NX_SFR_MODE_CALLS;++i) cfg->mode_lr[i]=t->images[0].bias+stock_mode_lr[i];
    cfg->work_lr=t->images[0].bias+stock_work_lr;
    cfg->work_execute_lr=t->images[0].bias+stock_work_execute_lr;
    if(t->fixture) {
        cfg->api=t->fixture_api.api;
        cfg->b2y_instance=t->fixture_api.b2y_instance;
        cfg->get_semaphore=t->fixture_api.get_semaphore;
        cfg->common_instance=t->fixture_api.common_instance;
#ifdef NX_SFR_SESSION
        cfg->udd_count=t->fixture_api.udd_count;
        cfg->capture_queue=t->fixture_api.capture_queue;
        cfg->liveview_instance=t->fixture_api.liveview_instance;
#endif
    }
}
static int blob_read(const char *path,unsigned char **bytes,struct nx_sfr_header *h) {
    int fd=open(path,O_RDONLY|O_CLOEXEC|O_NOFOLLOW); if(fd<0) return 0;
    struct stat64 st;
    int ok=!__fxstat64(3,fd,&st)&&S_ISREG(st.st_mode)&&st.st_size>=8192&&st.st_size<=NX_SFR_BLOB_MAX&&read_exact(fd,h,sizeof *h,0);
    if(ok) ok=h->magic==NX_SFR_MAGIC&&h->abi==NX_SFR_ABI&&h->build==NX_SFR_BUILD&&
        h->blob_size==(unsigned)st.st_size&&h->code_size==h->data_offset&&h->code_size>=4096&&!(h->code_size%4096)&&
        h->code_size<h->blob_size&&!(h->blob_size%4096)&&h->data_size==h->blob_size-h->data_offset&&
        h->config_size==sizeof(struct nx_sfr_config)&&h->runtime_size==sizeof(struct nx_sfr_runtime)&&
        h->config_offset>=h->data_offset&&!(h->config_offset%4)&&h->config_offset<=h->blob_size-h->config_size&&
        h->runtime_offset>=h->data_offset&&!(h->runtime_offset%4)&&h->runtime_offset<=h->blob_size-h->runtime_size&&
        (h->runtime_offset+h->runtime_size<=h->config_offset||h->config_offset+h->config_size<=h->runtime_offset);
    for(unsigned i=0;ok&&i<NX_SFR_SLOTS;++i) ok=h->entry[i]>=sizeof *h&&h->entry[i]<h->code_size&&!(h->entry[i]%4);
    unsigned char *p=ok?malloc(h->blob_size):NULL; if(ok) ok=p&&read_exact(fd,p,h->blob_size,0);
    close(fd); if(!ok) { free(p); return 0; } *bytes=p; return 1;
}
static int table_valid(const unsigned short *table) {
    for(unsigned i=0;i<NX_SF_TABLE_WORDS;++i)
        if(table[i]>1023||(i%NX_SF_CHANNEL_WORDS==729&&table[i])) return 0;
    return 1;
}
static int table_read(const char *path,unsigned short *table) {
    int fd=open(path,O_RDONLY|O_CLOEXEC|O_NOFOLLOW); if(fd<0) return 0;
    struct stat64 st;
    int ok=!__fxstat64(3,fd,&st)&&S_ISREG(st.st_mode)&&st.st_size==NX_SF_TABLE_WORDS*2U&&read_exact(fd,table,NX_SF_TABLE_WORDS*2U,0);
    close(fd); return ok&&table_valid(table);
}
static int id_valid(const char *id) {
    unsigned n=0; if(!id) return 0;
    for(;id[n];++n) if(n>=NX_SF_LOOK_ID_SIZE-1||!((id[n]>='a'&&id[n]<='z')||(id[n]>='0'&&id[n]<='9')||id[n]=='_'||id[n]=='-')) return 0;
    return n>0;
}
static unsigned owned_slots(const struct ram_state *s) { return s->profile==NX_SFR_S2?NX_SFR_SLOTS:NX_SFR_S1_SLOTS; }
static int slots_match(const struct target *t,const struct ram_state *s,const struct nx_sfr_header *h,int installed) {
    for(unsigned i=0;i<NX_SFR_SLOTS;++i)
        if(installed&&i<owned_slots(s)?t->value[i]!=s->region+h->entry[i]:
           (t->value[i]!=t->original[i]&&t->value[i]!=t->resolver[i])) {
            fprintf(stderr,"Original slot value differs: slot=%u file=%s address=%08x observed=%08x original=%08x resolver=%08x installed=%d\n",
                    i,stock_images[stock_slots[i].patch_image].name,t->slot[i],t->value[i],t->original[i],t->resolver[i],installed);
            return 0;
        }
    return 1;
}
static int work_mapping(const struct ram_state *s) {
    if(s->profile==NX_SFR_S1) return !s->work_base&&!s->work_size;
    if(s->profile!=NX_SFR_S2||!s->work_base||s->work_base%4096||s->work_size!=NX_SFR_WORK_BYTES||
       s->work_base>UINT32_MAX-s->work_size||
       !(s->work_base+s->work_size<=s->region||s->region+s->length<=s->work_base)) return 0;
    struct mapping *m=cover(s->work_base,s->work_size,1,0);
    return m&&!m->inode&&!strcmp(m->perm,"rw-p");
}
static int resident(struct target *t,const struct ram_state *s,const struct nx_sfr_header *h,
                    const unsigned char *blob,struct nx_sfr_config *cfg,struct nx_sfr_runtime *runtime) {
    if(s->start!=t->start||s->build!=h->build||!s->region||s->region%4096||s->region>UINT32_MAX-h->blob_size||
       s->length!=h->blob_size||s->config!=s->region+h->config_offset||s->runtime!=s->region+h->runtime_offset) return 0;
    for(unsigned i=0;i<STOCK_IMAGES;++i) if(s->bias[i]!=t->images[i].bias) return 0;
    struct mapping *rx=cover(s->region,h->code_size,0,1),*rw=cover(s->region+h->data_offset,h->data_size,1,0);
    if(!rx||!rw||rx->inode||rw->inode||strcmp(rx->perm,"r-xp")||strcmp(rw->perm,"rw-p")) return 0;
    unsigned char actual[4096];
    for(unsigned off=0;off<h->code_size;off+=sizeof actual)
        if(!read_exact(t->mem,actual,sizeof actual,s->region+off)||memcmp(actual,blob+off,sizeof actual)) return 0;
    if(!read_exact(t->mem,cfg,sizeof *cfg,s->config)||!read_exact(t->mem,runtime,sizeof *runtime,s->runtime)||
       cfg->abi!=NX_SFR_ABI||cfg->size!=sizeof *cfg||cfg->enabled>1||cfg->strength>100||!cfg->generation||
       !id_valid(cfg->look_id)||!table_valid(cfg->table)||runtime->initialized>3||
       cfg->profile!=s->profile||cfg->profile>NX_SFR_S2||cfg->insert_enabled>1||
       cfg->work_base!=s->work_base||cfg->work_size!=s->work_size||!work_mapping(s)||cfg->timing_enabled>1||cfg->probe_once>1||
       runtime->timing.schema>NX_SFT_SCHEMA||runtime->timing.enabled>1||runtime->timing.armed>1||runtime->timing.frozen>1) return 0;
    if(runtime->timing.schema==NX_SFT_SCHEMA&&(!runtime->timing.window||runtime->timing.generation!=cfg->generation||
       !id_valid(runtime->timing.look_id)||strcmp(runtime->timing.look_id,cfg->look_id))) return 0;
    if(runtime->probe.state>NX_SFR_PROBE_BLOCKED||runtime->probe.outcome>NX_SFR_PROBE_INVALID||
       (runtime->probe.slot!=UINT32_MAX&&runtime->probe.slot>=NX_SFT_FRAMES)) return 0;
    if(cfg->probe_once&&(cfg->profile!=NX_SFR_S2||cfg->strength!=100||!cfg->timing_enabled||
       strcmp(cfg->look_id,"identity")||runtime->probe.window!=runtime->timing.window||
       runtime->probe.generation!=cfg->generation||runtime->probe.state==NX_SFR_PROBE_OFF)) return 0;
    /* Once hooked, the GOT no longer identifies libc vs libpthread. Recover
     * only a verified original from this validated resident configuration. */
    for(unsigned i=0;i<NX_SFR_SLOTS;++i) if(!original_allowed(t,i,cfg->original[i])) return 0;
    memcpy(t->original,cfg->original,sizeof t->original);
    struct nx_sfr_config expected; bind_config(t,&expected);
#ifdef NX_SFR_SESSION
    if(cfg->session_mode>1||cfg->calltrace_enabled>1||
       cfg->get_lifecycle!=expected.get_lifecycle||cfg->thread_create!=expected.thread_create||
       cfg->attr_init!=expected.attr_init||cfg->attr_stack!=expected.attr_stack||
       cfg->attr_destroy!=expected.attr_destroy||cfg->wait_syscall!=expected.wait_syscall||cfg->udd_count!=expected.udd_count||
       cfg->udd_mutex_outer!=expected.udd_mutex_outer||cfg->udd_mutex_device!=expected.udd_mutex_device||
       cfg->capture_queue!=expected.capture_queue||cfg->queue_send!=expected.queue_send||
       cfg->liveview_instance!=expected.liveview_instance||cfg->liveview_changed!=expected.liveview_changed||
       cfg->liveview_change_state!=expected.liveview_change_state||
       cfg->liveview_memory_restart!=expected.liveview_memory_restart||
       runtime->session.state>NX_IPS_FAILED||runtime->session.held>2||
       runtime->session_initialized>3||runtime->session_started>3) return 0;
#endif
    if(memcmp(cfg->original,expected.original,sizeof cfg->original)||memcmp(&cfg->api,&expected.api,sizeof cfg->api)||
       cfg->b2y_instance!=expected.b2y_instance||cfg->get_semaphore!=expected.get_semaphore||
       cfg->common_instance!=expected.common_instance||cfg->work_lr!=expected.work_lr||
       cfg->work_execute_lr!=expected.work_execute_lr||
       memcmp(cfg->mode_lr,expected.mode_lr,sizeof cfg->mode_lr)) return 0;
    return slots_match(t,s,h,0)||slots_match(t,s,h,1);
}
static void status(unsigned pid,const char *command,int installed,const struct nx_sfr_config *c,const struct nx_sfr_runtime *s,unsigned long long start) {
    char token[128]; probe_token(token,pid,start,NX_SFR_BUILD,s->timing.window,c->generation);
    unsigned refresh_requests=0,refresh_dispatches=0,refresh_pending=0,refresh_resumes=0,refresh_state=0,refresh_wakeups=0; int refresh_error=0;
#ifdef NX_SFR_SESSION
    refresh_requests=s->refresh_requests; refresh_dispatches=s->refresh_dispatches; refresh_error=s->refresh_error;
    refresh_pending=s->refresh_pending; refresh_resumes=s->refresh_resumes; refresh_state=s->refresh_state;
    refresh_wakeups=s->refresh_wakeups;
#endif
    printf("{\"command\":\"%s\",\"pid\":%u,\"installed\":%s,\"enabled\":%s,\"host_liveview_mode\":%u,\"host_still_mode\":%u,"
        "\"look_id\":\"%s\",\"requested_generation\":%u,\"applied_generation\":%u,\"strength\":%u,"
        "\"initialized\":%u,\"inflight\":%u,\"backend_active\":%u,\"captures\":%u,\"capture_waits\":%u,\"control_paused\":%u,"
        "\"frames\":%u,\"rendered\":%u,\"passthrough\":%u,\"last_error\":%d,\"fault\":%u,"
        "\"stock_effects\":%u,\"busy\":%u,\"errors\":%u,"
        "\"hook_preview\":{\"calls\":%u,\"last\":%u,\"mask\":\"0x%x\",\"ensure_last\":%d},"
        "\"hook_effect\":{\"calls\":%u,\"last\":%u,\"mask\":\"0x%x\"},"
        "\"hook_still_mode\":{\"calls\":%u,\"last\":%u,\"mask\":\"0x%x\"},"
        "\"hook_prepare\":%u,\"hook_finish\":%u,"
        "\"abi\":%u,\"profile\":%u,\"insert_enabled\":%u,\"routing_retained\":%s,"
        "\"work_base\":%u,\"work_size\":%u,\"restart_required_for_full_restore\":%s,"
        "\"s2_path\":%u,\"s2_fault\":%u,\"s2_frames\":%u,\"s2_fallback\":%u,"
        "\"mode_forged\":[%u,%u,%u,%u],\"mode_stock\":%u,\"work_managed\":%u,\"work_stock\":%u,"
        "\"mapping_retained\":%s,\"diag_window\":%u,\"diag_frames\":%u,\"diag_frozen\":%u,\"diag_dropped\":%u,\"diag_clock_errors\":%u,"
        "\"start\":%llu,\"build\":%u,\"token\":\"%s\",\"probe_once\":%u,\"probe_state\":%u,\"probe_outcome\":%u,"
        "\"refresh_requests\":%u,\"refresh_dispatches\":%u,\"refresh_error\":%d,"
        "\"refresh_pending\":%u,\"refresh_resumes\":%u,\"refresh_state\":%u,\"refresh_wakeups\":%u,\"hardware_pixels_verified\":false}\n",
        command,pid,installed?"true":"false",c->enabled?"true":"false",NX_SFR_LIVEVIEW_MODE,NX_SFR_STILL_HOST_MODE,c->look_id,c->generation,
        s->bridge.stats.applied_generation,c->strength,s->initialized,s->inflight,s->backend_active,s->captures,s->capture_waits,s->control_pause,
        s->bridge.stats.frames,s->bridge.stats.rendered,s->bridge.stats.passthrough,
        s->init_error?s->init_error:s->bridge.stats.last_error,s->bridge.fault,
        s->bridge.stats.stock_effects,s->bridge.stats.busy,s->bridge.stats.errors,
        s->preview_calls,s->preview_last,s->preview_mask,(int)s->ensure_last,
        s->effect_calls,s->effect_last,s->effect_mask,
        s->still_calls,s->still_last,s->still_mask,
        s->prepare_calls,s->finish_calls,c->abi,c->profile,c->insert_enabled,
        installed&&c->profile==NX_SFR_S2?"true":"false",c->work_base,c->work_size,
        installed&&c->profile==NX_SFR_S2?"true":"false",s->s2_path,s->s2_fault,s->s2_frames,s->s2_fallback,
        s->mode_forged[0],s->mode_forged[1],s->mode_forged[2],s->mode_forged[3],s->mode_stock,s->work_managed,s->work_stock,
        c->abi==NX_SFR_ABI?"true":"false",s->timing.window,s->timing.next_frame,
        s->timing.frozen,s->timing.dropped,s->timing.clock_errors,
        start,NX_SFR_BUILD,token,c->probe_once,s->probe.state,s->probe.outcome,refresh_requests,refresh_dispatches,refresh_error,
        refresh_pending,refresh_resumes,refresh_state,refresh_wakeups);
}
#ifdef NX_SFR_SESSION
static void session_export_to(FILE *out,const struct nx_sfr_config *c,const struct nx_sfr_runtime *s,unsigned pid,
                              const struct nx_sft_ledger *ledger,int fixture) {
    const struct nx_ips *p=&s->session;
    fprintf(out,"{\"type\":\"ipcs_open_window\",\"measurement_source\":\"%s\",\"control_intervals_complete\":%s,\"controls\":[]}\n",
           fixture?"arm_fixture":"physical_camera",ledger->complete?"true":"false");
    for(unsigned i=0;i<ledger->count;++i) {
        const struct nx_sft_control *v=&ledger->controls[i];
        fprintf(out,"{\"type\":\"ipcs_open_control\",\"begin\":[%u,%u],\"end\":[%u,%u]}\n",v->begin.sec,v->begin.nsec,v->end.sec,v->end.nsec);
    }
    fprintf(out,"{\"type\":\"ipcs_session\",\"pid\":%u,\"build\":%u,\"mode\":%u,\"state\":%u,\"held\":%u,"
           "\"owner_tid\":%u,\"active\":%u,\"opens\":%u,\"closes\":%u,\"busy\":%u,\"error\":%d,"
           "\"external_stop\":%u,\"drain_timeouts\":%u,\"open_errors\":%u,\"close_errors\":%u,"
           "\"unlock_errors\":%u,\"recoveries\":%u,\"recovery_closes\":%u,"
           "\"b2y_leases\":%u,\"stream_stopped\":%u,\"trace_next\":%u,\"trace_dropped\":%u}\n",
           pid,NX_SFR_BUILD,c->session_mode,p->state,p->held,p->owner_tid,p->active,p->opens,p->closes,p->busy,p->error,
           p->external_stop,p->drain_timeouts,p->open_errors,p->close_errors,p->unlock_errors,
           p->recoveries,p->recovery_closes,
           s->b2y_leases,s->stream_stopped,s->open_trace.next,s->open_trace.dropped);
    for(unsigned i=0;i<NX_IPCT_RECORDS&&i<s->open_trace.next;++i) {
        const struct nx_ipct_record *r=&s->open_trace.records[i];
        fprintf(out,"{\"type\":\"ipcs_open_call\",\"pid\":%u,\"build\":%u,\"committed\":%u,\"seq\":%u,"
               "\"tid\":%u,\"role\":%u,\"frame\":%u,\"udd_before_snapshot\":%u,\"udd_after_snapshot\":%u,"
               "\"start_sec\":%u,\"start_nsec\":%u,\"end_sec\":%u,\"end_nsec\":%u,\"kdd_transition\":null,\"phases\":[",
               pid,NX_SFR_BUILD,r->committed,r->sequence,r->tid,r->role,r->frame,
               r->udd_count_before,r->udd_count_after,r->start.wall.sec,r->start.wall.nsec,r->end.sec,r->end.nsec);
        for(unsigned j=0;j<NX_IPCT_PHASES;++j) {
            const struct nx_ipct_phase_data *p=&r->phase[j];
            fprintf(out,"%s{\"phase\":%u,\"count\":%u,\"valid\":%u,\"wall_us\":%u,\"cpu_us\":%u,\"rc\":%d,\"arg\":%u}",
                   j?",":"",j,p->count,p->valid,p->wall_us,p->cpu_us,p->rc,p->argument);
        }
        fputs("]}\n",out);
    }
}
static int session_export(const struct nx_sfr_config *c,const struct nx_sfr_runtime *s,unsigned pid,
                          const struct nx_sft_ledger *ledger,int fixture) {
    /* Serialize after detach. Reuse the existing verified media append rather
     * than depending on the menu's bounded text buffer to retain 64 calls. */
    session_export_to(stdout,c,s,pid,ledger,fixture);
    if(!diag_log_path) return 1;
    char path[]="/var/run/nx300m-sf-diag-open-XXXXXX";
    int fd=mkstemp(path);
    if(fd<0) return diag_save_system(&diag_save_result,"snapshot","mkstemp");
    FILE *out=fdopen(fd,"w");
    if(!out) { close(fd); return diag_save_system(&diag_save_result,"snapshot","fdopen"); }
    session_export_to(out,c,s,pid,ledger,fixture);
    int ok=!ferror(out);
    if(fflush(out)) ok=diag_save_system(&diag_save_result,"snapshot","fflush");
    if(fsync(fd)) ok=diag_save_system(&diag_save_result,"snapshot","fsync");
    if(fclose(out)) ok=diag_save_system(&diag_save_result,"snapshot","fclose");
    int persisted=ok&&diag_persist(path,diag_log_path,0,&diag_save_result);
    printf("{\"command\":\"open-export\",\"status\":\"%s\",\"snapshot_created\":%s,\"persisted\":%s,\"diag_file\":\"%s\"",
           persisted?"persisted":ok?"ram_only":"export_failed",ok?"true":"false",persisted?"true":"false",path);
    diag_save_fields(stdout,&diag_save_result); puts("}");
    return persisted;
}
#include "ipcs_kdd_probe.h"
#endif
static int execute(unsigned pid,const char *command,const char *blob_path,const char *table,const char *look,unsigned strength,int fixture,int s2,struct target *t) {
#ifdef NX_SFR_SESSION
    if(!strcmp(command,"kdd-probe")) return kdd_observe(pid,blob_path,fixture);
#endif
    memset(&diag_save_result,0,sizeof diag_save_result);
    struct ram_state s={0}; struct nx_sfr_header h; struct nx_sfr_config cfg={0},prior_cfg={0};
    struct nx_sfr_runtime runtime={0}; unsigned short requested[NX_SF_TABLE_WORDS];
    unsigned char *blob=NULL; char path[128]; int rc=3,attempted=0,config_attempted=0,installed=0;
    unsigned long long stopped_at=0;
    struct nx_sft_ledger ledger;
    struct nx_sft_time stop_begin={0,0,0},snapshot_at={0,0,0};
    unsigned diag_epoch=0; int status_pending=0,export_pending=0,probe_save_pending=0,timing_attempted=0,probe_attempted=0;
    unsigned allocated_work=0,allocated_region=0;
#ifdef NX_SFR_SESSION
    int session_control_attempted=0,refresh_control_attempted=0;
#endif
    struct stopped *allocation_leader=NULL;
    struct kept_timing *kept=NULL;
    const char *keep_status="no_data";
    int keep_pending=0;
    if(probe_once_requested) { probe_identity(requested); look="identity"; strength=100; s2=1; }
    if(!state_path(pid,path,sizeof path)||!blob_read(blob_path,&blob,&h)||
       (!strcmp(command,"apply")&&(!id_valid(look)||(!probe_once_requested&&!table_read(table,requested))))) {
        fprintf(stderr,"Invalid native module/table or non-RAM state directory; no target changed.\n"); free(blob); return 3;
    }
    unsigned long long admission_at=now_ms();int reused=t->mem>=0;
    if(!(reused?target_refresh(pid,fixture,t):target_open(pid,fixture,t))) {
        fprintf(stderr,"Original ELF/code/slot admission failed.\n");free(blob);return 3;
    }
    fprintf(stderr,"Control admission: %s elapsed_ms=%llu cache_hits=%u hashed_files=%u\n",
            reused?"reused_authenticated_files":admission_cache_hits?"cached_file_identity":"full_hash",
            now_ms()-admission_at,admission_cache_hits,admission_cache_misses);
    diag_ledger_load(&ledger,pid,t->start,h.build);
    /* Optional diagnostics must not make restore depend on allocation. Allocate
     * before stopping the target; the authoritative copy is taken after drain. */
    if(keep_timing&&fixture_keep_failure!=1) kept=malloc(sizeof *kept);
    stop_begin=diag_now(); stopped_at=now_ms();
    if(!attach_all(pid)) {
        int detached=detach_all();
        if(detached) diag_ledger_save(&ledger,stop_begin,diag_now(),0);
        free(kept); free(blob);
        fprintf(stderr,"Thread admission failed; detached=%d.\n",detached);
        return detached&&!abort_requested?4:3;
    }
    if(!target_refresh(pid,fixture,t)) { fprintf(stderr,"Original process or mappings changed during admission.\n"); goto done; }
    int has_state=access(path,F_OK)==0;
    if(has_state) {
        if(!state_read(path,pid,&s)||!resident(t,&s,&h,blob,&cfg,&runtime)) {
            fprintf(stderr,"Resident module differs from this process/build. Restore with its matching loader; retained state must not be deleted.\n"); goto done;
        }
        installed=slots_match(t,&s,&h,1);
        if(s2&&cfg.profile!=NX_SFR_S2) { fprintf(stderr,"S1/S2 installation types cannot be switched; restart the camera process.\n"); goto done; }
    } else if(!slots_match(t,&s,&h,0)) { fprintf(stderr,"An original call site is already owned; no replacement.\n"); goto done; }
    if(probe_expect.present&&(!has_state||!probe_expect_matches(pid,t->start,h.build,&cfg,&runtime))) {
        probe_failure(command,has_state?"identity_mismatch":"no_data",0); goto done;
    }
    if(has_state) {
        diag_epoch=runtime.timing.epoch+1;
        if(!diag_epoch) { diag_epoch=1; ledger.complete=0; }
        if(!poke(pid,s.runtime+offsetof(struct nx_sfr_runtime,timing.epoch),&diag_epoch,4)) goto done;
        runtime.timing.epoch=diag_epoch;
    }
    if(!strcmp(command,"status")) { status_pending=1; rc=0; goto done; }
#ifdef NX_SFR_SESSION
    if(!strcmp(command,"open-export")) { status_pending=2; rc=0; goto done; }
#endif
    if(!strcmp(command,"probe-save")) {
        if(!has_state||runtime.timing.schema!=NX_SFT_SCHEMA||!runtime.timing.window) {
            probe_failure(command,"no_data",0); goto done;
        }
        probe_save_pending=1; rc=0; goto done;
    }
    if(!strcmp(command,"diag-export")) {
        if(!has_state||runtime.timing.schema!=NX_SFT_SCHEMA||!runtime.timing.window) {
            probe_failure(command,"no_data",0); goto done;
        }
        unsigned frozen=1;
        if(!poke(pid,s.runtime+offsetof(struct nx_sfr_runtime,timing.frozen),&frozen,4)||
           !read_exact(t->mem,&runtime,sizeof runtime,s.runtime)) goto done;
        snapshot_at=diag_now(); export_pending=1; rc=0; goto done;
    }
    /* Internal cancellation only reopens admission. It never changes the
     * selected profile, capture leases or a fault raised by the backend. */
    if(!strcmp(command,"resume")) {
        if(has_state&&pause_owner.present&&pause_owner.pid==pid&&pause_owner.start==t->start&&
           pause_owner.build==h.build&&pause_owner.window==runtime.timing.window&&pause_owner.generation==cfg.generation) {
#ifdef NX_SFR_SESSION
            if(runtime.session.state==NX_IPS_FAILED||runtime.session.held==2) {
                fprintf(stderr,"Uncertain session retained with admission stopped.\n"); rc=3; goto done;
            }
            unsigned session_stop=cfg.session_mode&&cfg.enabled&&cfg.strength?0:1;
            if(!poke(pid,s.runtime+offsetof(struct nx_sfr_runtime,session.external_stop),&session_stop,4)) goto done;
#endif
            unsigned paused=0;
            if(!poke(pid,s.runtime+offsetof(struct nx_sfr_runtime,control_pause),&paused,4)) goto done;
            pause_owner.present=0;
        }
        rc=0; goto done;
    }
    int session_busy=0;
#ifdef NX_SFR_SESSION
    session_busy=runtime.session.held||runtime.session.active||runtime.session.state==NX_IPS_OPENING||
                 runtime.session.state==NX_IPS_DRAINING||runtime.session.mutex;
#endif
    if(runtime.inflight||runtime.backend_active||runtime.captures||runtime.ipcs.held||runtime.ipcs.opened||runtime.ipcs.touched||session_busy) {
        unsigned paused=1;
        if(!poke(pid,s.runtime+offsetof(struct nx_sfr_runtime,control_pause),&paused,4)) goto done;
#ifdef NX_SFR_SESSION
        /* Resume the target before waiting. An owner cannot release a device
         * while ptrace has stopped every thread. Never zero held/refcounts. */
        if(!poke(pid,s.runtime+offsetof(struct nx_sfr_runtime,session.external_stop),&paused,4)) goto done;
        if(runtime.session.state==NX_IPS_FAILED||runtime.session.held==2) {
            fprintf(stderr,"Session reference uncertain; admission stays stopped. No baseline release claimed.\n"); goto done;
        }
#endif
        pause_owner=(struct nx_sfr_expect){pid,h.build,runtime.timing.window,cfg.generation,t->start,1};
        fprintf(stderr,"Native frame/capture in flight; preview admission paused until control transaction completes.\n"); rc=4; goto done;
    }
    for(unsigned i=0;has_state&&i<nstops;++i) if(stops[i].regs.r[15]>=s.region&&stops[i].regs.r[15]<s.region+h.code_size) { rc=4; goto done; }
    if(runtime.bridge.fault&&strcmp(command,"restore")) { fprintf(stderr,"Backend recovery required; restore the candidate first.\n"); goto done; }
    if(!has_state) {
        if(!strcmp(command,"restore")) { status_pending=1; rc=0; goto done; }
        struct stopped *leader=NULL;
        for(unsigned i=0;i<nstops;++i) if(stops[i].tid==pid) leader=&stops[i];
        if(!leader) goto done;
        allocation_leader=leader;
        bind_config(t,&cfg); cfg.generation=1; strcpy(cfg.look_id,"stock");
        cfg.timing_enabled=timing_requested<0?1U:(unsigned)timing_requested;
        cfg.profile=s2?NX_SFR_S2:NX_SFR_S1;
        unsigned args[6]={0,h.blob_size,3,0x22,~0U,0},region=0,result;
        if(s2) {
            unsigned work_args[6]={0,NX_SFR_WORK_BYTES,3,0x22,~0U,0};
            if(!remote_syscall(leader,t->images[4].bias+STOCK_SVC,192,work_args,&cfg.work_base)||
               kernel_error(cfg.work_base)||cfg.work_base%4096) goto done;
            cfg.work_size=NX_SFR_WORK_BYTES; allocated_work=cfg.work_base;
        }
        memcpy(blob+h.config_offset,&cfg,sizeof cfg);
        if(!remote_syscall(leader,t->images[4].bias+STOCK_SVC,192,args,&region)||kernel_error(region)||region%4096) goto done;
        allocated_region=region;
        if(!load_maps(pid)||!cover(region,h.blob_size,1,0)||!poke(pid,region,blob,h.blob_size)) goto done;
        unsigned flush[6]={region,region+h.code_size,0,0,0,0},protect[6]={region,h.code_size,5,0,0,0};
        if(!remote_syscall(leader,t->images[4].bias+STOCK_SVC,0xf0002,flush,&result)||kernel_error(result)||
           !remote_syscall(leader,t->images[4].bias+STOCK_SVC,125,protect,&result)||kernel_error(result)||!load_maps(pid)) goto done;
        s.magic=NX_SFR_MAGIC; s.abi=NX_SFR_ABI; s.pid=pid; s.build=h.build; s.start=t->start;
        for(unsigned i=0;i<STOCK_IMAGES;++i) s.bias[i]=t->images[i].bias;
        s.region=region; s.length=h.blob_size; s.config=region+h.config_offset; s.runtime=region+h.runtime_offset;
        s.profile=cfg.profile; s.work_base=cfg.work_base; s.work_size=cfg.work_size;
        if(!work_mapping(&s)||!state_write(path,&s)) goto done;
        /* From this point recovery owns both mappings, even if hook commit
         * fails. Never replace an address a node may already have cached. */
        allocated_work=allocated_region=0;
    }
    if(keep_timing&&has_state&&cfg.profile==NX_SFR_S2&&cfg.enabled&&cfg.strength&&
       cfg.timing_enabled&&!cfg.probe_once&&runtime.timing.enabled&&
       runtime.timing.schema==NX_SFT_SCHEMA&&runtime.timing.window&&
       (runtime.timing.next_frame||runtime.timing.next_capture)) {
        if(kept) {
            kept->config=cfg; kept->runtime=runtime;
            kept->runtime.timing.frozen=1;
            snapshot_at=diag_now(); keep_pending=1;
        } else keep_status="snapshot_unavailable";
    }
    prior_cfg=cfg;
    if(cfg.generation==UINT32_MAX) goto done;
    ++cfg.generation; cfg.enabled=strcmp(command,"restore")!=0;
    if(timing_requested>=0) cfg.timing_enabled=(unsigned)timing_requested;
    cfg.insert_enabled=cfg.profile==NX_SFR_S2&&cfg.enabled;
    cfg.strength=!strcmp(command,"apply")?strength:0;
#ifdef NX_SFR_SESSION
    if(session_requested>=0) cfg.session_mode=(unsigned)session_requested;
    if(calltrace_requested>=0) cfg.calltrace_enabled=(unsigned)calltrace_requested;
    if(!strcmp(command,"restore")) cfg.session_mode=0;
#endif
    cfg.probe_once=probe_once_requested?1U:0U;
    if(cfg.probe_once) cfg.timing_enabled=1;
    memset(cfg.table,0,sizeof cfg.table); memset(cfg.look_id,0,sizeof cfg.look_id);
    strcpy(cfg.look_id,!strcmp(command,"apply")?look:"stock");
    if(!strcmp(command,"apply")) memcpy(cfg.table,requested,sizeof requested);
    if(abort_requested) goto done;
    config_attempted=1; if(!poke(pid,s.config,&cfg,sizeof cfg)) goto done;
    int installing=strcmp(command,"restore")!=0||cfg.profile==NX_SFR_S2;
    for(unsigned i=0;i<owned_slots(&s);++i) {
        unsigned desired=installing?s.region+h.entry[i]:t->original[i],actual;
        attempted=(int)i+1;
        if(!poke(pid,t->slot[i],&desired,4)||!read_exact(t->mem,&actual,4,t->slot[i])||actual!=desired) goto done;
        if(fixture&&fixture_fail_slot==(int)i) { fprintf(stderr,"Fixture slot failure injected after slot %u.\n",i); goto done; }
    }
    { struct nx_sfr_config check; if(!read_exact(t->mem,&check,sizeof check,s.config)||memcmp(&check,&cfg,sizeof check)) goto done; }
    struct nx_sft_timing armed;
    memset(&armed,0,sizeof armed); armed.schema=NX_SFT_SCHEMA;
    armed.enabled=cfg.timing_enabled&&cfg.enabled; armed.armed=armed.enabled;
    armed.window=runtime.timing.window+1; armed.epoch=diag_epoch;
    armed.generation=cfg.generation; armed.strength=cfg.strength;
    memcpy(armed.look_id,cfg.look_id,sizeof armed.look_id);
    timing_attempted=1;
    if(!armed.window||!poke(pid,s.runtime+offsetof(struct nx_sfr_runtime,timing),&armed,sizeof armed)) goto done;
    struct nx_sfr_probe probe={0};
    probe.state=cfg.probe_once?NX_SFR_PROBE_ARMED:NX_SFR_PROBE_OFF; probe.slot=UINT32_MAX;
    probe.window=armed.window; probe.generation=cfg.generation;
    probe.prepare=runtime.prepare_calls; probe.finish=runtime.finish_calls;
    probe_attempted=1;
    if(!poke(pid,s.runtime+offsetof(struct nx_sfr_runtime,probe),&probe,sizeof probe)) goto done;
#ifdef NX_SFR_SESSION
    {
        unsigned stop=cfg.session_mode&&cfg.enabled&&cfg.strength?0:1;
        session_control_attempted=1;
        if(!poke(pid,s.runtime+offsetof(struct nx_sfr_runtime,session.external_stop),&stop,4)) goto done;
        if(calltrace_requested>=0) {
            struct nx_ipct armed_trace={0}; armed_trace.enabled=cfg.calltrace_enabled;
            if(!poke(pid,s.runtime+offsetof(struct nx_sfr_runtime,open_trace),&armed_trace,sizeof armed_trace)) goto done;
        } else {
            unsigned trace=cfg.calltrace_enabled;
            if(!poke(pid,s.runtime+offsetof(struct nx_sfr_runtime,open_trace.enabled),&trace,4)) goto done;
        }
    }
#endif
    { unsigned paused=0; if(!poke(pid,s.runtime+offsetof(struct nx_sfr_runtime,control_pause),&paused,4)) goto done; }
#ifdef NX_SFR_SESSION
    /* A plain liveview need not run the hooked node callback yet. Publish a
     * single native item-change notification while the target is stopped;
     * its controller performs the normal stop/drain/restart after detach.
     * Changing only the table keeps the existing topology and sends nothing. */
    unsigned wanted=cfg.enabled&&cfg.strength&&cfg.insert_enabled&&!runtime.s2_fault&&!runtime.bridge.fault;
    if(cfg.profile==NX_SFR_S2&&!cfg.probe_once&&!runtime.refresh_pending&&
       !runtime.refresh_queued&&wanted!=runtime.s2_path) {
        int queue=-1; unsigned queued=1,requests=runtime.refresh_requests+1; int error=0;
        if(!cfg.capture_queue||!read_exact(t->mem,&queue,sizeof queue,(unsigned)cfg.capture_queue)||queue<0) goto done;
        refresh_control_attempted=1;
        if(!poke(pid,s.runtime+offsetof(struct nx_sfr_runtime,refresh_queued),&queued,4)||
           !poke(pid,s.runtime+offsetof(struct nx_sfr_runtime,refresh_requests),&requests,4)||
           !poke(pid,s.runtime+offsetof(struct nx_sfr_runtime,refresh_error),&error,4)) goto done;
        unsigned message[4]={1,0x02010034,0x39,0};
        if(msgsnd(queue,message,12,IPC_NOWAIT)) {
            fprintf(stderr,"Native refresh notification failed: errno=%d; selection rolled back.\n",errno); goto done;
        }
        fprintf(stderr,"Native refresh queued: generation=%u topology=%u; controller applies after detach.\n",cfg.generation,wanted);
    }
#endif
    pause_owner.present=0;
    runtime.control_pause=0; runtime.timing=armed; runtime.probe=probe;
    installed=installing; status_pending=1; rc=0;
done:
    if(rc) {
#ifdef NX_SFR_SESSION
        if(refresh_control_attempted) {
            unsigned queued=runtime.refresh_queued,requests=runtime.refresh_requests;
            if(!poke(pid,s.runtime+offsetof(struct nx_sfr_runtime,refresh_queued),&queued,4)||
               !poke(pid,s.runtime+offsetof(struct nx_sfr_runtime,refresh_requests),&requests,4)||
               !poke(pid,s.runtime+offsetof(struct nx_sfr_runtime,refresh_error),&runtime.refresh_error,4)) transaction_poisoned=1;
        }
        if(session_control_attempted) {
            unsigned stop=runtime.session.external_stop;
            if(!poke(pid,s.runtime+offsetof(struct nx_sfr_runtime,session.external_stop),&stop,4)||
               !poke(pid,s.runtime+offsetof(struct nx_sfr_runtime,open_trace),&runtime.open_trace,sizeof runtime.open_trace)) transaction_poisoned=1;
        }
#endif
        if(probe_attempted&&!poke(pid,s.runtime+offsetof(struct nx_sfr_runtime,probe),&runtime.probe,sizeof runtime.probe)) transaction_poisoned=1;
        if(timing_attempted&&!poke(pid,s.runtime+offsetof(struct nx_sfr_runtime,timing),&runtime.timing,sizeof runtime.timing)) transaction_poisoned=1;
        for(int i=attempted-1;i>=0;--i) { unsigned actual; if(!poke(pid,t->slot[i],&t->value[i],4)||!read_exact(t->mem,&actual,4,t->slot[i])||actual!=t->value[i]) transaction_poisoned=1; }
        if(config_attempted) { struct nx_sfr_config check; if(!poke(pid,s.config,&prior_cfg,sizeof prior_cfg)||!read_exact(t->mem,&check,sizeof check,s.config)||memcmp(&check,&prior_cfg,sizeof check)) transaction_poisoned=1; }
    }
    /* Only unpublished allocations, with no recovery file and no exposed
     * hook, can be reclaimed here. Published mappings survive all rollbacks. */
    if((allocated_region||allocated_work)&&allocation_leader&&!transaction_poisoned&&
       access(path,F_OK)<0&&errno==ENOENT) {
        unsigned regions[2]={allocated_region,allocated_work};
        unsigned lengths[2]={h.blob_size,NX_SFR_WORK_BYTES};
        for(unsigned i=0;i<2;++i) if(regions[i]) {
            unsigned args[6]={regions[i],lengths[i],0,0,0,0},result;
            if(!remote_syscall(allocation_leader,t->images[4].bias+STOCK_SVC,91,args,&result)||kernel_error(result)) {
                fprintf(stderr,"Unpublished RAM mapping retained after cleanup failure.\n"); break;
            }
        }
    }
    free(blob);
    if(transaction_poisoned) { free(kept); fprintf(stderr,"Context/rollback recovery incomplete; tracer retained.\n"); return 5; }
    if(!detach_all()) { free(kept); return 5; }
    struct nx_sft_time stop_end=diag_now();
    unsigned long long stopped_duration=now_ms()-stopped_at;
    diag_ledger_save(&ledger,stop_begin,stop_end,diag_epoch);
    if(!rc&&status_pending) {
#ifdef NX_SFR_SESSION
        if(status_pending==2) { if(!session_export(&cfg,&runtime,pid,&ledger,fixture)) rc=3; }
        else
#endif
        status(pid,command,installed,&cfg,&runtime,t->start);
    }
    if(!rc&&export_pending&&!diag_export(pid,t->start,h.build,fixture,&cfg,&runtime,&ledger,snapshot_at)) {
        char frozen[192]; probe_frozen_path(frozen,pid,t->start,h.build,runtime.timing.window);
        /* Quiet integrity probe must never add or replace the primary error.
         * A bare/partially signed file is not a validated snapshot. */
        int valid=diag_frozen_hash(frozen,0);
        probe_failure(command,"export_failed",valid); rc=3;
    }
    if(!rc&&probe_save_pending&&!probe_save(pid,t->start,h.build,fixture,&cfg,&runtime)) rc=3;
    if(!rc&&keep_timing) {
        /* Restore is committed and detached. Export only the old private copy;
         * failures here never roll back soft stop or change its exit status. */
        if(keep_pending) {
            diag_command="restore-timing";
            int saved;
            if(fixture_keep_failure==2) {
                diag_save_logic(&diag_save_result,"snapshot","fixture","injected_failure"); saved=0;
            } else saved=diag_export(pid,t->start,h.build,fixture,&kept->config,&kept->runtime,&ledger,snapshot_at);
            diag_command="diag-export";
            if(!saved) {
                char frozen[192];
                probe_frozen_path(frozen,pid,t->start,h.build,kept->runtime.timing.window);
                probe_failure("restore-timing","export_failed",diag_frozen_hash(frozen,0));
            }
        } else {
            printf("{\"command\":\"restore-timing\",\"status\":\"%s\",\"snapshot_created\":false,\"persisted\":false}\n",keep_status);
        }
    }
    free(kept);
    fprintf(stderr,"Target stop window: %llu ms; file verification ran before attach.\n",stopped_duration);
    if(rc==3) fprintf(stderr,"SmartFilter admission/transaction failed; exit=3.\n");
    return rc;
}
static int supervise(unsigned pid,const char *command,const char *blob,const char *table,const char *look,unsigned strength,int fixture,int s2) {
    int observation=0;
#ifdef NX_SFR_SESSION
    observation=!strcmp(command,"kdd-probe");
#endif
    char path[128]; if(!state_path(pid,path,sizeof path)) return 3;
    snprintf(path,sizeof path,"/var/run/nx300m-smartfilter-worker-%u.log",(unsigned)getpid());
    int fd=open(path,O_RDWR|O_CREAT|O_EXCL|O_CLOEXEC|O_NOFOLLOW,0600); if(fd<0) return 3;
    pid_t child=fork(); if(child<0) { close(fd); unlink(path); return 3; }
    if(!child) {
        if(!observation) dup2(fd,1);
        dup2(fd,2); close(fd);
#ifdef NX_SFR_SESSION
        /* Each diagnostic record must reach the shared log before stderr can
         * append a control message. Block buffering can split a JSON record. */
        setvbuf(stdout,NULL,_IOLBF,0);
#endif
        signal(SIGUSR1,request_abort); signal(SIGTERM,request_abort); signal(SIGINT,request_abort); signal(SIGPIPE,SIG_IGN);
        /* A valid busy transaction first closes preview admission, then lets
         * target threads finish normally before the next stopped transaction.
         * This is control-event waiting, never a per-frame poll in the camera. */
        unsigned long long control_deadline=0;
        /* Keep authenticated file descriptors only for this one command's
         * drain/recovery retries. Every attach rechecks the live sites. */
        struct target target;target_init(&target);
        int result;
        for(;;) {
            result=execute(pid,command,blob,table,look,strength,fixture,s2,&target);
            if(result!=4||abort_requested) break;
            /* Hashing the native images precedes the first pause request.
             * Start the drain budget only after that request, and always give
             * the resumed target a subsequent transaction to commit it. */
            if(!control_deadline) control_deadline=now_ms()+2000;
            else if(now_ms()>=control_deadline) break;
            struct timespec pause={0,1000000}; nanosleep(&pause,NULL);
        }
        if(result&&result!=5&&pause_owner.present) {
            /* After verified detach, cancellation still owes admission
             * restoration. This recovery does not submit the cancelled LUT. */
            sig_atomic_t cancelled=abort_requested;abort_requested=0;
            int resumed=execute(pid,"resume",blob,NULL,NULL,0,fixture,s2,&target);
            abort_requested|=cancelled;
            fprintf(stderr,"Control transaction cancelled; preview admission restored=%s.\n",resumed?"false":"true");
            if(resumed) result=5;
        }
        target_close(&target);
        exit(result);
    }
    unsigned long long deadline=now_ms()+(observation?85000:15000); int finished=0,status_code=0;
    while(now_ms()<deadline) {
        pid_t n=waitpid(child,&status_code,WNOHANG); if(n==child) { finished=1; break; }
        if(n<0&&errno!=EINTR) break;
        struct timespec pause={0,10000000}; nanosleep(&pause,NULL);
    }
    if(!finished) {
        kill(child,SIGUSR1);
        /* A read-only observer handles cancellation by detaching. Reap its
         * confirmed exit before reporting an unresolved tracer to the outer
         * launcher; slow preflight alone is not an uncertain target context. */
        if(observation) {
            unsigned long long grace=now_ms()+3000;
            while(now_ms()<grace) {
                pid_t n=waitpid(child,&status_code,WNOHANG);
                if(n==child) { finished=1; break; }
                if(n<0&&errno!=EINTR) break;
                struct timespec pause={0,10000000}; nanosleep(&pause,NULL);
            }
        }
        if(!finished) { close(fd); fprintf(stderr,"Recovery tracer %u remains active; log %s; do not terminate it.\n",(unsigned)child,path); return 5; }
    }
    lseek(fd,0,SEEK_SET);
    FILE *log=fdopen(fd,"r"); char text[4096];
    if(!log) { close(fd); return 5; }
    while(fgets(text,sizeof text,log)) fputs(text,text[0]=='{'?stdout:stderr);
    fclose(log); unlink(path); return WIFEXITED(status_code)?WEXITSTATUS(status_code):5;
}
int main(int argc,char **argv) {
    const char *command="status",*blob=NULL,*table=NULL,*look=NULL;
    unsigned pid=0,strength=100; int fixture=0,s2=0,first=1,strength_given=0;
    if(argc>1&&argv[1][0]!='-') { command=argv[1]; first=2; }
    if(strcmp(command,"status")&&strcmp(command,"apply")&&strcmp(command,"stock")&&strcmp(command,"restore")&&
       strcmp(command,"diag-export")&&strcmp(command,"probe-save")
#ifdef NX_SFR_SESSION
       &&strcmp(command,"open-export")&&strcmp(command,"kdd-probe")
#endif
       ) goto usage;
    for(int i=first;i<argc;++i) {
        if(!strcmp(argv[i],"--fixture")) { fixture=1; continue; }
        if(!strcmp(argv[i],"--s2")) { s2=1; continue; }
        if(!strcmp(argv[i],"--keep-timing")) { keep_timing=1; continue; }
        if(!strcmp(argv[i],"--probe-once")) { probe_once_requested=1; continue; }
        if(!strcmp(argv[i],"--ack-idle-no-capture")) { probe_contract_ack=1; continue; }
        if(i+1>=argc) goto usage;
#ifdef NX_SFR_SESSION
        if(!strcmp(argv[i],"--session")||!strcmp(argv[i],"--calltrace")) {
            int is_session=!strcmp(argv[i],"--session"); const char *value=argv[++i];
            if(strcmp(value,"0")&&strcmp(value,"1")) goto usage;
            if(is_session) session_requested=value[0]-'0'; else calltrace_requested=value[0]-'0';
            s2=1; continue;
        }
#endif
        if(!strcmp(argv[i],"--pid")) { if(!decimal(argv[++i],&pid)) goto usage; }
        else if(!strcmp(argv[i],"--fixture-fail-slot")) {
            const char *value=argv[++i]; unsigned n;
            if(!strcmp(value,"0")) n=0; else if(!decimal(value,&n)) goto usage;
            if(n>=NX_SFR_SLOTS) goto usage;
            fixture_fail_slot=(int)n;
        }
        else if(!strcmp(argv[i],"--fixture-keep-failure")) {
            const char *value=argv[++i];
            if(!strcmp(value,"alloc")) fixture_keep_failure=1;
            else if(!strcmp(value,"export")) fixture_keep_failure=2;
            else goto usage;
        }
        else if(!strcmp(argv[i],"--expect")) { if(probe_expect.present||!probe_parse_expect(argv[++i])) goto usage; }
        else if(!strcmp(argv[i],"--probe-file")) probe_file_path=argv[++i];
        else if(!strcmp(argv[i],"--source-id")) probe_source_id=argv[++i];
        else if(!strcmp(argv[i],"--diag-log")) diag_log_path=argv[++i];
        else if(!strcmp(argv[i],"--timing")) {
            const char *value=argv[++i];
            if(strcmp(value,"0")&&strcmp(value,"1")) goto usage;
            timing_requested=value[0]-'0';
        }
        else if(!strcmp(argv[i],"--blob")) blob=argv[++i];
        else if(!strcmp(argv[i],"--table")) table=argv[++i];
        else if(!strcmp(argv[i],"--look")) look=argv[++i];
        else if(!strcmp(argv[i],"--strength")) {
            const char *n=argv[++i]; strength_given=1;
            if(!strcmp(n,"0")) strength=0; else if(!decimal(n,&strength)||strength>100) goto usage;
        }
        else goto usage;
    }
    if(!pid||!blob||(fixture_fail_slot>=0&&!fixture)) goto usage;
#ifdef NX_SFR_SESSION
    s2=1;
    if((session_requested>=0||calltrace_requested>=0)&&
       strcmp(command,"apply")&&strcmp(command,"stock")&&strcmp(command,"restore")) goto usage;
#endif
    if(probe_once_requested) {
        if(strcmp(command,"apply")||table||look||strength_given||timing_requested==0) goto usage;
        s2=1; timing_requested=1;
    } else if(!strcmp(command,"apply")&&(!table||!look)) goto usage;
    if(probe_expect.present&&(probe_expect.pid!=pid||
       (strcmp(command,"status")&&strcmp(command,"diag-export")&&strcmp(command,"probe-save")
#ifdef NX_SFR_SESSION
        &&strcmp(command,"kdd-probe")
#endif
        ))) goto usage;
    if(!strcmp(command,"probe-save")) {
        if(!probe_expect.present||!probe_contract_ack||!probe_file_path||!probe_source_id||diag_log_path) goto usage;
    } else if(probe_file_path||probe_source_id||probe_contract_ack) goto usage;
    if((keep_timing&&strcmp(command,"restore"))||
       (fixture_keep_failure&&(!fixture||!keep_timing))) goto usage;
    if(diag_log_path&&((strcmp(command,"diag-export")&&!(keep_timing&&!strcmp(command,"restore"))
#ifdef NX_SFR_SESSION
       &&strcmp(command,"open-export")&&strcmp(command,"kdd-probe")
#endif
       )||
       (strcmp(diag_log_path,"/mnt/mmc/nx300m-mod/logs/fn-key.log")&&
        (!fixture||strcmp(diag_log_path,"/perf/fn-key.log"))))) goto usage;
    if(timing_requested>=0&&(!strcmp(command,"status")||!strcmp(command,"diag-export")||!strcmp(command,"probe-save"))) goto usage;
#ifdef NX_SFR_SESSION
    /* The observer retains its authenticated descriptors across first-frame
     * waiting and acquires control/media/preview leases only for attachment. */
    if(!strcmp(command,"kdd-probe")) return supervise(pid,command,blob,table,look,strength,fixture,s2);
#endif
    char lock_path[128];
    if(!state_path(pid,lock_path,sizeof lock_path)) return 3;
    snprintf(lock_path,sizeof lock_path,"/var/run/nx300m-smartfilter-control-%u.lock",pid);
    int lock=open(lock_path,O_RDWR|O_CREAT|O_CLOEXEC|O_NOFOLLOW,0600);
    if(lock<0||!diag_regular(lock,0)||flock(lock,LOCK_EX|LOCK_NB)) {
        if(lock>=0) close(lock);
        probe_failure(command,"control_busy",0); return 3;
    }
#ifdef NX_SFR_SESSION
    if(!strcmp(command,"restore")&&!kdd_recover_guard(pid,fixture)) {
        close(lock); probe_failure(command,"unresolved_operation",0); return 3;
    }
#endif
    int result=supervise(pid,command,blob,table,look,strength,fixture,s2);
    close(lock); return result;
usage:
    fprintf(stderr,"usage: smartfilter_loader.arm [status|apply|stock|restore|diag-export|probe-save] --pid PID --blob FILE [--table FILE --look ID --strength 0..100 | --probe-once] [--expect PID:START:BUILD:WINDOW:GEN] [--probe-file FILE --source-id DEV:INO --ack-idle-no-capture] [--timing 0|1] [--keep-timing (restore only)] [--s2] [--fixture]\n");
    return 64;
}
