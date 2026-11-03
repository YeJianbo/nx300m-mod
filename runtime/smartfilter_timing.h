#ifndef NX_SMARTFILTER_TIMING_H
#define NX_SMARTFILTER_TIMING_H

#define NX_SFT_SCHEMA 2U
#define NX_SFT_FRAMES 128U
#define NX_SFT_CAPTURES 16U
#define NX_SFT_SCOPES 32U
#define NX_SFT_LOOK_ID 64U
#define NX_SFT_NO_STAGE 0xffffffffU

enum nx_sft_stage {
    NX_SFT_PREVIEW, NX_SFT_EFFECT, NX_SFT_ENSURE, NX_SFT_INITIALIZE,
    NX_SFT_SELECT, NX_SFT_BEGIN, NX_SFT_TRY0, NX_SFT_TRY1, NX_SFT_TRY2,
    NX_SFT_TRY3, NX_SFT_GEOMETRY, NX_SFT_OPEN, NX_SFT_ADE_INIT,
    NX_SFT_LUT0, NX_SFT_LUT1, NX_SFT_LUT2, NX_SFT_FLUSH_IN, NX_SFT_YCC,
    NX_SFT_FLUSH_OUT, NX_SFT_RESET, NX_SFT_IQ_INIT, NX_SFT_IQ_PROCESS,
    NX_SFT_CLOSE, NX_SFT_UNLOCK, NX_SFT_COPY, NX_SFT_DRAIN,
    NX_SFT_PREPARE, NX_SFT_FINISH, NX_SFT_CALIBRATION, NX_SFT_STAGES
};
enum nx_sft_state { NX_SFT_EMPTY, NX_SFT_PENDING, NX_SFT_COMMITTED };
enum nx_sft_kind { NX_SFT_FRAME = 1, NX_SFT_CAPTURE_PREPARE, NX_SFT_CAPTURE_FINISH,
                   NX_SFT_CLOCK_CALIBRATION, NX_SFT_INITIALIZATION };
enum nx_sft_path {
    NX_SFT_PATH_UNKNOWN, NX_SFT_PATH_RENDER, NX_SFT_PATH_STRENGTH_ZERO,
    NX_SFT_PATH_DISABLED, NX_SFT_PATH_CAPTURE, NX_SFT_PATH_CONTROL,
    NX_SFT_PATH_BUSY, NX_SFT_PATH_FAULT, NX_SFT_PATH_FALLBACK,
    NX_SFT_PATH_STOCK, NX_SFT_PATH_NO_CONTEXT,
    NX_SFT_PATH_PROBE_EXHAUSTED=11, NX_SFT_PATH_PROBE_BLOCKED=12
};
enum nx_sft_flag {
    NX_SFT_F_CLOCK = 1U, NX_SFT_F_FIRST = 2U, NX_SFT_F_COLD = 4U,
    NX_SFT_F_SELECT = 8U, NX_SFT_F_NESTED = 16U,
    NX_SFT_F_RESTORE = 32U, NX_SFT_F_UNLOCK = 64U, NX_SFT_F_EPOCH = 128U,
    NX_SFT_F_PROBE = 256U
};
/* Absolute CLOCK_MONOTONIC timestamps and normalized durations, never ns32.
 * A failed timestamp has valid=0; zero duration is valid only with its mask bit. */
struct nx_sft_time { unsigned sec, nsec, valid; };
struct nx_sft_duration { unsigned sec, nsec; };
struct nx_sft_record {
    volatile unsigned state;
    unsigned kind, tid, window, generation, strength;
    unsigned width, height, stride, sampling;
    struct nx_sft_time start, end, current_time;
    unsigned current_stage, epoch_begin, epoch_end, path, flags, fault;
    int rc;
    unsigned stage_mask, valid_mask, invalid_mask;
    unsigned count[NX_SFT_STAGES];
    struct nx_sft_duration duration[NX_SFT_STAGES];
    int stage_rc[NX_SFT_STAGES];
    unsigned self, captures_begin, captures_end;
    struct nx_sft_timing *owner; /* Internal writer context, not an export field. */
};
struct nx_sft_scope {
    volatile unsigned tid;
    struct nx_sft_record *record;
    unsigned last_epoch, seen;
};
/* Loader owns enabled/armed/frozen/epoch and all window reset operations.
 * Reset/arm only inside the existing no-inflight control transaction.
 * Freeze admission before all-thread-stop snapshot; pending records are copied
 * as-is, never polled until hardware returns. No concurrent live record reads. */
struct nx_sft_timing {
    unsigned schema;
    volatile unsigned enabled, armed, frozen;
    unsigned window, generation, strength;
    volatile unsigned epoch, next_frame, next_capture, dropped, clock_errors;
    char look_id[NX_SFT_LOOK_ID];
    struct nx_sft_record frames[NX_SFT_FRAMES];
    struct nx_sft_record captures[NX_SFT_CAPTURES];
    struct nx_sft_record calibration;
    struct nx_sft_scope scopes[NX_SFT_SCOPES];
};
/* Each mark lives on the calling stack. Nesting restores the enclosing pending
 * stage and its entry timestamp; no handle is stored on shared IPCS objects. */
struct nx_sft_mark {
    struct nx_sft_time start, previous_time;
    unsigned stage, previous_stage;
};
struct nx_sft_record *nx_sft_reserve(struct nx_sft_timing *, unsigned kind,
                                   unsigned tid, unsigned generation,
                                   unsigned strength, unsigned self);
struct nx_sft_record *nx_sft_current(struct nx_sft_timing *, unsigned tid);
struct nx_sft_mark nx_sft_enter_stage(struct nx_sft_record *, unsigned);
void nx_sft_leave_stage(struct nx_sft_record *, struct nx_sft_mark, int rc);
void nx_sft_commit(struct nx_sft_timing *, struct nx_sft_record *, int rc,
                   unsigned fault, unsigned captures);
/* Runtime-owned diagnostics live at the very end of nx_sfr_runtime. */
struct nx_sft_record *nx_sfr_trace_current(void);
#endif
