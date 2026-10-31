#include <stdio.h>

void matrix_mult_3x3(int *, const int *, const int *, int)
    __asm__("_ZN8CIpcMath13MatrixMult3x3EPiPKiS2_i");

static int equal9(const int *actual, const int *expected) {
    for (int i = 0; i < 9; ++i)
        if (actual[i] != expected[i]) return 0;
    return 1;
}

static void print9(const char *name, const int *values) {
    printf("%s=[", name);
    for (int i = 0; i < 9; ++i)
        printf("%s%d", i ? "," : "", values[i]);
    printf("]\n");
}

int main(void) {
    const int interpolate[9] = {
        256, 128, 0,
        0, 256, 0,
        0, 0, 256
    };
    const int signed_input[9] = {
        1, -3, 5,
        4, -7, 9,
        -11, 13, 17
    };
    const int expected_signed[9] = {
        3, -6, 9,
        4, -7, 9,
        -11, 13, 17
    };
    int product[9];
    matrix_mult_3x3(product, interpolate, signed_input, 256);
    print9("row_major_product", product);
    if (!equal9(product, expected_signed)) return 2;

    const int base[9] = {
        256, 128, 0,
        64, 256, -64,
        0, 32, 256
    };
    const int gain_q8[9] = {
        384, 0, 0,
        0, 256, 0,
        0, 0, 128
    };
    const int expected_output_rows[9] = {
        384, 192, 0,
        64, 256, -64,
        0, 16, 128
    };
    matrix_mult_3x3(product, gain_q8, base, 256);
    print9("diag_gain_times_base_q8", product);
    if (!equal9(product, expected_output_rows)) return 3;

    puts("MATRIX_PROBE=PASS; multiplier=stock-NX300M-1.15; scale=Q8(256); row-major=A*B; signed division=truncate-toward-zero");
    return 0;
}
