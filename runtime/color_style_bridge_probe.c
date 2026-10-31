/* Load the production bridge, run its request callback, and let unmodified
 * NX300M 1.15 libudd pack both matrices into separately allocated register RAM.
 * Ecore scheduling is the sole substituted service; no camera is launched.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <dlfcn.h>
#include <unistd.h>
/* The stock libc predates the toolchain's 64-bit time ABI. */
extern long stock_time(long *) __asm__("time");

typedef struct { unsigned int value[9]; } matrix;
extern int ipcs_color_correction_param(matrix *);
extern int ipcm_color_correction_param(matrix *);
extern void ecore_main_loop_begin(void);
extern int color_probe_tick(void);

static unsigned char *capture_ram, *preview_ram;
static unsigned int request_id;

static int check_ram(const char *phase, const char *path, unsigned char *ram,
                     unsigned int size, unsigned int offset, const int *values) {
    unsigned char *expected = calloc(1, size);
    if (!expected) return 0;
    for (int index = 0; index < 9; ++index) {
        unsigned int *word = (unsigned int *)(expected + offset + (index / 2) * 4);
        *word |= ((unsigned int)values[index] & 0x7ffu) << ((index % 2) * 16);
    }
    int match = !memcmp(ram, expected, size);
    printf("BRIDGE_RAM phase=%s path=%s full_ram_match=%d words=", phase, path, match);
    for (int index = 0; index < 5; ++index)
        printf("%s%08x", index ? "," : "", *(unsigned int *)(ram + offset + index * 4));
    puts("");
    free(expected);
    return match;
}

static int check_both(const char *phase, const int *capture, const int *preview) {
    return check_ram(phase, "ipcs", capture_ram, 0x2000, 0x1078, capture) &&
           check_ram(phase, "ipcm", preview_ram, 0x1000, 0x4c, preview);
}

static int submit(int operation, unsigned int red, unsigned int green, unsigned int blue) {
    FILE *file = fopen("/var/run/nx300m-color-style-request", "w");
    if (!file) return 0;
    fprintf(file, "%u %u %d %u %u %u\n", ++request_id,
            (unsigned int)stock_time(NULL) + 30u, operation, red, green, blue);
    fclose(file);
    if (!color_probe_tick()) return 0;
    file = fopen("/var/run/nx300m-color-style-response", "r");
    if (!file) return 0;
    int response[17];
    int fields = 0;
    for (; fields < 17; ++fields)
        if (fscanf(file, "%d", response + fields) != 1) break;
    fclose(file);
    if (fields != 17 || response[0] != (int)request_id || response[1] ||
        response[10] || response[11] || response[16] != getpid()) return 0;
    return 1;
}

static int forward(const int *capture, const int *preview) {
    matrix cap, prev;
    memcpy(&cap, capture, sizeof cap);
    memcpy(&prev, preview, sizeof prev);
    if (ipcs_color_correction_param(&cap) || ipcm_color_correction_param(&prev)) return 0;
    return !memcmp(&cap, capture, sizeof cap) && !memcmp(&prev, preview, sizeof prev);
}

int main(void) {
    const int base[9] = {256, -101, 3, 63, 255, -65, -3, 33, 257};
    const int preview[9] = {255, -33, 9, 11, 257, -61, -7, 45, 253};
    const int styled[9] = {384, -152, 5, 63, 255, -65, -2, 17, 129};
    const int preview_styled[9] = {383, -50, 14, 11, 257, -61, -4, 23, 127};
    const int new_base[9] = {300, -11, 25, 77, 201, -89, -31, 23, 271};
    const int new_preview[9] = {220, -71, 21, 53, 231, -41, -21, 61, 251};
    const int new_styled[9] = {450, -17, 38, 77, 201, -89, -16, 12, 136};
    const int new_preview_styled[9] = {330, -107, 32, 53, 231, -41, -11, 31, 126};
    const int high_base[9] = {700, -11, 25, 77, 201, -89, -31, 23, 271};
    const int low_preview[9] = {-700, -71, 21, 53, 231, -41, -21, 61, 251};
    capture_ram = calloc(1, 0x2000);
    preview_ram = calloc(1, 0x1000);
    void **capture_pointer = dlsym(RTLD_DEFAULT, "ipcs_reg_base");
    void **preview_pointer = dlsym(RTLD_DEFAULT, "ipcm_reg_core_base");
    if (!capture_ram || !preview_ram || !capture_pointer || !preview_pointer) return 2;
    *capture_pointer = capture_ram;
    *preview_pointer = preview_ram;
    ecore_main_loop_begin();
    FILE *file = fopen("/var/run/nx300m-color-style-ready", "r");
    int ready_pid = 0;
    if (!file) return 3;
    int ready_fields = fscanf(file, "%d", &ready_pid);
    fclose(file);
    if (ready_fields != 1 || ready_pid != getpid()) return 4;
    if (!forward(base, preview) || !check_both("neutral", base, preview)) return 10;
    if (!submit(1, 150, 100, 50) || !check_both("gain_cached", styled, preview_styled)) return 11;
    if (!forward(base, preview) || !check_both("repeat_no_accumulation", styled, preview_styled)) return 12;
    if (!forward(new_base, new_preview) || !check_both("fresh_base", new_styled, new_preview_styled)) return 13;
    if (!submit(1, 200, 100, 50)) return 14;
    if (!forward(high_base, low_preview) || !check_both("range_whole_matrix_passthrough", high_base, low_preview)) return 15;
    if (!forward(new_base, new_preview) || !submit(0, 100, 100, 100) ||
        !check_both("reset_latest_base", new_base, new_preview)) return 16;
    if (!forward(base, preview) || !check_both("neutral_after_reset", base, preview)) return 17;
    puts("BRIDGE_PROBE=PASS; bridge=production-color-style.so; setters=stock-libudd; backend=synthetic-register-RAM; ready=request-timer; image_verified=0");
    /* Keep the RAM alive until the production bridge's exit restoration. */
    return 0;
}
