/* Focused regression for the EOF spin and Samsung's two distinct read ABIs. */
typedef unsigned int u32;
extern int printf(const char *, ...), setvbuf(void *, char *, int, u32);
extern void *stdout;
extern int open(const char *, int, ...), close(int), read(int, void *, u32), write(int, const void *, u32);
extern int *__errno_location(void), eventfd(u32, int), usleep(u32);
extern void *dlsym(void *, const char *);
extern int pthread_create(unsigned long *, const void *, void *(*)(void *), void *), pthread_join(unsigned long, void **);
extern int d4_setfd(int, int);
extern void d4_register_isr(int, void (*)(void));
struct waitfd { int fd; short events, revents; };
extern int poll(struct waitfd *, u32, int);
#define CHECK(condition) do { if (!(condition)) { printf("FAIL line=%d errno=%d\n", __LINE__, *__errno_location()); return 1; } } while (0)
static u32 (*seen)(int);
static int observed(int still, u32 count) {
    for (int i = 0; i < 1000; i++) { if (seen(still) >= count) return 1; usleep(1000); }
    return 0;
}
struct operation { int fd, done, result; unsigned char bytes[12]; };
static void *read_wait(void *value) {
    struct operation *op = value;
    op->result = read(op->fd, op->bytes, 12);
    unsigned long long one = 1; write(op->done, &one, 8);
    return (void *)0;
}
static int finished(struct operation *op, int timeout) {
    struct waitfd descriptor = {op->done, 1, 0};
    return poll(&descriptor, 1, timeout);
}
static int callback_done;
static void callback(void) { unsigned long long one = 1; write(callback_done, &one, 8); }
int main(void) {
    setvbuf(stdout, (char *)0, 2, 0);
    int movie = open("/dev/d4_ipcm", 2|04000), still = open("/dev/d4_ipcs", 2|04000);
    int mi = open("/var/run/nx300m-ipcm-events", 1|04000), si = open("/var/run/nx300m-ipcs-events", 1|04000);
    seen = dlsym((void *)0, "nx300m_ipc_events_seen");
    CHECK(movie >= 0 && still >= 0 && mi >= 0 && si >= 0 && seen);
    struct operation op = {movie, eventfd(0, 04000), -99, {0}};
    for (int i = 0; i < 12; i++) op.bytes[i] = 0xa5;
    unsigned long thread;
    CHECK(pthread_create(&thread, (void *)0, read_wait, &op) == 0 && finished(&op, 25) == 0);
    CHECK(write(mi, "18\n", 3) == 3 && finished(&op, 1000) == 1);
    CHECK(pthread_join(thread, (void *)0) == 0 && op.result == 0);
    CHECK(op.bytes[0] == 18 && op.bytes[4] == 0 && op.bytes[8] == 0);
    for (int i = 0; i < 12; i++) if (i%4) CHECK(op.bytes[i] == 0xa5);
    close(op.done);
    CHECK(write(mi, "18 19 20 18\n", 12) == 12 && observed(0, 5));
    struct waitfd descriptor = {movie, 1|0x40, 0};
    CHECK(poll(&descriptor, 1, 10) == 0);
    CHECK(read(movie, op.bytes, 12) == 0 && op.bytes[0] == 18 && op.bytes[4] == 19 && op.bytes[8] == 20);
    printf("IPCM no_event_blocks_even_nonblock=1 batch_offsets=0,4,8 high_bytes_preserved=1 queue_capacity=3 poll_never_ready=1\n");

    unsigned char byte = 0xa5;
    CHECK(read(still, &byte, 1) == -1 && *__errno_location() == 11 && byte == 0xa5);
    CHECK(write(si, "23 26\n", 6) == 6 && observed(1, 2));
    descriptor = (struct waitfd){still, 1|0x40, 0};
    CHECK(poll(&descriptor, 1, 10) == 1 && descriptor.revents == (1|0x40));
    CHECK(read(still, &byte, 1) == 1 && byte == 23);
    CHECK(read(still, &byte, 1) == 0 && byte == 26 && poll(&descriptor, 1, 10) == 0);
    printf("IPCS no_event_eagain=1 poll_readiness=1 event_sequence=23,26 return_queue_depth=1,0\n");

    /* Exercise the original libudd interrupt thread, not a replacement reader. */
    callback_done = eventfd(0, 1|04000);
    CHECK(callback_done >= 0 && d4_setfd(3, movie) == 0);
    d4_register_isr(18, callback);
    descriptor = (struct waitfd){callback_done, 1, 0};
    CHECK(poll(&descriptor, 1, 25) == 0);
    CHECK(write(mi, "18\n", 3) == 3 && poll(&descriptor, 1, 1000) == 1);
    unsigned long long count;
    CHECK(read(callback_done, &count, 8) == 8);
    CHECK(poll(&descriptor, 1, 25) == 0);
    printf("STOCK_IPCM original_libudd_callback=1 no_spurious_callback=1 image_generated=0 dma_completed=0\n");
    return 0;
}
