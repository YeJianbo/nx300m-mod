/* Standalone host test, not part of the RAM blob or camera fixture.
 * cc -std=gnu99 -O2 -Wall -Wextra -Werror -pthread -DNX_SFT_TEST_CLOCK \
 *    runtime/smartfilter_timing.c runtime/smartfilter_timing_test.c -o /tmp/sft-test
 * /tmp/sft-test
 * All durations below are simulated clock values, not camera measurements. */
#ifndef NX_SFT_TEST_CLOCK
#error "Build this standalone test with NX_SFT_TEST_CLOCK"
#endif
#include <assert.h>
#include <pthread.h>
#include <stdio.h>
#include <string.h>
#include "smartfilter_timing.h"

static struct nx_sft_timing timing;
static unsigned ticks,calls,mode,claims[NX_SFT_FRAMES];
int nx_sft_test_clock(int *sec,int *nsec) {
    __sync_fetch_and_add(&calls,1);
    unsigned x=__sync_fetch_and_add(&ticks,17);
    *sec=100+(int)(x/1000000000U); *nsec=(int)(x%1000000000U);
    if(mode==1) return -5;
    if(mode==2) *nsec=1000000000;
    if(mode==3) *sec=-1;
    if(mode==4) *nsec=-1;
    return 0;
}
static void initialize(void) {
    memset(&timing,0,sizeof timing); ticks=0; calls=0; mode=0;
    timing.schema=NX_SFT_SCHEMA; timing.enabled=1; timing.armed=1;
    timing.window=5; timing.epoch=7;
}
static struct nx_sft_record *frame(unsigned tid) {
    return nx_sft_reserve(&timing,NX_SFT_FRAME,tid,3,100,0x1234);
}
static void commit(struct nx_sft_record *r) { nx_sft_commit(&timing,r,0,0,0); }
static void *worker(void *arg) {
    unsigned tid=(unsigned)(unsigned long)arg;
    for(unsigned i=0;i<16;++i) {
        struct nx_sft_record *r=frame(tid); assert(r);
        unsigned index=(unsigned)(r-timing.frames); assert(index<NX_SFT_FRAMES);
        assert(__sync_fetch_and_add(&claims[index],1)==0);
        struct nx_sft_mark m=nx_sft_enter_stage(r,NX_SFT_EFFECT);
        assert(nx_sft_current(&timing,tid)==r);
        nx_sft_leave_stage(r,m,0); commit(r);
    }
    return 0;
}
int main(void) {
    initialize(); timing.enabled=0; assert(!frame(11)&&calls==0);
    timing.enabled=1; timing.armed=0; assert(!frame(11)&&calls==0);
    timing.armed=1; timing.frozen=1; assert(!frame(11)&&calls==0);
    initialize(); struct nx_sft_record *r=frame(11); assert(r&&calls==3);
    assert(r->state==NX_SFT_PENDING&&r->flags==NX_SFT_F_FIRST);
    assert(r->window==5&&r->generation==3&&r->strength==100&&r->self==0x1234);
    assert(timing.calibration.state==NX_SFT_COMMITTED);
    assert(timing.calibration.duration[NX_SFT_CALIBRATION].nsec==17);
    assert(timing.calibration.valid_mask==(1U<<NX_SFT_CALIBRATION));
    struct nx_sft_mark a=nx_sft_enter_stage(r,NX_SFT_PREVIEW);
    struct nx_sft_mark b=nx_sft_enter_stage(r,NX_SFT_EFFECT);
    assert(r->current_stage==NX_SFT_EFFECT&&r->current_time.valid);
    unsigned before=calls;
    assert(!frame(11)&&calls==before&&timing.next_frame==1);
    assert(r->flags&NX_SFT_F_NESTED);
    nx_sft_leave_stage(r,b,0);
    assert(r->current_stage==NX_SFT_PREVIEW&&r->current_time.nsec==a.start.nsec);
    nx_sft_leave_stage(r,a,0);
    assert(r->duration[NX_SFT_EFFECT].nsec==17&&r->duration[NX_SFT_PREVIEW].nsec==51);
    assert(!r->count[NX_SFT_YCC]&&!(r->stage_mask&(1U<<NX_SFT_YCC)));
    struct nx_sft_record *cap=nx_sft_reserve(&timing,NX_SFT_CAPTURE_PREPARE,11,3,100,0x4567);
    assert(cap==timing.captures&&nx_sft_current(&timing,11)==r);
    nx_sft_commit(&timing,cap,-7,1,1);
    assert(cap->rc==-7&&cap->fault&&cap->captures_end==1);
    assert(nx_sft_current(&timing,11)==r);
    commit(r); assert(r->state==NX_SFT_COMMITTED&&!nx_sft_current(&timing,11));
    r=frame(11); assert(r&&!(r->flags&NX_SFT_F_FIRST)); commit(r);
    r=nx_sft_reserve(&timing,NX_SFT_INITIALIZATION,12,3,100,0); assert(r); commit(r);
    r=frame(12); assert(r->flags&NX_SFT_F_FIRST); commit(r);
    timing.epoch=8; r=frame(11); assert(r->flags&NX_SFT_F_FIRST);
    timing.epoch=9; commit(r);
    assert(r->epoch_begin==8&&r->epoch_end==9&&(r->flags&NX_SFT_F_EPOCH));

    initialize();
    for(unsigned i=0;i<NX_SFT_FRAMES;++i) { r=frame(1); assert(r==timing.frames+i); commit(r); }
    before=calls; assert(!frame(1)&&calls==before&&timing.dropped==1);
    for(unsigned i=0;i<NX_SFT_CAPTURES;++i) {
        cap=nx_sft_reserve(&timing,NX_SFT_CAPTURE_FINISH,1,3,100,0);
        assert(cap==timing.captures+i); commit(cap);
    }
    before=calls;
    assert(!nx_sft_reserve(&timing,NX_SFT_CAPTURE_FINISH,1,3,100,0));
    assert(calls==before&&timing.dropped==2);
    for(unsigned bad=1;bad<=4;++bad) {
        initialize(); mode=bad; r=frame(1); a=nx_sft_enter_stage(r,NX_SFT_YCC);
        nx_sft_leave_stage(r,a,0);
        assert((r->invalid_mask&(1U<<NX_SFT_YCC))&&!(r->valid_mask&(1U<<NX_SFT_YCC)));
        assert(r->count[NX_SFT_YCC]==1&&(r->flags&NX_SFT_F_CLOCK)&&timing.clock_errors>=5);
        commit(r); assert(!r->end.valid);
    }
    initialize(); r=frame(1); a=nx_sft_enter_stage(r,NX_SFT_YCC);
    ticks=a.start.nsec-17; nx_sft_leave_stage(r,a,0);
    assert(r->invalid_mask&(1U<<NX_SFT_YCC));
    assert((r->flags&NX_SFT_F_CLOCK)&&timing.clock_errors==1); commit(r);
    initialize(); r=frame(1); ticks=1000000000U-10;
    a=nx_sft_enter_stage(r,NX_SFT_YCC); nx_sft_leave_stage(r,a,0);
    assert(r->duration[NX_SFT_YCC].sec==0&&r->duration[NX_SFT_YCC].nsec==17); commit(r);
    initialize(); r=frame(1); a=nx_sft_enter_stage(r,NX_SFT_UNLOCK);
    nx_sft_leave_stage(r,a,-44); b=nx_sft_enter_stage(r,NX_SFT_UNLOCK);
    nx_sft_leave_stage(r,b,0);
    assert(r->count[NX_SFT_UNLOCK]==2&&r->stage_rc[NX_SFT_UNLOCK]==-44);
    assert(r->duration[NX_SFT_UNLOCK].nsec==34); commit(r);

    initialize();
    for(unsigned i=1;i<=NX_SFT_SCOPES;++i) { r=frame(i); assert(r); commit(r); }
    before=calls; assert(!frame(NX_SFT_SCOPES+1)&&calls==before&&timing.dropped==1);
    initialize(); pthread_t threads[8]; memset(claims,0,sizeof claims);
    for(unsigned i=0;i<8;++i) assert(!pthread_create(&threads[i],0,worker,(void *)(unsigned long)(i+1)));
    for(unsigned i=0;i<8;++i) assert(!pthread_join(threads[i],0));
    for(unsigned i=0;i<NX_SFT_FRAMES;++i)
        assert(claims[i]==1&&timing.frames[i].state==NX_SFT_COMMITTED);
    assert(timing.next_frame==128&&timing.dropped==0&&timing.clock_errors==0);
    puts("timing: admission, calibration, pending nesting, capture independence, epoch, capacities,");
    puts("clock failures/invalid/negative/rollover, unlock aggregation, scope exhaustion,");
    puts("and 8-thread unique tickets passed (simulated clock, not camera measurements)");
    return 0;
}
