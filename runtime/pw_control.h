/* NX300M 1.15 Picture Wizard transport. Execute on the original Ecore loop;
 * CDataManagementService performs locking and sends its normal OnNotify.
 * Never write IQ repeater storage directly: its readback is independent evidence.
 */
extern int rename(const char *, const char *);
extern int getpid(void);
extern long time(long *);
static unsigned int pw_last_request;
static int pw_scalar_valid(unsigned int raw) {
    int value = ((int)raw) >> 20;
    return value >= -4 && value <= 4 &&
        raw == (((unsigned int)value << 20) | 0xff004u);
}
static int pw_poll(void *unused) {
    (void)unused;
    void *file = fopen("/var/run/nx300m-pw-request", "r");
    if (!file) return 1;
    unsigned int id = 0, mask = 0, expires = 0, values[4] = {0};
    int n = fscanf(file, "%u %u %u %x %x %x %x", &id, &mask, &expires, values, values+1, values+2, values+3);
    fclose(file);
    if (n != 7 || !id || id == pw_last_request) return 1;
    pw_last_request = id;
    void *(*instance)(void) = dlsym((void *)0, "_ZN22CDataManagementService11GetInstanceEv");
    int (*get)(void *, int, void *) = dlsym((void *)0, "_ZN22CDataManagementService7GetDataEiPv");
    int (*set)(void *, int, const void *) = dlsym((void *)0, "_ZN22CDataManagementService7SetDataEiPKv");
    void *(*iq_instance)(void) = dlsym((void *)0, "_ZN15CIQDataRepeater11GetInstanceEv");
    int (*iq_get)(void *, int, int *) = dlsym((void *)0, "_ZN15CIQDataRepeater12GetParameterEiPi");
    int (*decoded[4])(void *) = {
        dlsym((void *)0, "_ZN10CIpcIfInfo12GetColorToneEv"),
        dlsym((void *)0, "_ZN10CIpcIfInfo13GetSaturationEv"),
        dlsym((void *)0, "_ZN10CIpcIfInfo12GetSharpnessEv"),
        dlsym((void *)0, "_ZN10CIpcIfInfo11GetContrastEv")
    };
    unsigned int before[4] = {0}, actual[4] = {0}, iq[4] = {0};
    int indices[4] = {-1, -1, -1, -1}, mode = -1;
    int rc = 0;
    if ((unsigned int)time((long *)0) > expires) rc = -108;
    if (!instance || !get || !set || !iq_instance || !iq_get) rc = -100;
    for (unsigned int i = 0; i < 4; ++i) if (!decoded[i]) rc = -100;
    if (mask & ~15u) rc = -101;
    for (unsigned int i = 0; i < 3; ++i)
        if ((mask & (1u << i)) && !pw_scalar_valid(values[i])) rc = -102;
    if ((mask & 8u) && values[3] != 0xffffffffu && values[3] > 359u) rc = -102;
    void *service = rc ? 0 : instance();
    if (!rc && !service) rc = -103;
    if (!rc) {
        for (int i = 0; i < 4; ++i)
            if (get(service, 0x117+i, before+i)) rc = -104;
    }
    if (!rc) {
        for (int i = 0; i < 4; ++i) {
            if (!(mask & (1u << i))) continue;
            int j = i == 3 ? 0 : i+1;
            int result = set(service, 0x117+j, values+i);
            if (result) { rc = result; break; }
        }
        if (rc) {
            for (int i = 0; i < 4; ++i) {
                int j = i == 3 ? 0 : i+1;
                if (mask & (1u << i)) set(service, 0x117+j, before+j);
            }
        }
        for (int i = 0; i < 4; ++i)
            if (get(service, 0x117+i, actual+i) && !rc) rc = -105;
        void *repeater = iq_instance();
        if (!repeater) rc = -106;
        else {
            for (int i = 0; i < 4; ++i)
                if (iq_get(repeater, 0x2d+i, (int *)(iq+i)) != 1 && !rc) rc = -107;
            if (iq_get(repeater, 0x49, &mode) != 1 && !rc) rc = -107;
            /* CIpcIfInfo's original ctor/dtor are empty; these getters only
             * consult the original repeater. Keep a valid aligned this slot. */
            unsigned int info = 0;
            for (int i = 0; i < 4; ++i) indices[i] = decoded[i](&info);
        }
    }
    file = fopen("/var/run/nx300m-pw-response.tmp", "w");
    if (file) {
        fprintf(file, "%u %d %08x %08x %08x %08x %08x %08x %08x %08x %d %d %d %d %d\n", id, rc,
            actual[0], actual[1], actual[2], actual[3], iq[0], iq[1], iq[2], iq[3],
            indices[0], indices[1], indices[2], indices[3], mode);
        fclose(file);
        rename("/var/run/nx300m-pw-response.tmp", "/var/run/nx300m-pw-response");
    }
    fprintf(stderr, "[NX PW] request=%u mask=%u result=%d data=%08x/%08x/%08x/%08x IQ=%08x/%08x/%08x/%08x decoded=%d/%d/%d/%d mode=%d\n",
        id, mask, rc, actual[0], actual[1], actual[2], actual[3], iq[0], iq[1], iq[2], iq[3],
        indices[0], indices[1], indices[2], indices[3], mode);
    return 1;
}

void ecore_main_loop_begin(void) {
    void *(*timer_add)(double, int (*)(void *), void *) = dlsym((void *)-1, "ecore_timer_add");
    void (*begin)(void) = dlsym((void *)-1, "ecore_main_loop_begin");
    static int attached;
    if (!attached && timer_add && timer_add(0.1, pw_poll, (void *)0)) {
        attached = 1;
        void *ready = fopen("/var/run/nx300m-pw-ready.tmp", "w");
        if (ready) {
            int written = fprintf(ready, "%d\n", getpid());
            int closed = fclose(ready);
            if (written > 0 && !closed)
                rename("/var/run/nx300m-pw-ready.tmp", "/var/run/nx300m-pw-ready");
        }
        fprintf(stderr, "[NX PW] original Ecore loop control ready pid=%d\n", getpid());
    }
    if (begin) begin();
}
