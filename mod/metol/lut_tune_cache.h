#ifndef NX_LUT_TUNE_CACHE_H
#define NX_LUT_TUNE_CACHE_H
#include <dirent.h>
#include "lut_tune.h"
/* Issue 11: bounded eviction of composed per-look cache directories. Control
 * events only, never per frame. Protection: the runtime selection, the
 * persisted choice and the directory just composed are never evicted; fresh
 * entries get a one-minute grace so an in-flight compose cannot be removed
 * under a concurrent reader. Temp compose dirs (.nx-tune-*) are untouched. */
#define NX_TUNE_CACHE_MAX 24
static void nx_tune_evict_extract(const char *body,char *out,size_t size) {
    const char *mark=strstr(body,"/looks/nx-tune-");
    if(!mark)return;
    mark+=strlen("/looks/");
    size_t n=0;
    while(mark[n]&&mark[n]!='/'&&n+1<size){out[n]=mark[n];n++;}
    out[n]=0;
}
static void nx_tune_cache_evict(const char *root,const char *just_composed_dir) {
    char looks[768];struct stat st;
    if(!root||snprintf(looks,sizeof looks,"%s/looks",root)>=(int)sizeof looks)return;
    char protect[3][32];
    memset(protect,0,sizeof protect);
    const char *base=strrchr(just_composed_dir,'/');
    snprintf(protect[0],sizeof protect[0],"%.24s",base?base+1:"");
    char body[512];
    FILE *in=fopen("/var/run/nx300m-lut-selection","r");
    if(in){size_t n=fread(body,1,sizeof body-1,in);fclose(in);body[n]=0;nx_tune_evict_extract(body,protect[1],sizeof protect[1]);}
    char choice[768];
    if(snprintf(choice,sizeof choice,"%s/.state/lut-choice",root)<(int)sizeof choice){
        in=fopen(choice,"r");
        if(in){size_t n=fread(body,1,sizeof body-1,in);fclose(in);body[n]=0;nx_tune_evict_extract(body,protect[2],sizeof protect[2]);}
    }
    DIR *dir=opendir(looks);
    if(!dir)return;
    char names[64][32];long long ages[64];int count=0,fresh=0;
    long long now=(long long)time(0);
    struct dirent *entry;
    while((entry=readdir(dir))&&count<64) {
        if(strncmp(entry->d_name,"nx-tune-",8))continue;
        char path[832];
        if(snprintf(path,sizeof path,"%.767s/%.31s",looks,entry->d_name)>=(int)sizeof path)continue;
        if(stat(path,&st)||!S_ISDIR(st.st_mode))continue;
        /* Grace for fresh composes; they still count against the bound so a
         * rapid burst cannot walk past it, but they are never victims. */
        if(now-(long long)st.st_mtime<60){fresh++;continue;}
        snprintf(names[count],sizeof names[0],"%.31s",entry->d_name);
        ages[count]=(long long)st.st_mtime;count++;
    }
    closedir(dir);
    int remaining=count+fresh;
    while(remaining>NX_TUNE_CACHE_MAX) {
        int oldest=-1;
        for(int i=0;i<count;i++) {
            if(!names[i][0])continue;
            int protected_entry=0;
            for(int p=0;p<3;p++)if(protect[p][0]&&!strcmp(names[i],protect[p]))protected_entry=1;
            if(protected_entry)continue;
            if(oldest<0||ages[i]<ages[oldest])oldest=i;
        }
        if(oldest<0)break;
        char victim[832];
        if(snprintf(victim,sizeof victim,"%.767s/%.31s",looks,names[oldest])>=(int)sizeof victim)break;
        for(int pass=0;pass<2;pass++) {
            DIR *v=opendir(victim);if(!v)break;
            struct dirent *ve;int entries=0;
            while((ve=readdir(v))) {
                if(!strcmp(ve->d_name,".")||!strcmp(ve->d_name,".."))continue;
                char fp[1100];snprintf(fp,sizeof fp,"%.831s/%.255s",victim,ve->d_name);
                unlink(fp);entries++;
            }
            closedir(v);
            if(!entries)break;
        }
        rmdir(victim);
        names[oldest][0]=0;remaining--;
    }
}
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
        nx_tune_cache_evict(nx_lut_data_root(),directory);
    }
    (void)strength;
    int n=snprintf(path,256,"%s/table.d4lut",directory);return n>0&&n<256?0:4;
}
/* Shared fixed-slot persistence for the UI, CLI and App. */
static inline int nx_tune_save(const char *id,const struct nx_lut_tune *t,unsigned strength) {
    if(!nx_lut_id_valid(id))return 0;
    return nx_tune_preset_save(nx_lut_data_root(),id,t,strength);
}
#endif
