/* Use unmodified libudd open/mmap/LUT calls with the ISP transport backend.
 * The MMIO target is private synthetic RAM, never a physical ISP.
 */
typedef unsigned int u32;
extern int printf(const char *, ...), setvbuf(void *, char *, int, unsigned int);
extern void *stdout;
extern int pp_core_open(void), pp_ssif_open(void), bwm_open(void), ipcm_open(void), ipcs_open(void);
extern int pp_3a_open(void);
extern int open(const char *, int, ...), close(int), ioctl(int, u32, ...), *__errno_location(void);
extern void *mmap(void *, unsigned int, int, int, int, long);
extern int memcmp(const void *, const void *, unsigned int);
extern void *dlsym(void *, const char *);
extern void ipcs_3d_lut(u32, const unsigned short *);
struct instant { long seconds, nanoseconds; };
extern int clock_gettime(int, struct instant *);
struct waitfd { int fd; short events, revents; };
extern int poll(struct waitfd *, u32, int);
static long long now(void) {
    struct instant value;
    if (clock_gettime(1, &value)) return -1;
    return (long long)value.seconds*1000000000LL+value.nanoseconds;
}
int main(void) {
    setvbuf(stdout, (char *)0, 2, 0);
    int result;
    printf("STOCK_OPEN beginning_pp_core=1\n");
    result = pp_core_open(); printf("STOCK_OPEN pp_core=%d\n", result); if (result) return 2;
    result = pp_ssif_open(); printf("STOCK_OPEN pp_ssif=%d\n", result); if (result) return 3;
    result = pp_3a_open(); printf("STOCK_OPEN pp_3a=%d\n", result); if (result) return 24;
    result = bwm_open(); printf("STOCK_OPEN bwm=%d\n", result); if (result) return 4;
    result = ipcm_open(); printf("STOCK_OPEN ipcm=%d\n", result); if (result) return 5;
    result = ipcs_open(); printf("STOCK_OPEN ipcs=%d\n", result); if (result) return 6;
    int pp = open("/dev/d4_pp_core", 2), live = open("/dev/d4_ipcm", 2), still = open("/dev/d4_ipcs", 2), csm = open("/dev/d4_csm", 2);
    if (pp < 0 || live < 0 || still < 0 || csm < 0) return 7;
    u32 info[2] = {0};
    if (ioctl(live, 0xc0086d01U, info) || info[0] != 0x50060000U || info[1] != 0x2001) return 8;
    void *alias = mmap((void *)0, info[1], 3, 1, live, info[0]);
    if (alias == (void *)-1) return 9;
    void **native = dlsym((void *)0, "ipcm_reg_top_base");
    if (!native || !*native) return 10;
    ((volatile u32 *)*native)[0x1800/4] = 0x13579bdfU;
    if (((volatile u32 *)alias)[0x1800/4] != 0x13579bdfU) return 11;
    printf("STOCK_MAP ipcm_size=%u shared_alias=1\n", info[1]);
    if (ioctl(still, 0xc0087303U, info) || info[0] != 0x50050000U || info[1] != 0x10000) return 12;
    unsigned char *view = mmap((void *)0, info[1], 3, 1, still, info[0]);
    if (view == (void *)-1) return 13;
    unsigned short table[730];
    for (u32 channel = 0; channel < 3; channel++) {
        for (u32 i = 0; i < 729; i++) table[i] = (i+channel*131)&1023;
        table[729] = 0;
        ipcs_3d_lut(channel, table);
        if (memcmp(view+0x9000+channel*0x700, table, 1460)) return 14+channel;
        printf("STOCK_LUT mapped_channel=%u matched=1\n", channel);
    }
    u32 enabled[4] = {1, 0, 1, 0};
    if (ioctl(still, 0x4010730aU, enabled) || ((volatile u32 *)view)[2] != ((1U<<0)|(1U<<6))) return 17;
    if (ioctl(still, 0x4010730bU, enabled) || ((volatile u32 *)view)[2]) return 18;
    printf("STOCK_IRQ subscription_register_roundtrip=1\n");
    int timeout = 7;
    long long start = now();
    if (ioctl(still, 0xc004730cU, &timeout) || timeout != -1 || now()-start < 7000000LL) return 19;
    printf("STOCK_TIMEOUT completion_result=%d elapsed_ns=%lld\n", timeout, now()-start);
    struct waitfd p = {pp, 1, 0};
    if (poll(&p, 1, 10) != 0 || p.revents) return 20;
    printf("STOCK_POLL no_synthetic_irq=1\n");
    if (mmap((void *)0, 4096, 3, 1, still, 0x50060000U) != (void *)-1 || *__errno_location() != 22) return 21;
    if (ioctl(csm, 0xc0086303U, info) || info[0] || info[1]) return 22;
    printf("STOCK_CSM wdma_error=%u wdma_done=%u\n", info[0], info[1]);
    if (ioctl(still, 0x73ffU) != -1 || *__errno_location() != 25) return 23;
    close(csm); close(still); close(live); close(pp);
    return 0;
}
