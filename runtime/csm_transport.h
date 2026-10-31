/* Samsung CSM/SSIF event transport. Only explicit FIFO input dispatches an
 * SSIF interrupt. CSM's released timeout fallback arms WDMA without a frame.
 */
enum { SSIF_INTERRUPTS = 13 };
enum { SSIF_CALLBACK_NONE, SSIF_CALLBACK_WAIT, SSIF_CALLBACK_CSM, SSIF_CALLBACK_QUEUE, SSIF_CALLBACK_FPS };
struct csm_spi_command { u32 channel, address, data; };
struct csm_wait_command { u32 reference; struct csm_spi_command sensor[2]; };
struct csm_read_command { u32 reference, d4c_enabled; struct csm_spi_command d4c[11], sensor[2]; };
static int ssif_initialized, ssif_fifo = -1, ssif_completions[SSIF_INTERRUPTS], csm_completions[7];
static unsigned char ssif_callbacks[SSIF_INTERRUPTS];
static volatile int ssif_lock_word;
static struct csm_wait_command csm_wait_data;
static struct csm_read_command csm_read_data;
static int csm_command, csm_read_started, csm_pending;
static int ssif_notification = -1;
static unsigned char ssif_queue[512];
static u32 ssif_queue_head, ssif_queue_count;
static u32 ssif_events_seen;
static void ssif_lock(void) { while (__sync_lock_test_and_set(&ssif_lock_word, 1)) raw(158, 0, 0, 0, 0, 0, 0); }
static void ssif_unlock(void) { __sync_lock_release(&ssif_lock_word); }
static void ssif_complete(int fd) {
    unsigned long long one = 1;
    raw(4, fd, (long)&one, 8, 0, 0, 0);
}
static void ssif_drain(int fd) {
    unsigned long long value;
    while (raw(3, fd, (long)&value, 8, 0, 0, 0) == 8) { }
}
static int ssif_wait(int fd, int timeout) {
    struct allegro_poll descriptor = {fd, 1, 0};
    int ready = checked(raw(168, (long)&descriptor, 1, timeout, 0, 0, 0));
    if (ready <= 0) return ready;
    unsigned long long value;
    return checked(raw(3, fd, (long)&value, 8, 0, 0, 0)) == 8 ? 1 : -1;
}
static void csm_spi_write(const struct csm_spi_command *command, u32 length) {
    /* CSM's NX300M kernel contexts are only SPI 1 and 5. The original driver
     * ignores polling failures; retain that behavior and log each result. */
    if ((command->channel != 1 && command->channel != 5) || !spi_registers[command->channel]) {
        event("CSM SPI unavailable", command->channel, command->address, command->data); return;
    }
    void *(*stock_open)(u32) = dlsym((void *)0, "spi_open");
    int (*stock_write)(void *, void *) = dlsym((void *)0, "spi_polling_write");
    if (!stock_open || !stock_write) { event("CSM SPI symbols absent", command->channel, 0, 0); return; }
    void *channel = stock_open(command->channel); /* Original singleton, no reconfiguration. */
    if (!channel) { event("CSM SPI handle absent", command->channel, 0, 0); return; }
    unsigned short sensor[2] = {(unsigned short)command->data, (unsigned short)command->address};
    u32 d4c[2] = {0x10000U|(command->address&0xffffU), command->data};
    struct { void *write_buffer, *read_buffer; u32 length; } transfer = {length == 1 ? (void *)sensor : (void *)d4c, (void *)0, length};
    int result = stock_write(channel, &transfer);
    event("CSM stock SPI write", command->channel, command->address, (u32)result);
}
static void csm_readout_start(void) {
    if (csm_read_data.d4c_enabled)
        for (int i = 0; i < 11; i++) csm_spi_write(csm_read_data.d4c+i, 2);
    volatile u32 *pp = isp_registers[ISP_PP];
    if (pp) {
        isp_lock();
        u32 reset = pp[0x2400/4]&~1U;
        pp[0x2400/4] = reset; pp[0x2400/4] = reset|1; pp[0x2400/4] = reset;
        u32 done_mask = (1U<<19)|(1U<<21);
        pp[0x28/4] = done_mask;
        pp[0x24/4] |= done_mask;
        pp[0x3a10/4] |= 2;
        pp[0x3a04/4] = 0;
        pp_reset_wdma_completion();
        pp[0x3314/4] = (pp[0x3314/4]&~(7U<<28))|(1U<<28);
        /* Retain exactly the programmed transport at arm time, before timeout
         * cleanup can overwrite it. This is an observation, not a DMA input. */
        int snapshot = checked(raw(5, (long)"/var/run/nx300m-readout-pp.bin", 0102|01000|02000000, 0600, 0, 0, 0));
        if (snapshot >= 0) {
            long bytes = raw(4, snapshot, (long)pp, 0xb000, 0, 0, 0);
            raw(6, snapshot, 0, 0, 0, 0, 0);
            event("CSM readout register snapshot bytes", (u32)bytes, pp[0x3304/4], pp[0x3308/4]);
        }
        const char *names[4] = {"cf_pp_sensor_get_global_width", "cf_pp_sensor_get_global_height",
            "cf_pp_sensor_get_effective_width", "cf_pp_sensor_get_effective_height"};
        u32 dimensions[4] = {0};
        for (int i = 0; i < 4; i++) {
            u32 (*getter)(u32) = dlsym((void *)0, names[i]);
            if (getter) dimensions[i] = getter(3);
        }
        snapshot = checked(raw(5, (long)"/var/run/nx300m-readout-dimensions.bin", 0102|01000|02000000, 0600, 0, 0, 0));
        if (snapshot >= 0) {
            raw(4, snapshot, (long)dimensions, sizeof dimensions, 0, 0, 0);
            raw(6, snapshot, 0, 0, 0, 0, 0);
        }
        isp_unlock();
    }
    csm_spi_write(csm_read_data.sensor, 1);
    event("CSM WDMA armed; no image completion", 0, 0, 0);
    pp_replay_arm();
}
/* Caller holds ssif_lock, like the released driver's serialized IRQ handler. */
static void ssif_dispatch(u32 selection) {
    volatile u32 *pp = isp_registers[ISP_PP];
    if (selection >= SSIF_INTERRUPTS || !pp || !isp_irq_open[ISP_SSIF] || (pp[0x2440/4]&(1U<<selection))) {
        event("SSIF explicit event masked", selection, isp_irq_open[ISP_SSIF], pp ? pp[0x2440/4] : ~0U); return;
    }
    pp[0x2444/4] |= 1U<<selection;
    pp[0x2444/4] &= ~(1U<<selection);
    int callback = ssif_callbacks[selection];
    if (callback == SSIF_CALLBACK_WAIT) ssif_complete(ssif_completions[selection]);
    else if (callback == SSIF_CALLBACK_CSM) {
        if (selection == 1) {
            if (csm_command == 0) for (int i = 0; i < 2; i++) csm_spi_write(csm_wait_data.sensor+i, 1);
            else if (!csm_read_started) { csm_read_started = 1; csm_readout_start(); }
        }
        /* The released ISR always restores USER_DEFINE_0 and completes [1],
         * even if a different reference interrupt was registered. */
        ssif_callbacks[1] = SSIF_CALLBACK_QUEUE;
        ssif_complete(csm_completions[1]);
    } else if (callback == SSIF_CALLBACK_QUEUE && ssif_queue_count < 512) {
        ssif_queue[(ssif_queue_head+ssif_queue_count)%512] = selection;
        ++ssif_queue_count;
        ssif_complete(ssif_notification);
    }
    if (selection == 0) pp_replay_frame_end();
    event("SSIF explicit event dispatched", selection, callback, 0);
}
static void *ssif_worker(void *unused) {
    (void)unused;
    u32 number = 0; int digits = 0, invalid = 0;
    for (;;) {
        struct allegro_poll descriptor = {ssif_fifo, 1, 0};
        if (raw(168, (long)&descriptor, 1, -1, 0, 0, 0) <= 0) continue;
        char input[64];
        int size = raw(3, ssif_fifo, (long)input, sizeof(input), 0, 0, 0);
        for (int i = 0; i < size; i++) {
            unsigned char value = input[i];
            if (value >= '0' && value <= '9') {
                if (number > 12) invalid = 1;
                if (!invalid) number = number*10+value-'0';
                digits = 1;
            } else if (value == '\n' || value == ' ' || value == '\r' || value == '\t') {
                if (digits && !invalid && number < SSIF_INTERRUPTS) {
                    ssif_lock(); ++ssif_events_seen; ssif_dispatch(number); ssif_unlock();
                }
                number = 0; digits = invalid = 0;
            } else invalid = 1;
        }
    }
    return (void *)0;
}
static int ssif_transport_initialize(void) {
    ssif_lock();
    if (ssif_initialized) { ssif_unlock(); return 0; }
    const char *path = "/var/run/nx300m-ssif-events";
    long result = raw(14, (long)path, 0010600, 0, 0, 0, 0);
    if (result < 0 && result != -17) { ssif_unlock(); return checked(result); }
    ssif_fifo = checked(raw(5, (long)path, 2|04000|02000000, 0, 0, 0, 0));
    if (ssif_fifo < 0) { ssif_unlock(); return -1; }
    for (int i = 0; i < SSIF_INTERRUPTS; i++) ssif_completions[i] = -1;
    for (int i = 0; i < 7; i++) csm_completions[i] = -1;
    ssif_notification = eventfd(0, 1|04000|02000000);
    int failure = ssif_notification < 0 ? *__errno_location() : 0;
    for (int i = 0; i < SSIF_INTERRUPTS+7; i++) {
        if (failure) break;
        int fd = eventfd(0, 1|04000|02000000);
        if (fd < 0) { failure = *__errno_location(); break; }
        if (i < SSIF_INTERRUPTS) ssif_completions[i] = fd; else csm_completions[i-SSIF_INTERRUPTS] = fd;
    }
    if (!failure) {
        unsigned long worker;
        failure = pthread_create(&worker, (void *)0, ssif_worker, (void *)0);
        if (!failure) pthread_detach(worker);
    }
    if (failure) {
        raw(6, ssif_fifo, 0, 0, 0, 0, 0);
        if (ssif_notification >= 0) raw(6, ssif_notification, 0, 0, 0, 0, 0);
        for (int i = 0; i < SSIF_INTERRUPTS; i++) if (ssif_completions[i] >= 0) raw(6, ssif_completions[i], 0, 0, 0, 0, 0);
        for (int i = 0; i < 7; i++) if (csm_completions[i] >= 0) raw(6, csm_completions[i], 0, 0, 0, 0, 0);
        ssif_unlock(); return error(failure);
    }
    ssif_initialized = 1;
    ssif_unlock();
    event("SSIF explicit event FIFO ready", SSIF_INTERRUPTS, 0, 0);
    return 0;
}
static int ssif_open_irq(void) {
    ssif_lock();
    for (int i = 0; i < SSIF_INTERRUPTS; i++) { ssif_callbacks[i] = SSIF_CALLBACK_QUEUE; ssif_drain(ssif_completions[i]); }
    isp_irq_open[ISP_SSIF] = 1;
    ssif_unlock(); return 0;
}
static int ssif_wait_interrupt(u32 selection) {
    if (selection >= SSIF_INTERRUPTS) return error(22);
    ssif_lock(); ssif_callbacks[selection] = SSIF_CALLBACK_WAIT; ssif_unlock();
    return ssif_wait(ssif_completions[selection], -1) < 0 ? -1 : 0;
}
static int ssif_read_interrupt(int fd, void *buffer) {
    if (!buffer) return error(14);
    int nonblocking = raw(55, fd, 3, 0, 0, 0, 0)&04000;
    int ready = ssif_wait(ssif_notification, nonblocking ? 0 : -1);
    if (ready < 0) return -1;
    if (!ready) return error(11);
    ssif_lock();
    *(unsigned char *)buffer = ssif_queue[ssif_queue_head];
    ssif_queue_head = (ssif_queue_head+1)%512;
    int remaining = --ssif_queue_count;
    ssif_unlock();
    /* Released driver puts one byte, returning remaining queue length rather
     * than byte count. Keep this ABI for libudd's original interrupt thread. */
    return remaining;
}
static int ssif_check_fps(void) {
    ssif_lock(); ssif_callbacks[7] = SSIF_CALLBACK_FPS;
    if (isp_registers[ISP_PP]) isp_registers[ISP_PP][0x2440/4] &= ~(1U<<7);
    ssif_unlock(); return 0;
}
/* Probe observation only; no event or completion is produced by these getters. */
int nx300m_csm_pending(void) { ssif_lock(); int result = csm_pending; ssif_unlock(); return result; }
u32 nx300m_ssif_events_seen(void) { ssif_lock(); u32 result = ssif_events_seen; ssif_unlock(); return result; }
int nx300m_ssif_waiting(u32 selection) {
    if (selection >= SSIF_INTERRUPTS) return 0;
    ssif_lock(); int result = ssif_callbacks[selection] == SSIF_CALLBACK_WAIT; ssif_unlock(); return result;
}
static int csm_ioctl(u32 request, void *arg) {
    if (request == 0xc0086303U) {
        u32 *p = arg;
        isp_lock(); p[0] = isp_csm_wdma_state[0]; p[1] = isp_csm_wdma_state[1]; isp_unlock();
        return 0;
    }
    if (request != 0xc01c6301U && request != 0xc0a46302U) return error(25);
    u32 reference = *(u32 *)arg;
    if (reference >= 7) return error(22);
    int readout = request == 0xc0a46302U;
    ssif_lock();
    csm_command = readout; csm_read_started = 0;
    if (readout) {
        csm_read_data = *(struct csm_read_command *)arg;
        isp_lock(); isp_csm_wdma_state[0] = isp_csm_wdma_state[1] = 0; isp_unlock();
    }
    else csm_wait_data = *(struct csm_wait_command *)arg;
    ssif_drain(csm_completions[reference]);
    ssif_callbacks[reference] = SSIF_CALLBACK_CSM;
    csm_pending = readout ? 2 : 1;
    ssif_unlock();
    event("CSM awaiting explicit SSIF", readout, reference, readout ? 1000 : 200);
    int result = ssif_wait(csm_completions[reference], readout ? 5000 : 1000);
    if (readout) isp_delay(5);
    ssif_lock();
    int fallback = 0;
    if (readout) {
        /* Source writes sensor[1] after the wait, before its timeout fallback. */
        csm_spi_write(csm_read_data.sensor+1, 1);
        if (result <= 0 && !csm_read_started) {
            csm_read_started = 1; ssif_callbacks[1] = SSIF_CALLBACK_QUEUE;
            csm_readout_start(); fallback = 1;
        }
    }
    csm_pending = 0;
    ssif_unlock();
    event("CSM wait returned", readout, (u32)result, fallback);
    /* wait-VD ignores a timeout; readout reports EPERM only on fallback. */
    return fallback ? error(1) : 0;
}
