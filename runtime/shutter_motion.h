/* Optional ideal rotor for software development, not measured NX300M mechanics.
 * GPIO reads execute the original libudd instructions. No IRQ configuration,
 * requested count, capture state or callback is an input to the motion model.
 */
static unsigned int (*motion_gpio_get)(void *, u32);
static int motion_mode, motion_timer = -1, motion_status = -1;
static u32 motion_step_us = 10000, motion_sector, motion_edges, motion_stalls;
static long long motion_previous, motion_fraction;
static int motion_drive, motion_pins = -1;

static int motion_pin(int name) {
    int pin = allegro_gpio_number(name);
    if (pin < 0 || pin >= 224) return -1;
    void *bank = allegro_gpio_open((u32)pin/8);
    return bank ? (int)motion_gpio_get(bank, (u32)pin%8) : -1;
}
static void motion_publish(int pins, long long tick) {
    /* Fixed little-endian ARM words, v1; readers treat this as a snapshot. */
    u32 state[16] = {0x4d53584eU, 1, (u32)motion_mode, motion_step_us,
        motion_sector, (u32)motion_drive, (u32)pins,
        (u32)motion_pin(32), (u32)allegro_level, motion_edges, motion_stalls,
        (u32)tick, (u32)((unsigned long long)tick >> 32), 1000, 0, 0};
    raw(181, motion_status, (long)state, sizeof state, 0, 0, 0);
}
static void *motion_worker(void *unused) {
    (void)unused;
    struct allegro_poll wait = {motion_timer, 1, 0};
    for (;;) {
        if (raw(168, (long)&wait, 1, -1, 0, 0, 0) < 0) continue;
        unsigned long long expirations;
        if (raw(3, motion_timer, (long)&expirations, 8, 0, 0, 0) != 8) continue;
        allegro_lock();
        struct allegro_time now;
        if (clock_gettime(1, &now)) { allegro_unlock(); continue; }
        long long tick = (long long)now.seconds*1000000000LL + now.nanoseconds;
        int power = motion_pin(31), mot1 = motion_pin(36), mot2 = motion_pin(37);
        int pins = power < 0 || mot1 < 0 || mot2 < 0 ? -1 : power*4+mot1*2+mot2;
        /* Stock motorFORWARD=01, BRAKE=11, STANDBY=00. Reverse is an
         * ideal H-bridge assumption and has not been observed in the stock app. */
        int drive = power == 1 && mot1 >= 0 && mot2 >= 0 ? mot2-mot1 : 0;
        int changed = pins != motion_pins;
        if (changed) event("Allegro sampled motor power/MOT1/MOT2", (u32)pins,
            (u32)now.seconds*1000000U+(u32)now.nanoseconds/1000U, motion_sector);
        long long elapsed = motion_previous ? tick-motion_previous : 0;
        long long step = (long long)motion_step_us*1000;
        if (motion_mode == 2 && drive && drive == motion_drive) {
            if (elapsed > step) {
                /* Host scheduling cannot recreate already missed edge timing.
                 * Freeze for this interval instead of compressing many edges. */
                motion_stalls++;
                changed = 1;
            } else {
                motion_fraction += elapsed*drive;
                if (motion_fraction >= step || motion_fraction <= -step) {
                    int direction = motion_fraction > 0 ? 1 : -1;
                    motion_fraction -= step*direction;
                    motion_sector = (motion_sector+8+(u32)direction)%8;
                    allegro_pin(32, motion_sector < 4);
                    if (allegro_edge((int)(motion_sector & 1), tick, "Allegro ideal rotor PI2 edge"))
                        event("Allegro ideal rotor edge error", (u32)*__errno_location(), motion_sector, 0);
                    motion_edges++;
                    changed = 1;
                }
            }
        }
        motion_previous = tick;
        motion_drive = drive;
        motion_pins = pins;
        if (changed) motion_publish(pins, tick);
        allegro_unlock();
    }
    return (void *)0;
}
static int allegro_motion_initialize(void) {
    const char *mode = getenv("NX300M_SHUTTER_MOTION");
    if (!mode) return 0;
    if (equal(mode, "observe")) motion_mode = 1;
    else if (equal(mode, "ideal-8-sector")) motion_mode = 2;
    else return error(22);
    const char *step = getenv("NX300M_SHUTTER_STEP_US");
    if (step) {
        u32 value = 0;
        for (const char *p = step; *p; p++) {
            if (*p < '0' || *p > '9' || value > 1000000) return error(22);
            value = value*10+(u32)(*p-'0');
        }
        if (value < 3000 || value > 1000000) return error(22);
        motion_step_us = value;
    }
    motion_gpio_get = dlsym((void *)0, "gpio_data_get");
    if (!motion_gpio_get) return error(38);
    int pi1 = motion_pin(32);
    motion_sector = pi1 == 1 ? (u32)allegro_level : (u32)(4+allegro_level);
    motion_status = checked(raw(5, (long)"/var/run/nx300m-shutter-motion.bin", 0102 | 02000000, 0600, 0, 0, 0));
    if (motion_status < 0) return -1;
    if (checked(raw(93, motion_status, 64, 0, 0, 0, 0))) return -1;
    motion_timer = timerfd_create(1, 02000000 | 04000);
    if (motion_timer < 0) return -1;
    struct allegro_timer_spec spec = {{0, 1000000}, {0, 1000000}};
    if (timerfd_settime(motion_timer, 0, &spec, (void *)0)) return -1;
    unsigned long thread;
    int failure = pthread_create(&thread, (void *)0, motion_worker, (void *)0);
    if (failure) return error(failure);
    pthread_detach(thread);
    event("Allegro motion scenario; uncalibrated", motion_mode, motion_step_us, motion_sector);
    return 0;
}
