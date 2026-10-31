/* Opt-in recorded-Bayer software ISP at the genuine JpegEncode task boundary.
 * The equations below are an explicit software model, not DRIMe4 pixel parity.
 * PP DPC/CS/DAMP, sensor readout and hardware JPEG remain unimplemented here. */
#include <errno.h>
struct nx_isp_state {
    int32_t ccm[9], ycc[9];
    uint16_t gamma[8192], lut[3][730];
    uint32_t present, lut_mask, revision;
};
static struct nx_isp_state nx_isp;
#ifndef NX_ISP_CORE_ONLY
static volatile unsigned int nx_isp_lock;
static void nx_isp_acquire(void) { while(__sync_lock_test_and_set(&nx_isp_lock,1)) usleep(100); }
static void nx_isp_release(void) { __sync_lock_release(&nx_isp_lock); }
void ipcs_tone_lut(uint16_t *source) {
    void (*next)(uint16_t *)=dlsym(RTLD_NEXT,"ipcs_tone_lut");
    if(!next) abort();
    next(source);
    if(source) { nx_isp_acquire();memcpy(nx_isp.gamma,source,sizeof(nx_isp.gamma));
        nx_isp.present|=1;nx_isp.revision++;nx_isp_release(); }
}
int ipcs_color_correction_param(void *source) {
    int (*next)(void *)=dlsym(RTLD_NEXT,"ipcs_color_correction_param");
    if(!next) abort();
    int result=next(source);
    if(!result && source) { nx_isp_acquire();memcpy(nx_isp.ccm,source,sizeof(nx_isp.ccm));
        nx_isp.present|=2;nx_isp.revision++;nx_isp_release(); }
    return result;
}
int ipcs_rgb2ycc_param(void *source) {
    int (*next)(void *)=dlsym(RTLD_NEXT,"ipcs_rgb2ycc_param");
    if(!next) abort();
    int result=next(source);const uint32_t *words=source;
    /* The vendor ABI selects the 3x3 RGB-to-YCC matrix with selector 3.
     * Other selectors leave the matrix uninitialized. */
    if(!result && words && words[0]==3) { nx_isp_acquire();
        memcpy(nx_isp.ycc,words+10,sizeof(nx_isp.ycc));
        nx_isp.present|=4;nx_isp.revision++;nx_isp_release(); }
    return result;
}
void ipcs_3d_lut(unsigned int channel,const uint16_t *source) {
    void (*next)(unsigned int,const uint16_t *)=dlsym(RTLD_NEXT,"ipcs_3d_lut");
    if(!next) abort();
    next(channel,source);
    if(source && channel<3) { nx_isp_acquire();
        memcpy(nx_isp.lut[channel],source,sizeof(nx_isp.lut[channel]));
        nx_isp.lut_mask|=1U<<channel;nx_isp.revision++;nx_isp_release(); }
}
#endif
/* Config is generated from the recovered SAME RAW's measured metadata, not
 * an already rendered JPEG. Fixed little-endian ARM words, NXSI version 1. */
struct nx_isp_source {
    uint32_t magic,version,width,height,stride,x,y,crop_width,crop_height;
    uint32_t wb_q12[4],black[4],white;
};
static unsigned int nx_isp_clip(int value,unsigned int high) {
    return value<0?0:(unsigned int)value>high?high:(unsigned int)value;
}
static uint32_t nx_isp_sample(const unsigned char *raw,const struct nx_isp_source *s,
        int x,int y) {
    /* Reflect at the active rectangle, preserving the Bayer parity. No
     * unmeasured reconstructed border participates in interpolation. */
    int left=(int)s->x,top=(int)s->y;
    int right=left+(int)s->crop_width-1,bottom=top+(int)s->crop_height-1;
    if(x<left) x=left+(left-x);
    if(x>right) x=right-(x-right);
    if(y<top) y=top+(top-y);
    if(y>bottom) y=bottom-(y-bottom);
    const unsigned char *p=raw+(uint32_t)y*s->stride+(uint32_t)(x/2)*3;
    return (x&1)?(p[1]>>4)|((uint32_t)p[2]<<4):p[0]|((uint32_t)(p[1]&15)<<8);
}
static void nx_isp_store(unsigned char *raw,const struct nx_isp_source *s,
        unsigned int x,unsigned int y,uint32_t a,uint32_t b) {
    unsigned char *p=raw+y*s->stride+x/2*3;
    p[0]=(unsigned char)a;p[1]=(unsigned char)((a>>8)|((b&15)<<4));p[2]=(unsigned char)(b>>4);
}
static int nx_isp_denoise(unsigned char *raw,const struct nx_isp_source *s) {
    /* A deliberate software low-pass before interpolation. The raw Bayer
     * fixture has sensor noise that the missing PP/BNR hardware would normally
     * process; without any NR, quality0 can exceed stock GetMaxEncodeSize.
     * This small separable same-CFA [1,2,1] filter is not that hardware model. */
    size_t bytes=(size_t)s->stride*s->height;
    unsigned char *horizontal=malloc(bytes);if(!horizontal) return -12;
    memcpy(horizontal,raw,bytes);
    for(unsigned int y=s->y;y<s->y+s->crop_height;y++) for(unsigned int x=s->x;x<s->x+s->crop_width;x+=2) {
        uint32_t values[2];
        for(unsigned int p=0;p<2;p++) {
            int xx=(int)(x+p);
            values[p]=(nx_isp_sample(raw,s,xx-2,(int)y)+2*nx_isp_sample(raw,s,xx,(int)y)+nx_isp_sample(raw,s,xx+2,(int)y)+2)/4;
        }
        nx_isp_store(horizontal,s,x,y,values[0],values[1]);
    }
    for(unsigned int y=s->y;y<s->y+s->crop_height;y++) for(unsigned int x=s->x;x<s->x+s->crop_width;x+=2) {
        uint32_t values[2];
        for(unsigned int p=0;p<2;p++) {
            int xx=(int)(x+p),yy=(int)y;
            values[p]=(nx_isp_sample(horizontal,s,xx,yy-2)+2*nx_isp_sample(horizontal,s,xx,yy)+nx_isp_sample(horizontal,s,xx,yy+2)+2)/4;
        }
        nx_isp_store(raw,s,x,y,values[0],values[1]);
    }
    free(horizontal);return 0;
}
static int nx_isp_reduce(unsigned char **raw,struct nx_isp_source *s,
        unsigned int target_width,unsigned int target_height) {
    /* Preserve GRBG while averaging four same-colour samples per 4x4 Bayer
     * block. Repeated area reduction before interpolation prevents the stock
     * preview/thumbnail JPEG from being fed a point-sampled noisy full frame. */
    while(s->crop_width>=target_width*2 && s->crop_height>=target_height*2) {
        struct nx_isp_source next=*s;
        next.width=next.crop_width=s->crop_width/4*2;
        next.height=next.crop_height=s->crop_height/4*2;
        next.x=next.y=0;next.stride=next.width/2*3;
        unsigned char *reduced=malloc((size_t)next.stride*next.height);
        if(!reduced) return -12;
        for(unsigned int y=0;y<next.height;y++) for(unsigned int x=0;x<next.width;x+=2) {
            uint32_t values[2];
            int sy=(int)(s->y+(y/2)*4+(y&1));
            for(unsigned int p=0;p<2;p++) {
                int sx=(int)(s->x+((x+p)/2)*4+((x+p)&1));
                values[p]=(nx_isp_sample(*raw,s,sx,sy)+nx_isp_sample(*raw,s,sx+2,sy)+
                    nx_isp_sample(*raw,s,sx,sy+2)+nx_isp_sample(*raw,s,sx+2,sy+2)+2)/4;
            }
            nx_isp_store(reduced,&next,x,y,values[0],values[1]);
        }
        free(*raw);*raw=reduced;*s=next;
    }
    return 0;
}
static uint32_t nx_isp_raw(const unsigned char *raw,const struct nx_isp_source *s,
        int x,int y) {
    uint32_t v=nx_isp_sample(raw,s,x,y);
    /* GRBG: LibRaw RGBG indices at even/even, even/odd, odd/even, odd/odd. */
    static const unsigned int index[4]={1,0,2,3};
    unsigned int c=index[(y&1)*2+(x&1)];
    return v>s->black[c]?(v-s->black[c])*s->wb_q12[c]/4096:0;
}
static void nx_isp_rgb(const unsigned char *raw,const struct nx_isp_source *s,
        const struct nx_isp_state *state,int x,int y,unsigned int out[3]) {
    uint32_t c=nx_isp_raw(raw,s,x,y),h=(nx_isp_raw(raw,s,x-1,y)+nx_isp_raw(raw,s,x+1,y)+1)/2;
    uint32_t v=(nx_isp_raw(raw,s,x,y-1)+nx_isp_raw(raw,s,x,y+1)+1)/2;
    uint32_t d=(nx_isp_raw(raw,s,x-1,y-1)+nx_isp_raw(raw,s,x+1,y-1)+
        nx_isp_raw(raw,s,x-1,y+1)+nx_isp_raw(raw,s,x+1,y+1)+2)/4;
    uint32_t camera[3];
    if(!(y&1) && !(x&1)) {camera[0]=h;camera[1]=c;camera[2]=v;}
    else if((y&1) && (x&1)) {camera[0]=v;camera[1]=c;camera[2]=h;}
    else if(x&1) {camera[0]=c;camera[1]=(h+v+1)/2;camera[2]=d;}
    else {camera[0]=d;camera[1]=(h+v+1)/2;camera[2]=c;}
    unsigned int tone[3];
    for(unsigned int ch=0;ch<3;ch++) {
        int32_t value=0;for(unsigned int k=0;k<3;k++) value+=state->ccm[3*ch+k]*(int32_t)camera[k];
        /* Q7 CCM and 12->13bit gamma indexing are software-model choices;
         * the hardware's MAC clipping/rounding and processing order differ. */
        unsigned int linear=nx_isp_clip((int)((value+64)/128),s->white);
        tone[ch]=state->gamma[linear*8191U/4095U];
    }
    unsigned int base[3],frac[3];
    for(unsigned int ch=0;ch<3;ch++) {
        unsigned int position=(tone[ch]*1024U+511U)/1023U;
        base[ch]=position>>7;frac[ch]=position&127U;
        if(base[ch]==8) {base[ch]=7;frac[ch]=128;}
    }
    uint32_t weights[8],nodes[8];unsigned int corner=0;
    for(unsigned int r=0;r<2;r++) for(unsigned int g=0;g<2;g++) for(unsigned int b=0;b<2;b++) {
        nodes[corner]=((base[0]+r)*9+base[1]+g)*9+base[2]+b;
        weights[corner]=(r?frac[0]:128-frac[0])*(g?frac[1]:128-frac[1])*(b?frac[2]:128-frac[2]);
        corner++;
    }
    for(unsigned int ch=0;ch<3;ch++) {
        /* Sum(weights)=2^21 and table values<=1023: the complete sum plus
         * rounding fits uint32. Q7 fractions avoid ARM software u64 division. */
        uint32_t total=0;
        for(unsigned int i=0;i<8;i++) total+=weights[i]*state->lut[ch][nodes[i]];
        out[ch]=(total+(1U<<20))>>21;
    }
}
static int nx_isp_load(const char *packet,const char *config,struct nx_isp_source *source,
        unsigned char **raw) {
    FILE *f=fopen(config,"rb");if(!f) return -errno;
    int valid=fread(source,1,sizeof(*source),f)==sizeof(*source) && fgetc(f)==EOF && !ferror(f);
    if(fclose(f)) valid=0;
    if(!valid || source->magic!=0x4953584e || source->version!=1 ||
            !source->width || source->width>8192 || !source->height || source->height>8192 ||
            source->white!=4095 || source->stride<source->width/2*3 ||
            source->stride>32768 || (source->width&1) ||
            ((source->x|source->y|source->crop_width|source->crop_height)&1) ||
            source->crop_width<4 || source->crop_height<4 ||
            source->x>source->width || source->crop_width>source->width-source->x ||
            source->y>source->height || source->crop_height>source->height-source->y) return -22;
    for(unsigned int c=0;c<4;c++) if(!source->wb_q12[c] || source->wb_q12[c]>16*4096 || source->black[c]>=source->white) return -22;
    f=fopen(packet,"rb");if(!f) return -errno;
    uint32_t header[16];size_t bytes=(size_t)source->stride*source->height;
    valid=fread(header,1,sizeof(header),f)==sizeof(header) && header[0]==0x3252584e &&
        header[1]==1 && header[2]==source->width && header[3]==source->height &&
        header[4]==source->stride && header[5]==12;
    for(unsigned int i=6;valid && i<16;i++) valid=!header[i];
    if(!valid) {fclose(f);return -22;}
    *raw=malloc(bytes);if(!*raw) {fclose(f);return -12;}
    valid=fread(*raw,1,bytes,f)==bytes && fgetc(f)==EOF && !ferror(f);
    if(fclose(f)) valid=0;
    if(!valid) {free(*raw);*raw=NULL;return -5;}return 0;
}
static int nx_isp_render(const unsigned char *raw,const struct nx_isp_source *source,
        const struct nx_isp_state *state,unsigned int width,unsigned int height,
        unsigned char *yy,unsigned char *cc) {
    if(state->present!=7 || state->lut_mask!=7) return -61;
    for(unsigned int i=0;i<8192;i++) if(state->gamma[i]>1023) return -95;
    for(unsigned int ch=0;ch<3;ch++) for(unsigned int i=0;i<730;i++)
        if(state->lut[ch][i]>1023 || (i==729 && state->lut[ch][i])) return -95;
    for(unsigned int i=0;i<9;i++) if(state->ccm[i]<-4096 || state->ccm[i]>4096 ||
        state->ycc[i]<-4096 || state->ycc[i]>4096) return -95;
    uint16_t *xmap=malloc(width*sizeof(*xmap));if(!xmap) return -12;
    for(unsigned int x=0;x<width;x++) xmap[x]=(uint16_t)(source->x+x*source->crop_width/width);
    for(unsigned int row=0;row<height;row++) {
        int sy=source->y+row*source->crop_height/height;
        for(unsigned int x=0;x<width;x+=2) {
            int chroma[2]={0};
            for(unsigned int p=0;p<2;p++) {
                int sx=xmap[x+p];
                unsigned int rgb[3];nx_isp_rgb(raw,source,state,sx,sy,rgb);
                for(unsigned int ch=0;ch<3;ch++) {
                    int value=0;for(unsigned int k=0;k<3;k++) value+=state->ycc[3*ch+k]*(int)rgb[k];
                    /* Full-range Q8 RGB2YCC, 10->8bit. Chroma is horizontally
                     * averaged before rounding into the progressive NV16 task. */
                    if(!ch) yy[row*width+x+p]=(unsigned char)nx_isp_clip((value+512)/1024,255);
                    else chroma[ch-1]+=value;
                }
            }
            cc[row*width+x]=(unsigned char)nx_isp_clip(128+(chroma[0]+1024)/2048,255);
            cc[row*width+x+1]=(unsigned char)nx_isp_clip(128+(chroma[1]+1024)/2048,255);
        }
    }
    free(xmap);return 0;
}
/* Private generated planes use the existing bounded encoder memory contract;
 * only encoded bytes are committed to the original live output generation. */
#ifndef NX_ISP_CORE_ONLY
static int nx_software_isp_encode(struct nx300m_jpeg_task *task,uint32_t capacity) {
    int error=-22;const char *stage="task";uint32_t committed=0,generation_before=0,revision=0;
    uint64_t output_token=0;unsigned char *raw=NULL;
    struct nx_isp_source source;struct nx_isp_state state;
    struct nx300m_jpeg_cpu_result result;memset(&result,0,sizeof(result));
    const char *packet=getenv("NX300M_PP_BAYER_REPLAY"),*config=getenv("NX300M_SOFTWARE_ISP_SOURCE");
    uint32_t (*generation)(void)=dlsym(RTLD_DEFAULT,"nx300m_pp_replay_generation");
    uint64_t (*live)(uint32_t,uint32_t,int)=dlsym(RTLD_DEFAULT,"nx300m_live_virtual_generation");
    int (*commit)(uint32_t,uint32_t,const void *,uint64_t)=dlsym(RTLD_DEFAULT,"nx300m_write_live_virtual_if_generation");
    if(!task) goto finished;
    task->size=0;stage="progressive-nv16-only";
    if(task->pack_type || task->ycc_type || task->y_odd || task->cb_odd || task->cr_even || task->cr_odd) {error=-95;goto finished;}
    struct nx300m_jpeg_layout layout;error=nx300m_jpeg_describe_task(task,capacity,&layout);
    if(error) {stage=layout.rejection;goto finished;}
    stage="recorded-bayer-transfer-generation";
    if(!packet || !config || !generation || !(generation_before=generation())) {error=-61;goto finished;}
    stage="live-output-generation";
    if(!live || !commit || !(output_token=live(task->output,capacity,1))) {error=-14;goto finished;}
    nx_isp_acquire();state=nx_isp;nx_isp_release();revision=state.revision;
    stage="recorded-bayer-and-calibration";error=nx_isp_load(packet,config,&source,&raw);if(error) goto finished;
    stage="software-same-CFA-lowpass";error=nx_isp_denoise(raw,&source);if(error) goto finished;
    stage="software-CFA-area-reduction";error=nx_isp_reduce(&raw,&source,task->width,task->height);if(error) goto finished;
    while(__sync_lock_test_and_set(&nx_diag_lock,1)) usleep(1000);
    memset(&nx_diag,0,sizeof(nx_diag));nx_diag.task=*task;nx_diag.count=3;
    uint32_t plane=task->width*task->height;
    for(unsigned int i=0;i<3;i++) {
        nx_diag.buffers[i].length=i?plane:capacity;nx_diag.buffers[i].writable=!i;
        nx_diag.buffers[i].data=malloc(nx_diag.buffers[i].length);
        if(!nx_diag.buffers[i].data) {error=-12;stage="private-planes";goto cleanup;}
    }
    nx_diag.task.output=(uint32_t)(uintptr_t)nx_diag.buffers[0].data;
    nx_diag.task.y_even=(uint32_t)(uintptr_t)nx_diag.buffers[1].data;
    nx_diag.task.cb_even=(uint32_t)(uintptr_t)nx_diag.buffers[2].data;
    nx_diag.task.stride_y=nx_diag.task.stride_c=task->width;
    stage="software-bayer-wb-ccm-gamma-3dlut-ycc";
    error=nx_isp_render(raw,&source,&state,task->width,task->height,nx_diag.buffers[1].data,nx_diag.buffers[2].data);
    if(error) goto cleanup;
    nx_diag.expected=nx_diag.task;
    struct nx300m_jpeg_memory_ops ops={nx_diag_map,nx_diag_ready};stage="stock-libjpeg-compression";
    error=nx300m_jpeg_cpu_encode(&nx_diag.task,capacity,&ops,0,&result);if(error) goto cleanup;
    stage="same-bayer-and-output-generation";
    if(generation()!=generation_before) {error=-116;goto cleanup;}
    error=commit(task->output,result.bytes,nx_diag.buffers[0].data,output_token);if(error) goto cleanup;
    task->size=result.bytes;committed=result.bytes;stage="software-isp-jpeg-committed";
cleanup:
    for(unsigned int i=0;i<nx_diag.count;i++) free(nx_diag.buffers[i].data);
    memset(&nx_diag,0,sizeof(nx_diag));__sync_lock_release(&nx_diag_lock);
finished:
    free(raw);
    fprintf(stderr,"[SOFTWARE ISP] {\"width\":%u,\"height\":%u,\"error\":%d,\"stage\":\"%s\",\"bytes_committed\":%u,\"bayer_transfer_generation\":%u,\"parameter_revision\":%u,\"source\":\"recorded-real-GRBG12\",\"wb\":\"same-RAW-camera-metadata\",\"parameters\":\"forwarded-final-stock-CCM-gamma-3DLUT-RGB2YCC\",\"model\":\"bilinear-CFA-Q7-CCM-13bit-gamma-trilinear-LUT-Q8-YCC\",\"hardware_bit_exact\":false,\"calibrated_srgb_proven\":false}\n",
        task?task->width:0,task?task->height:0,error,stage,committed,generation_before,revision);
    return error;
}
#endif
