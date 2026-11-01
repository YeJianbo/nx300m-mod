/* One existing SmartFilter slot hosts any selected look. No global UserData,
 * menu count, JPEG LUT hook, image-quality setting or pixel format is changed. */
#include "smartfilter_ram.h"

__attribute__((visibility("hidden"),section(".config"))) struct nx_sfr_config nx_sfr_config;
__attribute__((visibility("hidden"),section(".runtime"))) struct nx_sfr_runtime nx_sfr_runtime;

static unsigned get(const volatile unsigned *p) { return __atomic_load_n(p,__ATOMIC_ACQUIRE); }
static void put(volatile unsigned *p,unsigned n) { __atomic_store_n(p,n,__ATOMIC_RELEASE); }
static void copy(void *to,const void *from,unsigned n) {
    unsigned char *d=to; const unsigned char *s=from; while(n--) *d++=*s++;
}
static int syscall6(unsigned number,unsigned a,unsigned b,unsigned c,unsigned d,unsigned e,unsigned f) {
    register unsigned r0 __asm__("r0")=a, r1 __asm__("r1")=b, r2 __asm__("r2")=c;
    register unsigned r3 __asm__("r3")=d, r4 __asm__("r4")=e, r5 __asm__("r5")=f;
    register unsigned r7 __asm__("r7")=number;
    __asm__ volatile("svc 0" : "+r"(r0) : "r"(r1),"r"(r2),"r"(r3),"r"(r4),"r"(r5),"r"(r7) : "memory","cc");
    return (int)r0;
}
static unsigned tid(void *unused) { (void)unused; return (unsigned)syscall6(224,0,0,0,0,0,0); }
static void observe(volatile unsigned *calls,volatile unsigned *last,volatile unsigned *mask,unsigned value) {
    __sync_fetch_and_add(calls,1); put(last,value);
    __sync_fetch_and_or(mask,1U<<(value<31?value:31));
}
static void enter(void) { __sync_fetch_and_add(&nx_sfr_runtime.inflight,1); }
static void leave(void) { __sync_fetch_and_sub(&nx_sfr_runtime.inflight,1); }
static void release_backend(void) {
    if(__sync_sub_and_fetch(&nx_sfr_runtime.backend_active,1)==0)
        syscall6(240,(unsigned)&nx_sfr_runtime.backend_active,1,0x7fffffffU,0,0,0);
}
static int begin(void *opaque) {
    struct nx_sfr_runtime *s=&nx_sfr_runtime;
    if(get(&s->captures)||get(&s->control_pause)) return NX_SF_BUSY;
    __sync_fetch_and_add(&s->backend_active,1);
    /* Admission and the second check close the capture-versus-begin race. */
    if(get(&s->captures)||get(&s->control_pause)) { release_backend(); return NX_SF_BUSY; }
    int rc=nx_sf_ipcs_begin(opaque);
    if(rc) release_backend();
    return rc;
}
static void end(void *opaque) { nx_sf_ipcs_end(opaque); release_backend(); }
/* Local wrappers give callback addresses PC-relative definitions. Taking an
 * external function's address can otherwise leave a link-time absolute GOT
 * word in a freestanding blob, even when no dynamic relocations remain. */
static int render(void *opaque,void *image,const unsigned short *table) { return nx_sf_ipcs_render(opaque,image,table); }
static int restore(void *opaque) { return nx_sf_ipcs_restore(opaque); }
static int passthrough(void *opaque,void *image) { return nx_sf_ipcs_passthrough(opaque,image); }
static int ensure(void) {
    struct nx_sfr_runtime *s=&nx_sfr_runtime;
    struct nx_sfr_config *c=&nx_sfr_config;
    if(get(&s->initialized)!=2) {
        if(!__sync_bool_compare_and_swap(&s->initialized,0,1)) return NX_SF_BUSY;
        struct nx_sf_backend b;
        b.opaque=&s->ipcs; b.thread_id=tid;
        b.stock_preview=(void *)c->original[NX_SFR_PREVIEW];
        b.stock_effect=(void *)c->original[NX_SFR_EFFECT];
        b.begin=begin; b.render=render; b.restore=restore;
        b.end=end; b.passthrough=passthrough;
        struct nx_sf_ipcs_api a; copy(&a,&c->api,sizeof a);
        void *instance=c->b2y_instance();
        int rc=instance?0:NX_SF_INVALID;
        for(unsigned i=0;!rc&&i<NX_SF_IPCS_SEMAPHORES;++i) {
            a.semaphore[i]=c->get_semaphore(instance,i);
            if(!a.semaphore[i]) rc=NX_SF_INVALID;
        }
        if(!rc) rc=nx_sf_init(&s->bridge,&b,NX_SFR_LIVEVIEW_MODE);
        if(!rc) rc=nx_sf_ipcs_init(&s->ipcs,&a,&s->bridge);
        if(!rc) rc=nx_sf_enable(&s->bridge,1);
        s->init_error=rc;
        put(&s->initialized,rc?3:2);
        if(rc) return rc;
    }
    if(s->synced_generation!=c->generation) {
        int rc=nx_sf_select(&s->bridge,c->look_id,c->generation,c->strength,
                           c->strength?c->table:(void *)0,c->strength?sizeof c->table:0);
        if(rc) return rc;
        s->synced_generation=c->generation;
    }
    return 0;
}
void nx_sfr_preview(void *self,void *image,void *data,unsigned mode) {
    enter();
    struct nx_sfr_runtime *s=&nx_sfr_runtime;
    observe(&s->preview_calls,&s->preview_last,&s->preview_mask,mode);
    void (*original)(void *,void *,void *,unsigned)=(void *)nx_sfr_config.original[NX_SFR_PREVIEW];
    int host=nx_sfr_config.enabled&&mode==NX_SFR_LIVEVIEW_MODE;
    int rc=host?ensure():-1;
    if(host) put(&s->ensure_last,(unsigned)rc);
    if(rc) original(self,image,data,mode);
    else nx_sf_preview(&s->bridge,self,image,data,mode);
    leave();
}
int nx_sfr_effect(unsigned mode,void *image) {
    enter();
    observe(&nx_sfr_runtime.effect_calls,&nx_sfr_runtime.effect_last,&nx_sfr_runtime.effect_mask,mode);
    int (*original)(unsigned,void *)=(void *)nx_sfr_config.original[NX_SFR_EFFECT];
    int rc=nx_sfr_config.enabled&&get(&nx_sfr_runtime.initialized)==2?
        nx_sf_effect(&nx_sfr_runtime.bridge,mode,image):original(mode,image);
    leave(); return rc;
}
int nx_sfr_prepare(void *self,void (*callback)(void)) {
    enter();
    struct nx_sfr_runtime *s=&nx_sfr_runtime;
    /* Keep this lease until the real FinishOperation, even when Prepare fails:
     * Prepare's return is an operation status, not proof that locks are free. */
    __sync_fetch_and_add(&s->prepare_calls,1);
    __sync_fetch_and_add(&s->captures,1);
    unsigned active;
    while((active=get(&s->backend_active))) {
        ++s->capture_waits;
        syscall6(240,(unsigned)&s->backend_active,0,active,0,0,0);
    }
    int (*original)(void *,void (*)(void))=(void *)nx_sfr_config.original[NX_SFR_PREPARE];
    int rc=original(self,callback);
    if(rc<0) __sync_fetch_and_add(&s->capture_errors,1);
    leave(); return rc;
}
int nx_sfr_finish(void *self) {
    enter();
    __sync_fetch_and_add(&nx_sfr_runtime.finish_calls,1);
    int (*original)(void *)=(void *)nx_sfr_config.original[NX_SFR_FINISH];
    int rc=original(self);
    /* A Finish already underway when hooks were installed has no MOD lease. */
    unsigned n;
    do { n=get(&nx_sfr_runtime.captures); }
    while(n&&!__sync_bool_compare_and_swap(&nx_sfr_runtime.captures,n,n-1));
    leave(); return rc;
}
unsigned nx_sfr_still_mode(void *self) {
    enter();
    unsigned (*original)(void *)=(void *)nx_sfr_config.original[NX_SFR_STILL_MODE];
    unsigned mode=original(self);
    observe(&nx_sfr_runtime.still_calls,&nx_sfr_runtime.still_last,&nx_sfr_runtime.still_mask,mode);
    if(nx_sfr_config.enabled&&mode==NX_SFR_STILL_HOST_MODE) mode=0;
    leave(); return mode;
}
