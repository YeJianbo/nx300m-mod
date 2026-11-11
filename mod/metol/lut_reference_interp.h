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
#endif
