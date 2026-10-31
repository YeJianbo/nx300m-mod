/* Original Samsung NX500 1.12 P7 Contrast knots:
 * X7 at VA 0x8062f8a4; Y21x7 at VA 0x8062f8c0, 28-byte rows.
 * Extracted evidence: outputs/metol/nx500-contrast.json.
 * The 8-bit path below is explicit target quantization, not a claim that the
 * NX300M and NX500 Gamma pipelines or resulting images are equivalent.
 */
#ifndef NX500_CONTRAST_OPERATOR_H
#define NX500_CONTRAST_OPERATOR_H

#include "nx500_contrast_math.h"

static const int nx500_contrast_x[7] = {
    0, 168, 344, 512, 680, 856, 1023
};

static const int nx500_contrast_y[21][7] = {
    {0,252,428,512,592,768,1023},
    {0,244,420,512,602,778,1023},
    {0,236,412,512,612,788,1023},
    {0,228,404,512,622,798,1023},
    {0,220,396,512,632,808,1023},
    {0,210,386,512,640,816,1023},
    {0,200,376,512,648,824,1023},
    {0,192,368,512,656,832,1023},
    {0,184,360,512,664,840,1023},
    {0,176,352,512,672,848,1023},
    {0,168,344,512,680,856,1023},
    {0,160,336,512,688,864,1023},
    {0,152,328,512,696,872,1023},
    {0,144,320,512,704,880,1023},
    {0,136,312,512,712,888,1023},
    {0,128,304,512,722,898,1023},
    {0,120,296,512,732,908,1023},
    {0,110,286,512,740,916,1023},
    {0,100,276,512,748,924,1023},
    {0,90,266,512,756,932,1023},
    {0,80,256,512,764,940,1023}
};

/* Nearest integer, exact half ties away from zero. Products and negation use
 * int64_t before division; the operator's checked domains bound the result. */
static inline int nx500_contrast_scale_round(int value, int numerator,
                                            int denominator) {
    int64_t scaled = (int64_t)value * numerator;
    if (scaled < 0)
        return (int)(-((-scaled + denominator/2) / denominator));
    return (int)((scaled + denominator/2) / denominator);
}

/* Return 0 on success, -1 on invalid arguments, -2 on an input outside the
 * supported domain. Validate every element before writing any output.
 * Exact in-place use is supported; other overlapping ranges are not promised.
 * Preserve the source ApplyLut upper cap of1023, without a lower clip. The
 * downstream target IntSpline retains its separate output clipping policy.
 */
static inline int nx500_contrast_curve(int *out, const int *input, int count,
                                       unsigned int level, int maximum) {
    if (!out || !input || count <= 0 || level > 20 ||
        (maximum != 1023 && maximum != 255)) return -1;
    int low = maximum == 1023 ? -16 : 0;
    int high = maximum == 1023 ? 1039 : 255;
    for (int index = 0; index < count; ++index)
        if (input[index] < low || input[index] > high) return -2;
    for (int index = 0; index < count; ++index) {
        int query = input[index];
        if (maximum == 255)
            query = nx500_contrast_scale_round(query, 1023, 255);
        int result = nx500_interpolate(nx500_contrast_x, nx500_contrast_y[level],
                                       7, query);
        if (result > 1023) result = 1023;
        if (maximum == 255)
            result = nx500_contrast_scale_round(result, 255, 1023);
        out[index] = result;
    }
    return 0;
}

#endif
