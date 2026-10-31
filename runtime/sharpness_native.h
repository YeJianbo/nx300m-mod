/* Include once in the interposing translation unit. The original consumers
 * read these preemptible ELF data symbols only inside their SetParam methods.
 * A single lock protects both methods, including the complete original call.
 */
#ifndef NX_SHARPNESS_NATIVE_H
#define NX_SHARPNESS_NATIVE_H

typedef unsigned int (*nx_sharpness_native_fn)(void *, unsigned int);
enum {
    NX_SHARPNESS_NATIVE_OK = 0,
    NX_SHARPNESS_NATIVE_MISSING_ORIGINAL = 1,
    NX_SHARPNESS_NATIVE_TABLE_CHANGED = 2
};

unsigned int nx_sharpness_capture_table[9]
    __asm__("_ZN11CIpcsEdgeD412m_iSharpnessE") = {160,180,202,228,256,288,324,364,410};
unsigned int nx_sharpness_preview_table[9]
    __asm__("_ZN11CIpcmEdgeD412m_iSharpnessE") = {160,180,202,228,256,288,324,364,410};
static const unsigned int nx_sharpness_original_table[9] = {160,180,202,228,256,288,324,364,410};
static volatile int nx_sharpness_native_lock;
/* Recursive entry can occur through a callback on the same thread. Its local
 * saved table is restored before returning to the outer original consumer. */
static __thread unsigned int nx_sharpness_native_depth;

static unsigned int nx_sharpness_native_run(void *self, unsigned int index,
        int is_capture, unsigned int factor, int enabled,
        nx_sharpness_native_fn original, int *failure) {
    unsigned int saved[9], result;
    unsigned int *table = is_capture ? nx_sharpness_capture_table : nx_sharpness_preview_table;
    unsigned int i;
    int outer = nx_sharpness_native_depth == 0;
    if (failure) *failure = NX_SHARPNESS_NATIVE_OK;
    if (!original) {
        if (failure) *failure = NX_SHARPNESS_NATIVE_MISSING_ORIGINAL;
        return 0;
    }
    if (outer) {
        while (__sync_lock_test_and_set(&nx_sharpness_native_lock, 1)) { }
        for (i = 0; i < 9; ++i) {
            if (nx_sharpness_capture_table[i] != nx_sharpness_original_table[i] ||
                nx_sharpness_preview_table[i] != nx_sharpness_original_table[i]) {
                if (failure) *failure = NX_SHARPNESS_NATIVE_TABLE_CHANGED;
                __sync_lock_release(&nx_sharpness_native_lock);
                return 0;
            }
        }
    }
    ++nx_sharpness_native_depth;
    for (i = 0; i < 9; ++i) {
        saved[i] = table[i];
        table[i] = enabled ? factor : nx_sharpness_original_table[i];
    }
    __sync_synchronize();
    result = original(self, index);
    for (i = 0; i < 9; ++i) table[i] = saved[i];
    __sync_synchronize();
    --nx_sharpness_native_depth;
    if (outer) __sync_lock_release(&nx_sharpness_native_lock);
    return result;
}
#endif
