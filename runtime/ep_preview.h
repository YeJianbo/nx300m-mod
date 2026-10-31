/* EP device ABI from Samsung d4_ep_ioctl.[ch]. LVR rotation and the observed
 * MnF 12-bit Bayer splitter execute pixels. Stock libudd configures registers;
 * LVR executes at UDD_UNLOCK, MnF after the forwarded stock frame-start call.
 */
enum { EP_KIND = FB_KIND+1, EP_BYTES = 9*4096, EP_CORE_INTERRUPTS = 27, EP_DMA_INTERRUPTS = 18 };
#define EP_HOST_BASE 0x92000000U
static volatile u32 *ep_registers;
static u32 ep_path[17], ep_next_id = 10, ep_frames, ep_failures;
/* MnF and LVR are different hardware blocks and may own paths concurrently. */
static u32 ep_mnf_path[17];
static int ep_irq[EP_CORE_INTERRUPTS], ep_dma_irq[EP_DMA_INTERRUPTS], ep_initialized;
static int ep_open(int flags) {
    lock();
    int fd = checked(raw(5, (long)"/tmp/nx-ep", 0102|(flags&(04000|02000000)), 0600, 0, 0, 0));
    if (fd >= 0 && ((u32)fd >= FD_LIMIT || raw(93, fd, EP_BYTES, 0, 0, 0, 0) < 0)) {
        raw(6, fd, 0, 0, 0, 0, 0); fd = error(12);
    }
    if (fd >= 0 && !ep_registers) {
        void *memory = (void *)checked(raw(192, 0, EP_BYTES, 3, 1, fd, 0));
        if (memory == (void *)-1) { raw(6, fd, 0, 0, 0, 0, 0); fd = -1; }
        else ep_registers = memory;
    }
    if (fd >= 0 && !ep_initialized) {
        int failure = 0;
        for (int i = 0; i < EP_CORE_INTERRUPTS; i++) ep_irq[i] = -1;
        for (int i = 0; i < EP_DMA_INTERRUPTS; i++) ep_dma_irq[i] = -1;
        for (int i = 0; i < EP_CORE_INTERRUPTS && !failure; i++) {
            ep_irq[i] = eventfd(0, 1|04000|02000000);
            if (ep_irq[i] < 0) failure = *__errno_location();
        }
        for (int i = 0; i < EP_DMA_INTERRUPTS && !failure; i++) {
            ep_dma_irq[i] = eventfd(0, 1|04000|02000000);
            if (ep_dma_irq[i] < 0) failure = *__errno_location();
        }
        if (failure) {
            for (int i = 0; i < EP_CORE_INTERRUPTS; i++) if (ep_irq[i] >= 0) raw(6, ep_irq[i], 0, 0, 0, 0, 0);
            for (int i = 0; i < EP_DMA_INTERRUPTS; i++) if (ep_dma_irq[i] >= 0) raw(6, ep_dma_irq[i], 0, 0, 0, 0, 0);
            raw(6, fd, 0, 0, 0, 0, 0); fd = error(failure);
        } else ep_initialized = 1;
    }
    if (fd >= 0) kinds[fd] = EP_KIND;
    unlock();
    if (fd >= 0) event("EP transport open", fd, EP_HOST_BASE, EP_BYTES);
    return fd;
}
static int ep_rotate_plane(volatile u32 *r, int chroma) {
    u32 sh = r[0x44/4], sw = r[0x48/4], ss = r[0x60/4], ds = r[0x5c/4];
    u32 rotation = r[0x40/4]&3, mode = ep_registers[(7*4096+0xa8)/4];
    if (mode > 1 || r[0x64/4] || (r[0x40/4]&~3U) || !sw || !sh ||
        sw > 8192 || sh > 8192 || ((sw|sh)&1)) return error(95);
    /* A 4:2:2 quarter turn changes the subsampling axis; keep that mode
     * unsupported until its vertical chroma resampling is implemented. */
    if (chroma && !mode && (rotation&1)) return error(95);
    u32 components = chroma ? 2 : 1;
    if (chroma) { sw /= 2; if (mode) sh /= 2; }
    u32 dw = rotation&1 ? sh : sw, dh = rotation&1 ? sw : sh;
    if (ss < sw*components || ds < dw*components || ss > 65536 || ds > 65536) return error(22);
    u32 source_address = r[0x4c/4], target_address = r[0x54/4];
    /* CYcc stores alternating lines. Stride registers contain both lines. */
    if (r[0x50/4] != source_address+ss/2 || r[0x58/4] != target_address+ds/2) return error(95);
    ss /= 2; ds /= 2;
    if (ss < sw*components || ds < dw*components) return error(22);
    unsigned char *src = nx300m_map_physical_access(source_address, (sh-1)*ss+sw*components, 1);
    unsigned char *dst = nx300m_map_physical_access(target_address, (dh-1)*ds+dw*components, 2);
    if (!src || !dst) return error(14);
    /* Source and output allocations are distinct in CDisplayLCD. */
    unsigned long long se = (unsigned long long)source_address+(sh-1)*ss+sw*components;
    unsigned long long de = (unsigned long long)target_address+(dh-1)*ds+dw*components;
    if (source_address < de && target_address < se) return error(95);
    for (u32 y = 0; y < sh; y++) for (u32 x = 0; x < sw; x++) {
        u32 dx = x, dy = y;
        if (rotation == 1) { dx = sh-1-y; dy = x; }
        else if (rotation == 2) { dx = sw-1-x; dy = sh-1-y; }
        else if (rotation == 3) { dx = y; dy = sw-1-x; }
        for (u32 c = 0; c < components; c++) dst[dy*ds+dx*components+c] = src[y*ss+x*components+c];
    }
    return 0;
}
static int ep_execute_lvr(void) {
    if (!ep_path[0] || (ep_path[2] != 11 && ep_path[2] != 14)) return 0;
    volatile u32 *lvr = ep_registers+7*1024;
    /* These dimensions are only nonzero after original LVR_set_video_register. */
    if (!lvr[0x44/4] || !lvr[0x48/4]) return 0;
    int result = ep_rotate_plane(lvr, 0);
    if (!result) result = ep_rotate_plane(lvr+0x40/4, 1);
    if (!result) { ep_frames++; ssif_complete(ep_irq[18]); ssif_complete(ep_irq[26]); }
    else ep_failures++;
    if (ep_frames+ep_failures <= 4) event("EP LVR software rotation", lvr[0x48/4], lvr[0x44/4], result ? *__errno_location() : 0);
    /* A failed operation supplies no interrupt completion. */
    return result;
}
#include "ep_mnf_pixels.h"
static int ep_wait_queue(int fd, u32 request, u32 *words) {
    int dma = request == 0x40086808U || request == 0x40086809U;
    int initialize = request == 0x40086807U || request == 0x40086809U;
    if (words[1] >= (u32)(dma ? EP_DMA_INTERRUPTS : EP_CORE_INTERRUPTS) ||
        (!initialize && words[0] > 0x7fffffffU)) return error(22);
    long flags = checked(raw(55, fd, 3, 0, 0, 0, 0));
    if (flags < 0) return -1;
    if (flags&04000) return error(15); /* Released ioctl returns ENOTBLK. */
    int queue = dma ? ep_dma_irq[words[1]] : ep_irq[words[1]];
    if (initialize) { ssif_drain(queue); return 0; }
    /* NX300M kernel HZ=200. Return remaining rounded milliseconds as the
     * released completion wait does, rather than accepting an absent IRQ. */
    unsigned long long rounded = ((unsigned long long)words[0]+4)/5*5;
    struct allegro_time before, after;
    if (clock_gettime(1, &before)) return -1;
    int ready = ssif_wait(queue, rounded > 0x7fffffffU ? 0x7fffffff : (int)rounded);
    if (ready < 0) return -1;
    if (!ready) return error(110);
    if (clock_gettime(1, &after)) return -1;
    long long elapsed = (long long)(after.seconds-before.seconds)*1000000000LL+after.nanoseconds-before.nanoseconds;
    unsigned long long used = elapsed > 0 ? (unsigned long long)elapsed/5000000*5 : 0;
    return rounded > used ? (int)(rounded-used > 0x7fffffffU ? 0x7fffffffU : rounded-used) : 5;
}
static int ep_ioctl(int fd, u32 request, void *arg) {
    if (request == 0x6828) return 0; /* libudd serializes configuration itself. */
    if (request == 0x6829) { ep_execute_lvr(); return 0; }
    if (request == 0x6814 || request == 0x6815) {
        if (ep_path[0]) return error(16);
        return 0;
    }
    if (!arg) return error(14);
    u32 *words = arg;
    switch (request) {
    case 0x80486864U:
        for (int i = 0; i < 9; i++) { words[i*2] = EP_HOST_BASE+i*4096; words[i*2+1] = 4096; }
        return 0;
    case 0x40386816U:
        if (words[0] == 8) { /* EP_STATE_MNF_STANDALONE */
            if (words[1] || words[9] != 20) {
                event("unsupported EP MnF path format", words[1], words[9], 0);
                return error(95);
            }
            lock();
            if (ep_mnf_path[0]) { unlock(); return error(16); }
            ep_mnf_path[0] = ep_next_id++; ep_mnf_path[1] = 1; ep_mnf_path[2] = words[0];
            for (int i = 0; i < 14; i++) ep_mnf_path[i+3] = words[i];
            int id = ep_mnf_path[0];
            unlock();
            ep_registers[0x40/4] &= ~8U;
            event("EP MnF Bayer split path configured", id, words[3], words[9]);
            return id;
        }
        if (words[0] != 11 && words[0] != 14) {
            event("unsupported EP path", words[0], words[9], 0);
            return error(95);
        }
        if (ep_path[0]) return error(16);
        ep_path[0] = ep_next_id++; ep_path[1] = 1; ep_path[2] = words[0];
        for (int i = 0; i < 14; i++) ep_path[i+3] = words[i];
        ep_registers[(7*4096+0x44)/4] = ep_registers[(7*4096+0x48)/4] = 0;
        return ep_path[0];
    case 0xc0446818U:
        if (ep_mnf_path[0] && words[0] == ep_mnf_path[0]) {
            for (int i = 0; i < 17; i++) words[i] = ep_mnf_path[i];
            return 0;
        }
        if (!ep_path[0] || words[0] != ep_path[0]) return error(22);
        for (int i = 0; i < 17; i++) words[i] = ep_path[i];
        return 0;
    case 0x40046817U:
        if (ep_mnf_path[0] && words[0] == ep_mnf_path[0]) {
            ep_mnf_path[0] = ep_mnf_path[1] = 0; return 0;
        }
        if (!ep_path[0] || words[0] != ep_path[0]) return error(22);
        ep_path[0] = ep_path[1] = 0; return 0;
    case 0x40086806U: case 0x40086807U:
    case 0x40086808U: case 0x40086809U:
        return ep_wait_queue(fd, request, words);
    default: event("unsupported EP ioctl", request, 0, 0); return error(25);
    }
}
