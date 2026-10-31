/* Exercise the observed stock memory pass and keep unsupported pixels pending.
 * Image mappings are private synthetic RAM, not a supplied completed frame. */
#include "pp_memory_task.h"
typedef unsigned int u32;
extern int printf(const char *, ...), open(const char *, int, ...), close(int);
extern int read(int, void *, u32), write(int, const void *, u32), *__errno_location(void);
extern void *mmap(void *, u32, int, int, int, long), *dlsym(void *, const char *);
extern int pp_core_open(void), ioctl(int, u32, ...);
extern void pp_rw_dma_start(void);
struct waitfd { int fd; short events, revents; };
extern int poll(struct waitfd *, u32, int);
#define CHECK(x) do { if (!(x)) { printf("FAIL line=%d errno=%d\n", __LINE__, *__errno_location()); return 1; } } while (0)
static u32 bank[0xb000/4], bad[0xb000/4];
static unsigned char captured_rows[4][4224];
static unsigned short captured_expanded[4*2812];
static int read_all(int fd, void *buffer, u32 bytes) {
    unsigned char *p = buffer;
    while (bytes) { int n = read(fd, p, bytes); if (n <= 0) return -1; bytes -= n; p += n; }
    return 0;
}
static void copy_bank(void) { for (u32 i = 0; i < sizeof bank/4; i++) bad[i] = bank[i]; }
int main(void) {
    int source = open("/mod/pp-memory-task-probe/bank.bin", 0); CHECK(source >= 0);
    CHECK(!read_all(source, bank, sizeof bank)); close(source);
    CHECK(pp_core_open() == 0);
    int ppfd = open("/dev/d4_pp_core", 2|04000); CHECK(ppfd >= 0);
    volatile u32 *pp = mmap(0, sizeof bank, 3, 1, ppfd, 0x50040000); CHECK(pp != (void *)-1);
    for (u32 i = 0; i < sizeof bank/4; i++) pp[i] = bank[i];
    int (*describe)(const volatile u32 *, u32, struct nx_pp_memory_task *, int) = dlsym(0, "nx300m_pp_memory_describe");
    int (*input14)(const volatile u32 *, u32, unsigned short *, u32) = dlsym(0, "nx300m_pp_memory_input14");
    int (*planes14)(const volatile u32 *, const unsigned char *const [4],
                    const u32 [4], const u32 [4], u32, u32, unsigned short *, u32)
        = dlsym(0, "nx300m_pp_expand12_planes14");
    CHECK(describe && input14 && planes14);
    struct nx_pp_memory_task task;
    CHECK(describe(bank, sizeof bank, &task, 0) == -95 && !task.invalid);
    CHECK(task.width == 5624 && task.height == 3714 && task.plane_rows == 1857 && task.payload_bytes == 4218);
    CHECK(task.enabled_stages == (NX_PP_DYNAMIC_DPC|NX_PP_CS|NX_PP_DAMP|NX_PP_INPUT_EXPANSION));
    CHECK(task.cs_columns == 89 && task.cs_rows == 60 && task.cs_source_bytes == 42720);
    CHECK(describe(bank, sizeof bank, &task, 1) == -14 && (task.invalid&NX_PP_UNMAPPED_ADDRESS));
    int sma = open("/dev/d4_sma", 2); CHECK(sma >= 0);
    u32 bytes = 4224U*1857U*4U;
    unsigned char *input = mmap(0, bytes, 3, 1, sma, (long)bank[0x300c/4]);
    unsigned char *output = mmap(0, bytes, 3, 1, sma, (long)bank[0x3304/4]);
    CHECK(input != (void *)-1 && output != (void *)-1);
    for (u32 i = 0; i < bytes; i++) { input[i] = 0x3c; output[i] = 0xa5; }
    CHECK(describe(bank, sizeof bank, &task, 1) == -95 && !task.invalid && task.mapping_checked);
    for (u32 i = 0; i < 4; i++) {
        struct nx_pp_plane *p = task.planes+i;
        CHECK(p->read_mapped && p->write_mapped && p->read_stride == 4224 && p->write_stride == 4224);
        CHECK(p->read_span == 7843962 && p->write_span == 7843962);
        CHECK(p->read_allocation == 7843968 && p->write_allocation == 7843968);
        printf("PP_MEMORY_PLANE parity=%u read=%08x write=%08x stride=%u span=%u allocation=%u mapped=1\n",
            i, p->read_address, p->write_address, p->read_stride, p->read_span, p->read_allocation);
    }
    /* This calls the real start function and then the newly attached task
     * preparer. No fake completion is sent to relieve the stock consumer. */
    CHECK(!ioctl(ppfd, 0x740fU) && !ioctl(ppfd, 0x7410U));
    pp_rw_dma_start();
    CHECK(describe(pp, sizeof bank, &task, 1) == -95 && !task.pixels_completed && !task.completion_generated);
    struct waitfd wait = {ppfd, 1, 0}; CHECK(poll(&wait, 1, 15) == 0 && !wait.revents);
    unsigned char event = 99; CHECK(read(ppfd, &event, 1) == -1 && *__errno_location() == 11 && event == 99);
    for (u32 i = 0; i < bytes; i++) CHECK(input[i] == 0x3c && output[i] == 0xa5);
    int saved = open("/mod/pp-memory-task-probe/task.bin", 1|01000); CHECK(saved >= 0);
    CHECK(write(saved, &task, sizeof task) == (int)sizeof task); close(saved);
    printf("PP_MEMORY_STOCK_START unsupported=%u full_input_output_unchanged=1 callback_queue_empty=1 pixels_completed=0 completion_generated=0\n", task.unsupported_stages);
    copy_bank(); bad[0x300c/4]++;
    CHECK(describe(bad, sizeof bad, &task, 1) == -22 && (task.invalid&NX_PP_INVALID_ADDRESS));
    copy_bank(); bad[0x300c/4] = 0xfffff000U;
    CHECK(describe(bad, sizeof bad, &task, 1) == -22 && (task.invalid&NX_PP_INVALID_ADDRESS));
    copy_bank(); bad[0x3014/4] = 4216;
    CHECK(describe(bad, sizeof bad, &task, 1) == -22 && (task.invalid&NX_PP_INVALID_STRIDE));
    printf("PP_MEMORY_ADDRESS unallocated=EFAULT unaligned=EINVAL overflow=EINVAL short_stride=EINVAL\n");

    /* Execute on unmodified, previously observed MnF output row bytes.
     * Each sample includes six padding bytes, which must not be decoded. */
    source = open("/mod/pp-memory-task-probe/captured-rows.bin", 0); CHECK(source >= 0);
    CHECK(!read_all(source, captured_rows, sizeof captured_rows)); close(source);
    const unsigned char *captured[4];
    u32 lengths[4] = {4224,4224,4224,4224}, strides[4] = {4224,4224,4224,4224};
    for (u32 i = 0; i < 4; i++) captured[i] = captured_rows[i];
    CHECK(!planes14(bank, captured, lengths, strides, 2812, 1,
                    captured_expanded, 4*2812));
    saved = open("/mod/pp-memory-task-probe/expanded-rows.bin", 1|01000); CHECK(saved >= 0);
    CHECK(write(saved, captured_expanded, sizeof captured_expanded) == (int)sizeof captured_expanded); close(saved);
    printf("PP_MEMORY_CAPTURED_ROWS planes=4 samples=11248 source=geometry_after_split_effective_last padding_excluded=1\n");

    /* Full observed 5624x3714 geometry, private synthetic source. This checks
     * mapping, every strided row and reserved-row preservation, not hardware. */
    u32 count = 5624U*3714U;
    unsigned short *expanded = mmap(0, count*2U, 3, 0x22, -1, 0);
    CHECK(expanded != (void *)-1);
    for (u32 i = 0; i < count; i++) expanded[i] = 0xffff;
    copy_bank(); bad[0x301c/4] = 0x80000000U;
    CHECK(input14(bad, sizeof bad, expanded, count) == -14);
    for (u32 i = 0; i < count; i++) CHECK(expanded[i] == 0xffff);
    copy_bank(); bad[0x4518/4] ^= 1;
    CHECK(input14(bad, sizeof bad, expanded, count) == -95);
    for (u32 i = 0; i < count; i++) CHECK(expanded[i] == 0xffff);
    CHECK(input14(bank, sizeof bank, (unsigned short *)input, count) == -22);
    CHECK(!input14(bank, sizeof bank, expanded, count));
    for (u32 i = 0; i < count; i++) CHECK(expanded[i] == ((i&1U) ? 3852 : 12528));
    for (u32 i = 0; i < bytes; i++) CHECK(input[i] == 0x3c && output[i] == 0xa5);
    CHECK(poll(&wait, 1, 15) == 0 && !wait.revents);
    printf("PP_MEMORY_INPUT14 width=5624 height=3714 samples=%u all_samples_checked=1 source=synthetic mapped_planes=4 destination_pp_unchanged=1 completion_generated=0\n", count);
    printf("PP_MEMORY_INPUT14_REJECT late_unmapped_plane=EFAULT nonlinear_gradient=ENOTSUP overlap=EINVAL no_partial_writes=1\n");
    close(sma); close(ppfd);
    return 0;
}
