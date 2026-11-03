/* Host-only libc fault injection into the actual loader headers.
 * Build/run through test_smartfilter_persistence.py; no ARM or VM required. */
#define _GNU_SOURCE
#define _LARGEFILE64_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <stdarg.h>
#include <sys/stat.h>
#include <sys/file.h>
#include <sys/statfs.h>
#include <time.h>
#include <assert.h>
#include "smartfilter_ram.h"
/* Generated verbatim from live_loader_core.h by the Python runner. */
#include "smartfilter_host_sha.inc"

enum { SRC=1,DST,MARKER,SIDE,TEMP,DIR,MEDIA_ROOT,ANCHOR,LOGS };
static const char *fault="setup";
static char root[512];
static unsigned calls,appends,marker_writes,allocations,fsyncs,reads,preads;
static int root_replaced;
static int kinds[4096];
static char fdpaths[4096][1024];
static int is(const char *name) { return !strcmp(fault,name); }
static const char *mapped(const char *path,char buf[1024]) {
    assert(path[0]=='/');
    assert(snprintf(buf,1024,"%s%s",root,path)<1024); return buf;
}
static int kind_path(const char *path) {
    if(strstr(path,".persisted")) return MARKER;
    if(strstr(path,".sha256")) return SIDE;
    if(strstr(path,".tmp")) return TEMP;
    if(!strcmp(path,"/perf")||!strcmp(path,"/mnt/mmc/nx300m-mod")) return MEDIA_ROOT;
    if(!strcmp(path,"/mnt/mmc")) return ANCHOR;
    if(!strcmp(path,"/mnt/mmc/nx300m-mod/logs")) return LOGS;
    if(!strcmp(path,"/")||!strcmp(path,"/mnt")) return DIR;
    if(!strncmp(path,"/perf/",6)||!strncmp(path,"/mnt/mmc/nx300m-mod/logs/",25)) return DST;
    return SRC;
}
static int io_failure(int number) { errno=number; return -1; }
static int host_open(const char *path,int flags,...) {
    ++calls; int kind=kind_path(path),writing=(flags&O_ACCMODE)!=O_RDONLY;
    if(kind==SRC&&(is("src_open_eacces")||is("probe_no_snapshot"))) return io_failure(EACCES);
    if(kind==DST&&is("dst_open_erofs")) return io_failure(EROFS);
    if(kind==MARKER&&((writing&&is("marker_open_eacces"))||(!writing&&is("marker_lookup_eacces")))) return io_failure(EACCES);
    if(kind==TEMP&&is("snapshot_open_eacces")) return io_failure(EACCES);
    if(kind==SIDE&&((writing&&is("side_open_eacces"))||(!writing&&is("side_read_open_eacces")))) return io_failure(EACCES);
    mode_t mode=0;
    if(flags&O_CREAT) { va_list ap; va_start(ap,flags); mode=(mode_t)va_arg(ap,int); va_end(ap); }
    char buf[1024]; int fd=open(mapped(path,buf),flags,mode);
    if(fd>=0) { assert(fd<4096); kinds[fd]=kind; strcpy(fdpaths[fd],path); }
    if(kind==DST&&fd>=0) {
        assert((flags&O_ACCMODE)==O_RDWR); assert(flags&O_APPEND); assert(flags&O_NOFOLLOW);
    }
    return fd;
}
static int host_openat(int dir,const char *name,int flags,...) {
    ++calls; char path[1024];
    assert(dir>=0&&dir<4096&&fdpaths[dir][0]);
    assert(snprintf(path,sizeof path,"%s%s%s",fdpaths[dir],strcmp(fdpaths[dir],"/")?"/":"",name)<(int)sizeof path);
    int kind=kind_path(path);
    if((flags&O_DIRECTORY)&&(is("parent_lstat_eacces")||is("parent_openat_eacces"))) return io_failure(EACCES);
    if((flags&O_DIRECTORY)&&is("parent_type")) return io_failure(ENOTDIR);
    if(kind==DST&&is("dst_open_erofs")) return io_failure(EROFS);
    mode_t mode=0;
    if(flags&O_CREAT) { va_list ap; va_start(ap,flags); mode=(mode_t)va_arg(ap,int); va_end(ap); }
    int fd=openat(dir,name,flags,mode);
    if(fd>=0) {
        assert(fd<4096); kinds[fd]=kind; strcpy(fdpaths[fd],path);
        if(kind==DST) { assert((flags&O_ACCMODE)==O_RDWR); assert(flags&O_APPEND); assert(flags&O_NOFOLLOW); }
    }
    return fd;
}
static int host_dup(int fd) {
    int out=dup(fd);
    if(out>=0) { assert(out<4096); kinds[out]=kinds[fd]; memmove(fdpaths[out],fdpaths[fd],sizeof fdpaths[out]); }
    return out;
}
static int host_fstatfs(int fd,struct statfs *fs) {
    ++calls; int k=kinds[fd];
    if((k==MEDIA_ROOT&&(is("media_root_fstatfs_eio")||is("probe_media_fs_eio")))||
       (k==DST&&is("media_target_fstatfs_eio"))||(k==LOGS&&is("media_prod_logs_fs_eio"))||
       (k==ANCHOR&&is("media_prod_anchor_fs_eio"))) return io_failure(EIO);
    int rc=fstatfs(fd,fs); if(rc) return rc;
    /* Synthetic FS data is used only for fail-closed negative cases. These
     * cases never assert real FAT/exFAT save success. */
    if(is("media_fixture_fat_owner")) fs->f_type=0x4d44;
    if(is("media_fixture_exfat_owner")) fs->f_type=0x2011bab0;
    if(is("media_fixture_unknown_owner")) fs->f_type=0x12345678;
    if((k==DST&&is("media_target_fs_type"))||(k==MEDIA_ROOT&&is("media_prod_root_fs_type"))) fs->f_type^=1;
    if(k==DST&&is("media_target_fsid")) ((unsigned char *)&fs->f_fsid)[0]^=1;
    return 0;
}
static int host_fstat(int version,int fd,struct stat64 *st) {
    (void)version; ++calls; int k=kinds[fd];
    if((k==SRC&&is("src_stat_eio"))||(k==DST&&is("dst_stat_eio"))||(k==MARKER&&is("old_marker_stat_eio"))) return io_failure(EIO);
    int rc=fstat64(fd,st); if(rc) return rc;
    if((k==SRC&&is("src_type"))||(k==DST&&is("dst_type"))||(k==MARKER&&is("old_marker_type"))) st->st_mode=S_IFDIR|0600;
    if((k==SRC&&is("src_owner"))||(k==DST&&is("dst_owner"))||(k==MARKER&&is("old_marker_owner"))) st->st_uid=geteuid()+1;
    if((k==SRC&&is("src_mode"))||(k==MARKER&&is("old_marker_mode"))) st->st_mode|=0020;
    if(k==SRC&&is("src_oversize")) st->st_size=1048577;
    if(k==MEDIA_ROOT&&is("probe_media_stat_eacces")) return io_failure(EACCES);
    if(k==DST&&(is("media_fixture_fat_owner")||is("media_fixture_exfat_owner")||is("media_fixture_unknown_owner")||is("media_prod_target_owner"))) st->st_uid=geteuid()+1;
    if((k==DST&&is("media_target_device"))||(k==MEDIA_ROOT&&is("media_prod_root_device"))||(k==LOGS&&is("media_prod_logs_device"))) ++st->st_dev;
    if(k==MEDIA_ROOT&&is("probe_media_inode_drift")) ++st->st_ino;
    if(k==MEDIA_ROOT&&is("probe_media_root_replaced")&&!root_replaced) {
        root_replaced=1; char a[1024],b[1024];
        assert(!rename(mapped("/perf",a),mapped("/perf-held",b)));
        assert(!mkdir(mapped("/perf",a),0700));
        int out=open(mapped("/perf/sf-probe-0123456789abcdef0123456789abcdef.ndjson",a),O_WRONLY|O_CREAT|O_EXCL,0600);
        assert(out>=0); assert(write(out,"foreign",7)==7); assert(!close(out));
    }
    return 0;
}
static int host_lstat(int version,const char *path,struct stat64 *st) {
    (void)version; ++calls;
    if(is("parent_lstat_eacces")||(is("probe_media_stat_eacces")&&!strcmp(path,"/perf"))) return io_failure(EACCES);
    char buf[1024]; int rc=lstat64(mapped(path,buf),st);
    if(!rc&&is("parent_type")) st->st_mode=S_IFREG|0600;
    return rc;
}
static ssize_t host_read(int fd,void *data,size_t size) {
    ++calls; ++reads; int k=kinds[fd];
    if((k==SRC&&is("src_read_eio"))||(k==MARKER&&is("old_marker_read_eio"))||(k==SIDE&&is("side_read_eio"))) return io_failure(EIO);
    if((k==SRC&&is("src_read_short"))||(k==MARKER&&is("old_marker_read_short"))||(k==SIDE&&is("side_read_short")))
        return read(fd,data,size?size-1:0);
    return read(fd,data,size);
}
static ssize_t host_write(int fd,const void *data,size_t size) {
    ++calls; int k=kinds[fd];
    if(k==DST) {
        ++appends; assert(fcntl(fd,F_GETFL)&O_APPEND);
        if(is("dst_write_enospc")||is("cleanup_preserve")||is("receipt_write_enospc")||is("probe_write_enospc")) return io_failure(ENOSPC);
        if(is("dst_write_short")) return write(fd,data,size-1);
        if(is("dst_write_zero")) return 0;
    }
    if(k==MARKER) {
        ++marker_writes;
        if(is("marker_write_enospc")||is("marker_cleanup_preserve")||is("probe_marker_enospc")) return io_failure(ENOSPC);
        if(is("marker_write_short")) return write(fd,data,size-1);
    }
    if(k==SIDE) {
        if(is("side_write_enospc")) return io_failure(ENOSPC);
        if(is("side_write_short")) return write(fd,data,size-1);
    }
    return write(fd,data,size);
}
static ssize_t host_pread(int fd,void *data,size_t size,off64_t offset) {
    ++calls; ++preads;
    if(is("dst_pread_eio")) return io_failure(EIO);
    if(is("dst_pread_short")) return pread64(fd,data,size-1,offset);
    ssize_t n=pread64(fd,data,size,offset);
    if(n>0&&is("dst_pread_mismatch")) ((char *)data)[0]^=1;
    return n;
}
static int host_fsync(int fd) {
    ++calls; ++fsyncs; int k=kinds[fd];
    if((k==DST&&is("dst_fsync_eio"))||(k==MARKER&&is("marker_fsync_eio"))||
       (k==SIDE&&is("side_fsync_eio"))||(k==TEMP&&is("snapshot_fsync_eio"))) return io_failure(EIO);
    return fsync(fd);
}
static int host_close(int fd) {
    ++calls; int k=kinds[fd]; kinds[fd]=0; int rc=close(fd);
    if((k==DST&&(is("dst_close_eio")||is("cleanup_preserve")||is("idempotent_close_eio")))||
       (k==SRC&&is("src_close_eio"))||(k==MARKER&&(is("marker_close_eio")||is("marker_cleanup_preserve")))||
       (k==SIDE&&is("side_close_eio"))) return io_failure(EBADF);
    return rc;
}
static int host_flock(int fd,int flags) {
    ++calls; assert(flags==(LOCK_EX|LOCK_NB));
    if(is("flock_busy")) return io_failure(EWOULDBLOCK);
    return flock(fd,flags);
}
static off64_t host_lseek64(int fd,off64_t offset,int whence) {
    ++calls;
    if(is("dst_lseek_eio")) return io_failure(EIO);
    if(is("dst_lseek_short")) return 0;
    return lseek64(fd,offset,whence);
}
static off_t host_lseek(int fd,off_t offset,int whence) {
    ++calls;
    if(is("checksum_lseek_eio")) return io_failure(EIO);
    return lseek(fd,offset,whence);
}
static void *host_malloc(size_t size) {
    ++allocations;
    if((is("source_malloc_enomem")&&allocations==1)||(is("check_malloc_enomem")&&allocations==2)) { errno=ENOMEM; return NULL; }
    return malloc(size);
}
static void host_free(void *p) { free(p); if(is("cleanup_preserve")) errno=EINVAL; }
static FILE *host_fdopen(int fd,const char *mode) {
    if(is("snapshot_fdopen_emfile")) { errno=EMFILE; return NULL; }
    return fdopen(fd,mode);
}
static size_t host_fwrite(const void *ptr,size_t size,size_t n,FILE *stream) {
    if(is("snapshot_fwrite_short")) return fwrite(ptr,size,n-1,stream);
    return fwrite(ptr,size,n,stream);
}
static int host_fflush(FILE *stream) {
    if(is("snapshot_fflush_enospc")) return io_failure(ENOSPC);
    return fflush(stream);
}
static int host_fclose(FILE *stream) {
    int fd=fileno(stream); kinds[fd]=0; int rc=fclose(stream);
    if(is("snapshot_fclose_eio")||is("snapshot_fwrite_short")) return io_failure(EIO);
    return rc;
}
static int host_rename(const char *src,const char *dst) {
    if(is("snapshot_rename_eacces")) return io_failure(EACCES);
    char a[1024],b[1024]; return rename(mapped(src,a),mapped(dst,b));
}
static int host_unlink(const char *path) {
    char buf[1024]; return unlink(mapped(path,buf));
}
#define openat host_openat
#define dup host_dup
#define fstatfs host_fstatfs
#define open host_open
#define __fxstat64 host_fstat
#define __lxstat64 host_lstat
#define read host_read
#define write host_write
#define pread64 host_pread
#define fsync host_fsync
#define close host_close
#define flock host_flock
#define lseek64 host_lseek64
#define lseek host_lseek
#define malloc host_malloc
#define free host_free
#define fdopen host_fdopen
#define fwrite host_fwrite
#define fflush host_fflush
#define fclose host_fclose
#define rename host_rename
#define unlink host_unlink
#include "smartfilter_timing_export.h"
#include "smartfilter_probe_control.h"
#undef open
#undef openat
#undef dup
#undef fstatfs
#undef read
#undef write
#undef pread64
#undef fsync
#undef close
#undef flock
#undef lseek64
#undef lseek
#undef malloc
#undef free
#undef fdopen
#undef fwrite
#undef fflush
#undef fclose
#undef rename
#undef unlink

static const char *source="/var/run/nx300m-sf-diag-42-99-123-7.ndjson";
static const char *destination="/perf/fn-key.log";
static const char payload[]="{\"frozen\":true,\"host_test\":1}\n";
static void put(const char *path,const void *data,size_t bytes) {
    char buf[1024]; int fd=open(mapped(path,buf),O_WRONLY|O_CREAT|O_TRUNC,0600);
    assert(fd>=0); assert(write(fd,data,bytes)==(ssize_t)bytes); assert(!close(fd));
}
static off64_t length(const char *path) {
    char buf[1024]; struct stat64 st;
    return stat64(mapped(path,buf),&st)?-1:st.st_size;
}
static void mutate(const char *path) {
    char buf[1024]; int fd=open(mapped(path,buf),O_RDWR); assert(fd>=0);
    char c; assert(read(fd,&c,1)==1); c^=1; assert(pwrite64(fd,&c,1,0)==1); close(fd);
}
static void reset(void) {
    memset(&diag_save_result,0,sizeof diag_save_result);
    calls=appends=marker_writes=allocations=fsyncs=reads=preads=0;
    errno=E2BIG; /* Every logical rejection must ignore this stale errno. */
}
int main(int argc,char **argv) {
    assert(argc==3); const char *name=argv[1];
    assert(strlen(argv[2])<sizeof root); strcpy(root,argv[2]);
    char buf[1024];
    assert(!mkdir(mapped("/perf",buf),0700));
    assert(!mkdir(mapped("/var",buf),0700));
    assert(!mkdir(mapped("/var/run",buf),0700));
    int probe=!strncmp(name,"probe_",6);
    if(probe) destination="/perf/sf-probe-0123456789abcdef0123456789abcdef.ndjson";
    int production=!strncmp(name,"media_prod_",11);
    if(production) {
        assert(!mkdir(mapped("/mnt",buf),0700)); assert(!mkdir(mapped("/mnt/mmc",buf),0700));
        assert(!mkdir(mapped("/mnt/mmc/nx300m-mod",buf),0700)); assert(!mkdir(mapped("/mnt/mmc/nx300m-mod/logs",buf),0700));
        destination="/mnt/mmc/nx300m-mod/logs/fn-key.log";
    }
    put(source,payload,sizeof payload-1); put(destination,"",0);
    if(!strcmp(name,"src_empty")) put(source,"",0);
    if(!strcmp(name,"dst_nonempty")) put(destination,"old",3);
    char marker[256],side[256];
    snprintf(marker,sizeof marker,"%s.persisted",source); snprintf(side,sizeof side,"%s.sha256",source);
    int old_marker=!strncmp(name,"old_marker_",11)||!strncmp(name,"idempotent",10)||
        !strcmp(name,"probe_idempotent")||!strcmp(name,"receipt_idempotent");
    if(old_marker) { assert(diag_persist(source,destination,1,&diag_save_result)); }
    if(!strcmp(name,"old_marker_mismatch")) mutate(marker);
    int receipt=!strncmp(name,"receipt_",8);
    int checksum=!strncmp(name,"side_",5)||!strncmp(name,"checksum_",9);
    int snapshot=!strncmp(name,"snapshot_",9);
    if(probe||receipt||checksum) assert(diag_frozen_hash(source,1));
    if(!strcmp(name,"checksum_mismatch")||!strcmp(name,"probe_checksum_mismatch")) mutate(source);
    if(!strcmp(name,"side_invalid_hash")) { char invalid[65]; memset(invalid,'z',sizeof invalid); put(side,invalid,sizeof invalid); }
    if(snapshot) { assert(!unlink(mapped(source,buf))); }
    if(!strncmp(name,"side_",5)&&strcmp(name,"side_read_open_eacces")&&strcmp(name,"side_read_eio")&&strcmp(name,"side_read_short")&&strcmp(name,"side_invalid_hash"))
        assert(!unlink(mapped(side,buf)));
    struct nx_sfr_config cfg={0}; struct nx_sfr_runtime runtime={0};
    cfg.generation=5; cfg.probe_once=1; cfg.timing_enabled=1; strcpy(cfg.look_id,"identity");
    runtime.timing.window=7; runtime.timing.frozen=1; runtime.timing.schema=NX_SFT_SCHEMA;
    runtime.probe.slot=UINT32_MAX;
    probe_expect=(struct nx_sfr_expect){42,123,7,5,99,1}; probe_contract_ack=1; probe_file_path=destination;
    struct stat64 media; assert(!stat64(mapped("/perf",buf),&media));
    char source_id[128]; snprintf(source_id,sizeof source_id,"%llu:%llu",(unsigned long long)media.st_dev,(unsigned long long)media.st_ino);
    probe_source_id=source_id;
    if(!strcmp(name,"parent_actual_symlink")||!strcmp(name,"probe_media_real_replaced")) {
        char other[1024]; assert(!rename(mapped("/perf",buf),mapped("/perf-held",other)));
        if(!strcmp(name,"parent_actual_symlink")) assert(!symlink("perf-held",mapped("/perf",buf)));
        else assert(!mkdir(mapped("/perf",buf),0700));
    }
    if(!strcmp(name,"target_actual_symlink")) {
        assert(!unlink(mapped(destination,buf)));
        char other[1024]; assert(!symlink(mapped(source,other),mapped(destination,buf)));
    }
    if(!strcmp(name,"media_prod_logs_symlink")) {
        assert(!unlink(mapped(destination,buf))); assert(!rmdir(mapped("/mnt/mmc/nx300m-mod/logs",buf)));
        char other[1024]; assert(!symlink(mapped("/perf",other),mapped("/mnt/mmc/nx300m-mod/logs",buf)));
    }
    if(!strcmp(name,"probe_contract")) probe_contract_ack=0;
    if(!strcmp(name,"probe_identity")) ++probe_expect.generation;
    if(!strcmp(name,"probe_media_identity")) probe_source_id="0:0";
    if(!strcmp(name,"probe_capture")) runtime.captures=1;
    reset(); fault=!strncmp(name,"snapshot_side_",14)?name+9:name;
    int ok,printed=0;
    if(probe) { ok=probe_save(42,99,123,1,&cfg,&runtime); printed=1; }
    else if(snapshot) {
        struct nx_sft_ledger ledger={0}; struct nx_sft_time stamp={0};
        ok=diag_export(42,99,123,1,&cfg,&runtime,&ledger,stamp);
        if(!ok) probe_failure("diag-export","export_failed",diag_frozen_hash(source,0));
        printed=1;
    } else if(receipt) {
        diag_log_path=destination;
        ok=diag_receipt("42-99-123-7","42:99:123:7:5",source,1,0,0,0);
        if(!ok) probe_failure("diag-export","export_failed",diag_frozen_hash(source,0));
        printed=1;
    } else if(checksum) {
        int create=!strncmp(name,"side_",5)&&strcmp(name,"side_read_open_eacces")&&strcmp(name,"side_read_eio")&&strcmp(name,"side_read_short")&&strcmp(name,"side_invalid_hash");
        ok=diag_frozen_hash_result(source,create,&diag_save_result);
    } else if(!strcmp(name,"quote_escape")) {
        ok=0; diag_save_error(&diag_save_result,"source","read","syscall_failed",1,EIO);
        strcpy(diag_save_result.errno_text,"quoted\" slash\\ line\n\t\001\377");
    } else if(!strcmp(name,"quiet_no_data")) {
        assert(!diag_frozen_hash("/missing",0)); assert(!diag_save_result.stage);
        probe_failure("probe-save","no_data",0); ok=0; printed=1;
    } else {
        const char *src=!strcmp(name,"bad_src_path")?"/bad/source":source;
        const char *dst=!strcmp(name,"bad_dst_path")?"/bad/destination":destination;
        int probe_mode=strcmp(name,"bad_dst_path")&&strcmp(name,"ordinary_success")&&strcmp(name,"ordinary_create");
        if(!strcmp(name,"ordinary_create")) assert(!unlink(mapped(destination,buf)));
        if(!strcmp(name,"actual_flock_busy")) {
            int locked=open(mapped(destination,buf),O_RDWR); assert(locked>=0); assert(!flock(locked,LOCK_EX|LOCK_NB));
            ok=diag_persist(src,dst,probe_mode,&diag_save_result); close(locked);
        } else ok=diag_persist(src,dst,probe_mode,&diag_save_result);
    }
    if(!strcmp(name,"probe_media_root_replaced")) {
        assert(root_replaced&&ok); assert(length(destination)==7);
        destination="/perf-held/sf-probe-0123456789abcdef0123456789abcdef.ndjson";
    }
    /* A failed quiet check must not create/replace a primary error. */
    unsigned operation_calls=calls;
    struct nx_sft_save_result before=diag_save_result;
    assert(!diag_frozen_hash("/absent-frozen",0)); assert(!memcmp(&before,&diag_save_result,sizeof before));
    if(!printed) {
        printf("{\"command\":\"host-persist\",\"status\":\"%s\",\"snapshot_created\":false,\"persisted\":%s",ok?"persisted":"failed",ok?"true":"false");
        diag_save_fields(stdout,&diag_save_result); puts("}");
        diag_save_stderr(&diag_save_result);
    }
    printf("{\"host_metrics\":true,\"ok\":%s,\"calls\":%u,\"appends\":%u,\"marker_writes\":%u,\"fsyncs\":%u,\"preads\":%u,"
           "\"destination_size\":%lld,\"source_size\":%lld,\"marker_size\":%lld,\"side_size\":%lld,\"marker_layout_size\":%u}\n",
           ok?"true":"false",operation_calls,appends,marker_writes,fsyncs,preads,(long long)length(destination),(long long)length(source),
           (long long)length(marker),(long long)length(side),(unsigned)sizeof(struct nx_sft_persisted));
    return 0;
}
