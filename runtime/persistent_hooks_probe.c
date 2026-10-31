/* Loader/Ecore fixture only. No camera objects, hardware setters or success stubs. */
extern int printf(const char *, ...);
extern void *fopen(const char *, const char *);
extern int fscanf(void *, const char *, ...);
extern char *fgets(char *, int, void *);
extern int fclose(void *);
extern char *strstr(const char *, const char *);
extern void *dlsym(void *, const char *);
extern void *dlopen(const char *, int);
extern const char *dlerror(void);
extern int fork(void);
extern int getpid(void);
extern int waitpid(int, int *, int);
extern int ecore_init(void);
extern int ecore_shutdown(void);
extern void *ecore_timer_add(double, int (*)(void *), void *);
extern void ecore_main_loop_begin(void);
extern void ecore_main_loop_quit(void);

static const char *hooks[] = {"lut_port.so", "ui_mod.so", "exposure_controls.so",
    "shutter_limit.so", "color-style.so", "lcd-capture.so", "service-loader.so",
    "gallery-share.so", "camera_firmware_mod.so"};
static const char *required_symbols[] = {
    "_ZN22CDataManagementService11GetInstanceEv",
    "_ZN22CDataManagementService11GetUserDataEi",
    "_ZN22CDataManagementService11SetUserDataEii",
    "_ZN22CDataManagementService7GetDataEiPv",
    "_ZN22CDataManagementService7SetDataEiPKv",
    "_ZN15CIQDataRepeater11GetInstanceEv",
    "_ZN15CIQDataRepeater12GetParameterEiPi",
    "_ZN10CIpcIfInfo12GetColorToneEv",
    "_ZN10CIpcIfInfo13GetSaturationEv",
    "_ZN10CIpcIfInfo12GetSharpnessEv",
    "_ZN10CIpcIfInfo11GetContrastEv",
    "_ZN9CAeInfoIF16calcTvMaxCaptureEv",
    "_ZN17CIpcParameterNode6UpdateEPv",
    "_ZN17CIpcParameterNode5ResetEv",
    "_ZTV12CIpcs3DLutD4",
    "_ZN12CIpcs3DLutD47m_sRLutE",
    "ipcs_3d_lut", "ipcs_color_correction_param", "ipcm_color_correction_param"
};
static int child_result = 21;

static int marker(const char *path) {
    int pid = -1;
    void *file = fopen(path, "r");
    if (file) { if (fscanf(file, "%d", &pid) != 1) pid = -1; fclose(file); }
    return pid;
}

static unsigned int mapped_hooks(void) {
    unsigned int mask = 0;
    char line[1024];
    void *file = fopen("/proc/self/maps", "r");
    if (!file) return 0;
    while (fgets(line, sizeof line, file))
        for (unsigned int i = 0; i < sizeof hooks / sizeof hooks[0]; ++i)
            if (strstr(line, hooks[i])) mask |= 1u << i;
    fclose(file);
    return mask;
}

static int finish_loop(void *unused) {
    (void)unused;
    int pid = getpid();
    int lut = marker("/var/run/nx300m-lut-hook");
    int pw = marker("/var/run/nx300m-pw-ready");
    int exposure = marker("/var/run/nx300m-exposure-ready");
    int shutter = marker("/var/run/nx300m-shutter-limit-ready");
    int color = marker("/var/run/nx300m-color-style-ready");
    void *status = fopen("/var/run/nx300m-service-loader-status.json", "r");
    int service_status_present = status != (void *)0;
    if (status) fclose(status);
    unsigned int mask = mapped_hooks();
    printf("{\"event\":\"loop_observation\",\"pid\":%d,\"mapped_hook_mask\":%u,"
           "\"lut_marker_pid\":%d,\"pw_marker_pid\":%d,\"exposure_marker_pid\":%d,"
           "\"shutter_marker_pid\":%d,\"rgb_marker_pid\":%d,\"fixture_is_camera\":false,"
           "\"service_loader_status_present\":%s}\n",
           pid, mask, lut, pw, exposure, shutter, color, service_status_present ? "true" : "false");
    /* LUT and service loader must reject this honestly named non-camera fixture. */
    child_result = mask == ((1u << (sizeof hooks / sizeof hooks[0])) - 1u) && lut == -1 && !service_status_present && pw == pid && exposure == pid &&
        shutter == pid && color == pid ? 0 : 22;
    ecore_main_loop_quit();
    return 0;
}

static int child(void) {
    void *stock = dlopen("libudd.so", 2);
    if (!stock) { printf("{\"event\":\"stock_library_error\",\"error\":\"%s\"}\n", dlerror()); return 23; }
    for (unsigned int i = 0; i < sizeof required_symbols / sizeof required_symbols[0]; ++i) {
        const char *name = required_symbols[i];
        printf("{\"event\":\"symbol\",\"name\":\"%s\",\"global_present\":%s,"
               "\"present_in_loaded_stock_scope\":%s}\n", name,
               dlsym((void *)0, name) ? "true" : "false",
               dlsym(stock, name) ? "true" : "false");
    }
    printf("{\"event\":\"ecore_enter\",\"pid\":%d}\n", getpid());
    int initialized = ecore_init();
    printf("{\"event\":\"ecore_init\",\"pid\":%d,\"result\":%d}\n", getpid(), initialized);
    if (initialized <= 0) return 24;
    if (!ecore_timer_add(0.35, finish_loop, (void *)0)) return 25;
    ecore_main_loop_begin();
    int remaining = ecore_shutdown();
    printf("{\"event\":\"ecore_shutdown\",\"pid\":%d,\"result\":%d,\"probe_result\":%d}\n",
           getpid(), remaining, child_result);
    return remaining == 0 ? child_result : 26;
}

int main(void) {
    printf("{\"event\":\"fixture_main\",\"pid\":%d,\"mapped_hook_mask\":%u}\n", getpid(), mapped_hooks());
    int pid = fork();
    if (pid < 0) return 27;
    if (!pid) return child();
    int status = 0;
    if (waitpid(pid, &status, 0) != pid) return 28;
    printf("{\"event\":\"child_exit\",\"parent_pid\":%d,\"child_pid\":%d,"
           "\"wait_status\":%d,\"rgb_marker_after_child_exit\":%d}\n",
           getpid(), pid, status, marker("/var/run/nx300m-color-style-ready"));
    return status == 0 ? 0 : 29;
}
