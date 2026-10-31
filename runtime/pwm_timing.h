/* Finite PWM counter backend. Original libudd A32 loads/stores configure the
 * registers; only START, counter expiry and explicit input edges run the model.
 * The 7.5 ns lab tick follows libudd's period*10/75 conversion. It is not a
 * measured camera clock. No shutter state, callback or event flag is an input.
 * Continuous and intermediate-period IRQs are outside this backend's scope.
 */
struct pwm_counter {
    volatile u32 *registers;
    int timer, notification, handler, running;
    volatile int busy;
};
static struct pwm_counter pwm_counters[18];
static int pwm_timing_active, pwm_input = -1;
static int pwm_levels[9];
static u32 pwm_base(int channel) { return (u32)(channel%9)*0x100+(u32)(channel/9)*0x1000; }
static volatile u32 *pwm_regs(int channel) {
    volatile u32 *r = pwm_counters[channel].registers;
    return r ? r+pwm_base(channel)/4 : r;
}
static void pwm_lock(int channel) {
    while (__sync_lock_test_and_set(&pwm_counters[channel].busy, 1)) { }
}
static void pwm_unlock(int channel) { __sync_lock_release(&pwm_counters[channel].busy); }
static void pwm_cancel(int channel) {
    struct pwm_counter *counter = pwm_counters+channel;
    struct allegro_timer_spec spec = {{0, 0}, {0, 0}};
    timerfd_settime(counter->timer, 0, &spec, (void *)0);
    unsigned long long discarded;
    raw(3, counter->timer, (long)&discarded, 8, 0, 0, 0);
    counter->running = 0;
}
static void pwm_begin(int channel) {
    struct pwm_counter *counter = pwm_counters+channel;
    volatile u32 *r = pwm_regs(channel);
    if (!r || counter->running || !(r[0] & 1)) return;
    if (r[0] & 8) { event("PWM continuous counter unsupported", channel, r[0], r[1]); return; }
    unsigned long long cycles = (unsigned long long)((r[2] >> 16)+1)*(r[3]&65535)*(r[1] >> 24);
    cycles += (unsigned long long)((r[2]&65535)+1)*(r[4]&65535)*((r[1] >> 16)&255);
    unsigned long long nanoseconds = (cycles*15+1)/2;
    if (!nanoseconds) { event("PWM empty finite counter", channel, r[1], r[4]); return; }
    struct allegro_timer_spec spec = {{0, 0}, {(long)(nanoseconds/1000000000ULL), (long)(nanoseconds%1000000000ULL)}};
    if (timerfd_settime(counter->timer, 0, &spec, (void *)0)) {
        event("PWM timer arm failed", channel, (u32)*__errno_location(), 0); return;
    }
    counter->running = 1;
    event("PWM finite counter started ns", channel, (u32)nanoseconds, (u32)(nanoseconds >> 32));
}
static void pwm_input_level(int source, int level) {
    int previous = pwm_levels[source];
    pwm_levels[source] = level;
    event("PWM explicit external level", source, level, (u32)previous);
    if (previous < 0 || previous == level) return;
    for (int channel = 0; channel < 18; channel++) {
        pwm_lock(channel);
        struct pwm_counter *counter = pwm_counters+channel;
        volatile u32 *r = pwm_regs(channel);
        if (r && (r[0]&1) && (r[1]&6) == 6 && !counter->running) {
            int local = channel%9;
            u32 selector = counter->registers[(0x900+0x1000*(channel/9)+4*(local/5))/4];
            if (((selector >> ((local%5)*4))&15) == (u32)source &&
                level == !(r[1]&1)) pwm_begin(channel);
        }
        pwm_unlock(channel);
    }
}
static void *pwm_worker(void *unused) {
    (void)unused;
    struct allegro_poll descriptors[19];
    for (int i = 0; i < 18; i++) {
        descriptors[i].fd = pwm_counters[i].timer;
        descriptors[i].events = 1; descriptors[i].revents = 0;
    }
    descriptors[18].fd = pwm_input; descriptors[18].events = 1;
    u32 number = 0; int digits = 0, invalid = 0;
    for (;;) {
        if (raw(168, (long)descriptors, 19, -1, 0, 0, 0) <= 0) continue;
        for (int i = 0; i < 18; i++) if (descriptors[i].revents&1) {
            pwm_lock(i);
            struct pwm_counter *counter = pwm_counters+i;
            unsigned long long expirations;
            if (raw(3, counter->timer, (long)&expirations, 8, 0, 0, 0) == 8 && counter->running) {
                counter->running = 0;
                volatile u32 *r = pwm_regs(i);
                r[0] &= ~1U;
                /* The vendor ISR acknowledges INTSTATUS before waking poll.
                 * This scope implements the observed LAST_END mask (0x10). */
                if ((r[1]&0x70) == 0x10 && counter->handler) {
                    unsigned long long one = 1;
                    raw(4, counter->notification, (long)&one, 8, 0, 0, 0);
                    event("PWM finite last-end IRQ", i, r[1], r[4]);
                } else event("PWM finite counter ended without IRQ", i, r[1], counter->handler);
            }
            pwm_unlock(i);
        }
        if (!(descriptors[18].revents&1)) continue;
        char buffer[64];
        long count = raw(3, pwm_input, (long)buffer, sizeof buffer, 0, 0, 0);
        for (long i = 0; i < count; i++) {
            unsigned char byte = buffer[i];
            if (byte >= '0' && byte <= '9') {
                digits = 1;
                if (number > 17) invalid = 1;
                if (!invalid) number = number*10+byte-'0';
            } else if (byte == '\n' || byte == ' ' || byte == '\r') {
                if (digits && !invalid && number < 18) pwm_input_level(number/2, number&1);
                number = 0; digits = invalid = 0;
            } else invalid = 1;
        }
    }
    return (void *)0;
}
static int pwm_initialize(void) {
    if (pwm_timing_active) return 0;
    const char *enabled = getenv("NX300M_PWM_TIMING");
    if (!enabled || !equal(enabled, "1")) return 0;
    const char *path = "/var/run/nx300m-pwm-events";
    long status = raw(14, (long)path, 0010600, 0, 0, 0, 0);
    if (status < 0 && status != -17) return checked(status);
    pwm_input = checked(raw(5, (long)path, 2|04000|02000000, 0, 0, 0, 0));
    if (pwm_input < 0) return -1;
    for (int i = 0; i < 9; i++) pwm_levels[i] = -1;
    for (int i = 0; i < 18; i++) pwm_counters[i].timer = pwm_counters[i].notification = -1;
    int failure = 0;
    for (int i = 0; i < 18; i++) {
        pwm_counters[i].timer = timerfd_create(1, 04000|02000000);
        pwm_counters[i].notification = eventfd(0, 1|04000|02000000);
        if (pwm_counters[i].timer < 0 || pwm_counters[i].notification < 0) { failure = *__errno_location(); break; }
    }
    unsigned long worker;
    if (!failure) failure = pthread_create(&worker, (void *)0, pwm_worker, (void *)0);
    if (failure) {
        for (int i = 0; i < 18; i++) {
            if (pwm_counters[i].timer >= 0) raw(6, pwm_counters[i].timer, 0, 0, 0, 0, 0);
            if (pwm_counters[i].notification >= 0) raw(6, pwm_counters[i].notification, 0, 0, 0, 0, 0);
        }
        raw(6, pwm_input, 0, 0, 0, 0, 0); pwm_input = -1;
        return error(failure);
    }
    pthread_detach(worker);
    pwm_timing_active = 1;
    event("PWM finite timing; explicit input FIFO ready", 75, 10, 0);
    return 0;
}
static int pwm_register_open(int fd, int channel) {
    if (pwm_initialize()) return -1;
    if (!pwm_timing_active || pwm_counters[channel].registers) return 0;
    long mapped = checked(raw(192, 0, PWM_BYTES, 3, 1, fd, 0));
    if (mapped == -1) return -1;
    pwm_counters[channel].registers = (volatile u32 *)mapped;
    return 0;
}
static int pwm_access(int channel, u32 offset, int width, int write, u32 *value) {
    if ((offset&3)+(u32)width > 4) return 0;
    pwm_lock(channel);
    volatile u32 *r = pwm_counters[channel].registers;
    u32 word = offset/4, shift = (offset&3)*8;
    u32 mask = width == 4 ? 0xffffffffU : (1U << (width*8))-1;
    if (!write) *value = (r[word] >> shift)&mask;
    else {
        u32 old = r[word], next = (old & ~(mask << shift)) | ((*value&mask) << shift);
        if (word == pwm_base(channel)/4) {
            /* INTCLEAR is a write-one pulse, not a persistent bit in RAM. */
            if (next&4) {
                next &= ~6U;
                unsigned long long discarded;
                raw(3, pwm_counters[channel].notification, (long)&discarded, 8, 0, 0, 0);
            }
            r[word] = next;
            if (!(next&1)) pwm_cancel(channel);
            else if (!(old&1)) {
                volatile u32 *main = pwm_regs(channel);
                if (!(main[1]&4)) pwm_begin(channel);
                else event("PWM armed awaiting hardware trigger", channel, main[1], main[4]);
            }
        } else r[word] = next;
    }
    pwm_unlock(channel);
    return 1;
}
static int pwm_map(void *address, u32 size, int channel) {
    if (!pwm_timing_active) return 0;
    return mmio_register(address, size, channel, pwm_access);
}
