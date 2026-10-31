/* Execute the stock sJpegOp decode task with the camera's IJG ABI80 library.
 * Addresses are virtual SMA/BMA mappings, never guessed physical addresses.
 * Hardware/file-valid state is owned by the original caller. */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <setjmp.h>
#include <pthread.h>
#include <jpeglib.h>
#include "jpeg_cpu_adapter.h"

extern void *nx300m_map_virtual_access(uint32_t address,uint32_t minimum,uint32_t *remaining,int required);
typedef char nx_decode_task_size[sizeof(struct nx300m_jpeg_task)==0x40?1:-1];
typedef char nx_decode_abi[JPEG_LIB_VERSION==80 && sizeof(void *)==4?1:-1];
/* Stock libjpeg.so.8.3.0 has a 16-byte vendor extension after scale_denom.
 * The builder generates a local header from the IJG headers; these accesses
 * are checked against the original ARM field offsets, not just total size. */
#define NX_JPEG_OFFSET(type,field,value) typedef char nx_offset_##type##_##field[offsetof(struct type,field)==value?1:-1]
typedef char nx_decompress_size[sizeof(struct jpeg_decompress_struct)==504?1:-1];
typedef char nx_component_size[sizeof(jpeg_component_info)==0x58?1:-1];
NX_JPEG_OFFSET(jpeg_decompress_struct,image_width,0x1c);
NX_JPEG_OFFSET(jpeg_decompress_struct,image_height,0x20);
NX_JPEG_OFFSET(jpeg_decompress_struct,num_components,0x24);
NX_JPEG_OFFSET(jpeg_decompress_struct,jpeg_color_space,0x28);
NX_JPEG_OFFSET(jpeg_decompress_struct,out_color_space,0x2c);
NX_JPEG_OFFSET(jpeg_decompress_struct,scale_num,0x30);
NX_JPEG_OFFSET(jpeg_decompress_struct,scale_denom,0x34);
NX_JPEG_OFFSET(jpeg_decompress_struct,raw_data_out,0x54);
NX_JPEG_OFFSET(jpeg_decompress_struct,dct_method,0x58);
NX_JPEG_OFFSET(jpeg_decompress_struct,do_fancy_upsampling,0x5c);
NX_JPEG_OFFSET(jpeg_decompress_struct,output_width,0x80);
NX_JPEG_OFFSET(jpeg_decompress_struct,output_height,0x84);
NX_JPEG_OFFSET(jpeg_decompress_struct,comp_info,0xe8);
#undef NX_JPEG_OFFSET
#define NX_DECODE_MAX_INPUT (64u*1024u*1024u)
#define NX_DECODE_MAX_PIXELS (8192u*8192u)
#define NX_DECODE_MAX_SCRATCH (128u*1024u*1024u)

struct nx_completed_decode {
    uint32_t width,height,y0,y1,c0,c1,delta_y,delta_c,type;
    int valid;
};
static struct nx_completed_decode completed[8];
static unsigned completed_next;
static pthread_mutex_t completed_lock=PTHREAD_MUTEX_INITIALIZER;

static void remember_decode(const struct nx300m_jpeg_task *task) {
    if (task->pack_type) return;
    struct nx_completed_decode record={task->width,task->height,task->y_even,task->y_odd,
        task->cb_even,task->cb_odd,task->stride_y,task->stride_c,task->ycc_type,1};
    pthread_mutex_lock(&completed_lock);
    unsigned slot;
    for (slot=0;slot<8;slot++) if (completed[slot].valid &&
            completed[slot].y0==record.y0 && completed[slot].y1==record.y1) break;
    if (slot==8) slot=completed_next++%8;
    completed[slot]=record;
    pthread_mutex_unlock(&completed_lock);
}

/* Original DscImage::Resize's 0x53b5c helper reconstructs interleaved C0 as
 * Y1+width*height, one row past the actual decoder task's C0. The playback
 * adapter may correct only that exact completed producer/consumer mismatch.
 * Lookup never changes pixels, original task state or registry lifetime. */
int nx300m_jpeg_query_cbcr(uint32_t width,uint32_t height,uint32_t stride,uint32_t type,
        uint32_t y0,uint32_t y1,uint32_t c0,uint32_t c1,uint32_t *decoded_c0,uint32_t *decoded_c1) {
    if (!decoded_c0 || !decoded_c1 || !stride || stride>UINT32_MAX/2) return 0;
    int found=0;uint32_t first=0,second=0;
    pthread_mutex_lock(&completed_lock);
    for (unsigned i=0;i<8;i++) {
        const struct nx_completed_decode *r=completed+i;
        if (r->valid && r->width==width && r->height==height && r->type==type &&
            r->y0==y0 && r->y1==y1 && r->delta_y==2*stride && r->delta_c==2*stride &&
            r->c0<=UINT32_MAX-stride && r->c1<=UINT32_MAX-stride &&
            c0==r->c0+stride && c1==r->c1+stride) {
            first=r->c0;second=r->c1;found=1;break;
        }
    }
    pthread_mutex_unlock(&completed_lock);
    if (found) {*decoded_c0=first;*decoded_c1=second;}
    return found;
}

struct nx_decode_plane { unsigned char *base[2];uint32_t stride,width,rows;int split; };
struct nx_decode_error { struct jpeg_error_mgr base;jmp_buf jump; };
struct nx_decode_context {
    struct jpeg_decompress_struct jpeg;
    struct nx_decode_error error;
    unsigned char *samples;
    const char *stage;
    int created;
};

/* Length words are not supplied by JpegDecode. Walk marker segments and entropy
 * escapes inside the mapped input span; an EXIF thumbnail's EOI is not ours. */
static uint32_t jpeg_length(const unsigned char *p,uint32_t capacity) {
    if (capacity<4 || p[0]!=0xff || p[1]!=0xd8) return 0;
    uint32_t at=2;int entropy=0,frame=0,scan=0;
    while (at<capacity) {
        unsigned marker;
        if (entropy) {
            while (at<capacity && p[at]!=0xff) at++;
            if (at==capacity) return 0;
        } else if (p[at]!=0xff) return 0;
        while (at<capacity && p[at]==0xff) at++;
        if (at==capacity) return 0;
        marker=p[at++];
        if (entropy && (marker==0 || (marker>=0xd0 && marker<=0xd7))) continue;
        entropy=0;
        if (marker==0xd9) return frame && scan?at:0;
        if (marker<0xc0 || marker==0xd8 || (marker>=0xd0 && marker<=0xd7)) return 0;
        if (capacity-at<2) return 0;
        uint32_t length=((uint32_t)p[at]<<8)|p[at+1];
        if (length<2 || length>capacity-at) return 0;
        if (marker==0xc0 || marker==0xc1 || marker==0xc2) {
            if (frame || length<8 || p[at+2]!=8) return 0;
            frame=1;
        }
        if (marker==0xda) {
            if (!frame || length<6) return 0;
            scan=entropy=1;
        }
        at+=length;
    }
    return 0;
}
static void decode_error(j_common_ptr jpeg) {
    struct nx_decode_error *error=(struct nx_decode_error *)jpeg->err;
    char description[JMSG_LENGTH_MAX];jpeg->err->format_message(jpeg,description);
    fprintf(stderr,"[NX JPEG] libjpeg rejected input: %s\n",description);
    longjmp(error->jump,1);
}
static void decode_message(j_common_ptr jpeg,int level) {
    /* IJG can recover corrupt entropy or synthesize an EOI. Such recovery is
     * not a completed original task, even if it produces some scanlines. */
    if (level<0) decode_error(jpeg);
}
static int map_plane(struct nx_decode_plane *plane,uint32_t even,uint32_t odd,
                     uint32_t stride,uint32_t width,uint32_t rows,int split) {
    if (!even || !width || !rows || stride<width || stride>131072) return 0;
    plane->stride=stride;plane->width=width;plane->rows=rows;plane->split=split;
    uint32_t counts[2]={split?(rows+1)/2:rows,split?rows/2:0};
    uint32_t addresses[2]={even,odd};
    for (unsigned i=0;i<2;i++) if (counts[i]) {
        uint64_t span=(uint64_t)(counts[i]-1)*stride+width;
        if (!addresses[i] || span>UINT32_MAX || span+addresses[i]>0x100000000ULL) return 0;
        plane->base[i]=nx300m_map_virtual_access(addresses[i],(uint32_t)span,NULL,2);
        if (!plane->base[i]) return 0;
    }
    return 1;
}
static unsigned char *plane_row(const struct nx_decode_plane *plane,uint32_t row) {
    unsigned part=plane->split?(row&1):0;
    uint32_t line=plane->split?row/2:row;
    return plane->base[part]+(size_t)line*plane->stride;
}
static void copy_plane(const struct nx_decode_plane *plane,const unsigned char *source,uint32_t pitch) {
    for (uint32_t row=0;row<plane->rows;row++) {
        memcpy(plane_row(plane,row),source+(size_t)row*pitch,plane->width);
    }
}

/* Boolean ABI: leave task fields and all output bytes alone on every failure.
 * Explicit module preload opts into pack0=Y/CbCr, pack1=Y/Cb/Cr. This does not claim
 * that hardware DMA/register plane ordering has been proved bit-exact. */
int nx300m_jpeg_decode_task(struct nx300m_jpeg_task *task) {
    if (!task || task->pack_type>1) return 0;
    uint32_t available=0;
    const unsigned char *input=nx300m_map_virtual_access(task->output,2,&available,1);
    if (!input) return 0;
    if (available>NX_DECODE_MAX_INPUT) available=NX_DECODE_MAX_INPUT;
    uint32_t length=jpeg_length(input,available);
    if (!length) return 0;
    struct nx_decode_context *ctx=calloc(1,sizeof(*ctx));
    if (!ctx) return 0;
    volatile int success=0;
    ctx->jpeg.err=jpeg_std_error(&ctx->error.base);
    ctx->error.base.error_exit=decode_error;ctx->error.base.emit_message=decode_message;
    if (setjmp(ctx->error.jump)) goto cleanup;
    ctx->stage="header";ctx->created=1;jpeg_create_decompress(&ctx->jpeg);
    jpeg_mem_src(&ctx->jpeg,(unsigned char *)input,length);
    if (jpeg_read_header(&ctx->jpeg,TRUE)!=JPEG_HEADER_OK) goto cleanup;
    ctx->stage="dimensions/sampling";
    uint32_t width=ctx->jpeg.image_width,height=ctx->jpeg.image_height;
    if (!width || !height || width>8192 || height>8192 ||
        (uint64_t)width*height>NX_DECODE_MAX_PIXELS || ctx->jpeg.num_components!=3 ||
        ctx->jpeg.jpeg_color_space!=JCS_YCbCr) goto cleanup;
    jpeg_component_info *component=ctx->jpeg.comp_info;
    if (component[0].h_samp_factor!=2 || (component[0].v_samp_factor!=1 && component[0].v_samp_factor!=2) ||
        component[1].h_samp_factor!=1 || component[1].v_samp_factor!=1 ||
        component[2].h_samp_factor!=1 || component[2].v_samp_factor!=1) goto cleanup;
    uint32_t vertical=component[0].v_samp_factor;
    uint32_t widths[3]={width,(width+1)/2,(width+1)/2};
    uint32_t heights[3]={height,(height+vertical-1)/vertical,(height+vertical-1)/vertical};
    int split=task->y_odd!=0;
    struct nx_decode_plane planes[3];memset(planes,0,sizeof planes);
    ctx->stage="output spans";
    if (!map_plane(planes,task->y_even,task->y_odd,task->stride_y,width,height,split) ||
        !map_plane(planes+1,task->cb_even,task->cb_odd,task->stride_c,
            task->pack_type?widths[1]:2*widths[1],heights[1],split) ||
        (task->pack_type && !map_plane(planes+2,task->cr_even,task->cr_odd,
            task->stride_c,widths[2],heights[2],split))) goto cleanup;
    fprintf(stderr,"[NX JPEG TASK] image=%ux%u y=%08x,%08x c=%08x,%08x cr=%08x,%08x delta=%u,%u split=%d pack=%u\n",
        width,height,task->y_even,task->y_odd,task->cb_even,task->cb_odd,task->cr_even,task->cr_odd,
        task->stride_y,task->stride_c,split,task->pack_type);
    ctx->jpeg.raw_data_out=TRUE;ctx->jpeg.out_color_space=JCS_YCbCr;
    ctx->jpeg.scale_num=ctx->jpeg.scale_denom=1;ctx->jpeg.dct_method=JDCT_ISLOW;
    ctx->jpeg.do_fancy_upsampling=FALSE;
    ctx->stage="raw startup";
    if (!jpeg_start_decompress(&ctx->jpeg) || ctx->jpeg.output_width!=width || ctx->jpeg.output_height!=height) goto cleanup;
    uint32_t mcu_height=8*vertical,mcu_rows=(height+mcu_height-1)/mcu_height;
    uint32_t pitches[3],lines[3];size_t offsets[3],bytes=0;
    ctx->stage="raw component layout";
    for (unsigned i=0;i<3;i++) {
        pitches[i]=component[i].width_in_blocks*DCTSIZE;
        lines[i]=component[i].v_samp_factor*DCTSIZE;
        if (component[i].DCT_h_scaled_size!=DCTSIZE || component[i].DCT_v_scaled_size!=DCTSIZE ||
            component[i].downsampled_width!=widths[i] || component[i].downsampled_height!=heights[i]) goto cleanup;
        offsets[i]=bytes;uint64_t plane_bytes=(uint64_t)pitches[i]*lines[i]*mcu_rows;
        if (plane_bytes>NX_DECODE_MAX_SCRATCH-bytes) goto cleanup;
        bytes+=(size_t)plane_bytes;
    }
    ctx->samples=malloc(bytes);if (!ctx->samples) goto cleanup;
    ctx->stage="raw pixels";
    for (uint32_t mcu=0;mcu<mcu_rows;mcu++) {
        JSAMPROW rows[3][16];JSAMPARRAY image[3]={rows[0],rows[1],rows[2]};
        for (unsigned i=0;i<3;i++) for (uint32_t row=0;row<lines[i];row++)
            rows[i][row]=ctx->samples+offsets[i]+((size_t)mcu*lines[i]+row)*pitches[i];
        if (jpeg_read_raw_data(&ctx->jpeg,image,mcu_height)!=mcu_height) goto cleanup;
    }
    ctx->stage="EOI completion";if (!jpeg_finish_decompress(&ctx->jpeg)) goto cleanup;
    copy_plane(planes,ctx->samples,pitches[0]);
    if (task->pack_type) {
        for (unsigned i=1;i<3;i++) copy_plane(planes+i,ctx->samples+offsets[i],pitches[i]);
    } else for (uint32_t row=0;row<heights[1];row++) {
        unsigned char *out=plane_row(planes+1,row);
        const unsigned char *cb=ctx->samples+offsets[1]+(size_t)row*pitches[1];
        const unsigned char *cr=ctx->samples+offsets[2]+(size_t)row*pitches[2];
        for (uint32_t x=0;x<widths[1];x++) {out[2*x]=cb[x];out[2*x+1]=cr[x];}
    }
    {
        uint32_t last=heights[1]-1;
        const unsigned char *cb=ctx->samples+offsets[1]+(size_t)last*pitches[1];
        const unsigned char *cr=ctx->samples+offsets[2]+(size_t)last*pitches[2];
        const unsigned char *out=plane_row(planes+1,last);
        fprintf(stderr,"[NX JPEG TAIL] row=%u address=%08x raw=%u,%u output=%u,%u\n",
            last,(uint32_t)(uintptr_t)out,cb[0],cr[0],out[0],task->pack_type?plane_row(planes+2,last)[0]:out[1]);
    }
    task->width=width;task->height=height;task->ycc_type=vertical==2?1:0;
    remember_decode(task);success=1;
    fprintf(stderr,"[NX JPEG] software decode %ux%u ycc=%u pack=%u bytes=%u layout=software-cbcr\n",width,height,task->ycc_type,task->pack_type,length);
cleanup:
    if (!success) fprintf(stderr,"[NX JPEG] decode failed at %s\n",ctx->stage?ctx->stage:"initialization");
    if (ctx->created) jpeg_destroy_decompress(&ctx->jpeg);
    free(ctx->samples);free(ctx);return success;
}

int nx_decode_hook(void *self,struct nx300m_jpeg_task *task) __asm__("_ZN7DscJfif10JpegDecodeER7sJpegOp");
int nx_decode_hook(void *self,struct nx300m_jpeg_task *task) {
    (void)self;
    int result=nx300m_jpeg_decode_task(task);
    if (!result) fprintf(stderr,"[NX JPEG] software decode rejected task=%p\n",(void *)task);
    return result;
}
