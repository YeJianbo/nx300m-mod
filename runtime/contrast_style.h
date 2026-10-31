/* Replace only the Contrast ApplyLut stage; leave EffectTone, 3D tone and
 * the subsequent original spline / hardware setter in their original order. */
#include "nx500_contrast_operator.h"
static int chroma_supported_pasm(void);
typedef void (*contrast_apply_fn)(void *, int *, const int *, const int *, const int *, int, int);
typedef struct {
    unsigned int calls;
    int count, native_index, available, modified, error;
    unsigned int generation;
    int input[19], output[19], applied[19];
} contrast_path;
static contrast_path contrast_capture, contrast_preview;
static unsigned int contrast_level=10, contrast_enabled, contrast_generation=1, contrast_last_request;
static int contrast_ready_written;
typedef struct { void *identity; unsigned int generation, updates; int eligible; } contrast_node;
static contrast_node contrast_capture_node, contrast_preview_node;
typedef struct { unsigned int generation,calls; int available,modified,state_known,error; } contrast_hardware_state;
static contrast_hardware_state contrast_capture_hardware,contrast_preview_hardware;
typedef struct { unsigned int generation,level,enabled; int capture,eligible,saw_curve,error; } contrast_transaction;
static __thread contrast_transaction *contrast_current;

static void contrast_forward_tone(void *parameters,int capture) {
    void (*original)(void *)=dlsym((void *)-1,capture?"ipcs_tone_lut":"ipcm_tone_lut");
    if(!color_process_current()) { if(original) original(parameters); return; }
    lock_color();
    contrast_hardware_state *hardware=capture?&contrast_capture_hardware:&contrast_preview_hardware;
    if(original) { original(parameters);++hardware->calls;hardware->available=1; }
    contrast_transaction *transaction=contrast_current;
    if(original && parameters && transaction && transaction->capture==capture && transaction->saw_curve) {
        hardware->generation=transaction->generation;
        hardware->modified=transaction->enabled && transaction->eligible && !transaction->error;
        hardware->state_known=1;hardware->error=transaction->error;
    } else { hardware->state_known=0;hardware->error=original?-124:-120; }
    unlock_color();
}
void ipcs_tone_lut(unsigned short *parameters) { contrast_forward_tone(parameters,1); }
void ipcm_tone_lut(unsigned char *parameters) { contrast_forward_tone(parameters,0); }


void contrast_parameter_update(void *, void *) __asm__("_ZN17CIpcParameterNode6UpdateEPv");
void contrast_parameter_update(void *self, void *parameters) {
    void (*original)(void *,void *)=dlsym((void *)-1,"_ZN17CIpcParameterNode6UpdateEPv");
    void (*reset)(void *)=dlsym((void *)-1,"_ZN17CIpcParameterNode5ResetEv");
    void *cap=dlsym((void *)0,"_ZTV12CIpcsGammaD4");
    void *prev=dlsym((void *)0,"_ZTV12CIpcmGammaD4");
    if (!original) return;
    if(!color_process_current()) { original(self,parameters); return; }
    sharpness_prepare_update(self,reset);
    contrast_node *node = self && cap && *(void **)self==(char *)cap+8 ? &contrast_capture_node :
                          self && prev && *(void **)self==(char *)prev+8 ? &contrast_preview_node : (void *)0;
    contrast_transaction transaction={0};
    if(node) {
        lock_color();transaction.generation=contrast_generation;transaction.level=contrast_level;
        transaction.enabled=contrast_enabled;unlock_color();
        transaction.capture=node==&contrast_capture_node;
        transaction.eligible=!transaction.enabled || chroma_supported_pasm();
    }
    if (node && reset) {
        lock_color();
        contrast_hardware_state *hardware=transaction.capture?&contrast_capture_hardware:&contrast_preview_hardware;
        int changed=node->identity!=self || node->generation!=transaction.generation || node->eligible!=transaction.eligible ||
            (transaction.eligible && (!hardware->state_known || hardware->generation!=transaction.generation));
        node->identity=self; node->generation=transaction.generation; node->eligible=transaction.eligible; ++node->updates;
        unlock_color();
        if(changed) reset(self);
    }
    contrast_transaction *previous=contrast_current;
    contrast_current=node?&transaction:(void *)0;
    source_rgb_run_update(self,parameters,original,reset);
    contrast_current=previous;
}

static void contrast_observe(contrast_path *path, const char *function,
        const char *x_symbol, const char *y_symbol, int expected_count,
        void *self, int *output, const int *input, const int *x, const int *y,
        int count, int knots) {
    contrast_apply_fn original = (contrast_apply_fn)dlsym((void *)-1, function);
    if (!original) return;
    if(!color_process_current()) { original(self,output,input,x,y,count,knots); return; }
    const int *native_x = dlsym((void *)0, x_symbol);
    const int *native_y = dlsym((void *)0, y_symbol);
    /* Compare integer addresses before any array subtraction: unrelated
     * pointers may also arrive here from the EffectTone / LUT calls. */
    unsigned long offset = (unsigned long)y - (unsigned long)native_y;
    int matched = output && input && native_x && native_y && x == native_x &&
                  count == expected_count && knots == 7 &&
                  offset < 9u*7u*sizeof(int) && offset % (7u*sizeof(int)) == 0;
    contrast_path observed = {0};
    contrast_transaction *transaction=contrast_current && contrast_current->capture==(path==&contrast_capture)?contrast_current:(void *)0;
    unsigned int level, enabled;
    lock_color();
    level=contrast_level; enabled=contrast_enabled; observed.generation=contrast_generation;
    unlock_color();
    if(transaction) {
        level=transaction->level;enabled=transaction->enabled;observed.generation=transaction->generation;
    }
    if (matched) {
        observed.count = count;
        observed.native_index = (int)(offset / (7u*sizeof(int)));
        observed.available = 1;
        for (int i = 0; i < count; ++i) observed.input[i] = input[i];
    }
    original(self, output, input, x, y, count, knots);
    if (matched) {
        for (int i = 0; i < count; ++i) observed.output[i] = output[i];
        if (enabled) {
            observed.error=(transaction?transaction->eligible:chroma_supported_pasm()) ?
                nx500_contrast_curve(output,observed.input,count,level,expected_count==19?1023:255) : -112;
            observed.modified=observed.error==0;
        }
        for (int i=0;i<count;++i) observed.applied[i]=output[i];
        if(transaction) {
            transaction->saw_curve=1;
            if(observed.error && !transaction->error) transaction->error=observed.error;
        }
        lock_color();
        observed.calls = path->calls + 1;
        *path = observed;
        unlock_color();
    }
}

void contrast_capture_apply(void *, int *, const int *, const int *, const int *, int, int)
    __asm__("_ZN12CIpcsGammaD48ApplyLutEPiPKiS2_S2_ii");
void contrast_capture_apply(void *self, int *out, const int *in, const int *x,
                            const int *y, int count, int knots) {
    contrast_observe(&contrast_capture, "_ZN12CIpcsGammaD48ApplyLutEPiPKiS2_S2_ii",
        "_ZN12CIpcsGammaD412m_iContrastXE", "_ZN12CIpcsGammaD412m_iContrastYE",
        19, self, out, in, x, y, count, knots);
}

void contrast_preview_apply(void *, int *, const int *, const int *, const int *, int, int)
    __asm__("_ZN12CIpcmGammaD48ApplyLutEPiPKiS2_S2_ii");
void contrast_preview_apply(void *self, int *out, const int *in, const int *x,
                            const int *y, int count, int knots) {
    contrast_observe(&contrast_preview, "_ZN12CIpcmGammaD48ApplyLutEPiPKiS2_S2_ii",
        "_ZN12CIpcmGammaD412m_iContrastXE", "_ZN12CIpcmGammaD412m_iContrastYE",
        15, self, out, in, x, y, count, knots);
}

static void contrast_publish_path(void *file, const contrast_path *path, int maximum) {
    fprintf(file, "{\"available\":%s,\"calls\":%u,\"native_index\":%d,"
        "\"count\":%d,\"downstream_clip_max\":%d,\"generation\":%u,\"modified\":%s,\"error\":%d,\"input_y\":[",
        path->available ? "true" : "false", path->calls, path->native_index, path->count, maximum,
        path->generation,path->modified?"true":"false",path->error);
    for (int i = 0; i < path->count; ++i) fprintf(file, "%s%d", i ? "," : "", path->input[i]);
    fprintf(file, "],\"original_output_y\":[");
    for (int i = 0; i < path->count; ++i) fprintf(file, "%s%d", i ? "," : "", path->output[i]);
    fprintf(file, "],\"applied_output_y\":[");
    for(int i=0;i<path->count;++i) fprintf(file,"%s%d",i?",":"",path->applied[i]);
    fprintf(file, "]}");
}

static void contrast_publish_context(void *file) {
    contrast_path capture, preview;
    lock_color();
    capture = contrast_capture; preview = contrast_preview;
    unlock_color();
    fprintf(file, ",\"contrast_observer\":{\"modified\":%s,\"capture\":",
            capture.modified||preview.modified?"true":"false");
    contrast_publish_path(file, &capture, 1023);
    fprintf(file, ",\"preview\":");
    contrast_publish_path(file, &preview, 255);
    lock_color();
    contrast_hardware_state cap_hardware=contrast_capture_hardware,prev_hardware=contrast_preview_hardware;
    unlock_color();
    fprintf(file,",\"tone_setter_observer\":{\"return_abi\":\"void; no hardware error acknowledgement\","
        "\"capture\":{\"calls\":%u,\"generation\":%u,\"available\":%s,\"state_known\":%s,\"modified\":%s,\"error\":%d},"
        "\"preview\":{\"calls\":%u,\"generation\":%u,\"available\":%s,\"state_known\":%s,\"modified\":%s,\"error\":%d}}}",
        cap_hardware.calls,cap_hardware.generation,cap_hardware.available?"true":"false",cap_hardware.state_known?"true":"false",
        cap_hardware.modified?"true":"false",cap_hardware.error,
        prev_hardware.calls,prev_hardware.generation,prev_hardware.available?"true":"false",prev_hardware.state_known?"true":"false",
        prev_hardware.modified?"true":"false",prev_hardware.error);
}

static void contrast_poll_request(void) {
    lock_color();
    int preview_available=contrast_preview.available;
    unlock_color();
    if(preview_available) contrast_ready_written=bridge_write_ready("/var/run/nx300m-contrast-ready");
    void *file=fopen("/var/run/nx300m-contrast-request","r");
    if(!file) return;
    unsigned int id=0,expires=0,operation=0,level=10;
    char trailing;
    int fields=fscanf(file,"%u %u %u %u %c",&id,&expires,&operation,&level,&trailing);
    fclose(file);
    if(!id||id==contrast_last_request) return;
    contrast_last_request=id;
    int result=fields!=4?-100:(unsigned int)time((long *)0)>expires?-108:operation>2?-101:level>20?-102:0;
    if(!result&&operation==1&&!chroma_supported_pasm()) result=-112;
    lock_color();
    if(!result&&operation!=2) {
        contrast_level=operation?level:10; contrast_enabled=operation==1;
        if(++contrast_generation==0) contrast_generation=1;
    }
    contrast_path cap=contrast_capture,prev=contrast_preview;
    unsigned int selected=contrast_level,enabled=contrast_enabled,generation=contrast_generation;
    unlock_color();
    file=fopen("/var/run/nx300m-contrast-response.tmp","w");
    if(!file) return;
    fprintf(file,"%u %d %u %u %u %u %u %d %d %d %d %d\n",id,result,selected,enabled,generation,
            cap.generation,prev.generation,cap.modified,prev.modified,cap.error,prev.error,getpid());
    fclose(file);
    rename("/var/run/nx300m-contrast-response.tmp","/var/run/nx300m-contrast-response");
}
