/* Original libudd configuration/start/wait with a recorded full-size mosaic.
 * No sensor, PP, IPCS or JPEG operation is supplied by this isolated probe. */
typedef unsigned int u32;
extern int printf(const char *, ...), open(const char *, int, ...), close(int);
extern int read(int, void *, u32), write(int, const void *, u32), *__errno_location(void);
extern void *mmap(void *, u32, int, int, int, long);
extern int ep_open(void), ep_udd_path_open(void *), ep_udd_mnf_bayer_one_channel_setup(void *);
extern int ep_udd_path_block_start(void *, int), ep_udd_intr_wait(void *);
#define CHECK(x) do { if (!(x)) { printf("FAIL line=%d errno=%d\n", __LINE__, *__errno_location()); return 1; } } while (0)
static int read_all(int fd, void *target, u32 bytes) {
    unsigned char *p = target;
    while (bytes) { int n = read(fd, p, bytes); if (n <= 0) return -1; bytes -= n; p += n; }
    return 0;
}
int main(void) {
    int fd = open("/mod/ep-mnf-probe/packet.bin", 0); CHECK(fd >= 0);
    u32 header[16]; CHECK(!read_all(fd, header, sizeof header));
    CHECK(header[0] == 0x3252584e && header[2] == 5624 && header[3] == 3714 && header[4] == 8448);
    u32 bytes = header[3]*header[4], gap = bytes/4;
    int memory = open("/dev/d4_sma", 2); CHECK(memory >= 0);
    unsigned char *input = mmap(0, bytes, 3, 1, memory, (long)0xc6800000U);
    unsigned char *output = mmap(0, bytes, 3, 1, memory, (long)0xc9000000U);
    CHECK(input != (void *)-1 && output != (void *)-1);
    CHECK(!read_all(fd, input, bytes)); close(fd);
    for (u32 i = 0; i < bytes; i++) output[i] = 0xa5;
    CHECK(ep_open() >= 0);
    u32 path[14] = {8,0,1,44,1,0,1,1,0,20,0,0,0,0};
    int id = ep_udd_path_open(path); CHECK(id >= 0);
    u32 params[0x29c/4] = {0};
    params[1] = 20; params[5] = 128;
    params[8] = 0xc6800000U; params[9] = params[8]+8448;
    params[12] = params[13] = 8448; params[14] = 2;
    params[15] = 3696; params[16] = 5624; params[17] = 1;
    for (int i = 0; i < 4; i++) params[38+i] = 0xc9000000U+i*gap;
    params[42] = params[43] = 8448; params[44] = 2;
    params[45] = 3696; params[46] = 5624; params[47] = 1;
    params[0x294/4] = params[0x298/4] = 0x123;
    CHECK(ep_udd_mnf_bayer_one_channel_setup(params) == 0);
    int start[6] = {id, -1, 2000, 10, 2000, 5};
    int result = ep_udd_path_block_start(start, 1);
    printf("EP_MNF stock_block_start result=%d errno=%d bytes=%u plane_gap=%u\n", result, *__errno_location(), bytes, gap);
    CHECK(result >= 0);
    fd = open("/mod/ep-mnf-probe/planes.bin", 1|01000); CHECK(fd >= 0);
    CHECK(write(fd, output, bytes) == (int)bytes); close(fd);
    /* Consuming a completed frame must not leave another success pending. */
    int wait[2] = {11, 10};
    result = ep_udd_intr_wait(wait);
    printf("EP_MNF no_second_start result=%d errno=%d\n", result, *__errno_location());
    CHECK(result < 0 && *__errno_location() == 110);
    int ep = open("/dev/drime4_ep", 2); CHECK(ep >= 0);
    volatile u32 *mnf = mmap(0, 4096, 3, 1, ep, (long)0x92004000U);
    CHECK(mnf != (void *)-1);
    mnf[0] |= 0x10000; /* Unsupported color-effect enable must not complete. */
    start[2] = start[4] = 11;
    result = ep_udd_path_block_start(start, 1);
    printf("EP_MNF unsupported_color result=%d errno=%d\n", result, *__errno_location());
    CHECK(result < 0 && *__errno_location() == 110);
    return 0;
}
