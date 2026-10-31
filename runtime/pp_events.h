/* PP core's released ISR/completion/poll interface. Inputs are explicit IRQ
 * status snapshots; this backend performs no DMA or pixel processing.
 */
enum { PP_WDMA, PP_RDMA, PP_MIPI_RX, PP_MIPI_TX, PP_LUT_LOAD, PP_LUT_GEN, PP_VFPN, PP_COMPLETIONS };
#define PP_EVENT_MASK 0x04e803fdU
#define PP_LUT_LOAD_MASK 0x000001f1U
static int pp_events_initialized, pp_input = -1, pp_notification = -1;
static int pp_completions[PP_COMPLETIONS], pp_mipi_rx_callback, pp_mipi_tx_callback;
static unsigned char pp_queue[512];
static u32 pp_queue_head, pp_queue_count, pp_events_seen, pp_read_traces;
static volatile int pp_event_lock_word;
static void pp_event_lock(void) { while (__sync_lock_test_and_set(&pp_event_lock_word, 1)) raw(158, 0, 0, 0, 0, 0, 0); }
static void pp_event_unlock(void) { __sync_lock_release(&pp_event_lock_word); }
static void pp_enqueue(u32 type) {
    if (pp_queue_count == 512) return;
    pp_queue[(pp_queue_head+pp_queue_count)%512] = type;
    ++pp_queue_count; ssif_complete(pp_notification);
}
static void pp_reset_wdma_completion(void) {
    if (pp_events_initialized) ssif_drain(pp_completions[PP_WDMA]);
}
static void pp_dispatch(u32 status) {
    volatile u32 *pp = isp_registers[ISP_PP];
    if (!pp || !isp_irq_open[ISP_PP] || !(pp[0x24/4]&status)) {
        event("PP explicit IRQ snapshot masked", status, isp_irq_open[ISP_PP], pp ? pp[0x24/4] : 0); return;
    }
    /* One supplied snapshot enters the ISR once. No masked status latching,
     * electrical interrupt line or register-write-triggered IRQ is modeled. */
    isp_lock();
    pp[0x20/4] = status;
    if (status&(1U<<19)) {
        pp[0x3314/4] &= ~(1U<<28);
        pp[0x24/4] &= ~(1U<<19);
        pp[0x28/4] = 1U<<19;
        ssif_complete(pp_completions[PP_WDMA]);
        pp_enqueue(11); /* d4_lib.h INTR_PP_WDMA_WRITE_DONE */
        isp_csm_wdma_state[1] = 1;
    }
    pp[0x24/4] &= ~status | (1U<<26) | (1U<<23);
    if (status&(1U<<22)) {
        pp[0x28/4] = 1U<<22;
        ssif_complete(pp_completions[PP_RDMA]); pp_enqueue(12);
    }
    if (status&(1U<<2)) ssif_complete(pp_completions[PP_LUT_GEN]);
    if (status&(1U<<9)) ssif_complete(pp_completions[PP_VFPN]);
    if (status&PP_LUT_LOAD_MASK) ssif_complete(pp_completions[PP_LUT_LOAD]);
    else if (status&(1U<<21)) isp_csm_wdma_state[0] = 1;
    if (status&(1U<<26)) {
        pp[0x28/4] = 1U<<26;
        if (pp_mipi_tx_callback) { ssif_complete(pp_completions[PP_MIPI_TX]); pp_mipi_tx_callback = 0; }
    }
    if (status&(1U<<23)) {
        pp[0x28/4] = 1U<<23;
        /* PP_MIPI_POLL_ENABLE routes RX to the queue, not rx_completion. */
        if (pp_mipi_rx_callback) pp_enqueue(13);
    }
    pp[0x20/4] = 0; /* Supplied snapshot consumed; no pending/latch model. */
    isp_unlock();
    event("PP explicit IRQ snapshot dispatched", status, pp_queue_count, 0);
}
static void *pp_event_worker(void *unused) {
    (void)unused;
    u32 number = 0; int digits = 0, invalid = 0;
    for (;;) {
        struct allegro_poll descriptor = {pp_input, 1, 0};
        if (raw(168, (long)&descriptor, 1, -1, 0, 0, 0) <= 0) continue;
        char input[64];
        int size = raw(3, pp_input, (long)input, sizeof(input), 0, 0, 0);
        for (int i = 0; i < size; i++) {
            unsigned char value = input[i];
            if (value >= '0' && value <= '9') {
                if (number > 429496729U || (number == 429496729U && value > '5')) invalid = 1;
                if (!invalid) number = number*10+value-'0';
                digits = 1;
            } else if (value == '\n' || value == '\r' || value == ' ' || value == '\t') {
                if (digits && !invalid && number && !(number&~PP_EVENT_MASK)) {
                    pp_event_lock(); ++pp_events_seen; pp_dispatch(number); pp_event_unlock();
                }
                number = 0; digits = invalid = 0;
            } else invalid = 1;
        }
    }
    return (void *)0;
}
static int pp_event_initialize(void) {
    pp_event_lock();
    if (pp_events_initialized) { pp_event_unlock(); return 0; }
    const char *path = "/var/run/nx300m-pp-events";
    long result = raw(14, (long)path, 0010600, 0, 0, 0, 0);
    if (result < 0 && result != -17) { pp_event_unlock(); return checked(result); }
    pp_input = checked(raw(5, (long)path, 2|04000|02000000, 0, 0, 0, 0));
    if (pp_input < 0) { pp_event_unlock(); return -1; }
    for (int i = 0; i < PP_COMPLETIONS; i++) pp_completions[i] = -1;
    pp_notification = eventfd(0, 1|04000|02000000);
    int failure = pp_notification < 0 ? *__errno_location() : 0;
    for (int i = 0; i < PP_COMPLETIONS && !failure; i++) {
        pp_completions[i] = eventfd(0, 1|04000|02000000);
        if (pp_completions[i] < 0) failure = *__errno_location();
    }
    if (!failure) {
        unsigned long worker;
        failure = pthread_create(&worker, (void *)0, pp_event_worker, (void *)0);
        if (!failure) pthread_detach(worker);
    }
    if (failure) {
        raw(6, pp_input, 0, 0, 0, 0, 0);
        if (pp_notification >= 0) raw(6, pp_notification, 0, 0, 0, 0, 0);
        for (int i = 0; i < PP_COMPLETIONS; i++) if (pp_completions[i] >= 0) raw(6, pp_completions[i], 0, 0, 0, 0, 0);
        pp_event_unlock(); return error(failure);
    }
    pp_events_initialized = 1;
    pp_event_unlock();
    event("PP explicit IRQ FIFO ready", PP_EVENT_MASK, PP_COMPLETIONS, 0);
    return 0;
}
static int pp_read_interrupt(int fd, void *buffer, u32 size) {
    if (!buffer) return error(14);
    int nonblocking = raw(55, fd, 3, 0, 0, 0, 0)&04000;
    int ready = ssif_wait(pp_notification, nonblocking ? 0 : -1);
    if (ready < 0) return -1;
    if (!ready) return error(11);
    pp_event_lock();
    *(unsigned char *)buffer = pp_queue[pp_queue_head];
    pp_queue_head = (pp_queue_head+1)%512;
    int remaining = --pp_queue_count;
    if (pp_read_traces++ < 16) event("PP original reader consumed", size >= 4 ? *(u32 *)buffer : *(unsigned char *)buffer, remaining, size);
    pp_event_unlock();
    return remaining;
}
static int pp_event_ioctl(u32 request, void *arg) {
    if (request == 0x7401U) {
        pp_event_lock();
        for (int i = 0; i < PP_COMPLETIONS; i++) ssif_drain(pp_completions[i]);
        pp_mipi_rx_callback = pp_mipi_tx_callback = 0;
        isp_irq_open[ISP_PP] = 1;
        pp_event_unlock(); return 0;
    }
    int completion = -1, initialize = 0, timeout = -1;
    switch (request) {
    case 0x740aU: completion = PP_LUT_LOAD; break;
    case 0x740bU: completion = PP_LUT_GEN; break;
    case 0x740cU: completion = PP_VFPN; break;
    case 0x740dU: completion = PP_WDMA; break;
    case 0x740eU: completion = PP_RDMA; break;
    case 0x740fU: completion = PP_WDMA; initialize = 1; break;
    case 0x7410U: completion = PP_RDMA; initialize = 1; break;
    case 0x7413U: completion = PP_LUT_LOAD; initialize = 1; break;
    case 0x7425U: completion = PP_MIPI_RX; break;
    case 0x7426U: completion = PP_MIPI_TX; break;
    case 0x40047412U:
        completion = PP_LUT_LOAD;
        timeout = *(u32 *)arg > 2147483640U ? 2147483645 : (int)((*(u32 *)arg+4)/5*5);
        break;
    case 0x7427U: pp_event_lock(); pp_mipi_rx_callback = 1; pp_event_unlock(); return 0;
    case 0x7428U: pp_event_lock(); pp_mipi_rx_callback = 0; pp_event_unlock(); return 0;
    case 0x741eU: pp_event_lock(); pp_mipi_tx_callback = 1; pp_event_unlock(); return 0;
    default: return error(25);
    }
    if (initialize) { pp_event_lock(); ssif_drain(pp_completions[completion]); pp_event_unlock(); return 0; }
    int result = ssif_wait(pp_completions[completion], timeout);
    if (result < 0) return -1;
    return result ? 0 : error(1);
}
u32 nx300m_pp_events_seen(void) { pp_event_lock(); u32 result = pp_events_seen; pp_event_unlock(); return result; }
