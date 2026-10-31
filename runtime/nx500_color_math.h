/* Arithmetic transcribed from NX500 1.12 P7 0x80381c84 / 0x80381df0.
 * Keep the original modular arithmetic; target setter range checks belong
 * to the caller. These functions do not choose modes or alter hardware. */
#ifndef NX500_COLOR_MATH_H
#define NX500_COLOR_MATH_H
#include <stdint.h>

static inline int16_t nx500_low16(uint32_t word) {
    int32_t value = (int32_t)(word & 0x7fffu);
    if (word & 0x8000u) value -= 32768;
    return (int16_t)value;
}

/* Unsigned operations define ARM ASR bits without a signed C right shift. */
static inline uint32_t nx500_asr(uint32_t word, unsigned int shift) {
    return (word >> shift) | ((word & 0x80000000u) ? (~UINT32_C(0) << (32u-shift)) : 0u);
}

static inline uint32_t nx500_product(int16_t left, int16_t right) {
    return (uint32_t)((int32_t)left * (int32_t)right);
}

static inline void nx500_multiply_q8(int16_t out[9], const int16_t left[9],
                                      const int16_t right[9]) {
    int16_t result[9];
    for (int row = 0; row < 3; ++row) {
        for (int column = 0; column < 3; ++column) {
            uint32_t sum = 0;
            for (int k = 0; k < 3; ++k)
                sum += nx500_product(left[row*3+k], right[k*3+column]);
            result[row*3+column] = nx500_low16(nx500_asr(sum + 128u, 8));
        }
    }
    /* Also permits out to alias either input, without cumulative updates. */
    for (int i = 0; i < 9; ++i) out[i] = result[i];
}

static inline void nx500_chroma_mix_and_saturation(int16_t matrix[9],
        const int16_t mix_q8[4], const int16_t offsets[2], int32_t gain_q5) {
    int16_t result[9];
    int16_t gain = nx500_low16((uint32_t)gain_q5);
    for (int i = 0; i < 9; ++i) result[i] = matrix[i];
    for (int column = 0; column < 3; ++column) {
        int16_t u = matrix[3+column], v = matrix[6+column];
        uint32_t first = nx500_product(mix_q8[0], u) + nx500_product(mix_q8[1], v);
        uint32_t second = nx500_product(mix_q8[2], u) + nx500_product(mix_q8[3], v);
        result[3+column] = nx500_low16(nx500_asr(first, 8));
        result[6+column] = nx500_low16(nx500_asr(second, 8));
    }
    for (int i = 4; i <= 7; ++i)
        result[i] = nx500_low16(nx500_asr(nx500_product(result[i], gain), 5));
    result[3] = nx500_low16((uint32_t)((int32_t)offsets[0]-result[4]-result[5]));
    result[8] = nx500_low16((uint32_t)((int32_t)offsets[1]-result[6]-result[7]));
    for (int i = 0; i < 9; ++i) matrix[i] = result[i];
}
#endif
