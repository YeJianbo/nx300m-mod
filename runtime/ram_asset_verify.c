/* Finite RAM staging check: compare every listed byte without per-file exec. */
#define _GNU_SOURCE
#define _LARGEFILE64_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

extern int __fxstat64(int, int, struct stat64 *);
static unsigned char source_bytes[32768], staged_bytes[32768];

static int regular(int fd) {
    struct stat64 st;
    return fd >= 0 && !__fxstat64(3, fd, &st) && S_ISREG(st.st_mode);
}
static ssize_t block(int fd, unsigned char *bytes) {
    size_t used = 0;
    while (used < sizeof source_bytes) {
        ssize_t n = read(fd, bytes + used, sizeof source_bytes - used);
        if (n < 0) { if (errno == EINTR) continue; return -1; }
        if (!n) break;
        used += (size_t)n;
    }
    return (ssize_t)used;
}
static int same(int source, int staged, const char *name) {
    int a = openat(source, name, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
    int b = openat(staged, name, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
    int ok = regular(a) && regular(b);
    while (ok) {
        ssize_t n = block(a, source_bytes), m = block(b, staged_bytes);
        if (n < 0 || n != m || (n && memcmp(source_bytes, staged_bytes, (size_t)n))) { ok = 0; break; }
        if (!n) break;
    }
    if (a >= 0) close(a);
    if (b >= 0) close(b);
    if (!ok) fprintf(stderr, "RAM asset mismatch, missing or linked: %s\n", name);
    return ok;
}
static int relative(const char *name) {
    if (!*name || *name == '/') return 0;
    const char *part = name;
    for (const char *p = name;; p++) {
        if (!*p || *p == '/') {
            size_t n = (size_t)(p - part);
            if (!n || (n == 1 && part[0] == '.') || (n == 2 && part[0] == '.' && part[1] == '.')) return 0;
            if (!*p) return 1;
            part = p + 1;
        } else if (!((*p >= 'a' && *p <= 'z') || (*p >= 'A' && *p <= 'Z') ||
                     (*p >= '0' && *p <= '9') || *p == '_' || *p == '-' || *p == '.')) return 0;
    }
}
int main(int argc, char **argv) {
    if (argc != 4 || argv[1][0] != '/' || argv[2][0] != '/' || argv[3][0] != '/') return 64;
    int source = open(argv[1], O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    int staged = open(argv[2], O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    int fd = open(argv[3], O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
    if (source < 0 || staged < 0 || !regular(fd)) return 1;
    FILE *list = fdopen(fd, "r");
    if (!list) return 1;
    char name[PATH_MAX]; unsigned count = 0; int ok = 1;
    while (fgets(name, sizeof name, list)) {
        size_t length = strlen(name);
        if (!length || name[length-1] != '\n') { ok = 0; break; }
        name[length-1] = 0;
        if (!relative(name) || !same(source, staged, name)) { ok = 0; break; }
        count++;
    }
    if (ferror(list) || !count) ok = 0;
    fclose(list);
    if (ok) ok = same(source, staged, "runtime-files.list");
    close(source); close(staged);
    return ok ? 0 : 1;
}
