/* Wire format follows the original CAquilaDriver/CAquilaCt3Driver register
 * functions: 32 bits, address in the high half, read flag at bit 31.
 * Values become defined only after an original bus write. Unknown addresses
 * return the explicit idle-high MISO input, never a fabricated chip ID.
 * This models register transport, not sensor reset defaults or image capture.
 */
static unsigned short *aquila_values;
static unsigned char *aquila_defined;
static u32 aquila_read_traces;
static int aquila_register_open(void) {
    if (aquila_values) return 0;
    int fd = checked(raw(5, (long)"/var/run/nx300m-sensor-registers.bin", 0102 | 01000, 0644, 0, 0, 0));
    if (fd < 0) return -1;
    if (checked(raw(93, fd, 0x11000, 0, 0, 0, 0))) { raw(6, fd, 0, 0, 0, 0, 0); return -1; }
    long mapped = checked(raw(192, 0, 0x11000, 3, 1, fd, 0));
    raw(6, fd, 0, 0, 0, 0, 0);
    if (mapped == -1) return -1;
    aquila_values = (unsigned short *)mapped;
    aquila_defined = (unsigned char *)mapped+0x10000;
    return 0;
}
static int aquila_exchange(u32 bits, u32 tx, u32 *rx) {
    if (bits != 32 || !aquila_values) return 0;
    u32 address = (tx >> 16) & 0x7fff;
    if (tx & 0x80000000u) {
        int defined = !!(aquila_defined[address/8] & (1u << (address%8)));
        *rx = defined ? aquila_values[address] : 0xffffffffu;
        if (aquila_read_traces++ < 32) event("Aquila register read", address, *rx, defined);
    } else {
        aquila_values[address] = (unsigned short)tx;
        aquila_defined[address/8] |= 1u << (address%8);
        *rx = 0xffffffffu; /* Write clock cycles still shift the chosen idle MISO level. */
    }
    return 1;
}
