/* Stock ARM PP mappings/waits against explicitly supplied IRQ snapshots. */
typedef unsigned int u32;
extern int printf(const char *, ...), setvbuf(void *, char *, int, u32);
extern void *stdout;
extern int pp_core_dd_open_reg_base(int), pp_dd_wait_wdma_done(void);
extern int pp_core_dd_com_wait_lut_load_done_with_timeout(u32);
extern int open(const char *, int, ...), close(int), ioctl(int, u32, ...), read(int, void *, u32), write(int, const void *, u32);
extern int *__errno_location(void), eventfd(u32, int), usleep(u32);
extern void *mmap(void *, u32, int, int, int, long), *dlsym(void *, const char *);
extern int pthread_create(unsigned long *, const void *, void *(*)(void *), void *), pthread_join(unsigned long, void **);
struct instant { long seconds, nanoseconds; };
extern int clock_gettime(int, struct instant *);
struct waitfd { int fd; short events, revents; };
extern int poll(struct waitfd *, u32, int);
#define CHECK(condition) do { if (!(condition)) { printf("FAIL line=%d errno=%d\n", __LINE__, *__errno_location()); return 1; } } while (0)
static long long now(void) {
    struct instant value; clock_gettime(1, &value);
    return (long long)value.seconds*1000000000LL+value.nanoseconds;
}
struct operation { int mode, done, result; };
static void *stock_wait(void *value) {
    struct operation *operation = value;
    operation->result = operation->mode ? pp_core_dd_com_wait_lut_load_done_with_timeout(1000) : pp_dd_wait_wdma_done();
    unsigned long long one = 1; write(operation->done, &one, 8);
    return (void *)0;
}
static int finished(struct operation *operation, int timeout) {
    struct waitfd descriptor = {operation->done, 1, 0};
    return poll(&descriptor, 1, timeout);
}
static u32 (*seen)(void);
static int event_seen(u32 count) {
    long long deadline = now()+1000000000LL;
    do { if (seen() >= count) return 1; usleep(1000); } while (now() < deadline);
    return 0;
}
static int queue_value(int fd, unsigned char expected) {
    struct waitfd descriptor = {fd, 1|0x40, 0};
    unsigned char value = 99;
    return poll(&descriptor, 1, 1000) == 1 && descriptor.revents == (1|0x40) && read(fd, &value, 1) == 0 && value == expected;
}
int main(void) {
    setvbuf(stdout, (char *)0, 2, 0);
    int fd = open("/dev/d4_pp_core", 2|04000), csm = open("/dev/d4_csm", 2);
    CHECK(fd >= 0 && csm >= 0 && pp_core_dd_open_reg_base(fd) == 0 && ioctl(fd, 0x7401U) == 0);
    volatile u32 *pp = mmap((void *)0, 0xb000, 3, 1, fd, 0x50040000);
    CHECK(pp != (void *)-1);
    int input = open("/var/run/nx300m-pp-events", 1|04000);
    seen = dlsym((void *)0, "nx300m_pp_events_seen");
    CHECK(input >= 0 && seen);
    unsigned char byte = 99;
    CHECK(read(fd, &byte, 1) == -1 && *__errno_location() == 11 && byte == 99);
    u32 timeout = 7;
    long long start = now();
    CHECK(pp_core_dd_com_wait_lut_load_done_with_timeout(timeout) == -1 && now()-start >= 10000000LL);
    printf("STOCK_PP no_event_lut_timeout=1 elapsed_ns=%lld\n", now()-start);
    struct operation operation = {0, eventfd(0, 04000), -99};
    unsigned long thread;
    CHECK(operation.done >= 0 && pthread_create(&thread, (void *)0, stock_wait, &operation) == 0);
    CHECK(finished(&operation, 15) == 0);
    pp[0x24/4] = 0; u32 count = seen()+1;
    CHECK(write(input, "524288\n", 7) == 7 && event_seen(count) && finished(&operation, 15) == 0);
    pp[0x24/4] = (1U<<19)|(1U<<22); pp[0x3314/4] = 0xf0000055U;
    CHECK(write(input, "524288\n", 7) == 7 && finished(&operation, 1000) == 1);
    CHECK(pthread_join(thread, (void *)0) == 0 && operation.result == 0);
    close(operation.done);
    CHECK(queue_value(fd, 11) && pp[0x3314/4] == 0xe0000055U && pp[0x24/4] == (1U<<22));
    u32 state[2] = {99,99};
    CHECK(ioctl(csm, 0xc0086303U, state) == 0 && state[0] == 0 && state[1] == 1);
    printf("STOCK_PP masked_wdma_kept_waiting=1 explicit_wdma_released=1 queue=11 csm_done=1\n");

    CHECK(ioctl(fd, 0x7413U) == 0);
    operation = (struct operation){1, eventfd(0, 04000), -99};
    CHECK(pthread_create(&thread, (void *)0, stock_wait, &operation) == 0);
    CHECK(finished(&operation, 15) == 0);
    pp[0x24/4] = (1U<<19)|1; count = seen()+1;
    CHECK(write(input, "524288\n", 7) == 7 && event_seen(count) && finished(&operation, 15) == 0);
    CHECK(queue_value(fd, 11));
    CHECK(write(input, "1\n", 2) == 2 && finished(&operation, 1000) == 1);
    CHECK(pthread_join(thread, (void *)0) == 0 && operation.result == 0);
    close(operation.done);
    CHECK(ioctl(fd, 0x7413U) == 0 && pp_core_dd_com_wait_lut_load_done_with_timeout(1) == -1);
    printf("STOCK_PP wdma_did_not_release_lut=1 explicit_lut_released=1 init_cleared_completion=1\n");

    pp[0x24/4] = 1U<<23;
    CHECK(ioctl(fd, 0x7427U) == 0 && write(input, "8388608\n", 8) == 8 && queue_value(fd, 13));
    CHECK(pp[0x24/4] == (1U<<23));
    CHECK(ioctl(fd, 0x7428U) == 0); count = seen()+1;
    CHECK(write(input, "8388608\n", 8) == 8 && event_seen(count));
    struct waitfd descriptor = {fd, 1, 0};
    CHECK(poll(&descriptor, 1, 10) == 0);
    printf("STOCK_PP mipi_rx_subscribed_queue=13 clear_unsubscribed=1 enable_preserved=1\n");
    pp[0x24/4] = 1U<<21; count = seen()+1;
    CHECK(write(input, "2097152\n", 8) == 8 && event_seen(count));
    CHECK(ioctl(csm, 0xc0086303U, state) == 0 && state[0] == 1 && state[1] == 1);
    CHECK(pp[0x20/4] == 0);
    printf("STOCK_PP csm_error_irq_recorded=1 image_generated=0 real_dma_performed=0\n");
    return 0;
}
