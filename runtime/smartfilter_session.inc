/* ABI5 experiment only. Kept outside the ABI4 image and installation state. */
static unsigned session_tid(void) { return tid((void *)0); }
static int session_clock(unsigned id,struct nx_ipct_time *p) {
    return syscall6(263,id,(unsigned)p,0,0,0,0);
}
static unsigned session_ms(void) {
    struct nx_ipct_time t={0,0};
    if(session_clock(1,&t)) return 0;
    return t.sec*1000U+t.nsec/1000000U;
}
static int session_wait(volatile unsigned *p,unsigned value,unsigned ms) {
    struct nx_ipct_time timeout={ms/1000U,(ms%1000U)*1000000U};
    /* Park outside RAM text; the loader retains the published return address. */
    return nx_sfr_config.wait_syscall(240,(unsigned)p,0,value,ms?(unsigned)&timeout:0,0,0);
}
static void session_wake(volatile unsigned *p) {
    syscall6(240,(unsigned)p,1,0x7fffffffU,0,0,0);
}
static struct nx_ipct_api trace_api(void) {
    struct nx_ipct_api a; a.tid=session_tid; a.clock=session_clock; return a;
}
static int diagnostic_open(unsigned role) {
    struct nx_sfr_runtime *s=&nx_sfr_runtime;
    struct nx_sfr_config *c=&nx_sfr_config;
    struct nx_ipct_api a=trace_api();
    nx_ipct_start(&s->open_trace,&a,role,s->ipcs.transactions,
                  c->udd_count?get(c->udd_count):~0U);
    int rc=c->api.open();
    nx_ipct_finish(&s->open_trace,&a,rc,c->udd_count?get(c->udd_count):~0U);
    return rc;
}
static int preview_open(void) { return diagnostic_open(NX_IPCT_PREVIEW); }
static int session_open(void) { return diagnostic_open(NX_IPCT_SESSION); }
static int session_close(void) { return nx_sfr_config.api.close(); }
static void *session_owner(void *unused) {
    (void)unused; nx_ips_owner(&nx_sfr_runtime.session); return (void *)0;
}
static int session_initialize(void) {
    struct nx_sfr_config *c=&nx_sfr_config;
    struct nx_sfr_runtime *s=&nx_sfr_runtime;
    if(get(&s->session_initialized)==2) return 0;
    if(!__sync_bool_compare_and_swap(&s->session_initialized,0,1)) return NX_SF_BUSY;
    struct nx_ips_api a;
    a.locks[0]=c->get_lifecycle(c->b2y_instance());
    for(unsigned i=0;i<NX_SF_IPCS_SEMAPHORES;++i) a.locks[i+1]=s->ipcs.api.semaphore[i];
    if(!a.locks[0]) { put(&s->session_initialized,3); return NX_SF_INVALID; }
    a.trywait=c->api.trywait; a.post=c->api.post; a.open=session_open; a.close=session_close;
    a.tid=session_tid; a.ms=session_ms; a.wait=session_wait; a.wake=session_wake;
    nx_ips_init(&s->session,&a);
    s->ipcs.api.open=preview_open;
    put(&s->session_initialized,2); return 0;
}
static int session_start(void) {
    struct nx_sfr_config *c=&nx_sfr_config;
    struct nx_sfr_runtime *s=&nx_sfr_runtime;
    if(get(&s->session_started)==2) return 0;
    if(!__sync_bool_compare_and_swap(&s->session_started,0,1)) return NX_SF_BUSY;
    /* Storage exceeds the verified stock pthread_attr_t size. The original
     * attr functions own its representation; no pthread layout is fabricated. */
    unsigned attr[16];
    int rc=c->attr_init(attr);
    if(!rc) {
        rc=c->attr_stack(attr,65536);
        if(!rc) rc=c->thread_create(&s->owner_handle,attr,session_owner,(void *)0);
        int destroy=c->attr_destroy(attr); if(!rc&&destroy) rc=destroy;
    }
    put(&s->session_started,rc?3:2);
    if(rc) { nx_ips_fault(&s->session,-120); return NX_SF_BUSY; }
    return 0;
}
static int session_frame_begin(void) {
    struct nx_sfr_runtime *s=&nx_sfr_runtime;
    if(!nx_sfr_config.session_mode) return 0;
    /* Reject incoming preview frames locklessly while stopping liveview or
     * holding a capture lease so drain does not contend on s->session.mutex. */
    if(get(&s->captures)||get(&s->b2y_leases)||get(&s->stream_stopped)==1) return NX_SF_BUSY;
    if(get(&s->session_initialized)!=2||session_start()) return NX_SF_BUSY;
    if(nx_ips_frame_enter(&s->session)) return NX_SF_BUSY;
    /* Native lifecycle precedes all channel locks. No owner lock survives a
     * frame; this is the frame's own short nonblocking lease. */
    if(s->session.api.trywait(s->session.api.locks[0])) {
        nx_ips_frame_leave(&s->session); return NX_SF_BUSY;
    }
    s->frame_lifecycle_held=1; return 0;
}
static void session_frame_end(void) {
    struct nx_sfr_runtime *s=&nx_sfr_runtime;
    if(!nx_sfr_config.session_mode||!s->frame_lifecycle_held) return;
    s->frame_lifecycle_held=0;
    if(s->session.api.post(s->session.api.locks[0])) nx_ips_fault(&s->session,-121);
    if(get(&s->bridge.fault)) nx_ips_fault(&s->session,-122);
    nx_ips_frame_leave(&s->session);
}
static struct nx_sfr_capture_owner *capture_owner(int create) {
    struct nx_sfr_runtime *s=&nx_sfr_runtime; unsigned id=session_tid();
    for(unsigned i=0;i<64;++i) if(get(&s->capture_owners[i].tid)==id) return &s->capture_owners[i];
    if(create) for(unsigned i=0;i<64;++i)
        if(__sync_bool_compare_and_swap(&s->capture_owners[i].tid,0,id)) return &s->capture_owners[i];
    return (void *)0;
}
static void capture_owner_free(struct nx_sfr_capture_owner *o) {
    if(o&&!o->native&&!o->skipped) put(&o->tid,0);
}
/* Record call count, cumulative ms and max ms once per stop/B2Y/start/refresh
 * boundary without touching per-frame fast paths. */
static void record_elapsed_ms(unsigned start,unsigned *calls,unsigned *total_ms,unsigned *max_ms) {
    if(calls) __sync_fetch_and_add(calls,1);
    unsigned ms=session_ms()-start;
    if(ms) {
        __sync_fetch_and_add(total_ms,ms);
        unsigned prev;
        while(ms>(prev=__atomic_load_n(max_ms,__ATOMIC_ACQUIRE))&&
              !__sync_bool_compare_and_swap(max_ms,prev,ms)) {}
    }
}
/* Record drain frequency, active waits and millisecond latency once per
 * stop/B2Y boundary so device status/diag-export exposes real contention cost. */
static int drain_observed(struct nx_sfr_runtime *s,unsigned *calls,unsigned *waits,
                          unsigned *total_ms,unsigned *max_ms) {
    struct nx_ips *p=&s->session;
    unsigned st=__atomic_load_n(&p->state,__ATOMIC_ACQUIRE);
    int might_wait=__atomic_load_n(&p->held,__ATOMIC_ACQUIRE)||
        __atomic_load_n(&p->active,__ATOMIC_ACQUIRE)||
        st==NX_IPS_OPENING||st==NX_IPS_DRAINING;
    if(might_wait) __sync_fetch_and_add(waits,1);
    unsigned start=session_ms();
    int rc=nx_ips_drain(p,750);
    record_elapsed_ms(start,calls,total_ms,max_ms);
    return rc;
}
int nx_sfr_b2y_open(void *self) {
    enter(); struct nx_sfr_runtime *s=&nx_sfr_runtime;
    int rc=0;
    unsigned lease_start=0;
    if(nx_sfr_config.session_mode&&get(&s->session_initialized)==2) {
        struct nx_sfr_capture_owner *o=capture_owner(1);
        if(!o) {
            put(&s->capture_owner_overflow,1); nx_ips_fault(&s->session,-124);
            leave(); return -12;
        }
        if(o->native) { ++o->skipped; leave(); return -16; }
        /* This is BEFORE the original takes +0x44. Prepare is too late. */
        nx_ips_hold(&s->session,NX_IPS_CAPTURE);
        rc=drain_observed(s,&s->b2y_drain_calls,&s->b2y_drain_waits,&s->b2y_drain_total_ms,&s->b2y_drain_max_ms);
        if(rc&&rc!=NX_IPS_TIMEOUT) {
            ++o->skipped; nx_ips_release(&s->session,NX_IPS_CAPTURE); leave(); return rc;
        }
        /* Stock callers do not test Open's return before Prepare/Operate.
         * A slow preview is not a failed native Open: keep CAPTURE held and
         * let the original +0x44 wait serialize with the complete frame.
         * The owner may retain its power reference until native Close, but
         * cannot admit another frame or close it across the native lease. */
        lease_start=session_ms();
        o->opened_ms=lease_start;
        ++o->native;
        __sync_fetch_and_add(&s->b2y_leases,1);
    } else lease_start=session_ms();
    int (*original)(void *)=(void *)nx_sfr_config.original[NX_SFR_B2Y_OPEN];
    rc=original(self);
    record_elapsed_ms(lease_start,&s->b2y_open_calls,&s->b2y_open_total_ms,&s->b2y_open_max_ms);
    leave(); return rc;
}
int nx_sfr_b2y_close(void *self) {
    enter();
    struct nx_sfr_capture_owner *o=capture_owner(0);
    if(o&&o->skipped) { --o->skipped; capture_owner_free(o); leave(); return 0; }
    /* A rejected Open never acquired +0x44. Its paired Close must not post it.
     * On bookkeeping exhaustion admit no unknown Close and keep the fault. */
    if(!o&&get(&nx_sfr_runtime.capture_owner_overflow)) { leave(); return -12; }
    int (*original)(void *)=(void *)nx_sfr_config.original[NX_SFR_B2Y_CLOSE];
    int rc=original(self); struct nx_sfr_runtime *s=&nx_sfr_runtime;
    if(o&&o->native) {
        record_elapsed_ms(o->opened_ms,&s->b2y_lease_calls,&s->b2y_lease_total_ms,&s->b2y_lease_max_ms);
        --o->native; __sync_fetch_and_sub(&s->b2y_leases,1);
        if(!get(&s->b2y_leases)&&!get(&s->captures)) {
            unsigned t=session_ms();
            put(&s->capture_hold_ms,t?t:1U);
        }
        nx_ips_release(&s->session,NX_IPS_CAPTURE); capture_owner_free(o);
    }
    leave(); return rc;
}
static void cancel_refresh(void) {
    /* Native stop/start supersedes our optional topology rebuild. In
     * particular a playback/power transition must not inherit a request to
     * resume a previous liveview's state-20 memory wait. */
    put(&nx_sfr_runtime.refresh_pending,0);
    put(&nx_sfr_runtime.refresh_queued,0);
    put(&nx_sfr_runtime.refresh_attempt,0);
    put(&nx_sfr_runtime.capture_hold_ms,0);
}
/* stream_stopped is an admission phase: 1 stopped, 2 accepted Start awaiting
 * native running, 3 inside Start. A later Stop writes 1 and cancels the pending
 * acknowledgement; a successful Start return alone is not frame ownership. */
static void session_resume_stream(void) {
    struct nx_sfr_runtime *s=&nx_sfr_runtime;
    if(get(&s->stream_stopped)!=2) return;
    unsigned *liveview=nx_sfr_config.liveview_instance();
    if(get(liveview+0x34/4)!=7||get(liveview+0x38/4)) return;
    if(__sync_bool_compare_and_swap(&s->stream_stopped,2,0)&&get(&s->session_initialized)==2)
        nx_ips_release(&s->session,NX_IPS_STREAM);
}
int nx_sfr_stop(void *self,unsigned state,unsigned flag) {
    enter(); struct nx_sfr_runtime *s=&nx_sfr_runtime;
    cancel_refresh();
    unsigned was_stopped=__atomic_exchange_n(&s->stream_stopped,1,__ATOMIC_ACQ_REL);
    if(get(&s->session_initialized)==2&&!was_stopped)
        nx_ips_hold(&s->session,NX_IPS_STREAM);
    if(get(&s->session_initialized)==2) {
        int rc=drain_observed(s,&s->stop_drain_calls,&s->stop_drain_waits,&s->stop_drain_total_ms,&s->stop_drain_max_ms);
        if(rc) {
            /* BringDownNode/videoStreamOff may retire buffers still used by
             * the in-flight worker. Keep admission stopped and do not enter
             * native teardown while release is unconfirmed. Stock callers
             * ignore stopProcess's result; this is not a successful stop. */
            if(!__atomic_load_n(&s->session.error,__ATOMIC_ACQUIRE)) nx_ips_fault(&s->session,rc);
            leave(); return rc;
        }
    }
    int (*original)(void *,unsigned,unsigned)=(void *)nx_sfr_config.original[NX_SFR_STOP];
    unsigned stop_start=session_ms();
    int rc=original(self,state,flag);
    record_elapsed_ms(stop_start,&s->stop_native_calls,&s->stop_native_total_ms,&s->stop_native_max_ms);
    leave(); return rc;
}
int nx_sfr_start(void *self,int mode,unsigned rate) {
    enter();
    struct nx_sfr_runtime *s=&nx_sfr_runtime;
    /* Start can queue behind our already-dispatched changeProcess. Its stop
     * callback still needs the pending marker to leave native memory wait. */
    if(!get(&s->refresh_pending)) cancel_refresh();
    unsigned stopped=get(&s->stream_stopped);
    int armed=(stopped==1||stopped==2)&&__sync_bool_compare_and_swap(&s->stream_stopped,stopped,3);
    int (*original)(void *,int,unsigned)=(void *)nx_sfr_config.original[NX_SFR_START];
    unsigned start_begin=session_ms();
    int rc=original(self,mode,rate);
    record_elapsed_ms(start_begin,&s->start_calls,&s->start_total_ms,&s->start_max_ms);
    if(armed) __sync_bool_compare_and_swap(&s->stream_stopped,3,rc?2:stopped);
    /* startLiveviewProcess accepts queued starts during native state 8..11.
     * Its stop callback later runs startProcess directly, without another
     * Start call. Keep the gate until that transition actually reaches 7. */
    session_resume_stream();
    /* No reference is acquired here: an actual subsequent frame requests it. */
    leave(); return rc;
}
int nx_sfr_udd_open(const char *path,int flags,unsigned index,int *fd) {
    struct nx_ipct_api a=trace_api();
    struct nx_ipct_mark m=nx_ipct_enter(&nx_sfr_runtime.open_trace,&a,NX_IPCT_UDD,index);
    int (*original)(const char *,int,unsigned,int *)=(void *)nx_sfr_config.original[NX_SFR_UDD_OPEN];
    int rc=original(path,flags,index,fd); nx_ipct_leave(&a,m,rc); return rc;
}
int nx_sfr_mutex(void *mutex) {
    struct nx_ipct_api a=trace_api();
    unsigned phase=mutex==nx_sfr_config.udd_mutex_outer?NX_IPCT_MUTEX_OUTER:
                   mutex==nx_sfr_config.udd_mutex_device?NX_IPCT_MUTEX_UDD:NX_IPCT_PHASES;
    struct nx_ipct_mark m=nx_ipct_enter(&nx_sfr_runtime.open_trace,&a,phase,(unsigned)mutex);
    int (*original)(void *)=(void *)nx_sfr_config.original[NX_SFR_MUTEX];
    int rc=original(mutex); nx_ipct_leave(&a,m,rc); return rc;
}
int nx_sfr_device_open(const char *path,int flags,unsigned mode) {
    struct nx_ipct_api a=trace_api();
    struct nx_ipct_mark m=nx_ipct_enter(&nx_sfr_runtime.open_trace,&a,NX_IPCT_DEVICE_OPEN,(unsigned)flags);
    int (*original)(const char *,int,unsigned)=(void *)nx_sfr_config.original[NX_SFR_DEVICE_OPEN];
    int rc=original(path,flags,mode); nx_ipct_leave(&a,m,rc); return rc;
}
int nx_sfr_regmap(int fd) {
    struct nx_ipct_api a=trace_api();
    struct nx_ipct_mark m=nx_ipct_enter(&nx_sfr_runtime.open_trace,&a,NX_IPCT_REGMAP,(unsigned)fd);
    int (*original)(int)=(void *)nx_sfr_config.original[NX_SFR_REGMAP];
    int rc=original(fd); nx_ipct_leave(&a,m,rc); return rc;
}
int nx_sfr_irq(void) {
    struct nx_ipct_api a=trace_api();
    struct nx_ipct_mark m=nx_ipct_enter(&nx_sfr_runtime.open_trace,&a,NX_IPCT_IRQ,0);
    int (*original)(void)=(void *)nx_sfr_config.original[NX_SFR_IRQ];
    int rc=original(); nx_ipct_leave(&a,m,rc); return rc;
}
int nx_sfr_init(void) {
    struct nx_ipct_api a=trace_api();
    struct nx_ipct_mark m=nx_ipct_enter(&nx_sfr_runtime.open_trace,&a,NX_IPCT_INIT,0);
    int (*original)(void)=(void *)nx_sfr_config.original[NX_SFR_INIT];
    int rc=original(); nx_ipct_leave(&a,m,rc); return rc;
}
