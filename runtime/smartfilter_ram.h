#ifndef NX_SMARTFILTER_RAM_H
#define NX_SMARTFILTER_RAM_H
#include "smartfilter_ipcs.h"

#define NX_SFR_MAGIC 0x5246534eU
#define NX_SFR_ABI 1U
#define NX_SFR_SLOTS 5U
/* Values the stock Miniature host passes on the camera (observed 2026-11-01);
 * both reach ADE effect 1. Still value 0 takes processSmartFilter's no-effect branch. */
#define NX_SFR_LIVEVIEW_MODE 1U
#define NX_SFR_STILL_HOST_MODE 2U
enum nx_sfr_slot { NX_SFR_PREVIEW, NX_SFR_EFFECT, NX_SFR_PREPARE, NX_SFR_FINISH, NX_SFR_STILL_MODE };

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
};
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
};
/* Only --fixture on the named ARM test process accepts this UDD/lease boundary.
 * Original library identity and all five call sites remain checked. */
struct nx_sfr_fixture_bindings {
    unsigned magic, size;
    struct nx_sf_ipcs_api api;
    void *(*b2y_instance)(void);
    void *(*get_semaphore)(void *, unsigned);
};
extern struct nx_sfr_config nx_sfr_config;
extern struct nx_sfr_runtime nx_sfr_runtime;
void nx_sfr_preview(void *, void *, void *, unsigned);
int nx_sfr_effect(unsigned, void *);
int nx_sfr_prepare(void *, void (*)(void));
int nx_sfr_finish(void *);
unsigned nx_sfr_still_mode(void *);
#endif
