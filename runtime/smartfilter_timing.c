/* Bounded, append-only diagnostic RAM. Never participates in hardware admission. */
#include "smartfilter_timing.h"

static unsigned get(const volatile unsigned *p) { return __atomic_load_n(p,__ATOMIC_ACQUIRE); }
static void clear(void *p,unsigned n) { unsigned char *v=p; while(n--) *v++=0; }
static void clock_error(struct nx_sft_record *r) {
    r->flags|=NX_SFT_F_CLOCK;
    __sync_fetch_and_add(&r->owner->clock_errors,1);
}
#ifdef NX_SFT_TEST_CLOCK
/* Standalone test build only; no production/factory binding or ABI change. */
extern int nx_sft_test_clock(int *,int *);
#endif
static struct nx_sft_time now(struct nx_sft_record *r) {
    struct { int sec,nsec; } ts={0,0};
    struct nx_sft_time t={0,0,0};
#ifdef NX_SFT_TEST_CLOCK
    int rc=nx_sft_test_clock(&ts.sec,&ts.nsec);
#else
    register unsigned r0 __asm__("r0")=1U; /* CLOCK_MONOTONIC */
    register unsigned r1 __asm__("r1")=(unsigned)&ts;
    register unsigned r7 __asm__("r7")=263U; /* ARM EABI clock_gettime32 */
    __asm__ volatile("svc 0" : "+r"(r0) : "r"(r1),"r"(r7) : "memory","cc");
    int rc=(int)r0;
#endif
    if(rc||ts.sec<0||ts.nsec<0||ts.nsec>=1000000000) {
        clock_error(r); return t;
    }
    t.sec=(unsigned)ts.sec; t.nsec=(unsigned)ts.nsec; t.valid=1;
    return t;
}
static int difference(struct nx_sft_record *r,struct nx_sft_time a,
                      struct nx_sft_time b,struct nx_sft_duration *d) {
    if(!a.valid||!b.valid) return 0;
    if(b.sec<a.sec||(b.sec==a.sec&&b.nsec<a.nsec)) { clock_error(r); return 0; }
    d->sec=b.sec-a.sec;
    if(b.nsec<a.nsec) { --d->sec; d->nsec=1000000000U-a.nsec+b.nsec; }
    else d->nsec=b.nsec-a.nsec;
    return 1;
}
static void initialize(struct nx_sft_timing *t,struct nx_sft_record *r,unsigned kind,
                       unsigned tid,unsigned generation,unsigned strength,unsigned self) {
    clear(r,sizeof *r);
    r->owner=t; r->kind=kind; r->tid=tid; r->window=t->window;
    r->generation=generation; r->strength=strength; r->self=self;
    r->current_stage=NX_SFT_NO_STAGE; r->epoch_begin=get(&t->epoch);
    __atomic_store_n(&r->state,NX_SFT_PENDING,__ATOMIC_RELEASE);
    r->start=now(r);
}
struct nx_sft_record *nx_sft_current(struct nx_sft_timing *t,unsigned tid) {
    if(!tid) return (void *)0;
    for(unsigned i=0;i<NX_SFT_SCOPES;++i)
        if(get(&t->scopes[i].tid)==tid) return t->scopes[i].record;
    return (void *)0;
}
static struct nx_sft_scope *scope(struct nx_sft_timing *t,unsigned tid) {
    for(unsigned i=0;i<NX_SFT_SCOPES;++i)
        if(get(&t->scopes[i].tid)==tid) return &t->scopes[i];
    for(unsigned i=0;i<NX_SFT_SCOPES;++i)
        if(__sync_bool_compare_and_swap(&t->scopes[i].tid,0,tid)) return &t->scopes[i];
    return (void *)0;
}
static void calibrate(struct nx_sft_timing *t,unsigned tid) {
    struct nx_sft_record *r=&t->calibration;
    if(!__sync_bool_compare_and_swap(&r->state,NX_SFT_EMPTY,NX_SFT_PENDING)) return;
    /* Only this successful claimant writes calibration. The two stage reads
     * are consecutive; their walltime is reported, never subtracted from frames. */
    r->owner=t; r->kind=NX_SFT_CLOCK_CALIBRATION; r->tid=tid;
    r->window=t->window; r->generation=t->generation; r->strength=t->strength;
    r->epoch_begin=get(&t->epoch); r->current_stage=NX_SFT_NO_STAGE;
    struct nx_sft_mark m=nx_sft_enter_stage(r,NX_SFT_CALIBRATION);
    r->start=m.start;
    nx_sft_leave_stage(r,m,0);
    /* The stage endpoint is also the record endpoint; no extra clock syscall. */
    if((r->valid_mask&(1U<<NX_SFT_CALIBRATION))&&r->start.valid) {
        r->end.sec=r->start.sec+r->duration[NX_SFT_CALIBRATION].sec;
        r->end.nsec=r->start.nsec+r->duration[NX_SFT_CALIBRATION].nsec;
        if(r->end.nsec>=1000000000U) { ++r->end.sec; r->end.nsec-=1000000000U; }
        r->end.valid=1;
    }
    r->epoch_end=get(&t->epoch);
    if(r->epoch_begin!=r->epoch_end) r->flags|=NX_SFT_F_EPOCH;
    __atomic_store_n(&r->state,NX_SFT_COMMITTED,__ATOMIC_RELEASE);
}
struct nx_sft_record *nx_sft_reserve(struct nx_sft_timing *t,unsigned kind,unsigned tid,
                                   unsigned generation,unsigned strength,unsigned self) {
    if(!get(&t->enabled)||!get(&t->armed)||get(&t->frozen)||!tid) return (void *)0;
    struct nx_sft_scope *s=(void *)0;
    int frame=kind==NX_SFT_FRAME||kind==NX_SFT_INITIALIZATION;
    if(frame) {
        s=scope(t,tid);
        if(s&&s->record) { s->record->flags|=NX_SFT_F_NESTED; return (void *)0; }
        if(!s) { __sync_fetch_and_add(&t->dropped,1); return (void *)0; }
    }
    volatile unsigned *next=frame?&t->next_frame:&t->next_capture;
    unsigned capacity=frame?NX_SFT_FRAMES:NX_SFT_CAPTURES;
    /* Precheck bounds the ticket even after a window has been full for years.
     * Racing final claimants may increase it slightly beyond capacity, never
     * reuse a slot. Full frame storage does not consume capture storage. */
    if(get(next)>=capacity) { __sync_fetch_and_add(&t->dropped,1); return (void *)0; }
    unsigned ticket=__sync_fetch_and_add(next,1);
    if(ticket>=capacity) { __sync_fetch_and_add(&t->dropped,1); return (void *)0; }
    calibrate(t,tid);
    struct nx_sft_record *r=frame?&t->frames[ticket]:&t->captures[ticket];
    initialize(t,r,kind,tid,generation,strength,self);
    if(s) {
        if(!s->seen||s->last_epoch!=r->epoch_begin) r->flags|=NX_SFT_F_FIRST;
        /* An initialization-only record must not consume the first actual
         * frame marker for this thread after a control transaction. */
        if(kind==NX_SFT_FRAME) { s->last_epoch=r->epoch_begin; s->seen=1; }
        s->record=r;
    }
    return r;
}
struct nx_sft_mark nx_sft_enter_stage(struct nx_sft_record *r,unsigned stage) {
    struct nx_sft_mark m={{0,0,0},{0,0,0},NX_SFT_NO_STAGE,NX_SFT_NO_STAGE};
    if(!r||stage>=NX_SFT_STAGES) return m;
    m.stage=stage; m.previous_stage=r->current_stage; m.previous_time=r->current_time;
    m.start=now(r); r->current_stage=stage; r->current_time=m.start;
    r->stage_mask|=1U<<stage;
    return m;
}
void nx_sft_leave_stage(struct nx_sft_record *r,struct nx_sft_mark m,int rc) {
    if(!r||m.stage>=NX_SFT_STAGES) return;
    struct nx_sft_time finish=now(r);
    struct nx_sft_duration d;
    unsigned bit=1U<<m.stage;
    ++r->count[m.stage];
    /* Preserve any failed unlock/flush when later aggregated calls succeed. */
    if(rc||!r->stage_rc[m.stage]) r->stage_rc[m.stage]=rc;
    if(!difference(r,m.start,finish,&d)) {
        r->invalid_mask|=bit; r->valid_mask&=~bit;
    } else if(!(r->invalid_mask&bit)) {
        struct nx_sft_duration *total=&r->duration[m.stage];
        unsigned nsec=total->nsec+d.nsec,carry=nsec>=1000000000U;
        unsigned sec=total->sec+d.sec;
        if(sec<total->sec||(carry&&sec==~0U)) {
            clock_error(r); r->invalid_mask|=bit; r->valid_mask&=~bit;
        } else {
            total->sec=sec+carry; total->nsec=carry?nsec-1000000000U:nsec;
            r->valid_mask|=bit;
        }
    }
    r->current_stage=m.previous_stage; r->current_time=m.previous_time;
}
void nx_sft_commit(struct nx_sft_timing *t,struct nx_sft_record *r,int rc,
                   unsigned fault,unsigned captures) {
    if(!r) return;
    r->end=now(r); r->epoch_end=get(&t->epoch); r->rc=rc;
    r->fault|=fault; r->captures_end=captures;
    if(r->epoch_begin!=r->epoch_end) r->flags|=NX_SFT_F_EPOCH;
    struct nx_sft_duration elapsed;
    (void)difference(r,r->start,r->end,&elapsed);
    if(r->kind==NX_SFT_FRAME||r->kind==NX_SFT_INITIALIZATION) {
        for(unsigned i=0;i<NX_SFT_SCOPES;++i)
            if(get(&t->scopes[i].tid)==r->tid&&t->scopes[i].record==r) {
                t->scopes[i].record=(void *)0; break;
            }
    }
    r->current_stage=NX_SFT_NO_STAGE;
    __atomic_store_n(&r->state,NX_SFT_COMMITTED,__ATOMIC_RELEASE);
}
