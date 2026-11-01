/* One stock SmartFilter slot hosts the independently selected RAM LUT.
 * No per-frame allocation, filesystem access, polling loop or colour fitting. */
#include "smartfilter_lut_bridge.h"

enum { IDLE, FRAME, CONTROL, CAPTURE };

static unsigned get(const volatile unsigned *p) { return __atomic_load_n(p, __ATOMIC_ACQUIRE); }
static void set(volatile unsigned *p, unsigned v) { __atomic_store_n(p, v, __ATOMIC_RELEASE); }
static void count(unsigned *p) { __sync_fetch_and_add(p, 1); }
static int acquire(struct nx_sf_bridge *s, unsigned gate) {
    return __sync_bool_compare_and_swap(&s->gate, IDLE, gate);
}
static void release(struct nx_sf_bridge *s) { set(&s->gate, IDLE); }
static void zero(void *p, unsigned bytes) {
    unsigned char *at = p;
    while (bytes--) *at++ = 0;
}
static unsigned identity(unsigned channel, unsigned index) {
    unsigned coord = channel == 0 ? index / 81U : channel == 1 ? (index / 9U) % 9U : index % 9U;
    return coord * 1023U / 8U;
}

int nx_sf_init(struct nx_sf_bridge *s, const struct nx_sf_backend *b, unsigned slot_mode) {
    if (!s || !b || slot_mode > 9 || !b->thread_id || !b->stock_preview ||
        !b->stock_effect || !b->begin || !b->render || !b->restore ||
        !b->end || !b->passthrough) return NX_SF_INVALID;
    zero(s, sizeof *s);
    s->backend = *b;
    s->slot_mode = slot_mode;
    return NX_SF_OK;
}

int nx_sf_enable(struct nx_sf_bridge *s, unsigned enabled) {
    if (enabled > 1) return NX_SF_INVALID;
    if (!acquire(s, CONTROL)) return NX_SF_BUSY;
    if (enabled && get(&s->fault)) { release(s); return NX_SF_RECOVERY; }
    set(&s->enabled, enabled);
    release(s);
    return NX_SF_OK;
}

int nx_sf_select(struct nx_sf_bridge *s, const char *id, unsigned generation,
                 unsigned strength, const unsigned short *table, unsigned bytes) {
    unsigned n = 0;
    if (!id || !generation || strength > 100 ||
        (table ? bytes != NX_SF_TABLE_WORDS * sizeof *table : bytes || strength)) return NX_SF_INVALID;
    while (n < NX_SF_LOOK_ID_SIZE && id[n]) ++n;
    if (!n || n >= NX_SF_LOOK_ID_SIZE) return NX_SF_INVALID;
    if (table) {
        for (unsigned i = 0; i < NX_SF_TABLE_WORDS; ++i)
            if (table[i] > 1023 || ((i % NX_SF_CHANNEL_WORDS == 729) && table[i])) return NX_SF_INVALID;
    }
    if (!acquire(s, CONTROL)) return NX_SF_BUSY;
    for (unsigned i = 0; i <= n; ++i) s->profile.look_id[i] = id[i];
    for (unsigned c = 0; c < 3; ++c) {
        for (unsigned i = 0; i < 729; ++i) {
            unsigned base = identity(c, i), value = table ? table[c * NX_SF_CHANNEL_WORDS + i] : base;
            s->profile.table[c * NX_SF_CHANNEL_WORDS + i] =
                (unsigned short)((base * (100U - strength) + value * strength + 50U) / 100U);
        }
        s->profile.table[c * NX_SF_CHANNEL_WORDS + 729] = 0;
    }
    s->profile.generation = generation;
    s->profile.strength = strength;
    set(&s->stats.selected_generation, generation);
    release(s);
    return NX_SF_OK;
}

static struct nx_sf_context *context(struct nx_sf_bridge *s, unsigned tid) {
    for (unsigned i = 0; i < NX_SF_CONTEXTS; ++i)
        if (get(&s->contexts[i].tid) == tid) return &s->contexts[i];
    return (void *)0;
}

int nx_sf_preview(struct nx_sf_bridge *s, void *node, void *image, void *data, unsigned mode) {
    const struct nx_sf_backend *b = &s->backend;
    unsigned tid = b->thread_id(b->opaque);
    struct nx_sf_context *nested = tid ? context(s, tid) : (void *)0;
    if (nested) {
        ++nested->stock_depth;
        b->stock_preview(node, image, data, mode);
        --nested->stock_depth;
        return NX_SF_OK;
    }
    if (!get(&s->enabled) || mode != s->slot_mode || !tid) {
        b->stock_preview(node, image, data, mode); return NX_SF_OK;
    }
    struct nx_sf_context *c = (void *)0;
    for (unsigned i = 0; i < NX_SF_CONTEXTS; ++i) {
        if (__sync_bool_compare_and_swap(&s->contexts[i].tid, 0, tid)) { c = &s->contexts[i]; break; }
    }
    if (!c) { b->stock_preview(node, image, data, mode); return NX_SF_OK; }
    c->effect_seen = c->stock_depth = 0;
    c->admitted = acquire(s, FRAME);
    if (c->admitted && (get(&s->paused) || get(&s->capture_pending) || get(&s->fault))) {
        release(s); c->admitted = 0;
    }
    if (!c->admitted) count(&s->stats.busy);
    count(&s->stats.frames);
    b->stock_preview(node, image, data, mode);
    if (c->admitted) release(s);
    set(&c->tid, 0);
    return NX_SF_OK;
}

static int passthrough(struct nx_sf_bridge *s, unsigned effect, void *image, int selected) {
    int result = s->backend.passthrough(s->backend.opaque, image);
    if (!result) {
        count(&s->stats.passthrough);
        if (selected && s->profile.generation) set(&s->stats.applied_generation, s->profile.generation);
        return 0;
    }
    /* If layout validation failed, keep the original effect's error semantics;
     * claiming success with an unfilled output buffer would break the pipeline. */
    count(&s->stats.stock_effects);
    return s->backend.stock_effect(effect, image);
}

int nx_sf_effect(struct nx_sf_bridge *s, unsigned effect, void *image) {
    const struct nx_sf_backend *b = &s->backend;
    unsigned tid = b->thread_id(b->opaque);
    struct nx_sf_context *c = tid ? context(s, tid) : (void *)0;
    if (!c || c->effect_seen || c->stock_depth) {
        count(&s->stats.stock_effects); return b->stock_effect(effect, image);
    }
    c->effect_seen = 1;
    if (!c->admitted) return passthrough(s, effect, image, 0);
    if (!s->profile.generation || !s->profile.strength) return passthrough(s, effect, image, 1);
    int rc = b->begin(b->opaque);
    if (rc) { count(&s->stats.busy); return passthrough(s, effect, image, 0); }
    /* A capture request arriving during begin wins before any new operation. */
    if (get(&s->capture_pending) || get(&s->paused)) {
        int restored = b->restore(b->opaque);
        if (restored) {
            set(&s->fault, 1); set(&s->paused, 1);
            __atomic_store_n(&s->stats.last_error, restored, __ATOMIC_RELEASE);
            count(&s->stats.errors);
        }
        b->end(b->opaque);
        return passthrough(s, effect, image, 0);
    }
    rc = b->render(b->opaque, image, s->profile.table);
    int restored = b->restore(b->opaque);
    if (restored) { set(&s->fault, 1); set(&s->paused, 1); }
    b->end(b->opaque);
    if (rc || restored || get(&s->fault)) {
        if (!rc && !restored) return passthrough(s, effect, image, 0);
        __atomic_store_n(&s->stats.last_error, restored ? restored : rc, __ATOMIC_RELEASE);
        count(&s->stats.errors);
        return passthrough(s, effect, image, 0);
    }
    count(&s->stats.rendered);
    set(&s->stats.applied_generation, s->profile.generation);
    return 0;
}

int nx_sf_capture_begin(struct nx_sf_bridge *s) {
    unsigned tid = s->backend.thread_id(s->backend.opaque);
    if (!tid) return NX_SF_INVALID;
    set(&s->capture_pending, 1);
    if (get(&s->fault)) return NX_SF_RECOVERY;
    if (!acquire(s, CAPTURE)) return NX_SF_BUSY;
    set(&s->capture_owner, tid);
    return NX_SF_OK;
}

int nx_sf_capture_end(struct nx_sf_bridge *s) {
    if (get(&s->gate) != CAPTURE || get(&s->capture_owner) != s->backend.thread_id(s->backend.opaque))
        return NX_SF_INVALID;
    set(&s->capture_owner, 0);
    set(&s->capture_pending, 0);
    release(s);
    return NX_SF_OK;
}

int nx_sf_capture_cancel(struct nx_sf_bridge *s) {
    if (get(&s->gate) == CAPTURE) return NX_SF_BUSY;
    set(&s->capture_pending, 0);
    return NX_SF_OK;
}

int nx_sf_pause(struct nx_sf_bridge *s) {
    set(&s->paused, 1);
    return get(&s->gate) == IDLE ? NX_SF_OK : NX_SF_BUSY;
}

int nx_sf_resume(struct nx_sf_bridge *s) {
    if (!acquire(s, CONTROL)) return NX_SF_BUSY;
    int result = NX_SF_OK;
    if (get(&s->fault)) result = NX_SF_RECOVERY;
    else if (get(&s->capture_pending)) result = NX_SF_BUSY;
    else set(&s->paused, 0);
    release(s);
    return result;
}

int nx_sf_quiescent(const struct nx_sf_bridge *s) {
    if (get(&s->gate) != IDLE) return 0;
    for (unsigned i = 0; i < NX_SF_CONTEXTS; ++i) if (get(&s->contexts[i].tid)) return 0;
    return 1;
}
