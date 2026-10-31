/* Run original Gamma node code and original UDD setters on explicit parameter
 * and register-RAM fixtures. These are not an active capture or sensor image.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <dlfcn.h>
#include <unistd.h>

extern void gamma_construct(void *) __asm__("_ZN12CIpcsGammaD4C1Ev");
extern void gamma_destroy(void *) __asm__("_ZN12CIpcsGammaD4D1Ev");
extern void node_update(void *, void *) __asm__("_ZN17CIpcParameterNode6UpdateEPv");
extern void node_reset(void *) __asm__("_ZN17CIpcParameterNode5ResetEv");
extern void info_construct(void *) __asm__("_ZN12CIQParamInfoC1Ev");
typedef struct { unsigned int col_mat[9]; } matrix;
extern int ipcs_color_correction_param(matrix *);
typedef struct { unsigned int sel, controls[5], chroma[4], ycc[9]; } rgb2ycc;
extern int ipcs_rgb2ycc_param(rgb2ycc *);

static int copy_snapshot(const char *directory, const char *kind, unsigned int slot,
                         const char *destination, const char *label) {
    char source[256], target[512];
    snprintf(source, sizeof source, "%s/%s-%u.json", directory, kind, slot);
    snprintf(target, sizeof target, "%s/%s.json", destination, label);
    FILE *input = fopen(source, "rb"), *output = fopen(target, "wb");
    if (!input || !output) return 0;
    char buffer[4096];
    unsigned int count;
    while ((count = fread(buffer, 1, sizeof buffer, input)))
        if (fwrite(buffer, 1, count, output) != count) return 0;
    int valid = !ferror(input) && !ferror(output);
    fclose(input);
    if (fclose(output)) valid = 0;
    return valid;
}

int main(int argc, char **argv) {
    if (argc != 2) return 2;
    unsigned char *registers = calloc(1, 0x10000);
    unsigned short *gamma_table = calloc(8192, 2);
    unsigned short previous[8192];
    unsigned int gamma_node[48] = {0}, parameters[39] = {0};
    unsigned int info[32] = {0};
    void *manager[3] = {gamma_table, NULL, NULL};
    void **reg_base = dlsym(RTLD_DEFAULT, "ipcs_reg_base");
    void **manager_instance = dlsym(RTLD_DEFAULT, "_ZN13CIpcSingletonI17CIpcMemoryManagerE11m_pInstanceE");
    void **info_instance = dlsym(RTLD_DEFAULT, "_ZN13CIpcSingletonI12CIQParamInfoE11m_pInstanceE");
    const char *(*observer_directory)(void) = dlsym(RTLD_DEFAULT, "nx_isp_color_observer_directory");
    if (!registers || !gamma_table || !reg_base || !manager_instance || !info_instance ||
        !observer_directory) return 3;
    /* Substitute only storage backing. Original Gamma GetIndex/SetParam,
     * IntSpline and the UDD tone memcpy all execute unchanged. */
    *reg_base = registers;
    *manager_instance = manager;
    info_construct(info);
    *info_instance = info;
    gamma_construct(gamma_node);
    parameters[0x58/4] = 9;       /* Static Gamma row2; avoids unrelated AE/repeater path. */
    parameters[0x40/4] = 256;     /* Default exposure-index coordinate. */
    parameters[0x4c/4] = 0x111;   /* subindex4=17: retain full X endpoint8191. */
    parameters[0x60/4] = 4;       /* Middle contrast row. */
    unsigned char *bytes = (unsigned char *)parameters;
    for (unsigned int mode = 0; mode < 2; ++mode) {
        bytes[0x83] = mode;
        node_reset(gamma_node);
        memset(registers+0x4000, 0xa5, 0x4000);
        node_update(gamma_node, parameters);
        if (memcmp(registers+0x4000, gamma_table, 0x4000)) return 10+mode;
        unsigned int minimum = 65535, maximum = 0;
        for (unsigned int i = 0; i < 8192; ++i) {
            if (gamma_table[i] < minimum) minimum = gamma_table[i];
            if (gamma_table[i] > maximum) maximum = gamma_table[i];
        }
        if (mode && !memcmp(previous, gamma_table, sizeof previous)) return 12;
        memcpy(previous, gamma_table, sizeof previous);
        char label[48], output_path[512];
        snprintf(label, sizeof label, "gamma-mode-%u", mode);
        if (!copy_snapshot(observer_directory(), "gamma", mode, argv[1], label)) return 13;
        snprintf(output_path, sizeof output_path, "%s/%s.mmio.bin", argv[1], label);
        FILE *output = fopen(output_path, "wb");
        if (!output || fwrite(registers+0x4000,1,0x4000,output) != 0x4000 || fclose(output)) return 14;
        printf("ORIGINAL_GAMMA mode=%u full_table_matches_udd=1 min=%u max=%u words=8192\n",
               mode, minimum, maximum);
    }
    const int input[9] = {256, -101, 3, 63, 255, -65, -3, 33, 257};
    matrix coefficients;
    memcpy(coefficients.col_mat, input, sizeof input);
    int result = ipcs_color_correction_param(&coefficients);
    if (result || memcmp(coefficients.col_mat, input, sizeof input)) return 20;
    for (unsigned int i = 0; i < 9; ++i) {
        unsigned int packed = *(unsigned int *)(registers+0x1078+(i/2)*4);
        unsigned int actual = (packed >> ((i%2)*16)) & 0x7ffu;
        if (actual != ((unsigned int)input[i] & 0x7ffu)) return 21;
    }
    if (!copy_snapshot(observer_directory(), "ccm", 0, argv[1], "ccm-input")) return 22;
    /* A rejected matrix must retain the original error return and MMIO. */
    unsigned char saved[20];
    memcpy(saved, registers+0x1078, sizeof saved);
    coefficients.col_mat[0] = 1024;
    int rejected = ipcs_color_correction_param(&coefficients);
    if (!rejected || memcmp(saved, registers+0x1078,sizeof saved)) return 23;
    if (!copy_snapshot(observer_directory(), "ccm", 1, argv[1], "ccm-rejected")) return 24;
    printf("ORIGINAL_CCM accepted_return=%d rejected_return=%d caller_input_unchanged=1 packed_mmio_matches=1\n",
           result, rejected);
    void *udd = dlopen("libudd.so.3.1", RTLD_NOW);
    int (*original_ycc)(rgb2ycc *) = udd ? dlsym(udd, "ipcs_rgb2ycc_param") : NULL;
    unsigned char *expected_ram = malloc(0xb000);
    rgb2ycc ycc = {0}, ycc_copy;
    ycc.sel = 3;
    ycc.ycc[0] = ycc.ycc[4] = ycc.ycc[8] = 128;
    ycc_copy = ycc;
    if (!original_ycc || !expected_ram) return 25;
    memset(registers, 0, 0xb000);
    int original_rc = original_ycc(&ycc);
    memcpy(expected_ram, registers, 0xb000);
    memset(registers, 0, 0xb000);
    int observed_rc = ipcs_rgb2ycc_param(&ycc);
    if (original_rc || observed_rc != original_rc || memcmp(registers,expected_ram,0xb000) ||
        memcmp(&ycc,&ycc_copy,sizeof ycc)) return 26;
    if (!copy_snapshot(observer_directory(), "rgb2ycc", 0, argv[1], "rgb2ycc-input")) return 27;
    free(expected_ram);
    printf("ORIGINAL_RGB2YCC return=%d caller_input_unchanged=1 full_mmio_matches=1\n", observed_rc);
    gamma_destroy(gamma_node);
    *manager_instance = NULL;
    *info_instance = NULL;
    puts("ISP_COLOR_OBSERVER_PROBE=PASS; synthetic_parameters=1; original_gamma_node=1; original_udd=1; active_capture=0; image_verified=0");
    return 0;
}
