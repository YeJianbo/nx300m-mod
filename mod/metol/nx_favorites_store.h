/* Reliable fixed-slot store for the LUT favorite list (2026-11-12).
 * Included after lut_tune.h / lut_choice_store.h helpers (checksum, stat64).
 *
 * The camera's FAT driver has produced duplicate directory entries when a
 * file is renamed over an existing one, and the previous single lut-favorites
 * file was rewritten through the temp+rename path on every change: a short or
 * interrupted write could leave the newest 27-look list unread while an older
 * duplicate entry still answered the read. Keep two fixed slots exactly like
 * the LUT choice store: only the older slot is ever rewritten, a write is
 * read back and verified before the previous record is disturbed, and a
 * failed or interrupted write leaves the previous valid list readable.
 *
 * Record: "NXFAV1 <slot-generation> <checksum> <body>"
 * body:   newline-separated look keys, or empty for a deliberate empty list.
 * Readers accept the highest-generation valid slot, then fall back to the
 * legacy .state/lut-favorites file (whitespace-separated keys) for old cards;
 * the legacy mirror is removed only after a new slot verifies.
 * Missing, empty, truncated and checksum-mismatched records are invalid. */
#ifndef NX_FAVORITES_STORE_H
#define NX_FAVORITES_STORE_H
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "lut_tune.h"

#define NX_FAVORITES_KEY_MAX 64
/* The panel loads at most NXUI_MAX_RECIPES (256) looks; keep the record
 * buffers small enough for the UI process stack. */
#define NX_FAVORITES_MAX_KEYS 256
#define NX_FAVORITES_BODY_MAX (NX_FAVORITES_MAX_KEYS*NX_FAVORITES_KEY_MAX+1)
/* Same key grammar as the panel's fscanf("%63s") reader: printable, no
 * whitespace or control characters, never empty. */
static inline int nx_favorites_key_valid(const char *key,size_t length) {
    if(!length||length>=NX_FAVORITES_KEY_MAX)return 0;
    for(size_t i=0;i<length;i++){unsigned char c=(unsigned char)key[i];
        if(c<=32||c==127)return 0;}
    return 1;
}
static inline int nx_favorites_body_valid(const char *body,size_t length) {
    size_t keys=0,i=0;
    while(i<length) {
        size_t start=i;
        while(i<length&&body[i]!='\n')i++;
        if(i>=length)return 0;   /* every key line ends with '\n' */
        if(!nx_favorites_key_valid(body+start,i-start))return 0;
        keys++;i++;
    }
    return keys<=NX_FAVORITES_MAX_KEYS;
}
static inline int nx_favorites_slot_path(char *out,size_t size,const char *root,int slot) {
    return snprintf(out,size,"%s/.state/lut-favorites.%d",root,slot)<(int)size;
}
static inline int nx_favorites_legacy_path(const char *root,char *out,size_t size) {
    return snprintf(out,size,"%s/.state/lut-favorites",root)<(int)size;
}
/* 1 = valid record; body is canonicalized (newline-separated, trailing '\n'). */
static inline int nx_favorites_slot_read(const char *path,char *body,size_t size,
                                         unsigned long long *generation) {
    int fd=open(path,O_RDONLY|O_NOFOLLOW|O_NONBLOCK);if(fd<0)return 0;
    if(!nx_lut_choice_regular_fd(fd)){close(fd);return 0;}
    FILE *in=fdopen(fd,"r");if(!in){close(fd);return 0;}
    char record[NX_FAVORITES_BODY_MAX+64],canonical[NX_FAVORITES_BODY_MAX+2];
    size_t n=fread(record,1,sizeof record-1,in);
    int overflow=fgetc(in)!=EOF,failed=ferror(in);fclose(in);
    record[n]=0;
    if(!n||overflow||failed||strlen(record)!=n)return 0;
    unsigned long long gen=0;unsigned hash=0;int used=0;
    if(sscanf(record,"NXFAV1 %llu %8x %n",&gen,&hash,&used)!=2||!gen||used<=0||
       (size_t)used>=sizeof canonical)return 0;
    size_t body_length=n-(size_t)used;
    memcpy(canonical,record+used,body_length+1);
    if(nx_tune_checksum(canonical)!=hash)return 0;
    if(!nx_favorites_body_valid(canonical,body_length))return 0;
    if(body_length>=size)return 0;
    memcpy(body,canonical,body_length+1);
    *generation=gen;return 1;
}
/* Legacy single-file reader: whitespace-separated keys in file order. */
static inline int nx_favorites_legacy_read(const char *path,char *body,size_t size) {
    int fd=open(path,O_RDONLY|O_NOFOLLOW|O_NONBLOCK);if(fd<0)return 0;
    if(!nx_lut_choice_regular_fd(fd)){close(fd);return 0;}
    FILE *in=fdopen(fd,"r");if(!in){close(fd);return 0;}
    char raw[NX_FAVORITES_BODY_MAX+64],canonical[NX_FAVORITES_BODY_MAX+2];
    size_t n=fread(raw,1,sizeof raw-1,in);
    int overflow=fgetc(in)!=EOF,failed=ferror(in);fclose(in);
    raw[n]=0;
    if(overflow||failed||!n)return 0;
    size_t length=0;int count=0;
    for(char *p=raw;*p;) {
        while(*p==' '||*p=='\t'||*p=='\r'||*p=='\n')p++;
        if(!*p)break;
        char *key=p;size_t key_length=0;
        while(*p&&*p!=' '&&*p!='\t'&&*p!='\r'&&*p!='\n'){p++;key_length++;}
        if(!nx_favorites_key_valid(key,key_length))return 0;
        if(length+key_length+2>sizeof canonical)return 0;
        memcpy(canonical+length,key,key_length);length+=key_length;
        canonical[length++]='\n';count++;
    }
    canonical[length]=0;
    if(length>=size)return 0;
    memcpy(body,canonical,length+1);
    return count>=0;   /* an empty legacy file is a valid empty list */
}
/* 1 = a valid record exists (any list, including empty); 0 = none found.
 * `recorded` (optional) distinguishes a fresh card from an unreadable one. */
static inline int nx_favorites_read_root_full(const char *root,char *body,size_t size,int *recorded) {
    unsigned long long best_generation=0;int found=0,any=0;
    char path[768],candidate[NX_FAVORITES_BODY_MAX];
    for(int i=0;i<2;i++) {
        if(!nx_favorites_slot_path(path,sizeof path,root,i))continue;
        unsigned long long generation=0;
        struct stat64 st;
        if(nx_lut_choice_lstat(path,&st))any=1;
        if(nx_favorites_slot_read(path,candidate,sizeof candidate,&generation)&&
           (!found||generation>best_generation)) {
            size_t length=strlen(candidate);
            if(length>=size)return 0;
            memcpy(body,candidate,length+1);
            best_generation=generation;found=1;
        }
    }
    if(!found&&nx_favorites_legacy_path(root,path,sizeof path)) {
        struct stat64 st;
        if(nx_lut_choice_lstat(path,&st))any=1;
        if(nx_favorites_legacy_read(path,body,size))found=1;
    }
    if(recorded)*recorded=any;
    return found;
}
static inline int nx_favorites_read_root(const char *root,char *body,size_t size) {
    return nx_favorites_read_root_full(root,body,size,NULL);
}
/* 1 = durable, -1 = verified visible but durability unconfirmed, 0 = failed.
 * The other slot is retained; the legacy mirror is removed only after the
 * new slot verifies, so an interrupted write keeps the previous valid list. */
static inline int nx_favorites_write_root(const char *root,const char *body,size_t length) {
    if(!root||length>=NX_FAVORITES_BODY_MAX||!nx_favorites_body_valid(body,length))return 0;
    char directory[768],lock_path[768],path[2][768];
    if(snprintf(directory,sizeof directory,"%s/.state",root)>=(int)sizeof directory||
       (mkdir(directory,0700)&&errno!=EEXIST))return 0;
    struct stat64 st;
    if(!nx_lut_choice_lstat(directory,&st)||!S_ISDIR(st.st_mode))return 0;
    int dir=open(directory,O_RDONLY|O_DIRECTORY|O_NOFOLLOW);if(dir<0)return 0;
    if(snprintf(lock_path,sizeof lock_path,"%s/lut-favorites.lock",directory)>=(int)sizeof lock_path){close(dir);return 0;}
    int lock=open(lock_path,O_RDWR|O_CREAT|O_NOFOLLOW|O_NONBLOCK,0600);
    if(lock<0||!nx_lut_choice_regular_fd(lock)||flock(lock,LOCK_EX)){if(lock>=0)close(lock);close(dir);return 0;}
    unsigned long long generation[2]={0,0};
    char check_body[NX_FAVORITES_BODY_MAX];
    for(int i=0;i<2;i++) {
        if(!nx_favorites_slot_path(path[i],sizeof path[i],root,i)){close(lock);close(dir);return 0;}
        unsigned long long existing=0;
        if(nx_favorites_slot_read(path[i],check_body,sizeof check_body,&existing))generation[i]=existing;
    }
    int slot=generation[0]<=generation[1]?0:1;
    unsigned long long next=(generation[0]>generation[1]?generation[0]:generation[1])+1;
    if(!next){close(lock);close(dir);return 0;}
    char record[NX_FAVORITES_BODY_MAX+64];
    char canonical[NX_FAVORITES_BODY_MAX+2];
    memcpy(canonical,body,length);canonical[length]=0;
    int text_length=snprintf(record,sizeof record,"NXFAV1 %llu %08x %s",next,
                             nx_tune_checksum(canonical),canonical);
    if(text_length<0||text_length>=(int)sizeof record){close(lock);close(dir);return 0;}
    int fd=open(path[slot],O_WRONLY|O_CREAT|O_NOFOLLOW|O_NONBLOCK,0600);
    int ok=fd>=0&&nx_lut_choice_regular_fd(fd)&&!ftruncate(fd,0);
    size_t written=0;
    while(ok&&written<(size_t)text_length) {
        ssize_t n=write(fd,record+written,(size_t)text_length-written);
        if(n>0)written+=(size_t)n;else if(n<0&&errno==EINTR)continue;else ok=0;
    }
    int durable=ok&&!fsync(fd);
    if(fd>=0&&close(fd))ok=0;
    /* Read back through the same parser before the previous record is
     * disturbed; a short or corrupted write never publishes. */
    unsigned long long verify_generation=0;
    char verify[NX_FAVORITES_BODY_MAX];
    int visible=ok&&nx_favorites_slot_read(path[slot],verify,sizeof verify,&verify_generation)&&
        verify_generation==next&&(size_t)text_length>0&&
        strlen(verify)==length&&!memcmp(verify,body,length);
    char legacy[768];
    if(visible&&nx_favorites_legacy_path(root,legacy,sizeof legacy)&&unlink(legacy)&&errno!=ENOENT)visible=0;
    if(visible&&fsync(dir))durable=0;
    if(close(dir))durable=0;
    close(lock);
    return visible?(durable?1:-1):0;
}
#endif
