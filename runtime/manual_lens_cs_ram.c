/* NX300M 1.15: one manually requested CS Init on the stock Update thread.
 * No event injection, ready-bit override, register write or factory-data edit.
 * Production admission remains disabled until PP serialization is established.
 * SPDX-License-Identifier: AGPL-3.0-or-later */
#include "manual_lens_cs_ram.h"

struct nx_cs_ram_config nx_cs_ram_config;
typedef void (*update_fn)(void *,unsigned);
typedef void (*init_fn)(void *);
typedef int (*common_fn)(unsigned,const void *);
typedef void (*info_fn)(const void *);
typedef void (*onoff_fn)(unsigned);
typedef unsigned (*ready_fn)(void *);

static unsigned tid(void) {
    register unsigned r0 __asm__("r0");
    register unsigned r7 __asm__("r7")=224;
    __asm__ volatile("svc 0" : "=r"(r0) : "r"(r7) : "memory");
    return r0;
}
static unsigned word(const void *p,unsigned offset) {
    return *(const volatile unsigned *)((const unsigned char *)p+offset);
}
static unsigned byte(const void *p,unsigned offset) {
    return *(const volatile unsigned char *)((const unsigned char *)p+offset);
}
static unsigned short_word(const void *p,unsigned offset) {
    return *(const volatile unsigned short *)((const unsigned char *)p+offset);
}
static volatile struct nx_cs_ram_config *cfg(void) { return &nx_cs_ram_config; }
static void enter(volatile struct nx_cs_ram_config *c) {
    __atomic_add_fetch(&c->inflight,1U,__ATOMIC_ACQUIRE);
}
static void leave(volatile struct nx_cs_ram_config *c) {
    __atomic_sub_fetch(&c->inflight,1U,__ATOMIC_RELEASE);
}
static int recording(volatile struct nx_cs_ram_config *c) {
    return c->active_tid && c->active_tid==tid() && c->active_pp && c->busy;
}

int nx_cs_ram_common(unsigned kind,const void *parameters) {
    volatile struct nx_cs_ram_config *c=cfg(); enter(c);
    int result=((common_fn)c->original_common)(kind,parameters);
    if(kind==3 && recording(c)) {
        c->common_calls++; c->common_rc=(unsigned)result;
        c->source_ptr=parameters?word(parameters,0):0;
        c->source_bytes=parameters?word(parameters,4):0;
        c->source_lut_kind=kind;
        c->order=(c->order<<2)|1U;
    }
    leave(c); return result;
}
void nx_cs_ram_info(const void *parameters) {
    volatile struct nx_cs_ram_config *c=cfg(); enter(c);
    ((info_fn)c->original_info)(parameters);
    if(recording(c)) {
        /* The vendor API is void: r0 is not a submission result. */
        c->info_calls++;
        c->order=(c->order<<2)|2U;
    }
    leave(c);
}
void nx_cs_ram_onoff(unsigned on) {
    volatile struct nx_cs_ram_config *c=cfg(); enter(c);
    ((onoff_fn)c->original_onoff)(on);
    if(recording(c)) {
        c->onoff_calls++; c->onoff_arg=on;
        c->order=(c->order<<2)|3U;
    }
    leave(c);
}
static void finish(volatile struct nx_cs_ram_config *c,unsigned seq,
                   unsigned phase,unsigned reason) {
    c->reason=reason; c->phase=phase;
    __atomic_store_n(&c->done_seq,seq,__ATOMIC_RELEASE);
    __atomic_store_n(&c->busy,0U,__ATOMIC_RELEASE);
}
static void attempt(volatile struct nx_cs_ram_config *c,void *pp,unsigned seq,unsigned action) {
    c->phase=NX_CS_RUNNING; c->reason=NX_CS_OK; c->update_tid=tid();
    c->common_calls=0; c->info_calls=0; c->onoff_calls=0; c->order=0;
    c->common_rc=0xffffffffU; c->info_rc=0xffffffffU; c->onoff_rc=0xffffffffU;
    c->source_ptr=0; c->source_bytes=0; c->source_lut_kind=0; c->onoff_arg=0;
    c->operation_ptr=word((const void *)c->operation_slot,0);
    c->iq_ptr=word((const void *)c->iq_slot,0);
    if(!pp || (unsigned)pp!=word((const void *)c->pp_slot,0)) {
        finish(c,seq,NX_CS_REFUSED,NX_CS_WRONG_PP); return;
    }
    c->mode=word(pp,0x2c); c->three_dimensional=byte(pp,0x14);
    c->flags_before=word(pp,0x18); c->flags_after=c->flags_before;
    c->width=short_word(pp,0x44); c->height=short_word(pp,0x48);
    if(c->three_dimensional) { finish(c,seq,NX_CS_REFUSED,NX_CS_NOT_2D); return; }
    if(c->mode>3) { finish(c,seq,NX_CS_REFUSED,NX_CS_UNREVIEWED_MODE); return; }
    if(c->flags_before&2U) { finish(c,seq,NX_CS_DEFERRED,NX_CS_DMA_PENDING); return; }
    unsigned columns=(c->width>>6)+1U+((c->width&63U)!=0);
    unsigned rows=((c->height>>6)+2U+((c->height&63U)!=0))&~1U;
    if(!(c->flags_before&1U) || !word(pp,0) || !c->width || !c->height ||
       columns>256 || rows>256 || columns*rows*8U>0x27c00U) {
        finish(c,seq,NX_CS_REFUSED,NX_CS_BAD_GEOMETRY); return;
    }
    if(!c->operation_ptr || !c->iq_ptr) {
        finish(c,seq,NX_CS_REFUSED,NX_CS_NO_OPERATION); return;
    }
    c->operation_state=word((const void *)c->operation_ptr,0x70);
    c->ready_result=((ready_fn)c->original_ready)((void *)c->operation_ptr);
    c->iq_ready=byte((const void *)c->iq_ptr,8);
    c->iq_shading=word((const void *)c->iq_ptr,0x20);
    if(action==NX_CS_DEFAULT) {
        if(c->ready_result==0x20000019U) {
            finish(c,seq,NX_CS_REFUSED,NX_CS_ELECTRONIC_READY); return;
        }
        if(c->operation_state!=c->detached_state) {
            finish(c,seq,NX_CS_REFUSED,NX_CS_UNKNOWN_LENS_STATE); return;
        }
        /* Both must already be empty: never rewrite stale shared lens state.
         * Other states require observation rather than a broader force gate. */
        if(c->iq_ready || c->iq_shading) {
            finish(c,seq,NX_CS_REFUSED,NX_CS_IQ_NOT_EMPTY); return;
        }
    } else if(action==NX_CS_STOCK) {
        if(c->ready_result!=0x20000019U || !c->iq_ready || !c->iq_shading) {
            finish(c,seq,NX_CS_REFUSED,NX_CS_STOCK_NOT_READY); return;
        }
    } else { finish(c,seq,NX_CS_REFUSED,NX_CS_REQUEST_CANCELLED); return; }
    c->active_pp=(unsigned)pp;
    __atomic_store_n(&c->active_tid,c->update_tid,__ATOMIC_RELEASE);
    ((init_fn)c->original_init)(pp);
    __atomic_store_n(&c->active_tid,0U,__ATOMIC_RELEASE); c->active_pp=0;
    c->flags_after=word(pp,0x18);
    if(c->operation_ptr!=word((const void *)c->operation_slot,0) ||
       c->iq_ptr!=word((const void *)c->iq_slot,0)) {
        finish(c,seq,NX_CS_FAILED,NX_CS_STATE_CHANGED); return;
    }
    c->ready_after=((ready_fn)c->original_ready)((void *)c->operation_ptr);
    c->iq_ready_after=byte((const void *)c->iq_ptr,8);
    c->iq_shading_after=word((const void *)c->iq_ptr,0x20);
    if(c->operation_state!=word((const void *)c->operation_ptr,0x70) ||
       c->ready_result!=c->ready_after || c->iq_ready!=c->iq_ready_after ||
       c->iq_shading!=c->iq_shading_after) {
        finish(c,seq,NX_CS_FAILED,NX_CS_STATE_CHANGED); return;
    }
    if(c->common_calls!=1 || c->info_calls!=1 || c->onoff_calls!=1 ||
       c->onoff_arg!=1 || c->order!=27U) {
        finish(c,seq,NX_CS_FAILED,NX_CS_MISSING_RECEIPT); return;
    }
    if(c->common_rc) {
        finish(c,seq,NX_CS_FAILED,NX_CS_SUBMISSION_FAILED); return;
    }
    /* Only common_init has a result. The two void calls are observations,
     * not acknowledgement of hardware success or DMA/color correctness. */
    finish(c,seq,NX_CS_SUBMITTED,NX_CS_OK);
}
void nx_cs_ram_update(void *pp,unsigned force) {
    volatile struct nx_cs_ram_config *c=cfg(); enter(c);
    __atomic_add_fetch(&c->total_updates,1U,__ATOMIC_RELAXED);
    unsigned seq=__atomic_load_n(&c->request_seq,__ATOMIC_ACQUIRE);
    if(c->enabled && seq && seq!=c->done_seq) {
        unsigned expected=0;
        if(__atomic_compare_exchange_n(&c->busy,&expected,1U,0,__ATOMIC_ACQUIRE,__ATOMIC_RELAXED))
            attempt(c,pp,seq,c->request_action);
    }
    ((update_fn)c->original_update)(pp,force);
    leave(c);
}
