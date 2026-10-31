/* Real libudd configuration and polling, isolated from the active camera. */
#include "spi.h"
extern int printf(const char *, ...), ioctl(int, unsigned int, ...), *__errno_location(void);
extern void *gpio_open(unsigned int);
extern unsigned int gpio_data_get(void *, unsigned int);
extern const char *getenv(const char *);
extern int strcmp(const char *, const char *), memcmp(const void *, const void *, unsigned int);
static int pin(unsigned int number) {
    void *bank = gpio_open(number/8);
    return bank ? (int)gpio_data_get(bank, number%8) : -1;
}
int main(void) {
    struct spi_ch_data *spi = spi_open(SPI_CH2);
    if (!spi) return 2;
    struct hs_spi_config config = {1000000, 16, 0, 5, HS_SPI_TRAN_BURST_ON,
                                  HS_SPI_MODE_3, {0, 0, 0}, SPI_ALL};
    spi_config(spi, &config);
    volatile unsigned int *reg = (void *)spi->vaddr;
    printf("STOCK_SPI_CONFIG bpw=%u set_bpw=%u wait_loops=%u original_ms=%u cfg=%08x fifo=%08x sig=%08x\n",
        spi->cur_bpw, spi->set_bpw, spi->waittime, spi->org_time, reg[0], reg[2], reg[3]);
    if (spi->cur_bpw != 16 || spi->set_bpw != 16 || spi->waittime != 1000 || spi->org_time != 5) return 3;
    if ((reg[0] & 0x3c) != 0xc || (reg[2] & 0x1f00000) != 0x500000) return 4;
    if (pin(80) != 1 || pin(83) != 1) return 5;
    unsigned int burst = 1;
    if (ioctl(spi->fd, 0x4004680fU, &burst) || pin(83) != 0) return 6;
    burst = 0;
    if (ioctl(spi->fd, 0x4004680fU, &burst) || pin(83) != 1) return 7;
    printf("STOCK_SPI_BURST asserted_cs=0 released_cs=1\n");
    const char *peer = getenv("NX300M_SPI_PEER");
    if (peer && !strcmp(peer, "loopback")) {
        unsigned int tx[4] = {0x12121212, 0x12121212, 0xa5a5a5a5, 0x5a5a5a5a};
        for (unsigned int bits = 8; bits <= 32; bits *= 2) {
            unsigned int rx[4] = {0};
            config.bpw = bits;
            spi_config(spi, &config);
            struct spi_tran_data transfer = {tx, rx, 4};
            int result = spi_polling_rw(spi, &transfer);
            printf("STOCK_SPI_LOOPBACK bits=%u frames=4 result=%d rx=%08x/%08x/%08x/%08x\n",
                bits, result, rx[0], rx[1], rx[2], rx[3]);
            if (result || memcmp(tx, rx, 4*(bits/8))) return 10+bits;
        }
    } else {
        unsigned short value = 0xa55a;
        struct spi_tran_data transfer = {0, &value, 1};
        int result = spi_polling_read(spi, &transfer);
        printf("STOCK_SPI_NO_RX result=%d buffer=%04x status=%08x\n", result, value, reg[5]);
        if (result >= 0 || value != 0xa55a) return 8;
    }
    if (ioctl(spi->fd, 0x68ffU) != -1 || *__errno_location() != 25) return 9;
    printf("STOCK_SPI_UNKNOWN errno=%d\n", *__errno_location());
    spi_close(spi);
    return 0;
}
