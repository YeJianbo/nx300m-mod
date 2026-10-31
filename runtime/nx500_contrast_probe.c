#include <stdio.h>
#ifdef VERIFY_PORT
#include "nx500_contrast_math.h"
#endif
extern int nx500_original_interpolate(const int *, const int *, int, int);
#ifdef COMPARE_TARGET
extern int nx300_target_interpolate(const int *, const int *, int, int)
    __asm__("_ZN8CIpcMath20GetInterpolatedValueEPKiS1_ii");
#endif

int main(void) {
    int count, query, x[32], y[32];
    while (scanf("%d %d", &count, &query) == 2) {
        if (count < 3 || count > 32) return 2;
        for (int i = 0; i < count; ++i) if (scanf("%d", x+i) != 1) return 2;
        for (int i = 0; i < count; ++i) if (scanf("%d", y+i) != 1) return 2;
        int original = nx500_original_interpolate(x, y, count, query);
#ifdef VERIFY_PORT
        int port = nx500_interpolate(x, y, count, query);
        if (port != original) {
            fprintf(stderr,"Mismatch query=%d count=%d original=%d port=%d\n",query,count,original,port);
            return 4;
        }
#endif
#ifdef COMPARE_TARGET
        int target = nx300_target_interpolate(x, y, count, query);
        if (target != original) {
            fprintf(stderr,"Target mismatch query=%d count=%d source=%d target=%d\n",query,count,original,target);
            return 5;
        }
#endif
        printf("%d\n", original);
    }
    return ferror(stdin) ? 3 : 0;
}
