/* Test-only child filter: reproduce absent Linux syscalls, never success. */
#include <errno.h>
#include <linux/filter.h>
#include <linux/seccomp.h>
#include <stddef.h>
#include <stdio.h>
#include <sys/prctl.h>
#include <sys/syscall.h>
#include <unistd.h>

#define DENY(number) \
    BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, (number), 0, 1), \
    BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | ENOSYS)

int main(int argc, char **argv) {
    struct sock_filter instructions[] = {
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, nr)),
        DENY(SYS_getrandom),
        DENY(SYS_epoll_create),
        DENY(SYS_epoll_create1),
        DENY(SYS_epoll_wait),
        DENY(SYS_epoll_pwait),
#ifdef SYS_epoll_pwait2
        DENY(SYS_epoll_pwait2),
#endif
        DENY(SYS_eventfd),
        DENY(SYS_eventfd2),
        DENY(SYS_timerfd_create),
        DENY(SYS_timerfd_settime),
        DENY(SYS_timerfd_gettime),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW),
    };
    struct sock_fprog program = {
        .len = (unsigned short)(sizeof(instructions) / sizeof(instructions[0])),
        .filter = instructions,
    };
    if (argc < 2) {
        fputs("usage: legacy-syscalls PROGRAM ARG...\n", stderr);
        return 2;
    }
    if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) ||
        prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, &program)) {
        perror("legacy syscall filter");
        return 2;
    }
    fputs("test-only ENOSYS filter enabled: getrandom, epoll, eventfd, timerfd\n", stderr);
    execvp(argv[1], argv + 1);
    perror("legacy syscall test exec");
    return 127;
}
