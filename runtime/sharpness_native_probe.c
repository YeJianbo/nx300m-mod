/* Original EdgeD4 consumers + UDD setters against private synthetic RAM. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <dlfcn.h>
#include <pthread.h>
#include "sharpness_native.h"
#include "nx500_sharpness_math.h"

typedef unsigned int u32;
typedef void (*ctor_fn)(void *);
typedef u32 (*index_fn)(void *, void *);
typedef int (*setter_fn)(u32 *);
static nx_sharpness_native_fn original[2];
static ctor_fn construct[2];
static index_fn get_index[2];
static setter_fn setters[2];
static unsigned char *ram[2];
static const u32 native_factors[9] = {160,180,202,228,256,288,324,364,410};
static const u32 base_gains[10] = {22,22,22,22,21,20,19,18,17,16};
static unsigned int comparisons, failures, intercepted[2];
struct fixture {
    int path, enabled, level, check_gains, calls;
    u32 params[40], node[64], gain_a, gain_b;
};
static __thread struct fixture *active;

/* This fixture opens no devices; only the actual arithmetic/register writers
 * run. Unrelated capture DSP controls are explicitly stubbed. */
int d4_get_udd_open_count(int id) { (void)id; return 1; }
int capture_dsp(int path, u32 key, int value) __asm__("_Z11CapIfDspSet15eCAP_PATH_VALUEji");
int capture_dsp(int path, u32 key, int value) { (void)path; (void)key; (void)value; return 1; }

static int observe(int path, u32 *p) {
    struct fixture *f = active;
    int offset = path ? 4 : 3;
    u32 expected = 0;
    if (f && f->check_gains) {
        expected = path && p[0] ? f->gain_b : f->gain_a;
        if (f->path != path || p[offset] != expected || p[offset+1] != expected) {
            __sync_fetch_and_add(&failures, 1);
            fprintf(stderr, "GAIN_FAIL path=%d selector=%u got=%u/%u expected=%u\n",
                    path, path ? p[0] : 0, p[offset], p[offset+1], expected);
        }
        ++f->calls;
    }
    return setters[path](p);
}
int ipcs_edge_enhancement_param(u32 *p) { return observe(1, p); }
int ipcm_edge_enhancement_param(u32 *p) { return observe(0, p); }

u32 capture_hook(void *, u32) __asm__("_ZN11CIpcsEdgeD48SetParamEj");
u32 preview_hook(void *, u32) __asm__("_ZN11CIpcmEdgeD48SetParamEj");
static u32 wrap(void *self, u32 index, int path) {
    int failure = 0;
    struct fixture *f = active;
    __sync_fetch_and_add(&intercepted[path], 1);
    u32 result = nx_sharpness_native_run(self, index, path,
            f ? nx500_sharpness_q8[f->level] : 256, f ? f->enabled : 0,
            original[path], &failure);
    if (failure || !result) __sync_fetch_and_add(&failures, 1);
    return result;
}
u32 capture_hook(void *self, u32 index) { return wrap(self, index, 1); }
u32 preview_hook(void *self, u32 index) { return wrap(self, index, 0); }

static int tables_restored(void) {
    return !memcmp(nx_sharpness_capture_table, native_factors, 36) &&
           !memcmp(nx_sharpness_preview_table, native_factors, 36);
}
static void prepare(struct fixture *f, int path, u32 exposure, u32 mode,
                    u32 index, int zero, int flag) {
    memset(f, 0, sizeof *f);
    f->path = path;
    f->params[0] = mode;
    f->params[0x2c/4] = exposure;
    f->params[0x68/4] = index;
    f->params[0x88/4] = zero ? 51 : 0;
    ((unsigned char *)f->params)[0x7d] = flag;
    construct[path](f->node);
    f->node[2] = (u32)f->params;
}
static void expected_gains(struct fixture *f) {
    u32 factor, index = f->params[0x68/4];
    if (!f->path && f->params[0] == 3) ++index;
    if (index > 8) index = 8;
    factor = f->enabled ? nx500_sharpness_q8[f->level] : native_factors[index];
    if (f->path) {
        u32 ev = f->params[0x2c/4];
        u32 base_index = ev < 56 ? 9 : (ev-56)/16;
        if (base_index > 9) base_index = 9;
        f->gain_a = f->params[0x88/4] > 50 ? 0 : (base_gains[base_index]*factor)>>8;
        f->gain_b = f->gain_a;
        if (ev > 160) f->gain_b = ((unsigned char *)f->params)[0x7d] ? 0 : f->gain_a/2;
        else if (ev > 128) f->gain_b = f->gain_a/2;
        else if (ev > 112) f->gain_b = (2*f->gain_a)/3;
    } else {
        f->gain_a = 20*factor/256;
        if (f->params[0] == 3) {
            f->gain_a *= 2;
            if (f->gain_a > 255) f->gain_a = 255;
        }
        f->gain_b = f->gain_a;
    }
}
static int execute(struct fixture *f, int compare_ram) {
    unsigned char expected[0x4000];
    nx_sharpness_native_fn *vtable = (nx_sharpness_native_fn *)(unsigned long)f->node[0];
    u32 index = get_index[f->path](f->node, f->params);
    active = f;
    if (compare_ram) {
        memset(ram[f->path], 0xa5, 0x4000);
        f->check_gains = 0;
        if (!original[f->path](f->node, index)) return 0;
        memcpy(expected, ram[f->path], sizeof expected);
    }
    expected_gains(f);
    if (compare_ram) {
        u32 *words = (u32 *)expected;
        if (f->path) {
            words[0x10c0/4] = (words[0x10c0/4] & ~0x0007f7f0u) |
                ((f->gain_a & 127)<<4) | ((f->gain_a & 127)<<12);
            words[0x10b4/4] = (words[0x10b4/4] & ~0x7f000000u) | ((f->gain_b & 127)<<24);
            words[0x10b8/4] = (words[0x10b8/4] & ~0x7fu) | (f->gain_b & 127);
        } else {
            words[0x44/4] = (words[0x44/4] & ~0x7f7f0000u) |
                ((f->gain_a & 127)<<16) | ((f->gain_a & 127)<<24);
        }
        memset(ram[f->path], 0xa5, 0x4000);
    }
    f->check_gains = 1; f->calls = 0;
    if (!vtable[4](f->node, index)) return 0;
    if (f->calls != (f->path ? 2 : 1)) return 0;
    if (compare_ram && (memcmp(ram[f->path], expected, sizeof expected) || !tables_restored())) return 0;
    __sync_fetch_and_add(&comparisons, 1);
    return 1;
}
static void *thread_test(void *argument) {
    struct fixture f;
    int path = (int)(unsigned long)argument;
    prepare(&f, path, path ? 136 : 96, path ? 1 : 3, path ? 0 : 8, 0, 0);
    for (int i = 0; i < 600; ++i) {
        f.enabled = (i%3) != 0; f.level = (i*7+path)%21;
        if (!execute(&f, 0)) __sync_fetch_and_add(&failures, 1);
    }
    return 0;
}
/* Exercise nesting and failure paths without calling original consumers. */
static u32 leaf_original(void *unused, u32 index) {
    (void)unused;
    return nx_sharpness_capture_table[index];
}
static u32 nested_original(void *unused, u32 index) {
    int failure;
    (void)unused;
    if (nx_sharpness_native_run(0, index, 1, 460, 1, leaf_original, &failure) != 460 || failure) return 0;
    if (nx_sharpness_capture_table[index] != 140) return 0;
    if (nx_sharpness_native_run(0, index, 1, 460, 0, leaf_original, &failure) != native_factors[index] || failure) return 0;
    return nx_sharpness_capture_table[index] == 140 ? 17 : 0;
}
int main(void) {
    const char *classes[2] = {"CIpcmEdgeD4", "CIpcsEdgeD4"};
    void *iq = dlopen("libiq-fw-slpcam.so", RTLD_NOW), *udd = dlopen("libudd.so.3.1", RTLD_NOW);
    if (!iq || !udd) { fprintf(stderr, "DLOPEN %s\n", dlerror()); return 2; }
    for (int path = 0; path < 2; ++path) {
        char name[120];
        snprintf(name, sizeof name, "_ZN11%s8SetParamEj", classes[path]); original[path] = dlsym(iq, name);
        snprintf(name, sizeof name, "_ZN11%s8GetIndexEPv", classes[path]); get_index[path] = dlsym(iq, name);
        snprintf(name, sizeof name, "_ZN11%sC1Ev", classes[path]); construct[path] = dlsym(iq, name);
        setters[path] = dlsym(udd, path ? "ipcs_edge_enhancement_param" : "ipcm_edge_enhancement_param");
        void **pointer = dlsym(RTLD_DEFAULT, path ? "ipcs_reg_base" : "ipcm_reg_core_base");
        ram[path] = calloc(1, 0x4000);
        if (!pointer || !ram[path] || !original[path] || !get_index[path] || !construct[path] || !setters[path]) return 3;
        *pointer = ram[path];
        struct fixture f;
        prepare(&f, path, 96, 1, 4, 0, 0);
        nx_sharpness_native_fn *vt = (nx_sharpness_native_fn *)(unsigned long)f.node[0];
        if (vt[4] != (path ? capture_hook : preview_hook) || vt[4] == original[path]) return 4;
        printf("VTABLE path=%d hooked=1 original_distinct=1\n", path);
    }
    const u32 evs[] = {56,112,120,128,136,160,168,192,208};
    for (int path = 0; path < 2; ++path)
        for (unsigned int e = 0; e < sizeof evs/sizeof evs[0]; ++e)
            for (int mode = 1; mode <= 3; mode += 2)
                for (int flags = 0; flags < 4; ++flags) {
                    struct fixture f;
                    prepare(&f, path, evs[e], mode, e%9, flags&1, flags>>1);
                    for (int enabled = 0; enabled < 2; ++enabled)
                        for (int level = 0; level < 21; ++level) {
                            f.enabled = enabled; f.level = level;
                            if (!execute(&f, 1)) { fprintf(stderr, "RAM_FAIL p=%d ev=%u mode=%d flags=%d level=%d enabled=%d\n", path, evs[e], mode, flags, level, enabled); return 5; }
                        }
                }
    printf("SERIAL cases=%u complete_ram_equal=1 tables_restored=1\n", comparisons);
    pthread_t threads[2];
    for (int path = 0; path < 2; ++path) if (pthread_create(&threads[path], 0, thread_test, (void *)(unsigned long)path)) return 6;
    for (int path = 0; path < 2; ++path) pthread_join(threads[path], 0);
    if (!tables_restored()) return 7;
    int failure;
    if (nx_sharpness_native_run(0, 4, 1, 140, 1, nested_original, &failure) != 17 || failure || !tables_restored()) return 8;
    if (nx_sharpness_native_run(0, 4, 1, 140, 1, 0, &failure) || failure != NX_SHARPNESS_NATIVE_MISSING_ORIGINAL) return 9;
    nx_sharpness_capture_table[0] = 99;
    if (nx_sharpness_native_run(0, 4, 1, 140, 1, leaf_original, &failure) || failure != NX_SHARPNESS_NATIVE_TABLE_CHANGED) return 10;
    nx_sharpness_capture_table[0] = native_factors[0];
    printf("SHARPNESS_NATIVE cases=%u failures=%u capture_hooks=%u preview_hooks=%u threaded_calls=1200 nested_restore=1 failure_paths=1\n", comparisons, failures, intercepted[1], intercepted[0]);
    return failures ? 11 : 0;
}
