/* Execute stock UDD refcount/thread bookkeeping. Device/reg/IRQ are doubles. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <pthread.h>
#include <semaphore.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>
#include "ipcs_calltrace.h"
#include "smartfilter_ram.h"
#include "smartfilter_stock.h"
#include "ipcs_trace_fixture_stock.h"
#define CHECK(v) do { if(!(v)) { fprintf(stderr,"calltrace check failed line %u: %s\n",__LINE__,#v); exit(1); } } while(0)
static struct nx_ipct trace;
static struct nx_ipct_api api;
static int (*original_open)(void),(*original_close)(void);
static int (*original_udd)(const char *,int,unsigned,int *);
static int (*original_mutex)(void *);
static int (*libc_open)(const char *,int,...);
static unsigned *udd_count;
static void *outer_mutex;
static unsigned physical_opens;
static unsigned native_udd,native_mutex,*udd_slot,*mutex_slot;
static sem_t ready,finish_owner;
static unsigned tid(void) { return (unsigned)syscall(SYS_gettid); }
static int clock_time(unsigned which,struct nx_ipct_time *p) { return (int)syscall(SYS_clock_gettime,which,p); }
static int mutex(void *p) {
    unsigned phase=p==outer_mutex?NX_IPCT_MUTEX_OUTER:NX_IPCT_MUTEX_UDD;
    struct nx_ipct_mark m=nx_ipct_enter(&trace,&api,phase,(unsigned)p);
    int rc=original_mutex(p); nx_ipct_leave(&api,m,rc); return rc;
}
static int udd(const char *p,int flags,unsigned index,int *fd) {
    CHECK(index==6); struct nx_ipct_mark m=nx_ipct_enter(&trace,&api,NX_IPCT_UDD,index);
    int rc=original_udd(p,flags,index,fd); nx_ipct_leave(&api,m,rc); return rc;
}
static int device_open(const char *p,int flags,unsigned mode) {
    CHECK(!strcmp(p,"/dev/d4_ipcs")&&flags==2); ++physical_opens;
    struct nx_ipct_mark m=nx_ipct_enter(&trace,&api,NX_IPCT_DEVICE_OPEN,(unsigned)flags);
    int rc=libc_open("/dev/null",flags,mode); nx_ipct_leave(&api,m,rc); return rc;
}
static int regmap(int fd) {
    struct nx_ipct_mark m=nx_ipct_enter(&trace,&api,NX_IPCT_REGMAP,(unsigned)fd);
    CHECK(fd>=0); nx_ipct_leave(&api,m,0); return 0;
}
static int irq(void) {
    struct nx_ipct_mark m=nx_ipct_enter(&trace,&api,NX_IPCT_IRQ,0); nx_ipct_leave(&api,m,0); return 0;
}
static int init(void) {
    struct nx_ipct_mark m=nx_ipct_enter(&trace,&api,NX_IPCT_INIT,0); nx_ipct_leave(&api,m,0); return 0;
}
static int no_hardware(void) { return 0; }
static void open_call(unsigned role) {
    nx_ipct_start(&trace,&api,role,trace.next,*udd_count);
    int rc=original_open(); nx_ipct_finish(&trace,&api,rc,*udd_count); CHECK(!rc);
}
static void *owner(void *unused) {
    (void)unused; open_call(NX_IPCT_SESSION); CHECK(!sem_post(&ready));
    CHECK(!sem_wait(&finish_owner)); CHECK(!original_close()); return NULL;
}
static void export(void) {
    puts("{\"type\":\"ipcs_open_window\",\"measurement_source\":\"stock_udd_fixture\",\"control_intervals_complete\":true,\"controls\":[]}");
    for(unsigned i=0;i<trace.next;++i) {
        const struct nx_ipct_record *r=&trace.records[i];
        printf("{\"type\":\"ipcs_open_call\",\"pid\":%u,\"build\":%u,\"committed\":%u,\"seq\":%u,\"tid\":%u,\"role\":%u,\"frame\":%u,"
               "\"start_sec\":%u,\"start_nsec\":%u,\"end_sec\":%u,\"end_nsec\":%u,\"kdd_transition\":null,\"phases\":[",
               (unsigned)getpid(),NX_SFR_BUILD,r->committed,r->sequence,r->tid,r->role,r->frame,
               r->start.wall.sec,r->start.wall.nsec,r->end.sec,r->end.nsec);
        for(unsigned j=0;j<NX_IPCT_PHASES;++j) {
            const struct nx_ipct_phase_data *p=&r->phase[j];
            printf("%s{\"phase\":%u,\"count\":%u,\"valid\":%u,\"wall_us\":%u,\"cpu_us\":%u,\"rc\":%d}",
                   j?",":"",j,p->count,p->valid,p->wall_us,p->cpu_us,p->rc);
        }
        puts("]}");
    }
}
static unsigned long long ns(unsigned which) {
    struct timespec t; CHECK(!syscall(SYS_clock_gettime,which,&t));
    return (unsigned long long)t.tv_sec*1000000000ULL+t.tv_nsec;
}
static void costs(void) {
    pthread_t thread; CHECK(!pthread_create(&thread,NULL,owner,NULL));
    CHECK(!sem_wait(&ready)&&*udd_count==1);
    /* The same real UDD bookkeeping is used in all cases. Only the device,
     * IRQ and MMIO boundary is substituted. Timed Open/Close calls remain
     * paired and on the rendering thread; the owner keeps its own reference. */
    const char *names[]={"native-existing","wrapped-off","wrapped-on"};
    for(unsigned mode=0;mode<3;++mode) {
        *udd_slot=mode?(unsigned)udd:native_udd;
        *mutex_slot=mode?(unsigned)mutex:native_mutex;
        for(unsigned sample=0;sample<31;++sample) {
            memset(&trace,0,sizeof trace); trace.enabled=mode==2;
            unsigned long long w=ns(1),c=ns(3);
            for(unsigned i=0;i<64;++i) {
                if(mode) open_call(NX_IPCT_PREVIEW); else CHECK(!original_open());
                CHECK(!original_close());
            }
            c=ns(3)-c; w=ns(1)-w;
            CHECK(*udd_count==1&&trace.dropped==0);
            if(mode==2) CHECK(trace.next==64);
            printf("{\"type\":\"udd-cost\",\"mode\":\"%s\",\"sample\":%u,\"calls\":64,\"wall_ns\":%llu,\"cpu_ns\":%llu}\n",
                   names[mode],sample,w,c);
        }
    }
    trace.enabled=0;
    CHECK(!sem_post(&finish_owner)&&!pthread_join(thread,NULL)&&*udd_count==0);
    CHECK(physical_opens==1);
    puts("{\"check\":\"real_udd_existing_reference_cost\",\"calls\":5952,\"device_opens\":1,\"remaining_references\":0,\"passed\":true}");
}
int main(int argc,char **argv) {
    void *u=dlopen("libudd.so.3.1",RTLD_LAZY|RTLD_LOCAL); CHECK(u);
    void *p=dlopen("libpthread.so.0",RTLD_NOW|RTLD_LOCAL); CHECK(p);
    original_open=dlsym(u,"ipcs_open"); original_close=dlsym(u,"ipcs_close");
    original_udd=dlsym(u,"d4_udd_open"); original_mutex=dlsym(p,"pthread_mutex_lock");
    libc_open=dlsym(RTLD_DEFAULT,"open");
    CHECK(original_open&&original_close&&original_udd&&original_mutex&&libc_open);
    Dl_info info; CHECK(dladdr(original_open,&info)); unsigned bias=(unsigned)info.dli_fbase;
    udd_count=(void *)(bias+0x126744U); CHECK(*udd_count==0);
    outer_mutex=(void *)(bias+0x1012ecU);
    api.tid=tid; api.clock=clock_time; trace.enabled=1;
    unsigned *slot;
    slot=(void *)(bias+stock_slots[NX_SFR_UDD_OPEN].slot); udd_slot=slot; native_udd=*slot; *slot=(unsigned)udd;
    slot=(void *)(bias+stock_slots[NX_SFR_MUTEX].slot); mutex_slot=slot; native_mutex=*slot; *slot=(unsigned)mutex;
    slot=(void *)(bias+stock_slots[NX_SFR_DEVICE_OPEN].slot); *slot=(unsigned)device_open;
    slot=(void *)(bias+stock_slots[NX_SFR_REGMAP].slot); *slot=(unsigned)regmap;
    slot=(void *)(bias+stock_slots[NX_SFR_IRQ].slot); *slot=(unsigned)irq;
    slot=(void *)(bias+stock_slots[NX_SFR_INIT].slot); *slot=(unsigned)init;
    slot=(void *)(bias+FIXTURE_CLOSE_REG_SLOT); *slot=(unsigned)no_hardware;
    slot=(void *)(bias+FIXTURE_CLOSE_IRQ_SLOT); *slot=(unsigned)no_hardware;
    /* Avoid absolute runtime addresses in the generated header. */
    (void)bias;
    CHECK(!sem_init(&ready,0,0)&&!sem_init(&finish_owner,0,0));
    if(argc==2&&!strcmp(argv[1],"--cost")) { costs(); return 0; }
    pthread_t thread; CHECK(!pthread_create(&thread,NULL,owner,NULL));
    CHECK(!sem_wait(&ready)); CHECK(*udd_count==1);
    open_call(NX_IPCT_PREVIEW); CHECK(*udd_count==2);
    CHECK(!original_close()&&*udd_count==1);
    CHECK(!sem_post(&finish_owner)); CHECK(!pthread_join(thread,NULL)&&*udd_count==0);
    open_call(NX_IPCT_PREVIEW); CHECK(!original_close()&&*udd_count==0);
    CHECK(physical_opens==2&&trace.next==3);
    CHECK(trace.records[0].tid!=trace.records[1].tid);
    for(unsigned i=0;i<3;++i) {
        struct nx_ipct_record *r=&trace.records[i]; CHECK(r->committed&&r->phase[NX_IPCT_TOTAL].valid==3);
        CHECK(r->phase[NX_IPCT_UDD].count==1&&r->phase[NX_IPCT_UDD].rc==(i==1?1:0));
        CHECK(r->phase[NX_IPCT_DEVICE_OPEN].count==(i==1?0U:1U));
        CHECK(r->phase[NX_IPCT_REGMAP].count==(i==1?0U:1U));
    }
    export(); return 0;
}
