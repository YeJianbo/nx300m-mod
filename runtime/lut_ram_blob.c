/* RAM-only ARM adapter. No ELF loader, libc, TLS, heap or remote C calls.
 * Each original Update owns a snapshot until all its channel calls return.
 * Calls already inside SetParam at installation have no context and pass
 * through. Fixed slots fail open when busy; no waiting on stopped threads.
 */
#include "lut_ram_blob.h"
#include "lut_ram_blob_build.h"
typedef unsigned u32;
typedef unsigned short u16;
enum { WORDS=730, TOTAL=2190, SLOTS=8, DIRECT=0, COMPOSED=1, TABLE_ERROR=2 };
__attribute__((visibility("hidden")))
struct nx_lut_ram_config nx_lut_ram_config;
struct context {
    volatile u32 tid;
    u32 active, generation, strength, forwarded, composed;
    char path[256];
    u16 table[TOTAL];
    u32 source_branch, channel_order[3], order_count, writer_tids[3];
    u32 source_ptrs[3], forwarded_ptrs[3], trace_enabled;
};
__attribute__((visibility("hidden"))) struct context nx_lut_ram_contexts[SLOTS];
#define contexts nx_lut_ram_contexts

static int sys3(int n, int a, int b, int c) {
    register int r0 __asm__("r0")=a;
    register int r1 __asm__("r1")=b;
    register int r2 __asm__("r2")=c;
    register int r7 __asm__("r7")=n;
    __asm__ volatile("svc 0" : "+r"(r0) : "r"(r1),"r"(r2),"r"(r7) : "memory","cc");
    return r0;
}
static unsigned length(const char *s) { unsigned n=0; while(s[n]) n++; return n; }
static int equal(const char *a,const char *b) { while(*a && *a==*b) {a++;b++;} return *a==*b; }
static char *append(char *p,const char *s) { while(*s) *p++=*s++; *p=0; return p; }
static char *number(char *p,u32 v) {
    char b[10]; unsigned n=0;
    do {b[n++]=(char)('0'+v%10);v/=10;} while(v);
    while(n) *p++=b[--n];
    *p=0; return p;
}
static int read_file(const char *path,void *dst,unsigned capacity) {
    int fd=sys3(5,(int)path,0,0); if(fd<0) return fd;
    unsigned n=0; int rc=0;
    while(n<capacity) {
        rc=sys3(3,fd,(int)((char *)dst+n),(int)(capacity-n));
        if(rc==-4) continue;
        if(rc<=0) break;
        n+=(unsigned)rc;
    }
    char extra;
    if(n==capacity) { do {rc=sys3(3,fd,(int)&extra,1);} while(rc==-4); if(rc>0) rc=-1; }
    sys3(6,fd,0,0);
    return rc<0 ? -1 : (int)n;
}
static int write_file(const char *path,const char *data,unsigned n) {
    int fd=sys3(5,(int)path,1|64|512,0600); if(fd<0) return -1;
    unsigned done=0;
    while(done<n) {
        int rc=sys3(4,fd,(int)(data+done),(int)(n-done));
        if(rc==-4) continue;
        if(rc<=0) {sys3(6,fd,0,0);return -1;}
        done+=(unsigned)rc;
    }
    return sys3(6,fd,0,0);
}
static int tracing(void) { return sys3(33,(int)"/var/run/nx300m-lut-trace-enabled",0,0)==0; }
static char *array(char *p,const u32 *values,unsigned count) {
    p=append(p,"[");
    for(unsigned i=0;i<count;i++) {if(i) p=append(p,",");p=number(p,values[i]);}
    return append(p,"]");
}
static void trace(struct context *s,const char *stage) {
    if(!s->trace_enabled) return;
    char text[1200],path[96],temp[104];char *p=text;
    p=append(p,"{\"stage\":\"");p=append(p,stage);
    p=append(p,"\",\"update_tid\":");p=number(p,s->tid);
    p=append(p,",\"writer_tids\":");p=array(p,s->writer_tids,3);
    p=append(p,",\"channel_order\":");p=array(p,s->channel_order,s->order_count<3?s->order_count:3);
    p=append(p,",\"write_calls\":");p=number(p,s->order_count);
    p=append(p,",\"source_branch\":\"");
    p=append(p,s->source_branch==1?"factory":s->source_branch==2?"unity":"unknown");
    p=append(p,"\",\"source_ptrs\":");p=array(p,s->source_ptrs,3);
    p=append(p,",\"forwarded_ptrs\":");p=array(p,s->forwarded_ptrs,3);
    p=append(p,",\"generation\":");p=number(p,s->generation);
    p=append(p,",\"strength\":");p=number(p,s->strength);
    p=append(p,",\"selection_path\":\"");p=append(p,s->path);p=append(p,"\"}\n");
    char *q=append(path,"/var/run/nx300m-lut-trace-");q=number(q,s->tid);append(q,".json");
    q=append(temp,path);append(q,".tmp");
    if(!write_file(temp,text,(unsigned)(p-text))) sys3(38,(int)temp,(int)path,0);
}
static unsigned unmatched_logged;
/* Context-less callers (the viewfinder writes tables without our Update
 * wrapper) are passed through untouched. Their evidence must survive power
 * off, so append to the card log next to the rest of the chain evidence.
 * Rate-limited: the viewfinder can write per frame. */
static void unmatched(u32 tid,unsigned channel,const u16 *source) {
    if(unmatched_logged>=64) return;
    char text[192]; char *p=text;
    p=append(p,"{\"context_found\":false,\"writer_tid\":");p=number(p,tid);
    p=append(p,",\"channel\":");p=number(p,channel);
    p=append(p,",\"source_ptr\":");p=number(p,(u32)source);
    p=append(p,",\"forwarded_ptr\":");p=number(p,(u32)source);p=append(p,"}\n");
    char path[128],temp[136];char *q=append(path,nx_lut_ram_config.root);
    q=append(q,"/logs/lut-unmatched.log");
    q=append(temp,path);append(q,".tmp");
    if(!write_file(temp,text,(unsigned)(p-text))) sys3(38,(int)temp,(int)path,0);
    unmatched_logged++;
}
static int decimal(const char **p,u32 *value) {
    unsigned n=0,v=0;
    while(**p>='0' && **p<='9') {
        unsigned d=(unsigned)(*(*p)++-'0');
        if(v>429496729U || (v==429496729U && d>5)) return 0;
        v=v*10+d;n++;
    }
    *value=v;return n!=0;
}
static int space(char c) { return c==' ' || c=='\t' || c=='\r' || c=='\n'; }
static int valid_path(const char *path) {
    const char *r=nx_lut_ram_config.root;
    while(*r) if(*path++!=*r++) return 0;
    r="/looks/"; while(*r) if(*path++!=*r++) return 0;
    if(!*path) return 0;
    for(;*path;path++) {
        unsigned c=(unsigned char)*path;
        if(c=='.' && path[1]=='.') return 0;
        if(!((c>='a'&&c<='z')||(c>='0'&&c<='9')||c=='/'||c=='_'||c=='-'||c=='.')) return 0;
    }
    return 1;
}
static void selection(struct context *s) {
    char text[320]; const char *p=text; unsigned k=0;
    s->active=0;s->generation=0;s->strength=100;s->forwarded=0;s->composed=0;s->path[0]=0;
    int n=read_file("/var/run/nx300m-lut-selection",text,sizeof(text)-1);
    if(n<=0) return;
    /* A NUL inside the file must not hide a malformed tail. */
    for(int i=0;i<n;i++) if(!text[i]) return;
    text[n]=0;
    if(!decimal(&p,&s->generation)||!s->generation||!space(*p)) goto invalid;
    while(space(*p)) p++;
    while(*p&&!space(*p)) {if(k>=255) goto invalid;s->path[k++]=*p++;}
    s->path[k]=0;
    while(space(*p)) p++;
    if(*p) {
        if(!decimal(&p,&s->strength)||s->strength>100) goto invalid;
        while(space(*p)) p++;
        if(*p) goto invalid;
    }
    if(!valid_path(s->path)) goto invalid;
    s->active=1;
    return;
invalid:
    s->path[0]=0;s->generation=0;s->strength=100;
}
static int load_table(struct context *s,int stock) {
    char path[272]; append(path,s->path);
    unsigned n=length(path);
    if(stock && n>=12 && equal(path+n-12,"/table.d4lut")) {
        append(path+n-12,"/table-stock3d.d4lut");
        int got=read_file(path,s->table,sizeof(s->table));
        if(got==(int)sizeof(s->table)) s->composed=1;
        else if(got==-2) {
            append(path,s->path);
            if(read_file(path,s->table,sizeof(s->table))!=(int)sizeof(s->table)) return 0;
        } else return 0;
    } else {
        if(read_file(path,s->table,sizeof(s->table))!=(int)sizeof(s->table)) return 0;
    }
    for(unsigned i=0;i<TOTAL;i++) if(s->table[i]>1023 || (i%WORDS==WORDS-1 && s->table[i])) return 0;
    return 1;
}
/* Keep status writes out of libc; only a complete channel triplet is published.
 * The pid-specific temporary path cannot collide with the preload adapter. */
static void publish(struct context *s) {
    char text[960],temp[96]; char *p=text;
    unsigned pid=(unsigned)sys3(20,0,0,0);
    p=append(p,"{\"guest_pid\":");p=number(p,pid);
    p=append(p,",\"root\":\"");p=append(p,nx_lut_ram_config.root);
    p=append(p,"\",\"selection_path\":\"");p=append(p,s->path);
    p=append(p,"\",\"generation\":");p=number(p,s->generation);
    p=append(p,",\"strength\":");p=number(p,s->strength);
    p=append(p,",\"active\":");p=number(p,s->active);
    p=append(p,",\"forwarded_mask\":7,\"stage\":\"");
    p=append(p,s->active?"forwarded-to-stock-api":s->composed==TABLE_ERROR?"invalid-or-unreadable-table":"stock");
    p=append(p,"\",\"composition\":\"");
    p=append(p,s->active&&s->composed==COMPOSED?"after-stock-3d-lut":"direct");
    p=append(p,"\",\"image_verified\":false,\"loader\":\"ram-blob\",\"build\":");p=number(p,NX_LUT_BLOB_BUILD);p=append(p,"}\n");
    char *q=append(temp,"/var/run/nx300m-lut-ram-");q=number(q,s->tid);append(q,".tmp");
    if(!write_file(temp,text,(unsigned)(p-text))) sys3(38,(int)temp,(int)"/var/run/nx300m-lut-status.json",0);
}

/* Viewfinder stage experiment. The preview pipeline never calls the hooked
 * Update/IPCS paths, so the firmware appears to gate the 3D LUT stage off in
 * viewfinder mode (bit 13 of the IPCS control word). While a look is staged,
 * re-arm that bit on every still-path event: if the firmware leaves it set
 * when returning to live view, the preview shows the staged table. Cycling
 * to stock clears the bit again; reboot restores stock regardless. */
static void stage_follow(struct context *s) {
    unsigned udd=nx_lut_ram_config.original_ipcs-0x955c8U;
    unsigned *reg_slot=(unsigned *)(udd+0x127bd8U);
    unsigned regs=*reg_slot;
    if(!regs) return;
    volatile unsigned *control=(volatile unsigned *)(regs+0x1040U);
    unsigned before=*control,after;
    if(s->active) after=before|0x2000U;
    else after=before&~0x2000U;
    if(after==before) return;
    *control=after;
    if(s->trace_enabled || tracing()) {
        char text[160],path[128],temp[136];char *p=text;
        p=append(p,"{\"stage_follow\":\"applied\",\"active\":");p=number(p,s->active);
        p=append(p,",\"before\":");p=number(p,before);
        p=append(p,",\"after\":");p=number(p,after);p=append(p,"}\n");
        char *q=append(path,nx_lut_ram_config.root);
        q=append(q,"/logs/lut-stage-follow.log");
        q=append(temp,path);append(q,".tmp");
        if(!write_file(temp,text,(unsigned)(p-text))) sys3(38,(int)temp,(int)path,0);
    }
}

__attribute__((visibility("hidden")))
void nx_lut_ram_update(void *self,void *params) {
    void (*original)(void *,void *)=(void *)nx_lut_ram_config.original_update;
    if(!nx_lut_ram_config.enabled) {original(self,params);return;}
    u32 tid=(u32)sys3(224,0,0,0);
    struct context *s=0;
    for(unsigned i=0;i<SLOTS;i++) if(contexts[i].tid==tid) {original(self,params);return;}
    for(unsigned i=0;i<SLOTS;i++) if(__sync_bool_compare_and_swap(&contexts[i].tid,0,tid)) {s=&contexts[i];break;}
    if(!s) {original(self,params);return;}
    selection(s);
    s->source_branch=0;s->order_count=0;s->trace_enabled=(u32)tracing();
    for(unsigned i=0;i<3;i++) {
        s->channel_order[i]=0;s->writer_tids[i]=0;s->source_ptrs[i]=0;s->forwarded_ptrs[i]=0;
    }
    trace(s,"update-enter");
    /* Stock Reset is exactly this cache assignment. Only our dedicated
     * 3D-LUT virtual method enters here; no other parameter cache is touched. */
    ((u32 *)self)[1]=~0U;
    original(self,params);
    stage_follow(s);
    trace(s,"update-return");
    __sync_synchronize();s->tid=0;
}
__attribute__((visibility("hidden")))
void nx_lut_ram_ipcs(unsigned channel,const u16 *source) {
    void (*original)(unsigned,const u16 *)=(void *)nx_lut_ram_config.original_ipcs;
    u32 tid=(u32)sys3(224,0,0,0);struct context *s=0;
    for(unsigned i=0;i<SLOTS;i++) if(contexts[i].tid==tid) {s=&contexts[i];break;}
    if(!s || channel>=3 || !source) {unmatched(tid,channel,source);original(channel,source);return;}
    if(s->order_count<3) s->channel_order[s->order_count]=channel;
    s->order_count++;s->writer_tids[channel]=tid;s->source_ptrs[channel]=(u32)source;
    if(channel==0) s->source_branch=(u32)source==nx_lut_ram_config.factory_red?1:2;
    if(channel==0 && s->active && s->strength && !load_table(s,s->source_branch==1)) {
        s->active=0;s->composed=TABLE_ERROR;
    }
    if(s->active && s->strength) {
        u16 mixed[WORDS];
        for(unsigned i=0;i<WORDS;i++) mixed[i]=(u16)(((u32)source[i]*(100-s->strength)+(u32)s->table[channel*WORDS+i]*s->strength+50)/100);
        s->forwarded_ptrs[channel]=(u32)mixed;original(channel,mixed);
    } else {s->forwarded_ptrs[channel]=(u32)source;original(channel,source);}
    s->forwarded|=1U<<channel;
    if(s->forwarded==7) publish(s);
}
