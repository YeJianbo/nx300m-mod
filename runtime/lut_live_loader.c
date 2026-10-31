/* NX300M 1.15 live-loader admission inspector, old ARM glibc ABI.
 *
 * status opens target memory O_RDONLY and never attaches/stops a thread.
 * Stopping all threads around dlopen can deadlock on loader/malloc locks; resuming
 * other threads cannot make a borrowed arbitrary thread safe. A raw clone
 * lacks NPTL TLS/TID registration and is not a valid dlopen worker. A bounded
 * ptrace wait cannot justify restoring registers out of a partial dlopen.
 * This backend uses only mmap2/cacheflush/mprotect/munmap syscalls and restores
 * the interrupted ARM register image before resuming the process. VFP state
 * is checked independently; no target stack or signal mask is modified.
 *
 * apply instead maps a freestanding blob using pure syscalls. No old injector,
 * code cave, instruction overwrite, rootfs write, process restart, or remote
 * function call exists in this candidate.
 */
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
#include "lut_ram_blob.h"
#include "lut_live_stock.h"
#include "lut_ram_blob_build.h"

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

static int dynamic_abi(struct image *iq) {
    uint32_t rel=0,nrel=0,drel=0,ndrel=0,sym=0,str=0;int ended=0;
    for(unsigned i=0;i<iq->eh.e_phnum;i++)if(iq->ph[i].p_type==PT_DYNAMIC){
        unsigned count=iq->ph[i].p_filesz/sizeof(Elf32_Dyn);if(count>4096)return 0;
        for(unsigned j=0;j<count;j++){Elf32_Dyn d;if(!read_exact(iq->fd,&d,sizeof d,iq->ph[i].p_offset+j*sizeof d))return 0;
            if(d.d_tag==DT_NULL){ended=1;break;}
            if(d.d_tag==DT_JMPREL)rel=d.d_un.d_ptr;
            if(d.d_tag==DT_PLTRELSZ)nrel=d.d_un.d_val;
            if(d.d_tag==DT_REL)drel=d.d_un.d_ptr;
            if(d.d_tag==DT_RELSZ)ndrel=d.d_un.d_val;
            if(d.d_tag==DT_SYMTAB)sym=d.d_un.d_ptr;
            if(d.d_tag==DT_STRTAB)str=d.d_un.d_ptr;}}
    if(!ended||!rel||!nrel||nrel%sizeof(Elf32_Rel)||nrel>65536||!sym||!str)return 0;
    unsigned found=0;
    for(unsigned i=0;i<nrel/sizeof(Elf32_Rel);i++){Elf32_Rel r;if(!file_vaddr(iq,&r,sizeof r,rel+i*sizeof r))return 0;
        if(r.r_offset==0x3c2ab4){Elf32_Sym s;char name[12];
            if(ELF32_R_TYPE(r.r_info)!=R_ARM_JUMP_SLOT || !file_vaddr(iq,&s,sizeof s,sym+ELF32_R_SYM(r.r_info)*sizeof s)||
               !file_vaddr(iq,name,sizeof name,str+s.st_name)||memcmp(name,"ipcs_3d_lut",12))return 0;
            found++;}}
    if(!drel||!ndrel||ndrel%sizeof(Elf32_Rel)||ndrel>1024*1024)return 0;
    unsigned vfound=0;
    for(unsigned i=0;i<ndrel/sizeof(Elf32_Rel);i++){Elf32_Rel r;if(!file_vaddr(iq,&r,sizeof r,drel+i*sizeof r))return 0;
        if(r.r_offset==0x3bfd08){Elf32_Sym s;char name[34];
            if(ELF32_R_TYPE(r.r_info)!=R_ARM_ABS32 || !file_vaddr(iq,&s,sizeof s,sym+ELF32_R_SYM(r.r_info)*sizeof s)||
               s.st_value!=0x350dc0 || !file_vaddr(iq,name,sizeof name,str+s.st_name)||memcmp(name,"_ZN17CIpcParameterNode6UpdateEPv",32))return 0;
            vfound++;}}
    uint32_t value;return found==1 && vfound==1 && file_vaddr(iq,&value,4,0x3bfd08) && value==0;
}
static int stock_writer_slot(int mem,const struct image *iq,const struct image *udd,uint32_t value) {
    if(value==udd->bias+0x955c8) return 1;
    /* The untouched JUMP_SLOT is rebased PLT0 until the first stock call.
     * Admit that exact original code, then call the resolved UDD function
     * from the adapter; PLT0 itself requires the caller's PLT frame. */
    return value==iq->bias+STOCK_LUT_RESOLVER &&
        code_check(mem,value,stock_lut_resolver);
}
struct live_state {
    uint32_t magic,abi,pid;unsigned long long start;
    uint32_t iq,udd,region,length,update,ipcs,config,contexts,stride,count;
};
static int state_path(unsigned pid,char *path,unsigned size) {
    struct statfs fs;if(statfs("/var/run",&fs))return 0;
    if((unsigned long)fs.f_type!=0x01021994UL && (unsigned long)fs.f_type!=0x858458f6UL)return 0;
    return snprintf(path,size,"/var/run/nx300m-lut-live-%u.state",pid)>0;
}
static int read_blob(const char *path,unsigned char **bytes,struct nx_lut_ram_header *h,int known) {
    int fd=open(path,O_RDONLY|O_CLOEXEC);if(fd<0)return 0;
    struct stat64 st;int ok=!__fxstat64(3,fd,&st)&&S_ISREG(st.st_mode)&&st.st_size>=64&&st.st_size<=1024*1024;
    if(ok)ok=read_exact(fd,h,sizeof *h,0);
    if(ok)ok=h->magic==NX_LUT_RAM_MAGIC&&h->abi==NX_LUT_RAM_ABI&&(known||h->reserved[3]==NX_LUT_BLOB_BUILD)&&h->blob_size==(unsigned)st.st_size&&h->data_offset>=4096&&
        h->data_offset%4096==0&&h->data_offset<=h->blob_size&&h->code_size==h->data_offset&&h->data_size>0&&
        h->data_size==h->blob_size-h->data_offset&&h->config_size<=h->data_size&&
        (h->config_size==sizeof(struct nx_lut_ram_config)||(known&&h->config_size==200))&&h->config_offset>=h->data_offset&&h->config_offset%4==0&&
        h->config_offset<=h->blob_size-h->config_size&&h->update_entry>=64&&h->update_entry<h->code_size&&
        h->ipcs_entry>=64&&h->ipcs_entry<h->code_size&&!(h->update_entry%4)&&!(h->ipcs_entry%4)&&
        h->reserved[0]>=h->data_offset&&h->reserved[1]>=4&&h->reserved[1]%4==0&&h->reserved[2]>0&&h->reserved[2]<=32&&
        h->reserved[0]<=h->data_offset+h->data_size&&h->reserved[1]*h->reserved[2]<=h->data_offset+h->data_size-h->reserved[0];
    unsigned char *p=NULL;
    if(ok){p=malloc((unsigned)st.st_size);ok=p&&read_exact(fd,p,(unsigned)st.st_size,0);}
    close(fd);if(!ok){free(p);return 0;}*bytes=p;return 1;
}
static int write_state(const char *path,const struct live_state *s) {
    int fd=open(path,O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC,0600);if(fd<0)return 0;
    int ok=write(fd,s,sizeof *s)==sizeof *s;int rc=close(fd);return ok&&!rc;
}
static int read_state(const char *path,unsigned pid,struct live_state *s) {
    int fd=open(path,O_RDONLY|O_CLOEXEC|O_NOFOLLOW);if(fd<0)return 0;
    struct stat64 st;int ok=!__fxstat64(3,fd,&st)&&S_ISREG(st.st_mode)&&st.st_size==sizeof *s&&read(fd,s,sizeof *s)==sizeof *s;
    close(fd);return ok&&s->magic==NX_LUT_RAM_MAGIC&&s->abi==1&&s->pid==pid;
}
static int bytes_match(int mem,uint32_t address,const void *expected,unsigned size) {
    unsigned char actual[4096];const unsigned char *p=expected;
    while(size){unsigned n=size>sizeof actual?sizeof actual:size;
        if(!read_exact(mem,actual,n,address)||memcmp(actual,p,n))return 0;
        address+=n;p+=n;size-=n;}
    return 1;
}
/* Reuse changes neither code nor existing context records. The supplied blob
 * must describe exactly the installed image; a different build needs a new
 * process rather than replacement of code that may still be returning. */
static int reusable_image(int mem,const struct live_state *s,const struct nx_lut_ram_header *h,
                          const unsigned char *blob,const struct nx_lut_ram_config *expected,
                          unsigned long long start,uint32_t iq,uint32_t udd,struct nx_lut_ram_config *cfg) {
    unsigned length=(h->data_offset+h->data_size+4095)&~4095U;
    if(s->start!=start||s->iq!=iq||s->udd!=udd||!s->region||s->region%4096||s->length!=length||s->region>UINT32_MAX-length||
       s->update!=s->region+h->update_entry||s->ipcs!=s->region+h->ipcs_entry||s->config!=s->region+h->config_offset||
       s->contexts!=s->region+h->reserved[0]||s->stride!=h->reserved[1]||s->count!=h->reserved[2])return 0;
    struct mapping *code=cover(s->region,h->code_size,0,1),*data=cover(s->region+h->data_offset,h->data_size,1,0);
    if(!code||!data||code->inode||data->inode||strcmp(code->perm,"r-xp")||strcmp(data->perm,"rw-p")||
       !bytes_match(mem,s->region,blob,h->code_size)||!read_exact(mem,cfg,sizeof *cfg,s->config)||cfg->enabled>1)return 0;
    struct nx_lut_ram_config want=*expected;want.enabled=cfg->enabled;
    return !memcmp(cfg,&want,sizeof want);
}
static const char *marker_names[]={"nx300m-lut-ram-hook","nx300m-lut-hook","nx300m-lut-strength"};
static void clear_markers(unsigned pid) {
    static const unsigned order[3]={1,2,0};
    for(unsigned n=0;n<3;n++){unsigned i=order[n];char path[96];snprintf(path,sizeof path,"/var/run/%s",marker_names[i]);
        FILE *f=fopen(path,"r");unsigned owner=0;if(f){int count=fscanf(f,"%u",&owner);fclose(f);if(count==1&&owner==pid)unlink(path);}}
}
static int marker(unsigned index,const char *text,unsigned pid) {
    char path[96],tmp[128];snprintf(path,sizeof path,"/var/run/%s",marker_names[index]);snprintf(tmp,sizeof tmp,"%s.tmp.%u",path,pid);
    int fd=open(tmp,O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC,0600);if(fd<0)return 0;
    size_t n=strlen(text);int ok=write(fd,text,n)==(ssize_t)n;int rc=close(fd);
    if(ok&&!rc&&rename(tmp,path)==0)return 1;
    unlink(tmp);return 0;
}
static int publish_markers(unsigned pid,const struct live_state *s,const char *root) {
    char text[512];snprintf(text,sizeof text,"%u %llu %s %u %u\n",pid,s->start,root,s->region,s->length);
    if(!marker(0,text,pid))return 0;
    snprintf(text,sizeof text,"%u %s 1\n",pid,root);if(!marker(2,text,pid))return 0;
    snprintf(text,sizeof text,"%u %s\n",pid,root);return marker(1,text,pid);
}
static int invalidate_lut_cache(int mem,unsigned tid,uint32_t iq,uint32_t *cache_address) {
    uint32_t processor,still,node;
    *cache_address=0;
    if(!cover(iq+0x45e660,4,1,0)||!read_exact(mem,&processor,4,iq+0x45e660))return 0;
    if(!processor)return 1;
    if(processor%4||!cover(processor,8,1,0)||!read_exact(mem,&still,4,processor+4))return 0;
    if(!still)return 1;
    if(still%4||!cover(still,8,1,0)||!read_exact(mem,&node,4,still+4))return 0;
    uint32_t previous=0,seen[1024];unsigned count=0,matches=0;
    while(node){
        if(node%4||count==1024||!cover(node,0x14,1,0))return 0;
        for(unsigned i=0;i<count;i++)if(seen[i]==node)return 0;
        seen[count++]=node;uint32_t fields[5];if(!read_exact(mem,fields,sizeof fields,node)||fields[3]!=previous||!cover(fields[0],4,0,0))return 0;
        if(fields[0]==iq+0x3bfd08){matches++;*cache_address=node+4;}
        previous=node;node=fields[4];
    }
    if(matches>1)return 0;
    if(matches==1){uint32_t invalid=0xffffffffU,actual;
        if(!poke(tid,*cache_address,&invalid,4)||!read_exact(mem,&actual,4,*cache_address)||actual!=invalid)return 0;}
    return 1;
}
static int mutate(unsigned pid,const char *command,const char *blob_path,const char *root,int fixture) {
    char path[128];if(!state_path(pid,path,sizeof path)){fprintf(stderr,"State directory must be tmpfs/ramfs; no target changed.\n");return 3;}
    struct live_state state;memset(&state,0,sizeof state);
    int known=!strcmp(command,"restore-known"),restoring=!strcmp(command,"restore")||known,existing=0;
    if(!known&&(restoring||access(path,F_OK)==0)){
        if(!read_state(path,pid,&state)){
            fprintf(stderr,"Invalid existing live state; refusing mutation.\n");return 3;
        }
        existing=1;
    }
    if(!attach_all(pid)){int detached=detach_all();clear_markers(pid);fprintf(stderr,"Thread admission/attach failed; detached=%d; no slot write.\n",detached);return 3;}
    int rc=3,mem=-1,iq_ok=0,udd_ok=0,libc_ok=0,patched=0,state_written=0;
    const char *stage="process-identity";
    uint32_t owned_region=0,owned_size=0;
    int mapping_reused=0,already_installed=0,reuse_enabled=0;
    uint32_t restore_prev_a=0,restore_prev_b=0,restore_cache=0;int restore_attempted=0;
    struct image iq={0},udd={0},libc={0};iq.fd=udd.fd=libc.fd=-1;
    struct stopped *main_thread=NULL;for(unsigned i=0;i<nstops;i++)if(stops[i].tid==pid)main_thread=&stops[i];
    unsigned n=0;unsigned long long start=0;if(threads(pid,after,&n))for(unsigned i=0;i<n;i++)if(after[i].tid==pid)start=after[i].start;
    snprintf(path,sizeof path,"/proc/%u/exe",pid);char exe[512];ssize_t len=readlink(path,exe,sizeof exe-1);if(len<0)goto done;exe[len]=0;
    if(strcmp(base(exe),fixture?"lut_live_fixture.arm":"di-camera-app-nx300")){fprintf(stderr,"Unexpected target executable.\n");goto done;}
    if(!fixture){int appfd=open(path,O_RDONLY|O_CLOEXEC);int exact=appfd>=0&&file_hash(appfd,STOCK_APP_SIZE,STOCK_APP_SHA);
        if(appfd>=0)close(appfd);
        if(!exact){fprintf(stderr,"Camera executable is not the original NX300M 1.15 ELF.\n");goto done;}}
    if(!main_thread||!start||!load_maps(pid))goto done;
    if(!restoring)for(unsigned i=0;i<nmaps;i++)if(strstr(maps[i].path,"lut_port")||strstr(maps[i].path,"lut_node_port"))goto done;
    stage="stock-ELF-and-code";
    iq_ok=image_open(pid,"libiq-fw-slpcam.so",STOCK_IQ_SIZE,STOCK_IQ_SHA,&iq);
    udd_ok=image_open(pid,"libudd.so.3.1",STOCK_UDD_SIZE,STOCK_UDD_SHA,&udd);
    libc_ok=image_open(pid,"libc-2.13.so",STOCK_LIBC_SIZE,STOCK_LIBC_SHA,&libc);
    if(!libc_ok)libc_ok=image_open(pid,"libc.so.6",STOCK_LIBC_SIZE,STOCK_LIBC_SHA,&libc);
    snprintf(path,sizeof path,"/proc/%u/mem",pid);mem=open(path,O_RDONLY|O_CLOEXEC);
    if(!iq_ok||!udd_ok||!libc_ok||mem<0||!dynamic_abi(&iq)||
       !code_check(mem,iq.bias+0x350dc0,stock_update)||!code_check(mem,iq.bias+0x350e0c,stock_reset)||
       !code_check(mem,udd.bias+0x955c8,stock_lut)||!code_check(mem,libc.bias+STOCK_SVC,stock_svc))goto done;
    stage="RW-slot-admission";
    uint32_t slot_a=iq.bias+0x3bfd08,slot_b=iq.bias+0x3c2ab4,a=0,b=0;
    struct mapping *ma=cover(slot_a,4,1,0),*mb=cover(slot_b,4,1,0);
    if(!ma||!mb||ma->inode!=iq.first->inode||mb->inode!=iq.first->inode||!read_exact(mem,&a,4,slot_a)||!read_exact(mem,&b,4,slot_b))goto done;
    state_path(pid,path,sizeof path);
    if(known){
        /* Lost marker files do not establish ownership. An explicitly saved
         * image must match every immutable resident code byte before we even
         * disable it. Keep the allocation resident after restoring the slots. */
        unsigned char *saved=NULL;struct nx_lut_ram_header header;
        if(!read_blob(blob_path,&saved,&header,1)){
            fprintf(stderr,"Invalid saved compatibility blob; no target changed.\n");goto done;
        }
        uint32_t region=a>=header.update_entry?a-header.update_entry:0;
        struct mapping *rx=region?cover(region,header.code_size,0,1):NULL;
        struct mapping *rw=region&&region<=UINT32_MAX-header.blob_size?cover(region+header.data_offset,header.data_size,1,0):NULL;
        struct nx_lut_ram_config resident;
        int match=region&&region%4096==0&&rx&&rw&&!rx->inode&&!rw->inode&&
            !strcmp(rx->perm,"r-xp")&&!strcmp(rw->perm,"rw-p")&&b==region+header.ipcs_entry&&
            bytes_match(mem,region,saved,header.code_size)&&
            read_exact(mem,&resident,sizeof resident,region+header.config_offset)&&
            resident.abi==NX_LUT_RAM_ABI&&resident.size==header.config_size&&resident.enabled<=1&&
            resident.original_update==iq.bias+0x350dc0&&resident.original_reset==iq.bias+0x350e0c&&
            resident.original_ipcs==udd.bias+0x955c8&&resident.lut_vptr==iq.bias+0x3bfd08&&
            resident.factory_red==iq.bias+STOCK_FACTORY_RED&&memchr(resident.root,0,sizeof resident.root)&&!strcmp(resident.root,root);
        free(saved);
        if(!match){fprintf(stderr,"Saved blob does not match both resident hooks, code and config; no target changed.\n");goto done;}
        state.start=start;state.iq=iq.bias;state.udd=udd.bias;state.region=region;state.length=header.blob_size;
        state.update=region+header.update_entry;state.ipcs=region+header.ipcs_entry;
        state.config=region+header.config_offset;state.contexts=region+header.reserved[0];
        state.stride=header.reserved[1];state.count=header.reserved[2];
    }
    if(restoring){
        if(state.start!=start||state.iq!=iq.bias||state.udd!=udd.bias||
           (a!=state.update&&a!=iq.bias+0x350dc0)||(b!=state.ipcs&&!stock_writer_slot(mem,&iq,&udd,b))||
           !cover(state.config,sizeof(struct nx_lut_ram_config),1,0)||!cover(state.update,4,0,1)||!cover(state.ipcs,4,0,1))goto done;
        uint32_t disabled=0;if(!poke(pid,state.config+28,&disabled,4))goto done;
        /* Stop new contexts first. Existing wrapper contexts drain while all
         * threads run. Reattach/revalidate is handled by the next restore; a
         * busy context is never bypassed and the mapping is never unmapped. */
        int busy=0;
        for(unsigned i=0;i<state.count;i++){uint32_t tid;if(!read_exact(mem,&tid,4,state.contexts+i*state.stride))goto done;if(tid)busy=1;}
        if(busy){fprintf(stderr,"Restore draining: new contexts disabled, existing contexts still active; retry restore.\n");rc=4;goto done;}
        uint32_t cache_address=0;
        if(!fixture&&!invalidate_lut_cache(mem,pid,iq.bias,&cache_address)){
            fprintf(stderr,"Restore waiting: stock LUT node chain could not be validated; hooks retained, retry required.\n");rc=4;goto done;}
        restore_cache=cache_address;restore_prev_a=a;restore_prev_b=b;restore_attempted=1;
        a=iq.bias+0x350dc0;b=udd.bias+0x955c8;
        if(!poke(pid,slot_a,&a,4)||!poke(pid,slot_b,&b,4))goto done;
        uint32_t va,vb;if(!read_exact(mem,&va,4,slot_a)||!read_exact(mem,&vb,4,slot_b)||va!=a||vb!=b)goto done;
        rc=0;goto done;
    }
    stage="blob-admission";
    unsigned char *blob=NULL;struct nx_lut_ram_header h;
    if(!read_blob(blob_path,&blob,&h,0)){fprintf(stderr,"Invalid RAM blob/header.\n");goto done;}
    struct nx_lut_ram_config cfg;memset(&cfg,0,sizeof cfg);cfg.abi=1;cfg.size=sizeof cfg;
    cfg.original_update=iq.bias+0x350dc0;cfg.original_reset=iq.bias+0x350e0c;cfg.original_ipcs=udd.bias+0x955c8;
    cfg.lut_vptr=iq.bias+0x3bfd08;cfg.factory_red=iq.bias+STOCK_FACTORY_RED;cfg.enabled=1;strcpy(cfg.root,root);
    if(existing){
        stage="existing-RAM-image-admission";struct nx_lut_ram_config installed;
        int same=reusable_image(mem,&state,&h,blob,&cfg,start,iq.bias,udd.bias,&installed);
        free(blob);blob=NULL;
        if(!same){fprintf(stderr,"Existing RAM image, identity, root or code differs; validated restore required before upgrade.\n");rc=7;goto done;}
        mapping_reused=1;
        if(a==state.update&&b==state.ipcs&&installed.enabled==1){already_installed=1;rc=0;goto done;}
        if(a!=iq.bias+0x350dc0||!stock_writer_slot(mem,&iq,&udd,b)||installed.enabled!=0){
            fprintf(stderr,"Existing hooks are not fully installed or fully restored/disabled; refusing reapply.\n");goto done;}
        for(unsigned i=0;i<state.count;i++){uint32_t tid;
            if(!read_exact(mem,&tid,4,state.contexts+i*state.stride))goto done;
            if(tid){fprintf(stderr,"Existing RAM contexts have not drained; retry apply after restore.\n");rc=4;goto done;}}
        stage="existing-RAM-reenable";uint32_t enabled=1,actual;
        if(abort_requested||!poke(pid,state.config+28,&enabled,4))goto done;
        reuse_enabled=1;
        if(!read_exact(mem,&actual,4,state.config+28)||actual!=enabled)goto done;
        goto commit_slots;
    }
    if(a!=iq.bias+0x350dc0||!stock_writer_slot(mem,&iq,&udd,b)){
        fprintf(stderr,"Unrecognized resident hooks without a valid live state: update=%08x writer=%08x; refusing replacement.\n",a,b);
        free(blob);goto done;
    }
    memcpy(blob+h.config_offset,&cfg,sizeof cfg);
    uint32_t size=(h.data_offset+h.data_size+4095)&~4095U,args[6]={0,size,3,0x22,0xffffffff,0},region;
    stage="mmap2";
    int called=remote_syscall(main_thread,libc.bias+STOCK_SVC,192,args,&region);
    if(!called||kernel_error(region)||region%4096){free(blob);goto done;}
    owned_region=region;owned_size=size;
    stage="private-map-copy";
    if(!load_maps(pid)||!cover(region,size,1,0)){free(blob);goto done;}
    int wrote=poke(pid,region,blob,h.blob_size);free(blob);if(!wrote)goto done;
    uint32_t result;uint32_t flush[6]={region,region+h.code_size,0,0,0,0};
    stage="cacheflush";
    if(!remote_syscall(main_thread,libc.bias+STOCK_SVC,0xf0002,flush,&result)||kernel_error(result))goto done;
    uint32_t protect[6]={region,h.code_size,5,0,0,0};
    stage="mprotect";
    if(!remote_syscall(main_thread,libc.bias+STOCK_SVC,125,protect,&result)||kernel_error(result))goto done;
    if(!load_maps(pid)||!cover(region,h.code_size,0,1)||!cover(region+h.data_offset,h.data_size,1,0))goto done;
    state.magic=NX_LUT_RAM_MAGIC;state.abi=1;state.pid=pid;state.start=start;state.iq=iq.bias;state.udd=udd.bias;
    state.region=region;state.length=size;state.update=region+h.update_entry;state.ipcs=region+h.ipcs_entry;state.config=region+h.config_offset;
    state.contexts=region+h.reserved[0];state.stride=h.reserved[1];state.count=h.reserved[2];
    stage="state-and-slot-commit";
    if(abort_requested||!write_state(path,&state))goto done;
    state_written=1;
commit_slots:
    stage="slot-commit";
    if(!poke(pid,slot_a,&state.update,4))goto done;
    patched=1;
    if(!poke(pid,slot_b,&state.ipcs,4))goto done;
    if(!read_exact(mem,&a,4,slot_a)||!read_exact(mem,&b,4,slot_b)||a!=state.update||b!=state.ipcs)goto done;
    rc=0;
done:
    if(rc&&restore_attempted){uint32_t va,vb;
        if(!poke(pid,iq.bias+0x3bfd08,&restore_prev_a,4)||!poke(pid,iq.bias+0x3c2ab4,&restore_prev_b,4)||
           !read_exact(mem,&va,4,iq.bias+0x3bfd08)||!read_exact(mem,&vb,4,iq.bias+0x3c2ab4)||va!=restore_prev_a||vb!=restore_prev_b)transaction_poisoned=1;}
    if(rc&&patched){uint32_t a0=iq.bias+0x350dc0,b0=udd.bias+0x955c8;
        uint32_t va=0,vb=0;
        if(!poke(pid,iq.bias+0x3bfd08,&a0,4)||!poke(pid,iq.bias+0x3c2ab4,&b0,4)||
           !read_exact(mem,&va,4,iq.bias+0x3bfd08)||!read_exact(mem,&vb,4,iq.bias+0x3c2ab4)||va!=a0||vb!=b0)transaction_poisoned=1;
        else patched=0;}
    if(rc&&reuse_enabled){uint32_t disabled=0,actual;
        if(!poke(pid,state.config+28,&disabled,4)||!read_exact(mem,&actual,4,state.config+28)||actual)transaction_poisoned=1;}
    if(rc&&owned_region&&!patched&&!transaction_poisoned){uint32_t args[6]={owned_region,owned_size,0,0,0,0},value;
        if(remote_syscall(main_thread,libc.bias+STOCK_SVC,91,args,&value)&&!kernel_error(value)){
            if(state_written){state_path(pid,path,sizeof path);unlink(path);state_written=0;}}
        else fprintf(stderr,"Unused private mapping retained after failure; no hooks claim success.\n");}
    if(rc&&state_written)fprintf(stderr,"Partial transaction recorded in tmpfs; retained mapping/state require inspect.\n");
    if(mem>=0)close(mem);
    if(iq.fd>=0)close(iq.fd);
    if(udd.fd>=0)close(udd.fd);
    if(libc.fd>=0)close(libc.fd);
    if(transaction_poisoned){clear_markers(pid);fprintf(stderr,"FATAL: context recovery incomplete; no success claimed.\n");return 5;}
    if(!detach_all()){clear_markers(pid);fprintf(stderr,"FATAL: not all TIDs detached.\n");return 5;}
    if(known&&!rc){state_path(pid,path,sizeof path);unlink(path);}
    if(restoring||rc)clear_markers(pid);
    if(restoring&&!rc)printf("{\"command\":\"restore\",\"pid\":%u,\"restored\":true,\"mapping_retained\":true,\"cache_invalidated\":%s,\"ready\":false,\"image_verified\":false}\n",pid,restore_cache?"true":"false");
    if(!restoring&&!rc){
        if(!publish_markers(pid,&state,root)){clear_markers(pid);fprintf(stderr,"Hooks installed, marker publication failed; restore required.\n");return 6;}
        printf("{\"command\":\"apply\",\"pid\":%u,\"hooks_installed\":true,\"region\":%u,\"mapping_reused\":%s,\"already_installed\":%s,\"ready\":true,\"image_verified\":false}\n",pid,state.region,mapping_reused?"true":"false",already_installed?"true":"false");
        /* Initial telemetry is optional. Readiness checks the installed code
         * header and bindings, never this mutable per-shot status file. */
        FILE *status=fopen("/var/run/nx300m-lut-status.json","w");
        if(status){
            fprintf(status,"{\"command\":\"apply\",\"pid\":%u,\"hooks_installed\":true,\"ready\":true,\"build\":%u,\"loader\":\"ram-blob\"}",pid,NX_LUT_BLOB_BUILD);
            fclose(status);
        } else fprintf(stderr,"Initial telemetry publication failed errno=%d; readiness still uses live bindings.\n",errno);}
    if(rc==3)fprintf(stderr,"Admission/syscall/patch failed at %s (errno=%d); no successful installation claimed.\n",stage,errno);
    return rc;
}
static int supervised_mutate(unsigned pid,const char *command,const char *blob,const char *root,int fixture) {
    char path[128];
    if(!state_path(pid,path,sizeof path)){
        fprintf(stderr,"No tmpfs/ramfs state directory for the transaction; nothing changed.\n");return 3;}
    snprintf(path,sizeof path,"/var/run/nx300m-lut-worker-%u.log",(unsigned)getpid());
    int log=open(path,O_RDWR|O_CREAT|O_EXCL|O_CLOEXEC,0600);
    if(log<0&&errno==EEXIST){
        /* The file name carries this process's own pid, so an existing file
         * can only be a leftover from an earlier process whose pid the camera
         * snapshot restore reused. A dead worker cannot still need it. */
        fprintf(stderr,"Stale worker log %s removed; pid reused after snapshot restore.\n",path);
        unlink(path);
        log=open(path,O_RDWR|O_CREAT|O_EXCL|O_CLOEXEC,0600);
    }
    if(log<0){fprintf(stderr,"Worker log open failed errno=%d path=%s; nothing changed.\n",errno,path);return 3;}
    pid_t child=fork();
    if(child<0){fprintf(stderr,"Recovery worker fork failed errno=%d; nothing changed.\n",errno);close(log);unlink(path);return 3;}
    if(!child){
        dup2(log,STDOUT_FILENO);dup2(log,STDERR_FILENO);close(log);
        signal(SIGUSR1,request_abort);signal(SIGTERM,request_abort);signal(SIGINT,request_abort);signal(SIGPIPE,SIG_IGN);
        exit(mutate(pid,command,blob,root,fixture));
    }
    unsigned long long deadline=now_ms()+15000;int status=0,finished=0;
    while(now_ms()<deadline){pid_t n=waitpid(child,&status,WNOHANG);if(n==child){finished=1;break;}
        if(n<0&&errno!=EINTR)break;
        struct timespec pause={0,10000000};nanosleep(&pause,NULL);}
    if(!finished){kill(child,SIGUSR1);printf("{\"command\":\"%s\",\"pid\":%u,\"success\":false,\"recovery_worker\":%u,\"recovery_log\":\"%s\"}\n",command,pid,(unsigned)child,path);
        fprintf(stderr,"Deadline exceeded: recovery tracer remains alive; do not terminate that worker until it restores/detaches all TIDs.\n");close(log);return 5;}
    lseek(log,0,SEEK_SET);char buf[4096];ssize_t n;while((n=read(log,buf,sizeof buf))>0)fwrite(buf,1,(size_t)n,stdout);
    close(log);unlink(path);return WIFEXITED(status)?WEXITSTATUS(status):5;
}
static void quoted(const char *s) {putchar('"');for(;*s;s++){unsigned char c=(unsigned char)*s;if(c=='"'||c=='\\'){putchar('\\');putchar(c);}else if(c<32)printf("\\u%04x",c);else putchar(c);}putchar('"');}
static int inspect(unsigned pid) {
    unsigned nb=0,na=0;int identity=threads(pid,before,&nb);char path[64],exe[512];
    snprintf(path,sizeof path,"/proc/%u/exe",pid);ssize_t len=readlink(path,exe,sizeof exe-1);if(len<0)return 2;exe[len]=0;
    int camera=!strcmp(base(exe),"di-camera-app-nx300"),map_ok=load_maps(pid),conflict=0;
    for(unsigned i=0;i<nmaps;i++)if(strstr(maps[i].path,"lut_port")||strstr(maps[i].path,"lut_node_port")||strstr(maps[i].path,"lut-live"))conflict=1;
    struct image iq={0},udd={0};iq.fd=udd.fd=-1;
    int iq_ok=map_ok&&image_open(pid,"libiq-fw-slpcam.so",STOCK_IQ_SIZE,STOCK_IQ_SHA,&iq);
    int udd_ok=map_ok&&image_open(pid,"libudd.so.3.1",STOCK_UDD_SIZE,STOCK_UDD_SHA,&udd);
    int abi=iq_ok&&dynamic_abi(&iq);snprintf(path,sizeof path,"/proc/%u/mem",pid);int mem=open(path,O_RDONLY|O_CLOEXEC);int mem_errno=mem<0?errno:0;
    uint32_t slot_update=0,slot_lut=0;int code=0,slots=0,ram_hooks=0,ready=0;
    if(iq_ok&&udd_ok&&mem>=0){code=code_check(mem,iq.bias+0x350dc0,stock_update)&&code_check(mem,iq.bias+0x350e0c,stock_reset)&&code_check(mem,udd.bias+0x955c8,stock_lut);
        struct mapping *a=cover(iq.bias+0x3bfd08,4,1,0),*b=cover(iq.bias+0x3c2ab4,4,1,0);
        slots=a&&b&&a->inode==iq.first->inode&&b->inode==iq.first->inode&&
            read_exact(mem,&slot_update,4,iq.bias+0x3bfd08)&&read_exact(mem,&slot_lut,4,iq.bias+0x3c2ab4)&&
            slot_update==iq.bias+0x350dc0&&stock_writer_slot(mem,&iq,&udd,slot_lut);
        char saved[128];if(state_path(pid,saved,sizeof saved)){
            struct live_state s;int fd=open(saved,O_RDONLY|O_CLOEXEC);int got=fd>=0&&read(fd,&s,sizeof s)==sizeof s;
            if(fd>=0)close(fd);
            unsigned long long start=0;for(unsigned i=0;i<nb;i++)if(before[i].tid==pid)start=before[i].start;
            struct nx_lut_ram_header h;struct nx_lut_ram_config cfg;
            if(got&&s.magic==NX_LUT_RAM_MAGIC&&s.abi==1&&s.pid==pid&&s.start==start&&s.iq==iq.bias&&s.udd==udd.bias&&
               cover(s.region,64,0,1)&&read_exact(mem,&h,sizeof h,s.region)&&h.magic==NX_LUT_RAM_MAGIC&&h.abi==1&&
               h.code_size==h.data_offset&&h.code_size%4096==0&&h.code_size<=s.length&&h.data_size<=s.length-h.data_offset&&
               cover(s.region,h.code_size,0,1)&&cover(s.region+h.data_offset,h.data_size,1,0)&&
               s.config==s.region+h.config_offset&&h.config_size==sizeof cfg&&read_exact(mem,&cfg,sizeof cfg,s.config)&&
               cfg.abi==1&&cfg.size==sizeof cfg&&cfg.original_update==iq.bias+0x350dc0&&cfg.original_reset==iq.bias+0x350e0c&&
               cfg.original_ipcs==udd.bias+0x955c8&&s.update==s.region+h.update_entry&&s.ipcs==s.region+h.ipcs_entry&&
               slot_update==s.update&&slot_lut==s.ipcs&&cfg.root[163]==0){
                ram_hooks=1;FILE *f=fopen("/var/run/nx300m-lut-ram-hook","r");
                if(f){unsigned owner=0,region=0,size=0;unsigned long long stamp=0;char root[164];
                    int count=fscanf(f,"%u %llu %163s %u %u",&owner,&stamp,root,&region,&size);fclose(f);
                    ready=count==5&&owner==pid&&stamp==start&&region==s.region&&size==s.length&&!strcmp(root,cfg.root)&&cfg.enabled==1;}
            }
        }
    }
    if(mem>=0)close(mem);
    if(iq.fd>=0)close(iq.fd);
    if(udd.fd>=0)close(udd.fd);
    identity=identity&&threads(pid,after,&na)&&nb==na;
    for(unsigned i=0;identity&&i<nb;i++)if(before[i].tid!=after[i].tid||before[i].start!=after[i].start||before[i].tracer||after[i].tracer)identity=0;
    printf("{\"command\":\"status\",\"pid\":%u,\"executable\":",pid);quoted(exe);
    printf(",\"camera_name\":%s,\"thread_identity_stable\":%s,\"threads\":[",camera?"true":"false",identity?"true":"false");
    for(unsigned i=0;i<nb;i++){if(i)putchar(',');printf("{\"tid\":%u,\"starttime\":%llu,\"state\":\"%c\",\"tracer_pid\":%d}",before[i].tid,before[i].start,before[i].state,before[i].tracer);}putchar(']');
    printf(",\"maps_read\":%s,\"old_module_conflict\":%s,\"stock_iq_sha256\":%s,\"stock_udd_sha256\":%s,\"dynamic_abi\":%s,\"memory_errno\":%d,\"code_bytes\":%s,\"rw_slots_original\":%s,\"ram_hooks_installed\":%s,\"iq_bias\":%u,\"udd_bias\":%u,\"slot_update\":%u,\"slot_lut\":%u,\"apply_enabled\":true,\"restore_enabled\":true,\"ready\":%s,\"target_writes\":0,\"image_verified\":false}\n",
        map_ok?"true":"false",conflict?"true":"false",iq_ok?"true":"false",udd_ok?"true":"false",abi?"true":"false",mem_errno,code?"true":"false",slots?"true":"false",ram_hooks?"true":"false",iq.bias,udd.bias,slot_update,slot_lut,ready?"true":"false");
    /* Exit 0 means the read-only admission checks passed, never installation. */
    return identity&&camera&&iq_ok&&udd_ok&&abi&&code&&(slots||ram_hooks)&&!conflict?0:2;
}
int main(int argc,char **argv) {
    const char *command="status";unsigned pid=0;int next=1;
    if(argc>1&&argv[1][0]!='-'){command=argv[1];next=2;}
    if(strcmp(command,"status")&&strcmp(command,"apply")&&strcmp(command,"restore")&&strcmp(command,"restore-known")){fprintf(stderr,"usage: lut_live_loader.arm [status|apply|restore|restore-known] --pid PID\n");return 64;}
    const char *blob=NULL,*root=NULL;int fixture=0;
    for(int i=next;i<argc;i++){
        if(!strcmp(argv[i],"--fixture")){fixture=1;continue;}
        if(i+1>=argc)goto usage;
        const char *key=argv[i++];
        if(!strcmp(key,"--pid")){if(pid||!decimal(argv[i],&pid))goto usage;}
        else if(!strcmp(key,"--blob")){if(blob)goto usage;blob=argv[i];}
        else if(!strcmp(key,"--root")){if(root)goto usage;root=argv[i];}
        else goto usage;
    }
    if(!pid)goto usage;
    if(!strcmp(command,"status")){if(blob||root||fixture)goto usage;return inspect(pid);}
    if(!strcmp(command,"restore")){if(blob||root)goto usage;return supervised_mutate(pid,command,NULL,NULL,fixture);}
    if(!blob||!root||root[0]!='/'||strlen(root)>160||strstr(root,"..")||strstr(root,"//"))goto usage;
    for(const char *p=root;*p;p++)if(!((*p>='a'&&*p<='z')||(*p>='A'&&*p<='Z')||(*p>='0'&&*p<='9')||*p=='/'||*p=='_'||*p=='-'||*p=='.'))goto usage;
    return supervised_mutate(pid,command,blob,root,fixture);
usage:
    fprintf(stderr,"usage: lut_live_loader.arm [status|apply|restore|restore-known] --pid PID [--blob FILE --root DIR] [--fixture]\n");return 64;
}
