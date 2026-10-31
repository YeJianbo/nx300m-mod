/* Software pixels at the original libmm-photo playback task boundaries.
 * CYcc is the 0x24-byte NX300M 1.15 libmm-type object. Its progressive scan
 * stores alternating rows at Y0/Y1 and C0/C1; each parity advances 2*stride.
 * Explicit preload opts into software CbCr and opaque little-endian BGRA.
 * These are laboratory pixel conventions, not DRIMeIV bit-exact processing.
 */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <dlfcn.h>

struct nx_cycc {
    uint32_t width,height,stride,y0,y1,c0,c1,scan,type;
};
typedef char nx_cycc_size[sizeof(struct nx_cycc)==0x24?1:-1];
typedef char nx_cycc_scan[offsetof(struct nx_cycc,scan)==0x1c?1:-1];
typedef void *(*nx_map_fn)(uint32_t,uint32_t,uint32_t *,int);
struct nx_plane { unsigned char *row[2];uint32_t step,width,height; };
struct nx_image { struct nx_plane y,c; };

#define NX_MAX_EDGE 8192u
#define NX_MAX_STRIDE 32768u
#define NX_MAX_SCRATCH (128u*1024u*1024u)

static int nx_plane_map(struct nx_plane *plane,nx_map_fn map,uint32_t even,
                        uint32_t odd,uint32_t stride,uint32_t width,uint32_t height,int access) {
    uint32_t addresses[2]={even,odd},counts[2]={(height+1)/2,height/2};
    plane->step=stride*2;plane->width=width;plane->height=height;
    for (unsigned i=0;i<2;i++) {
        plane->row[i]=NULL;
        if (!counts[i]) continue;
        uint64_t bytes=(uint64_t)(counts[i]-1)*plane->step+width;
        if (!addresses[i] || bytes>UINT32_MAX || bytes+addresses[i]>0x100000000ULL) return -3;
        plane->row[i]=map(addresses[i],(uint32_t)bytes,NULL,access);
        if (!plane->row[i]) return -3;
    }
    return 0;
}

static int nx_image_map(const struct nx_cycc *image,struct nx_image *mapped,nx_map_fn map,int access) {
    /* Other scan modes and odd-width packing have not been established for
     * this task boundary. Reject before inspecting or changing any pixels. */
    if (!image || !map || !image->width || !image->height ||
        image->width>NX_MAX_EDGE || image->height>NX_MAX_EDGE || (image->width&1) ||
        image->stride<image->width || image->stride>NX_MAX_STRIDE ||
        image->scan!=1 || image->type>1) return -2;
    int result=nx_plane_map(&mapped->y,map,image->y0,image->y1,image->stride,image->width,image->height,access);
    if (result) return result;
    return nx_plane_map(&mapped->c,map,image->c0,image->c1,image->stride,image->width,
                        image->type?(image->height+1)/2:image->height,access);
}

static unsigned char *nx_row(const struct nx_plane *plane,uint32_t row) {
    return plane->row[row&1]+(size_t)(row/2)*plane->step;
}

static nx_map_fn nx_mapper(void) {
    return (nx_map_fn)dlsym(RTLD_DEFAULT,"nx300m_map_virtual_access");
}

static unsigned char *nx_snapshot(const struct nx_image *image,size_t *length) {
    uint64_t bytes=(uint64_t)image->y.width*(image->y.height+image->c.height);
    if (bytes>NX_MAX_SCRATCH) return NULL;
    unsigned char *copy=malloc((size_t)bytes);
    if (!copy) return NULL;
    for (uint32_t y=0;y<image->y.height;y++)
        memcpy(copy+(size_t)y*image->y.width,nx_row(&image->y,y),image->y.width);
    unsigned char *c=copy+(size_t)image->y.width*image->y.height;
    for (uint32_t y=0;y<image->c.height;y++)
        memcpy(c+(size_t)y*image->c.width,nx_row(&image->c,y),image->c.width);
    *length=(size_t)bytes;return copy;
}

static uint32_t nx_coordinate(uint32_t at,uint32_t source,uint32_t target) {
    if (target<=1) return 0;
    uint32_t product=at*(source-1),divisor=target-1;
    return (product/divisor)*256+(product%divisor)*256/divisor;
}

static void nx_resize_plane(const unsigned char *source,uint32_t width,uint32_t height,
                            const struct nx_plane *target,uint32_t components) {
    uint32_t target_width=target->width/components,source_stride=width*components;
    for (uint32_t y=0;y<target->height;y++) {
        uint32_t fy=nx_coordinate(y,height,target->height),y0=fy/256;
        uint32_t y1=y0+1<height?y0+1:y0;fy%=256;
        unsigned char *row=nx_row(target,y);
        for (uint32_t x=0;x<target_width;x++) {
            uint32_t fx=nx_coordinate(x,width,target_width),x0=fx/256;
            uint32_t x1=x0+1<width?x0+1:x0;fx%=256;
            for (uint32_t c=0;c<components;c++) {
                uint32_t a=source[(size_t)y0*source_stride+x0*components+c];
                uint32_t b=source[(size_t)y0*source_stride+x1*components+c];
                uint32_t d=source[(size_t)y1*source_stride+x0*components+c];
                uint32_t e=source[(size_t)y1*source_stride+x1*components+c];
                row[x*components+c]=(unsigned char)(((a*(256-fx)+b*fx)*(256-fy)+
                    (d*(256-fx)+e*fx)*fy+32768)/65536);
            }
        }
    }
}

int nx_playback_resize(void *,const struct nx_cycc *,const struct nx_cycc *)
    __asm__("_ZN15CMmDeviceDriver11ResizeImageER4CYccS1_");
static int nx_resize_pixels(const struct nx_cycc *source,const struct nx_cycc *target,const char *stage) {
    struct nx_image input,output;nx_map_fn map=nx_mapper();
    int error=nx_image_map(source,&input,map,1);size_t bytes=0;unsigned char *copy=NULL;
    if (!error) error=nx_image_map(target,&output,map,2);
    if (!error) {
        copy=nx_snapshot(&input,&bytes);
        if (!copy) error=-4;
    }
    if (!error) {
        nx_resize_plane(copy,input.y.width,input.y.height,&output.y,1);
        nx_resize_plane(copy+(size_t)input.y.width*input.y.height,input.c.width/2,input.c.height,&output.c,2);
        const unsigned char *tail=copy+(size_t)input.y.width*input.y.height+(size_t)(input.c.height-1)*input.c.width;
        const unsigned char *out_tail=nx_row(&output.c,output.c.height-1);
        fprintf(stderr,"[CPU PLAYBACK TAIL] stage=%s source_c=%08x,%08x target_c=%08x,%08x input_last=%u,%u output_last=%u,%u\n",
            stage,source->c0,source->c1,target->c0,target->c1,tail[0],tail[1],out_tail[0],out_tail[1]);
    }
    free(copy);
    fprintf(stderr,"[CPU PLAYBACK] resize stage=%s src=%ux%u stride=%u type=%u dst=%ux%u stride=%u type=%u bytes=%u error=%d layout=software-cbcr hardware_bit_exact=0\n",
        stage,source?source->width:0,source?source->height:0,source?source->stride:0,source?source->type:0,
        target?target->width:0,target?target->height:0,target?target->stride:0,target?target->type:0,
        (unsigned)bytes,error);
    return error?-1:1;
}

int nx_playback_resize(void *self,const struct nx_cycc *source,const struct nx_cycc *target) {
    (void)self;return nx_resize_pixels(source,target,"mm-photo");
}

/* libsif owns another real resize task before libmm-photo sees its YCC.
 * DscImage::Resize -> DscJfifHal::ResizeYCC builds the same CYcc objects and
 * calls this free function, including its own multi-stage downscale policy.
 * Preserve that policy and its boolean ABI rather than replacing image state. */
int nx_sif_resize(const struct nx_cycc *,const struct nx_cycc *)
    __asm__("_Z9resizeOneR4CYccS0_");
int nx_sif_resize(const struct nx_cycc *source,const struct nx_cycc *target) {
    typedef int (*query_fn)(uint32_t,uint32_t,uint32_t,uint32_t,uint32_t,uint32_t,
                            uint32_t,uint32_t,uint32_t *,uint32_t *);
    query_fn query=(query_fn)dlsym(RTLD_DEFAULT,"nx300m_jpeg_query_cbcr");
    struct nx_cycc decoded;
    uint32_t c0=0,c1=0;
    /* DscImage's address reconstruction adds one full row to both C bases.
     * Correct only an exact match to pixels actually produced by our decoder:
     * the query checks dimensions, Y bases, both parity deltas, type and that
     * precise one-row offset. Unrelated and later-stage CYcc stay unchanged. */
    if (source && source->scan==1 && source->stride && source->stride<=NX_MAX_STRIDE &&
        query && query(source->width,source->height,source->stride,source->type,
                       source->y0,source->y1,source->c0,source->c1,&c0,&c1)) {
        decoded=*source;decoded.c0=c0;decoded.c1=c1;
        fprintf(stderr,"[CPU PLAYBACK] producer-layout stage=libsif image=%ux%u stride=%u y=%08x,%08x original_c=%08x,%08x decoded_c=%08x,%08x\n",
            source->width,source->height,source->stride,source->y0,source->y1,
            source->c0,source->c1,c0,c1);
        return nx_resize_pixels(&decoded,target,"libsif")==1;
    }
    return nx_resize_pixels(source,target,"libsif")==1;
}

static unsigned char nx_clip(int value) {
    return value<0?0:value>255?255:(unsigned char)value;
}
static int nx_round(int value) {
    return value>=0?(value+32768)/65536:-((-value+32768)/65536);
}

int nx_playback_rgb(void *,const struct nx_cycc *,uint32_t)
    __asm__("_ZN15CMmDeviceDriver15ConvertYccToRGBER4CYccj");
int nx_playback_rgb(void *self,const struct nx_cycc *source,uint32_t destination) {
    (void)self;struct nx_image input;nx_map_fn map=nx_mapper();
    int error=nx_image_map(source,&input,map,1);size_t bytes=0;unsigned char *copy=NULL,*rgb=NULL;
    uint64_t output_bytes=0;
    /* CRgb publishes only width,height,pointer; Evas has no row-stride field.
     * Snapshot removes input padding, and the separate RGB output is packed
     * at width*4 even when the source YCC rows have a larger stride. */
    if (!error) {
        output_bytes=(uint64_t)source->width*source->height*4;
        if (!destination || output_bytes>UINT32_MAX || output_bytes+destination>0x100000000ULL ||
            !(rgb=map(destination,(uint32_t)output_bytes,NULL,2))) error=-3;
    }
    if (!error) {
        copy=nx_snapshot(&input,&bytes);
        if (!copy) error=-4;
    }
    if (!error) {
        const unsigned char *chroma=copy+(size_t)input.y.width*input.y.height;
        const unsigned char *tail=chroma+(size_t)(input.c.height-1)*input.c.width;
        fprintf(stderr,"[CPU PLAYBACK TAIL] stage=rgb source_c=%08x,%08x input_last=%u,%u\n",source->c0,source->c1,tail[0],tail[1]);
        for (uint32_t y=0;y<input.y.height;y++) {
            const unsigned char *luma=copy+(size_t)y*input.y.width;
            const unsigned char *uv=chroma+(size_t)(source->type?y/2:y)*input.c.width;
            unsigned char *row=rgb+(size_t)y*input.y.width*4;
            for (uint32_t x=0;x<input.y.width;x++) {
                int cb=(int)uv[x&~1u]-128,cr=(int)uv[(x&~1u)+1]-128,yy=luma[x];
                row[x*4]=nx_clip(yy+nx_round(116130*cb));
                row[x*4+1]=nx_clip(yy-nx_round(22554*cb+46802*cr));
                row[x*4+2]=nx_clip(yy+nx_round(91881*cr));
                row[x*4+3]=255;
            }
        }
    }
    free(copy);
    fprintf(stderr,"[CPU PLAYBACK] rgb src=%ux%u stride=%u type=%u output=0x%08x bytes=%u error=%d layout=software-cbcr-bgra full_range_bt601=1 hardware_bit_exact=0\n",
        source?source->width:0,source?source->height:0,source?source->stride:0,source?source->type:0,
        destination,(unsigned)output_bytes,error);
    return error?-1:0;
}
