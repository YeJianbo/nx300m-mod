/* Userspace device backing for stock DRIMeIV startup under QEMU.
 * ABI: Samsung NX300M GPL release, pwmdev.h, d4_sma_ioctl.h and d4_cma.h.
 * Memory allocation is process-local and thread-safe, for one camera process.
 * PWM/SPI provide mapped registers; Allegro is opt-in and uses explicit PI2
 * input or a separately selected ideal rotor plus monotonic timers. Lens motion
 * and sensor image frames remain absent.
 * Ordinary files/syscalls pass through. Unknown device ioctls fail ENOTTY.
 */
typedef unsigned int u32;
typedef unsigned int size_t;
typedef __builtin_va_list va_list;
extern int *__errno_location(void);
extern char *getenv(const char *);

static long raw(int number, long a, long b, long c, long d, long e, long f) {
    register long r0 __asm__("r0") = a;
    register long r1 __asm__("r1") = b;
    register long r2 __asm__("r2") = c;
    register long r3 __asm__("r3") = d;
    register long r4 __asm__("r4") = e;
    register long r5 __asm__("r5") = f;
    register long r7 __asm__("r7") = number;
    __asm__ volatile("svc 0" : "+r"(r0) : "r"(r1), "r"(r2), "r"(r3), "r"(r4), "r"(r5), "r"(r7) : "memory");
    return r0;
}
static long checked(long result) {
    if ((unsigned long)result >= (unsigned long)-4095) {
        *__errno_location() = -result;
        return -1;
    }
    return result;
}
static int error(int number) { *__errno_location() = number; return -1; }
static int equal(const char *a, const char *b) {
    while (*a && *a == *b) { a++; b++; }
    return *a == *b;
}
static int prefix(const char *a, const char *b) {
    while (*b && *a == *b) { a++; b++; }
    return !*b;
}
static char *hex(char *out, u32 value) {
    *out++ = '0'; *out++ = 'x';
    for (int n = 28; n >= 0; n -= 4) *out++ = "0123456789abcdef"[(value >> n) & 15];
    return out;
}
static void event(const char *action, u32 a, u32 b, u32 c) {
    char buffer[128], *p = buffer;
    const char *tag = "[DEVICE SUBSTITUTE] ";
    while (*tag) *p++ = *tag++;
    while (*action) *p++ = *action++;
    *p++ = ' '; p = hex(p, a); *p++ = ' '; p = hex(p, b);
    *p++ = ' '; p = hex(p, c); *p++ = '\n';
    raw(4, 2, (long)buffer, p - buffer, 0, 0, 0);
}

enum { FD_LIMIT = 1024, PWM_BYTES = 0x2000, SPI_BYTES = 0x1000 };
static unsigned char kinds[FD_LIMIT], channels[FD_LIMIT];
static unsigned char initialized_registers[2][18];
static unsigned int spi_log_counts[8][32];
static unsigned int memory_log_counts[2][16];
#include "allegro_preview.h"
static void pp_cs_lut_snapshot(void);
#include "isp_preview.h"
struct pwm_info { u32 address, size, secondary; int swap; };
#define SMA_BASE 0xc6800000U
#define BMA_BASE 0x90000000U /* Host-only physical tokens for non-SMA allocations. */
enum { SMA_SIZE = 0x18b00000, BMA_SIZE = 0x08000000, SMA_PAGES = SMA_SIZE / 4096, RECORDS = 512 };
struct allocation { u32 physical, size; };
enum { PROTECTION_UNKNOWN = -1 };
struct mapping { u32 virtual, physical, size; int prot, shared; };
static struct allocation arena_allocations[2][RECORDS];
static u32 allocation_generation[2][RECORDS], allocation_serial;
static struct mapping mappings[RECORDS];
static unsigned char arena_pages[2][SMA_PAGES];
static u32 arena_allocated[2], arena_initialized[2];
/* d4_sma_if.h enables SMA_TEMP_RESERVED_AREA. These are borrowed bytes,
 * separate from CMA allocations; the stock setter uses addr+size as top.
 * The released d4-cma.c allocation path does not call the remain getter. */
static struct { u32 top, current, size; } sma_remaining;
static volatile int memory_lock;
static void lock(void) { while (__sync_lock_test_and_set(&memory_lock, 1)) raw(158, 0, 0, 0, 0, 0, 0); }
static void unlock(void) { __sync_lock_release(&memory_lock); }
/* PWM initialization starts a pthread whose stock stack allocator calls our
 * mmap. Keep peripheral setup serialized without holding the mapping lock. */
static volatile int peripheral_init_lock;
static void peripheral_init_begin(void) {
    while (__sync_lock_test_and_set(&peripheral_init_lock, 1)) raw(158, 0, 0, 0, 0, 0, 0);
}
static void peripheral_init_end(void) { __sync_lock_release(&peripheral_init_lock); }
#include "spi_control.h"
#include "pwm_timing.h"
#include "csm_transport.h"
#include "pp_events.h"
#include "pp_replay.h"
#include "ipc_events.h"
/* Callers retaining a pointer must serialize against unmapping/protection
 * changes, just as with mmap itself. Access bits are Linux PROT_READ/WRITE/EXEC.
 * Existing pixel helpers may read and write, so their common API requires RW;
 * read-only input mappings are conservatively rejected by that legacy API. */
void *nx300m_map_physical_access(u32 physical, u32 size, int required) {
    void *result = (void *)0;
    if (!size || !required || (required & ~7)) return result;
    lock();
    for (int i = 0; i < RECORDS; i++) {
        struct mapping *map = mappings+i;
        if (map->prot != PROTECTION_UNKNOWN && (map->prot & required) == required && map->size && physical >= map->physical && physical-map->physical < map->size && size <= map->size-(physical-map->physical)) {
            result = (void *)(map->virtual+physical-map->physical); break;
        }
    }
    unlock();
    return result;
}
void *nx300m_map_physical(u32 physical, u32 size) {
    return nx300m_map_physical_access(physical, size, 3);
}
/* JPEG tasks carry virtual BMA/SMA pointers without a compressed byte count.
 * Expose only the tracked mapping span so the software decoder can bound its
 * marker parser and validate full output planes before writing any pixels. */
void *nx300m_map_virtual_access(u32 address, u32 minimum, u32 *remaining, int required) {
    void *result = (void *)0;
    if (remaining) *remaining = 0;
    if (!minimum || !required || (required & ~7)) return result;
    lock();
    for (int i = 0; i < RECORDS; i++) {
        const struct mapping *map = mappings+i;
        if (map->prot != PROTECTION_UNKNOWN && (map->prot & required) == required && map->size && address >= map->virtual && address-map->virtual < map->size) {
            u32 available = map->size-(address-map->virtual);
            if (minimum <= available) {
                result = (void *)address;
                if (remaining) *remaining = available;
                break;
            }
        }
    }
    unlock();
    return result;
}
void *nx300m_map_virtual(u32 address, u32 minimum, u32 *remaining) {
    return nx300m_map_virtual_access(address, minimum, remaining, 3);
}
/* Diagnostic byte snapshot, not pixel-producer authorization. Unlike the raw
 * pointer helpers, this holds the allocator/mapping lock throughout the copy.
 * The caller allocates destination before entering; failures may leave a copied
 * prefix there. Other pixel writers are not stopped, so this is not a frame
 * transaction. Only an exact virtual source is accepted, avoiding physical
 * alias ambiguity (including private mappings with different contents). */
static unsigned long long live_virtual_token(u32 address, u32 length, int writable, int shared_only) {
    for (int i = 0; i < RECORDS; i++) {
        const struct mapping *map = mappings+i;
        if (!map->size || map->prot == PROTECTION_UNKNOWN || !(map->prot&(writable ? 2 : 1)) ||
            (shared_only && !map->shared) || address < map->virtual ||
            address-map->virtual >= map->size || length > map->size-(address-map->virtual)) continue;
        u32 physical = map->physical+address-map->virtual;
        for (int arena = 0; arena < 2; arena++)
            for (int slot = 0; slot < RECORDS; slot++) {
                const struct allocation *allocation = arena_allocations[arena]+slot;
                if (allocation->size && physical >= allocation->physical &&
                    physical-allocation->physical < allocation->size &&
                    length <= allocation->size-(physical-allocation->physical))
                    return ((unsigned long long)allocation_generation[arena][slot]<<32)|physical;
            }
    }
    return 0;
}
unsigned long long nx300m_live_virtual_generation(u32 address, u32 length, int writable) {
    if (!address || !length || length > ~0U-address || (writable != 0 && writable != 1)) return 0;
    lock();
    unsigned long long token = live_virtual_token(address, length, writable, 1);
    unlock();
    return token;
}
static int transfer_live_virtual(u32 address, u32 length, void *buffer, int writable,
                                unsigned long long expected) {
    u32 target = (u32)buffer;
    if (!address || !target || !length || length > ~0U-address || length > ~0U-target ||
        (address < target+length && target < address+length)) return -22;
    int descriptors[2];
    long status = raw(42, (long)descriptors, 0, 0, 0, 0, 0);
    if (status < 0) return (int)status;
    lock();
    unsigned long long token = live_virtual_token(address, length, writable, writable);
    int valid = token && (!writable || token == expected);
    status = valid ? 0 : writable && token ? -116 : -14;
    for (u32 done = 0; valid && done < length;) {
        /* A Linux pipe holds at least PIPE_BUF (4096) bytes. Drain each chunk
         * before writing the next, so no worker thread or heap is needed. */
        u32 chunk = length-done > 4096 ? 4096 : length-done;
        long written = raw(4, descriptors[1], (writable ? target : address)+done, chunk, 0, 0, 0);
        if (written != (long)chunk) { status = written < 0 ? written : -14; break; }
        long received = raw(3, descriptors[0], (writable ? address : target)+done, chunk, 0, 0, 0);
        if (received != (long)chunk) { status = received < 0 ? received : -14; break; }
        done += chunk;
    }
    unlock();
    raw(6, descriptors[0], 0, 0, 0, 0, 0);
    raw(6, descriptors[1], 0, 0, 0, 0, 0);
    return (int)status;
}
int nx300m_copy_live_virtual(u32 address, u32 length, void *destination) {
    return transfer_live_virtual(address, length, destination, 0, 0);
}
/* A token binds the exact physical start and allocation lifetime. Shared arena
 * mappings alone are accepted; private aliases cannot identify arena contents.
 * A failed write can leave a prefix; callers must not report encoding success. */
int nx300m_write_live_virtual_if_generation(u32 address, u32 length, const void *source,
                                            unsigned long long token) {
    if (!token) return -22;
    return transfer_live_virtual(address, length, (void *)source, 1, token);
}
#include "pp_cs_lut.h"
static u32 physical_address(u32 virtual) {
    for (int i = 0; i < RECORDS; i++)
        if (mappings[i].size && virtual >= mappings[i].virtual && virtual - mappings[i].virtual < mappings[i].size)
            return mappings[i].physical + virtual - mappings[i].virtual;
    return 0;
}
static int sma_read_user_info(const void *source, struct allocation *info) {
    /* Kernel copying through a private pipe gives copy_from_user's EFAULT
     * behavior for invalid or partially mapped pointers, without directly
     * dereferencing the ioctl argument inside the camera process. */
    int descriptors[2];
    long result = checked(raw(42, (long)descriptors, 0, 0, 0, 0, 0));
    if (result < 0) return -1;
    long written = raw(4, descriptors[1], (long)source, sizeof *info, 0, 0, 0);
    long read_bytes = written == sizeof *info ? raw(3, descriptors[0], (long)info, sizeof *info, 0, 0, 0) : 0;
    raw(6, descriptors[0], 0, 0, 0, 0, 0);
    raw(6, descriptors[1], 0, 0, 0, 0, 0);
    return written == sizeof *info && read_bytes == sizeof *info ? 0 : error(14);
}
static int memory_ioctl(u32 request, void *arg, int bma) {
    if (bma) {
        switch (request) {
        case 0xc0086201U: request = 0xc0087305U; break;
        case 0xc0046202U: request = 0xc0047306U; break;
        case 0xc0046203U: request = 0xc0047302U; break;
        case 0xc0046204U: request = 0xc0047307U; break;
        default: event("unsupported BMA ioctl", 0, request, (u32)arg); return error(25);
        }
    }
    if (!arg) return error(14);
    u32 base = bma ? BMA_BASE : SMA_BASE, size = bma ? BMA_SIZE : SMA_SIZE;
    struct allocation *allocations = arena_allocations[bma];
    unsigned char *pages = arena_pages[bma];
    u32 *value = arg;
    struct allocation *buffer = arg;
    struct allocation remaining_info;
    if (request == 0xc008730aU && sma_read_user_info(arg, &remaining_info)) return -1;
    lock();
    int result = 0;
    switch (request) {
    case 0x80047301U: *value = size; break;
    case 0x80047304U: *value = base; break;
    case 0xc0047302U:
        *value = physical_address(*value);
        if (!*value) result = error(14);
        break;
    case 0xc0047303U: /* Coherent host mappings have no uncached alias. */ break;
    case 0x80047308U: *value = arena_allocated[bma]; break;
    case 0xc0087305U: {
        u32 needed = buffer->size / 4096 + !!(buffer->size & 4095), start = 0, count = 0;
        int slot = -1;
        for (int i = 0; i < RECORDS; i++) if (!allocations[i].size) { slot = i; break; }
        if (!needed || needed > size / 4096 || slot < 0 || allocation_serial == ~0U) { result = error(12); break; }
        for (u32 i = 0; i < size / 4096; i++) {
            if (pages[i]) { count = 0; continue; }
            if (!count) start = i;
            if (++count == needed) break;
        }
        if (count != needed) { result = error(12); break; }
        for (u32 i = start; i < start + needed; i++) pages[i] = 1;
        allocations[slot].physical = buffer->physical = base + start * 4096;
        allocations[slot].size = needed * 4096;
        allocation_generation[bma][slot] = ++allocation_serial;
        arena_allocated[bma] += needed * 4096;
        event(bma ? "BMA allocated" : "SMA allocated", buffer->physical, buffer->size, arena_allocated[bma]);
        break;
    }
    case 0xc0047306U:
    case 0xc0047307U: {
        u32 physical = request == 0xc0047306U ? physical_address(*value) : *value;
        if (!bma && sma_remaining.top <= physical && physical < sma_remaining.top+sma_remaining.size) {
            /* Stock d4_sma_free_buf consumes borrowed-region addresses
             * without calling d4_cma_free, including an unmatched address.
             * There are no borrowed allocations here: no released SMA/CMA
             * allocation entry point calls d4_sma_get_remain_buf. */
            event("SMA remain release", physical, sma_remaining.top, sma_remaining.size);
            break;
        }
        result = error(22);
        for (int i = 0; i < RECORDS; i++) if (allocations[i].size && allocations[i].physical == physical) {
            u32 start = (physical - base) / 4096, count = allocations[i].size / 4096;
            for (u32 n = start; n < start + count; n++) pages[n] = 0;
            arena_allocated[bma] -= allocations[i].size;
            allocations[i].size = 0;
            result = 0; break;
        }
        break;
    }
    case 0xc0087309U: /* CPU cache flush is unnecessary for coherent host RAM. */ break;
    case 0xc008730aU:
        sma_remaining.top = remaining_info.physical ? remaining_info.physical+remaining_info.size : 0;
        sma_remaining.current = sma_remaining.top;
        sma_remaining.size = remaining_info.physical ? remaining_info.size : 0;
        event("SMA remain region configured", remaining_info.physical, sma_remaining.top, sma_remaining.size);
        break;
    default: event("unsupported SMA ioctl", 0, request, (u32)arg); result = error(25); break;
    }
    unlock();
    return result;
}

#include "fb_preview.h"
#include "ep_preview.h"

static int open_file(const char *path, int flags, int mode) {
    const char *isp_enabled = getenv("NX300M_ISP_SIM");
    if (isp_enabled && equal(isp_enabled, "1")) {
        if (equal(path, "/dev/fb0")) return fb_open(flags);
        if (equal(path, "/dev/drime4_ep")) return ep_open(flags);
        for (int i = 0; i < ISP_COUNT; i++) if (equal(path, isp_paths[i])) return isp_open(i, flags);
    }
    if (equal(path, "/dev/allegro")) {
        const char *enabled = getenv("NX300M_SHUTTER_SIM");
        if (enabled && equal(enabled, "1")) return allegro_open(flags);
    }
    int bma = equal(path, "/dev/d4_bma");
    if (bma || equal(path, "/dev/d4_sma")) {
        lock();
        int fd = checked(raw(5, (long)(bma ? "/tmp/nx-bma" : "/tmp/nx-sma"), 0102 | (arena_initialized[bma] ? 0 : 01000), 0600, 0, 0, 0));
        if (fd >= 0) {
            if (fd >= FD_LIMIT || raw(93, fd, bma ? BMA_SIZE : SMA_SIZE, 0, 0, 0, 0) < 0) {
                raw(6, fd, 0, 0, 0, 0, 0); fd = error(12);
            } else {
                kinds[fd] = bma ? 4 : 2; arena_initialized[bma] = 1;
                event(bma ? "BMA open" : "SMA open", fd, bma ? BMA_BASE : SMA_BASE, bma ? BMA_SIZE : SMA_SIZE);
            }
        }
        unlock();
        return fd;
    }
    int channel = -1;
    int spi = prefix(path, "/dev/hs_spidev.");
    if (spi || prefix(path, "/dev/pwmdev.")) {
        const char *digit = path + (spi ? 15 : 12);
        int value = 0;
        if (*digit) {
            while (*digit >= '0' && *digit <= '9') value = value * 10 + *digit++ - '0';
            if (!*digit && value < (spi ? 8 : 18)) channel = value;
        }
    }
    if (channel < 0) return checked(raw(5, (long)path, flags, mode, 0, 0, 0));
    char backing[] = "/tmp/nx-pwm-00";
    if (spi) { backing[8] = 's'; backing[9] = 'p'; backing[10] = 'i'; }
    backing[12] = '0' + channel / 10;
    backing[13] = '0' + channel % 10;
    peripheral_init_begin();
    int fd = checked(raw(5, (long)backing, 0102 | (initialized_registers[spi][channel] ? 0 : 01000), 0600, 0, 0, 0));
    if (fd < 0) { peripheral_init_end(); return fd; }
    if (fd >= FD_LIMIT || raw(93, fd, spi ? SPI_BYTES : PWM_BYTES, 0, 0, 0, 0) < 0) {
        raw(6, fd, 0, 0, 0, 0, 0); peripheral_init_end(); return error(24);
    }
    initialized_registers[spi][channel] = 1;
    if (spi && spi_control_open(fd, channel)) {
        raw(6, fd, 0, 0, 0, 0, 0); peripheral_init_end(); return -1;
    }
    if (!spi && pwm_register_open(fd, channel)) {
        raw(6, fd, 0, 0, 0, 0, 0); peripheral_init_end(); return -1;
    }
    lock();
    kinds[fd] = spi ? 3 : 1; channels[fd] = channel;
    unlock();
    event(spi ? "SPI open" : "PWM open", fd, channel, spi ? SPI_BYTES : PWM_BYTES);
    peripheral_init_end();
    return fd;
}
int open(const char *path, int flags, ...) {
    int mode = 0;
    if (flags & 0100) { va_list args; __builtin_va_start(args, flags); mode = __builtin_va_arg(args, int); __builtin_va_end(args); }
    return open_file(path, flags, mode);
}
int open64(const char *path, int flags, ...) {
    int mode = 0;
    if (flags & 0100) { va_list args; __builtin_va_start(args, flags); mode = __builtin_va_arg(args, int); __builtin_va_end(args); }
    return open_file(path, flags, mode);
}
int close(int fd) {
    if ((u32)fd < FD_LIMIT) kinds[fd] = 0;
    return checked(raw(6, fd, 0, 0, 0, 0, 0));
}
int read(int fd, void *buffer, u32 count) {
    if (isp_index(fd) == ISP_IPCM || isp_index(fd) == ISP_IPCS) return ipc_read_interrupt(fd, buffer, isp_index(fd) == ISP_IPCS);
    if (isp_index(fd) == ISP_SSIF) return ssif_read_interrupt(fd, buffer);
    if (isp_index(fd) == ISP_PP) return pp_read_interrupt(fd, buffer, count);
    if (isp_index(fd) == ISP_MIPI || isp_index(fd) == ISP_CSM) return 0;
    return checked(raw(3, fd, (long)buffer, count, 0, 0, 0));
}
int write(int fd, const void *buffer, u32 count) {
    if (isp_index(fd) == ISP_IPCM || isp_index(fd) == ISP_IPCS) return count;
    if (isp_index(fd) == ISP_MIPI || isp_index(fd) == ISP_CSM || isp_index(fd) == ISP_SSIF || isp_index(fd) == ISP_PP) return 0;
    return checked(raw(4, fd, (long)buffer, count, 0, 0, 0));
}
int ioctl(int fd, u32 request, ...) {
    void *arg = (void *)0;
    /* Allegro's _IO INITIALIZE/INTERRUPT_WAIT have no third argument. */
    if (!((u32)fd < FD_LIMIT && (kinds[fd] >= 5 || kinds[fd] == 3 || kinds[fd] == 1) && !(request >> 30))) {
        va_list args; __builtin_va_start(args, request);
        arg = __builtin_va_arg(args, void *); __builtin_va_end(args);
    }
    if ((u32)fd >= FD_LIMIT || !kinds[fd]) return checked(raw(54, fd, request, (long)arg, 0, 0, 0));
    if (kinds[fd] == 5) return allegro_ioctl(fd, request, arg);
    if (kinds[fd] == FB_KIND) return fb_ioctl(request, arg);
    if (kinds[fd] == EP_KIND) return ep_ioctl(fd, request, arg);
    if (isp_index(fd) >= 0) return isp_ioctl(isp_index(fd), request, arg);
    if (kinds[fd] == 2 || kinds[fd] == 4) {
        int bma = kinds[fd] == 4;
        if (__sync_fetch_and_add(&memory_log_counts[bma][request&15], 1) < 4)
            event(bma ? "BMA ioctl" : "SMA ioctl", fd, request, (u32)arg);
        return memory_ioctl(request, arg, bma);
    }
    if (kinds[fd] == 3) {
        /* Lens retry loops otherwise fill interactive logs indefinitely. Keep
         * initial observations while preserving every ioctl result. */
        int log_request = __sync_fetch_and_add(&spi_log_counts[channels[fd]][request & 31], 1) < 4;
        if (log_request) event("SPI ioctl", fd, request, (u32)arg);
        return spi_control_ioctl(fd, request, arg, log_request);
    }
    event("PWM ioctl", fd, request, (u32)arg);
    if (request == 0x8010700eU) {
        if (!arg) return error(14);
        struct pwm_info *info = arg;
        info->address = 0x10000000U; /* Synthetic physical token, translated by mmap. */
        info->size = PWM_BYTES;
        info->secondary = channels[fd] == 2 ? 0 : channels[fd] == 3 ? 1 : 18;
        info->swap = -1;
        return 0;
    }
    if (pwm_timing_active && (request == 0x700f || request == 0x7013)) {
        /* Only the single-channel handler is implemented. The multi-channel
         * handler also drives a board GPIO and must not be silently accepted. */
        if (request == 0x700f) return error(95);
        pwm_counters[channels[fd]].handler = 1;
        return 0;
    }
    if (request == 0x700f || request == 0x7013 || request == 0x40047014U) return 0;
    event("unsupported ioctl", fd, request, (u32)arg);
    return error(25);
}
/* Caller holds memory_lock. Count the records that survive removing a range,
 * including both halves of an interior split, before changing kernel memory. */
static int mapping_room(unsigned long long start, unsigned long long end, int added) {
    int available = RECORDS - added;
    for (int i = 0; i < RECORDS; i++) {
        const struct mapping *map = &mappings[i];
        unsigned long long bottom = map->virtual, top = bottom + map->size;
        if (!map->size) continue;
        if (end <= bottom || start >= top) available--;
        else {
            if (bottom < start) available--;
            if (end < top) available--;
        }
    }
    return available >= 0;
}
static void mapping_forget(unsigned long long start, unsigned long long end) {
    /* Reclaim whole records first: a later split can reuse those slots even
     * when the table was full before the operation. mapping_room reserved them. */
    for (int i = 0; i < RECORDS; i++) {
        struct mapping *map = &mappings[i];
        if (map->size && start <= map->virtual && end >= (unsigned long long)map->virtual + map->size)
            map->size = 0;
    }
    for (int i = 0; i < RECORDS; i++) {
        struct mapping *map = &mappings[i];
        unsigned long long bottom = map->virtual, top = bottom + map->size;
        if (!map->size || end <= bottom || start >= top) continue;
        if (start <= bottom) {
            map->physical += end - bottom; map->virtual = end; map->size = top - end;
        } else if (end >= top) map->size = start - bottom;
        else {
            for (int slot = 0; slot < RECORDS; slot++) if (!mappings[slot].size) {
                mappings[slot].virtual = end;
                mappings[slot].physical = map->physical + end - bottom;
                mappings[slot].size = top - end;
                mappings[slot].prot = map->prot;
                mappings[slot].shared = map->shared;
                break;
            }
            map->size = start - bottom;
        }
    }
}
/* Scratch storage is protected by memory_lock. A protection update can cut
 * each record into three pieces. Sort and merge before counting capacity so a
 * restoration can reclaim records even when the live table is full. */
static struct mapping protection_plan[3*RECORDS];
static int mapping_compact(int count) {
    for (int i = 1; i < count; i++) {
        struct mapping piece = protection_plan[i];
        int j = i;
        while (j && protection_plan[j-1].virtual > piece.virtual) {
            protection_plan[j] = protection_plan[j-1]; j--;
        }
        protection_plan[j] = piece;
    }
    int kept = 0;
    for (int i = 0; i < count; i++) {
        struct mapping *previous = kept ? &protection_plan[kept-1] : (void *)0;
        const struct mapping piece = protection_plan[i];
        if (previous && previous->prot == piece.prot && previous->shared == piece.shared &&
            (unsigned long long)previous->virtual + previous->size == piece.virtual &&
            (unsigned long long)previous->physical + previous->size == piece.physical)
            previous->size += piece.size;
        else protection_plan[kept++] = piece;
    }
    return kept;
}
static int mapping_plan(unsigned long long start, unsigned long long end, int prot) {
    int count = 0;
    for (int i = 0; i < RECORDS; i++) {
        const struct mapping *map = &mappings[i];
        unsigned long long bottom = map->virtual, top = bottom + map->size;
        if (!map->size) continue;
        unsigned long long cuts[4] = {bottom, bottom, top, top};
        if (start < top && end > bottom) {
            cuts[1] = start > bottom ? start : bottom;
            cuts[2] = end < top ? end : top;
        }
        for (int part = 0; part < 3; part++) if (cuts[part] < cuts[part+1]) {
            struct mapping piece = *map;
            piece.virtual = cuts[part];
            piece.physical += cuts[part] - bottom;
            piece.size = cuts[part+1] - cuts[part];
            if (part == 1 && start < top && end > bottom) piece.prot = prot;
            protection_plan[count++] = piece;
        }
    }
    return mapping_compact(count);
}
static void mapping_commit(int count) {
    for (int i = 0; i < count; i++) mappings[i] = protection_plan[i];
    for (int i = count; i < RECORDS; i++) mappings[i].size = 0;
}
static void mapping_merge(void) {
    mapping_commit(mapping_plan(0, 0, 0));
}
int mprotect(void *address, size_t length, int prot) {
    unsigned long long start = (u32)address;
    unsigned long long end = start + (((unsigned long long)length + 4095) & ~4095ULL);
    /* Linux validates alignment, length overflow and the empty range before
     * walking VMAs, so these cases cannot have a partially changed prefix. */
    if (!length || (start & 4095) || end > 0x100000000ULL)
        return checked(raw(125, (long)address, length, prot, 0, 0, 0));
    lock();
    int tracked = 0, any_tracked = 0;
    for (int i = 0; i < RECORDS; i++) if (mappings[i].size) {
        any_tracked = 1;
        if (start < (unsigned long long)mappings[i].virtual + mappings[i].size &&
            end > mappings[i].virtual) tracked = 1;
    }
    /* Growth can reach outside the given interval. Conservatively reject it
     * while any device mapping is tracked, even without a direct overlap. */
    if (any_tracked && (prot & (0x01000000 | 0x02000000))) {
        unlock(); return error(22);
    }
    if (!tracked) {
        int result = checked(raw(125, (long)address, length, prot, 0, 0, 0));
        unlock(); return result;
    }
    /* Nonstandard permission flags are outside the tracked device contract. */
    if (prot & ~7) { unlock(); return error(22); }
    /* Reserve BOTH outcomes before the syscall. Linux can change a prefix and
     * still return ENOMEM/EACCES at a later VMA. On failure we cannot infer the
     * exact boundary (QEMU guest maps can retain stale page flags), so mark
     * protection UNKNOWN and deny helper access throughout the request, retaining
     * physical identity for a subsequent successful permission restoration. */
    if (mapping_plan(start, end, prot) > RECORDS || mapping_plan(start, end, PROTECTION_UNKNOWN) > RECORDS) {
        unlock(); return error(12);
    }
    int result = checked(raw(125, (long)address, length, prot, 0, 0, 0));
    mapping_commit(mapping_plan(start, end, result ? PROTECTION_UNKNOWN : prot));
    unlock();
    return result;
}
static void *map_file(void *address, size_t length, int prot, int flags, int fd, unsigned long long offset) {
    u32 sma_offset = 0;
    if ((u32)fd < FD_LIMIT && kinds[fd]) {
        if (kinds[fd] == 5 || kinds[fd] == FB_KIND) { error(22); return (void *)-1; }
        if (kinds[fd] == EP_KIND) {
            if (offset < EP_HOST_BASE || offset-EP_HOST_BASE >= EP_BYTES || length > EP_BYTES-(offset-EP_HOST_BASE)) { error(22); return (void *)-1; }
            offset -= EP_HOST_BASE;
        } else if (isp_index(fd) >= 0) {
            event("ISP mmap", isp_index(fd), length, (u32)offset);
            if (isp_index(fd) == ISP_3A && offset >= ISP_3A_MEMORY && offset < ISP_3A_MEMORY+0x100000) sma_offset = offset;
            if (isp_translate(isp_index(fd), length, &offset)) return (void *)-1;
        } else if (kinds[fd] == 2 || kinds[fd] == 4) {
            int bma = kinds[fd] == 4;
            u32 base = bma ? BMA_BASE : SMA_BASE, size = bma ? BMA_SIZE : SMA_SIZE;
            event(bma ? "BMA mmap" : "SMA mmap", fd, length, (u32)offset);
            if (offset < base || offset - base >= size || length > size - (offset - base)) { error(22); return (void *)-1; }
            sma_offset = offset;
            offset -= base;
        } else {
            int spi = kinds[fd] == 3;
            event(spi ? "SPI mmap" : "PWM mmap", fd, length, (u32)offset);
            if (offset != (spi ? 0x11000000U : 0x10000000U) || length > (u32)(spi ? SPI_BYTES : PWM_BYTES)) { error(22); return (void *)-1; }
            offset = 0;
        }
    }
    if ((offset & 4095) || (offset >> 44)) { error(22); return (void *)-1; }
    u32 rounded = (length + 4095U) & ~4095U;
    int fixed = flags & 0x10;
    unsigned long long start = fixed ? (u32)address : 0, end = fixed ? start + rounded : 0;
    lock();
    if (length && length <= 0xfffff000U && (!fixed || !((u32)address & 4095U)) &&
        !mapping_room(start, end, !!sma_offset)) {
        unlock(); error(12); return (void *)-1;
    }
    void *result = (void *)checked(raw(192, (long)address, length, prot, flags, fd, offset >> 12));
    if (result != (void *)-1) {
        if (fixed) {
            mapping_forget(start, end);
            mmio_forget(result, rounded);
        }
        if (sma_offset) for (int slot = 0; slot < RECORDS; slot++) if (!mappings[slot].size) {
            mappings[slot].virtual = (u32)result;
            mappings[slot].physical = sma_offset;
            mappings[slot].size = rounded;
            mappings[slot].prot = prot & 7;
            mappings[slot].shared = (flags&3) == 1;
            break;
        }
    }
    if (result != (void *)-1) mapping_merge();
    unlock();
    if (result != (void *)-1 && (u32)fd < FD_LIMIT && kinds[fd] == 3) {
        lock();
        int status = spi_fifo_map(result, length, channels[fd]);
        unlock();
        if (status) { raw(91, (long)result, length, 0, 0, 0, 0); return (void *)-1; }
    }
    if (result != (void *)-1 && (u32)fd < FD_LIMIT && kinds[fd] == 1) {
        lock();
        int status = pwm_map(result, length, channels[fd]);
        unlock();
        if (status) { raw(91, (long)result, length, 0, 0, 0, 0); return (void *)-1; }
    }
    return result;
}
void *mmap(void *address, size_t length, int prot, int flags, int fd, long offset) {
    return map_file(address, length, prot, flags, fd, (u32)offset);
}
void *mmap64(void *address, size_t length, int prot, int flags, int fd, unsigned long long offset) {
    return map_file(address, length, prot, flags, fd, offset);
}
int munmap(void *address, size_t length) {
    if (!length || ((u32)address & 4095U) || length > 0xfffff000U) return checked(raw(91, (long)address, length, 0, 0, 0, 0));
    unsigned long long start = (u32)address, end = start + ((length + 4095U) & ~4095U);
    lock();
    if (!mapping_room(start, end, 0)) { unlock(); return error(12); }
    int result = checked(raw(91, (long)address, length, 0, 0, 0, 0));
    if (!result) mmio_forget(address, length);
    if (!result) { mapping_forget(start, end); mapping_merge(); }
    unlock();
    return result;
}


/* Keep mremap bookkeeping in the same bounded scratch space as mprotect.
 * Source and fixed destination are disjoint; at most four boundary cuts are
 * added to the live records, including relocated source fragments. */
static void mapping_piece(int *count, const struct mapping *map,
                          unsigned long long bottom, unsigned long long top, long long delta) {
    if (bottom >= top) return;
    struct mapping piece = *map;
    piece.virtual = bottom + delta;
    piece.physical += bottom - map->virtual;
    piece.size = top - bottom;
    protection_plan[(*count)++] = piece;
}
static int remapping_plan(unsigned long long start, u32 old_size, u32 new_size,
                          unsigned long long target, int fixed, int failed, int extend_prot, u32 extend_physical, int extend_shared) {
    unsigned long long source_end = start + old_size, target_end = target + new_size;
    unsigned long long kept_end = start + (old_size < new_size ? old_size : new_size);
    int count = 0;
    for (int i = 0; i < RECORDS; i++) {
        const struct mapping *map = &mappings[i];
        unsigned long long bottom = map->virtual, top = bottom + map->size;
        if (!map->size) continue;
        unsigned long long cuts[6] = {bottom, top, start, source_end, target, target_end};
        /* Failure may have deleted only the shortened source tail. */
        if (failed) cuts[2] = failed == 2 ? start : start + new_size;
        for (int a = 1; a < 6; a++) {
            unsigned long long cut = cuts[a]; int b = a;
            while (b && cuts[b-1] > cut) { cuts[b] = cuts[b-1]; b--; }
            cuts[b] = cut;
        }
        for (int part = 0; part < 5; part++) {
            unsigned long long lo = cuts[part], hi = cuts[part+1];
            if (lo < bottom || hi > top || lo >= hi) continue;
            int remove_source = failed == 1 ? new_size < old_size && lo >= start+new_size && hi <= source_end : lo >= start && hi <= source_end;
            int remove_target = fixed && lo >= target && hi <= target_end;
            if (!remove_source && !remove_target) mapping_piece(&count, map, lo, hi, 0);
        }
        if (!failed) {
            unsigned long long lo = bottom > start ? bottom : start;
            unsigned long long hi = top < kept_end ? top : kept_end;
            mapping_piece(&count, map, lo, hi, (long long)target-start);
        }
    }
    if (!failed && new_size > old_size && extend_physical) {
        struct mapping extension = {(u32)(target+old_size), extend_physical, new_size-old_size, extend_prot, extend_shared};
        protection_plan[count++] = extension;
    }
    return mapping_compact(count);
}
static int mmio_intersects(unsigned long long start, unsigned long long end) {
    for (int i = 0; i < 32; i++)
        if (mmio_regions[i].base && start < (unsigned long long)mmio_regions[i].base+mmio_regions[i].size && end > mmio_regions[i].base)
            return 1;
    return 0;
}
void *mremap(void *address, size_t old_length, size_t new_length, int flags, ...) {
    void *destination = (void *)0;
    if (flags & 2) {
        va_list args; __builtin_va_start(args, flags);
        destination = __builtin_va_arg(args, void *); __builtin_va_end(args);
    }
    unsigned long long start = (u32)address, target = (u32)destination;
    unsigned long long old_rounded = ((unsigned long long)old_length+4095) & ~4095ULL;
    unsigned long long new_rounded = ((unsigned long long)new_length+4095) & ~4095ULL;
    int fixed = flags & 2;
    /* These checks precede destructive FIXED unmaps in Linux 3.5. Reject
     * malformed input before touching either kernel mappings or metadata. */
    if ((start & 4095) || !new_rounded || new_rounded > 0xfffff000ULL || old_rounded > 0xfffff000ULL ||
        start+old_rounded > 0x100000000ULL ||
        (fixed && (!(flags & 1) || (target & 4095) || target+new_rounded > 0x100000000ULL ||
                   (target < start+old_rounded && start < target+new_rounded)))) {
        error(22); return (void *)-1;
    }
    u32 old_size = old_rounded, new_size = new_rounded;
    lock();
    int source_tracked = 0, target_tracked = 0;
    for (int i = 0; i < RECORDS; i++) if (mappings[i].size) {
        unsigned long long bottom = mappings[i].virtual, top = bottom+mappings[i].size;
        if (start < top && bottom < start+(old_size ? old_size : 1)) source_tracked = 1;
        if (fixed && target < top && bottom < target+new_size) target_tracked = 1;
    }
    /* Trapped peripherals cannot be moved without rebuilding signal regions.
     * Reject only operations actually touching a registered MMIO interval. */
    if (mmio_intersects(start, start+(old_size ? old_size : 1)) ||
        (fixed && mmio_intersects(target, target+new_size))) {
        unlock(); error(95); return (void *)-1;
    }
    if (!source_tracked && !target_tracked) {
        void *result = (void *)checked(raw(163, (long)address, old_length, new_length, flags, (long)destination, 0));
        unlock(); return result;
    }
    /* Zero old-size clones and post-3.5 DONTUNMAP semantics are unsupported
     * for tracked mappings. Ordinary, untracked operations pass through. */
    if (!old_size || (flags & ~3)) { unlock(); error(95); return (void *)-1; }
    int extend_prot = 0, extend_shared = 0; u32 extend_physical = 0;
    if (source_tracked && new_size > old_size) {
        unsigned long long cursor = start; u32 base_physical = 0;
        while (cursor < start+old_size) {
            const struct mapping *found = (void *)0;
            for (int i = 0; i < RECORDS; i++)
                if (mappings[i].size && cursor >= mappings[i].virtual && cursor-(unsigned long long)mappings[i].virtual < mappings[i].size) {
                    found = &mappings[i]; break;
                }
            if (!found) { unlock(); error(95); return (void *)-1; }
            u32 physical = found->physical + cursor-found->virtual;
            if (cursor == start) base_physical = physical;
            else if (physical != base_physical+cursor-start) { unlock(); error(95); return (void *)-1; }
            extend_prot = found->prot;
            extend_shared = found->shared;
            cursor = (unsigned long long)found->virtual+found->size;
        }
        u32 arena_base, arena_size;
        if (base_physical >= BMA_BASE && base_physical-BMA_BASE < BMA_SIZE) { arena_base = BMA_BASE; arena_size = BMA_SIZE; }
        else if (base_physical >= SMA_BASE && base_physical-SMA_BASE < SMA_SIZE) { arena_base = SMA_BASE; arena_size = SMA_SIZE; }
        else { unlock(); error(95); return (void *)-1; }
        if (new_size > arena_size-(base_physical-arena_base)) { unlock(); error(22); return (void *)-1; }
        extend_physical = base_physical+old_size;
    }
    /* A nonfixed MAYMOVE can land anywhere vacant. Reserving without a
     * possible destination merge is the conservative capacity upper bound. */
    unsigned long long planned_target = fixed ? target : start;
    int count = remapping_plan(start, old_size, new_size, planned_target, fixed, 0, extend_prot, extend_physical, extend_shared);
    int move_room = 1;
    if (!fixed && (flags & 1) && source_tracked) {
        int moved_records = 0;
        for (int i = 0; i < RECORDS; i++)
            if (mappings[i].size && start < (unsigned long long)mappings[i].virtual+mappings[i].size &&
                start+(old_size < new_size ? old_size : new_size) > mappings[i].virtual) moved_records++;
        /* QEMU can move even a shrinking MAYMOVE request. Any extension
         * merges with the last source fragment: physical
         * continuity and its protection were established above. Count moved
         * fragments separately from survivors so no unknown target merge is
         * assumed when reserving table capacity. */
        move_room = mapping_room(start, start+old_size, moved_records);
    }
    int failure_room = fixed ? remapping_plan(start, old_size, new_size, target, 1, 1, 0, 0, 0) :
        (flags & 1) ? remapping_plan(start, old_size, new_size, start, 0, 2, 0, 0, 0) : 0;
    if (!move_room || count > RECORDS || failure_room > RECORDS) {
        unlock(); error(12); return (void *)-1;
    }
    void *result = (void *)checked(raw(163, (long)address, old_length, new_length, flags, (long)destination, 0));
    if (result != (void *)-1)
        mapping_commit(remapping_plan(start, old_size, new_size, (u32)result, fixed, 0, extend_prot, extend_physical, extend_shared));
    else if (fixed) {
        /* Linux 3.5 mremap_to unmaps the destination, then a shortened old
         * tail, BEFORE validating/resizing the source. Failure does not imply
         * rollback of those unmaps. Revoke only these possibly deleted spans. */
        mapping_commit(remapping_plan(start, old_size, new_size, target, 1, 1, 0, 0, 0));
    } else if (flags & 1) {
        /* QEMU 11 target_mremap implements MAYMOVE using host FIXED and in
         * reserved_va mode re-reserves the source even when host mremap fails.
         * Native Linux restores the source, but this shim must also be safe
         * under its actual emulator: revoke the possibly cleared old range. */
        mapping_commit(remapping_plan(start, old_size, new_size, start, 0, 2, 0, 0, 0));
    }
    unlock();
    return result;
}

struct poll_descriptor { int fd; short events, revents; };
int poll(struct poll_descriptor *descriptors, u32 count, int timeout) {
    int substitute = 0;
    for (u32 i = 0; i < count; i++) {
        int fd = descriptors[i].fd;
        if ((u32)fd < FD_LIMIT && (kinds[fd] == 1 || kinds[fd] == 3 || isp_poll_device(fd))) { substitute = 1; break; }
    }
    if (!substitute) return checked(raw(168, (long)descriptors, count, timeout, 0, 0, 0));
    if (count > 4096) return error(22);
    struct poll_descriptor *waiting = __builtin_alloca(count * sizeof(*waiting));
    for (u32 i = 0; i < count; i++) {
        waiting[i] = descriptors[i];
        int fd = waiting[i].fd;
        /* Device notifications replace always-ready regular backing files. */
        if ((u32)fd < FD_LIMIT && kinds[fd] == 1 && pwm_timing_active) {
            waiting[i].fd = pwm_counters[channels[fd]].notification;
            waiting[i].events = (descriptors[i].events & (1|0x40)) ? 1 : 0;
        } else if (isp_index(fd) == ISP_SSIF || isp_index(fd) == ISP_PP || isp_index(fd) == ISP_IPCS) {
            waiting[i].fd = isp_index(fd) == ISP_PP ? pp_notification : isp_index(fd) == ISP_IPCS ? ipc_queues[1].notification : ssif_notification;
            waiting[i].events = (descriptors[i].events & (1|0x40)) ? 1 : 0;
        } else if ((u32)fd < FD_LIMIT && (kinds[fd] == 1 || kinds[fd] == 3 || isp_poll_device(fd))) waiting[i].fd = -1;
    }
    int result = checked(raw(168, (long)waiting, count, timeout, 0, 0, 0));
    if (result >= 0) for (u32 i = 0; i < count; i++) {
        descriptors[i].revents = waiting[i].revents;
        int fd = descriptors[i].fd;
        if ((u32)fd < FD_LIMIT && kinds[fd] == 1 && pwm_timing_active && (waiting[i].revents & 1)) {
            unsigned long long one;
            if (raw(3, waiting[i].fd, (long)&one, 8, 0, 0, 0) == 8)
                descriptors[i].revents = (descriptors[i].events & (1|0x40));
            else descriptors[i].revents = 0;
        }
        if ((isp_index(descriptors[i].fd) == ISP_SSIF || isp_index(descriptors[i].fd) == ISP_PP || isp_index(descriptors[i].fd) == ISP_IPCS) && (waiting[i].revents & 1))
            descriptors[i].revents = (waiting[i].revents & ~1) | (descriptors[i].events & (1|0x40));
    }
    if (result > 0) {
        result = 0;
        for (u32 i = 0; i < count; i++) result += descriptors[i].revents != 0;
    }
    return result;
}
