/* Execute only genuine stock libudd setters against separately allocated RAM. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stddef.h>
#include <dlfcn.h>
#include "drime4/udd/ipcs_type.h"
#include "drime4/udd/ipcs_submodule.h"
#include "drime4/udd/ipcm_submodule.h"

static unsigned int packed(int value) { return (unsigned int)value & 0x7ffu; }

static int one_case(const char *path, unsigned char *ram, unsigned int size,
                    const char *name, const int *coefficients, int accepted) {
    unsigned char *expected = malloc(size);
    struct ipcs_rgb2ycc capture;
    struct ipcm_rgb2ycc preview;
    int is_capture = !strcmp(path, "ipcs");
    if (!expected) return 0;
    memset(ram, 0xa5, size);
    memcpy(expected, ram, size);
    memset(&capture, 0, sizeof capture);
    memset(&preview, 0, sizeof preview);
    capture.sel = IPCS_CHM_MAT;
    preview.sel = IPCM_CHM_MAT;
    for (int i = 0; i < 4; ++i) {
        capture.chm_mat[i] = (unsigned int)coefficients[i];
        preview.chm_mat[i] = (unsigned int)coefficients[i];
    }
    struct ipcs_rgb2ycc original_capture = capture;
    struct ipcm_rgb2ycc original_preview = preview;
    if (accepted) {
        if (is_capture) {
            *(unsigned int *)(expected + 0x1090) = packed(coefficients[0]) |
                (packed(coefficients[1]) << 16);
            *(unsigned int *)(expected + 0x1094) = packed(coefficients[2]) |
                (packed(coefficients[3]) << 16);
        } else {
            unsigned int *word = (unsigned int *)(expected + 0x60);
            *word = (*word & ~0x07ff0000u) | (packed(coefficients[0]) << 16);
            *(unsigned int *)(expected + 0x64) = packed(coefficients[1]) |
                (packed(coefficients[2]) << 16);
            word = (unsigned int *)(expected + 0x68);
            *word = (*word & ~0x7ffu) | packed(coefficients[3]);
        }
    }
    int result = is_capture ? ipcs_rgb2ycc_param(&capture) : ipcm_rgb2ycc_param(&preview);
    int input_same = !memcmp(&capture, &original_capture, sizeof capture) &&
        !memcmp(&preview, &original_preview, sizeof preview);
    int ram_same = !memcmp(ram, expected, size);
    int pass = result == (accepted ? 0 : 2) && input_same && ram_same;
    unsigned int offset = is_capture ? 0x1090 : 0x60;
    printf("YCC_CASE path=%s case=%s rc=%d expected_rc=%d full_ram_match=%d input_unchanged=%d coefficients=%d,%d,%d,%d words=%08x,%08x,%08x pass=%d\n",
        path, name, result, accepted ? 0 : 2, ram_same, input_same,
        coefficients[0], coefficients[1], coefficients[2], coefficients[3],
        *(unsigned int *)(ram + offset), *(unsigned int *)(ram + offset + 4),
        *(unsigned int *)(ram + offset + 8), pass);
    free(expected);
    return pass;
}

int main(void) {
    if (sizeof(struct ipcs_rgb2ycc) != 76 || sizeof(struct ipcm_rgb2ycc) != 76 ||
        offsetof(struct ipcs_rgb2ycc, chm_mat) != 24 ||
        offsetof(struct ipcm_rgb2ycc, chm_mat) != 24 ||
        offsetof(struct ipcs_rgb2ycc, ycc_mat) != 40 ||
        offsetof(struct ipcm_rgb2ycc, ycc_mat) != 40) return 2;
    unsigned char *capture_ram = malloc(0x2000), *preview_ram = malloc(0x1000);
    void **capture_pointer = dlsym(RTLD_DEFAULT, "ipcs_reg_base");
    void **preview_pointer = dlsym(RTLD_DEFAULT, "ipcm_reg_core_base");
    if (!capture_ram || !preview_ram || !capture_pointer || !preview_pointer) return 3;
    *capture_pointer = capture_ram;
    *preview_pointer = preview_ram;
    const int cases[5][4] = {
        {128, 0, 0, 128}, {111, -64, 64, 111},
        {-1024, 1023, -1024, 1023}, {1024, 0, 0, 128}, {-1025, 0, 0, 128}
    };
    const char *names[5] = {"neutral_q7", "negative_cross_term", "signed11_limits",
                             "reject_positive_overflow", "reject_negative_overflow"};
    for (int i = 0; i < 5; ++i) {
        if (!one_case("ipcs", capture_ram, 0x2000, names[i], cases[i], i < 3)) return 10 + i;
        if (!one_case("ipcm", preview_ram, 0x1000, names[i], cases[i], i < 3)) return 20 + i;
    }
    puts("YCC_ABI_PROBE=PASS; setters=stock-libudd; backend=synthetic-RAM; cases=10; camera_started=0; image_verified=0");
    return 0;
}
