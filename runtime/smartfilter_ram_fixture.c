/* Stock worker plus actual ptrace-installed RAM hooks. Device/cache and
 * B2Y lease construction are explicit doubles; the camera libraries are intact. */
#define _GNU_SOURCE
#define NX_SF_RAM_FIXTURE
#define main unused_boundary_main
#include "smartfilter_fixture.c"
#undef main
#include <stddef.h>
#include <fcntl.h>
#include <errno.h>
#include <sys/mman.h>
#include <sys/msg.h>
#include "smartfilter_ram.h"
#include "smartfilter_stock.h"
extern int native_fscanf(FILE *,const char *,...) __asm__("fscanf");
#define fscanf native_fscanf

struct nx_sfr_fixture_bindings nx_sfr_fixture_bindings;
static unsigned *ram_slots[NX_SFR_SLOTS], ram_b2y[0x5100/4];
static unsigned ram_capture_base;
static unsigned ram_capture_callback_count;
static struct nx_sfr_runtime *ram_runtime;
static int ram_block,ram_pending_stage,ram_reset_error,ram_close_error,ram_post_error;
#ifdef NX_LUT_SWITCH_FIXTURE
static unsigned lut_node[9],lut_params[39],lut_memory[3];
static unsigned char *lut_registers;
static int lut_sync_enabled;
static void lut_switch_setup(void *iq) {
    lut_registers=calloc(1,0xb000); REQUIRE(lut_registers);
    void *udd=dlopen("libudd.so.3.1",RTLD_LAZY|RTLD_GLOBAL); REQUIRE(udd);
    void **register_base=symbol(udd,"ipcs_reg_base");
    void **manager=symbol(iq,"_ZN13CIpcSingletonI17CIpcMemoryManagerE11m_pInstanceE");
    REQUIRE(!*manager); lut_memory[0]=(unsigned)calloc(1,16384); REQUIRE(lut_memory[0]);
    *manager=lut_memory; *register_base=lut_registers;
    void (*construct)(void *)=symbol(iq,"_ZN12CIpcs3DLutD4C1Ev"); construct(lut_node);
    lut_params[0x58/4]=9; lut_params[0x40/4]=256;
    ((unsigned char *)lut_params)[0x83]=1;
}
static void lut_switch_process(void) {
    void (*update_node)(void *,void *)=(void *)((unsigned *)lut_node[0])[0];
    native_node_reset(lut_node); update_node(lut_node,lut_params);
    for(unsigned c=0;c<3;++c) memcpy(table[c],lut_registers+0x9000+c*0x700,1460);
}
#endif
static pthread_t ram_async_frame,ram_async_release;
static jmp_buf ram_path_done;
static int ram_path_role=-1;
static void ram_path_probe(unsigned);
#ifdef NX_SFR_SESSION
static unsigned ram_native_stops,ram_native_starts;
static int ram_capture_queue=-1;
static unsigned ram_liveview[64],ram_handler_factory[84],ram_item_handler[4],ram_callback[8],ram_callback_vptr[3];
static unsigned ram_controller_dispatches,ram_topology_rebuilds,ram_stock_callback_done;
static unsigned ram_frame_tid,ram_controller_tid;
static unsigned ram_stop_pending,ram_stop_callbacks,ram_memory_state=1,ram_waiting_observed;
static void *ram_liveview_instance(void) { return ram_liveview; }
EXPORTED(void *, ram_factory,"_ZN15CHandlerFactory11GetInstanceEv",(void)) { return ram_handler_factory; }
EXPORTED(void *, ram_controller,"_ZN18CCaptureController8InstanceEv",(void)) { return ram_b2y; }
EXPORTED(int, ram_measure,"_ZNK18CCaptureController18MeasurePerformanceEv",(void *self)) {
    REQUIRE(self==ram_b2y); return 0;
}
EXPORTED(void *, ram_3a_instance,"_ZN13C3AController11GetInstanceEv",(void)) { return ram_b2y; }
EXPORTED(void, ram_3a_handle,"_ZN13C3AController6HandleEP7tMSGBUF",(void *self,void *packet)) {
    unsigned *p=packet; REQUIRE(self==ram_b2y&&p[0]==1&&p[1]==0x02010034&&p[2]==0x39);
}
EXPORTED(void, ram_callback_execute,"_ZN17CLiveviewCallback19executeCallbackNodeEiP9CCapImagePvj",
         (void *self,int which,void *image,void *data_ptr,unsigned tag)) {
    (void)image; (void)data_ptr; (void)tag; REQUIRE(self==ram_callback&&which==1); ++ram_stock_callback_done;
}
static void ram_callback_notify(void *self,int which,int value) {
    REQUIRE(self==ram_callback+1&&which==1&&value==0);
}
/* Only hardware stop and the final Create boundary are doubles. Execute the
 * real stop callback (including CheckMapChange=false), memory matcher and
 * restart prefix, instead of assuming changeState completed reconstruction. */
EXPORTED(int, ram_bring_down,"_ZN17CLiveviewCallback13BringDownNodeEv",(void *self)) {
    REQUIRE(self==ram_callback); return 1;
}
EXPORTED(void, ram_stream_off,"_ZN9CLiveview14videoStreamOffEb",(void *self,unsigned flag)) {
    REQUIRE(self==ram_liveview&&!flag); ram_stop_pending=1;
}
EXPORTED(void, ram_tear_down,"_ZN17CLiveviewCallback12TearDownNodeEv",(void *self)) {
    REQUIRE(self==ram_callback); ++ram_stop_callbacks;
}
EXPORTED(unsigned, ram_memory,"_ZN15CLiveviewBuffer14GetMemoryStateEv",(void *self)) {
    REQUIRE(self==buffer_object); return ram_memory_state;
}
EXPORTED(unsigned, ram_fd,"_ZN15CLiveviewCommon11GetFDEnableEv",(void *self)) { REQUIRE(self==common); return 0; }
EXPORTED(unsigned, ram_zoom,"_ZN15CLiveviewCommon20GetZoomMagnificationEv",(void *self)) { REQUIRE(self==common); return 0; }
EXPORTED(unsigned, ram_display,"_ZN15CLiveviewCommon22GetOutputDisplayDeviceEv",(void *self)) { REQUIRE(self==common); return 0; }
EXPORTED(unsigned, ram_peak,"_ZN15CLiveviewCommon18GetFocusPeakEnableEv",(void *self)) { REQUIRE(self==common); return 0; }
EXPORTED(unsigned, ram_tracking,"_ZN15CLiveviewCommon23GetObjectTrackingEnableEv",(void *self)) { REQUIRE(self==common); return 0; }
EXPORTED(int, ram_create,"_ZN20CLiveviewPathCreator6CreateEi",(void *self,int mode)) {
    REQUIRE(self==ram_liveview&&mode==0&&ram_controller_tid==(unsigned)syscall(SYS_gettid));
    ++ram_topology_rebuilds;
    ram_path_probe(NX_SFR_CREATE);
    return 0;
}
EXPORTED(void, ram_skip_rate,"_ZN9CLiveview16setFrameSkipRateEv",(void *self)) { REQUIRE(self==ram_liveview); }
EXPORTED(int, ram_prepare_nodes,"_ZN17CLiveviewCallback11PrepareNodeEv",(void *self)) {
    REQUIRE(self==ram_callback); return 1;
}
EXPORTED(void, ram_stream_on,"_ZN9CLiveview13videoStreamOnEv",(void *self)) {
    REQUIRE(self==ram_liveview); ram_liveview[0x34/4]=7;
}
static unsigned ram_device_open_original;
static const char *ram_device_open_owner;
static int ram_stop_double(void *self,unsigned state,unsigned flag) {
    REQUIRE(self==ram_b2y&&state==17&&flag==1); ++ram_native_stops; return 41;
}
static int ram_start_double(void *self,int mode,unsigned rate) {
    REQUIRE(self==ram_b2y&&mode==17&&rate==60); ++ram_native_starts; return 1;
}
static int ram_b2y_open_call(void) {
    int (*call)(void *)=(void *)*ram_slots[NX_SFR_B2Y_OPEN]; return call(ram_b2y);
}
static void ram_b2y_close_call(void) {
    int (*call)(void *)=(void *)*ram_slots[NX_SFR_B2Y_CLOSE]; call(ram_b2y);
}
EXPORTED(int, session_sem_get,"_fx_semaphore_get",(const char *a,const char *b,unsigned line,void *p)) {
    (void)a; (void)b; (void)line; return sem_wait(p);
}
EXPORTED(int, session_sem_put,"_fx_semaphore_put",(const char *a,const char *b,unsigned line,void *p)) {
    (void)a; (void)b; (void)line; return sem_post(p);
}
#endif

EXPORTED(unsigned, ram_smartauto,"_ZN15CLiveviewCommon18GetSmartAutoEnableEv",(void *self)) {
    REQUIRE(self==common);
    if(ram_path_role==NX_SFR_CREATE) longjmp(ram_path_done,1);
    return 0;
}
EXPORTED(unsigned, ram_ldc,"_ZN15CLiveviewCommon12GetLDCEnableEv",(void *self)) {
    REQUIRE(self==common);
    if(ram_path_role==NX_SFR_LDC) longjmp(ram_path_done,1);
    return 0;
}
EXPORTED(unsigned, ram_focus,"_ZN25CLiveviewPathListLiveview18getFocusPeakEnableEv",(void *self)) {
    (void)self; REQUIRE(ram_path_role==NX_SFR_B2Y); longjmp(ram_path_done,1);
}
/* Execute the original path prefixes through the real whitelisted BL, then
 * stop at the next environmental call. This checks routing decisions, not a
 * complete hardware topology construction. */
static void ram_path_probe(unsigned role) {
    unsigned path[32]={0},index=0; path[3]=(unsigned)common;
    static const unsigned entry[NX_SFR_MODE_CALLS]={0x292be4,0,0x293278,0x2934cc};
    REQUIRE(role<NX_SFR_MODE_CALLS&&role!=NX_SFR_EXECUTE);
    ram_path_role=(int)role; filter_mode=15;
    if(!setjmp(ram_path_done)) {
        void (*call)(void *,unsigned,unsigned *)=(void *)(ram_capture_base+entry[role]);
        call(path,0,&index); REQUIRE(0);
    }
    ram_path_role=-1;
}
static unsigned ram_live_mode(void) {
    unsigned address=*ram_slots[NX_SFR_LIVEVIEW_MODE_GETTER];
    if(address==ram_capture_base+stock_slots[NX_SFR_LIVEVIEW_MODE_GETTER].resolver)
        address=ram_capture_base+stock_slots[NX_SFR_LIVEVIEW_MODE_GETTER].address;
    unsigned (*call)(void *)=(void *)address;
    return call(common);
}
#ifdef NX_SFR_SESSION
static void *ram_refresh_controller(void *unused) {
    (void)unused;
    unsigned payload[4],receiver[16]={0};
    ssize_t n=msgrcv(ram_capture_queue,payload,12,0,IPC_NOWAIT);
    if(n<0) { REQUIRE(errno==ENOMSG); return NULL; }
    REQUIRE(n==12&&payload[0]==1&&payload[1]==0x02010034&&payload[2]==0x39);
    memcpy(receiver+2,payload,sizeof payload);
    ram_controller_tid=(unsigned)syscall(SYS_gettid);
    int (*handle)(void *)=(void *)*ram_slots[NX_SFR_MESSAGE_HANDLE];
    REQUIRE(!handle(receiver)); ++ram_controller_dispatches;
    if(ram_stop_pending) {
        ram_stop_pending=0;
        void (*stopped)(void *,int)=(void *)*ram_slots[NX_SFR_CHANGE_STOP_CALLBACK];
        stopped(ram_liveview,0);
        /* No map-change byte is forged. The original callback must wait. */
        REQUIRE(ram_liveview[0x34/4]==20);
        ++ram_waiting_observed;
        REQUIRE(msgrcv(ram_capture_queue,payload,12,0,IPC_NOWAIT)==12);
        REQUIRE(payload[0]==1&&payload[1]==0x02010034&&payload[2]==0x39);
        memcpy(receiver+2,payload,sizeof payload);
        REQUIRE(!handle(receiver));
    }
    return NULL;
}
static void ram_refresh_callback(void) {
    void (*call)(void *,int,void *,void *,unsigned)=(void *)*ram_slots[NX_SFR_FRAME_CALLBACK];
    ram_frame_tid=(unsigned)syscall(SYS_gettid);
    unsigned before=ram_stock_callback_done;
    call(ram_callback,1,NULL,NULL,0);
    REQUIRE(ram_stock_callback_done==before+1);
}
static void ram_refresh(void) {
    filter_mode=15;
    unsigned before=ram_topology_rebuilds;
    ram_refresh_callback();
    pthread_t controller; REQUIRE(!pthread_create(&controller,NULL,ram_refresh_controller,NULL));
    REQUIRE(!pthread_join(controller,NULL));
    if(ram_topology_rebuilds!=before) REQUIRE(ram_frame_tid!=ram_controller_tid);
    REQUIRE(!ram_runtime->refresh_queued);
    printf("{\"type\":\"fixture_refresh\",\"requests\":%u,\"dispatches\":%u,\"rebuilds\":%u,\"s2_path\":%u,\"frame_tid\":%u,\"controller_tid\":%u,\"error\":%d}\n",
           ram_runtime->refresh_requests,ram_controller_dispatches,ram_topology_rebuilds,
           ram_runtime->s2_path,ram_frame_tid,ram_controller_tid,ram_runtime->refresh_error);
}
#endif
static void ram_off_prepare(int queue) {
    reset(); filter_mode=15; work_available=0; case_name="s2-off";
    REQUIRE(prepare_node(node)==1&&node[0x6c/4]);
    if(queue) REQUIRE(execute_node(node,data+2,data,71)==1&&queued==1);
}
static void ram_off_worker(void) {
    REQUIRE(queued); processing=1;
    if(!setjmp(worker_done)) run_worker(node);
    processing=0;
    REQUIRE(callbacks==1&&!buffer_lock[0]&&!node_status[0]&&!((unsigned char *)node)[0x70]);
    REQUIRE(!memcmp(input,original,FRAME));
    save("/tmp/smartfilter-ram.frame",output,FRAME);
}

EXPORTED(unsigned, ram_userdata,"_ZN22CDataManagementService11GetUserDataEi",(void *self,int key)) {
    (void)self; REQUIRE(key==0x39);
    if(filter_mode==15) return 0x390000;
    REQUIRE(filter_mode<=1); return 0x390001+filter_mode;
}
static void *ram_b2y_instance(void) { return ram_b2y; }
static void *ram_get_semaphore(void *self,unsigned index) {
    REQUIRE(self==ram_b2y&&index<4); return &native_semaphore[index];
}
EXPORTED(void, ram_cache_flush,"_ZN15CLiveviewBuffer10CacheFlushEmj",
         (void *self,unsigned address,unsigned length)) {
    REQUIRE(self==buffer_object); record_slot_flush(address,length);
}
int ipcs_get_ring_size_for_bayer_to_ycc(void *param,unsigned *ring) {
    REQUIRE(param&&ring); memset(ring,0,16); return 0;
}
int ipcs_set_one_tile_bayer_to_ycc_operation(void *param) { REQUIRE(param); return 0; }
static void ram_capture_callback(void) { ++ram_capture_callback_count; }
static int ram_prepare(void) {
    int (*call)(void *,void (*)(void))=(void *)(uintptr_t)*ram_slots[NX_SFR_PREPARE];
    return call(ram_b2y,ram_capture_callback);
}
static int ram_finish(void) {
    int (*call)(void *)=(void *)(uintptr_t)*ram_slots[NX_SFR_FINISH]; return call(ram_b2y);
}
static unsigned ram_get_mode(unsigned mode) {
    unsigned parameter[32]={0}; parameter[0x48/4]=mode;
    unsigned address=*ram_slots[NX_SFR_STILL_MODE];
    /* A direct test call through an unresolved GOT word has no PLT veneer to
     * populate ip for the dynamic resolver. Call the original getter instead. */
    if(address==ram_capture_base+stock_slots[NX_SFR_STILL_MODE].resolver)
        address=ram_capture_base+stock_slots[NX_SFR_STILL_MODE].address;
    unsigned (*call)(void *)=(void *)(uintptr_t)address; return call(parameter);
}
static void ram_block_here(void) {
    if(!ram_block) return;
    REQUIRE(!pthread_mutex_lock(&render_mutex)); render_entered=1;
    REQUIRE(!pthread_cond_signal(&render_condition));
    while(!render_continue) REQUIRE(!pthread_cond_wait(&render_condition,&render_mutex));
    REQUIRE(!pthread_mutex_unlock(&render_mutex));
}
static void ram_operation_enter(void) { if(!ram_pending_stage) ram_block_here(); }
static int ram_probe_reset(void) {
    if(ram_pending_stage==1) ram_block_here();
    int rc=ipcs_init(); return rc?rc:ram_reset_error?-77:0;
}
static int ram_probe_close(void) { int rc=ipcs_close(); return rc?rc:ram_close_error?-78:0; }
static int ram_probe_post(void *semaphore) {
    if(ram_pending_stage==3) ram_block_here();
    int rc=sem_post(semaphore); return rc?rc:ram_post_error?-79:0;
}
static void ram_probe_iq_process(int mode) {
    if(ram_pending_stage==2) ram_block_here();
    native_iq_process(mode);
#ifdef NX_LUT_SWITCH_FIXTURE
    if(lut_sync_enabled) lut_switch_process();
#endif
}
static unsigned ram_frame_mode(void) {
    return ram_runtime&&ram_runtime->work_managed?15:NX_SFR_LIVEVIEW_MODE;
}
static void *ram_frame_thread(void *unused) {
    (void)unused;
    if(ram_runtime&&ram_runtime->work_managed) {
        /* The owner opens its Session asynchronously. A cold first frame can
         * pass through; keep supplying frames until the held one is admitted. */
        for(unsigned tries=0;;++tries) {
            REQUIRE(tries<100); ram_off_prepare(1); ram_off_worker();
            if(operations) break;
            usleep(10000);
        }
    }
    else slot_frame("ram-inflight",ram_frame_mode());
    return NULL;
}
static void *ram_capture_thread(void *unused) { (void)unused; REQUIRE(!ram_prepare()); return NULL; }
static void *ram_release_on_control(void *unused) {
    (void)unused; unsigned tries=0;
    while(!__atomic_load_n(&ram_runtime->control_pause,__ATOMIC_ACQUIRE)) { REQUIRE(++tries<30000); usleep(1000); }
    REQUIRE(!pthread_mutex_lock(&render_mutex)); render_continue=1;
    REQUIRE(!pthread_cond_signal(&render_condition)); REQUIRE(!pthread_mutex_unlock(&render_mutex));
    return NULL;
}
static void ram_start_async_frame(void) {
    ram_block=1; render_entered=render_continue=0;
    REQUIRE(!pthread_create(&ram_async_frame,NULL,ram_frame_thread,NULL));
    REQUIRE(!pthread_mutex_lock(&render_mutex));
    while(!render_entered) REQUIRE(!pthread_cond_wait(&render_condition,&render_mutex));
    REQUIRE(!pthread_mutex_unlock(&render_mutex));
    REQUIRE(!pthread_create(&ram_async_release,NULL,ram_release_on_control,NULL));
}
static void ram_capture_test(void) {
    pthread_t frame_thread,capture_thread;
    ram_block=1; render_entered=render_continue=0;
    REQUIRE(!pthread_create(&frame_thread,NULL,ram_frame_thread,NULL));
    REQUIRE(!pthread_mutex_lock(&render_mutex));
    while(!render_entered) REQUIRE(!pthread_cond_wait(&render_condition,&render_mutex));
    REQUIRE(!pthread_mutex_unlock(&render_mutex));
    unsigned callbacks_before=ram_capture_callback_count;
    unsigned waits_before=__atomic_load_n(&ram_runtime->capture_waits,__ATOMIC_ACQUIRE);
    REQUIRE(!pthread_create(&capture_thread,NULL,ram_capture_thread,NULL));
    unsigned tries=0;
    while(__atomic_load_n(&ram_runtime->capture_waits,__ATOMIC_ACQUIRE)==waits_before) { REQUIRE(++tries<3000); usleep(1000); }
    REQUIRE(ram_runtime->captures==1&&ram_runtime->backend_active==1&&ram_capture_callback_count==callbacks_before);
    REQUIRE(!pthread_mutex_lock(&render_mutex)); render_continue=1;
    REQUIRE(!pthread_cond_signal(&render_condition)); REQUIRE(!pthread_mutex_unlock(&render_mutex));
    REQUIRE(!pthread_join(frame_thread,NULL)); REQUIRE(!pthread_join(capture_thread,NULL));
    ram_block=0;
    REQUIRE(ram_capture_callback_count==callbacks_before+1&&ram_runtime->backend_active==0&&ram_runtime->captures==1);
    slot_frame("ram-capture-held",ram_frame_mode());
    REQUIRE(operations==0&&!memcmp(input,output,FRAME));
    REQUIRE(ram_finish()==1&&ram_runtime->captures==0);
    slot_frame("ram-after-capture",ram_frame_mode()); REQUIRE(operations==1);
}
#if defined(NX_SFR_SESSION) && defined(NX_LUT_SWITCH_FIXTURE)
static pthread_t ram_stream;
static unsigned ram_stream_stop,ram_stream_frames,ram_stream_processed,ram_stream_capture,ram_stream_captured;
static void ram_still_capture(void) {
    REQUIRE(!ram_b2y_open_call()); REQUIRE(!ram_prepare());
    lut_sync_enabled=1; native_iq_initialize(0); ram_probe_iq_process(0);
    save("/tmp/lut-switch-still.bin",table,sizeof table);
    REQUIRE(ram_finish()==1); ram_b2y_close_call();
}
static void *ram_stream_worker(void *unused) {
    (void)unused;
    while(!__atomic_load_n(&ram_stream_stop,__ATOMIC_ACQUIRE)) {
        if(__atomic_load_n(&ram_stream_capture,__ATOMIC_ACQUIRE)) {
            ram_still_capture();
            __atomic_store_n(&ram_stream_capture,0,__ATOMIC_RELEASE);
            __atomic_store_n(&ram_stream_captured,1,__ATOMIC_RELEASE);
        }
        ram_off_prepare(1); ram_off_worker();
        ++ram_stream_frames; if(operations) ++ram_stream_processed;
        usleep(1000);
    }
    return NULL;
}
static void ram_stream_start(void) {
    ram_stream_stop=ram_stream_frames=ram_stream_processed=ram_stream_capture=ram_stream_captured=0;
    /* Hold the first real hooked frame until the controller pauses admission.
     * This makes every switch exercise drain/retry, independent of timing. */
    ram_block=1; render_entered=render_continue=0;
    REQUIRE(!pthread_create(&ram_stream,NULL,ram_stream_worker,NULL));
    REQUIRE(!pthread_mutex_lock(&render_mutex));
    while(!render_entered) REQUIRE(!pthread_cond_wait(&render_condition,&render_mutex));
    REQUIRE(!pthread_mutex_unlock(&render_mutex));
    REQUIRE(!pthread_create(&ram_async_release,NULL,ram_release_on_control,NULL));
}
static void ram_stream_stop_and_check(void) {
    __atomic_store_n(&ram_stream_stop,1,__ATOMIC_RELEASE);
    REQUIRE(!pthread_join(ram_stream,NULL)); REQUIRE(!pthread_join(ram_async_release,NULL)); ram_block=0;
    REQUIRE(ram_stream_frames>1&&ram_stream_processed>0&&!ram_runtime->control_pause&&!ram_runtime->inflight&&!ram_runtime->backend_active);
    printf("{\"type\":\"fixture_stream\",\"frames\":%u,\"processed\":%u,\"control_paused\":%u,\"captures\":%u}\n",
           ram_stream_frames,ram_stream_processed,ram_runtime->control_pause,ram_stream_captured);
    REQUIRE(!fflush(stdout));
}
#endif
int main(void) {
    void *capture=dlopen("libcapture-fw-slpcam-nx300.so",RTLD_LAZY|RTLD_GLOBAL); REQUIRE(capture);
    void *platform=dlopen("libdi-alg-ipplatform.so",RTLD_LAZY|RTLD_GLOBAL); REQUIRE(platform);
    void *iq=dlopen("libiq-fw-slpcam.so",RTLD_LAZY|RTLD_GLOBAL); REQUIRE(iq);
    stock_preview=symbol(capture,"_ZN24CLiveviewNodeSmartFilter11smartFilterEP9CCapImagePv22E_LIVEVIEW_SMARTFILTER");
    prepare_node=symbol(capture,"_ZN24CLiveviewNodeSmartFilter7PrepareEv");
    execute_node=symbol(capture,"_ZN24CLiveviewNodeSmartFilter7ExecuteEP9CCapImagePvj");
    run_worker=symbol(capture,"_ZN24CLiveviewNodeSmartFilter6t_MainEv");
    ade_ycc=symbol(platform,"_Z30ADE_Ipcs_Ycc_correct_Param_SETP9ADE_Image");
    flush_cache=symbol(platform,"_ZN9ADE_Image11FlushDCacheEjj");
    rgb3d_switch=symbol(platform,"ADE_IP_switch_RGB3D_LUT");
    ade_width=symbol(platform,"_ZN9ADE_Image8GetWidthEv");
    ade_height=symbol(platform,"_ZN9ADE_Image9GetHeightEv");
    ade_stride=symbol(platform,"_ZN9ADE_Image9GetStrideEv");
    ade_address=symbol(platform,"_ZN9ADE_Image10GetAddressE17eADE_ADDRESS_TYPE");
    native_api_setup(platform,&nx_sfr_fixture_bindings.api); native_iq_setup(iq);
#ifdef NX_LUT_SWITCH_FIXTURE
    lut_switch_setup(iq);
#endif
    nx_sfr_fixture_bindings.api.reset=ram_probe_reset;
    nx_sfr_fixture_bindings.api.close=ram_probe_close;
    nx_sfr_fixture_bindings.api.post=ram_probe_post;
    nx_sfr_fixture_bindings.api.iq_process=ram_probe_iq_process;
    nx_sfr_fixture_bindings.magic=NX_SFR_MAGIC; nx_sfr_fixture_bindings.size=sizeof nx_sfr_fixture_bindings;
    nx_sfr_fixture_bindings.b2y_instance=ram_b2y_instance;
    nx_sfr_fixture_bindings.get_semaphore=ram_get_semaphore;
    nx_sfr_fixture_bindings.common_instance=common_instance;
#ifdef NX_SFR_SESSION
    nx_sfr_fixture_bindings.udd_count=&native_opened;
    ram_capture_queue=msgget(IPC_PRIVATE,0600); REQUIRE(ram_capture_queue>=0);
    nx_sfr_fixture_bindings.capture_queue=&ram_capture_queue;
    nx_sfr_fixture_bindings.liveview_instance=ram_liveview_instance;
    ram_liveview[0x1c/4]=(unsigned)ram_liveview; ram_liveview[0x20/4]=(unsigned)ram_callback;
    ram_liveview[0x24/4]=(unsigned)common; ram_liveview[0x30/4]=(unsigned)buffer_object;
    ram_liveview[0x34/4]=7; ram_liveview[0x48/4]=15;
    ram_callback_vptr[2]=(unsigned)ram_callback_notify; ram_callback[1]=(unsigned)ram_callback_vptr;
    /* Constructor environment is a double: CHandler's constructor otherwise
     * starts drive/capture services. Keep its native vtable and dispatch. */
    ram_item_handler[0]=(unsigned)symbol(capture,"_ZTV17CNotifyItemChange")+8;
    ram_handler_factory[0xd4/4]=(unsigned)ram_item_handler;
    REQUIRE(!sem_init((void *)((unsigned char *)ram_b2y+0x44),0,1));
#endif
    native_active=1; backend=1; use_slot_bridge=0; operation_enter=ram_operation_enter;
    load("/mnt/mmc/input.yuv",input,FRAME); memcpy(original,input,FRAME);
    load("/mnt/mmc/identity.d4lut",cached_identity,sizeof cached_identity);
    Dl_info info; REQUIRE(dladdr(stock_preview,&info)&&info.dli_fbase);
    ram_capture_base=(unsigned)info.dli_fbase;
    for(unsigned i=0;i<NX_SFR_SLOTS;++i) {
        unsigned bias=(unsigned)info.dli_fbase;
#ifdef NX_SFR_SESSION
        if(stock_slots[i].patch_image==3) {
            void *udd=dlopen("libudd.so.3.1",RTLD_LAZY|RTLD_GLOBAL); REQUIRE(udd);
            Dl_info u; REQUIRE(dladdr(symbol(udd,"ipcs_open"),&u)); bias=(unsigned)u.dli_fbase;
        }
#endif
        ram_slots[i]=(void *)(bias+stock_slots[i].slot);
    }
#ifdef NX_SFR_SESSION
    const char *binding=getenv("NX_SFR_FIXTURE_OPEN_BINDING");
    int threaded=binding&&!strcmp(binding,"pthread");
    REQUIRE(!binding||!strcmp(binding,"libc")||threaded);
    ram_device_open_owner=threaded?"libpthread":"libc";
    void *owner=dlopen(threaded?"libpthread.so.0":"libc.so.6",RTLD_LAZY|RTLD_GLOBAL); REQUIRE(owner);
    ram_device_open_original=(unsigned)symbol(owner,"open");
    Dl_info opened; REQUIRE(dladdr((void *)ram_device_open_original,&opened));
    unsigned offset=threaded?stock_slot_alternatives[0].address:stock_slots[NX_SFR_DEVICE_OPEN].address;
    REQUIRE(ram_device_open_original==(unsigned)opened.dli_fbase+offset);
    *ram_slots[NX_SFR_DEVICE_OPEN]=ram_device_open_original;
#endif
    FILE *ready=fopen("/tmp/smartfilter-ram.ready","w"); REQUIRE(ready);
    fprintf(ready,"%u\n",(unsigned)getpid()); REQUIRE(!fclose(ready));
    unsigned last=0;
    for(;;) {
        unsigned seq=0,command=0;
        FILE *request=fopen("/tmp/smartfilter-ram.request","r");
        if(request) { int count=fscanf(request,"%u %u",&seq,&command); fclose(request); if(count!=2) seq=0; }
        if(!seq||seq==last) { usleep(10000); continue; }
        last=seq; int result=0;
        printf("request seq=%u command=%u\n",seq,command); fflush(stdout);
        if(command==0) break;
        if(command==2) { result=(int)ram_get_mode(NX_SFR_STILL_HOST_MODE); REQUIRE(ram_get_mode(1)==1&&ram_get_mode(3)==3); }
        else {
            const struct nx_sfr_header *h=(void *)(uintptr_t)(*ram_slots[0]&~4095U);
            REQUIRE(h->magic==NX_SFR_MAGIC&&(unsigned)h+h->entry[0]==*ram_slots[0]);
            ram_runtime=(void *)((unsigned)h+h->runtime_offset);
#ifdef NX_SFR_SESSION
#ifdef NX_LUT_SWITCH_FIXTURE
            if(command==113) {
                ram_still_capture();
            }
            else if(command==114) lut_sync_enabled=0;
            else if(command==115) save("/tmp/lut-switch-still.bin",table,sizeof table);
            else if(command==116) ram_stream_start();
            else if(command==117) ram_stream_stop_and_check();
            else if(command==118) {
                __atomic_store_n(&ram_stream_capture,1,__ATOMIC_RELEASE);
                unsigned tries=0;
                while(!__atomic_load_n(&ram_stream_captured,__ATOMIC_ACQUIRE)) { REQUIRE(++tries<3000); usleep(1000); }
            }
            else
#endif
            if(command==120) ram_refresh();
            else if(command==124) {
                filter_mode=15;
                unsigned frames=ram_stock_callback_done,before=ram_topology_rebuilds;
                REQUIRE(ram_runtime->refresh_queued==1);
                pthread_t controller; REQUIRE(!pthread_create(&controller,NULL,ram_refresh_controller,NULL));
                REQUIRE(!pthread_join(controller,NULL));
                REQUIRE(ram_stock_callback_done==frames&&ram_topology_rebuilds==before+1&&
                        ram_runtime->s2_path==1&&!ram_runtime->refresh_pending);
                printf("{\"type\":\"fixture_refresh_without_frame\",\"frame_callbacks\":%u,\"rebuilds\":%u}\n",
                       frames,ram_topology_rebuilds);
            }
            else if(command==121) {
                /* Replace the queued control wake with a capture boundary;
                 * this fault injection tests the frame callback's deferral. */
                if(ram_runtime->refresh_queued) {
                    unsigned packet[4]; REQUIRE(msgrcv(ram_capture_queue,packet,12,0,IPC_NOWAIT)==12);
                    ram_runtime->refresh_queued=0;
                }
                unsigned before=ram_runtime->refresh_requests;
                ram_runtime->captures=1; ram_refresh_callback(); ram_runtime->captures=0;
                REQUIRE(ram_runtime->refresh_requests==before&&!ram_runtime->refresh_queued);
                ram_refresh();
            }
            else if(command==122) {
                /* The loader now wakes the controller itself. Remove that
                 * pending notification to isolate the full-queue frame path. */
                if(ram_runtime->refresh_queued) {
                    unsigned packet[4]; REQUIRE(msgrcv(ram_capture_queue,packet,12,0,IPC_NOWAIT)==12);
                    ram_runtime->refresh_queued=0;
                }
                unsigned marker[4]={1,0,0,0},filled=0;
                while(!msgsnd(ram_capture_queue,marker,12,IPC_NOWAIT)) REQUIRE(++filled<4096);
                REQUIRE(filled&&errno==EAGAIN);
                filter_mode=15; unsigned before=ram_runtime->refresh_requests;
                ram_refresh_callback();
                REQUIRE(ram_runtime->refresh_requests==before+1&&!ram_runtime->refresh_queued&&ram_runtime->refresh_error);
                for(unsigned i=0;i<filled;++i) REQUIRE(msgrcv(ram_capture_queue,marker,12,0,IPC_NOWAIT)==12);
                ram_refresh_callback(); REQUIRE(ram_runtime->refresh_requests==before+1);
            }
            else if(command==123) {
                unsigned before=ram_topology_rebuilds;
                ram_memory_state=0; ram_refresh();
                REQUIRE(ram_runtime->refresh_pending&&ram_liveview[0x34/4]==20&&ram_topology_rebuilds==before);
                /* Pending work yields even in the B2Y Open->Prepare window. */
                ram_memory_state=1; ram_runtime->b2y_leases=1;
                unsigned packet[4]={1,0x02010034,0x39,0};
                REQUIRE(!msgsnd(ram_capture_queue,packet,12,IPC_NOWAIT));
                pthread_t controller; REQUIRE(!pthread_create(&controller,NULL,ram_refresh_controller,NULL));
                REQUIRE(!pthread_join(controller,NULL));
                REQUIRE(ram_runtime->refresh_pending&&ram_topology_rebuilds==before);
                ram_runtime->b2y_leases=0;
                REQUIRE(!msgsnd(ram_capture_queue,packet,12,IPC_NOWAIT));
                REQUIRE(!pthread_create(&controller,NULL,ram_refresh_controller,NULL));
                REQUIRE(!pthread_join(controller,NULL));
                REQUIRE(!ram_runtime->refresh_pending&&ram_topology_rebuilds==before+1&&ram_liveview[0x34/4]==7);
                printf("{\"type\":\"fixture_memory_resume\",\"stop_callbacks\":%u,\"waiting_observed\":%u,\"resumes\":%u,\"native_matcher_and_capture_defer\":true}\n",
                       ram_stop_callbacks,ram_waiting_observed,ram_runtime->refresh_resumes);
            }
            else if(command==100) {
                for(unsigned tries=0;;++tries) {
                    REQUIRE(tries<100); ram_off_prepare(1); ram_off_worker();
                    if(operations) break;
                    usleep(10000);
                }
                REQUIRE(native_opened==1&&ram_runtime->session.held==1&&
                        !ram_runtime->session.active&&!ram_runtime->frame_lifecycle_held);
                REQUIRE(ram_runtime->session.owner_tid!=(unsigned)syscall(224));
                unsigned powered=session_device_opens,refs=session_ref_opens;
                for(unsigned i=0;i<8;++i) { ram_off_prepare(1); ram_off_worker(); REQUIRE(operations==1); usleep(10000); }
                REQUIRE(session_device_opens==powered&&session_ref_opens==refs+8&&native_opened==1);
                for(unsigned i=0;i<4;++i) { int v=0; REQUIRE(!sem_getvalue(&native_semaphore[i],&v)&&v==1); }
                int v=0; REQUIRE(!sem_getvalue((void *)((unsigned char *)ram_b2y+0x44),&v)&&v==1);
                save("/tmp/smartfilter-ram.frame",output,FRAME);
            }
            else if(command==101) {
                for(unsigned tries=0;!native_opened;++tries) {
                    REQUIRE(tries<100); ram_off_prepare(1); ram_off_worker(); usleep(10000);
                }
                unsigned before=session_device_closes;
                REQUIRE(!ram_b2y_open_call());
                REQUIRE(!ram_runtime->session.held&&!ram_runtime->session.active&&native_opened==1);
                REQUIRE(session_device_closes>before&&ram_runtime->b2y_leases==1);
                REQUIRE(!ram_prepare());
                ram_off_prepare(1); ram_off_worker(); REQUIRE(!operations);
                REQUIRE(ram_finish()==1); ram_b2y_close_call();
                REQUIRE(!native_opened&&!ram_runtime->b2y_leases&&!ram_runtime->session.blockers[NX_IPS_CAPTURE]);
            }
            else if(command==102) {
                usleep(500000); REQUIRE(!native_opened&&!ram_runtime->session.held&&ram_runtime->session.idle_releases);
            }
            else if(command==103) {
                REQUIRE(!native_opened); unsigned before=session_device_opens;
                REQUIRE(!sem_trywait(&native_semaphore[2]));
                for(unsigned i=0;i<4;++i) { ram_off_prepare(1); ram_off_worker(); REQUIRE(!operations); usleep(10000); }
                REQUIRE(!native_opened&&session_device_opens==before);
                int v=0; REQUIRE(!sem_getvalue((void *)((unsigned char *)ram_b2y+0x44),&v)&&v==1);
                REQUIRE(!sem_post(&native_semaphore[2]));
            }
            else if(command==104) {
                REQUIRE(!ram_runtime->session.held&&!ram_runtime->session.active&&!native_opened);
                unsigned before=session_ref_opens;
                ram_off_prepare(1); ram_off_worker(); REQUIRE(operations==1);
                REQUIRE(!native_opened&&session_ref_opens==before+1);
                save("/tmp/smartfilter-ram.frame",output,FRAME);
            }
            else if(command==105) {
                printf("{\"type\":\"fixture_refs\",\"opens\":%u,\"closes\":%u,\"device_opens\":%u,\"device_closes\":%u,\"held\":%u}\n",
                       session_ref_opens,session_ref_closes,session_device_opens,session_device_closes,native_opened);
            }
            else if(command==106) {
                ram_close_error=1;
                REQUIRE(ram_b2y_open_call()==NX_IPS_UNCERTAIN);
                REQUIRE(ram_runtime->session.state==NX_IPS_FAILED&&ram_runtime->session.held==2);
                ram_b2y_close_call();
                int v=0; REQUIRE(!sem_getvalue((void *)((unsigned char *)ram_b2y+0x44),&v)&&v==1);
                REQUIRE(!ram_runtime->b2y_leases&&!native_opened); ram_close_error=0;
            }
            else if(command==107) {
                ram_off_prepare(1); ram_off_worker(); REQUIRE(!operations&&!memcmp(input,output,FRAME));
            }
            else if(command==112) {
                struct nx_sfr_config *cfg=(void *)((unsigned)h+h->config_offset);
                REQUIRE(cfg->enabled&&cfg->session_mode==1&&!cfg->timing_enabled&&!cfg->calltrace_enabled);
                REQUIRE(!ram_runtime->timing.enabled&&!ram_runtime->open_trace.enabled);
                printf("{\"type\":\"fixture_fast_preview\",\"session\":%u,\"timing\":%u,\"calltrace\":%u}\n",
                       cfg->session_mode,cfg->timing_enabled,cfg->calltrace_enabled);
                REQUIRE(!fflush(stdout));
            }
            else if(command==111) {
                int (*device_open)(const char *,int,... )=(void *)ram_device_open_original;
                for(unsigned i=0;i<800;++i) {
                    int fd=device_open("/dev/null",O_RDONLY,0); REQUIRE(fd>=0&&!close(fd));
                    usleep(5000);
                }
            }
            else if(command==110) {
                struct nx_sfr_config *cfg=(void *)((unsigned)h+h->config_offset);
                REQUIRE(cfg->original[NX_SFR_DEVICE_OPEN]==ram_device_open_original);
                REQUIRE(*ram_slots[NX_SFR_DEVICE_OPEN]==(unsigned)h+h->entry[NX_SFR_DEVICE_OPEN]);
                int (*device_open)(const char *,int,... )=(void *)*ram_slots[NX_SFR_DEVICE_OPEN];
                int fd=device_open("/dev/null",O_RDONLY,0); REQUIRE(fd>=0&&!close(fd));
                printf("{\"type\":\"fixture_open_binding\",\"owner\":\"%s\",\"original\":%u,\"forwarded_original\":%u,\"enabled\":%u,\"native_open_succeeded\":true}\n",
                       ram_device_open_owner,ram_device_open_original,cfg->original[NX_SFR_DEVICE_OPEN],cfg->enabled);
                REQUIRE(!fflush(stdout));
            }
            else if(command==108||command==109) {
                /* Only the native terminal callbacks are doubles. Execute the
                 * installed stop/start adapters and real session owner/drain. */
                struct nx_sfr_config *cfg=(void *)((unsigned)h+h->config_offset);
                unsigned stop=cfg->original[NX_SFR_STOP],start=cfg->original[NX_SFR_START];
                cfg->original[NX_SFR_STOP]=(unsigned)ram_stop_double;
                cfg->original[NX_SFR_START]=(unsigned)ram_start_double;
                int (*stop_call)(void *,unsigned,unsigned)=(void *)*ram_slots[NX_SFR_STOP];
                int (*start_call)(void *,int,unsigned)=(void *)*ram_slots[NX_SFR_START];
                unsigned before=ram_native_stops;
                if(command==108) {
                    REQUIRE(ram_runtime->session.held==2);
                    REQUIRE(stop_call(ram_b2y,17,1)==NX_IPS_UNCERTAIN);
                    REQUIRE(ram_native_stops==before&&ram_runtime->stream_stopped==1);
                } else {
                    REQUIRE(native_opened==1);
                    REQUIRE(stop_call(ram_b2y,17,1)==41);
                    REQUIRE(ram_native_stops==before+1&&!ram_runtime->session.held&&!native_opened);
                    REQUIRE(ram_runtime->stream_stopped==1);
                    REQUIRE(start_call(ram_b2y,17,60)==1&&ram_native_starts==1);
                    REQUIRE(!ram_runtime->stream_stopped&&!native_opened);
                    REQUIRE(!ram_runtime->session.blockers[NX_IPS_STREAM]);
                }
                cfg->original[NX_SFR_STOP]=stop; cfg->original[NX_SFR_START]=start;
            }
            else
#endif
            if(command==10) { ram_off_prepare(1); ram_off_worker(); }
            else if(command==11) ram_off_prepare(1);
            else if(command==12) ram_off_worker();
            else if(command==13) { ram_path_probe(NX_SFR_CREATE); ram_path_probe(NX_SFR_B2Y); ram_path_probe(NX_SFR_LDC); }
            else if(command==14) { filter_mode=15; REQUIRE(ram_live_mode()==15); filter_mode=1; REQUIRE(ram_live_mode()==1); filter_mode=15; }
            else if(command==15) {
                ram_runtime->initialized=3; ram_off_prepare(1); ram_off_worker();
            }
            else if(command==16) { ram_runtime->initialized=2; ram_runtime->s2_fault=0; }
            else if(command==17) {
                for(unsigned i=0;i<NX_SF_CONTEXTS;++i) ram_runtime->bridge.contexts[i].tid=0xfeed0000+i;
                ram_off_prepare(1); ram_off_worker();
                for(unsigned i=0;i<NX_SF_CONTEXTS;++i) ram_runtime->bridge.contexts[i].tid=0;
            }
            else if(command==18) {
                for(unsigned i=0;i<NX_SF_CONTEXTS;++i) ram_runtime->origins[i].tid=0xfeed0000+i;
                ram_off_prepare(1); ram_off_worker();
                for(unsigned i=0;i<NX_SF_CONTEXTS;++i) ram_runtime->origins[i].tid=0;
            }
            else if(command==19) {
                ram_off_prepare(0); node[0x6c/4]=0;
                REQUIRE(execute_node(node,data+2,data,71)==1&&queued==1); ram_off_worker();
            }
            else if(command==20) {
                reset(); filter_mode=1; REQUIRE(prepare_node(node)==1&&node[0x6c/4]==(unsigned)work);
                REQUIRE(ram_live_mode()==1); filter_mode=15;
            }
            else if(command==21||command==22) {
                struct nx_sfr_config *cfg=(void *)((unsigned)h+h->config_offset);
                REQUIRE(cfg->work_base&&cfg->work_size==NX_SFR_WORK_BYTES);
                REQUIRE(!mprotect((void *)cfg->work_base,cfg->work_size,command==21?PROT_READ:PROT_READ|PROT_WRITE));
            }
            else if(command==23) ram_runtime->initialized=3;
            else if(command==24) ram_runtime->initialized=0;
            else if(command==25) {
                ram_off_prepare(1); node[0x6c/4]=(unsigned)work; ram_off_worker();
            }
            else if(command==26) {
                ram_runtime->synced_generation=0; ram_runtime->bridge.gate=2;
                ram_off_prepare(1); ram_off_worker(); ram_runtime->bridge.gate=0;
            }
            else if(command==27||command==44||command==45||command==46) {
                ram_pending_stage=command==44?1:command==45?2:command==46?3:0;
                ram_block=1; render_entered=render_continue=0;
                REQUIRE(!pthread_create(&ram_async_frame,NULL,ram_frame_thread,NULL));
                REQUIRE(!pthread_mutex_lock(&render_mutex));
                while(!render_entered) REQUIRE(!pthread_cond_wait(&render_condition,&render_mutex));
                REQUIRE(!pthread_mutex_unlock(&render_mutex));
            }
            else if(command==28) {
                REQUIRE(!pthread_mutex_lock(&render_mutex)); render_continue=1;
                REQUIRE(!pthread_cond_signal(&render_condition)); REQUIRE(!pthread_mutex_unlock(&render_mutex));
                REQUIRE(!pthread_join(ram_async_frame,NULL)); ram_block=ram_pending_stage=0;
            }
            else if(command==30) ram_runtime->timing.next_frame=NX_SFT_FRAMES;
            else if(command==31) {
                for(unsigned i=0;i<NX_SFT_SCOPES;++i) ram_runtime->timing.scopes[i].tid=0xfeed0000+i;
            }
            else if(command==32) ram_runtime->timing.frozen=1;
            else if(command==33) {
                struct nx_sfr_config *cfg=(void *)((unsigned)h+h->config_offset);
                cfg->timing_enabled=0; ram_off_prepare(1); ram_off_worker(); cfg->timing_enabled=1;
            }
            else if(command==34) result=(int)ram_runtime->timing.epoch;
            else if(command==35) ram_runtime->control_pause=1;
            else if(command==36) { result=(int)ram_runtime->control_pause; ram_runtime->control_pause=0; }
            else if(command==40) {
                REQUIRE(!ram_runtime->backend_active&&!ram_runtime->ipcs.held&&!native_opened);
                ram_runtime->bridge.fault=ram_runtime->bridge.paused=0;
                ram_runtime->bridge.stats.last_error=0; ram_runtime->init_error=0;
                ram_reset_error=ram_close_error=ram_post_error=0;
            }
            else if(command==41) ram_reset_error=1;
            else if(command==42) ram_close_error=1;
            else if(command==43) ram_post_error=1;
            else if(command==6) ram_capture_test();
            else if(command==8) ram_start_async_frame();
            else if(command==3) result=ram_prepare();
            else if(command==4) result=ram_finish();
            else {
                if(command==9) {
                    REQUIRE(!pthread_join(ram_async_frame,NULL)); REQUIRE(!pthread_join(ram_async_release,NULL));
                    ram_block=0;
                }
                char name[48]; snprintf(name,sizeof name,"ram-%u",seq);
                injected_error=command==5;
                if(command==7) REQUIRE(!sem_trywait(&native_semaphore[2]));
                slot_frame(name,ram_frame_mode()); save("/tmp/smartfilter-ram.frame",output,FRAME);
                if(command==7) REQUIRE(!sem_post(&native_semaphore[2]));
                injected_error=0;
            }
        }
        FILE *response=fopen("/tmp/smartfilter-ram.response.tmp","w"); REQUIRE(response);
        fprintf(response,"%u %d %u %u\n",seq,result,operations,callbacks); REQUIRE(!fclose(response));
        REQUIRE(!rename("/tmp/smartfilter-ram.response.tmp","/tmp/smartfilter-ram.response"));
    }
    native_semaphores_free();
    return 0;
}
