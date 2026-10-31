/* Read-only observer of accepted NX300M LCD video submissions. This module
 * never replaces a driver ioctl, a DMA completion, or a display interrupt.
 * Build against the ARM EABI, with the stock glibc 2.13 resolved at runtime. */
typedef unsigned int u32;
typedef unsigned short u16;
typedef unsigned long long u64;
extern void *dlsym(void *, const char *);
extern int *__errno_location(void);
extern int open(const char *, int, ...), close(int), pipe(int *);
extern int read(int, void *, u32), write(int, const void *, u32);
extern int readlink(const char *, char *, u32), strcmp(const char *, const char *);
extern int snprintf(char *, u32, const char *, ...);
extern int rename(const char *, const char *), unlink(const char *);
extern void *mmap64(void *, u32, int, int, int, u64);
extern int munmap(void *, u32);
extern int sched_yield(void);

struct lcd_video {
    u32 window,on,format,bit,address_mode;
    u16 stride,padding;
    u32 width,height,x,display_width,y,display_height,y0,y1,c0,c1;
};
struct lcd_window { struct lcd_video video; u32 configured,visible,ring_size,irq,submitted,completed,failed; };
struct lcd_state { u32 magic,version,sequence,reserved; struct lcd_window windows[4]; };
typedef char video_abi_check[sizeof(struct lcd_video)==64 ? 1 : -1];
typedef char state_abi_check[sizeof(struct lcd_state)==384 ? 1 : -1];
static struct lcd_state state = { .magic=0x434c584eU, .version=1, .reserved=1 };
static volatile int capture_lock;
static int (*original_ioctl)(int, u32, void *);

static int write_all(int fd, const void *data, u32 bytes) {
    const unsigned char *p=data;
    while (bytes) {
        int n=write(fd,p,bytes);
        if (n<0 && *__errno_location()==4) continue;
        if (n<=0) return -1;
        bytes-=(u32)n; p+=n;
    }
    return 0;
}

/* A private pipe uses the existing kernel copy_from_user path. A successful
 * driver call does not guarantee the argument is still readable afterwards.
 * Never memcpy/dereference application pointers in the observer. */
static int copy_argument(void *target, const void *source, u32 bytes) {
    int fds[2];
    if (pipe(fds)<0) return -1;
    int n;
    do { n=write(fds[1],source,bytes); } while (n<0 && *__errno_location()==4);
    close(fds[1]);
    int ok=n==(int)bytes;
    u32 done=0;
    while (ok && done<bytes) {
        n=read(fds[0],(unsigned char *)target+done,bytes-done);
        if (n<0 && *__errno_location()==4) continue;
        if (n<=0) { ok=0; break; }
        done+=(u32)n;
    }
    close(fds[0]);
    return ok ? 0 : -1;
}

static int is_lcd(int fd) {
    char path[48],target[80];
    snprintf(path,sizeof path,"/proc/self/fd/%d",fd);
    int length=readlink(path,target,sizeof target-1);
    if (length<0) return 0;
    target[length]=0;
    return !strcmp(target,"/dev/fb0");
}

/* Both state and image files are replaced atomically. Publishing odd before
 * the image rename prevents existing readers from mixing two generations. */
static int publish_state(void) {
    static const unsigned char zeros[4096-sizeof(struct lcd_state)];
    const char *temp="/var/run/nx300m-lcd-state.bin.capture.tmp";
    int fd=open(temp,0101|01000|02000000,0600);
    if (fd<0) return -1;
    int result=write_all(fd,&state,sizeof state);
    if (!result) result=write_all(fd,zeros,sizeof zeros);
    if (close(fd)<0) result=-1;
    if (!result) result=rename(temp,"/var/run/nx300m-lcd-state.bin");
    if (result) unlink(temp);
    return result;
}

struct plane { const unsigned char *data; void *mapping; u32 length; };
static int map_plane(struct plane *plane, int fd, u32 address, u32 bytes, u32 base, u32 size) {
    if (!address || !bytes || (u64)address+bytes>0x100000000ULL) return -1;
    if (fd<0) { plane->data=(void *)address; return 0; }
    if (address<base || (u64)address+bytes>(u64)base+size) return -1;
    u32 start=address&~4095U,delta=address-start;
    u64 mapping_end=((u64)address+bytes+4095)&~4095ULL;
    if (start<base || mapping_end>(u64)base+size) return -1;
    if ((u64)delta+bytes>0xffffffffULL) return -1;
    plane->length=delta+bytes;
    plane->mapping=mmap64(0,plane->length,1,1,fd,(u64)start);
    if (plane->mapping==(void *)-1) { plane->mapping=0; return -1; }
    plane->data=(const unsigned char *)plane->mapping+delta;
    return 0;
}

static int export_video(const struct lcd_video *v, char *temporary, char *target) {
    u32 pitch=v->stride/2;
    if (v->window>3 || v->on>1 || v->format>1 || v->bit || v->address_mode>1 ||
        !v->width || !v->height || v->width>8192 || v->height>8192 ||
        ((v->width|v->height)&1) || (v->stride&7) || pitch<v->width ||
        v->display_width!=v->width || v->display_height!=v->height ||
        (u64)v->y0+pitch!=v->y1 || (u64)v->c0+pitch!=v->c1) return -1;
    u32 chroma_rows=v->height/(v->format==0 ? 2 : 1);
    u32 ybytes=(v->height-1)*pitch+v->width;
    u32 cbytes=(chroma_rows-1)*pitch+v->width;
    int sma=-1,output=-1,result=-1;
    u32 base=0,size=0;
    struct plane yp={0},cp={0};
    if (v->address_mode==1) {
        /* Stock sma_mmap honors O_SYNC by using pgprot_noncached. */
        sma=open("/dev/d4_sma",02000000|04010000);
        if (sma<0 || original_ioctl(sma,0x80047304U,&base)<0 ||
            original_ioctl(sma,0x80047301U,&size)<0 || !base || !size ||
            (u64)base+size>0x100000000ULL) goto done;
    }
    if (map_plane(&yp,sma,v->y0,ybytes,base,size) ||
        map_plane(&cp,sma,v->c0,cbytes,base,size)) goto done;
    snprintf(temporary,96,"/var/run/nx300m-lcd-video-%u.yuv.capture.tmp",v->window);
    snprintf(target,96,"/var/run/nx300m-lcd-video-%u.yuv",v->window);
    output=open(temporary,0101|01000|02000000,0600);
    if (output<0) goto done;
    result=write_all(output,v,sizeof *v);
    for (u32 row=0;!result && row<v->height;row++)
        result=write_all(output,yp.data+row*pitch,v->width);
    for (u32 row=0;!result && row<chroma_rows;row++)
        result=write_all(output,cp.data+row*pitch,v->width);
done:
    if (output>=0 && close(output)<0) result=-1;
    if (yp.mapping) munmap(yp.mapping,yp.length);
    if (cp.mapping) munmap(cp.mapping,cp.length);
    if (sma>=0) close(sma);
    if (result && *temporary) unlink(temporary);
    return result;
}

static void observe(u32 request, void *arg, int accepted) {
    union { struct lcd_video video; u32 words[16]; } input;
    u32 bytes=request==0x40407406U ? 64 : request==0x40207407U ? 32 : 8;
    if (copy_argument(&input,arg,bytes) || input.words[0]>3) {
        /* An accepted but unreadable descriptor cannot be attributed to a
         * particular plane. Invalidate exports rather than show stale data. */
        if (accepted) {
            for (u32 i=0;i<4;i++) { state.windows[i].completed=0; state.windows[i].failed++; }
            state.sequence=(state.sequence+2)&~1U;
            publish_state();
        }
        return;
    }
    struct lcd_window *w=&state.windows[input.words[0]];
    char temporary[96]={0},target[96]={0};
    int image=0;
    if (request==0x40407406U || request==0x40207407U) {
        w->submitted++;
        w->completed=0;
        if (accepted && request==0x40407406U) {
            w->video=input.video;
            w->configured=1;
            w->visible=input.video.on==1;
        } else if (accepted && w->configured) {
            w->video.address_mode=input.words[1];
            w->video.y0=input.words[2]; w->video.y1=input.words[3];
            w->video.c0=input.words[4]; w->video.c1=input.words[5];
        }
        if (accepted && w->configured && !export_video(&w->video,temporary,target)) image=1;
        else w->failed++;
    } else if (accepted && input.words[1]<=1) {
        w->visible=input.words[1];
        w->video.on=input.words[1];
    }
    state.sequence|=1U;
    int odd_published=!publish_state();
    if (image) {
        if (odd_published && !rename(temporary,target)) w->completed=1;
        else { w->failed++; unlink(temporary); }
    }
    __sync_synchronize();
    state.sequence++;
    publish_state();
}

/* libc's variadic entry receives the optional argument in r2. A fixed three
 * register EABI entry forwards it without va_arg, including argumentless
 * requests. All unrelated requests go straight to the original libc entry. */
int ioctl(int fd, u32 request, void *argument) {
    int previous_errno=*__errno_location();
    if (!original_ioctl) original_ioctl=dlsym((void *)-1,"ioctl");
    if (!original_ioctl) { *__errno_location()=38; return -1; }
    int watched=(request==0x40407406U || request==0x40207407U || request==0x40087405U) && is_lcd(fd);
    *__errno_location()=previous_errno;
    if (!watched) return original_ioctl(fd,request,argument);
    while (__sync_lock_test_and_set(&capture_lock,1)) sched_yield();
    *__errno_location()=previous_errno;
    int result=original_ioctl(fd,request,argument);
    int original_errno=*__errno_location();
    observe(request,argument,result>=0);
    __sync_lock_release(&capture_lock);
    *__errno_location()=original_errno;
    return result;
}
