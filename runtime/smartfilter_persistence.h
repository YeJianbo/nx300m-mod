/* Loader-only persistence. Not part of the injected runtime or its ABI.
 * Callers provide the loader's SHA-256 primitives. Tests wrap libc calls at
 * compile time; there is deliberately no production injection switch. */
#ifndef NX_SMARTFILTER_PERSISTENCE_H
#define NX_SMARTFILTER_PERSISTENCE_H
#include <sys/file.h>
#include "smartfilter_media.h"
static int diag_media_stat(int fd,struct nx_sfm_meta *m) {
    struct stat64 s;
    if(__fxstat64(3,fd,&s)) return -1;
    m->device=s.st_dev; m->inode=s.st_ino; m->uid=s.st_uid; m->nlink=s.st_nlink;
    m->size=s.st_size; m->mode=s.st_mode; return 0;
}
struct nx_sft_save_result {
    const char *stage,*operation,*reason;
    int error_errno,error_errno_valid,io_lengths;
    unsigned long long expected_bytes,actual_bytes;
    char errno_text[192];
    int write_attempted,destination_may_have_changed,destination_bytes_verified;
    int media_valid;
    struct nx_sfm_context media;
};
static struct nx_sft_save_result diag_save_result;
static void diag_save_error(struct nx_sft_save_result *r,const char *stage,const char *op,
                            const char *reason,int valid,int number) {
    if(!r||r->stage) return;
    r->stage=stage; r->operation=op; r->reason=reason;
    r->error_errno_valid=valid; r->error_errno=valid&&number>=0?number:0;
    snprintf(r->errno_text,sizeof r->errno_text,"%s",valid?strerror(r->error_errno):"not a system call error");
}
static int diag_save_logic(struct nx_sft_save_result *r,const char *stage,const char *op,const char *reason) {
    diag_save_error(r,stage,op,reason,0,0); return 0;
}
static int diag_save_system(struct nx_sft_save_result *r,const char *stage,const char *op) {
    int saved_errno=errno;
    diag_save_error(r,stage,op,"syscall_failed",1,saved_errno); return 0;
}
static int diag_save_io(struct nx_sft_save_result *r,const char *stage,const char *op,
                       ssize_t actual,unsigned expected) {
    int saved_errno=errno;
    if(actual==(ssize_t)expected) return 1;
    int first=r&&!r->stage;
    diag_save_error(r,stage,op,actual<0?"syscall_failed":(!strcmp(op,"write")||!strcmp(op,"fwrite")?"short_write":"short_read"),
                    actual<0,saved_errno);
    if(first) { r->io_lengths=1; r->expected_bytes=expected; r->actual_bytes=actual<0?0:(unsigned long long)actual; }
    return 0;
}
/* ASCII-only JSON escaping also keeps stderr on exactly one bounded line,
 * including errno strings returned by a non-English libc locale. */
static void diag_save_quote(FILE *out,const char *text,unsigned limit) {
    fputc('"',out);
    for(unsigned i=0;i<limit&&text&&text[i];++i) {
        unsigned char c=(unsigned char)text[i];
        if(c=='"'||c=='\\') { fputc('\\',out); fputc(c,out); }
        else if(c<32||c>=127) fprintf(out,"\\u%04x",(unsigned)c);
        else fputc(c,out);
    }
    fputc('"',out);
}
static void diag_save_fields(FILE *out,const struct nx_sft_save_result *r) {
    if(r&&r->stage) {
        fputs(",\"error_stage\":",out); diag_save_quote(out,r->stage,48);
        fputs(",\"error_operation\":",out); diag_save_quote(out,r->operation,48);
        fputs(",\"error_reason\":",out); diag_save_quote(out,r->reason,64);
        fprintf(out,",\"error_errno\":%d,\"error_errno_valid\":%s,\"error_errno_text\":",
                r->error_errno,r->error_errno_valid?"true":"false");
        diag_save_quote(out,r->errno_text,sizeof r->errno_text-1);
        if(r->io_lengths) fprintf(out,",\"error_expected_bytes\":%llu,\"error_actual_bytes\":%llu",
                                 r->expected_bytes,r->actual_bytes);
    }
    if(r&&r->media_valid) nx_sfm_fields(out,&r->media);
    fprintf(out,",\"write_attempted\":%s,\"destination_may_have_changed\":%s,\"destination_bytes_verified\":%s",
            r&&r->write_attempted?"true":"false",r&&r->destination_may_have_changed?"true":"false",
            r&&r->destination_bytes_verified?"true":"false");
}
static void diag_save_stderr(const struct nx_sft_save_result *r) {
    if(!r||!r->stage) return;
    fputs("Diagnostic save failed: stage=",stderr); diag_save_quote(stderr,r->stage,48);
    fputs(" op=",stderr); diag_save_quote(stderr,r->operation,48);
    fputs(" reason=",stderr); diag_save_quote(stderr,r->reason,64);
    fprintf(stderr," errno=%d valid=%s text=",r->error_errno,r->error_errno_valid?"true":"false");
    diag_save_quote(stderr,r->errno_text,sizeof r->errno_text-1);
    if(r->io_lengths) fprintf(stderr," expected_bytes=%llu actual_bytes=%llu",r->expected_bytes,r->actual_bytes);
    fputc('\n',stderr);
}
static int diag_regular_result(int fd,unsigned max,const char *stage,struct nx_sft_save_result *r) {
    struct stat64 st;
    if(__fxstat64(3,fd,&st)) return diag_save_system(r,stage,"fstat");
    if(!S_ISREG(st.st_mode)) return diag_save_logic(r,stage,"validate","not_regular");
    if(st.st_uid!=geteuid()) return diag_save_logic(r,stage,"validate","owner_mismatch");
    if(st.st_mode&0022) return diag_save_logic(r,stage,"validate","unsafe_permissions");
    if(st.st_size<0||(unsigned long long)st.st_size>max) return diag_save_logic(r,stage,"validate","size_out_of_range");
    return 1;
}
static int diag_regular(int fd,unsigned max) { return diag_regular_result(fd,max,"snapshot",NULL); }
static void diag_sha_hex(struct sha *s,char hex[65]) {
    uint64_t bits=s->bytes*8; unsigned char one=128,zero=0; sha_feed(s,&one,1);
    while(s->fill!=56) sha_feed(s,&zero,1);
    for(int i=7;i>=0;--i) { unsigned char v=(unsigned char)(bits>>(8*i)); sha_feed(s,&v,1); }
    for(unsigned i=0;i<8;++i) snprintf(hex+8*i,9,"%08x",s->h[i]);
}
/* Preserve the original bounded whole-file read/hash behavior, but distinguish
 * read/seek failures, length differences and digest mismatches without stale errno. */
static int diag_file_hash(int fd,unsigned size,const char *expected,struct nx_sft_save_result *r) {
    struct sha hash={{0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19},{0},0,0};
    if(lseek(fd,0,SEEK_SET)<0) return diag_save_system(r,"checksum","lseek");
    char buffer[4096],hex[65]; unsigned total=0; ssize_t n;
    while((n=read(fd,buffer,sizeof buffer))>0) {
        if((unsigned)n>size-total) return diag_save_logic(r,"checksum","read","size_mismatch");
        sha_feed(&hash,buffer,(unsigned)n); total+=(unsigned)n;
    }
    if(n<0) return diag_save_system(r,"checksum","read");
    if(!diag_save_io(r,"checksum","read",total,size)) return 0;
    diag_sha_hex(&hash,hex);
    return !strcmp(hex,expected)||diag_save_logic(r,"checksum","validate","content_mismatch");
}
static int diag_frozen_hash_result(const char *path,int create,struct nx_sft_save_result *r) {
    int fd=open(path,O_RDONLY|O_CLOEXEC|O_NOFOLLOW);
    if(fd<0) return diag_save_system(r,"snapshot","open");
    struct stat64 st;
    int ok=diag_regular_result(fd,1048576,"snapshot",r);
    if(ok&&__fxstat64(3,fd,&st)) ok=diag_save_system(r,"snapshot","fstat");
    if(ok&&st.st_size<=0) ok=diag_save_logic(r,"snapshot","validate","empty_source");
    char side[256],hex[65]; snprintf(side,sizeof side,"%s.sha256",path);
    if(create&&ok) {
        struct sha hash={{0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19},{0},0,0};
        char buffer[4096]; ssize_t n; unsigned total=0;
        while((n=read(fd,buffer,sizeof buffer))>0) { sha_feed(&hash,buffer,(unsigned)n); total+=(unsigned)n; }
        if(n<0) ok=diag_save_system(r,"checksum","read");
        else ok=diag_save_io(r,"checksum","read",total,(unsigned)st.st_size);
        diag_sha_hex(&hash,hex);
        int out=ok?open(side,O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC|O_NOFOLLOW,0600):-1;
        if(out<0) { if(ok) ok=diag_save_system(r,"checksum","open"); }
        else {
            ok=diag_save_io(r,"checksum","write",write(out,hex,sizeof hex),sizeof hex);
            if(ok&&fsync(out)) ok=diag_save_system(r,"checksum","fsync");
            if(close(out)) { diag_save_system(r,"checksum","close"); ok=0; }
        }
    } else if(ok) {
        int in=open(side,O_RDONLY|O_CLOEXEC|O_NOFOLLOW);
        if(in<0) ok=diag_save_system(r,"checksum","open");
        else {
            ok=diag_regular_result(in,sizeof hex,"checksum",r);
            if(ok) ok=diag_save_io(r,"checksum","read",read(in,hex,sizeof hex),sizeof hex);
            close(in); /* Previously ignored cleanup, still not a primary failure. */
        }
        if(ok) {
            ok=hex[64]==0;
            for(unsigned i=0;ok&&i<64;++i) if(!((hex[i]>='0'&&hex[i]<='9')||(hex[i]>='a'&&hex[i]<='f'))) ok=0;
            if(!ok) diag_save_logic(r,"checksum","validate","invalid_hash");
            if(ok) ok=diag_file_hash(fd,(unsigned)st.st_size,hex,r);
        }
    }
    close(fd); return ok;
}
static int diag_frozen_hash(const char *path,int create) { return diag_frozen_hash_result(path,create,NULL); }
struct nx_sft_persisted { unsigned long long device,inode,offset; unsigned bytes; char sha[65]; };
static int diag_media_finish(struct nx_sfm_context *c,struct nx_sft_save_result *r,int ok) {
    if(r) {
        r->media_valid=1; r->media=*c;
        if(c->stage) diag_save_error(r,c->stage,c->operation,c->reason,c->error_errno_valid,c->error_errno);
    }
    nx_sfm_close(c); return ok;
}
static int diag_persist_expected(const char *path,const char *log,int probe,struct nx_sft_save_result *r,
                                 const struct nx_sfm_identity *expected) {
    const char *production="/mnt/mmc/nx300m-mod/logs/",*fixture="/perf/",*leaf=NULL,*root=NULL;
    if(!strncmp(log,production,strlen(production))) { leaf=log+strlen(production); root="/mnt/mmc/nx300m-mod"; }
    else if(!strncmp(log,fixture,strlen(fixture))) { leaf=log+strlen(fixture); root="/perf"; }
    int allowed=leaf&&!strcmp(leaf,"fn-key.log")&&!probe;
    if(probe&&leaf&&strlen(leaf)==48&&!strncmp(leaf,"sf-probe-",9)&&!strcmp(leaf+41,".ndjson")) {
        allowed=1;
        for(unsigned i=9;i<41;++i) if(!((leaf[i]>='0'&&leaf[i]<='9')||(leaf[i]>='a'&&leaf[i]<='f'))) allowed=0;
    }
    /* Four-argument internal callers may use an existing fn-key.log with
     * probe semantics; external probe-save still requires the nonce name. */
    if(probe&&!expected&&leaf&&!strcmp(leaf,"fn-key.log")) allowed=1;
    if(!allowed) return diag_save_logic(r,"destination","validate","path_not_allowed");
    if(strncmp(path,"/var/run/nx300m-sf-diag-",24)) return diag_save_logic(r,"source","validate","path_not_allowed");
    struct nx_sfm_context media;
    if(!nx_sfm_open(&media,root,"/perf",0,expected,diag_media_stat)) return diag_media_finish(&media,r,0);
    int src=open(path,O_RDONLY|O_CLOEXEC|O_NOFOLLOW);
    if(src<0) { diag_save_system(r,"source","open"); return diag_media_finish(&media,r,0); }
    struct stat64 st;
    int ok=diag_regular_result(src,1048576,"source",r);
    if(ok&&__fxstat64(3,src,&st)) ok=diag_save_system(r,"source","fstat");
    if(ok&&!st.st_size) ok=diag_save_logic(r,"source","validate","empty_source");
    if(!ok) { close(src); return diag_media_finish(&media,r,0); }
    unsigned bytes=(unsigned)st.st_size; char *data=malloc(bytes);
    if(!data) { diag_save_system(r,"source","malloc"); close(src); return diag_media_finish(&media,r,0); }
    ok=diag_save_io(r,"source","read",read(src,data,bytes),bytes); close(src);
    struct sha hash={{0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19},{0},0,0};
    struct nx_sft_persisted saved; memset(&saved,0,sizeof saved);
    sha_feed(&hash,data,bytes); diag_sha_hex(&hash,saved.sha); saved.bytes=bytes;
    int dst=openat(media.logs,leaf,O_RDWR|O_APPEND|O_CLOEXEC|O_NOFOLLOW|(probe?0:O_CREAT),0600);
    if(dst<0) { diag_save_system(r,"destination","openat"); free(data); return diag_media_finish(&media,r,0); }
    /* O_CREAT can create an empty log even before the append is attempted. */
    if(r&&!probe) r->destination_may_have_changed=1;
    if(!nx_sfm_target(&media,dst)) { close(dst); free(data); return diag_media_finish(&media,r,0); }
    if(flock(dst,LOCK_EX|LOCK_NB)) { diag_save_system(r,"destination","flock"); close(dst); free(data); return diag_media_finish(&media,r,0); }
    saved.device=media.t.meta.device; saved.inode=media.t.meta.inode;
    char marker[256]; snprintf(marker,sizeof marker,"%s.persisted",path);
    int old=open(marker,O_RDONLY|O_CLOEXEC|O_NOFOLLOW);
    if(old>=0) {
        struct nx_sft_persisted prior;
        if(ok) ok=diag_regular_result(old,sizeof prior,"marker",r);
        if(ok) ok=diag_save_io(r,"marker","read",read(old,&prior,sizeof prior),sizeof prior);
        if(ok&&(prior.device!=saved.device||prior.inode!=saved.inode||prior.bytes!=bytes||prior.sha[64]!=0||memcmp(prior.sha,saved.sha,sizeof saved.sha)))
            ok=diag_save_logic(r,"marker","validate","marker_mismatch");
        close(old);
        if(ok) {
            char *check=malloc(bytes);
            if(!check) ok=diag_save_system(r,"destination_readback","malloc");
            else {
                ok=diag_save_io(r,"destination_readback","pread",pread64(dst,check,bytes,(off64_t)prior.offset),bytes);
                if(ok&&memcmp(check,data,bytes)) ok=diag_save_logic(r,"destination_readback","validate","content_mismatch");
                free(check);
            }
            if(ok&&r) r->destination_bytes_verified=1;
        }
        close(dst); free(data); return diag_media_finish(&media,r,ok); /* Original idempotent close ignored. */
    }
    if(errno!=ENOENT) { diag_save_system(r,"marker","open"); ok=0; }
    else if(probe&&media.t.meta.size!=0) ok=diag_save_logic(r,"destination","validate","destination_not_empty");
    if(ok) {
        /* Exactly one O_APPEND write; never retry or repair a partial dump. */
        if(r) { r->write_attempted=1; r->destination_may_have_changed=1; }
        ok=diag_save_io(r,"destination","write",write(dst,data,bytes),bytes);
        if(ok&&fsync(dst)) ok=diag_save_system(r,"destination","fsync");
        off64_t end=lseek64(dst,0,SEEK_CUR);
        if(end<0) { diag_save_system(r,"destination","lseek"); ok=0; }
        else if(end<(off64_t)bytes) ok=diag_save_logic(r,"destination","lseek","offset_out_of_range");
        else saved.offset=(unsigned long long)(end-bytes);
        char *check=ok?malloc(bytes):NULL;
        if(ok&&!check) ok=diag_save_system(r,"destination_readback","malloc");
        if(ok) ok=diag_save_io(r,"destination_readback","pread",pread64(dst,check,bytes,(off64_t)saved.offset),bytes);
        if(ok&&memcmp(check,data,bytes)) ok=diag_save_logic(r,"destination_readback","validate","content_mismatch");
        if(ok&&r) r->destination_bytes_verified=1;
        free(check);
    }
    if(close(dst)) { diag_save_system(r,"destination","close"); ok=0; }
    if(ok) {
        int fd=open(marker,O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC|O_NOFOLLOW,0600);
        if(fd<0) ok=diag_save_system(r,"marker","open");
        else {
            ok=diag_save_io(r,"marker","write",write(fd,&saved,sizeof saved),sizeof saved);
            if(ok&&fsync(fd)) ok=diag_save_system(r,"marker","fsync");
            if(close(fd)) { diag_save_system(r,"marker","close"); ok=0; }
        }
    }
    free(data); return diag_media_finish(&media,r,ok);
}
static int diag_persist(const char *path,const char *log,int probe,struct nx_sft_save_result *r) {
    return diag_persist_expected(path,log,probe,r,NULL);
}
#endif
