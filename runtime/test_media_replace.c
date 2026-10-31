#define _GNU_SOURCE
#include <assert.h>
#include <signal.h>
#include <string.h>
#include <sys/resource.h>
#include "../mod/metol/remote/media_replace.h"

static void expect(const char *path,const char *text) {
    char bytes[64]={0};FILE *file=fopen(path,"rb");assert(file);
    size_t length=fread(bytes,1,sizeof bytes,file);assert(!ferror(file));fclose(file);
    assert(length==strlen(text) && !memcmp(bytes,text,length));
}
int main(void) {
    char directory[]="/tmp/nx-media-replace-XXXXXX";assert(mkdtemp(directory));
    char photo[PATH_MAX],victim[PATH_MAX],old_tmp[PATH_MAX],backup[PATH_MAX],lock_path[PATH_MAX];
    snprintf(photo,sizeof photo,"%s/photo.JPG",directory);
    snprintf(victim,sizeof victim,"%s/victim",directory);
    snprintf(old_tmp,sizeof old_tmp,"%s/photo.JPG.tmp",directory);
    FILE *file=fopen(photo,"wb");assert(file);assert(fputs("original",file)>=0);assert(!fclose(file));
    file=fopen(victim,"wb");assert(file);assert(fputs("untouched",file)>=0);assert(!fclose(file));
    assert(!symlink(victim,old_tmp));
    assert(!nx_media_backup_path(photo,backup,sizeof backup));
    int edit_lock=nx_media_edit_lock(directory);assert(edit_lock>=0);
    assert(nx_media_edit_lock(directory)<0);
    assert(nx_media_replace(photo,"",0)<0);expect(photo,"original");
    struct rlimit saved,limited;assert(!getrlimit(RLIMIT_FSIZE,&saved));limited=saved;limited.rlim_cur=4;
    signal(SIGXFSZ,SIG_IGN);assert(!setrlimit(RLIMIT_FSIZE,&limited));
    assert(nx_media_replace(photo,"replacement",11)<0);
    assert(nx_media_preserve_original(photo,backup)<0);
    assert(!setrlimit(RLIMIT_FSIZE,&saved));expect(photo,"original");
    assert(nx_media_backup_available(backup)==0);
    assert(!nx_media_preserve_original(photo,backup));expect(backup,"original");
    assert(!nx_media_replace(photo,"replacement",11));expect(photo,"replacement");expect(victim,"untouched");
    assert(!nx_media_preserve_original(photo,backup));expect(backup,"original");
    assert(!nx_media_copy(backup,photo));expect(photo,"original");
    assert(!unlink(backup));assert(!symlink(victim,backup));
    assert(nx_media_preserve_original(photo,backup)<0);expect(victim,"untouched");
    assert(!unlink(backup));assert(!close(edit_lock));
    snprintf(lock_path,sizeof lock_path,"%s/.nx300m-edit.lock",directory);assert(!unlink(lock_path));
    assert(!unlink(old_tmp));assert(!unlink(photo));assert(!unlink(victim));assert(!rmdir(directory));
    puts("PASS: partial writes and backup failures preserve original; backup is immutable; restore, edit lock and symlink rejection work");
    return 0;
}
