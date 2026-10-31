/* Exercise the genuine stock C++ LUT/Gamma index nodes and LUT update cache.
 * Parameter input and MMIO RAM are explicit fixtures, not a sensor pipeline.
 */
extern void *calloc(unsigned int, unsigned int);
extern void free(void *);
extern void *fopen(const char *, const char *);
extern unsigned int fread(void *, unsigned int, unsigned int, void *);
extern int fclose(void *);
extern int printf(const char *, ...);
extern int fprintf(void *, const char *, ...);
extern int memcmp(const void *, const void *, unsigned int);
extern int unlink(const char *);
extern void *memset(void *, int, unsigned int);
extern void *dlsym(void *, const char *);
extern void ipcs_hl_3d_lut_onoff(unsigned int);
extern void lut_construct(void *) __asm__("_ZN12CIpcs3DLutD4C1Ev");
extern void gamma_construct(void *) __asm__("_ZN12CIpcsGammaD4C1Ev");
extern unsigned int lut_index(void *, void *) __asm__("_ZN12CIpcs3DLutD48GetIndexEPv");
extern unsigned int gamma_index(void *, void *) __asm__("_ZN12CIpcsGammaD48GetIndexEPv");
extern unsigned int sub_index(void *, unsigned int, int) __asm__("_ZN17CIpcParameterNode11GetSubIndexEji");
extern void node_update(void *, void *) __asm__("_ZN17CIpcParameterNode6UpdateEPv");
extern void node_reset(void *) __asm__("_ZN17CIpcParameterNode5ResetEv");
extern void lut_destroy(void *) __asm__("_ZN12CIpcs3DLutD4D1Ev");
extern void gamma_destroy(void *) __asm__("_ZN12CIpcsGammaD4D1Ev");

static void virtual_update(unsigned int *node, void *parameters) {
    void (**vtable)(void *, void *) = *(void (***)(void *, void *))node;
    vtable[0](node, parameters);
}

int main(void) {
    unsigned char *registers = calloc(1, 0xb000);
    unsigned int lut[9] = {0}, gamma[48] = {0}, parameters[39] = {0};
    unsigned short expected[2190];
    void *file = fopen("/mod/looks/node-probe/table-stock3d.d4lut", "rb");
    if (!registers || !file) return 2;
    unsigned int count = fread(expected, 2, 2190, file); fclose(file);
    if (count != 2190) return 3;
    void **base = dlsym((void *)0, "ipcs_reg_base");
    if (!base) return 4;
    *base = registers;
    lut_construct(lut); gamma_construct(gamma);
    /* Avoid the unrelated AE/repeater path in Gamma GetIndex. All inputs
     * remain identical while toggling the original +0x83 LUT selector. */
    parameters[0x58/4] = 9;
    parameters[0x40/4] = 256;
    unsigned char *bytes = (unsigned char *)parameters;
    for (unsigned int enabled = 0; enabled < 2; enabled++) {
        bytes[0x83] = enabled;
        unsigned int li = sub_index(lut, lut_index(lut, parameters), 0);
        unsigned int gi = sub_index(gamma, gamma_index(gamma, parameters), 5);
        printf("STOCK_NODE_INDEX input=%u lut=%u gamma=%u\n", enabled, li, gi);
        if (li != enabled || gi != enabled) return 10+enabled;
    }
    file = fopen("/var/run/nx300m-lut-selection", "w");
    if (!file) return 5;
    fprintf(file, "1 /mod/looks/node-probe/table.d4lut\n"); fclose(file);
    virtual_update(lut, parameters);
    for (unsigned int c = 0; c < 3; c++) {
        if (memcmp(registers+0x9000+c*0x700, expected+c*730, 1460)) return 20+c;
        printf("STOCK_NODE_TABLE channel=%u matched=1\n", c);
        memset(registers+0x9000+c*0x700, 0, 1460);
    }
    /* A second Update with unchanged parameters must use the original cache. */
    virtual_update(lut, parameters);
    unsigned short zero[730] = {0};
    for (unsigned int c = 0; c < 3; c++)
        if (memcmp(registers+0x9000+c*0x700, zero, 1460)) return 30+c;
    printf("STOCK_NODE_CACHE unchanged_index_skips_write=1\n");
    node_reset(lut); virtual_update(lut, parameters);
    for (unsigned int c = 0; c < 3; c++)
        if (memcmp(registers+0x9000+c*0x700, expected+c*730, 1460)) return 40+c;
    printf("STOCK_NODE_RESET reloads_three_channels=1\n");
    file = fopen("/mod/looks/node-probe-alt/table-stock3d.d4lut", "rb");
    if (!file) return 6;
    count = fread(expected, 2, 2190, file); fclose(file);
    if (count != 2190) return 7;
    file = fopen("/var/run/nx300m-lut-selection", "w");
    if (!file) return 8;
    fprintf(file, "2 /mod/looks/node-probe-alt/table.d4lut\n"); fclose(file);
    virtual_update(lut, parameters);
    for (unsigned int c = 0; c < 3; c++)
        if (memcmp(registers+0x9000+c*0x700, expected+c*730, 1460)) return 60+c;
    printf("STOCK_NODE_CHANGED_SELECTION reloads_without_parameter_change=1\n");
    if (unlink("/var/run/nx300m-lut-selection")) return 9;
    virtual_update(lut, parameters);
    const char *stock_symbols[3] = {"_ZN12CIpcs3DLutD47m_sRLutE", "_ZN12CIpcs3DLutD47m_sGLutE", "_ZN12CIpcs3DLutD47m_sBLutE"};
    for (unsigned int c = 0; c < 3; c++) {
        void *stock = dlsym((void *)0, stock_symbols[c]);
        if (!stock || memcmp(registers+0x9000+c*0x700, stock, 1460)) return 70+c;
    }
    printf("STOCK_NODE_CLEAR_SELECTION restores_original_table=1\n");
    /* The strength baseline is the incoming factory table, not a neutral
     * identity grid. Keep the generation unchanged to check cache invalidation
     * also notices a changed strength in externally supplied selections. */
    for (unsigned int amount = 0; amount <= 100; amount += 50) {
        file = fopen("/var/run/nx300m-lut-selection", "w");
        if (!file) return 80;
        fprintf(file, "3 /mod/looks/node-probe-alt/table.d4lut %u\n", amount); fclose(file);
        virtual_update(lut, parameters);
        for (unsigned int c = 0; c < 3; c++) {
            const unsigned short *stock = dlsym((void *)0, stock_symbols[c]);
            const unsigned short *written = (const unsigned short *)(registers+0x9000+c*0x700);
            for (unsigned int i = 0; i < 730; i++) {
                unsigned int value = ((unsigned int)stock[i]*(100-amount) +
                    (unsigned int)expected[c*730+i]*amount + 50)/100;
                if (written[i] != value) return 81+c;
            }
        }
        printf("STOCK_NODE_STRENGTH amount=%u factory_based_blend=1\n", amount);
    }
    const char *invalid[] = {"101", "-1", "50 extra"};
    for (unsigned int i = 0; i < 3; i++) {
        file = fopen("/var/run/nx300m-lut-selection", "w");
        if (!file) return 85;
        fprintf(file, "4 /mod/looks/node-probe-alt/table.d4lut %s\n", invalid[i]); fclose(file);
        /* Reset the stock node to observe the fallback even if the previous
         * invalid selection has already restored the unchanged original. */
        node_reset(lut); virtual_update(lut, parameters);
        for (unsigned int c = 0; c < 3; c++) {
            void *stock = dlsym((void *)0, stock_symbols[c]);
            if (!stock || memcmp(registers+0x9000+c*0x700, stock, 1460)) return 86+c;
        }
    }
    printf("STOCK_NODE_INVALID_STRENGTH factory_passthrough=1\n");
    if (unlink("/var/run/nx300m-lut-selection")) return 90;
    virtual_update(lut, parameters);
    unsigned int *control = (unsigned int *)(registers+0x1040);
    unsigned int initial = 0xa5a50055U;
    *control = initial;
    ipcs_hl_3d_lut_onoff(1);
    if (*control != (initial | 0x2000)) return 50;
    ipcs_hl_3d_lut_onoff(0);
    if (*control != initial) return 51;
    printf("STOCK_LUT_ENABLE offset=0x1040 bit=13 other_bits_preserved=1\n");
    gamma_destroy(gamma); lut_destroy(lut); free(registers);
    return 0;
}
