#ifndef NX_MENU_LIVE_FRAME_POSIX_H
#define NX_MENU_LIVE_FRAME_POSIX_H
#include <sys/mman.h>
#include <sys/vfs.h>
#include "menu_live_frame.h"
extern int __fxstat64(int,int,struct stat64 *);
static struct nx_menu_frame *nx_menu_frame_map(void) {
    int created=1;
    int fd=open(NX_MENU_FRAME_PATH,O_RDWR|O_CREAT|O_EXCL|O_NOFOLLOW|O_NONBLOCK,0600);
    if(fd<0&&errno==EEXIST){created=0;fd=open(NX_MENU_FRAME_PATH,O_RDWR|O_NOFOLLOW|O_NONBLOCK);}
    if(fd<0)return NULL;
    struct stat64 st;struct statfs fs;
    int ok=!__fxstat64(3,fd,&st)&&S_ISREG(st.st_mode)&&st.st_uid==geteuid()&&st.st_nlink==1&&
        !fstatfs(fd,&fs)&&((unsigned)fs.f_type==0x01021994U||(unsigned)fs.f_type==0x858458f6U);
    if(ok&&created)ok=!ftruncate(fd,sizeof(struct nx_menu_frame));
    else if(ok)ok=st.st_size==(off64_t)sizeof(struct nx_menu_frame);
    void *map=ok?mmap(NULL,sizeof(struct nx_menu_frame),PROT_READ|PROT_WRITE,MAP_SHARED,fd,0):MAP_FAILED;
    close(fd);
    if(map==MAP_FAILED)return NULL;
    struct nx_menu_frame *p=map;
    if(created) {
        p->size=sizeof *p;
        __atomic_store_n(&p->magic,NX_MENU_FRAME_MAGIC,__ATOMIC_RELEASE);
    }
    if(__atomic_load_n(&p->magic,__ATOMIC_ACQUIRE)!=NX_MENU_FRAME_MAGIC||p->size!=sizeof *p){munmap(p,sizeof *p);return NULL;}
    return p;
}
#endif
