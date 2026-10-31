#ifndef NX_MEDIA_REPLACE_H
#define NX_MEDIA_REPLACE_H
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/file.h>
#include <unistd.h>

/* Caller resolves an existing file inside media_root. A unique same-directory
 * temporary avoids following a predictable .tmp symlink and keeps rename atomic.
 * No failure before rename may change the original file. */
static int nx_media_replace(const char *path,const void *data,size_t length) {
    struct stat original;
    if (!length || length>120U*1024U*1024U) { errno=EINVAL;return -1; }
    if (stat(path,&original)) return -1;
    if (!S_ISREG(original.st_mode)) { errno=EINVAL;return -1; }
    char temporary[PATH_MAX];
    int n=snprintf(temporary,sizeof temporary,"%s.upload-XXXXXX",path);
    if (n<0 || n>=(int)sizeof temporary) { errno=ENAMETOOLONG;return -1; }
    int fd=mkstemp(temporary);
    if (fd<0) return -1;
    int error=0;
    if (fchmod(fd,original.st_mode&0777)) error=errno;
    const unsigned char *cursor=data;
    size_t remaining=length;
    while (!error && remaining) {
        ssize_t written=write(fd,cursor,remaining);
        if (written<0 && errno==EINTR) continue;
        if (written<=0) { error=written<0?errno:EIO;break; }
        cursor+=written;remaining-=(size_t)written;
    }
    if (!error && fsync(fd)) error=errno;
    if (close(fd) && !error) error=errno;
    if (!error && rename(temporary,path)) error=errno;
    if (error) { unlink(temporary);errno=error;return -1; }
    return 0;
}

/* Streaming copy for the original backup and restore: never truncate either
 * source or destination until a complete independent copy has been flushed. */
static int nx_media_copy(const char *source,const char *destination) {
    int input=open(source,O_RDONLY|O_NOFOLLOW);
    if (input<0) return -1;
    struct stat before,after;
    if (fstat(input,&before) || !S_ISREG(before.st_mode) || before.st_size<=0 || before.st_size>120*1024*1024) {
        close(input);errno=EINVAL;return -1;
    }
    char temporary[PATH_MAX];
    int n=snprintf(temporary,sizeof temporary,"%s.copy-XXXXXX",destination);
    if (n<0 || n>=(int)sizeof temporary) { close(input);errno=ENAMETOOLONG;return -1; }
    int output=mkstemp(temporary);
    if (output<0) { int error=errno;close(input);errno=error;return -1; }
    int error=0;off_t total=0;
    if (fchmod(output,before.st_mode&0777)) error=errno;
    unsigned char bytes[16384];
    while (!error) {
        ssize_t count=read(input,bytes,sizeof bytes);
        if (count<0 && errno==EINTR) continue;
        if (count<0) { error=errno;break; }
        if (!count) break;
        total+=count;
        if (total>before.st_size) { error=ESTALE;break; }
        ssize_t offset=0;
        while (offset<count) {
            ssize_t wrote=write(output,bytes+offset,(size_t)(count-offset));
            if (wrote<0 && errno==EINTR) continue;
            if (wrote<=0) { error=wrote<0?errno:EIO;break; }
            offset+=wrote;
        }
    }
    if (!error && (fstat(input,&after) || total!=before.st_size ||
            after.st_size!=before.st_size || after.st_mtime!=before.st_mtime || after.st_ctime!=before.st_ctime)) error=ESTALE;
    if (close(input) && !error) error=errno;
    if (!error && fsync(output)) error=errno;
    if (close(output) && !error) error=errno;
    if (!error && rename(temporary,destination)) error=errno;
    if (error) { unlink(temporary);errno=error;return -1; }
    return 0;
}

static int nx_media_backup_path(const char *path,char *backup,size_t capacity) {
    const char *leaf=strrchr(path,'/');
    if (!leaf || !leaf[1]) { errno=EINVAL;return -1; }
    int n=snprintf(backup,capacity,"%.*s/.%s.nxoriginal",(int)(leaf-path),path,leaf+1);
    if (n<0 || (size_t)n>=capacity) { errno=ENAMETOOLONG;return -1; }
    return 0;
}
static int nx_media_backup_available(const char *backup) {
    struct stat state;
    if (lstat(backup,&state)) return errno==ENOENT?0:-1;
    if (!S_ISREG(state.st_mode) || state.st_size<=0) { errno=EINVAL;return -1; }
    return 1;
}
/* Called while holding the upload lock. Existing completed backups are kept. */
static int nx_media_preserve_original(const char *path,const char *backup) {
    int available=nx_media_backup_available(backup);
    if (available<0 || (!available && nx_media_copy(path,backup))) return -1;
    char parent[PATH_MAX];
    const char *slash=strrchr(backup,'/');
    if (!slash || (size_t)(slash-backup)>=sizeof parent) { errno=EINVAL;return -1; }
    memcpy(parent,backup,(size_t)(slash-backup));parent[slash-backup]=0;
    int fd=open(parent,O_RDONLY|O_DIRECTORY);
    if (fd<0) return -1;
    int error=fsync(fd)?errno:0;
    if (close(fd) && !error) error=errno;
    if (error) { errno=error;return -1; }
    return 0;
}
static int nx_media_edit_lock(const char *root) {
    char path[PATH_MAX];
    int n=snprintf(path,sizeof path,"%s/.nx300m-edit.lock",root);
    if (n<0 || n>=(int)sizeof path) { errno=ENAMETOOLONG;return -1; }
    int fd=open(path,O_RDWR|O_CREAT|O_NOFOLLOW,0600);
    if (fd<0) return -1;
    if (flock(fd,LOCK_EX|LOCK_NB)) { int error=errno;close(fd);errno=error;return -1; }
    return fd;
}
#endif
