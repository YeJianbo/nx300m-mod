#ifndef NX300M_JPEG_CPU_ADAPTER_H
#define NX300M_JPEG_CPU_ADAPTER_H
#include <stdint.h>

/* libsif.so 1.15 sJpegOp: 16 ARM words, no host pointers. */
struct nx300m_jpeg_task {
    uint32_t width,height,y_even,y_odd,cb_even,cb_odd,cr_even,cr_odd;
    uint32_t stride_y,stride_c,output,ycc_type,size,pack_type,quality,timeout;
};
enum { NX_JPEG_INPUT_UNKNOWN=0, NX_JPEG_INPUT_CBCR=1, NX_JPEG_INPUT_CRCB=2 };
enum nx300m_jpeg_range_role {
    NX_JPEG_OUTPUT, NX_JPEG_Y_EVEN, NX_JPEG_Y_ODD,
    NX_JPEG_CB_EVEN, NX_JPEG_CB_ODD, NX_JPEG_CR_EVEN, NX_JPEG_CR_ODD
};
struct nx300m_jpeg_range {
    uint32_t address,length,role,writable;
};
struct nx300m_jpeg_layout {
    struct nx300m_jpeg_range ranges[7];
    uint32_t count,sampling;
    const char *rejection;
};
/* Describes requested memory only. A valid layout is not producer permission,
 * a live allocation, or evidence that any pixel was written. Never mutates task. */
int nx300m_jpeg_describe_task(const struct nx300m_jpeg_task *task,uint32_t capacity,
    struct nx300m_jpeg_layout *layout);
struct nx300m_jpeg_memory_ops {
    /* Must validate the complete range against live SMA allocations/mappings,
     * accepting an actual camera virtual address or a physical SMA address. */
    void *(*map_address)(uint32_t address,uint32_t length,int writable);
    /* Consume producer authorization for this exact task; 0 rejects unknown
     * pixels. Return 1/2 records the actual interleaved chroma byte order. */
    int (*input_ready)(const struct nx300m_jpeg_task *task);
};
struct nx300m_jpeg_cpu_result {
    int error;
    uint32_t bytes,capacity,sampling,quant_table_index;
    const char *quality_policy;
    const char *failure_stage;
};

/* Actual YCC task execution. software_quality=0 uses extracted stock tables;
 * 1..100 explicitly selects IJG software quality. Only success updates size;
 * failures clear size and leave the entire output allocation unchanged. */
int nx300m_jpeg_cpu_encode(struct nx300m_jpeg_task *task,uint32_t capacity,
    const struct nx300m_jpeg_memory_ops *ops,unsigned int software_quality,
    struct nx300m_jpeg_cpu_result *result);

/* Integration exports resolved by the optional JpegEncode interposer.
 * The device/producer owns allocation and one-shot readiness accounting. */
void *nx300m_jpeg_map_address(uint32_t address,uint32_t length,int writable);
int nx300m_jpeg_input_ready(const struct nx300m_jpeg_task *task);
#endif
