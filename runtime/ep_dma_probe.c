/* Exercise the missing EP DMA queue ABI through original libudd calls.
 * No pixel job or interrupt is supplied: waits must time out, never succeed. */
typedef unsigned int u32;
extern int printf(const char *, ...), ep_open(void), open(const char *, int, ...);
extern int ioctl(int, u32, ...), close(int), *__errno_location(void);
extern int ep_udd_dma_intr_init_wait_queue(void *), ep_udd_dma_intr_wait(void *);
struct wait_info { int timeout_ms, intr; };
struct instant { long seconds, nanoseconds; };
extern int clock_gettime(int, struct instant *);
static long long now(void) {
    struct instant t;
    if (clock_gettime(1, &t)) return -1;
    return (long long)t.seconds*1000000000LL+t.nanoseconds;
}
int main(void) {
    if (ep_open() < 0) return 1;
    struct wait_info info = {11, 5}; /* MnF WDMA bus end */
    if (ep_udd_dma_intr_init_wait_queue(&info)) return 2;
    long long start = now();
    int result = ep_udd_dma_intr_wait(&info), saved = *__errno_location();
    long long elapsed = now()-start;
    printf("EP_DMA no_job result=%d errno=%d elapsed_ns=%lld\n", result, saved, elapsed);
    /* Original libudd maps a failed ioctl to its own -2 status. */
    if (result != -2 || saved != 110 || elapsed < 14000000LL || elapsed > 1000000000LL) return 3;
    int fd = open("/dev/drime4_ep", 2);
    if (fd < 0) return 4;
    info.intr = 18;
    if (ioctl(fd, 0x40086809U, &info) != -1 || *__errno_location() != 22) return 5;
    info.intr = 5; info.timeout_ms = -1;
    if (ioctl(fd, 0x40086808U, &info) != -1 || *__errno_location() != 22) return 6;
    close(fd);
    fd = open("/dev/drime4_ep", 2|04000);
    if (fd < 0) return 7;
    info.timeout_ms = 11;
    if (ioctl(fd, 0x40086809U, &info) != -1 || *__errno_location() != 15) return 8;
    if (ioctl(fd, 0x40086808U, &info) != -1 || *__errno_location() != 15) return 9;
    close(fd);
    printf("EP_DMA invalid_intr=EINVAL negative_timeout=EINVAL nonblock=ENOTBLK pixels_generated=0\n");
    return 0;
}
