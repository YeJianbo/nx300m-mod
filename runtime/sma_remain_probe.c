/* The real stock mmap-manager calls the repaired SMA ioctl, in isolation. */
typedef unsigned int u32;
extern int printf(const char *, ...), open(const char *, int, ...), close(int);
extern int ioctl(int, u32, ...), mprotect(void *, u32, int), *__errno_location(void);
extern void *mmap(void *, u32, int, int, int, long);
extern int sysmmap_osif_init(void), sysmmap_osif_open(void);
extern int sysmmap_osif_update_remain_buf(u32, u32);
struct info { u32 addr, size; };
#define CHECK(x) do { if (!(x)) { printf("FAIL line=%d errno=%d\n", __LINE__, *__errno_location()); return 1; } } while (0)
int main(void) {
    int fd = open("/dev/d4_sma", 2); CHECK(fd >= 0);
    u32 initial = 99, allocated = 99;
    CHECK(!ioctl(fd, 0x80047308U, &initial));
    CHECK(initial == 0);
    struct info buffer = {0, 4096};
    CHECK(!ioctl(fd, 0xc0087305U, &buffer));
    CHECK(buffer.addr == 0xc6800000U);
    CHECK(!ioctl(fd, 0x80047308U, &allocated) && allocated == 4096);
    unsigned char *argument = mmap(0, 8192, 3, 0x22, -1, 0);
    CHECK(argument != (void *)-1);
    struct info *read_only = (struct info *)argument;
    read_only->addr = 0xc6900000U; read_only->size = 8192;
    CHECK(!mprotect(argument, 4096, 1));
    CHECK(!ioctl(fd, 0xc008730aU, read_only));
    CHECK(read_only->addr == 0xc6900000U && read_only->size == 8192);
    u32 borrowed = 0xc6902000U; /* Original setter's top = addr+size. */
    CHECK(!ioctl(fd, 0xc0047307U, &borrowed));
    CHECK(!ioctl(fd, 0x80047308U, &allocated) && allocated == 4096);
    CHECK(ioctl(fd, 0xc008730aU, (void *)0) == -1 && *__errno_location() == 14);
    CHECK(ioctl(fd, 0xc008730aU, (void *)1) == -1 && *__errno_location() == 14);
    CHECK(!mprotect(argument+4096, 4096, 0));
    CHECK(ioctl(fd, 0xc008730aU, argument+4092) == -1 && *__errno_location() == 14);
    /* Failed pointer copies must leave the previous remain state intact. */
    CHECK(!ioctl(fd, 0xc0047307U, &borrowed));
    struct info clear = {0, 123};
    CHECK(!ioctl(fd, 0xc008730aU, &clear));
    CHECK(clear.addr == 0 && clear.size == 123);
    CHECK(ioctl(fd, 0xc0047307U, &borrowed) == -1 && *__errno_location() == 22);
    CHECK(!sysmmap_osif_init());
    CHECK(!sysmmap_osif_open());
    CHECK(!sysmmap_osif_update_remain_buf(0xc6a00000U, 16384));
    borrowed = 0xc6a04000U;
    CHECK(!ioctl(fd, 0xc0047307U, &borrowed));
    CHECK(!sysmmap_osif_update_remain_buf(0, 0));
    CHECK(ioctl(fd, 0xc0047307U, &borrowed) == -1 && *__errno_location() == 22);
    CHECK(!ioctl(fd, 0x80047308U, &allocated) && allocated == 4096);
    CHECK(!ioctl(fd, 0xc0047307U, &buffer.addr));
    CHECK(!ioctl(fd, 0x80047308U, &allocated) && allocated == initial);
    close(fd);
    printf("SMA_REMAIN stock_set_clear=ok readonly_input=ok invalid_pointer=EFAULT state_preserved=ok cma_accounting=ok\n");
    return 0;
}
