/* Media-destination FD admission only. No SHA, injected ABI or RAM policy.
 * Callers adapt their own stat ABI into nx_sfm_meta. */
#ifndef NX_SMARTFILTER_MEDIA_H
#define NX_SMARTFILTER_MEDIA_H
#include <sys/types.h>
#include <sys/stat.h>
#include <sys/statfs.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#include <string.h>
#include <stdio.h>
struct nx_sfm_meta {
    unsigned long long device,inode,uid,nlink;
    long long size;
    unsigned mode;
};
struct nx_sfm_identity { unsigned long long device,inode; };
typedef int (*nx_sfm_stat_fn)(int,struct nx_sfm_meta *);
struct nx_sfm_observation {
    struct nx_sfm_meta meta;
    struct statfs fs;
    int stat_valid,fs_valid;
};
struct nx_sfm_context {
    int anchor,root,logs,production,fat_exception;
    unsigned long long euid;
    struct nx_sfm_observation a,r,l,t;
    nx_sfm_stat_fn stat_fd;
    const char *stage,*operation,*reason;
    int error_errno,error_errno_valid;
};
static int nx_sfm_error(struct nx_sfm_context *c,const char *stage,const char *op,const char *reason,int system) {
    int number=errno;
    if(!c->stage) {
        c->stage=stage; c->operation=op; c->reason=reason;
        c->error_errno_valid=system; c->error_errno=system?number:0;
    }
    return 0;
}
static int nx_sfm_system(struct nx_sfm_context *c,const char *stage,const char *op) {
    return nx_sfm_error(c,stage,op,"syscall_failed",1);
}
static int nx_sfm_logic(struct nx_sfm_context *c,const char *stage,const char *reason) {
    return nx_sfm_error(c,stage,"validate",reason,0);
}
static void nx_sfm_init(struct nx_sfm_context *c,nx_sfm_stat_fn stat_fd) {
    memset(c,0,sizeof *c); c->anchor=c->root=c->logs=-1;
    c->euid=(unsigned long long)geteuid(); c->stat_fd=stat_fd;
}
static void nx_sfm_close(struct nx_sfm_context *c) {
    if(c->logs>=0) close(c->logs);
    if(c->root>=0) close(c->root);
    if(c->anchor>=0) close(c->anchor);
    c->anchor=c->root=c->logs=-1;
}
static int nx_sfm_observe(struct nx_sfm_context *c,int fd,struct nx_sfm_observation *o,const char *stage) {
    if(c->stat_fd(fd,&o->meta)) return nx_sfm_system(c,stage,"fstat");
    o->stat_valid=1;
    if(fstatfs(fd,&o->fs)) return nx_sfm_system(c,stage,"fstatfs");
    o->fs_valid=1; return 1;
}
static int nx_sfm_fat(const struct statfs *fs) {
    unsigned long type=(unsigned long)fs->f_type;
    return type==0x4d44UL||type==0x2011bab0UL;
}
static int nx_sfm_bind(struct nx_sfm_context *c,const struct nx_sfm_observation *base,
                       const struct nx_sfm_observation *o,const char *stage) {
    if(base->meta.device!=o->meta.device) return nx_sfm_logic(c,stage,"device_mismatch");
    if(base->fs.f_type!=o->fs.f_type||memcmp(&base->fs.f_fsid,&o->fs.f_fsid,sizeof base->fs.f_fsid))
        return nx_sfm_logic(c,stage,"filesystem_mismatch");
    return 1;
}
/* prepare=1 retains the launcher's source owner, logs owner/mode policy and
 * mkdir behavior. prepare=0 adds no new parent owner/mode requirement. Only
 * the literal production source can gain the FAT/exFAT owner/mode exception. */
static int nx_sfm_open(struct nx_sfm_context *c,const char *source,const char *fixture_source,int prepare,
                       const struct nx_sfm_identity *expected,nx_sfm_stat_fn stat_fd) {
    char copy[1024],*part,*save=NULL; int fd,next;
    nx_sfm_init(c,stat_fd);
    c->production=source&&!strcmp(source,"/mnt/mmc/nx300m-mod");
    if(!source||(!c->production&&(!fixture_source||strcmp(source,fixture_source))))
        return nx_sfm_logic(c,"media_identity","path_not_allowed");
    if(source[0]!='/'||strlen(source)>=sizeof copy) return nx_sfm_logic(c,"media_identity","path_not_allowed");
    strcpy(copy,source);
    fd=open("/",O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
    if(fd<0) return nx_sfm_system(c,"destination_parent","open");
    unsigned component=0;
    for(part=strtok_r(copy,"/",&save);part;part=strtok_r(NULL,"/",&save)) {
        if(!strcmp(part,".")||!strcmp(part,"..")) { close(fd); return nx_sfm_logic(c,"destination_parent","path_not_allowed"); }
        next=openat(fd,part,O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
        if(next<0) { nx_sfm_system(c,"destination_parent","openat"); close(fd); return 0; }
        close(fd); fd=next; ++component;
        if(c->production&&component==2) {
            c->anchor=dup(fd);
            if(c->anchor<0) { nx_sfm_system(c,"media_anchor","dup"); close(fd); return 0; }
        }
    }
    c->root=fd;
    if(c->production) {
        if(!nx_sfm_observe(c,c->anchor,&c->a,"media_anchor")) return 0;
        if(!S_ISDIR(c->a.meta.mode)) return nx_sfm_logic(c,"media_anchor","not_directory");
    }
    if(!nx_sfm_observe(c,c->root,&c->r,"media_identity")) return 0;
    if(!S_ISDIR(c->r.meta.mode)) return nx_sfm_logic(c,"media_identity","not_directory");
    if(c->production&&!nx_sfm_bind(c,&c->a,&c->r,"media_identity")) return 0;
    c->fat_exception=c->production&&nx_sfm_fat(&c->a.fs)&&nx_sfm_fat(&c->r.fs);
    if(expected&&(c->r.meta.device!=expected->device||c->r.meta.inode!=expected->inode))
        return nx_sfm_logic(c,"media_identity","source_identity_mismatch");
    if(prepare&&!c->fat_exception&&c->r.meta.uid!=c->euid) return nx_sfm_logic(c,"media_identity","owner_mismatch");
    if(c->production) {
        if(prepare&&mkdirat(c->root,"logs",0700)&&errno!=EEXIST) return nx_sfm_system(c,"destination_parent","mkdirat");
        c->logs=openat(c->root,"logs",O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
        if(c->logs<0) return nx_sfm_system(c,"destination_parent","openat");
    } else {
        c->logs=dup(c->root);
        if(c->logs<0) return nx_sfm_system(c,"destination_parent","dup");
    }
    if(!nx_sfm_observe(c,c->logs,&c->l,"destination_parent")) return 0;
    if(!S_ISDIR(c->l.meta.mode)) return nx_sfm_logic(c,"destination_parent","not_directory");
    if(!nx_sfm_bind(c,&c->r,&c->l,"destination_parent")) return 0;
    if(prepare&&!c->fat_exception) {
        if(c->l.meta.uid!=c->euid) return nx_sfm_logic(c,"destination_parent","owner_mismatch");
        if(c->l.meta.mode&022) return nx_sfm_logic(c,"destination_parent","unsafe_permissions");
    }
    return 1;
}
static int nx_sfm_target(struct nx_sfm_context *c,int fd) {
    if(!nx_sfm_observe(c,fd,&c->t,"destination")) return 0;
    if(!nx_sfm_bind(c,&c->l,&c->t,"destination")) return 0;
    if(!S_ISREG(c->t.meta.mode)) return nx_sfm_logic(c,"destination","not_regular");
    if(!c->fat_exception&&c->t.meta.uid!=c->euid) return nx_sfm_logic(c,"destination","owner_mismatch");
    return 1;
}
/* Only observed values are emitted. null never means a fabricated zero UID/FS.
 * This receipt context is deliberately outside frozen NDJSON. */
static void nx_sfm_observation_fields(FILE *out,const char *name,const struct nx_sfm_observation *o) {
    fprintf(out,",\"media_%s_uid\":",name);
    if(o->stat_valid) fprintf(out,"%llu",o->meta.uid); else fputs("null",out);
    fprintf(out,",\"media_%s_fs_type\":",name);
    if(o->fs_valid) fprintf(out,"%llu",(unsigned long long)(unsigned long)o->fs.f_type); else fputs("null",out);
}
static void nx_sfm_fields(FILE *out,const struct nx_sfm_context *c) {
    fprintf(out,",\"media_euid\":%llu,\"media_production_path\":%s,\"media_owner_policy\":\"%s\"",
        c->euid,c->production?"true":"false",c->fat_exception?"fat_fd_identity":"posix_owner");
    nx_sfm_observation_fields(out,"anchor",&c->a); nx_sfm_observation_fields(out,"root",&c->r);
    nx_sfm_observation_fields(out,"logs",&c->l); nx_sfm_observation_fields(out,"target",&c->t);
}
#endif
