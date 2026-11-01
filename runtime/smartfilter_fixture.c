/* Isolated ARM fixture. Only the queue, environment and pixel backend are doubles.
 * Stock SmartFilter worker, image construction/getters and ADE YCC wrapper execute.
 * No device is opened; the software RGB/YCC model is not an ISP emulator. */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <dlfcn.h>
#include <setjmp.h>
#include <unistd.h>
#include <sys/wait.h>
#include <signal.h>
#include <pthread.h>
#include <sys/syscall.h>
#include <semaphore.h>
#include "smartfilter_lut_bridge.h"
#include "smartfilter_ipcs.h"

#ifdef NX_SF_RAM_FIXTURE
#define PREVIEW_BRIDGE_NAME "nx_fixture_unused_preview_bridge"
#define FILTER_BRIDGE_NAME "nx_fixture_unused_filter_bridge"
#else
#define PREVIEW_BRIDGE_NAME "_ZN24CLiveviewNodeSmartFilter11smartFilterEP9CCapImagePv22E_LIVEVIEW_SMARTFILTER"
#define FILTER_BRIDGE_NAME "_Z26ADE_SmartFilter_YccProcess9eADE_MODEP9ADE_Image"
#endif

#define W 64U
#define H 48U
#define PIXELS (W * H)
#define FRAME (PIXELS * 3U / 2U)
#define REQUIRE(x) do { if (!(x)) { fprintf(stderr, "check line %d: %s\n", __LINE__, #x); exit(90); } } while (0)
#define EXPORTED(type, name, symbol, args) type name args __asm__(symbol); type name args

static unsigned node[64], common[64], buffer_object[64];
static unsigned data[0x140/4], buffer_lock[2], node_status[2];
static unsigned message[4], queued, received, callbacks, operations, bridges, flushes;
static unsigned work_available = 1, filter_mode, processing, backend, injected_error;
static unsigned char input[FRAME], output[FRAME], original[FRAME], work[32768];
/* S2 probe: the stock layout used while the filter is off has no 0x1000f area.
 * This buffer stands in for the RAM hook's managed work area, sized from the
 * stock CAPTURE_NORMAL_EF entry (0x168000). */
#define S2_WORK_SIZE 0x168000U
static unsigned char s2_work[S2_WORK_SIZE];
static unsigned s2_managed, s2_lr_filter;
static uintptr_t s2_execute_start, s2_execute_end;
static uint16_t table[3][730];
static struct nx_sf_ipcs native_ipcs;
static sem_t native_semaphore[4];
static unsigned native_opened, native_init_calls, native_nodes_updated;
static unsigned native_parameter_list[8], native_parameter_nodes[3][8];
static unsigned native_processor[40], native_list_vptr[3], native_node_vptr[2];
static void (*native_processor_init)(void *,int), (*native_processor_process)(void *,int);
static void (*native_list_update)(void *,void *), (*native_list_init)(void *), (*native_node_reset)(void *);
static unsigned char *native_switches[6];
static int native_active;
static jmp_buf worker_done;
static int (*ade_ycc)(void *);
static void (*reg_flush)(void *, void (*)(unsigned, unsigned));
static int (*flush_cache)(void *, unsigned, unsigned);
static unsigned char *rgb3d_switch;
static const char *case_name;
static int use_slot_bridge;
static void record_slot_flush(unsigned,unsigned);
static struct nx_sf_bridge slot_bridge;
static void (*stock_preview)(void *, void *, void *, unsigned);
static unsigned (*ade_width)(void *), (*ade_height)(void *), (*ade_stride)(void *);
static unsigned (*ade_address)(void *, unsigned);
static int (*prepare_node)(void *);
static int (*execute_node)(void *, void *, void *, unsigned);
static void (*run_worker)(void *);
static void (*operation_enter)(void);

EXPORTED(void, preview_bridge,
    PREVIEW_BRIDGE_NAME,
    (void *self, void *image, void *data_ptr, unsigned mode)) {
    if (use_slot_bridge) { nx_sf_preview(&slot_bridge, self, image, data_ptr, mode); return; }
    stock_preview(self, image, data_ptr, mode);
}

EXPORTED(void *, common_instance, "_ZN15CLiveviewCommon11GetInstanceEv", (void)) { return common; }
EXPORTED(void, common_print, "_ZN15CLiveviewCommon13LiveviewPrintEiPKcz", (void *self, int level, const char *text, ...)) {
    (void)self; (void)level; (void)text;
}
/* S2 probe: forge mode 0 only for calls whose return address lies inside the
 * stock execute function, exactly like the RAM hook filters its PLT slot. */
EXPORTED(int, common_filter, "_ZN15CLiveviewCommon18GetSmartFilterModeEv", (void *self)) {
    (void)self;
    if (s2_lr_filter) {
        void *lr;
        __asm__ volatile("mov %0, lr" : "=r"(lr));
        uintptr_t pc = (uintptr_t)lr;
        if (pc >= s2_execute_start && pc < s2_execute_end) return 0;
    }
    return filter_mode;
}
EXPORTED(int, common_stream, "_ZN15CLiveviewCommon13GetStreamModeEv", (void *self)) { (void)self; return 0; }
EXPORTED(int, common_aspect, "_ZN15CLiveviewCommon14GetAspectRatioEv", (void *self)) { (void)self; return 0; }
EXPORTED(void *, buffer_instance, "_ZN15CLiveviewBuffer11GetInstanceEv", (void)) { return buffer_object; }
EXPORTED(unsigned, buffer_address, "_ZN15CLiveviewBuffer14GetVirtAddressEii", (void *self, int key, int index)) {
    REQUIRE(self == buffer_object && key == 0x1000f && index == 0);
    return work_available ? (unsigned)(uintptr_t)work : 0;
}
/* S2 probe: the missing 0x1000f area is replaced before getBaseAddress runs,
 * so the stock assertion on a zero GetAddress result is never reached. */
EXPORTED(unsigned, work_address, "_ZN24CLiveviewNodeSmartFilter14getWorkAddressE15E_LIVEVIEW_MODE",
         (void *self, int stream_mode)) {
    (void)self; (void)stream_mode;
    if (s2_managed && filter_mode >= 15) return (unsigned)(uintptr_t)s2_work;
    return work_available ? (unsigned)(uintptr_t)work : 0;
}
EXPORTED(void *, filter_instance, "_ZN24CLiveviewNodeSmartFilter11GetInstanceEv", (void)) { return node; }
EXPORTED(int, send_message, "_ZN16CLiveviewMessage7sendMsgERNS_20stLIVE_THREAD_MSG_IFE", (void *self, void *msg)) {
    REQUIRE(self == (char *)node + 0x20 && !queued);
    memcpy(message, msg, sizeof message); queued = 1; return 1;
}
EXPORTED(int, receive_message, "_ZN16CLiveviewMessage10receiveMsgERNS_20stLIVE_THREAD_MSG_IFE", (void *self, void *msg)) {
    REQUIRE(self == (char *)node + 0x20);
    if (received++) longjmp(worker_done, 1);
    REQUIRE(queued && message[0] == 0xddd30001U);
    memcpy(msg, message, sizeof message); queued = 0; return 1;
}

static unsigned byte_round(double x) {
    return x <= 0 ? 0 : x >= 255 ? 255 : (unsigned)(x + .5);
}
static double unit(double x) { return x < 0 ? 0 : x > 1 ? 1 : x; }
static void sample_lut(const double in[3], double out[3]) {
    unsigned lo[3]; double fraction[3];
    for (unsigned c = 0; c < 3; ++c) {
        double x = unit(in[c]) * 8.; lo[c] = x >= 8 ? 7 : (unsigned)x;
        fraction[c] = x - lo[c]; out[c] = 0;
    }
    for (unsigned r = 0; r < 2; ++r) for (unsigned g = 0; g < 2; ++g) for (unsigned b = 0; b < 2; ++b) {
        unsigned i = ((lo[0]+r)*9 + lo[1]+g)*9 + lo[2]+b;
        double weight = (r ? fraction[0] : 1-fraction[0]) *
            (g ? fraction[1] : 1-fraction[1]) * (b ? fraction[2] : 1-fraction[2]);
        for (unsigned c = 0; c < 3; ++c) out[c] += weight * table[c][i] / 1023.;
    }
}

/* Explicit replacement at the UDD boundary. Addresses are process virtual RAM.
 * BT.601 full-range and trilinear interpolation are reference assumptions. */
int ipcs_ycc_correction_to_ycc_operation(unsigned *params) {
    ++operations;
    REQUIRE(processing && buffer_lock[0] == 1 && node_status[0] == 1);
    REQUIRE(((unsigned char *)node)[0x70] == 1);
    REQUIRE(params[0] == W && params[1] == H && params[2] == 1 && params[3] == 0);
    REQUIRE(params[6] == (unsigned)(uintptr_t)input && params[8] == (unsigned)(uintptr_t)(input+PIXELS));
    REQUIRE(params[10] == W && params[11] == W);
    REQUIRE(params[16] == W && params[17] == H && params[18] == 1 && params[19] == 0);
    REQUIRE(params[22] == (unsigned)(uintptr_t)output && params[24] == (unsigned)(uintptr_t)(output+PIXELS));
    REQUIRE(params[26] == W && params[27] == W);
    REQUIRE(params[0x1a0/4] == (backend != 0));
    char path[96]; snprintf(path, sizeof path, "%s.operation.bin", case_name);
    FILE *record = fopen(path, "wb"); REQUIRE(record);
    REQUIRE(fwrite(params, 1, 0x210, record) == 0x210 && !fclose(record));
    if(operation_enter) operation_enter();
    if (injected_error) return -17;
    if (!backend) { memcpy(output, input, FRAME); return 0; }
    for (unsigned y = 0; y < H; y += 2) for (unsigned x = 0; x < W; x += 2) {
        double chroma[2] = {0, 0};
        double cb = input[PIXELS + y/2*W+x]-128.;
        double cr = input[PIXELS + y/2*W+x+1]-128.;
        for (unsigned dy = 0; dy < 2; ++dy) for (unsigned dx = 0; dx < 2; ++dx) {
            unsigned at = (y+dy)*W+x+dx; double luma = input[at];
            double rgb[3] = {(luma+1.402*cr)/255., (luma-.344136*cb-.714136*cr)/255., (luma+1.772*cb)/255.};
            double result[3]; sample_lut(rgb, result);
            double r = result[0]*255., g = result[1]*255., b = result[2]*255.;
            output[at] = byte_round(.299*r+.587*g+.114*b);
            chroma[0] += -.168736*r-.331264*g+.5*b+128.;
            chroma[1] += .5*r-.418688*g-.081312*b+128.;
        }
        output[PIXELS+y/2*W+x] = byte_round(chroma[0]/4.);
        output[PIXELS+y/2*W+x+1] = byte_round(chroma[1]/4.);
    }
    return 0;
}

static void record_flush(unsigned address, unsigned length) {
    REQUIRE(address == (unsigned)(uintptr_t)input && length == FRAME); ++flushes;
}

/* This bridge is fixture-only: it demonstrates where a future RAM adapter would
 * connect, without pretending the stock filter already calls ADE YCC correction. */
EXPORTED(int, filter_bridge, FILTER_BRIDGE_NAME, (unsigned effect, void *image)) {
    if (use_slot_bridge) {
        ++bridges;
        if(native_active) reg_flush(image,record_slot_flush);
        return nx_sf_effect(&slot_bridge, effect, image);
    }
    REQUIRE(effect == 10 && processing); ++bridges;
    reg_flush(image, record_flush);
    REQUIRE(!flush_cache(image, (unsigned)(uintptr_t)input, FRAME));
    unsigned before = flushes;
    int result = ade_ycc(image);
    /* The genuine ADE wrapper does not invoke the registered cache callback. */
    REQUIRE(flushes == before);
    return result;
}

static void completed(void) {
    REQUIRE(processing && !buffer_lock[0] && !node_status[0] && !((unsigned char *)node)[0x70]);
    ++callbacks;
}
static void *symbol(void *handle, const char *name) {
    void *value = dlsym(handle, name);
    if (!value) { fprintf(stderr, "symbol %s: %s\n", name, dlerror()); exit(91); }
    return value;
}
static void image(unsigned *at, unsigned char *pixels) {
    memset(at, 0, 0x90);
    at[0] = at[2] = W; at[1] = at[3] = H;
    at[0x58/4] = 6;
    at[0x6c/4] = (unsigned)(uintptr_t)pixels;
    at[0x70/4] = (unsigned)(uintptr_t)(pixels+W);
    at[0x74/4] = (unsigned)(uintptr_t)(pixels+PIXELS);
    at[0x78/4] = (unsigned)(uintptr_t)(pixels+PIXELS+W);
}
static void reset(void) {
    memset(node, 0, sizeof node); memset(data, 0, sizeof data);
    buffer_lock[0] = node_status[0] = 0;
    buffer_lock[1] = node_status[1] = 0x1234eeff;
    queued = received = callbacks = operations = bridges = flushes = 0;
    image(data+8/4, input); image(data+0x98/4, output);
    data[0x128/4] = (unsigned)(uintptr_t)completed;
    data[0x130/4] = (unsigned)(uintptr_t)node_status;
    data[0x134/4] = (unsigned)(uintptr_t)buffer_lock;
    memset(output, 0xa5, FRAME); work_available = 1; filter_mode = 0;
}
static void load(const char *path, void *target, unsigned size) {
    FILE *in = fopen(path, "rb"); REQUIRE(in);
    REQUIRE(fread(target, 1, size, in) == size && fgetc(in) == EOF && !fclose(in));
}
static void save(const char *name, const void *source, unsigned size) {
    FILE *out = fopen(name, "wb"); REQUIRE(out);
    REQUIRE(fwrite(source, 1, size, out) == size && !fclose(out));
}

/* The transaction backend below is an isolated test double. Only ADE image
 * getters, cache callback dispatch, and YCC parameter construction are stock.
 * It does not implement physical IPCS leasing, DMA completion or state restore. */
static unsigned backend_owned, backend_busy, backend_restores, backend_releases;
static unsigned backend_uploads, backend_begin_capture, stock_effect_calls;
static unsigned saved_switch, restore_failure, stock_marker_only, stock_last_effect;
static void *stock_last_image;
static uint16_t cached_identity[3][730], cached_monochrome[3][730], saved_table[3][730];
static pthread_mutex_t render_mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t render_condition = PTHREAD_COND_INITIALIZER;
static unsigned block_render, render_entered, render_continue;

static unsigned thread_id(void *opaque) { (void)opaque; return (unsigned)syscall(SYS_gettid); }
static int copy_frame(void *opaque, void *image_ptr) {
    (void)opaque;
    if (ade_width(image_ptr) != W || ade_height(image_ptr) != H || ade_stride(image_ptr) != W ||
        ade_address(image_ptr, 1) != (unsigned)(uintptr_t)input ||
        ade_address(image_ptr, 3) != (unsigned)(uintptr_t)(input+PIXELS) ||
        ade_address(image_ptr, 6) != (unsigned)(uintptr_t)output ||
        ade_address(image_ptr, 8) != (unsigned)(uintptr_t)(output+PIXELS)) return -1;
    memcpy(output, input, FRAME);
    return 0;
}

/* The device calls below replace the UDD boundary. Real ADE init and IQ
 * parameter-list code execute; no physical device or register is accessed. */
int ipcs_open(void) { REQUIRE(native_active && !native_opened); native_opened=1; return 0; }
int ipcs_close(void) { REQUIRE(native_active && native_opened); native_opened=0; return 0; }
int ipcs_init(void) { REQUIRE(native_active && native_opened); ++native_init_calls; return 0; }
void ipcs_tone_lut(void *p) { REQUIRE(native_active && p); }
void ipcs_ycc2rgb_param(void *p) { REQUIRE(native_active && p); }
void ipcs_color_correction_param(void *p) { REQUIRE(native_active && p); }
void ipcs_rgb2ycc_param(void *p) { REQUIRE(native_active && p); }
void ipcs_edge_enhancement_param(void *p) { REQUIRE(native_active && p); }
void ipcs_saturation_correction_param(void *p) { REQUIRE(native_active && p); }
void ipcs_pcse_gray_th_param(void *p) { REQUIRE(p); }
void ipcs_3d_lut(unsigned channel,const uint16_t *p) {
    REQUIRE(native_active && native_opened && channel<3 && p);
    memcpy(table[channel],p,sizeof table[channel]);
}
EXPORTED(void *, iq_repeater_instance, "_ZN15CIQDataRepeater11GetInstanceEv", (void)) { return common; }
EXPORTED(void, iq_repeater_parameter, "_ZN15CIQDataRepeater12GetParameterEiPi", (void *self,int key,int *value)) {
    REQUIRE(native_active && self==common && key==0x45); *value=0;
}
static void native_update_parameter(void *self,void *info) {
    unsigned *n=self; REQUIRE(info==common && n[1]==~0U);
    unsigned channel=n[6]; REQUIRE(channel<3);
    ipcs_3d_lut(channel,cached_identity[channel]); n[1]=0; ++native_nodes_updated;
}
static void *native_sys_info(void *self) { REQUIRE(self==native_parameter_list); return common; }
static void native_iq_initialize(int mode) {
    REQUIRE(mode==0); native_processor_init(native_processor,mode);
}
static void native_iq_process(int mode) {
    REQUIRE(mode==0); native_processor_process(native_processor,mode);
}
static int native_passthrough(void *opaque,void *image_ptr) {
    (void)opaque; return nx_sf_ipcs_passthrough(&native_ipcs,image_ptr);
}
static int stock_effect(unsigned effect, void *image_ptr) {
    ++stock_effect_calls; stock_last_effect = effect; stock_last_image = image_ptr;
    return stock_marker_only ? -29 : copy_frame(NULL, image_ptr);
}
static int begin_backend(void *opaque) {
    (void)opaque;
    if (backend_busy) return 1;
    REQUIRE(!backend_owned); backend_owned = 1;
    saved_switch = *rgb3d_switch;
    memcpy(saved_table, table, sizeof table);
    if (backend_begin_capture) REQUIRE(nx_sf_capture_begin(&slot_bridge) == NX_SF_BUSY);
    return 0;
}
static void record_slot_flush(unsigned address, unsigned length) {
    REQUIRE((address == (unsigned)(uintptr_t)input || address == (unsigned)(uintptr_t)output) && length == FRAME);
    ++flushes;
}
static int render_backend(void *opaque, void *image_ptr, const unsigned short *selected_table) {
    (void)opaque;
    REQUIRE(backend_owned); ++backend_uploads;
    memcpy(table, selected_table, sizeof table);
    *rgb3d_switch = 1;
    reg_flush(image_ptr, record_slot_flush);
    REQUIRE(!flush_cache(image_ptr, (unsigned)(uintptr_t)input, FRAME));
    if (block_render) {
        REQUIRE(!pthread_mutex_lock(&render_mutex));
        render_entered = 1;
        REQUIRE(!pthread_cond_signal(&render_condition));
        while (!render_continue) REQUIRE(!pthread_cond_wait(&render_condition, &render_mutex));
        REQUIRE(!pthread_mutex_unlock(&render_mutex));
    }
    int result = ade_ycc(image_ptr);
    REQUIRE(!flush_cache(image_ptr, (unsigned)(uintptr_t)output, FRAME));
    return result;
}
static int restore_backend(void *opaque) {
    (void)opaque; REQUIRE(backend_owned); ++backend_restores;
    if (restore_failure) return -32;
    *rgb3d_switch = saved_switch;
    memcpy(table, saved_table, sizeof table);
    return 0;
}
static void end_backend(void *opaque) {
    (void)opaque; REQUIRE(backend_owned); backend_owned = 0; ++backend_releases;
    if (!restore_failure) REQUIRE(*rgb3d_switch == saved_switch && !memcmp(table, saved_table, sizeof table));
}
static void initialize_slot(void) {
    struct nx_sf_backend b = {NULL, thread_id, stock_preview, stock_effect,
        begin_backend, render_backend, restore_backend, end_backend, copy_frame};
    REQUIRE(nx_sf_init(&slot_bridge, &b, 0) == NX_SF_OK);
    REQUIRE(nx_sf_enable(&slot_bridge, 1) == NX_SF_OK);
    use_slot_bridge = 1; backend = 1;
}
static void slot_frame(const char *name, unsigned mode) {
    reset(); filter_mode = mode; case_name = name;
    REQUIRE(prepare_node(node) == 1 && execute_node(node, data+2, data, 71) == 1);
    processing = 1;
    if (!setjmp(worker_done)) run_worker(node);
    processing = 0;
    REQUIRE(callbacks == 1 && !buffer_lock[0] && !node_status[0] && !((unsigned char *)node)[0x70]);
    REQUIRE(!memcmp(input, original, FRAME));
    REQUIRE(!backend_owned && nx_sf_quiescent(&slot_bridge));
}
static void *blocked_frame(void *unused) { (void)unused; slot_frame("slot-inflight", 0); return NULL; }

static void check_slot_bridge(void) {
    initialize_slot();
    REQUIRE(nx_sf_select(&slot_bridge, "identity", 1, 100, &cached_identity[0][0], sizeof cached_identity) == NX_SF_OK);
    REQUIRE(slot_bridge.stats.selected_generation == 1 && !slot_bridge.stats.applied_generation);
    *rgb3d_switch = 0;
    slot_frame("slot-identity", 0);
    REQUIRE(slot_bridge.stats.applied_generation == 1 && !*rgb3d_switch && flushes == 2);
    save("slot-identity.yuv", output, FRAME);
    REQUIRE(nx_sf_select(&slot_bridge, "monochrome", 2, 100, &cached_monochrome[0][0], sizeof cached_monochrome) == NX_SF_OK);
    REQUIRE(slot_bridge.stats.applied_generation == 1);
    slot_frame("slot-monochrome", 0); save("slot-monochrome.yuv", output, FRAME);
    REQUIRE(slot_bridge.stats.applied_generation == 2);
    REQUIRE(nx_sf_select(&slot_bridge, "monochrome", 3, 50, &cached_monochrome[0][0], sizeof cached_monochrome) == NX_SF_OK);
    slot_frame("slot-half", 0); save("slot-half.yuv", output, FRAME);
    REQUIRE(nx_sf_select(&slot_bridge, "stock", 4, 0, NULL, 0) == NX_SF_OK);
    unsigned uploads = backend_uploads;
    slot_frame("slot-zero", 0);
    REQUIRE(!operations && backend_uploads == uploads && slot_bridge.stats.applied_generation == 4 && !memcmp(input, output, FRAME));
    uint16_t invalid[3][730]; memcpy(invalid, cached_identity, sizeof invalid); invalid[0][0] = 1024;
    REQUIRE(nx_sf_select(&slot_bridge, "invalid", 5, 100, &invalid[0][0], sizeof invalid) == NX_SF_INVALID);
    REQUIRE(slot_bridge.stats.selected_generation == 4);
    REQUIRE(nx_sf_select(&slot_bridge, "identity", 5, 100, &cached_identity[0][0], sizeof cached_identity) == NX_SF_OK);
    slot_frame("slot-return", 0); save("slot-return.yuv", output, FRAME);
    puts("{\"check\":\"single_slot_ram_selection_strength_and_generation\",\"passed\":true}");

    unsigned stock_before = stock_effect_calls;
    slot_frame("other-slot", 1);
    REQUIRE(stock_effect_calls == stock_before+1 && stock_last_effect == 1 && !operations);
    stock_marker_only = 1;
    REQUIRE(nx_sf_effect(&slot_bridge, 7, (void *)0x1234) == -29);
    REQUIRE(stock_last_effect == 7 && stock_last_image == (void *)0x1234);
    stock_marker_only = 0;
    REQUIRE(nx_sf_enable(&slot_bridge, 0) == NX_SF_OK);
    stock_before = stock_effect_calls; slot_frame("slot-disabled", 0);
    REQUIRE(stock_effect_calls == stock_before+1 && !operations);
    REQUIRE(nx_sf_enable(&slot_bridge, 1) == NX_SF_OK);
    puts("{\"check\":\"other_slot_and_out_of_preview_effect_delegation\",\"passed\":true}");

    backend_busy = 1; uploads = backend_uploads;
    slot_frame("slot-backend-busy", 0);
    REQUIRE(!operations && uploads == backend_uploads && !memcmp(input, output, FRAME));
    backend_busy = 0; injected_error = 1;
    unsigned restored = backend_restores, released = backend_releases;
    slot_frame("slot-operation-error", 0);
    REQUIRE(operations == 1 && backend_restores == restored+1 && backend_releases == released+1);
    /* Stock ADE returns -1 for a negative UDD result (4f30..4f58),
     * so the controller observes -1 even when the injected UDD error is -17. */
    REQUIRE(slot_bridge.stats.last_error == -1 && !memcmp(input, output, FRAME) && !*rgb3d_switch);
    injected_error = 0;
    puts("{\"check\":\"backend_busy_and_error_complete_with_passthrough\",\"passed\":true}");

    block_render = 1; render_entered = render_continue = 0;
    pthread_t thread;
    REQUIRE(!pthread_mutex_lock(&render_mutex));
    REQUIRE(!pthread_create(&thread, NULL, blocked_frame, NULL));
    while (!render_entered) REQUIRE(!pthread_cond_wait(&render_condition, &render_mutex));
    REQUIRE(nx_sf_capture_begin(&slot_bridge) == NX_SF_BUSY);
    REQUIRE(nx_sf_select(&slot_bridge, "monochrome", 6, 100, &cached_monochrome[0][0], sizeof cached_monochrome) == NX_SF_BUSY);
    REQUIRE(slot_bridge.stats.selected_generation == 5);
    render_continue = 1; REQUIRE(!pthread_cond_signal(&render_condition));
    REQUIRE(!pthread_mutex_unlock(&render_mutex));
    REQUIRE(!pthread_join(thread, NULL)); block_render = 0;
    uploads = backend_uploads;
    slot_frame("capture-pending", 0);
    REQUIRE(!operations && backend_uploads == uploads);
    REQUIRE(nx_sf_capture_begin(&slot_bridge) == NX_SF_OK);
    /* The original worker still completes neutral frames while capture owns the gate. */
    reset(); case_name = "capture-owned"; REQUIRE(prepare_node(node) == 1);
    REQUIRE(execute_node(node, data+2, data, 71) == 1);
    processing = 1; if (!setjmp(worker_done)) run_worker(node); processing = 0;
    REQUIRE(callbacks == 1 && !operations && !memcmp(input, output, FRAME));
    REQUIRE(nx_sf_capture_end(&slot_bridge) == NX_SF_OK);
    slot_frame("capture-resumed", 0); REQUIRE(operations == 1);
    puts("{\"check\":\"capture_priority_drains_inflight_before_admission\",\"passed\":true}");

    backend_begin_capture = 1; restored = backend_restores;
    slot_frame("capture-during-begin", 0);
    REQUIRE(!operations && backend_restores == restored+1 && !memcmp(input, output, FRAME));
    backend_begin_capture = 0;
    REQUIRE(nx_sf_capture_cancel(&slot_bridge) == NX_SF_OK);
    REQUIRE(nx_sf_pause(&slot_bridge) == NX_SF_OK);
    slot_frame("slot-paused", 0); REQUIRE(!operations);
    REQUIRE(nx_sf_resume(&slot_bridge) == NX_SF_OK);
    slot_frame("slot-resumed", 0); REQUIRE(operations == 1);
    puts("{\"check\":\"capture_during_begin_and_pause_resume\",\"passed\":true}");

    restore_failure = 1; slot_frame("slot-restore-error", 0);
    REQUIRE(slot_bridge.fault && slot_bridge.paused && slot_bridge.stats.last_error == -32);
    REQUIRE(!memcmp(input, output, FRAME));
    uploads = backend_uploads; slot_frame("slot-quarantined", 0);
    REQUIRE(!operations && backend_uploads == uploads);
    REQUIRE(nx_sf_capture_begin(&slot_bridge) == NX_SF_RECOVERY);
    REQUIRE(nx_sf_resume(&slot_bridge) == NX_SF_RECOVERY);
    puts("{\"check\":\"restore_failure_stops_backend_and_reports_recovery\",\"passed\":true}");
}

static void native_semaphores_free(void) {
    for(unsigned i=0;i<4;++i) { int value=-1; REQUIRE(!sem_getvalue(&native_semaphore[i],&value)&&value==1); }
    REQUIRE(!native_ipcs.held&&!native_ipcs.opened&&!native_ipcs.touched&&!native_opened);
}
static void native_iq_setup(void *iq) {
    native_processor_init=symbol(iq,"_ZN13CIpcProcessor10InitializeE13ePROCESS_MODE");
    native_processor_process=symbol(iq,"_ZN13CIpcProcessor7ProcessE13ePROCESS_MODE");
    native_list_init=symbol(iq,"_ZN17CIpcParameterList19InitializeParameterEv");
    native_list_update=symbol(iq,"_ZN17CIpcParameterList15UpdateParameterEPv");
    native_node_reset=symbol(iq,"_ZN17CIpcParameterNode5ResetEv");
    native_list_vptr[0]=(unsigned)(uintptr_t)native_list_update;
    native_list_vptr[1]=(unsigned)(uintptr_t)native_list_init;
    native_list_vptr[2]=(unsigned)(uintptr_t)native_sys_info;
    native_node_vptr[0]=(unsigned)(uintptr_t)native_update_parameter;
    native_node_vptr[1]=(unsigned)(uintptr_t)native_node_reset;
    native_parameter_list[0]=(unsigned)(uintptr_t)native_list_vptr;
    native_parameter_list[1]=(unsigned)(uintptr_t)native_parameter_nodes;
    native_processor[1]=(unsigned)(uintptr_t)native_parameter_list;
    for(unsigned c=0;c<3;++c) {
        native_parameter_nodes[c][0]=(unsigned)(uintptr_t)native_node_vptr;
        native_parameter_nodes[c][1]=0;
        native_parameter_nodes[c][4]=c==2?0:(unsigned)(uintptr_t)native_parameter_nodes[c+1];
        native_parameter_nodes[c][6]=c;
    }
}
static void native_api_setup(void *platform,struct nx_sf_ipcs_api *a) {
    memset(a,0,sizeof *a);
    for(unsigned i=0;i<4;++i) { REQUIRE(!sem_init(&native_semaphore[i],0,1)); a->semaphore[i]=&native_semaphore[i]; }
    a->trywait=(void *)sem_trywait; a->post=(void *)sem_post;
    a->open=ipcs_open; a->close=ipcs_close; a->reset=ipcs_init;
    a->ade_init=symbol(platform,"_Z19ADE_Ipcs_param_initv"); a->write_lut=ipcs_3d_lut; a->ycc=ade_ycc;
    a->iq_initialize=native_iq_initialize; a->iq_process=native_iq_process;
    a->width=ade_width; a->height=ade_height; a->stride=ade_stride; a->address=ade_address;
    a->sampling=symbol(platform,"_ZN9ADE_Image10GetSamTypeEv"); a->flush=flush_cache;
    const char *flags[]={"ADE_IP_switch_pcse_GSHC","ADE_IP_switch_pcse_PSHC","ADE_IP_switch_pcse_SE",
                        "ADE_IP_switch_RGB3D_LUT","ADE_IP_switch_YCNR","ADE_IP_switch_YLUT"};
    for(unsigned i=0;i<6;++i) { a->ade_switch[i]=native_switches[i]=symbol(platform,flags[i]); *native_switches[i]=0; }
    a->rgb3d_index=3;
}
static void check_native_adapter(void *platform,void *iq) {
    struct nx_sf_ipcs_api a;
    native_api_setup(platform,&a); native_iq_setup(iq);
    struct nx_sf_backend b={&native_ipcs,thread_id,stock_preview,stock_effect,
        nx_sf_ipcs_begin,nx_sf_ipcs_render,nx_sf_ipcs_restore,nx_sf_ipcs_end,native_passthrough};
    REQUIRE(!nx_sf_init(&slot_bridge,&b,0));
    REQUIRE(!nx_sf_ipcs_init(&native_ipcs,&a,&slot_bridge));
    REQUIRE(!nx_sf_enable(&slot_bridge,1));
    native_active=1; restore_failure=0;
    REQUIRE(!nx_sf_select(&slot_bridge,"identity",1,100,&cached_identity[0][0],sizeof cached_identity));
    slot_frame("native-identity",0); save("native-identity.yuv",output,FRAME);
    if(!(operations==1&&native_ipcs.uploads==3&&native_ipcs.reloads==1&&native_init_calls==2))
        fprintf(stderr,"native adapter: operations=%u uploads=%u reloads=%u inits=%u error=%d bridge_error=%d\n",
            operations,native_ipcs.uploads,native_ipcs.reloads,native_init_calls,native_ipcs.last_error,slot_bridge.stats.last_error);
    REQUIRE(operations==1&&native_ipcs.uploads==3&&native_ipcs.reloads==1&&native_init_calls==2);
    REQUIRE(native_nodes_updated==3&&!memcmp(table,cached_identity,sizeof table)&&!*rgb3d_switch);
    for(unsigned c=0;c<3;++c) REQUIRE(native_parameter_nodes[c][1]==0);
    REQUIRE(!nx_sf_select(&slot_bridge,"monochrome",2,100,&cached_monochrome[0][0],sizeof cached_monochrome));
    slot_frame("native-monochrome",0); save("native-monochrome.yuv",output,FRAME);
    REQUIRE(native_nodes_updated==6&&native_ipcs.reloads==2&&slot_bridge.stats.applied_generation==2);
    REQUIRE(!memcmp(table,cached_identity,sizeof table)&&!*rgb3d_switch);
    native_semaphores_free();
    puts("{\"check\":\"native_api_upload_cache_and_iq_reset_reload\",\"passed\":true}");

    REQUIRE(!sem_trywait(&native_semaphore[2]));
    unsigned opens=native_init_calls;
    slot_frame("native-busy",0);
    REQUIRE(!operations&&native_init_calls==opens&&!memcmp(input,output,FRAME));
    REQUIRE(!sem_post(&native_semaphore[2])); native_semaphores_free();
    injected_error=1; slot_frame("native-operation-error",0); injected_error=0;
    REQUIRE(operations==1&&slot_bridge.stats.last_error==-1&&native_ipcs.reloads==3);
    REQUIRE(!memcmp(input,output,FRAME)&&!memcmp(table,cached_identity,sizeof table)&&!*rgb3d_switch);
    native_semaphores_free();
    for(unsigned i=0;i<4;++i) REQUIRE(!sem_destroy(&native_semaphore[i]));
    puts("{\"check\":\"native_api_lock_contention_and_error_recovery\",\"passed\":true}");
    native_active=0;
}

/* S2 probe: the UI keeps the filter off (real mode 15) so the stock memory
 * map has no SMART_FILTER_WORK area. The managed buffer and the call-site
 * filtered mode getter are the two substitutions the RAM hook would make.
 * The stock CMemoryManager assertion on a zero GetAddress result is proven by
 * static disassembly; this fixture cannot build the real map, so it replaces
 * getWorkAddress before getBaseAddress is reached. */
static void check_s2_managed_work(void) {
    load("/mnt/mmc/identity.d4lut", table, sizeof table);
    reset(); filter_mode = 15; work_available = 0;
    s2_managed = 1; s2_lr_filter = 1; backend = 1; use_slot_bridge = 0;
    /* The lazy binder's first entry does not preserve the caller's LR. Warm the
     * shared PLT slot once, then measure the call that behaves like the RAM
     * hook's already-resolved GOT replacement. */
    int (*warm_mode)(void *) = common_filter;
    REQUIRE(warm_mode(common) == 15);
    REQUIRE(prepare_node(node) == 1);
    REQUIRE(((unsigned *)node)[0x6c/4] == (unsigned)(uintptr_t)s2_work);
    REQUIRE(execute_node(node, data+2, data, 71) == 1 && queued == 1);
    processing = 1; if (!setjmp(worker_done)) run_worker(node); processing = 0;
    REQUIRE(operations == 1 && bridges == 1 && callbacks == 1);
    REQUIRE(!memcmp(input, original, FRAME));
    puts("{\"check\":\"s2_filter_off_managed_work_runs_bridge\",\"passed\":true}");

    /* Calls outside the stock execute function still observe the real mode,
     * so UI, capture-format and map-matching logic are not forged. */
    int (*mode_fn)(void *) = common_filter;
    REQUIRE(mode_fn(common) == 15);
    puts("{\"check\":\"s2_lr_filter_keeps_other_callers_stock\",\"passed\":true}");

    /* Forged mode alone is not enough: with no work address the stock
     * Execute returns true but never queues the worker (see 0x27cb18). */
    reset(); filter_mode = 15; work_available = 0;
    s2_managed = 0; s2_lr_filter = 1;
    REQUIRE(prepare_node(node) == 1);
    REQUIRE(((unsigned *)node)[0x6c/4] == 0);
    REQUIRE(execute_node(node, data+2, data, 71) == 1 && queued == 0);
    puts("{\"check\":\"s2_forged_mode_without_work_does_not_queue\",\"passed\":true}");

    /* With the filter genuinely on, the stock area wins; the hook only fills
     * the gap left by the filter-off memory map. */
    reset(); filter_mode = 0; work_available = 1;
    s2_managed = 1; s2_lr_filter = 1;
    REQUIRE(prepare_node(node) == 1);
    REQUIRE(((unsigned *)node)[0x6c/4] == (unsigned)(uintptr_t)work);
    s2_managed = 0; s2_lr_filter = 0; filter_mode = 0; work_available = 1; backend = 0;
    puts("{\"check\":\"s2_filter_on_uses_stock_work\",\"passed\":true}");
}

int main(void) {
    void *capture = dlopen("libcapture-fw-slpcam-nx300.so", RTLD_LAZY | RTLD_GLOBAL);
    if (!capture) { fprintf(stderr, "%s\n", dlerror()); return 2; }
    void *platform = dlopen("libdi-alg-ipplatform.so", RTLD_LAZY | RTLD_GLOBAL); REQUIRE(platform);
    int (*prepare)(void *) = symbol(capture, "_ZN24CLiveviewNodeSmartFilter7PrepareEv");
    int (*execute)(void *, void *, void *, unsigned) = symbol(capture, "_ZN24CLiveviewNodeSmartFilter7ExecuteEP9CCapImagePvj");
    void (*worker)(void *) = symbol(capture, "_ZN24CLiveviewNodeSmartFilter6t_MainEv");
    int (*bring_down)(void *) = symbol(capture, "_ZN13CLiveviewNode9BringDownEv");
    stock_preview = symbol(capture, "_ZN24CLiveviewNodeSmartFilter11smartFilterEP9CCapImagePv22E_LIVEVIEW_SMARTFILTER");
    REQUIRE(stock_preview != preview_bridge);
    prepare_node = prepare; execute_node = execute; run_worker = worker;
    /* The call-site filter belongs to the lower-case worker entry, not the
     * upper-case Execute dispatcher held in the execute variable above. */
    void *execute_callback = symbol(capture, "_ZN24CLiveviewNodeSmartFilter7executeEP9CCapImagePv");
    s2_execute_start = (uintptr_t)execute_callback;
    s2_execute_end = s2_execute_start + 0x13cU; /* stock execute symbol size */
    ade_ycc = symbol(platform, "_Z30ADE_Ipcs_Ycc_correct_Param_SETP9ADE_Image");
    reg_flush = symbol(platform, "_ZN9ADE_Image12RegFlushFuncEPFvjjE");
    flush_cache = symbol(platform, "_ZN9ADE_Image11FlushDCacheEjj");
    rgb3d_switch = symbol(platform, "ADE_IP_switch_RGB3D_LUT");
    ade_width = symbol(platform, "_ZN9ADE_Image8GetWidthEv");
    ade_height = symbol(platform, "_ZN9ADE_Image9GetHeightEv");
    ade_stride = symbol(platform, "_ZN9ADE_Image9GetStrideEv");
    ade_address = symbol(platform, "_ZN9ADE_Image10GetAddressE17eADE_ADDRESS_TYPE");
    load("/mnt/mmc/input.yuv", input, FRAME); memcpy(original, input, FRAME);
    reset(); work_available = 0; REQUIRE(prepare(node) == 1);
    REQUIRE(execute(node, data+2, data, 71) == 1 && queued == 0);
    puts("{\"check\":\"no_work_returns_true_without_queue\",\"passed\":true}");
    reset(); REQUIRE(prepare(node) == 1); ((unsigned char *)node)[0x70] = 1;
    REQUIRE(execute(node, data+2, data, 71) == 1 && queued == 0);
    REQUIRE(bring_down(node) == 1 && ((unsigned char *)node)[0x70] == 1);
    puts("{\"check\":\"busy_skip_and_bring_down_does_not_drain\",\"passed\":true}");
    for (backend = 0; backend < 3; ++backend) {
        reset(); case_name = backend == 0 ? "passthrough" : backend == 1 ? "identity" : "monochrome";
        if (backend) load(backend == 1 ? "/mnt/mmc/identity.d4lut" : "/mnt/mmc/monochrome.d4lut", table, sizeof table);
        *rgb3d_switch = backend != 0; REQUIRE(prepare(node) == 1);
        REQUIRE(execute(node, data+2, data, 71) == 1 && queued == 1);
        REQUIRE(message[1] == (unsigned)(uintptr_t)(data+2) && message[2] == (unsigned)(uintptr_t)data && message[3] == 71);
        processing = 1; if (!setjmp(worker_done)) worker(node); processing = 0;
        REQUIRE(operations == 1 && bridges == 1 && callbacks == 1 && flushes == 1);
        REQUIRE(!buffer_lock[0] && !node_status[0] && !((unsigned char *)node)[0x70]);
        REQUIRE(!memcmp(input, original, FRAME));
        char path[96]; snprintf(path, sizeof path, "%s.yuv", case_name); save(path, output, FRAME);
        printf("{\"check\":\"%s\",\"passed\":true,\"operations\":%u,\"callbacks\":%u,\"pixel_backend\":\"software-reference\"}\n", case_name, operations, callbacks);
    }
    reset(); filter_mode = 15; REQUIRE(prepare(node) == 1);
    REQUIRE(execute(node, data+2, data, 71) == 1 && queued == 1);
    processing = 1; if (!setjmp(worker_done)) worker(node); processing = 0;
    REQUIRE(!operations && !callbacks && !buffer_lock[0] && !node_status[0]);
    puts("{\"check\":\"stock_filter_off_skips_callback\",\"passed\":true}");
    /* Confirm the stock assertion on an operation failure in a disposable child. */
    fflush(NULL);
    pid_t child = fork(); REQUIRE(child >= 0);
    if (!child) {
        reset(); injected_error = 1; case_name = "failure";
        REQUIRE(prepare(node) == 1 && execute(node, data+2, data, 71) == 1);
        processing = 1; if (!setjmp(worker_done)) worker(node);
        _exit(92);
    }
    int status; REQUIRE(waitpid(child, &status, 0) == child);
    REQUIRE(WIFSIGNALED(status) && WTERMSIG(status) == SIGABRT);
    puts("{\"check\":\"stock_effect_failure_asserts\",\"passed\":true,\"signal\":6}");
    load("/mnt/mmc/identity.d4lut", cached_identity, sizeof cached_identity);
    load("/mnt/mmc/monochrome.d4lut", cached_monochrome, sizeof cached_monochrome);
    check_s2_managed_work();
    check_slot_bridge();
    void *iq=dlopen("libiq-fw-slpcam.so",RTLD_LAZY|RTLD_GLOBAL); REQUIRE(iq);
    check_native_adapter(platform,iq);
    return 0;
}
