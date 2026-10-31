/* Source tables and composition order from NX500 1.12 P7.
 * Tables: 0x811238d0 (mix), 0x81123908 (row sums), 0x81123924 (HUE).
 * Selectors are explicit: this helper never guesses a camera mode.
 */
#ifndef NX500_COLOR_OPERATOR_H
#define NX500_COLOR_OPERATOR_H
#include "nx500_color_math.h"

static const short chroma_hue_q8[21][4] = {
    {222,128,-128,222}, {228,116,-116,228}, {235,104,-104,235},
    {239,92,-92,239}, {243,79,-79,243}, {247,66,-66,247},
    {250,53,-53,250}, {253,40,-40,253}, {255,27,-27,255},
    {256,13,-13,256}, {256,0,0,256}, {256,-13,13,256},
    {255,-27,27,255}, {253,-40,40,253}, {250,-53,53,250},
    {247,-66,66,247}, {243,-79,79,243}, {239,-92,92,239},
    {234,-104,104,234}, {228,-116,116,228}, {222,-128,128,222}
};
static const int16_t nx500_chroma_mix_q8[7][4] = {
    {256,0,0,256}, {270,5,7,277}, {256,0,0,256}, {256,0,0,256},
    {256,0,0,256}, {270,5,7,277}, {270,5,7,277}
};
static const int16_t nx500_chroma_offsets[7][2] = {
    {0,0}, {-25,22}, {12,-7}, {-11,16}, {-12,11}, {4,-1}, {-3,-3}
};

/* Source builder tail: variant mix, SAT reconstruction, then HUE. The output
 * can alias base. Range enforcement for the NX300M signed11 setter follows
 * this calculation and must not truncate its signed16 intermediate result. */
static inline int nx500_compose_color(int16_t output[9], const int16_t base[9],
        unsigned hue, unsigned saturation, unsigned mix, unsigned offset,
        unsigned saturation_delta) {
    if (hue > 20 || saturation > 20 || mix > 6 || offset > 6 || saturation_delta > 2)
        return -102;
    int16_t matrix[9], rotation[9] = {256,0,0,0,0,0,0,0,0};
    for (int i = 0; i < 9; ++i) matrix[i] = base[i];
    rotation[4] = chroma_hue_q8[hue][0];
    rotation[5] = chroma_hue_q8[hue][1];
    rotation[7] = chroma_hue_q8[hue][2];
    rotation[8] = chroma_hue_q8[hue][3];
    int gain = saturation ? 32 + 3*((int)saturation-10) : 0;
    nx500_chroma_mix_and_saturation(matrix, nx500_chroma_mix_q8[mix],
                                    nx500_chroma_offsets[offset], gain+(int)saturation_delta);
    nx500_multiply_q8(output, rotation, matrix);
    return 0;
}
#endif
