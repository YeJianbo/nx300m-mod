/* Isolated stock libudd CS transfers; no pixel pipeline or supplied IRQs. */
#include "pp_cs_lut_state.h"
typedef unsigned int u32;
extern int printf(const char *, ...), open(const char *, int, ...), close(int);
extern int read(int, void *, u32), ioctl(int, u32, ...), *__errno_location(void);
extern void *mmap(void *, u32, int, int, int, long), *dlsym(void *, const char *);
extern int pp_core_open(void), pp_core_start_lut_update(u32, const void *);
extern int pp_core_wait_lut_update(u32, u32), pp_core_common_lut_init(u32, const void *);
extern int pp_core_common_lut_update(u32, const void *);
extern int pp_core_dd_com_wait_lut_load_done_with_timeout(u32);
extern u32 pp_core_lut_load_enable(u32, u32);
struct waitfd { int fd; short events, revents; };
extern int poll(struct waitfd *, u32, int);
struct load_info { u32 source, bytes, timeout; };
enum { BYTES = 42720 };
static unsigned char expected[BYTES], copied[BYTES];
#define CHECK(x) do { if (!(x)) { printf("FAIL line=%d errno=%d\n", __LINE__, *__errno_location()); return 1; } } while (0)
static int (*snapshot)(struct nx300m_pp_cs_lut_state *, void *, u32);
static int same(void) {
    for (u32 i = 0; i < BYTES; i++) if (expected[i] != copied[i]) return 0;
    return 1;
}
int main(void) {
    CHECK(pp_core_open() == 0);
    int fd = open("/dev/d4_pp_core", 2|04000), bma = open("/dev/d4_bma", 2);
    CHECK(fd >= 0 && bma >= 0);
    volatile u32 *pp = mmap(0, 0xb000, 3, 1, fd, 0x50040000);
    CHECK(pp != (void *)-1);
    snapshot = dlsym(0, "nx300m_pp_cs_lut_read"); CHECK(snapshot);
    struct nx300m_pp_cs_lut_state state;
    CHECK(snapshot(&state, copied, sizeof copied) == -61 && !state.valid && !state.generation);
    struct { u32 physical, bytes; } allocation = {0, BYTES};
    CHECK(ioctl(bma, 0xc0086201U, &allocation) == 0);
    unsigned char *source = mmap(0, BYTES, 3, 1, bma, (long)allocation.physical);
    CHECK(source != (void *)-1);
    int input = open("/mod/pp-cs-lut-probe/source.bin", 0); CHECK(input >= 0);
    u32 offset = 0;
    while (offset < BYTES) { int n = read(input, source+offset, BYTES-offset); CHECK(n > 0); offset += n; }
    close(input);
    for (u32 i = 0; i < BYTES; i++) expected[i] = source[i];
    struct load_info info = {(u32)source, BYTES, 5};
    /* Stock async start reuses CS size from prior init/update. It does not
     * itself program +0x42e0, unlike the synchronous common APIs. */
    CHECK(pp_core_common_lut_init(3, &info) == 0);
    CHECK(snapshot(&state, copied, sizeof copied) == BYTES && same() && state.generation == 1);
    CHECK(pp_core_start_lut_update(3, &info) == 0);
    CHECK(snapshot(&state, copied, sizeof copied) == BYTES && same());
    CHECK(state.valid && state.source == allocation.physical && state.bytes == BYTES && state.generation == 2);
    CHECK(!(pp[0x24/4]&0x40U) && pp[0x20/4] == 0 && (pp[0x10/4]&0x1000U));
    for (u32 i = 0; i < BYTES; i++) source[i] = 0xa5;
    CHECK(snapshot(&state, copied, sizeof copied) == BYTES && same());
    CHECK(pp_core_wait_lut_update(3, 5) == 0);
    CHECK(!(pp[0x10/4]&0x1000U) && !(pp[0x42d0/4]&0x100U));
    CHECK(pp_core_dd_com_wait_lut_load_done_with_timeout(1) == -1);
    CHECK(snapshot(&state, copied, sizeof copied) == BYTES && same());
    printf("CS_DMA start source_bytes=%u copied=all source_reuse_independent=1 stock_wait_consumed=1 disable_retained_cache=1 generation=%u\n", BYTES, state.generation);

    for (u32 i = 0; i < BYTES; i++) expected[i] = source[i] = (unsigned char)(i*37+11);
    CHECK(pp_core_common_lut_update(3, &info) == 0);
    CHECK(snapshot(&state, copied, sizeof copied) == BYTES && same() && state.generation == 3);
    CHECK(pp_core_dd_com_wait_lut_load_done_with_timeout(1) == -1);
    struct waitfd waiting = {fd, 1, 0}; CHECK(poll(&waiting, 1, 5) == 0);
    printf("CS_DMA stock_common_update=success stock_common_init=success exact_varied_bytes=1 frame_queue_empty=1 generation=%u\n", state.generation);

    /* The real start/wait executes, but unsupported double-source arithmetic
     * must not consume the previous cache or receive a synthetic completion. */
    pp[0x42d0/4] |= 0x1000U;
    CHECK(pp_core_start_lut_update(3, &info) == 0);
    CHECK(snapshot(&state, copied, sizeof copied) == -61 && !state.valid && state.error == -95 && state.generation == 3);
    CHECK(pp_core_wait_lut_update(3, 1) == -1);
    pp[0x42d0/4] &= ~0x1000U;
    CHECK(pp_core_common_lut_update(3, &info) == 0);
    CHECK(snapshot(&state, copied, sizeof copied) == BYTES && same() && state.generation == 4);
    /* Free the allocation but keep its mapping: allocation lifetime, rather
     * than a stale virtual mapping, must authorize the next DMA transfer. */
    u32 physical = allocation.physical;
    CHECK(ioctl(bma, 0xc0046204U, &physical) == 0);
    for (u32 i = 0; i < BYTES; i++) source[i] = 0x5a;
    CHECK(snapshot(&state, copied, sizeof copied) == BYTES && same() && state.generation == 4);
    CHECK(pp_core_start_lut_update(3, &info) == 0);
    CHECK(snapshot(&state, copied, sizeof copied) == -61 && !state.valid && state.error == -14 && state.generation == 4);
    CHECK(pp_core_wait_lut_update(3, 1) == -1);
    CHECK(poll(&waiting, 1, 5) == 0);
    printf("CS_DMA double_source=no_completion freed_source=no_completion loaded_cache_survived_source_free=1 stale_cache_invalidated=1 frame_queue_empty=1 pixels_completed=0\n");
    return 0;
}
