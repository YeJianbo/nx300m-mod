#ifndef NX300M_PP_MEMORY_TASK_H
#define NX300M_PP_MEMORY_TASK_H
typedef unsigned int nx_pp_word;
enum nx_pp_stage {
    NX_PP_STATIC_DPC = 1, NX_PP_DYNAMIC_DPC = 2, NX_PP_CS = 4,
    NX_PP_DAMP = 8, NX_PP_INPUT_EXPANSION = 16, NX_PP_OTHER_CORRECTION = 32,
    NX_PP_OUTPUT_QUANTIZATION = 64
};
enum nx_pp_invalid {
    NX_PP_INVALID_MODE = 1, NX_PP_INVALID_FORMAT = 2,
    NX_PP_INVALID_GEOMETRY = 4, NX_PP_INVALID_ADDRESS = 8,
    NX_PP_INVALID_STRIDE = 16, NX_PP_UNMAPPED_ADDRESS = 32
};
struct nx_pp_plane {
    nx_pp_word read_address, write_address, read_stride, write_stride;
    nx_pp_word read_span, write_span, read_allocation, write_allocation;
    nx_pp_word read_mapped, write_mapped;
};
struct nx_pp_memory_task {
    nx_pp_word version, width, height, plane_width, plane_rows, payload_bytes;
    nx_pp_word invalid, enabled_stages, unsupported_stages, mapping_checked;
    nx_pp_word ipc_output_enabled, bit_shift_control, dpc_control, dynamic_control;
    nx_pp_word cs_control, cs_source, cs_source_bytes, cs_columns, cs_rows;
    nx_pp_word pixels_completed, completion_generated;
    struct nx_pp_plane planes[4]; /* parity order 00, 01, 10, 11 */
};
/* The register pointer and destination are caller-owned trusted buffers.
 * map_physical validates complete image ranges against the device allocator.
 * This prepares the actual task; it never substitutes a pixel executor. */
int nx300m_pp_memory_describe(const volatile nx_pp_word *, nx_pp_word,
                             struct nx_pp_memory_task *, int check_mappings);
int nx300m_pp_memory_task(void);
/* Decode only the observed strided RDMA input into a caller-owned planar
 * 14-bit intermediate. Does not require or modify WDMA output mappings,
 * execute corrections, update task completion, or send interrupts. The caller
 * must hold source allocation/mapping lifetimes stable for the entire call
 * and supply independent destination backing, without physical aliases. */
int nx300m_pp_memory_input14(const volatile nx_pp_word *, nx_pp_word,
                             unsigned short *expanded, nx_pp_word samples);
#endif
