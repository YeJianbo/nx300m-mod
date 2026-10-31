/* Execute the genuine stock libudd LUT copy routine against synthetic mapped
 * register RAM. This checks ABI and byte placement, not ISP image processing.
 */
extern void *calloc(unsigned int, unsigned int);
extern void free(void *);
extern void *fopen(const char *, const char *);
extern unsigned int fread(void *, unsigned int, unsigned int, void *);
extern int fclose(void *);
extern int printf(const char *, ...);
extern int fprintf(void *, const char *, ...);
extern int snprintf(char *, unsigned int, const char *, ...);
extern int memcmp(const void *, const void *, unsigned int);
extern void *dlsym(void *, const char *);
extern void ipcs_3d_lut(unsigned int, const unsigned short *);
#include "../mod/metol/mod_paths.h"
int main(void) {
    const char *root = nx_mod_root();
    if (!root) return 2;
    char table_path[256], invalid_path[256];
    snprintf(table_path, sizeof table_path, "%s/looks/probe/table.d4lut", root);
    snprintf(invalid_path, sizeof invalid_path, "%s/looks/probe/invalid.d4lut", root);
    unsigned char *registers = calloc(1, 0xb000);
    unsigned short expected[2190], stock[730];
    void *file = fopen(table_path, "rb");
    if (!registers || !file) return 2;
    unsigned int count = fread(expected, 2, 2190, file); fclose(file);
    if (count != 2190) return 3;
    void **base = dlsym((void *)0, "ipcs_reg_base");
    if (!base) return 4;
    *base = registers;
    for (unsigned int i = 0; i < 730; i++) stock[i] = 0x155;
    file = fopen("/var/run/nx300m-lut-selection", "w");
    if (!file) return 5;
    fprintf(file, "1 %s\n", table_path); fclose(file);
    for (unsigned int c = 0; c < 3; c++) {
        ipcs_3d_lut(c, stock);
        unsigned int offset = 0x9000 + c * 0x700;
        if (memcmp(registers + offset, expected + c*730, 1460)) return 10+c;
        printf("STOCK_LUT_COPY channel=%u offset=0x%x bytes=1460 matched=1\n", c, offset);
    }
    file = fopen("/var/run/nx300m-lut-selection", "w");
    fprintf(file, "2 %s\n", invalid_path); fclose(file);
    for (unsigned int c = 0; c < 3; c++) {
        ipcs_3d_lut(c, stock);
        if (memcmp(registers + 0x9000 + c*0x700, stock, 1460)) return 20+c;
    }
    printf("INVALID_TABLE_STOCK_PASSTHROUGH=1\n");
    free(registers); return 0;
}
