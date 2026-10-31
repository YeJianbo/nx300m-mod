/* NX500 1.12 P7 interpolation, original ARM 0x803887f0..0x80388ca8.
 *
 * Each helper below corresponds to one original soft-float call. Volatile
 * binary32 results keep the original rounding points and prevent contraction
 * across operations. Build without fast-math. This is the interpolation leaf,
 * not the Gamma node, mode selector, upper clipping or hardware LUT builder.
 *
 * Valid source call: count >= 3, strictly increasing x, readable x/y arrays.
 * The original source Contrast caller supplies seven knots in 0..1023.
 */
#ifndef NX500_CONTRAST_MATH_H
#define NX500_CONTRAST_MATH_H

#include <stdint.h>
#include <limits.h>

/* Define the original 32-bit ARM integer wrap before any float conversion. */
static inline int32_t nx500_contrast_s32(uint32_t word) {
    return (word & UINT32_C(0x80000000))
        ? -1 - (int32_t)(~word) : (int32_t)word;
}

static inline int32_t nx500_contrast_sub(int32_t left, int32_t right) {
    return nx500_contrast_s32((uint32_t)left - (uint32_t)right);
}

static inline int32_t nx500_contrast_add(int32_t left, int32_t right) {
    return nx500_contrast_s32((uint32_t)left + (uint32_t)right);
}

static inline int32_t nx500_contrast_mul(int32_t left, int32_t right) {
    return nx500_contrast_s32((uint32_t)left * (uint32_t)right);
}

static inline int32_t nx500_contrast_sdiv(int32_t numerator, int32_t denominator) {
    /* ARM SDIV returns zero on division by zero and wraps INT_MIN/-1. */
    if (!denominator) return 0;
    if (numerator == INT32_MIN && denominator == -1) return INT32_MIN;
    return numerator / denominator;
}

/* 0x804fa8b8: signed integer -> binary32. */
static inline float nx500_contrast_i2f(int32_t value) {
    volatile float result = (float)value;
    return result;
}

/* 0x804fa6f4 / 0x804fa6f0 / 0x804fa994 / 0x804fab2c. */
static inline float nx500_contrast_fadd(float left, float right) {
    volatile float result = left + right;
    return result;
}

static inline float nx500_contrast_fsub(float left, float right) {
    volatile float result = left - right;
    return result;
}

static inline float nx500_contrast_fmul(float left, float right) {
    volatile float result = left * right;
    return result;
}

static inline float nx500_contrast_fdiv(float left, float right) {
    volatile float result = left / right;
    return result;
}

/* 0x804fada0: truncate toward zero, saturate overflow, NaN -> 0.
 * Bit decoding avoids an out-of-range C float-to-int conversion. */
static inline int32_t nx500_contrast_f2iz(float value) {
    union { float f; uint32_t u; } bits;
    bits.f = value;
    uint32_t magnitude = bits.u & UINT32_C(0x7fffffff);
    unsigned int exponent = magnitude >> 23;
    unsigned int negative = bits.u >> 31;
    if (exponent == 255 && (magnitude & UINT32_C(0x7fffff))) return 0;
    if (exponent >= 158) return negative ? INT32_MIN : INT32_MAX;
    if (exponent < 127) return 0;
    magnitude = (magnitude & UINT32_C(0x7fffff)) | UINT32_C(0x800000);
    magnitude = exponent >= 150 ? magnitude << (exponent - 150)
                                : magnitude >> (150 - exponent);
    return negative ? -(int32_t)magnitude : (int32_t)magnitude;
}

static inline int nx500_interpolate(const int *x, const int *y,
                                    int count, int query) {
    int last = count - 1;
    int interval;
    float width, distance, fraction;
    float slope, ordinate, first, second, result;

    /* 0x80388a48 / 0x80388a18: integer extrapolation, including MUL wrap. */
    if (query < x[0]) {
        int32_t numerator = nx500_contrast_mul(
            nx500_contrast_sub(y[1], y[0]),
            nx500_contrast_sub(x[0], query));
        return nx500_contrast_add(y[0], nx500_contrast_sdiv(
            numerator, nx500_contrast_sub(x[0], x[1])));
    }
    if (query > x[last]) {
        int32_t numerator = nx500_contrast_mul(
            nx500_contrast_sub(y[last], y[last-1]),
            nx500_contrast_sub(query, x[last]));
        return nx500_contrast_add(y[last], nx500_contrast_sdiv(
            numerator, nx500_contrast_sub(x[last], x[last-1])));
    }
    if (query == x[0]) return y[0];
    for (interval = 1; interval < count; ++interval)
        if (query <= x[interval]) break;
    if (query == x[interval]) return y[interval];

    if (interval == 1) {
        /* 0x80388a70..0x80388b88: first interval, in original call order. */
        width = nx500_contrast_i2f(nx500_contrast_sub(x[1], x[0]));
        slope = nx500_contrast_i2f(nx500_contrast_sub(y[2], y[0]));
        result = nx500_contrast_i2f(nx500_contrast_sub(x[2], x[0]));
        slope = nx500_contrast_fdiv(slope, result);
        distance = nx500_contrast_i2f(nx500_contrast_sub(query, x[0]));
        fraction = nx500_contrast_fdiv(distance, width);
        result = nx500_contrast_i2f(nx500_contrast_sub(y[1], y[0]));
        result = nx500_contrast_fdiv(result, width);
        result = nx500_contrast_fmul(result, distance);
        ordinate = nx500_contrast_i2f(y[0]);
        result = nx500_contrast_fadd(result, ordinate);
        first = nx500_contrast_fsub(1.0f, fraction);
        first = nx500_contrast_fmul(result, first);
        second = nx500_contrast_fmul(slope, distance);
        result = nx500_contrast_i2f(y[1]);
        slope = nx500_contrast_fmul(width, slope);
        result = nx500_contrast_fsub(result, slope);
        result = nx500_contrast_fadd(second, result);
        result = nx500_contrast_fmul(result, fraction);
        result = nx500_contrast_fadd(first, result);
        result = nx500_contrast_fadd(result, 0.5f);
        return nx500_contrast_f2iz(result);
    }

    if (interval == last) {
        /* 0x80388ba8..0x80388ca4: last interval. */
        width = nx500_contrast_i2f(nx500_contrast_sub(x[last], x[last-1]));
        ordinate = nx500_contrast_i2f(y[last-1]);
        distance = nx500_contrast_i2f(nx500_contrast_sub(query, x[last-1]));
        fraction = nx500_contrast_fdiv(distance, width);
        slope = nx500_contrast_i2f(nx500_contrast_sub(y[last], y[last-2]));
        result = nx500_contrast_i2f(nx500_contrast_sub(x[last], x[last-2]));
        slope = nx500_contrast_fdiv(slope, result);
        result = nx500_contrast_fmul(slope, distance);
        result = nx500_contrast_fadd(result, ordinate);
        first = nx500_contrast_fsub(1.0f, fraction);
        first = nx500_contrast_fmul(result, first);
        result = nx500_contrast_i2f(nx500_contrast_sub(y[last], y[last-1]));
        result = nx500_contrast_fdiv(result, width);
        result = nx500_contrast_fmul(result, distance);
        result = nx500_contrast_fadd(ordinate, result);
        result = nx500_contrast_fmul(result, fraction);
        result = nx500_contrast_fadd(first, result);
        result = nx500_contrast_fadd(result, 0.5f);
        return nx500_contrast_f2iz(result);
    }

    /* 0x80388878..0x80388a14: interior interval. The factor is t/(2*d),
     * formed by multiplying t by 0.5 before division, as in the original. */
    int previous = interval - 1;
    width = nx500_contrast_i2f(nx500_contrast_sub(x[interval], x[previous]));
    slope = nx500_contrast_i2f(nx500_contrast_sub(y[interval+1], y[previous]));
    result = nx500_contrast_i2f(nx500_contrast_sub(x[interval+1], x[previous]));
    slope = nx500_contrast_fdiv(slope, result);
    ordinate = nx500_contrast_i2f(y[previous]);
    distance = nx500_contrast_i2f(nx500_contrast_sub(query, x[previous]));
    fraction = nx500_contrast_fmul(distance, 0.5f);
    fraction = nx500_contrast_fdiv(fraction, width);
    result = nx500_contrast_i2f(nx500_contrast_sub(y[interval], y[interval-2]));
    first = nx500_contrast_i2f(nx500_contrast_sub(x[interval], x[interval-2]));
    result = nx500_contrast_fdiv(result, first);
    result = nx500_contrast_fmul(result, distance);
    result = nx500_contrast_fadd(result, ordinate);
    first = nx500_contrast_fsub(0.5f, fraction);
    first = nx500_contrast_fmul(result, first);
    result = nx500_contrast_i2f(nx500_contrast_sub(y[interval], y[previous]));
    result = nx500_contrast_fdiv(result, width);
    result = nx500_contrast_fmul(result, distance);
    result = nx500_contrast_fadd(ordinate, result);
    result = nx500_contrast_fmul(result, 0.5f);
    first = nx500_contrast_fadd(first, result);
    second = nx500_contrast_fmul(slope, distance);
    result = nx500_contrast_i2f(y[interval]);
    slope = nx500_contrast_fmul(width, slope);
    result = nx500_contrast_fsub(result, slope);
    result = nx500_contrast_fadd(second, result);
    result = nx500_contrast_fmul(result, fraction);
    result = nx500_contrast_fadd(first, result);
    result = nx500_contrast_fadd(result, 0.5f);
    return nx500_contrast_f2iz(result);
}

#endif
