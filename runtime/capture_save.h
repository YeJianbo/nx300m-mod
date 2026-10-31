/* Narrow write/close evidence for the reviewed stock libsif WriteChunks.
 * TLS scopes isolate nested calls and other threads; stdio outside a matching
 * stock Close is forwarded without being recorded. No extra flush/close. */
struct capture_save_scope {
    struct capture_save_scope *previous;
    u32 library_base, handler, descriptor, osal, stream, expected_chunks;
    unsigned long long expected_bytes, actual_bytes;
    u32 create_calls, write_calls, close_calls, normal_close_calls, flush_calls, fclose_calls;
    int pid, enabled, expected_available, all_writes_complete, tracking_failed;
    int flush_result, flush_errno, fclose_result, fclose_errno, close_active;
    int close_stop_read, close_stop, stream_error_before_close, confirmed;
};
static __thread struct capture_save_scope *capture_save_current;

static int capture_save_stock(void *function, u32 offset, u32 *base) {
    struct library_info info = {0};
    if (!dladdr(function, &info) || !info.path || (u32)function-(u32)info.base != offset) return 0;
    const char *name = info.path, *expected = "libsif.so";
    for (const char *p = name; *p; ++p) if (*p == '/') name = p+1;
    while (*name && *name == *expected) { ++name; ++expected; }
    if (*name || *expected) return 0;
    if (base) *base = (u32)info.base;
    return 1;
}
static int capture_save_caller(const struct capture_save_scope *scope, void *caller, u32 offset) {
    return (u32)caller-scope->library_base == offset;
}
static void capture_save_begin(struct capture_save_scope *scope, void *self, void *next, const struct phase *before, int enabled) {
    scope->previous = capture_save_current;
    capture_save_current = scope;
    scope->all_writes_complete = 1;
    scope->handler = (u32)self;
    scope->pid = getpid();
    scope->enabled = enabled && capture_save_stock(next, 0x274a4, &scope->library_base);
    if (!scope->enabled || !before->descriptor_read || !before->path_terminated || !before->path[0]) return;
    scope->descriptor = before->descriptor;
    u32 vector[3];
    if (!copy_read(vector, scope->descriptor, sizeof vector) || !vector[0] || vector[1] < vector[0]
        || vector[2] < vector[1] || (vector[1]-vector[0])%8) return;
    scope->expected_chunks = (vector[1]-vector[0])/8;
    // Optional observer input is bounded. An oversized/unreadable vector never
    // qualifies as complete; the original function is still forwarded intact.
    if (!scope->expected_chunks || scope->expected_chunks > 4096) return;
    for (u32 i = 0; i < scope->expected_chunks; ++i) {
        u32 chunk[2];
        if (!copy_read(chunk, vector[0]+i*8, sizeof chunk)) return;
        scope->expected_bytes += chunk[1];
    }
    scope->expected_available = scope->expected_bytes != 0;
}
static void capture_save_end(struct capture_save_scope *scope, const struct phase *before,
                             const struct phase *after, unsigned int result) {
    scope->confirmed = scope->pid == getpid() && scope->enabled && scope->expected_available && !scope->tracking_failed
        && result == 1 && before->stop_read && before->stop != 2 && after->stop_read && after->stop != 2
        && scope->create_calls == 1 && scope->write_calls > 0 && scope->all_writes_complete
        && scope->actual_bytes > 0 && scope->actual_bytes == scope->expected_bytes
        && scope->normal_close_calls == 1 && scope->close_stop_read && scope->close_stop != 2
        && scope->flush_calls == 1 && scope->flush_result == 0
        && scope->fclose_calls == 1 && scope->fclose_result == 0 && scope->stream_error_before_close == 0;
    capture_save_current = scope->previous;
}
static void capture_save_print(void *file, const struct capture_save_scope *scope) {
    fprintf(file, ",\"write_completion_confirmed\":%s,\"write_io_observation\":{"
            "\"scope\":\"current WriteChunks stock stream; stdio write/flush/close observations\","
            "\"power_loss_durability_proven\":false,\"photo_completion_proven\":false,\"request_associated\":false,"
            "\"expected_available\":%s,\"expected_chunks\":%u,\"expected_bytes\":%llu,"
            "\"actual_write_bytes\":%llu,\"create_calls\":%u,\"write_calls\":%u,"
            "\"all_writes_complete\":%s,\"tracking_failed\":%s,\"close_calls\":%u,"
            "\"normal_close_calls\":%u,\"close_stop_read\":%s,\"close_stop_raw\":%d,"
            "\"stream_error_before_close\":%d,\"fflush_calls\":%u,\"fflush_result\":%d,"
            "\"fflush_errno\":%d,\"fclose_calls\":%u,\"fclose_result\":%d,\"fclose_errno\":%d}",
            scope->confirmed ? "true" : "false", scope->expected_available ? "true" : "false",
            scope->expected_chunks, scope->expected_bytes, scope->actual_bytes,
            scope->create_calls, scope->write_calls, scope->all_writes_complete ? "true" : "false",
            scope->tracking_failed ? "true" : "false", scope->close_calls, scope->normal_close_calls,
            scope->close_stop_read ? "true" : "false", scope->close_stop,
            scope->stream_error_before_close, scope->flush_calls, scope->flush_result, scope->flush_errno,
            scope->fclose_calls, scope->fclose_result, scope->fclose_errno);
}

unsigned int capture_save_create(void *, const char *, int)
    __asm__("_ZN11DscOsalFile6CreateEPKcNS_15DscOsalFileFlagE");
unsigned int capture_save_create(void *self, const char *path, int flag) {
    int saved = *__errno_location();
    unsigned int (*next)(void *, const char *, int) = dlsym((void *)-1, "_ZN11DscOsalFile6CreateEPKcNS_15DscOsalFileFlagE");
    if (!next) { *__errno_location() = 38; return 0; }
    struct capture_save_scope *scope = capture_save_current;
    int observe = scope && scope->pid == getpid() && scope->enabled;
    if (observe && (!capture_save_caller(scope, __builtin_return_address(0), 0x2769c)
        || !capture_save_stock((void *)next, 0x3c8bc, (void *)0)
        || flag != 2 || (u32)path != scope->descriptor+12 || scope->create_calls)) scope->tracking_failed = 1;
    *__errno_location() = saved;
    unsigned int result = next(self, path, flag);
    int outgoing = *__errno_location();
    if (observe) {
        ++scope->create_calls;
        scope->osal = (u32)self;
        if (result != 1 || (u32)self > ~0U-4 || !copy_read(&scope->stream, (u32)self+4, 4) || !scope->stream)
            scope->tracking_failed = 1;
    }
    *__errno_location() = outgoing;
    return result;
}

unsigned int capture_save_write(void *, const void *, u32 *) __asm__("_ZN11DscOsalFile5WriteEPKvPm");
unsigned int capture_save_write(void *self, const void *data, u32 *bytes) {
    int saved = *__errno_location();
    unsigned int (*next)(void *, const void *, u32 *) = dlsym((void *)-1, "_ZN11DscOsalFile5WriteEPKvPm");
    if (!next) { *__errno_location() = 38; return 0; }
    struct capture_save_scope *scope = capture_save_current;
    int observe = scope && scope->pid == getpid() && scope->enabled;
    u32 requested = 0, actual = 0, stream = 0;
    int readable = observe && copy_read(&requested, (u32)bytes, 4);
    if (observe) {
        void *caller = __builtin_return_address(0);
        if ((!capture_save_caller(scope, caller, 0x27870) && !capture_save_caller(scope, caller, 0x2791c)
             && !capture_save_caller(scope, caller, 0x279d0)) || (u32)self != scope->osal
            || !capture_save_stock((void *)next, 0x3caa4, (void *)0)
            || !copy_read(&stream, scope->osal+4, 4) || stream != scope->stream || !stream || !readable)
            scope->tracking_failed = 1;
    }
    *__errno_location() = saved;
    unsigned int result = next(self, data, bytes);
    int outgoing = *__errno_location();
    if (observe) {
        ++scope->write_calls;
        if (!readable || !copy_read(&actual, (u32)bytes, 4)) scope->tracking_failed = 1;
        else scope->actual_bytes += actual;
        if (result != 1 || actual != requested) scope->all_writes_complete = 0;
    }
    *__errno_location() = outgoing;
    return result;
}

/* Close has no reliable source-level return value; preserve its raw r0 bits.
 * fflush/fclose return codes below, not this value, establish close success. */
unsigned int capture_save_close(void *) __asm__("_ZN11DscOsalFile5CloseEv");
unsigned int capture_save_close(void *self) {
    int saved = *__errno_location();
    unsigned int (*next)(void *) = dlsym((void *)-1, "_ZN11DscOsalFile5CloseEv");
    if (!next) { *__errno_location() = 38; return 0; }
    struct capture_save_scope *scope = capture_save_current;
    int observe = scope && scope->pid == getpid() && scope->enabled;
    if (observe) {
        ++scope->close_calls;
        if (capture_save_caller(scope, __builtin_return_address(0), 0x27a4c)) {
            ++scope->normal_close_calls;
            u32 stream = 0;
            if ((u32)self != scope->osal || !capture_save_stock((void *)next, 0x3cbe0, (void *)0)
                || !copy_read(&stream, scope->osal+4, 4) || !stream || stream != scope->stream
                || scope->close_active || scope->normal_close_calls != 1) scope->tracking_failed = 1;
            else {
                scope->close_stop_read = scope->handler <= ~0U-0x70
                    && copy_read(&scope->close_stop, scope->handler+0x70, 4);
                scope->stream_error_before_close = ferror((void *)stream);
                scope->close_active = 1;
            }
        }
    }
    *__errno_location() = saved;
    unsigned int result = next(self);
    int outgoing = *__errno_location();
    if (observe) scope->close_active = 0;
    *__errno_location() = outgoing;
    return result;
}

int fflush(void *stream) {
    int saved = *__errno_location();
    int (*next)(void *) = dlsym((void *)-1, "fflush");
    if (!next) { *__errno_location() = 38; return -1; }
    struct capture_save_scope *scope = capture_save_current;
    int observe = scope && scope->pid == getpid() && scope->close_active && (u32)stream == scope->stream
        && capture_save_caller(scope, __builtin_return_address(0), 0x3cc14);
    *__errno_location() = saved;
    int result = next(stream), outgoing = *__errno_location();
    if (observe) { ++scope->flush_calls; scope->flush_result = result; scope->flush_errno = outgoing; }
    *__errno_location() = outgoing;
    return result;
}
int fclose(void *stream) {
    int saved = *__errno_location();
    int (*next)(void *) = dlsym((void *)-1, "fclose");
    if (!next) { *__errno_location() = 38; return -1; }
    struct capture_save_scope *scope = capture_save_current;
    int observe = scope && scope->pid == getpid() && scope->close_active && (u32)stream == scope->stream
        && capture_save_caller(scope, __builtin_return_address(0), 0x3cc1c);
    *__errno_location() = saved;
    int result = next(stream), outgoing = *__errno_location();
    if (observe) { ++scope->fclose_calls; scope->fclose_result = result; scope->fclose_errno = outgoing; }
    *__errno_location() = outgoing;
    return result;
}
