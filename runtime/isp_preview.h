/* NX300M register/driver transport, included by device_preview.c.
 * Contracts: Samsung GPL PP core/SSIF, IPCM, IPCS, BWM and CSM drivers.
 * This stores registers and subscriptions. No sensor, DMA, vsync or completed
 * image is generated. Completion waits retain their actual timeout outcome.
 */
enum { ISP_PP, ISP_IPCM, ISP_IPCS, ISP_SSIF, ISP_BWM, ISP_CSM, ISP_3A, ISP_MIPI, ISP_COUNT };
#define ISP_3A_MEMORY 0x88000000U /* Explicit host-only physical token, like BMA_BASE. */
static const char *isp_paths[ISP_COUNT] = {"/dev/d4_pp_core", "/dev/d4_ipcm", "/dev/d4_ipcs", "/dev/d4_pp_ssif", "/dev/d4_bwm", "/dev/d4_csm", "/dev/d4_pp_3a", "/dev/d4_mipi"};
static const char *isp_files[ISP_COUNT] = {"/tmp/nx-isp-pp", "/tmp/nx-isp-ipcm", "/tmp/nx-isp-ipcs", "/tmp/nx-isp-pp", "/tmp/nx-isp-bwm", "/tmp/nx-isp-csm", "/tmp/nx-isp-pp", "/tmp/nx-isp-pp"};
static const u32 isp_physical[ISP_COUNT] = {0x50040000U, 0x50060000U, 0x50050000U, 0x50040000U, 0x5e000000U, 0, 0x50040000U, 0x50040000U};
static const u32 isp_sizes[ISP_COUNT] = {0xb000, 0x2001, 0x10000, 0xb000, 0x6000, 0, 0xb000, 0xb000};
static volatile u32 *isp_registers[ISP_COUNT];
static unsigned char isp_initialized[ISP_COUNT];
static u32 isp_irq_open[ISP_COUNT], isp_clock[ISP_COUNT], isp_power[ISP_COUNT];
static u32 isp_log_count[ISP_COUNT][64];
static int isp_ldcm_locked;
static int isp_3a_disable_after_capture;
static int ssif_transport_initialize(void), ssif_wait_interrupt(u32), ssif_open_irq(void);
static int ssif_check_fps(void);
static int csm_ioctl(u32, void *);
static int pp_event_initialize(void), pp_event_ioctl(u32, void *);
static int ipc_event_initialize(int);
static void ipc_reset_dma(u32);
static int ipc_wait_dma(u32, void *);
static void pp_reset_wdma_completion(void);
static void pp_replay_readout(void);
static void pp_replay_arm(void), pp_replay_frame_end(void);
void *nx300m_map_physical(u32, u32);
void *nx300m_map_physical_access(u32, u32, int);
static u32 isp_csm_wdma_state[2];
static volatile int isp_lock_word;
static void isp_lock(void) { while (__sync_lock_test_and_set(&isp_lock_word, 1)) raw(158, 0, 0, 0, 0, 0, 0); }
static void isp_unlock(void) { __sync_lock_release(&isp_lock_word); }
static int isp_index(int fd) {
    if ((u32)fd >= FD_LIMIT || kinds[fd] < 6 || kinds[fd] >= 6+ISP_COUNT) return -1;
    return kinds[fd]-6;
}
static int isp_poll_device(int fd) { int i = isp_index(fd); return (i >= ISP_PP && i <= ISP_SSIF) || i == ISP_3A; }
static int isp_open(int index, int flags) {
    int bank = index == ISP_SSIF || index == ISP_3A || index == ISP_MIPI ? ISP_PP : index;
    isp_lock();
    int fd = checked(raw(5, (long)isp_files[index], 0102 | (isp_initialized[bank] ? 0 : 01000) | (flags & (04000 | 02000000)), 0600, 0, 0, 0));
    if (fd < 0) { isp_unlock(); return fd; }
    u32 size = (isp_sizes[index]+4095U)&~4095U;
    if (bank == ISP_PP) size += 0x100000; /* Zeroed 3A result storage; no statistics generated. */
    if (fd >= FD_LIMIT || raw(93, fd, size, 0, 0, 0, 0) < 0) {
        raw(6, fd, 0, 0, 0, 0, 0); isp_unlock(); return error(12);
    }
    if (size && !isp_initialized[bank]) {
        void *mapping = (void *)checked(raw(192, 0, size, 3, 1, fd, 0));
        if (mapping == (void *)-1) { raw(6, fd, 0, 0, 0, 0, 0); isp_unlock(); return -1; }
        isp_registers[bank] = mapping;
    }
    isp_initialized[bank] = 1;
    kinds[fd] = 6+index;
    isp_unlock();
    if (index == ISP_PP && pp_event_initialize()) {
        kinds[fd] = 0; raw(6, fd, 0, 0, 0, 0, 0); return -1;
    }
    if ((index == ISP_IPCM || index == ISP_IPCS) && ipc_event_initialize(index == ISP_IPCS)) {
        kinds[fd] = 0; raw(6, fd, 0, 0, 0, 0, 0); return -1;
    }
    if ((index == ISP_SSIF || index == ISP_CSM) && ssif_transport_initialize()) {
        kinds[fd] = 0; raw(6, fd, 0, 0, 0, 0, 0); return -1;
    }
    event("ISP transport open", index, fd, isp_sizes[index]);
    return fd;
}
static int isp_translate(int index, size_t length, unsigned long long *offset) {
    if (index == ISP_CSM || index == ISP_MIPI) return error(19);
    if (index == ISP_3A && *offset >= ISP_3A_MEMORY && *offset-ISP_3A_MEMORY < 0x100000 && length <= 0x100000-(*offset-ISP_3A_MEMORY)) {
        *offset = 0xb000+(*offset-ISP_3A_MEMORY); return 0;
    }
    if (index == ISP_BWM) {
        /* BWM exposes PMU plus low-speed DDR resources, in separate windows. */
        if (*offset >= isp_physical[index] && *offset-isp_physical[index] < 0x5000 && length <= 0x5000-(*offset-isp_physical[index])) {
            *offset -= isp_physical[index]; return 0;
        }
        if (*offset >= 0x50000000U && *offset-0x50000000U < 0x1000 && length <= 0x1000-(*offset-0x50000000U)) {
            *offset = 0x5000+(*offset-0x50000000U); return 0;
        }
        return error(22);
    }
    u32 size = (isp_sizes[index]+4095U)&~4095U;
    if (*offset < isp_physical[index] || *offset-isp_physical[index] >= size || length > size-(*offset-isp_physical[index])) return error(22);
    *offset -= isp_physical[index];
    return 0;
}
static int isp_delay(u32 milliseconds) {
    /* The released NX300M config uses HZ=200. Preserve jiffy rounding. */
    unsigned long long remaining = ((unsigned long long)milliseconds+4)/5*5;
    while (remaining) {
        int interval = remaining > 0x7fffffffU ? 0x7fffffff : (int)remaining;
        if (checked(raw(168, 0, 0, interval, 0, 0, 0)) < 0) return -1;
        remaining -= interval;
    }
    return 0;
}
static int isp_wait_timeout(void *arg) {
    if (!arg) return error(14);
    u32 timeout = *(u32 *)arg;
    if (isp_delay(timeout)) return -1;
    *(int *)arg = -1; /* Kernel wait_for_completion_timeout reports no IRQ. */
    return 0;
}
static int isp_wait_indefinite(void) { return checked(raw(168, 0, 0, -1, 0, 0, 0)); }
static void isp_mask_at(int index, u32 offset, u32 mask, int enable) {
    volatile u32 *reg = isp_registers[index];
    isp_lock();
    if (enable) reg[offset/4] |= mask; else reg[offset/4] &= ~mask;
    isp_unlock();
}
static void isp_interrupt_mask(int index, u32 mask, int enable) { isp_mask_at(index, 8, mask, enable); }
static int isp_dma_mask(int index, u32 *p, int enable, int errors) {
    if (!p) return error(14);
    u32 mask = 0;
    if (index == ISP_IPCS) {
        for (int c = 0; c < 4; c++) if (p[c]) mask |= 1U << (c*3+(errors ? 2 : 0));
    } else {
        if (p[0]) mask |= 1U << (errors ? 2 : 0);
        if (p[1]) mask |= 1U << (errors ? 5 : p[2] ? 18 : 3);
        if (p[3]) mask |= 1U << (errors ? 8 : 6);
    }
    isp_interrupt_mask(index, mask, enable);
    if (index == ISP_IPCM && enable && !errors)
        ipc_reset_dma((p[0] ? 1U : 0) | (p[1] ? 2U : 0) | (p[3] ? 4U : 0));
    return 0;
}
#include "mipi_transport.h"
static int isp_ioctl(int index, u32 request, void *arg) {
    if (__sync_fetch_and_add(&isp_log_count[index][request&63], 1) < 4) event("ISP ioctl", index, request, (u32)arg);
    if ((request >> 30) && !arg) return error(14);
    if (index == ISP_MIPI) return mipi_ioctl(request, arg);
    if (index == ISP_PP) {
        switch (request) {
        case 0x8004743fU: *(u32 *)arg = isp_physical[index]; return 0;
        case 0x7401U: return pp_event_ioctl(request, arg);
        case 0x7402U: isp_irq_open[index] = 0; return 0;
        case 0x740fU: case 0x7410U: case 0x7413U: return pp_event_ioctl(request, arg);
        case 0x7411U:
            isp_lock();
            if (!(isp_registers[index][0x20/4]&0x07800000U)) {
                u32 mipi = isp_registers[index][0x24/4]&0x07800000U;
                isp_registers[index][0x0c/4] = 1;
                isp_registers[index][0x24/4] = mipi;
            }
            isp_unlock(); return 0;
        case 0x40047433U: isp_clock[index] = *(u32 *)arg; return 0;
        case 0x40047434U: isp_power[index] = *(u32 *)arg; return 0;
        case 0x40047412U: return pp_event_ioctl(request, arg);
        case 0x740aU: case 0x740bU: case 0x740cU: case 0x740dU: case 0x740eU:
        case 0x7425U: case 0x7426U: return pp_event_ioctl(request, arg);
        case 0x7427U: case 0x7428U: case 0x741eU: return pp_event_ioctl(request, arg);
        }
    } else if (index == ISP_IPCM || index == ISP_IPCS) {
        int still = index == ISP_IPCS;
        if (request == (still ? 0xc0087303U : 0xc0086d01U)) {
            u32 *p = arg; p[0] = isp_physical[index]; p[1] = isp_sizes[index]; return 0;
        }
        if (request == (still ? 0x80047301U : 0x80046d06U)) {
            isp_irq_open[index] = 1; *(int *)arg = 0; return 0;
        }
        if (request == (still ? 0x7302U : 0x6d07U)) {
            isp_irq_open[index] = 0; isp_interrupt_mask(index, 0xffffffffU, 0); return 0;
        }
        if (request == (still ? 0x40047305U : 0x40046d02U)) { isp_clock[index] = *(u32 *)arg; return 0; }
        if (still && request == 0x40047304U) { isp_power[index] = *(u32 *)arg; return 0; }
        if (request == (still ? 0x4010730aU : 0x40106d08U)) return isp_dma_mask(index, arg, 1, 0);
        if (request == (still ? 0x4010730bU : 0x40106d09U)) return isp_dma_mask(index, arg, 0, 0);
        if ((still && request >= 0xc004730cU && request <= 0xc004730fU) ||
                (!still && (request == 0xc0046d0cU || request == 0xc0046d0dU))) return isp_wait_timeout(arg);
        if (!still && request >= 0xc0046d03U && request <= 0xc0046d05U) return ipc_wait_dma(request-0xc0046d03U, arg);
        if (still && request == 0xc0187310U) {
            u32 *p = arg;
            for (int i = 0; i < 4; i++) if (p[i]) { if (isp_delay(p[4])) return -1; ((int *)p)[5]--; p[i] = 0; }
            return 0;
        }
        if (!still) {
            switch (request) {
            case 0x40106d0aU: return isp_dma_mask(index, arg, 1, 1);
            case 0x40106d0bU: return isp_dma_mask(index, arg, 0, 1);
            case 0x6d0eU: isp_irq_open[index] |= 2; return 0;
            case 0x6d0fU: isp_irq_open[index] &= ~2U; isp_mask_at(index, 0x638, 0xffffffffU, 0); return 0;
            case 0x40086d10U: case 0x40086d11U: {
                u32 *p = arg, mask = (p[0] ? 1U : 0) | (p[1] ? 1U<<12 : 0);
                isp_mask_at(index, 0x638, mask, request == 0x40086d10U); return 0;
            }
            case 0x6d12U:
                if (!__sync_bool_compare_and_swap(&isp_ldcm_locked, 0, 1)) return error(1);
                return 0;
            case 0x6d13U: __sync_lock_release(&isp_ldcm_locked); return 0;
            case 0x6d14U: isp_irq_open[index] |= 4; return 0;
            case 0x6d15U: isp_irq_open[index] &= ~4U; isp_mask_at(index, 0x724, 0xffffffffU, 0); return 0;
            case 0x40046d16U: case 0x40046d17U:
                if (*(u32 *)arg >= 27) return error(22);
                isp_mask_at(index, 0x724, 1U<<*(u32 *)arg, request == 0x40046d17U); return 0; /* active low */
            case 0x40086d18U:
                if (isp_delay(*(u32 *)arg)) return -1;
                return error(1);
            case 0x40106d19U: return isp_dma_mask(index, arg, 1, 0);
            case 0x40106d1aU: return isp_dma_mask(index, arg, 0, 0);
            }
        }
    } else if (index == ISP_SSIF) {
        switch (request) {
        case 0x7300U: return 0;
        case 0x7301U: return ssif_open_irq();
        case 0x7302U: isp_irq_open[index] = 0; return 0;
        case 0x4004730aU: return ssif_wait_interrupt(*(u32 *)arg);
        case 0x40047314U: isp_clock[index] = *(u32 *)arg; return 0;
        case 0x7315U: return ssif_check_fps(); /* Counts are not exposed as synthetic frame data. */
        case 0x40047332U: return isp_delay(*(u32 *)arg);
        }
    } else if (index == ISP_BWM && request == 0x80106d01U) {
        u32 *p = arg; p[0] = isp_physical[index]; p[1] = 0x5000; p[2] = 0x50000000U; p[3] = 0x1000; return 0;
    } else if (index == ISP_3A) {
        switch (request) {
        case 0x6101U: isp_irq_open[index] = 1; return 0;
        case 0x6102U: isp_irq_open[index] = 0; return 0;
        case 0x610aU: return isp_wait_indefinite();
        case 0x610bU: isp_registers[ISP_PP][0x270/4] = ISP_3A_MEMORY; return 0;
        case 0x6114U: isp_3a_disable_after_capture = 1; return 0;
        }
    } else if (index == ISP_CSM) return csm_ioctl(request, arg);
    event("unsupported ISP ioctl", index, request, (u32)arg);
    return error(25);
}
#include "pp_memory_task_impl.h"
