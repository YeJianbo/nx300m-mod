/* Finite RAM staging check and private control-job copy with byte readback. */
#define _GNU_SOURCE
#define _LARGEFILE64_SOURCE
#include <errno.h>
#include <dirent.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/vfs.h>
#include <unistd.h>

extern int __fxstat64(int, int, struct stat64 *);
static unsigned char source_bytes[32768], staged_bytes[32768];

static int regular(int fd) {
    struct stat64 st;
    return fd >= 0 && !__fxstat64(3, fd, &st) && S_ISREG(st.st_mode);
}
static ssize_t block(int fd, unsigned char *bytes) {
    size_t used = 0;
    while (used < sizeof source_bytes) {
        ssize_t n = read(fd, bytes + used, sizeof source_bytes - used);
        if (n < 0) { if (errno == EINTR) continue; return -1; }
        if (!n) break;
        used += (size_t)n;
    }
    return (ssize_t)used;
}
static int equal_fds(int a, int b) {
    for (;;) {
        ssize_t n=block(a,source_bytes),m=block(b,staged_bytes);
        if (n<0 || n!=m || (n && memcmp(source_bytes,staged_bytes,(size_t)n))) return 0;
        if (!n) return 1;
    }
}
static int same(int source, int staged, const char *name) {
    int a = openat(source, name, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
    int b = openat(staged, name, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
    int ok = regular(a) && regular(b) && equal_fds(a,b);
    if (a >= 0) close(a);
    if (b >= 0) close(b);
    if (!ok) fprintf(stderr, "RAM asset mismatch, missing or linked: %s\n", name);
    return ok;
}
static int relative(const char *name) {
    if (!*name || *name == '/') return 0;
    const char *part = name;
    for (const char *p = name;; p++) {
        if (!*p || *p == '/') {
            size_t n = (size_t)(p - part);
            if (!n || (n == 1 && part[0] == '.') || (n == 2 && part[0] == '.' && part[1] == '.')) return 0;
            if (!*p) return 1;
            part = p + 1;
        } else if (!((*p >= 'a' && *p <= 'z') || (*p >= 'A' && *p <= 'Z') ||
                     (*p >= '0' && *p <= '9') || *p == '_' || *p == '-' || *p == '.')) return 0;
    }
}
static int copy_file(int source,int staged,const char *from,const char *to,mode_t mode,
                     int size_checked,unsigned long long expected) {
    int a=openat(source,from,O_RDONLY|O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC),b=-1;
    int ok=regular(a),created=0;
    struct stat64 info;
    if (ok && size_checked) ok=!__fxstat64(3,a,&info) && info.st_size>=0 &&
        (unsigned long long)info.st_size==expected;
    if (ok) {
        b=openat(staged,to,O_RDWR|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600);
        created=b>=0;ok=regular(b);
    }
    while (ok) {
        ssize_t n=block(a,source_bytes);
        if (n<0) {ok=0;break;}
        if (!n) break;
        ssize_t used=0;
        while (used<n) {
            ssize_t wrote=write(b,source_bytes+used,(size_t)(n-used));
            if (wrote<0 && errno==EINTR) continue;
            if (wrote<=0) {ok=0;break;}
            used+=wrote;
        }
    }
    if (a>=0) close(a);
    /* Reopen the source path: a replacement/link between copy and readback
     * must receive the same rejection as the former cp + cmp sequence. */
    a=-1;
    if (ok) {
        a=openat(source,from,O_RDONLY|O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC);
        ok=regular(a) && lseek64(b,0,SEEK_SET)==0 && equal_fds(a,b);
    }
    if (ok && size_checked) ok=!__fxstat64(3,b,&info) && info.st_size>=0 &&
        (unsigned long long)info.st_size==expected;
    if (ok) ok=fchmod(b,mode)==0;
    if (a>=0) close(a);
    if (b>=0 && close(b)) ok=0;
    if (!ok) {
        /* O_EXCL owns only a newly created destination. Preserve any existing
         * regular file or link that caused creation to be refused. */
        if (created) unlinkat(staged,to,0);
        fprintf(stderr,"RAM control copy rejected: %s -> %s\n",from,to);
    }
    return ok;
}
static int copy_files(int argc,char **argv) {
    /* --copy SOURCE DEST (MODE SIZE|any SOURCE_NAME DEST_NAME)... */
    if (argc<8 || (argc-4)%4 || argv[2][0]!='/' || argv[3][0]!='/') return 64;
    int source=open(argv[2],O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
    int staged=open(argv[3],O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
    struct statfs fs;
    if (source<0 || staged<0 || fstatfs(staged,&fs) ||
        ((unsigned long)fs.f_type!=0x01021994UL && (unsigned long)fs.f_type!=0x858458f6UL)) return 1;
    int ok=1;
    for (int i=4;ok && i<argc;i+=4) {
        mode_t mode=0;
        if (!strcmp(argv[i],"400")) mode=0400;
        else if (!strcmp(argv[i],"500")) mode=0500;
        else if (!strcmp(argv[i],"700")) mode=0700;
        else {ok=0;break;}
        const char *size=argv[i+1];unsigned long long expected=0;
        int size_checked=strcmp(size,"any")!=0;
        if (size_checked) {
            if (!*size) {ok=0;break;}
            for (;*size;size++) {
                if (*size<'0' || *size>'9' || expected>(~0ULL-(unsigned)(*size-'0'))/10) {ok=0;break;}
                expected=expected*10+(unsigned)(*size-'0');
            }
        }
        if (ok) ok=relative(argv[i+2]) && relative(argv[i+3]) &&
            copy_file(source,staged,argv[i+2],argv[i+3],mode,size_checked,expected);
    }
    close(source);close(staged);
    return ok?0:1;
}
static int service_shell(const char *pid,const char *source) {
    char path[PATH_MAX],targets[3][PATH_MAX];
    const char *names[]={"mfw-services.sh","start.sh","service.sh"};
    for (unsigned i=0;i<3;i++)
        if (snprintf(targets[i],sizeof targets[i],"%s/%s",source,names[i])>=(int)sizeof targets[i]) return -1;
    snprintf(path,sizeof path,"/proc/%s/cmdline",pid);
    FILE *stream=fopen(path,"r");
    if (!stream) return 0; /* A process may have exited during the scan. */
    char *arg=NULL;size_t capacity=0;int found=0;
    while (getdelim(&arg,&capacity,0,stream)>=0) {
        for (unsigned i=0;i<3;i++) if (!strcmp(arg,targets[i])) found=1;
        if (found) break;
    }
    free(arg);fclose(stream);
    return found;
}
static int process_scan(int argc,char **argv) {
    if (argc!=3 || argv[2][0]!='/') return 64;
    DIR *proc=opendir("/proc");
    if (!proc) return 1;
    int ok=1;
    for (;;) {
        errno=0;
        struct dirent *entry=readdir(proc);
        if (!entry) {if (errno) ok=0;break;}
        const char *pid=entry->d_name,*p=pid;
        for (;*p>='0' && *p<='9';p++) {}
        if (!*pid || *p) continue;
        char path[PATH_MAX],exe[PATH_MAX];
        snprintf(path,sizeof path,"/proc/%s/exe",pid);
        ssize_t size=readlink(path,exe,sizeof exe-1);
        if (size<0 || size==(ssize_t)sizeof exe-1) continue;
        exe[size]=0;
        const char *base=strrchr(exe,'/');
        if (!base) continue;
        base++;
        /* Do not filter by comm: tasks may rename themselves. Keep the old
         * executable and NUL-separated argv matching; the shell still checks
         * boot/starttime/inode ownership for every candidate we report. */
        if (!strcmp(base,"nx-remote-controller.arm") || !strcmp(base,"nx-remote-controller.arm (deleted)"))
            printf("remote %s\n",pid);
        else if (!strcmp(base,"sh") || !strcmp(base,"bash") || !strcmp(base,"busybox")) {
            int old=service_shell(pid,argv[2]);
            if (old<0) {ok=0;break;}
            if (old) printf("shell %s\n",pid);
        }
    }
    closedir(proc);
    return ok && !fflush(stdout) && !ferror(stdout)?0:1;
}
int main(int argc, char **argv) {
    if (argc>1 && !strcmp(argv[1],"--process-scan")) return process_scan(argc,argv);
    if (argc>1 && !strcmp(argv[1],"--copy")) return copy_files(argc,argv);
    if (argc != 4 || argv[1][0] != '/' || argv[2][0] != '/' || argv[3][0] != '/') return 64;
    int source = open(argv[1], O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    int staged = open(argv[2], O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    int fd = open(argv[3], O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
    if (source < 0 || staged < 0 || !regular(fd)) return 1;
    FILE *list = fdopen(fd, "r");
    if (!list) return 1;
    char name[PATH_MAX]; unsigned count = 0; int ok = 1;
    while (fgets(name, sizeof name, list)) {
        size_t length = strlen(name);
        if (!length || name[length-1] != '\n') { ok = 0; break; }
        name[length-1] = 0;
        if (!relative(name) || !same(source, staged, name)) { ok = 0; break; }
        count++;
    }
    if (ferror(list) || !count) ok = 0;
    fclose(list);
    if (ok) ok = same(source, staged, "runtime-files.list");
    close(source); close(staged);
    return ok ? 0 : 1;
}
