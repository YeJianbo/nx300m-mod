#ifndef NXUI_LIVE_FRAME_H
#define NXUI_LIVE_FRAME_H
#include "../../runtime/menu_live_frame_posix.h"
static struct nx_menu_frame *lut_live_frame;
static unsigned lut_live_sequence;
static unsigned lut_live_width,lut_live_height;
static unsigned lut_live_received,lut_live_paints;
static long long lut_live_max_ms;
static int lut_live_valid,lut_live_pending,lut_live_bounds_valid;
static long long lut_live_due;
static struct nk_rect lut_live_bounds;
static unsigned char lut_live_rgb[NX_MENU_FRAME_PIXELS*3];
static struct nx_menu_frame lut_live_snapshot;
static int lut_live_open(void) {
    lut_live_frame=nx_menu_frame_map();
    if(!lut_live_frame)return 0;
    fprintf(stderr,"LUT_LIVE mailbox_ready bytes=%u\n",(unsigned)sizeof *lut_live_frame);
    return 1;
}
static unsigned char lut_live_clamp(int n) {return n<0?0:n>255?255:(unsigned char)n;}
/* Returns bit 0 for a source/status change (needs normal layout), bit 1 for
 * new pixels (only the image rectangle needs painting). */
static int lut_live_poll(long long tick,int active) {
    struct nx_menu_frame *p=lut_live_frame;
    unsigned me=(unsigned)getpid(),now=(unsigned)tick;
    if(!active) {
        if(p&&__atomic_load_n(&p->owner,__ATOMIC_ACQUIRE)==me)
            __atomic_store_n(&p->owner,0,__ATOMIC_RELEASE);
        int changed=lut_live_valid;
        lut_live_valid=lut_live_pending=lut_live_bounds_valid=0;
        lut_live_sequence=0;lut_live_due=0;
        return changed;
    }
    if(tick<lut_live_due)return 0;
    lut_live_due=tick+NX_MENU_FRAME_INTERVAL;
    if(!p){if(!lut_live_open()){lut_live_due=tick+1000;return 0;}p=lut_live_frame;}
    unsigned owner=__atomic_load_n(&p->owner,__ATOMIC_ACQUIRE);
    if(owner!=me) {
        if(owner&&now-__atomic_load_n(&p->heartbeat,__ATOMIC_ACQUIRE)<=1000U)return 0;
        if(!__sync_bool_compare_and_swap(&p->owner,owner,me))return 0;
    }
    __atomic_store_n(&p->heartbeat,now,__ATOMIC_RELEASE);
    unsigned seq=__atomic_load_n(&p->sequence,__ATOMIC_ACQUIRE);
    int was_valid=lut_live_valid;
    if(!(seq&1)&&seq&&seq!=lut_live_sequence) {
        struct nx_menu_frame *f=&lut_live_snapshot;
        f->width=__atomic_load_n(&p->width,__ATOMIC_RELAXED);
        f->height=__atomic_load_n(&p->height,__ATOMIC_RELAXED);
        f->timestamp=__atomic_load_n(&p->timestamp,__ATOMIC_RELAXED);
        for(unsigned i=0;i<NX_MENU_FRAME_PIXELS;i++)f->y[i]=__atomic_load_n(&p->y[i],__ATOMIC_RELAXED);
        for(unsigned i=0;i<NX_MENU_FRAME_PIXELS/2;i++)f->uv[i]=__atomic_load_n(&p->uv[i],__ATOMIC_RELAXED);
        __atomic_thread_fence(__ATOMIC_ACQUIRE);
        if(seq==__atomic_load_n(&p->sequence,__ATOMIC_ACQUIRE)&&f->width&&f->height&&
           f->width<=NX_MENU_FRAME_W&&f->height<=NX_MENU_FRAME_H&&!((f->width|f->height)&1)&&now-f->timestamp<=1000U) {
            lut_live_width=f->width;lut_live_height=f->height;
            memset(lut_live_rgb,0,sizeof lut_live_rgb);
            unsigned ox=(NX_MENU_FRAME_W-f->width)/2,oy=(NX_MENU_FRAME_H-f->height)/2;
            /* Full-range BT.601 CbCr; the camera's precise preview/JPEG colour
             * encoding still needs calibration. This is a viewing reference. */
            for(unsigned y=0;y<f->height;y++)for(unsigned x=0;x<f->width;x++) {
                unsigned at=y*NX_MENU_FRAME_W+x,uv=(y/2)*NX_MENU_FRAME_W+(x&~1U);
                int yy=f->y[at],cb=(int)f->uv[uv]-128,cr=(int)f->uv[uv+1]-128;
                unsigned char *rgb=lut_live_rgb+((y+oy)*NX_MENU_FRAME_W+x+ox)*3;
                rgb[0]=lut_live_clamp(yy+((359*cr)>>8));
                rgb[1]=lut_live_clamp(yy-((88*cb+183*cr)>>8));
                rgb[2]=lut_live_clamp(yy+((454*cb)>>8));
            }
            lut_live_sequence=seq;lut_live_valid=lut_live_pending=1;lut_live_received++;
            return 2|(!was_valid);
        }
    }
    if(now-__atomic_load_n(&p->timestamp,__ATOMIC_ACQUIRE)>1000U){lut_live_valid=0;lut_live_due=tick+500;}
    return was_valid!=lut_live_valid;
}
static void lut_live_close(void) {
    lut_live_poll(now_ms(),0);
    if(lut_live_frame){munmap(lut_live_frame,sizeof *lut_live_frame);lut_live_frame=NULL;}
    /* Keep the file: the resident camera has its own mapping until shutdown. */
}
#endif
