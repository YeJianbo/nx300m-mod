/* Production color-style.so + real libudd, with isolated synthetic RAM. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <dlfcn.h>
#include <unistd.h>
#include "drime4/udd/ipcs_type.h"
#include "drime4/udd/ipcs_submodule.h"
#include "drime4/udd/ipcm_submodule.h"
extern long stock_time(long *) __asm__("time");
extern void ecore_main_loop_begin(void);
extern int color_probe_tick(void);
typedef int (*setter_fn)(void *);
static unsigned char *ram[2];
static void **base_pointer[2];
static setter_fn original[2];
static unsigned int request_id;
static int response[16];
static const unsigned int sizes[2] = {0x2000, 0x1000};
static int fixture_3d, fixture_pw;
/* Explicit mode fixture for lifecycle checks; source/target mode conversion
 * evidence is generated separately from the original capture libraries. */
void *mode_instance(void) __asm__("_ZN15CIQDataRepeater11GetInstanceEv");
int mode_parameter(void *, int, int *) __asm__("_ZN15CIQDataRepeater12GetParameterEiPi");
void *mode_instance(void) { return &fixture_3d; }
int mode_parameter(void *unused, int key, int *value) {
    (void)unused;
    *value = key == 0x41 ? 2 : key == 0x49 ? 1 : key == 0x4b ? fixture_3d : key == 0x2c ? fixture_pw : 0;
    return 1;
}
extern void nx500_original_multiply(short *, const short *, const short *);
extern void nx500_original_saturation(short *, const short *, int, const short *);

static int forward_ycc(const int matrix[9]) {
    struct ipcs_rgb2ycc param = {0};
    param.sel = IPCS_YCC_MAT;
    memcpy(param.ycc_mat, matrix, 36);
    return !ipcs_rgb2ycc_param(&param) && !ipcm_rgb2ycc_param((struct ipcm_rgb2ycc *)&param);
}

static int complete_ram(const char *phase, const int matrix[9], int styled, int gain) {
    short calculated[9], output[9];
    const short mix[4] = {256,0,0,256}, offsets[2] = {0,0};
    const short hue[9] = {256,0,0,0,235,104,0,-104,235};
    for (int i = 0; i < 9; ++i) calculated[i] = matrix[i];
    if (styled) {
        nx500_original_saturation(calculated, offsets, gain, mix);
        nx500_original_multiply(output, hue, calculated);
    } else memcpy(output, calculated, sizeof output);
    for (int path = 0; path < 2; ++path) {
        unsigned char *expected = malloc(sizes[path]);
        if (!expected) return 0;
        memset(expected, 0xa5, sizes[path]);
        struct ipcs_rgb2ycc chm = {0}, ycc = {0};
        chm.sel = IPCS_CHM_MAT; chm.chm_mat[0] = chm.chm_mat[3] = 128;
        ycc.sel = IPCS_YCC_MAT;
        for (int i = 0; i < 9; ++i) ycc.ycc_mat[i] = (unsigned int)(int)output[i];
        *base_pointer[path] = expected;
        int rc = original[path](&chm) | original[path](&ycc);
        *base_pointer[path] = ram[path];
        int same = !rc && !memcmp(expected, ram[path], sizes[path]);
        free(expected);
        printf("CHROMA_COMPLETE phase=%s path=%d original_leaf_and_setter_match=%d\n", phase, path, same);
        if (!same) return 0;
    }
    return 1;
}

static void prepare(void) {
    for (int p = 0; p < 2; ++p) memset(ram[p], 0xa5, sizes[p]);
}

static int forward(const int *capture, const int *preview) {
    struct ipcs_rgb2ycc cap = {0}, cap_copy;
    struct ipcm_rgb2ycc prev = {0}, prev_copy;
    cap.sel = IPCS_CHM_MAT; prev.sel = IPCM_CHM_MAT;
    memcpy(cap.chm_mat, capture, 16); memcpy(prev.chm_mat, preview, 16);
    cap_copy = cap; prev_copy = prev;
    int a = ipcs_rgb2ycc_param(&cap), b = ipcm_rgb2ycc_param(&prev);
    return !a && !b && !memcmp(&cap, &cap_copy, sizeof cap) &&
        !memcmp(&prev, &prev_copy, sizeof prev);
}

static int check_ram(const char *phase, int path, const int *expected_matrix) {
    unsigned char *expected = malloc(sizes[path]);
    struct ipcs_rgb2ycc param = {0};
    if (!expected) return 0;
    memset(expected, 0xa5, sizes[path]);
    param.sel = IPCS_CHM_MAT;
    memcpy(param.chm_mat, expected_matrix, 16);
    *base_pointer[path] = expected;
    int rc = original[path](&param);
    *base_pointer[path] = ram[path];
    int same = !memcmp(expected, ram[path], sizes[path]);
    printf("CHROMA_RAM phase=%s path=%s full_ram_match=%d original_rc=%d\n",
           phase, path ? "ipcm" : "ipcs", same, rc);
    free(expected);
    return !rc && same;
}

static int check_both(const char *phase, const int *capture, const int *preview) {
    return check_ram(phase, 0, capture) && check_ram(phase, 1, preview);
}

static int submit(int operation, int hue, int sat, const char *tail, int expected_rc) {
    FILE *file = fopen("/var/run/nx300m-chroma-style-request", "w");
    if (!file) return 0;
    fprintf(file, "%u %u %d %d %d%s\n", ++request_id,
            (unsigned int)stock_time(NULL) + 30u, operation, hue, sat, tail);
    fclose(file);
    if (!color_probe_tick()) return 0;
    file = fopen("/var/run/nx300m-chroma-style-response", "r");
    if (!file) return 0;
    int fields = 0;
    for (; fields < 16; ++fields)
        if (fscanf(file, "%d", response + fields) != 1) break;
    char trailing;
    int extra = fscanf(file, " %c", &trailing);
    fclose(file);
    int pass = fields == 16 && extra != 1 && response[0] == (int)request_id &&
        response[1] == expected_rc && response[15] == getpid();
    printf("CHROMA_REQUEST op=%d hue=%d sat=%d rc=%d expected_rc=%d fields=%d pass=%d\n",
           operation, hue, sat, response[1], expected_rc, fields, pass);
    return pass;
}

static int other_selectors(void) {
    for (int path = 0; path < 2; ++path) {
        for (int selector = 0; selector < 4; ++selector) {
            if (selector == 1) continue;
            struct ipcs_rgb2ycc param = {0}, copy;
            param.sel = selector;
            param.chm_sup_sub = 23; param.chm_sup_gain = 7;
            param.chm_sup_th_dark = 31; param.chm_sup_gain_dark = 41;
            param.chm_sup_gain_dark_sat = 59;
            const int ycc[9] = {77,150,29,-43,-85,128,128,-107,-21};
            memcpy(param.ycc_mat, ycc, sizeof ycc);
            copy = param;
            unsigned char *expected = malloc(sizes[path]);
            if (!expected) return 0;
            memset(expected, 0xa5, sizes[path]);
            memset(ram[path], 0xa5, sizes[path]);
            *base_pointer[path] = expected;
            int expected_rc = original[path](&param);
            *base_pointer[path] = ram[path];
            int rc = path ? ipcm_rgb2ycc_param((struct ipcm_rgb2ycc *)&param) : ipcs_rgb2ycc_param(&param);
            int same = rc == expected_rc && !memcmp(ram[path], expected, sizes[path]) &&
                !memcmp(&copy, &param, sizeof param);
            free(expected);
            printf("CHROMA_SELECTOR path=%d selector=%d original_match=%d\n", path, selector, same);
            if (!same) return 0;
        }
    }
    return 1;
}

int main(void) {
    const int identity[4] = {128,0,0,128};
    const int hue0_identity[4] = {111,64,-64,111};
    const int hue2_identity[4] = {118,52,-52,118};
    const int hue18_identity[4] = {117,-52,52,117};
    const int cap[4] = {129,-1,3,127}, prev[4] = {140,-21,31,111};
    const int cap0[4] = {113,63,-62,111}, prev0[4] = {137,37,-43,107};
    const int large[4] = {1023,1023,1023,-1024};
    void *library = dlopen("libudd.so.3.1", RTLD_NOW);
    if (!library) return 2;
    base_pointer[0] = dlsym(RTLD_DEFAULT, "ipcs_reg_base");
    base_pointer[1] = dlsym(RTLD_DEFAULT, "ipcm_reg_core_base");
    original[0] = (setter_fn)dlsym(library, "ipcs_rgb2ycc_param");
    original[1] = (setter_fn)dlsym(library, "ipcm_rgb2ycc_param");
    for (int p = 0; p < 2; ++p) {
        ram[p] = malloc(sizes[p]);
        if (!ram[p] || !base_pointer[p] || !original[p]) return 3;
        *base_pointer[p] = ram[p];
    }
    ecore_main_loop_begin();
    FILE *file = fopen("/var/run/nx300m-chroma-style-ready", "r");
    int pid = 0;
    if (!file) return 4;
    int fields = fscanf(file, "%d", &pid);
    fclose(file);
    if (fields != 1 || pid != getpid()) return 5;
    prepare();
    if (!forward(identity, identity) || !check_both("neutral", identity, identity)) return 10;
    prepare();
    if (!submit(1,0,10,"",0) || response[2] != 0 || response[3] != 10 ||
        response[4] != 1 || !check_both("hue0_cached", hue0_identity, hue0_identity)) return 11;
    prepare();
    if (!forward(identity,identity) || !check_both("repeat_no_accumulation", hue0_identity,hue0_identity)) return 12;
    prepare();
    if (!forward(cap,prev) || !check_both("fresh_stock_base_signed_rounding",cap0,prev0)) return 13;
    prepare();
    if (!submit(0,10,10,"",0) || response[4] || !check_both("reset_latest_base",cap,prev)) return 14;
    prepare();
    if (!forward(identity,identity) || !submit(1,2,10,"",0) ||
        !check_both("source_table_index2",hue2_identity,hue2_identity)) return 15;
    prepare();
    if (!submit(1,18,10,"",0) || !check_both("source_table_index18",hue18_identity,hue18_identity)) return 16;
    if (!submit(1,0,9,"",-110) || response[2] != 18 || response[3] != 10) return 17;
    if (!submit(1,21,10,"",-102) || response[2] != 18) return 18;
    if (!submit(1,0,10," extra",-100) || response[2] != 18) return 19;
    prepare();
    if (!submit(1,0,10,"",0) || !forward(large,large) ||
        !check_both("whole_matrix_overflow_fallback",large,large) ||
        !submit(2,10,10,"",0) || response[7] < 1 || response[8] < 1 ||
        response[13] || response[14]) return 20;
    if (!other_selectors()) return 21;
    /* Invalid stock inputs still report the genuine setter failure. */
    struct ipcs_rgb2ycc invalid = {0};
    invalid.sel = IPCS_CHM_MAT; invalid.chm_mat[0] = 1024;
    if (ipcs_rgb2ycc_param(&invalid) != 2 || !submit(2,10,10,"",0) || response[9] != 2) return 22;
    prepare();
    if (!forward(cap,prev) || !submit(0,10,10,"",0) || !check_both("final_restore",cap,prev)) return 23;
    const int ycc[9] = {77,150,29,-47,-91,138,138,-115,-23};
    const int fresh[9] = {77,150,29,-43,-85,128,128,-107,-21};
    prepare();
    if (!forward(identity,identity) || !forward_ycc(ycc) || !submit(1,2,14,"",0) ||
        !complete_ram("complete_source_operator", ycc, 1, 44)) return 24;
    prepare();
    if (!forward_ycc(fresh) || !complete_ram("new_ycc_no_accumulation", fresh, 1, 44)) return 25;
    fixture_3d = 1;
    prepare();
    if (!forward_ycc(fresh) || !complete_ram("mode_change_restores_both", fresh, 0, 32) ||
        !submit(2,10,10,"",0) || response[9] != -112 || response[10] != -112) return 26;
    if (!submit(0,10,10,"",0)) return 27;
    fixture_3d = 0;
    prepare();
    if (!forward_ycc(fresh) || !submit(1,2,14,"",0) || !submit(0,10,10,"",0) ||
        !complete_ram("complete_reset", fresh, 0, 32)) return 28;
    fixture_pw = 16;
    prepare();
    if (!forward(identity,identity) || !forward_ycc(fresh) || !submit(1,2,14,"",0) ||
        !complete_ram("custom1_source_operator", fresh, 1, 44)) return 29;
    prepare();
    if (!submit(1,2,10,"",0) || !complete_ram("neutral_sat_keeps_source_order", fresh, 1, 32) ||
        !submit(0,10,10,"",0)) return 30;
    puts("CHROMA_BRIDGE_PROBE=PASS; bridge=production-color-style.so; setters=stock-libudd; backend=synthetic-RAM; mode_fixture=standard-P; camera_started=0; image_verified=0");
    return 0;
}
