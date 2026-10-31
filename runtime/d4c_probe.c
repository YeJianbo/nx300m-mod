/* Stock D4C command encoder, SPI configuration and transfer instructions. */
#include "spi.h"
typedef unsigned int u32;
extern int printf(const char *, ...), setvbuf(void *, char *, int, u32);
extern void *stdout;
extern int get_com_channel(int), open(const char *, int, ...), close(int), read(int, void *, u32), memcmp(const void *, const void *, u32);
extern void *dlsym(void *, const char *), *mmap(void *, u32, int, int, int, long);
extern void d4c_register_write(u32, u32);
extern u32 d4c_register_read(u32);
extern int d4c_register_burst_write(u32, const u32 *, u32);
#define CHECK(condition) do { if (!(condition)) { printf("FAIL line=%d\n", __LINE__); return 1; } } while (0)
int main(void) {
    setvbuf(stdout, (char *)0, 2, 0);
    int channel = get_com_channel(3);
    struct spi_ch_data *spi = spi_open(channel);
    CHECK(spi);
    /* Same original helper called by d4c_if_config, without its unrelated
     * GPIO initialization. Offset is verified in NX300M 1.15 libd4c 0.19. */
    void *stock_write = dlsym((void *)0, "d4c_register_write");
    CHECK(stock_write);
    void (*configure_spi)(void *) = (void (*)(void *))((u32)stock_write-0x5534+0x5c08);
    configure_spi(spi);
    printf("STOCK_D4C spi_channel=%d cur_bpw=%u set_bpw=%u\n", channel, spi->cur_bpw, spi->set_bpw);
    CHECK(channel == 1 && spi->cur_bpw == 32 && spi->set_bpw == 32);
    int fd = open("/var/run/nx300m-d4c-registers.bin", 0);
    unsigned char *mapping = mmap((void *)0, 0x42000, 1, 1, fd, 0);
    CHECK(fd >= 0 && mapping != (void *)-1);
    volatile u32 *bank = (void *)mapping;
    unsigned char *defined = mapping+0x40000;
    CHECK(!(defined[0xfffc/8]&(1U<<(0xfffc%8))));
    CHECK(d4c_register_read(0xfffc) == 0xffffffffU);
    u32 values[] = {0x12345678U, 0x00014008U, 0, 0xffffffffU};
    for (u32 i = 0; i < sizeof(values)/sizeof(values[0]); i++) {
        d4c_register_write(0x4008, values[i]);
        u32 actual = d4c_register_read(0x4008);
        CHECK(actual == values[i] && bank[0x4008] == values[i]);
        printf("STOCK_D4C register=4008 stock_readback=%08x\n", actual);
    }
    CHECK(defined[0x4008/8]&(1U<<(0x4008%8)));
    u32 payload[64];
    for (u32 i = 0; i < 64; i++) payload[i] = i ? (i*0x01010101U) : 0x00014008;
    CHECK(d4c_register_burst_write(0x8000, payload, 4) == 0);
    u32 saved[66];
    int burst = open("/var/run/nx300m-d4c-burst.bin", 0);
    CHECK(burst >= 0 && read(burst, saved, sizeof(saved)) == 24);
    CHECK(saved[0] == 0x00118000 && saved[1] == 4 && !memcmp(saved+2, payload, 16));
    close(burst);
    CHECK(!(defined[0x8000/8]&(1U<<(0x8000%8))));
    printf("STOCK_D4C register_burst_opaque_words=4 payload_matched=1 inferred_register_writes=0\n");
    CHECK(d4c_register_burst_write(0x208000, payload, 256) == 0);
    burst = open("/var/run/nx300m-d4c-burst.bin", 0);
    CHECK(burst >= 0 && read(burst, saved, sizeof(saved)) == sizeof(saved));
    CHECK(saved[0] == 0x00208001 && saved[1] == 64 && !memcmp(saved+2, payload, sizeof(payload)));
    close(burst); close(fd);
    printf("STOCK_D4C dpc_pipe_opaque_words=64 payload_matched=1 dsp_result_generated=0\n");
    return 0;
}
