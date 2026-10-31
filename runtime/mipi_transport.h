/* Samsung NX300M d4_mipi_ioctl.c / d4_mipi_ctrl_dd.c transport.
 * CSIM/CSIS/CON share the original PP mapping. Registration updates masks;
 * no packets, frame completion, PHY lock or DMA result is generated here.
 */
static unsigned char mipi_irq_enabled[2], mipi_callback_registered[2];
static void mipi_mask_callbacks(int receiver, int enabled) {
    u32 offset = receiver ? 0xa010 : 0x9030;
    u32 bits = receiver ? 0xf000103fU : 0xc1000003U;
    volatile u32 *registers = isp_registers[ISP_PP];
    isp_lock();
    if (enabled) registers[offset/4] &= ~bits;
    else registers[offset/4] |= bits;
    mipi_callback_registered[receiver] = !!enabled;
    isp_unlock();
}
static int mipi_wait_without_event(u32 jiffies) {
    unsigned long long milliseconds = (unsigned long long)jiffies*5;
    while (milliseconds) {
        u32 part = milliseconds > 2147483645u ? 2147483645u : (u32)milliseconds;
        if (isp_delay(part)) return -1;
        milliseconds -= part;
    }
    return 0; /* Kernel wait_for_completion_timeout: zero means timeout. */
}
static int mipi_ioctl(u32 request, void *arg) {
    if (request == 0x80046d00U) {
        *(u32 *)arg = 0x50048000U; /* First platform resource, MIPI CON. */
        return 1; /* Driver retains access_ok's positive result. */
    }
    if (request >= 0x6d01U && request <= 0x6d06U) {
        int receiver = !(request & 1);
        if (request <= 0x6d02U) mipi_irq_enabled[receiver] = 1;
        else if (request <= 0x6d04U) mipi_irq_enabled[receiver] = 0;
        /* INIT_COMPLETION and OPEN_IRQ reset an empty completion here.
         * Released _IO cases leave err=MIPI_FAIL, despite their side effect. */
        return error(1);
    }
    if (request >= 0x80046d07U && request <= 0x80046d0aU) {
        int receiver = !(request & 1);
        mipi_mask_callbacks(receiver, request <= 0x80046d08U);
        *(int *)arg = 0;
        return 0;
    }
    if (request == 0x80046d0bU || request == 0x80046d0cU) {
        /* Unlike PP waits, this ioctl copies the unchanged input timeout back. */
        return mipi_wait_without_event(*(u32 *)arg);
    }
    if (request >= 0x80046d0dU && request <= 0x80046d0fU) {
        event("MIPI DPC PDMA not implemented", request, 0, 0);
        return error(95);
    }
    event("unsupported MIPI ioctl", request, (u32)arg, 0);
    return error(25);
}
