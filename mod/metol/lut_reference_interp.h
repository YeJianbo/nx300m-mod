#ifndef NX_LUT_REFERENCE_INTERP_H
#define NX_LUT_REFERENCE_INTERP_H
#include <stdint.h>

/* Validated 10-bit samples bound n to 1023*255^3 + floor(255^3/2).
 * 259/2^32 is below 1/255^3; its quotient error in this range is <0.094.
 * One remainder correction therefore gives the exact integer quotient. */
static unsigned nx_ref_round(uint64_t n) {
    unsigned q=(unsigned)((n*259)>>32);
    return q+(n-(uint64_t)q*16581375>=16581375);
}
static unsigned nx_ref_interpolate(const uint16_t *t,unsigned r,unsigned g,unsigned b) {
    unsigned ib=255-b,ig=255-g;
    uint32_t low=(t[0]*ib+t[1]*b)*ig+(t[9]*ib+t[10]*b)*g;
    uint32_t high=(t[81]*ib+t[82]*b)*ig+(t[90]*ib+t[91]*b)*g;
    uint64_t sum=(uint64_t)low*(255-r)+(uint64_t)high*r;
    return nx_ref_round(sum+8290687);
}
static inline void nx_ref_interpolate3(const uint16_t *t,unsigned index,unsigned r,unsigned g,unsigned b,
        const unsigned char to8[1024],unsigned char out[3]) {
    unsigned ib=255u-b,ig=255u-g,ir=255u-r;
    uint32_t ig_ib=ig*ib,ig_b=ig*b,g_ib=g*ib,g_b=g*b;
    for(int c=0;c<3;c++) {
        const uint16_t *p=t+(size_t)c*730u+index;
        /* 面内 4 点求和最大 1023*65025 < 2^32，全部保持 32 位乘加后再做 2 次 Z 轴 64 位乘法。 */
        uint32_t low=p[0]*ig_ib+p[1]*ig_b+p[9]*g_ib+p[10]*g_b;
        uint32_t high=p[81]*ig_ib+p[82]*ig_b+p[90]*g_ib+p[91]*g_b;
        uint64_t sum=(uint64_t)low*ir+(uint64_t)high*r;
        out[c]=to8[nx_ref_round(sum+8290687ULL)];
    }
}
#endif
