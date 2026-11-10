/* Freestanding NX300M 1.15 runtime-settings callbacks (2026-11-08 rev2).
 * Pure syscalls with proper ARM EABI register constraints.
 * The Ecore select wrapper is the natural-thread execution point; the
 * custom-key handler is installed by the loader into the liveview key table.
 * Stock Reset/Still IQ/Session/capture arbitration/RAW/LCD DMA untouched. */
#include "settings_runtime_stock.h"

/* ARM EABI syscall: r7=nr, r0-r5=args, r0=return.
 * Register-bound operands prevent the compiler from allocating conflicting
 * registers (the B3 bug: "r" constraints let r7 overlap arg registers). */
static inline int sys3(unsigned nr, unsigned a, unsigned b, unsigned c) {
    register unsigned r0 __asm__("r0") = a;
    register unsigned r1 __asm__("r1") = b;
    register unsigned r2 __asm__("r2") = c;
    register unsigned r7 __asm__("r7") = nr;
    __asm__ volatile("svc 0"
        : "+r"(r0) : "r"(r1), "r"(r2), "r"(r7) : "memory");
    return (int)r0;
}
static inline int sys1(unsigned nr, unsigned a) {
    register unsigned r0 __asm__("r0") = a;
    register unsigned r7 __asm__("r7") = nr;
    __asm__ volatile("svc 0" : "+r"(r0) : "r"(r7) : "memory");
    return (int)r0;
}
static inline unsigned now_sec(void) {
    /* Kernels built without CONFIG_COMPAT_32BIT_TIME reject the legacy
     * clock_gettime (263) with ENOSYS, which would leave every timestamp at
     * zero and silently disable the launch debounce and the watchdog. Prefer
     * clock_gettime64 (403) and fall back to the 32-bit form. */
    unsigned long long ts64[2] = {0, 0};
    if (sys3(403 /*clock_gettime64*/, 0 /*CLOCK_REALTIME*/, (unsigned)ts64, 0) >= 0)
        return (unsigned)ts64[0];
    unsigned ts32[2] = {0, 0};
    sys3(263 /*clock_gettime*/, 0, (unsigned)ts32, 0);
    return ts32[0];
}

static int sl_open(const char *path,unsigned flags){return sys3(5,(unsigned)path,flags,0);}
static int sl_close(int fd){return sys1(6,(unsigned)fd);}
static int sl_read(int fd,void *buf,unsigned size){return sys3(3,(unsigned)fd,(unsigned)buf,size);}
static int sl_write(int fd,const void *buf,unsigned size){return sys3(4,(unsigned)fd,(unsigned)buf,size);}

struct settings_config {
    unsigned magic, abi;
    volatile unsigned bootstrapped;
    unsigned select_slot;
    unsigned orig_select;
    unsigned wrapper_addr;
    unsigned pt_func;
    volatile unsigned pt_state;
    unsigned pt_deadline;
    unsigned key_entry;
    unsigned orig_key_handler;
    unsigned custom_enabled;
    unsigned launch_action;
    unsigned last_launch;
    char launch_cmd[128];
    char request_path[96];
    char bootstrap_marker[96];
    char seen[128];
};

__attribute__((visibility("hidden")))
struct settings_config nx_settings_config={0x4e585352,1,0,0,0,0,0,0,0,0,0,0,0,0,{0},{0},{0},{0}};

struct nx_tv { int sec, usec; };

int nx_ecore_select_wrapper(int nfds, void *rfds, void *wfds, void *efds, void *timeout) {
    struct settings_config *c = &nx_settings_config;
    if (!c->bootstrapped) {
        c->bootstrapped = 1;
        int fd = sl_open(c->bootstrap_marker, 1|0100|01000);
        if (fd >= 0) { sl_write(fd, "bootstrap\n", 10); sl_close(fd); }
    }
    unsigned now = now_sec();
    /* The power-activity deadline must fire even while select blocks, so the
     * wait is clamped to the remaining watchdog time instead of relying on a
     * later pass through the wrapper. */
    void *tmo = timeout;
    struct nx_tv local;
    int clamped = 0;
    if (c->pt_state == 1) {
        int rem = (int)(c->pt_deadline - now);
        if (rem < 0) rem = 0;
        if (!timeout) {
            local.sec = rem; local.usec = 0; tmo = &local; clamped = 1;
        } else {
            struct nx_tv *caller = (struct nx_tv *)timeout;
            if (caller->sec < 0 || caller->sec > rem) {
                local.sec = rem; local.usec = 0; tmo = &local; clamped = 1;
            }
        }
    }
    int ret = ((int (*)(int,void*,void*,void*,void*))c->orig_select)(nfds, rfds, wfds, efds, tmo);
    if (clamped && timeout) {
        struct nx_tv *caller = (struct nx_tv *)timeout;
        caller->sec = local.sec; caller->usec = local.usec;
    }
    now = now_sec();
    if (c->pt_state == 1 && c->pt_func && (int)(now - c->pt_deadline) >= 0) {
        ((void (*)(int))c->pt_func)(0);
        c->pt_state = 0;
    }
    int fd = sl_open(c->request_path, 0);
    if (fd >= 0) {
        char body[128];
        int n = sl_read(fd, body, (unsigned)sizeof body - 1);
        sl_close(fd);
        if (n > 0) {
            body[n] = 0;
            int changed = 0;
            unsigned old_len = 0;
            while (old_len < 127 && c->seen[old_len]) old_len++;
            if (n != (int)old_len) changed = 1;
            for (int i = 0; i < n && !changed; i++)
                if (body[i] != c->seen[i]) changed = 1;
            if (changed) {
                for (int i = 0; i < n && i < 127; i++) c->seen[i] = body[i];
                c->seen[n < 127 ? n : 127] = 0;
                if (body[0]=='p' && body[1]=='t' && body[2]==' ' && c->pt_func) {
                    unsigned want = body[3] == '1' ? 1u : 0u;
                    if (want) c->pt_deadline = now_sec() + 45;
                    if (c->pt_state != want) {
                        ((void (*)(int))c->pt_func)((int)want);
                        c->pt_state = want;
                    }
                } else if (body[0]=='c' && body[1]=='k' && body[2]==' ') {
                    if (body[3] >= '0' && body[3] <= '9')
                        c->custom_enabled = (unsigned)(body[3]-'0') ? 1u : 0u;
                } else if (body[0]=='c' && body[1]=='k' && body[2]=='-' &&
                           body[3]=='a' && body[4]=='c' && body[5]=='t' &&
                           body[6]=='i' && body[7]=='o' && body[8]=='n') {
                    /* "ck-action N": body[2] is '-', so this branch is not
                     * reachable through the space-delimited "ck " test. */
                    unsigned v = 0;
                    int i = 9;
                    while (i < n && (body[i] < '0' || body[i] > '9')) i++;
                    while (i < n && body[i] >= '0' && body[i] <= '9')
                        v = v * 10 + (unsigned)(body[i++] - '0');
                    if (v <= 15) c->launch_action = v;
                }
            }
        }
    }
    return ret;
}

void nx_settings_custom_handler(void *event) {
    struct settings_config *c = &nx_settings_config;
    unsigned action = event ? ((unsigned *)event)[2] : 0;
    unsigned now = now_sec();
    if (c->custom_enabled && action == (c->launch_action & 15u) &&
        (int)(now - c->last_launch) >= 2) {
        c->last_launch = now;
        int pid = sys1(2, 0);
        if (pid == 0) {
            /* Build argv/envp element by element at runtime.
             *
             * A `static char *av[]={"sh",...}` initializer makes the linker
             * bake link-time absolute addresses into .data. The blob is
             * copied to an arbitrary mmap region with no relocation pass, so
             * those words point outside the process and execve fails with
             * EFAULT (measured 0x0e with valid arrays, versus 0x02 for a bad
             * path, which proved the path argument itself was correct).
             * Assigning each element at runtime keeps every pointer valid. */
            static char sh_name[4] = {'s', 'h', 0, 0};
            static char dash_c[4] = {'-', 'c', 0, 0};
            static char env_home[] = "HOME=/root";
            static char env_path[] = "PATH=/bin:/usr/bin:/sbin";
            static char env_display[] = "DISPLAY=:0";
            static char env_root[] = "NX300M_MOD_ROOT=/mnt/mmc/nx300m-mod";
            char *av[4];
            char *ev[6];
            av[0] = sh_name;
            av[1] = dash_c;
            av[2] = c->launch_cmd;
            av[3] = 0;
            ev[0] = env_home;
            ev[1] = env_path;
            ev[2] = env_display;
            ev[3] = env_root;
            ev[4] = 0;
            ev[5] = 0;
            sys3(11, (unsigned)"/bin/sh", (unsigned)av, (unsigned)ev);
            sys1(1, 127);
        }
    }
    ((void (*)(void *))c->orig_key_handler)(event);
}
