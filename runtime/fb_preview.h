/* Samsung d4_dp_type.h/d4_dp_ioctl.h video-plane ABI. Software scanout
 * publishes only pixels submitted by CDisplayLCD; it does not run an ISP.
 * No vsync is synthesized. A wait consumes a completed software scanout.
 */
enum { FB_KIND = 6+ISP_COUNT };
struct fb_video {
    u32 win, on, format, bit, address_mode;
    unsigned short stride, padding;
    u32 width, height, x, display_width, y, display_height;
    u32 y0, y1, c0, c1;
};
struct fb_window { struct fb_video video; u32 configured, visible, ring_size, irq, submitted, completed, failed; };
struct fb_state { u32 magic, version, sequence, reserved; struct fb_window windows[4]; };
static struct fb_state *fb;
static volatile int fb_lock_word;
static volatile u32 fb_completed, fb_consumed;
static void fb_lock(void) { while (__sync_lock_test_and_set(&fb_lock_word, 1)) raw(158, 0, 0, 0, 0, 0, 0); }
static void fb_unlock(void) { __sync_lock_release(&fb_lock_word); }
static int fb_open(int flags) {
    fb_lock();
    int fd = checked(raw(5, (long)"/var/run/nx300m-lcd-state.bin", 0102|(flags&02000000), 0600, 0, 0, 0));
    if (fd >= 0 && ((u32)fd >= FD_LIMIT || raw(93, fd, 4096, 0, 0, 0, 0) < 0)) {
        raw(6, fd, 0, 0, 0, 0, 0); fd = error(12);
    }
    if (fd >= 0 && !fb) {
        void *memory = (void *)checked(raw(192, 0, 4096, 3, 1, fd, 0));
        if (memory == (void *)-1) { raw(6, fd, 0, 0, 0, 0, 0); fd = -1; }
        else {
            fb = memory;
            for (u32 i = 0; i < sizeof(*fb); i++) ((unsigned char *)fb)[i] = 0;
            fb->magic = 0x434c584eU; fb->version = 1;
        }
    }
    if (fd >= 0) { kinds[fd] = FB_KIND; event("LCD framebuffer open", fd, 0, 0); }
    fb_unlock();
    return fd;
}
static int fb_write_all(int fd, const void *source, u32 bytes) {
    const unsigned char *p = source;
    while (bytes) {
        int written = checked(raw(4, fd, (long)p, bytes, 0, 0, 0));
        if (written < 0 && *__errno_location() == 4) continue;
        if (written <= 0) return -1;
        bytes -= written; p += written;
    }
    return 0;
}
static int fb_scanout(struct fb_window *window) {
    struct fb_video *v = &window->video;
    if (!window->configured) return error(22);
    /* DP video does not scale. 10-bit and field-separated buffers need their
     * own decoder; reject them rather than treating their bytes as 8-bit. */
    /* Samsung's stride spans an even/odd line pair, not a single row. */
    u32 stride = v->stride/2;
    if (v->format > 1 || v->bit || v->width > 8192 || v->height > 8192 ||
        !v->width || !v->height || ((v->width|v->height|v->stride)&1) || stride < v->width ||
        v->y1 != v->y0+stride || v->c1 != v->c0+stride) return error(95);
    u32 yaddr = v->y0, caddr = v->c0;
    if (!v->address_mode) {
        lock(); yaddr = physical_address(yaddr); caddr = physical_address(caddr); unlock();
    } else if (v->address_mode != 1) return error(22);
    u32 chroma_rows = v->height/(v->format == 0 ? 2 : 1);
    unsigned char *yp = nx300m_map_physical_access(yaddr, (v->height-1)*stride+v->width, 1);
    unsigned char *cp = nx300m_map_physical_access(caddr, (chroma_rows-1)*stride+v->width, 1);
    if (!yp || !cp) return error(14);
    char temporary[] = "/var/run/nx300m-lcd-video-0.yuv.tmp";
    char target[] = "/var/run/nx300m-lcd-video-0.yuv";
    temporary[26] = target[26] = '0'+v->win;
    int fd = checked(raw(5, (long)temporary, 0101|01000, 0600, 0, 0, 0));
    if (fd < 0) return -1;
    int result = fb_write_all(fd, v, sizeof(*v));
    for (u32 y = 0; !result && y < v->height; y++) result = fb_write_all(fd, yp+y*stride, v->width);
    for (u32 y = 0; !result && y < chroma_rows; y++) result = fb_write_all(fd, cp+y*stride, v->width);
    raw(6, fd, 0, 0, 0, 0, 0);
    if (!result) result = checked(raw(38, (long)temporary, (long)target, 0, 0, 0, 0));
    if (!result) {
        window->completed++;
        if (window->irq) {
            __sync_add_and_fetch(&fb_completed, 1);
            raw(240, (long)&fb_completed, 129, 0x7fffffff, 0, 0, 0);
        }
    }
    return result;
}
static int fb_ioctl(u32 request, void *arg) {
    if (request == 0x744c) {
        for (;;) {
            u32 done = fb_completed, consumed = fb_consumed;
            if (done != consumed && __sync_bool_compare_and_swap(&fb_consumed, consumed, consumed+1)) return 0;
            struct { long seconds, nanos; } timeout = {1, 0};
            long status = raw(240, (long)&fb_completed, 128, done, (long)&timeout, 0, 0);
            if (status == -110) return error(110);
            if (status < 0 && status != -11) return checked(status);
        }
    }
    if (!arg) return error(14);
    u32 *words = arg, win = words[0];
    if (win > 3) return error(22);
    fb_lock();
    struct fb_window *window = &fb->windows[win];
    int result = 0;
    fb->sequence++; __sync_synchronize();
    switch (request) {
    case 0x40407406U:
        window->video = *(struct fb_video *)arg;
        window->configured = 1;
        /* Visibility is independently controlled by VID_WINDOW_ONOFF. */
        window->submitted++; result = fb_scanout(window); break;
    case 0x40207407U:
        if (!window->configured) { result = error(22); break; }
        window->video.address_mode = words[1];
        window->video.y0 = words[2]; window->video.y1 = words[3];
        window->video.c0 = words[4]; window->video.c1 = words[5];
        window->submitted++; result = fb_scanout(window); break;
    case 0x40087405U:
        if (words[1] > 1) result = error(22);
        else window->visible = words[1];
        break;
    case 0x4008744dU:
        if (!words[1] || words[1] > 32) result = error(22);
        else window->ring_size = words[1];
        break;
    case 0x4004744bU: window->irq = 1; break;
    case 0x40047451U: window->irq = 0; break;
    case 0xc0087452U: words[1] = 0; break; /* Synchronous export has no queued buffers. */
    default: event("unsupported LCD ioctl", win, request, 0); result = error(25); break;
    }
    if (result && (request == 0x40407406U || request == 0x40207407U)) window->failed++;
    if (window->submitted <= 4 || (result && window->failed <= 4)) event("LCD video ioctl", request, win, result ? *__errno_location() : 0);
    __sync_synchronize(); fb->sequence++;
    fb_unlock();
    return result;
}
