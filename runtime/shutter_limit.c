/* NX300M 1.15: constrain the actual capture AE program-line Tv upper bound. */
extern void *dlsym(void *, const char *);
extern void *fopen(const char *, const char *);
extern int fscanf(void *, const char *, ...);
extern int fprintf(void *, const char *, ...);
extern int fclose(void *);
extern int rename(const char *, const char *);
extern long time(long *);
extern int getpid(void);
extern void abort(void);
extern void *stderr;

/* Stock-table times are rounded to the slower side of each user's 1/N target. */
static const int limits_tv16[] = {0, 94, 111, 127, 143, 159, 175, 191, 200, 207};
static const unsigned int limits_us[] = {0, 17039, 8158, 4079, 2039, 1019, 509, 254, 172, 127};
static unsigned int selected, call_count, capped_count, last_selection;
static int last_stock, last_result;
static unsigned int last_request;

int nx_shutter_limit_configure(unsigned int selection) {
    if (selection >= sizeof(limits_tv16) / sizeof(limits_tv16[0])) return -102;
    __atomic_store_n(&selected, selection, __ATOMIC_RELAXED);
    return 0;
}

unsigned int nx_shutter_limit_selection(void) {
    return __atomic_load_n(&selected, __ATOMIC_RELAXED);
}

int nx_capture_tv_max(void *self)
    __asm__("_ZN9CAeInfoIF16calcTvMaxCaptureEv");
int nx_capture_tv_max(void *self) {
    typedef int (*StockGetter)(void *);
    static StockGetter original;
    StockGetter getter = __atomic_load_n(&original, __ATOMIC_RELAXED);
    if (!getter) {
        getter = (StockGetter)dlsym((void *)-1, "_ZN9CAeInfoIF16calcTvMaxCaptureEv");
        if (!getter) {
            fprintf(stderr, "[NX SHUTTER] original capture Tv getter is missing\n");
            abort();
        }
        __atomic_store_n(&original, getter, __ATOMIC_RELAXED);
    }
    int stock = getter(self);
    unsigned int choice = nx_shutter_limit_selection();
    int result = stock;
    if (choice && stock > limits_tv16[choice]) result = limits_tv16[choice];
    if (__atomic_load_n(&last_stock, __ATOMIC_RELAXED) != stock ||
        __atomic_load_n(&last_result, __ATOMIC_RELAXED) != result ||
        __atomic_load_n(&last_selection, __ATOMIC_RELAXED) != choice) {
        fprintf(stderr,
                "[NX SHUTTER] stock_tv16=%d selection=%u result_tv16=%d limit_us=%u\n",
                stock, choice, result, limits_us[choice]);
    }
    __atomic_store_n(&last_stock, stock, __ATOMIC_RELAXED);
    __atomic_store_n(&last_result, result, __ATOMIC_RELAXED);
    __atomic_store_n(&last_selection, choice, __ATOMIC_RELAXED);
    __atomic_add_fetch(&call_count, 1, __ATOMIC_RELAXED);
    if (result != stock) __atomic_add_fetch(&capped_count, 1, __ATOMIC_RELAXED);
    return result;
}

static int shutter_limit_poll(void *unused) {
    (void)unused;
    void *file = fopen("/var/run/nx300m-shutter-limit-request", "r");
    if (!file) return 1;
    unsigned int id = 0, expires = 0, operation = 0, requested = 0;
    int fields = fscanf(file, "%u %u %u %u", &id, &expires, &operation, &requested);
    fclose(file);
    if (fields != 4 || !id || id == last_request) return 1;
    last_request = id;
    int code = 0;
    if ((unsigned int)time((long *)0) > expires) code = -108;
    else if (operation > 1) code = -101;
    else if (operation == 1) code = nx_shutter_limit_configure(requested);
    unsigned int choice = nx_shutter_limit_selection();
    file = fopen("/var/run/nx300m-shutter-limit-response.tmp", "w");
    if (file) {
        fprintf(file, "%u %d %u %d %u %d %d %u %u %u\n", id, code, choice,
                limits_tv16[choice], limits_us[choice],
                __atomic_load_n(&last_stock, __ATOMIC_RELAXED),
                __atomic_load_n(&last_result, __ATOMIC_RELAXED),
                __atomic_load_n(&call_count, __ATOMIC_RELAXED),
                __atomic_load_n(&capped_count, __ATOMIC_RELAXED),
                __atomic_load_n(&last_selection, __ATOMIC_RELAXED));
        fclose(file);
        rename("/var/run/nx300m-shutter-limit-response.tmp",
               "/var/run/nx300m-shutter-limit-response");
    }
    return 1;
}

void ecore_main_loop_begin(void) {
    typedef void *(*TimerAdd)(double, int (*)(void *), void *);
    TimerAdd add = (TimerAdd)dlsym((void *)-1, "ecore_timer_add");
    void (*begin)(void) = (void (*)(void))dlsym((void *)-1, "ecore_main_loop_begin");
    static int attached;
    if (!attached && add && add(0.1, shutter_limit_poll, (void *)0)) {
        attached = 1;
        void *ready = fopen("/var/run/nx300m-shutter-limit-ready.tmp", "w");
        if (ready) {
            fprintf(ready, "%d\n", getpid());
            fclose(ready);
            rename("/var/run/nx300m-shutter-limit-ready.tmp",
                   "/var/run/nx300m-shutter-limit-ready");
        }
        fprintf(stderr, "[NX SHUTTER] capture AE upper-bound bridge ready; disabled\n");
    }
    if (begin) begin();
}
