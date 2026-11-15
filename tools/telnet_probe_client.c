/* Telnet protocol test client for the isolated login-chain test (2026-11-11).
 *
 * Runs INSIDE the same isolated network namespace as the real busybox telnetd
 * and connects to its loopback listener. Implements just enough of RFC 854 to
 * drive the login: answers every DO with WONT and every WILL with DONT, skips
 * subnegotiation, un-escapes IAC IAC, and treats everything else as data.
 *
 *   telnet_probe_client.arm <addr> <port> <mode> [password]
 *
 * Modes and exit codes:
 *   login   - wait for the password prompt, authenticate, run the fixed test
 *             command and wait for its marker. 0 = full chain OK.
 *   reject  - send a wrong password three times and require the server to
 *             close the connection. 0 = rejected and closed as expected.
 *   refused - require the connection to be refused (service closed). 0 = port
 *             unreachable as expected.
 * Any other outcome exits nonzero with a diagnostic on stdout.
 *
 * Build (stock glibc 2.13, same recipe as the login tool):
 *   arm-linux-gnueabi-gcc -std=c99 -O1 -Wall -Wextra -Werror -D_GNU_SOURCE \
 *     -include mod/metol/remote/compat.h -mfloat-abi=soft -fno-pic -no-pie \
 *     -fno-stack-protector -nostdlib -nostartfiles -Wl,-e,_start \
 *     -Wl,--dynamic-linker=/lib/ld-linux.so.3 -Wl,--allow-shlib-undefined \
 *     -Wl,-rpath-link,"$rf/lib" tools/telnet_probe_client.c \
 *     mod/metol/remote/compat.c work/reference/metol/test_server/filmlab/src/crt0_arm.S \
 *     "$rf/lib/libc-2.13.so" -lgcc -o .../telnet_probe_client.arm
 */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <stdlib.h>
#include <string.h>

#include <sys/socket.h>
#include <sys/time.h>
#include <sys/types.h>
#include <unistd.h>

#define IAC 255
#define DONT 254
#define WONT 252
#define DO 253
#define WILL 251
#define SB 250
#define SE 240
#define WAIT_SECONDS 8
#define MARKER "MOD_TELNET_OK_7391"

/* The modern cross headers route select(2) through __select64, a symbol the
 * stock glibc 2.13 does not export. ARM EABI keeps the classic _newselect
 * syscall at 142, which the 2.13 libc's syscall() wrapper reaches directly. */
#ifndef SYS__newselect
#define SYS__newselect 142
#endif
/* Plain glibc socket wrappers (socketcall on ARM). qemu-user 7.2 mistranslates
 * that multiplexer, which is why earlier non-VM attempts failed; the real ARM
 * kernel inside the private VM executes it natively and correctly. */

static int say(const char *s) {
    size_t left = strlen(s);
    while (left) {
        ssize_t n = write(1, s, left);
        if (n < 0) return -1;
        s += n; left -= (size_t)n;
    }
    return 0;
}

/* Read with a whole-step timeout; returns 1 with data, 0 on timeout/close. */
static int wait_readable(int fd) {
    struct timeval tv;
    fd_set set;
    FD_ZERO(&set);
    FD_SET(fd, &set);
    tv.tv_sec = WAIT_SECONDS;
    tv.tv_usec = 0;
    return syscall(SYS__newselect, fd + 1, &set, NULL, NULL, &tv) > 0;
}

static ssize_t read_some(int fd, char *buffer, size_t size) {
    if (!wait_readable(fd)) return 0;
    return read(fd, buffer, size);
}

static int send_all(int fd, const char *data, size_t left) {
    while (left) {
        ssize_t n = write(fd, data, left);
        if (n < 0) { if (errno == EINTR) continue; return -1; }
        data += n; left -= (size_t)n;
    }
    return 0;
}

/* Wait until needle appears in the (IAC-filtered) stream. Returns 0 when
 * found, 1 on timeout/close, and stores any trailing bytes in tail/tail_len
 * so consecutive waits keep the context. */
static int wait_for(int fd, const char *needle,
                    char *window, size_t window_size,
                    size_t *used) {
    size_t needle_len = strlen(needle);
    for (;;) {
        if (*used >= needle_len && strstr(window, needle)) return 0;
        char raw[512];
        ssize_t n = read_some(fd, raw, sizeof raw);
        if (n <= 0) return 1;
        for (ssize_t i = 0; i < n; i++) {
            unsigned char c = (unsigned char)raw[i];
            if (c != IAC) {
                if (*used + 1 < window_size) { window[(*used)++] = (char)c; window[*used] = 0; }
                continue;
            }
            i++;
            if (i >= n) break;
            unsigned char cmd = (unsigned char)raw[i];
            if (cmd == IAC) {           /* escaped 255: literal data byte */
                if (*used + 1 < window_size) { window[(*used)++] = (char)IAC; window[*used] = 0; }
                continue;
            }
            if (cmd == DO || cmd == WILL) {
                if (i + 1 >= n) break;
                unsigned char opt = (unsigned char)raw[i + 1];
                char reply[3] = {IAC, cmd == DO ? (char)WONT : (char)DONT, (char)opt};
                if (send_all(fd, reply, 3)) return 1;
                i++;
                continue;
            }
            if (cmd == SB) {             /* skip subnegotiation to SE */
                while (i < n && raw[i] != SE) i++;
                continue;
            }
            /* DONT/WONT and the rest need no answer from the client side */
        }
        /* keep the tail so a marker split across reads still matches */
        if (*used > needle_len + 64) {
            size_t keep = needle_len + 64;
            memmove(window, window + *used - keep, keep);
            *used = keep;
            window[*used] = 0;
        }
    }
}

static void drain(int fd) {
    char raw[512];
    for (;;) {
        if (!wait_readable(fd)) return;
        ssize_t n = read(fd, raw, sizeof raw);
        if (n <= 0) return;
    }
}

int main(int argc, char **argv) {
    if (argc < 4) { say("usage: client ADDR PORT MODE [PASSWORD]\n"); return 9; }
    const char *addr = argv[1];
    int port = 0;
    for (const char *p = argv[2]; *p; p++) {
        if (*p < '0' || *p > '9') { say("bad port\n"); return 9; }
        port = port * 10 + (*p - '0');
    }
    const char *mode = argv[3];
    const char *password = argc > 4 ? argv[4] : "";

    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) { say("socket failed\n"); return 9; }
    struct sockaddr_in sa;
    memset(&sa, 0, sizeof sa);
    sa.sin_family = AF_INET;
    sa.sin_port = (unsigned short)((port >> 8) | ((port & 0xff) << 8)); /* htons */
    /* dotted-quad octet parser (no libc string helpers needed) */
    unsigned octet = 0; int shift = 24; int digits = 0;
    for (const char *p = addr;; p++) {
        if (*p >= '0' && *p <= '9') { octet = octet * 10 + (unsigned)(*p - '0'); digits++; continue; }
        if ((*p == '.' || !*p) && digits && octet < 256 && shift >= 0) {
            sa.sin_addr.s_addr |= (octet << shift);
            shift -= 8; octet = 0; digits = 0;
            if (!*p) break;
            continue;
        }
        say("bad address\n"); return 9;
    }
    if (connect(fd, (struct sockaddr *)&sa, sizeof sa) < 0) {
        if (getenv("NX_TELNET_CLIENT_DEBUG")) {
            static const char hexd[] = "0123456789abcdef";
            unsigned v = (unsigned)sa.sin_addr.s_addr;
            unsigned pv = (unsigned)sa.sin_port;
            char out[64]; int oi = 0;
            out[oi++] = 'e'; out[oi++] = 'r'; out[oi++] = 'r'; out[oi++] = '=';
            out[oi++] = hexd[(errno >> 12) & 0xf]; out[oi++] = hexd[(errno >> 8) & 0xf];
            out[oi++] = hexd[(errno >> 4) & 0xf]; out[oi++] = hexd[errno & 0xf];
            out[oi++] = ' '; out[oi++] = 'a'; out[oi++] = 'd'; out[oi++] = 'd'; out[oi++] = 'r'; out[oi++] = '=';
            for (int k = 28; k >= 0; k -= 4) out[oi++] = hexd[(v >> k) & 0xf];
            out[oi++] = ' '; out[oi++] = 'p'; out[oi++] = 'w'; out[oi++] = '=';
            for (int k = 28; k >= 0; k -= 4) out[oi++] = hexd[(pv >> k) & 0xf];
            out[oi++] = '\n'; out[oi] = 0;
            say(out);
        }
        if (!strcmp(mode, "refused")) { say("REFUSED-AS-EXPECTED\n"); return 0; }
        say("connect failed\n"); return 4;
    }
    if (!strcmp(mode, "refused")) { say("UNEXPECTED-CONNECT\n"); return 4; }

    static char window[2048];
    size_t used = 0;
    window[0] = 0;
    if (!strcmp(mode, "reject")) {
        for (int attempt = 0; attempt < 3; attempt++) {
            if (wait_for(fd, "Password:", window, sizeof window, &used)) { say("no prompt\n"); return 4; }
            if (send_all(fd, password, strlen(password))) return 4;
            if (send_all(fd, "\r\n", 2)) return 4;
            drain(fd);
        }
        if (wait_for(fd, "Too many failures", window, sizeof window, &used)) { say("no failure banner\n"); return 4; }
        say("REJECTED-AS-EXPECTED\n");
        return 0;
    }
    if (!strcmp(mode, "login")) {
        if (wait_for(fd, "Password:", window, sizeof window, &used)) { say("no prompt\n"); return 4; }
        if (send_all(fd, password, strlen(password))) return 4;
        if (send_all(fd, "\r\n", 2)) return 4;
        if (wait_for(fd, "Login OK", window, sizeof window, &used)) {
            if (strstr(window, "Login incorrect")) { say("REJECTED-UNEXPECTEDLY\n"); return 4; }
            say("no login ok\n"); return 4;
        }
        /* the debug shell is up; run the fixed test command */
        if (send_all(fd, "echo " MARKER "\r\n", strlen("echo " MARKER "\r\n"))) return 4;
        if (wait_for(fd, MARKER, window, sizeof window, &used)) { say("no marker\n"); return 4; }
        say("LOGIN-CHAIN-OK\n");
        return 0;
    }
    say("unknown mode\n");
    return 9;
}
