#ifndef NX_LUT_REFERENCE_INTERP_H
#define NX_LUT_REFERENCE_INTERP_H
#include <stdint.h>

/* The first two axes fit uint32_t for 10-bit samples. Preserve all weight
 * factors and round only once, identically to the eight-corner uint64 sum. */
static unsigned nx_ref_interpolate(const uint16_t *t,unsigned r,unsigned g,unsigned b) {
    unsigned ib=255-b,ig=255-g;
    uint32_t low=(t[0]*ib+t[1]*b)*ig+(t[9]*ib+t[10]*b)*g;
    uint32_t high=(t[81]*ib+t[82]*b)*ig+(t[90]*ib+t[91]*b)*g;
    uint64_t sum=(uint64_t)low*(255-r)+(uint64_t)high*r;
    return (unsigned)((sum+8290687)/16581375);
}
#endif
