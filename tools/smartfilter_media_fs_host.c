/* Native production persistence on an isolated real filesystem, no I/O mocks. */
#define _GNU_SOURCE
#define _LARGEFILE64_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/statfs.h>
#include <sys/types.h>
#include <unistd.h>

/* Only adapt the original ARM libc stat version to the native host ABI. */
static int host_fxstat64(int version,int fd,struct stat64 *st) {
    (void)version; return fstat64(fd,st);
}
static int host_lxstat64(int version,const char *path,struct stat64 *st) {
    (void)version; return lstat64(path,st);
}
#define __fxstat64 host_fxstat64
#define __lxstat64 host_lxstat64
#include "smartfilter_media_fs_sha.h"
#include "smartfilter_persistence.h"

int main(int argc,char **argv) {
    if(argc!=5) return 64;
    int probe=!strcmp(argv[3],"probe");
    if(!probe&&strcmp(argv[3],"ordinary")) return 64;
    struct nx_sfm_identity expected,*identity=NULL;
    if(strcmp(argv[4],"-")) {
        char tail;
        if(sscanf(argv[4],"%llu:%llu%c",&expected.device,&expected.inode,&tail)!=2) return 64;
        identity=&expected;
    }
    struct nx_sft_save_result *result=&diag_save_result;
    memset(result,0,sizeof *result);
    char side[256];
    if(snprintf(side,sizeof side,"%s.sha256",argv[1])>=(int)sizeof side) return 64;
    struct stat64 st;
    int create=lstat64(side,&st)!=0;
    int valid=diag_frozen_hash_result(argv[1],create,result);
    int ok=valid&&diag_persist_expected(argv[1],argv[2],probe,result,identity);
    printf("{\"command\":\"native-fs-persist\",\"snapshot_created\":%s,\"persisted\":%s,\"euid\":%u",
           valid?"true":"false",ok?"true":"false",(unsigned)geteuid());
    diag_save_fields(stdout,result);
    fputs("}\n",stdout);
    if(!ok) diag_save_stderr(result);
    return ok?0:3;
}
