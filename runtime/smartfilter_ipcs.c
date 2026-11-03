/* Native API adapter. The VM replaces the UDD device boundary, not this
 * transaction, cache handoff, LUT upload or restoration implementation. */
#include "smartfilter_ipcs.h"

static void clear(void *p, unsigned n) { unsigned char *v=p; while(n--) *v++=0; }
static void copy(void *to, const void *from, unsigned n) {
    unsigned char *d=to; const unsigned char *s=from;
    if (d==s) return;
    while(n--) *d++=*s++;
}
static struct nx_sft_record *trace(struct nx_sf_ipcs *s) {
    return s->trace_current?s->trace_current():(void *)0;
}
static int failure(struct nx_sf_ipcs *s, int error) {
    ++s->failures; s->last_error=error;
    return error;
}
static void quarantine(struct nx_sf_ipcs *s, int error) {
    failure(s,error);
    __atomic_store_n(&s->bridge->fault,1,__ATOMIC_RELEASE);
    __atomic_store_n(&s->bridge->paused,1,__ATOMIC_RELEASE);
    __atomic_store_n(&s->bridge->stats.last_error,error,__ATOMIC_RELEASE);
}
static void release_locks(struct nx_sf_ipcs *s,struct nx_sft_record *r) {
    while(s->held) {
        unsigned i=--s->held;
        struct nx_sft_mark m=nx_sft_enter_stage(r,NX_SFT_UNLOCK);
        int rc=s->api.post(s->api.semaphore[i]);
        nx_sft_leave_stage(r,m,rc);
        if(rc) {
            if(r) { r->flags|=NX_SFT_F_UNLOCK; r->fault=1; }
            quarantine(s,-44);
        }
    }
}
int nx_sf_ipcs_init(struct nx_sf_ipcs *s, const struct nx_sf_ipcs_api *a, struct nx_sf_bridge *b) {
    if(!s||!a||!b||!a->trywait||!a->post||!a->open||!a->close||!a->reset||!a->ade_init||
       !a->write_lut||!a->ycc||!a->iq_initialize||!a->iq_process||!a->width||!a->height||
       !a->stride||!a->sampling||!a->address||!a->flush||a->rgb3d_index>=NX_SF_ADE_SWITCHES)
        return NX_SF_INVALID;
    for(unsigned i=0;i<NX_SF_IPCS_SEMAPHORES;++i) if(!a->semaphore[i]) return NX_SF_INVALID;
    for(unsigned i=0;i<NX_SF_ADE_SWITCHES;++i) if(!a->ade_switch[i]) return NX_SF_INVALID;
    /* trace_current deliberately stays NULL until the runtime's ensure sets it. */
    clear(s,sizeof *s); copy(&s->api,a,sizeof *a); s->bridge=b;
    return NX_SF_OK;
}
int nx_sf_ipcs_begin(void *opaque) {
    struct nx_sf_ipcs *s=opaque;
    struct nx_sft_record *r=trace(s);
    if(s->held||s->opened||s->touched) {
        if(r) r->path=NX_SFT_PATH_BUSY;
        return NX_SF_BUSY;
    }
    /* Same ascending order as the native wrappers; never wait in preview. */
    for(unsigned i=0;i<NX_SF_IPCS_SEMAPHORES;++i) {
        struct nx_sft_mark m=nx_sft_enter_stage(r,NX_SFT_TRY0+i);
        int rc=s->api.trywait(s->api.semaphore[i]);
        nx_sft_leave_stage(r,m,rc);
        if(rc) {
            if(r) r->path=NX_SFT_PATH_BUSY;
            ++s->busy; release_locks(s,r); return NX_SF_BUSY;
        }
        ++s->held;
    }
    for(unsigned i=0;i<NX_SF_ADE_SWITCHES;++i) s->saved_switch[i]=*s->api.ade_switch[i];
    ++s->transactions;
    return NX_SF_OK;
}

struct frame { unsigned y,uv,oy,ouv,bytes,chroma; };
static int geometry(struct nx_sf_ipcs *s,void *image,struct frame *f,struct nx_sft_record *r) {
    const struct nx_sf_ipcs_api *a=&s->api;
    unsigned w=a->width(image),h=a->height(image),stride=a->stride(image);
    if(r) { r->width=w; r->height=h; r->stride=stride; r->sampling=~0U; }
    /* Preserve the original short circuit: do not call sampling on invalid
     * dimensions, or add any diagnostic geometry calls on another path. */
    if(!w||!h||(w&1)||(h&1)||stride<w||stride>8192||h>8192) return -41;
    unsigned sampling=a->sampling(image);
    if(r) r->sampling=sampling;
    /* SmartFilter constructs sADEPixelType {0,1,0}; GetSamType reads the
     * first word. ADE maps zero to IPCS_YCC420 (also zero), not to the
     * adjacent plane-format value 1. */
    if(sampling!=0) return -41;
    f->bytes=stride*h; f->chroma=f->bytes/2;
    f->y=a->address(image,1); f->uv=a->address(image,3);
    f->oy=a->address(image,6); f->ouv=a->address(image,8);
    if(!f->y||!f->uv||!f->oy||!f->ouv||
       f->y>~0U-f->bytes||f->oy>~0U-f->bytes||f->uv>~0U-f->chroma||f->ouv>~0U-f->chroma)
        return -41;
    return 0;
}
static int timed_geometry(struct nx_sf_ipcs *s,void *image,struct frame *f,struct nx_sft_record *r) {
    struct nx_sft_mark m=nx_sft_enter_stage(r,NX_SFT_GEOMETRY);
    int rc=geometry(s,image,f,r);
    nx_sft_leave_stage(r,m,rc); return rc;
}
static int cache(struct nx_sf_ipcs *s,void *image,unsigned y,unsigned uv,const struct frame *f) {
    /* Merge adjacent planes, otherwise flush each explicit plane. */
    if(uv==y+f->bytes) return s->api.flush(image,y,f->bytes+f->chroma);
    int rc=s->api.flush(image,y,f->bytes);
    return rc?rc:s->api.flush(image,uv,f->chroma);
}
static int timed_cache(struct nx_sf_ipcs *s,void *image,unsigned y,unsigned uv,
                       const struct frame *f,struct nx_sft_record *r,unsigned stage) {
    struct nx_sft_mark m=nx_sft_enter_stage(r,stage);
    int rc=cache(s,image,y,uv,f);
    nx_sft_leave_stage(r,m,rc); return rc;
}
int nx_sf_ipcs_render(void *opaque,void *image,const unsigned short *lut) {
    struct nx_sf_ipcs *s=opaque;
    struct nx_sft_record *r=trace(s);
    struct frame f;
    if(r) r->path=NX_SFT_PATH_RENDER;
    if(s->held!=NX_SF_IPCS_SEMAPHORES||s->opened||s->touched||timed_geometry(s,image,&f,r)) return failure(s,-41);
    struct nx_sft_mark m=nx_sft_enter_stage(r,NX_SFT_OPEN);
    int rc=s->api.open();
    nx_sft_leave_stage(r,m,rc);
    if(rc<0) return failure(s,rc);
    s->opened=1; s->touched=1;
    m=nx_sft_enter_stage(r,NX_SFT_ADE_INIT);
    s->api.ade_init();
    nx_sft_leave_stage(r,m,0);
    for(unsigned i=0;i<NX_SF_ADE_SWITCHES;++i) *s->api.ade_switch[i]=(i==s->api.rgb3d_index);
    for(unsigned c=0;c<3;++c) {
        m=nx_sft_enter_stage(r,NX_SFT_LUT0+c);
        s->api.write_lut(c,lut+c*NX_SF_CHANNEL_WORDS);
        nx_sft_leave_stage(r,m,0); ++s->uploads;
    }
    rc=timed_cache(s,image,f.y,f.uv,&f,r,NX_SFT_FLUSH_IN);
    if(rc) return failure(s,rc);
    ++s->operations;
    m=nx_sft_enter_stage(r,NX_SFT_YCC);
    rc=s->api.ycc(image);
    nx_sft_leave_stage(r,m,rc);
    if(rc) return failure(s,rc);
    rc=timed_cache(s,image,f.oy,f.ouv,&f,r,NX_SFT_FLUSH_OUT);
    return rc?failure(s,rc):0;
}
int nx_sf_ipcs_restore(void *opaque) {
    struct nx_sf_ipcs *s=opaque;
    struct nx_sft_record *r=trace(s);
    int result=0;
    if(s->touched) {
        /* Native reset also runs on an operation error before any CPU fallback.
         * Initialize(0) invalidates Still parameter-node indices; Process(0)
         * forwards fresh native colour settings through the existing JPEG hook. */
        struct nx_sft_mark m=nx_sft_enter_stage(r,NX_SFT_RESET);
        result=s->api.reset();
        nx_sft_leave_stage(r,m,result);
        if(!result) {
            m=nx_sft_enter_stage(r,NX_SFT_IQ_INIT);
            s->api.iq_initialize(0);
            nx_sft_leave_stage(r,m,0);
            m=nx_sft_enter_stage(r,NX_SFT_IQ_PROCESS);
            s->api.iq_process(0);
            nx_sft_leave_stage(r,m,0);
            ++s->reloads;
        }
    }
    for(unsigned i=0;i<NX_SF_ADE_SWITCHES;++i) *s->api.ade_switch[i]=s->saved_switch[i];
    if(s->opened) {
        struct nx_sft_mark m=nx_sft_enter_stage(r,NX_SFT_CLOSE);
        int rc=s->api.close();
        nx_sft_leave_stage(r,m,rc);
        if(!result) result=rc;
        s->opened=0;
    }
    s->touched=0;
    if(result&&r) { r->flags|=NX_SFT_F_RESTORE; r->fault=1; }
    return result?failure(s,result):0;
}
void nx_sf_ipcs_end(void *opaque) {
    struct nx_sf_ipcs *s=opaque;
    struct nx_sft_record *r=trace(s);
    release_locks(s,r);
}
int nx_sf_ipcs_passthrough(void *opaque,void *image) {
    struct nx_sf_ipcs *s=opaque; struct frame f;
    struct nx_sft_record *r=trace(s);
    int rc=timed_geometry(s,image,&f,r);
    if(rc) return rc;
    struct nx_sft_mark m=nx_sft_enter_stage(r,NX_SFT_COPY);
    copy((void *)f.oy,(const void *)f.y,f.bytes);
    copy((void *)f.ouv,(const void *)f.uv,f.chroma);
    nx_sft_leave_stage(r,m,0);
    return timed_cache(s,image,f.oy,f.ouv,&f,r,NX_SFT_FLUSH_OUT);
}
