#include "ipcs_session.h"

#define NX_IPS_IDLE_MS 250U
static void lock(struct nx_ips *s) {
    while(__atomic_exchange_n(&s->mutex,1,__ATOMIC_ACQUIRE))
        s->api.wait(&s->mutex,1,1000);
}
static void unlock(struct nx_ips *s) {
    __atomic_store_n(&s->mutex,0,__ATOMIC_RELEASE); s->api.wake(&s->mutex);
}
static void changed(struct nx_ips *s) { ++s->event; s->api.wake(&s->event); }
static unsigned blocked(struct nx_ips *s) {
    unsigned n=__atomic_load_n(&s->external_stop,__ATOMIC_ACQUIRE);
    for(unsigned i=0;i<NX_IPS_REASONS;++i) n|=s->blockers[i];
    return n;
}
static int release_locks(struct nx_ips *s,unsigned held) {
    int error=0;
    while(held) if(s->api.post(s->api.locks[--held])) error=NX_IPS_UNCERTAIN;
    return error;
}
static int acquire_locks(struct nx_ips *s) {
    for(unsigned i=0;i<NX_IPS_LOCKS;++i) if(s->api.trywait(s->api.locks[i])) {
        int rc=release_locks(s,i);
        return rc?rc:NX_IPS_BUSY;
    }
    return 0;
}
void nx_ips_init(struct nx_ips *s,const struct nx_ips_api *a) {
    unsigned char *p=(void *)s;
    for(unsigned i=0;i<sizeof *s;++i) p[i]=0;
    s->api=*a;
}
void nx_ips_hold(struct nx_ips *s,unsigned reason) {
    if(reason>=NX_IPS_REASONS) return;
    lock(s); ++s->blockers[reason]; s->requested=0; changed(s); unlock(s);
}
void nx_ips_release(struct nx_ips *s,unsigned reason) {
    if(reason>=NX_IPS_REASONS) return;
    lock(s); if(s->blockers[reason]) --s->blockers[reason]; changed(s); unlock(s);
}
void nx_ips_fault(struct nx_ips *s,int error) {
    lock(s); s->error=error; s->requested=0; ++s->blockers[NX_IPS_RECOVERY]; changed(s); unlock(s);
}
int nx_ips_frame_enter(struct nx_ips *s) {
    lock(s);
    if(blocked(s)||s->error||!s->running) { ++s->busy; unlock(s); return NX_IPS_BUSY; }
    s->requested=1; s->last_frame_ms=s->api.ms();
    if(s->state!=NX_IPS_READY||s->held!=1) {
        ++s->busy; changed(s); unlock(s); return NX_IPS_BUSY;
    }
    ++s->active; ++s->frames; changed(s); unlock(s); return 0;
}
void nx_ips_frame_leave(struct nx_ips *s) {
    lock(s);
    if(s->active) {
        --s->active;
        /* An expensive frame is not an idle gap. Start the idle lease after
         * its native Close/Reset has completed, rather than at admission. */
        if(s->requested&&!s->error) s->last_frame_ms=s->api.ms();
    }
    else { s->error=NX_IPS_UNCERTAIN; ++s->blockers[NX_IPS_RECOVERY]; }
    changed(s); unlock(s);
}
int nx_ips_drain(struct nx_ips *s,unsigned timeout_ms) {
    unsigned start=s->api.ms();
    for(;;) {
        lock(s);
        unsigned event=s->event;
        if(s->state==NX_IPS_FAILED||s->held==2) { unlock(s); return NX_IPS_UNCERTAIN; }
        if(!s->active&&!s->held&&s->state!=NX_IPS_OPENING&&s->state!=NX_IPS_DRAINING) {
            unlock(s); return 0;
        }
        unsigned elapsed=s->api.ms()-start;
        if(elapsed>=timeout_ms) {
            ++s->drain_timeouts; unlock(s); return NX_IPS_TIMEOUT;
        }
        unlock(s);
        s->api.wait(&s->event,event,timeout_ms-elapsed);
    }
}
void nx_ips_owner(struct nx_ips *s) {
    lock(s);
    if(s->running) { unlock(s); return; }
    s->owner_tid=s->api.tid(); s->running=1; changed(s); unlock(s);
    for(;;) {
        lock(s);
        unsigned event=s->event;
        int idle=s->requested&&s->api.ms()-s->last_frame_ms>=NX_IPS_IDLE_MS;
        int want=s->requested&&!blocked(s)&&!s->error&&!idle;
        int opening=want&&!s->held&&!s->active;
        int closing=!want&&s->held==1&&!s->active;
        if(s->state==NX_IPS_FAILED||(!opening&&!closing)) {
            /* A released/failed owner has no timed work. Wait for an actual
             * admission event; only a retained idle reference needs a timer. */
            unsigned timeout=(s->state==NX_IPS_FAILED||!s->held||s->active)?0:100;
            unlock(s); s->api.wait(&s->event,event,timeout); continue;
        }
        s->state=opening?NX_IPS_OPENING:NX_IPS_DRAINING;
        unlock(s);
        /* The admission state excludes new frames. Recheck all blockers after
         * acquiring the native locks: capture may arrive during trywait. */
        int rc=acquire_locks(s);
        if(rc) {
            lock(s); ++s->lock_busy;
            if(rc<0) { ++s->unlock_errors; s->error=rc; s->state=NX_IPS_FAILED; }
            else s->state=s->held?NX_IPS_READY:NX_IPS_IDLE;
            changed(s); event=s->event; unlock(s);
            s->api.wait(&s->event,event,20); continue;
        }
        lock(s);
        int fresh=s->requested&&!blocked(s)&&!s->error&&
                  s->api.ms()-s->last_frame_ms<NX_IPS_IDLE_MS;
        int cancel=opening?!fresh:fresh;
        unlock(s);
        if(!cancel) rc=opening?s->api.open():s->api.close();
        int post_error=release_locks(s,NX_IPS_LOCKS);
        lock(s);
        if(!cancel) {
            if(opening) {
                if(rc<0) { ++s->open_errors; s->held=2; }
                else { ++s->opens; s->held=1; }
            } else {
                if(rc) { ++s->close_errors; s->held=2; }
                else { ++s->closes; s->held=0; s->requested=0; if(idle) ++s->idle_releases; }
            }
        }
        if(post_error) { ++s->unlock_errors; s->error=post_error; }
        if((opening?rc<0:rc!=0)&&!cancel) s->error=rc;
        if(s->held==2||post_error) s->state=NX_IPS_FAILED;
        else s->state=s->held?NX_IPS_READY:NX_IPS_IDLE;
        /* A render error requests release, but a fully successful release
         * remains distinguishable from an uncertain Open/Close failure. */
        changed(s); unlock(s);
    }
}
