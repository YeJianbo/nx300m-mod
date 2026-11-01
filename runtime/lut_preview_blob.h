#ifndef NX_LUT_PREVIEW_BLOB_H
#define NX_LUT_PREVIEW_BLOB_H
#define NX_PREVIEW_MAGIC 0x4e585056U
#define NX_PREVIEW_ABI 1U
struct nx_preview_header {
    unsigned magic, abi, blob_size, code_size, data_offset, data_size;
    unsigned entry, config_offset, config_size, stats_offset, stats_size, build;
    unsigned reserved[4];
};
struct nx_preview_config { unsigned abi, size, original, enabled; };
struct nx_preview_stats {
    unsigned inflight, calls, observations, sample_lock, sample_seq;
    unsigned callback, image, result, geometry[11];
};
#endif
