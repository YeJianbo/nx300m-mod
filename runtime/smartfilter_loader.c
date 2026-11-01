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
#include <signal.h>
#include <time.h>
#include "smartfilter_ram.h"
#include "smartfilter_stock.h"
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

struct ram_state {
    unsigned magic,abi,pid,build;
    unsigned long long start;
    unsigned bias[STOCK_IMAGES],region,length,config,runtime;
};
struct target {
    struct image images[STOCK_IMAGES];
    struct stat64 app_file;
    struct stat64 image_file[STOCK_IMAGES];
    int mem;
    unsigned long long start;
    unsigned slot[NX_SFR_SLOTS],original[NX_SFR_SLOTS],resolver[NX_SFR_SLOTS],value[NX_SFR_SLOTS];
    int fixture;
    struct nx_sfr_fixture_bindings fixture_api;
};
static int state_path(unsigned pid,char *path,unsigned size) {
    struct statfs fs;
    if(statfs("/var/run",&fs)||((unsigned long)fs.f_type!=0x01021994UL&&(unsigned long)fs.f_type!=0x858458f6UL)) return 0;
    return snprintf(path,size,"/var/run/nx300m-smartfilter-%u.state",pid)>0;
}
static int state_read(const char *path,unsigned pid,struct ram_state *s) {
    int fd=open(path,O_RDONLY|O_CLOEXEC|O_NOFOLLOW); if(fd<0) return 0;
    struct stat64 st;
    int ok=!__fxstat64(3,fd,&st)&&S_ISREG(st.st_mode)&&st.st_size==sizeof *s&&read(fd,s,sizeof *s)==sizeof *s;
    close(fd); return ok&&s->magic==NX_SFR_MAGIC&&s->abi==NX_SFR_ABI&&s->pid==pid;
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
static int target_sites(unsigned pid,int fixture,struct target *t) {
    for(unsigned i=0;i<sizeof stock_checks/sizeof stock_checks[0];++i) {
        const struct stock_check *c=&stock_checks[i]; unsigned address=t->images[c->image].bias+c->address;
        unsigned char actual[32];
        if(!c->size||!cover(address,c->size,0,1)||!read_exact(t->mem,actual,c->size,address)||memcmp(actual,c->bytes,c->size)) {
            fprintf(stderr,"Original code differs: image=%u offset=%x\n",c->image,c->address); return 0;
        }
    }
    for(unsigned i=0;i<NX_SFR_SLOTS;++i) {
        t->slot[i]=t->images[0].bias+stock_slots[i].slot;
        t->original[i]=t->images[stock_slots[i].image].bias+stock_slots[i].address;
        t->resolver[i]=t->images[0].bias+stock_slots[i].resolver;
        struct mapping *m=cover(t->slot[i],4,0,0);
        if(!m||m->inode!=t->images[0].first->inode||m->perm[2]=='x'||!read_exact(t->mem,&t->value[i],4,t->slot[i])) return 0;
    }
    t->fixture=fixture;
    return !fixture||fixture_bindings(pid,t);
}
static int target_open(unsigned pid,int fixture,struct target *t) {
    char path[96],exe[512]; snprintf(path,sizeof path,"/proc/%u/exe",pid);
    ssize_t n=readlink(path,exe,sizeof exe-1); if(n<0) return 0; exe[n]=0;
    if(strcmp(base(exe),fixture?"smartfilter_fixture.arm":"di-camera-app-nx300")) return 0;
    int fd=open(path,O_RDONLY|O_CLOEXEC);
    int ok=fd>=0&&!__fxstat64(3,fd,&t->app_file)&&
        (fixture||file_hash(fd,STOCK_APP_SIZE,STOCK_APP_SHA));
    if(fd>=0) close(fd);
    if(!ok) return 0;
    struct thread thread;
    if(!task_identity(pid,pid,&thread)||!load_maps(pid)) return 0;
    t->start=thread.start;
    for(unsigned i=0;i<STOCK_IMAGES;++i) {
        int ok=image_open(pid,stock_images[i].name,stock_images[i].size,stock_images[i].sha,&t->images[i]);
        const char *alias=i==4?"libc.so.6":i==5?"libpthread.so.0":NULL;
        if(!ok&&alias) ok=image_open(pid,alias,stock_images[i].size,stock_images[i].sha,&t->images[i]);
        if(!ok||__fxstat64(3,t->images[i].fd,&t->image_file[i])) { fprintf(stderr,"Original image admission failed: %s\n",stock_images[i].name); return 0; }
    }
    snprintf(path,sizeof path,"/proc/%u/mem",pid); t->mem=open(path,O_RDONLY|O_CLOEXEC); if(t->mem<0) return 0;
    return target_sites(pid,fixture,t);
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
    struct mapping expected[STOCK_IMAGES];
    for(unsigned i=0;i<STOCK_IMAGES;++i) expected[i]=*t->images[i].first;
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
            struct mapping *m=&maps[j],*e=&expected[i];
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
    if(t->fixture) {
        cfg->api=t->fixture_api.api;
        cfg->b2y_instance=t->fixture_api.b2y_instance;
        cfg->get_semaphore=t->fixture_api.get_semaphore;
    }
}
static int blob_read(const char *path,unsigned char **bytes,struct nx_sfr_header *h) {
    int fd=open(path,O_RDONLY|O_CLOEXEC|O_NOFOLLOW); if(fd<0) return 0;
    struct stat64 st;
    int ok=!__fxstat64(3,fd,&st)&&S_ISREG(st.st_mode)&&st.st_size>=8192&&st.st_size<=131072&&read_exact(fd,h,sizeof *h,0);
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
static int slots_match(const struct target *t,const struct ram_state *s,const struct nx_sfr_header *h,int installed) {
    for(unsigned i=0;i<NX_SFR_SLOTS;++i)
        if(installed?t->value[i]!=s->region+h->entry[i]:(t->value[i]!=t->original[i]&&t->value[i]!=t->resolver[i])) return 0;
    return 1;
}
static int resident(const struct target *t,const struct ram_state *s,const struct nx_sfr_header *h,
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
       !id_valid(cfg->look_id)||!table_valid(cfg->table)||runtime->initialized>3) return 0;
    struct nx_sfr_config expected; bind_config(t,&expected);
    if(memcmp(cfg->original,expected.original,sizeof cfg->original)||memcmp(&cfg->api,&expected.api,sizeof cfg->api)||
       cfg->b2y_instance!=expected.b2y_instance||cfg->get_semaphore!=expected.get_semaphore) return 0;
    return slots_match(t,s,h,0)||slots_match(t,s,h,1);
}
static void status(unsigned pid,const char *command,int installed,const struct nx_sfr_config *c,const struct nx_sfr_runtime *s) {
    printf("{\"command\":\"%s\",\"pid\":%u,\"installed\":%s,\"enabled\":%s,\"host_liveview_mode\":%u,\"host_still_mode\":%u,"
        "\"look_id\":\"%s\",\"requested_generation\":%u,\"applied_generation\":%u,\"strength\":%u,"
        "\"initialized\":%u,\"inflight\":%u,\"backend_active\":%u,\"captures\":%u,\"capture_waits\":%u,\"control_paused\":%u,"
        "\"frames\":%u,\"rendered\":%u,\"passthrough\":%u,\"last_error\":%d,\"fault\":%u,"
        "\"stock_effects\":%u,\"busy\":%u,\"errors\":%u,"
        "\"hook_preview\":{\"calls\":%u,\"last\":%u,\"mask\":\"0x%x\",\"ensure_last\":%d},"
        "\"hook_effect\":{\"calls\":%u,\"last\":%u,\"mask\":\"0x%x\"},"
        "\"hook_still_mode\":{\"calls\":%u,\"last\":%u,\"mask\":\"0x%x\"},"
        "\"hook_prepare\":%u,\"hook_finish\":%u,"
        "\"mapping_retained\":%s,\"hardware_pixels_verified\":false}\n",
        command,pid,installed?"true":"false",c->enabled?"true":"false",NX_SFR_LIVEVIEW_MODE,NX_SFR_STILL_HOST_MODE,c->look_id,c->generation,
        s->bridge.stats.applied_generation,c->strength,s->initialized,s->inflight,s->backend_active,s->captures,s->capture_waits,s->control_pause,
        s->bridge.stats.frames,s->bridge.stats.rendered,s->bridge.stats.passthrough,
        s->init_error?s->init_error:s->bridge.stats.last_error,s->bridge.fault,
        s->bridge.stats.stock_effects,s->bridge.stats.busy,s->bridge.stats.errors,
        s->preview_calls,s->preview_last,s->preview_mask,(int)s->ensure_last,
        s->effect_calls,s->effect_last,s->effect_mask,
        s->still_calls,s->still_last,s->still_mask,
        s->prepare_calls,s->finish_calls,c->abi==NX_SFR_ABI?"true":"false");
}
static int execute(unsigned pid,const char *command,const char *blob_path,const char *table,const char *look,unsigned strength,int fixture) {
    struct target t; target_init(&t);
    struct ram_state s={0}; struct nx_sfr_header h; struct nx_sfr_config cfg={0},prior_cfg={0};
    struct nx_sfr_runtime runtime={0}; unsigned short requested[NX_SF_TABLE_WORDS];
    unsigned char *blob=NULL; char path[128]; int rc=3,attempted=0,config_attempted=0,installed=0;
    unsigned long long stopped_at=0;
    if(!state_path(pid,path,sizeof path)||!blob_read(blob_path,&blob,&h)||
       (!strcmp(command,"apply")&&(!id_valid(look)||!table_read(table,requested)))) {
        fprintf(stderr,"Invalid native module/table or non-RAM state directory; no target changed.\n"); free(blob); return 3;
    }
    if(!target_open(pid,fixture,&t)) {
        fprintf(stderr,"Original ELF/code/slot admission failed.\n");target_close(&t);free(blob);return 3;
    }
    stopped_at=now_ms();
    if(!attach_all(pid)) {
        int detached=detach_all(); free(blob);
        target_close(&t);
        fprintf(stderr,"Thread admission failed; detached=%d.\n",detached);
        return detached&&!abort_requested?4:3;
    }
    if(!target_refresh(pid,fixture,&t)) { fprintf(stderr,"Original process or mappings changed during admission.\n"); goto done; }
    int has_state=access(path,F_OK)==0;
    if(has_state) {
        if(!state_read(path,pid,&s)||!resident(&t,&s,&h,blob,&cfg,&runtime)) {
            fprintf(stderr,"Resident module differs from this process/build. Restore with its matching loader; retained state must not be deleted.\n"); goto done;
        }
        installed=slots_match(&t,&s,&h,1);
    } else if(!slots_match(&t,&s,&h,0)) { fprintf(stderr,"An original call site is already owned; no replacement.\n"); goto done; }
    if(!strcmp(command,"status")) { status(pid,command,installed,&cfg,&runtime); rc=0; goto done; }
    /* Internal cancellation only reopens admission. It never changes the
     * selected profile, capture leases or a fault raised by the backend. */
    if(!strcmp(command,"resume")) {
        if(has_state) {
            unsigned paused=0;
            if(!poke(pid,s.runtime+offsetof(struct nx_sfr_runtime,control_pause),&paused,4)) goto done;
        }
        rc=0; goto done;
    }
    if(runtime.inflight||runtime.backend_active||runtime.captures||runtime.ipcs.held||runtime.ipcs.opened||runtime.ipcs.touched) {
        unsigned paused=1;
        if(!poke(pid,s.runtime+offsetof(struct nx_sfr_runtime,control_pause),&paused,4)) goto done;
        fprintf(stderr,"Native frame/capture in flight; preview admission paused until control transaction completes.\n"); rc=4; goto done;
    }
    for(unsigned i=0;has_state&&i<nstops;++i) if(stops[i].regs.r[15]>=s.region&&stops[i].regs.r[15]<s.region+h.code_size) { rc=4; goto done; }
    if(runtime.bridge.fault&&strcmp(command,"restore")) { fprintf(stderr,"Backend recovery required; restore the candidate first.\n"); goto done; }
    if(!has_state) {
        if(!strcmp(command,"restore")) { status(pid,command,0,&cfg,&runtime); rc=0; goto done; }
        struct stopped *leader=NULL;
        for(unsigned i=0;i<nstops;++i) if(stops[i].tid==pid) leader=&stops[i];
        if(!leader) goto done;
        bind_config(&t,&cfg); cfg.generation=1; strcpy(cfg.look_id,"stock");
        memcpy(blob+h.config_offset,&cfg,sizeof cfg);
        unsigned args[6]={0,h.blob_size,3,0x22,~0U,0},region=0,result;
        if(!remote_syscall(leader,t.images[4].bias+STOCK_SVC,192,args,&region)||kernel_error(region)||region%4096) goto done;
        if(!load_maps(pid)||!cover(region,h.blob_size,1,0)||!poke(pid,region,blob,h.blob_size)) goto done;
        unsigned flush[6]={region,region+h.code_size,0,0,0,0},protect[6]={region,h.code_size,5,0,0,0};
        if(!remote_syscall(leader,t.images[4].bias+STOCK_SVC,0xf0002,flush,&result)||kernel_error(result)||
           !remote_syscall(leader,t.images[4].bias+STOCK_SVC,125,protect,&result)||kernel_error(result)||!load_maps(pid)) goto done;
        s.magic=NX_SFR_MAGIC; s.abi=NX_SFR_ABI; s.pid=pid; s.build=h.build; s.start=t.start;
        for(unsigned i=0;i<STOCK_IMAGES;++i) s.bias[i]=t.images[i].bias;
        s.region=region; s.length=h.blob_size; s.config=region+h.config_offset; s.runtime=region+h.runtime_offset;
        if(!state_write(path,&s)) goto done;
    }
    prior_cfg=cfg;
    if(cfg.generation==UINT32_MAX) goto done;
    ++cfg.generation; cfg.enabled=strcmp(command,"restore")!=0;
    cfg.strength=!strcmp(command,"apply")?strength:0;
    memset(cfg.table,0,sizeof cfg.table); memset(cfg.look_id,0,sizeof cfg.look_id);
    strcpy(cfg.look_id,!strcmp(command,"apply")?look:"stock");
    if(!strcmp(command,"apply")) memcpy(cfg.table,requested,sizeof requested);
    if(abort_requested) goto done;
    config_attempted=1; if(!poke(pid,s.config,&cfg,sizeof cfg)) goto done;
    int installing=strcmp(command,"restore")!=0;
    for(unsigned i=0;i<NX_SFR_SLOTS;++i) {
        unsigned desired=installing?s.region+h.entry[i]:t.original[i],actual;
        attempted=(int)i+1;
        if(!poke(pid,t.slot[i],&desired,4)||!read_exact(t.mem,&actual,4,t.slot[i])||actual!=desired) goto done;
    }
    { struct nx_sfr_config check; if(!read_exact(t.mem,&check,sizeof check,s.config)||memcmp(&check,&cfg,sizeof check)) goto done; }
    { unsigned paused=0; if(!poke(pid,s.runtime+offsetof(struct nx_sfr_runtime,control_pause),&paused,4)) goto done; }
    runtime.control_pause=0;
    status(pid,command,installing,&cfg,&runtime); rc=0;
done:
    if(rc) {
        for(int i=attempted-1;i>=0;--i) { unsigned actual; if(!poke(pid,t.slot[i],&t.value[i],4)||!read_exact(t.mem,&actual,4,t.slot[i])||actual!=t.value[i]) transaction_poisoned=1; }
        if(config_attempted) { struct nx_sfr_config check; if(!poke(pid,s.config,&prior_cfg,sizeof prior_cfg)||!read_exact(t.mem,&check,sizeof check,s.config)||memcmp(&check,&prior_cfg,sizeof check)) transaction_poisoned=1; }
    }
    target_close(&t); free(blob);
    if(transaction_poisoned) { fprintf(stderr,"Context/rollback recovery incomplete; tracer retained.\n"); return 5; }
    if(!detach_all()) return 5;
    fprintf(stderr,"Target stop window: %llu ms; file verification ran before attach.\n",now_ms()-stopped_at);
    if(rc==3) fprintf(stderr,"SmartFilter admission/transaction failed; exit=3.\n");
    return rc;
}
static int supervise(unsigned pid,const char *command,const char *blob,const char *table,const char *look,unsigned strength,int fixture) {
    char path[128]; if(!state_path(pid,path,sizeof path)) return 3;
    snprintf(path,sizeof path,"/var/run/nx300m-smartfilter-worker-%u.log",(unsigned)getpid());
    int fd=open(path,O_RDWR|O_CREAT|O_EXCL|O_CLOEXEC|O_NOFOLLOW,0600); if(fd<0) return 3;
    pid_t child=fork(); if(child<0) { close(fd); unlink(path); return 3; }
    if(!child) {
        dup2(fd,1); dup2(fd,2); close(fd);
        signal(SIGUSR1,request_abort); signal(SIGTERM,request_abort); signal(SIGINT,request_abort); signal(SIGPIPE,SIG_IGN);
        /* A valid busy transaction first closes preview admission, then lets
         * target threads finish normally before the next stopped transaction.
         * This is control-event waiting, never a per-frame poll in the camera. */
        unsigned long long control_deadline=0;
        int result;
        for(;;) {
            result=execute(pid,command,blob,table,look,strength,fixture);
            if(result!=4||abort_requested) break;
            /* Hashing the native images precedes the first pause request.
             * Start the drain budget only after that request, and always give
             * the resumed target a subsequent transaction to commit it. */
            if(!control_deadline) control_deadline=now_ms()+2000;
            else if(now_ms()>=control_deadline) break;
            struct timespec pause={0,1000000}; nanosleep(&pause,NULL);
        }
        if(result&&result!=5&&!abort_requested) {
            int resumed=execute(pid,"resume",blob,NULL,NULL,0,fixture);
            fprintf(stderr,"Control transaction cancelled; preview admission restored=%s.\n",resumed?"false":"true");
        }
        exit(result);
    }
    unsigned long long deadline=now_ms()+15000; int finished=0,status_code=0;
    while(now_ms()<deadline) {
        pid_t n=waitpid(child,&status_code,WNOHANG); if(n==child) { finished=1; break; }
        if(n<0&&errno!=EINTR) break;
        struct timespec pause={0,10000000}; nanosleep(&pause,NULL);
    }
    if(!finished) { kill(child,SIGUSR1); close(fd); fprintf(stderr,"Recovery tracer %u remains active; log %s; do not terminate it.\n",(unsigned)child,path); return 5; }
    lseek(fd,0,SEEK_SET);
    FILE *log=fdopen(fd,"r"); char text[4096];
    if(!log) { close(fd); return 5; }
    while(fgets(text,sizeof text,log)) fputs(text,text[0]=='{'?stdout:stderr);
    fclose(log); unlink(path); return WIFEXITED(status_code)?WEXITSTATUS(status_code):5;
}
int main(int argc,char **argv) {
    const char *command="status",*blob=NULL,*table=NULL,*look=NULL;
    unsigned pid=0,strength=100; int fixture=0,first=1;
    if(argc>1&&argv[1][0]!='-') { command=argv[1]; first=2; }
    if(strcmp(command,"status")&&strcmp(command,"apply")&&strcmp(command,"stock")&&strcmp(command,"restore")) goto usage;
    for(int i=first;i<argc;++i) {
        if(!strcmp(argv[i],"--fixture")) { fixture=1; continue; }
        if(i+1>=argc) goto usage;
        if(!strcmp(argv[i],"--pid")) { if(!decimal(argv[++i],&pid)) goto usage; }
        else if(!strcmp(argv[i],"--blob")) blob=argv[++i];
        else if(!strcmp(argv[i],"--table")) table=argv[++i];
        else if(!strcmp(argv[i],"--look")) look=argv[++i];
        else if(!strcmp(argv[i],"--strength")) { const char *n=argv[++i]; if(!strcmp(n,"0")) strength=0; else if(!decimal(n,&strength)||strength>100) goto usage; }
        else goto usage;
    }
    if(!pid||!blob||(!strcmp(command,"apply")&&(!table||!look))) goto usage;
    return supervise(pid,command,blob,table,look,strength,fixture);
usage:
    fprintf(stderr,"usage: smartfilter_loader.arm [status|apply|stock|restore] --pid PID --blob FILE [--table FILE --look ID --strength 0..100] [--fixture]\n");
    return 64;
}
