/* Resource trigger; the shared RAM stage launches service.sh's lifecycle. */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#ifndef _LARGEFILE64_SOURCE
#define _LARGEFILE64_SOURCE
#endif
#include <sys/stat.h>
#include <sys/wait.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <dlfcn.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include <poll.h>
#include "../mod/metol/mod_paths.h"
#include "close_inherited_fds.h"

/* Use the toolchain's ARM stat64 layout and the original libc's versioned ABI.
 * No modern stat/time64 entry point or private copy of struct stat is used. */
extern int __xstat64(int, const char *, struct stat64 *);
extern int stock_fcntl(int, int, ...) __asm__("fcntl");
extern char **environ;

struct resource_id {
    dev_t root_dev, script_dev; ino64_t root_ino, script_ino;
    unsigned long mount_id;
    int mount_known;
};
static struct {
    pid_t owner, child;
    void *timer, *exit_handler;
    char root[192], script[208], root_env[224];
    struct resource_id current, launched;
    unsigned int epoch, child_epoch, attempts, cooldown;
    unsigned int missing_status_ticks;
    unsigned int mount_changes;
    int mount_watch;
    int present, succeeded, disabled;
    int last_exit, last_signal, last_errno;
    const char *exit_source;
} loader;

static void publish_status(const char *reason) {
    FILE *file = fopen("/var/run/nx300m-service-loader-status.tmp", "w");
    if (!file) return;
    int written = fprintf(file,
        "{\"owner_pid\":%d,\"root\":\"%s\",\"present\":%s,\"epoch\":%u,"
        "\"attempts\":%u,\"child_pid\":%d,\"succeeded\":%s,\"auto_start_disabled\":%s,"
        "\"mount_id\":%lu,\"mount_known\":%s,"
        "\"mount_watch_active\":%s,\"mount_changes\":%u,"
        "\"last_exit\":%d,\"last_signal\":%d,\"last_errno\":%d,\"exit_source\":\"%s\",\"reason\":\"%s\"}\n",
        loader.owner, loader.root, loader.present ? "true" : "false", loader.epoch,
        loader.attempts, loader.child, loader.succeeded ? "true" : "false", loader.disabled ? "true" : "false",
        loader.current.mount_id, loader.current.mount_known ? "true" : "false",
        loader.mount_watch >= 0 ? "true" : "false", loader.mount_changes,
        loader.last_exit, loader.last_signal, loader.last_errno,
        loader.exit_source ? loader.exit_source : "none", reason);
    int closed = fclose(file);
    if (written > 0 && !closed)
        rename("/var/run/nx300m-service-loader-status.tmp", "/var/run/nx300m-service-loader-status.json");
}

static int camera_name(const char *name) {
    const char *base = strrchr(name, '/');
    base = base ? base + 1 : name;
    return !strcmp(base, "di-camera-app") || !strcmp(base, "di-camera-app-nx300");
}

static int camera_process(void) {
    /* Check the executable, not arbitrary arguments containing a camera name.
     * The original di-camera-app symlink resolves to di-camera-app-nx300. */
    char executable[4096];
    ssize_t n = readlink("/proc/self/exe", executable, sizeof executable - 1);
    if (n <= 0 || n >= (ssize_t)sizeof executable - 1) return 0;
    executable[n] = 0;
    return camera_name(executable);
}

static int same_id(const struct resource_id *a, const struct resource_id *b) {
    return a->root_dev == b->root_dev && a->root_ino == b->root_ino &&
           a->script_dev == b->script_dev && a->script_ino == b->script_ino &&
           (!a->mount_known || !b->mount_known || a->mount_id == b->mount_id);
}

static int mount_number(const char *text, unsigned long *value) {
    unsigned long n = 0;
    if (!*text) return 0;
    for (; *text; text++) {
        if (*text < '0' || *text > '9' || n > (UINT32_MAX - (*text - '0')) / 10U)
            return 0;
        n = n * 10U + (unsigned int)(*text - '0');
    }
    *value = n;
    return n != 0;
}

static int mount_device(const char *text) {
    for (int part = 0; part < 2; part++) {
        unsigned long n = 0;
        const char *start = text;
        while (*text >= '0' && *text <= '9') {
            if (n > (UINT32_MAX - (*text - '0')) / 10U) return 0;
            n = n * 10U + (unsigned int)(*text++ - '0');
        }
        if (text == start || (part == 0 ? *text++ != ':' : *text != 0)) return 0;
    }
    return 1;
}

static int mount_path(char *text) {
    /* mountinfo escapes whitespace and backslash with these octal sequences.
     * Decode in place; malformed escapes must not masquerade as another path. */
    char *out = text;
    if (*text != '/') return 0;
    for (char *p = text; *p; p++) {
        if (*p == '\\') {
            if (!strncmp(p, "\\040", 4)) *out++ = ' ';
            else if (!strncmp(p, "\\011", 4)) *out++ = '\t';
            else if (!strncmp(p, "\\012", 4)) *out++ = '\n';
            else if (!strncmp(p, "\\134", 4)) *out++ = '\\';
            else return 0;
            p += 3;
        } else *out++ = *p;
    }
    *out = 0;
    return 1;
}

static int mount_covers(const char *point, const char *root) {
    size_t length = strlen(point);
    return !strcmp(point, "/") ||
           (!strncmp(point, root, length) && (!root[length] || root[length] == '/'));
}

static int mount_identity(FILE *file, const char *root, unsigned long *identity) {
    /* Linux 3.5 already exposes mount IDs in mountinfo. Read the entire
     * snapshot before accepting it: a partial read cannot create a new epoch.
     * Oversized/malformed lines simply leave the stat-only fallback in use. */
    char *line = malloc(65536);
    unsigned long selected = 0;
    size_t longest = 0;
    int valid = line != NULL, ambiguous = 0;
    while (valid && fgets(line, 65536, file)) {
        size_t length = strlen(line);
        if (!length || line[length - 1] != '\n') { valid = 0; break; }
        line[length - 1] = 0;
        char *state = NULL, *fields[6];
        for (int i = 0; i < 6; i++) fields[i] = strtok_r(i ? NULL : line, " ", &state);
        unsigned long id, parent;
        if (!fields[5] || !mount_number(fields[0], &id) ||
            !mount_number(fields[1], &parent) || !mount_device(fields[2]) ||
            !mount_path(fields[3]) || !mount_path(fields[4])) { valid = 0; break; }
        char *token;
        do { token = strtok_r(NULL, " ", &state); } while (token && strcmp(token, "-"));
        if (!token || !strtok_r(NULL, " ", &state) || !strtok_r(NULL, " ", &state) ||
            !strtok_r(NULL, " ", &state) || strtok_r(NULL, " ", &state)) { valid = 0; break; }
        length = strlen(fields[4]);
        if (mount_covers(fields[4], root) && length >= longest) {
            /* Equal-path stacked mounts cannot be ordered by numeric ID or
             * file order. Keep stat fallback rather than choose a hidden one. */
            if (length == longest && selected && selected != id) ambiguous = 1;
            if (length > longest) ambiguous = 0;
            selected = id; longest = length;
        }
    }
    if (ferror(file)) valid = 0;
    free(line);
    if (!valid || !selected || ambiguous) return 0;
    *identity = selected;
    return 1;
}

static int resource_mount(unsigned long *identity) {
    char resolved[4096];
    if (!realpath(loader.root, resolved)) return 0;
    FILE *file = fopen("/proc/self/mountinfo", "r");
    if (!file) return 0;
    int known = mount_identity(file, resolved, identity);
    if (fclose(file)) known = 0;
    return known;
}

static int resource(struct resource_id *id) {
    struct stat64 root, script;
    if (__xstat64(3, loader.root, &root) || !S_ISDIR(root.st_mode) ||
        __xstat64(3, loader.script, &script) || !S_ISREG(script.st_mode) ||
        access(loader.script, R_OK)) return 0;
    id->root_dev = root.st_dev; id->root_ino = root.st_ino;
    id->script_dev = script.st_dev; id->script_ino = script.st_ino;
    id->mount_id = 0;
    id->mount_known = resource_mount(&id->mount_id);
    return 1;
}

static int mount_namespace_changed(void) {
    if (loader.mount_watch < 0) {
        loader.mount_watch = open("/proc/self/mountinfo", O_RDONLY | O_CLOEXEC);
        if (loader.mount_watch < 0)
            loader.mount_watch = open("/proc/mounts", O_RDONLY | O_CLOEXEC);
        /* Open establishes the kernel's namespace-event baseline. Reopening
         * after an error is not itself evidence of a mount change. */
        return 0;
    }
    struct pollfd watch = { loader.mount_watch, POLLPRI | POLLERR, 0 };
    int ready = poll(&watch, 1, 0);
    if (ready < 0 || (watch.revents & (POLLNVAL | POLLHUP))) {
        if (ready < 0 && errno == EINTR) return 0;
        close(loader.mount_watch); loader.mount_watch = -1;
        return 0;
    }
    /* NX300M GPL mounts_poll consumes ns->event on poll itself. Keep this fd
     * open: reopening each tick would lose even an unmount+mount with ID reuse.
     * This is namespace-wide, so start.sh must reconcile idempotently and
     * honor its explicit manual-stop marker. It does not identify an SD event. */
    if (ready > 0 && (watch.revents & (POLLPRI | POLLERR))) {
        loader.mount_changes++;
        return 1;
    }
    return 0;
}

static void failed_attempt(void) {
    /* Half-second timer ticks: 1, 2, 4, 8 seconds; then stop until a new
     * resource identity or an observed removal. No service watchdog. */
    loader.cooldown = 1U << loader.attempts;
}

static void finish_child(int exit_code, int signal, int error) {
    loader.last_errno = error;
    loader.last_exit = exit_code;
    loader.last_signal = signal;
    loader.child = 0;
    if (loader.present && loader.child_epoch == loader.epoch &&
        same_id(&loader.current, &loader.launched)) {
        if (!error && !signal && exit_code == 0) {
            loader.succeeded = 1;
        } else failed_attempt();
    }
    publish_status(error ? "child-status-unavailable" :
                   loader.last_exit == 0 ? "start-exited-success" : "start-exited-failure");
}

/* Original Ecore 1.2 reaps all children with waitpid(-1), including plain fork.
 * Its event prefix is confirmed in stock libecore: pid +0, exit +4, exe +8,
 * signal +12 and exited/signalled bits at +16 (see ecore-abi/stock-ecore.asm).
 * We only observe our exact PID and pass all events to the existing handlers. */
struct stock_exe_del_prefix {
    pid_t pid; int exit_code; void *exe; int exit_signal; unsigned char flags;
};
static int child_exit_event(void *data, int type, void *event) {
    (void)data; (void)type;
    const struct stock_exe_del_prefix *exit = event;
    if (loader.owner == getpid() && loader.child && exit && exit->pid == loader.child) {
        loader.exit_source = "ecore-event";
        finish_child((exit->flags & 1) ? exit->exit_code : -1,
                     (exit->flags & 2) ? exit->exit_signal : 0, 0);
    }
    return 1;
}

static void reap_child(void) {
    int status;
    if (!loader.child || loader.owner != getpid()) return;
    pid_t result = waitpid(loader.child, &status, WNOHANG);
    if (!result || (result < 0 && errno == EINTR)) return;
    if (result < 0) {
        if (errno != ECHILD) return;
        /* Ecore may have reaped it and queued the DEL event in this iteration.
         * Give that event time to arrive before declaring status unavailable. */
        if (++loader.missing_status_ticks <= 4) return;
        loader.exit_source = "status-unavailable";
        finish_child(-1, 0, ECHILD);
    } else {
        loader.exit_source = "waitpid";
        finish_child(WIFEXITED(status) ? WEXITSTATUS(status) : -1,
                        WIFSIGNALED(status) ? WTERMSIG(status) : 0, 0);
    }
}

static void launch_child(void) {
    /* Prepare allocation/environment before fork: the camera may be threaded.
     * Child uses only async-signal-safe libc operations before execve. */
    size_t count = 0, used = 0;
    while (environ[count]) count++;
    char **environment = calloc(count + 3, sizeof *environment);
    int null_fd = open("/dev/null", O_RDWR | O_CLOEXEC);
    loader.attempts++;
    if (!environment || null_fd < 0) {
        loader.last_errno = errno;
        free(environment); if (null_fd >= 0) close(null_fd);
        failed_attempt(); publish_status("child-preparation-failed"); return;
    }
    for (size_t i = 0; i < count; i++) {
        if (!strncmp(environ[i], "LD_PRELOAD=", 11) ||
            !strncmp(environ[i], "LD_AUDIT=", 9) ||
            !strncmp(environ[i], "NX300M_AUTO_START=", 18) ||
            !strncmp(environ[i], "NX300M_MOD_ROOT=", 16)) continue;
        environment[used++] = environ[i];
    }
    environment[used++] = loader.root_env;
    environment[used] = "NX300M_AUTO_START=1";
    char *arguments[] = {"/bin/sh", loader.script, "--service", NULL};
    pid_t child = fork();
    if (!child) {
        if (dup2(null_fd, 0) < 0 || dup2(null_fd, 1) < 0 || dup2(null_fd, 2) < 0)
            _exit(126);
        /* dup2(fd,fd) leaves CLOEXEC intact when the camera had closed stdio
         * and open(/dev/null) reused 0, 1 or 2. Preserve all three across exec. */
        if (stock_fcntl(0, F_SETFD, 0) || stock_fcntl(1, F_SETFD, 0) ||
            stock_fcntl(2, F_SETFD, 0)) _exit(126);
        if (nx_close_inherited_fds()) _exit(126);
        if (chdir("/")) _exit(126);
        execve("/bin/sh", arguments, environment);
        _exit(127);
    }
    int fork_error = errno;
    close(null_fd); free(environment);
    if (child < 0) { loader.last_errno = fork_error; failed_attempt(); publish_status("fork-failed"); return; }
    loader.child = child; loader.child_epoch = loader.epoch;
    loader.missing_status_ticks = 0;
    loader.launched = loader.current;
    loader.last_errno = 0;
    publish_status("start-forked");
}

static int service_tick(void *unused) {
    (void)unused;
    /* A forked app child must never wait on or trigger its parent's work. */
    if (loader.owner != getpid()) return 0;
    int mount_changed = mount_namespace_changed();
    struct resource_id observed;
    int present = resource(&observed);
    if (present != loader.present ||
        (present && (!same_id(&observed, &loader.current) || mount_changed))) {
        loader.epoch++; loader.present = present;
        if (present) loader.current = observed;
        loader.succeeded = 0; loader.attempts = 0; loader.cooldown = 0;
        publish_status(!present ? "resources-absent" :
                       mount_changed ? "namespace-mount-change" : "resources-present");
    } else if (present && observed.mount_known && !loader.current.mount_known) {
        /* First successful mountinfo read enriches this identity. It is not
         * evidence of a remount, and must not restart a manually stopped service. */
        loader.current.mount_id = observed.mount_id;
        loader.current.mount_known = 1;
        publish_status("mount-identity-known");
    }
    reap_child();
    /* A resumed app skips the exec wrapper. Suppress future service starts,
     * but leave existing services and mapped hooks to their own lifecycle. */
    char disabled_path[224];
    snprintf(disabled_path, sizeof disabled_path, "%s/DISABLED", loader.root);
    struct stat64 disabled_info;
    char link_byte;
    int disabled = !__xstat64(3, "/usr/lib/nx300m-mod/DISABLED", &disabled_info) ||
                   readlink("/usr/lib/nx300m-mod/DISABLED", &link_byte, 1) >= 0 ||
                   !__xstat64(3, disabled_path, &disabled_info) ||
                   readlink(disabled_path, &link_byte, 1) >= 0;
    if (disabled != loader.disabled) {
        loader.disabled = disabled;
        publish_status(disabled ? "auto-start-disabled" : "auto-start-enabled");
    }
    if (disabled) return 1;
    if (loader.child || !present || loader.succeeded || loader.attempts >= 5) return 1;
    if (loader.cooldown) { loader.cooldown--; return 1; }
    launch_child();
    return 1;
}

static void register_timer(void) {
    pid_t pid = getpid();
    if (loader.owner == pid && loader.timer) return;
    const char *root = nx_mod_root();
    if (!root) return;
    void *(*timer_add)(double, int (*)(void *), void *) = dlsym(RTLD_NEXT, "ecore_timer_add");
    void *(*handler_add)(int, int (*)(void *, int, void *), void *) = dlsym(RTLD_NEXT, "ecore_event_handler_add");
    const int *exit_type = dlsym(RTLD_NEXT, "ECORE_EXE_EVENT_DEL");
    if (!timer_add || !handler_add || !exit_type || !*exit_type) return;
    memset(&loader, 0, sizeof loader);
    loader.mount_watch = -1;
    loader.owner = pid;
    loader.last_exit = -1;
    snprintf(loader.root, sizeof loader.root, "%s", root);
    snprintf(loader.script, sizeof loader.script, "%s/ram-start.sh", root);
    snprintf(loader.root_env, sizeof loader.root_env, "NX300M_MOD_ROOT=%s", root);
    loader.exit_handler = handler_add(*exit_type, child_exit_event, NULL);
    if (loader.exit_handler) loader.timer = timer_add(0.5, service_tick, NULL);
    if (loader.timer) mount_namespace_changed();
    publish_status(loader.timer ? "timer-registered" : "timer-registration-failed");
}

void ecore_main_loop_begin(void) {
    if (camera_process()) register_timer();
    void (*begin)(void) = dlsym(RTLD_NEXT, "ecore_main_loop_begin");
    if (begin) begin();
    reap_child();
}
