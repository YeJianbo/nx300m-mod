/* Experimental single-slot RAM observer. Production still-LUT loader is unchanged. */
#define _GNU_SOURCE
#define _LARGEFILE64_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <dirent.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <elf.h>
#include <errno.h>
#include <sys/ptrace.h>
#include <sys/wait.h>
#include <sys/vfs.h>
#include <signal.h>
#include <time.h>
#include "lut_preview_blob.h"
#include "lut_preview_stock.h"

extern int __fxstat64(int, int, struct stat64 *);
extern unsigned long long old_strtoull(const char *,char **,int) __asm__("strtoull");
extern unsigned long old_strtoul(const char *,char **,int) __asm__("strtoul");
extern long old_strtol(const char *,char **,int) __asm__("strtol");
extern int old_sscanf(const char *,const char *,...) __asm__("sscanf");
#define strtoull old_strtoull
#define strtoul old_strtoul
#define strtol old_strtol
#define sscanf old_sscanf
#include "live_loader_core.h"

struct preview_state {
    uint32_t magic, abi, pid, build;
    unsigned long long start;
    uint32_t capture, region, length, entry, config, stats;
};
struct target {
    struct image capture, libc;
    int mem;
    unsigned long long start;
    uint32_t slot, original, value;
};
static int state_path(unsigned pid, char *path, unsigned size) {
    struct statfs fs;
    if (statfs("/var/run", &fs) ||
        ((unsigned long)fs.f_type != 0x01021994UL && (unsigned long)fs.f_type != 0x858458f6UL)) return 0;
    return snprintf(path, size, "/var/run/nx300m-lut-preview-%u.state", pid) > 0;
}
static int state_read(const char *path, unsigned pid, struct preview_state *s) {
    int fd = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) return 0;
    struct stat64 st;
    int ok = !__fxstat64(3, fd, &st) && S_ISREG(st.st_mode) && st.st_size == sizeof *s &&
             read(fd, s, sizeof *s) == sizeof *s;
    close(fd);
    return ok && s->magic == NX_PREVIEW_MAGIC && s->abi == NX_PREVIEW_ABI && s->pid == pid;
}
static int state_write(const char *path, const struct preview_state *s) {
    int fd = open(path, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
    if (fd < 0) return 0;
    int ok = write(fd, s, sizeof *s) == sizeof *s;
    int rc = close(fd);
    if (!ok || rc) { unlink(path); return 0; }
    return 1;
}
static void target_close(struct target *t) {
    if (t->mem >= 0) close(t->mem);
    if (t->capture.fd >= 0) close(t->capture.fd);
    if (t->libc.fd >= 0) close(t->libc.fd);
}
static int target_open(unsigned pid, int fixture, struct target *t) {
    memset(t, 0, sizeof *t);
    t->mem = t->capture.fd = t->libc.fd = -1;
    char path[96], exe[512];
    snprintf(path, sizeof path, "/proc/%u/exe", pid);
    ssize_t n = readlink(path, exe, sizeof exe - 1);
    if (n < 0) return 0;
    exe[n] = 0;
    if (strcmp(base(exe), fixture ? "lut_preview_fixture.arm" : "di-camera-app-nx300")) return 0;
    if (!fixture) {
        int fd = open(path, O_RDONLY | O_CLOEXEC);
        int ok = fd >= 0 && file_hash(fd, STOCK_APP_SIZE, STOCK_APP_SHA);
        if (fd >= 0) close(fd);
        if (!ok) return 0;
    }
    struct thread thread;
    if (!task_identity(pid, pid, &thread) || !load_maps(pid)) return 0;
    t->start = thread.start;
    if (!image_open(pid, "libcapture-fw-slpcam-nx300.so", STOCK_CAP_SIZE, STOCK_CAP_SHA, &t->capture)) return 0;
    if (!image_open(pid, "libc-2.13.so", STOCK_LIBC_SIZE, STOCK_LIBC_SHA, &t->libc) &&
        !image_open(pid, "libc.so.6", STOCK_LIBC_SIZE, STOCK_LIBC_SHA, &t->libc)) return 0;
    snprintf(path, sizeof path, "/proc/%u/mem", pid);
    t->mem = open(path, O_RDONLY | O_CLOEXEC);
    if (t->mem < 0 || !code_check(t->mem, t->capture.bias + STOCK_PREVIEW_EXECUTE, stock_preview_execute) ||
        !code_check(t->mem, t->libc.bias + STOCK_SVC, stock_svc)) return 0;
    t->slot = t->capture.bias + STOCK_PREVIEW_SLOT;
    t->original = t->capture.bias + STOCK_PREVIEW_EXECUTE;
    struct mapping *slot = cover(t->slot, 4, 0, 0);
    return slot && slot->inode == t->capture.first->inode && slot->perm[2] != 'x' &&
           read_exact(t->mem, &t->value, 4, t->slot);
}
static int read_blob(const char *path, unsigned char **bytes, struct nx_preview_header *h) {
    int fd = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) return 0;
    struct stat64 st;
    int ok = !__fxstat64(3, fd, &st) && S_ISREG(st.st_mode) && st.st_size >= 4096 && st.st_size <= 65536 &&
             read_exact(fd, h, sizeof *h, 0);
    if (ok) ok = h->magic == NX_PREVIEW_MAGIC && h->abi == NX_PREVIEW_ABI && h->build == NX_PREVIEW_BUILD &&
        h->blob_size == (unsigned)st.st_size && h->code_size == h->data_offset && h->code_size >= 4096 &&
        h->code_size % 4096 == 0 && h->code_size < h->blob_size && h->blob_size % 4096 == 0 &&
        h->data_size == h->blob_size - h->data_offset && h->entry >= 64 && h->entry < h->code_size && h->entry % 4 == 0 &&
        h->config_size == sizeof(struct nx_preview_config) && h->config_offset >= h->data_offset && h->config_offset % 4 == 0 &&
        h->config_offset <= h->blob_size - h->config_size && h->stats_size == sizeof(struct nx_preview_stats) &&
        h->stats_offset >= h->data_offset && h->stats_offset % 4 == 0 &&
        h->stats_offset <= h->blob_size - h->stats_size &&
        (h->stats_offset + h->stats_size <= h->config_offset ||
         h->config_offset + h->config_size <= h->stats_offset);
    unsigned char *p = ok ? malloc(h->blob_size) : NULL;
    if (ok) ok = p && read_exact(fd, p, h->blob_size, 0);
    close(fd);
    if (!ok) { free(p); return 0; }
    *bytes = p;
    return 1;
}
static int resident(const struct target *t, const struct preview_state *s,
                    const struct nx_preview_header *h, const unsigned char *blob,
                    struct nx_preview_config *cfg) {
    if (s->start != t->start || s->capture != t->capture.bias || s->build != h->build ||
        !s->region || s->region % 4096 || s->region > UINT32_MAX - h->blob_size || s->length != h->blob_size ||
        s->entry != s->region + h->entry || s->config != s->region + h->config_offset ||
        s->stats != s->region + h->stats_offset) return 0;
    struct mapping *rx = cover(s->region, h->code_size, 0, 1);
    struct mapping *rw = cover(s->region + h->data_offset, h->data_size, 1, 0);
    if (!rx || !rw || rx->inode || rw->inode || strcmp(rx->perm, "r-xp") || strcmp(rw->perm, "rw-p")) return 0;
    unsigned char actual[4096];
    for (unsigned offset = 0; offset < h->code_size; offset += sizeof actual)
        if (!read_exact(t->mem, actual, sizeof actual, s->region + offset) ||
            memcmp(actual, blob + offset, sizeof actual)) return 0;
    return read_exact(t->mem, cfg, sizeof *cfg, s->config) && cfg->abi == NX_PREVIEW_ABI &&
           cfg->size == sizeof *cfg && cfg->original == t->original && cfg->enabled <= 1 &&
           (t->value == t->original || t->value == s->entry);
}
static int mutate(unsigned pid, const char *command, const char *blob_path, int fixture) {
    struct target t;
    struct preview_state s = {0};
    struct nx_preview_config cfg;
    struct nx_preview_header h;
    unsigned char *blob = NULL;
    char path[128];
    int rc = 3, new_state = 0, slot_attempted = 0, enabled_attempted = 0;
    uint32_t region = 0, allocated = 0, prior_slot = 0, prior_enabled = 0;
    if (!state_path(pid, path, sizeof path) || !read_blob(blob_path, &blob, &h)) {
        fprintf(stderr, "Invalid blob or non-RAM state directory; no target changed.\n"); return 3;
    }
    if (!attach_all(pid)) {
        int detached = detach_all(); free(blob);
        fprintf(stderr, "Thread admission failed; detached=%d.\n", detached); return 3;
    }
    memset(&t, 0, sizeof t); t.mem = t.capture.fd = t.libc.fd = -1;
    if (!target_open(pid, fixture, &t)) goto done;
    prior_slot = t.value;
    struct stopped *leader = NULL;
    for (unsigned i = 0; i < nstops; ++i) if (stops[i].tid == pid) leader = &stops[i];
    if (!leader) goto done;
    if (access(path, F_OK) == 0) {
        if (!state_read(path, pid, &s) || !resident(&t, &s, &h, blob, &cfg)) {
            fprintf(stderr, "Resident observer does not match this process/build; no replacement.\n"); goto done;
        }
    } else {
        if (strcmp(command, "apply") || t.value != t.original) goto done;
        cfg.abi = NX_PREVIEW_ABI; cfg.size = sizeof cfg; cfg.original = t.original; cfg.enabled = 0;
        memcpy(blob + h.config_offset, &cfg, sizeof cfg);
        uint32_t args[6] = {0, h.blob_size, 3, 0x22, 0xffffffff, 0}, result;
        if (!remote_syscall(leader, t.libc.bias + STOCK_SVC, 192, args, &region) || kernel_error(region) || region % 4096) goto done;
        allocated = h.blob_size;
        if (!load_maps(pid) || !cover(region, allocated, 1, 0) || !poke(pid, region, blob, h.blob_size)) goto done;
        uint32_t flush[6] = {region, region + h.code_size, 0, 0, 0, 0};
        if (!remote_syscall(leader, t.libc.bias + STOCK_SVC, 0xf0002, flush, &result) || kernel_error(result)) goto done;
        uint32_t protect[6] = {region, h.code_size, 5, 0, 0, 0};
        if (!remote_syscall(leader, t.libc.bias + STOCK_SVC, 125, protect, &result) || kernel_error(result)) goto done;
        if (!load_maps(pid) || !cover(region, h.code_size, 0, 1) || !cover(region + h.data_offset, h.data_size, 1, 0)) goto done;
        s.magic = NX_PREVIEW_MAGIC; s.abi = NX_PREVIEW_ABI; s.pid = pid; s.build = h.build; s.start = t.start;
        s.capture = t.capture.bias; s.region = region; s.length = h.blob_size;
        s.entry = region + h.entry; s.config = region + h.config_offset; s.stats = region + h.stats_offset;
        if (!state_write(path, &s)) goto done;
        new_state = 1;
    }
    prior_enabled = cfg.enabled;
    if ((!strcmp(command, "observe") || !strcmp(command, "pause")) && t.value != s.entry) goto done;
    uint32_t enabled = !strcmp(command, "observe") ? 1 : 0;
    if (abort_requested) goto done;
    enabled_attempted = 1;
    if (!poke(pid, s.config + 12, &enabled, 4)) goto done;
    uint32_t desired = !strcmp(command, "restore") ? t.original : s.entry, actual;
    slot_attempted = 1;
    /* PTRACE_POKEDATA updates a private data word, including RELRO COW.
     * No permission change is made to the original vtable page. */
    if (!poke(pid, t.slot, &desired, 4) || !read_exact(t.mem, &actual, 4, t.slot) || actual != desired) goto done;
    if (!read_exact(t.mem, &actual, 4, s.config + 12) || actual != enabled) goto done;
    rc = 0;
done:
    if (rc && slot_attempted) {
        uint32_t actual;
        if (!poke(pid, t.slot, &prior_slot, 4) || !read_exact(t.mem, &actual, 4, t.slot) || actual != prior_slot)
            transaction_poisoned = 1;
    }
    if (rc && enabled_attempted && !poke(pid, s.config + 12, &prior_enabled, 4)) transaction_poisoned = 1;
    if (rc && allocated && !transaction_poisoned) {
        uint32_t args[6] = {region, allocated, 0, 0, 0, 0}, result;
        struct stopped *leader = NULL;
        for (unsigned i = 0; i < nstops; ++i) if (stops[i].tid == pid) leader = &stops[i];
        if (leader && remote_syscall(leader, t.libc.bias + STOCK_SVC, 91, args, &result) && !kernel_error(result)) {
            if (new_state) unlink(path);
        } else fprintf(stderr, "Unused mapping retained; inspect RAM state.\n");
    }
    target_close(&t); free(blob);
    if (transaction_poisoned) { fprintf(stderr, "Context recovery incomplete; tracer retained.\n"); return 5; }
    if (!detach_all()) return 5;
    if (rc) fprintf(stderr, "Preview admission/transaction failed; exit=%d.\n", rc);
    else printf("{\"command\":\"%s\",\"pid\":%u,\"installed\":%s,\"observing\":%s,\"mapping_retained\":true,\"pixel_processing\":false}\n",
                command, pid, !strcmp(command, "restore") ? "false" : "true", !strcmp(command, "observe") ? "true" : "false");
    return rc;
}
static int inspect(unsigned pid, const char *blob_path, int fixture) {
    struct target t;
    if (!target_open(pid, fixture, &t)) { target_close(&t); fprintf(stderr, "Target identity/code admission failed.\n"); return 3; }
    struct preview_state s;
    struct nx_preview_header h;
    struct nx_preview_config cfg;
    unsigned char *blob = NULL;
    char path[128];
    int installed = 0, enabled = 0;
    struct nx_preview_stats stats = {0}, again;
    if (!state_path(pid, path, sizeof path)) { target_close(&t); return 3; }
    if (access(path, F_OK) == 0) {
        if (!state_read(path, pid, &s) || !read_blob(blob_path, &blob, &h) || !resident(&t, &s, &h, blob, &cfg)) {
            target_close(&t); free(blob); fprintf(stderr, "Observer ownership/code admission failed.\n"); return 3;
        }
        int stable = 0;
        for (unsigned i = 0; i < 4; ++i) {
            if (!read_exact(t.mem, &stats, sizeof stats, s.stats) || !read_exact(t.mem, &again, sizeof again, s.stats)) break;
            if (!(stats.sample_seq & 1) && stats.sample_seq == again.sample_seq && !stats.sample_lock && !again.sample_lock) { stable = 1; break; }
        }
        if (!stable) { target_close(&t); free(blob); fprintf(stderr, "Sample busy; retry status.\n"); return 4; }
        installed = t.value == s.entry; enabled = cfg.enabled;
    } else if (t.value != t.original) { target_close(&t); return 3; }
    printf("{\"pid\":%u,\"installed\":%s,\"observing\":%s,\"calls\":%u,\"observations\":%u,\"inflight\":%u,\"callback\":%u,\"result\":%u,\"geometry\":[",
           pid, installed ? "true" : "false", enabled ? "true" : "false", stats.calls, stats.observations,
           stats.inflight, stats.callback, stats.result);
    for (unsigned i = 0; i < 11; ++i) printf("%s%u", i ? "," : "", stats.geometry[i]);
    puts("],\"pixel_processing\":false,\"read_only\":true}");
    target_close(&t); free(blob); return 0;
}
static int supervise(unsigned pid, const char *command, const char *blob, int fixture) {
    char path[128];
    if (!state_path(pid, path, sizeof path)) return 3;
    snprintf(path, sizeof path, "/var/run/nx300m-preview-worker-%u.log", (unsigned)getpid());
    int fd = open(path, O_RDWR | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
    if (fd < 0) return 3;
    pid_t child = fork();
    if (child < 0) { close(fd); unlink(path); return 3; }
    if (!child) {
        dup2(fd, 1); dup2(fd, 2); close(fd);
        signal(SIGUSR1, request_abort); signal(SIGTERM, request_abort); signal(SIGINT, request_abort); signal(SIGPIPE, SIG_IGN);
        exit(mutate(pid, command, blob, fixture));
    }
    unsigned long long deadline = now_ms() + 15000;
    int status, finished = 0;
    while (now_ms() < deadline) {
        pid_t n = waitpid(child, &status, WNOHANG);
        if (n == child) { finished = 1; break; }
        if (n < 0 && errno != EINTR) break;
        struct timespec pause = {0, 10000000}; nanosleep(&pause, NULL);
    }
    if (!finished) {
        kill(child, SIGUSR1); close(fd);
        fprintf(stderr, "Recovery tracer %u remains active; log %s; do not terminate it.\n", (unsigned)child, path); return 5;
    }
    lseek(fd, 0, SEEK_SET);
    char text[4096]; ssize_t n;
    while ((n = read(fd, text, sizeof text)) > 0) fwrite(text, 1, n, stdout);
    close(fd); unlink(path);
    return WIFEXITED(status) ? WEXITSTATUS(status) : 5;
}
int main(int argc, char **argv) {
    const char *command = "status", *blob = NULL;
    unsigned pid = 0; int fixture = 0, first = 1;
    if (argc > 1 && argv[1][0] != '-') { command = argv[1]; first = 2; }
    if (strcmp(command, "status") && strcmp(command, "apply") && strcmp(command, "observe") &&
        strcmp(command, "pause") && strcmp(command, "restore")) goto usage;
    for (int i = first; i < argc; ++i) {
        if (!strcmp(argv[i], "--fixture")) { fixture = 1; continue; }
        if (i + 1 >= argc) goto usage;
        if (!strcmp(argv[i], "--pid")) { if (!decimal(argv[++i], &pid)) goto usage; }
        else if (!strcmp(argv[i], "--blob")) blob = argv[++i];
        else goto usage;
    }
    if (!pid || !blob) goto usage;
    if (!strcmp(command, "status")) return inspect(pid, blob, fixture);
    return supervise(pid, command, blob, fixture);
usage:
    fprintf(stderr, "usage: lut_preview_loader.arm [status|apply|observe|pause|restore] --pid PID --blob FILE [--fixture]\n");
    return 64;
}
