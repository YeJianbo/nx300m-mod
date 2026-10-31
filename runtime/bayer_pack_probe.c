/* Synthetic image descriptor; packed bytes come from an actual SRW fixture.
 * All descriptor fields below are verified stock getter offsets. */
typedef unsigned int u32;
extern int printf(const char *, ...), open(const char *, int, ...), close(int), read(int, void *, u32);
extern void *mmap(void *, u32, int, int, int, long);
extern unsigned long strtoul(const char *, char **, int);
extern int stock_pixel(void *, u32, u32, unsigned short *, unsigned short *, unsigned short *, unsigned short *)
    __asm__("_Z32dbg_get_pixelValue_fromPackBayerP9CCapImagejjRtS1_S1_S1_");
#define CHECK(x) do { if (!(x)) { printf("FAIL line=%d\n", __LINE__); return 1; } } while (0)
int main(int argc, char **argv) {
    CHECK(argc == 8);
    int fd = open(argv[1], 0);
    CHECK(fd >= 0);
    u32 header[16] = {0}; CHECK(read(fd, header, sizeof header) == sizeof header);
    CHECK(header[0] == 0x3252584e && header[1] == 1 && header[5] == 12);
    void *memory = mmap((void *)0, 64+header[3]*header[4], 1, 2, fd, 0);
    CHECK(memory != (void *)-1); close(fd);
    u32 image[64] = {0};
    image[0x24/4] = image[0x28/4] = 2;
    image[0x38/4] = header[4];
    image[0x58/4] = 2; image[0x6c/4] = (u32)memory+64;
    u32 x = strtoul(argv[2], (char **)0, 10), y = strtoul(argv[3], (char **)0, 10);
    CHECK(!(x&1) && x+1 < header[2] && y+1 < header[3]);
    unsigned short a=0, b=0, c=0, d=0;
    CHECK(stock_pixel(image, x, y, &a, &b, &c, &d) == 0);
    printf("STOCK_PACKED_BAYER x=%u y=%u samples=%u,%u,%u,%u\n", x, y, a, b, c, d);
    CHECK(a == strtoul(argv[4], (char **)0, 10) && b == strtoul(argv[5], (char **)0, 10));
    CHECK(c == strtoul(argv[6], (char **)0, 10) && d == strtoul(argv[7], (char **)0, 10));
    return 0;
}
