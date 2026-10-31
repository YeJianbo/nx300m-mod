/* Exercise the released CSM ABI and stock SPI instructions with explicit edges. */
#include "spi.h"
#include "d4_csm_type.h"
typedef unsigned int u32;
extern int printf(const char *, ...), setvbuf(void *, char *, int, unsigned int);
extern void *stdout;
extern int pp_core_open(void), open(const char *, int, ...), close(int), ioctl(int, u32, ...);
extern int read(int, void *, u32), write(int, const void *, u32), *__errno_location(void);
extern void *mmap(void *, u32, int, int, int, long), *dlsym(void *, const char *);
extern int pthread_create(unsigned long *, const void *, void *(*)(void *), void *), pthread_join(unsigned long, void **);
extern int eventfd(u32, int), usleep(u32);
struct instant { long seconds, nanoseconds; };
extern int clock_gettime(int, struct instant *);
struct waitfd { int fd; short events, revents; };
extern int poll(struct waitfd *, u32, int);
#define CHECK(condition) do { if (!(condition)) { printf("FAIL line=%d errno=%d\n", __LINE__, *__errno_location()); return 1; } } while (0)
static long long now(void) {
    struct instant value; clock_gettime(1, &value);
    return (long long)value.seconds*1000000000LL+value.nanoseconds;
}
struct operation { int fd; u32 command; void *argument; int result, error, done; };
static void *call_ioctl(void *value) {
    struct operation *operation = value;
    operation->result = ioctl(operation->fd, operation->command, operation->argument);
    operation->error = *__errno_location();
    unsigned long long one = 1; write(operation->done, &one, 8);
    return (void *)0;
}
static int (*pending)(void), (*waiting)(u32);
static u32 (*events_seen)(void);
static int await_condition(int kind, u32 value) {
    long long end = now()+1000000000LL;
    do {
        if ((kind == 0 && pending() == (int)value) || (kind == 1 && waiting(value)) ||
            (kind == 2 && events_seen() >= value)) return 1;
        usleep(1000);
    } while (now() < end);
    return 0;
}
static int finished(struct operation *operation, int timeout) {
    struct waitfd descriptor = {operation->done, 1, 0};
    return poll(&descriptor, 1, timeout);
}
int main(void) {
    setvbuf(stdout, (char *)0, 2, 0);
    CHECK(pp_core_open() == 0);
    int ppfd = open("/dev/d4_pp_core", 2), ssif = open("/dev/d4_pp_ssif", 2|04000), csm = open("/dev/d4_csm", 2);
    CHECK(ppfd >= 0 && ssif >= 0 && csm >= 0 && ioctl(ssif, 0x7301U) == 0);
    volatile u32 *pp = mmap((void *)0, 0xb000, 3, 1, ppfd, 0x50040000);
    CHECK(pp != (void *)-1);
    struct spi_ch_data *spi = spi_open(SPI_CH5);
    CHECK(spi);
    struct hs_spi_config config = {1000000, 32, 0, 5, HS_SPI_TRAN_BURST_ON, HS_SPI_MODE_3, {0, 0, 0}, SPI_ALL};
    spi_config(spi, &config);
    u32 seed = 0x700c021a;
    struct spi_tran_data transfer = {&seed, (void *)0, 1};
    CHECK(spi_polling_write(spi, &transfer) == 0);
    int bankfd = open("/var/run/nx300m-sensor-registers.bin", 0);
    volatile unsigned short *bank = mmap((void *)0, 0x10000, 1, 1, bankfd, 0);
    CHECK(bankfd >= 0 && bank != (void *)-1 && bank[0x700c] == 0x021a);
    pending = dlsym((void *)0, "nx300m_csm_pending");
    waiting = dlsym((void *)0, "nx300m_ssif_waiting");
    events_seen = dlsym((void *)0, "nx300m_ssif_events_seen");
    CHECK(pending && waiting && events_seen);
    int input = open("/var/run/nx300m-ssif-events", 1|04000);
    CHECK(input >= 0);
    unsigned char byte = 99;
    CHECK(read(ssif, &byte, 1) == -1 && *__errno_location() == 11 && byte == 99);
    CHECK(write(input, "12\n", 3) == 3);
    struct waitfd descriptor = {ssif, 1|0x40, 0};
    CHECK(poll(&descriptor, 1, 1000) == 1 && descriptor.revents == (1|0x40));
    CHECK(read(ssif, &byte, 1) == 0 && byte == 12);
    CHECK(poll(&descriptor, 1, 0) == 0);
    printf("SSIF_QUEUE one_byte=12 return_remaining=0 nonblocking_empty=EAGAIN\n");

    u32 selection = 2;
    struct operation operation = {ssif, 0x4004730aU, &selection, -99, 0, eventfd(0, 04000)};
    CHECK(operation.done >= 0);
    unsigned long thread;
    pp[0x2440/4] |= 1U<<selection;
    CHECK(pthread_create(&thread, (void *)0, call_ioctl, &operation) == 0 && await_condition(1, selection));
    u32 seen = events_seen()+1;
    CHECK(write(input, "2\n", 2) == 2 && await_condition(2, seen));
    CHECK(finished(&operation, 15) == 0);
    pp[0x2440/4] &= ~(1U<<selection);
    CHECK(write(input, "2\n", 2) == 2 && finished(&operation, 1000) == 1);
    CHECK(pthread_join(thread, (void *)0) == 0 && operation.result == 0);
    close(operation.done);
    printf("SSIF_WAIT masked_edge_kept_waiting=1 unmasked_explicit_edge_released=1\n");

    struct csm_chg_wait_vd_mode wait_command = {1, {{5, 0x700c, 0x021b}, {5, 0x702c, 0x0285}}};
    long long start = now();
    CHECK(ioctl(csm, 0xc01c6301U, &wait_command) == 0 && now()-start >= 1000000000LL);
    CHECK(bank[0x700c] == 0x021a);
    printf("CSM_WAIT_VD no_event_timeout_ignored=1 no_spi_write=1 elapsed_ns=%lld\n", now()-start);
    operation = (struct operation){csm, 0xc01c6301U, &wait_command, -99, 0, eventfd(0, 04000)};
    CHECK(pthread_create(&thread, (void *)0, call_ioctl, &operation) == 0 && await_condition(0, 1));
    CHECK(finished(&operation, 15) == 0 && write(input, "1\n", 2) == 2 && finished(&operation, 1000) == 1);
    CHECK(pthread_join(thread, (void *)0) == 0 && operation.result == 0);
    close(operation.done);
    CHECK(bank[0x700c] == 0x021b && bank[0x702c] == 0x0285);
    printf("CSM_WAIT_VD explicit_edge_stock_spi_readback=021b/0285\n");

    struct csm_chg_read_out_mode read_command = {0};
    read_command.ref_vsync = 1;
    read_command.sensor_spi_data[0] = (struct csm_spi_data){5, 0x700c, 0x021c};
    read_command.sensor_spi_data[1] = (struct csm_spi_data){5, 0x702c, 0x0286};
    operation = (struct operation){csm, 0xc0a46302U, &read_command, -99, 0, eventfd(0, 04000)};
    pp[0x3a10/4] = 0x10; pp[0x3a04/4] = 0xffffffffU; pp[0x3314/4] = 0xe0000055U;
    CHECK(pthread_create(&thread, (void *)0, call_ioctl, &operation) == 0 && await_condition(0, 2));
    CHECK(write(input, "1\n", 2) == 2 && finished(&operation, 1000) == 1);
    CHECK(pthread_join(thread, (void *)0) == 0 && operation.result == 0);
    close(operation.done);
    CHECK(bank[0x700c] == 0x021c && bank[0x702c] == 0x0286);
    CHECK(pp[0x3a10/4] == 0x12 && pp[0x3a04/4] == 0 && pp[0x3314/4] == 0x90000055U);
    CHECK(pp[0x28/4] == ((1U<<19)|(1U<<21)) && (pp[0x24/4]&((1U<<19)|(1U<<21))) == ((1U<<19)|(1U<<21)));
    printf("CSM_READOUT explicit_edge_stock_spi_readback=021c/0286 wdma_armed=1 no_completion=1\n");
    read_command.sensor_spi_data[0].data = 0x021d;
    read_command.sensor_spi_data[1].data = 0x0287;
    start = now();
    CHECK(ioctl(csm, 0xc0a46302U, &read_command) == -1 && *__errno_location() == 1 && now()-start >= 5000000000LL);
    CHECK(bank[0x700c] == 0x021d && bank[0x702c] == 0x0287);
    printf("CSM_READOUT no_event_timeout_fallback=EPERM stock_spi_readback=021d/0287 elapsed_ns=%lld\n", now()-start);
    u32 status[2] = {99, 99};
    CHECK(ioctl(csm, 0xc0086303U, status) == 0 && status[0] == 0 && status[1] == 0);
    CHECK(pp[0x20/4] == 0);
    printf("CSM_RESULT image_generated=0 dma_completion_generated=0\n");
    return 0;
}
