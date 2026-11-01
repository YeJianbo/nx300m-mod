/* DisplayAdjust observation only. No pixel writes, files, allocations or ISP API. */
#include "lut_preview_blob.h"
__attribute__((visibility("hidden"))) struct nx_preview_config nx_preview_config;
__attribute__((visibility("hidden"))) struct nx_preview_stats nx_preview_stats;

__attribute__((visibility("hidden")))
int nx_preview_execute(void *self, void *image, void *data, unsigned callback)
{
    struct nx_preview_stats *s = &nx_preview_stats;
    __sync_fetch_and_add(&s->inflight, 1);
    __sync_fetch_and_add(&s->calls, 1);
    int (*original)(void *, void *, void *, unsigned) = (void *)nx_preview_config.original;
    int result = original(self, image, data, callback);
    if (nx_preview_config.enabled && __sync_bool_compare_and_swap(&s->sample_lock, 0, 1)) {
        ++s->sample_seq;
        __sync_synchronize();
        s->callback = callback;
        s->image = (unsigned)image;
        s->result = (unsigned)result;
        /* These are CCapImage metadata words, never its pixel buffers. */
        for (unsigned i = 0; i < 11; ++i) s->geometry[i] = image ? ((unsigned *)image)[i] : 0;
        ++s->observations;
        __sync_synchronize();
        ++s->sample_seq;
        __sync_lock_release(&s->sample_lock);
    }
    __sync_fetch_and_sub(&s->inflight, 1);
    return result;
}
