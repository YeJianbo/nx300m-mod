/* Optional CPU execution of the original libsif JpegEncode task boundary.
 * Default execution preserves authorized YCC samples and 422/420 sampling.
 * The separate opt-in software ISP renders a verified recorded Bayer source;
 * diagnostic snapshots remain explicitly unverified. No hardware JPEG IRQs. */
#define _GNU_SOURCE
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <setjmp.h>
#include <dlfcn.h>
#include <unistd.h>
#include <sys/syscall.h>
#include <jpeglib.h>
#include <jerror.h>
#include "jpeg_cpu_adapter.h"
#include "stock_quant_tables.h"

typedef char nx_task_size[sizeof(struct nx300m_jpeg_task)==0x40?1:-1];
typedef char nx_jpeg_abi[sizeof(struct jpeg_compress_struct)==0x1b0 && JPEG_LIB_VERSION==80?1:-1];
typedef char nx_jpeg_raw_offset[offsetof(struct jpeg_compress_struct,raw_data_in)==0xd0?1:-1];
typedef char nx_jpeg_fancy_offset[offsetof(struct jpeg_compress_struct,do_fancy_downsampling)==0xe0?1:-1];
#define NX_JPEG_MAX_CAPACITY (64u*1024u*1024u)
struct nx_jpeg_api {
    void *library;
    struct jpeg_error_mgr *(*std_error)(struct jpeg_error_mgr *);
    void (*create)(j_compress_ptr,int,size_t);
    void (*defaults)(j_compress_ptr);
    void (*quality)(j_compress_ptr,int,boolean);
    void (*quant)(j_compress_ptr,int,const unsigned int *,int,boolean);
    void (*start)(j_compress_ptr,boolean);
    JDIMENSION (*raw)(j_compress_ptr,JSAMPIMAGE,JDIMENSION);
    void (*finish)(j_compress_ptr);
    void (*destroy)(j_compress_ptr);
};
struct nx_plane { const unsigned char *even,*odd;uint32_t stride,rows,width; };
struct nx_error { struct jpeg_error_mgr base;jmp_buf jump; };
struct nx_destination { struct jpeg_destination_mgr base;unsigned char *data;size_t capacity; };
struct nx_context {
    struct jpeg_compress_struct c;
    struct nx_error error;
    struct nx_destination destination;
    struct nx_jpeg_api api;
    unsigned char *rows,*encoded;
};
static int nx_plane_span(uint32_t address,uint32_t stride,uint32_t width,
        uint32_t rows,uint32_t *length) {
    if(!address || !width || !rows || stride<width || stride>131072) return -22;
    uint64_t span=(uint64_t)(rows-1)*stride+width;
    if(span>UINT32_MAX || (uint64_t)address+span>0x100000000ULL) return -14;
    *length=(uint32_t)span;return 0;
}
static int nx_describe_plane(struct nx300m_jpeg_layout *layout,uint32_t even,
        uint32_t odd,uint32_t stride,uint32_t width,uint32_t rows,uint32_t role) {
    uint32_t counts[2]={odd?(rows+1)/2:rows,odd?rows/2:0},addresses[2]={even,odd};
    for(unsigned int i=0;i<2;++i) if(counts[i]) {
        uint32_t length;int error=nx_plane_span(addresses[i],stride,width,counts[i],&length);
        if(error) return error;
        layout->ranges[layout->count++]=(struct nx300m_jpeg_range){addresses[i],length,role+i,0};
    }
    return 0;
}
int nx300m_jpeg_describe_task(const struct nx300m_jpeg_task *task,uint32_t capacity,
        struct nx300m_jpeg_layout *layout) {
    if(!layout) return -22;
    memset(layout,0,sizeof(*layout));layout->rejection="task";
    if(!task) return -22;
    layout->rejection="geometry-or-format";
    if(!task->width || !task->height || task->width>8192 || task->height>8192 ||
            ((task->width|task->height)&1) || task->ycc_type>1 || task->pack_type>1) return -95;
    layout->sampling=task->ycc_type?420:422;
    layout->rejection="output-capacity";
    if(!capacity || capacity>NX_JPEG_MAX_CAPACITY) return -28;
    layout->rejection="output-range";
    if(!task->output || (uint64_t)task->output+capacity>0x100000000ULL) return -14;
    layout->ranges[layout->count++]=(struct nx300m_jpeg_range){task->output,capacity,NX_JPEG_OUTPUT,1};
    layout->rejection="y-range";
    int error=nx_describe_plane(layout,task->y_even,task->y_odd,task->stride_y,
        task->width,task->height,NX_JPEG_Y_EVEN);
    if(error) return error;
    uint32_t cw=task->width/2,ch=task->height/(task->ycc_type?2:1);
    layout->rejection="cb-range";
    error=nx_describe_plane(layout,task->cb_even,task->cb_odd,task->stride_c,
        task->pack_type?cw:task->width,ch,NX_JPEG_CB_EVEN);
    if(error) return error;
    layout->rejection="cr-range";
    if(task->pack_type) {
        error=nx_describe_plane(layout,task->cr_even,task->cr_odd,task->stride_c,cw,ch,NX_JPEG_CR_EVEN);
        if(error) return error;
    } else if(task->cr_even || task->cr_odd) return -95;
    layout->rejection=NULL;return 0;
}
static void nx_error_exit(j_common_ptr c) {
    struct nx_error *error=(struct nx_error *)c->err;longjmp(error->jump,1);
}
static void nx_destination_start(j_compress_ptr c) {
    struct nx_destination *d=(struct nx_destination *)c->dest;
    d->base.next_output_byte=d->data;d->base.free_in_buffer=d->capacity;
}
static boolean nx_destination_full(j_compress_ptr c) {
    c->err->msg_code=JERR_BUFFER_SIZE;c->err->error_exit((j_common_ptr)c);return FALSE;
}
static void nx_destination_finish(j_compress_ptr c) { (void)c; }
static int nx_load_api(struct nx_jpeg_api *api) {
    /* A stock soname is required; never load a host encoder with another ABI. */
    void *library=dlopen("libjpeg.so.8",RTLD_NOW|RTLD_LOCAL);
    if(!library) return -2;
#define NX_LOAD(field,name) do { *(void **)(&api->field)=dlsym(library,name);if(!api->field) { dlclose(library);memset(api,0,sizeof(*api));return -2; } } while(0)
    NX_LOAD(std_error,"jpeg_std_error");NX_LOAD(create,"jpeg_CreateCompress");
    NX_LOAD(defaults,"jpeg_set_defaults");NX_LOAD(quality,"jpeg_set_quality");
    NX_LOAD(quant,"jpeg_add_quant_table");NX_LOAD(start,"jpeg_start_compress");
    NX_LOAD(raw,"jpeg_write_raw_data");NX_LOAD(finish,"jpeg_finish_compress");
    NX_LOAD(destroy,"jpeg_destroy_compress");
#undef NX_LOAD
    /* Function pointers and compressor callbacks remain live through destroy. */
    api->library=library;return 0;
}
static int nx_map_plane(struct nx_plane *p,uint32_t even,uint32_t odd,
        uint32_t stride,uint32_t width,uint32_t rows,
        const struct nx300m_jpeg_memory_ops *ops) {
    if(!even || !width || !rows || stride<width || stride>131072) return -22;
    p->stride=stride;p->rows=rows;p->width=width;p->odd=NULL;
    uint32_t counts[2]={odd?(rows+1)/2:rows,odd?rows/2:0};
    uint32_t addresses[2]={even,odd};
    for(unsigned int i=0;i<2;++i) if(counts[i]) {
        uint32_t span;int error=nx_plane_span(addresses[i],stride,width,counts[i],&span);
        if(error) return error;
        const unsigned char *mapped=ops->map_address(addresses[i],span,0);
        if(!mapped) return -14;
        if(i) p->odd=mapped;else p->even=mapped;
    }
    return 0;
}
static const unsigned char *nx_row(const struct nx_plane *p,uint32_t row) {
    if(row>=p->rows) row=p->rows-1;
    return p->odd?((row&1)?p->odd:p->even)+(row/2)*p->stride:p->even+row*p->stride;
}
int nx300m_jpeg_cpu_encode(struct nx300m_jpeg_task *task,uint32_t capacity,
        const struct nx300m_jpeg_memory_ops *ops,unsigned int software_quality,
        struct nx300m_jpeg_cpu_result *result) {
    if(!result) return -22;
    memset(result,0,sizeof(*result));result->capacity=capacity;
    result->quant_table_index=UINT32_MAX;
    result->failure_stage="task";
    int failure=-22;
    if(!task) goto rejected;
    task->size=0;
    struct nx300m_jpeg_layout layout;
    failure=nx300m_jpeg_describe_task(task,capacity,&layout);
    if(failure) {result->failure_stage=layout.rejection;goto rejected;}
    result->failure_stage="memory-provider";
    if(!ops || !ops->map_address || !ops->input_ready) {failure=-61;goto rejected;}
    result->failure_stage="quality-policy";
    if(software_quality>100 || (!software_quality && task->quality>2)) {failure=-95;goto rejected;}
    result->failure_stage="output-map";
    unsigned char *output=ops->map_address(task->output,capacity,1);
    if(!output) {failure=-14;goto rejected;}
    uint32_t width=task->width,height=task->height,cw=width/2;
    /* libsif YCC type 0 selects hardware 16x8 MCU; type 1 selects 16x16. */
    uint32_t vfactor=task->ycc_type?2:1,ch=height/vfactor;
    struct nx_plane y={0},cb={0},cr={0};
    result->failure_stage="y-map";
    failure=nx_map_plane(&y,task->y_even,task->y_odd,task->stride_y,width,height,ops);
    if(failure) goto rejected;
    result->failure_stage="cb-map";
    failure=nx_map_plane(&cb,task->cb_even,task->cb_odd,task->stride_c,
        task->pack_type?cw:width,ch,ops);
    if(failure) goto rejected;
    if(task->pack_type) {
        result->failure_stage="cr-map";
        failure=nx_map_plane(&cr,task->cr_even,task->cr_odd,task->stride_c,cw,ch,ops);
        if(failure) goto rejected;
    } else if(task->cr_even || task->cr_odd) {failure=-95;goto rejected;}
    result->failure_stage="producer-authorization";
    int order=ops->input_ready(task);
    if(order!=NX_JPEG_INPUT_CBCR && order!=NX_JPEG_INPUT_CRCB) {failure=-61;goto rejected;}
    result->failure_stage="encoder-allocation";
    struct nx_context *ctx=calloc(1,sizeof(*ctx));
    if(!ctx) {failure=-12;goto rejected;}
    result->failure_stage="stock-libjpeg";
    failure=nx_load_api(&ctx->api);if(failure) {free(ctx);goto rejected;}
    uint32_t yp=(width+15)&~15u,cp=(cw+7)&~7u,yr=8*vfactor;
    result->failure_stage="encoder-allocation";
    ctx->encoded=malloc(capacity);ctx->rows=malloc(yp*yr+cp*16);
    if(!ctx->encoded || !ctx->rows) {failure=-12;goto cleanup;}
    result->failure_stage="jpeg-compression";
    ctx->c.err=ctx->api.std_error(&ctx->error.base);ctx->error.base.error_exit=nx_error_exit;
    if(setjmp(ctx->error.jump)) {
        fprintf(stderr,"[CPU JPEG ERROR] libjpeg_message_code=%d buffer_capacity=%u\n",ctx->error.base.msg_code,capacity);
        failure=-5;goto cleanup;
    }
    ctx->api.create(&ctx->c,80,sizeof(ctx->c));
    ctx->c.image_width=width;ctx->c.image_height=height;
    ctx->c.input_components=3;ctx->c.in_color_space=JCS_YCbCr;
    ctx->api.defaults(&ctx->c);
    if(software_quality) {
        ctx->api.quality(&ctx->c,(int)software_quality,TRUE);
        result->quality_policy="explicit-ijg-software-quality";
    } else {
        unsigned int tables[2][64];
        for(unsigned int component=0;component<2;++component)
            for(unsigned int i=0;i<64;++i) tables[component][i]=nx_stock_quant[task->quality][component][i];
        ctx->api.quant(&ctx->c,0,tables[0],100,TRUE);ctx->api.quant(&ctx->c,1,tables[1],100,TRUE);
        result->quant_table_index=nx_stock_quant_indices[task->quality];
        result->quality_policy="stock-quant-tables-cpu-entropy";
    }
    /* Planes are already subsampled. Match the stock raw-data wrapper so
     * JPEG8 does not select enlarged chroma DCT input blocks. */
    ctx->c.raw_data_in=TRUE;ctx->c.do_fancy_downsampling=FALSE;
    ctx->c.dct_method=JDCT_ISLOW;
    ctx->c.comp_info[0].h_samp_factor=2;ctx->c.comp_info[0].v_samp_factor=(int)vfactor;
    for(unsigned int i=1;i<3;++i) {ctx->c.comp_info[i].h_samp_factor=1;ctx->c.comp_info[i].v_samp_factor=1;}
    ctx->destination.data=ctx->encoded;ctx->destination.capacity=capacity;
    ctx->destination.base.init_destination=nx_destination_start;
    ctx->destination.base.empty_output_buffer=nx_destination_full;
    ctx->destination.base.term_destination=nx_destination_finish;
    ctx->c.dest=&ctx->destination.base;
    ctx->api.start(&ctx->c,TRUE);
    JSAMPROW yy[16],uu[8],vv[8];JSAMPARRAY components[3]={yy,uu,vv};
    for(unsigned int i=0;i<yr;++i) yy[i]=ctx->rows+i*yp;
    for(unsigned int i=0;i<8;++i) {uu[i]=ctx->rows+yp*yr+i*cp;vv[i]=ctx->rows+yp*yr+cp*8+i*cp;}
    for(uint32_t line=0;line<height;line+=yr) {
        for(uint32_t row=0;row<yr;++row) {
            const unsigned char *source=nx_row(&y,line+row);
            memcpy(yy[row],source,width);memset(yy[row]+width,source[width-1],yp-width);
        }
        for(uint32_t row=0;row<8;++row) {
            const unsigned char *first=nx_row(&cb,line/vfactor+row);
            const unsigned char *second=task->pack_type?nx_row(&cr,line/vfactor+row):NULL;
            for(uint32_t x=0;x<cw;++x) {
                uu[row][x]=second?first[x]:first[2*x+(order==NX_JPEG_INPUT_CRCB)];
                vv[row][x]=second?second[x]:first[2*x+(order==NX_JPEG_INPUT_CBCR)];
            }
            memset(uu[row]+cw,uu[row][cw-1],cp-cw);memset(vv[row]+cw,vv[row][cw-1],cp-cw);
        }
        if(ctx->api.raw(&ctx->c,components,yr)!=yr) {failure=-5;goto cleanup;}
    }
    ctx->api.finish(&ctx->c);
    size_t bytes=capacity-ctx->destination.base.free_in_buffer;
    if(bytes<4 || bytes>capacity || ctx->encoded[0]!=255 || ctx->encoded[1]!=216 ||
            ctx->encoded[bytes-2]!=255 || ctx->encoded[bytes-1]!=217) {failure=-5;goto cleanup;}
    memcpy(output,ctx->encoded,bytes);task->size=(uint32_t)bytes;
    result->bytes=task->size;result->sampling=task->ycc_type?420:422;result->failure_stage=NULL;failure=0;
cleanup:
    if(ctx->c.mem) ctx->api.destroy(&ctx->c);
    dlclose(ctx->api.library);
    free(ctx->rows);free(ctx->encoded);free(ctx);
rejected:
    result->error=failure;return failure;
}

#include "jpeg_diagnostic.h"
#include "software_isp.h"
unsigned int nx300m_jpeg_encode_hook(void *,struct nx300m_jpeg_task *,unsigned int)
    __asm__("_ZN7DscJfif10JpegEncodeER7sJpegOpb");
static void nx_trace_task(const struct nx300m_jpeg_task *task,unsigned int synchronous,
        uint32_t capacity,int cpu_enabled) {
    struct nx300m_jpeg_layout layout;
    int error=nx300m_jpeg_describe_task(task,capacity,&layout);
    int (*copy_live)(uint32_t,uint32_t,void *)=dlsym(RTLD_DEFAULT,"nx300m_copy_live_virtual");
    char line[4096];size_t used=0;
#define NX_TRACE(...) do { int n=snprintf(line+used,sizeof(line)-used,__VA_ARGS__); \
    if(n<0 || (size_t)n>=sizeof(line)-used) { return; } used+=(size_t)n; } while(0)
    NX_TRACE("[JPEG TASK] {\"guest_pid\":%u,\"guest_tid\":%ld,\"cpu_enabled\":%s,\"synchronous\":%u,\"task_present\":%s,\"capacity\":%u,\"layout_error\":%d,\"layout_rejection\":\"%s\",\"producer_authorization_checked\":false",
        (unsigned int)getpid(),syscall(SYS_gettid),cpu_enabled?"true":"false",synchronous,task?"true":"false",
        capacity,error,layout.rejection?layout.rejection:"");
    if(task) NX_TRACE(",\"task\":{\"width\":%u,\"height\":%u,\"y_even\":%u,\"y_odd\":%u,\"cb_even\":%u,\"cb_odd\":%u,\"cr_even\":%u,\"cr_odd\":%u,\"stride_y\":%u,\"stride_c\":%u,\"output\":%u,\"ycc_type\":%u,\"size\":%u,\"pack_type\":%u,\"quality\":%u,\"timeout\":%u}",
        task->width,task->height,task->y_even,task->y_odd,task->cb_even,task->cb_odd,
        task->cr_even,task->cr_odd,task->stride_y,task->stride_c,task->output,
        task->ycc_type,task->size,task->pack_type,task->quality,task->timeout);
    NX_TRACE(",\"ranges\":[");
    for(uint32_t i=0;i<layout.count;++i) {
        const struct nx300m_jpeg_range *range=&layout.ranges[i];
        NX_TRACE("%s{\"role\":%u,\"address\":%u,\"length\":%u,\"writable\":%s",
            i?",":"",range->role,range->address,range->length,range->writable?"true":"false");
        /* Small thumbnails can be inspected completely, while large main
         * images remain bounded. Neither case authorizes an input producer. */
        if(!range->writable && copy_live) {
            uint32_t length=range->length<=262144?range->length:64;
            unsigned char *sample=malloc(length);
            int copied=sample?copy_live(range->address,length,sample):-12;
            unsigned int zero=0,low=255,high=0;
            if(!copied) for(uint32_t j=0;j<length;++j) {
                zero+=!sample[j];if(sample[j]<low)low=sample[j];if(sample[j]>high)high=sample[j];
            }
            free(sample);
            NX_TRACE(",\"sample_error\":%d",copied);
            if(!copied) {
                NX_TRACE(",\"sample_length\":%u,\"sample_full_range\":%s,\"sample_zero_bytes\":%u,\"sample_min\":%u,\"sample_max\":%u",
                    length,length==range->length?"true":"false",zero,low,high);
            }
        }
        NX_TRACE("}");
    }
    NX_TRACE("]}\n");
#undef NX_TRACE
    /* Stay below PIPE_BUF and emit one record before the stock call can abort. */
    (void)write(STDERR_FILENO,line,used);
}
#define NX_ENCODE_JFIF "_ZN7DscJfif10EncodeJfifEjjjjjjjj12eImgPackMode11eImgYCCType11eImgQualityRj"
typedef unsigned int (*nx_encode_jfif_fn)(void *,uint32_t,uint32_t,uint32_t,uint32_t,
    uint32_t,uint32_t,uint32_t,uint32_t,int,int,int,uint32_t *);
unsigned int nx300m_encode_jfif_trace(void *,uint32_t,uint32_t,uint32_t,uint32_t,
    uint32_t,uint32_t,uint32_t,uint32_t,int,int,int,uint32_t *) __asm__(NX_ENCODE_JFIF);
unsigned int nx300m_encode_jfif_trace(void *self,uint32_t output,uint32_t width,
        uint32_t height,uint32_t buffer_height,uint32_t stride,uint32_t input,
        uint32_t byte_offset,uint32_t row_offset,int pack,int ycc,int quality,uint32_t *bytes) {
    nx_encode_jfif_fn original=(nx_encode_jfif_fn)dlsym(RTLD_NEXT,NX_ENCODE_JFIF);
    const char *trace=getenv("NX300M_JPEG_TASK_TRACE");
    if(trace && !strcmp(trace,"1")) {
        void *caller=__builtin_return_address(0);Dl_info info;memset(&info,0,sizeof(info));
        int found=dladdr(caller,&info);char line[1536];
        int count=snprintf(line,sizeof(line),"[JPEG CALLER] {\"guest_pid\":%u,\"guest_tid\":%ld,\"caller_return_address\":%u,\"caller_library\":\"%s\",\"caller_return_offset\":%u,\"width\":%u,\"height\":%u,\"buffer_height\":%u,\"stride\":%u,\"input\":%u,\"output\":%u,\"byte_offset\":%u,\"row_offset\":%u,\"pack_mode\":%d,\"ycc_type\":%d,\"quality\":%d,\"original_found\":%s}\n",
            (unsigned)getpid(),syscall(SYS_gettid),(uint32_t)(uintptr_t)caller,
            found?info.dli_fname:"",found?(uint32_t)((uintptr_t)caller-(uintptr_t)info.dli_fbase):0,
            width,height,buffer_height,stride,input,output,byte_offset,row_offset,
            pack,ycc,quality,original?"true":"false");
        if(count>0 && (size_t)count<sizeof(line)) (void)write(STDERR_FILENO,line,(size_t)count);
    }
    return original?original(self,output,width,height,buffer_height,stride,input,
        byte_offset,row_offset,pack,ycc,quality,bytes):0;
}
unsigned int nx300m_jpeg_encode_hook(void *self,struct nx300m_jpeg_task *task,unsigned int synchronous) {
    const char *enabled=getenv("NX300M_CPU_JPEG");
    int cpu_enabled=enabled && !strcmp(enabled,"1");
    const char *diagnostic=getenv("NX300M_JPEG_DIAGNOSTIC");
    const char *isolated=getenv("NX300M_JPEG_DIAGNOSTIC_ISOLATED");
    int diagnostic_enabled=diagnostic && !strcmp(diagnostic,"1") &&
        isolated && !strcmp(isolated,"1");
    const char *trace=getenv("NX300M_JPEG_TASK_TRACE");
    const char *software_isp=getenv("NX300M_SOFTWARE_ISP_SOURCE");
    int software_isp_enabled=software_isp && *software_isp;
    uint32_t (*maximum)(uint32_t,uint32_t,uint32_t,unsigned int,int)=NULL;
    if(cpu_enabled || diagnostic_enabled || software_isp_enabled || (trace && !strcmp(trace,"1")))
        maximum=dlsym(RTLD_DEFAULT,"_ZN8DscUtils16GetMaxEncodeSizeEjj11eImgQualitybi");
    uint32_t capacity=task && maximum?maximum(task->width,task->height,task->quality,0,12):0;
    if(trace && !strcmp(trace,"1")) nx_trace_task(task,synchronous,capacity,cpu_enabled);
    if(software_isp_enabled) return nx_software_isp_encode(task,capacity)?0:1;
    if(diagnostic_enabled) return nx_diagnostic_encode(task,capacity)?0:1;
    if(!cpu_enabled) {
        unsigned int (*original)(void *,struct nx300m_jpeg_task *,unsigned int)=
            dlsym(RTLD_NEXT,"_ZN7DscJfif10JpegEncodeER7sJpegOpb");
        return original?original(self,task,synchronous):0;
    }
    if(!task) return 0;
    task->size=0;
    /* Stock EncodeJfif mode 1 places Cr using a half-pitch plane size but
     * passes full-pitch deltaC. Hardware planar addressing is unresolved;
     * the standalone CPU API's explicit planar stride is not that ABI. */
    if(task->pack_type!=0) {
        fprintf(stderr,"[CPU JPEG] stock pack=%u unsupported; planar hardware stride unresolved\n",task->pack_type);
        return 0;
    }
    struct nx300m_jpeg_memory_ops ops;
    ops.map_address=dlsym(RTLD_DEFAULT,"nx300m_jpeg_map_address");
    ops.input_ready=dlsym(RTLD_DEFAULT,"nx300m_jpeg_input_ready");
    const char *policy=getenv("NX300M_CPU_JPEG_QUALITY");unsigned int quality=0;
    if(policy) {char *end;long number=strtol(policy,&end,10);if(!*policy || *end || number<1 || number>100) return 0;quality=(unsigned int)number;}
    struct nx300m_jpeg_cpu_result result;
    int error=maximum?nx300m_jpeg_cpu_encode(task,capacity,&ops,quality,&result):-2;
    fprintf(stderr,"[CPU JPEG] %ux%u ycc=%u pack=%u quality=%u size=%u error=%d policy=%s stage=%s hardware_bit_exact=0\n",
        task->width,task->height,task->ycc_type,task->pack_type,task->quality,task->size,error,
        !error?result.quality_policy:"rejected",!maximum?"maximum-size-provider":result.failure_stage?result.failure_stage:"");
    return error?0:1;
}
