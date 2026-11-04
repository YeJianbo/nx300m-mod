/* ARM lifecycle fault tests. No ISP device or register is touched. */
#define _GNU_SOURCE
#include <pthread.h>
#include <semaphore.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#include "ipcs_session.h"

#define CHECK(v) do { if(!(v)) { fprintf(stderr,"session check failed line %u: %s\n",__LINE__,#v); exit(1); } } while(0)
static struct nx_ips state;
static sem_t leases[5];
static unsigned opens,closes,refs,owner,order,held;
static int fail_open,fail_close;
static unsigned thread_id(void) { return (unsigned)syscall(SYS_gettid); }
static unsigned ms(void) { struct timespec t; CHECK(!syscall(SYS_clock_gettime,CLOCK_MONOTONIC,&t)); return (unsigned)t.tv_sec*1000U+(unsigned)t.tv_nsec/1000000U; }
static int wait_word(volatile unsigned *p,unsigned n,unsigned timeout_ms) {
    struct timespec t={timeout_ms/1000U,(timeout_ms%1000U)*1000000U};
    return (int)syscall(SYS_futex,p,0,n,timeout_ms?&t:NULL,0,0);
}
static void wake_word(volatile unsigned *p) { syscall(SYS_futex,p,1,0x7fffffff,0,0,0); }
static int take(void *p) {
    unsigned i=(sem_t *)p-leases;
    CHECK(i<5&&i==order);
    if(sem_trywait(p)) return -1;
    ++order; ++held; return 0;
}
static int give(void *p) {
    unsigned i=(sem_t *)p-leases;
    CHECK(held&&i==held-1); --held;
    if(!held) order=0;
    return sem_post(p);
}
static int open_ref(void) {
    CHECK(held==5&&state.active==0); ++opens; ++refs;
    if(!owner) owner=thread_id();
    CHECK(owner==thread_id()); return fail_open;
}
static int close_ref(void) {
    CHECK(held==5&&state.active==0&&refs==1&&owner==thread_id()); ++closes;
    if(fail_close) return -78;
    --refs; return 0;
}
static void *run(void *p) { nx_ips_owner(p); return NULL; }
static void setup(void) {
    struct nx_ips_api a={0};
    for(unsigned i=0;i<5;++i) { CHECK(!sem_init(&leases[i],0,1)); a.locks[i]=&leases[i]; }
    a.trywait=take; a.post=give; a.open=open_ref; a.close=close_ref;
    a.tid=thread_id; a.ms=ms; a.wait=wait_word; a.wake=wake_word;
    nx_ips_init(&state,&a);
    pthread_t t; pthread_attr_t attr;
    CHECK(sizeof attr<=64); CHECK(!pthread_attr_init(&attr)); CHECK(!pthread_attr_setstacksize(&attr,65536));
    CHECK(!pthread_create(&t,&attr,run,&state)); CHECK(!pthread_attr_destroy(&attr));
    for(unsigned i=0;!__atomic_load_n(&state.running,__ATOMIC_ACQUIRE);++i) { CHECK(i<1000); usleep(1000); }
}
static int enter_frame(void) {
    for(unsigned i=0;i<1000;++i) {
        if(!nx_ips_frame_enter(&state)) return 0;
        if(__atomic_load_n(&state.state,__ATOMIC_ACQUIRE)==NX_IPS_FAILED) return -1;
        usleep(1000);
    }
    return -1;
}
static void free_locks(void) {
    CHECK(!held);
    for(unsigned i=0;i<5;++i) { int v=0; CHECK(!sem_getvalue(&leases[i],&v)&&v==1); }
}
static void scenario(unsigned test) {
    if(test==1||test==8||test==10||test==11) fail_open=NX_IPS_OPEN_IRQ_ERROR;
    if(test==9) fail_open=-1;
    setup();
    if(test>=9) {
        CHECK(enter_frame()==-1&&state.held==2&&opens==1&&refs==1);
        if(test==9) {
            for(unsigned i=0;i<50;++i) { CHECK(enter_frame()==-1); usleep(50000); }
            CHECK(opens==1&&closes==0&&state.error==-1&&state.recoveries==0);
        } else if(test==10) {
            fail_close=1; fail_open=0;
            unsigned guard=0;
            while(!closes) { enter_frame(); usleep(50000); CHECK(++guard<120); }
            for(unsigned i=0;i<50;++i) { CHECK(enter_frame()==-1); usleep(50000); }
            CHECK(opens==1&&closes==1&&state.held==2&&state.error==-78&&state.recoveries==1);
        } else {
            CHECK(!sem_wait(&leases[0]));
            for(unsigned i=0;i<50;++i) { CHECK(enter_frame()==-1); usleep(50000); }
            CHECK(closes==0&&opens==1&&state.lock_busy>0);
            CHECK(!sem_post(&leases[0])); fail_open=0;
            unsigned guard=0;
            while(enter_frame()) { CHECK(++guard<60); usleep(50000); }
            nx_ips_frame_leave(&state); nx_ips_hold(&state,NX_IPS_CONTROL);
            CHECK(!nx_ips_drain(&state,1000)&&opens==closes&&!refs);
        }
        free_locks(); return;
    }
    if(test==1) {
        /* Demand-driven retry: no demand, no retry; renewed demand retries
         * close-then-open until the bounded limit, then stays blocked. */
        CHECK(enter_frame()==-1); CHECK(state.state==NX_IPS_FAILED&&state.held==2&&opens==1&&closes==0);
        usleep(200000); CHECK(opens==1&&closes==0&&refs==1);
        unsigned guard=0;
        while(opens<4) { enter_frame(); usleep(50000); CHECK(++guard<200); }
        CHECK(opens==4&&closes==3&&refs==1&&state.held==2&&state.state==NX_IPS_FAILED);
        unsigned settled=opens;
        usleep(300000); CHECK(opens==settled&&state.recoveries==NX_IPS_RECOVERY_LIMIT);
        free_locks(); return;
    }
    if(test==8) {
        /* A renewed frame demand recovers the open failure through one
         * close-then-open retry on the fixed owner thread; the fresh open
         * reruns the full stock path, and the frame keeps its arbitration. */
        CHECK(enter_frame()==-1);
        CHECK(state.state==NX_IPS_FAILED&&state.held==2&&opens==1&&closes==0&&refs==1);
        fail_open=0;
        unsigned guard=0;
        while(enter_frame()) { CHECK(++guard<30); usleep(200000); }
        CHECK(opens==2&&closes==1&&refs==1&&state.held==1&&state.state==NX_IPS_READY&&state.active==1);
        /* A successful recovery opens a new epoch: the retry budget resets,
         * while recovery_closes keeps the total close attempts for diagnosis. */
        CHECK(state.recoveries==0&&state.recovery_closes==1&&state.error==0);
        nx_ips_frame_leave(&state);
        CHECK(!nx_ips_drain(&state,1000));
        CHECK(!refs&&opens==closes&&owner==state.owner_tid&&owner!=thread_id()); free_locks(); return;
    }
    CHECK(!enter_frame()); CHECK(state.active==1&&refs==1); free_locks();
    if(test==2) {
        fail_close=1; nx_ips_frame_leave(&state); nx_ips_hold(&state,NX_IPS_CONTROL);
        CHECK(nx_ips_drain(&state,1000)==NX_IPS_UNCERTAIN);
        CHECK(state.held==2&&closes==1&&refs==1); usleep(200000); CHECK(closes==1); free_locks(); return;
    }
    if(test==3) {
        nx_ips_hold(&state,NX_IPS_CAPTURE); CHECK(nx_ips_frame_enter(&state)==NX_IPS_BUSY);
        CHECK(nx_ips_drain(&state,5)==NX_IPS_TIMEOUT&&closes==0&&state.active==1);
        nx_ips_frame_leave(&state); CHECK(!nx_ips_drain(&state,1000)); CHECK(!refs&&closes==1);
        nx_ips_hold(&state,NX_IPS_CAPTURE); nx_ips_release(&state,NX_IPS_CAPTURE);
        CHECK(nx_ips_frame_enter(&state)==NX_IPS_BUSY);
        nx_ips_release(&state,NX_IPS_CAPTURE); CHECK(!enter_frame()); nx_ips_frame_leave(&state);
    } else if(test==4) {
        nx_ips_fault(&state,-88); CHECK(nx_ips_frame_enter(&state)==NX_IPS_BUSY);
        CHECK(nx_ips_drain(&state,5)==NX_IPS_TIMEOUT); nx_ips_frame_leave(&state);
        CHECK(!nx_ips_drain(&state,1000)); CHECK(state.error==-88&&!refs&&closes==1); free_locks(); return;
    } else if(test==5) {
        __atomic_store_n(&state.external_stop,1,__ATOMIC_RELEASE);
        CHECK(nx_ips_frame_enter(&state)==NX_IPS_BUSY);
        CHECK(nx_ips_drain(&state,5)==NX_IPS_TIMEOUT&&closes==0);
        nx_ips_frame_leave(&state); CHECK(!nx_ips_drain(&state,1000)); CHECK(!refs);
        __atomic_store_n(&state.external_stop,0,__ATOMIC_RELEASE);
        CHECK(!enter_frame()); nx_ips_frame_leave(&state);
    } else if(test==6) {
        nx_ips_hold(&state,NX_IPS_STREAM); nx_ips_frame_leave(&state);
        CHECK(!nx_ips_drain(&state,1000)); CHECK(nx_ips_frame_enter(&state)==NX_IPS_BUSY);
        nx_ips_release(&state,NX_IPS_STREAM); CHECK(!enter_frame()); nx_ips_frame_leave(&state);
    } else if(test==7) {
        nx_ips_hold(&state,NX_IPS_STREAM);
        CHECK(nx_ips_drain(&state,5)==NX_IPS_TIMEOUT&&state.active==1&&closes==0);
        nx_ips_fault(&state,NX_IPS_TIMEOUT);
        CHECK(nx_ips_frame_enter(&state)==NX_IPS_BUSY&&state.blockers[NX_IPS_STREAM]==1);
        nx_ips_frame_leave(&state); CHECK(!nx_ips_drain(&state,1000));
        CHECK(!refs&&opens==closes&&state.error==NX_IPS_TIMEOUT);
        CHECK(nx_ips_frame_enter(&state)==NX_IPS_BUSY); free_locks(); return;
    }
    nx_ips_hold(&state,NX_IPS_CONTROL); CHECK(!nx_ips_drain(&state,1000));
    CHECK(!refs&&opens==closes&&owner==state.owner_tid&&owner!=thread_id()); free_locks();
}
int main(void) {
    setbuf(stdout,NULL);
    static const char *names[]={"open_failure_demand_retry_capped","close_failure_no_retry","capture_drain_and_nested_admission",
        "render_fault_releases_healthy_reference","control_drain_before_snapshot","stream_stop_and_resume",
        "stream_drain_timeout_keeps_admission_closed","open_failure_recovers_on_demand",
        "ambiguous_open_failure_no_cleanup","recovery_close_failure_stays_blocked",
        "recovery_waits_for_lifecycle_lock"};
    for(unsigned i=1;i<=11;++i) {
        pid_t p=fork(); CHECK(p>=0);
        if(!p) { scenario(i); exit(0); }
        int result; CHECK(waitpid(p,&result,0)==p&&WIFEXITED(result)&&WEXITSTATUS(result)==0);
        printf("{\"check\":\"%s\",\"passed\":true}\n",names[i-1]);
    }
    return 0;
}
