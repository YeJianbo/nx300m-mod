/* Apply the NX500 RGB column operation to a copy of the target native Q7
 * base, before target PSS/ColorTone. Target base interpolation already ran;
 * this does not replace the source builder or its later hardware-domain F. */
#include "nx500_rgb_math.h"
static int chroma_supported_pasm(void);

typedef struct {
    unsigned int calls, generation, getter_calls, setter_calls, enabled, values[3], codes[3];
    unsigned int sequence, last_update_sequence, last_update_generation, last_update_getter_calls, last_update_setter_calls;
    int last_update_error;
    int available, eligible, modified, error, setter_error, custom_row_gains_modified;
    int16_t input[9], output[9];
    i32 setter_input[9];
} source_rgb_path;
typedef struct { void *identity; unsigned int generation, updates; int eligible; } source_rgb_node;
typedef struct { unsigned int generation; int modified, state_known, error; } source_rgb_owner;
typedef struct { unsigned int generation, setter_calls; int modified, state_known, last_result, error; } source_rgb_hardware;
typedef struct {
    source_rgb_path observed;
    int capture;
    int16_t scratch[9];
} source_rgb_transaction;
static source_rgb_path source_rgb_capture, source_rgb_preview;
static source_rgb_node source_rgb_capture_node, source_rgb_preview_node;
static source_rgb_hardware source_rgb_capture_hardware, source_rgb_preview_hardware;
static source_rgb_owner source_rgb_capture_cache_owner, source_rgb_preview_cache_owner;
static unsigned int source_rgb_values[3]={100,100,100};
static unsigned int source_rgb_enabled, source_rgb_generation=1, source_rgb_last_request;
static int source_rgb_ready_written, source_rgb_preview_seen;
static __thread source_rgb_transaction *source_rgb_current;

int16_t *source_rgb_get_matrix(void) __asm__("_Z16GetRgb2RgbMatrixv");
int16_t *source_rgb_get_matrix(void) {
    int16_t *(*original)(void)=dlsym((void *)-1,"_Z16GetRgb2RgbMatrixv");
    int16_t *base=original?original():(void *)0;
    source_rgb_transaction *transaction=source_rgb_current;
    if (!color_process_current() || !transaction) return base;
    source_rgb_path *observed=&transaction->observed;
    if (!base) { observed->error=-120;return base; }
    ++observed->getter_calls;
    for (int i=0;i<9;++i) observed->input[i]=observed->output[i]=base[i];
    if (!observed->enabled || !observed->eligible || observed->error) return base;
    if (nx500_rgb_columns(transaction->scratch,base,observed->codes)) {
        observed->error=-122;return base;
    }
    for (int i=0;i<9;++i) observed->output[i]=transaction->scratch[i];
    /* The two native consumers copy all nine values before another getter.
     * Storage belongs to this thread's synchronous Update transaction. */
    return transaction->scratch;
}

static void source_rgb_missing_setter(int capture) {
    if (source_rgb_current && source_rgb_current->capture==capture)
        source_rgb_current->observed.error=-120;
}
static source_rgb_owner source_rgb_transaction_owner(int capture) {
    source_rgb_owner owner={0};
    source_rgb_transaction *transaction=source_rgb_current;
    if (transaction && transaction->capture==capture && transaction->observed.getter_calls && !transaction->observed.error) {
        source_rgb_path *observed=&transaction->observed;
        owner.generation=observed->generation;owner.state_known=1;
        owner.error=observed->error?observed->error:!observed->eligible?-112:0;
        owner.modified=observed->enabled && observed->eligible && !observed->error;
    }
    return owner;
}
static void source_rgb_cache_matrix_locked(int capture) {
    source_rgb_owner *owner=capture?&source_rgb_capture_cache_owner:&source_rgb_preview_cache_owner;
    *owner=source_rgb_transaction_owner(capture);
}
static void source_rgb_record_hardware_locked(int capture,const source_rgb_owner *owner,int result) {
    source_rgb_hardware *hardware=capture?&source_rgb_capture_hardware:&source_rgb_preview_hardware;
    ++hardware->setter_calls;hardware->last_result=result;
    if (!result && owner->state_known) {
        hardware->generation=owner->generation;hardware->modified=owner->modified;
        hardware->state_known=1;hardware->error=owner->error;
    } else {
        /* Keep the previous generation and modified bits as history. A failed
         * or unowned write cannot establish which matrix the hardware retains. */
        hardware->state_known=0;hardware->error=result?result:-124;
    }
}
static void source_rgb_cached_setter_result_locked(int capture,int result) {
    source_rgb_owner *owner=capture?&source_rgb_capture_cache_owner:&source_rgb_preview_cache_owner;
    source_rgb_record_hardware_locked(capture,owner,result);
}
/* Called with color_lock still held at the genuine setter boundary. Updating
 * this record later at Update return would let an older thread relabel a newer
 * write. Cached custom-row replays carry the owner captured with their base. */
static void source_rgb_setter_result_locked(int capture,const color_matrix *matrix,
                                     int result,int custom_row_gains_modified) {
    source_rgb_transaction *transaction=source_rgb_current;
    source_rgb_owner owner=source_rgb_transaction_owner(capture);
    if (!matrix) owner.state_known=0;
    source_rgb_record_hardware_locked(capture,&owner,result);
    if (!transaction || transaction->capture!=capture) return;
    source_rgb_path *observed=&transaction->observed;
    ++observed->setter_calls;
    if (result) observed->setter_error=result;
    observed->custom_row_gains_modified=!result && custom_row_gains_modified;
    if (matrix) for (int i=0;i<9;++i) observed->setter_input[i]=(i32)matrix->col_mat[i];
    if (observed->getter_calls && matrix) {
        if (!capture) source_rgb_preview_seen=1;
    }
}

static void source_rgb_run_update(void *self,void *parameters,
        void (*original)(void *,void *),void (*reset)(void *)) {
    if(!color_process_current()) { original(self,parameters); return; }
    void *cap=dlsym((void *)0,"_ZTV12CIpcsColorD4");
    void *prev=dlsym((void *)0,"_ZTV12CIpcmColorD4");
    int capture=self && cap && *(void **)self==(char *)cap+8;
    int preview=self && prev && *(void **)self==(char *)prev+8;
    source_rgb_transaction *previous=source_rgb_current;
    if (!capture && !preview) {
        source_rgb_current=(void *)0;
        original(self,parameters);
        source_rgb_current=previous;
        return;
    }
    source_rgb_transaction transaction={0};
    source_rgb_path *observed=&transaction.observed;
    transaction.capture=capture;
    lock_color();
    observed->generation=source_rgb_generation;observed->enabled=source_rgb_enabled;
    for (int i=0;i<3;++i) observed->values[i]=source_rgb_values[i];
    unlock_color();
    observed->eligible=!observed->enabled || chroma_supported_pasm();
    if (nx500_rgb_recipe_codes(observed->codes,observed->values)) observed->error=-122;
    source_rgb_node *node=capture?&source_rgb_capture_node:&source_rgb_preview_node;
    lock_color();
    int changed=node->identity!=self || node->generation!=observed->generation || node->eligible!=observed->eligible;
    node->identity=self;node->generation=observed->generation;node->eligible=observed->eligible;
    observed->sequence=++node->updates;
    unlock_color();
    if (changed && reset) reset(self);
    source_rgb_current=&transaction;
    original(self,parameters);
    source_rgb_current=previous;
    lock_color();
    source_rgb_path *path=capture?&source_rgb_capture:&source_rgb_preview;
    unsigned int calls=path->calls+1;
    int complete=observed->getter_calls!=0 && observed->setter_calls!=0;
    int idle=!observed->error && !observed->setter_error && !observed->setter_calls &&
             path->generation==observed->generation && path->eligible==observed->eligible;
    if (!observed->error)
        observed->error=!observed->eligible?-112:observed->setter_error?observed->setter_error:
                         !complete && !idle?-123:0;
    if (observed->sequence>=path->last_update_sequence) {
        if (!idle) {
            observed->available=path->available || complete;
            /* A failed setter does not establish restoration of an old write. */
            observed->modified=observed->error?path->modified:(int)observed->enabled;
            *path=*observed;
        }
        path->last_update_sequence=observed->sequence;
        path->last_update_generation=observed->generation;
        path->last_update_getter_calls=observed->getter_calls;
        path->last_update_setter_calls=observed->setter_calls;
        path->last_update_error=observed->error;
    } else if (complete) path->available=1;
    path->calls=calls;
    unlock_color();
}

static void source_rgb_publish_path(void *file,const source_rgb_path *path,const source_rgb_hardware *hardware) {
    fprintf(file,"{\"available\":%s,\"calls\":%u,\"generation\":%u,\"enabled\":%s,"
        "\"eligible\":%s,\"modified\":%s,\"error\":%d,\"getter_calls\":%u,\"setter_calls\":%u,"
        "\"setter_error\":%d,\"custom_row_gains_modified\":%s,\"values\":[%u,%u,%u],"
        "\"codes\":[%u,%u,%u],\"getter_input\":[",
        path->available?"true":"false",path->calls,path->generation,path->enabled?"true":"false",
        path->eligible?"true":"false",path->modified?"true":"false",path->error,
        path->getter_calls,path->setter_calls,path->setter_error,path->custom_row_gains_modified?"true":"false",
        path->values[0],path->values[1],path->values[2],path->codes[0],path->codes[1],path->codes[2]);
    for (int i=0;i<9;++i) fprintf(file,"%s%d",i?",":"",path->input[i]);
    fprintf(file,"],\"getter_output\":[");
    for (int i=0;i<9;++i) fprintf(file,"%s%d",i?",":"",path->output[i]);
    fprintf(file,"],\"setter_input\":[");
    for (int i=0;i<9;++i) fprintf(file,"%s%d",i?",":"",path->setter_input[i]);
    fprintf(file,"],\"last_update_generation\":%u,\"last_update_getter_calls\":%u,"
        "\"last_update_setter_calls\":%u,\"last_update_error\":%d,\"hardware_generation\":%u,"
        "\"hardware_modified\":%s,\"hardware_state_known\":%s,\"hardware_setter_calls\":%u,\"hardware_last_result\":%d,\"hardware_error\":%d}",
        path->last_update_generation,path->last_update_getter_calls,path->last_update_setter_calls,path->last_update_error,
        hardware->generation,hardware->modified?"true":"false",hardware->state_known?"true":"false",
        hardware->setter_calls,hardware->last_result,hardware->error);
}
static void source_rgb_publish_context(void *file) {
    lock_color();
    source_rgb_path cap=source_rgb_capture,prev=source_rgb_preview;
    source_rgb_hardware cap_hardware=source_rgb_capture_hardware,prev_hardware=source_rgb_preview_hardware;
    unsigned int enabled=source_rgb_enabled,generation=source_rgb_generation;
    unsigned int r=source_rgb_values[0],g=source_rgb_values[1],b=source_rgb_values[2];
    unlock_color();
    fprintf(file,",\"source_rgb_observer\":{\"enabled\":%s,\"generation\":%u,\"values\":[%u,%u,%u],\"capture\":",
        enabled?"true":"false",generation,r,g,b);
    source_rgb_publish_path(file,&cap,&cap_hardware);
    fprintf(file,",\"preview\":");source_rgb_publish_path(file,&prev,&prev_hardware);fprintf(file,"}");
}

static void source_rgb_poll_request(void) {
    lock_color();
    int available=source_rgb_preview_seen;
    unlock_color();
    if(available) source_rgb_ready_written=bridge_write_ready("/var/run/nx300m-source-rgb-ready");
    void *file=fopen("/var/run/nx300m-source-rgb-request","r");
    if (!file) return;
    unsigned int id=0,expires=0,operation=0,values[3]={100,100,100};char trailing;
    int fields=fscanf(file,"%u %u %u %u %u %u %c",&id,&expires,&operation,values,values+1,values+2,&trailing);
    fclose(file);
    if (!id || id==source_rgb_last_request) return;
    source_rgb_last_request=id;
    int result=fields!=6?-100:(unsigned int)time((long *)0)>expires?-108:operation>2?-101:
               values[0]>200 || values[1]>200 || values[2]>200?-102:0;
    if (!result && operation==1 && !chroma_supported_pasm()) result=-112;
    lock_color();
    if (!result && operation!=2) {
        source_rgb_enabled=operation==1;
        for (int i=0;i<3;++i) source_rgb_values[i]=operation?values[i]:100;
        if (++source_rgb_generation==0) source_rgb_generation=1;
    }
    source_rgb_hardware cap=source_rgb_capture_hardware,prev=source_rgb_preview_hardware;
    unsigned int enabled=source_rgb_enabled,generation=source_rgb_generation;
    unsigned int r=source_rgb_values[0],g=source_rgb_values[1],b=source_rgb_values[2];
    unlock_color();
    file=fopen("/var/run/nx300m-source-rgb-response.tmp","w");
    if (!file) return;
    int written=fprintf(file,"%u %d %u %u %u %u %u %u %u %d %d %d %d %d\n",
        id,result,r,g,b,enabled,generation,cap.generation,prev.generation,
        cap.modified,prev.modified,cap.state_known?cap.error:cap.error?cap.error:-124,
        prev.state_known?prev.error:prev.error?prev.error:-124,getpid());
    int closed=fclose(file);
    if (written>0 && !closed)
        rename("/var/run/nx300m-source-rgb-response.tmp","/var/run/nx300m-source-rgb-response");
    else unlink("/var/run/nx300m-source-rgb-response.tmp");
}

static void source_rgb_cleanup_files(void) { bridge_cleanup("source-rgb"); }
static void source_rgb_initialize(void) {
    source_rgb_capture=(source_rgb_path){0}; source_rgb_preview=(source_rgb_path){0};
    source_rgb_capture_node=(source_rgb_node){0}; source_rgb_preview_node=(source_rgb_node){0};
    source_rgb_capture_hardware=(source_rgb_hardware){0}; source_rgb_preview_hardware=(source_rgb_hardware){0};
    source_rgb_capture_cache_owner=(source_rgb_owner){0}; source_rgb_preview_cache_owner=(source_rgb_owner){0};
    source_rgb_current=(void *)0;
    source_rgb_enabled=0;source_rgb_generation=1;source_rgb_last_request=0;source_rgb_ready_written=0;
    source_rgb_preview_seen=0;
    for (int i=0;i<3;++i) source_rgb_values[i]=100;
}
static void source_rgb_shutdown(void) {
    source_rgb_cleanup_files();
}
