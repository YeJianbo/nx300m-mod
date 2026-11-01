/* ARM fixture: real DisplayAdjust Execute and setters; logging is a test double. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <unistd.h>
#include <dlfcn.h>
#include <pthread.h>

void *fixture_common(void) __asm__("_ZN15CLiveviewCommon11GetInstanceEv");
void *fixture_common(void) { static unsigned common[64]; return common; }
void fixture_print(void *, int, const char *, ...) __asm__("_ZN15CLiveviewCommon13LiveviewPrintEiPKcz");
void fixture_print(void *self, int level, const char *text, ...) { (void)self; (void)level; (void)text; }

static volatile unsigned running = 1, heartbeat;
static void *worker(void *unused) {
    (void)unused;
    while (running) { ++heartbeat; usleep(10000); }
    return NULL;
}
int main(void) {
    void *lib = dlopen("libcapture-fw-slpcam-nx300.so", RTLD_LAZY | RTLD_GLOBAL);
    if (!lib) { fprintf(stderr, "%s\n", dlerror()); return 2; }
    unsigned *vtable = dlsym(lib, "_ZTV26CLiveviewNodeDisplayAdjust");
    if (!vtable) return 3;
    unsigned *slot = vtable + 7;
    /* Force a real RELRO-like read-only page to exercise ptrace COW. */
    extern int mprotect(void *, unsigned, int);
    if (mprotect((void *)((uintptr_t)slot & ~4095U), 4096, 1)) return 4;
    unsigned self[64] = {0}, image[64], payload[1024];
    self[0x38/4] = 640; self[0x3c/4] = 480;
    self[0x40/4] = 8; self[0x44/4] = 12;
    self[0x48/4] = 4; self[0x4c/4] = 3;
    pthread_t thread;
    if (pthread_create(&thread, NULL, worker, NULL)) return 5;
    FILE *ready = fopen("/tmp/preview-fixture.ready", "w");
    if (!ready) return 6;
    fprintf(ready, "%u %u\n", (unsigned)getpid(), (unsigned)(uintptr_t)slot); fclose(ready);
    unsigned last = 0;
    while (running) {
        unsigned seq = 0, callback = 0;
        FILE *request = fopen("/tmp/preview-request", "r");
        if (!request) { usleep(10000); continue; }
        int parsed = fscanf(request, "%u %u", &seq, &callback); fclose(request);
        if (parsed != 2 || seq <= last) { usleep(10000); continue; }
        last = seq;
        if (!callback) break;
        for (unsigned i = 0; i < 64; ++i) image[i] = 0x00010000 + i;
        image[0] = 800; image[1] = 600;
        image[0x20/4] = 7; image[0x24/4] = 1; image[0x28/4] = 0;
        image[0x58/4] = 7;
        image[0x5c/4] = (unsigned)(uintptr_t)payload;
        for (unsigned i = 0; i < 1024; ++i) payload[i] = i * 2654435761U;
        int (*execute)(void *, void *, void *, unsigned) = (void *)(uintptr_t)*slot;
        int result = execute(self, image, payload, callback);
        if (result != 1 || image[2] != 640 || image[3] != 480 || image[4] != 8 || image[5] != 12) return 7;
        char path[128]; snprintf(path, sizeof path, "/tmp/preview-frame-%u.bin", seq);
        FILE *out = fopen(path, "wb");
        if (!out || fwrite(image, 1, sizeof image, out) != sizeof image ||
            fwrite(payload, 1, sizeof payload, out) != sizeof payload || fclose(out)) return 8;
        FILE *response = fopen("/tmp/preview-response.tmp", "w");
        if (!response) return 9;
        fprintf(response, "%u %u %u\n", seq, (unsigned)result, heartbeat);
        if (fclose(response) || rename("/tmp/preview-response.tmp", "/tmp/preview-response")) return 10;
    }
    running = 0; pthread_join(thread, NULL);
    /* Keep the library resident until process exit; no hardware was opened. */
    return 0;
}
