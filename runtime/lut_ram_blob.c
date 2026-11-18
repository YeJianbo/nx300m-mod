/* RAM-only ARM adapter. No ELF loader, libc, TLS, heap or remote C calls.
 * Each original Update owns a snapshot until all its channel calls return.
 * Calls already inside SetParam at installation have no context and pass
 * through. Fixed slots fail open when busy; no waiting on stopped threads.
 */
#include "lut_ram_blob.h"
typedef unsigned u32;
typedef unsigned short u16;
enum { WORDS=730, TOTAL=2190, SLOTS=8, DIRECT=0, COMPOSED=1, TABLE_ERROR=2 };
__attribute__((visibility("hidden")))
struct nx_lut_ram_config nx_lut_ram_config;
struct context {
    volatile u32 tid;
    u32 active, generation, strength, forwarded, composed;
    char path[256];
    /* One table per source branch: the stock preview and still paths hand the
     * adapter different source pointers, and a branch flip on one thread must
     * never fall back to a synchronous SD read inside the IPCS callback. */
    u16 tables[2][TOTAL];
    u32 loaded_slot;
    u32 source_branch, channel_order[3], order_count, writer_tids[3];
    u32 source_ptrs[3], forwarded_ptrs[3], trace_enabled;
    /* R4 step-1 frequency counters: always incremented (memory writes in
     * control context), reported only through the gated trace channel. Field
     * layout stays private to this file, but the total stride is
     * loader-visible via the blob header; lut_ram_blob.ld pins it and the
     * build guard fails if it drifts. */
    u32 stat_updates, stat_ipcs_completes, stat_publishes, stat_reads;
};
__attribute__((visibility("hidden"))) struct context nx_lut_ram_contexts[SLOTS];
#define contexts nx_lut_ram_contexts

/* Linux ARM EABI stat64, checked against asm/stat.h in the ARM toolchain.
 * Keep this private: the loader's config and context layouts stay unchanged. */
struct file_stamp {
    unsigned long long device;
    unsigned char pad0[4];
    u32 old_inode, mode, links, uid, gid;
    unsigned long long rdev;
    unsigned char pad3[4];
    long long size;
    u32 block_size;
    unsigned long long blocks;
    u32 atime, atime_ns, mtime, mtime_ns, ctime, ctime_ns;
    unsigned long long inode;
};
typedef char stamp_size_check[sizeof(struct file_stamp)==104?1:-1];
typedef char stamp_size_offset_check[__builtin_offsetof(struct file_stamp,size)==48?1:-1];
typedef char stamp_mtime_offset_check[__builtin_offsetof(struct file_stamp,mtime)==80?1:-1];
struct table_cache {
    u32 valid, generation, branch, reads, hit;
    struct file_stamp stamp;
    char path[272];
};
/* A context slot owns both its branch tables and these caches until Update
 * returns. Other threads never wait for it or reuse its tables while a writer
 * runs. Slot 0 is the direct branch, slot 1 the stock-composed branch. */
static struct table_cache table_caches[SLOTS][2];
struct selection_cache {
    u32 valid, generation, strength, reads, hit;
    struct file_stamp stamp;
    char path[256];
};
static struct selection_cache selection_caches[SLOTS];
/* The stock camera may submit an unchanged selection repeatedly. Rewriting the
 * same status through open/write/rename each time is repeated I/O inside the
 * capture thread, so one published state is reused until it stops matching.
 * The cached key covers only the fields that define the state; the counters are
 * refreshed by PUBLISH_REFRESH_HELD. Every obligation that made the write
 * necessary is kept: first publish, any state change, error and recovery, a
 * deleted or externally rewritten file, a failed write (no cache), and a
 * bounded counter refresh. */
enum { PUBLISH_REFRESH_UPDATES = 32 };
struct publish_cache {
    u32 valid, hash, updates, skips;
    struct file_stamp stamp;
};
/* Same ownership rule as the table and selection caches: one slot per slot. */
static struct publish_cache publish_caches[SLOTS];

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
static int file_stamp(const char *path,struct file_stamp *stamp) {
    int rc;
    do {rc=sys3(195,(int)path,(int)stamp,0);} while(rc==-4);
    return rc;
}
static int same_stamp(const struct file_stamp *a,const struct file_stamp *b) {
    /* Reads may change atime; it does not change the contents. */
    return a->device==b->device && a->inode==b->inode && a->mode==b->mode &&
        a->size==b->size && a->mtime==b->mtime && a->mtime_ns==b->mtime_ns &&
        a->ctime==b->ctime && a->ctime_ns==b->ctime_ns;
}
static void save_stamp(struct file_stamp *dst,const struct file_stamp *src) {
    /* A whole-struct assignment emits an unavailable libc memcpy on ARM. */
    dst->device=src->device;dst->inode=src->inode;dst->mode=src->mode;
    dst->size=src->size;dst->mtime=src->mtime;dst->mtime_ns=src->mtime_ns;
    dst->ctime=src->ctime;dst->ctime_ns=src->ctime_ns;
}
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
    struct table_cache *cache=&table_caches[s-contexts][0];
    struct table_cache *composed=&table_caches[s-contexts][1];
    struct selection_cache *choice=&selection_caches[s-contexts];
    p=append(p,",\"selection_reads\":");p=number(p,choice->reads);
    p=append(p,",\"selection_cache_hit\":");p=append(p,choice->hit?"true":"false");
    p=append(p,",\"table_reads\":");p=number(p,cache->reads+composed->reads);
    p=append(p,",\"table_cache_hit\":");p=append(p,(cache->hit||composed->hit)?"true":"false");
    p=append(p,",\"selection_path\":\"");p=append(p,s->path);p=append(p,"\"}\n");
    char *q=append(path,"/var/run/nx300m-lut-trace-");q=number(q,s->tid);append(q,".json");
    q=append(temp,path);append(q,".tmp");
    if(!write_file(temp,text,(unsigned)(p-text))) sys3(38,(int)temp,(int)path,0);
}
static void unmatched(u32 tid,unsigned channel,const u16 *source) {
    if(!tracing()) return;
    char text[192],path[96],temp[104];char *p=text;
    p=append(p,"{\"context_found\":false,\"writer_tid\":");p=number(p,tid);
    p=append(p,",\"channel\":");p=number(p,channel);
    p=append(p,",\"source_ptr\":");p=number(p,(u32)source);
    p=append(p,",\"forwarded_ptr\":");p=number(p,(u32)source);p=append(p,"}\n");
    char *q=append(path,"/var/run/nx300m-lut-unmatched-");q=number(q,tid);append(q,".json");
    q=append(temp,path);append(q,".tmp");
    if(!write_file(temp,text,(unsigned)(p-text))) sys3(38,(int)temp,(int)path,0);
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
    struct selection_cache *cache=&selection_caches[s-contexts];
    struct file_stamp before,after;
    cache->hit=0;
    s->active=0;s->generation=0;s->strength=100;s->forwarded=0;s->composed=0;s->path[0]=0;
    int rc=file_stamp("/var/run/nx300m-lut-selection",&before);
    if(rc==-2) {cache->valid=0;return;} /* Missing: no read can succeed, skip the failed open. */
    int stamped=!rc;
    if(stamped && cache->valid && same_stamp(&cache->stamp,&before)) {
        cache->hit=1;
        s->active=1;s->generation=cache->generation;s->strength=cache->strength;
        append(s->path,cache->path);return;
    }
    cache->valid=0;
    ++cache->reads;
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
    /* Controls publish by rename. Also detect same-size edits and refuse a
     * changing read; each context owns its cache without any shared lock. */
    if(stamped && !file_stamp("/var/run/nx300m-lut-selection",&after)) {
        if(!same_stamp(&before,&after)) goto invalid;
        save_stamp(&cache->stamp,&after);append(cache->path,s->path);
        cache->generation=s->generation;cache->strength=s->strength;cache->valid=1;
    }
    return;
invalid:
    s->active=0;s->path[0]=0;s->generation=0;s->strength=100;
}
static int load_table(struct context *s,int stock) {
    unsigned slot=stock?1:0;
    struct table_cache *cache=&table_caches[s-contexts][slot];
    u16 *table=s->tables[slot];
    /* sizeof on the member array, not on the local pointer: the local is a
     * u16* and sizeof(*table) would be 2. */
    const unsigned table_bytes=sizeof(s->tables[slot]);
    struct file_stamp before,after;
    char path[272]; append(path,s->path);
    unsigned n=length(path);
    int rc;
    if(stock && n>=12 && equal(path+n-12,"/table.d4lut")) {
        append(path+n-12,"/table-stock3d.d4lut");
        rc=file_stamp(path,&before);
        if(rc==-2) {
            append(path,s->path);
            rc=file_stamp(path,&before);
        } else s->composed=1;
    } else rc=file_stamp(path,&before);
    /* Path + stamp fully identify the table contents: a selection write that
     * only bumps the generation (every engine stage does) must not force a
     * synchronous card read of unchanged bytes inside the capture callback. */
    if(!rc && cache->valid &&
       cache->branch==s->source_branch && equal(cache->path,path) &&
       same_stamp(&cache->stamp,&before)) {cache->hit=1;s->loaded_slot=slot;return 1;}
    cache->valid=0;
    /* If stat64 is unavailable, retain the uncached reader. Never serve an
     * old table after a missing, replaced, edited or unreadable file. */
    if(!rc && before.size!=(long long)table_bytes) return 0;
    ++cache->reads;++s->stat_reads;
    int got=read_file(path,table,table_bytes);
    if(got==-2 && s->composed) {
        s->composed=0;append(path,s->path);rc=file_stamp(path,&before);
        ++cache->reads;++s->stat_reads;got=read_file(path,table,table_bytes);
    }
    if(got!=(int)table_bytes) return 0;
    for(unsigned i=0;i<TOTAL;i++) if(table[i]>1023 || (i%WORDS==WORDS-1 && table[i])) return 0;
    if(!rc && !file_stamp(path,&after)) {
        if(!same_stamp(&before,&after)) return 0;
        save_stamp(&cache->stamp,&after);append(cache->path,path);
        cache->generation=s->generation;cache->branch=s->source_branch;cache->valid=1;
    }
    s->loaded_slot=slot;
    return 1;
}
/* Keep status writes out of libc; only a complete channel triplet is published.
 * The pid-specific temporary path cannot collide with the preload adapter. */
static u32 hash_text(u32 h,const char *s) {
    while(*s) { h^=(unsigned char)*s++; h*=16777619U; }
    return h;
}
static u32 hash_number(u32 h,u32 v) { return (h^v)*16777619U; }
static void publish(struct context *s) {
    char text[1040],temp[96]; char *p=text;
    struct file_stamp stamp;
    struct publish_cache *cache=&publish_caches[s-contexts];
    /* Identity of the published state, excluding the counters that only move
     * with time. Derived stage/composition text is a function of these. */
    u32 hash=hash_text(2166136261U,nx_lut_ram_config.root);
    hash=hash_text(hash,s->path);
    hash=hash_number(hash,s->generation);
    hash=hash_number(hash,s->strength);
    hash=hash_number(hash,s->active);
    hash=hash_number(hash,s->active&&s->composed==COMPOSED);
    if(cache->valid&&cache->hash==hash&&s->stat_updates-cache->updates<PUBLISH_REFRESH_UPDATES) {
        /* Only an exact match with the file this hook last wrote may skip:
         * removal or an independent replacement changes inode/mtime/ctime. */
        if(!file_stamp("/var/run/nx300m-lut-status.json",&stamp)&&
           same_stamp(&cache->stamp,&stamp)) { ++cache->skips; return; }
    }
    cache->valid=0;
    s->stat_publishes++;
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
    p=append(p,"\",\"image_verified\":false,\"loader\":\"ram-blob\",\"stats\":{\"updates\":");
    p=number(p,s->stat_updates);
    p=append(p,",\"ipcs_completes\":");p=number(p,s->stat_ipcs_completes);
    p=append(p,",\"publishes\":");p=number(p,s->stat_publishes);
    p=append(p,",\"publish_skips\":");p=number(p,cache->skips);
    p=append(p,",\"branch\":");p=number(p,s->source_branch);
    p=append(p,",\"cache_reads\":");p=number(p,s->stat_reads);
    p=append(p,"}}\n");
    char *q=append(temp,"/var/run/nx300m-lut-ram-");q=number(q,s->tid);append(q,".tmp");
    if(write_file(temp,text,(unsigned)(p-text))) return;
    if(sys3(38,(int)temp,(int)"/var/run/nx300m-lut-status.json",0)) return;
    /* Remember exactly the file this call produced; a later update republishes
     * as soon as that identity differs in any way. */
    if(!file_stamp("/var/run/nx300m-lut-status.json",&stamp)) {
        save_stamp(&cache->stamp,&stamp);
        cache->hash=hash;cache->updates=s->stat_updates;cache->valid=1;
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
    table_caches[s-contexts][0].hit=0;
    table_caches[s-contexts][1].hit=0;
    s->source_branch=0;s->order_count=0;s->trace_enabled=(u32)tracing();
    s->stat_updates++;
    for(unsigned i=0;i<3;i++) {
        s->channel_order[i]=0;s->writer_tids[i]=0;s->source_ptrs[i]=0;s->forwarded_ptrs[i]=0;
    }
    trace(s,"update-enter");
    /* Stock Reset is exactly this cache assignment. Only our dedicated
     * 3D-LUT virtual method enters here; no other parameter cache is touched. */
    ((u32 *)self)[1]=~0U;
    original(self,params);
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
    if(s->active && s->strength==100) {
        const u16 *table=s->tables[s->loaded_slot]+channel*WORDS;
        s->forwarded_ptrs[channel]=(u32)table;original(channel,table);
    } else if(s->active && s->strength) {
        u16 mixed[WORDS];
        for(unsigned i=0;i<WORDS;i++) mixed[i]=(u16)(((u32)source[i]*(100-s->strength)+(u32)s->tables[s->loaded_slot][channel*WORDS+i]*s->strength+50)/100);
        s->forwarded_ptrs[channel]=(u32)mixed;original(channel,mixed);
    } else {s->forwarded_ptrs[channel]=(u32)source;original(channel,source);}
    s->forwarded|=1U<<channel;
    if(s->forwarded==7) { s->stat_ipcs_completes++; publish(s); }
}
