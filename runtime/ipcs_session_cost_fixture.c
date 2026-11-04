/* Measure the unchanged ARM session manager and stress its reference owner.
 * Shares native pthread/futex/lock fixtures with the failure checks. */
#define main nx_failure_fixture_main
#include "ipcs_session_failure_fixture.c"
#undef main

static unsigned long long ns(unsigned which) {
    struct timespec t; CHECK(!syscall(SYS_clock_gettime,which,&t));
    return (unsigned long long)t.tv_sec*1000000000ULL+t.tv_nsec;
}
static void pair(void) {
    CHECK(!nx_ips_frame_enter(&state));
    CHECK(!take(&leases[0])); CHECK(!give(&leases[0]));
    nx_ips_frame_leave(&state);
}
int main(void) {
    setbuf(stdout,NULL); setup(); CHECK(!enter_frame()); nx_ips_frame_leave(&state);
    unsigned first=opens;
    for(unsigned sample=0;sample<31;++sample) {
        unsigned long long w=ns(1),c=ns(3),p=ns(2);
        for(unsigned i=0;i<64;++i) pair();
        p=ns(2)-p; c=ns(3)-c; w=ns(1)-w;
        CHECK(state.held==1&&state.active==0&&refs==1&&opens==first&&closes==0);
        free_locks();
        printf("{\"type\":\"session-cost\",\"mode\":\"ready-admission-pair\",\"sample\":%u,\"calls\":64,"
               "\"wall_ns\":%llu,\"cpu_ns\":%llu,\"process_cpu_ns\":%llu}\n",sample,w,c,p);
    }
    /* Repeated capture, stop and control interleavings exercise real locks and
     * owner wakeups. Native Reset/IQ processing is not substituted or removed
     * by this state-machine fixture; it is outside this measured scope. */
    for(unsigned cycle=0;cycle<96;++cycle) {
        unsigned reason=cycle%3;
        nx_ips_hold(&state,reason); nx_ips_hold(&state,reason);
        CHECK(nx_ips_frame_enter(&state)==NX_IPS_BUSY);
        CHECK(!nx_ips_drain(&state,1000)&&!refs);
        free_locks(); nx_ips_release(&state,reason);
        CHECK(nx_ips_frame_enter(&state)==NX_IPS_BUSY);
        nx_ips_release(&state,reason);
        CHECK(!enter_frame()); nx_ips_frame_leave(&state);
        for(unsigned i=0;i<16;++i) pair();
        CHECK(state.held==1&&refs==1); free_locks();
    }
    nx_ips_hold(&state,NX_IPS_CONTROL); CHECK(!nx_ips_drain(&state,1000));
    CHECK(!refs&&opens==closes&&owner==state.owner_tid&&owner!=thread_id()); free_locks();
    nx_ips_release(&state,NX_IPS_CONTROL); CHECK(!enter_frame()); nx_ips_frame_leave(&state);
    usleep(450000); CHECK(state.held==0&&state.idle_releases>0&&refs==0&&opens==closes); free_locks();
    CHECK(!state.error&&!state.drain_timeouts&&!state.open_errors&&!state.close_errors&&!state.unlock_errors);
    printf("{\"check\":\"session_stress\",\"cycles\":96,\"frames\":%u,\"opens\":%u,\"closes\":%u,"
           "\"held\":%u,\"idle_releases\":%u,\"owner_tid\":%u,\"render_tid\":%u,\"passed\":true}\n",
           state.frames,opens,closes,state.held,state.idle_releases,owner,thread_id());
    return 0;
}
