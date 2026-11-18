#ifndef NX_LUT_TABLE_CACHE_H
#define NX_LUT_TABLE_CACHE_H
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
/* LOOK-CACHE: a bounded session cache for the original 4380-byte LUT tables.
 *
 * Browsing A -> B -> A re-reads the same small files from the card for every
 * compose and every reference refresh. Four raw tables are ~17 KiB, so four
 * entries cover the "recent 4 looks" window the task asks for without copying
 * reference images.
 *
 * Freshness is decided by file identity, never by name alone:
 *   - the slot records dev/ino/size/mtime(sec+nsec)/ctime(sec+nsec);
 *   - the entry is re-validated with stat() on every lookup, so an external
 *     App/CLI rewrite, a same-size replacement, or a rename-over invalidates
 *     it immediately;
 *   - the read path re-checks fstat() after opening, so a swap between the
 *     stat and the read cannot be served from either slot;
 *   - a missing or unreadable file drops the entry and returns the error: the
 *     cache never reports a stale table as valid.
 *
 * Only raw source tables are cached here. Drafts and saved presets live in
 * their own stores, so evicting a cache slot can neither save nor discard a
 * draft, and the composed tune output keeps its existing byte-for-byte
 * verification in nx_tune_cache(). */
#ifndef NX_TABLE_CACHE_SLOTS
#define NX_TABLE_CACHE_SLOTS 4
#endif
#define NX_TABLE_CACHE_BYTES 4380
struct nx_table_cache_slot {
    int used;
    char path[512];
    dev_t dev; ino_t ino;
    long long size;
    long long mtime_s, mtime_ns, ctime_s, ctime_ns;
    unsigned long long last_use, generation;
    unsigned char data[NX_TABLE_CACHE_BYTES];
};
static struct nx_table_cache_slot nx_table_cache[NX_TABLE_CACHE_SLOTS];
static unsigned long long nx_table_cache_clock;
static unsigned int nx_table_cache_hits, nx_table_cache_misses, nx_table_cache_reads,
    nx_table_cache_invalidations, nx_table_cache_evictions, nx_table_cache_errors;
/* The camera rootfs carries glibc 2.13, which exports the versioned metadata
 * calls only (every other mod source that checks a path goes through
 * __xstat64 as well). The host fixture links a current glibc, where plain
 * stat()/fstat() exist, so the wrapper picks the matching ABI. */
#if defined(__arm__) || defined(NX_TABLE_CACHE_STAT64)
extern int __xstat64(int, const char *, struct stat64 *);
extern int __fxstat64(int, int, struct stat64 *);
typedef struct stat64 nx_table_stat_t;
static inline int nx_table_stat(const char *path, nx_table_stat_t *st) { return __xstat64(3, path, st); }
static inline int nx_table_fstat(int fd, nx_table_stat_t *st) { return __fxstat64(3, fd, st); }
#else
typedef struct stat nx_table_stat_t;
static inline int nx_table_stat(const char *path, nx_table_stat_t *st) { return stat(path, st); }
static inline int nx_table_fstat(int fd, nx_table_stat_t *st) { return fstat(fd, st); }
#endif
static inline int nx_table_identity_equal(const struct nx_table_cache_slot *s, const nx_table_stat_t *st) {
    return s->dev == (dev_t)st->st_dev && s->ino == (ino_t)st->st_ino && s->size == (long long)st->st_size &&
        s->mtime_s == (long long)st->st_mtim.tv_sec && s->mtime_ns == (long long)st->st_mtim.tv_nsec &&
        s->ctime_s == (long long)st->st_ctim.tv_sec && s->ctime_ns == (long long)st->st_ctim.tv_nsec;
}
/* Compares two metadata snapshots of the same open file. Used to bind the bytes
 * actually read to one inode state: the snapshot taken when the descriptor was
 * opened, and the one taken after the last read. This is metadata equality, not
 * an atomic content snapshot; a writer that rewrites the file inside one
 * filesystem timestamp tick with the same size and inode is still invisible
 * here, which is why writers that mutate published tables in place remain a
 * separate contract (see the note above nx_table_cache_invalidate). */
static inline int nx_table_meta_equal(const nx_table_stat_t *a, const nx_table_stat_t *b) {
    return a->st_dev == b->st_dev && a->st_ino == b->st_ino &&
        (long long)a->st_size == (long long)b->st_size &&
        a->st_mtim.tv_sec == b->st_mtim.tv_sec && a->st_mtim.tv_nsec == b->st_mtim.tv_nsec &&
        a->st_ctim.tv_sec == b->st_ctim.tv_sec && a->st_ctim.tv_nsec == b->st_ctim.tv_nsec;
}
static inline void nx_table_record_identity(struct nx_table_cache_slot *s, const char *path, const nx_table_stat_t *st) {
    snprintf(s->path, sizeof s->path, "%s", path);
    s->dev = (dev_t)st->st_dev; s->ino = (ino_t)st->st_ino; s->size = (long long)st->st_size;
    s->mtime_s = (long long)st->st_mtim.tv_sec; s->mtime_ns = (long long)st->st_mtim.tv_nsec;
    s->ctime_s = (long long)st->st_ctim.tv_sec; s->ctime_ns = (long long)st->st_ctim.tv_nsec;
    s->used = 1;
}
/* Explicit drop, for a writer that mutates a published table in place. Nothing
 * in this tree calls it today: the in-tree composer publishes composed tables
 * into a content-addressed directory (looks/nx-tune-<hash>/) via mkdir+rename,
 * so those paths are immutable once visible and identity checking is enough.
 * A writer that overwrites looks/<id>/table.d4lut in place, inside one
 * filesystem timestamp tick and with an unchanged size and inode, is invisible
 * to the identity check and must call this after writing. Until such a writer
 * does, the cache contract covers: external rename/replace, size change, any
 * mtime/ctime change, and a change observed between open and the last read. */
static inline void nx_table_cache_invalidate(const char *path) {
    if (!path) return;
    for (int i = 0; i < NX_TABLE_CACHE_SLOTS; i++)
        if (nx_table_cache[i].used && !strcmp(nx_table_cache[i].path, path)) {
            nx_table_cache[i].used = 0;
            nx_table_cache_invalidations++;
        }
}
/* Returns 0 on a served table (hit or fresh read), negative errno-style codes
 * on failure: -ENOENT missing, -EIO unreadable/partial, -EINVAL wrong size. */
static int nx_table_cache_load(const char *path, void *out, size_t size) {
    if (!path || !out || size != NX_TABLE_CACHE_BYTES) return -EINVAL;
    nx_table_stat_t st;
    if (nx_table_stat(path, &st) || !S_ISREG(st.st_mode) || (long long)st.st_size != (long long)size) {
        for (int i = 0; i < NX_TABLE_CACHE_SLOTS; i++)
            if (nx_table_cache[i].used && !strcmp(nx_table_cache[i].path, path))
                nx_table_cache[i].used = 0, nx_table_cache_invalidations++;
        nx_table_cache_errors++;
        return -ENOENT;
    }
    /* The whole slot array is scanned before a victim is chosen. Stopping at the
     * first empty slot would miss a matching entry stored later in the array,
     * re-read the file, and then store a second slot for the same path. */
    int stale = -1, empty = -1, lru = -1;
    unsigned long long oldest = ~0ULL;
    for (int i = 0; i < NX_TABLE_CACHE_SLOTS; i++) {
        struct nx_table_cache_slot *s = &nx_table_cache[i];
        if (!s->used) { if (empty < 0) empty = i; continue; }
        if (!strcmp(s->path, path)) {
            if (nx_table_identity_equal(s, &st)) {
                memcpy(out, s->data, size);
                s->last_use = ++nx_table_cache_clock;
                nx_table_cache_hits++;
                return 0;
            }
            /* Same path, different file: the cached bytes are no longer this
             * path's content, so drop them instead of shadowing the new file. */
            s->used = 0;
            nx_table_cache_invalidations++;
            if (stale < 0) stale = i;
            continue;
        }
        if (s->last_use < oldest) { oldest = s->last_use; lru = i; }
    }
    int victim = stale >= 0 ? stale : (empty >= 0 ? empty : lru);
    if (victim < 0) { nx_table_cache_errors++; return -EIO; }
    int fd = open(path, O_RDONLY | O_NOFOLLOW);
    if (fd < 0) { nx_table_cache_errors++; return -ENOENT; }
    /* Bound the read to one inode state: the descriptor must still be the file
     * the path named, and the file must not have changed while it was read. */
    nx_table_stat_t opened_at, closed_at;
    if (nx_table_fstat(fd, &opened_at) || !nx_table_meta_equal(&opened_at, &st)) {
        close(fd);
        nx_table_cache_invalidations++;
        nx_table_cache_errors++;
        return -EIO;
    }
    unsigned char buffer[NX_TABLE_CACHE_BYTES];
    size_t got = 0;
    while (got < size) {
        ssize_t n = read(fd, buffer + got, size - got);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) break;
        got += (size_t)n;
    }
    int extra = 0;
    if (got == size) { unsigned char probe; ssize_t n = read(fd, &probe, 1); extra = n != 0; }
    /* Re-checked before close, after the bytes are in hand. A file replaced,
     * truncated or rewritten during the read is rejected: the buffer is dropped
     * before it can reach the caller or a slot. */
    int changed = nx_table_fstat(fd, &closed_at) || !nx_table_meta_equal(&closed_at, &opened_at);
    close(fd);
    nx_table_cache_reads++;
    if (changed) {
        nx_table_cache_invalidations++;
    }
    if (got != size || extra || changed) {
        nx_table_cache_errors++;
        return -EIO;
    }
    struct nx_table_cache_slot *s = &nx_table_cache[victim];
    if (s->used) nx_table_cache_evictions++;
    /* One slot per path: a duplicate left by any earlier path is dropped here. */
    for (int i = 0; i < NX_TABLE_CACHE_SLOTS; i++)
        if (i != victim && nx_table_cache[i].used && !strcmp(nx_table_cache[i].path, path)) {
            nx_table_cache[i].used = 0;
            nx_table_cache_invalidations++;
        }
    memcpy(s->data, buffer, size);
    nx_table_record_identity(s, path, &closed_at);
    s->generation = ++nx_table_cache_clock;
    s->last_use = s->generation;
    memcpy(out, buffer, size);
    nx_table_cache_misses++;
    return 0;
}
static inline void nx_table_cache_summary(const char *where) {
    fprintf(stderr, "LUT_TABLE_CACHE %s slots=%d hits=%u misses=%u reads=%u invalidations=%u evictions=%u errors=%u\n",
            where, NX_TABLE_CACHE_SLOTS, nx_table_cache_hits, nx_table_cache_misses,
            nx_table_cache_reads, nx_table_cache_invalidations, nx_table_cache_evictions,
            nx_table_cache_errors);
}
#endif
