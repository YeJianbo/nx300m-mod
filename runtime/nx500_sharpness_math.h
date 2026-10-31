/* NX500 gain arithmetic only. These six EE fields have no established
 * one-to-one mapping to NX300M's positive/negative edge gains. */
#ifndef NX500_SHARPNESS_MATH_H
#define NX500_SHARPNESS_MATH_H
#include <stdint.h>

static const uint32_t nx500_sharpness_q8[21] = {
    140,150,160,170,180,191,202,215,228,242,256,
    272,288,306,324,344,364,387,410,435,460
};

static inline uint32_t nx500_sharpness_effective(uint32_t factor, uint32_t percent) {
    /* MUL wraps before the original unsigned division by 100. */
    return (factor * percent) / 100u;
}

/* bits must be 7 or 8, as in the original EE fields. */
static inline uint32_t nx500_sharpness_gain(uint32_t base, uint32_t effective, unsigned bits) {
    uint32_t product = base * effective;
    uint32_t mask = (1u << bits) - 1u;
    /* Original CMP is signed; avoid implementation-defined uint-to-int casts. */
    if (!(product & 0x80000000u) && product >= (1u << (bits + 8))) return mask;
    if (product & 0x80000000u) product += 255u;
    return (product >> 8) & mask;
}

static inline uint8_t nx500_sharpness_pack7(uint8_t original, uint32_t gain) {
    return (uint8_t)((original & 128u) | (gain & 127u));
}
#endif
