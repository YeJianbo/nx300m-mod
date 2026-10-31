/* Scanout of exported original LCD submissions, from the lab backend or the
 * native read-only observer. Keep in step with runtime/fb_preview.h,
 * runtime/lcd_capture.c and tools/lcd_plane.py's ABI. Writer provenance is
 * independent of whether physical sensor/display operation was verified.
 */
struct lcd_video {
    uint32_t window,on,format,bit,address_mode;
    uint16_t stride,padding;
    uint32_t width,height,x,display_width,y,display_height,y0,y1,c0,c1;
};
struct lcd_window { struct lcd_video video; uint32_t configured,visible,ring_size,irq,submitted,completed,failed; };
struct lcd_state { uint32_t magic,version,sequence,reserved; struct lcd_window windows[4]; };
static unsigned char screen_video[800*480*3], screen_opaque[800*480];
static unsigned char clamp_rgb(int value) { return value<0?0:value>255?255:value; }
static int lcd_state_read(struct lcd_state *state) {
    FILE *file=fopen("/var/run/nx300m-lcd-state.bin","rb");
    if (!file) return 0;
    int ok=fread(state,1,sizeof *state,file)==sizeof *state;
    fclose(file);
    return ok && state->magic==0x434c584eU && state->version==1 ? 1 : -1;
}
static const char *lcd_video_source(void) {
    struct lcd_state state;
    if (lcd_state_read(&state)!=1) return "none";
    if (state.reserved==1) return "stock-lcd-observer";
    if (state.reserved==0) return "laboratory-lcd-backend";
    return "unknown-lcd-writer";
}
static const char *lcd_screen_source(void) {
    struct lcd_state state;
    return lcd_state_read(&state)==1 ? "stock-lcd-video-and-premultiplied-osd" : "stock-x11-osd";
}
static int lcd_video_read(int width,int height) {
    struct lcd_state before,after;
    for (int attempt=0;attempt<4;attempt++) {
        memset(screen_video,0,sizeof screen_video);
        int state=lcd_state_read(&before);
        if (!state) return 1; /* Stock X11-only screens also remain usable. */
        if (state<0) return 0;
        if (before.sequence&1) { usleep(2000);continue; }
        for (int index=0;index<4;index++) {
            const struct lcd_window *window=&before.windows[index];
            if (!window->visible || !window->completed) continue;
            char path[96];snprintf(path,sizeof path,"/var/run/nx300m-lcd-video-%d.yuv",index);
            FILE *file=fopen(path,"rb");
            if (!file) return 0;
            struct lcd_video info;
            if (fread(&info,1,sizeof info,file)!=sizeof info || info.window!=(uint32_t)index ||
                !info.width || !info.height || info.width>8192 || info.height>8192 ||
                ((info.width|info.height)&1) || info.bit || info.format>1 ||
                info.display_width!=info.width || info.display_height!=info.height) { fclose(file);return 0; }
            size_t luma=(size_t)info.width*info.height;
            size_t chroma=(size_t)info.width*(info.height/(info.format==0?2:1));
            unsigned char *data=malloc(luma+chroma);
            if (!data) { fclose(file);return 0; }
            int ok=fread(data,1,luma+chroma,file)==luma+chroma && fgetc(file)==EOF;
            fclose(file);
            if (!ok) { free(data);return 0; }
            if (info.x>=(uint32_t)width || info.y>=(uint32_t)height) { free(data);continue; }
            for (uint32_t y=0;y<info.height && y+info.y<(uint32_t)height;y++)
                for (uint32_t x=0;x<info.width && x+info.x<(uint32_t)width;x++) {
                    int yy=data[(size_t)y*info.width+x];
                    size_t at=luma+(size_t)(y/(info.format==0?2:1))*info.width+(x&~1U);
                    int cb=data[at]-128,cr=data[at+1]-128;
                    unsigned char *out=screen_video+((y+info.y)*width+x+info.x)*3;
                    /* BT.601 full-range CbCr, matching the local viewer. The
                     * actual hardware's color encoding is not calibrated. */
                    out[0]=clamp_rgb(yy+((91881*cr)>>16));
                    out[1]=clamp_rgb(yy-((22554*cb+46802*cr)>>16));
                    out[2]=clamp_rgb(yy+((116130*cb)>>16));
                }
            free(data);
        }
        if (lcd_state_read(&after)==1 && after.sequence==before.sequence && !(after.sequence&1)) return 1;
    }
    return 0;
}
static void opaque_windows(int width,int height) {
    memset(screen_opaque,0,sizeof screen_opaque);
    Window root=DefaultRootWindow(display),returned_root,parent,*children=NULL;unsigned int count=0;
    if (!XQueryTree(display,root,&returned_root,&parent,&children,&count)) return;
    for (unsigned int i=0;i<count;i++) {
        char *name=NULL;XWindowAttributes a;
        int opaque=XFetchName(display,children[i],&name) && name &&
            (!strcmp(name,"Metol-NX300M") || !strcmp(name,"NX300M Quick Share"));
        if (name) XFree(name);
        if (!opaque || !XGetWindowAttributes(display,children[i],&a) || a.map_state!=IsViewable) continue;
        int left=a.x<0?0:a.x,top=a.y<0?0:a.y;
        int right=a.x+a.width,bottom=a.y+a.height;
        if (right>width) right=width;
        if (bottom>height) bottom=height;
        for (int y=top;y<bottom;y++) for (int x=left;x<right;x++) screen_opaque[y*width+x]=255;
    }
    if (children) XFree(children);
}
