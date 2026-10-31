/* NX300M 1.15 passive capture boundaries and read-only Ecore snapshots.
 * Lifecycle observations do not associate a capture request, confirm Close,
 * or establish that a captured photo was saved. */
extern void *dlsym(void *, const char *);
extern int *__errno_location(void), getpid(void), pipe(int *), close(int);
extern int read(int, void *, unsigned int), write(int, const void *, unsigned int);
extern int access(const char *, int), open(const char *, int, ...);
extern long long lseek64(int, long long, int);
extern void *fopen(const char *, const char *);
extern int fprintf(void *, const char *, ...), fputc(int, void *), fclose(void *), ferror(void *);
extern int snprintf(char *, unsigned int, const char *, ...), rename(const char *, const char *), unlink(const char *);
extern char *mkdtemp(char *);
struct instant { long seconds, nanoseconds; };
extern int clock_gettime(int, struct instant *);
typedef unsigned int u32;
struct library_info { const char *path; void *base; const char *symbol; void *address; };
extern int dladdr(const void *, struct library_info *);
enum { EVENT_LIMIT = 128, PATH_BYTES = 52 };
static volatile int recorder_pid, recorder_lock;
static int directory_pid;
static char directory[128];
static u32 sequence;
static unsigned long long monotonic_ns(void) {
    struct instant value;
    if (clock_gettime(1, &value)) return 0;
    return (unsigned long long)value.seconds*1000000000ULL+value.nanoseconds;
}

static void json_string(void *file, const char *value) {
    fputc('"', file);
    if (value) for (; *value; ++value) {
        unsigned char c = (unsigned char)*value;
        if (c == '"' || c == '\\') { fputc('\\', file); fputc(c, file); }
        else if (c < 32 || c >= 127) fprintf(file, "\\u%04x", (u32)c);
        else fputc(c, file);
    }
    fputc('"', file);
}
static int claim(void) {
    int pid = getpid(), previous = __sync_val_compare_and_swap(&recorder_pid, 0, pid);
    if (previous && previous != pid) { recorder_lock = 0; recorder_pid = pid; }
    return !__sync_lock_test_and_set(&recorder_lock, 1);
}
static void finish_file(void *file, const char *temporary, const char *target) {
    int failed = ferror(file), closed = fclose(file);
    if (failed || closed || rename(temporary, target)) unlink(temporary);
}
static int prepare(void) {
    int pid = getpid();
    if (directory_pid == pid) return directory[0] != 0;
    directory_pid = pid; sequence = 0;
    snprintf(directory, sizeof directory, "/var/run/nx300m-capture-observer-%d-XXXXXX", pid);
    if (!mkdtemp(directory)) { directory[0] = 0; return 0; }
    char target[180], temporary[184];
    snprintf(target, sizeof target, "%s/ready.json", directory);
    snprintf(temporary, sizeof temporary, "%s.tmp", target);
    void *file = fopen(temporary, "w");
    if (file) {
        fprintf(file, "{\"guest_pid\":%d,\"sequence\":0,\"kind\":\"observer-ready\","
                "\"observation_only\":true,\"completion_event\":false,\"event_limit\":%u}\n", pid, EVENT_LIMIT);
        finish_file(file, temporary, target);
    }
    return 1;
}
static u32 reserve(void) {
    if (!claim()) return 0;
    u32 result = prepare() && sequence < EVENT_LIMIT ? ++sequence : 0;
    __sync_lock_release(&recorder_lock);
    return result;
}
const char *nx_capture_observer_directory(void) { return directory_pid == getpid() ? directory : ""; }
__attribute__((constructor)) static void initialize(void) {
    int saved = *__errno_location();
    if (claim()) { prepare(); __sync_lock_release(&recorder_lock); }
    *__errno_location() = saved;
}

/* Private pipe implements a bounded, fault-reporting copy without dereferencing
 * an optional camera object. Invalid observer input cannot suppress forwarding. */
static int copy_read(void *out, u32 address, u32 bytes) {
    if (!address || !bytes || bytes > 64 || address > ~0U-bytes) return 0;
    int fds[2];
    if (pipe(fds)) return 0;
    int written = write(fds[1], (const void *)address, bytes);
    int copied = written == (int)bytes ? read(fds[0], out, bytes) : -1;
    close(fds[0]); close(fds[1]);
    return copied == (int)bytes;
}
#include "capture_status.h"
struct file_observation {
    int attempted, access_result, access_errno, read_open, open_errno, size_known, seek_errno;
    long long size;
};
struct phase {
    int stop_read, image_read, descriptor_read, path_read, path_terminated;
    u32 stop, image, descriptor;
    char path[PATH_BYTES+1];
    struct file_observation file;
};
static void observe_file(const char *path, struct file_observation *value) {
    value->attempted = 1;
    value->access_result = access(path, 0);
    value->access_errno = value->access_result ? *__errno_location() : 0;
    /* Do not block on an unexpected FIFO or follow an unexpected symlink.
     * Opening and seeking read-only observe size; they prove no write end. */
    int fd = open(path, 04000|02000000|0400000);
    if (fd < 0) { value->open_errno = *__errno_location(); return; }
    value->read_open = 1;
    value->size = lseek64(fd, 0, 2);
    if (value->size < 0) value->seek_errno = *__errno_location();
    else value->size_known = 1;
    close(fd);
}
static void observe_phase(void *self, struct phase *value) {
    u32 address = (u32)self;
    if (address > ~0U-0x74U) return;
    value->stop_read = copy_read(&value->stop, address+0x70, 4);
    value->image_read = copy_read(&value->image, address+0x64, 4);
    if (!value->image_read || value->image > ~0U-0x30U) return;
    value->descriptor_read = copy_read(&value->descriptor, value->image+0x2c, 4);
    if (!value->descriptor_read || value->descriptor > ~0U-(12+PATH_BYTES)) return;
    value->path_read = copy_read(value->path, value->descriptor+12, PATH_BYTES);
    if (!value->path_read) return;
    value->path[PATH_BYTES] = 0;
    for (u32 i = 0; i < PATH_BYTES; ++i) if (!value->path[i]) { value->path_terminated = 1; break; }
    if (value->path_terminated && value->path[0]) observe_file(value->path, &value->file);
}
static void print_phase(void *file, const struct phase *value) {
    fprintf(file, "{\"stop_read\":%s,\"stop_raw\":", value->stop_read ? "true" : "false");
    if (value->stop_read) fprintf(file, "%u", value->stop); else fprintf(file, "null");
    fprintf(file, ",\"image_pointer_read\":%s,\"image_pointer\":\"%08x\","
            "\"descriptor_pointer_read\":%s,\"descriptor_pointer\":\"%08x\","
            "\"path_read\":%s,\"path_nul_terminated\":%s,\"path_bytes_latin1\":",
            value->image_read ? "true" : "false", value->image,
            value->descriptor_read ? "true" : "false", value->descriptor,
            value->path_read ? "true" : "false", value->path_terminated ? "true" : "false");
    if (value->path_read) json_string(file, value->path); else fprintf(file, "null");
    const struct file_observation *f = &value->file;
    fprintf(file, ",\"file_metadata_observation\":{\"attempted\":%s,\"exists\":",
            f->attempted ? "true" : "false");
    if (!f->attempted || (f->access_result && f->access_errno != 2 && f->access_errno != 20)) fprintf(file, "null");
    else fprintf(file, "%s", f->access_result ? "false" : "true");
    fprintf(file, ",\"access_errno\":%d,\"read_open\":%s,\"open_errno\":%d,\"size_bytes\":",
            f->access_errno, f->read_open ? "true" : "false", f->open_errno);
    if (f->size_known) fprintf(file, "%lld", f->size); else fprintf(file, "null");
    fprintf(file, ",\"seek_errno\":%d,\"metadata_proves_write_completion\":false}}", f->seek_errno);
}
#include "capture_save.h"
static void publish(u32 event, const char *kind, void *self, void *next, void *caller,
                    int result, int incoming_errno, int outgoing_errno,
                    int drive, int argument, const struct phase *before, const struct phase *after,
                    unsigned long long started, unsigned long long returned,
                    const struct capture_save_scope *save) {
    char target[180], temporary[184];
    snprintf(target, sizeof target, "%s/event-%u.json", directory, event);
    snprintf(temporary, sizeof temporary, "%s.tmp", target);
    void *file = fopen(temporary, "w");
    if (!file) return;
    struct library_info provider = {0}, origin = {0};
    dladdr(next, &provider); dladdr(caller, &origin);
    fprintf(file, "{\"guest_pid\":%d,\"sequence\":%u,\"kind\":", getpid(), event);
    json_string(file, kind);
    fprintf(file, ",\"observation_only\":true,\"request_associated\":false,"
            "\"sequence_order\":\"hook-entry\",\"monotonic_before_original_ns\":%llu,"
            "\"monotonic_after_original_ns\":%llu,\"monotonic_zero_means_unavailable\":true,"
            "\"pointer_scope\":\"current-process observation; not a task identifier\","
            "\"self_pointer\":\"%p\",\"next_provider\":", started, returned, self);
    json_string(file, provider.path);
    fprintf(file, ",\"next_relative_offset\":\"%#x\",\"caller_provider\":", (u32)next-(u32)provider.base);
    json_string(file, origin.path);
    fprintf(file, ",\"caller_relative_offset\":\"%#x\",\"return_code_raw\":%d,"
            "\"errno_before_original\":%d,\"errno_after_original\":%d,"
            "\"capture_may_drop_on_contention\":true,\"event_limit\":%u",
            (u32)caller-(u32)origin.base, result, incoming_errno, outgoing_errno, EVENT_LIMIT);
    if (before) {
        fprintf(file, ",\"path_bound_bytes\":%u,\"path_string_encoding\":"
                "\"byte-values-u00xx; recover via latin1 bytes\",\"before\":", PATH_BYTES); print_phase(file, before);
        fprintf(file, ",\"after\":"); print_phase(file, after);
    } else fprintf(file, ",\"drive_id_raw\":%d,\"completion_arg_raw\":%d", drive, argument);
    if (save) capture_save_print(file, save);
    fprintf(file, "}\n");
    finish_file(file, temporary, target);
}

int observe_backend(void *, int, int) __asm__("_ZN10CDriveMode17BackEndCompletionEii");
int observe_backend(void *self, int drive, int argument) {
    int incoming_errno = *__errno_location();
    int (*next)(void *, int, int) = dlsym((void *)-1, "_ZN10CDriveMode17BackEndCompletionEii");
    if (!next) { *__errno_location() = 38; return -1; }
    u32 event = reserve();
    unsigned long long started = event ? monotonic_ns() : 0;
    *__errno_location() = incoming_errno;
    int result = next(self, drive, argument), outgoing_errno = *__errno_location();
    unsigned long long returned = event ? monotonic_ns() : 0;
    if (event) publish(event, "backend-completion", self, (void *)next, __builtin_return_address(0),
                       result, incoming_errno, outgoing_errno, drive, argument, (void *)0, (void *)0, started, returned, (void *)0);
    *__errno_location() = outgoing_errno;
    return result;
}
unsigned int observe_write(void *) __asm__("_ZN14DscFileHandler11WriteChunksEv");
unsigned int observe_write(void *self) {
    int incoming_errno = *__errno_location();
    unsigned int (*next)(void *) = dlsym((void *)-1, "_ZN14DscFileHandler11WriteChunksEv");
    if (!next) { *__errno_location() = 38; return 0; }
    struct phase before = {0}, after = {0};
    u32 event = reserve();
    if (event) observe_phase(self, &before);
    struct capture_save_scope save = {0};
    capture_save_begin(&save, self, (void *)next, &before, event != 0);
    unsigned long long started = event ? monotonic_ns() : 0;
    *__errno_location() = incoming_errno;
    unsigned int result = next(self);
    int outgoing_errno = *__errno_location();
    unsigned long long returned = event ? monotonic_ns() : 0;
    if (event) {
        observe_phase(self, &after);
    }
    capture_save_end(&save, &before, &after, result);
    if (event) {
        publish(event, "write-chunks", self, (void *)next, __builtin_return_address(0),
                (int)result, incoming_errno, outgoing_errno, 0, 0, &before, &after, started, returned, &save);
    }
    *__errno_location() = outgoing_errno;
    return result;
}

void ecore_main_loop_begin(void) {
    int incoming_errno = *__errno_location();
    void (*begin)(void) = dlsym((void *)-1, "ecore_main_loop_begin");
    capture_status_attach();
    *__errno_location() = incoming_errno;
    if (begin) begin();
}
