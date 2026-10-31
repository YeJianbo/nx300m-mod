#ifndef NX_CLOSE_INHERITED_FDS_H
#define NX_CLOSE_INHERITED_FDS_H
#include <stdint.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <sys/syscall.h>

/* Direct getdents64 avoids allocator/stdio locks in a threaded app's fork
 * child, and closes descriptors above a subsequently lowered RLIMIT_NOFILE. */
static int nx_close_inherited_fds(void) {
    struct nx_kernel_dirent64 {
        uint64_t ino; int64_t offset; unsigned short length;
        unsigned char type; char name[];
    };
    char entries[4096] __attribute__((aligned(8)));
    int directory=open("/proc/self/fd",O_RDONLY|O_DIRECTORY|O_CLOEXEC);
    if(directory<0)return -1;
    for(;;) {
        long bytes=syscall(__NR_getdents64,directory,entries,sizeof entries);
        if(bytes<0 && errno==EINTR)continue;
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
