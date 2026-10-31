/* Shared original-ELF admission and ARM ptrace pure-syscall backend. */
#ifndef NX_LIVE_LOADER_CORE_H
#define NX_LIVE_LOADER_CORE_H
enum { MAX_MAPS=2048, MAX_THREADS=1024 };
struct mapping { uint32_t lo, hi, offset; unsigned devmajor, devminor;
    unsigned long long inode; char perm[5], path[512]; };
static struct mapping maps[MAX_MAPS];
static unsigned nmaps;
struct thread { unsigned tid; unsigned long long start; char state; int tracer; };
static struct thread before[MAX_THREADS], after[MAX_THREADS];

/* SHA-256 authenticates the actual mapped file against the build's original
 * 1.15 ELF. Runtime executable bytes are checked separately below. */
struct sha { uint32_t h[8]; unsigned char block[64]; unsigned fill; uint64_t bytes; };
static uint32_t rotr(uint32_t x, unsigned n) { return x>>n | x<<(32-n); }
static void sha_block(struct sha *s) {
    static const uint32_t k[64]={
        0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
        0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
        0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
        0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
        0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
        0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
        0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
        0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2};
    uint32_t w[64],a=s->h[0],b=s->h[1],c=s->h[2],d=s->h[3],e=s->h[4],f=s->h[5],g=s->h[6],h=s->h[7];
    for(unsigned i=0;i<16;i++) w[i]=(uint32_t)s->block[4*i]<<24 | (uint32_t)s->block[4*i+1]<<16 |
        (uint32_t)s->block[4*i+2]<<8 | s->block[4*i+3];
    for(unsigned i=16;i<64;i++) {uint32_t x=w[i-15],y=w[i-2];w[i]=w[i-16]+(rotr(x,7)^rotr(x,18)^(x>>3))+w[i-7]+(rotr(y,17)^rotr(y,19)^(y>>10));}
    for(unsigned i=0;i<64;i++) {uint32_t t=h+(rotr(e,6)^rotr(e,11)^rotr(e,25))+((e&f)^(~e&g))+k[i]+w[i];
        uint32_t u=(rotr(a,2)^rotr(a,13)^rotr(a,22))+((a&b)^(a&c)^(b&c));h=g;g=f;f=e;e=d+t;d=c;c=b;b=a;a=t+u;}
    s->h[0]+=a;s->h[1]+=b;s->h[2]+=c;s->h[3]+=d;s->h[4]+=e;s->h[5]+=f;s->h[6]+=g;s->h[7]+=h;
}
static void sha_feed(struct sha *s,const void *data,unsigned n) {
    const unsigned char *p=data;s->bytes+=n;
    while(n--) {s->block[s->fill++]=*p++;if(s->fill==64){sha_block(s);s->fill=0;}}
}
static int file_hash(int fd,unsigned size,const char *expected) {
    struct sha s={{0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19},{0},0,0};
    unsigned char buf[4096];unsigned total=0;ssize_t n;
    if(lseek(fd,0,SEEK_SET)<0) return 0;
    while((n=read(fd,buf,sizeof buf))>0) {if((unsigned)n>size-total)return 0;sha_feed(&s,buf,(unsigned)n);total+=(unsigned)n;}
    if(n<0 || total!=size)return 0;
    uint64_t bits=s.bytes*8;unsigned char one=128,zero=0;sha_feed(&s,&one,1);
    while(s.fill!=56)sha_feed(&s,&zero,1);
    for(int i=7;i>=0;i--) {unsigned char v=(unsigned char)(bits>>(8*i));sha_feed(&s,&v,1);}
    char hex[65];for(unsigned i=0;i<8;i++)snprintf(hex+8*i,9,"%08x",s.h[i]);
    return !strcmp(hex,expected);
}
static const char *base(const char *p) {const char *q=strrchr(p,'/');return q?q+1:p;}
static int decimal(const char *s,unsigned *out) {
    unsigned n=0;if(!*s)return 0;
    for(;*s;s++){if(*s<'0'||*s>'9'||n>(0x7fffffffU-(*s-'0'))/10)return 0;n=n*10+(*s-'0');}
    *out=n;return n>0;
}
static int read_exact(int fd,void *buf,unsigned n,uint32_t pos) {
    return pread64(fd,buf,n,(off64_t)pos)==(ssize_t)n;
}
static int task_identity(unsigned pid,unsigned tid,struct thread *t) {
    char path[96],line[4096];snprintf(path,sizeof path,"/proc/%u/task/%u/stat",pid,tid);
    FILE *f=fopen(path,"r");if(!f)return 0;
    int ok=fgets(line,sizeof line,f)!=NULL;fclose(f);if(!ok)return 0;
    char *p=strrchr(line,')');if(!p||p[1]!=' ')return 0;p+=2;t->state=*p;
    /* stat fields after comm start at field 3; field 22 is starttime. */
    for(unsigned field=3;field<22;field++){p=strchr(p,' ');if(!p)return 0;p++;}
    char *end;t->start=strtoull(p,&end,10);if(end==p||!t->start)return 0;
    snprintf(path,sizeof path,"/proc/%u/task/%u/status",pid,tid);f=fopen(path,"r");if(!f)return 0;
    unsigned tgid=0;t->tracer=-1;
    while(fgets(line,sizeof line,f)){if(!strncmp(line,"Tgid:",5))tgid=(unsigned)strtoul(line+5,NULL,10);
        if(!strncmp(line,"TracerPid:",10))t->tracer=(int)strtol(line+10,NULL,10);}
    ok=!ferror(f);fclose(f);t->tid=tid;return ok && tgid==pid && t->tracer>=0;
}
static int thread_cmp(const void *a,const void *b) {unsigned x=((const struct thread*)a)->tid,y=((const struct thread*)b)->tid;return (x>y)-(x<y);}
static int threads(unsigned pid,struct thread *out,unsigned *count) {
    char path[64];snprintf(path,sizeof path,"/proc/%u/task",pid);DIR *d=opendir(path);if(!d)return 0;
    struct dirent *e;unsigned n=0;int ok=1;errno=0;
    while((e=readdir(d))){unsigned tid;if(!decimal(e->d_name,&tid))continue;
        if(n==MAX_THREADS || !task_identity(pid,tid,&out[n])){ok=0;break;}n++;errno=0;}
    if(errno)ok=0;
    closedir(d);if(!n)return 0;qsort(out,n,sizeof *out,thread_cmp);*count=n;return ok;
}
static int load_maps(unsigned pid) {
    char path[64],line[1024];snprintf(path,sizeof path,"/proc/%u/maps",pid);FILE *f=fopen(path,"r");if(!f)return 0;
    int ok=1;nmaps=0;
    while(fgets(line,sizeof line,f)) {if(!strchr(line,'\n')||nmaps==MAX_MAPS){ok=0;break;}
        struct mapping *m=&maps[nmaps];int used=0;
        if(sscanf(line,"%x-%x %4s %x %x:%x %llu %n",&m->lo,&m->hi,m->perm,&m->offset,&m->devmajor,&m->devminor,&m->inode,&used)!=7 || m->lo>=m->hi){ok=0;break;}
        char *p=line+used;size_t n=strcspn(p,"\n");if(n>=sizeof m->path){ok=0;break;}memcpy(m->path,p,n);m->path[n]=0;nmaps++;
    }
    if(ferror(f))ok=0;
    fclose(f);return ok;
}
static struct mapping *cover(uint32_t address,unsigned size,int writable,int executable) {
    for(unsigned i=0;i<nmaps;i++){struct mapping *m=&maps[i];
        if(address>=m->lo && size<=m->hi-address && address<m->hi && m->perm[0]=='r' &&
           (!writable || m->perm[1]=='w') && (!executable || m->perm[2]=='x'))return m;}
    return NULL;
}
struct image {int fd;uint32_t bias;struct mapping *first;Elf32_Ehdr eh;Elf32_Phdr ph[32];};
static int image_open(unsigned pid,const char *name,unsigned size,const char *sha,struct image *out) {
    memset(out,0,sizeof *out);out->fd=-1;struct mapping *first=NULL;
    for(unsigned i=0;i<nmaps;i++)if(!strcmp(base(maps[i].path),name)){
        if(!first)first=&maps[i];
        if(first->inode!=maps[i].inode || first->devmajor!=maps[i].devmajor || first->devminor!=maps[i].devminor)return 0;
        if(!maps[i].offset)out->first=&maps[i];}
    if(!first||!out->first||out->first->path[0]!='/')return 0;
    char path[640];snprintf(path,sizeof path,"/proc/%u/root%s",pid,first->path);int fd=open(path,O_RDONLY|O_CLOEXEC);if(fd<0)return 0;
    struct stat64 st;int ok=!__fxstat64(3,fd,&st)&&S_ISREG(st.st_mode)&&st.st_size==size &&
        (unsigned long long)st.st_ino==first->inode && major(st.st_dev)==first->devmajor && minor(st.st_dev)==first->devminor && file_hash(fd,size,sha);
    if(ok)ok=read_exact(fd,&out->eh,sizeof out->eh,0);
    Elf32_Ehdr *h=&out->eh;
    if(ok)ok=!memcmp(h->e_ident,ELFMAG,SELFMAG)&&h->e_ident[EI_CLASS]==ELFCLASS32&&h->e_ident[EI_DATA]==ELFDATA2LSB&&
        h->e_machine==EM_ARM&&h->e_type==ET_DYN&&h->e_phentsize==sizeof(Elf32_Phdr)&&h->e_phnum>0&&h->e_phnum<=32;
    if(ok)ok=read_exact(fd,out->ph,h->e_phnum*sizeof(Elf32_Phdr),h->e_phoff);
    int found=0;
    for(unsigned i=0;ok&&i<h->e_phnum;i++)if(out->ph[i].p_type==PT_LOAD && out->ph[i].p_offset==0){
        if(found++)ok=0;
        out->bias=out->first->lo-out->ph[i].p_vaddr;}
    if(!found)ok=0;
    if(!ok){close(fd);return 0;}out->fd=fd;return 1;
}
static int file_vaddr(struct image *im,void *out,unsigned n,uint32_t address) {
    for(unsigned i=0;i<im->eh.e_phnum;i++){Elf32_Phdr *p=&im->ph[i];
        if(p->p_type==PT_LOAD && address>=p->p_vaddr && address-p->p_vaddr<=p->p_filesz && n<=p->p_filesz-(address-p->p_vaddr))
            return read_exact(im->fd,out,n,p->p_offset+address-p->p_vaddr);}
    return 0;
}
static int code_check(int mem,uint32_t address,const unsigned char *expected) {
    unsigned char actual[32];return cover(address,32,0,1)&&read_exact(mem,actual,32,address)&&!memcmp(actual,expected,32);
}
/* Mutation backend: only raw, bounded ARM syscalls through an existing,
 * verified SVC instruction. All other code runs in fresh anonymous pages.
 * No target stack or floating-point register is written. */
struct arm_regs {uint32_t r[18];};
struct stopped {unsigned tid;int attached,stopped,signal,event;struct arm_regs regs;};
static struct stopped stops[MAX_THREADS];
static unsigned nstops;
static int transaction_poisoned;
static volatile sig_atomic_t abort_requested;
static void request_abort(int sig) {(void)sig;abort_requested=1;}
static unsigned long long now_ms(void) {
    struct timespec ts;if(syscall(263,CLOCK_MONOTONIC,&ts))return 0;
    return (unsigned long long)ts.tv_sec*1000+(unsigned)ts.tv_nsec/1000000;
}
static int wait_stop(struct stopped *s,unsigned timeout) {
    unsigned long long deadline=now_ms()+timeout;
    for(;;){int status;pid_t n=waitpid((pid_t)s->tid,&status,WNOHANG|__WALL);
        if(n==(pid_t)s->tid){if(!WIFSTOPPED(status))return 0;s->stopped=1;s->signal=WSTOPSIG(status);s->event=status>>16;return 1;}
        if(n<0&&errno!=EINTR)return 0;
        if(now_ms()>=deadline)return 0;
        struct timespec pause={0,1000000};nanosleep(&pause,NULL);}
}
static int detach_all(void) {
    int ok=1;
    for(unsigned i=0;i<nstops;i++)if(stops[i].attached){
        if(!stops[i].stopped){ok=0;continue;}
        int sig=stops[i].signal;
        if(stops[i].event==PTRACE_EVENT_STOP||sig==(SIGTRAP|0x80))sig=0;
        if(ptrace(PTRACE_DETACH,stops[i].tid,NULL,(void*)(long)sig)<0)ok=0;
        else stops[i].attached=0;
    }
    return ok;
}
static int attach_all(unsigned pid) {
    nstops=0;transaction_poisoned=0;
    /* Enumerate again after attach: no thread can escape through an unchecked
     * newly-created TID. Any churn aborts before writing target memory. */
    unsigned n=0;if(!threads(pid,before,&n))return 0;
    for(unsigned i=0;i<n;i++){
        if(abort_requested)return 0;
        if(before[i].tracer||before[i].state=='T'||before[i].state=='t')return 0;
        struct stopped *s=&stops[nstops++];memset(s,0,sizeof *s);s->tid=before[i].tid;
        if(ptrace(PTRACE_SEIZE,s->tid,NULL,(void*)(long)PTRACE_O_TRACESYSGOOD)<0)return 0;
        s->attached=1;
        if(ptrace(PTRACE_INTERRUPT,s->tid,NULL,NULL)<0)return 0;
        if(!wait_stop(s,1500))return 0;
        if(s->event!=PTRACE_EVENT_STOP)return 0;
        if(ptrace(PTRACE_GETREGS,s->tid,NULL,&s->regs)<0)return 0;
    }
    unsigned m=0;if(!threads(pid,after,&m)||m!=n)return 0;
    for(unsigned i=0;i<n;i++)if(before[i].tid!=after[i].tid||before[i].start!=after[i].start)return 0;
    return 1;
}
static int poke(unsigned tid,uint32_t address,const void *data,unsigned length) {
    const unsigned char *p=data;
    if(address%4 || length%4)return 0;
    for(unsigned i=0;i<length;i+=4){uint32_t word;memcpy(&word,p+i,4);
        if(ptrace(PTRACE_POKEDATA,tid,(void*)(uintptr_t)(address+i),(void*)(uintptr_t)word)<0)return 0;}
    return 1;
}
static int syscall_stop(struct stopped *s) {
    s->stopped=0;
    if(ptrace(PTRACE_SYSCALL,s->tid,NULL,NULL)<0){s->stopped=1;return 0;}
    if(wait_stop(s,1500))return s->signal==(SIGTRAP|0x80);
    /* These calls neither acquire userspace locks nor enter target functions.
     * Recover only after a real ptrace stop. Never detach a running borrowed
     * context. An uninterruptible kernel task is an explicit fatal state. */
    abort_requested=1;
    fprintf(stderr,"RECOVERY_PENDING: TID %u syscall deadline; waiting for a real stop before restoring context.\n",s->tid);
    fflush(stderr);
    /* A supervisor bounds the CLI wait. This tracer remains alive until the
     * target really stops, so its borrowed registers can never escape just
     * because a timeout expires. It performs no later installation on abort. */
    while(!s->stopped){
        ptrace(PTRACE_INTERRUPT,s->tid,NULL,NULL);
        if(wait_stop(s,1500))break;
        if(ptrace(PTRACE_GETREGS,s->tid,NULL,&s->regs)<0&&errno==ESRCH){transaction_poisoned=1;return 0;}
    }
    return 0;
}
static int remote_syscall(struct stopped *s,uint32_t svc,unsigned nr,const uint32_t a[6],uint32_t *result) {
    struct arm_regs original,call,check={0},entry={0};
    if(!s->stopped||ptrace(PTRACE_GETREGS,s->tid,NULL,&original)<0)return 0;
    unsigned char vfp[272]={0},vfp_after[272]={0};
    if(ptrace((enum __ptrace_request)27,s->tid,NULL,vfp)<0){fprintf(stderr,"Cannot preserve ARM VFP state.\n");return 0;}
    call=original;for(unsigned i=0;i<6;i++)call.r[i]=a[i];
    call.r[7]=nr;call.r[15]=svc;call.r[16]=(original.r[16]&~(1U<<5));call.r[17]=a[0];
    if(ptrace(PTRACE_SETOPTIONS,s->tid,NULL,(void*)(long)PTRACE_O_TRACESYSGOOD)<0||ptrace(PTRACE_SETREGS,s->tid,NULL,&call)<0)return 0;
    int ok=syscall_stop(s);
    if(ok)ok=ptrace(PTRACE_GETREGS,s->tid,NULL,&check)==0;
    if(ok)entry=check;
    if(ok)ok=check.r[7]==nr && check.r[15]==svc+4;
    if(ok)ok=syscall_stop(s);
    if(ok)ok=ptrace(PTRACE_GETREGS,s->tid,NULL,&check)==0;
    if(ok)*result=check.r[0];
    if(!ok)fprintf(stderr,"Remote syscall %u failed: original pc=%08x r0=%08x r7=%u orig_r0=%08x; entry pc=%08x r0=%08x r7=%u orig_r0=%08x; latest pc=%08x r0=%08x r7=%u orig_r0=%08x sig=%d event=%d errno=%d\n",
        nr,original.r[15],original.r[0],original.r[7],original.r[17],entry.r[15],entry.r[0],entry.r[7],entry.r[17],check.r[15],check.r[0],check.r[7],check.r[17],s->signal,s->event,errno);
    if(!s->stopped){transaction_poisoned=1;return 0;}
    while(ptrace(PTRACE_SETREGS,s->tid,NULL,&original)<0||ptrace(PTRACE_GETREGS,s->tid,NULL,&check)<0||memcmp(&original,&check,sizeof original)){
        if(errno==ESRCH){transaction_poisoned=1;return 0;}
        abort_requested=1;fprintf(stderr,"RECOVERY_PENDING: original ARM register restoration not yet verified.\n");fflush(stderr);
        ptrace(PTRACE_INTERRUPT,s->tid,NULL,NULL);wait_stop(s,1500);
    }
    if(ptrace((enum __ptrace_request)27,s->tid,NULL,vfp_after)<0||memcmp(vfp,vfp_after,260)){
        abort_requested=1;
        if(ptrace((enum __ptrace_request)28,s->tid,NULL,vfp)<0){transaction_poisoned=1;return 0;}
    }
    return ok&&!abort_requested;
}
static int kernel_error(uint32_t v){return v>=0xfffff001U;}
#endif
