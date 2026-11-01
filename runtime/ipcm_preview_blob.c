/* IPCM only. Originals execute synchronously; no heap, I/O, remote C call,
 * original-input write, blocking lock, IPCS hook, or LCD DMA access. */
#include "ipcm_preview_blob.h"
__attribute__((visibility("hidden"))) struct nx_ipcm_config nx_ipcm_config;
__attribute__((visibility("hidden"))) struct nx_ipcm_stats nx_ipcm_stats;
static int active(void) { return nx_ipcm_config.enabled && nx_ipcm_config.strength; }
static int rounded(int n,int d) { return n<0?-((-n+d/2)/d):(n+d/2)/d; }
static int coefficient(int value, int identity) {
    return rounded(identity*(100-(int)nx_ipcm_config.strength)+value*(int)nx_ipcm_config.strength,100);
}
static int matrix(unsigned *out, const unsigned *in, const int *profile, unsigned n) {
    for (unsigned i=0;i<n*n;i++) if ((int)in[i]<-1024 || (int)in[i]>1023) return 0;
    for (unsigned row=0;row<n;row++) for (unsigned col=0;col<n;col++) {
        int sum=0;
        for (unsigned k=0;k<n;k++)
            sum += coefficient(profile[row*n+k],row==k?4096:0)*(int)in[k*n+col];
        int result=rounded(sum,4096);
        if(result < -1024 || result > 1023) return 0;
        out[row*n+col]=(unsigned)result;
    }
    return 1;
}
static int claim(unsigned k) {
    struct nx_ipcm_stats *s=&nx_ipcm_stats;
    if (__sync_bool_compare_and_swap(&s->lock[k],0,1)) {
        __sync_lock_test_and_set(&s->dirty[k],0); return 1;
    }
    /* A concurrent raw write can race a styled write. Keep pending until a
     * later uncontended native call establishes stock forwarding. */
    __sync_lock_test_and_set(&s->dirty[k],1);
    __sync_lock_test_and_set(&s->pending[k],1);
    return 0;
}
static void finish(unsigned k, int held, int changed, int result, int valid) {
    struct nx_ipcm_stats *s=&nx_ipcm_stats;
    __sync_fetch_and_add(&s->setter_forwarded[k],1);
    if (held) {
        s->last_result[k]=result;
        /* Failure never certifies restoration, even if the input was stock. */
        s->pending[k]=changed || (s->pending[k] && (result || !valid));
        __sync_synchronize();
        if(s->dirty[k]) __sync_lock_test_and_set(&s->pending[k],1);
        __sync_lock_release(&s->lock[k]);
    }
    __sync_fetch_and_sub(&s->inflight,1);
}
__attribute__((visibility("hidden"))) int nx_ipcm_ccm(unsigned *source) {
    struct nx_ipcm_stats *s=&nx_ipcm_stats;
    __sync_fetch_and_add(&s->inflight,1); __sync_fetch_and_add(&s->setter_calls[0],1);
    int held=claim(0), changed=0; unsigned scratch[9]; unsigned *forward=source;
    if(held && source) {
        for(unsigned i=0;i<9;i++) s->stock_ccm[i]=source[i];
        s->cache_valid[0]=1;
        if(active()) {
            if(matrix(scratch,source,nx_ipcm_config.profile.ccm,3)) {forward=scratch;changed=1;}
            else ++s->fallbacks[0];
        }
    }
    int (*original)(unsigned *)=(void *)nx_ipcm_config.original[0];
    if(held) {s->last_source[0]=(unsigned)source;s->last_forwarded[0]=(unsigned)forward;}
    int result=original(forward);finish(0,held,changed,result,source!=0);return result;
}
__attribute__((visibility("hidden"))) void nx_ipcm_tone(unsigned char *source) {
    struct nx_ipcm_stats *s=&nx_ipcm_stats;
    __sync_fetch_and_add(&s->inflight,1); __sync_fetch_and_add(&s->setter_calls[1],1);
    int held=claim(1), changed=0; unsigned char scratch[2048], *forward=source;
    if(held && source) {
        for(unsigned i=0;i<2048;i++) s->stock_tone[i]=source[i];
        s->cache_valid[1]=1;
        if(active()) {
            for(unsigned i=0;i<2048;i++) {
                unsigned x=source[i];
                scratch[i]=(unsigned char)coefficient(nx_ipcm_config.profile.tone[x],(int)x);
            }
            forward=scratch;changed=1;
        }
    }
    void (*original)(unsigned char *)=(void *)nx_ipcm_config.original[1];
    if(held) {s->last_source[1]=(unsigned)source;s->last_forwarded[1]=(unsigned)forward;}
    original(forward);finish(1,held,changed,0,source!=0);
}
__attribute__((visibility("hidden"))) int nx_ipcm_chm(unsigned *source) {
    struct nx_ipcm_stats *s=&nx_ipcm_stats;
    __sync_fetch_and_add(&s->inflight,1);
    int relevant=source && (source[0]==1 || source[0]==3);
    unsigned k=source && source[0]==3?NX_IPCM_YCC:NX_IPCM_CHM;
    if(relevant) __sync_fetch_and_add(&s->setter_calls[k],1);
    /* Each selector has independent ownership. A stock sel1 write cannot
     * certify restoration of an earlier styled sel3 submission, or reverse. */
    int held=relevant?claim(k):0, changed=0;unsigned scratch[19], *forward=source;
    if(held) {
        unsigned offset=k==NX_IPCM_YCC?10:6,n=k==NX_IPCM_YCC?3:2;
        unsigned *cache=k==NX_IPCM_YCC?s->stock_ycc:s->stock_chm;
        for(unsigned i=0;i<19;i++) scratch[i]=0;
        scratch[0]=cache[0]=source[0];
        for(unsigned i=offset;i<offset+n*n;i++) scratch[i]=cache[i]=source[i];
        s->cache_valid[k]=1;
        int selected=k==NX_IPCM_YCC?nx_ipcm_config.profile.version>=2U:nx_ipcm_config.profile.version==1U;
        if(active() && selected) {
            if(matrix(scratch+offset,source+offset,nx_ipcm_config.profile.post,n)) {forward=scratch;changed=1;}
            else ++s->fallbacks[k];
        }
    }
    int (*original)(unsigned *)=(void *)nx_ipcm_config.original[2];
    if(held) {s->last_source[k]=(unsigned)source;s->last_forwarded[k]=(unsigned)forward;}
    int result=original(forward);
    if(relevant) finish(k,held,changed,result,1);
    else __sync_fetch_and_sub(&s->inflight,1);
    return result;
}
__attribute__((visibility("hidden"))) int nx_ipcm_pcse(unsigned *source) {
    struct nx_ipcm_stats *s=&nx_ipcm_stats;
    __sync_fetch_and_add(&s->inflight,1);
    int relevant=source && source[0]<2;
    unsigned selector=relevant?source[0]:0,k=NX_IPCM_PHC+selector;
    if(relevant) __sync_fetch_and_add(&s->setter_calls[k],1);
    int held=relevant?claim(k):0,changed=0;unsigned scratch[13],*forward=source;
    if(held) {
        int valid=1,different=0;
        for(unsigned i=0;i<13;i++) s->stock_pcse[selector][i]=scratch[i]=source[i];
        s->cache_valid[k]=1;
        if(active() && nx_ipcm_config.profile.version==3U) {
            for(unsigned i=0;i<12;i++) {
                int value=(int)source[i+1];
                int delta=coefficient(nx_ipcm_config.profile.pcse[selector][i],0);
                if(value < -4095 || value > 4095 || delta < -4095 || delta > 4095) {valid=0;break;}
                int result=value+delta;
                if(result < -4095 || result > 4095) {valid=0;break;}
                scratch[i+1]=(unsigned)result;different|=delta!=0;
            }
            if(!valid) ++s->fallbacks[k];
            else if(different) {forward=scratch;changed=1;}
        }
    }
    int (*original)(unsigned *)=(void *)nx_ipcm_config.original[NX_IPCM_PCSE];
    if(held) {s->last_source[k]=(unsigned)source;s->last_forwarded[k]=(unsigned)forward;}
    int result=original(forward);
    if(relevant) finish(k,held,changed,result,1);
    else __sync_fetch_and_sub(&s->inflight,1);
    return result;
}
static void update(void *self, void *parameters, unsigned k) {
    struct nx_ipcm_stats *s=&nx_ipcm_stats;
    __sync_fetch_and_add(&s->inflight,1); __sync_fetch_and_add(&s->node_updates[k],1);
    int held=__sync_bool_compare_and_swap(&s->node_lock[k],0,1);
    if(held && self && *(unsigned *)self==nx_ipcm_config.vptr[k]) {
        unsigned generation=nx_ipcm_config.generation;
        int stock_pending=k==2?s->pending[NX_IPCM_PHC]||s->pending[NX_IPCM_PSC]:
                          k==1?s->pending[1]:s->pending[0]||s->pending[2]||s->pending[3];
        int retired_pending=!k && active() && s->pending[nx_ipcm_config.profile.version==1?NX_IPCM_YCC:NX_IPCM_CHM];
        if(k==2 && active() && nx_ipcm_config.profile.version<3U && stock_pending) retired_pending=1;
        if(s->node_self[k]!=(unsigned)self || s->node_generation[k]!=generation ||
           (!active() && stock_pending) || retired_pending) {
            void (*reset)(void *)=(void *)nx_ipcm_config.reset;
            reset(self); ++s->node_resets[k];
            s->node_self[k]=(unsigned)self;s->node_generation[k]=generation;
        }
    }
    void (*original)(void *,void *)=(void *)nx_ipcm_config.update;
    original(self,parameters);
    if(held) __sync_lock_release(&s->node_lock[k]);
    __sync_fetch_and_sub(&s->inflight,1);
}
__attribute__((visibility("hidden"))) void nx_ipcm_color_update(void *self,void *p) { update(self,p,0); }
__attribute__((visibility("hidden"))) void nx_ipcm_gamma_update(void *self,void *p) { update(self,p,1); }
__attribute__((visibility("hidden"))) void nx_ipcm_pcse_update(void *self,void *p) { update(self,p,2); }
