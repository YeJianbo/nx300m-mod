/* Independent Linux launcher for the UI-owned ABI4 probe collector.
 * Build separately from the loader/runtime. No camera or ptrace code here.
 * --launch JOB NONCE SOURCE DEV:INO STATE CAMERA_PID CAMERA_START
 * --ready JOB NONCE                 (caller must be the collector shell)
 * --run SECONDS STDOUT STDERR GUARD PROGRAM [ARG...]
 * --prepare SOURCE DEV:INO NONCE    (exclusive, empty probe target)
 */
#define _GNU_SOURCE
#include <sys/types.h>
#include <sys/stat.h>
#include <sys/statfs.h>
#include <sys/prctl.h>
#include <sys/wait.h>
#include <sys/syscall.h>
#include <fcntl.h>
#include <unistd.h>
#include <dirent.h>
#include <signal.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

/* Match the loader's old-glibc stat ABI when using modern ARM headers. */
#if defined(__arm__)
extern unsigned long long old_strtoull(const char *, char **, int) __asm__("strtoull");
#define strtoull old_strtoull
extern int old_sscanf(const char *, const char *, ...) __asm__("sscanf");
#define sscanf old_sscanf
extern int __fxstat64(int, int, struct stat64 *);
extern int __lxstat64(int, const char *, struct stat64 *);
#define stat stat64
#define fstat(fd, st) __fxstat64(3, (fd), (st))
#define lstat(path, st) __lxstat64(3, (path), (st))
#endif
#include "smartfilter_media.h"
static int launcher_media_stat(int fd,struct nx_sfm_meta *m) {
    struct stat s;
    if(fstat(fd,&s)) return -1;
    m->device=s.st_dev; m->inode=s.st_ino; m->uid=s.st_uid; m->nlink=s.st_nlink;
    m->size=s.st_size; m->mode=s.st_mode; return 0;
}
#ifndef NX_SF_FIXTURE_SOURCE
#define NX_SF_FIXTURE_SOURCE "/perf"
#endif
#define READY_MS 2500
#define ACK_MS 3500
static void die(const char *s) { fprintf(stderr, "sf-probe-launcher: %s (%s)\n", s, strerror(errno)); exit(1); }
static long long now_ms(void) {
    struct timespec t;
    /* glibc 2.13 keeps clock_gettime in librt; avoid a new dependency. */
    if (syscall(SYS_clock_gettime, CLOCK_MONOTONIC, &t)) die("clock");
    return (long long)t.tv_sec * 1000 + t.tv_nsec / 1000000;
}
static void tick(void) { struct timespec t = {0, 20000000}; while (nanosleep(&t, &t) && errno == EINTR) {} }
static int decimal(const char *s) {
    if (!*s) return 0;
    for (; *s; ++s) if (*s < '0' || *s > '9') return 0;
    return 1;
}
static int nonce_ok(const char *s) {
    size_t i;
    if (strlen(s) != 32) return 0;
    for (i=0; i<32; ++i) if (!((s[i]>='0' && s[i]<='9') || (s[i]>='a' && s[i]<='f'))) return 0;
    return 1;
}
static unsigned long long starttime(pid_t pid) {
    char path[80], text[4096], *p, *save, *field;
    FILE *f; int n;
    snprintf(path, sizeof path, "/proc/%ld/stat", (long)pid);
    f=fopen(path,"r"); if (!f) return 0;
    if (!fgets(text,sizeof text,f)) { fclose(f); return 0; }
    fclose(f); p=strrchr(text,')'); if (!p || p[1]!=' ') return 0;
    p+=2; save=NULL;
    for (n=3, field=strtok_r(p," ", &save); field; ++n, field=strtok_r(NULL," ", &save)) {
        if (n==3 && (*field=='Z' || *field=='X')) return 0;
        if (n==22) return strtoull(field,NULL,10);
    }
    return 0;
}
static void detach_signals(void) {
    sigset_t empty;
    /* Linux clears PDEATHSIG at fork; explicitly clear before every exec too. */
    if (prctl(PR_SET_PDEATHSIG,0,0,0,0)) die("clear PDEATHSIG");
    signal(SIGHUP,SIG_IGN); signal(SIGINT,SIG_DFL); signal(SIGTERM,SIG_DFL);
    signal(SIGCHLD,SIG_DFL); signal(SIGPIPE,SIG_DFL);
    sigemptyset(&empty); if (sigprocmask(SIG_SETMASK,&empty,NULL)) die("signal mask");
    unsetenv("LD_PRELOAD"); unsetenv("LD_AUDIT"); unsetenv("LD_LIBRARY_PATH");
    unsetenv("ENV"); unsetenv("BASH_ENV"); unsetenv("NX_FN_KEY_LOG");
}
static void close_fds(int keep_locks) {
    DIR *d=opendir("/proc/self/fd"); struct dirent *e; int own;
    if (!d) die("enumerate descriptors");
    own=dirfd(d);
    while ((e=readdir(d))) if (decimal(e->d_name)) {
        int fd=atoi(e->d_name);
        if (fd>2 && fd!=own && !(keep_locks && (fd==6 || fd==9))) close(fd);
    }
    closedir(d);
}
static int open_regular(const char *p, int flags, mode_t mode) {
    struct stat s; int fd=open(p,flags|O_NOFOLLOW|O_CLOEXEC,mode);
    if (fd<0) die("open output");
    if (fstat(fd,&s) || !S_ISREG(s.st_mode) || s.st_nlink!=1 || s.st_uid!=geteuid()) die("unsafe output");
    return fd;
}
static void redirect(const char *out, const char *err, int keep_locks) {
    int a,b,c;
    close_fds(keep_locks);
    a=open("/dev/null",O_RDONLY); if (a<0 || dup2(a,0)<0) die("stdin"); if (a>2) close(a);
    b=open_regular(out,O_WRONLY|O_CREAT|O_APPEND,0600);
    if (dup2(b,1)<0) die("stdout");
    if (b>2) close(b);
    c=open_regular(err,O_WRONLY|O_CREAT|O_APPEND,0600);
    if (dup2(c,2)<0) die("stderr");
    if (c>2) close(c);
}
static void job_ok(const char *p) {
    struct stat s; struct statfs fs;
    if (strncmp(p,"/tmp/nx300m-sf-job.",19) || strchr(p+19,'/') || !p[19] ||
        lstat(p,&s) || !S_ISDIR(s.st_mode) || s.st_uid!=geteuid() || (s.st_mode&077) ||
        statfs(p,&fs) || ((unsigned long)fs.f_type!=0x01021994UL && (unsigned long)fs.f_type!=0x858458f6UL)) {
        errno=EINVAL; die("private job must be root-owned RAM directory");
    }
}
static void publish(const char *path, const char *data) {
    char temp[1024]; int fd; size_t n=strlen(data);
    if (snprintf(temp,sizeof temp,"%s.%ld.tmp",path,(long)getpid()) >= (int)sizeof temp) die("path length");
    fd=open_regular(temp,O_WRONLY|O_CREAT|O_EXCL,0600);
    if (write(fd,data,n)!=(ssize_t)n || close(fd)) die("write handshake");
    if (rename(temp,path)) die("publish handshake");
}
static int read_line(const char *path, char *text, size_t size) {
    struct stat s; int fd=open(path,O_RDONLY|O_NOFOLLOW|O_CLOEXEC); ssize_t n;
    if (fd<0) return 0;
    if (fstat(fd,&s) || !S_ISREG(s.st_mode) || s.st_uid!=geteuid() || s.st_size<1 || s.st_size>=(off_t)size) { close(fd); return 0; }
    n=read(fd,text,size-1); close(fd); if (n<=0) return 0;
    text[n]=0; return 1;
}
static int ready(const char *job,const char *nonce) {
    char path[1024], text[256], seen[256]; pid_t shell=getppid();
    unsigned long long start=starttime(shell); long long until;
    job_ok(job); if (!nonce_ok(nonce) || !start) { errno=EINVAL; die("ready identity"); }
    snprintf(path,sizeof path,"%s/ready",job);
    snprintf(text,sizeof text,"%s %ld %llu\n",nonce,(long)shell,start);
    publish(path,text);
    snprintf(path,sizeof path,"%s/ack",job); until=now_ms()+ACK_MS;
    while (now_ms()<until) {
        if (read_line(path,seen,sizeof seen) && !strcmp(seen,text) && starttime(shell)==start) return 0;
        tick();
    }
    errno=ETIMEDOUT; die("UI acknowledgment absent; no operation started"); return 1;
}
static int launch(int argc,char **argv) {
    const char *job=argv[2], *nonce=argv[3];
    char path[1024], log[1024], text[256], got_nonce[80], tail;
    long got_pid=0; unsigned long long got_start=0; pid_t child, sid=getsid(0), pg=getpgrp(); long long until;
    (void)argc;
    job_ok(job); if (!nonce_ok(nonce)) { errno=EINVAL; die("nonce"); }
    child=fork(); if (child<0) die("fork");
    if (!child) {
        pid_t worker;
        detach_signals(); if (setsid()<0) die("setsid");
        worker=fork(); if (worker<0) die("second fork"); if (worker) _exit(0);
        detach_signals();
        snprintf(log,sizeof log,"%s/collector.log",job); redirect(log,log,0);
        if (chdir(job)) die("chdir job");
        snprintf(text,sizeof text,"%ld",(long)getpid());
        if (setenv("NX_SF_COLLECTOR_PID",text,1) || setenv("NX_STATE_DIR",argv[6],1)) die("collector environment");
        snprintf(path,sizeof path,"%s/collector.sh",job);
        execl("/bin/sh","sh",path,nonce,argv[4],argv[5],argv[6],argv[7],argv[8],(char *)NULL);
        die("exec collector");
    }
    /* Only the first, already-exited fork is reaped, never the real job. */
    while (waitpid(child,NULL,0)<0 && errno==EINTR) {}
    snprintf(path,sizeof path,"%s/ready",job); until=now_ms()+READY_MS;
    while (now_ms()<until) {
        if (read_line(path,text,sizeof text) &&
            sscanf(text,"%79s %ld %llu %c",got_nonce,&got_pid,&got_start,&tail)==3 &&
            !strcmp(nonce,got_nonce) && got_pid>1 && got_start &&
            starttime((pid_t)got_pid)==got_start && getsid((pid_t)got_pid)>0 &&
            getsid((pid_t)got_pid)!=sid && getpgid((pid_t)got_pid)!=pg) {
            snprintf(path,sizeof path,"%s/ack",job); publish(path,text);
            printf("accepted nonce=%s pid=%ld start=%llu ram=%s\n",nonce,got_pid,got_start,job);
            return 0;
        }
        tick();
    }
    errno=ETIMEDOUT; die("READY deadline; RAM retained, no retry"); return 1;
}
static int lock_matches(int fd,const char *path) {
    struct stat a,b;
    return !fstat(fd,&a) && !lstat(path,&b) && S_ISREG(a.st_mode) && S_ISREG(b.st_mode) &&
        a.st_uid==geteuid() && b.st_uid==geteuid() && a.st_dev==b.st_dev && a.st_ino==b.st_ino;
}
static int collector_locks(void) {
    const char *owner=getenv("NX_SF_COLLECTOR_PID"), *state=getenv("NX_STATE_DIR");
    char cwd[1024],path[1200],text[256],nonce[80],tail; long pid; unsigned long long start;
    if (!owner || !decimal(owner) || atol(owner)!=(long)getppid() || !state || !getcwd(cwd,sizeof cwd)) return 0;
    snprintf(path,sizeof path,"%s/ready",cwd);
    if (!read_line(path,text,sizeof text) || sscanf(text,"%79s %ld %llu %c",nonce,&pid,&start,&tail)!=3 ||
        pid!=(long)getppid() || starttime((pid_t)pid)!=start) return 0;
    snprintf(path,sizeof path,"%s/media-io.lock",state);
    if (!lock_matches(6,path) || !lock_matches(9,"/tmp/nx300m-smartfilter-preview.lock")) { errno=EPERM; die("collector lock identity"); }
    return 1;
}
static int run(int argc,char **argv) {
    unsigned sec; pid_t child; long long until; int status,keep_locks=collector_locks();
    if (!decimal(argv[2]) || strlen(argv[2])>2 || (sec=(unsigned)atoi(argv[2]))<1) { errno=EINVAL; die("invalid deadline"); }
    if(sec>30) {
        char cwd[1024],loader[1200];
        /* Only the acknowledged read-only observer waits for a real frame.
         * Mutating controls keep their existing thirty-second outer limit. */
        if(sec!=90||!keep_locks||argc<8||strcmp(argv[7],"kdd-probe")||!getcwd(cwd,sizeof cwd)||
           snprintf(loader,sizeof loader,"%s/loader.arm",cwd)>=(int)sizeof loader||strcmp(argv[6],loader)) {
            errno=EINVAL; die("extended deadline requires the job-owned KDD observer");
        }
    }
    child=fork(); if (child<0) die("run fork");
    if (!child) {
        detach_signals(); redirect(argv[3],argv[4],keep_locks);
        execv(argv[6],argv+6); die("run exec");
    }
    until=now_ms()+(long long)sec*1000;
    while (now_ms()<until) {
        pid_t p=waitpid(child,&status,WNOHANG);
        if (p==child) return WIFEXITED(status) ? WEXITSTATUS(status) : 128+WTERMSIG(status);
        if (p<0 && errno!=EINTR) die("run wait");
        tick();
    }
    {
        char text[256]; int fd;
        snprintf(text,sizeof text,"timeout child=%ld start=%llu; no process killed; reboot before another preview operation\n",(long)child,starttime(child));
        fd=open(argv[5],O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600);
        if (fd>=0) { ssize_t ignored=write(fd,text,strlen(text)); (void)ignored; close(fd); }
        else if (errno!=EEXIST) fprintf(stderr,"sf-probe-launcher: cannot publish uncertainty guard\n");
        fputs(text,stderr);
    }
    /* No signal to loader/restore tracer. A timed-out collector writer retains
     * its verified job-owned media/preview leases until it really exits. It is
     * impossible to bound blocked I/O and safely drop its lease simultaneously. */
    return 124;
}
static void prepare_quote(FILE *out,const char *text) {
    unsigned i; fputc('"',out);
    for(i=0;i<191&&text[i];++i) {
        unsigned char v=(unsigned char)text[i];
        if(v=='"'||v=='\\') { fputc('\\',out); fputc(v,out); }
        else if(v<32||v>=127) fprintf(out,"\\u%04x",(unsigned)v);
        else fputc(v,out);
    }
    fputc('"',out);
}
static int prepare(const char *source,const char *id,const char *nonce) {
    struct nx_sfm_context media; struct nx_sfm_identity expected;
    char actual[100],name[100],tail; int fd=-1,changed=0;
    nx_sfm_init(&media,launcher_media_stat);
    if(!nonce_ok(nonce)) { nx_sfm_logic(&media,"destination","invalid_nonce"); goto failed; }
    if(sscanf(id,"%llu:%llu%c",&expected.device,&expected.inode,&tail)!=2) {
        nx_sfm_logic(&media,"media_identity","invalid_source_identity"); goto failed;
    }
    snprintf(actual,sizeof actual,"%llu:%llu",expected.device,expected.inode);
    if(strcmp(id,actual)) { nx_sfm_logic(&media,"media_identity","invalid_source_identity"); goto failed; }
    if(!nx_sfm_open(&media,source,NX_SF_FIXTURE_SOURCE,1,&expected,launcher_media_stat)) goto failed;
    snprintf(name,sizeof name,"sf-probe-%s.ndjson",nonce);
    fd=openat(media.logs,name,O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600);
    if(fd<0) { nx_sfm_system(&media,"destination","openat"); goto failed; }
    changed=1;
    if(!nx_sfm_target(&media,fd)) goto failed;
    if(media.t.meta.nlink!=1) { nx_sfm_logic(&media,"destination","link_count_mismatch"); goto failed; }
    if(media.t.meta.size) { nx_sfm_logic(&media,"destination","destination_not_empty"); goto failed; }
    if(!media.fat_exception&&(media.t.meta.mode&077)) { nx_sfm_logic(&media,"destination","unsafe_permissions"); goto failed; }
    printf("%s%s/%s %llu:%llu\n",source,media.production?"/logs":"",name,media.t.meta.device,media.t.meta.inode);
    close(fd); nx_sfm_close(&media); return 0;
failed:
    fputs("{\"command\":\"probe-prepare\",\"status\":\"prepare_failed\",\"snapshot_created\":false,\"persisted\":false,\"error_stage\":",stdout);
    prepare_quote(stdout,media.stage); fputs(",\"error_operation\":",stdout); prepare_quote(stdout,media.operation);
    fputs(",\"error_reason\":",stdout); prepare_quote(stdout,media.reason);
    fprintf(stdout,",\"error_errno\":%d,\"error_errno_valid\":%s,\"error_errno_text\":",media.error_errno,media.error_errno_valid?"true":"false");
    prepare_quote(stdout,media.error_errno_valid?strerror(media.error_errno):"not a system call error");
    fprintf(stdout,",\"write_attempted\":false,\"destination_may_have_changed\":%s,\"destination_bytes_verified\":false",changed?"true":"false");
    nx_sfm_fields(stdout,&media); fputs("}\n",stdout);
    fprintf(stderr,"sf-probe-launcher: stage=%s op=%s reason=%s errno=%d valid=%s text=",media.stage,media.operation,media.reason,media.error_errno,media.error_errno_valid?"true":"false");
    prepare_quote(stderr,media.error_errno_valid?strerror(media.error_errno):"not a system call error"); fputc('\n',stderr);
    if(fd>=0) close(fd);
    nx_sfm_close(&media); return 1;
}
int main(int argc,char **argv) {
    umask(077);
    if (argc==9 && !strcmp(argv[1],"--launch")) return launch(argc,argv);
    if (argc==4 && !strcmp(argv[1],"--ready")) return ready(argv[2],argv[3]);
    if (argc>=7 && !strcmp(argv[1],"--run")) return run(argc,argv);
    if (argc==5 && !strcmp(argv[1],"--prepare")) return prepare(argv[2],argv[3],argv[4]);
    errno=EINVAL; die("invalid launcher arguments"); return 64;
}
