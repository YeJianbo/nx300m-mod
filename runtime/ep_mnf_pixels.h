/* The stock Bayer splitter writes the four (x parity, y parity) planes.
 * Its DMA registers are programmed by unmodified MnF_set_bayer_spliter.
 * This implements the observed 12-bit memory path, without demosaic or IQ.
 */
static u32 ep_mnf_frames, ep_mnf_failures;
static int ep_mnf_ranges_overlap(u32 a, u32 as, u32 b, u32 bs) {
    return (unsigned long long)a+as > b && (unsigned long long)b+bs > a;
}
static int ep_mnf_split_pixels(void) {
    volatile u32 *m = ep_registers+4*1024, *dma = ep_registers+8*1024;
    volatile u32 *rd = dma+0x40/4, *wd = dma+0x140/4;
    u32 width = m[0x38/4]&8191U, height = (m[0x38/4]>>16)&8191U;
    /* Mode 20 is bypass (14), with SPLIT bit 31. Memory input, no effect,
     * no overlap, automatic horizontal tile geometry, no byte reversal. */
    if (m[0] != 0x800030e0U || m[0x18c/4] ||
        !width || !height || ((width|height)&1) || width%4 ||
        rd[0] != 0x223 || wd[0] != 0x220 || wd[2] ||
        rd[8] || rd[9] != 0x10000 || wd[9] != 0x10000 ||
        (rd[2]>>16) != height || (rd[2]&65535)) return error(95);
    u32 input_pitch = rd[5], output_pitch[2] = {wd[5], wd[8]};
    u32 input_line = width/2*3, output_line = width/4*3;
    u32 rows = height/2;
    if (input_pitch < input_line*2 || input_pitch > 65536 ||
        output_pitch[0] < output_line || output_pitch[1] < output_line ||
        output_pitch[0] > 65536 || output_pitch[1] > 65536) return error(22);
    u32 source_addr[2] = {rd[3], rd[4]};
    u32 target_addr[4] = {wd[3], wd[6], wd[4], wd[7]};
    u32 source_size = (rows-1)*input_pitch+input_line;
    u32 target_size[4];
    unsigned char *source[2], *target[4];
    /* Validate every span before the first write. Unsupported or absent
     * mappings must leave the output and completion queues untouched. */
    for (int i = 0; i < 2; i++) {
        source[i] = nx300m_map_physical_access(source_addr[i], source_size, 1);
        if (!source[i]) return error(14);
    }
    for (int i = 0; i < 4; i++) {
        target_size[i] = (rows-1)*output_pitch[i&1]+output_line;
        target[i] = nx300m_map_physical_access(target_addr[i], target_size[i], 2);
        if (!target[i]) return error(14);
        for (int j = 0; j < 2; j++)
            if (ep_mnf_ranges_overlap(target_addr[i], target_size[i], source_addr[j], source_size)) return error(95);
        for (int j = 0; j < i; j++)
            if (ep_mnf_ranges_overlap(target_addr[i], target_size[i], target_addr[j], target_size[j])) return error(95);
    }
    for (u32 y = 0; y < height; y++) {
        const unsigned char *s = source[y&1]+(y>>1)*input_pitch;
        unsigned char *a = target[(y&1)*2]+(y>>1)*output_pitch[0];
        unsigned char *b = target[(y&1)*2+1]+(y>>1)*output_pitch[1];
        /* Four input pixels occupy six bytes. Deinterleave their two
         * packed pairs into one packed pair for each output port. */
        for (u32 x = 0; x < width/4; x++, s += 6, a += 3, b += 3) {
            u32 p0 = s[0] | ((u32)(s[1]&15)<<8);
            u32 p1 = (s[1]>>4) | ((u32)s[2]<<4);
            u32 p2 = s[3] | ((u32)(s[4]&15)<<8);
            u32 p3 = (s[4]>>4) | ((u32)s[5]<<4);
            a[0] = p0; a[1] = (p0>>8)|((p2&15)<<4); a[2] = p2>>4;
            b[0] = p1; b[1] = (p1>>8)|((p3&15)<<4); b[2] = p3>>4;
        }
    }
    return 0;
}
static void ep_execute_mnf(u32 command) {
    if (!ep_registers || !ep_mnf_path[0] || !(command&8)) return;
    int result = ep_mnf_split_pixels();
    if (!result) {
        ep_mnf_frames++;
        ssif_complete(ep_irq[10]);   /* MnF operation finished. */
        ssif_complete(ep_dma_irq[5]); /* MnF WDMA bus finished. */
    } else ep_mnf_failures++;
    if (ep_mnf_frames+ep_mnf_failures <= 8)
        event("EP MnF software Bayer split", ep_mnf_frames, ep_mnf_failures, result ? *__errno_location() : 0);
}
void ep_top_start_frame(u32 command) {
    void (*original)(u32) = dlsym((void *)-1, "ep_top_start_frame");
    if (!original) {
        event("EP stock frame command unresolved", command, 0, 0);
        extern void abort(void);
        abort();
    }
    original(command);
    /* This write-only command register is shared with concurrent LVR
     * starts. Observe the actual stock command rather than rereading a
     * backing word that another writer could already have overwritten. */
    ep_execute_mnf(command);
}
