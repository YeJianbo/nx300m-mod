/* Select source factors before the native Edge node performs its arithmetic. */
#include "nx500_sharpness_math.h"
#include "sharpness_native.h"
static int chroma_supported_pasm(void);

typedef struct {
    unsigned int calls, generation, factor, index, native_result, setter_calls;
    unsigned int gain_plus, gain_minus, enabled, last_setter_sequence;
    int available, modified, error, setter_error, eligible;
} sharpness_path;
static sharpness_path sharpness_capture, sharpness_preview;
typedef struct { unsigned int generation,calls,write_sequence; int available,modified,state_known,error; } sharpness_hardware_state;
static sharpness_hardware_state sharpness_capture_hardware,sharpness_preview_hardware;
static unsigned int sharpness_level=10, sharpness_enabled, sharpness_generation=1, sharpness_last_request;
static int sharpness_ready_written;
static __thread sharpness_path *sharpness_current;

typedef struct { void *identity; unsigned int generation; int eligible; } sharpness_node;
static sharpness_node sharpness_capture_node, sharpness_preview_node;

static void sharpness_prepare_update(void *self, void (*reset)(void *)) {
    if(!color_process_current()) return;
    void *cap=dlsym((void *)0,"_ZTV11CIpcsEdgeD4");
    void *prev=dlsym((void *)0,"_ZTV11CIpcmEdgeD4");
    sharpness_node *node=self && cap && *(void **)self==(char *)cap+8 ? &sharpness_capture_node :
                         self && prev && *(void **)self==(char *)prev+8 ? &sharpness_preview_node : (void *)0;
    if (!node || !reset) return;
    lock_color();
    unsigned int enabled=sharpness_enabled,generation=sharpness_generation;
    unlock_color();
    int eligible=!enabled || chroma_supported_pasm();
    lock_color();
    sharpness_hardware_state *hardware=node==&sharpness_capture_node?&sharpness_capture_hardware:&sharpness_preview_hardware;
    int changed=node->identity!=self || node->generation!=generation || node->eligible!=eligible ||
        (eligible && (!hardware->state_known || hardware->generation!=generation));
    node->identity=self;node->generation=generation;node->eligible=eligible;
    unlock_color();
    if(changed) reset(self);
}

static int sharpness_forward_setter(void *parameters, int capture) {
    int (*original)(void *)=dlsym((void *)-1,capture?"ipcs_edge_enhancement_param":"ipcm_edge_enhancement_param");
    if(!color_process_current()) return original?original(parameters):-120;
    lock_color();
    int result=original?original(parameters):-120;
    sharpness_hardware_state *hardware=capture?&sharpness_capture_hardware:&sharpness_preview_hardware;
    if(original) { ++hardware->calls;hardware->available=1;++hardware->write_sequence; }
    /* A capture SetParam may submit more than one edge group. Keep the
     * transaction pending until all actual calls have returned successfully. */
    hardware->state_known=0;hardware->error=result?result:-123;
    if(sharpness_current) {
        if(original) { ++sharpness_current->setter_calls;sharpness_current->last_setter_sequence=hardware->write_sequence; }
        if(parameters) {
            const unsigned int *words=parameters;
            sharpness_current->gain_plus=words[capture?4:3];
            sharpness_current->gain_minus=words[capture?5:4];
        }
        if(result) sharpness_current->setter_error=result;
    }
    unlock_color();
    return result;
}
int ipcs_edge_enhancement_param(void *parameters) { return sharpness_forward_setter(parameters,1); }
int ipcm_edge_enhancement_param(void *parameters) { return sharpness_forward_setter(parameters,0); }

static unsigned int sharpness_run(void *self, unsigned int index, int capture) {
    nx_sharpness_native_fn original=(nx_sharpness_native_fn)dlsym((void *)-1,
        capture?"_ZN11CIpcsEdgeD48SetParamEj":"_ZN11CIpcmEdgeD48SetParamEj");
    if(!color_process_current()) return original?original(self,index):0;
    sharpness_path observed={0};
    lock_color();
    unsigned int level=sharpness_level,enabled=sharpness_enabled;
    observed.generation=sharpness_generation;
    unlock_color();
    int eligible=!enabled || chroma_supported_pasm();
    observed.enabled=enabled;observed.eligible=eligible;
    observed.factor=nx500_sharpness_q8[level];observed.index=index;
    int failure=0;
    sharpness_path *previous=sharpness_current;
    sharpness_current=&observed;
    observed.native_result=nx_sharpness_native_run(self,index,capture,observed.factor,
                                                  enabled && eligible,original,&failure);
    sharpness_current=previous;
    observed.available=observed.setter_calls!=0;
    observed.error=failure?-120-failure:observed.setter_error?observed.setter_error:
                   !eligible?-112:!observed.available?-123:0;
    observed.modified=enabled && eligible && !observed.error;
    lock_color();
    sharpness_hardware_state *hardware=capture?&sharpness_capture_hardware:&sharpness_preview_hardware;
    if(observed.setter_calls && hardware->write_sequence==observed.last_setter_sequence) {
        if(!observed.error) {
            hardware->generation=observed.generation;hardware->modified=observed.modified;
            hardware->state_known=1;hardware->error=0;
        } else { hardware->state_known=0;hardware->error=observed.error; }
    }
    sharpness_path *path=capture?&sharpness_capture:&sharpness_preview;
    observed.calls=path->calls+1;*path=observed;
    unlock_color();
    return observed.native_result;
}
unsigned int sharpness_capture_set(void *,unsigned int) __asm__("_ZN11CIpcsEdgeD48SetParamEj");
unsigned int sharpness_capture_set(void *self,unsigned int index) { return sharpness_run(self,index,1); }
unsigned int sharpness_preview_set(void *,unsigned int) __asm__("_ZN11CIpcmEdgeD48SetParamEj");
unsigned int sharpness_preview_set(void *self,unsigned int index) { return sharpness_run(self,index,0); }

static void sharpness_publish_path(void *file,const sharpness_path *p) {
    fprintf(file,"{\"available\":%s,\"calls\":%u,\"generation\":%u,\"modified\":%s,\"error\":%d,"
        "\"factor_q8\":%u,\"native_result\":%u,\"setter_calls\":%u,\"setter_error\":%d,\"gain_plus\":%u,\"gain_minus\":%u}",
        p->available?"true":"false",p->calls,p->generation,p->modified?"true":"false",p->error,
        p->factor,p->native_result,p->setter_calls,p->setter_error,p->gain_plus,p->gain_minus);
}
static void sharpness_publish_context(void *file) {
    lock_color();
    sharpness_path cap=sharpness_capture,prev=sharpness_preview;
    unlock_color();
    fprintf(file,",\"sharpness_observer\":{\"capture\":");sharpness_publish_path(file,&cap);
    fprintf(file,",\"preview\":");sharpness_publish_path(file,&prev);
    lock_color();
    sharpness_hardware_state cap_hardware=sharpness_capture_hardware,prev_hardware=sharpness_preview_hardware;
    unlock_color();
    fprintf(file,",\"setter_observer\":{\"capture\":{\"calls\":%u,\"generation\":%u,\"available\":%s,\"state_known\":%s,\"modified\":%s,\"error\":%d},"
        "\"preview\":{\"calls\":%u,\"generation\":%u,\"available\":%s,\"state_known\":%s,\"modified\":%s,\"error\":%d}}}",
        cap_hardware.calls,cap_hardware.generation,cap_hardware.available?"true":"false",cap_hardware.state_known?"true":"false",
        cap_hardware.modified?"true":"false",cap_hardware.error,
        prev_hardware.calls,prev_hardware.generation,prev_hardware.available?"true":"false",prev_hardware.state_known?"true":"false",
        prev_hardware.modified?"true":"false",prev_hardware.error);
}
static void sharpness_poll_request(void) {
    lock_color();
    int available=sharpness_preview.available;
    unlock_color();
    if(available) sharpness_ready_written=bridge_write_ready("/var/run/nx300m-sharpness-ready");
    void *file=fopen("/var/run/nx300m-sharpness-request","r");
    if(!file) return;
    unsigned int id=0,expires=0,operation=0,level=10;char trailing;
    int fields=fscanf(file,"%u %u %u %u %c",&id,&expires,&operation,&level,&trailing);
    fclose(file);
    if(!id||id==sharpness_last_request) return;
    sharpness_last_request=id;
    int result=fields!=4?-100:(unsigned int)time((long *)0)>expires?-108:operation>2?-101:level>20?-102:0;
    if(!result && operation==1 && !chroma_supported_pasm()) result=-112;
    lock_color();
    if(!result && operation!=2) {
        sharpness_level=operation?level:10;sharpness_enabled=operation==1;if(++sharpness_generation==0) sharpness_generation=1;
    }
    sharpness_path cap=sharpness_capture,prev=sharpness_preview;
    unsigned int selected=sharpness_level,enabled=sharpness_enabled,generation=sharpness_generation;
    unlock_color();
    file=fopen("/var/run/nx300m-sharpness-response.tmp","w");
    if(!file) return;
    fprintf(file,"%u %d %u %u %u %u %u %d %d %d %d %d\n",id,result,selected,enabled,generation,
        cap.generation,prev.generation,cap.modified,prev.modified,cap.error,prev.error,getpid());
    fclose(file);rename("/var/run/nx300m-sharpness-response.tmp","/var/run/nx300m-sharpness-response");
}
