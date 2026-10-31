/* Explicit recorded Bayer input at the PP WDMA boundary, after sensor/D4C.
 * No sensor, PAF, PP corrections or ISP pixel operations are emulated here.
 * Notify the ordinary PP ISR queue only after all real payload rows are copied.
 * Stock dd_csm_start_read_out_mode resets its software completion AFTER ioctl
 * returns. Deliver a supplied frame end after that original command returns,
 * preserving those stock writes rather than racing an IRQ inside the ioctl.
 */
static int pp_replay_phase;
static volatile u32 pp_replay_generation;
u32 nx300m_pp_replay_generation(void) { return pp_replay_generation; }
/* CSM and frame-end callers already hold ssif_lock. */
static void pp_replay_arm(void) {
    pp_replay_phase = getenv("NX300M_PP_BAYER_REPLAY") ? 1 : 0;
    if (pp_replay_phase) event("PP Bayer source armed; awaiting stock CSM return", 1, 0, 0);
}
int dd_csm_start_read_out_mode(void) {
    int (*original)(void) = dlsym((void *)-1, "dd_csm_start_read_out_mode");
    if (!original) {
        event("PP stock CSM command unresolved", 0, 0, 0);
        extern void abort(void);
        abort();
    }
    int result = original();
    ssif_lock();
    if (!result && pp_replay_phase == 1) {
        pp_replay_phase = 2;
        event("PP stock CSM returned; awaiting recorded frame end", 2, 0, 0);
    }
    ssif_unlock();
    return result;
}
static void pp_replay_frame_end(void) {
    if (pp_replay_phase != 2) return;
    pp_replay_phase = 0;
    event("PP recorded frame end received", 0, 0, 0);
    pp_replay_readout();
}
void pp_rw_dma_start(void) {
    void (*original)(void) = dlsym((void *)-1, "pp_rw_dma_start");
    if (!original) {
        event("PP memory DMA command unresolved", 0, 0, 0);
        extern void abort(void);
        abort();
    }
    original();
    int saved_errno = *__errno_location();
    nx300m_pp_memory_task();
    *__errno_location() = saved_errno;
    /* The later stillBayer path is RDMA->PP->WDMA, distinct from CSM sensor
     * readout. Preserve its programmed state for development; copying the
     * sensor fixture again would skip the requested PP pixel operations. */
    volatile u32 *pp = isp_registers[ISP_PP];
    if (pp && getenv("NX300M_PP_BAYER_REPLAY")) {
        int fd = checked(raw(5, (long)"/var/run/nx300m-memory-pass-pp.bin", 0101|01000|02000000, 0600, 0, 0, 0));
        if (fd >= 0) {
            long bytes = raw(4, fd, (long)pp, 0xb000, 0, 0, 0);
            raw(6, fd, 0, 0, 0, 0, 0);
            event("PP memory-pass snapshot; pixels unsupported", (u32)bytes, pp[0x3304/4], pp[0x3308/4]);
        }
    }
}
static void pp_replay_readout(void) {
    const char *path = getenv("NX300M_PP_BAYER_REPLAY");
    if (!path) return;
    int fd = checked(raw(5, (long)path, 02000000, 0, 0, 0, 0));
    if (fd < 0) { event("PP Bayer replay open failed", (u32)*__errno_location(), 0, 0); return; }
    u32 header[16] = {0};
    long count = raw(3, fd, (long)header, sizeof header, 0, 0, 0);
    volatile u32 *pp = isp_registers[ISP_PP];
    u32 (*get_width)(u32) = dlsym((void *)0, "cf_pp_sensor_get_global_width");
    u32 (*get_height)(u32) = dlsym((void *)0, "cf_pp_sensor_get_global_height");
    u32 width = header[2], height = header[3], stride = header[4];
    u32 format = pp ? pp[0x3314/4] : 0;
    if (count != sizeof header || header[0] != 0x3252584e || header[1] != 1 || header[5] != 12 ||
        !get_width || !get_height || width != get_width(3) || height != get_height(3) ||
        !width || width > 8192 || (width&1) || !height || height > 8192 || (height&1) ||
        stride < width/2*3 || stride > 32768 || (stride&7) ||
        (format&0x701) != 0x400 || ((format >> 4)&7) != 2 || ((format >> 28)&7) != 1 ||
        !pp || !(pp[0x3a10/4]&2) || (pp[0x330c/4]&1) || stride != pp[0x330c/4]/2) {
        event("PP Bayer replay rejected format/geometry", width, height, format);
        raw(6, fd, 0, 0, 0, 0, 0); return;
    }
    u32 bytes = stride*height;
    if (raw(19, fd, 0, 2, 0, 0, 0) != (long)(sizeof header+bytes)) {
        event("PP Bayer replay rejected payload size", bytes, 0, 0);
        raw(6, fd, 0, 0, 0, 0, 0); return;
    }
    void *source = (void *)checked(raw(192, 0, sizeof header+bytes, 1, 2, fd, 0));
    raw(6, fd, 0, 0, 0, 0, 0);
    if (source == (void *)-1) return;
    u32 even = pp[0x3304/4], odd = pp[0x3308/4], delta = pp[0x330c/4];
    unsigned char *even_out = nx300m_map_physical_access(even, (height/2-1)*delta+stride, 2);
    unsigned char *odd_out = nx300m_map_physical_access(odd, (height/2-1)*delta+stride, 2);
    if (!even_out || !odd_out || (even&7) || odd != even+stride) {
        event("PP Bayer replay rejected destination", even, odd, delta);
        raw(91, (long)source, sizeof header+bytes, 0, 0, 0, 0); return;
    }
    const u32 *pixels = (const u32 *)((const char *)source+sizeof header);
    for (u32 row = 0; row < height; row++) {
        u32 *destination = (u32 *)((row&1 ? odd_out : even_out)+(row/2)*delta);
        for (u32 i = 0; i < stride/4; i++) destination[i] = pixels[row*(stride/4)+i];
    }
    __sync_synchronize();
    raw(91, (long)source, sizeof header+bytes, 0, 0, 0, 0);
    if (pp[0x3304/4] != even || pp[0x3308/4] != odd || pp[0x330c/4] != delta || pp[0x3314/4] != format) {
        event("PP Bayer replay destination changed; no IRQ", even, odd, 0); return;
    }
    event("PP recorded Bayer rows transferred", width, height, bytes);
    __sync_add_and_fetch(&pp_replay_generation,1);
    pp_event_lock(); pp_dispatch(1U<<19); pp_event_unlock();
}
