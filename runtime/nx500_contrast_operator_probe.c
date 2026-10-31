#include "nx500_contrast_operator.h"

extern int printf(const char *, ...);
extern int nx500_original_interpolate(const int *, const int *, int, int);

int main(void) {
    const int input[15] = {0,1,2,3,6,11,22,45,81,129,181,225,245,255,255};
    int output[15], inplace[15];
    const unsigned int level = 19;
    for (int index = 0; index < 15; ++index) inplace[index] = input[index];
    if (nx500_contrast_curve(output, input, 15, level, 255) ||
        nx500_contrast_curve(inplace, inplace, 15, level, 255)) return 2;
    for (int index = 0; index < 15; ++index) {
        /* Independent quantization around the unchanged ARM leaf. */
        int query = (int)(((int64_t)input[index]*1023 + 127) / 255);
        int original = nx500_original_interpolate(nx500_contrast_x,
                                                  nx500_contrast_y[level], 7, query);
        int capped = original > 1023 ? 1023 : original;
        int64_t product = (int64_t)capped*255;
        int expected = product < 0 ? (int)(-((-product+511)/1023))
                                   : (int)((product+511)/1023);
        if (output[index] != expected || inplace[index] != expected) {
            printf("mismatch %d %d %d %d\n", index, expected, output[index], inplace[index]);
            return 3;
        }
        printf("%d %d %d %d\n", input[index], query, original, expected);
    }
    const int upper_input = 1039;
    int upper_output;
    int upper_original = nx500_original_interpolate(nx500_contrast_x,
                                                     nx500_contrast_y[0], 7, upper_input);
    int upper_expected = upper_original > 1023 ? 1023 : upper_original;
    if (nx500_contrast_curve(&upper_output, &upper_input, 1, 0, 1023) ||
        upper_output != upper_expected || upper_original <= 1023) return 4;
    printf("capture %d %d %d\n", upper_input, upper_original, upper_output);
    return 0;
}
