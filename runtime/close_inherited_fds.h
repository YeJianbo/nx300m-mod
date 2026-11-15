#ifndef NX_CLOSE_INHERITED_FDS_H
#define NX_CLOSE_INHERITED_FDS_H
/* Direct getdents64 avoids allocator/stdio locks in a threaded app's fork
 * child, and closes descriptors above a subsequently lowered RLIMIT_NOFILE.
 *
 * Define NX_CLOSE_INHERITED_FDS_FREESTANDING before including this header
 * from the -nostdlib/-ffreestanding fork child (runtime/
 * camera_firmware_mod.c): that translation unit provides its own open()/
 * close() extern declarations, and the ARM EABI syscall number plus open(2)
 * flag values below are fixed kernel ABI constants (arch/arm/tools/
 * syscall.tbl and asm-generic/fcntl.h). Both inclusion modes share one
 * implementation; only the syscall entry and constants differ. */
#ifdef NX_CLOSE_INHERITED_FDS_FREESTANDING
typedef unsigned long long nx_close_fd_uint64;
typedef long long nx_close_fd_int64;
#ifndef O_RDONLY
#define O_RDONLY 0
#endif
#ifndef O_DIRECTORY
#define O_DIRECTORY 040000
#endif
#ifndef O_CLOEXEC
#define O_CLOEXEC 02000000
#endif
#ifndef __NR_getdents64
#define __NR_getdents64 217 /* ARM EABI */
#endif
static long nx_close_getdents64(int fd,void *buffer,unsigned int length) {
    register long number __asm__("r7")=__NR_getdents64;
    register long a0 __asm__("r0")=fd;
    register long a1 __asm__("r1")=(long)buffer;
    register long a2 __asm__("r2")=(long)length;
    __asm__ volatile("swi 0"
                     : "=r"(a0)
                     : "r"(number),"r"(a0),"r"(a1),"r"(a2)
                     : "memory");
    return a0;
}
#else
#include <stdint.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <sys/syscall.h>
typedef uint64_t nx_close_fd_uint64;
typedef int64_t nx_close_fd_int64;
static long nx_close_getdents64(int fd,void *buffer,unsigned int length) {
    return syscall(__NR_getdents64,fd,buffer,length);
}
#endif

static int nx_close_inherited_fds(void) {
    struct nx_kernel_dirent64 {
        nx_close_fd_uint64 ino; nx_close_fd_int64 offset; unsigned short length;
        unsigned char type; char name[];
    };
    char entries[4096] __attribute__((aligned(8)));
    int directory=open("/proc/self/fd",O_RDONLY|O_DIRECTORY|O_CLOEXEC);
    if(directory<0)return -1;
    for(;;) {
        long bytes=nx_close_getdents64(directory,entries,sizeof entries);
#ifdef NX_CLOSE_INHERITED_FDS_FREESTANDING
        if(bytes==-4)continue; /* raw kernel call: -EINTR */
#else
        if(bytes<0 && errno==EINTR)continue;
#endif
        if(bytes<0){close(directory);return -1;}
        if(!bytes)break;
        for(long offset=0;offset<bytes;) {
            struct nx_kernel_dirent64 *entry=(void *)(entries+offset);
            if(entry->length<20 || offset+entry->length>bytes){close(directory);return -1;}
            unsigned int fd=0;const char *p=entry->name;
            while(*p>='0' && *p<='9')fd=fd*10+(unsigned int)(*p++-'0');
            if(!*p && p!=entry->name && fd>2 && fd!=(unsigned int)directory)close((int)fd);
            offset+=entry->length;
        }
    }
    close(directory);return 0;
}
#endif
