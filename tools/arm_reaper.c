/* Test-fixture subreaper for the private ARM VM (not shipped).
 *
 * PID 1 in the VM is a BusyBox shell. Daemons started by the production
 * scripts are orphaned when their launcher exits, and a killed orphan stays
 * a zombie under PID 1, so hotspot.sh's "/proc/<pid> still exists" wait runs
 * into its 2 s cap for every worker. That made a stop+start cycle exceed the
 * panel's real 12 s backend limit. This program becomes a child subreaper,
 * runs the probe as its child, and reaps every reparented orphan, which is
 * what the camera's init does. */
#include <sys/prctl.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <unistd.h>

int main(int argc, char **argv) {
    if (argc < 2) return 2;
    /* The cross headers redirect prctl() to __prctl_time64, absent from the
     * stock glibc 2.13; the raw syscall is the same call. */
    if (syscall(SYS_prctl, PR_SET_CHILD_SUBREAPER, 1) != 0) return 3;
    pid_t child = fork();
    if (child < 0) return 4;
    if (child == 0) {
        execvp(argv[1], argv + 1);
        _exit(127);
    }
    int status = 0;
    for (;;) {
        pid_t done = waitpid(-1, &status, 0);
        if (done < 0) return 5;
        if (done == child)
            return WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status);
    }
}
