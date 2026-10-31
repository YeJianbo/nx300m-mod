/* DRIMeIV master FIFO model for instruction-trapped SPI mappings. Loopback is
 * an explicit diagnostic peer; the disconnected peer provides no RX events.
 * Each TX store and RX load is a distinct event, including identical words.
 */
struct spi_fifo_state {
    u32 tx[64], rx[64], tx_head, tx_count, rx_head, rx_count, pending;
    u32 traces;
    int peer;
    volatile int busy;
};
static struct spi_fifo_state spi_fifos[8];
#include "aquila_registers.h"
#include "d4c_registers.h"
static void spi_fifo_lock(int channel) {
    while (__sync_lock_test_and_set(&spi_fifos[channel].busy, 1)) { }
}
static void spi_fifo_unlock(int channel) { __sync_lock_release(&spi_fifos[channel].busy); }
static void spi_fifo_reset(int channel) {
    struct spi_fifo_state *fifo = spi_fifos+channel;
    spi_fifo_lock(channel);
    fifo->tx_head = fifo->tx_count = fifo->rx_head = fifo->rx_count = fifo->pending = 0;
    spi_fifo_unlock(channel);
}
static u32 spi_frame_bits(int channel) {
    volatile u32 *registers = spi_registers[channel];
    u32 width = (registers[2] >> 22) & 3;
    u32 bits = width == 0 ? 8 : width == 1 ? 16 : 32;
    u32 valid = registers[0x34/4];
    if ((valid & 0x100) && (valid & 63) && (valid & 63) < bits) bits = valid & 63;
    return bits;
}
static u32 spi_swap_word(u32 value, u32 bits, u32 config) {
    u32 mask = bits == 32 ? 0xffffffffu : (1u << bits)-1;
    value &= mask;
    if (!(config & 1)) return value;
    if (config & 2) {
        u32 reversed = 0;
        for (u32 i = 0; i < bits; ++i) { reversed = (reversed << 1) | (value & 1); value >>= 1; }
        value = reversed;
    }
    if ((config & 4) && bits > 8) {
        if (bits <= 16) value = ((value & 0xff) << 8) | ((value >> 8) & 0xff);
        else value = ((value & 0xff) << 24) | ((value & 0xff00) << 8) |
                     ((value >> 8) & 0xff00) | ((value >> 24) & 0xff);
    }
    if ((config & 8) && bits > 16) value = (value << 16) | (value >> 16);
    return value & mask;
}
static void spi_fifo_shift(int channel) {
    struct spi_fifo_state *fifo = spi_fifos+channel;
    volatile u32 *registers = spi_registers[channel];
    if (!(registers[0x30/4] & 1) || (registers[0] & 16)) return;
    while (fifo->tx_count) {
        u32 bits = spi_frame_bits(channel), swap = registers[0x28/4];
        u32 value = spi_swap_word(fifo->tx[fifo->tx_head], bits, swap);
        fifo->tx_head = (fifo->tx_head+1) & 63; --fifo->tx_count;
        if (fifo->traces++ < 128) event("SPI shifted TX", channel, bits, value);
        u32 received = value;
        int has_rx = fifo->peer == 1 || (fifo->peer == 2 && aquila_exchange(bits, value, &received)) ||
                     (fifo->peer == 3 && d4c_exchange(bits, value, &received));
        if ((registers[0x30/4] & 2) && has_rx) {
            u32 bytes = bits <= 8 ? 1 : bits <= 16 ? 2 : 4;
            if ((fifo->rx_count+1)*bytes > 64) fifo->pending |= 0x20;
            else {
                u32 tail = (fifo->rx_head+fifo->rx_count) & 63;
                fifo->rx[tail] = spi_swap_word(received, bits, swap >> 4);
                ++fifo->rx_count;
            }
        }
        fifo->pending |= 0x80;
    }
}
static int spi_fifo_access(int channel, u32 offset, int width, int write, u32 *value) {
    if ((offset & 3)+(u32)width > 4) return 0;
    volatile u32 *registers = spi_registers[channel];
    struct spi_fifo_state *fifo = spi_fifos+channel;
    u32 word = offset & ~3u, shift = (offset & 3)*8;
    u32 mask = width == 4 ? 0xffffffffu : (1u << ((u32)width*8))-1;
    spi_fifo_lock(channel);
    if (write) {
        u32 data = (registers[word/4] & ~(mask << shift)) | ((*value & mask) << shift);
        registers[word/4] = data;
        if (word == 0x18) {
            u32 bits = spi_frame_bits(channel), bytes = bits <= 8 ? 1 : bits <= 16 ? 2 : 4;
            if ((fifo->tx_count+1)*bytes > 64) fifo->pending |= 8;
            else { fifo->tx[(fifo->tx_head+fifo->tx_count) & 63] = data; ++fifo->tx_count; }
            spi_fifo_shift(channel);
        } else if (word == 0x30) spi_fifo_shift(channel);
        else if (word == 0x24) fifo->pending &= ~(data & 0xfc);
        else if (word == 0 && (data & 0x20)) {
            fifo->tx_head = fifo->tx_count = fifo->rx_head = fifo->rx_count = fifo->pending = 0;
        }
    } else {
        u32 data;
        if (word == 0x14) {
            u32 bits = spi_frame_bits(channel), bytes = bits <= 8 ? 1 : bits <= 16 ? 2 : 4;
            u32 tx_bytes = fifo->tx_count*bytes, rx_bytes = fifo->rx_count*bytes;
            data = fifo->pending | ((tx_bytes < 64 ? tx_bytes : 63) << 8) |
                   ((rx_bytes < 64 ? rx_bytes : 63) << 15);
            if (fifo->tx_count*bytes < 64) data |= 1;
            if (fifo->rx_count) data |= 2;
        } else if (word == 0xf0) {
            if (!fifo->rx_count) { data = 0; fifo->pending |= 16; }
            else { data = fifo->rx[fifo->rx_head]; fifo->rx_head = (fifo->rx_head+1) & 63; --fifo->rx_count; }
        } else data = registers[word/4];
        *value = (data >> shift) & mask;
    }
    spi_fifo_unlock(channel);
    return 1;
}
static int spi_fifo_map(void *address, u32 size, int channel) {
    const char *enabled = getenv("NX300M_SPI_MMIO");
    if (!enabled || !equal(enabled, "1")) return 0;
    const char *peer = getenv("NX300M_SPI_PEER");
    spi_fifos[channel].peer = peer && equal(peer, "loopback");
    if (peer && equal(peer, "aquila-registers")) {
        int (*get_channel)(int) = dlsym((void *)0, "get_com_channel");
        if (!get_channel) return error(38);
        if (channel == get_channel(2)) {
            if (aquila_register_open()) return -1;
            spi_fifos[channel].peer = 2;
            event("Aquila register peer", channel, 0x10000, 0);
        }
    }
    const char *d4c_enabled = getenv("NX300M_D4C_REGISTERS");
    if (d4c_enabled && equal(d4c_enabled, "1")) {
        int (*get_channel)(int) = dlsym((void *)0, "get_com_channel");
        if (!get_channel) return error(38);
        if (channel == get_channel(3)) {
            if (d4c_register_open()) return -1;
            spi_fifos[channel].peer = 3;
            event("D4C written register peer", channel, 0x10000, 0);
        }
    }
    int result = mmio_register(address, size, channel, spi_fifo_access);
    if (!result) event("SPI instruction MMIO", channel, (u32)address, spi_fifos[channel].peer);
    return result;
}
