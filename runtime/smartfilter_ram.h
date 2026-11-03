#ifndef NX_SMARTFILTER_RAM_H
#define NX_SMARTFILTER_RAM_H
#include "smartfilter_ipcs.h"

#define NX_SFR_MAGIC 0x5246534eU
#ifdef NX_SFR_SESSION
#include "ipcs_session.h"
#include "ipcs_calltrace.h"
#define NX_SFR_ABI 7U
#define NX_SFR_SLOTS 20U
#define NX_SFR_BLOB_MAX 262144U
#else
#define NX_SFR_ABI 4U
#define NX_SFR_SLOTS 7U
#define NX_SFR_BLOB_MAX 131072U
#endif
#define NX_SFR_S1_SLOTS 5U
#define NX_SFR_WORK_BYTES 0x168000U
#ifdef NX_SFR_SESSION
#define NX_SFR_MODE_CALLS 6U
#else
#define NX_SFR_MODE_CALLS 4U
#endif
enum nx_sfr_profile { NX_SFR_S1, NX_SFR_S2 };
enum nx_sfr_mode_call { NX_SFR_CREATE, NX_SFR_EXECUTE, NX_SFR_B2Y, NX_SFR_LDC,
                       NX_SFR_CHANGE_CHECK, NX_SFR_CHANGE_CACHE };
/* Values the stock Miniature host passes on the camera (observed 2026-11-01);
 * both reach ADE effect 1. Still value 0 takes processSmartFilter's no-effect branch. */
#define NX_SFR_LIVEVIEW_MODE 1U
#define NX_SFR_STILL_HOST_MODE 2U
enum nx_sfr_slot { NX_SFR_PREVIEW, NX_SFR_EFFECT, NX_SFR_PREPARE, NX_SFR_FINISH, NX_SFR_STILL_MODE,
                   NX_SFR_LIVEVIEW_MODE_GETTER, NX_SFR_WORK_ADDRESS
#ifdef NX_SFR_SESSION
    ,NX_SFR_B2Y_OPEN, NX_SFR_B2Y_CLOSE, NX_SFR_STOP, NX_SFR_START,
    NX_SFR_UDD_OPEN, NX_SFR_MUTEX, NX_SFR_DEVICE_OPEN, NX_SFR_REGMAP, NX_SFR_IRQ, NX_SFR_INIT,
    NX_SFR_FRAME_CALLBACK, NX_SFR_MESSAGE_HANDLE, NX_SFR_CHANGE_STOP_CALLBACK
#endif
};

struct nx_sfr_header {
    unsigned magic, abi, blob_size, code_size, data_offset, data_size;
    unsigned entry[NX_SFR_SLOTS];
    unsigned config_offset, config_size, runtime_offset, runtime_size, build;
};
struct nx_sfr_config {
    unsigned abi, size, enabled, generation, strength;
    char look_id[NX_SF_LOOK_ID_SIZE];
    unsigned short table[NX_SF_TABLE_WORDS];
    unsigned original[NX_SFR_SLOTS];
    struct nx_sf_ipcs_api api;
    void *(*b2y_instance)(void);
    void *(*get_semaphore)(void *, unsigned);
    void *(*common_instance)(void);
    unsigned profile, insert_enabled, work_base, work_size;
    unsigned mode_lr[NX_SFR_MODE_CALLS], work_lr, work_execute_lr;
    unsigned timing_enabled;
    unsigned probe_once;
#ifdef NX_SFR_SESSION
    void *(*get_lifecycle)(void *);
    int (*thread_create)(unsigned *,const void *,void *(*)(void *),void *);
    int (*attr_init)(void *), (*attr_stack)(void *,unsigned), (*attr_destroy)(void *);
    int (*wait_syscall)(unsigned,...);
    volatile unsigned *udd_count;
    void *udd_mutex_outer, *udd_mutex_device;
    unsigned session_mode, calltrace_enabled;
    void *(*liveview_instance)(void);
    volatile int *capture_queue;
    int (*queue_send)(int,const void *,unsigned,int);
    int (*liveview_changed)(void *,int);
    void (*liveview_change_state)(void *,unsigned);
    void (*liveview_memory_restart)(void *,int);
#endif
};
enum nx_sfr_probe_state {
    NX_SFR_PROBE_OFF=0, NX_SFR_PROBE_ARMED=1, NX_SFR_PROBE_CLAIMED=2,
    NX_SFR_PROBE_RETURNED=3, NX_SFR_PROBE_BLOCKED=4
};
enum nx_sfr_probe_outcome {
    NX_SFR_PROBE_NONE=0, NX_SFR_PROBE_SUCCESS=1, NX_SFR_PROBE_BUSY=2,
    NX_SFR_PROBE_CAPTURE=3, NX_SFR_PROBE_RENDER=4, NX_SFR_PROBE_RESTORE=5,
    NX_SFR_PROBE_UNLOCK=6, NX_SFR_PROBE_TRACE=7, NX_SFR_PROBE_CONTROL=8,
    NX_SFR_PROBE_FAULT=9, NX_SFR_PROBE_INVALID=10
};
struct nx_sfr_probe {
    volatile unsigned state;
    unsigned slot, outcome, window, generation, prepare, finish;
};
struct nx_sfr_origin { volatile unsigned tid, pending, active; };
#ifdef NX_SFR_SESSION
struct nx_sfr_capture_owner { volatile unsigned tid; unsigned native,skipped; };
#endif
struct nx_sfr_runtime {
    volatile unsigned inflight, initialized, backend_active, captures, control_pause;
    unsigned synced_generation, capture_waits, capture_errors;
    int init_error;
    struct nx_sf_bridge bridge;
    struct nx_sf_ipcs ipcs;
    /* Observed hook arguments: bit n of a mask means value n was seen (n>=31 → bit 31). */
    volatile unsigned preview_calls, preview_last, preview_mask, ensure_last;
    volatile unsigned effect_calls, effect_last, effect_mask;
    volatile unsigned still_calls, still_last, still_mask;
    volatile unsigned prepare_calls, finish_calls;
    volatile unsigned mode_calls, mode_forged[NX_SFR_MODE_CALLS], mode_stock;
    volatile unsigned work_managed, work_stock, s2_frames, s2_fallback, s2_fault, s2_path;
    struct nx_sfr_origin origins[NX_SF_CONTEXTS];
    /* Independent tail: bridge/IPCS initialization must never clear this. */
    struct nx_sft_timing timing;
    struct nx_sfr_probe probe;
#ifdef NX_SFR_SESSION
    struct nx_ips session;
    struct nx_ipct open_trace;
    volatile unsigned session_initialized, session_started;
    unsigned owner_handle, frame_lifecycle_held, b2y_leases, stream_stopped;
    struct nx_sfr_capture_owner capture_owners[64];
    unsigned capture_owner_overflow;
    volatile unsigned refresh_queued, refresh_attempt, refresh_requests, refresh_dispatches;
    int refresh_error;
    volatile unsigned refresh_pending, refresh_resumes, refresh_state, refresh_wakeups;
#endif
};
/* Only --fixture on the named ARM test process accepts this UDD/lease boundary.
 * Original library identity and all five call sites remain checked. */
struct nx_sfr_fixture_bindings {
    unsigned magic, size;
    struct nx_sf_ipcs_api api;
    void *(*b2y_instance)(void);
    void *(*get_semaphore)(void *, unsigned);
    void *(*common_instance)(void);
#ifdef NX_SFR_SESSION
    volatile unsigned *udd_count;
    void *(*liveview_instance)(void);
    volatile int *capture_queue;
#endif
};
extern struct nx_sfr_config nx_sfr_config;
extern struct nx_sfr_runtime nx_sfr_runtime;
void nx_sfr_preview(void *, void *, void *, unsigned);
int nx_sfr_effect(unsigned, void *);
int nx_sfr_prepare(void *, void (*)(void));
int nx_sfr_finish(void *);
unsigned nx_sfr_still_mode(void *);
unsigned nx_sfr_liveview_mode(void *);
unsigned nx_sfr_work_address(void *, unsigned);
#ifdef NX_SFR_SESSION
void nx_sfr_frame_callback(void *,int,void *,void *,unsigned);
int nx_sfr_message_handle(void *);
void nx_sfr_change_stop_callback(void *,int);
#endif
#endif
