/* Original ARM setup/start/wait with actual software-resized buffer contents. */
#include "../work/vendor-source/TIZEN/project/NX300M/imagedev/usr/include/drime4/udd/ipcm_type.h"
typedef unsigned int u32;
extern int printf(const char *, ...), setvbuf(void *, char *, int, u32);
extern void *stdout;
extern void *dlsym(void *, const char *);
extern int open(const char *, int, ...), ioctl(int, u32, ...), read(int, void *, u32), *__errno_location(void);
extern void *mmap(void *, u32, int, int, int, long);
extern int mprotect(void *, u32, int);
extern int ipcm_open_reg_base(int), ipcm_ycc_combi_set(struct ipcm_ycc_combi_op_info *);
extern void ipcm_ycc_combi_do_1frm(void);
#define CHECK(condition) do { if (!(condition)) { printf("FAIL line=%d errno=%d\n", __LINE__, *__errno_location()); return 1; } } while (0)
static struct ipcm_ycc_info image(u32 width, u32 height, u32 address) {
    struct ipcm_ycc_info info = {0};
    info.width = width; info.height = height; info.stride_y = info.stride_c = 64;
    info.ycc_type = IPCM_YCC420; info.scan_type = IPCM_PROGRESSIVE;
    info.addr_y0 = address; info.addr_y1 = address+64;
    info.addr_c0 = address+64*height; info.addr_c1 = info.addr_c0+64;
    return info;
}
int main(void) {
    setvbuf(stdout, (char *)0, 2, 0);
    CHECK(dlsym((void *)0, "nx300m_map_physical"));
    int fd = open("/dev/d4_ipcm", 2|04000), bma = open("/dev/d4_bma", 2);
    CHECK(fd >= 0 && bma >= 0 && ipcm_open_reg_base(fd) == 0);
    int opened = -1;
    CHECK(ioctl(fd, 0x80046d06U, &opened) == 0 && opened == 0);
    struct {u32 physical, bytes;} allocation = {0,8192};
    CHECK(ioctl(bma, 0xc0086201U, &allocation) == 0);
    unsigned char *data = mmap((void *)0, allocation.bytes, 3, 1, bma, allocation.physical);
    CHECK(data != (void *)-1);
    for (u32 i = 0; i < allocation.bytes; i++) data[i] = 0xa5;
    for (u32 y = 0; y < 32; y++) for (u32 x = 0; x < 64; x++) data[y*64+x] = x+y;
    for (u32 y = 0; y < 16; y++) for (u32 x = 0; x < 64; x++) data[2048+y*64+x] = x&1 ? 0xb4 : 0x31;
    struct ipcm_ycc_combi_op_info info = {0};
    info.Src_Img_Ycc = image(64,32,allocation.physical);
    info.Src_Img_Ycc.width = 62; info.Src_Img_Ycc.pix_offset_x = 2;
    info.RSZOut_Img = image(32,16,allocation.physical+4096);
    info.RSZOut_Img.reverse_CbCr = 1;
    info.RSZ_SW = info.WDMA_RSZ_SW = IPCM_SW_ON;
    info.srsz_fir_x_autoset = info.srsz_fir_y_autoset = 1;
    u32 enabled[4] = {0,1,0,0};
    CHECK(ioctl(fd, 0x40106d08U, enabled) == 0);
    int timeout = 1;
    CHECK(ioctl(fd, 0xc0046d04U, &timeout) == 0 && timeout == -1);
    CHECK(mprotect(data, 4096, 1) == 0);
    CHECK(mprotect(data+4096, 4096, 2) == 0);
    CHECK(ipcm_ycc_combi_set(&info) == 0);
    ipcm_ycc_combi_do_1frm();
    timeout = 1;
    CHECK(ioctl(fd, 0xc0046d04U, &timeout) == 0 && timeout == 0);
    CHECK(mprotect(data+4096, 4096, 1) == 0);
    unsigned char *target = data+4096;
    CHECK(target[0] == 2 && target[31] == 63 && target[15*64] == 33 && target[15*64+31] == 94);
    CHECK(target[1024] == 0xb4 && target[1025] == 0x31);
    CHECK(target[32] == 0xa5 && target[15*64+32] == 0xa5 && target[1024+32] == 0xa5);
    unsigned char event = 0;
    CHECK(read(fd, &event, 1) == 0 && event == 19);
    printf("STOCK_RESIZE original_setup_start_wait=1 source_crop_applied=1 gradient_corners_preserved=1 chroma_order_changed=1 padding_preserved=1 completion_after_pixels=1\n");
    CHECK(ioctl(fd, 0x40106d08U, enabled) == 0);
    CHECK(ipcm_ycc_combi_set(&info) == 0);
    ipcm_ycc_combi_do_1frm();
    timeout = 1;
    CHECK(ioctl(fd, 0xc0046d04U, &timeout) == 0 && timeout == -1);
    CHECK(target[0] == 2 && target[31] == 63);
    printf("STOCK_RESIZE readonly_source_writeonly_target=pass readonly_target_rejected_without_completion=pass\n");
    CHECK(ioctl(fd, 0x40106d08U, enabled) == 0);
    info.Saturation_Correction_SW = 1;
    CHECK(ipcm_ycc_combi_set(&info) == 0);
    ipcm_ycc_combi_do_1frm();
    timeout = 1;
    CHECK(ioctl(fd, 0xc0046d04U, &timeout) == 0 && timeout == -1);
    printf("STOCK_RESIZE unsupported_color_operation_did_not_complete=1 hardware_bit_exact=0\n");
    return 0;
}
