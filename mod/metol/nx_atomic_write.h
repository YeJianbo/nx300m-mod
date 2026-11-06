/* Atomic text persistence for card-side small state files.
 * Same-directory temp -> full write -> fsync/close -> rename -> fsync dir.
 * Returns 1 saved, 0 failed with the old file untouched, -1 published but
 * directory durability unconfirmed (the old content is NOT restored). */
#ifndef NX_ATOMIC_WRITE_H
#define NX_ATOMIC_WRITE_H
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/stat.h>
#include <sys/types.h>
static int nx_atomic_text_write(const char *directory,const char *name,const char *text,size_t length) {
    char final_file[768],temp[768];
    int final_n=snprintf(final_file,sizeof final_file,"%s/%s",directory,name);
    if(final_n<0||final_n>=(int)sizeof final_file)return 0;
    int fd=-1,attempt;
    for(attempt=0;attempt<4&&fd<0;attempt++) {
        int temp_n=snprintf(temp,sizeof temp,"%s/.%s.nx-new-%ld-%d",directory,name,(long)getpid(),attempt);
        if(temp_n<0||temp_n>=(int)sizeof temp)return 0;
        fd=open(temp,O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW,0600);
        if(fd<0&&(errno!=EEXIST&&errno!=EINTR))return 0;
    }
    if(fd<0)return 0;
    int ok=1;size_t written=0;
    while(ok&&written<length) {
        ssize_t n=write(fd,text+written,length-written);
        if(n>0)written+=(size_t)n;
        else if(n<0&&errno==EINTR)continue;
        else ok=0;
    }
    if(ok&&fsync(fd))ok=0;
    if(close(fd)&&ok)ok=0;
    if(!ok){unlink(temp);return 0;}
    if(rename(temp,final_file)){unlink(temp);return 0;}
    /* After rename the new content is live. A failed directory sync leaves it
     * published but not durable; report that instead of claiming a rollback. */
    int dir=open(directory,O_RDONLY|O_DIRECTORY);
    if(dir<0)return -1;
    int confirmed=!fsync(dir);
    if(close(dir)&&confirmed)return -1;
    return confirmed?1:-1;
}
#endif
