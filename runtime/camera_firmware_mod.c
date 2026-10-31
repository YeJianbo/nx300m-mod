/* NX300M 1.15 camera UI controls.
 * Mode requests call the executable's exported entry on the Ecore thread.
 * Executable-local direct BL calls use verified in-memory entry trampolines.
 * Exposure UserData controls use exposure_controls.so, never fabricated getters.
 * AE metering stays stock. Capture Tv limits belong to shutter_limit.so only.
 * SPDX-License-Identifier: AGPL-3.0-or-later
 */
extern void *dlsym(void *, const char *);
extern void *fopen(const char *, const char *);
extern int fscanf(void *, const char *, ...);
extern int fprintf(void *, const char *, ...) __attribute__((format(printf, 2, 3)));
extern int fclose(void *);
extern int rename(const char *, const char *);
extern int getpid(void);
extern long time(long *);
extern void abort(void);
extern void *stderr;
extern int memcmp(const void *, const void *, unsigned int);
extern void *memcpy(void *, const void *, unsigned int);
extern void *mmap(void *, unsigned int, int, int, int, int);
extern int mprotect(void *, unsigned int, int);
extern int munmap(void *, unsigned int);
extern char *getenv(const char *);
extern int snprintf(char *, unsigned int, const char *, ...);
extern int access(const char *, int);
extern int strncmp(const char *, const char *, unsigned int);
extern void *calloc(unsigned int, unsigned int);
extern void free(void *);
extern int fork(void);
extern int execve(const char *, char *const [], char *const []);
extern void _exit(int);
extern int close(int);
extern int open(const char *, int, ...);
extern int dup2(int, int);
extern char **environ;
#include "camera_firmware_mod_stock.h"
#include "manual_lens_wifi_patch.h"

#define READY "/var/run/nx300m-firmware-mod-ready"
#define REQUEST "/var/run/nx300m-firmware-mod-request"
#define RESPONSE "/var/run/nx300m-firmware-mod-response"

static unsigned int last_request, pending_id, pending_expiry;
static int pending_mode;
static int owner_pid;
static unsigned int patch_mask, mode_locked, animation_mode, custom_enabled, iso_lock;
static unsigned int mode_events, mode_blocked, animation_calls, animation_suppressed;
static unsigned int custom_calls, custom_launches, iso_blocks;
static unsigned int entry_diagnostics;
static void (*stock_mode_event)(void *), (*stock_mode_draw)(void *), (*stock_custom)(void *);
static void (*stock_animation_start)(void *, double);
static int (*stock_iso_set)(void *, int, int);

static void flush_code(void *begin, void *end) {
    register unsigned int first __asm__("r0") = (unsigned int)begin;
    register unsigned int last __asm__("r1") = (unsigned int)end;
    register unsigned int flags __asm__("r2") = 0;
    register unsigned int syscall __asm__("r7") = 0x0f0002;
    __asm__ volatile("svc 0" : "+r"(first) : "r"(last), "r"(flags), "r"(syscall) : "memory");
}

static unsigned int patch_entry(const char *symbol, unsigned int address,
                                const unsigned char *expected, unsigned int bytes,
                                void *replacement, void **fallback) {
    unsigned char *entry = dlsym((void *)0, symbol);
    if (!entry || (address && (unsigned int)entry != address) || memcmp(entry, expected, bytes)) {
        fprintf(stderr, "[NX FIRMWARE] stock bytes differ, retained: %s\n", symbol);
        return 0;
    }
    unsigned int *bridge = mmap((void *)0, 4096, 3, 0x22, -1, 0);
    if (bridge == (void *)-1) return 0;
    memcpy(bridge, entry, 8);
    bridge[2] = 0xe51ff004;
    bridge[3] = (unsigned int)(entry + 8);
    flush_code(bridge, bridge + 4);
    if (mprotect(bridge, 4096, 5)) { munmap(bridge, 4096); return 0; }
    void *page = (void *)((unsigned int)entry & ~4095u);
    if (mprotect(page, 4096, 7)) { munmap(bridge, 4096); return 0; }
    *fallback = bridge;
    unsigned int words[2] = {0xe51ff004, (unsigned int)replacement};
    memcpy(entry, words, 8);
    flush_code(entry, entry + 8);
    if (mprotect(page, 4096, 5)) abort();
    return 1;
}

static void physical_mode_event(void *event) {
    __atomic_add_fetch(&mode_events, 1, __ATOMIC_RELAXED);
    if (__atomic_load_n(&mode_locked, __ATOMIC_RELAXED)) {
        __atomic_add_fetch(&mode_blocked, 1, __ATOMIC_RELAXED);
        return;
    }
    stock_mode_event(event);
}

static void animation_start(void *self, double interval) {
    __atomic_add_fetch(&animation_calls, 1, __ATOMIC_RELAXED);
    if (__atomic_load_n(&animation_mode, __ATOMIC_RELAXED)) {
        void (*stop)(void *) = dlsym((void *)0, "_ZN15CAPPGUIModeView14ani_timer_stopEv");
        stop(self);
        __atomic_add_fetch(&animation_suppressed, 1, __ATOMIC_RELAXED);
        return;
    }
    stock_animation_start(self, interval);
}

static void mode_draw(void *self) {
    unsigned int selected = __atomic_load_n(&animation_mode, __ATOMIC_RELAXED);
    void (*stop)(void *) = dlsym((void *)0, "_ZN15CAPPGUIModeView14ani_timer_stopEv");
    if (selected == 2) {
        stop(self);
        void (*clear)(void *) = dlsym((void *)0, "_ZN15CAPPGUIModeView13ClearModeDialEv");
        clear(self);
        __atomic_add_fetch(&animation_suppressed, 1, __ATOMIC_RELAXED);
        return;
    }
    stock_mode_draw(self);
    if (selected == 1) {
        /* DrawModeDial has built its original objects but its timer was skipped.
         * Apply the stock frame-eight position and centre image, then the final
         * frame's alpha image. No intermediate rotation callbacks are needed. */
        int x = 0, y = 0;
        void (*geometry)(int, int *, int *, int *, int *) = dlsym((void *)0, "osd_get_geometry");
        void (*move)(int, int, int) = dlsym((void *)0, "osd_move_item_direct");
        void (*update)(void *) = dlsym((void *)0, "_ZN15CAPPGUIModeView23update_center_mode_iconEv");
        void (*frame)(void *) = dlsym((void *)0, "_ZN15CAPPGUIModeView17draw_modedial_aniEv");
        geometry(0x35c, &x, &y, (int *)0, (int *)0);
        move(0x35c, *((unsigned char *)self + 8) ? -22 : 103, y);
        update(self);
        *((int *)((char *)self + 0x14)) = 18;
        frame(self);
    }
}

struct settings_launcher {
    const char *root;
    char path[512];
};

static int resolve_settings_launcher(struct settings_launcher *launcher) {
    char binary[512];
    const char *root = getenv("NX300M_MOD_ROOT");
    if (!root) root = access("/mnt/mmc/nx300m-mod/panel.sh", 4) ? "/mod" : "/mnt/mmc/nx300m-mod";
    if (snprintf(launcher->path, sizeof launcher->path, "%s/panel.sh", root) >= (int)sizeof launcher->path ||
        snprintf(binary, sizeof binary, "%s/bin/nxui.arm", root) >= (int)sizeof binary ||
        access(launcher->path, 4) || access(binary, 1)) return -111;
    launcher->root = root;
    return 0;
}

static int launch_settings(void) {
    struct settings_launcher launcher;
    char root_environment[544];
    int code = resolve_settings_launcher(&launcher);
    if (code) return code;
    unsigned int count = 0, kept = 0;
    while (environ[count]) count++;
    char **environment = calloc(count + 3, sizeof(char *));
    if (!environment) return -112;
    for (unsigned int i = 0; i < count; i++)
        if (strncmp(environ[i], "LD_PRELOAD=", 11) && strncmp(environ[i], "LD_AUDIT=", 9) &&
            strncmp(environ[i], "NX300M_MOD_ROOT=", 16) && strncmp(environ[i], "PATH=", 5))
            environment[kept++] = environ[i];
    snprintf(root_environment, sizeof root_environment, "NX300M_MOD_ROOT=%s", launcher.root);
    environment[kept++] = root_environment;
    environment[kept++] = "PATH=/bin:/usr/bin:/sbin:/usr/sbin";
    int child = fork();
    if (!child) {
        char *args[] = {"/bin/sh", launcher.path, "settings", (char *)0};
        int input = open("/dev/null", 0);
        if (input >= 0) dup2(input, 0);
        for (int fd = 3; fd < 4096; fd++) close(fd);
        execve("/bin/sh", args, environment);
        _exit(127);
    }
    free(environment);
    if (child < 0) return -112;
    __atomic_add_fetch(&custom_launches, 1, __ATOMIC_RELAXED);
    return child;
}

static void custom_key(void *event) {
    __atomic_add_fetch(&custom_calls, 1, __ATOMIC_RELAXED);
    if (__atomic_load_n(&custom_enabled, __ATOMIC_RELAXED) && launch_settings() > 0) return;
    stock_custom(event);
}

static int iso_set(void *self, int data_id, int value) {
    if (data_id == 0x3b && __atomic_load_n(&iso_lock, __ATOMIC_RELAXED) == 6400 && value != 0x003b000f) {
        value = 0x003b000f;
        __atomic_add_fetch(&iso_blocks, 1, __ATOMIC_RELAXED);
    }
    return stock_iso_set(self, data_id, value);
}

static int actual_auto_iso(void) {
    void *(*instance)(void) = dlsym((void *)0, "_ZN22CDataManagementService11GetInstanceEv");
    int (*get)(void *, int) = dlsym((void *)0, "_ZN22CDataManagementService11GetUserDataEi");
    void *service = instance ? instance() : (void *)0;
    return get && service ? get(service, 0x3b) : -100;
}

static int configure_iso_lock(unsigned int requested) {
    if (!(patch_mask & 16)) return -110;
    if (requested != 0 && requested != 6400) return -102;
    unsigned int before = __atomic_load_n(&iso_lock, __ATOMIC_RELAXED);
    if (!requested) {
        __atomic_store_n(&iso_lock, 0, __ATOMIC_RELAXED);
        return 0;
    }
    void *(*instance)(void) = dlsym((void *)0, "_ZN22CDataManagementService11GetInstanceEv");
    void *service = instance ? instance() : (void *)0;
    if (!service) return -100;
    __atomic_store_n(&iso_lock, requested, __ATOMIC_RELAXED);
    int code = stock_iso_set(service, 0x3b, 0x003b000f);
    if (!code && actual_auto_iso() != 0x003b000f) code = -113;
    if (code) __atomic_store_n(&iso_lock, before, __ATOMIC_RELAXED);
    return code;
}

/* Explicit lab commands exercise the installed entry, not a replacement-only
 * unit stub. They never synthesize sensor data or current-mode readback. */
static int exercise_entry(unsigned int selected) {
    if (selected == 1) {
        if (!mode_locked || !(patch_mask & 1)) return -114;
        void (*entry)(void *) = dlsym((void *)0, NX_MODE_EVENT_SYMBOL);
        unsigned int event[4] = {0};
        entry(event);
        return 0;
    }
    if (selected == 2) {
        if (!animation_mode || (patch_mask & 6) != 6) return -114;
        void *(*instance)(void) = dlsym((void *)0, "_ZN15CAPPGUIModeView11getInstanceEv");
        void (*entry)(void *) = dlsym((void *)0, NX_MODE_DRAW_SYMBOL);
        void *view = instance ? instance() : (void *)0;
        if (!view) return -100;
        entry(view);
        return 0;
    }
    if (selected == 3) {
        if (!custom_enabled || !(patch_mask & 8)) return -114;
        /* An absent installation must not send a synthetic key into stock. */
        struct settings_launcher launcher;
        int code = resolve_settings_launcher(&launcher);
        if (code) return code;
        void (*entry)(void *) = dlsym((void *)0, NX_CUSTOM_SYMBOL);
        unsigned int event[4] = {0};
        entry(event);
        return 0;
    }
    if (selected == 4) {
        if (iso_lock != 6400 || !(patch_mask & 16)) return -114;
        void *(*instance)(void) = dlsym((void *)0, "_ZN22CDataManagementService11GetInstanceEv");
        int (*entry)(void *, int, int) = dlsym((void *)0, NX_ISO_SET_SYMBOL);
        void *service = instance ? instance() : (void *)0;
        return service ? entry(service, 0x3b, 0x003b000e) : -100;
    }
    return -102;
}

/* Constructors run before camera threads exist; the eight-byte instruction
 * replacement must never race a thread executing an old half of the prologue. */
__attribute__((constructor)) static void install_entries(void) {
    const char *diagnostics = getenv("NX300M_FIRMWARE_ENTRY_DIAGNOSTICS");
    entry_diagnostics = diagnostics && diagnostics[0] == '1' && diagnostics[1] == '\0';
    if (patch_entry(NX_MODE_EVENT_SYMBOL, NX_MODE_EVENT_ADDRESS, nx_mode_event_stock,
                    sizeof nx_mode_event_stock, physical_mode_event, (void **)&stock_mode_event)) patch_mask |= 1;
    if (patch_entry(NX_ANIM_START_SYMBOL, NX_ANIM_START_ADDRESS, nx_anim_start_stock,
                    sizeof nx_anim_start_stock, animation_start, (void **)&stock_animation_start)) patch_mask |= 2;
    if (patch_entry(NX_MODE_DRAW_SYMBOL, NX_MODE_DRAW_ADDRESS, nx_mode_draw_stock,
                    sizeof nx_mode_draw_stock, mode_draw, (void **)&stock_mode_draw)) patch_mask |= 4;
    if (patch_entry(NX_CUSTOM_SYMBOL, NX_CUSTOM_ADDRESS, nx_custom_stock,
                    sizeof nx_custom_stock, custom_key, (void **)&stock_custom)) {
        patch_mask |= 8;
        custom_enabled = 1;
    }
    if (patch_entry(NX_ISO_SET_SYMBOL, NX_ISO_SET_ADDRESS, nx_iso_set_stock,
                    sizeof nx_iso_set_stock, iso_set, (void **)&stock_iso_set)) patch_mask |= 16;
    if (patch_manual_lens_wifi()) patch_mask |= 32;
    /* Both hooks are needed: stopping the timer alone leaves an unfinished dial. */
    if ((patch_mask & 6) == 6) animation_mode = 1;
    fprintf(stderr, "[NX FIRMWARE] verified entry patch mask=%u; animation=%u; AE stock\n",
            patch_mask, animation_mode);
}

/* eUI_MODE values from UI_Get_ModeName at 0x1d0010, not the
 * eUI_MODEDIAL_POS inputs of UI_Get_Next_Mode_By_Dial_Event (0x1a7400). */
static int valid_mode(int mode) {
    return (mode >= 1 && mode <= 8) || mode == 30;
}

static int current_mode(void) {
    typedef int (*GetValue)(int);
    GetValue get = (GetValue)dlsym((void *)0, "_Z12UI_Get_Value14eUI_VALUE_ITEM");
    return get ? get(0x3f0) : -100;
}

/* Invoke only from the Ecore poll callback. The stock entry returns void. */
int nx_switch_mode_dial(int mode) {
    typedef void (*StockSwitch)(int);
    typedef void (*SetValue)(int, int);
    if (!valid_mode(mode)) return -102;
    StockSwitch run = (StockSwitch)dlsym((void *)0, "_Z25UI_Manager_ModeDial_Event8eUI_MODE");
    SetValue set = (SetValue)dlsym((void *)0, "_Z12UI_Set_Value14eUI_VALUE_ITEMi");
    if (!run || !set) return -100;
    /* Match the stock manager's staging at 0x1a7100..0x1a7118. 0x3f2 is
     * the requested mode, while 0x3f0 remains the independently read current
     * mode. The stock asynchronous transition owns completion of that state. */
    set(0x3fc, 1);
    set(0x3f2, mode);
    run(mode);
    return 0;
}

static void respond(unsigned int id, int code) {
    void *file = fopen(RESPONSE ".tmp", "w");
    if (!file) return;
    /* Retain the 26-field wire format for installed clients. Retired AE fields
     * stay zero; the shutter-limit bridge owns its own status and counters. */
    fprintf(file, "%u %d %d %d %u %u %d %u %u %d %d %u %u %u %u %u %u %u %u %u %u %u %u %u %u %d\n",
            id, code, getpid(), current_mode(),
            0u, 0u, 0, 0u, 0u, 0, 0, 0u, 0u,
            mode_locked, animation_mode, custom_enabled, iso_lock, patch_mask,
            mode_events, mode_blocked, animation_calls, animation_suppressed,
            custom_calls, custom_launches, iso_blocks, actual_auto_iso());
    if (fclose(file) == 0) rename(RESPONSE ".tmp", RESPONSE);
}

static int firmware_mod_poll(void *unused) {
    (void)unused;
    if (getpid() != owner_pid) return 0;
    unsigned int now = (unsigned int)time((long *)0);
    if (pending_id) {
        int code = current_mode() == pending_mode ? 0 : -109;
        if (code == 0 || now > pending_expiry) {
            respond(pending_id, code);
            pending_id = 0;
        }
        return 1;
    }
    void *file = fopen(REQUEST, "r");
    if (!file) return 1;
    unsigned int id = 0, expires = 0, operation = 0, requested = 0;
    int fields = fscanf(file, "%u %u %u %u", &id, &expires, &operation, &requested);
    fclose(file);
    if (fields != 4 || !id || id == last_request) return 1;
    last_request = id;
    int code = 0;
    if (now > expires) code = -108;
    else if (operation > 8) code = -101;
    else if (operation == 1) {
        if (!valid_mode((int)requested)) code = -102;
        else if (current_mode() < 0) code = -100;
        else if (current_mode() != (int)requested) {
            code = nx_switch_mode_dial((int)requested);
            if (!code) {
                pending_id = id;
                pending_expiry = expires;
                pending_mode = (int)requested;
                return 1;
            }
        }
    } else if (operation == 2 || operation == 3) {
        /* A legacy disable request is harmless; never re-enable retired AE hooks. */
        if (requested) code = -101;
    } else if (operation == 4) {
        if (requested > 1) code = -102;
        else if (!(patch_mask & 1)) code = -110;
        else __atomic_store_n(&mode_locked, requested, __ATOMIC_RELAXED);
    } else if (operation == 5) {
        if (requested > 2) code = -102;
        else if ((patch_mask & 6) != 6) code = -110;
        else __atomic_store_n(&animation_mode, requested, __ATOMIC_RELAXED);
    } else if (operation == 6) {
        if (requested > 1) code = -102;
        else if (!(patch_mask & 8)) code = -110;
        else __atomic_store_n(&custom_enabled, requested, __ATOMIC_RELAXED);
    } else if (operation == 7) {
        code = configure_iso_lock(requested);
    } else if (operation == 8) {
        code = entry_diagnostics ? exercise_entry(requested) : -101;
    }
    respond(id, code);
    return 1;
}

void ecore_main_loop_begin(void) {
    typedef void *(*TimerAdd)(double, int (*)(void *), void *);
    TimerAdd add = (TimerAdd)dlsym((void *)-1, "ecore_timer_add");
    void (*begin)(void) = (void (*)(void))dlsym((void *)-1, "ecore_main_loop_begin");
    if (owner_pid != getpid() && add && add(0.1, firmware_mod_poll, (void *)0)) {
        owner_pid = getpid();
        pending_id = last_request = 0;
        void *ready = fopen(READY ".tmp", "w");
        if (ready) {
            fprintf(ready, "%d\n", owner_pid);
            if (fclose(ready) == 0) rename(READY ".tmp", READY);
        }
        fprintf(stderr, "[NX FIRMWARE] camera UI bridge ready; AE metering stock\n");
    }
    if (begin) begin();
}
