/* Stock worker plus actual ptrace-installed RAM hooks. Device/cache and
 * B2Y lease construction are explicit doubles; the camera libraries are intact. */
#define _GNU_SOURCE
#define NX_SF_RAM_FIXTURE
#define main unused_boundary_main
#include "smartfilter_fixture.c"
#undef main
#include <stddef.h>
#include "smartfilter_ram.h"
#include "smartfilter_stock.h"
extern int native_fscanf(FILE *,const char *,...) __asm__("fscanf");
#define fscanf native_fscanf

struct nx_sfr_fixture_bindings nx_sfr_fixture_bindings;
static unsigned *ram_slots[NX_SFR_SLOTS], ram_b2y[0x5100/4];
static unsigned ram_capture_base;
static unsigned ram_capture_callback_count;
static struct nx_sfr_runtime *ram_runtime;
static int ram_block;
static pthread_t ram_async_frame,ram_async_release;

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
static void ram_operation_enter(void) {
    if(!ram_block) return;
    REQUIRE(!pthread_mutex_lock(&render_mutex)); render_entered=1;
    REQUIRE(!pthread_cond_signal(&render_condition));
    while(!render_continue) REQUIRE(!pthread_cond_wait(&render_condition,&render_mutex));
    REQUIRE(!pthread_mutex_unlock(&render_mutex));
}
static void *ram_frame_thread(void *unused) { (void)unused; slot_frame("ram-inflight",NX_SFR_LIVEVIEW_MODE); return NULL; }
static void *ram_capture_thread(void *unused) { (void)unused; REQUIRE(!ram_prepare()); return NULL; }
static void *ram_release_on_control(void *unused) {
    (void)unused; unsigned tries=0;
    while(!__atomic_load_n(&ram_runtime->control_pause,__ATOMIC_ACQUIRE)) { REQUIRE(++tries<3000); usleep(1000); }
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
    REQUIRE(!pthread_create(&capture_thread,NULL,ram_capture_thread,NULL));
    unsigned tries=0;
    while(!__atomic_load_n(&ram_runtime->capture_waits,__ATOMIC_ACQUIRE)) { REQUIRE(++tries<3000); usleep(1000); }
    REQUIRE(ram_runtime->captures==1&&ram_runtime->backend_active==1&&ram_capture_callback_count==callbacks_before);
    REQUIRE(!pthread_mutex_lock(&render_mutex)); render_continue=1;
    REQUIRE(!pthread_cond_signal(&render_condition)); REQUIRE(!pthread_mutex_unlock(&render_mutex));
    REQUIRE(!pthread_join(frame_thread,NULL)); REQUIRE(!pthread_join(capture_thread,NULL));
    ram_block=0;
    REQUIRE(ram_capture_callback_count==callbacks_before+1&&ram_runtime->backend_active==0&&ram_runtime->captures==1);
    slot_frame("ram-capture-held",NX_SFR_LIVEVIEW_MODE);
    REQUIRE(operations==0&&!memcmp(input,output,FRAME));
    REQUIRE(ram_finish()==1&&ram_runtime->captures==0);
    slot_frame("ram-after-capture",NX_SFR_LIVEVIEW_MODE); REQUIRE(operations==1);
}
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
    nx_sfr_fixture_bindings.magic=NX_SFR_MAGIC; nx_sfr_fixture_bindings.size=sizeof nx_sfr_fixture_bindings;
    nx_sfr_fixture_bindings.b2y_instance=ram_b2y_instance;
    nx_sfr_fixture_bindings.get_semaphore=ram_get_semaphore;
    native_active=1; backend=1; use_slot_bridge=0; operation_enter=ram_operation_enter;
    load("/mnt/mmc/input.yuv",input,FRAME); memcpy(original,input,FRAME);
    load("/mnt/mmc/identity.d4lut",cached_identity,sizeof cached_identity);
    Dl_info info; REQUIRE(dladdr(stock_preview,&info)&&info.dli_fbase);
    ram_capture_base=(unsigned)info.dli_fbase;
    for(unsigned i=0;i<NX_SFR_SLOTS;++i) ram_slots[i]=(void *)((unsigned)info.dli_fbase+stock_slots[i].slot);
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
            if(command==6) ram_capture_test();
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
                slot_frame(name,NX_SFR_LIVEVIEW_MODE); save("/tmp/smartfilter-ram.frame",output,FRAME);
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
