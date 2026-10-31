/* NX300M 1.15: select the existing no-lens 2D CS path at stock PP boundaries.
 * Rebuild through stock Init on the existing PP thread, never from a timer.
 * Load only at process startup; never dlopen into an already running camera.
 * SPDX-License-Identifier: AGPL-3.0-or-later
 */
extern void *dlsym(void *, const char *);
extern char *getenv(const char *);
extern int memcmp(const void *, const void *, unsigned int);
extern void *memcpy(void *, const void *, unsigned int);
extern void *mmap(void *, unsigned int, int, int, int, int);
extern int mprotect(void *, unsigned int, int);
extern int munmap(void *, unsigned int);
extern int fprintf(void *, const char *, ...);
extern void *stderr;
extern void abort(void);
#include "manual_lens_cs_stock.h"

static unsigned int (*stock_module_ready)(void *);
static void *volatile *stock_operation;
static unsigned int (*stock_capture_gain)(void);
static void (*stock_init)(void *);
static unsigned int gates_installed, checked[2], suppressed[2];
static unsigned int repair_lock, default_owner, rebuilt, deferred;
static unsigned int repair_owner;
static int init_result;
struct pp_key { unsigned int source, settings[11], capture_gain; };
static struct pp_key default_key;

static int electronic_ready(void) {
    void *operation = *stock_operation;
    /* Never lazy-create a lens service from an image-processing callback. */
    return operation && stock_module_ready(operation) == 0x20000019u;
}

static void note_init_result(int result, void *pp) {
    if (__atomic_load_n(&repair_owner, __ATOMIC_RELAXED) == (unsigned int)pp)
        init_result = result;
}

static void rebuild_default(unsigned char *pp) {
    if (!pp || !stock_init) return;
    /* -1 and 4 use the original CS-off path, with no LUT-upload result.
     * Repeated Init there would never produce a cacheable submission. */
    if (*(const volatile unsigned int *)(pp + 0x2c) > 3u) {
        __atomic_store_n(&default_owner, 0u, __ATOMIC_RELAXED);
        return;
    }
    unsigned int expected = 0;
    if (!__atomic_compare_exchange_n(&repair_lock, &expected, 1u, 0,
                                     __ATOMIC_ACQUIRE, __ATOMIC_RELAXED)) return;
    struct pp_key key;
    memcpy(&key.source, pp, 4);
    memcpy(key.settings, pp + 0x2c, sizeof key.settings);
    key.capture_gain = key.settings[0] == 0 ? stock_capture_gain() : 0;
    unsigned int flags = *(const volatile unsigned int *)(pp + 0x18);
    if (__atomic_load_n(&default_owner, __ATOMIC_RELAXED) == (unsigned int)pp &&
        !memcmp(&key, &default_key, sizeof key) && (flags & 5u) == 5u) goto done;
    /* Do not replace backing while DMA is pending or add a blocking wait.
     * A pending operation may persist: deferred is not a successful repair. */
    if (flags & 2u) {
        __atomic_store_n(&default_owner, 0u, __ATOMIC_RELAXED);
        __atomic_add_fetch(&deferred, 1u, __ATOMIC_RELAXED);
        goto done;
    }
    __atomic_store_n(&default_owner, 0u, __ATOMIC_RELAXED);
    init_result = -1;
    __atomic_store_n(&repair_owner, (unsigned int)pp, __ATOMIC_RELAXED);
    stock_init(pp);
    __atomic_store_n(&repair_owner, 0u, __ATOMIC_RELAXED);
    /* Init calls our phase-0 gate, selects its own default gain/table and
     * submits it via stock code. Old flags can survive a failed upload, so
     * require this invocation's common_lut_init return value, not old bit4. */
    if (init_result == 0) {
        default_key = key;
        __atomic_store_n(&default_owner, (unsigned int)pp, __ATOMIC_RELAXED);
        __atomic_add_fetch(&rebuilt, 1u, __ATOMIC_RELAXED);
    }
done:
    __atomic_store_n(&repair_lock, 0u, __ATOMIC_RELEASE);
}

/* Only this invocation's ready value changes. Shared lens objects and their
 * ownership stay with the firmware, including real Ready callbacks.
 */
static unsigned int effective_ready(const unsigned char *lens, unsigned int phase,
                                    unsigned char *pp) {
    unsigned int ready = *(const volatile unsigned char *)(lens + 8);
    unsigned int profile = *(const volatile unsigned int *)(lens + 0x20);
    __atomic_add_fetch(&checked[phase], 1u, __ATOMIC_RELAXED);
    int electronic = electronic_ready();
    if (phase == 0 || (electronic == 1 && ready && profile))
        __atomic_store_n(&default_owner, 0u, __ATOMIC_RELAXED);
    if (electronic == 1 && ready && profile) return ready;
    __atomic_add_fetch(&suppressed[phase], 1u, __ATOMIC_RELAXED);
    if (phase == 1) {
        /* A mount-switch closure alone does not establish an electronic lens.
         * Init has its own 2D default; a not-ready Update used to just return,
         * leaving any previously uploaded profile in place. */
        rebuild_default(pp);
    }
    return 0;
}

/* The incoming SP at both stock sites is 8-byte aligned. Save every integer
 * register and LR, replace only r3, replay CMP, and return to the original BEQ.
 * No PC-relative original instructions are moved into this bridge.
 */
static void fill_gate(unsigned int *code, unsigned int lens_register,
                      unsigned int phase, unsigned int resume) {
    code[0] = 0xe92d5fffu;                      /* push {r0-r12,lr} */
    code[1] = 0xe1a00000u | lens_register;      /* mov r0,r6/r9 */
    code[2] = 0xe3a01000u | phase;              /* mov r1,#phase */
    code[3] = phase ? 0xe1a02004u : 0xe3a02000u; /* mov r2,r4 / #0 */
    code[4] = 0xe59fc014u;                      /* ldr ip,[pc,#20] */
    code[5] = 0xe12fff3cu;                      /* blx ip */
    code[6] = 0xe58d000cu;                      /* str r0,[sp,#12] */
    code[7] = 0xe8bd5fffu;                      /* pop {r0-r12,lr} */
    code[8] = 0xe3530000u;                      /* cmp r3,#0 */
    code[9] = 0xe51ff004u;                      /* ldr pc,[pc,#-4] */
    code[10] = resume;
    code[11] = (unsigned int)effective_ready;
}

static void fill_result_gate(unsigned int *code, unsigned int resume) {
    code[0] = 0xe92d5fffu; /* push {r0-r12,lr} */
    code[1] = 0xe59d1058u; /* ldr r1,[sp,#56+0x20]: original this */
    code[2] = 0xe59fc014u; /* ldr ip,[pc,#20] */
    code[3] = 0xe12fff3cu; /* blx ip */
    code[4] = 0xe8bd5fffu; /* pop {r0-r12,lr} */
    code[5] = 0xe1a05000u; /* replay mov r5,r0 */
    code[6] = 0xe28d007cu; /* replay add r0,sp,#0x7c */
    code[7] = 0xe51ff004u; /* ldr pc,[pc,#-4] */
    code[8] = resume;
    code[9] = (unsigned int)note_init_result;
}

static void flush_code(void *begin, void *end) {
    register unsigned int first __asm__("r0") = (unsigned int)begin;
    register unsigned int last __asm__("r1") = (unsigned int)end;
    register unsigned int flags __asm__("r2") = 0;
    register unsigned int syscall __asm__("r7") = 0x0f0002;
    __asm__ volatile("svc 0" : "+r"(first) : "r"(last), "r"(flags), "r"(syscall) : "memory");
}

/* Read-only counters for an in-process diagnostic/probe. No background poll,
 * SD access, or HTTP call is added to the image-processing thread.
 */
void nx_manual_lens_cs_status(unsigned int values[5]) {
    values[0] = __atomic_load_n(&gates_installed, __ATOMIC_RELAXED);
    values[1] = __atomic_load_n(&checked[0], __ATOMIC_RELAXED);
    values[2] = __atomic_load_n(&suppressed[0], __ATOMIC_RELAXED);
    values[3] = __atomic_load_n(&checked[1], __ATOMIC_RELAXED);
    values[4] = __atomic_load_n(&suppressed[1], __ATOMIC_RELAXED);
}

void nx_manual_lens_cs_rebuild_status(unsigned int values[2]) {
    values[0] = __atomic_load_n(&rebuilt, __ATOMIC_RELAXED);
    values[1] = __atomic_load_n(&deferred, __ATOMIC_RELAXED);
}

#ifndef NX_MANUAL_LENS_CS_ABI_PROBE
__attribute__((constructor)) static void install(void) {
    const char *enabled = getenv("NX300M_MANUAL_LENS_CS");
    if (!enabled || enabled[0] != '1' || enabled[1]) return;
    /* Helpers can inherit LD_PRELOAD; require the reviewed camera executable. */
    if ((unsigned int)dlsym((void *)0, "_Z31UI_Check_WiFi_ModeChange_Enablei") != 0x1cc870u) return;
    unsigned char *init = dlsym((void *)0, CS_INIT_SYMBOL);
    unsigned char *update = dlsym((void *)0, CS_UPDATE_SYMBOL);
    void *module_ready = dlsym((void *)0, CS_READY_SYMBOL);
    void *operation = dlsym((void *)0, CS_OPERATION_SYMBOL);
    void *capture_gain = dlsym((void *)0, CS_GAIN_SYMBOL);
    if (!init || !update || !module_ready || !operation || !capture_gain ||
        (unsigned int)update - (unsigned int)init != CS_UPDATE_ADDRESS - CS_INIT_ADDRESS ||
        (unsigned int)operation - (unsigned int)module_ready != CS_OPERATION_ADDRESS - CS_READY_ADDRESS ||
        (unsigned int)capture_gain - (unsigned int)init != CS_GAIN_ADDRESS - CS_INIT_ADDRESS ||
        memcmp(init, cs_init_stock, sizeof cs_init_stock) ||
        memcmp(update, cs_update_stock, sizeof cs_update_stock) ||
        memcmp(module_ready, cs_ready_stock, sizeof cs_ready_stock) ||
        memcmp((unsigned char *)module_ready + CS_STATE_TABLE_ADDRESS - CS_READY_ADDRESS,
               cs_state_table_stock, sizeof cs_state_table_stock) ||
        memcmp(capture_gain, cs_gain_stock, sizeof cs_gain_stock)) {
        fprintf(stderr, "[NX LENS CS] stock bytes differ; retained stock\n");
        return;
    }
    unsigned int *bridge = mmap((void *)0, 4096, 3, 0x22, -1, 0);
    if (bridge == (void *)-1) return;
    unsigned char *sites[3] = {init + CS_INIT_OFFSET, update + CS_UPDATE_OFFSET,
                             init + CS_RESULT_OFFSET};
    void *pages[2] = {(void *)((unsigned int)sites[0] & ~4095u),
                      (void *)((unsigned int)sites[1] & ~4095u)};
    stock_module_ready = module_ready;
    stock_operation = operation;
    stock_capture_gain = capture_gain;
    stock_init = (void (*)(void *))init;
    fill_gate(bridge, 6, 0, (unsigned int)(sites[0] + 8));
    fill_gate(bridge + 16, 9, 1, (unsigned int)(sites[1] + 8));
    fill_result_gate(bridge + 32, (unsigned int)(sites[2] + 8));
    flush_code(bridge, bridge + 42);
    if (mprotect(bridge, 4096, 5)) { munmap(bridge, 4096); return; }
    if (mprotect(pages[0], 4096, 7)) { munmap(bridge, 4096); return; }
    if (mprotect(pages[1], 4096, 7)) {
        if (mprotect(pages[0], 4096, 5)) abort();
        munmap(bridge, 4096); return;
    }
    for (unsigned int i = 0; i < 3; i++) {
        unsigned int jump[2] = {0xe51ff004u, (unsigned int)(bridge + 16 * i)};
        memcpy(sites[i], jump, sizeof jump);
        flush_code(sites[i], sites[i] + 8);
    }
    if (mprotect(pages[0], 4096, 5) || mprotect(pages[1], 4096, 5)) abort();
    __atomic_store_n(&gates_installed, 7u, __ATOMIC_RELAXED);
    fprintf(stderr, "[NX LENS CS] electronic-lens 2D guards and default rebuild installed; physical color unverified\n");
}
#endif
