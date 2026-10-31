/* Manual, one-shot NX300M 1.15 2D CS submission; all addresses are ARM32.
 * No configuration field is a persistent calibration value. */
#ifndef NX_MANUAL_LENS_CS_RAM_H
#define NX_MANUAL_LENS_CS_RAM_H
#include <stdint.h>
#define NX_CS_RAM_MAGIC 0x4e584353U
#define NX_CS_RAM_ABI 1U
enum nx_cs_action { NX_CS_NONE=0, NX_CS_DEFAULT=1, NX_CS_STOCK=2 };
enum nx_cs_phase {
    NX_CS_IDLE=0, NX_CS_PENDING=1, NX_CS_RUNNING=2,
    NX_CS_SUBMITTED=3, NX_CS_REFUSED=4, NX_CS_DEFERRED=5, NX_CS_FAILED=6
};
enum nx_cs_reason {
    NX_CS_OK=0, NX_CS_NO_OPERATION=1, NX_CS_ELECTRONIC_READY=2,
    NX_CS_IQ_NOT_EMPTY=3, NX_CS_STOCK_NOT_READY=4, NX_CS_WRONG_PP=5,
    NX_CS_NOT_2D=6, NX_CS_UNREVIEWED_MODE=7, NX_CS_DMA_PENDING=8,
    NX_CS_BAD_GEOMETRY=9, NX_CS_MISSING_RECEIPT=10,
    NX_CS_SUBMISSION_FAILED=11, NX_CS_STATE_CHANGED=12,
    NX_CS_UNKNOWN_LENS_STATE=13, NX_CS_REQUEST_CANCELLED=14
};
struct nx_cs_ram_header {
    uint32_t magic,abi,blob_size,code_size,data_offset,data_size;
    uint32_t update_entry,common_entry,info_entry,onoff_entry;
    uint32_t config_offset,config_size,reserved[4];
};
struct nx_cs_ram_config {
    uint32_t magic,abi,enabled,inflight,busy,request_seq,request_action,done_seq;
    uint32_t phase,reason;
    uint32_t original_update,original_init,original_common,original_info;
    uint32_t original_onoff,original_ready,iq_slot,pp_slot,operation_slot;
    /* Exact reviewed raw state written by the lens detach callback.
     * This is not a mechanical switch or proof that a detach event occurred. */
    uint32_t detached_state;
    uint32_t active_tid,active_pp,update_tid,operation_ptr,operation_state;
    uint32_t ready_result,iq_ptr,iq_ready,iq_shading;
    uint32_t flags_before,flags_after,mode,three_dimensional,width,height;
    /* info_rc/onoff_rc stay UINT32_MAX: those vendor functions return void. */
    uint32_t common_calls,common_rc,info_calls,info_rc,onoff_calls,onoff_rc;
    uint32_t onoff_arg,source_ptr,source_bytes,source_lut_kind,order;
    uint32_t ready_after,iq_ready_after,iq_shading_after,total_updates;
};
#define NX_CS_RAM_CONFIG_BYTES (50U * 4U)
#endif
