/* NX500 PW RGB scales input columns, before later color composition.
 * Codes here are already decoded by the original parameter path; they are
 * not the recipe's 0..200 percentages or this project's output-row gains. */
#ifndef NX500_RGB_MATH_H
#define NX500_RGB_MATH_H
#include "nx500_color_math.h"

/* Recipe100 encodes127, not the bypass sentinel128. Disabling a recipe must
 * bypass/restore its stage rather than submitting RGB100 as a reset. */
static inline int nx500_rgb_recipe_codes(unsigned int codes[3],const unsigned int values[3]) {
    for(int i=0;i<3;++i) if(values[i]>200u) return -1;
    for(int i=0;i<3;++i) codes[i]=(255u*values[i])/200u;
    return 0;
}

static inline int16_t nx500_rgb_coefficient(int16_t coefficient, unsigned int code) {
    uint32_t product=(uint32_t)(int32_t)coefficient * code;
    return nx500_low16(nx500_asr(product+64u,7));
}

/* Valid decoded source codes are 0..256. Exact in-place use is supported.
 * Check all codes before touching output, then preserve the original STRH
 * wrap instead of clipping to the target hardware's smaller coefficient range. */
static inline int nx500_rgb_columns(int16_t output[9],const int16_t input[9],
                                    const unsigned int codes[3]) {
    int16_t result[9];
    for(int column=0;column<3;++column) if(codes[column]>256u) return -1;
    for(int i=0;i<9;++i) result[i]=nx500_rgb_coefficient(input[i],codes[i%3]);
    for(int i=0;i<9;++i) output[i]=result[i];
    return 0;
}

/* ColorD5's final CCM transform, original F at0x8062fd40. The input/output
 * pixel domains of F have not been established; this is source arithmetic,
 * not an instruction to submit these coefficients to an NX300M setter. */
static const int16_t nx500_rgb_final_q7[9]={128,96,-32,128,-32,-32,128,-32,96};
static inline void nx500_rgb_final_matrix(int16_t output[9],const int16_t input[9]) {
    int16_t result[9];
    for(int row=0;row<3;++row) for(int column=0;column<3;++column) {
        uint32_t sum=0;
        for(int k=0;k<3;++k) sum+=nx500_product(input[row*3+k],nx500_rgb_final_q7[k*3+column]);
        /* Original SDIV128 truncates toward zero, unlike the preceding
         * per-column +64 rounding and unlike the SAT/HUE +128 operation. */
        if(sum & 0x80000000u) sum+=127u;
        result[row*3+column]=nx500_low16(nx500_asr(sum,7));
    }
    for(int i=0;i<9;++i) output[i]=result[i];
}
#endif
