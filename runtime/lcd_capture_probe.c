/* ARM userspace fixture only; it does not emulate successful physical I/O. */
typedef unsigned int u32;
typedef unsigned short u16;
extern int printf(const char *, ...), open(const char *, int, ...), close(int);
extern int ioctl(int, u32, ...), read(int, void *, u32), *__errno_location(void);
extern void fixture_config(int,int);
extern int fixture_calls(void), fixture_maps(void);
struct video {
    u32 window,on,format,bit,address_mode;
    u16 stride,padding;
    u32 width,height,x,display_width,y,display_height,y0,y1,c0,c1;
};
struct window { struct video v; u32 configured,visible,ring_size,irq,submitted,completed,failed; };
struct state { u32 magic,version,sequence,reserved; struct window windows[4]; };
#define CHECK(x) do { if (!(x)) { printf("FAIL line=%d errno=%d calls=%d\n",__LINE__,*__errno_location(),fixture_calls()); return 1; } } while (0)
static int load(const char *path, void *out, u32 capacity) {
    int fd=open(path,0);
    if (fd<0) return -1;
    int n=read(fd,out,capacity); close(fd); return n;
}
int main(void) {
    int fd=open("/dev/fb0",2), other=open("/tmp/unrelated",0102,0600);
    CHECK(fd>=0 && other>=0);
    fixture_config(7,123);
    CHECK(ioctl(other,0x40407406U,(void *)1)==7 && *__errno_location()==123);
    CHECK(ioctl(fd,0x1234U)==7 && *__errno_location()==123);
    unsigned char state_bytes[4096],image[256];
    CHECK(load("/var/run/nx300m-lcd-state.bin",state_bytes,sizeof state_bytes)==-1);
    unsigned char y[32],c[16];
    for (u32 i=0;i<32;i++) y[i]=(unsigned char)i;
    for (u32 i=0;i<16;i++) c[i]=(unsigned char)(128+i);
    struct video v={0,1,0,0,0,16,0,4,4,0,4,0,4,(u32)y,(u32)y+8,(u32)c,(u32)c+8};
    CHECK(ioctl(fd,0x40407406U,&v)==7 && *__errno_location()==123);
    CHECK(load("/var/run/nx300m-lcd-state.bin",state_bytes,sizeof state_bytes)==4096);
    struct state *s=(void *)state_bytes;
    CHECK(s->magic==0x434c584eU && s->version==1 && s->reserved==1 && !(s->sequence&1));
    CHECK(s->windows[0].visible==1 && s->windows[0].completed==1);
    CHECK(load("/var/run/nx300m-lcd-video-0.yuv",image,sizeof image)==88);
    for (u32 row=0;row<4;row++) for (u32 x=0;x<4;x++) CHECK(image[64+row*4+x]==y[row*8+x]);
    for (u32 row=0;row<2;row++) for (u32 x=0;x<4;x++) CHECK(image[80+row*4+x]==c[row*8+x]);
    for (u32 i=0;i<32;i++) y[i]=(unsigned char)(99+i);
    u32 address[8]={0,0,(u32)y,(u32)y+8,(u32)c,(u32)c+8,0,0};
    fixture_config(0,77);
    CHECK(!ioctl(fd,0x40207407U,address) && *__errno_location()==77);
    CHECK(load("/var/run/nx300m-lcd-video-0.yuv",image,sizeof image)==88 && image[64]==99);
    u32 visibility[2]={0,0};
    CHECK(!ioctl(fd,0x40087405U,visibility) && *__errno_location()==77);
    CHECK(load("/var/run/nx300m-lcd-state.bin",state_bytes,sizeof state_bytes)==4096 && !s->windows[0].visible);
    fixture_config(-1,5);
    CHECK(ioctl(fd,0x40407406U,&v)==-1 && *__errno_location()==5);
    CHECK(load("/var/run/nx300m-lcd-state.bin",state_bytes,sizeof state_bytes)==4096 && !s->windows[0].completed);
    fixture_config(3,31);
    v.bit=1;
    CHECK(ioctl(fd,0x40407406U,&v)==3 && *__errno_location()==31);
    CHECK(load("/var/run/nx300m-lcd-state.bin",state_bytes,sizeof state_bytes)==4096 && !s->windows[0].completed);
    CHECK(ioctl(fd,0x40407406U,(void *)1)==3 && *__errno_location()==31);
    v.bit=0; v.y0=1; v.y1=9;
    CHECK(ioctl(fd,0x40407406U,&v)==3 && *__errno_location()==31);
    CHECK(load("/var/run/nx300m-lcd-state.bin",state_bytes,sizeof state_bytes)==4096 && !s->windows[0].completed);
    v.address_mode=1; v.y0=0x1000; v.y1=0x1008; v.c0=0x2000; v.c1=0x2008;
    CHECK(ioctl(fd,0x40407406U,&v)==3 && *__errno_location()==31);
    CHECK(fixture_maps()==0);
    CHECK(load("/var/run/nx300m-lcd-state.bin",state_bytes,sizeof state_bytes)==4096 && !s->windows[0].completed);
    CHECK(!(s->sequence&1));
    close(fd); close(other);
    printf("LCD_CAPTURE forwarding=ok errno=ok argumentless=ok padded_yuv=ok address_update=ok visibility=ok rejected_submit=hidden invalid_virtual_pointer=safe out_of_sma=no_mmap sequence=even physical_driver_verified=false\n");
    return 0;
}
