/* Reliable fixed-slot store for the current LUT choice (2026-11-11).
 * Included after lut_tune.h (checksum + regular-file helpers come from
 * lut_tune_store.h).
 *
 * The camera's FAT driver has produced duplicate directory entries when
 * renaming over an existing file, and the previous single lut-choice file
 * was rewritten in place with O_TRUNC: a failed write could clear the only
 * record, and an empty file read back as "applied". Keep two fixed slots
 * instead, exactly like the tune preset store: only the older slot is ever
 * rewritten, a write is verified before the old record is disturbed, and an
 * interrupted write leaves the previous valid record readable.
 *
 * Record: "NXCH1 <slot-generation> <checksum> <body>"
 * body:   "0\n" (deliberate Off) or "<ram-generation> <table path> <strength>\n"
 * Readers accept the highest-generation valid slot, then fall back to the
 * legacy .state/lut-choice and .state/lut-selection files for old cards.
 * Missing, empty, truncated and checksum-mismatched records are all invalid;
 * they never read as "applied" and never read as Off. */
#ifndef NX_LUT_CHOICE_STORE_H
#define NX_LUT_CHOICE_STORE_H
#include <errno.h>
#include <limits.h>
#include <stdint.h>
#include <string.h>
#include <sys/stat.h>
#include "lut_tune.h"
/* glibc 2.13 exposes the versioned ARM stat64 entry points (the same
 * pattern nxui.c uses); stat64 keeps one layout under either
 * _FILE_OFFSET_BITS setting and never follows symlinks. */
extern int __lxstat64(int, const char *, struct stat64 *);
extern int __fxstat64(int, int, struct stat64 *);
static inline int nx_lut_choice_lstat(const char *path, struct stat64 *st) {
    return __lxstat64(3, path, st) == 0;
}
/* Same stat64 family as the lstat above; the 32-bit struct stat variant in
 * lut_tune_store is not reliable on every filesystem this store reads. */
static inline int nx_lut_choice_regular_fd(int fd) {
    struct stat64 st;
    return !__fxstat64(3, fd, &st) && S_ISREG(st.st_mode);
}
struct nx_lut_choice {
    int valid,off;                  /* off=1 only for a valid deliberate Off */
    unsigned long long slot_generation;
    unsigned generation,strength;   /* RAM generation + strength of the body */
    char table[256];                /* empty for Off */
};
static inline int nx_lut_choice_slot_path(char *out,size_t size,const char *root,int slot) {
    return snprintf(out,size,"%s/.state/lut-choice.%d",root,slot)<(int)size;
}
static inline int nx_lut_choice_legacy_path(const char *root,char *out,size_t size) {
    return snprintf(out,size,"%s/.state/lut-choice",root)<(int)size;
}
/* Body grammar shared with the legacy files: exact "0\n", or two/three
 * space/tab-separated fields (generation, absolute printable path,
 * optional strength <= 100) with nothing after the final newline. */
static inline int nx_lut_choice_parse_uint(const char *text,unsigned long *out) {
    if(!*text)return 0;
    unsigned long value=0;
    for(const char *p=text;*p;p++) {
        if(*p<'0'||*p>'9'||value>(ULONG_MAX-(unsigned long)(*p-'0'))/10)return 0;
        value=value*10+(unsigned long)(*p-'0');
    }
    *out=value;return 1;
}
static inline int nx_lut_choice_body_parse(const char *body,struct nx_lut_choice *out) {
    size_t n=strlen(body);
    if(!n||body[n-1]!='\n')return 0;
    for(size_t i=0;i<n-1;i++)if(body[i]=='\n')return 0;
    if(n==2&&body[0]=='0') {out->off=1;out->table[0]=0;out->generation=0;out->strength=100;return 1;}
    char *copy=strdup(body);if(!copy)return 0;
    copy[n-1]=0;   /* the trailing newline is not part of the last field */
    char *fields[3];int count=0,ok=1;
    for(char *p=copy;*p;) {
        while(*p==' '||*p=='\t')p++;
        if(!*p)break;
        if(count==3){ok=0;break;}
        fields[count++]=p;
        while(*p&&*p!=' '&&*p!='\t')p++;
        if(*p)*p++=0;
    }
    unsigned long generation=0,strength=100;
    if(ok&&(count==2||count==3)) {
        ok=nx_lut_choice_parse_uint(fields[0],&generation)&&generation<=0xFFFFFFFFUL;
        if(ok&&count==3)ok=nx_lut_choice_parse_uint(fields[2],&strength)&&strength<=100;
    } else ok=0;
    size_t path_length=ok?strlen(fields[1]):0;
    if(ok&&(path_length<1||path_length>=sizeof out->table||fields[1][0]!='/'))ok=0;
    for(size_t i=0;ok&&i<path_length;i++){unsigned char c=(unsigned char)fields[1][i];if(c<=32||c==127)ok=0;}
    if(ok) {
        out->off=0;out->generation=(unsigned)generation;out->strength=(unsigned)strength;
        memcpy(out->table,fields[1],path_length+1);
    }
    free(copy);
    return ok;
}
/* 1 = valid slot record. Slot generation is validated to increase so a stale
 * rewrite can never win over a newer slot. */
static inline int nx_lut_choice_slot_read(const char *path,struct nx_lut_choice *out) {
    int fd=open(path,O_RDONLY|O_NOFOLLOW|O_NONBLOCK);if(fd<0)return 0;
    if(!nx_lut_choice_regular_fd(fd)){close(fd);return 0;}
    FILE *in=fdopen(fd,"r");if(!in){close(fd);return 0;}
    char body[520],canonical[520];
    size_t n=fread(body,1,sizeof body-1,in);
    int overflow=fgetc(in)!=EOF,failed=ferror(in);fclose(in);
    body[n]=0;
    if(!n||overflow||failed||strlen(body)!=n||body[n-1]!='\n')return 0;
    unsigned long long generation=0;unsigned hash=0;int used=0;
    if(sscanf(body,"NXCH1 %llu %8x %n",&generation,&hash,&used)!=2||!generation||used<=0||
       (size_t)used>=sizeof canonical-1)return 0;
    memcpy(canonical,body+used,n-(size_t)used+1);
    if(nx_tune_checksum(canonical)!=hash)return 0;
    struct nx_lut_choice parsed;memset(&parsed,0,sizeof parsed);
    if(!nx_lut_choice_body_parse(canonical,&parsed))return 0;
    *out=parsed;out->slot_generation=generation;return 1;
}
static inline int nx_lut_choice_legacy_read(const char *path,struct nx_lut_choice *out) {
    int fd=open(path,O_RDONLY|O_NOFOLLOW|O_NONBLOCK);if(fd<0)return 0;
    if(!nx_lut_choice_regular_fd(fd)){close(fd);return 0;}
    FILE *in=fdopen(fd,"r");if(!in){close(fd);return 0;}
    char body[520];size_t n=fread(body,1,sizeof body-1,in);
    int overflow=fgetc(in)!=EOF,failed=ferror(in);fclose(in);body[n]=0;
    struct nx_lut_choice parsed;memset(&parsed,0,sizeof parsed);
    if(overflow||failed||!n||strlen(body)!=n||!nx_lut_choice_body_parse(body,&parsed))return 0;
    *out=parsed;return 1;
}
/* 1 = a valid record exists (Off or selection); 0 = missing or all corrupt.
 * `recorded` (optional) tells "no file at all" apart from "present but
 * invalid", so callers can distinguish a fresh card from damage. */
static inline int nx_lut_choice_read_root_full(const char *root,struct nx_lut_choice *out,int *recorded) {
    struct nx_lut_choice best,slot;memset(&best,0,sizeof best);memset(out,0,sizeof *out);
    char path[PATH_MAX];int found=0,any=0;
    for(int i=0;i<2;i++) {
        if(!nx_lut_choice_slot_path(path,sizeof path,root,i))continue;
        struct stat64 st;
        if(nx_lut_choice_lstat(path,&st))any=1;
        if(nx_lut_choice_slot_read(path,&slot)&&(!found||slot.slot_generation>best.slot_generation)){best=slot;found=1;}
    }
    static const char *const legacy_names[]={"lut-choice","lut-selection"};
    for(unsigned i=0;!found&&i<sizeof legacy_names/sizeof *legacy_names;i++) {
        int n=snprintf(path,sizeof path,"%s/.state/%s",root,legacy_names[i]);
        if(n<0||n>=(int)sizeof path)continue;
        struct stat64 st;
        if(nx_lut_choice_lstat(path,&st))any=1;
        if(nx_lut_choice_legacy_read(path,&slot)){best=slot;found=1;}
    }
    if(recorded)*recorded=any;
    if(!found)return 0;
    *out=best;out->valid=1;return 1;
}
static inline int nx_lut_choice_read_root(const char *root,struct nx_lut_choice *out) {
    return nx_lut_choice_read_root_full(root,out,NULL);
}
/* 1 = durable, -1 = complete and verified visible but durability unconfirmed,
 * 0 = failed. The other slot is retained; the legacy mirror is removed only
 * after the new slot verifies. */
static inline int nx_lut_choice_write_root(const char *root,const struct nx_lut_choice *record) {
    if(!root)return 0;
    char body[320];
    if(record->off)strcpy(body,"0\n");
    else {
        int n=snprintf(body,sizeof body,"%u %s %u\n",record->generation,record->table,record->strength);
        if(n<0||n>=(int)sizeof body)return 0;
    }
    char directory[PATH_MAX],lock_path[PATH_MAX],path[2][PATH_MAX];
    if(snprintf(directory,sizeof directory,"%s/.state",root)>=(int)sizeof directory||
       (mkdir(directory,0700)&&errno!=EEXIST))return 0;
    struct stat64 st;
    if(!nx_lut_choice_lstat(directory,&st)||!S_ISDIR(st.st_mode))return 0;
    int dir=open(directory,O_RDONLY|O_DIRECTORY|O_NOFOLLOW);if(dir<0)return 0;
    if(snprintf(lock_path,sizeof lock_path,"%s/lut-choice.lock",directory)>=(int)sizeof lock_path){close(dir);return 0;}
    int lock=open(lock_path,O_RDWR|O_CREAT|O_NOFOLLOW|O_NONBLOCK,0600);
    if(lock<0||!nx_lut_choice_regular_fd(lock)||flock(lock,LOCK_EX)){if(lock>=0)close(lock);close(dir);return 0;}
    unsigned long long generation[2]={0,0};struct nx_lut_choice existing;
    for(int i=0;i<2;i++) {
        if(!nx_lut_choice_slot_path(path[i],sizeof path[i],root,i)){close(lock);close(dir);return 0;}
        if(nx_lut_choice_slot_read(path[i],&existing))generation[i]=existing.slot_generation;
    }
    int slot=generation[0]<=generation[1]?0:1;
    unsigned long long next=(generation[0]>generation[1]?generation[0]:generation[1])+1;
    if(!next||next==ULLONG_MAX){close(lock);close(dir);return 0;}
    char text[520];
    int length=snprintf(text,sizeof text,"NXCH1 %llu %08x %s",next,nx_tune_checksum(body),body);
    int fd=length>0&&length<(int)sizeof text?open(path[slot],O_WRONLY|O_CREAT|O_NOFOLLOW|O_NONBLOCK,0600):-1;
    int ok=fd>=0&&nx_lut_choice_regular_fd(fd)&&!ftruncate(fd,0);
    size_t written=0;
    while(ok&&written<(size_t)length) {
        ssize_t n=write(fd,text+written,(size_t)length-written);
        if(n>0)written+=(size_t)n;else if(n<0&&errno==EINTR)continue;else ok=0;
    }
    int durable=ok&&!fsync(fd);
    if(fd>=0&&close(fd))ok=0;
    struct nx_lut_choice check;
    int visible=ok&&nx_lut_choice_slot_read(path[slot],&check)&&check.slot_generation==next&&
        check.off==record->off&&
        (record->off?check.table[0]==0:(check.strength==record->strength&&
         check.generation==record->generation&&!strcmp(check.table,record->table)));
    /* Only after the new slot verifies does the legacy mirror go away, so a
     * crash between the two still leaves one valid old-format record. */
    char legacy[PATH_MAX];
    if(visible&&nx_lut_choice_legacy_path(root,legacy,sizeof legacy)&&unlink(legacy)&&errno!=ENOENT)visible=0;
    if(visible&&fsync(dir))durable=0;
    if(close(dir))durable=0;
    close(lock);
    return visible?(durable?1:-1):0;
}
#endif
