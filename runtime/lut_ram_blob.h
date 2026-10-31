#ifndef NX_LUT_RAM_BLOB_H
#define NX_LUT_RAM_BLOB_H
/* All addresses are ARM32 target addresses, never host pointers. */
struct nx_lut_ram_header {
    unsigned magic, abi, blob_size, code_size, data_offset, data_size;
    unsigned update_entry, ipcs_entry, config_offset, config_size, reserved[6];
};
struct nx_lut_ram_config {
    unsigned abi, size, original_update, original_reset, original_ipcs;
    unsigned lut_vptr, factory_red, enabled;
    char root[164];
    unsigned build;
};
#define NX_LUT_RAM_MAGIC 0x4e584c42U
#define NX_LUT_RAM_ABI 1U
#define NX_LUT_RAM_CONTEXT_STRIDE_V1 4660U
#define NX_LUT_RAM_CONTEXT_STRIDE_DIAGNOSTIC_V1 4720U
#endif
