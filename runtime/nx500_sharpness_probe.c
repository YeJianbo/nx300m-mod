#include <stdio.h>
#include "nx500_sharpness_math.h"
extern unsigned original_percent(unsigned, unsigned);
extern unsigned original_gain8(unsigned, unsigned);
extern unsigned original_gain7(unsigned, unsigned);
extern unsigned original_pack7(unsigned, unsigned);

int main(void) {
    unsigned factor, percent, base, old, count = 0;
    while (scanf("%u %u %u %u", &factor, &percent, &base, &old) == 4) {
        unsigned effective = original_percent(factor, percent);
        unsigned g8 = original_gain8(base, effective);
        unsigned g7 = original_gain7(base, effective);
        unsigned packed = original_pack7(old, g7);
        if (effective != nx500_sharpness_effective(factor, percent) ||
            g8 != nx500_sharpness_gain(base, effective, 8) ||
            g7 != nx500_sharpness_gain(base, effective, 7) ||
            packed != nx500_sharpness_pack7((uint8_t)old, g7)) {
            fprintf(stderr, "Mismatch case=%u factor=%u percent=%u base=%u old=%u\n",
                    count, factor, percent, base, old);
            return 4;
        }
        ++count;
    }
    printf("%u\n", count);
    return ferror(stdin) ? 3 : 0;
}
