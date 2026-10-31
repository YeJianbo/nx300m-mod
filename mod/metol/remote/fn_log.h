/* Event-driven Fn-entry log on the SD card. The RAM state directory holds
 * daemon stderr only until power-off, so a physical camera failure leaves no
 * evidence. When NX_FN_KEY_LOG names a file, rare events (stage results,
 * key presses, panel forks) are appended there; the card reader then shows
 * the whole chain without a network connection. Heartbeats stay out on
 * purpose: the file must not grow while the camera idles. */
#ifndef NX_REMOTE_FN_LOG_H
#define NX_REMOTE_FN_LOG_H
#include <stdarg.h>
static struct { char path[PATH_MAX]; } fn_log_state;
static void fn_log(const char *fmt, ...) {
    if (!fn_log_state.path[0]) return;
    int fd = open(fn_log_state.path, O_WRONLY|O_CREAT|O_APPEND|O_CLOEXEC, 0600);
    if (fd < 0) return;
    struct timespec wall;
    char line[256];
    clock_gettime(CLOCK_REALTIME, &wall);
    int size = snprintf(line, sizeof line, "%lld [%ld] ",
        (long long)wall.tv_sec*1000+wall.tv_nsec/1000000, (long)getpid());
    if (size > 0 && size < (int)sizeof line) {
        va_list args;
        va_start(args, fmt);
        int rest = vsnprintf(line+size, sizeof line-size, fmt, args);
        va_end(args);
        if (rest > 0 && size+rest < (int)sizeof line) {
            line[size+rest] = '\n';
            ssize_t ignored = write(fd, line, size+rest+1);
            (void)ignored;
        }
    }
    close(fd);
}
static void fn_log_init(void) {
    const char *path = getenv("NX_FN_KEY_LOG");
    fn_log_state.path[0] = 0;
    if (!path || !*path || strlen(path) >= sizeof fn_log_state.path) return;
    strcpy(fn_log_state.path, path);
}
#endif
