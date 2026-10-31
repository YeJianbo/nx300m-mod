#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stddef.h>
#include <dlfcn.h>
#include "drime4/udd/ipcs_type.h"
#include "drime4/udd/ipcs_submodule.h"
#include "drime4/udd/ipcm_submodule.h"
#include "chroma_pair.h"
#include "nx500_color_math.h"

static int capture, calls, fail_a, fail_b;
static int stock(nx_chroma_parameter *p) {
    return capture ? ipcs_rgb2ycc_param((struct ipcs_rgb2ycc *)p)
                   : ipcm_rgb2ycc_param((struct ipcm_rgb2ycc *)p);
}
static int injected(nx_chroma_parameter *p) {
    ++calls;
    if (calls == fail_a || calls == fail_b) return 7;
    return stock(p);
}
static int expect(int pass, const char *name) {
    printf("PAIR_CASE path=%s case=%s pass=%d\n", capture ? "ipcs" : "ipcm", name, pass);
    return pass;
}
static int check_path(unsigned char *ram, size_t size) {
    nx_chroma_pair pair = {0};
    nx_chroma_parameter chm = {0}, ycc = {0};
    unsigned char *expected = malloc(size), *stock_image = malloc(size);
    if (!expected || !stock_image) return 0;
    const int16_t base[9] = {77,150,29,-43,-85,128,128,-107,-21};
    const int16_t mix[4] = {256,0,0,256}, offsets[2] = {0,0};
    const int16_t hue[9] = {256,0,0,0,222,128,0,-128,222};
    int16_t transformed[9], coefficients[9];
    memcpy(transformed, base, sizeof base);
    nx500_chroma_mix_and_saturation(transformed, mix, offsets, 44);
    nx500_multiply_q8(coefficients, hue, transformed);
    chm.sel = 1; ycc.sel = 3;
    chm.chm_mat[0] = 150; chm.chm_mat[1] = 10;
    chm.chm_mat[2] = (uint32_t)-5; chm.chm_mat[3] = 140;
    for (int i = 0; i < 9; ++i) ycc.ycc_mat[i] = (uint32_t)(int32_t)base[i];
    memset(ram, 0xa5, size);
    if (stock(&chm) || stock(&ycc)) return 0;
    memcpy(stock_image, ram, size);
    nx_chroma_remember(&pair, &chm);
    calls = fail_a = fail_b = 0;
    if (!expect(nx_chroma_apply_complete(&pair, injected, coefficients) == -110 && calls == 0 &&
                !memcmp(ram, stock_image, size), "wait_for_both_bases")) return 0;
    nx_chroma_remember(&pair, &ycc);
    nx_chroma_parameter unit = chm, changed = ycc;
    unit.chm_mat[0] = unit.chm_mat[3] = 128;
    unit.chm_mat[1] = unit.chm_mat[2] = 0;
    for (int i = 0; i < 9; ++i) changed.ycc_mat[i] = (uint32_t)(int32_t)coefficients[i];
    if (stock(&unit) || stock(&changed)) return 0;
    memcpy(expected, ram, size);
    if (stock(&chm) || stock(&ycc)) return 0;
    if (!expect(nx_chroma_apply_complete(&pair, injected, coefficients) == 0 &&
                pair.modified && pair.state_known && !memcmp(ram, expected, size) &&
                !memcmp(&pair.chm, &chm, sizeof chm) && !memcmp(&pair.ycc, &ycc, sizeof ycc),
                "source_ycc_unit_chm")) return 0;
    if (!expect(nx_chroma_apply_complete(&pair, injected, coefficients) == 0 &&
                !memcmp(ram, expected, size), "repeat_no_accumulation")) return 0;
    int16_t invalid[9]; memcpy(invalid, coefficients, sizeof invalid); invalid[4] = 1024;
    calls = 0;
    if (!expect(nx_chroma_apply_complete(&pair, injected, invalid) == -111 && calls == 0 &&
                !memcmp(ram, expected, size), "range_reject_before_writes")) return 0;
    if (!expect(nx_chroma_restore(&pair, injected) == 0 && !pair.modified && pair.state_known &&
                !memcmp(ram, stock_image, size), "restore_both")) return 0;
    calls = 0; fail_a = 2;
    if (!expect(nx_chroma_apply_complete(&pair, injected, coefficients) == 7 &&
                pair.last_result == 7 && pair.restore_result == 0 && pair.state_known &&
                !pair.modified && !memcmp(ram, stock_image, size), "second_write_failure_restore")) return 0;
    calls = 0; fail_b = 3;
    if (!expect(nx_chroma_apply_complete(&pair, injected, coefficients) == 7 &&
                pair.restore_result == 7 && !pair.state_known, "restore_failure_reported")) return 0;
    calls = fail_a = fail_b = 0;
    if (nx_chroma_restore(&pair, injected)) return 0;
    chm.chm_mat[0] = 160; ycc.ycc_mat[0] = 78;
    nx_chroma_remember(&pair, &chm); nx_chroma_remember(&pair, &ycc);
    if (stock(&chm) || stock(&ycc)) return 0;
    memcpy(stock_image, ram, size);
    if (nx_chroma_apply_complete(&pair, injected, coefficients)) return 0;
    if (!expect(nx_chroma_restore(&pair, injected) == 0 && !memcmp(ram, stock_image, size),
                "restore_latest_bases")) return 0;
    free(expected); free(stock_image);
    return 1;
}
int main(void) {
    if (sizeof(nx_chroma_parameter) != sizeof(struct ipcs_rgb2ycc) ||
        sizeof(nx_chroma_parameter) != sizeof(struct ipcm_rgb2ycc) ||
        offsetof(nx_chroma_parameter, ycc_mat) != 40) return 2;
    unsigned char *cap = malloc(0x2000), *prev = malloc(0x1000);
    void **cap_base = dlsym(RTLD_DEFAULT, "ipcs_reg_base");
    void **prev_base = dlsym(RTLD_DEFAULT, "ipcm_reg_core_base");
    if (!cap || !prev || !cap_base || !prev_base) return 3;
    *cap_base = cap; *prev_base = prev;
    capture = 1; if (!check_path(cap, 0x2000)) return 4;
    capture = 0; if (!check_path(prev, 0x1000)) return 5;
    puts("CHROMA_PAIR_PROBE=PASS; camera_started=0; image_verified=0");
    return 0;
}
