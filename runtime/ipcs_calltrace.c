#include "ipcs_calltrace.h"
static struct nx_ipct_clock clock(const struct nx_ipct_api *a) {
    struct nx_ipct_clock c={{0,0},{0,0},0};
    /* Both clocks belong to the calling thread; procfs tick accounting is not
     * used. bit 0=wall, bit 1=thread CPU. A failed clock never becomes zero cost. */
    if(!a->clock(1,&c.wall)) c.valid|=1;
    if(!a->clock(3,&c.cpu)) c.valid|=2;
    return c;
}
static unsigned us(struct nx_ipct_time begin,struct nx_ipct_time end,unsigned *valid) {
    if(end.sec<begin.sec||(end.sec==begin.sec&&end.nsec<begin.nsec)) { *valid=0; return 0; }
    unsigned sec=end.sec-begin.sec,nsec;
    if(end.nsec<begin.nsec) { --sec; nsec=1000000000U+end.nsec-begin.nsec; }
    else nsec=end.nsec-begin.nsec;
    if(sec>4000) { *valid=0; return 0; }
    return sec*1000000U+nsec/1000U;
}
static struct nx_ipct_scope *scope(struct nx_ipct *t,unsigned tid,int create) {
    for(unsigned i=0;i<NX_IPCT_SCOPES;++i)
        if(__atomic_load_n(&t->scopes[i].tid,__ATOMIC_ACQUIRE)==tid) return &t->scopes[i];
    if(create) for(unsigned i=0;i<NX_IPCT_SCOPES;++i)
        if(__sync_bool_compare_and_swap(&t->scopes[i].tid,0,tid)) return &t->scopes[i];
    return (void *)0;
}
void nx_ipct_start(struct nx_ipct *t,const struct nx_ipct_api *a,
                   unsigned role,unsigned frame,unsigned before) {
    if(!__atomic_load_n(&t->enabled,__ATOMIC_ACQUIRE)) return;
    struct nx_ipct_scope *s=scope(t,a->tid(),1);
    if(!s||s->record) { __sync_fetch_and_add(&t->dropped,1); return; }
    unsigned n=__sync_fetch_and_add(&t->next,1);
    if(n>=NX_IPCT_RECORDS) { __sync_fetch_and_add(&t->dropped,1); __atomic_store_n(&s->tid,0,__ATOMIC_RELEASE); return; }
    struct nx_ipct_record *r=&t->records[n];
    r->sequence=n; r->tid=a->tid(); r->role=role; r->frame=frame; r->udd_count_before=before;
    r->start=clock(a); s->record=r;
}
struct nx_ipct_mark nx_ipct_enter(struct nx_ipct *t,const struct nx_ipct_api *a,unsigned p,unsigned arg) {
    struct nx_ipct_mark m={{{0,0},{0,0},0},(void *)0,p};
    if(!__atomic_load_n(&t->enabled,__ATOMIC_ACQUIRE)||p>=NX_IPCT_PHASES) return m;
    struct nx_ipct_scope *s=scope(t,a->tid(),0);
    if(!s||!s->record) return m;
    m.record=s->record; m.clock=clock(a);
    m.record->phase[p].argument=arg; return m;
}
void nx_ipct_leave(const struct nx_ipct_api *a,struct nx_ipct_mark m,int rc) {
    if(!m.record) return;
    struct nx_ipct_clock end=clock(a);
    struct nx_ipct_phase_data *p=&m.record->phase[m.phase];
    unsigned valid=m.clock.valid&end.valid;
    unsigned w=us(m.clock.wall,end.wall,&valid),c=us(m.clock.cpu,end.cpu,&valid);
    p->valid=p->count?(p->valid&valid):valid;
    p->wall_us+=w; p->cpu_us+=c; ++p->count; p->rc=rc;
}
void nx_ipct_finish(struct nx_ipct *t,const struct nx_ipct_api *a,int rc,unsigned after) {
    struct nx_ipct_scope *s=scope(t,a->tid(),0);
    if(!s||!s->record) return;
    struct nx_ipct_record *r=s->record;
    struct nx_ipct_mark m={r->start,r,NX_IPCT_TOTAL};
    nx_ipct_leave(a,m,rc); r->udd_count_after=after;
    struct nx_ipct_clock end=clock(a); r->end=end.wall;
    __atomic_store_n(&r->committed,1,__ATOMIC_RELEASE); s->record=(void *)0;
    __atomic_store_n(&s->tid,0,__ATOMIC_RELEASE);
}
