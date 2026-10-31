/* Modern headers name stat64/fcntl64 directly. The stock glibc 2.13 exports
 * the same ARM stat64 ABI through __xstat64 and keeps fcntl under its old name.
 * Original __xstat64 at 0xb5910 forwards path/buffer to ARM syscall 195.
 */
#ifndef _LARGEFILE64_SOURCE
#define _LARGEFILE64_SOURCE
#endif
#include <sys/stat.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdint.h>

extern int __xstat64(int, const char *, struct stat64 *);
extern int __lxstat64(int, const char *, struct stat64 *);
extern int __fxstat64(int, int, struct stat64 *);
extern int camera_fcntl(int, int, ...) __asm__("fcntl");

int stat64(const char *path, struct stat64 *value) { return __xstat64(3,path,value); }
int lstat64(const char *path, struct stat64 *value) { return __lxstat64(3,path,value); }
int fstat64(int fd, struct stat64 *value) { return __fxstat64(3,fd,value); }
int fcntl64(int fd, int command, ...) {
    uintptr_t argument=0;
    if (command!=F_GETFL && command!=F_GETFD && command!=F_GETOWN) {
        va_list args;va_start(args,command);argument=va_arg(args,uintptr_t);va_end(args);
    }
    return camera_fcntl(fd,command,argument);
}
