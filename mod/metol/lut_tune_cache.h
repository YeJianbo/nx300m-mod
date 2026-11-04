#ifndef NX_LUT_TUNE_CACHE_H
#define NX_LUT_TUNE_CACHE_H
#include "lut_tune.h"
/* The cache key includes both source tables and the output controls. A cache
 * hit is also compared byte for byte, so a hash collision cannot alias looks.
 * All writes finish in a private directory before publishing a selectable path. */
static inline int nx_tune_cache(const char *id,const struct nx_lut_tune *t,unsigned strength,char path[256]) {
    char source[256];int composed;
    if(!nx_tune_valid(t)||!nx_lut_validate_look(id,source,&composed))return 5;
    if(nx_tune_zero(t)){strcpy(path,source);return 0;}
    uint16_t original[2][2190],tables[2][2190];
    const char *names[2]={"table.d4lut","table-stock3d.d4lut"};
    uint64_t hash=1469598103934665603ULL;
    for(int k=0;k<=composed;k++) {
        char file[512];snprintf(file,sizeof file,"%s/looks/%s/%s",nx_lut_data_root(),id,names[k]);
        FILE *in=fopen(file,"rb");if(!in)return 4;
        int ok=fread(original[k],1,4380,in)==4380 && fgetc(in)==EOF && !ferror(in);fclose(in);if(!ok)return 5;
        nx_tune_table(original[k],tables[k],t);
        for(unsigned j=0;j<4380;j++){hash^=((unsigned char *)original[k])[j];hash*=1099511628211ULL;}
    }
    for(const unsigned char *p=(const unsigned char *)id;*p;p++){hash^=*p;hash*=1099511628211ULL;}
    for(int i=0;i<NX_TUNE_FIELDS;i++){hash^=(unsigned)(t->value[i]+10);hash*=1099511628211ULL;}
    hash^=1;hash*=1099511628211ULL; /* output transform format */
    char directory[512],temp[512],file[600];
    snprintf(directory,sizeof directory,"%s/looks/nx-tune-%016llx",nx_lut_data_root(),(unsigned long long)hash);
    struct stat st;
    if(!lstat(directory,&st)) {
        if(!S_ISDIR(st.st_mode))return 5;
        char base[64];struct nx_lut_tune saved;unsigned level;
        snprintf(file,sizeof file,"%s/tune.txt",directory);
        if(!nx_tune_read(file,base,&saved,&level)||strcmp(base,id)||memcmp(&saved,t,sizeof saved))return 5;
        for(int k=0;k<=composed;k++) {
            snprintf(file,sizeof file,"%s/%s",directory,names[k]);
            uint16_t check[2190];int fd=open(file,O_RDONLY|O_NOFOLLOW);if(fd<0)return 5;
            int ok=read(fd,check,4380)==4380 && read(fd,&st,1)==0 && !memcmp(check,tables[k],4380);close(fd);if(!ok)return 5;
        }
        if(!composed) {
            snprintf(file,sizeof file,"%s/table-stock3d.d4lut",directory);
            if(!lstat(file,&st)||errno!=ENOENT)return 5;
        }
    } else {
        if(errno!=ENOENT)return 4;
        snprintf(temp,sizeof temp,"%s/looks/.nx-tune-%ld",nx_lut_data_root(),(long)getpid());
        if(mkdir(temp,0700))return 4;
        int ok=1;
        for(int k=0;k<=composed;k++) {
            snprintf(file,sizeof file,"%s/%s",temp,names[k]);int fd=open(file,O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW,0600);
            if(fd<0){ok=0;break;}
            if(write(fd,tables[k],4380)!=4380||fsync(fd))ok=0;
            if(close(fd))ok=0;
            if(!ok)break;
        }
        snprintf(file,sizeof file,"%s/tune.txt",temp);
        int fd=ok?open(file,O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW,0600):-1;
        FILE *out=fd>=0?fdopen(fd,"w"):NULL;
        if(!out){if(fd>=0)close(fd);ok=0;}
        else {if(!nx_tune_print(out,id,t,100)||fflush(out)||fsync(fd))ok=0;if(fclose(out))ok=0;}
        if(ok && rename(temp,directory))ok=0;
        if(!ok) {
            for(int k=0;k<2;k++){snprintf(file,sizeof file,"%s/%s",temp,names[k]);unlink(file);}
            snprintf(file,sizeof file,"%s/tune.txt",temp);unlink(file);rmdir(temp);return 4;
        }
        fd=open(directory,O_RDONLY|O_DIRECTORY);if(fd<0)return 4;ok=!fsync(fd);close(fd);if(!ok)return 4;
        snprintf(file,sizeof file,"%s/looks",nx_lut_data_root());fd=open(file,O_RDONLY|O_DIRECTORY);
        if(fd<0)return 4;
        ok=!fsync(fd);close(fd);if(!ok)return 4;
    }
    (void)strength;
    int n=snprintf(path,256,"%s/table.d4lut",directory);return n>0&&n<256?0:4;
}
static inline int nx_tune_save(const char *id,const struct nx_lut_tune *t,unsigned strength) {
    char directory[512],file[640];struct stat st;
    struct nx_lut_tune saved;unsigned previous;
    if(nx_tune_load(nx_lut_data_root(),id,&saved,&previous)&&previous==strength&&!memcmp(&saved,t,sizeof saved))return 1;
    snprintf(directory,sizeof directory,"%s/.state",nx_lut_data_root());
    if(mkdir(directory,0700)&&errno!=EEXIST)return 0;
    if(lstat(directory,&st)||!S_ISDIR(st.st_mode))return 0;
    snprintf(file,sizeof file,"%s/lut-tune-%s",directory,id);
    int fd=open(file,O_WRONLY|O_CREAT|O_TRUNC|O_NOFOLLOW,0600);if(fd<0)return 0;
    FILE *out=fdopen(fd,"w");if(!out){close(fd);return 0;}
    int ok=nx_tune_print(out,id,t,strength);if(fflush(out)||fsync(fd))ok=0;if(fclose(out))ok=0;
    fd=open(directory,O_RDONLY|O_DIRECTORY);if(fd<0)return 0;if(fsync(fd))ok=0;close(fd);return ok;
}
#endif
