/* Optional thumbnail tap. Only the existing SmartFilter input is read: no
 * additional IPCS work, DMA ownership change, frame queue or SD write. */
#include "menu_live_frame.h"
static struct nx_menu_frame *menu_frame;
static unsigned menu_frame_retry, menu_frame_last;
static unsigned menu_div(unsigned n,unsigned d) {
    unsigned q=0,r=0;
    for(unsigned i=32;i;--i) {r=(r<<1)|((n>>(i-1))&1);if(r>=d){r-=d;q|=1U<<(i-1);}}
    return q;
}
static void menu_frame_publish(void *image) {
    struct nx_sfr_runtime *s=&nx_sfr_runtime;
    const struct nx_sf_ipcs_api *a=&nx_sfr_config.api;
    if(get(&s->captures)||get(&s->control_pause))return;
    if(!menu_frame) {
        if(menu_frame_retry){--menu_frame_retry;return;}
        menu_frame_retry=15;
        /* ARM EABI open/lseek/mmap2. NONBLOCK also rejects accidental FIFOs
         * without blocking the camera; NOFOLLOW preserves the RAM boundary. */
        int fd=syscall6(5,(unsigned)NX_MENU_FRAME_PATH,2U|04000U|0400000U,0,0,0,0);
        if(fd<0)return;
        int size=syscall6(19,(unsigned)fd,0,2,0,0,0);
        int mapping=-1;
        if(size==(int)sizeof(struct nx_menu_frame))
            mapping=syscall6(192,0,sizeof(struct nx_menu_frame),3,1,(unsigned)fd,0);
        syscall6(6,(unsigned)fd,0,0,0,0,0);
        if((unsigned)mapping>=0xfffff001U)return;
        struct nx_menu_frame *p=(void *)mapping;
        if(get(&p->magic)!=NX_MENU_FRAME_MAGIC||p->size!=sizeof *p) {
            syscall6(91,(unsigned)p,sizeof *p,0,0,0,0);return;
        }
        menu_frame=p;
    }
    struct nx_menu_frame *p=menu_frame;
    unsigned owner=get(&p->owner);
    if(!owner)return;
    struct {unsigned sec,nsec;} ts;
    if(syscall6(263,1,(unsigned)&ts,0,0,0,0))return;
    unsigned now=ts.sec*1000U+menu_div(ts.nsec,1000000U);
    if(now-get(&p->heartbeat)>1000U||now-menu_frame_last<NX_MENU_FRAME_INTERVAL)return;
    unsigned w=a->width(image),h=a->height(image),stride=a->stride(image);
    if(!w||!h||((w|h)&1)||stride<w||stride>8192||h>8192||a->sampling(image)!=0)return;
    unsigned y=a->address(image,1),uv=a->address(image,3);
    if(!y||!uv||y==a->address(image,6)||uv==a->address(image,8)||
       y>~0U-stride*h||uv>~0U-stride*(h/2))return;
    unsigned ow=NX_MENU_FRAME_W,oh=NX_MENU_FRAME_H;
    if(w*oh>h*ow)oh=menu_div(h*ow,w)&~1U;
    else ow=menu_div(w*oh,h)&~1U;
    if(!ow||!oh)return;
    unsigned sequence=get(&p->sequence);
    if((sequence&1)||!__sync_bool_compare_and_swap(&p->sequence,sequence,sequence+1))return;
    unsigned dx=menu_div(w<<16,ow),dy=menu_div(h<<16,oh);
    unsigned source_x[NX_MENU_FRAME_W];
    for(unsigned x=0;x<ow;x++)source_x[x]=(x*dx)>>16;
    for(unsigned row=0;row<oh;row++) {
        const unsigned char *src=(void *)(y+((row*dy)>>16)*stride);
        for(unsigned x=0;x<ow;x++)
            __atomic_store_n(&p->y[row*NX_MENU_FRAME_W+x],src[source_x[x]],__ATOMIC_RELAXED);
    }
    for(unsigned row=0;row<oh/2;row++) {
        const unsigned char *src=(void *)(uv+(((row*2*dy)>>16)/2)*stride);
        for(unsigned x=0;x<ow;x+=2) {
            unsigned at=source_x[x]&~1U;
            __atomic_store_n(&p->uv[row*NX_MENU_FRAME_W+x],src[at],__ATOMIC_RELAXED);
            __atomic_store_n(&p->uv[row*NX_MENU_FRAME_W+x+1],src[at+1],__ATOMIC_RELAXED);
        }
    }
    __atomic_store_n(&p->width,ow,__ATOMIC_RELAXED);
    __atomic_store_n(&p->height,oh,__ATOMIC_RELAXED);
    __atomic_store_n(&p->timestamp,now,__ATOMIC_RELAXED);
    /* Keep the 40 ms cadence on a stock ~30 fps source: anchoring every
     * deadline to this frame would quantize the output down to ~15 fps. */
    menu_frame_last=now-menu_frame_last<2*NX_MENU_FRAME_INTERVAL?
        menu_frame_last+NX_MENU_FRAME_INTERVAL:now;
    put(&p->sequence,sequence+2);
}
