/* Run stock libudd MIPI initialization against the private driver transport. */
#include "mipi.h"
typedef unsigned int u32;
extern int printf(const char *, ...), setvbuf(void *, char *, int, unsigned int);
extern void *stdout;
extern int pp_core_open(void), pp_get_virtual_reg_base(void **);
extern int open(const char *, int, ...), close(int), ioctl(int, u32, ...);
extern int read(int, void *, unsigned int), write(int, const void *, unsigned int);
extern int *__errno_location(void);
extern void *mmap(void *, unsigned int, int, int, int, long);
extern void *dlsym(void *, const char *);
struct instant { long seconds, nanoseconds; };
extern int clock_gettime(int, struct instant *);
static long long now(void) {
    struct instant value;
    if (clock_gettime(1, &value)) return -1;
    return (long long)value.seconds*1000000000LL+value.nanoseconds;
}
#define CHECK(condition) do { if (!(condition)) { printf("FAIL line=%d errno=%d\n", __LINE__, *__errno_location()); return 1; } } while (0)
int main(void) {
    setvbuf(stdout, (char *)0, 2, 0);
    CHECK(pp_core_open() == 0);
    CHECK(mipi_open() == 0);
    void *pp = 0;
    CHECK(pp_get_virtual_reg_base(&pp) == 0 && pp);
    const char *names[3] = {"mipi_con_base_addr", "mipi_csim_base_addr", "mipi_csis_base_addr"};
    for (int i = 0; i < 3; i++) {
        void **symbol = dlsym((void *)0, names[i]);
        CHECK(symbol && *symbol == (unsigned char *)pp+0x8000+i*0x1000);
        printf("STOCK_MIPI base=%s pp_offset=0x%x\n", names[i], 0x8000+i*0x1000);
    }
    int ppfd = open("/dev/d4_pp_core", 2), fd = open("/dev/d4_mipi", 2);
    CHECK(ppfd >= 0 && fd >= 0);
    volatile u32 *view = mmap((void *)0, 0xb000, 3, 1, ppfd, 0x50040000);
    CHECK(view != (void *)-1);
    u32 address = 0;
    CHECK(ioctl(fd, 0x80046d00U, &address) == 1 && address == 0x50048000U);
    CHECK(mmap((void *)0, 4096, 3, 1, fd, 0x50048000) == (void *)-1 && *__errno_location() == 19);
    CHECK(mipi_device_enable(MIPI_CSIS_ONLY) == MIPI_SUCCESS);
    struct MIPI_CSIS_Params param = {MIPI_27M_594M, MIPI_IMG_FORMAT_RAW10, MIPI_LANE_2, 720, 1280, 0};
    int result = mipi_csis_use(param);
    printf("STOCK_MIPI csis_use=%d con=0x%08x ctrl=0x%08x phy=0x%08x config=0x%08x resolution=0x%08x mask=0x%08x\n",
           result, view[0x8000/4], view[0xa000/4], view[0xa004/4], view[0xa008/4], view[0xa02c/4], view[0xa010/4]);
    CHECK(result == MIPI_SUCCESS);
    CHECK(view[0xa02c/4] == ((1280U<<16)|720U) && (view[0xa000/4]&1));
    mipi_csis_img_update(MIPI_IMG_FORMAT_RAW12, 600, 800);
    CHECK(view[0xa02c/4] == ((800U<<16)|600U));
    printf("STOCK_MIPI stock_resolution_update=1 shared_pp_alias=1\n");
    for (u32 command = 0x6d01; command <= 0x6d06; command++) {
        CHECK(ioctl(fd, command) == -1 && *__errno_location() == 1);
    }
    for (int receiver = 0; receiver < 2; receiver++) {
        u32 offset = receiver ? 0xa010 : 0x9030;
        u32 bits = receiver ? 0xf000103fU : 0xc1000003U;
        view[offset/4] = 0xffffffffU;
        int status = -99;
        CHECK(ioctl(fd, 0x80046d07U+receiver, &status) == 0 && status == 0);
        CHECK(view[offset/4] == ~bits);
        CHECK(ioctl(fd, 0x80046d09U+receiver, &status) == 0 && status == 0);
        CHECK(view[offset/4] == 0xffffffffU);
        u32 timeout = 2;
        long long start = now();
        CHECK(ioctl(fd, 0x80046d0bU+receiver, &timeout) == 0 && timeout == 2);
        long long elapsed = now()-start;
        CHECK(elapsed >= 10000000LL);
        printf("STOCK_MIPI receiver=%d masks_roundtrip=1 no_event_timeout_jiffies=%u elapsed_ns=%lld\n", receiver, timeout, elapsed);
    }
    char value = 42;
    CHECK(read(fd, &value, 1) == 0 && value == 42 && write(fd, &value, 1) == 0);
    u32 unsupported[16] = {0};
    CHECK(ioctl(fd, 0x80046d0dU, unsupported) == -1 && *__errno_location() == 95);
    printf("STOCK_MIPI io_quirks=1 pdma_unimplemented=1 frame_generated=0\n");
    close(fd); close(ppfd);
    return 0;
}
