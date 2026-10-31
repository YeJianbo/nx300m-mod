/* D4C SPI wire transport from stock libd4c.so.0.19. Single accesses use a
 * command (low 16 address bits, bit 16 write) followed by one data clock word.
 * FIFO reset is a controller operation and does not reset the external peer.
 * Burst payloads are retained opaque: no inferred address stepping or DSP work.
 */
static u32 *d4c_values;
static unsigned char *d4c_defined;
static u32 d4c_address, d4c_command, d4c_remaining, d4c_count, d4c_words;
static int d4c_read_pending, d4c_burst_pending;
static u32 d4c_payload[16384], d4c_read_traces, d4c_write_traces;
static int d4c_register_open(void) {
    if (d4c_values) return 0;
    int fd = checked(raw(5, (long)"/var/run/nx300m-d4c-registers.bin", 0102|01000, 0644, 0, 0, 0));
    if (fd < 0) return -1;
    if (checked(raw(93, fd, 0x42000, 0, 0, 0, 0))) { raw(6, fd, 0, 0, 0, 0, 0); return -1; }
    long mapping = checked(raw(192, 0, 0x42000, 3, 1, fd, 0));
    raw(6, fd, 0, 0, 0, 0, 0);
    if (mapping == -1) return -1;
    d4c_values = (u32 *)mapping; d4c_defined = (unsigned char *)mapping+0x40000;
    return 0;
}
static void d4c_publish_burst(void) {
    const char *temporary = "/var/run/nx300m-d4c-burst.tmp";
    int fd = checked(raw(5, (long)temporary, 0102|01000, 0644, 0, 0, 0));
    if (fd < 0) return;
    u32 header[2] = {d4c_command, d4c_words};
    int complete = raw(4, fd, (long)header, sizeof(header), 0, 0, 0) == (long)sizeof(header);
    complete &= raw(4, fd, (long)d4c_payload, d4c_words*4, 0, 0, 0) == (long)(d4c_words*4);
    raw(6, fd, 0, 0, 0, 0, 0);
    if (complete && !raw(38, (long)temporary, (long)"/var/run/nx300m-d4c-burst.bin", 0, 0, 0, 0))
        event("D4C opaque burst received", d4c_command, d4c_words, 0);
}
static int d4c_exchange(u32 bits, u32 tx, u32 *rx) {
    if (bits != 32 || !d4c_values) return 0;
    *rx = 0xffffffffU; /* Explicit idle-high MISO clock input, not a device ACK. */
    if (d4c_remaining) {
        if (d4c_burst_pending) d4c_payload[d4c_count++] = tx;
        else {
            d4c_values[d4c_address] = tx;
            d4c_defined[d4c_address/8] |= 1U<<(d4c_address%8);
            if (d4c_write_traces++ < 32) event("D4C register written", d4c_address, tx, 1);
        }
        if (!--d4c_remaining && d4c_burst_pending) d4c_publish_burst();
        return 1;
    }
    if (d4c_read_pending) {
        int defined = !!(d4c_defined[d4c_address/8]&(1U<<(d4c_address%8)));
        if (defined) *rx = d4c_values[d4c_address];
        d4c_read_pending = 0;
        if (d4c_read_traces++ < 32) event("D4C register read", d4c_address, *rx, defined);
        return 1;
    }
    d4c_address = tx&0xffffU; d4c_command = tx;
    d4c_burst_pending = 0;
    if (!(tx&~0x1ffffU)) {
        if (tx&0x10000U) d4c_remaining = 1;
        else d4c_read_pending = 1;
    } else if (!(tx&~0x1fffffU) && (tx&0x110000U) == 0x110000U) {
        d4c_remaining = d4c_words = 4U<<((tx>>17)&7);
        d4c_burst_pending = 1; d4c_count = 0;
    } else if ((tx&0x200000U) && !(tx&~0x20ffffU) && (tx&0xff)) {
        /* Stock DPC pipe packets encode ceil(size/256) in the low byte,
         * followed by 64 words per block. Retain data without interpreting it. */
        d4c_remaining = d4c_words = (tx&0xff)*64;
        d4c_burst_pending = 1; d4c_count = 0;
    } else event("D4C unmodeled command; idle MISO", tx, 0, 0);
    return 1;
}
