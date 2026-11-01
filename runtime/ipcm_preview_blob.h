#ifndef NX_IPCM_PREVIEW_BLOB_H
#define NX_IPCM_PREVIEW_BLOB_H
#define NX_IPCM_MAGIC 0x4950584eU
#define NX_IPCM_ABI 3U
#define NX_IPCM_PROFILE_MAGIC 0x5056584eU
/* Little-endian disk v1: header32 + CCM36 + CHM16 + tone256 = 340.
 * Disk v2: header32 + CCM36 + YCC36 + tone256 = 360.
 * Loader normalises v1 to this 360-byte internal layout: post[0..3] holds
 * CHM, post[4..8] is zero, tone moves to +104. Header version/size/CRC retain
 * their original disk meanings; v2 post[0..8] holds the complete YCC matrix.
 * Disk v3 appends PHC12 and PSC12 signed native parameter deltas (96B). */
struct nx_ipcm_profile {
    unsigned magic, version, size, flags, payload_crc32, reserved[3];
    int ccm[9], post[9];
    unsigned char tone[256];
    int pcse[2][12];
};
enum { NX_IPCM_CCM, NX_IPCM_TONE, NX_IPCM_CHM, NX_IPCM_PCSE,
       NX_IPCM_COLOR_UPDATE, NX_IPCM_GAMMA_UPDATE, NX_IPCM_PCSE_UPDATE, NX_IPCM_SLOTS };
enum { NX_IPCM_YCC=3, NX_IPCM_PHC=4, NX_IPCM_PSC=5, NX_IPCM_KINDS=6, NX_IPCM_NODES=3, NX_IPCM_SETTERS=4 };
struct nx_ipcm_header {
    unsigned magic, abi, blob_size, code_size, data_offset, data_size;
    unsigned entry[NX_IPCM_SLOTS];
    unsigned config_offset, config_size, stats_offset, stats_size, build, reserved[4];
};
struct nx_ipcm_config {
    unsigned abi, size, generation, enabled, strength;
    unsigned original[NX_IPCM_SETTERS], update, reset, vptr[NX_IPCM_NODES];
    struct nx_ipcm_profile profile;
};
/* pending means a styled/uncertain software submission still needs an actual
 * stock setter call. Tone has no return code: forwarded is not hardware proof.
 * Caches are captured only from fresh native inputs, under nonblocking locks. */
struct nx_ipcm_stats {
    unsigned inflight, setter_calls[NX_IPCM_KINDS], setter_forwarded[NX_IPCM_KINDS];
    unsigned lock[NX_IPCM_KINDS], dirty[NX_IPCM_KINDS], cache_valid[NX_IPCM_KINDS], pending[NX_IPCM_KINDS], fallbacks[NX_IPCM_KINDS];
    unsigned last_source[NX_IPCM_KINDS], last_forwarded[NX_IPCM_KINDS];
    int last_result[NX_IPCM_KINDS];
    unsigned node_lock[NX_IPCM_NODES], node_self[NX_IPCM_NODES], node_generation[NX_IPCM_NODES], node_resets[NX_IPCM_NODES], node_updates[NX_IPCM_NODES];
    unsigned stock_ccm[9], stock_chm[19], stock_ycc[19];
    unsigned char stock_tone[2048];
    unsigned stock_pcse[2][13];
};
#endif
