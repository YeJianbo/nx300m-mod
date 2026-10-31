/* Included by device_preview.c. ABI and edge/timer jobs follow Samsung's
 * GPL allegro_ioctl.h and drivers/misc/shutter/allegro.c for NX300M.
 * The FIFO carries explicit synthetic PI2 levels. An optional, uncalibrated
 * rotor scenario derives levels from motor GPIO outputs and monotonic time.
 */
extern void *dlsym(void *, const char *);
extern int timerfd_create(int, int);
extern int timerfd_settime(int, int, const void *, void *);
extern int eventfd(unsigned int, int);
extern int pthread_create(unsigned long *, const void *, void *(*)(void *), void *);
extern int pthread_detach(unsigned long);
struct allegro_time { long seconds, nanoseconds; };
extern int clock_gettime(int, struct allegro_time *);
struct allegro_timer_spec { struct allegro_time interval, value; };
struct allegro_command { int level, count; u32 microseconds; int job; };
struct allegro_timer_command { int channel; u32 microseconds; int job; };
struct allegro_timer { int fd, notification, job; };
struct allegro_poll { int fd; short events, revents; };
static struct allegro_timer allegro_timers[5];
static struct allegro_command allegro_irq = { -1, 0, 0, 0 };
static volatile int allegro_lock_word;
static int allegro_initialized, allegro_level = 1, allegro_enabled, allegro_pending;
static long long allegro_last_edge;
static int allegro_notification = -1;
static void *(*allegro_gpio_open)(u32);
static void (*allegro_gpio_set)(void *, u32, int);
static int (*allegro_gpio_number)(int);
static int allegro_motion_initialize(void);

static void allegro_lock(void) {
    while (__sync_lock_test_and_set(&allegro_lock_word, 1)) raw(158, 0, 0, 0, 0, 0, 0);
}
static void allegro_unlock(void) { __sync_lock_release(&allegro_lock_word); }
static void allegro_pin(int name, int value) {
    int pin = allegro_gpio_number(name);
    if (pin < 0 || pin >= 224) return;
    void *bank = allegro_gpio_open((u32)pin/8);
    if (bank) allegro_gpio_set(bank, (u32)pin%8, value);
}
static void allegro_job(int job) {
    /* Stock CShutterHAL::Initialize uses GPIO names 34/35 for MG1/MG2,
     * 36/37 for the motor pins; get_gpio_number supplies board pin numbers. */
    if (job == 1) { allegro_pin(36, 1); allegro_pin(37, 1); }
    else if (job == 2) allegro_pin(34, 1);
    else if (job == 3) allegro_pin(35, 1);
    /* MG1_OFF/MG2_OFF are defined in the vendor header but have no action
     * in the released ISR/timer handler. Preserve that driver behavior. */
    event("Allegro simulated GPIO job", job, 0, 0);
}
static void *allegro_timer_worker(void *unused) {
    (void)unused;
    struct allegro_poll waits[5];
    for (int i = 0; i < 5; i++) { waits[i].fd = allegro_timers[i].fd; waits[i].events = 1; waits[i].revents = 0; }
    for (;;) {
        int ready = checked(raw(168, (long)waits, 5, -1, 0, 0, 0));
        if (ready < 0) continue;
        allegro_lock();
        for (int i = 0; i < 5; i++) if (waits[i].revents & 1) {
            unsigned long long expirations;
            if (raw(3, waits[i].fd, (long)&expirations, 8, 0, 0, 0) != 8) continue;
            /* The kernel driver performs GPIO actions at timer expiry, even
             * if user space has not entered TIMER_WAIT yet. */
            allegro_job(allegro_timers[i].job);
            raw(4, allegro_timers[i].notification, (long)&expirations, 8, 0, 0, 0);
            event("Allegro real timer expired", i, (u32)expirations, 0);
        }
        allegro_unlock();
    }
    return (void *)0;
}
static int allegro_timer_arm(int channel, u32 microseconds, int job) {
    struct allegro_timer_spec spec = {{0, 0}, {microseconds/1000000U, (microseconds%1000000U)*1000U}};
    /* A zero hrtimer duration fires immediately; timerfd's all-zero value
     * disarms instead. One nanosecond preserves the requested immediate fire. */
    if (!microseconds) spec.value.nanoseconds = 1;
    if (timerfd_settime(allegro_timers[channel].fd, 0, &spec, (void *)0)) return -1;
    unsigned long long discarded;
    raw(3, allegro_timers[channel].notification, (long)&discarded, 8, 0, 0, 0);
    allegro_timers[channel].job = job;
    event("Allegro timer armed us", channel, microseconds, job);
    return 0;
}
static int allegro_initialize(void) {
    if (allegro_initialized) return 0;
    allegro_gpio_open = dlsym((void *)0, "gpio_open");
    allegro_gpio_set = dlsym((void *)0, "gpio_data_set");
    allegro_gpio_number = dlsym((void *)0, "get_gpio_number");
    if (!allegro_gpio_open || !allegro_gpio_set || !allegro_gpio_number) return error(38);
    for (int i = 0; i < 5; i++) {
        allegro_timers[i].fd = -1; allegro_timers[i].notification = -1;
    }
    int failure = 0;
    allegro_notification = eventfd(0, 02000000 | 04000);
    if (allegro_notification < 0) return -1;
    for (int i = 0; i < 5; i++) {
        allegro_timers[i].fd = timerfd_create(1, 02000000 | 04000); /* MONOTONIC, CLOEXEC, NONBLOCK */
        if (allegro_timers[i].fd < 0) { failure = *__errno_location(); break; }
        allegro_timers[i].notification = eventfd(0, 02000000 | 04000);
        if (allegro_timers[i].notification < 0) { failure = *__errno_location(); break; }
    }
    if (!failure) {
        unsigned long worker;
        failure = pthread_create(&worker, (void *)0, allegro_timer_worker, (void *)0);
        if (!failure) pthread_detach(worker);
    }
    if (failure) {
        raw(6, allegro_notification, 0, 0, 0, 0, 0);
        allegro_notification = -1;
        for (int i = 0; i < 5; i++) {
            if (allegro_timers[i].fd >= 0) raw(6, allegro_timers[i].fd, 0, 0, 0, 0, 0);
            if (allegro_timers[i].notification >= 0) raw(6, allegro_timers[i].notification, 0, 0, 0, 0, 0);
        }
        return error(failure);
    }
    const char *park = getenv("NX300M_SHUTTER_PARK");
    if (park && (equal(park, "10") || equal(park, "01"))) {
        /* Cold PI levels are simulator inputs. Stock initialization reads
         * them normally; no callback, timer or assertion is bypassed. */
        allegro_pin(32, park[0]-'0');
        allegro_level = park[1]-'0';
        event("Allegro explicit cold PI levels", park[0]-'0', allegro_level, 0);
    }
    allegro_pin(33, allegro_level);
    if (allegro_motion_initialize()) return -1;
    allegro_initialized = 1;
    event("Allegro initialized; PI2 input ready", allegro_level, 5, 0);
    return 0;
}
static int allegro_open(int flags) {
    const char *fifo = "/var/run/nx300m-allegro-pi2";
    long result = raw(14, (long)fifo, 0010600, 0, 0, 0, 0); /* private FIFO, uid owns it */
    if (result < 0 && result != -17) return checked(result);
    int fd = checked(raw(5, (long)fifo, 04000 | 2 | (flags & 02000000), 0, 0, 0, 0));
    if (fd >= FD_LIMIT) { raw(6, fd, 0, 0, 0, 0, 0); return error(24); }
    if (fd >= 0) { kinds[fd] = 5; event("Allegro open", fd, 0, 0); }
    return fd;
}
/* Called with the Allegro lock held, independently of the source of a level. */
static int allegro_edge(int level, long long tick, const char *source) {
    if (level == allegro_level) return 0;
    allegro_level = level;
    allegro_pin(33, level);
    event(source, level, allegro_irq.count, allegro_enabled);
    if (!allegro_enabled || allegro_irq.count <= 0 || level != allegro_irq.level ||
            tick-allegro_last_edge < 2000000LL) return 0;
    allegro_last_edge = tick;
    if (--allegro_irq.count) allegro_irq.level = !allegro_irq.level;
    else {
        allegro_enabled = 0;
        if (allegro_irq.microseconds) {
            /* The vendor ISR wakes TIMER_WAIT(2), not IRQ_WAIT, after delay. */
            return allegro_timer_arm(2, allegro_irq.microseconds, allegro_irq.job);
        }
        allegro_job(allegro_irq.job);
        allegro_pending = 1;
        unsigned long long one = 1;
        if (raw(4, allegro_notification, (long)&one, 8, 0, 0, 0) != 8) return error(5);
    }
    return 0;
}

#include "shutter_motion.h"

static int allegro_drain(int fd) {
    char input[64];
    long count;
    while ((count = raw(3, fd, (long)input, sizeof input, 0, 0, 0)) > 0) {
        for (long i = 0; i < count; i++) {
            if (input[i] != '0' && input[i] != '1') continue;
            struct allegro_time now;
            if (clock_gettime(1, &now)) return -1;
            long long tick = (long long)now.seconds*1000000000LL + now.nanoseconds;
            if (allegro_edge(input[i]-'0', tick, "Allegro explicit PI2 edge")) return -1;
        }
    }
    return count == -11 || count == 0 ? 0 : checked(count);
}
static int allegro_timer_wait(int channel) {
    struct allegro_poll wait = {allegro_timers[channel].notification, 1, 0};
    for (;;) {
        int ready = checked(raw(168, (long)&wait, 1, -1, 0, 0, 0));
        if (ready < 0) return -1;
        allegro_lock();
        unsigned long long expirations;
        long received = raw(3, wait.fd, (long)&expirations, 8, 0, 0, 0);
        if (received == 8) {
            allegro_unlock();
            return 0;
        }
        allegro_unlock();
        if (received != -11) return received < 0 ? checked(received) : error(5);
    }
}
static int allegro_ioctl(int fd, u32 request, void *arg) {
    allegro_lock();
    int result = 0;
    if (request == 0x730eU) result = allegro_initialize();
    else if (!allegro_initialized) result = error(19);
    else if (request == 0x80107301U) {
        if (!arg) result = error(14);
        else if (!(result = allegro_drain(fd))) {
            struct allegro_command *p = arg;
            p->level = allegro_level; p->count = 0; p->microseconds = 0; p->job = 0;
        }
    } else if (request == 0x40107303U) {
        struct allegro_command *p = arg;
        if (!p) result = error(14);
        else if ((p->level != 0 && p->level != 1) || p->count <= 0 || p->job < 0 || p->job > 5) result = error(22);
        else {
            result = allegro_drain(fd); /* old queued stimuli cannot satisfy a new arm */
            if (!result) {
                allegro_irq = *p; allegro_pending = 0;
                unsigned long long discarded;
                raw(3, allegro_notification, (long)&discarded, 8, 0, 0, 0);
                event("Allegro IRQ configured", p->level, p->count, p->microseconds);
            }
        }
    } else if (request == 0x40107304U) {
        struct allegro_command *p = arg;
        if (!p) result = error(14);
        else if (p->level && allegro_irq.level < 0) result = error(22);
        else if (p->level) {
            result = allegro_drain(fd);
            if (!result) allegro_enabled = 1;
        } else {
            allegro_enabled = 0; allegro_irq.level = -1;
            struct allegro_timer_spec zero = {{0, 0}, {0, 0}};
            if (allegro_irq.microseconds) result = timerfd_settime(allegro_timers[2].fd, 0, &zero, (void *)0);
            allegro_irq.microseconds = 0;
        }
    } else if (request == 0x7305U) {
        for (;;) {
            result = allegro_drain(fd);
            if (result || allegro_pending) {
                allegro_pending = 0;
                unsigned long long discarded;
                raw(3, allegro_notification, (long)&discarded, 8, 0, 0, 0);
                break;
            }
            allegro_unlock();
            struct allegro_poll wait[2] = {{fd, 1, 0}, {allegro_notification, 1, 0}};
            result = checked(raw(168, (long)wait, 2, -1, 0, 0, 0));
            allegro_lock();
            if (result < 0) break;
        }
        if (result >= 0) result = 0;
    } else if (request == 0x400c7306U || request == 0x400c7307U || request == 0x400c7308U) {
        struct allegro_timer_command *p = arg;
        if (!p) result = error(14);
        else if (p->channel < 0 || p->channel >= 5 || (request == 0x400c7306U && (p->job < 0 || p->job > 5))) result = error(22);
        else if (request == 0x400c7306U) result = allegro_timer_arm(p->channel, p->microseconds, p->job);
        else if (request == 0x400c7307U) {
            struct allegro_timer_spec zero = {{0, 0}, {0, 0}};
            result = timerfd_settime(allegro_timers[p->channel].fd, 0, &zero, (void *)0);
            unsigned long long discarded;
            raw(3, allegro_timers[p->channel].notification, (long)&discarded, 8, 0, 0, 0);
            allegro_timers[p->channel].job = 0;
        } else {
            int channel = p->channel;
            allegro_unlock();
            return allegro_timer_wait(channel);
        }
    } else { event("unsupported Allegro ioctl", fd, request, (u32)arg); result = error(25); }
    allegro_unlock();
    return result;
}
