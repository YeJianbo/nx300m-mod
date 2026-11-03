/* Short, passive NX300M IPCS observer. All opened paths are procfs, O_RDONLY.
 * No target calls, device access, ptrace, signals, hooks or process suspension.
 * Snapshots are deliberately NOT labelled as exact per-Open measurements. */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <errno.h>
#include <fcntl.h>
#include <time.h>
#include <unistd.h>
#include <sys/stat.h>
#include "ipcs_observe_stock.h"

/* Use the same glibc-2.13 entry points as the existing RAM loaders; the
 * isolated GCC 14 headers otherwise redirect these to glibc C23 symbols. */
extern unsigned long long old_strtoull(const char *,char **,int) __asm__("strtoull");
extern unsigned long old_strtoul(const char *,char **,int) __asm__("strtoul");
extern int old_sscanf(const char *,const char *,...) __asm__("sscanf");
extern int __xstat64(int,const char *,struct stat64 *);
#define strtoull old_strtoull
#define strtoul old_strtoul
#define sscanf old_sscanf

static uint64_t stamp(clockid_t clock) {
    struct timespec t;
    if (clock_gettime(clock, &t)) return 0;
    return (uint64_t)t.tv_sec*1000000000ULL+(unsigned)t.tv_nsec;
}
static int read_text(const char *path, char *buf, size_t n) {
    int fd=open(path,O_RDONLY|O_CLOEXEC);
    if(fd<0) { buf[0]=0; return -1; }
    ssize_t got=read(fd,buf,n-1);
    int error=errno;
    close(fd);
    if(got<0) { buf[0]=0; errno=error; return -1; }
    buf[got]=0;
    while(got && (buf[got-1]=='\n'||buf[got-1]=='\r')) buf[--got]=0;
    return (int)got;
}
static void quoted(const char *s) {
    putchar('"');
    for(const unsigned char *p=(const unsigned char *)s;*p;++p) {
        if(*p=='"'||*p=='\\') { putchar('\\'); putchar(*p); }
        else if(*p<32) printf("\\u%04x",*p);
        else putchar(*p);
    }
    putchar('"');
}
static int error(const char *why) {
    printf("{\"type\":\"error\",\"errno\":%d,\"message\":",errno);
    quoted(why); puts("}"); return 2;
}
struct task {
    char state;
    uint64_t user,system,start,runtime,runqueue,switches;
    int sched_ok;
};
static int task_read(unsigned pid,unsigned tid,struct task *t) {
    char path[96],b[4096];
    memset(t,0,sizeof *t);
    snprintf(path,sizeof path,"/proc/%u/task/%u/stat",pid,tid);
    if(read_text(path,b,sizeof b)<0) return -1;
    char *p=strrchr(b,')');
    if(!p || strlen(p)<4) { errno=EINVAL; return -1; }
    p+=2; t->state=*p; p+=2;
    for(unsigned field=4;field<=22;++field) {
        char *end;
        unsigned long long v=strtoull(p,&end,10);
        if(end==p) { errno=EINVAL; return -1; }
        if(field==14) t->user=v;
        if(field==15) t->system=v;
        if(field==22) t->start=v;
        p=end;
        while(*p==' ') ++p;
    }
    snprintf(path,sizeof path,"/proc/%u/task/%u/schedstat",pid,tid);
    if(read_text(path,b,sizeof b)>=0) {
        unsigned long long a,c,d;
        if(sscanf(b,"%llu %llu %llu",&a,&c,&d)==3) {
            t->runtime=a; t->runqueue=c; t->switches=d; t->sched_ok=1;
        }
    }
    return 0;
}
static uintptr_t udd_base(unsigned pid) {
    char path[80],line[1024];
    snprintf(path,sizeof path,"/proc/%u/maps",pid);
    FILE *f=fopen(path,"r");
    if(!f) return 0;
    uintptr_t result=0;
    while(fgets(line,sizeof line,f)) {
        unsigned long a,z,off;
        char flags[8];
        if(sscanf(line,"%lx-%lx %7s %lx",&a,&z,flags,&off)==4 && off==0 &&
           strstr(line,"/libudd.so.3.1") && flags[0]=='r') {
            if(result && result!=(uintptr_t)a) { fclose(f); errno=EINVAL; return 0; }
            result=(uintptr_t)a;
        }
    }
    fclose(f);
    return result;
}
static int memory_read(int fd,uintptr_t base,unsigned offset,void *buf,size_t n) {
    return pread(fd,buf,n,(off_t)base+offset)==(ssize_t)n?0:-1;
}
static int verify(int fd,uintptr_t base) {
    unsigned char bytes[32];
    if(memory_read(fd,base,NX_VERIFY_OPEN_ADDR,bytes,sizeof bytes) ||
       memcmp(bytes,nx_verify_open,sizeof bytes)) return -1;
    if(memory_read(fd,base,NX_VERIFY_COUNT_ADDR,bytes,sizeof bytes) ||
       memcmp(bytes,nx_verify_count,sizeof bytes)) return -1;
    if(memory_read(fd,base,NX_VERIFY_CLOSE_ADDR,bytes,sizeof bytes) ||
       memcmp(bytes,nx_verify_close,sizeof bytes)) return -1;
    return 0;
}
static void capability(const char *label,const char *path) {
    struct stat64 st;
    printf(",\"%s\":%s",label,__xstat64(3,path,&st)==0?"true":"false");
}
static int positive(const char *s,unsigned *v) {
    char *end;
    unsigned long n=strtoul(s,&end,10);
    if(!*s || *end || !n || n>0x7fffffffUL) return -1;
    *v=(unsigned)n; return 0;
}
static const char *syscall_class(const char *s,uintptr_t base,int fd) {
    int nr;
    unsigned long a,b;
    if(sscanf(s,"%d %lx %lx",&nr,&a,&b)!=3)
        return !strcmp(s,"running")?"running":"unavailable";
    /* ARM EABI syscall numbers in the stock libc. These are observations of
     * one instant, with no Open entry/exit identity or inferred duration. */
    if(nr==5 && a==base+NX_IPCS_PATH) return "ipcs_device_open";
    if(nr==240 && a==base+NX_IPCS_MUTEX) return "ipcs_outer_mutex_futex";
    if(nr==240 && a==base+NX_UDD_MUTEX) return "ipcs_udd_mutex_futex";
    if(nr==54 && fd>=0 && a==(unsigned)fd) return "ipcs_fd_ioctl";
    if(nr==6 && fd>=0 && a==(unsigned)fd) return "ipcs_fd_close";
    if(nr==240) return "other_futex";
    return "other_syscall";
}
int main(int argc,char **argv) {
    unsigned pid=0,tid=0,seconds=3,interval=10;
    if(argc==2 && !strcmp(argv[1],"--help")) {
        puts("ipcs_observe --pid PID --tid PREVIEW_TID [--seconds 1..5] [--interval-ms 5..100]");
        puts("Outputs passive procfs/UDD snapshots to stdout. Does not enable LUT preview or call IPCS.");
        return 0;
    }
    for(int i=1;i<argc;i+=2) {
        unsigned v;
        if(i+1>=argc || positive(argv[i+1],&v)) { errno=EINVAL; return error("invalid numeric option"); }
        if(!strcmp(argv[i],"--pid")) pid=v;
        else if(!strcmp(argv[i],"--tid")) tid=v;
        else if(!strcmp(argv[i],"--seconds")) seconds=v;
        else if(!strcmp(argv[i],"--interval-ms")) interval=v;
        else { errno=EINVAL; return error("unknown option"); }
    }
    if(!pid||!tid||seconds>5||interval<5||interval>100) {
        errno=EINVAL; return error("pid/tid required; seconds 1..5, interval-ms 5..100");
    }
    char path[96],exe[256],text[4096];
    snprintf(path,sizeof path,"/proc/%u/exe",pid);
    ssize_t n=readlink(path,exe,sizeof exe-1);
    if(n<0) return error("cannot identify target executable");
    exe[n]=0;
    const char *basename=strrchr(exe,'/');
    if(!basename || strcmp(basename+1,"di-camera-app-nx300")) {
        errno=EINVAL; return error("target is not the original camera executable");
    }
    struct task first,current;
    if(task_read(pid,tid,&first)) return error("cannot read selected camera thread");
    uintptr_t base=udd_base(pid);
    if(!base) return error("UDD mapping unavailable or ambiguous");
    snprintf(path,sizeof path,"/proc/%u/mem",pid);
    int mem=open(path,O_RDONLY|O_CLOEXEC);
    if(mem<0) return error("read-only process memory access unavailable; no ptrace fallback");
    if(verify(mem,base)) { close(mem); errno=EINVAL; return error("UDD code fingerprints differ"); }
    /* Buffer stdout until the short observation ends; avoid SD/network logging
     * in the sampling loop. Redirection is the caller's choice. */
    char *output=malloc(1024*1024);
    if(!output || setvbuf(stdout,output,_IOFBF,1024*1024)) {
        close(mem); errno=ENOMEM; return error("cannot buffer observation output");
    }
    long hz=sysconf(_SC_CLK_TCK);
    if(hz<=0) { close(mem); errno=EINVAL; return error("invalid procfs clock tick rate"); }
    printf("{\"type\":\"capabilities\",\"pid\":%u,\"tid\":%u,\"start_ticks\":%llu,"
           "\"clock_ticks_per_second\":%ld,\"udd_base\":%lu,\"seconds\":%u,\"interval_ms\":%u,"
           "\"kernel_count_read\":false,\"exact_open_timing\":false,\"lcd_fps_measured\":false",
           pid,tid,(unsigned long long)first.start,hz,(unsigned long)base,seconds,interval);
    capability("kallsyms_present","/proc/kallsyms");
    capability("tracefs_present","/sys/kernel/debug/tracing");
    capability("proc_config_present","/proc/config.gz");
    if(read_text("/proc/version",text,sizeof text)>=0) { printf(",\"kernel\":"); quoted(text); }
    if(read_text("/proc/sys/kernel/random/boot_id",text,sizeof text)>=0) { printf(",\"boot_id\":"); quoted(text); }
    printf("}\n");
    uint64_t begin=stamp(CLOCK_MONOTONIC), observer_begin=stamp(CLOCK_PROCESS_CPUTIME_ID);
    if(!begin) { close(mem); return error("monotonic clock unavailable"); }
    unsigned samples=0; int failed=0;
    uint64_t last=begin;
    for(unsigned i=0;i<(seconds*1000U+interval-1)/interval;++i) {
        uint64_t at=stamp(CLOCK_MONOTONIC);
        if(!at || at-begin>seconds*1000000000ULL) break;
        if(task_read(pid,tid,&current) || current.start!=first.start) { failed=1; break; }
        int32_t count,fd,outer[6],inner[6];
        if(memory_read(mem,base,NX_UDD_COUNT,&count,sizeof count) ||
           memory_read(mem,base,NX_IPCS_FD,&fd,sizeof fd) ||
           memory_read(mem,base,NX_IPCS_MUTEX,outer,sizeof outer) ||
           memory_read(mem,base,NX_UDD_MUTEX,inner,sizeof inner)) { failed=1; break; }
        printf("{\"type\":\"sample\",\"index\":%u,\"begin_ns\":%llu,\"elapsed_ns\":%llu,"
               "\"task_state\":\"%c\",\"user_ticks\":%llu,\"system_ticks\":%llu,"
               "\"udd_count\":%d,\"ipcs_fd_number\":%d,\"outer_mutex_raw\":[",
               i,(unsigned long long)at,(unsigned long long)(at-begin),current.state,
               (unsigned long long)current.user,(unsigned long long)current.system,count,fd);
        for(unsigned j=0;j<6;++j) printf("%s%d",j?",":"",outer[j]);
        printf("],\"inner_mutex_raw\":[");
        for(unsigned j=0;j<6;++j) printf("%s%d",j?",":"",inner[j]);
        printf("],\"schedstat_available\":%s",current.sched_ok?"true":"false");
        if(current.sched_ok) printf(",\"sched_runtime_ns\":%llu,\"sched_runqueue_ns\":%llu,\"sched_switches\":%llu",
            (unsigned long long)current.runtime,(unsigned long long)current.runqueue,(unsigned long long)current.switches);
        snprintf(path,sizeof path,"/proc/%u/task/%u/wchan",pid,tid);
        if(read_text(path,text,sizeof text)>=0) { printf(",\"wchan\":"); quoted(text); }
        snprintf(path,sizeof path,"/proc/%u/task/%u/syscall",pid,tid);
        if(read_text(path,text,sizeof text)>=0) {
            printf(",\"syscall\":"); quoted(text);
            printf(",\"syscall_class\":"); quoted(syscall_class(text,base,fd));
        }
        last=stamp(CLOCK_MONOTONIC);
        printf(",\"end_ns\":%llu}\n",(unsigned long long)last);
        ++samples;
        uint64_t next=begin+(uint64_t)(i+1)*interval*1000000ULL;
        if(next>last) {
            uint64_t remain=next-last;
            struct timespec pause={(time_t)(remain/1000000000ULL),(long)(remain%1000000000ULL)};
            if(nanosleep(&pause,NULL)) { failed=1; break; }
        }
    }
    int verified=udd_base(pid)==base && verify(mem,base)==0;
    close(mem);
    printf("{\"type\":\"end\",\"samples\":%u,\"window_ns\":%llu,\"observer_cpu_ns\":%llu,"
           "\"same_udd_code_and_base\":%s,\"interrupted\":%s}\n",samples,
           (unsigned long long)(last-begin),
           (unsigned long long)(stamp(CLOCK_PROCESS_CPUTIME_ID)-observer_begin),
           verified?"true":"false",failed?"true":"false");
    fflush(stdout);
    return failed||!verified?2:0;
}
