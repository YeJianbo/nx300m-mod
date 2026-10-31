/* Stream arithmetic inputs through the same portable functions used by the
 * forthcoming color backend. No driver or camera libraries are loaded. */
#include <stdio.h>
#include <stdint.h>
#include "nx500_color_operator.h"
extern void nx500_original_multiply(int16_t *, const int16_t *, const int16_t *);
extern void nx500_original_saturation(int16_t *, const int16_t *, int32_t, const int16_t *);

static int read_values(int16_t *values, int count) {
    for (int i = 0; i < count; ++i) {
        int value;
        if (scanf("%d", &value) != 1 || value < -32768 || value > 32767) return 0;
        values[i] = (int16_t)value;
    }
    return 1;
}

int main(void) {
    int operation;
    while (scanf("%d", &operation) == 1) {
        int16_t result[9], original[9], left[9], right[9], mix[4], offsets[2];
        int gain;
        if (operation == 0) {
            if (!read_values(left, 9) || !read_values(right, 9)) return 2;
            nx500_multiply_q8(result, left, right);
            nx500_original_multiply(original, left, right);
        } else if (operation == 1) {
            if (!read_values(result, 9) || !read_values(mix, 4) ||
                !read_values(offsets, 2) || scanf("%d", &gain) != 1) return 2;
            for (int i = 0; i < 9; ++i) original[i] = result[i];
            nx500_chroma_mix_and_saturation(result, mix, offsets, gain);
            nx500_original_saturation(original, offsets, gain, mix);
        } else if (operation == 2) {
            unsigned hue, saturation, variant, offset, delta;
            if (!read_values(right, 9) ||
                scanf("%u %u %u %u %u", &hue, &saturation, &variant, &offset, &delta) != 5 ||
                !read_values(left, 9) || !read_values(mix, 4) || !read_values(offsets, 2) ||
                scanf("%d", &gain) != 1) return 2;
            if (nx500_compose_color(result, right, hue, saturation, variant, offset, delta)) return 2;
            for (int i = 0; i < 9; ++i) original[i] = right[i];
            nx500_original_saturation(original, offsets, gain, mix);
            int16_t temporary[9];
            nx500_original_multiply(temporary, left, original);
            for (int i = 0; i < 9; ++i) original[i] = temporary[i];
        } else return 2;
        for (int i = 0; i < 9; ++i) {
            if (result[i] != original[i]) {
                fprintf(stderr, "Original ARM mismatch op=%d element=%d port=%d original=%d\n",
                        operation, i, result[i], original[i]);
                return 4;
            }
            printf("%s%d", i ? " " : "", result[i]);
        }
        putchar('\n');
    }
    return ferror(stdin) ? 3 : 0;
}
