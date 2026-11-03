/* Standalone host budget/transaction test, never linked into the RAM blob.
 * gcc -std=gnu99 -O2 -Wall -Wextra -Werror -pthread \
 *   -Wno-pointer-to-int-cast -Wno-int-to-pointer-cast \
 *   -DNX_SFR_TEST_SYSCALL -DNX_SFT_TEST_CLOCK \
 *   runtime/smartfilter_probe_test.c runtime/smartfilter_timing.c \
 *   runtime/smartfilter_lut_bridge.c runtime/smartfilter_ipcs.c -o /tmp/sfr-probe-test
 * On 64-bit MinGW add -Wno-attributes -Wl,--image-base,0x400000;
 * on Linux add -no-pie.
 * Low static addresses model the runtime's existing unsigned ARM address ABI.
 * All clocks, image planes and hardware callbacks below are simulated. */
#if !defined(NX_SFR_TEST_SYSCALL) || !defined(NX_SFT_TEST_CLOCK)
#error "Build only as a standalone host test with both test hooks enabled"
#endif
#include <assert.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "smartfilter_ram.c"

static __thread unsigned test_tid=11;
static unsigned ticks,clock_fail,clock_fail_at,wakes,checks;
static unsigned tries,posts,opens,closes,resets,yccs,flushes,native_effects;
static unsigned fail_try,fail_post,fail_open,fail_reset,fail_close,fail_ycc,fail_flush;
static unsigned yield_capture,yield_control,check_pending;
static int effect_rc;
static char order[512];
static unsigned order_n;
static unsigned char switches[NX_SF_ADE_SWITCHES];
static struct {
    unsigned char iy[16],gap0[8],iuv[8],gap1[8],oy[16],gap2[8],ouv[8];
} planes;
#define input_y planes.iy
#define input_uv planes.iuv
#define output_y planes.oy
#define output_uv planes.ouv
static unsigned semaphores[NX_SF_IPCS_SEMAPHORES],image;

int nx_sfr_test_syscall(unsigned n,unsigned a,unsigned b,unsigned c,unsigned d,unsigned e,unsigned f) {
    (void)a; (void)c; (void)d; (void)e; (void)f;
    if(n==224) return (int)test_tid;
    assert(n==240&&b==1); ++wakes; return 0;
}
int nx_sft_test_clock(int *sec,int *nsec) {
    unsigned n=__sync_add_and_fetch(&ticks,1);
    *sec=1; *nsec=(int)(n*100);
    return clock_fail||(clock_fail_at&&n>=clock_fail_at)?-5:0;
}
static void event(char c) { assert(order_n+1<sizeof order); order[order_n++]=c; order[order_n]=0; }
static void hardware(void) {
    assert(nx_sfr_runtime.backend_active==1);
    if(nx_sfr_config.probe_once) {
        struct nx_sfr_probe *p=&nx_sfr_runtime.probe;
        assert(p->state==NX_SFR_PROBE_CLAIMED&&p->slot<NX_SFT_FRAMES);
        assert(nx_sfr_runtime.timing.frames[p->slot].flags&NX_SFT_F_PROBE);
        assert(nx_sfr_runtime.timing.frames[p->slot].state==NX_SFT_PENDING);
    }
}
static int trywait_api(void *p) {
    hardware(); unsigned i=(unsigned *)p-semaphores; assert(i<4);
    ++tries; event((char)('0'+i));
    if(i==3) {
        if(yield_capture) nx_sfr_runtime.bridge.capture_pending=1;
        if(yield_control) nx_sfr_runtime.bridge.paused=1;
    }
    return fail_try==i+1?-7:0;
}
static int post_api(void *p) {
    hardware(); unsigned i=(unsigned *)p-semaphores; assert(i<4);
    ++posts; event((char)('d'-i)); return fail_post==i+1?-8:0;
}
static int open_api(void) { hardware(); ++opens; event('O'); return fail_open?-9:0; }
static int close_api(void) { hardware(); ++closes; event('C'); return fail_close?-10:0; }
static int reset_api(void) { hardware(); ++resets; event('R'); return fail_reset?-11:0; }
static void ade_api(void) { hardware(); event('A'); }
static void lut_api(unsigned c,const unsigned short *table) { hardware(); assert(table&&c<3); event((char)('L'+c)); }
static int ycc_api(void *p) { hardware(); assert(p==&image); ++yccs; event('Y'); return fail_ycc?-12:0; }
static void iq_initialize_api(int n) { hardware(); assert(!n); event('I'); }
static void iq_process_api(int n) { hardware(); assert(!n); event('P'); }
static unsigned width_api(void *p) { assert(p==&image); return 4; }
static unsigned height_api(void *p) { assert(p==&image); return 4; }
static unsigned sampling_api(void *p) { assert(p==&image); return 0; }
static unsigned address_api(void *p,unsigned n) {
    assert(p==&image);
    void *address=n==1?input_y:n==3?input_uv:n==6?output_y:n==8?output_uv:0;
    assert((uintptr_t)address<=UINT32_MAX); return (unsigned)(uintptr_t)address;
}
static int flush_api(void *p,unsigned address,unsigned bytes) {
    assert(p==&image&&address&&bytes); ++flushes; event('F');
    return fail_flush&&flushes==fail_flush?-13:0;
}
static void *instance_api(void) { return &image; }
static void *semaphore_api(void *p,unsigned n) { assert(p==&image&&n<4); return &semaphores[n]; }
static int original_effect(unsigned mode,void *p) { (void)mode; (void)p; ++native_effects; return -99; }
static void original_preview(void *self,void *p,void *data,unsigned mode) {
    (void)self; (void)data; assert(mode==NX_SFR_LIVEVIEW_MODE);
    effect_rc=nx_sfr_effect(1,p);
    if(check_pending) {
        assert(nx_sfr_runtime.probe.state==NX_SFR_PROBE_CLAIMED);
        assert(nx_sfr_runtime.timing.frames[nx_sfr_runtime.probe.slot].state==NX_SFT_PENDING);
        check_pending=0;
    }
}
static void initialize(void) {
    memset(&nx_sfr_config,0,sizeof nx_sfr_config);
    memset(&nx_sfr_runtime,0,sizeof nx_sfr_runtime);
    memset(switches,7,sizeof switches); memset(input_y,0x31,sizeof input_y);
    memset(input_uv,0x42,sizeof input_uv); memset(output_y,0,sizeof output_y); memset(output_uv,0,sizeof output_uv);
    ticks=clock_fail=clock_fail_at=wakes=0; tries=posts=opens=closes=resets=yccs=flushes=native_effects=0;
    fail_try=fail_post=fail_open=fail_reset=fail_close=fail_ycc=fail_flush=0;
    yield_capture=yield_control=check_pending=0; order_n=0; order[0]=0; effect_rc=0; test_tid=11;
    struct nx_sfr_config *c=&nx_sfr_config;
    c->abi=NX_SFR_ABI; c->size=sizeof *c; c->enabled=1; c->generation=7; c->strength=100;
    strcpy(c->look_id,"identity"); c->timing_enabled=1; c->probe_once=1;
    assert((uintptr_t)original_preview<=UINT32_MAX&&(uintptr_t)original_effect<=UINT32_MAX);
    c->original[NX_SFR_PREVIEW]=(unsigned)(uintptr_t)original_preview;
    c->original[NX_SFR_EFFECT]=(unsigned)(uintptr_t)original_effect;
    c->b2y_instance=instance_api; c->get_semaphore=semaphore_api;
    struct nx_sf_ipcs_api *a=&c->api;
    a->trywait=trywait_api; a->post=post_api; a->open=open_api; a->close=close_api; a->reset=reset_api;
    a->ade_init=ade_api; a->write_lut=lut_api; a->ycc=ycc_api;
    a->iq_initialize=iq_initialize_api; a->iq_process=iq_process_api;
    a->width=width_api; a->height=height_api; a->stride=width_api; a->sampling=sampling_api;
    a->address=address_api; a->flush=flush_api; a->rgb3d_index=1;
    for(unsigned i=0;i<NX_SF_ADE_SWITCHES;++i) a->ade_switch[i]=&switches[i];
    struct nx_sfr_probe *p=&nx_sfr_runtime.probe;
    p->state=NX_SFR_PROBE_ARMED; p->slot=~0U; p->window=9; p->generation=7; p->prepare=23; p->finish=22;
    struct nx_sft_timing *t=&nx_sfr_runtime.timing;
    t->schema=NX_SFT_SCHEMA; t->enabled=1; t->armed=1; t->window=9; t->generation=7; t->strength=100;
    assert(!ensure_body(0));
    assert(p->state==NX_SFR_PROBE_ARMED&&p->slot==~0U&&p->prepare==23&&p->finish==22&&t->window==9);
}
static void preview(void) {
    if(nx_sfr_config.profile==NX_SFR_S2) {
        struct nx_sfr_origin *o=origin(test_tid,1); assert(o); o->pending=1;
    }
    nx_sfr_preview(0,&image,0,NX_SFR_LIVEVIEW_MODE);
}
static void no_hardware(void) { assert(!tries&&!posts&&!opens&&!closes&&!resets&&!yccs&&!nx_sfr_runtime.backend_active); }
static unsigned probe_flags(void) {
    unsigned n=0;
    for(unsigned i=0;i<NX_SFT_FRAMES;++i) if(nx_sfr_runtime.timing.frames[i].flags&NX_SFT_F_PROBE) ++n;
    return n;
}
static void returned(unsigned outcome) {
    struct nx_sfr_probe *p=&nx_sfr_runtime.probe;
    assert(p->state==NX_SFR_PROBE_RETURNED&&p->outcome==outcome&&p->slot<NX_SFT_FRAMES);
    assert(nx_sfr_runtime.timing.frames[p->slot].state==NX_SFT_COMMITTED);
    assert(probe_flags()==1&&!nx_sfr_runtime.backend_active&&!native_effects);
    ++checks;
}
static void later_copy(unsigned path) {
    unsigned old_tries=tries,old_posts=posts,old_open=opens,old_ycc=yccs,old_flushes=flushes;
    struct nx_sfr_probe before=nx_sfr_runtime.probe;
    memset(output_y,0,sizeof output_y); memset(output_uv,0,sizeof output_uv);
    unsigned next=nx_sfr_runtime.timing.next_frame;
    preview();
    assert(tries==old_tries&&posts==old_posts&&opens==old_open&&yccs==old_ycc&&flushes==old_flushes+2);
    assert(!memcmp(input_y,output_y,sizeof input_y)&&!memcmp(input_uv,output_uv,sizeof input_uv));
    assert(!memcmp(&before,&nx_sfr_runtime.probe,sizeof before));
    if(next<NX_SFT_FRAMES) assert(nx_sfr_runtime.timing.frames[next].path==path);
    assert(probe_flags()<=1); ++checks;
}
static void blocked(unsigned outcome) {
    no_hardware(); assert(nx_sfr_runtime.probe.state==NX_SFR_PROBE_BLOCKED);
    assert(nx_sfr_runtime.probe.slot==~0U&&nx_sfr_runtime.probe.outcome==outcome&&!probe_flags()); ++checks;
}
static void failures(void) {
    for(unsigned i=1;i<=4;++i) {
        initialize(); fail_try=i; check_pending=1; preview(); returned(NX_SFR_PROBE_BUSY);
        assert(tries==i&&posts==i-1&&!opens&&!effect_rc);
        assert(nx_sfr_runtime.timing.frames[0].stage_rc[NX_SFT_BEGIN]==NX_SF_BUSY);
        later_copy(NX_SFT_PATH_PROBE_EXHAUSTED);
    }
    initialize(); fail_try=3; fail_post=1; preview(); returned(NX_SFR_PROBE_UNLOCK);
    assert(!effect_rc&&nx_sfr_runtime.timing.frames[0].stage_rc[NX_SFT_UNLOCK]==-8);
    later_copy(NX_SFT_PATH_PROBE_EXHAUSTED);
    for(unsigned i=0;i<4;++i) {
        initialize(); if(i==0) fail_open=1; if(i==1) fail_ycc=1; if(i==2) fail_flush=1; if(i==3) fail_flush=3;
        preview(); returned(NX_SFR_PROBE_RENDER); assert(!effect_rc); later_copy(NX_SFT_PATH_PROBE_EXHAUSTED);
    }
    initialize(); fail_reset=1; fail_ycc=1; preview(); returned(NX_SFR_PROBE_RESTORE);
    assert(nx_sfr_runtime.timing.frames[0].stage_rc[NX_SFT_YCC]==-12);
    assert(nx_sfr_runtime.timing.frames[0].stage_rc[NX_SFT_RESET]==-11);
    later_copy(NX_SFT_PATH_PROBE_EXHAUSTED);
    initialize(); fail_close=1; preview(); returned(NX_SFR_PROBE_RESTORE); later_copy(NX_SFT_PATH_PROBE_EXHAUSTED);
    for(unsigned i=1;i<=4;++i) {
        initialize(); fail_post=i; preview(); returned(NX_SFR_PROBE_UNLOCK);
        assert(posts==4); later_copy(NX_SFT_PATH_PROBE_EXHAUSTED);
    }
    initialize(); fail_ycc=fail_reset=1; fail_post=2; preview(); returned(NX_SFR_PROBE_UNLOCK);
    assert(nx_sfr_runtime.timing.frames[0].stage_rc[NX_SFT_YCC]==-12);
    assert(nx_sfr_runtime.timing.frames[0].stage_rc[NX_SFT_RESET]==-11);
    assert(nx_sfr_runtime.timing.frames[0].stage_rc[NX_SFT_UNLOCK]==-8);
}
static void diagnostics(void) {
    for(unsigned i=0;i<6;++i) {
        initialize();
        if(i==0) nx_sfr_config.timing_enabled=0;
        if(i==1) nx_sfr_runtime.timing.enabled=0;
        if(i==2) nx_sfr_runtime.timing.armed=0;
        if(i==3) nx_sfr_runtime.timing.frozen=1;
        if(i==4) nx_sfr_runtime.timing.next_frame=NX_SFT_FRAMES;
        if(i==5) clock_fail=1;
        preview(); blocked(NX_SFR_PROBE_TRACE);
    }
    for(unsigned i=0;i<6;++i) {
        initialize();
        if(i==0) ++nx_sfr_runtime.probe.window;
        if(i==1) ++nx_sfr_runtime.probe.generation;
        if(i==2) ++nx_sfr_runtime.timing.generation;
        if(i==3) nx_sfr_runtime.timing.schema=1;
        if(i==4) nx_sfr_runtime.probe.state=NX_SFR_PROBE_OFF;
        if(i==5) nx_sfr_runtime.probe.state=99;
        preview(); blocked(NX_SFR_PROBE_INVALID);
        later_copy(NX_SFT_PATH_PROBE_BLOCKED);
    }
    initialize(); struct nx_sft_record *r=reserve_trace(NX_SFT_FRAME,0); assert(r);
    nx_sfr_runtime.timing.frozen=1; assert(begin(&nx_sfr_runtime.ipcs)==NX_SF_BUSY);
    blocked(NX_SFR_PROBE_TRACE); commit_trace(r,0);
    initialize(); r=reserve_trace(NX_SFT_FRAME,0); assert(r); r->kind=NX_SFT_INITIALIZATION;
    assert(begin(&nx_sfr_runtime.ipcs)==NX_SF_BUSY); blocked(NX_SFR_PROBE_INVALID); commit_trace(r,0);
    initialize(); r=reserve_trace(NX_SFT_FRAME,0); assert(r); clock_fail=1;
    assert(begin(&nx_sfr_runtime.ipcs)==NX_SF_BUSY); blocked(NX_SFR_PROBE_TRACE); commit_trace(r,0);
    initialize(); clock_fail_at=25; preview(); returned(NX_SFR_PROBE_TRACE);
    for(unsigned i=0;i<9;++i) {
        initialize(); r=reserve_trace(NX_SFT_FRAME,0); assert(r);
        struct nx_sft_record saved=*r;
        if(i==0) r->owner=0;
        if(i==1) r->state=NX_SFT_COMMITTED;
        if(i==2) ++r->window;
        if(i==3) ++r->generation;
        if(i==4) ++r->tid;
        if(i==5) r->start.valid=0;
        if(i==6) r->flags|=NX_SFT_F_CLOCK;
        if(i==7) r->invalid_mask=1U<<NX_SFT_EFFECT;
        if(i==8) r->current_time.valid=0;
        /* Inject an invalid current timestamp directly at the validated gate;
         * begin normally replaces it with its own fresh clock mark first. */
        int rc=i==8?probe_begin(r):begin(&nx_sfr_runtime.ipcs);
        assert(rc==NX_SF_BUSY); blocked(i>=5?NX_SFR_PROBE_TRACE:NX_SFR_PROBE_INVALID);
        *r=saved; commit_trace(r,0);
    }
    initialize(); r=reserve_trace(NX_SFT_FRAME,0); assert(r);
    nx_sfr_runtime.timing.captures[0]=*r;
    nx_sfr_runtime.timing.scopes[0].record=&nx_sfr_runtime.timing.captures[0];
    assert(begin(&nx_sfr_runtime.ipcs)==NX_SF_BUSY); blocked(NX_SFR_PROBE_INVALID);
    nx_sfr_runtime.timing.scopes[0].record=r; commit_trace(r,0);
    /* A repaired diagnostic source cannot revive a BLOCKED budget. */
    initialize(); nx_sfr_runtime.timing.frozen=1; preview(); blocked(NX_SFR_PROBE_TRACE);
    nx_sfr_runtime.timing.frozen=0; later_copy(NX_SFT_PATH_PROBE_BLOCKED);
}
static void arbitration(void) {
    for(unsigned control=0;control<2;++control) {
        initialize(); struct nx_sft_record *r=reserve_trace(NX_SFT_FRAME,0); assert(r);
        if(control) nx_sfr_runtime.control_pause=1; else nx_sfr_runtime.captures=1;
        assert(begin(&nx_sfr_runtime.ipcs)==NX_SF_BUSY); no_hardware();
        assert(nx_sfr_runtime.probe.state==NX_SFR_PROBE_CLAIMED&&probe_flags()==1);
        commit_trace(0,0); assert(nx_sfr_runtime.probe.state==NX_SFR_PROBE_CLAIMED);
        commit_trace(r,0); returned(control?NX_SFR_PROBE_CONTROL:NX_SFR_PROBE_CAPTURE);
        nx_sfr_runtime.control_pause=nx_sfr_runtime.captures=0; later_copy(NX_SFT_PATH_PROBE_EXHAUSTED);
    }
    initialize(); yield_capture=1; preview(); returned(NX_SFR_PROBE_CAPTURE);
    assert(tries==4&&posts==4&&!opens&&!resets&&!yccs);
    nx_sfr_runtime.bridge.capture_pending=0; later_copy(NX_SFT_PATH_PROBE_EXHAUSTED);
    initialize(); yield_control=1; preview(); returned(NX_SFR_PROBE_CONTROL);
    assert(tries==4&&posts==4&&!opens&&!resets&&!yccs);
    later_copy(NX_SFT_PATH_PROBE_EXHAUSTED);
}
/* Exercise the actual production CAS helper with distinct valid pending slots.
 * The real bridge additionally serializes begin with FRAME; this is stricter. */
static unsigned winners;
static void *claimant(void *arg) {
    unsigned i=(unsigned)(uintptr_t)arg; test_tid=100+i;
    struct nx_sft_record *r=&nx_sfr_runtime.timing.frames[i];
    if(!probe_begin(r)) __sync_fetch_and_add(&winners,1);
    return 0;
}
static void concurrent(void) {
    initialize(); pthread_t threads[16]; winners=0;
    nx_sfr_runtime.timing.next_frame=16;
    for(unsigned i=0;i<16;++i) {
        struct nx_sft_record *r=&nx_sfr_runtime.timing.frames[i];
        r->owner=&nx_sfr_runtime.timing; r->kind=NX_SFT_FRAME; r->state=NX_SFT_PENDING;
        r->tid=100+i; r->window=9; r->generation=7; r->start.valid=r->current_time.valid=1;
    }
    for(unsigned i=0;i<16;++i) assert(!pthread_create(&threads[i],0,claimant,(void *)(uintptr_t)i));
    for(unsigned i=0;i<16;++i) assert(!pthread_join(threads[i],0));
    assert(winners==1&&probe_flags()==1&&nx_sfr_runtime.probe.state==NX_SFR_PROBE_CLAIMED);
    no_hardware(); ++checks;
}
int main(void) {
    assert(NX_SFR_ABI==4&&NX_SFT_SCHEMA==2&&NX_SFT_F_PROBE==256);
    assert(NX_SFT_PATH_PROBE_EXHAUSTED==11&&NX_SFT_PATH_PROBE_BLOCKED==12);
    initialize(); check_pending=1; preview(); returned(NX_SFR_PROBE_SUCCESS);
    assert(!strcmp(order,"0123OALMNFFYFFRIPCabcd"));
    assert(tries==4&&posts==4&&opens==1&&closes==1&&resets==1&&yccs==1);
    for(unsigned i=0;i<NX_SF_ADE_SWITCHES;++i) assert(switches[i]==7);
    for(unsigned i=0;i<140;++i) later_copy(NX_SFT_PATH_PROBE_EXHAUSTED);
    failures(); diagnostics(); arbitration();
    for(unsigned i=0;i<20;++i) concurrent();
    initialize(); nx_sfr_config.profile=NX_SFR_S2; check_pending=1; preview(); returned(NX_SFR_PROBE_SUCCESS);
    later_copy(NX_SFT_PATH_PROBE_EXHAUSTED);
    assert(nx_sfr_runtime.s2_frames==2);
    initialize(); nx_sfr_config.profile=NX_SFR_S2; nx_sfr_config.timing_enabled=0;
    preview(); blocked(NX_SFR_PROBE_TRACE);
    nx_sfr_config.timing_enabled=1; later_copy(NX_SFT_PATH_PROBE_BLOCKED);
    assert(!native_effects);
    for(unsigned profile=NX_SFR_S1;profile<=NX_SFR_S2;++profile) {
        for(unsigned timing=0;timing<2;++timing) {
            initialize(); nx_sfr_config.profile=profile; nx_sfr_config.probe_once=0;
            nx_sfr_config.timing_enabled=timing;
            for(unsigned i=0;i<3;++i) preview();
            assert(tries==12&&posts==12&&yccs==3&&nx_sfr_runtime.probe.state==NX_SFR_PROBE_ARMED&&!probe_flags());
            ++checks;
        }
    }
    printf("smartfilter probe host: PASS (%u checks; real RAM/bridge/IPCS/timing, simulated callbacks)\n",checks);
    return 0;
}
