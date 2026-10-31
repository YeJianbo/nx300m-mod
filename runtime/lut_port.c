/* NX300M DRIMeIV LUT adapter. A selected table is forwarded only when the
 * original firmware calls ipcs_3d_lut; this does not invent a reload event.
 * ABI/layout: NX300M 1.15 libudd + CIpcs3DLutD4::SetParam.
 */
extern void *dlsym(void *, const char *);
extern void *fopen(const char *, const char *);
extern unsigned int fread(void *, unsigned int, unsigned int, void *);
extern int fscanf(void *, const char *, ...);
extern int fprintf(void *, const char *, ...);
extern int fclose(void *);
extern int fgetc(void *);
extern int rename(const char *, const char *);
extern int strcmp(const char *, const char *);
extern char *strcpy(char *, const char *);
extern unsigned int strlen(const char *);
extern int snprintf(char *, unsigned int, const char *, ...);
extern int getpid(void);
extern void abort(void);
extern void *stderr;
typedef unsigned int u32;
typedef unsigned short u16;
#include "../mod/metol/mod_paths.h"
enum { WORDS = 730, PLANES = 3, TOTAL_WORDS = WORDS * PLANES };
static u16 tables[TOTAL_WORDS];
static unsigned int generation, forwarded;
static unsigned int strength = 100;
static int active;
static int composed;
static const char *fallback_stage = "stock";
static char active_path[256];
static char selection_path[256];
static volatile int table_lock;
static void *updated_node;
static unsigned int updated_generation;
static int updated_active;
static unsigned int updated_strength;
static char updated_path[256];

static int valid_path(const char *path) {
    const char *root = nx_mod_root();
    if (!root) return 0;
    while (*root) if (*path++ != *root++) return 0;
    const char *prefix = "/looks/";
    while (*prefix) if (*path++ != *prefix++) return 0;
    if (!*path) return 0;
    for (; *path; path++) {
        if (*path == '.' && path[1] == '.') return 0;
        unsigned char c = (unsigned char)*path;
        if (!((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') ||
              c == '_' || c == '-' || c == '.' || c == '/')) return 0;
    }
    return 1;
}
static void publish(const char *stage) {
    void *file = fopen("/var/run/nx300m-lut-status.tmp", "w");
    if (!file) return;
    const char *root = nx_mod_root();
    fprintf(file, "{\"guest_pid\":%d,\"root\":\"%s\",\"selection_path\":\"%s\",\"generation\":%u,\"strength\":%u,\"active\":%d,\"forwarded_mask\":%u,\"stage\":\"%s\",\"composition\":\"%s\",\"image_verified\":false}\n",
        getpid(), root ? root : "", selection_path, generation, strength, active, forwarded, stage, active && composed ? "after-stock-3d-lut" : "direct");
    fclose(file);
    rename("/var/run/nx300m-lut-status.tmp", "/var/run/nx300m-lut-status.json");
}
static void select_table(int stock_source) {
    char path[256], counter[16], amount[16], trailing; unsigned int next_generation = 0, next_strength = 100;
    void *selection = fopen("/var/run/nx300m-lut-selection", "r");
    if (!selection) {
        if (!active && !generation && !selection_path[0] && forwarded == 7 && !strcmp(fallback_stage, "stock")) return;
        active = 0; forwarded = 0; generation = 0; strength = 100; selection_path[0] = 0;
        fallback_stage = "stock"; publish("stock-awaiting-stock-call"); return;
    }
    int parsed = fscanf(selection, "%15s %255s %15s %c", counter, path, amount, &trailing);
    fclose(selection);
    if (parsed >= 2) for (unsigned int i = 0; counter[i]; i++) {
        unsigned int digit = (unsigned int)(counter[i]-'0');
        if (digit > 9 || next_generation > 429496729U ||
            (next_generation == 429496729U && digit > 5)) { parsed = 0; break; }
        next_generation = next_generation * 10 + digit;
    }
    if (parsed == 3) {
        next_strength = 0;
        for (unsigned int i = 0; amount[i]; i++) {
            if (amount[i] < '0' || amount[i] > '9' || next_strength > 100) { parsed = 0; break; }
            next_strength = next_strength * 10 + (unsigned int)(amount[i]-'0');
        }
        if (next_strength > 100) parsed = 0;
    }
    if ((parsed != 2 && parsed != 3) || !next_generation || !valid_path(path)) {
        active = 0; forwarded = 0; selection_path[0] = 0;
        fallback_stage = "invalid-selection"; publish(fallback_stage); return;
    }
    strcpy(selection_path, path);
    void *file = (void *)0;
    int next_composed = 0;
    unsigned int length = strlen(path);
    if (stock_source && length >= 12 && !strcmp(path+length-12, "/table.d4lut")) {
        char variant[256];
        int count = snprintf(variant, sizeof variant, "%.*s/table-stock3d.d4lut", (int)(length-12), path);
        if (count > 0 && count < (int)sizeof variant) file = fopen(variant, "rb");
        if (file) { strcpy(path, variant); next_composed = 1; }
    }
    if (!file) file = fopen(path, "rb");
    if (!file) {
        active = 0; forwarded = 0; generation = next_generation; strength = next_strength;
        fallback_stage = "missing-table"; publish(fallback_stage); return;
    }
    if (active && next_generation == generation && next_strength == strength && !strcmp(active_path, path)) { fclose(file); return; }
    u16 pending[TOTAL_WORDS];
    unsigned int count = fread(pending, sizeof(u16), TOTAL_WORDS, file);
    int extra = fgetc(file); fclose(file);
    int valid = count == TOTAL_WORDS && extra == -1;
    for (unsigned int i = 0; valid && i < TOTAL_WORDS; i++)
        if (pending[i] > 1023 || (i % WORDS == WORDS-1 && pending[i])) valid = 0;
    if (!valid) {
        active = 0; forwarded = 0; generation = next_generation; strength = next_strength;
        fallback_stage = "invalid-table"; publish(fallback_stage); return;
    }
    for (unsigned int i = 0; i < TOTAL_WORDS; i++) tables[i] = pending[i];
    generation = next_generation; strength = next_strength; forwarded = 0; active = 1; composed = next_composed;
    strcpy(active_path, path);
    fprintf(stderr, "[NX LUT] selected generation=%u strength=%u file=%s bytes=4380\n", generation, strength, path);
    publish("selected");
}
void parameter_update(void *, void *) __asm__("_ZN17CIpcParameterNode6UpdateEPv");
void parameter_update(void *self, void *parameters) {
    static void *lut_vtable;
    void (*original)(void *, void *) = dlsym((void *)-1, "_ZN17CIpcParameterNode6UpdateEPv");
    if (!original) { fprintf(stderr, "[NX LUT] original parameter Update unavailable\n"); abort(); }
    if (!lut_vtable) lut_vtable = dlsym((void *)0, "_ZTV12CIpcs3DLutD4");
    if (lut_vtable && *(void **)self == (char *)lut_vtable+8) {
        while (__sync_lock_test_and_set(&table_lock, 1)) { }
        select_table(parameters && ((unsigned char *)parameters)[0x83] != 0);
        /* The firmware caches the parameter index, which does not include our
         * selected file. Reset only this node when that extra input changes. */
        int changed = updated_node != self || updated_active != active ||
            (active && (updated_generation != generation || updated_strength != strength || strcmp(updated_path, active_path)));
        updated_node = self; updated_active = active; updated_generation = generation;
        updated_strength = strength;
        strcpy(updated_path, active_path);
        __sync_lock_release(&table_lock);
        if (changed) {
            void (*reset)(void *) = dlsym((void *)-1, "_ZN17CIpcParameterNode5ResetEv");
            if (!reset) { fprintf(stderr, "[NX LUT] original parameter Reset unavailable\n"); abort(); }
            reset(self);
            fprintf(stderr, "[NX LUT] invalidated stock LUT-node cache; active=%d generation=%u\n", active, generation);
        }
    }
    original(self, parameters);
}
void ipcs_3d_lut(unsigned int channel, const u16 *source) {
    void (*original)(unsigned int, const u16 *) = dlsym((void *)-1, "ipcs_3d_lut");
    if (!original) { fprintf(stderr, "[NX LUT] original ipcs_3d_lut unavailable\n"); abort(); }
    if (channel >= PLANES) { original(channel, source); return; }
    while (__sync_lock_test_and_set(&table_lock, 1)) { }
    /* Stock SetParam emits channels 0,1,2. Keep one generation for that triplet. */
    if (channel == 0) {
        const u16 *factory_red = dlsym((void *)0, "_ZN12CIpcs3DLutD47m_sRLutE");
        select_table(factory_red && source == factory_red);
    }
    if (active && source && strength) {
        /* Blend with the actual table supplied by this stock path. For the
         * composed variant this retains the factory transform at strength 0;
         * blending against an identity grid here would erase it. */
        u16 mixed[WORDS];
        for (unsigned int i = 0; i < WORDS; i++)
            mixed[i] = (u16)(((unsigned int)source[i] * (100-strength) +
                (unsigned int)tables[channel * WORDS+i] * strength + 50) / 100);
        original(channel, mixed);
        forwarded |= 1U << channel;
        publish("forwarded-to-stock-api");
    } else {
        original(channel, source);
        forwarded |= 1U << channel;
        publish(active ? (source ? "forwarded-to-stock-api" : "missing-stock-source") :
            (forwarded == 7 ? fallback_stage : "stock-awaiting-stock-call"));
    }
    __sync_lock_release(&table_lock);
}
static int camera_process(void) {
    /* App children inherit LD_PRELOAD. Their constructors must not replace
     * the camera's ready identity when stock code execs a helper command. */
    char command[4096];
    void *file = fopen("/proc/self/cmdline", "rb");
    if (!file) return 0;
    unsigned int bytes = fread(command, 1, sizeof command-1, file);
    fclose(file); command[bytes] = 0;
    for (unsigned int offset = 0; offset < bytes;) {
        const char *name = command+offset;
        for (const char *p = name; *p; p++) if (*p == '/') name = p+1;
        if (!strcmp(name, "di-camera-app-nx300") || !strcmp(name, "di-camera-app")) return 1;
        offset += strlen(command+offset)+1;
    }
    return 0;
}
static void publish_ready(void) {
    const char *root = nx_mod_root();
    if (!root) {
        fprintf(stderr, "[NX LUT] invalid NX300M_MOD_ROOT; custom tables are disabled\n");
        return;
    }
    if (!camera_process()) return;
    void *file = fopen("/var/run/nx300m-lut-hook.tmp", "w");
    if (file) {
        int written = fprintf(file, "%d %s\n", getpid(), root);
        int closed = fclose(file);
        if (written > 0 && !closed && !rename("/var/run/nx300m-lut-hook.tmp", "/var/run/nx300m-lut-hook")) {
            file = fopen("/var/run/nx300m-lut-strength.tmp", "w");
            if (file) {
                written = fprintf(file, "%d %s 1\n", getpid(), root);
                closed = fclose(file);
                if (written > 0 && !closed) rename("/var/run/nx300m-lut-strength.tmp", "/var/run/nx300m-lut-strength");
            }
        }
    }
}
void ecore_main_loop_begin(void) {
    /* The stock launcher forks after ELF constructors. Publish the identity
     * of the process that actually enters the UI loop, as the other bridges do. */
    static int ready_pid;
    int pid = getpid();
    if (ready_pid != pid) { publish_ready(); ready_pid = pid; }
    void (*begin)(void) = dlsym((void *)-1, "ecore_main_loop_begin");
    if (begin) begin();
}
