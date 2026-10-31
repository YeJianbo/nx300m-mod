#include "pp_memory_task.h"
#include "pp_memory_pixels_impl.h"

int nx300m_pp_memory_describe(const volatile nx_pp_word *p, nx_pp_word bytes,
                             struct nx_pp_memory_task *task, int check_mappings) {
    if (!p || !task || bytes != 0xb000) return -22;
    nx_pp_word *clear = (nx_pp_word *)task;
    for (nx_pp_word i = 0; i < sizeof *task/sizeof *clear; i++) clear[i] = 0;
    task->version = 1;
    nx_pp_word common = p[8/4], rd = p[0x3000/4], wd = p[0x3314/4];
    nx_pp_word w0 = p[0x3004/4]&8191U, w1 = (p[0x3004/4]>>16)&8191U;
    task->width = w0*4;
    task->height = p[0x3008/4]>>16;
    task->ipc_output_enabled = (p[0]>>24)&1U;
    if ((common&15U) != 3 || ((common>>24)&3U) != 2 || ((common>>28)&3U) != 2)
        task->invalid |= NX_PP_INVALID_MODE;
    /* Stock PP_CH_4 + PP_BIT_12 selects RDMA code 6 and mode 7. Widths
     * describe a quarter mosaic; four memory planes each contain half width.
     * V/H repeat and inverse delta modes are not interpreted as plain rows. */
    if ((rd&0x01130ff3U) != 0x663U || ((p[0x3038/4]>>4)&7U) != 7 ||
        (wd&0x771U) != 0x421U || ((p[0]>>16)&7U) != 2)
        task->invalid |= NX_PP_INVALID_FORMAT;
    if (!w0 || w0 != w1 || task->width > 8192 || !task->height ||
        task->height > 8192 || (task->height&1U)) task->invalid |= NX_PP_INVALID_GEOMETRY;
    task->plane_width = task->width/2;
    task->plane_rows = task->height/2;
    task->payload_bytes = task->plane_width/2*3;
    static const nx_pp_word ra[4] = {0x300c, 0x3018, 0x3010, 0x301c};
    static const nx_pp_word wa[4] = {0x3304, 0x3324, 0x3308, 0x3328};
    static const nx_pp_word rs[4] = {0x3014, 0x3020, 0x3014, 0x3020};
    static const nx_pp_word ws[4] = {0x330c, 0x332c, 0x330c, 0x332c};
    for (nx_pp_word i = 0; i < 4; i++) {
        struct nx_pp_plane *plane = task->planes+i;
        plane->read_address = p[ra[i]/4]; plane->write_address = p[wa[i]/4];
        plane->read_stride = p[rs[i]/4]; plane->write_stride = p[ws[i]/4];
        if (!plane->read_address || !plane->write_address ||
            ((plane->read_address|plane->write_address)&7U)) task->invalid |= NX_PP_INVALID_ADDRESS;
        if (plane->read_stride < task->payload_bytes || plane->write_stride < task->payload_bytes ||
            plane->read_stride > 131071 || plane->write_stride > 131071 ||
            ((plane->read_stride|plane->write_stride)&7U)) task->invalid |= NX_PP_INVALID_STRIDE;
        if (!task->invalid) {
            plane->read_span = (task->plane_rows-1)*plane->read_stride+task->payload_bytes;
            plane->write_span = (task->plane_rows-1)*plane->write_stride+task->payload_bytes;
            plane->read_allocation = task->plane_rows*plane->read_stride;
            plane->write_allocation = task->plane_rows*plane->write_stride;
            if (plane->read_address > ~0U-plane->read_allocation ||
                plane->write_address > ~0U-plane->write_allocation) task->invalid |= NX_PP_INVALID_ADDRESS;
        }
    }
    task->dpc_control = p[0x4000/4]; task->dynamic_control = p[0x4040/4];
    if (task->dpc_control&1U) {
        if (task->dpc_control&2U) task->enabled_stages |= NX_PP_STATIC_DPC;
        if (task->dpc_control&4U) task->enabled_stages |= NX_PP_DYNAMIC_DPC;
    }
    task->cs_control = p[0x42d0/4];
    if (task->cs_control&1U) task->enabled_stages |= NX_PP_CS;
    if (p[0x4310/4]&1U) task->enabled_stages |= NX_PP_DAMP;
    task->bit_shift_control = p[0x4550/4];
    if (task->bit_shift_control&1U) task->enabled_stages |= NX_PP_INPUT_EXPANSION;
    /* GPL module enable fields are all bit 0 at these independently named
     * blocks; DPC dynamic/static are handled separately above. */
    static const nx_pp_word other[15] = {0x40c0,0x4140,0x4180,0x41a0,0x41c0,
        0x4240,0x4280,0x4340,0x4380,0x43c0,0x4400,0x4440,0x4480,0x44c0,0x4540};
    for (nx_pp_word i = 0; i < 15; i++) if (p[other[i]/4]&1U) task->enabled_stages |= NX_PP_OTHER_CORRECTION;
    task->unsupported_stages = task->enabled_stages;
    if (task->bit_shift_control&1U) task->unsupported_stages |= NX_PP_OUTPUT_QUANTIZATION;
    task->cs_source = p[0x4608/4];
    task->cs_source_bytes = ((p[0x42e0/4]>>8)&8191U)*16;
    nx_pp_word sample = (p[0x42f0/4]>>16)&255U;
    if (sample) {
        task->cs_columns = ((p[0x42d8/4]&8191U)+sample-1)/sample+1;
        task->cs_rows = ((((p[0x42d8/4]>>16)&8191U)+sample-1)/sample+2)&~1U;
    }
    if (task->invalid) return -22;
    if (check_mappings) {
        task->mapping_checked = 1;
        for (nx_pp_word i = 0; i < 4; i++) {
            struct nx_pp_plane *plane = task->planes+i;
            plane->read_mapped = nx300m_map_physical_access(plane->read_address, plane->read_allocation, 1) != (void *)0;
            plane->write_mapped = nx300m_map_physical_access(plane->write_address, plane->write_allocation, 2) != (void *)0;
            if (!plane->read_mapped || !plane->write_mapped) task->invalid |= NX_PP_UNMAPPED_ADDRESS;
        }
        if (task->invalid) return -14;
    }
    /* No complete PP pixel executor exists. Do not write any destination or
     * synthesize RDMA/WDMA IRQs, even if all correction enables are cleared. */
    return -95;
}

int nx300m_pp_memory_input14(const volatile nx_pp_word *p, nx_pp_word bytes,
                             unsigned short *expanded, nx_pp_word samples) {
    struct nx_pp_memory_task task;
    int result = nx300m_pp_memory_describe(p, bytes, &task, 0);
    /* Unsupported corrections belong after this intermediate. Invalid DMA
     * geometry must still fail, and all four readable spans must exist. */
    if (result != -95) return result;
    const unsigned char *source[4];
    nx_pp_word spans[4], strides[4];
    for (nx_pp_word i = 0; i < 4; i++) {
        spans[i] = task.planes[i].read_span;
        strides[i] = task.planes[i].read_stride;
        source[i] = nx300m_map_physical_access(task.planes[i].read_address,
                                               spans[i], 1);
        if (!source[i]) return -14;
    }
    return nx300m_pp_expand12_planes14(p, source, spans, strides,
                                       task.plane_width, task.plane_rows,
                                       expanded, samples);
}

int nx300m_pp_memory_task(void) {
    struct nx_pp_memory_task task;
    volatile u32 *p = isp_registers[ISP_PP];
    if (!p) return -19;
    int result = nx300m_pp_memory_describe(p, 0xb000, &task, 1);
    pp_cs_lut_snapshot();
    int fd = checked(raw(5, (long)"/var/run/nx300m-pp-memory-task.bin", 0101|01000|02000000, 0600, 0, 0, 0));
    if (fd >= 0) {
        raw(4, fd, (long)&task, sizeof task, 0, 0, 0);
        raw(6, fd, 0, 0, 0, 0, 0);
    }
    event("PP memory task prepared; pixels unsupported", (u32)result, task.invalid, task.unsupported_stages);
    return result;
}
