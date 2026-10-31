/* Software YCC combi-resize execution behind the original libudd setup/start.
 * Bilinear sampling is a development model, not the DRIMeIV FIR's bit-exact
 * implementation. Unsupported color/codec operations retain the native wait
 * timeout instead of reporting an image operation that did not execute.
 */
#include "../work/vendor-source/TIZEN/project/NX300M/imagedev/usr/include/drime4/udd/ipcm_type.h"
typedef unsigned int u32;
extern void *dlsym(void *, const char *), *malloc(u32);
extern void free(void *);
extern int fprintf(void *, const char *, ...), snprintf(char *, u32, const char *, ...);
extern void *stderr;
extern int open(const char *, int, ...), write(int, const void *, u32), close(int), rename(const char *, const char *);
static __thread struct ipcm_ycc_combi_op_info pending;
static __thread int pending_ready;
static u32 jobs, failures;
static u32 coordinate(u32 position, u32 source, u32 target) {
    if (target <= 1) return 0;
    u32 product = position*(source-1), divisor = target-1;
    return (product/divisor)*256+(product%divisor)*256/divisor;
}
static int valid(const struct ipcm_ycc_info *info) {
    return info->width && info->height && info->width <= 8192 && info->height <= 8192 &&
        !((info->width|info->height)&1) && info->stride_y >= info->width && info->stride_y <= 65535 &&
        info->stride_c >= info->width && info->stride_c <= 65535 && info->scan_type == IPCM_PROGRESSIVE &&
        (info->ycc_type == IPCM_YCC420 || info->ycc_type == IPCM_YCC422) &&
        !info->NLC_sw && info->pix_offset_x <= info->stride_y-info->width &&
        info->pix_offset_x <= info->stride_c-info->width && info->pix_offset_y <= 8192 &&
        !((info->pix_offset_x|info->pix_offset_y)&1);
}
static unsigned char *plane(const struct ipcm_ycc_info *info, int chroma, int access) {
    void *(*map)(u32, u32, int) = dlsym((void *)0, "nx300m_map_physical_access");
    if (!map) return (void *)0;
    u32 rows = info->height/(chroma && info->ycc_type == IPCM_YCC420 ? 2 : 1);
    u32 stride = chroma ? info->stride_c : info->stride_y;
    u32 y = info->pix_offset_y/(chroma && info->ycc_type == IPCM_YCC420 ? 2 : 1);
    unsigned long long address = (unsigned long long)(chroma ? info->addr_c0 : info->addr_y0)+y*stride+info->pix_offset_x;
    if (address > 0xffffffffU) return (void *)0;
    return map(address, (rows-1)*stride+info->width, access);
}
static void sample(const unsigned char *source, u32 sw, u32 sh, u32 ss,
                   unsigned char *target, u32 tw, u32 th, u32 ts, u32 components, int reverse) {
    for (u32 y = 0; y < th; y++) {
        u32 fy = coordinate(y, sh, th), iy = fy/256, ny = iy+1 < sh ? iy+1 : iy;
        fy %= 256;
        for (u32 x = 0; x < tw; x++) {
            u32 fx = coordinate(x, sw, tw), ix = fx/256, nx = ix+1 < sw ? ix+1 : ix;
            fx %= 256;
            for (u32 c = 0; c < components; c++) {
                u32 a = source[iy*ss+ix*components+c], b = source[iy*ss+nx*components+c];
                u32 d = source[ny*ss+ix*components+c], e = source[ny*ss+nx*components+c];
                u32 value = ((a*(256-fx)+b*fx)*(256-fy)+(d*(256-fx)+e*fx)*fy+32768)/65536;
                target[y*ts+x*components+(reverse ? 1-c : c)] = value;
            }
        }
    }
}
static int resize(const struct ipcm_ycc_info *source, const struct ipcm_ycc_info *target) {
    if (!valid(source) || !valid(target)) return -1;
    unsigned char *sy = plane(source, 0, 1), *sc = plane(source, 1, 1), *dy = plane(target, 0, 2), *dc = plane(target, 1, 2);
    if (!sy || !sc || !dy || !dc) return -2;
    u32 sch = source->height/(source->ycc_type == IPCM_YCC420 ? 2 : 1);
    u32 dch = target->height/(target->ycc_type == IPCM_YCC420 ? 2 : 1);
    /* Snapshot input planes so an overlapping destination remains correct. */
    unsigned char *copy = malloc(source->width*(source->height+sch));
    if (!copy) return -3;
    for (u32 y = 0; y < source->height; y++) for (u32 x = 0; x < source->width; x++) copy[y*source->width+x] = sy[y*source->stride_y+x];
    unsigned char *chroma = copy+source->width*source->height;
    for (u32 y = 0; y < sch; y++) for (u32 x = 0; x < source->width; x++) chroma[y*source->width+x] = sc[y*source->stride_c+x];
    sample(copy, source->width, source->height, source->width, dy, target->width, target->height, target->stride_y, 1, 0);
    sample(chroma, source->width/2, sch, source->width, dc, target->width/2, dch, target->stride_c, 2, !!source->reverse_CbCr != !!target->reverse_CbCr);
    free(copy);
    return 0;
}
int ipcm_ycc_combi_set(struct ipcm_ycc_combi_op_info *info) {
    int (*original)(struct ipcm_ycc_combi_op_info *) = dlsym((void *)-1, "ipcm_ycc_combi_set");
    if (!original) return -1;
    int result = original(info);
    pending_ready = !result && info;
    if (pending_ready) pending = *info;
    return result;
}
void ipcm_ycc_combi_do_1frm(void) {
    void (*original)(void) = dlsym((void *)-1, "ipcm_ycc_combi_do_1frm");
    void (*complete)(u32) = dlsym((void *)0, "nx300m_ipcm_complete");
    if (!original) return;
    original();
    if (!pending_ready) return;
    pending_ready = 0;
    int failure = !complete || pending.Saturation_Correction_SW || pending.RSZ_MODE != IPCM_RSZ_NORMAL;
    u32 mask = 0;
    if (!failure && pending.WDMA_RSZ_SW == IPCM_SW_ON) {
        failure = resize(&pending.Src_Img_Ycc, &pending.RSZOut_Img);
        if (!failure) { complete(1); mask |= 2; }
    }
    if (!failure && pending.WDMA_SRSZ_SW == IPCM_SW_ON) {
        if (pending.Srsz_In_Sel == IPCM_SRSZ_INSEL_MAINOUT) failure = 1;
        else failure = resize(pending.Srsz_In_Sel == IPCM_SRSZ_INSEL_RSZOUT ? &pending.RSZOut_Img : &pending.Src_Img_Ycc, &pending.SRSZOut_Img);
        if (!failure) { complete(2); mask |= 4; }
    }
    if (failure) __sync_fetch_and_add(&failures, 1); else __sync_fetch_and_add(&jobs, 1);
    if (failure && failures <= 4) {
        const struct ipcm_ycc_info *images[2] = {&pending.Src_Img_Ycc, &pending.RSZOut_Img};
        for (int i = 0; i < 2; i++) {
            const struct ipcm_ycc_info *p = images[i];
            fprintf(stderr, "[CPU YCC FORMAT] plane=%d stride=%u,%u scan=%d ycc=%d nlc=%u offset=%u,%u addresses=%x,%x,%x,%x\n",
                i, p->stride_y, p->stride_c, p->scan_type, p->ycc_type, p->NLC_sw, p->pix_offset_x, p->pix_offset_y,
                p->addr_y0, p->addr_y1, p->addr_c0, p->addr_c1);
        }
    }
    if (jobs+failures <= 16) fprintf(stderr, "[CPU YCC RESIZE] source=%ux%u rsz=%ux%u sub=%ux%u mask=%u error=%d\n",
        pending.Src_Img_Ycc.width, pending.Src_Img_Ycc.height, pending.RSZOut_Img.width, pending.RSZOut_Img.height,
        pending.SRSZOut_Img.width, pending.SRSZOut_Img.height, mask, failure);
    char status[256];
    int bytes = snprintf(status, sizeof(status), "{\"jobs\":%u,\"failures\":%u,\"last_mask\":%u,\"last_error\":%d,\"filter\":\"software-bilinear-align-corners\",\"hardware_bit_exact\":false}\n", jobs, failures, mask, failure);
    int fd = open("/var/run/nx300m-ipcm-resize-status.json.tmp", 0101|01000, 0600);
    if (fd >= 0) { write(fd, status, bytes); close(fd); rename("/var/run/nx300m-ipcm-resize-status.json.tmp", "/var/run/nx300m-ipcm-resize-status.json"); }
}
