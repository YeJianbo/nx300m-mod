/* Stock pp_core_lut_load_enable (0x279b0) programs common +0x10. The
 * original first resets LUT completion, then writes the requested start edge.
 * Execute after that reset and before its caller enters the original wait. */
#include "pp_cs_lut_state.h"
enum { PP_CS_LUT_MAX_BYTES = 8191*16 };
static unsigned char pp_cs_lut_bytes[PP_CS_LUT_MAX_BYTES];
static unsigned char pp_cs_lut_staging[PP_CS_LUT_MAX_BYTES];
static struct nx300m_pp_cs_lut_state pp_cs_lut_state;
static volatile int pp_cs_lut_lock_word;
static void pp_cs_lut_lock(void) {
    while (__sync_lock_test_and_set(&pp_cs_lut_lock_word, 1)) raw(158, 0, 0, 0, 0, 0, 0);
}
static void pp_cs_lut_unlock(void) { __sync_lock_release(&pp_cs_lut_lock_word); }

/* Keep allocation/mapping lifetime locked during copy. A mapped but released
 * BMA allocation is not a new DMA source. Pipe copying also rejects unreadable
 * pages rather than faulting on a direct userspace dereference. */
static int pp_cs_lut_copy_source(u32 physical, u32 bytes) {
    void *source = (void *)0;
    int allocated = 0, descriptors[2];
    lock();
    for (int i = 0; i < RECORDS; i++) {
        const struct allocation *a = arena_allocations[1]+i;
        if (a->size && physical >= a->physical && physical-a->physical < a->size &&
                bytes <= a->size-(physical-a->physical)) { allocated = 1; break; }
    }
    if (allocated) for (int i = 0; i < RECORDS; i++) {
        const struct mapping *m = mappings+i;
        if (m->size && physical >= m->physical && physical-m->physical < m->size &&
                bytes <= m->size-(physical-m->physical)) {
            source = (void *)(m->virtual+physical-m->physical); break;
        }
    }
    if (!source) { unlock(); return -14; }
    if (raw(42, (long)descriptors, 0, 0, 0, 0, 0) < 0) { unlock(); return -14; }
    int result = 0;
    for (u32 offset = 0; offset < bytes;) {
        u32 count = bytes-offset > 1024 ? 1024 : bytes-offset;
        long written = raw(4, descriptors[1], (long)source+offset, count, 0, 0, 0);
        if (written != (long)count ||
                raw(3, descriptors[0], (long)pp_cs_lut_staging+offset, count, 0, 0, 0) != (long)count) {
            result = -14; break;
        }
        offset += count;
    }
    raw(6, descriptors[0], 0, 0, 0, 0, 0);
    raw(6, descriptors[1], 0, 0, 0, 0, 0);
    unlock();
    return result;
}

static void pp_cs_lut_load(u32 mask) {
    volatile u32 *pp = isp_registers[ISP_PP];
    if (!pp) return;
    pp_cs_lut_state.valid = 0;
    pp_cs_lut_state.source = pp[0x4608/4];
    pp_cs_lut_state.bytes = ((pp[0x42e0/4]>>8)&8191U)*16;
    int result = -95;
    if (mask == 0x1000U && (pp[0x10/4]&0x1000U) && !(pp[0x42d0/4]&0x1000U)) {
        if (!pp_cs_lut_state.bytes || !pp_cs_lut_state.source ||
                pp_cs_lut_state.source > ~0U-pp_cs_lut_state.bytes) result = -22;
        else result = pp_cs_lut_copy_source(pp_cs_lut_state.source, pp_cs_lut_state.bytes);
    }
    pp_cs_lut_state.error = result;
    if (result) {
        event("PP CS LUT load unsupported; no completion", (u32)result,
              pp_cs_lut_state.source, pp_cs_lut_state.bytes);
        return;
    }
    for (u32 i = 0; i < pp_cs_lut_state.bytes; i++) pp_cs_lut_bytes[i] = pp_cs_lut_staging[i];
    ++pp_cs_lut_state.generation;
    pp_cs_lut_state.valid = 1;
    event("PP CS LUT bytes loaded", pp_cs_lut_state.source,
          pp_cs_lut_state.bytes, pp_cs_lut_state.generation);
    /* GPL ISR bit6 completes only LUT_LOAD. It does not queue a frame event. */
    pp_event_lock(); pp_dispatch(0x40U); pp_event_unlock();
}

/* The memory pass must observe SRAM bytes, not the reusable DMA source.
 * Keep metadata and payload from one generation in the same private dump. */
static void pp_cs_lut_snapshot(void) {
    pp_cs_lut_lock();
    int fd = checked(raw(5, (long)"/var/run/nx300m-pp-cs-at-memory.bin", 0101|01000|02000000, 0600, 0, 0, 0));
    if (fd >= 0) {
        const unsigned char *parts[2] = {(const unsigned char *)&pp_cs_lut_state, pp_cs_lut_bytes};
        u32 sizes[2] = {sizeof pp_cs_lut_state, pp_cs_lut_state.valid ? pp_cs_lut_state.bytes : 0};
        int failed = 0;
        for (u32 part = 0; part < 2 && !failed; part++) {
            u32 offset = 0;
            while (offset < sizes[part]) {
                long n = raw(4, fd, (long)(parts[part]+offset), sizes[part]-offset, 0, 0, 0);
                if (n <= 0) { failed = 1; break; }
                offset += (u32)n;
            }
        }
        raw(6, fd, 0, 0, 0, 0, 0);
        if (failed) raw(10, (long)"/var/run/nx300m-pp-cs-at-memory.bin", 0, 0, 0, 0, 0);
    }
    pp_cs_lut_unlock();
}

int nx300m_pp_cs_lut_read(struct nx300m_pp_cs_lut_state *state, void *destination, u32 capacity) {
    if (!state || (!destination && capacity)) return -22;
    pp_cs_lut_lock();
    *state = pp_cs_lut_state;
    int result = state->valid ? 0 : -61;
    if (!result && destination) {
        if (capacity < state->bytes) result = -28;
        else {
            unsigned char *out = destination;
            for (u32 i = 0; i < state->bytes; i++) out[i] = pp_cs_lut_bytes[i];
            result = state->bytes;
        }
    }
    pp_cs_lut_unlock();
    return result;
}

u32 pp_core_lut_load_enable(u32 mask, u32 on) {
    u32 (*original)(u32, u32) = dlsym((void *)-1, "pp_core_lut_load_enable");
    if (!original) { extern void abort(void); abort(); }
    /* Preserve even the raw r0 of this original void-style register setter. */
    if (mask&0x1000U) pp_cs_lut_lock();
    u32 result = original(mask, on);
    int saved_errno = *__errno_location();
    if ((mask&0x1000U) && on == 1) pp_cs_lut_load(mask);
    if (mask&0x1000U) pp_cs_lut_unlock();
    *__errno_location() = saved_errno;
    return result;
}
