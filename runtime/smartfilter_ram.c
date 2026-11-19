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
#ifdef NX_SFR_TEST_SYSCALL
/* Standalone host test only; production keeps the inline ARM EABI boundary. */
extern int nx_sfr_test_syscall(unsigned,unsigned,unsigned,unsigned,unsigned,unsigned,unsigned);
#endif
static int syscall6(unsigned number,unsigned a,unsigned b,unsigned c,unsigned d,unsigned e,unsigned f) {
#ifdef NX_SFR_TEST_SYSCALL
    return nx_sfr_test_syscall(number,a,b,c,d,e,f);
#else
    register unsigned r0 __asm__("r0")=a, r1 __asm__("r1")=b, r2 __asm__("r2")=c;
    register unsigned r3 __asm__("r3")=d, r4 __asm__("r4")=e, r5 __asm__("r5")=f;
    register unsigned r7 __asm__("r7")=number;
    __asm__ volatile("svc 0" : "+r"(r0) : "r"(r1),"r"(r2),"r"(r3),"r"(r4),"r"(r5),"r"(r7) : "memory","cc");
    return (int)r0;
#endif
}
static unsigned tid(void *unused) { (void)unused; return (unsigned)syscall6(224,0,0,0,0,0,0); }
#ifdef NX_SFR_SESSION
static unsigned session_ms(void);
static void sync_sensor_fps(unsigned s2_active);
#endif
struct nx_sft_record *nx_sfr_trace_current(void) {
    struct nx_sfr_runtime *s=&nx_sfr_runtime;
    if(!nx_sfr_config.timing_enabled||!get(&s->timing.next_frame)) return (void *)0;
    return nx_sft_current(&s->timing,tid((void *)0));
}
static struct nx_sft_record *trace_callback(void) { return nx_sfr_trace_current(); }
static struct nx_sft_record *reserve_trace(unsigned kind,unsigned self) {
    struct nx_sfr_config *c=&nx_sfr_config;
    struct nx_sft_timing *t=&nx_sfr_runtime.timing;
    if(!c->timing_enabled||!get(&t->enabled)||!get(&t->armed)||get(&t->frozen)) return (void *)0;
    struct nx_sft_record *r=nx_sft_reserve(t,kind,tid((void *)0),c->generation,c->strength,self);
    if(r) r->captures_begin=get(&nx_sfr_runtime.captures);
    return r;
}
static int probe_owns(struct nx_sft_record *r) {
    struct nx_sfr_runtime *s=&nx_sfr_runtime;
    return nx_sfr_config.probe_once&&r&&get(&s->probe.state)==NX_SFR_PROBE_CLAIMED&&
        s->probe.slot<NX_SFT_FRAMES&&r==&s->timing.frames[s->probe.slot]&&
        (r->flags&NX_SFT_F_PROBE);
}
static void probe_note(struct nx_sft_record *r,unsigned outcome) {
    if(probe_owns(r)) nx_sfr_runtime.probe.outcome=outcome;
}
static unsigned probe_path(struct nx_sft_record *r) {
    if(!nx_sfr_config.probe_once||probe_owns(r)) return NX_SFT_PATH_UNKNOWN;
    unsigned state=get(&nx_sfr_runtime.probe.state);
    if(state==NX_SFR_PROBE_CLAIMED||state==NX_SFR_PROBE_RETURNED)
        return NX_SFT_PATH_PROBE_EXHAUSTED;
    if(state!=NX_SFR_PROBE_ARMED) return NX_SFT_PATH_PROBE_BLOCKED;
    return NX_SFT_PATH_UNKNOWN;
}
/* Called only by begin, while the bridge owns FRAME. Even failed begin consumes
 * the attempt. No IPCS API, semaphore or backend lease is touched on rejection. */
static int probe_begin(struct nx_sft_record *r) {
    struct nx_sfr_config *c=&nx_sfr_config;
    struct nx_sfr_runtime *s=&nx_sfr_runtime;
    struct nx_sft_timing *t=&s->timing;
    struct nx_sfr_probe *p=&s->probe;
    if(!c->probe_once) return 0;
    unsigned state=get(&p->state),bad=NX_SFR_PROBE_NONE,slot=~0U;
    if(state==NX_SFR_PROBE_CLAIMED||state==NX_SFR_PROBE_RETURNED||state==NX_SFR_PROBE_BLOCKED) {
        if(r) r->path=state==NX_SFR_PROBE_BLOCKED?NX_SFT_PATH_PROBE_BLOCKED:NX_SFT_PATH_PROBE_EXHAUSTED;
        return NX_SF_BUSY;
    }
    if(state!=NX_SFR_PROBE_ARMED) bad=NX_SFR_PROBE_INVALID;
    else if(!c->timing_enabled||!get(&t->enabled)||!get(&t->armed)||get(&t->frozen)||!r)
        bad=NX_SFR_PROBE_TRACE;
    else {
        for(unsigned i=0;i<NX_SFT_FRAMES;++i) if(r==&t->frames[i]) { slot=i; break; }
        if(slot==~0U||slot>=get(&t->next_frame)||r->owner!=t||r->kind!=NX_SFT_FRAME||
           get(&r->state)!=NX_SFT_PENDING||r->tid!=tid((void *)0)||t->schema!=NX_SFT_SCHEMA||
           p->window!=t->window||r->window!=p->window||
           p->generation!=c->generation||t->generation!=p->generation||r->generation!=p->generation)
            bad=NX_SFR_PROBE_INVALID;
        else if(!r->start.valid||!r->current_time.valid||(r->flags&NX_SFT_F_CLOCK)||r->invalid_mask)
            bad=NX_SFR_PROBE_TRACE;
    }
    unsigned next=bad?NX_SFR_PROBE_BLOCKED:NX_SFR_PROBE_CLAIMED;
    if(!__sync_bool_compare_and_swap(&p->state,state,next)) {
        /* A losing CAS must not inspect the winner's not-yet-bound slot. */
        if(r) r->path=get(&p->state)==NX_SFR_PROBE_BLOCKED?NX_SFT_PATH_PROBE_BLOCKED:NX_SFT_PATH_PROBE_EXHAUSTED;
        return NX_SF_BUSY;
    }
    p->slot=bad?~0U:slot; p->outcome=bad;
    if(bad) { if(r) r->path=NX_SFT_PATH_PROBE_BLOCKED; return NX_SF_BUSY; }
    r->flags|=NX_SFT_F_PROBE;
    return 0;
}
static void commit_trace(struct nx_sft_record *r,int rc) {
    struct nx_sfr_runtime *s=&nx_sfr_runtime;
    nx_sft_commit(&s->timing,r,rc,get(&s->s2_fault)|get(&s->bridge.fault),get(&s->captures));
    /* Nested effect commits pass NULL. Only the bound outer FRAME may publish
     * RETURNED, after its append-only timing record is completely committed. */
    if(probe_owns(r)&&get(&r->state)==NX_SFT_COMMITTED) {
        unsigned outcome=s->probe.outcome;
        if(r->flags&NX_SFT_F_UNLOCK) outcome=NX_SFR_PROBE_UNLOCK;
        else if(r->flags&NX_SFT_F_RESTORE) outcome=NX_SFR_PROBE_RESTORE;
        else if(!outcome) {
            if(r->fault) outcome=NX_SFR_PROBE_FAULT;
            else if(r->path==NX_SFT_PATH_CAPTURE) outcome=NX_SFR_PROBE_CAPTURE;
            else if(r->path==NX_SFT_PATH_CONTROL) outcome=NX_SFR_PROBE_CONTROL;
            else if(!rc&&r->path==NX_SFT_PATH_RENDER&&r->count[NX_SFT_YCC]) outcome=NX_SFR_PROBE_SUCCESS;
            else outcome=NX_SFR_PROBE_INVALID;
        }
        if((outcome==NX_SFR_PROBE_SUCCESS)&&((r->flags&NX_SFT_F_CLOCK)||r->invalid_mask||!r->end.valid))
            outcome=NX_SFR_PROBE_TRACE;
        s->probe.outcome=outcome;
        put(&s->probe.state,NX_SFR_PROBE_RETURNED);
    }
}
static unsigned bypass_path(struct nx_sft_record *r) {
    struct nx_sfr_runtime *s=&nx_sfr_runtime;
    unsigned path=probe_path(r);
    if(path!=NX_SFT_PATH_UNKNOWN) return path;
    if(get(&s->s2_fault)||get(&s->bridge.fault)) return NX_SFT_PATH_FAULT;
    if(get(&s->captures)||get(&s->bridge.capture_pending)) return NX_SFT_PATH_CAPTURE;
    if(get(&s->control_pause)||get(&s->bridge.paused)) return NX_SFT_PATH_CONTROL;
    if(!nx_sfr_config.enabled) return NX_SFT_PATH_DISABLED;
    if(!nx_sfr_config.strength) return NX_SFT_PATH_STRENGTH_ZERO;
    return NX_SFT_PATH_BUSY;
}
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
#ifdef NX_SFR_SESSION
static int session_initialize(void);
static int session_frame_begin(void);
static void session_frame_end(void);
static struct nx_sfr_capture_owner *capture_owner(int);
#endif
static int begin(void *opaque) {
    struct nx_sfr_runtime *s=&nx_sfr_runtime;
    struct nx_sft_record *r=nx_sfr_trace_current();
    struct nx_sft_mark m=nx_sft_enter_stage(r,NX_SFT_BEGIN);
    int rc=probe_begin(r);
    if(rc) { nx_sft_leave_stage(r,m,rc); return rc; }
    if(get(&s->captures)||get(&s->control_pause)) {
        if(r) r->path=bypass_path(r);
        probe_note(r,get(&s->captures)?NX_SFR_PROBE_CAPTURE:NX_SFR_PROBE_CONTROL);
        nx_sft_leave_stage(r,m,NX_SF_BUSY); return NX_SF_BUSY;
    }
    __sync_fetch_and_add(&s->backend_active,1);
    /* Admission and the second check close the capture-versus-begin race. */
    if(get(&s->captures)||get(&s->control_pause)) {
        if(r) r->path=bypass_path(r);
        probe_note(r,get(&s->captures)?NX_SFR_PROBE_CAPTURE:NX_SFR_PROBE_CONTROL);
        release_backend(); nx_sft_leave_stage(r,m,NX_SF_BUSY); return NX_SF_BUSY;
    }
#ifdef NX_SFR_SESSION
    rc=session_frame_begin();
    if(rc) { release_backend(); nx_sft_leave_stage(r,m,rc); return rc; }
#endif
    rc=nx_sf_ipcs_begin(opaque);
    if(rc) {
#ifdef NX_SFR_SESSION
        session_frame_end();
#endif
        probe_note(r,rc==NX_SF_BUSY?NX_SFR_PROBE_BUSY:NX_SFR_PROBE_INVALID);
        release_backend();
    }
    nx_sft_leave_stage(r,m,rc);
    return rc;
}
static void end(void *opaque) {
    nx_sf_ipcs_end(opaque);
#ifdef NX_SFR_SESSION
    session_frame_end();
#endif
    struct nx_sft_record *r=nx_sfr_trace_current();
    if(r&&(r->flags&NX_SFT_F_UNLOCK)) probe_note(r,NX_SFR_PROBE_UNLOCK);
    release_backend();
}
/* Local wrappers give callback addresses PC-relative definitions. Taking an
 * external function's address can otherwise leave a link-time absolute GOT
 * word in a freestanding blob, even when no dynamic relocations remain. */
static int render(void *opaque,void *image,const unsigned short *table) {
    int rc=nx_sf_ipcs_render(opaque,image,table);
    if(rc) probe_note(nx_sfr_trace_current(),NX_SFR_PROBE_RENDER);
    return rc;
}
static int restore(void *opaque) {
    struct nx_sft_record *r=nx_sfr_trace_current();
    struct nx_sfr_runtime *s=&nx_sfr_runtime;
    /* The bridge can yield to capture/control after begin, before render. */
    if(probe_owns(r)&&s->probe.outcome==NX_SFR_PROBE_NONE&&r->path!=NX_SFT_PATH_RENDER) {
        if(get(&s->captures)||get(&s->bridge.capture_pending)) {
            r->path=NX_SFT_PATH_CAPTURE; probe_note(r,NX_SFR_PROBE_CAPTURE);
        } else if(get(&s->control_pause)||get(&s->bridge.paused)) {
            r->path=NX_SFT_PATH_CONTROL; probe_note(r,NX_SFR_PROBE_CONTROL);
        }
    }
    int rc=nx_sf_ipcs_restore(opaque);
    if(rc) probe_note(r,NX_SFR_PROBE_RESTORE);
    return rc;
}
static int passthrough(void *opaque,void *image) {
    struct nx_sft_record *r=nx_sfr_trace_current();
    if(r) {
        unsigned path=probe_path(r);
        if(path!=NX_SFT_PATH_UNKNOWN) r->path=path;
        else if(r->path==NX_SFT_PATH_RENDER) r->path=NX_SFT_PATH_FALLBACK;
        else if(r->path==NX_SFT_PATH_UNKNOWN||r->path==NX_SFT_PATH_STOCK) r->path=bypass_path(r);
    }
    return nx_sf_ipcs_passthrough(opaque,image);
}
static unsigned real_mode(void) {
    void *common=nx_sfr_config.common_instance();
    unsigned (*original)(void *)=(void *)nx_sfr_config.original[NX_SFR_LIVEVIEW_MODE_GETTER];
    return common?original(common):~0U;
}
static int managed_image(void *image) {
    /* ADE address type 10 is the first work plane, stored at image+0x34.
     * It is never an IPCS input/output plane in our YCC adapter. */
    return nx_sfr_config.profile==NX_SFR_S2&&nx_sfr_config.work_base&&
        nx_sfr_config.api.address(image,10)==nx_sfr_config.work_base;
}
static int direct_passthrough(void *image) {
    struct nx_sf_ipcs direct;
    /* This local adapter is not initialized through nx_sf_ipcs_init. Initialize
     * the callback explicitly; never read an indeterminate stack function ptr. */
    direct.trace_current=trace_callback;
    copy(&direct.api,&nx_sfr_config.api,sizeof direct.api);
    struct nx_sft_record *r=nx_sfr_trace_current();
    unsigned path=probe_path(r);
    if(r&&path!=NX_SFT_PATH_UNKNOWN) r->path=path;
    else if(r&&(r->path==NX_SFT_PATH_UNKNOWN||r->path==NX_SFT_PATH_STOCK))
        r->path=bypass_path(r);
    else if(r&&r->path==NX_SFT_PATH_RENDER) r->path=NX_SFT_PATH_FALLBACK;
    __sync_fetch_and_add(&nx_sfr_runtime.s2_fallback,1);
    int rc=nx_sf_ipcs_passthrough(&direct,image);
    if(rc) { put(&nx_sfr_runtime.s2_fault,1); nx_sfr_runtime.init_error=rc; }
    return rc;
}
static int synthetic_scope(void);
static int stock_effect(unsigned effect,void *image) {
    /* A managed-work frame must never enter a native scratch-using effect,
     * including bridge context exhaustion and failed passthrough validation. */
    struct nx_sft_record *r=nx_sfr_trace_current();
    if(r&&r->path==NX_SFT_PATH_UNKNOWN) r->path=NX_SFT_PATH_NO_CONTEXT;
    if(managed_image(image)||synthetic_scope()) return direct_passthrough(image);
    if(r) r->path=NX_SFT_PATH_STOCK;
    int (*original)(unsigned,void *)=(void *)nx_sfr_config.original[NX_SFR_EFFECT];
    return original(effect,image);
}
static struct nx_sfr_origin *origin(unsigned id,int create) {
    for(unsigned i=0;i<NX_SF_CONTEXTS;++i)
        if(get(&nx_sfr_runtime.origins[i].tid)==id) return &nx_sfr_runtime.origins[i];
    if(create) for(unsigned i=0;i<NX_SF_CONTEXTS;++i)
        if(__sync_bool_compare_and_swap(&nx_sfr_runtime.origins[i].tid,0,id)) return &nx_sfr_runtime.origins[i];
    return (void *)0;
}
static int synthetic_scope(void) {
    if(nx_sfr_config.profile!=NX_SFR_S2) return 0;
    struct nx_sfr_origin *o=origin(tid((void *)0),0);
    return o&&get(&o->active);
}
static int ensure_body(struct nx_sft_record *r) {
    struct nx_sfr_runtime *s=&nx_sfr_runtime;
    struct nx_sfr_config *c=&nx_sfr_config;
    if(get(&s->initialized)!=2) {
        if(!__sync_bool_compare_and_swap(&s->initialized,0,1)) return NX_SF_BUSY;
        if(r) r->flags|=NX_SFT_F_COLD;
        struct nx_sft_mark m=nx_sft_enter_stage(r,NX_SFT_INITIALIZE);
        struct nx_sf_backend b;
        b.opaque=&s->ipcs; b.thread_id=tid;
        b.stock_preview=(void *)c->original[NX_SFR_PREVIEW];
        b.stock_effect=stock_effect;
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
        if(!rc) s->ipcs.trace_current=trace_callback;
#ifdef NX_SFR_SESSION
        if(!rc) rc=session_initialize();
#endif
        if(!rc) rc=nx_sf_enable(&s->bridge,1);
        s->init_error=rc;
        put(&s->initialized,rc?3:2);
        nx_sft_leave_stage(r,m,rc);
        if(rc) return rc;
    }
    if(s->synced_generation!=c->generation) {
        if(r) r->flags|=NX_SFT_F_SELECT;
        struct nx_sft_mark m=nx_sft_enter_stage(r,NX_SFT_SELECT);
        int rc=nx_sf_select(&s->bridge,c->look_id,c->generation,c->strength,
                           c->strength?c->table:(void *)0,c->strength?sizeof c->table:0);
        nx_sft_leave_stage(r,m,rc);
        if(rc) return rc;
        s->synced_generation=c->generation;
    }
    return 0;
}
static int ensure(void) {
    struct nx_sft_record *r=nx_sfr_trace_current();
    struct nx_sft_record *owned=r?(void *)0:reserve_trace(NX_SFT_INITIALIZATION,0);
    if(!r) r=owned;
    struct nx_sft_mark m=nx_sft_enter_stage(r,NX_SFT_ENSURE);
    int rc=ensure_body(r);
    nx_sft_leave_stage(r,m,rc);
    commit_trace(owned,rc);
    return rc;
}
void nx_sfr_preview(void *self,void *image,void *data,unsigned mode) {
    enter();
    struct nx_sft_record *r=reserve_trace(NX_SFT_FRAME,(unsigned)self);
    struct nx_sft_mark m=nx_sft_enter_stage(r,NX_SFT_PREVIEW);
    struct nx_sfr_runtime *s=&nx_sfr_runtime;
    observe(&s->preview_calls,&s->preview_last,&s->preview_mask,mode);
    void (*original)(void *,void *,void *,unsigned)=(void *)nx_sfr_config.original[NX_SFR_PREVIEW];
    struct nx_sfr_origin *o=nx_sfr_config.profile==NX_SFR_S2?origin(tid((void *)0),0):(void *)0;
    int synthetic=o&&get(&o->pending);
    if(o) put(&o->pending,0);
    if(synthetic) __sync_fetch_and_add(&o->active,1);
    int host=nx_sfr_config.enabled&&mode==NX_SFR_LIVEVIEW_MODE&&
        (nx_sfr_config.profile==NX_SFR_S1||(synthetic&&!get(&s->s2_fault)));
    if(synthetic) __sync_fetch_and_add(&s->s2_frames,1);
    int rc=host?ensure():-1;
    if(host) put(&s->ensure_last,(unsigned)rc);
    /* When not admitted, the original smartFilter still builds ADE_Image and
     * completes the original callback. Its effect hook recognises managed work
     * and fills output without opening IPCS or executing Miniature. */
    int result=0;
    if(rc) original(self,image,data,mode);
    else result=nx_sf_preview(&s->bridge,self,image,data,mode);
    if(synthetic) __sync_fetch_and_sub(&o->active,1);
    if(o&&!get(&o->active)&&!get(&o->pending)) put(&o->tid,0);
    nx_sft_leave_stage(r,m,result);
    commit_trace(r,result);
    leave();
}
int nx_sfr_effect(unsigned mode,void *image) {
    enter();
    struct nx_sft_record *r=nx_sfr_trace_current();
    struct nx_sft_record *record_owner=r?(void *)0:reserve_trace(NX_SFT_FRAME,0);
    if(!r) r=record_owner;
    struct nx_sft_mark m=nx_sft_enter_stage(r,NX_SFT_EFFECT);
    observe(&nx_sfr_runtime.effect_calls,&nx_sfr_runtime.effect_last,&nx_sfr_runtime.effect_mask,mode);
    int (*original)(unsigned,void *)=(void *)nx_sfr_config.original[NX_SFR_EFFECT];
    int owned=managed_image(image)||synthetic_scope();
    int rc;
    if(get(&nx_sfr_runtime.initialized)==2&&(nx_sfr_config.enabled||owned))
        rc=nx_sf_effect(&nx_sfr_runtime.bridge,mode,image);
    else if(owned) rc=direct_passthrough(image);
    else { if(r) r->path=NX_SFT_PATH_STOCK; rc=original(mode,image); }
    nx_sft_leave_stage(r,m,rc);
    commit_trace(record_owner,rc);
    leave(); return rc;
}
int nx_sfr_prepare(void *self,void (*callback)(void)) {
    enter();
    struct nx_sft_record *r=reserve_trace(NX_SFT_CAPTURE_PREPARE,(unsigned)self);
    struct nx_sfr_runtime *s=&nx_sfr_runtime;
#ifdef NX_SFR_SESSION
    /* B2Y Open's native lifecycle wait also handles a preview slower than the
     * bounded owner drain. Its caller already owns +0x44 at this point; a
     * retained power reference is not an active preview transaction.
     * Unknown/direct callers still must not wait for the owner here. */
    struct nx_sfr_capture_owner *capture=capture_owner(0);
    if(nx_sfr_config.session_mode&&get(&s->session_initialized)==2&&
       !(capture&&capture->native)&&
       (get(&s->session.held)||get(&s->session.state)==NX_IPS_OPENING||
        get(&s->session.state)==NX_IPS_DRAINING)) {
        nx_ips_fault(&s->session,-123); leave(); return -16;
    }
#endif
    /* Keep this lease until the real FinishOperation, even when Prepare fails:
     * Prepare's return is an operation status, not proof that locks are free. */
    __sync_fetch_and_add(&s->prepare_calls,1);
    __sync_fetch_and_add(&s->captures,1);
    struct nx_sft_mark m=nx_sft_enter_stage(r,NX_SFT_DRAIN);
    unsigned active;
    while((active=get(&s->backend_active))) {
        __sync_fetch_and_add(&s->capture_waits,1);
        syscall6(240,(unsigned)&s->backend_active,0,active,0,0,0);
    }
    nx_sft_leave_stage(r,m,0);
    int (*original)(void *,void (*)(void))=(void *)nx_sfr_config.original[NX_SFR_PREPARE];
    m=nx_sft_enter_stage(r,NX_SFT_PREPARE);
    int rc=original(self,callback);
    nx_sft_leave_stage(r,m,rc);
    if(rc<0) __sync_fetch_and_add(&s->capture_errors,1);
    commit_trace(r,rc);
    leave(); return rc;
}
int nx_sfr_finish(void *self) {
    enter();
    struct nx_sft_record *r=reserve_trace(NX_SFT_CAPTURE_FINISH,(unsigned)self);
    __sync_fetch_and_add(&nx_sfr_runtime.finish_calls,1);
    int (*original)(void *)=(void *)nx_sfr_config.original[NX_SFR_FINISH];
    struct nx_sft_mark m=nx_sft_enter_stage(r,NX_SFT_FINISH);
    int rc=original(self);
    nx_sft_leave_stage(r,m,rc);
    /* A Finish already underway when hooks were installed has no MOD lease. */
    unsigned n;
    do { n=get(&nx_sfr_runtime.captures); }
    while(n&&!__sync_bool_compare_and_swap(&nx_sfr_runtime.captures,n,n-1));
#ifdef NX_SFR_SESSION
    if(nx_sfr_config.session_mode&&n==1&&!get(&nx_sfr_runtime.b2y_leases)) {
        unsigned t=session_ms();
        put(&nx_sfr_runtime.capture_hold_ms,t?t:1U);
    }
#endif
    commit_trace(r,rc);
    leave(); return rc;
}
unsigned nx_sfr_still_mode(void *self) {
    enter();
    unsigned (*original)(void *)=(void *)nx_sfr_config.original[NX_SFR_STILL_MODE];
    unsigned mode=original(self);
    observe(&nx_sfr_runtime.still_calls,&nx_sfr_runtime.still_last,&nx_sfr_runtime.still_mask,mode);
    if(nx_sfr_config.profile==NX_SFR_S1&&nx_sfr_config.enabled&&mode==NX_SFR_STILL_HOST_MODE) mode=0;
    leave(); return mode;
}

__attribute__((noinline)) unsigned nx_sfr_liveview_mode(void *self) {
    unsigned lr=(unsigned)__builtin_return_address(0)&~1U;
    enter();
    struct nx_sfr_config *c=&nx_sfr_config;
    struct nx_sfr_runtime *s=&nx_sfr_runtime;
    unsigned (*original)(void *)=(void *)c->original[NX_SFR_LIVEVIEW_MODE_GETTER];
    unsigned mode=original(self),role=NX_SFR_MODE_CALLS;
    __sync_fetch_and_add(&s->mode_calls,1);
    for(unsigned i=0;i<NX_SFR_MODE_CALLS;++i) if(lr==c->mode_lr[i]) { role=i; break; }
    if(c->profile!=NX_SFR_S2||self!=c->common_instance()||role==NX_SFR_MODE_CALLS) goto stock;
    if(role==NX_SFR_CHANGE_CHECK||role==NX_SFR_CHANGE_CACHE) {
        /* These are the two stock topology-decision reads, not global
         * SmartFilter UserData. The stock item-change path compares/caches
         * this effective value and owns stream-off, drain and reconstruction. */
        if(mode!=15||!c->insert_enabled||!c->enabled||!c->strength||
           get(&s->s2_fault)||get(&s->bridge.fault)||get(&s->control_pause)) goto stock;
    } else if(role==NX_SFR_CREATE) {
        #ifdef NX_SFR_SESSION
        /* RecipeView::Register can prebuild in stopped state 1, followed by
         * Start(-1), which reuses that path without another Create. Preserve
         * both that path and a Start-owned Create in state 2. The first native
         * running callback queues our controlled topology rebuild. */
        unsigned *liveview=c->liveview_instance();
        unsigned state=get(liveview+0x34/4);
        __sync_fetch_and_add(&s->create_calls,1);
        put(&s->create_native_state,state);
        if(state==1||state==2) {
            __sync_fetch_and_add(&s->create_deferred,1);
            int want_fps=c->enabled&&c->strength&&c->insert_enabled&&!get(&s->s2_fault)&&
                !get(&s->bridge.fault)&&!get(&s->control_pause);
            sync_sensor_fps((unsigned)want_fps);
            put(&s->s2_path,0);goto stock;
        }
        #endif
        /* Create reads this before configuring B2Y/LDC. Keep its decision for
         * the whole topology, including a later soft stop without a rebuild. */
        int ready=mode==15&&c->insert_enabled&&c->enabled&&c->strength&&!get(&s->s2_fault)&&
            !get(&s->control_pause)&&c->work_base&&c->work_size==NX_SFR_WORK_BYTES&&!ensure();
        #ifdef NX_SFR_SESSION
        sync_sensor_fps((unsigned)ready);
        #endif
        put(&s->s2_path,(unsigned)ready);
        if(!ready) goto stock;
    } else if(role==NX_SFR_EXECUTE) {
        struct nx_sfr_origin *o=origin(tid((void *)0),mode==15);
        if(o) {
            put(&o->pending,mode==15);
            if(mode!=15&&!get(&o->active)) put(&o->tid,0);
        } else if(mode==15) put(&s->s2_fault,1);
        /* Even a queued frame delivered after soft stop must complete. Source
         * exhaustion falls back via managed ADE work, never native Miniature. */
        if(mode!=15) goto stock;
    } else if(mode!=15||!get(&s->s2_path)) goto stock;
    __sync_fetch_and_add(&s->mode_forged[role],1);
    leave(); return NX_SFR_LIVEVIEW_MODE;
stock:
    __sync_fetch_and_add(&s->mode_stock,1);
    leave(); return mode;
}

#ifdef NX_SFR_SESSION
#include "smartfilter_session.inc"
static unsigned refresh_goal(void) {
    struct nx_sfr_config *c=&nx_sfr_config;
    struct nx_sfr_runtime *s=&nx_sfr_runtime;
    return c->enabled&&c->strength&&c->insert_enabled&&!get(&s->s2_fault)&&!get(&s->bridge.fault);
}
static int refresh_running(void) {
    unsigned *liveview=nx_sfr_config.liveview_instance();
    /* SmartFilter Off (15) persists in playback: it says nothing about the
     * stream's lifecycle. Only a running, unqueued native liveview may start
     * our changeProcess. A completed native Start callback queues selections
     * made while stopped; the LUT choice remains available in playback. */
    return !get(&nx_sfr_runtime.stream_stopped)&&get(liveview+0x34/4)==7&&
        !get(liveview+0x38/4);
}
/* Clamp the liveview sensor framerate to the configured cap while S2 LUT
 * rendering is desired, and restore the factory rate once SmartFilter returns
 * to stock. The cap is control-stage input (loader --fps-limit 30/45/60); 0
 * keeps the previous 30 fps default. Ownership model: saved_sensor_fps holds
 * the factory value for as long as we own the rate, so 30->45->60 switches
 * never mistake an applied cap for the stock value; a stock-side rate change
 * (cur != the value we last left) releases it instead of restoring over it. */
static void sync_sensor_fps(unsigned s2_active) {
    struct nx_sfr_config *c=&nx_sfr_config;
    struct nx_sfr_runtime *s=&nx_sfr_runtime;
    if(c->profile!=NX_SFR_S2||c->probe_once) return;
    volatile unsigned *common=(volatile unsigned *)c->common_instance();
    if(!common) return;
    unsigned limit=c->fps_limit?c->fps_limit:30U;
    unsigned cur=get(&common[0x14/4]);
    unsigned saved=get(&s->saved_sensor_fps);
    unsigned active=get(&s->active_sensor_fps);
    if(saved&&cur!=active) {
        put(&s->saved_sensor_fps,0);
        saved=0;
    }
    if(s2_active) {
        if(!saved&&cur>limit) {
            /* First cap of this ownership; captured once, never overwritten
             * by a later cap, so it stays the pre-preview factory value. */
            put(&s->saved_sensor_fps,cur);
            saved=cur;
        }
        if(saved) {
            /* Only lower to the cap; a factory rate below it is kept. Raising
             * the cap here runs inside the rebuild's Create, the same safe
             * boundary the initial cap uses. */
            unsigned target=saved<limit?saved:limit;
            if(cur!=target) { put(&common[0x14/4],target); cur=target; }
        }
    } else if(saved) {
        if(cur==active&&cur!=saved) { put(&common[0x14/4],saved); cur=saved; }
        put(&s->saved_sensor_fps,0);
        saved=0;
    }
    put(&s->active_sensor_fps,cur);
}
/* Pending means the observed sensor rate is not the target for the current
 * goal, so the stock ChangeUserInfo(0x39) topology rebuild must run; the rate
 * is only written at Create inside that rebuild, never on the frame thread.
 * Both the frame-callback queue and the controller dispatch must use this one
 * predicate, or a queued request would be dropped without a rebuild. */
static int fps_pending(unsigned wanted) {
    struct nx_sfr_config *c=&nx_sfr_config;
    struct nx_sfr_runtime *s=&nx_sfr_runtime;
    if(c->profile!=NX_SFR_S2||c->probe_once) return 0;
    volatile unsigned *common=(volatile unsigned *)c->common_instance();
    if(!common) return 0;
    unsigned limit=c->fps_limit?c->fps_limit:30U;
    unsigned cur=get(&common[0x14/4]);
    unsigned saved=get(&s->saved_sensor_fps);
    /* A zero observed rate means the liveview rate is not established (or not
     * modeled by a fixture); there is nothing to converge and the initial cap
     * still happens at Create. */
    if(!cur) return 0;
    if(!wanted) return saved&&cur!=saved;
    /* Above the cap: lower it. Factory already lower: keep the factory value. */
    unsigned target=saved&&saved<limit?saved:limit;
    return target!=cur;
}
/* Defer post-capture LUT rebuild until still RAW/YCC/JPEG encoding counters
 * finish and a brief write-start window elapses, preventing 0x39 teardown from
 * competing with shutter-to-card-LED encoding. */
static int capture_settling(const struct nx_sfr_config *c,struct nx_sfr_runtime *s) {
    unsigned hold=get(&s->capture_hold_ms);
    const volatile unsigned *cc=(void *)0;
    if(c->capture_counter_ptr) cc=(const volatile unsigned *)*c->capture_counter_ptr;
    int enc_busy=cc&&(get(&cc[1])!=get(&cc[3])||get(&cc[7])!=get(&cc[8])||get(&cc[10])!=get(&cc[11]));
    if(!hold&&!enc_busy) return 0;
    unsigned now=session_ms();
    if(!now) now=1U;
    if(!hold) { put(&s->capture_hold_ms,now); hold=now; }
    unsigned elapsed=now-hold;
    if(elapsed>=4000U) { put(&s->capture_hold_ms,0); return 0; }
    if(enc_busy||elapsed<600U) {
        __sync_fetch_and_add(&s->refresh_deferred,1);
        return 1;
    }
    put(&s->capture_hold_ms,0);
    return 0;
}
/* Hot-swap mailbox: same identity/validation rules as the loader's apply
 * path, re-checked here at the safe frame boundary before anything commits.
 * Table and identity land first, generation last (release), so the existing
 * select-on-generation-change logic in ensure_body consumes a complete
 * request and never a torn one. A rejected request is dropped, never
 * half-applied; the loader observes the stalled generation and reports
 * honestly instead of claiming the new look is displayed. */
static unsigned mail_hash(const struct nx_sfr_mailbox *m) {
    /* FNV-1a over the contiguous payload: generation..table end. The struct
     * layout has no padding in this range and the loader zeroes it first. */
    const unsigned char *p=(const unsigned char *)&m->generation;
    const unsigned char *end=(const unsigned char *)(m->table+NX_SF_TABLE_WORDS);
    unsigned h=2166136261U;
    while(p<end) { h^=*p++; h*=16777619U; }
    return h;
}
static int mail_id_valid(const char *id) {
    unsigned n=0; if(!id||!id[0]) return 0;
    for(;id[n];++n) if(n>=NX_SF_LOOK_ID_SIZE-1||!((id[n]>='a'&&id[n]<='z')||(id[n]>='0'&&id[n]<='9')||id[n]=='_'||id[n]=='-')) return 0;
    return 1;
}
static int mail_table_valid(const unsigned short *table) {
    for(unsigned i=0;i<NX_SF_TABLE_WORDS;++i)
        if(table[i]>1023||(i%NX_SF_CHANNEL_WORDS==729&&table[i])) return 0;
    return 1;
}
static void mail_consume(struct nx_sfr_config *c,struct nx_sfr_runtime *s) {
    struct nx_sfr_mailbox *m=&s->mail;
    unsigned generation=get(&m->seq);
    int ok=generation&&generation!=c->generation&&m->checksum==mail_hash(m)&&
        mail_id_valid(m->look_id)&&mail_table_valid(m->table)&&m->strength<=100&&
        (m->fps_limit==0||m->fps_limit==30||m->fps_limit==45||m->fps_limit==60);
    if(!ok) { __sync_fetch_and_add(&s->mail_rejected,1); put(&m->seq,0); return; }
    copy(c->look_id,m->look_id,sizeof c->look_id);
    copy(c->table,m->table,sizeof c->table);
    c->strength=m->strength;
    c->fps_limit=m->fps_limit;
    c->enabled=1;
    c->insert_enabled=1;
    /* Keep the diagnostic ledger consistent with the consumed request: the
     * loader's resident validation requires timing generation/strength/look
     * to match the committed config. Same frame thread, ordered before the
     * generation release store. */
    s->timing.generation=generation;
    s->timing.strength=m->strength;
    copy(s->timing.look_id,m->look_id,sizeof s->timing.look_id);
    __atomic_store_n(&c->generation,generation,__ATOMIC_RELEASE);
    __sync_fetch_and_add(&s->mail_consumed,1);
    put(&m->seq,0);
}
/* Queue at the completed IPCM callback, before any SmartFilter node exists.
 * The frame thread never calls a stop/restart routine or waits for a rebuild. */
void nx_sfr_frame_callback(void *self,int which,void *image,void *data,unsigned tag) {
    enter();
    void (*original)(void *,int,void *,void *,unsigned)=(void *)nx_sfr_config.original[NX_SFR_FRAME_CALLBACK];
    original(self,which,image,data,tag);
    /* The native callback completes asynchronous Start and queued restarts.
     * Only its actual running state may reopen the retained stream gate. */
    session_resume_stream();
    struct nx_sfr_config *c=&nx_sfr_config;
    struct nx_sfr_runtime *s=&nx_sfr_runtime;
    /* Consume the latest hot-swap request before the topology decision so the
     * new generation's goal drives the same queued 0x39 rebuild path. The
     * boundary needs the preview path idle (no capture, no pending topology
     * operation, healthy session) — exactly where the refresh queue below is
     * allowed to act. */
    if(which==1&&c->profile==NX_SFR_S2&&!c->probe_once&&get(&s->initialized)==2&&
       !get(&s->control_pause)&&!get(&s->captures)&&!get(&s->b2y_leases)&&
       !get(&s->refresh_pending)&&!get(&s->refresh_queued)) {
        unsigned st=s->session.state,seq=get(&s->mail.seq);
        if(seq&&seq!=c->generation&&
           (st==NX_IPS_IDLE||st==NX_IPS_READY)&&s->session.held<2) mail_consume(c,s);
    }
    if(which==1&&c->profile==NX_SFR_S2&&!c->probe_once&&!get(&s->control_pause)&&
       !get(&s->captures)&&!get(&s->b2y_leases)&&!get(&s->refresh_pending)) {
        unsigned wanted=refresh_goal(),key=(c->generation<<1)|wanted;
        /* A cap change arrives as a config poke (generation++), so the rate
         * difference is the only signal; the safe switch point is the same
         * queued 0x39 rebuild, deferred while capture/encoding is settling. */
        int need_fps=fps_pending(wanted);
        if((wanted!=get(&s->s2_path)||need_fps)&&get(&s->refresh_attempt)!=key&&
           real_mode()==15&&refresh_running()&&(!wanted||!capture_settling(c,s))&&
           __sync_bool_compare_and_swap(&s->refresh_queued,0,1)) {
            /* Same 12-byte payload as ChangeUserInfo(0x39), with IPC_NOWAIT.
             * Stock Send uses blocking msgsnd; a full controller queue must
             * never stall the video callback. No UserData value is written. */
            unsigned message[4]={1,0x02010034,0x39,0};
            int rc=c->queue_send(*c->capture_queue,message,12,0x800);
            __sync_fetch_and_add(&s->refresh_requests,1);
            s->refresh_error=rc;
            if(!rc) {
                unsigned now=session_ms();
                put(&s->refresh_start_ms,now?now:1U);
            } else { put(&s->refresh_attempt,key); put(&s->refresh_queued,0); }
        }
    }
    leave();
}
/* Front-end completion can arrive after the initiating Capture message has
 * returned. Wake the controller once at the actual stopped callback; there
 * may be no more frames (or controller messages) until another photograph. */
void nx_sfr_change_stop_callback(void *self,int value) {
    enter();
    struct nx_sfr_config *c=&nx_sfr_config;
    struct nx_sfr_runtime *s=&nx_sfr_runtime;
    void (*original)(void *,int)=(void *)c->original[NX_SFR_CHANGE_STOP_CALLBACK];
    original(self,value);
    if(get(&s->refresh_pending)&&!get(&s->stream_stopped)&&self==c->liveview_instance()&&
       get((volatile unsigned *)((char *)self+0x34))==20&&
       __sync_bool_compare_and_swap(&s->refresh_queued,0,1)) {
        unsigned message[4]={1,0x02010034,0x39,0};
        int rc=c->queue_send(*c->capture_queue,message,12,0x800);
        __sync_fetch_and_add(&s->refresh_wakeups,1);
        s->refresh_error=rc;
        if(rc) put(&s->refresh_queued,0);
    }
    leave();
}
/* Run only after the native queue handler finishes on its controller thread.
 * The stock observer requires Buffer::CheckMapChange(), a byte set by a real
 * UserData/memory-map change. Our work area is already allocated, so there is
 * no such change. Use its same data-change decision and changeState(14) entry
 * without forging UserData or that byte. changeState owns transition queuing,
 * stop callbacks and drain; none runs on the frame thread. An unchanged buffer
 * map makes changeStopCallbackProcess enter state 20. Resume only our pending
 * transition through the stock memory-matching entry after native dispatch. */
int nx_sfr_message_handle(void *self) {
    enter();
    int (*original)(void *)=(void *)nx_sfr_config.original[NX_SFR_MESSAGE_HANDLE];
    int result=original(self);
    struct nx_sfr_config *c=&nx_sfr_config;
    struct nx_sfr_runtime *s=&nx_sfr_runtime;
    if(__sync_bool_compare_and_swap(&s->refresh_queued,1,2)) {
        if(!get(&s->refresh_pending)&&!get(&s->captures)&&!get(&s->b2y_leases)&&!get(&s->control_pause)&&
           c->profile==NX_SFR_S2&&!c->probe_once&&real_mode()==15&&refresh_running()) {
            unsigned wanted=refresh_goal();
            put(&s->refresh_attempt,(c->generation<<1)|wanted);
            if(wanted!=get(&s->s2_path)||fps_pending(wanted)) {
                void *liveview=c->liveview_instance();
                /* Native Start does not refresh its cached SmartFilter mode.
                 * After a native-first Create that cache can already equal
                 * our target despite the actual topology being different.
                 * Keep the stock mode-admission check and cache update, then
                 * rebuild for the observed topology mismatch itself. */
                if(c->liveview_restart_allowed(liveview)) {
                    (void)c->liveview_changed(liveview,0x39);
                    put(&s->refresh_pending,1);
                    __sync_fetch_and_add(&s->refresh_dispatches,1);
                    c->liveview_change_state(liveview,14);
                } else s->refresh_error=-11;
            }
        }
        put(&s->refresh_queued,0);
    }
    if(get(&s->refresh_pending)&&!get(&s->captures)&&!get(&s->b2y_leases)&&
       !get(&s->control_pause)&&c->profile==NX_SFR_S2&&!c->probe_once) {
        /* Verified 1.15 fields: current state +0x34, next action +0x38.
         * They are observed, never written here. The original resume checks
         * UserData against the current buffer memory state before rebuilding. */
        unsigned *liveview=c->liveview_instance();
        unsigned state=get(liveview+0x34/4);
        if(real_mode()!=15||get(&s->stream_stopped)||
           (state!=7&&state!=14&&state!=15&&state!=20)) {
            put(&s->refresh_pending,0); put(&s->refresh_start_ms,0);
        }
        else if(state==20) {
            /* Native changeRestartProcess owns queued Start/Stop actions.
             * A queued action must not strand our unchanged-map transition
             * in state 20 with no frames or recovery notification remaining. */
            __sync_fetch_and_add(&s->refresh_resumes,1);
            c->liveview_memory_restart(liveview,0);
            state=get(liveview+0x34/4);
        }
        put(&s->refresh_state,state);
        /* Reaching running completes this transition even if a later LUT
         * request changed the goal. Let its next frame request that change,
         * rather than retaining an old pending flag indefinitely. */
        if(state==7&&!get(liveview+0x38/4)) {
            unsigned rs=get(&s->refresh_start_ms);
            if(rs&&__sync_bool_compare_and_swap(&s->refresh_start_ms,rs,0))
                record_elapsed_ms(rs,(void *)0,&s->refresh_total_ms,&s->refresh_max_ms);
            put(&s->refresh_pending,0);
        }
    }
    leave(); return result;
}
#endif

__attribute__((noinline)) unsigned nx_sfr_work_address(void *self,unsigned stream_mode) {
    unsigned lr=(unsigned)__builtin_return_address(0)&~1U;
    enter();
    struct nx_sfr_config *c=&nx_sfr_config;
    if(c->profile==NX_SFR_S2&&(lr==c->work_lr||lr==c->work_execute_lr)&&real_mode()==15&&c->work_base&&c->work_size==NX_SFR_WORK_BYTES) {
        __sync_fetch_and_add(&nx_sfr_runtime.work_managed,1);
        leave(); return c->work_base;
    }
    __sync_fetch_and_add(&nx_sfr_runtime.work_stock,1);
    unsigned (*original)(void *,unsigned)=(void *)c->original[NX_SFR_WORK_ADDRESS];
    unsigned address=original(self,stream_mode);
    leave(); return address;
}
