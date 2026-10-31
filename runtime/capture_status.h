/* On-demand observation on the original Ecore thread. No singleton factories,
 * state setters, capture callbacks, or completion inference belong here. */
extern int readlink(const char *, char *, unsigned int);

#define CAPTURE_STATUS_REQUEST "/var/run/nx300m-capture-status-request"
#define CAPTURE_STATUS_RESPONSE "/var/run/nx300m-capture-status-response"
#define CAPTURE_STATUS_READY "/var/run/nx300m-capture-status-ready"

static int capture_status_pid, capture_status_attached, capture_status_ready;
static u32 capture_status_last_request;

static int capture_status_camera(void) {
    char executable[4096];
    int count = readlink("/proc/self/exe", executable, sizeof executable-1);
    if (count <= 0 || count >= (int)sizeof executable-1) return 0;
    executable[count] = 0;
    const char *name = executable;
    for (const char *p = executable; *p; ++p) if (*p == '/') name = p+1;
    const char *expected[2] = {"di-camera-app", "di-camera-app-nx300"};
    for (int i = 0; i < 2; ++i) {
        const char *a = name, *b = expected[i];
        while (*a && *a == *b) { ++a; ++b; }
        if (!*a && !*b) return 1;
    }
    return 0;
}

static int capture_status_decimal(const char **cursor, unsigned long long *value) {
    const char *p = *cursor;
    unsigned long long number = 0;
    if (*p < '0' || *p > '9') return 0;
    do {
        u32 digit = (u32)(*p-'0');
        if (number > (~0ULL-digit)/10ULL) return 0;
        number = number*10ULL+digit;
        ++p;
    } while (*p >= '0' && *p <= '9');
    *cursor = p; *value = number;
    return 1;
}
static int capture_status_space(char c) { return c == ' ' || c == '\t' || c == '\r' || c == '\n'; }
static int capture_status_request(u32 *id, unsigned long long *deadline) {
    // A malformed FIFO or symlink must not block the UI thread.
    int fd = open(CAPTURE_STATUS_REQUEST, 04000|0400000);
    if (fd < 0) return 0;
    char content[96];
    int bytes = read(fd, content, sizeof content-1);
    close(fd);
    if (bytes <= 0 || bytes == (int)sizeof content-1) return 0;
    content[bytes] = 0;
    const char *p = content;
    unsigned long long request = 0;
    while (capture_status_space(*p)) ++p;
    if (!capture_status_decimal(&p, &request) || !request || request > ~0U || !capture_status_space(*p)) return 0;
    while (capture_status_space(*p)) ++p;
    if (!capture_status_decimal(&p, deadline) || !*deadline) return 0;
    while (capture_status_space(*p)) ++p;
    if (*p) return 0;
    *id = (u32)request;
    return 1;
}

struct capture_status_value {
    int slot_available, slot_readable, constructed, vtable_verified;
    int getter_available, memory_readable, raw_available, available, raw;
    const char *reason;
};
static void capture_status_read(struct capture_status_value *value,
                                const char *slot_name, const char *vtable_name,
                                const char *getter_name, u32 extent, int capture_state) {
    void *slot = dlsym((void *)0, slot_name);
    void *vtable = dlsym((void *)0, vtable_name);
    int (*getter)(void *) = dlsym((void *)0, getter_name);
    value->slot_available = slot != 0;
    value->getter_available = getter != 0;
    value->reason = "slot_symbol_unavailable";
    if (!slot) return;
    u32 object = 0, object_vptr = 0;
    value->slot_readable = copy_read(&object, (u32)slot, 4);
    value->reason = "slot_unreadable";
    if (!value->slot_readable) return;
    value->constructed = object != 0;
    value->reason = "not_constructed";
    if (!object) return;
    value->reason = "vtable_unavailable_or_mismatch";
    if (!vtable || !copy_read(&object_vptr, object, 4) || object_vptr != (u32)vtable+8) return;
    value->vtable_verified = 1;
    unsigned char bytes[64];
    value->memory_readable = copy_read(bytes, object, extent);
    value->reason = "object_fields_unreadable";
    if (!value->memory_readable) return;
    value->reason = "getter_symbol_unavailable";
    if (!getter) return;
    value->raw = getter((void *)object);
    value->raw_available = 1;
    value->available = capture_state ? value->raw >= 0 && value->raw <= 3 : value->raw == 0 || value->raw == 1;
    value->reason = value->available ? "available" : "unknown_raw_value";
}
static void capture_status_print(void *file, const struct capture_status_value *value, int state) {
    fprintf(file, "{\"available\":%s,\"%s\":", value->available ? "true" : "false", state ? "raw_state" : "raw");
    if (value->raw_available) fprintf(file, "%d", value->raw); else fprintf(file, "null");
    fprintf(file, ",\"%s\":", state ? "state" : "value");
    if (!value->available) fprintf(file, "null");
    else if (state) {
        const char *names[4] = {"off", "idle", "capture", "movie"};
        json_string(file, names[value->raw]);
    } else fprintf(file, "%s", value->raw ? "true" : "false");
    fprintf(file, ",\"slot_available\":%s,\"slot_readable\":%s,\"constructed\":%s,"
            "\"vtable_verified\":%s,\"getter_available\":%s,\"memory_readable\":%s,\"reason\":",
            value->slot_available ? "true" : "false", value->slot_readable ? "true" : "false",
            value->constructed ? "true" : "false", value->vtable_verified ? "true" : "false",
            value->getter_available ? "true" : "false", value->memory_readable ? "true" : "false");
    json_string(file, value->reason);
    fprintf(file, "}");
}
static int capture_status_poll(void *owner) {
    int saved = *__errno_location(), pid = getpid();
    if ((int)owner != pid) { *__errno_location() = saved; return 0; }
    u32 id = 0;
    unsigned long long deadline = 0;
    if (!capture_status_request(&id, &deadline) || id == capture_status_last_request) {
        *__errno_location() = saved; return 1;
    }
    capture_status_last_request = id;
    unsigned long long sampled = monotonic_ns();
    int expired = sampled && sampled/1000000ULL > deadline;
    struct capture_status_value values[6] = {{0}};
    for (int i = 0; i < 6; ++i) values[i].reason = expired ? "request_expired" : "monotonic_clock_unavailable";
    if (sampled && !expired) {
        capture_status_read(values, "_ZN9SingletonI13CStateManagerE11m_pInstanceE", "_ZTV13CStateManager",
                            "_ZNK13CStateManager15GetCaptureStateEv", 0x1c, 1);
        capture_status_read(values+1, "_ZN9SingletonI18CInformationBureauE11m_pInstanceE", "_ZTV18CInformationBureau",
                            "_ZNK18CInformationBureau18IsCaptureRequestedEv", 0x23, 0);
        const char *getters[4] = {"_ZN15CCaptureCounter12isRawWorkEndEv", "_ZN15CCaptureCounter11isPPWorkEndEv",
                                 "_ZN15CCaptureCounter12isYccWorkEndEv", "_ZN15CCaptureCounter12isEncWorkEndEv"};
        for (int i = 0; i < 4; ++i)
            capture_status_read(values+2+i, "_ZN9SingletonI15CCaptureCounterE11m_pInstanceE", "_ZTV15CCaptureCounter",
                                getters[i], 0x30, 0);
    }
    char temporary[128];
    snprintf(temporary, sizeof temporary, "%s.tmp-%d", CAPTURE_STATUS_RESPONSE, pid);
    void *file = fopen(temporary, "w");
    if (file) {
        fprintf(file, "{\"request_id\":%u,\"guest_pid\":%d,\"sampled_monotonic_ns\":%llu,"
                "\"deadline_monotonic_ms\":%llu,\"request_expired\":%s,\"read_only\":true,"
                "\"ecore_thread\":true,\"photo_completion_proven\":false,\"capture\":",
                id, pid, sampled, deadline, expired ? "true" : "false");
        capture_status_print(file, values, 1);
        fprintf(file, ",\"capture_requested\":"); capture_status_print(file, values+1, 0);
        fprintf(file, ",\"work_end\":{");
        const char *stages[4] = {"raw", "pp", "ycc", "enc"};
        for (int i = 0; i < 4; ++i) {
            fprintf(file, "%s\"%s\":", i ? "," : "", stages[i]);
            capture_status_print(file, values+2+i, 0);
        }
        fprintf(file, "}}\n");
        finish_file(file, temporary, CAPTURE_STATUS_RESPONSE);
    }
    *__errno_location() = saved;
    return 1;
}
static void capture_status_attach(void) {
    int pid = getpid();
    if (capture_status_pid != pid) {
        capture_status_pid = pid;
        capture_status_attached = capture_status_ready = 0;
        capture_status_last_request = 0;
    }
    if (!capture_status_camera()) return;
    void *(*timer_add)(double, int (*)(void *), void *) = dlsym((void *)-1, "ecore_timer_add");
    if (!capture_status_attached && timer_add && timer_add(0.1, capture_status_poll, (void *)pid))
        capture_status_attached = 1;
    if (capture_status_attached && !capture_status_ready) {
        char temporary[128];
        snprintf(temporary, sizeof temporary, "%s.tmp-%d", CAPTURE_STATUS_READY, pid);
        void *file = fopen(temporary, "w");
        if (file) {
            int written = fprintf(file, "%d\n", pid), failed = ferror(file), closed = fclose(file);
            if (written > 0 && !failed && !closed && !rename(temporary, CAPTURE_STATUS_READY))
                capture_status_ready = 1;
            else unlink(temporary);
        }
    }
}
