/* Read-only NX300M 1.15 IPCS setter boundary observer. The captured argument is
 * forwarded unchanged to RTLD_NEXT, which may itself be another preload shim.
 * No sensor, pixel, selector or ISP parameter is modified by this observer.
 */
extern void *dlsym(void *, const char *);
extern void *fopen(const char *, const char *);
extern int fprintf(void *, const char *, ...);
extern int fputc(int, void *);
extern int fclose(void *);
extern int ferror(void *);
extern int rename(const char *, const char *);
extern int unlink(const char *);
extern int snprintf(char *, unsigned int, const char *, ...);
extern char *mkdtemp(char *);
extern int getpid(void);
extern int *__errno_location(void);
extern void abort(void);
extern void *stderr;
typedef struct { const char *filename; void *base; const char *symbol; void *address; } dl_info;
extern int dladdr(const void *, dl_info *);

typedef unsigned int u32;
typedef unsigned short u16;
/* Vendor ipcs_submodule_type.h:95-97: unsigned int col_mat[9]. */
struct ipcs_color_correction { u32 col_mat[9]; };
/* Vendor ipcs_rgb2ycc: selector and five scalar controls, then two matrices. */
struct ipcs_rgb2ycc { u32 sel, controls[5], chroma[4], ycc[9]; };
enum { GAMMA_WORDS = 8192, MATRIX_WORDS = 9, RING_SLOTS = 8 };
static volatile int recording;
static volatile int recording_pid;
static int directory_pid;
static char directory[128];
static u32 sequence, gamma_count, matrix_count, rgb2ycc_count;
static u16 gamma_snapshot[GAMMA_WORDS];
static u32 matrix_snapshot[MATRIX_WORDS];

static int claim_recorder(void) {
    int pid = getpid();
    int previous = __sync_val_compare_and_swap(&recording_pid, 0, pid);
    if (previous && previous != pid) {
        /* Stock launches fork. A child must not inherit a permanently busy
         * recorder lock from another thread in its parent. */
        recording = 0;
        recording_pid = pid;
    }
    return !__sync_lock_test_and_set(&recording, 1);
}

static void json_string(void *stream, const char *value) {
    fputc('"', stream);
    if (value) for (; *value; ++value) {
        unsigned char c = (unsigned char)*value;
        if (c == '"' || c == '\\') { fputc('\\', stream); fputc(c, stream); }
        else if (c < 32) fprintf(stream, "\\u%04x", (unsigned int)c);
        else fputc(c, stream);
    }
    fputc('"', stream);
}

static int prepare_directory(void) {
    int pid = getpid();
    if (directory_pid == pid && directory[0]) return 1;
    directory_pid = pid;
    directory[0] = 0;
    sequence = gamma_count = matrix_count = rgb2ycc_count = 0;
    snprintf(directory, sizeof directory, "/var/run/nx300m-isp-color-%d-XXXXXX", pid);
    if (!mkdtemp(directory)) { directory[0] = 0; return 0; }
    fprintf(stderr, "[NX ISP observer] boundary snapshots: %s (private;8 slots/setter)\n", directory);
    return 1;
}

static void publish(const char *kind, const void *source, void *caller,
                    void *next, int result, unsigned int words) {
    if (!prepare_directory()) return;
    unsigned int event = ++sequence;
    unsigned int count = words == GAMMA_WORDS ? ++gamma_count :
                         kind[0] == 'r' ? ++rgb2ycc_count : ++matrix_count;
    char path[176], temporary[180];
    snprintf(path, sizeof path, "%s/%s-%u.json", directory, kind, (count-1)%RING_SLOTS);
    snprintf(temporary, sizeof temporary, "%s.tmp", path);
    void *stream = fopen(temporary, "w");
    if (!stream) return;
    dl_info provider = {0}, origin = {0};
    dladdr(next, &provider);
    dladdr(caller, &origin);
    int written = fprintf(stream,
        "{\"guest_pid\":%d,\"sequence\":%u,\"setter_sequence\":%u,"
        "\"ring_slots\":8,\"kind\":", getpid(), event, count);
    json_string(stream, kind);
    fprintf(stream, ",\"source_pointer\":\"%p\",\"caller\":\"%p\",\"caller_library\":", source, caller);
    json_string(stream, origin.filename);
    fprintf(stream, ",\"caller_library_base\":\"%p\",\"caller_relative_offset\":\"%#x\","
                    "\"next_function\":\"%p\",\"next_provider\":",
            origin.base, (u32)caller-(u32)origin.base, next);
    json_string(stream, provider.filename);
    fprintf(stream, ",\"next_function_relative_offset\":\"%#x\",\"return_code\":",
            (u32)next-(u32)provider.base);
    if (words == GAMMA_WORDS) fprintf(stream, "null");
    else fprintf(stream, "%d", result);
    fprintf(stream,
        ",\"next_provider_returned\":true,"
        "\"capture_boundary\":\"input before RTLD_NEXT; not necessarily final hardware input\","
        "\"capture_may_drop_on_contention\":true,"
        "\"image_verified\":false,\"word_count\":%u,\"values_uint%u\":[",
        words, words == GAMMA_WORDS ? 16u : 32u);
    for (unsigned int i = 0; i < words; ++i)
        fprintf(stream, "%s%u", i ? "," : "", words == GAMMA_WORDS ?
                (unsigned int)gamma_snapshot[i] : matrix_snapshot[i]);
    if (words == MATRIX_WORDS) {
        fprintf(stream, "],\"values_int32_bit_view\":[");
        for (unsigned int i = 0; i < words; ++i)
            fprintf(stream, "%s%d", i ? "," : "", (int)matrix_snapshot[i]);
    }
    int end_written = fprintf(stream, "]}\n");
    int failed = ferror(stream);
    int closed = fclose(stream);
    if (written > 0 && end_written > 0 && !failed && !closed) {
        if (rename(temporary, path)) unlink(temporary);
    } else unlink(temporary);
}

/* Read-only introspection for isolated probes and local collection. No switch
 * or runtime activation command is exposed; loading the .so enables capture. */
const char *nx_isp_color_observer_directory(void) {
    return directory_pid == getpid() ? directory : "";
}

void ipcs_tone_lut(u16 *source) {
    int incoming_errno = *__errno_location();
    void (*next)(u16 *) = dlsym((void *)-1, "ipcs_tone_lut");
    if (!next) { fprintf(stderr, "[NX ISP observer] next ipcs_tone_lut missing\n"); abort(); }
    /* Never wait for a concurrent recorder or introduce another setter call.
     * Failed/busy capture leaves the original call and argument unchanged. */
    int acquired = source && claim_recorder();
    if (acquired) for (unsigned int i = 0; i < GAMMA_WORDS; ++i) gamma_snapshot[i] = source[i];
    *__errno_location() = incoming_errno;
    next(source);
    int outgoing_errno = *__errno_location();
    if (acquired) {
        publish("gamma", source, __builtin_return_address(0), (void *)next, 0, GAMMA_WORDS);
        __sync_lock_release(&recording);
    }
    *__errno_location() = outgoing_errno;
}

int ipcs_color_correction_param(struct ipcs_color_correction *source) {
    int incoming_errno = *__errno_location();
    int (*next)(struct ipcs_color_correction *) = dlsym((void *)-1, "ipcs_color_correction_param");
    if (!next) { fprintf(stderr, "[NX ISP observer] next ipcs_color_correction_param missing\n"); abort(); }
    int acquired = source && claim_recorder();
    if (acquired) for (unsigned int i = 0; i < MATRIX_WORDS; ++i) matrix_snapshot[i] = source->col_mat[i];
    *__errno_location() = incoming_errno;
    int result = next(source);
    int outgoing_errno = *__errno_location();
    if (acquired) {
        publish("ccm", source, __builtin_return_address(0), (void *)next, result, MATRIX_WORDS);
        __sync_lock_release(&recording);
    }
    *__errno_location() = outgoing_errno;
    return result;
}

int ipcs_rgb2ycc_param(struct ipcs_rgb2ycc *source) {
    int incoming_errno = *__errno_location();
    int (*next)(struct ipcs_rgb2ycc *) = dlsym((void *)-1, "ipcs_rgb2ycc_param");
    if (!next) { fprintf(stderr, "[NX ISP observer] next ipcs_rgb2ycc_param missing\n"); abort(); }
    /* Only selector 3 consumes the nine RGB-to-YCC coefficients. Other
     * selectors can leave those fields uninitialized; forward them unread. */
    int acquired = source && source->sel == 3 && claim_recorder();
    if (acquired) for (unsigned int i = 0; i < MATRIX_WORDS; ++i) matrix_snapshot[i] = source->ycc[i];
    *__errno_location() = incoming_errno;
    int result = next(source);
    int outgoing_errno = *__errno_location();
    if (acquired) {
        publish("rgb2ycc", source, __builtin_return_address(0), (void *)next, result, MATRIX_WORDS);
        __sync_lock_release(&recording);
    }
    *__errno_location() = outgoing_errno;
    return result;
}
