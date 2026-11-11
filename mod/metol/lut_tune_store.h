/* Included after lut_tune's value/parser definitions.
 * The camera's FAT driver has produced duplicate directory entries when
 * renaming over an existing preference. Keep two fixed slots instead: only
 * the older slot is rewritten, and readers accept complete checksummed
 * records with the highest generation. Legacy preferences stay readable. */
#ifndef NX_LUT_TUNE_STORE_H
#define NX_LUT_TUNE_STORE_H
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>
static inline int nx_tune_key_valid(const char *key) {
    size_t n=strlen(key);if(!n||n>63)return 0;
    for(size_t i=0;i<n;i++)if(!((key[i]>='a'&&key[i]<='z')||(key[i]>='0'&&key[i]<='9')||key[i]=='-'||key[i]=='_'))return 0;
    return 1;
}
static inline int nx_tune_regular_fd(int fd) {
    /* Use a fixed layout for both the 32-bit UI and large-file engine. */
#if defined(__GLIBC__)
    extern int __fxstat64(int,int,struct stat64 *);
    struct stat64 st;return !__fxstat64(3,fd,&st)&&S_ISREG(st.st_mode);
#else
    struct stat st;return !fstat(fd,&st)&&S_ISREG(st.st_mode);
#endif
}
static inline uint32_t nx_tune_checksum(const char *text) {
    uint32_t hash=2166136261U;
    for(const unsigned char *p=(const unsigned char *)text;*p;p++){hash^=*p;hash*=16777619U;}
    return hash;
}
static inline int nx_tune_slot_read(const char *path,const char *id,struct nx_lut_tune *t,unsigned *strength,unsigned long long *generation) {
    int fd=open(path,O_RDONLY|O_NOFOLLOW|O_NONBLOCK);if(fd<0)return 0;
    if(!nx_tune_regular_fd(fd)){close(fd);return 0;}
    FILE *in=fdopen(fd,"r");if(!in){close(fd);return 0;}
    char body[320],tag[8],key[64],canonical[192];unsigned hash,s;unsigned long long gen;
    struct nx_lut_tune next;int used=0;
    size_t n=fread(body,1,sizeof body-1,in);int overflow=fgetc(in)!=EOF,failed=ferror(in);fclose(in);
    body[n]=0;
    if(!n||overflow||failed||strlen(body)!=n||body[n-1]!='\n'||
       sscanf(body,"%7s %llu %8x %n",tag,&gen,&hash,&used)!=3||strcmp(tag,"NXTP1")||!gen||used<=0||
       !nx_tune_parse(body+used,key,&next,&s)||strcmp(key,id)||
       !nx_tune_snprint(canonical,sizeof canonical,key,&next,s)||strcmp(canonical,body+used)||
       nx_tune_checksum(canonical)!=hash)return 0;
    *t=next;if(strength)*strength=s;*generation=gen;return 1;
}
static inline int nx_tune_slot_path(char *out,size_t size,const char *root,const char *id,int slot) {
    return nx_tune_key_valid(id)&&snprintf(out,size,"%s/.state/lut-preset-%s.%d",root,id,slot)<(int)size;
}
static inline int nx_tune_preset_load(const char *root,const char *id,struct nx_lut_tune *t,unsigned *strength) {
    unsigned long long best=0;struct nx_lut_tune next;unsigned level;char path[768];
    for(int slot=0;slot<2;slot++) {
        unsigned long long generation=0;
        if(nx_tune_slot_path(path,sizeof path,root,id,slot)&&nx_tune_slot_read(path,id,&next,&level,&generation)&&generation>best) {
            best=generation;*t=next;if(strength)*strength=level;
        }
    }
    return best!=0;
}
/* 1 = durable, -1 = complete and visible but durability unconfirmed,
 * 0 = failed. The previous valid slot is never truncated or renamed. */
static inline int nx_tune_preset_save(const char *root,const char *id,const struct nx_lut_tune *t,unsigned strength) {
    if(!root||!nx_tune_key_valid(id)||!nx_tune_valid(t)||strength>100)return 0;
    char directory[768],path[2][768],lock_path[768],payload[192],record[320];
    if(snprintf(directory,sizeof directory,"%s/.state",root)>=(int)sizeof directory||
       (mkdir(directory,0700)&&errno!=EEXIST))return 0;
    int dir=open(directory,O_RDONLY|O_DIRECTORY|O_NOFOLLOW);if(dir<0)return 0;
    if(snprintf(lock_path,sizeof lock_path,"%s/lut-preset-%s.lock",directory,id)>=(int)sizeof lock_path){close(dir);return 0;}
    int lock=open(lock_path,O_RDWR|O_CREAT|O_NOFOLLOW|O_NONBLOCK,0600);
    if(lock<0||!nx_tune_regular_fd(lock)||flock(lock,LOCK_EX)){if(lock>=0)close(lock);close(dir);return 0;}
    unsigned long long gen[2]={0,0};struct nx_lut_tune previous;unsigned level;
    for(int i=0;i<2;i++) {
        if(!nx_tune_slot_path(path[i],sizeof path[i],root,id,i)){close(lock);close(dir);return 0;}
        nx_tune_slot_read(path[i],id,&previous,&level,&gen[i]);
    }
    int slot=gen[0]<=gen[1]?0:1;unsigned long long generation=gen[0]>gen[1]?gen[0]:gen[1];
    if(generation==ULLONG_MAX||!nx_tune_snprint(payload,sizeof payload,id,t,strength)){close(lock);close(dir);return 0;}
    generation++;
    int length=snprintf(record,sizeof record,"NXTP1 %llu %08x %s",generation,nx_tune_checksum(payload),payload);
    int fd=length>0&&length<(int)sizeof record?open(path[slot],O_WRONLY|O_CREAT|O_NOFOLLOW|O_NONBLOCK,0600):-1;
    int ok=fd>=0&&nx_tune_regular_fd(fd)&&!ftruncate(fd,0);size_t written=0;
    while(ok&&written<(size_t)length) {
        ssize_t n=write(fd,record+written,(size_t)length-written);
        if(n>0)written+=(size_t)n;else if(n<0&&errno==EINTR)continue;else ok=0;
    }
    if(ok&&fsync(fd))ok=0;
    if(fd>=0&&close(fd))ok=0;
    unsigned long long check_gen=0;struct nx_lut_tune check;unsigned check_strength;
    int visible=nx_tune_slot_read(path[slot],id,&check,&check_strength,&check_gen)&&check_gen==generation&&
        check_strength==strength&&!memcmp(&check,t,sizeof check);
    if(fsync(dir))ok=0;
    if(close(dir))ok=0;
    close(lock);
    return visible?(ok?1:-1):0;
}
#endif
