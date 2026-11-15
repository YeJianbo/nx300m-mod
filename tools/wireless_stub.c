/* Multicall command double for isolated wireless lifecycle tests (2026-11-11).
 *
 * One x86 binary, dispatched by argv[0] basename (hard-linked as hostapd,
 * udhcpd, insmod, ip, busybox.arm), standing in for the camera binaries while
 * the REAL production control scripts run inside a private mount/PID/NET
 * namespace. Not shipped to the camera; built on demand by the test harness.
 *
 * Behaviour:
 *  - every invocation appends "CALL <name> <sanitized args>" to $STUB_LOG
 *    (the telnetd -l path and mkpasswd passwords are never written there)
 *  - $STUB_FAIL selects a staged failure: hostapd, udhcpd, telnetd,
 *    insmod-<file>, ip-linkup, ip-add
 *  - $STUB_SLOW=<name> delays that daemon by 3 s (stop-while-starting test)
 *  - $STUB_HANG=<name> makes that daemon ignore SIGTERM (KILL-fallback test)
 *  - hostapd/udhcpd/telnetd daemonize like the real ones and write real PID
 *    files, so the scripts' /proc/<pid>/exe identity checks are exercised
 *  - the ip double keeps interface addresses in $STUB_STATE/<dev>.addr
 *  - the busybox double implements telnetd (real listen socket on the
 *    requested address plus one session-lookalike child) and mkpasswd
 */
/* Host builds want _GNU_SOURCE; the ARM fixture build against the camera's
 * stock libc-2.13 passes STUB_NO_GNU_SOURCE because the cross toolchain's
 * GCC >= 13 headers then redirect ISOC23 symbols (strtol -> __isoc23_strtol)
 * that stock libc-2.13 does not export. gnu99 defaults cover the rest. */
#if !defined(STUB_NO_GNU_SOURCE) && !defined(_GNU_SOURCE)
#define _GNU_SOURCE
#endif
/* The cross toolchain (glibc headers + GCC >= 13) redirects ISOC23 strtol to
 * __isoc23_strtol, which the camera's stock libc-2.13 does not export. Alias
 * it to the classic implementation for every fixture build of this double. */
#if defined(__GLIBC__)
__asm__(".globl __isoc23_strtol\n\t.set __isoc23_strtol, strtol");
#endif
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>
#include <unistd.h>

static const char *stub_base;

/* The modern cross headers route setsockopt(2) through __setsockopt64, which
 * the stock glibc 2.13 does not export. The ARM EABI kernel keeps the classic
 * socketcall multiplexer at 102 with SYS_SETSOCKOPT=14, so route through it
 * directly (only used by the telnetd double's listen socket). */
static int stub_setsockopt(int fd, int level, int optname, const void *optval, unsigned optlen) {
    unsigned long args[5] = {(unsigned long)fd, (unsigned long)level,
                             (unsigned long)optname, (unsigned long)optval,
                             (unsigned long)optlen};
    return (int)syscall(102, 14, args);
}
#define setsockopt stub_setsockopt

static void log_call(const char *fmt, ...) {
    const char *log = getenv("STUB_LOG");
    if (!log || !*log) return;
    FILE *f = fopen(log, "a");
    if (!f) return;
    va_list ap;
    fprintf(f, "CALL %s", stub_base);
    va_start(ap, fmt);
    vfprintf(f, fmt, ap);
    va_end(ap);
    fputc('\n', f);
    fclose(f);
}

static int fail_selected(const char *what) {
    const char *f = getenv("STUB_FAIL");
    return f && *f && !strcmp(f, what);
}
static int slow_selected(const char *what) {
    const char *f = getenv("STUB_SLOW");
    return f && *f && !strcmp(f, what);
}
/* TEST ONLY: $STUB_EXEC_ON_TERM=<stub-name> makes that daemon's child replace
 * its own image on SIGTERM (same PID, different exe: /bin/sleep). This gives
 * stop-path tests a deterministic same-slot identity change, standing in for
 * the exit-then-PID-reuse race that cannot be orchestrated offline. */
static void exec_on_term_handler(int sig) {
    (void)sig;
    execl("/bin/sleep", "sleep", "120", (char *)0);
    _exit(0);
}

static void maybe_hang(void) {
    const char *f = getenv("STUB_HANG");
    if (f && *f && !strcmp(f, stub_base)) signal(SIGTERM, SIG_IGN);
    const char *e = getenv("STUB_EXEC_ON_TERM");
    if (e && *e && !strcmp(e, stub_base)) signal(SIGTERM, exec_on_term_handler);
}

static void write_pid_file(const char *path) {
    if (!path || !*path) return;
    FILE *f = fopen(path, "w");
    if (f) { fprintf(f, "%ld\n", (long)getpid()); fclose(f); }
    /* The real daemons create the file writable by root only. */
    chmod(path, 0644);
}

/* daemonize: parent exits 0 after the child is running and has written its
 * pid file, matching how hostapd -B and udhcpd behave. The child detaches
 * stdin/stdout/stderr like a real daemon so it never keeps a test pipe or a
 * lock fd alive after the control script exits. */
static pid_t daemon_child(void) {
    pid_t pid = fork();
    if (pid > 0) return pid;          /* caller exits with 0 */
    if (pid < 0) return -1;
    setsid();
    signal(SIGHUP, SIG_IGN);
    int devnull = open("/dev/null", O_RDWR);
    if (devnull >= 0) {
        dup2(devnull, 0); dup2(devnull, 1); dup2(devnull, 2);
        if (devnull > 2) close(devnull);
    }
    for (int fd = 3; fd < 64; fd++) close(fd);
    maybe_hang();
    return 0;                          /* child continues */
}

static void sleep_forever(void) {
    for (;;) sleep(3600);
}

static int do_hostapd(int argc, char **argv) {
    const char *conf = NULL, *pidfile = NULL;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "-B")) continue;
        if (!strcmp(argv[i], "-P") && i + 1 < argc) { pidfile = argv[++i]; continue; }
        if (!strncmp(argv[i], "-P", 2) && argv[i][2]) { pidfile = argv[i] + 2; continue; }
        if (argv[i][0] != '-' && !conf) conf = argv[i];
    }
    log_call(" start %s %s -B -P%s", argv[0], conf ? conf : "-", pidfile ? pidfile : "-");
    if (fail_selected("hostapd")) return 1;
    if (slow_selected("hostapd")) sleep(3);
    if (daemon_child() > 0) return 0;
    write_pid_file(pidfile);
    sleep_forever();
    return 0;
}

static int do_udhcpd(int argc, char **argv) {
    const char *conf = argc > 1 ? argv[1] : NULL;
    char pidfile[256] = "";
    if (conf) {
        FILE *f = fopen(conf, "r");
        if (f) {
            char line[256];
            while (fgets(line, sizeof line, f))
                if (!strncmp(line, "pidfile ", 8)) {
                    line[strcspn(line, "\r\n")] = 0;
                    snprintf(pidfile, sizeof pidfile, "%s", line + 8);
                }
            fclose(f);
        }
    }
    log_call(" start %s %s", argv[0], conf ? conf : "-");
    if (fail_selected("udhcpd")) return 1;
    if (slow_selected("udhcpd")) sleep(3);
    if (daemon_child() > 0) return 0;
    write_pid_file(pidfile);
    sleep_forever();
    return 0;
}

static int do_insmod(int argc, char **argv) {
    const char *file = argc > 1 ? argv[1] : "-";
    const char *base = strrchr(file, '/');
    base = base ? base + 1 : file;
    log_call(" insmod %s", file);
    const char *f = getenv("STUB_FAIL");
    if (f && *f && !strcmp(f, base)) return 1;
    return 0;
}

static int ip_cmdline(int argc, char **argv) {
    const char *state = getenv("STUB_STATE");
    int i = 1;
    if (i < argc && (!strcmp(argv[i], "-4") || !strcmp(argv[i], "-6"))) i++;
    if (i >= argc) return 0;
    if (!strcmp(argv[i], "addr") && i + 1 < argc && !strcmp(argv[i + 1], "show")) {
        const char *dev = NULL;
        for (int k = i; k + 1 < argc; k++)
            if (!strcmp(argv[k], "dev")) dev = argv[k + 1];
        if (state) {
            if (dev) {
                char path[512];
                snprintf(path, sizeof path, "%s/%s.addr", state, dev);
                FILE *f = fopen(path, "r");
                if (f) {
                    char addr[128] = "";
                    if (fgets(addr, sizeof addr, f)) addr[strcspn(addr, "\r\n")] = 0;
                    fclose(f);
                    if (addr[0])
                        printf("    inet %s/24 brd 255.255.255.255 scope global %s\n"
                               "       valid_lft forever preferred_lft forever\n", addr, dev);
                }
            } else {
                /* print every tracked device */
                char path[512];
                snprintf(path, sizeof path, "%s", state);
                return 0;
            }
        }
        return 0;
    }
    if (!strcmp(argv[i], "link") && i + 2 < argc && !strcmp(argv[i + 1], "set")) {
        log_call(" ip link set %s %s", argv[i + 2], i + 3 < argc ? argv[i + 3] : "?");
        if (i + 3 < argc && !strcmp(argv[i + 3], "up") && fail_selected("ip-linkup")) return 1;
        return 0;
    }
    if (!strcmp(argv[i], "addr") && i + 3 < argc && !strcmp(argv[i + 1], "add")) {
        const char *addr = argv[i + 2], *dev = NULL;
        for (int k = i + 2; k + 1 < argc; k++)
            if (!strcmp(argv[k], "dev")) dev = argv[k + 1];
        log_call(" ip addr add %s dev %s", addr, dev ? dev : "?");
        if (fail_selected("ip-add")) return 1;
        if (state && dev) {
            char path[512], plain[128] = "";
            snprintf(plain, sizeof plain, "%s", addr);
            char *slash = strchr(plain, '/');
            if (slash) *slash = 0;
            snprintf(path, sizeof path, "%s/%s.addr", state, dev);
            FILE *f = fopen(path, "w");
            if (f) { fprintf(f, "%s\n", plain); fclose(f); }
        }
        return 0;
    }
    if (!strcmp(argv[i], "addr") && i + 3 < argc && !strcmp(argv[i + 1], "del")) {
        const char *addr = argv[i + 2], *dev = NULL;
        for (int k = i + 2; k + 1 < argc; k++)
            if (!strcmp(argv[k], "dev")) dev = argv[k + 1];
        log_call(" ip addr del %s dev %s", addr, dev ? dev : "?");
        if (state && dev) {
            char path[512];
            snprintf(path, sizeof path, "%s/%s.addr", state, dev);
            unlink(path);
        }
        return 0;
    }
    log_call(" ip (unhandled)");
    return 0;
}

static unsigned fnv1a(const char *s) {
    unsigned h = 2166136261u;
    while (*s) { h ^= (unsigned char)*s++; h *= 16777619u; }
    return h;
}

static int do_telnetd(int argc, char **argv) {
    const char *addr = NULL;
    int port = 23;
    for (int i = 2; i < argc; i++) {
        if (!strcmp(argv[i], "-p") && i + 1 < argc) { port = atoi(argv[++i]); continue; }
        if (!strcmp(argv[i], "-b") && i + 1 < argc) { addr = argv[++i]; continue; }
        if (!strcmp(argv[i], "-K") || !strcmp(argv[i], "-F")) continue;
        if (!strcmp(argv[i], "-l") && i + 1 < argc) {
            /* record only that a login program was configured, never paths
             * that could carry secrets into the call log */
            log_call(" telnetd -l <program>");
            i++;
            continue;
        }
    }
    log_call(" telnetd -p %d -b %s -K", port, addr ? addr : "-");
    if (fail_selected("telnetd")) return 1;
    if (slow_selected("telnetd")) sleep(3);
    pid_t pid = fork();
    if (pid > 0) return 0;
    if (pid < 0) return 1;
    setsid();
    int devnull = open("/dev/null", O_RDWR);
    if (devnull >= 0) {
        dup2(devnull, 0); dup2(devnull, 1); dup2(devnull, 2);
        if (devnull > 2) close(devnull);
    }
    for (int fd = 3; fd < 64; fd++) close(fd);
    maybe_hang();
    /* One session-lookalike child (same exe, telnetd cmdline) so the disable
     * sweep has an active connection to clean up. */
    if (!fork()) sleep_forever();
#ifndef IP_FREEBIND
#define IP_FREEBIND 15
#endif
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd >= 0) {
        int one = 1;
        setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
        /* The test namespace has no real wlan0 address; allow binding the
         * requested one so the /proc/net/tcp LISTEN check stays faithful. */
        setsockopt(fd, SOL_IP, IP_FREEBIND, &one, sizeof one);
        struct sockaddr_in sa;
        memset(&sa, 0, sizeof sa);
        sa.sin_family = AF_INET;
        sa.sin_port = htons((unsigned short)port);
        sa.sin_addr.s_addr = addr ? inet_addr(addr) : INADDR_ANY;
        if (bind(fd, (struct sockaddr *)&sa, sizeof sa) == 0 && listen(fd, 4) == 0) {
            log_call(" telnetd LISTEN %s:%d", addr ? addr : "0.0.0.0", port);
            for (;;) {
                int c = accept(fd, NULL, NULL);
                if (c >= 0) close(c);
                else sleep(1);
            }
        }
        close(fd);
    }
    log_call(" telnetd BIND-FAILED %s:%d", addr ? addr : "?", port);
    sleep_forever();
    return 0;
}

static int do_mkpasswd(int argc, char **argv) {
    /* Mirrors busybox mkpasswd: the password comes from argv or, with
     * -P FD, from that file descriptor. The production script now always
     * uses -P 0, so the stdin path must behave like the real applet. */
    const char *password = NULL;
    int password_fd = -1;
    for (int i = 2; i < argc; i++) {
        if (!strcmp(argv[i], "-P") && i + 1 < argc) password_fd = atoi(argv[++i]);
        else if (strcmp(argv[i - 1], "-m")) password = argv[i];
    }
    char from_stdin[256] = "";
    if (password_fd >= 0) {
        ssize_t n;
        size_t total = 0;
        while (total < sizeof from_stdin - 1 &&
               (n = read(password_fd, from_stdin + total, sizeof from_stdin - 1 - total)) > 0)
            total += (size_t)n;
        while (total && (from_stdin[total - 1] == '\n' || from_stdin[total - 1] == '\r'))
            from_stdin[--total] = 0;
        from_stdin[total] = 0;
        password = from_stdin;
    }
    log_call(" mkpasswd -m sha512 <redacted>");
    printf("$6$stub$%08x\n", password ? fnv1a(password) : 0u);
    return 0;
}

int main(int argc, char **argv) {
    stub_base = strrchr(argv[0], '/');
    stub_base = stub_base ? stub_base + 1 : argv[0];
    signal(SIGPIPE, SIG_IGN);
    if (!strcmp(stub_base, "hostapd")) return do_hostapd(argc, argv);
    if (!strcmp(stub_base, "udhcpd")) return do_udhcpd(argc, argv);
    if (!strcmp(stub_base, "insmod")) return do_insmod(argc, argv);
    if (!strcmp(stub_base, "ip")) return ip_cmdline(argc, argv);
    if (!strcmp(stub_base, "busybox.arm") || !strcmp(stub_base, "busybox")) {
        if (argc > 1 && !strcmp(argv[1], "telnetd")) return do_telnetd(argc, argv);
        if (argc > 1 && !strcmp(argv[1], "mkpasswd")) return do_mkpasswd(argc, argv);
        log_call(" busybox (unhandled applet)");
        return 64;
    }
    log_call(" (unknown)");
    return 64;
}
