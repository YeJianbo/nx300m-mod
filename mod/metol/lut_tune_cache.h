#ifndef NX_LUT_TUNE_CACHE_H
#define NX_LUT_TUNE_CACHE_H
#include <dirent.h>
#include <stdlib.h>
#include "lut_tune.h"
#include "lut_choice_store.h"
/* Issue 11 + 2026-11-11 acceptance: bounded eviction of composed per-look
 * cache directories. Control events only, never per frame.
 *
 * Contract enforced here:
 *  - Candidates are found with lstat: a `looks/nx-tune-*` symlink (live or
 *    dangling) is never followed and never deleted; it is reported as an
 *    anomalous entry, not silently counted or destroyed.
 *  - Only files the generator owns (tune.txt, table.d4lut,
 *    table-stock3d.d4lut) are unlinked. Any other content marks the
 *    directory anomalous and the whole directory is preserved.
 *  - A directory counts as removed only after every owned file was unlinked
 *    AND rmdir succeeded. A failed unlink/rmdir is reported as failed and
 *    never decrements the remaining count.
 *  - Protected from eviction: the runtime RAM selection, the saved choice
 *    in both valid slots and legacy fallback records, and the directory
 *    just composed. Fresh composes keep their
 *    one-minute grace; they count toward the total but are never victims.
 *  - The whole candidate set is scanned (the previous 64-entry cap left 100
 *    stale caches at 60). Protected/fresh/anomalous directories may keep the
 *    total above the target; the summary line reports that state instead of
 *    pretending the hard bound was met. Temp compose dirs (.nx-tune-*) are
 *    untouched: they do not match the nx-tune- prefix scan. */
#define NX_TUNE_CACHE_MAX 24
/* Transient scan memory ceiling. Far above any real burst; hitting it is
 * reported as truncated rather than silently ignored. */
#define NX_TUNE_SCAN_LIMIT 4096
static int nx_tune_cache_name_valid(const char *name) {
    if(strlen(name)!=24||strncmp(name,"nx-tune-",8))return 0;
    for(int i=8;i<24;i++)if(!((name[i]>='0'&&name[i]<='9')||(name[i]>='a'&&name[i]<='f')))return 0;
    return 1;
}
static void nx_tune_evict_extract(const char *body,char *out,size_t size) {
    const char *mark=strstr(body,"/looks/nx-tune-");
    if(!mark)return;
    mark+=strlen("/looks/");
    size_t n=0;
    while(mark[n]&&mark[n]!='/'&&n+1<size){out[n]=mark[n];n++;}
    out[n]=0;
}
struct nx_tune_candidate { char name[32]; long long age; int fresh,protected_entry,anomalous,failed; };
static int nx_tune_candidate_compare(const void *a,const void *b) {
    long long delta=((const struct nx_tune_candidate*)a)->age-((const struct nx_tune_candidate*)b)->age;
    return delta<0?-1:delta>0?1:0;
}
/* Returns the number of cache directories remaining after this pass. */
static int nx_tune_cache_evict(const char *root,const char *just_composed_dir) {
    char looks[768];struct stat64 st;
    if(!root||snprintf(looks,sizeof looks,"%s/looks",root)>=(int)sizeof looks)return -1;
    char protect[6][32];
    memset(protect,0,sizeof protect);
    const char *base=strrchr(just_composed_dir,'/');
    snprintf(protect[0],sizeof protect[0],"%.24s",base?base+1:"");
    char body[512];
    FILE *in=fopen("/var/run/nx300m-lut-selection","r");
    if(in){size_t n=fread(body,1,sizeof body-1,in);fclose(in);body[n]=0;nx_tune_evict_extract(body,protect[1],sizeof protect[1]);}
    struct nx_lut_choice choice;char saved_path[PATH_MAX];
    for(int i=0;i<2;i++)
        if(nx_lut_choice_slot_path(saved_path,sizeof saved_path,root,i)&&
           nx_lut_choice_slot_read(saved_path,&choice)&&!choice.off)
            nx_tune_evict_extract(choice.table,protect[2+i],sizeof protect[2+i]);
    static const char *const legacy_names[]={"lut-choice","lut-selection"};
    for(int i=0;i<2;i++) {
        int n=snprintf(saved_path,sizeof saved_path,"%s/.state/%s",root,legacy_names[i]);
        if(n>0&&n<(int)sizeof saved_path&&nx_lut_choice_legacy_read(saved_path,&choice)&&!choice.off)
            nx_tune_evict_extract(choice.table,protect[4+i],sizeof protect[4+i]);
    }
    DIR *dir=opendir(looks);
    if(!dir)return -1;
    struct nx_tune_candidate *candidates=NULL;
    int count=0,links=0,truncated=0,invalid_names=0;
    long long now=(long long)time(0);
    struct dirent *entry;
    while((entry=readdir(dir))) {
        if(strncmp(entry->d_name,"nx-tune-",8))continue;
        if(!nx_tune_cache_name_valid(entry->d_name)){invalid_names++;continue;}
        char path[832];
        if(snprintf(path,sizeof path,"%.767s/%.31s",looks,entry->d_name)>=(int)sizeof path)continue;
        if(!nx_lut_choice_lstat(path,&st))continue;                /* raced away: not ours to judge */
        if(S_ISLNK(st.st_mode)) {links++;continue;} /* never follow, never delete */
        if(!S_ISDIR(st.st_mode))continue;
        if(count==NX_TUNE_SCAN_LIMIT){truncated=1;break;}
        struct nx_tune_candidate *grown=realloc(candidates,(size_t)(count+1)*sizeof *candidates);
        if(!grown){truncated=1;break;}
        candidates=grown;
        snprintf(candidates[count].name,sizeof candidates[count].name,"%.31s",entry->d_name);
        candidates[count].age=(long long)st.st_mtime;
        candidates[count].fresh=now-(long long)st.st_mtime<60;
        candidates[count].protected_entry=0;
        for(unsigned p=0;p<sizeof protect/sizeof *protect;p++)if(protect[p][0]&&!strcmp(candidates[count].name,protect[p]))candidates[count].protected_entry=1;
        candidates[count].anomalous=0;
        candidates[count].failed=0;
        count++;
    }
    closedir(dir);
    qsort(candidates,(size_t)count,sizeof *candidates,nx_tune_candidate_compare);
    int deleted=0,remaining=count;
    static const char *const owned[]={"tune.txt","table.d4lut","table-stock3d.d4lut"};
    for(int i=0;i<count&&remaining>NX_TUNE_CACHE_MAX;i++) {
        struct nx_tune_candidate *victim=&candidates[i];
        if(victim->protected_entry||victim->fresh||victim->anomalous||victim->failed)continue;
        char directory[832];
        if(snprintf(directory,sizeof directory,"%.767s/%.31s",looks,victim->name)>=(int)sizeof directory){victim->failed=1;continue;}
        /* O_NOFOLLOW refuses a directory swapped for a symlink after lstat. */
        int fd=open(directory,O_RDONLY|O_DIRECTORY|O_NOFOLLOW);
        if(fd<0){victim->failed=1;continue;}
        DIR *inside=fdopendir(fd);
        if(!inside){close(fd);victim->failed=1;continue;}
        struct dirent *file;int unknown=0,unlink_failed=0;
        while((file=readdir(inside))) {
            if(!strcmp(file->d_name,".")||!strcmp(file->d_name,".."))continue;
            int known=0;
            for(unsigned k=0;k<sizeof owned/sizeof *owned;k++)if(!strcmp(file->d_name,owned[k]))known=1;
            if(!known)unknown=1;
        }
        rewinddir(inside);
        if(!unknown) {
            while((file=readdir(inside))) {
                int known=0;
                for(unsigned k=0;k<sizeof owned/sizeof *owned;k++)if(!strcmp(file->d_name,owned[k]))known=1;
                if(!known)continue;
                /* Keep deletion bound to the opened directory if its path
                 * is renamed or replaced after the admission check. */
                if(unlinkat(dirfd(inside),file->d_name,0)&&errno!=ENOENT)unlink_failed=1;
            }
        }
        closedir(inside);
        if(unknown) {victim->anomalous=1;continue;}   /* preserve non-generator content */
        if(unlink_failed) {victim->failed=1;continue;} /* removal did not happen */
        if(rmdir(directory)) {victim->failed=1;continue;} /* still referenced or busy */
        deleted++;remaining--;
    }
    int protected_count=0,fresh_count=0,anomalous_count=0,failed_count=0;
    for(int i=0;i<count;i++) {
        protected_count+=candidates[i].protected_entry!=0;
        fresh_count+=candidates[i].fresh&&!candidates[i].protected_entry;
        anomalous_count+=candidates[i].anomalous;
        failed_count+=candidates[i].failed&&!candidates[i].anomalous;
    }
    /* One bounded control-event line; never a per-frame log. `after` above
     * the target with victims exhausted means protected/fresh/anomalous
     * directories are holding the total up: reported, not hidden. */
    fprintf(stderr,"TUNE_CACHE_EVICT target=%d before=%d after=%d deleted=%d protected=%d fresh=%d anomalous=%d failed=%d links=%d truncated=%d invalid_names=%d\n",
            NX_TUNE_CACHE_MAX,count,remaining,deleted,protected_count,fresh_count,anomalous_count,failed_count,links,truncated,invalid_names);
    free(candidates);
    return remaining;
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
