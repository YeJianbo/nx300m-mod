/* Native API adapter. The VM replaces the UDD device boundary, not this
 * transaction, cache handoff, LUT upload or restoration implementation. */
#include "smartfilter_ipcs.h"

static void clear(void *p, unsigned n) { unsigned char *v=p; while(n--) *v++=0; }
static void copy(void *to, const void *from, unsigned n) {
    unsigned char *d=to; const unsigned char *s=from;
    if (d==s) return;
    while(n--) *d++=*s++;
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
static void release_locks(struct nx_sf_ipcs *s) {
    while(s->held) {
        unsigned i=--s->held;
        if(s->api.post(s->api.semaphore[i])) quarantine(s,-44);
    }
}
int nx_sf_ipcs_init(struct nx_sf_ipcs *s, const struct nx_sf_ipcs_api *a, struct nx_sf_bridge *b) {
    if(!s||!a||!b||!a->trywait||!a->post||!a->open||!a->close||!a->reset||!a->ade_init||
       !a->write_lut||!a->ycc||!a->iq_initialize||!a->iq_process||!a->width||!a->height||
       !a->stride||!a->sampling||!a->address||!a->flush||a->rgb3d_index>=NX_SF_ADE_SWITCHES)
        return NX_SF_INVALID;
    for(unsigned i=0;i<NX_SF_IPCS_SEMAPHORES;++i) if(!a->semaphore[i]) return NX_SF_INVALID;
    for(unsigned i=0;i<NX_SF_ADE_SWITCHES;++i) if(!a->ade_switch[i]) return NX_SF_INVALID;
    clear(s,sizeof *s); copy(&s->api,a,sizeof *a); s->bridge=b;
    return NX_SF_OK;
}
int nx_sf_ipcs_begin(void *opaque) {
    struct nx_sf_ipcs *s=opaque;
    if(s->held||s->opened||s->touched) return NX_SF_BUSY;
    /* Same ascending order as the native wrappers; never wait in preview. */
    for(unsigned i=0;i<NX_SF_IPCS_SEMAPHORES;++i) {
        if(s->api.trywait(s->api.semaphore[i])) {
            ++s->busy; release_locks(s); return NX_SF_BUSY;
        }
        ++s->held;
    }
    for(unsigned i=0;i<NX_SF_ADE_SWITCHES;++i) s->saved_switch[i]=*s->api.ade_switch[i];
    ++s->transactions;
    return NX_SF_OK;
}

struct frame { unsigned y,uv,oy,ouv,bytes,chroma; };
static int geometry(struct nx_sf_ipcs *s,void *image,struct frame *f) {
    const struct nx_sf_ipcs_api *a=&s->api;
    unsigned w=a->width(image),h=a->height(image),stride=a->stride(image);
    /* SmartFilter constructs sADEPixelType {0,1,0}; GetSamType reads the
     * first word. ADE maps zero to IPCS_YCC420 (also zero), not to the
     * adjacent plane-format value 1. */
    if(!w||!h||(w&1)||(h&1)||stride<w||stride>8192||h>8192||a->sampling(image)!=0) return -41;
    f->bytes=stride*h; f->chroma=f->bytes/2;
    f->y=a->address(image,1); f->uv=a->address(image,3);
    f->oy=a->address(image,6); f->ouv=a->address(image,8);
    if(!f->y||!f->uv||!f->oy||!f->ouv||
       f->y>~0U-f->bytes||f->oy>~0U-f->bytes||f->uv>~0U-f->chroma||f->ouv>~0U-f->chroma)
        return -41;
    return 0;
}
static int cache(struct nx_sf_ipcs *s,void *image,unsigned y,unsigned uv,const struct frame *f) {
    /* Merge adjacent planes, otherwise flush each explicit plane. */
    if(uv==y+f->bytes) return s->api.flush(image,y,f->bytes+f->chroma);
    int rc=s->api.flush(image,y,f->bytes);
    return rc?rc:s->api.flush(image,uv,f->chroma);
}
int nx_sf_ipcs_render(void *opaque,void *image,const unsigned short *lut) {
    struct nx_sf_ipcs *s=opaque;
    struct frame f;
    if(s->held!=NX_SF_IPCS_SEMAPHORES||s->opened||s->touched||geometry(s,image,&f)) return failure(s,-41);
    int rc=s->api.open();
    if(rc<0) return failure(s,rc);
    s->opened=1; s->touched=1;
    s->api.ade_init();
    for(unsigned i=0;i<NX_SF_ADE_SWITCHES;++i) *s->api.ade_switch[i]=(i==s->api.rgb3d_index);
    for(unsigned c=0;c<3;++c) { s->api.write_lut(c,lut+c*NX_SF_CHANNEL_WORDS); ++s->uploads; }
    rc=cache(s,image,f.y,f.uv,&f);
    if(rc) return failure(s,rc);
    ++s->operations;
    rc=s->api.ycc(image);
    if(rc) return failure(s,rc);
    rc=cache(s,image,f.oy,f.ouv,&f);
    return rc?failure(s,rc):0;
}
int nx_sf_ipcs_restore(void *opaque) {
    struct nx_sf_ipcs *s=opaque;
    int result=0;
    if(s->touched) {
        /* Native reset also runs on an operation error before any CPU fallback.
         * Initialize(0) invalidates Still parameter-node indices; Process(0)
         * forwards fresh native colour settings through the existing JPEG hook. */
        result=s->api.reset();
        if(!result) {
            s->api.iq_initialize(0);
            s->api.iq_process(0);
            ++s->reloads;
        }
    }
    for(unsigned i=0;i<NX_SF_ADE_SWITCHES;++i) *s->api.ade_switch[i]=s->saved_switch[i];
    if(s->opened) {
        int rc=s->api.close();
        if(!result) result=rc;
        s->opened=0;
    }
    s->touched=0;
    return result?failure(s,result):0;
}
void nx_sf_ipcs_end(void *opaque) { release_locks(opaque); }
int nx_sf_ipcs_passthrough(void *opaque,void *image) {
    struct nx_sf_ipcs *s=opaque; struct frame f;
    int rc=geometry(s,image,&f);
    if(rc) return rc;
    copy((void *)f.oy,(const void *)f.y,f.bytes);
    copy((void *)f.ouv,(const void *)f.uv,f.chroma);
    return cache(s,image,f.oy,f.ouv,&f);
}
