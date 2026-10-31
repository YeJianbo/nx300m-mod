/*
 * NX300M 1.15 exposure preference bridge.
 *
 * Requests are serviced on the stock Ecore thread through the original
 * CDataManagementService UserData API. A readiness file records this camera
 * process PID so the host can distinguish a loaded bridge from a stale file.
 */
extern void *dlsym(void *, const char *);
extern void *fopen(const char *, const char *);
extern int fscanf(void *, const char *, ...);
extern int fprintf(void *, const char *, ...);
extern int fclose(void *);
extern int rename(const char *, const char *);
extern int unlink(const char *);
extern int getpid(void);
extern long time(long *);
extern void *stderr;

typedef int (*EcoreTimerCallback)(void *);
typedef void *(*EcoreTimerAdd)(double, EcoreTimerCallback, void *);

static unsigned int exposure_last_request;
#ifndef NX_EXPOSURE_READ_ONLY
static unsigned int exposure_extended_iso;
static const int exposure_stock_max[]={125,160,200,250,320,400,500,640,800,1000,1250,1600,2000,2500,3200,6400};

/* 1.15 IQ parameter 37 is the numeric Auto ISO ceiling. Stock UserData has
 * only sixteen codes; the two extra bridge codes never enter that table. */
static int exposure_iq_access(void **object, int (**set)(void *, int, int)) {
    void **instance=dlsym((void *)0,"_ZN15CIQDataRepeater11m_pInstanceE");
    int (*writer)(void *,int,int)=dlsym((void *)-1,"_ZN15CIQDataRepeater12SetParameterEii");
    const unsigned int *code=(const unsigned int *)writer;
    const unsigned int *getter=dlsym((void *)-1,"_ZNK15CIQDataRepeater16GetISOMaxSettingEv");
    if (!instance || !*instance || !((void **)*instance)[2] || !writer || !getter ||
        code[0]!=0xe3510072u || code[1]!=0x83e00000u || code[2]!=0x95903008u ||
        code[3]!=0x93a00001u || code[4]!=0x97832101u || code[5]!=0xe12fff1eu ||
        getter[0]!=0xe5903008u || getter[1]!=0xe5930094u || getter[2]!=0xe12fff1eu) return 0;
    *object=*instance;*set=writer;return 1;
}

int exposure_iq_set(void *,int,int) __asm__("_ZN15CIQDataRepeater12SetParameterEii");
int exposure_iq_set(void *self,int parameter,int value) {
    int (*original)(void *,int,int)=dlsym((void *)-1,"_ZN15CIQDataRepeater12SetParameterEii");
    if (!original) return -1;
    unsigned int selected=__atomic_load_n(&exposure_extended_iso,__ATOMIC_RELAXED);
    if (parameter==37 && selected) {
        void *iq;int (*writer)(void *,int,int);
        if (exposure_iq_access(&iq,&writer) && iq==self) {
            /* A different stock ceiling is an explicit reset, including menu
             * edits. Do not keep overriding a later stock user choice. */
            if (value==6400) value=(int)selected;
            else if (value!=(int)selected) __atomic_store_n(&exposure_extended_iso,0,__ATOMIC_RELAXED);
        }
    }
    return original(self,parameter,value);
}

static unsigned int exposure_effective_raw(unsigned int stock) {
    unsigned int selected=__atomic_load_n(&exposure_extended_iso,__ATOMIC_RELAXED);
    if (stock!=0x003b000fu) {
        __atomic_store_n(&exposure_extended_iso,0,__ATOMIC_RELAXED);return stock;
    }
    void *iq;int (*writer)(void *,int,int);
    int (*get)(void *)=dlsym((void *)-1,"_ZNK15CIQDataRepeater16GetISOMaxSettingEv");
    if (selected && exposure_iq_access(&iq,&writer) && get(iq)==(int)selected)
        return selected==12800?0x003b0010u:0x003b0011u;
    /* Lost/recreated IQ state is reported as stock, never a stale success. */
    if (selected) __atomic_store_n(&exposure_extended_iso,0,__ATOMIC_RELAXED);
    return stock;
}
#else
static unsigned int exposure_effective_raw(unsigned int stock) { return stock; }
#endif

int nx_auto_iso_max_configure(unsigned int iso) {
#ifdef NX_EXPOSURE_READ_ONLY
    (void)iso;
    return -120;
#else
    if (iso!=0 && iso!=12800 && iso!=25600) return -102;
    void *iq;int (*writer)(void *,int,int);
    if (!exposure_iq_access(&iq,&writer)) return -118;
    if (writer(iq,37,iso?(int)iso:6400)!=1) return -119;
    __atomic_store_n(&exposure_extended_iso,iso,__ATOMIC_RELAXED);
    return 0;
#endif
}

/* Inspect the actual initialized IQ/AE objects; never create a second solver
 * merely to produce a successful-looking preference readback. */
static void exposure_ae_state(int state[7]) {
    for (int i=0;i<7;i++) state[i]=0;
    state[6]=-1;
    void **iq_instance=dlsym((void *)0,"_ZN15CIQDataRepeater11m_pInstanceE");
    void **ae_instance=dlsym((void *)0,"_ZN9CAeInfoIF11m_pInstanceE");
    void *iq=iq_instance?*iq_instance:0,*ae=ae_instance?*ae_instance:0;
    int (*getter)(void *);
    if (iq) {
        getter=dlsym((void *)0,"_ZNK15CIQDataRepeater13GetISOSettingEv");
        if (getter) { state[1]=getter(iq);state[0]|=1; }
        getter=dlsym((void *)0,"_ZNK15CIQDataRepeater16GetISOMaxSettingEv");
        if (getter) { state[2]=getter(iq);state[0]|=2; }
        getter=dlsym((void *)0,"_ZNK15CIQDataRepeater15GetShootingModeEv");
        if (getter) { state[3]=getter(iq);state[0]|=4; }
    }
    if (ae) {
        getter=dlsym((void *)0,"_ZN9CAeInfoIF25getAeSysInfoISOMaxSettingEv");
        if (getter) { state[4]=getter(ae);state[0]|=8; }
        getter=dlsym((void *)0,"_ZN9CAeInfoIF24getAeValueSvalue_CurrentEv");
        if (getter) { state[5]=getter(ae);state[0]|=16; }
        getter=dlsym((void *)0,"_ZN9CAeInfoIF23getAeSysInfoCaptureModeEv");
        if (getter) state[6]=getter(ae);
    }
}

static int exposure_value_is_valid(unsigned int data_id,
                                   unsigned int value) {
    if (data_id == 0x37u)
        return value == 0x00370000u || value == 0x00370001u;
    if (data_id == 0x38u)
        return value == 0x00380000u || value == 0x00380001u;
    if (data_id == 0x3bu)
        return (value & 0xffff0000u) == 0x003b0000u &&
               (value & 0x0000ffffu) <= 17u;
    return 0;
}

static int exposure_poll(void *unused) {
    (void)unused;
    void *file = fopen("/var/run/nx300m-exposure-request", "r");
    if (!file) return 1;

    unsigned int id = 0, expires = 0, operation = 0;
    unsigned int data_id = 0, requested = 0;
    int fields = fscanf(file, "%u %u %u %x %x", &id, &expires, &operation,
                        &data_id, &requested);
    fclose(file);
    if (fields != 5 || !id || id == exposure_last_request) return 1;
    exposure_last_request = id;

#ifdef NX_EXPOSURE_READ_ONLY
    /* Stock GetInstance allocates and constructs when the exported slot is
     * empty (1.15 libcapture, 0x21af78..0x21afa8). Status reads use only the
     * instance the camera has already created. */
    void **instance_slot =
        dlsym((void *)0, "_ZN22CDataManagementService11m_pInstanceE");
#else
    void *(*instance)(void) =
        dlsym((void *)0, "_ZN22CDataManagementService11GetInstanceEv");
#endif
    int (*get_user_data)(void *, int) =
        dlsym((void *)0, "_ZN22CDataManagementService11GetUserDataEi");
#ifndef NX_EXPOSURE_READ_ONLY
    int (*set_user_data)(void *, int, int) =
        dlsym((void *)0, "_ZN22CDataManagementService11SetUserDataEii");
#endif

    int result = 0;
    int before = -1, actual = -1;
    int applied = 0, notified = 0;
    void *service = 0;

    if ((unsigned int)time((long *)0) > expires) result = -108;
#ifdef NX_EXPOSURE_READ_ONLY
    if (!instance_slot || !get_user_data) result = -100;
#else
    if (!instance || !get_user_data || !set_user_data) result = -100;
#endif
    if (operation > 1u) result = -101;
    if (data_id != 0x37u && data_id != 0x38u && data_id != 0x3bu)
        result = -110;
    if (operation == 1u && !exposure_value_is_valid(data_id, requested))
        result = -102;
#ifdef NX_EXPOSURE_READ_ONLY
    if (operation == 1u) result = -120;
#endif

    if (!result) {
#ifdef NX_EXPOSURE_READ_ONLY
        service = *instance_slot;
#else
        service = instance();
#endif
        if (!service) result = -103;
    }
    if (!result) {
        before = get_user_data(service, (int)data_id);
        if (before == -1) result = -104;
        else if (data_id==0x3bu) before=(int)exposure_effective_raw((unsigned int)before);
    }
#ifndef NX_EXPOSURE_READ_ONLY
    if (!result && operation == 1u) {
        unsigned int old_extended=__atomic_load_n(&exposure_extended_iso,__ATOMIC_RELAXED);
        void *iq=0;int (*writer)(void *,int,int)=0;
        int extended=data_id==0x3bu && requested>0x003b000fu;
        if (extended && !exposure_iq_access(&iq,&writer)) result=-118;
        if (!result) {
            if (data_id==0x3bu) __atomic_store_n(&exposure_extended_iso,0,__ATOMIC_RELAXED);
            result = set_user_data(service, (int)data_id, (int)(extended?0x003b000fu:requested));
            if (!result && extended) {
                unsigned int iso=requested==0x003b0010u?12800u:25600u;
                result=nx_auto_iso_max_configure(iso);
            } else if (!result && data_id==0x3bu && old_extended) {
                /* Setting 6400 while UserData already contains 6400 produces
                 * no notification. Explicitly restore the real IQ ceiling;
                 * use stock readback so the optional 6400 lock stays honest. */
                unsigned int stock=(unsigned int)get_user_data(service,0x3b);
                if ((stock&0xffff0000u)!=0x003b0000u || (stock&0xffffu)>15u ||
                    !exposure_iq_access(&iq,&writer)) result=-118;
                else if (writer(iq,37,exposure_stock_max[stock&15u])!=1) result=-119;
            }
            if (result && data_id==0x3bu) __atomic_store_n(&exposure_extended_iso,old_extended,__ATOMIC_RELAXED);
        }
        if (!result) {
            applied = 1;
            notified = before != (int)requested;
        }
    }
#endif
    int stock_actual=-1;
    if (service && get_user_data) stock_actual=actual=get_user_data(service,(int)data_id);
    if (data_id==0x3bu && actual!=-1) actual=(int)exposure_effective_raw((unsigned int)actual);
    int extended_ready = 0;
#ifndef NX_EXPOSURE_READ_ONLY
    void *iq;int (*writer)(void *,int,int);
    extended_ready=exposure_iq_access(&iq,&writer);
#endif

    int ae[7];exposure_ae_state(ae);
    file = fopen("/var/run/nx300m-exposure-response.tmp", "w");
    if (file) {
        fprintf(file, "%u %d %08x %08x %d %d %d %d %d %d %d %d %08x %d %d\n", id, result,
                (unsigned int)before, (unsigned int)actual, applied, notified,
                ae[0],ae[1],ae[2],ae[3],ae[4],ae[5],(unsigned int)stock_actual,extended_ready,ae[6]);
        fclose(file);
        rename("/var/run/nx300m-exposure-response.tmp",
               "/var/run/nx300m-exposure-response");
    }

    fprintf(stderr,
            "[NX EXPOSURE] request=%u op=%u user_data=0x%x result=%d "
            "before=%08x requested=%08x actual=%08x applied=%d notified=%d\n",
            id, operation, data_id, result, (unsigned int)before, requested,
            (unsigned int)actual, applied, notified);
    return 1;
}

static int exposure_write_ready(void) {
#ifdef NX_EXPOSURE_READ_ONLY
    const char *marker = "/var/run/nx300m-exposure-readonly";
    const char *temporary = "/var/run/nx300m-exposure-readonly.tmp";
    void *mode_file = fopen(temporary, "w");
    if (!mode_file) return 0;
    int mode_written = fprintf(mode_file, "%d\n", getpid());
    int mode_closed = fclose(mode_file);
    if (mode_written <= 0 || mode_closed || rename(temporary, marker)) {
        unlink(temporary);
        return 0;
    }
#endif
    void *file = fopen("/var/run/nx300m-exposure-ready", "w");
    if (!file) return 0;
    int written = fprintf(file, "%d\n", getpid());
    int closed = fclose(file);
    return written > 0 && closed == 0;
}

void ecore_main_loop_begin(void) {
    EcoreTimerAdd timer_add =
        (EcoreTimerAdd)dlsym((void *)-1, "ecore_timer_add");
    void (*begin)(void) = dlsym((void *)-1, "ecore_main_loop_begin");
    static int attached;
    static int ready_written;

    if (!attached) {
        unlink("/var/run/nx300m-exposure-ready");
        unlink("/var/run/nx300m-exposure-readonly");
        if (timer_add && timer_add(0.1, exposure_poll, (void *)0)) {
            attached = 1;
            fprintf(stderr, "[NX EXPOSURE] original UserData bridge attached\n");
        }
    }
    if (attached && !ready_written) {
        ready_written = exposure_write_ready();
        if (ready_written)
            fprintf(stderr, "[NX EXPOSURE] ready pid=%d\n", getpid());
        else
            fprintf(stderr, "[NX EXPOSURE] failed to write readiness marker\n");
    }
    if (begin) begin();
}
