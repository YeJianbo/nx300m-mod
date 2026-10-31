/* NX300M 1.15: share the same reviewed Wi-Fi gate between the minimal module
 * and camera_firmware_mod. Load at camera process startup only.
 * SPDX-License-Identifier: AGPL-3.0-or-later
 */
#ifndef NX300M_MANUAL_LENS_WIFI_PATCH_H
#define NX300M_MANUAL_LENS_WIFI_PATCH_H

extern void *dlsym(void *, const char *);
extern char *getenv(const char *);
extern int memcmp(const void *, const void *, unsigned int);
extern int readlink(const char *, char *, unsigned int);
extern int mprotect(void *, unsigned int, int);
extern int fprintf(void *, const char *, ...);
extern void *stderr;
extern void abort(void);
#include "manual_lens_wifi_stock.h"

enum nx_manual_lens_wifi_result {
    NX_MANUAL_LENS_WIFI_DISABLED = 0,
    NX_MANUAL_LENS_WIFI_NOT_CAMERA = 1,
    NX_MANUAL_LENS_WIFI_STOCK_MISMATCH = 2,
    NX_MANUAL_LENS_WIFI_PROTECT_FAILED = 3,
    NX_MANUAL_LENS_WIFI_INSTALLED = 4
};
static unsigned int manual_lens_wifi_result;

static int manual_lens_wifi_camera(void) {
    char executable[4096];
    int count = readlink("/proc/self/exe", executable, sizeof executable - 1);
    if (count <= 0 || count >= (int)sizeof executable - 1) return 0;
    executable[count] = 0;
    const char *name = executable;
    for (const char *p = executable; *p; ++p) if (*p == '/') name = p + 1;
    const char *expected[2] = {"di-camera-app", "di-camera-app-nx300"};
    for (unsigned int i = 0; i < 2; ++i) {
        const char *a = name, *b = expected[i];
        while (*a && *a == *b) { ++a; ++b; }
        if (!*a && !*b) return 1;
    }
    return 0;
}

static void manual_lens_wifi_flush(void *begin, void *end) {
    register unsigned int first __asm__("r0") = (unsigned int)begin;
    register unsigned int last __asm__("r1") = (unsigned int)end;
    register unsigned int flags __asm__("r2") = 0;
    register unsigned int syscall __asm__("r7") = 0x0f0002;
    __asm__ volatile("svc 0" : "+r"(first) : "r"(last), "r"(flags), "r"(syscall) : "memory");
}

static unsigned int patch_manual_lens_wifi(void) {
    const char *enabled = getenv("NX300M_MANUAL_LENS_WIFI");
    if (!enabled || enabled[0] != '1' || enabled[1]) return 0;
    /* Helpers inherit LD_PRELOAD. Check executable identity before resolving
     * the reviewed non-PIE camera entry and comparing its complete bytes. */
    if (!manual_lens_wifi_camera()) {
        manual_lens_wifi_result = NX_MANUAL_LENS_WIFI_NOT_CAMERA;
        return 0;
    }
    unsigned char *entry = dlsym((void *)0, NX_WIFI_GATE_SYMBOL);
    if (!entry || (unsigned int)entry != NX_WIFI_GATE_ADDRESS ||
        memcmp(entry, nx_wifi_gate_stock, sizeof nx_wifi_gate_stock)) {
        manual_lens_wifi_result = NX_MANUAL_LENS_WIFI_STOCK_MISMATCH;
        fprintf(stderr, "[NX LENS WIFI] Wi-Fi admission differs; retained stock\n");
        return 0;
    }
    unsigned int *branch = (unsigned int *)(entry + NX_WIFI_GATE_OFFSET);
    unsigned int *kmount_branch = (unsigned int *)(entry + NX_WIFI_KMOUNT_OFFSET);
    void *page = (void *)((unsigned int)branch & ~4095u);
    if (mprotect(page, 4096, 7)) {
        manual_lens_wifi_result = NX_MANUAL_LENS_WIFI_PROTECT_FAILED;
        return 0;
    }
    /* Both sites belong to this fully verified function and one code page.
     * cmp Lensconnection,#1: BEQ -> BLS accepts disconnected(0)/connected(1).
     * cmp Kmount,#0: BEQ -> B skips only the UI adapter admission refusal.
     * Keep Lenslock, storage/busy and actual RVF lens errors; do not change
     * global attachment, Is_Kmount_Adaper_Equipped, or detach event handling. */
    __atomic_store_n(branch, NX_WIFI_GATE_REPLACEMENT, __ATOMIC_RELAXED);
    __atomic_store_n(kmount_branch, NX_WIFI_KMOUNT_REPLACEMENT, __ATOMIC_RELAXED);
    manual_lens_wifi_flush(branch, kmount_branch + 1);
    if (mprotect(page, 4096, 5)) abort();
    manual_lens_wifi_result = NX_MANUAL_LENS_WIFI_INSTALLED;
    fprintf(stderr, "[NX LENS WIFI] connection/Kmount UI admission enabled; RVF unverified\n");
    return 1;
}

#endif
