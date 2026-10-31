/* Explicit independent-lab encoding of byte snapshots whose image producer is
 * unknown. This is not the regular encoder's producer-authorization provider. */
#include "jpeg_diagnostic_sample.h"
struct nx_diag_buffer { unsigned char *data;uint32_t length,writable; };
struct nx_diag_context {
    struct nx_diag_buffer buffers[7];uint32_t count;
    struct nx300m_jpeg_task task,expected;
};
static struct nx_diag_context nx_diag;
static volatile unsigned int nx_diag_lock;
static void *nx_diag_map(uint32_t address,uint32_t length,int writable) {
    for(uint32_t i=0;i<nx_diag.count;++i) {
        struct nx_diag_buffer *buffer=&nx_diag.buffers[i];
        uint32_t base=(uint32_t)(uintptr_t)buffer->data;
        if(buffer->data && address>=base && address-base<buffer->length &&
                length<=buffer->length-(address-base) && writable==(int)buffer->writable)
            return buffer->data+(address-base);
    }
    return NULL;
}
static int nx_diag_ready(const struct nx300m_jpeg_task *task) {
    return !memcmp(task,&nx_diag.expected,sizeof(*task))?NX_JPEG_INPUT_CBCR:NX_JPEG_INPUT_UNKNOWN;
}
static int nx_diagnostic_encode(struct nx300m_jpeg_task *task,uint32_t capacity) {
    struct nx300m_jpeg_layout layout;const char *stage="task";
    struct nx300m_jpeg_cpu_result result;memset(&result,0,sizeof(result));
    int error=-22;uint64_t token=0;uint32_t committed=0;
    const char *sample_dir=getenv("NX300M_DIAGNOSTIC_SAMPLE_DIR");
    int sample_requested=sample_dir && *sample_dir,sample_injected=0;
    uint32_t sample_planes=0,sample_bytes=0;
    if(!task) goto finished_unlocked;
    task->size=0;
    /* The stock planar hardware pitch is still unresolved. This experiment
     * interprets only the proven interleaved layout, explicitly assuming CbCr. */
    stage="stock-pack";
    if(task->pack_type!=0) {error=-95;goto finished_unlocked;}
    /* A sample file is one complete progressive plane. Interlaced even/odd
     * ranges need a different file contract; do not load CbCr into Y_ODD. */
    stage="sample-progressive-layout";
    if(sample_requested && (task->y_odd || task->cb_odd || task->cr_odd)) {
        error=-95;goto finished_unlocked;
    }
    error=nx300m_jpeg_describe_task(task,capacity,&layout);
    if(error) {stage=layout.rejection;goto finished_unlocked;}
    uint64_t total=0;
    for(uint32_t i=0;i<layout.count;++i) total+=layout.ranges[i].length;
    stage="diagnostic-memory-limit";
    if(total>128u*1024u*1024u) {error=-12;goto finished_unlocked;}
    int (*copy_live)(uint32_t,uint32_t,void *)=dlsym(RTLD_DEFAULT,"nx300m_copy_live_virtual");
    uint64_t (*generation)(uint32_t,uint32_t,int)=dlsym(RTLD_DEFAULT,"nx300m_live_virtual_generation");
    int (*commit)(uint32_t,uint32_t,const void *,uint64_t)=
        dlsym(RTLD_DEFAULT,"nx300m_write_live_virtual_if_generation");
    stage="snapshot-and-generation-provider";
    if(!copy_live || !generation || !commit) {error=-61;goto finished_unlocked;}
    stage="output-generation";token=generation(task->output,capacity,1);
    if(!token) {error=-14;goto finished_unlocked;}
    while(__sync_lock_test_and_set(&nx_diag_lock,1)) usleep(1000);
    memset(&nx_diag,0,sizeof(nx_diag));nx_diag.task=*task;nx_diag.count=layout.count;
    for(uint32_t i=0;i<layout.count;++i) {
        const struct nx300m_jpeg_range *range=&layout.ranges[i];
        struct nx_diag_buffer *buffer=&nx_diag.buffers[i];
        buffer->length=range->length;buffer->writable=range->writable;
        stage="snapshot-allocation";buffer->data=malloc(buffer->length);
        if(!buffer->data) {error=-12;goto cleanup;}
        if(!buffer->writable) {
            if(sample_requested) {
                const char *role=range->role==NX_JPEG_Y_EVEN?"y":
                    range->role==NX_JPEG_CB_EVEN?"cbcr":NULL;
                if(!role) {error=-95;stage="sample-plane-role";goto cleanup;}
                error=nx_diag_read_sample(sample_dir,task->width,task->height,
                                           role,buffer->data,buffer->length,&stage);
                if(error) goto cleanup;
                ++sample_planes;sample_bytes+=buffer->length;
            } else {
                stage="live-input-snapshot";
                error=copy_live(range->address,range->length,buffer->data);
                if(error) goto cleanup;
            }
        }
        uint32_t address=(uint32_t)(uintptr_t)buffer->data;
        switch(range->role) {
            case NX_JPEG_OUTPUT:nx_diag.task.output=address;break;
            case NX_JPEG_Y_EVEN:nx_diag.task.y_even=address;break;
            case NX_JPEG_Y_ODD:nx_diag.task.y_odd=address;break;
            case NX_JPEG_CB_EVEN:nx_diag.task.cb_even=address;break;
            case NX_JPEG_CB_ODD:nx_diag.task.cb_odd=address;break;
            case NX_JPEG_CR_EVEN:nx_diag.task.cr_even=address;break;
            case NX_JPEG_CR_ODD:nx_diag.task.cr_odd=address;break;
        }
    }
    sample_injected=sample_requested && sample_planes==2;
    struct nx300m_jpeg_memory_ops ops={nx_diag_map,nx_diag_ready};
    nx_diag.expected=nx_diag.task;
    stage="snapshot-compression";
    error=nx300m_jpeg_cpu_encode(&nx_diag.task,capacity,&ops,0,&result);
    if(error) {stage=result.failure_stage;goto cleanup;}
    stage="same-generation-output-commit";
    error=commit(task->output,result.bytes,nx_diag.buffers[0].data,token);
    if(error) goto cleanup;
    task->size=result.bytes;committed=result.bytes;stage="diagnostic-bytes-committed";
cleanup:
    for(uint32_t i=0;i<nx_diag.count;++i) free(nx_diag.buffers[i].data);
    memset(&nx_diag,0,sizeof(nx_diag));__sync_lock_release(&nx_diag_lock);
finished_unlocked:
    ;char line[768];int count=snprintf(line,sizeof(line),"[JPEG DIAGNOSTIC] {\"guest_pid\":%u,\"guest_tid\":%ld,\"width\":%u,\"height\":%u,\"bytes_committed\":%u,\"error\":%d,\"stage\":\"%s\",\"source_pixels_unverified\":true,\"sample_requested\":%s,\"sample_injected\":%s,\"sample_planes_loaded\":%u,\"sample_bytes_loaded\":%u,\"chroma_order_assumed\":\"CbCr\",\"regular_producer_authorization_used\":false,\"image_verified\":false}\n",
        (unsigned)getpid(),syscall(SYS_gettid),task?task->width:0,task?task->height:0,
        committed,error,stage?stage:"",sample_requested?"true":"false",
        sample_injected?"true":"false",sample_planes,sample_bytes);
    if(count>0 && (size_t)count<sizeof(line)) (void)write(STDERR_FILENO,line,(size_t)count);
    return error;
}
