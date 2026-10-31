/* Observational hooks: forward each call with its original arguments and r0.
 * Names and argument conventions come from stock ARM symbols/disassembly.
 * raw_r0 is not a semantic return value for methods returning void.
 */
extern void *dlsym(void *, const char *);
extern int fprintf(void *, const char *, ...);
extern int sprintf(char *, const char *, ...);
extern void *fopen(const char *, const char *);
extern unsigned int fwrite(const void *, unsigned int, unsigned int, void *);
extern int fclose(void *), rename(const char *, const char *);
extern void *stderr;
extern void abort(void);
struct trace_library { const char *path; void *base; const char *symbol; void *address; };
extern int dladdr(const void *, struct trace_library *);
extern char *strstr(const char *, const char *);
static void *next(const char *name) {
    void *function = dlsym((void *)-1, name);
    if (!function) { fprintf(stderr, "[STOCK TRACE] unresolved %s\n", name); abort(); }
    return function;
}
#ifdef NX300M_CAPTURE_GEOMETRY_TRACE
#include "capture_geometry_trace.h"
#endif
static void division_origin(const char *name, void *caller) {
    struct trace_library library;
    if (dladdr(caller, &library))
        fprintf(stderr, "[STOCK TRACE] %s caller=%p library=%s offset=%lx symbol=%s\n", name, caller,
            library.path ? library.path : "unknown", (unsigned long)caller-(unsigned long)library.base,
            library.symbol ? library.symbol : "unknown");
}
static void cs_upload_source(const char *stem, unsigned int address, unsigned int bytes) {
    if (!address || !bytes || bytes > 131056) return;
    /* Liveview and still use different grids; retain each grid's most recent
     * input so a subsequent liveview upload cannot erase still-path evidence. */
    char temporary[128], target[128];
    sprintf(target, "%s-%u.bin", stem, bytes);
    sprintf(temporary, "%s-%u.bin.tmp", stem, bytes);
    void *file = fopen(temporary, "wb");
    if (!file) return;
    unsigned int written = fwrite((const void *)address, 1, bytes, file);
    int closed = fclose(file);
    if (written == bytes && !closed && !rename(temporary, target))
        fprintf(stderr, "[STOCK TRACE] CS upload source saved path=%s virtual=%08x bytes=%u\n", target, address, bytes);
}
static void cs_upload_single(unsigned int selection, const unsigned int *info) {
    if (selection != 3 || !info) return;
    /* Released pp_core_lut_load_info holds source virtual, byte size, timeout.
     * Record before original DMA can consume it or its owner can reuse BMA. */
    cs_upload_source("/var/run/nx300m-cs-upload-lut1", info[0], info[1]);
}
int pp_core_common_lut_init(unsigned int selection, const unsigned int *info) {
    int (*original)(unsigned int, const unsigned int *) = next("pp_core_common_lut_init");
    cs_upload_single(selection, info);
    return original(selection, info);
}
int pp_core_common_lut_update(unsigned int selection, const unsigned int *info) {
    int (*original)(unsigned int, const unsigned int *) = next("pp_core_common_lut_update");
    cs_upload_single(selection, info);
    return original(selection, info);
}
int pp_core_start_lut_update(unsigned int selection, const unsigned int *info) {
    int (*original)(unsigned int, const unsigned int *) = next("pp_core_start_lut_update");
    cs_upload_single(selection, info);
    return original(selection, info);
}
void pp_core_cs_interpolation_lut_update(const unsigned int *info) {
    void (*original)(const unsigned int *) = next("pp_core_cs_interpolation_lut_update");
    if (info) {
        cs_upload_source("/var/run/nx300m-cs-upload-lut1", info[0], info[2]);
        cs_upload_source("/var/run/nx300m-cs-upload-lut2", info[1], info[2]);
        fprintf(stderr, "[STOCK TRACE] CS interpolation upload bytes=%u ratio=%u\n", info[2], ((const unsigned char *)info)[12]);
    }
    original(info);
}
int __aeabi_idiv0(int value) {
    int (*original)(int) = next("__aeabi_idiv0");
    division_origin("integer division by zero", __builtin_return_address(0));
    return original(value);
}
long long __aeabi_ldiv0(long long value) {
    long long (*original)(long long) = next("__aeabi_ldiv0");
    division_origin("long division by zero", __builtin_return_address(0));
    return original(value);
}
int raise(int signal) {
    int (*original)(int) = next("raise");
    if (signal == 8) division_origin("SIGFPE raised", __builtin_return_address(0));
    return original(signal);
}
#define TRACE_THIS(label, symbol) \
unsigned long label(void *self) __asm__(symbol); \
unsigned long label(void *self) { \
    unsigned long (*original)(void *) = next(symbol); \
    fprintf(stderr, "[STOCK TRACE] enter %s self=%p\n", symbol, self); \
    unsigned long result = original(self); \
    fprintf(stderr, "[STOCK TRACE] leave %s raw_r0=%lx\n", symbol, result); \
    return result; \
}
#define TRACE_INT(label, symbol) \
unsigned long label(void *self, int value) __asm__(symbol); \
unsigned long label(void *self, int value) { \
    unsigned long (*original)(void *, int) = next(symbol); \
    fprintf(stderr, "[STOCK TRACE] enter %s self=%p value=%d\n", symbol, self, value); \
    unsigned long result = original(self, value); \
    fprintf(stderr, "[STOCK TRACE] leave %s raw_r0=%lx\n", symbol, result); \
    return result; \
}
TRACE_THIS(memory_init, "_ZN17CMemoryManager_Nx10InitializeEv")
TRACE_THIS(memory_start, "_ZN17CMemoryManager_Nx5StartEv")
TRACE_INT(memory_mode, "_ZN17CMemoryManager_Nx10ChangeModeE19eMEMORY_MAP_MODE_ID")
TRACE_INT(memory_map, "_ZN17CMemoryManager_Nx9changeMapEi")
TRACE_THIS(video_b2y, "_ZN18CVideoEventHandler14handleB2YEventEv")
TRACE_THIS(video_callback, "_ZN15CCapVideoStream19exeInternalCallbackEv")
/* Capture-only observations: pass original arguments/r0 through unchanged.
 * These hooks do not schedule a callback, release a wait or edit state. */
TRACE_THIS(capture_exposure, "_ZN18CFrontendServiceNx11capExposureEv")
TRACE_THIS(capture_change_mode, "_ZN18CFrontendServiceNx15ChangeStillModeEv")
TRACE_THIS(capture_charge, "_ZN18CFrontendServiceNx13ChargeShutterEv")
TRACE_THIS(capture_trigger, "_ZN18CFrontendServiceNx14TriggerShutterEv")
TRACE_THIS(capture_ready_open, "_ZN18CFrontendServiceNx18ReadyToOpenShutterEv")
TRACE_THIS(capture_open, "_ZN18CFrontendServiceNx11OpenShutterEv")
TRACE_THIS(capture_stop_exposure, "_ZN18CFrontendServiceNx12StopExposureEv")
TRACE_THIS(focal_trigger, "_ZN18CFocalPlaneShutter7triggerEv")
void cf_pp_capture_frame_read_out_done(void) {
    void (*original)(void) = next("cf_pp_capture_frame_read_out_done");
    fprintf(stderr, "[STOCK TRACE] stock PP capture readout callback enter\n");
    original();
    volatile unsigned int *flag = dlsym((void *)0, "g_cf_pp_wait_frame_read_out");
    fprintf(stderr, "[STOCK TRACE] stock PP capture readout callback leave flag=%u\n", flag ? *flag : ~0U);
}
void d4_register_isr(int type, void (*callback)(void)) {
    void (*original)(int, void (*)(void)) = next("d4_register_isr");
    if (type == 11) {
        struct trace_library library;
        if (dladdr((const void *)callback, &library))
            fprintf(stderr, "[STOCK TRACE] PP ISR register type=%d callback=%p library=%s symbol=%s\n", type,
                    (void *)callback, library.path ? library.path : "unknown", library.symbol ? library.symbol : "unknown");
    }
    original(type, callback);
}
void d4_deregister_isr(int type) {
    void (*original)(int) = next("d4_deregister_isr");
    if (type == 11) fprintf(stderr, "[STOCK TRACE] PP ISR deregister type=%d\n", type);
    original(type);
}
void focal_hal_event(void *self, int type, long value) __asm__("_ZN18CFocalPlaneShutter14MessageFromHALEil");
void focal_hal_event(void *self, int type, long value) {
    void (*original)(void *, int, long) = next("_ZN18CFocalPlaneShutter14MessageFromHALEil");
    fprintf(stderr, "[STOCK TRACE] focal HAL event self=%p type=%d value=%ld\n", self, type, value);
    original(self, type, value);
}
void pwm_config(void *self, const unsigned int *config) {
    void (*original)(void *, const unsigned int *) = next("pwm_config");
    fprintf(stderr, "[STOCK TRACE] pwm config channel=%u", ((unsigned int *)self)[1]);
    for (unsigned int i = 0; i < 20; i++) fprintf(stderr, " %08x", config[i]);
    fprintf(stderr, "\n");
    original(self, config);
}
void pwm_enable(void *self) {
    void (*original)(void *) = next("pwm_enable");
    fprintf(stderr, "[STOCK TRACE] pwm enable channel=%u\n", ((unsigned int *)self)[1]);
    original(self);
}
TRACE_THIS(sensor_readout, "_ZN13CAquilaDriver12BeginReadoutEv")
TRACE_THIS(capture_ssif, "_ZN20CFrontEndServiceBase10capSetSsifEv")
unsigned long capture_wait_done(void *self) __asm__("_ZN18CFrontendServiceNx11capWaitDoneEv");
unsigned long capture_wait_done(void *self) {
    unsigned long (*original)(void *) = next("_ZN18CFrontendServiceNx11capWaitDoneEv");
    division_origin("capWaitDone entry", __builtin_return_address(0));
    fprintf(stderr, "[STOCK TRACE] capWaitDone self=%p\n", self);
    return original(self);
}
int ep_udd_mnf_bayer_one_channel_setup(const unsigned int *parameter) {
    int (*original)(const unsigned int *) = next("ep_udd_mnf_bayer_one_channel_setup");
    /* Released ep_type.h: 8 header words followed by four MnF_image objects
     * of 10 words each. Observe the stock setup without replacing its work. */
    char fields[48*9+1];
    unsigned int used = 0;
    fields[0] = 0;
    if (parameter) for (unsigned int i = 0; i < 48; i++) used += sprintf(fields+used, " %08x", parameter[i]);
    fprintf(stderr, "[STOCK TRACE] MnF Bayer one-channel parameters%s\n", fields);
    int result = original(parameter);
    fprintf(stderr, "[STOCK TRACE] MnF Bayer one-channel setup returned=%d\n", result);
    return result;
}
void cf_pp_capture_wait_frame_read_out_done(void) {
    void (*original)(void) = next("cf_pp_capture_wait_frame_read_out_done");
    volatile unsigned int *flag = dlsym((void *)0, "g_cf_pp_wait_frame_read_out");
    fprintf(stderr, "[STOCK TRACE] stock PP capture readout wait enter flag=%u\n", flag ? *flag : ~0U);
    original();
    fprintf(stderr, "[STOCK TRACE] stock PP capture readout wait leave flag=%u\n", flag ? *flag : ~0U);
}
TRACE_INT(capture_readout, "_ZN18CFrontendServiceNx15capFrameReadoutE15eCAP_PATH_VALUE")
TRACE_INT(capture_end, "_ZN18CFrontendServiceNx6capEndEb")
TRACE_INT(capture_setup_flash, "_ZN18CFrontendServiceNx10SetupFlashEb")
TRACE_INT(strobe_trigger_source, "_ZN14CStrobeService16SetTriggerSourceEi")
TRACE_INT(strobe_exec_trigger_source, "_ZN14CStrobeService20execSetTriggerSourceEi")
TRACE_INT(strobe_exec_gv, "_ZN14CStrobeService13execSetGvCodeEt")
TRACE_INT(strobe_wait_gv, "_ZN24CStrobeSystemAbstraction14LockMutexForGvEi")
TRACE_THIS(strobe_release_gv, "_ZN24CStrobeSystemAbstraction16UnlockMutexForGvEv")
TRACE_THIS(strobe_run, "_ZN14CStrobeService7RunTaskEv")
unsigned long strobe_gv(void *self, unsigned short gv, unsigned short offset)
    __asm__("_ZN14CStrobeService9SetGvCodeEtt");
unsigned long strobe_gv(void *self, unsigned short gv, unsigned short offset) {
    unsigned long (*original)(void *, unsigned short, unsigned short) = next("_ZN14CStrobeService9SetGvCodeEtt");
    fprintf(stderr, "[STOCK TRACE] strobe SetGvCode enter self=%p gv=%u offset=%u\n", self, gv, offset);
    unsigned long result = original(self, gv, offset);
    fprintf(stderr, "[STOCK TRACE] strobe SetGvCode leave raw_r0=%lx\n", result);
    return result;
}
int usleep(unsigned int microseconds) {
    int (*original)(unsigned int) = next("usleep");
    void *caller = __builtin_return_address(0);
    struct trace_library library;
    if (dladdr(caller, &library) && library.path && strstr(library.path, "libstrobe.so")) {
        /* Record each sleep site a few times, so a hardware handshake loop is
         * observable without turning its polling frequency into log growth. */
        static void *sites[64];
        static unsigned int counts[64];
        for (unsigned int i = 0; i < 64; i++) {
            if (!sites[i]) __sync_bool_compare_and_swap(sites+i, (void *)0, caller);
            if (sites[i] != caller) continue;
            if (__sync_fetch_and_add(counts+i, 1) < 4)
                fprintf(stderr, "[STOCK TRACE] strobe sleep offset=%lx symbol=%s us=%u\n",
                    (unsigned long)caller-(unsigned long)library.base,
                    library.symbol ? library.symbol : "unknown", microseconds);
            break;
        }
    }
    return original(microseconds);
}
unsigned long strobe_message(void *self, int type, int command, int value)
    __asm__("_ZN14CStrobeService11sendMessageEiii");
unsigned long strobe_message(void *self, int type, int command, int value) {
    unsigned long (*original)(void *, int, int, int) = next("_ZN14CStrobeService11sendMessageEiii");
    fprintf(stderr, "[STOCK TRACE] strobe send enter self=%p type=%d command=%d value=%d\n", self, type, command, value);
    unsigned long result = original(self, type, command, value);
    fprintf(stderr, "[STOCK TRACE] strobe send leave raw_r0=%lx\n", result);
    return result;
}
static void *volatile capture_flags[3];
static unsigned int capture_wait_messages;
static int capture_flag(void *self) {
    if (self == capture_flags[0] || self == capture_flags[1] || self == capture_flags[2]) return 1;
    return capture_flags[0] && __sync_fetch_and_add(&capture_wait_messages, 1) < 128;
}
unsigned long capture_prepare(void *self, int value) __asm__("_ZN18CFrontendServiceNx10capPrepareEb");
unsigned long capture_prepare(void *self, int value) {
    unsigned long (*original)(void *, int) = next("_ZN18CFrontendServiceNx10capPrepareEb");
    /* These event-object offsets are verified in stock capPrepare/capExposure.
     * Only remember pointer identities; never modify their flags or masks. */
    capture_flags[0] = *(void **)((char *)self+0x108);
    capture_flags[1] = *(void **)((char *)self+0x110);
    capture_flags[2] = *(void **)((char *)self+0x114);
    capture_wait_messages = 0;
    fprintf(stderr, "[STOCK TRACE] capPrepare enter self=%p value=%d flags=%p/%p/%p\n",
        self, value, capture_flags[0], capture_flags[1], capture_flags[2]);
    unsigned long result = original(self, value);
    fprintf(stderr, "[STOCK TRACE] capPrepare leave raw_r0=%lx\n", result);
    return result;
}
unsigned long capture_wait_or(void *self, unsigned long mask, unsigned long *flags, int timeout)
    __asm__("_ZN10CEventFlag6WaitOrEmPmi");
unsigned long capture_wait_or(void *self, unsigned long mask, unsigned long *flags, int timeout) {
    unsigned long (*original)(void *, unsigned long, unsigned long *, int) = next("_ZN10CEventFlag6WaitOrEmPmi");
    int watched = capture_flag(self);
    if (watched) fprintf(stderr, "[STOCK TRACE] capture WaitOr enter self=%p mask=%lx timeout=%d\n", self, mask, timeout);
    unsigned long result = original(self, mask, flags, timeout);
    if (watched) fprintf(stderr, "[STOCK TRACE] capture WaitOr leave self=%p raw_r0=%lx\n", self, result);
    return result;
}
unsigned long capture_wait(void *self, unsigned long *flags, int mode, int timeout)
    __asm__("_ZN10CEventFlag4WaitEPmii");
unsigned long capture_wait(void *self, unsigned long *flags, int mode, int timeout) {
    unsigned long (*original)(void *, unsigned long *, int, int) = next("_ZN10CEventFlag4WaitEPmii");
    int watched = capture_flag(self);
    if (watched) fprintf(stderr, "[STOCK TRACE] capture Wait enter self=%p mode=%d timeout=%d\n", self, mode, timeout);
    unsigned long result = original(self, flags, mode, timeout);
    if (watched) fprintf(stderr, "[STOCK TRACE] capture Wait leave self=%p raw_r0=%lx\n", self, result);
    return result;
}
int pthread_cancel(unsigned long thread) {
    int (*original)(unsigned long) = next("pthread_cancel");
    fprintf(stderr, "[STOCK TRACE] pthread_cancel thread=%lx\n", thread);
    return original(thread);
}
unsigned long capture_time_log(void *self, const char *section, const char *label)
    __asm__("_ZN18CFrontendServiceNx19PrintCaptureTimeLogEPKcS1_");
unsigned long capture_time_log(void *self, const char *section, const char *label) {
    unsigned long (*original)(void *, const char *, const char *) = next("_ZN18CFrontendServiceNx19PrintCaptureTimeLogEPKcS1_");
    fprintf(stderr, "[STOCK TRACE] capture time-log enter section=%s label=%s\n", section, label);
    unsigned long result = original(self, section, label);
    fprintf(stderr, "[STOCK TRACE] capture time-log leave raw_r0=%lx\n", result);
    return result;
}
unsigned long capture_notify(void *self, int type, long value)
    __asm__("_ZN18CFrontendServiceNx18onNotifyCompletionEil");
unsigned long capture_notify(void *self, int type, long value) {
    unsigned long (*original)(void *, int, long) = next("_ZN18CFrontendServiceNx18onNotifyCompletionEil");
    fprintf(stderr, "[STOCK TRACE] capture_notify self=%p type=%d value=%ld\n", self, type, value);
    return original(self, type, value);
}
void frontend_callback(int event, int value) __asm__("_ZN17CLiveviewCallback16FrontEndCallbackEii");
void frontend_callback(int event, int value) {
    void (*original)(int, int) = next("_ZN17CLiveviewCallback16FrontEndCallbackEii");
    fprintf(stderr, "[STOCK TRACE] frontend_callback event=%d value=%d\n", event, value);
    original(event, value);
}
void liveview_started(void *self, unsigned long event, int value) __asm__("_ZN9CLiveview20startCallbackProcessEmi");
void liveview_started(void *self, unsigned long event, int value) {
    void (*original)(void *, unsigned long, int) = next("_ZN9CLiveview20startCallbackProcessEmi");
    fprintf(stderr, "[STOCK TRACE] liveview_start_callback self=%p event=%lu value=%d\n", self, event, value);
    original(self, event, value);
}
