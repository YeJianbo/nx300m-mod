/* NX300M kernel hs_spi_ioctl.c control path. libudd still configures its own
 * mmap registers and executes polling transfers. Control requests do not create
 * RX frames; the optional FIFO/peer model handles data, without IRQ synthesis.
 * LPJ=1000 is an explicit lab loop-budget input, not camera CPU calibration.
 */
enum { SPI_SIM_LPJ = 1000, SPI_SIM_HZ = 200 };
static volatile u32 *spi_registers[8];
static unsigned char spi_gpio_mux[8];
static const unsigned char spi_clk_pins[8] = {3, 38, 80, 113, 117, 121, 66, 213};
static const unsigned char spi_cs_pins[8] = {6, 41, 83, 116, 120, 124, 69, 216};
#include "mmio_trap.h"
#include "spi_fifo.h"

static int spi_control_open(int fd, int channel) {
    if (spi_registers[channel]) return 0;
    long mapped = checked(raw(192, 0, SPI_BYTES, 3, 1, fd, 0));
    if (mapped == -1) return -1;
    spi_registers[channel] = (volatile u32 *)mapped;
    return 0;
}

static int spi_drive_pin(unsigned int pin, int level) {
    int (*preview)(void) = dlsym((void *)0, "nx300m_gpio_preview_active");
    void *(*open_gpio)(u32) = dlsym((void *)0, "gpio_open");
    void (*direction)(void *, u32, int) = dlsym((void *)0, "gpio_port_set");
    void (*set_gpio)(void *, u32, int) = dlsym((void *)0, "gpio_data_set");
    if (!preview || !preview() || !open_gpio || !direction || !set_gpio) return error(38);
    void *bank = open_gpio(pin/8);
    if (!bank) return error(19);
    direction(bank, pin%8, 1);
    set_gpio(bank, pin%8, !!level);
    return 0;
}

static int spi_control_ioctl(int fd, u32 request, void *arg, int log_request) {
    int channel = channels[fd];
    volatile u32 *registers = spi_registers[channel];
    if (!registers) return error(19);
    if (request == 0x8008680cU) {
        if (!arg) return error(14);
        u32 *info = arg;
        info[0] = 0x11000000U; info[1] = SPI_BYTES;
        return 0;
    }
    if (request == 0xc004680dU) {
        if (!arg) return error(14);
        /* Kernel integer order and 32-bit multiplication are intentional. */
        u32 milliseconds = *(u32 *)arg;
        *(u32 *)arg = (SPI_SIM_LPJ/1000U)*SPI_SIM_HZ*milliseconds;
        if (log_request) event("SPI wait-loop budget", channel, milliseconds, *(u32 *)arg);
        return 0;
    }
    if (request == 0x4004680fU) {
        if (!arg) return error(14);
        int enabled = *(u32 *)arg == 1;
        if (spi_drive_pin(spi_cs_pins[channel], !enabled)) return -1;
        if (enabled) spi_gpio_mux[channel] |= 8;
        else spi_gpio_mux[channel] = 0; /* Original restores the default group. */
        if (log_request) event("SPI burst CS", channel, enabled, !enabled);
        return 0;
    }
    if (request == 0x680eU) {
        if (spi_drive_pin(spi_clk_pins[channel], (registers[0] >> 3) & 1)) return -1;
        /* Released driver passes bitwise ~inversion to gpio_direction_output;
         * either result is nonzero and therefore drives CS high. */
        if (spi_drive_pin(spi_cs_pins[channel], 1)) return -1;
        u32 value = registers[0];
        registers[0] = value | 0x20;
        registers[0] = value & ~0x20U;
        spi_fifo_reset(channel);
        spi_gpio_mux[channel] = 0;
        return 0;
    }
    if (request == 0x40146812U) {
        if (!arg) return error(14);
        const u32 *pin = arg;
        /* Kernel layout: fix, DI, DO, CLK, CS; 0 means select this signal.
         * Switching to SPI restores its complete default pinctrl group. */
        if (!pin[0]) spi_gpio_mux[channel] = 0;
        else {
            if (!pin[3]) spi_gpio_mux[channel] |= 1;
            if (!pin[1]) spi_gpio_mux[channel] |= 2;
            if (!pin[2]) spi_gpio_mux[channel] |= 4;
            if (!pin[4]) spi_gpio_mux[channel] |= 8;
        }
        return 0;
    }
    if (log_request) event("unsupported SPI ioctl", fd, request, (u32)arg);
    return error(25);
}
