/* MOD Telnet login tool (2026-11-11).
 *
 * busybox telnetd -l LOGIN executes LOGIN once per connection with the pty
 * as stdin/stdout. busybox does not run the -l string through a shell, so
 * the tool is started with a bare program path and locates its sha512-crypt
 * hash itself: the --hash-file PATH argument, then the NX_TELNET_HASH_FILE
 * environment variable (exported by telnet_toggle.sh before telnetd is
 * started), then <mod root>/.state/telnet-hash. The hash therefore never
 * appears in argv, and the password never appears anywhere in the process
 * list. The camera's /etc/shadow (root with an EMPTY password) is never read,
 * modified or consulted, and "-l /bin/sh" is never offered.
 *
 * It asks for the password with echo disabled, rehashes with crypt(3) and
 * compares in constant time. On success it execs /bin/sh for that session
 * only; on three failures it exits, and telnetd -K closes the connection.
 *
 * Build (same entry as the engine, links stock libcrypt.so.1):
 *   arm-linux-gnueabi-gcc -std=c99 -O1 -Wall -Wextra -Werror \
 *     -D_GNU_SOURCE -include mod/metol/remote/compat.h \
 *     -U_TIME_BITS -D_TIME_BITS=32 -U_FILE_OFFSET_BITS -D_FILE_OFFSET_BITS=64 \
 *     -mfloat-abi=soft -fno-pic -no-pie -fno-stack-protector \
 *     -nostdlib -nostartfiles -Wl,-e,_start \
 *     -Wl,--dynamic-linker=/lib/ld-linux.so.3 -Wl,--allow-shlib-undefined \
 *     -Wl,-rpath-link,"$rf/lib" tools/telnet_login.c mod/metol/remote/compat.c \
 *     work/reference/metol/test_server/filmlab/src/crt0_arm.S \
 *     "$rf/lib/libc-2.13.so" "$rf/lib/libcrypt.so.1" -lgcc \
 *     -o outputs/deployment/wireless-telnet/telnet_login.arm
 */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <termios.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/types.h>

extern char *crypt(const char *key, const char *setting);

#define PROMPT_PASS "Password: "
#define MAX_HASH 160
#define MAX_PASSWORD 128

static void write_all(const char *text) {
    size_t left = strlen(text);
    const char *p = text;
    while (left) {
        ssize_t n = write(STDOUT_FILENO, p, left);
        if (n < 0) { if (errno == EINTR) continue; _exit(1); }
        p += n; left -= (size_t)n;
    }
}

/* Load one line (the hash) from a 0600 state file, trimming CR/LF. */
static int load_hash_file(const char *path, char *buffer, size_t size) {
    int fd = open(path, O_RDONLY);
    size_t used = 0;
    if (fd < 0) return -1;
    while (used + 1 < size) {
        char c;
        ssize_t n = read(fd, &c, 1);
        if (n < 0) { if (errno == EINTR) continue; close(fd); return -1; }
        if (n == 0) break;
        if (c == '\n' || c == '\r') break;
        buffer[used++] = c;
    }
    close(fd);
    buffer[used] = 0;
    return (int)used;
}

/* Constant-time comparison so a network client cannot time the digest. */
static int same_secret(const char *computed, const char *expected) {
    unsigned diff = 0;
    size_t i = 0;
    for (;;) {
        unsigned char a = (unsigned char)computed[i];
        unsigned char b = (unsigned char)expected[i];
        diff |= (unsigned)(a ^ b);
        if (!a || !b) { diff |= (unsigned)(a | b); break; }  /* length mismatch adds diff */
        i++;
    }
    return diff == 0;
}

/*
 * Password reader for the pty telnetd hands us. The tty is switched to
 * no-echo, non-canonical mode, so every byte is handled here: CR LF, CR NUL
 * and bare CR all end the line, a lone CR never blocks waiting for a second
 * byte, and backspace edits the buffer like a normal login prompt.
 */
static int read_password(int fd, char *buffer, size_t size) {
    size_t used = 0;
    int pending = -1;
    for (;;) {
        char c;
        ssize_t n;
        if (pending >= 0) { c = (char)pending; pending = -1; }
        else {
            n = read(fd, &c, 1);
            if (n < 0) { if (errno == EINTR) continue; return -1; }
            if (n == 0) break;
        }
        if (c == '\n') break;
        if (c == '\r') {
            /* Consume a following LF or NUL only if it has already arrived;
             * a bare CR ends the line immediately instead of blocking. */
            int available = 0;
            if (ioctl(fd, FIONREAD, &available) == 0 && available > 0) {
                char next;
                if (read(fd, &next, 1) == 1) {
                    if (next == '\n' || next == '\0') break;
                    pending = (unsigned char)next;
                }
            }
            break;
        }
        if (c == 0x7f || c == '\b') {
            if (used) used--;
            continue;
        }
        if ((unsigned char)c < 0x20 && c != '\t') continue;  /* drop control bytes */
        if (used + 1 >= size) continue;                      /* keep longest prefix */
        buffer[used++] = c;
    }
    buffer[used] = 0;
    return (int)used;
}

int main(int argc, char **argv) {
    const char *hash = NULL;
    const char *hash_file = NULL;
    char hash_buffer[MAX_HASH];
    int i;
    for (i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--hash") && i + 1 < argc) { hash = argv[++i]; continue; }
        if (!strcmp(argv[i], "--hash-file") && i + 1 < argc) { hash_file = argv[++i]; continue; }
        /* a positional user name is accepted for compatibility; the session
         * always runs as the invoking uid (root from telnetd) */
    }
    if (!hash) {
        const char *env_file = getenv("NX_TELNET_HASH_FILE");
        if (hash_file || (env_file && *env_file)) {
            if (load_hash_file(hash_file ? hash_file : env_file,
                               hash_buffer, sizeof hash_buffer) < 0) {
                write_all("MOD telnet login: cannot read hash file\n");
                return 1;
            }
            hash = hash_buffer;
        } else {
            static char default_path[256];
            const char *root = getenv("NX300M_MOD_ROOT");
            if (!root || !*root) root = "/mnt/mmc/nx300m-mod";
            snprintf(default_path, sizeof default_path, "%s/.state/telnet-hash", root);
            if (load_hash_file(default_path, hash_buffer, sizeof hash_buffer) < 0) {
                write_all("MOD telnet login: cannot read hash file\n");
                return 1;
            }
            hash = hash_buffer;
        }
    }
    if (!hash || strncmp(hash, "$6$", 3)) {
        write_all("MOD telnet login: configuration error\n");
        return 1;
    }
    /* The session must be on a tty; telnetd guarantees one. */
    if (!isatty(STDIN_FILENO) || !isatty(STDOUT_FILENO)) {
        write_all("MOD telnet login: no terminal\n");
        return 1;
    }
    write_all("\nMOD Telnet (debug)\n");

    struct termios original, raw;
    if (tcgetattr(STDIN_FILENO, &original)) return 1;
    raw = original;
    raw.c_lflag &= ~(tcflag_t)(ECHO | ICANON);
    raw.c_cc[VMIN] = 1;
    raw.c_cc[VTIME] = 0;
    char password[MAX_PASSWORD];
    int attempt;
    for (attempt = 0; attempt < 3; attempt++) {
        write_all(PROMPT_PASS);
        if (tcsetattr(STDIN_FILENO, TCSAFLUSH, &raw)) return 1;
        int n = read_password(STDIN_FILENO, password, sizeof password);
        tcsetattr(STDIN_FILENO, TCSAFLUSH, &original);
        write_all("\n");
        if (n < 0) return 1;
        if (n == 0) continue;
        char *computed = crypt(password, hash);
        /* wipe the plaintext immediately */
        memset(password, 0, sizeof password);
        if (computed && same_secret(computed, hash)) {
            write_all("Login OK\n\n");
            /* A clean, minimal environment for the debug shell. */
            setenv("PATH", "/bin:/usr/bin:/sbin:/usr/sbin", 1);
            setenv("HOME", "/root", 1);
            setenv("TERM", "vt100", 1);
            unsetenv("NX_TELNET_HASH_FILE");
            unsetenv("LD_PRELOAD");
            unsetenv("LD_AUDIT");
            /* Login-shell convention: argv[0] starting with '-' makes busybox
             * ash source /etc/profile; no option string is passed. */
            char *shell_argv[] = {(char *)"-sh", NULL};
            execv("/bin/sh", shell_argv);
            write_all("MOD telnet login: cannot start shell\n");
            return 1;
        }
        write_all("Login incorrect\n");
    }
    write_all("Too many failures; connection closing.\n");
    return 1;
}
