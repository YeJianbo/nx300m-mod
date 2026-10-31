/* Check the missing original PP callback path, not just the driver's read(). */
typedef unsigned int u32;
extern int printf(const char *, ...), pp_core_open(void), open(const char *, int, ...), write(int, const void *, u32);
extern void d4_register_isr(int, void (*)(void)), d4_deregister_isr(int);
extern void *mmap(void *, u32, int, int, int, long);
extern int usleep(u32);
static volatile int callbacks;
static void on_wdma(void) { __sync_fetch_and_add(&callbacks, 1); }
#define CHECK(x) do { if (!(x)) { printf("FAIL line=%d callbacks=%d\n", __LINE__, callbacks); return 1; } } while (0)
int main(void) {
    CHECK(pp_core_open() == 0);
    int fd = open("/dev/d4_pp_core", 2), input = open("/var/run/nx300m-pp-events", 1|04000);
    CHECK(fd >= 0 && input >= 0);
    volatile u32 *pp = mmap((void *)0, 0xb000, 3, 1, fd, 0x50040000);
    CHECK(pp != (void *)-1);
    d4_register_isr(11, on_wdma);
    pp[0x24/4] |= 1U<<19;
    CHECK(write(input, "524288\n", 7) == 7);
    for (int i = 0; i < 1000 && !callbacks; i++) usleep(1000);
    printf("STOCK_PP_CALLBACKS=%d\n", callbacks);
    CHECK(callbacks == 1);
    d4_deregister_isr(11);
    return 0;
}
