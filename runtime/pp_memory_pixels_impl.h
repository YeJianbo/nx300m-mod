#ifndef NX300M_PP_MEMORY_PIXELS_IMPL_H
#define NX300M_PP_MEMORY_PIXELS_IMPL_H
#include "pp_memory_pixels.h"

static int pp_input14_configured(const volatile unsigned int *p) {
    /* Stock pp_dd_rdma_bit_shift_on(PP_BIT_12): ten linear segments,
     * gradient 0x40, zero offsets, 12-bit input and 14-bit output. */
    static const unsigned int boundaries[5] = {
        0x01000200U, 0x04000800U, 0x10002000U, 0x3fff3fffU, 0x3fff0000U
    };
    if (p[0x4550/4] != 0x3001U) return 0;
    for (unsigned int i = 0; i < 5; i++)
        if (p[(0x4504+i*4)/4] != boundaries[i] ||
            p[(0x4518+i*4)/4] != 0x00400040U ||
            p[(0x452c+i*4)/4] != 0U) return 0;
    return 1;
}

static void pp_input14_row(const unsigned char *packed,
                           unsigned short *expanded, unsigned int pixels) {
    for (unsigned int i = 0; i < pixels/2U; i++) {
        const unsigned char *s = packed+i*3U;
        unsigned int a = s[0] | ((unsigned int)(s[1]&15U)<<8);
        unsigned int b = (s[1]>>4) | ((unsigned int)s[2]<<4);
        expanded[i*2U] = (unsigned short)(a<<2);
        expanded[i*2U+1U] = (unsigned short)(b<<2);
    }
}

static int pp_input14_overlap(const void *source, unsigned int bytes,
                               const void *target, unsigned int target_bytes) {
    unsigned long a = (unsigned long)source, b = (unsigned long)target;
    unsigned long end_a = a+bytes, end_b = b+target_bytes;
    return end_a < a || end_b < b || (a < end_b && b < end_a);
}

int nx300m_pp_expand12_to14(const volatile unsigned int *p,
                          const unsigned char *packed, unsigned int bytes,
                          unsigned short *expanded, unsigned int pixels) {
    if (!p || !packed || !expanded || ((unsigned long)expanded&1U) ||
        !pixels || (pixels&1U) || pixels > 0x1fffffffU ||
        bytes != pixels/2U*3U) return -22;
    if (!pp_input14_configured(p)) return -95;
    if (pp_input14_overlap(packed, bytes, expanded, pixels*2U)) return -22;
    pp_input14_row(packed, expanded, pixels);
    return 0;
}

int nx300m_pp_expand12_planes14(const volatile unsigned int *p,
                              const unsigned char *const packed[4],
                              const unsigned int bytes[4],
                              const unsigned int stride[4],
                              unsigned int width, unsigned int rows,
                              unsigned short *expanded, unsigned int samples) {
    if (!p || !packed || !bytes || !stride || !expanded ||
        ((unsigned long)expanded&1U) || !width || (width&1U) ||
        width > 4096 || !rows || rows > 4096 ||
        samples != width*rows*4U) return -22;
    if (!pp_input14_configured(p)) return -95;
    unsigned int payload = width/2U*3U;
    for (unsigned int i = 0; i < 4; i++) {
        if (!packed[i] || stride[i] < payload || stride[i] > 131071U)
            return -22;
        unsigned int span = (rows-1U)*stride[i]+payload;
        if (bytes[i] < span ||
            pp_input14_overlap(packed[i], span, expanded, samples*2U))
            return -22;
    }
    for (unsigned int i = 0; i < 4; i++)
        for (unsigned int y = 0; y < rows; y++)
            pp_input14_row(packed[i]+y*stride[i],
                           expanded+(i*rows+y)*width, width);
    return 0;
}
#endif
