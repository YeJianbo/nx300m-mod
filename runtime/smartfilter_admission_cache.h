/* Cache only successful file hashes. Live process/mapping/code/GOT admission
 * still runs on every operation, including after ptrace has stopped threads. */
#ifndef NX_SMARTFILTER_ADMISSION_CACHE_H
#define NX_SMARTFILTER_ADMISSION_CACHE_H
struct nx_sfr_file_proof {
    struct stat64 file;
    char sha[65];
};
struct nx_sfr_file_cache {
    unsigned magic, build, pid, fixture, count;
    unsigned long long start;
    char boot[40];
    struct nx_sfr_file_proof proof[STOCK_IMAGES+1];
};
static struct nx_sfr_file_cache admission_cache;
static char admission_cache_path[128];
static unsigned admission_cache_hits,admission_cache_misses;
static int admission_same_file(const struct stat64 *a,const struct stat64 *b) {
    return a->st_dev==b->st_dev&&a->st_ino==b->st_ino&&a->st_size==b->st_size&&
        a->st_mtim.tv_sec==b->st_mtim.tv_sec&&a->st_mtim.tv_nsec==b->st_mtim.tv_nsec&&
        a->st_ctim.tv_sec==b->st_ctim.tv_sec&&a->st_ctim.tv_nsec==b->st_ctim.tv_nsec;
}
static void admission_cache_begin(unsigned pid,unsigned long long start,int fixture) {
    memset(&admission_cache,0,sizeof admission_cache);
    admission_cache_path[0]=0; admission_cache_hits=admission_cache_misses=0;
    char boot[40]={0}; int fd=open("/proc/sys/kernel/random/boot_id",O_RDONLY|O_CLOEXEC);
    ssize_t length=fd>=0?read(fd,boot,sizeof boot-1):-1;
    if(fd>=0) close(fd);
    struct statfs fs;
    if(length!=37||boot[36]!='\n'||statfs("/var/run",&fs)||
       ((unsigned long)fs.f_type!=0x01021994UL&&(unsigned long)fs.f_type!=0x858458f6UL)) return;
    admission_cache.magic=0x48434653U; admission_cache.build=NX_SFR_BUILD;
    admission_cache.pid=pid; admission_cache.start=start; admission_cache.fixture=(unsigned)fixture;
    memcpy(admission_cache.boot,boot,sizeof boot);
    snprintf(admission_cache_path,sizeof admission_cache_path,
             "/var/run/nx300m-sf-files-%u-%u.cache",pid,NX_SFR_BUILD);
    fd=open(admission_cache_path,O_RDONLY|O_CLOEXEC|O_NOFOLLOW);
    struct stat64 st; struct nx_sfr_file_cache saved;
    int ok=fd>=0&&!__fxstat64(3,fd,&st)&&S_ISREG(st.st_mode)&&
        st.st_uid==geteuid()&&!(st.st_mode&077)&&st.st_size==sizeof saved&&
        read(fd,&saved,sizeof saved)==sizeof saved;
    if(fd>=0) close(fd);
    if(ok&&saved.magic==admission_cache.magic&&saved.build==NX_SFR_BUILD&&saved.pid==pid&&
       saved.start==start&&saved.fixture==(unsigned)fixture&&saved.count<=STOCK_IMAGES+1&&
       !memcmp(saved.boot,boot,sizeof boot)) admission_cache=saved;
}
static int admission_file_hash(int fd,unsigned size,const char *expected) {
    struct stat64 before,after;
    if(__fxstat64(3,fd,&before)||!S_ISREG(before.st_mode)||before.st_size!=size) return 0;
    for(unsigned i=0;i<admission_cache.count;++i) {
        const struct nx_sfr_file_proof *p=&admission_cache.proof[i];
        if(!memcmp(p->sha,expected,65)&&admission_same_file(&p->file,&before)) {
            ++admission_cache_hits; return 1;
        }
    }
    ++admission_cache_misses;
    if(!file_hash(fd,size,expected)||__fxstat64(3,fd,&after)||!admission_same_file(&before,&after)) return 0;
    if(admission_cache_path[0]) {
        unsigned i;
        for(i=0;i<admission_cache.count;++i)
            if(!memcmp(admission_cache.proof[i].sha,expected,65)) break;
        if(i<STOCK_IMAGES+1) {
            admission_cache.proof[i].file=after;
            memcpy(admission_cache.proof[i].sha,expected,65);
            if(i==admission_cache.count) ++admission_cache.count;
        }
    }
    return 1;
}
static void admission_cache_save(void) {
    if(!admission_cache_path[0]||!admission_cache_misses) return;
    char temporary[160]; snprintf(temporary,sizeof temporary,"%s.%u",admission_cache_path,(unsigned)getpid());
    int fd=open(temporary,O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC|O_NOFOLLOW,0600);
    if(fd<0) return;
    int ok=write(fd,&admission_cache,sizeof admission_cache)==sizeof admission_cache;
    if(close(fd)) ok=0;
    if(!ok||rename(temporary,admission_cache_path)) unlink(temporary);
}
#endif
