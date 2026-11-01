#ifndef NX_SMARTFILTER_LUT_BRIDGE_H
#define NX_SMARTFILTER_LUT_BRIDGE_H

#define NX_SF_CHANNEL_WORDS 730U
#define NX_SF_TABLE_WORDS (3U * NX_SF_CHANNEL_WORDS)
#define NX_SF_LOOK_ID_SIZE 64U
#define NX_SF_CONTEXTS 4U

enum nx_sf_result { NX_SF_OK = 0, NX_SF_BUSY = 1, NX_SF_INVALID = -1, NX_SF_RECOVERY = -2 };

/* begin must acquire the complete backend transaction without waiting. A failed
 * begin owns nothing and changes no hardware state. render must finish/drain its
 * operation before returning, including errors. restore runs before end releases
 * resources. smartfilter_ipcs.c binds the native APIs; physical completion and
 * cache behaviour require device validation. */
struct nx_sf_backend {
    void *opaque;
    unsigned (*thread_id)(void *);
    void (*stock_preview)(void *, void *, void *, unsigned);
    int (*stock_effect)(unsigned, void *);
    int (*begin)(void *);
    int (*render)(void *, void *, const unsigned short *);
    int (*restore)(void *);
    void (*end)(void *);
    int (*passthrough)(void *, void *);
};

struct nx_sf_profile {
    unsigned generation, strength;
    char look_id[NX_SF_LOOK_ID_SIZE];
    unsigned short table[NX_SF_TABLE_WORDS];
};

struct nx_sf_context {
    volatile unsigned tid;
    unsigned admitted, effect_seen, stock_depth;
};

struct nx_sf_stats {
    unsigned frames, rendered, passthrough, stock_effects, busy, errors;
    unsigned selected_generation, applied_generation;
    int last_error;
};

struct nx_sf_bridge {
    struct nx_sf_backend backend;
    /* slot_mode is the one original liveview mode, not the ADE effect enum. */
    unsigned slot_mode;
    volatile unsigned enabled, paused, capture_pending, gate, capture_owner, fault;
    struct nx_sf_profile profile;
    struct nx_sf_context contexts[NX_SF_CONTEXTS];
    struct nx_sf_stats stats;
};

/* Initialize only before attaching the two call sites. No automatic hooks or ISP
 * initialization occur here. Mode 0..9 shares the original smartFilter branch. */
int nx_sf_init(struct nx_sf_bridge *, const struct nx_sf_backend *, unsigned slot_mode);
int nx_sf_enable(struct nx_sf_bridge *, unsigned enabled);
/* The source is the direct preview table, never table-stock3d.d4lut. Copy and
 * strength mixing happen only here, outside the per-frame path. Zero strength
 * uses literal passthrough without touching the backend. */
int nx_sf_select(struct nx_sf_bridge *, const char *look_id, unsigned generation,
                 unsigned strength, const unsigned short *table, unsigned bytes);
int nx_sf_preview(struct nx_sf_bridge *, void *, void *, void *, unsigned mode);
int nx_sf_effect(struct nx_sf_bridge *, unsigned effect, void *ade_image);

/* One capture coordinator requests priority, retries BUSY after the in-flight
 * preview returns, and calls end only after the original capture has completed.
 * A BUSY result is not permission to start a physical capture. */
int nx_sf_capture_begin(struct nx_sf_bridge *);
int nx_sf_capture_end(struct nx_sf_bridge *);
int nx_sf_capture_cancel(struct nx_sf_bridge *);
int nx_sf_pause(struct nx_sf_bridge *);
int nx_sf_resume(struct nx_sf_bridge *);
/* A status snapshot, not permission to free code while call sites are attached. */
int nx_sf_quiescent(const struct nx_sf_bridge *);

#endif
