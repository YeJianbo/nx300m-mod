#ifndef NX_IPCS_SESSION_H
#define NX_IPCS_SESSION_H

/* A reference is a power lifetime, never an IPCS operation/channel lease. */
enum nx_ips_state { NX_IPS_IDLE, NX_IPS_OPENING, NX_IPS_READY,
                    NX_IPS_DRAINING, NX_IPS_FAILED };
enum nx_ips_reason { NX_IPS_CAPTURE, NX_IPS_STREAM, NX_IPS_CONTROL,
                     NX_IPS_RECOVERY, NX_IPS_REASONS };
enum nx_ips_error { NX_IPS_BUSY=1, NX_IPS_TIMEOUT=-110, NX_IPS_UNCERTAIN=-117 };
#define NX_IPS_LOCKS 5U /* lifecycle first, then channels 0..3 */
struct nx_ips_api {
    void *locks[NX_IPS_LOCKS];
    int (*trywait)(void *), (*post)(void *);
    int (*open)(void), (*close)(void);
    unsigned (*tid)(void), (*ms)(void);
    int (*wait)(volatile unsigned *, unsigned, unsigned);
    void (*wake)(volatile unsigned *);
};
struct nx_ips {
    struct nx_ips_api api;
    volatile unsigned mutex, event, state, external_stop;
    unsigned owner_tid, running, requested, held, active, last_frame_ms;
    unsigned blockers[NX_IPS_REASONS];
    unsigned opens, closes, frames, busy, lock_busy, idle_releases;
    unsigned open_errors, close_errors, unlock_errors, drain_timeouts;
    int error;
};
void nx_ips_init(struct nx_ips *, const struct nx_ips_api *);
/* The adapter starts exactly one thread and calls this once on that thread.
 * It sleeps with no device reference when disabled. Published RAM stays mapped. */
void nx_ips_owner(struct nx_ips *);
int nx_ips_frame_enter(struct nx_ips *);
void nx_ips_frame_leave(struct nx_ips *);
void nx_ips_hold(struct nx_ips *, unsigned);
void nx_ips_release(struct nx_ips *, unsigned);
int nx_ips_drain(struct nx_ips *, unsigned timeout_ms);
void nx_ips_fault(struct nx_ips *, int);
#endif
