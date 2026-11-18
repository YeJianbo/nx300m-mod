/* NX300M menu backend: Nuklear layout, FreeType CPU rasterization, core X11
 * upload. No server-side pixmap, glyph rendering, Render composite or readback.
 * Set the unused high byte explicitly too; every menu pixel is opaque without
 * a viewer repairing its alpha. The physical scanout format remains unverified.
 */
#ifndef NXUI_XIMAGE_H
#define NXUI_XIMAGE_H
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <X11/keysym.h>
#include <ft2build.h>
#include FT_FREETYPE_H
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <limits.h>
#include <sys/mman.h>
#include <sys/shm.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

typedef unsigned long XShmSeg;
typedef struct {
    XShmSeg shmseg;
    int shmid;
    char *shmaddr;
    Bool readOnly;
} XShmSegmentInfo;
extern Bool XShmQueryExtension(Display *);
extern XImage *XShmCreateImage(Display *, Visual *, unsigned int, int, char *, XShmSegmentInfo *, unsigned int, unsigned int);
extern Bool XShmAttach(Display *, XShmSegmentInfo *);
extern Bool XShmDetach(Display *, XShmSegmentInfo *);
extern Bool XShmPutImage(Display *, Drawable, GC, XImage *, int, int, int, int, unsigned int, unsigned int, Bool);

static int nx_shm_error_seen;
static int nx_shm_err_handler(Display *dpy, XErrorEvent *ev) {
    (void)dpy; (void)ev;
    nx_shm_error_seen = 1;
    return 0;
}

/* Per-frame phase timing, off unless the panel runs with NX_PANEL_TRACE=1.
 * The counters separate damage detection, software rasterization and the
 * rotated XImage upload so a slow frame can be attributed instead of guessed. */
static int nx_cpu_timing;
static unsigned long long nx_cpu_us(void) {
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts)) return 0;
    return (unsigned long long)ts.tv_sec * 1000000ULL + (unsigned long long)ts.tv_nsec / 1000;
}

struct nx_cpu_glyph {
    nk_rune rune;
    unsigned used;
    int valid, advance, left, top;
    unsigned w, h;
    unsigned char *coverage;
};
typedef struct XFont {
    FT_Library library;
    FT_Face ft;
    void *mapping;
    size_t mapping_size;
    int ascent, height;
    struct nk_user_font handle;
    /* Four-way bounded cache plus direct O(1) ASCII slot table. */
    unsigned clock;
    struct nx_cpu_glyph *ascii[128];
    struct nx_cpu_glyph glyphs[256][4];
} XFont;
/* Faces and glyph caches keep their own sizes; the UI thread shares the
 * FreeType library and the read-only font file mapping across sizes so opening
 * both panel and hint fonts does not mmap the TTF twice. */
static FT_Library nx_cpu_library;
static unsigned nx_cpu_library_users;
static struct {
    char *path;
    void *mapping;
    size_t size;
    unsigned users;
} nx_cpu_font_file;
static int nx_cpu_font_acquire_file(const char *path, void **mapping, size_t *size) {
    if (nx_cpu_font_file.mapping && nx_cpu_font_file.path && !strcmp(nx_cpu_font_file.path, path)) {
        nx_cpu_font_file.users++;
        *mapping = nx_cpu_font_file.mapping;
        *size = nx_cpu_font_file.size;
        return 0;
    }
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return -1;
    off_t bytes = lseek(fd, 0, SEEK_END);
    if (bytes <= 0 || bytes > LONG_MAX) { close(fd); return -1; }
    void *addr = mmap(NULL, (size_t)bytes, PROT_READ, MAP_PRIVATE, fd, 0);
    close(fd);
    if (addr == MAP_FAILED) return -1;
    if (!nx_cpu_font_file.mapping) {
        char *copy = strdup(path);
        if (copy) {
            nx_cpu_font_file.path = copy;
            nx_cpu_font_file.mapping = addr;
            nx_cpu_font_file.size = (size_t)bytes;
            nx_cpu_font_file.users = 1;
        }
    }
    *mapping = addr;
    *size = (size_t)bytes;
    return 0;
}
static void nx_cpu_font_release_file(void *mapping, size_t size) {
    if (!mapping) return;
    if (mapping == nx_cpu_font_file.mapping) {
        if (--nx_cpu_font_file.users) return;
        free(nx_cpu_font_file.path);
        nx_cpu_font_file.path = NULL;
        nx_cpu_font_file.mapping = NULL;
        nx_cpu_font_file.size = 0;
    }
    munmap(mapping, size);
}
struct nx_cpu_box { int left,top,right,bottom; };
enum { NX_CPU_MAX_DAMAGE = 8 };
struct nx_cpu_frame { unsigned char *data; size_t size,capacity; };
struct nx_cpu_record {
    unsigned span,bytes,type,revision;
    struct nx_cpu_box clip,bounds;
};
typedef struct XSurface {
    Display *dpy;
    int screen, present_failed;
    GC gc;
    unsigned int w, h, presents;
    int rotated_ready, image_valid, force_full, skip_put;
    unsigned full_uploads, partial_uploads, skipped_uploads;
    unsigned long long uploaded_pixels;
    unsigned long long raster_pixels;
    unsigned long long last_damage_us, last_raster_us, last_upload_us;
    unsigned long long last_rotate_us, last_put_us, last_flush_us;
    unsigned long long last_raster_pixels, last_upload_pixels;
    struct nx_cpu_frame previous,current;
    struct nx_cpu_box damage_rects[NX_CPU_MAX_DAMAGE];
    int damage_count;
    uint32_t clear_color;
    uint32_t *pixels;
    XImage *image;
    XShmSegmentInfo shm_info;
    int shm_ready, shm_pending_sync;
    int left, top, right, bottom;
} XSurface;
static struct { Display *dpy; struct nk_context ctx; XSurface *surf; } xlib;
struct nx_cpu_image { unsigned w,h;const uint32_t *pixels; unsigned revision; };

static int nx_cpu_rune(const char *text, int size, nk_rune *rune) {
    int length = nk_utf_decode(text, rune, size);
    if (length <= 0) { *rune = 0xfffd; return 1; }
    return length;
}
static struct nx_cpu_glyph *nx_cpu_get_glyph(XFont *font, nk_rune rune) {
    if (rune < 128u) {
        struct nx_cpu_glyph *fast = font->ascii[rune];
        if (fast && fast->valid && fast->rune == rune) {
            fast->used = ++font->clock;
            return fast;
        }
    }
    struct nx_cpu_glyph *set=font->glyphs[(unsigned)rune%256u],*slot=&set[0];
    for(unsigned i=0;i<4;i++) {
        if(set[i].valid&&set[i].rune==rune){
            set[i].used=++font->clock;
            if(rune<128u)font->ascii[rune]=&set[i];
            return &set[i];
        }
        if(!set[i].valid||(slot->valid&&set[i].used<slot->used))slot=&set[i];
    }
    if(slot->valid&&slot->rune<128u&&font->ascii[slot->rune]==slot)font->ascii[slot->rune]=NULL;
    free(slot->coverage);memset(slot,0,sizeof *slot);
    slot->rune=rune;slot->used=++font->clock;slot->valid=1;
    if(rune<128u)font->ascii[rune]=slot;
    if(FT_Load_Char(font->ft,rune,FT_LOAD_RENDER|FT_LOAD_TARGET_NORMAL))return slot;
    FT_GlyphSlot glyph=font->ft->glyph;FT_Bitmap *bitmap=&glyph->bitmap;
    slot->advance=glyph->advance.x>>6;slot->left=glyph->bitmap_left;slot->top=glyph->bitmap_top;
    slot->w=bitmap->width;slot->h=bitmap->rows;
    if(!slot->w||!slot->h)return slot;
    slot->coverage=malloc((size_t)slot->w*slot->h);
    if(!slot->coverage)return slot;
    for(unsigned y=0;y<slot->h;y++) {
        const unsigned char *row=bitmap->buffer+(bitmap->pitch>=0?y:slot->h-1-y)*abs(bitmap->pitch);
        for(unsigned x=0;x<slot->w;x++)
            slot->coverage[(size_t)y*slot->w+x]=bitmap->pixel_mode==FT_PIXEL_MODE_MONO?
                ((row[x>>3]&(0x80>>(x&7)))?255:0):row[x];
    }
    return slot;
}
static float nx_cpu_text_width(nk_handle handle, float height, const char *text, int length) {
    XFont *font = handle.ptr; int advance = 0, offset = 0; nk_rune rune;
    (void)height;
    while (text && offset < length) {
        unsigned char c = (unsigned char)text[offset];
        if (c < 0x80u) {
            offset++;
            struct nx_cpu_glyph *fast = font->ascii[c];
            if (fast && fast->valid && fast->rune == c) {
                fast->used = ++font->clock;
                advance += fast->advance;
            } else advance += nx_cpu_get_glyph(font, c)->advance;
        } else {
            offset += nx_cpu_rune(text + offset, length - offset, &rune);
            advance += nx_cpu_get_glyph(font, rune)->advance;
        }
    }
    return (float)advance;
}
static XFont *nx_cpu_font_sized(const char *path, unsigned pixels) {
    XFont *font = calloc(1, sizeof *font);
    if (!font) return NULL;
    if (nx_cpu_font_acquire_file(path, &font->mapping, &font->mapping_size)) {
        free(font); return NULL;
    }
    if (!nx_cpu_library && FT_Init_FreeType(&nx_cpu_library)) {
        nx_cpu_font_release_file(font->mapping, font->mapping_size);
        free(font); return NULL;
    }
    font->library=nx_cpu_library;
    ++nx_cpu_library_users;
    if (FT_New_Memory_Face(font->library, font->mapping, (FT_Long)font->mapping_size, 0, &font->ft) ||
        FT_Set_Pixel_Sizes(font->ft, 0, pixels)) {
        if (font->ft) FT_Done_Face(font->ft);
        nx_cpu_font_release_file(font->mapping, font->mapping_size);
        if (!--nx_cpu_library_users) { FT_Done_FreeType(nx_cpu_library);nx_cpu_library=NULL; }
        free(font); return NULL;
    }
    font->ascent = (font->ft->size->metrics.ascender + 63) >> 6;
    font->height = (font->ft->size->metrics.height + 63) >> 6;
    font->handle.userdata = nk_handle_ptr(font);
    font->handle.height = (float)font->height;
    font->handle.width = nx_cpu_text_width;
    return font;
}
static XFont *nx_cpu_font(const char *path) {return nx_cpu_font_sized(path,28);}
static XFont *nk_xfont_create(Display *dpy, const char *name) {
    (void)dpy; (void)name;
    return nx_cpu_font("/usr/share/fonts/SDIC_GP_US_20120720.ttf");
}
static void nk_xfont_del(Display *dpy, XFont *font) {
    (void)dpy;
    if (!font) return;
    for(unsigned i=0;i<256;i++)for(unsigned j=0;j<4;j++)free(font->glyphs[i][j].coverage);
    FT_Done_Face(font->ft);
    nx_cpu_font_release_file(font->mapping,font->mapping_size);
    if (!--nx_cpu_library_users) { FT_Done_FreeType(nx_cpu_library);nx_cpu_library=NULL; }
    free(font);
}
static void nk_xsurf_free_image(XSurface *s) {
    if (s->shm_ready) {
        XShmDetach(s->dpy, &s->shm_info);
        XSync(s->dpy, False);
        if (s->shm_info.shmaddr && s->shm_info.shmaddr != (char *)-1) shmdt(s->shm_info.shmaddr);
        if (s->image) s->image->data = NULL;
        s->shm_ready = 0;
        s->shm_pending_sync = 0;
    }
    if (s->image) { XDestroyImage(s->image); s->image = NULL; }
    free(s->pixels); s->pixels = NULL;
}
/* Samsung NX300M (X.Org 1.9.3) 实机在窗口映射后，局部 XShmPutImage 无法触发硬件屏幕刷新
 * （含单联合框，均复现"选择界面卡死、仅确认/返回有响应"）；仅走已实测稳定的 XPutImage。 */
static int nk_xsurf_shm_attach(XSurface *s, unsigned int w, unsigned int h) {
    (void)s; (void)w; (void)h;
    return 0;
}
static void nk_xsurf_resize(XSurface *s, unsigned int w, unsigned int h) {
    if (s->w == w && s->h == h && s->image) return;
    if (!w || !h || w > 800 || h > 800) { s->present_failed = 1; return; }
    nk_xsurf_free_image(s);
    s->w = w; s->h = h; s->rotated_ready = s->image_valid = 0;
    s->pixels = calloc((size_t)w * h, sizeof *s->pixels);
    if (!s->pixels) { s->present_failed = 1; return; }
    if (!nk_xsurf_shm_attach(s, w, h)) {
        s->image = XCreateImage(s->dpy, DefaultVisual(s->dpy, s->screen),
            DefaultDepth(s->dpy, s->screen), ZPixmap, 0, NULL, h, w, 32, 0);
        if (!s->image) { s->present_failed = 1; return; }
        /* Verified camera layout; reject other storage instead of uploading a
         * misencoded frame. Xvfb also uses this format in the ARM checks. */
        if (s->image->bits_per_pixel != 32 || s->image->byte_order != LSBFirst ||
            s->image->red_mask != 0xff0000 || s->image->green_mask != 0xff00 || s->image->blue_mask != 0xff ||
            s->image->bytes_per_line < (int)h * 4) { s->present_failed = 1; return; }
        s->image->data = calloc(w, (size_t)s->image->bytes_per_line);
        if (!s->image->data) s->present_failed = 1;
    }
    s->left = s->top = 0; s->right = w; s->bottom = h;
}
/* 在 [0, 65025] 区间与 (t + 127) / 255 逐值严格相等，避免 Cortex-A9 软件除法。 */
static inline unsigned nx_div255_round(unsigned t) {
    unsigned u = t + 128u;
    return (u + (u >> 8)) >> 8;
}
static void nx_cpu_pixel(XSurface *s, int x, int y, struct nk_color c, unsigned coverage) {
    if (x < s->left || y < s->top || x >= s->right || y >= s->bottom) return;
    uint32_t *pixel = s->pixels + (size_t)y * s->w + x;
    unsigned a = nx_div255_round(coverage * c.a), inv = 255u - a;
    if (!a) return;
    if (a == 255u) { *pixel = 0xff000000u | ((uint32_t)c.r<<16) | ((uint32_t)c.g<<8) | c.b; return; }
    uint32_t px = *pixel;
    unsigned r = nx_div255_round(c.r*a + ((px>>16)&255u)*inv);
    unsigned g = nx_div255_round(c.g*a + ((px>>8)&255u)*inv);
    unsigned b = nx_div255_round(c.b*a + (px&255u)*inv);
    *pixel = 0xff000000u | (r<<16) | (g<<8) | b;
}
static void nx_cpu_rect(XSurface *s, int x, int y, int w, int h, int round, struct nk_color c) {
    int r = NK_MIN(round, NK_MIN(w/2, h/2));
    if (!c.a) return;
    int top = NK_MAX(y, s->top), bottom = NK_MIN(y+h, s->bottom);
    int left = NK_MAX(x, s->left), right = NK_MIN(x+w, s->right);
    if (left >= right || top >= bottom) return;
    uint32_t opaque_color = 0xff000000u|((uint32_t)c.r<<16)|((uint32_t)c.g<<8)|c.b;
    for (int j=top; j<bottom; j++) {
        int dy = j < y+r ? y+r-1-j : (j >= y+h-r ? j-(y+h-r) : 0);
        uint32_t *row = s->pixels + (size_t)j*s->w;
        /* The interior and straight edges cover almost the entire menu.
         * Clip once per row and blend only its translucent/rounded pixels. */
        if (c.a == 255 && (!r || !dy)) {
            for (int i=left; i<right; i++) row[i]=opaque_color;
            continue;
        }
        int r2 = r*r, dy2 = dy*dy;
        if (c.a == 255) {
            for (int i=left; i<right; i++) {
                int dx = i < x+r ? x+r-1-i : (i >= x+w-r ? i-(x+w-r) : 0);
                if (dx*dx+dy2 < r2) row[i]=opaque_color;
            }
            continue;
        }
        for (int i=left; i<right; i++) {
            int dx = i < x+r ? x+r-1-i : (i >= x+w-r ? i-(x+w-r) : 0);
            if (!r || dx*dx+dy2 < r2) nx_cpu_pixel(s,i,j,c,255);
        }
    }
}
static void nx_cpu_line(XSurface *s, int x, int y, int ex, int ey, int thickness, struct nk_color c) {
    int dx=abs(ex-x), sx=x<ex?1:-1, dy=-abs(ey-y), sy=y<ey?1:-1, err=dx+dy;
    int size=NK_MAX(thickness,1);
    for (;;) {
        nx_cpu_rect(s,x-size/2,y-size/2,size,size,0,c);
        if (x==ex && y==ey) break;
        int e=2*err;
        if (e>=dy) {err+=dy;x+=sx;} if (e<=dx) {err+=dx;y+=sy;}
    }
}
static long nx_cpu_edge(struct nk_vec2i a, struct nk_vec2i b, int x, int y) {
    return (long)(x-a.x)*(b.y-a.y)-(long)(y-a.y)*(b.x-a.x);
}
static void nx_cpu_triangle(XSurface *s, const struct nk_command_triangle_filled *t) {
    for (int y=NK_MAX(s->top,NK_MIN(t->a.y,NK_MIN(t->b.y,t->c.y))); y<NK_MIN(s->bottom,NK_MAX(t->a.y,NK_MAX(t->b.y,t->c.y))+1); y++)
        for (int x=NK_MAX(s->left,NK_MIN(t->a.x,NK_MIN(t->b.x,t->c.x))); x<NK_MIN(s->right,NK_MAX(t->a.x,NK_MAX(t->b.x,t->c.x))+1); x++) {
            long a=nx_cpu_edge(t->a,t->b,x,y),b=nx_cpu_edge(t->b,t->c,x,y),c=nx_cpu_edge(t->c,t->a,x,y);
            if ((a>=0 && b>=0 && c>=0) || (a<=0 && b<=0 && c<=0)) nx_cpu_pixel(s,x,y,t->color,255);
        }
}
static void nx_cpu_text(XSurface *s, const struct nk_command_text *t) {
    XFont *font=t->font->userdata.ptr; int pen=t->x, offset=0; nk_rune rune;
    struct nk_color c=t->foreground;
    if (!c.a) return;
    uint32_t fg=0xff000000u|((uint32_t)c.r<<16)|((uint32_t)c.g<<8)|c.b;
    int opaque=(c.a==255);
    while (offset<t->length) {
        unsigned char ch=(unsigned char)t->string[offset];
        struct nx_cpu_glyph *glyph=NULL;
        if(ch<0x80u){offset++;if(font->ascii[ch]&&font->ascii[ch]->valid&&font->ascii[ch]->rune==ch){font->ascii[ch]->used=++font->clock;glyph=font->ascii[ch];}else glyph=nx_cpu_get_glyph(font,ch);}
        else{offset+=nx_cpu_rune(t->string+offset,t->length-offset,&rune);glyph=nx_cpu_get_glyph(font,rune);}
        int gx=pen+glyph->left,gy=t->y+font->ascent-glyph->top;
        if(glyph->coverage) {
            int y0=NK_MAX(s->top-gy,0), y1=NK_MIN(s->bottom-gy,(int)glyph->h);
            int x0=NK_MAX(s->left-gx,0), x1=NK_MIN(s->right-gx,(int)glyph->w);
            if(x0<x1&&y0<y1) {
                for(int y=y0;y<y1;y++) {
                    const unsigned char *crow=glyph->coverage+(size_t)y*glyph->w;
                    uint32_t *prow=s->pixels+(size_t)(gy+y)*s->w+gx;
                    if(opaque) {
                        for(int x=x0;x<x1;x++) {
                            unsigned a=crow[x];
                            if(!a)continue;
                            if(a==255){prow[x]=fg;continue;}
                            unsigned inv=255u-a; uint32_t px=prow[x];
                            unsigned r=nx_div255_round(c.r*a+((px>>16)&255u)*inv);
                            unsigned g=nx_div255_round(c.g*a+((px>>8)&255u)*inv);
                            unsigned b=nx_div255_round(c.b*a+(px&255u)*inv);
                            prow[x]=0xff000000u|(r<<16)|(g<<8)|b;
                        }
                    } else {
                        for(int x=x0;x<x1;x++) {
                            unsigned cover=crow[x];
                            if(!cover)continue;
                            unsigned a=nx_div255_round(cover*c.a), inv=255u-a;
                            if(!a)continue;
                            if(a==255){prow[x]=fg;continue;}
                            uint32_t px=prow[x];
                            unsigned r=nx_div255_round(c.r*a+((px>>16)&255u)*inv);
                            unsigned g=nx_div255_round(c.g*a+((px>>8)&255u)*inv);
                            unsigned b=nx_div255_round(c.b*a+(px&255u)*inv);
                            prow[x]=0xff000000u|(r<<16)|(g<<8)|b;
                        }
                    }
                }
            }
        }
        pen+=glyph->advance;
    }
}
static struct nx_cpu_box nx_cpu_intersect(struct nx_cpu_box a,struct nx_cpu_box b) {
    return (struct nx_cpu_box){NK_MAX(a.left,b.left),NK_MAX(a.top,b.top),
                              NK_MIN(a.right,b.right),NK_MIN(a.bottom,b.bottom)};
}
static void nx_cpu_damage_add(struct nx_cpu_box *a,struct nx_cpu_box b) {
    if(b.right<=b.left||b.bottom<=b.top)return;
    a->left=NK_MIN(a->left,b.left);a->top=NK_MIN(a->top,b.top);
    a->right=NK_MAX(a->right,b.right);a->bottom=NK_MAX(a->bottom,b.bottom);
}
static void nx_cpu_damage_push(struct nx_cpu_box *rects,int *count,struct nx_cpu_box b) {
    if(b.right<=b.left||b.bottom<=b.top)return;
    for(;;) {
        int merged=0;
        for(int i=0;i<*count;i++) {
            struct nx_cpu_box r=rects[i];
            if(b.left>=r.left&&b.top>=r.top&&b.right<=r.right&&b.bottom<=r.bottom)return;
            if(r.left>=b.left&&r.top>=b.top&&r.right<=b.right&&r.bottom<=b.bottom) {
                rects[i]=rects[--(*count)];merged=1;break;
            }
            int overlap=!(b.right<=r.left||b.left>=r.right||b.bottom<=r.top||b.top>=r.bottom);
            struct nx_cpu_box u={NK_MIN(r.left,b.left),NK_MIN(r.top,b.top),NK_MAX(r.right,b.right),NK_MAX(r.bottom,b.bottom)};
            long long waste=(long long)(u.right-u.left)*(u.bottom-u.top)-(long long)(r.right-r.left)*(r.bottom-r.top)-(long long)(b.right-b.left)*(b.bottom-b.top);
            if(overlap||waste<=2048){b=u;rects[i]=rects[--(*count)];merged=1;break;}
        }
        if(merged)continue;
        if(*count<NX_CPU_MAX_DAMAGE){rects[(*count)++]=b;return;}
        /* Full: merge the least-wasteful pair (with b or existing) and re-normalize. */
        int best_i=0;long long best_waste=LLONG_MAX;
        for(int i=0;i<*count;i++) {
            struct nx_cpu_box r=rects[i];
            struct nx_cpu_box u={NK_MIN(r.left,b.left),NK_MIN(r.top,b.top),NK_MAX(r.right,b.right),NK_MAX(r.bottom,b.bottom)};
            long long w=(long long)(u.right-u.left)*(u.bottom-u.top)-(long long)(r.right-r.left)*(r.bottom-r.top)-(long long)(b.right-b.left)*(b.bottom-b.top);
            if(w<best_waste){best_waste=w;best_i=i;}
        }
        struct nx_cpu_box r=rects[best_i];
        b=(struct nx_cpu_box){NK_MIN(r.left,b.left),NK_MIN(r.top,b.top),NK_MAX(r.right,b.right),NK_MAX(r.bottom,b.bottom)};
        rects[best_i]=rects[--(*count)];
    }
}
/* Keep owned copies: Nuklear reuses its command storage after nk_clear().
 * Compare command payloads and effective clips exactly, without pixel hashes.
 * Repainting non-overlapping damage boxes through intersecting commands preserves
 * overlaps, removed objects and translucent edges in painter order. */
static void nx_cpu_damage(XSurface *s,struct nk_color clear) {
    const struct nk_command *cmd;
    struct nx_cpu_frame *frame=&s->current,*old=&s->previous;
    struct nx_cpu_box full={0,0,(int)s->w,(int)s->h},clip=full;
    size_t previous=0;frame->size=0;s->damage_count=0;
    uint32_t color=0xff000000u|((uint32_t)clear.r<<16)|((uint32_t)clear.g<<8)|clear.b;
    int complete=!s->image_valid||s->force_full||s->clear_color!=color;
    s->clear_color=color;
    nk_foreach(cmd,&xlib.ctx) {
        struct nx_cpu_record record;memset(&record,0,sizeof record);
        record.type=cmd->type;record.clip=clip;
        size_t bytes=0;struct nx_cpu_box bounds=full;
        const struct nx_cpu_record *before=previous<old->size?(const void *)(old->data+previous):NULL;
        switch(cmd->type) {
        case NK_COMMAND_NOP:continue;
        case NK_COMMAND_SCISSOR: {
            const struct nk_command_scissor *c=(const void *)cmd;
            clip=nx_cpu_intersect(full,(struct nx_cpu_box){c->x,c->y,c->x+c->w,c->y+c->h});continue;
        }
        case NK_COMMAND_RECT_FILLED: {
            const struct nk_command_rect_filled *c=(const void *)cmd;
            bytes=offsetof(struct nk_command_rect_filled,color)+sizeof c->color;
            bounds=(struct nx_cpu_box){c->x,c->y,c->x+c->w,c->y+c->h};
        } break;
        case NK_COMMAND_RECT: {
            const struct nk_command_rect *c=(const void *)cmd;
            bytes=offsetof(struct nk_command_rect,color)+sizeof c->color;
            bounds=(struct nx_cpu_box){c->x,c->y,c->x+c->w,c->y+c->h};
        } break;
        case NK_COMMAND_LINE: {
            const struct nk_command_line *c=(const void *)cmd;
            bytes=offsetof(struct nk_command_line,color)+sizeof c->color;
            int pad=NK_MAX(c->line_thickness,1);
            bounds=(struct nx_cpu_box){NK_MIN(c->begin.x,c->end.x)-pad,NK_MIN(c->begin.y,c->end.y)-pad,
                                      NK_MAX(c->begin.x,c->end.x)+pad+1,NK_MAX(c->begin.y,c->end.y)+pad+1};
        } break;
        case NK_COMMAND_TRIANGLE_FILLED: {
            const struct nk_command_triangle_filled *c=(const void *)cmd;
            bytes=offsetof(struct nk_command_triangle_filled,color)+sizeof c->color;
            bounds=(struct nx_cpu_box){NK_MIN(c->a.x,NK_MIN(c->b.x,c->c.x)),NK_MIN(c->a.y,NK_MIN(c->b.y,c->c.y)),
                                      NK_MAX(c->a.x,NK_MAX(c->b.x,c->c.x))+1,NK_MAX(c->a.y,NK_MAX(c->b.y,c->c.y))+1};
        } break;
        case NK_COMMAND_TEXT: {
            const struct nk_command_text *c=(const void *)cmd;
            bytes=offsetof(struct nk_command_text,string)+(size_t)c->length;
            unsigned payload=(unsigned)(bytes-sizeof *cmd);
            if(before&&before->type==NK_COMMAND_TEXT&&before->bytes==payload&&
               !memcmp(&before->clip,&clip,sizeof clip)&&
               !memcmp((const unsigned char *)before+sizeof record,cmd+1,payload)) {
                record.bounds=before->bounds;
                goto text_cached;
            }
            XFont *font=c->font->userdata.ptr;int pen=c->x,offset=0;nk_rune rune;
            bounds=(struct nx_cpu_box){(int)s->w,(int)s->h,0,0};
            while(offset<c->length) {
                offset+=nx_cpu_rune(c->string+offset,c->length-offset,&rune);
                struct nx_cpu_glyph *g=nx_cpu_get_glyph(font,rune);
                if(g->coverage)nx_cpu_damage_add(&bounds,(struct nx_cpu_box){pen+g->left,c->y+font->ascent-g->top,
                    pen+g->left+(int)g->w,c->y+font->ascent-g->top+(int)g->h});
                pen+=g->advance;
            }
        } break;
        case NK_COMMAND_IMAGE: {
            const struct nk_command_image *c=(const void *)cmd;
            bytes=offsetof(struct nk_command_image,col)+sizeof c->col;
            const struct nx_cpu_image *image=c->img.handle.ptr;
            bounds=(struct nx_cpu_box){c->x,c->y,c->x+c->w,c->y+c->h};
            if(image)record.revision=image->revision;
        } break;
        default:complete=1;continue;
        }
        record.bounds=nx_cpu_intersect(bounds,clip);
text_cached:
        record.bytes=(unsigned)(bytes-sizeof *cmd);
        record.span=(unsigned)((sizeof record+record.bytes+7)&~(size_t)7);
        if(frame->size+record.span>frame->capacity) {
            size_t capacity=(frame->size+record.span+16383)&~(size_t)16383;
            unsigned char *data=realloc(frame->data,capacity);
            if(!data){old->size=0;frame->size=0;s->damage_rects[0]=full;s->damage_count=1;return;}
            frame->data=data;frame->capacity=capacity;
        }
        unsigned char *data=frame->data+frame->size;
        memcpy(data,&record,sizeof record);memcpy(data+sizeof record,cmd+1,record.bytes);
        if(!complete) {
            if(!before||before->bytes!=record.bytes||memcmp(before,&record,sizeof record)||
               memcmp((const unsigned char *)before+sizeof record,cmd+1,record.bytes)) {
                nx_cpu_damage_push(s->damage_rects,&s->damage_count,record.bounds);
                if(before)nx_cpu_damage_push(s->damage_rects,&s->damage_count,before->bounds);
            }
        }
        if(before)previous+=before->span;
        frame->size+=record.span;
    }
    if(!complete) {
        while(previous<old->size) {
            const struct nx_cpu_record *before=(const void *)(old->data+previous);
            nx_cpu_damage_push(s->damage_rects,&s->damage_count,before->bounds);previous+=before->span;
        }
    }
    struct nx_cpu_frame swap=*old;*old=*frame;*frame=swap;
    if(complete){s->damage_rects[0]=full;s->damage_count=1;}
}
static void nx_cpu_put_box(Drawable target, XSurface *s, int x, int y, unsigned w, unsigned h) {
    if (s->shm_ready) XShmPutImage(s->dpy, target, s->gc, s->image, x, y, x, y, w, h, False);
    else XPutImage(s->dpy, target, s->gc, s->image, x, y, x, y, w, h);
}
static void nx_cpu_upload(Drawable target, XSurface *s, unsigned int w, unsigned int h, int changes_only) {
    unsigned long long started=nx_cpu_timing?nx_cpu_us():0,rotated=0;
    s->last_rotate_us=s->last_put_us=0;
    if (s->present_failed) return;
    if (w!=s->w || h!=s->h) {s->present_failed=1;return;}
    int partial=changes_only&&s->image_valid&&!s->force_full;
    if (!partial) {
        if (!s->rotated_ready) {
            if (s->shm_ready && s->shm_pending_sync) { XSync(s->dpy, False); s->shm_pending_sync = 0; }
            int stride=s->image->bytes_per_line;
            char *base=s->image->data;
            for (unsigned by=0;by<h;by+=16) {
                unsigned by_end=NK_MIN(by+16,h);
                unsigned rx0=h-by_end, rx1=h-by;
                for (unsigned bx=0;bx<w;bx+=16) {
                    unsigned bx_end=NK_MIN(bx+16,w);
                    for (unsigned x=bx;x<bx_end;x++) {
                        const uint32_t *src=s->pixels+(size_t)(by_end-1)*w+x;
                        uint32_t *dst=(uint32_t *)(base+(size_t)x*stride)+rx0;
                        for (unsigned rx=rx0;rx<rx1;rx++,src-=w,dst++)
                            *dst=*src;
                    }
                }
            }
            s->rotated_ready=1;
        }
        rotated=nx_cpu_timing?nx_cpu_us():0;
        if(nx_cpu_timing)s->last_rotate_us=rotated-started;
        s->image_valid=1;s->force_full=0;
        if (s->skip_put) return;
        s->last_upload_pixels=(unsigned long long)h*(unsigned long long)w;
        s->full_uploads++;
        s->uploaded_pixels+=s->last_upload_pixels;
        nx_cpu_put_box(target,s,0,0,h,w);
        if(s->shm_ready)s->shm_pending_sync=1;
        if(nx_cpu_timing)s->last_put_us=nx_cpu_us()-rotated;
        if (!s->presents++) fprintf(stderr,"PANEL_PRESENT mode=cpu-ximage raw=%ux%u alpha=255 shm=%d\n",h,w,s->shm_ready);
        return;
    }
    struct nx_cpu_box puts[NX_CPU_MAX_DAMAGE]; int put_count=0;
    unsigned long long upload_px=0;
    int stride=s->image->bytes_per_line;
    char *base=s->image->data;
    if (s->shm_ready && s->shm_pending_sync) { XSync(s->dpy, False); s->shm_pending_sync = 0; }
    for (int k=0;k<s->damage_count;k++) {
        int left=(int)h,top=(int)w,right=0,bottom=0;
        unsigned x0=(unsigned)s->damage_rects[k].left,y0=(unsigned)s->damage_rects[k].top;
        unsigned x1=(unsigned)s->damage_rects[k].right,y1=(unsigned)s->damage_rects[k].bottom;
        for (unsigned by=y0;by<y1;by+=16) {
            unsigned by_end=NK_MIN(by+16,y1);
            unsigned rx0=h-by_end, rx1=h-by;
            for (unsigned bx=x0;bx<x1;bx+=16) {
                unsigned bx_end=NK_MIN(bx+16,x1);
                for (unsigned x=bx;x<bx_end;x++) {
                    const uint32_t *src=s->pixels+(size_t)(by_end-1)*w+x;
                    uint32_t *dst=(uint32_t *)(base+(size_t)x*stride)+rx0;
                    int first_rx=-1, last_rx=-1;
                    for (unsigned rx=rx0;rx<rx1;rx++,src-=w,dst++) {
                        uint32_t value=*src;
                        if(*dst==value)continue;
                        *dst=value;
                        if(first_rx<0)first_rx=(int)rx;
                        last_rx=(int)rx;
                    }
                    if(first_rx>=0) {
                        if(first_rx<left)left=first_rx;
                        if(last_rx+1>right)right=last_rx+1;
                        if((int)x<top)top=(int)x;
                        if((int)x+1>bottom)bottom=(int)x+1;
                    }
                }
            }
        }
        if(right>left&&bottom>top) {
            puts[put_count++]=(struct nx_cpu_box){left,top,right,bottom};
            upload_px+=(unsigned long long)(right-left)*(unsigned long long)(bottom-top);
        }
    }
    s->rotated_ready=1;
    rotated=nx_cpu_timing?nx_cpu_us():0;
    if(nx_cpu_timing)s->last_rotate_us=rotated-started;
    s->image_valid=1;s->force_full=0;
    s->last_upload_pixels=upload_px;
    if(!put_count){s->skipped_uploads++;return;}
    s->partial_uploads++;
    s->uploaded_pixels+=upload_px;
    for (int k = 0; k < put_count; k++)
        nx_cpu_put_box(target, s, puts[k].left, puts[k].top,
                       (unsigned)(puts[k].right - puts[k].left), (unsigned)(puts[k].bottom - puts[k].top));
    if (nx_cpu_timing) s->last_put_us = nx_cpu_us() - rotated;
    /* No per-frame XSync here. The caller already flushes once at the end of
     * draw(); a synchronous round trip after every partial upload was what
     * turned the panel into a slideshow on the device. */
    if(s->shm_ready)s->shm_pending_sync=1;
    if (!s->presents++) fprintf(stderr,"PANEL_PRESENT mode=cpu-ximage raw=%ux%u alpha=255 shm=%d\n",h,w,s->shm_ready);
}
static void nx_cpu_flush(Display *dpy) {
    unsigned long long started=nx_cpu_timing?nx_cpu_us():0;
    XFlush(dpy);
    if(nx_cpu_timing)xlib.surf->last_flush_us=nx_cpu_us()-started;
}
static void nk_xsurf_blit(Drawable target, XSurface *s, unsigned int w, unsigned int h) {
    /* Expose/map must repaint even unchanged pixels of the retained image. */
    nx_cpu_upload(target,s,w,h,0);
}
static struct nk_context *nk_xlib_init(XFont *font, Display *dpy, int screen, Window window,
    Visual *visual, Colormap colormap, unsigned w, unsigned h) {
    (void)visual;(void)colormap;
    xlib.dpy=dpy; xlib.surf=calloc(1,sizeof *xlib.surf);
    if (!xlib.surf) return NULL;
    xlib.surf->dpy=dpy; xlib.surf->screen=screen;
    xlib.surf->gc=XCreateGC(dpy,window,0,NULL);
    nk_xsurf_resize(xlib.surf,w,h);
    if (xlib.surf->present_failed) return NULL;
    nk_init_default(&xlib.ctx,&font->handle); return &xlib.ctx;
}
static void nk_xlib_render(Drawable target, struct nk_color clear) {
    XSurface *s=xlib.surf; const struct nk_command *cmd;
    unsigned long long t_start=nx_cpu_timing?nx_cpu_us():0,t_damage=0,t_raster=0;
    if (s->present_failed) {nk_clear(&xlib.ctx);return;}
    nx_cpu_damage(s,clear);
    t_damage=nx_cpu_timing?nx_cpu_us():0;
    if(s->damage_count<=0) {
        s->skipped_uploads++;
        if(nx_cpu_timing){s->last_damage_us=t_damage-t_start;s->last_raster_us=0;
            s->last_upload_us=0;s->last_raster_pixels=0;s->last_upload_pixels=0;
            s->last_rotate_us=s->last_put_us=0;}
        nk_clear(&xlib.ctx);return;
    }
    s->rotated_ready=0;
    uint32_t background=0xff000000u|((uint32_t)clear.r<<16)|((uint32_t)clear.g<<8)|clear.b;
    int covered_by_first=0;
    nk_foreach(cmd,&xlib.ctx) {
        if(cmd->type==NK_COMMAND_NOP)continue;
        if(cmd->type==NK_COMMAND_SCISSOR){
            const struct nk_command_scissor *sc=(const void *)cmd;
            if(sc->x<=0&&sc->y<=0&&sc->x+sc->w>=(int)s->w&&sc->y+sc->h>=(int)s->h)continue;
            break;
        }
        if(cmd->type==NK_COMMAND_RECT_FILLED){
            const struct nk_command_rect_filled *rc=(const void *)cmd;
            if(rc->color.a==255&&rc->rounding==0&&rc->x<=0&&rc->y<=0&&
               rc->x+rc->w>=(int)s->w&&rc->y+rc->h>=(int)s->h)covered_by_first=1;
        }
        break;
    }
    s->last_raster_pixels=0;
    for(int k=0;k<s->damage_count;k++) {
        struct nx_cpu_box dmg=s->damage_rects[k];
        if(!covered_by_first) {
            for(int y=dmg.top;y<dmg.bottom;y++) {
                uint32_t *row=s->pixels+(size_t)y*s->w;
                for(int x=dmg.left;x<dmg.right;x++) row[x]=background;
            }
        }
        s->last_raster_pixels+=(unsigned long long)(dmg.right-dmg.left)*(unsigned long long)(dmg.bottom-dmg.top);
    }
    s->raster_pixels+=s->last_raster_pixels;
    for(int k=0;k<s->damage_count;k++) {
        struct nx_cpu_box dmg=s->damage_rects[k];
        s->left=dmg.left;s->top=dmg.top;s->right=dmg.right;s->bottom=dmg.bottom;
        nk_foreach(cmd,&xlib.ctx) switch(cmd->type) {
        case NK_COMMAND_NOP: break;
        case NK_COMMAND_SCISSOR: {
            const struct nk_command_scissor *c=(const void *)cmd;
            s->left=NK_MAX(c->x,dmg.left);s->top=NK_MAX(c->y,dmg.top);
            s->right=NK_MIN(c->x+c->w,dmg.right);s->bottom=NK_MIN(c->y+c->h,dmg.bottom);
        } break;
        case NK_COMMAND_RECT_FILLED: {
            const struct nk_command_rect_filled *c=(const void *)cmd;
            if(c->x>=s->right||c->x+c->w<=s->left||c->y>=s->bottom||c->y+c->h<=s->top) break;
            nx_cpu_rect(s,c->x,c->y,c->w,c->h,c->rounding,c->color);
        } break;
        case NK_COMMAND_RECT: {
            const struct nk_command_rect *c=(const void *)cmd;
            if(c->x>=s->right||c->x+c->w<=s->left||c->y>=s->bottom||c->y+c->h<=s->top) break;
            int n=c->line_thickness;
            nx_cpu_rect(s,c->x,c->y,c->w,n,0,c->color);nx_cpu_rect(s,c->x,c->y+c->h-n,c->w,n,0,c->color);
            nx_cpu_rect(s,c->x,c->y,n,c->h,0,c->color);nx_cpu_rect(s,c->x+c->w-n,c->y,n,c->h,0,c->color);
        } break;
        case NK_COMMAND_LINE: {
            const struct nk_command_line *c=(const void *)cmd;
            nx_cpu_line(s,c->begin.x,c->begin.y,c->end.x,c->end.y,c->line_thickness,c->color);
        } break;
        case NK_COMMAND_TRIANGLE_FILLED: nx_cpu_triangle(s,(const void *)cmd);break;
        case NK_COMMAND_TEXT: nx_cpu_text(s,(const void *)cmd);break;
        case NK_COMMAND_IMAGE: {
            const struct nk_command_image *c=(const void *)cmd;
            const struct nx_cpu_image *im=c->img.handle.ptr;
            if(!im||!im->pixels||!im->w||!im->h||im->w>800||im->h>800)break;
            int left=NK_MAX(c->x,s->left),right=NK_MIN(c->x+c->w,s->right);
            int top=NK_MAX(c->y,s->top),bottom=NK_MIN(c->y+c->h,s->bottom);
            if(left>=right||top>=bottom)break;
            /* Keep the same nearest-neighbour pixels. Divide once per column and
             * row, rather than twice for every output pixel on the ARM CPU. */
            unsigned source_x[800];
            for(int x=left;x<right;x++)source_x[x-left]=(unsigned)(x-c->x)*im->w/c->w;
            for(int y=top;y<bottom;y++) {
                const uint32_t *row=im->pixels+(size_t)((unsigned)(y-c->y)*im->h/c->h)*im->w;
                uint32_t *out=s->pixels+(size_t)y*s->w+left;
                for(int x=0;x<right-left;x++)out[x]=row[source_x[x]];
            }
        } break;
        default:
            fprintf(stderr,"PANEL_PRESENT unsupported_command=%d\n",cmd->type);s->present_failed=1;break;
        }
    }
    nk_clear(&xlib.ctx);
    t_raster=nx_cpu_timing?nx_cpu_us():0;
    nx_cpu_upload(target,s,s->w,s->h,1);
    if(nx_cpu_timing){
        s->last_damage_us=t_damage-t_start;s->last_raster_us=t_raster-t_damage;
        s->last_upload_us=nx_cpu_us()-t_raster;
    }
}
static void nk_xlib_handle_event(Display *dpy, int screen, Window window, XEvent *event) {
    struct nk_context *ctx=&xlib.ctx;(void)dpy;(void)screen;(void)window;
    if (event->type==MappingNotify) XRefreshKeyboardMapping(&event->xmapping);
    if (event->type==ButtonPress || event->type==ButtonRelease) {
        int down=event->type==ButtonPress;
        if (event->xbutton.button==1) nk_input_button(ctx,NK_BUTTON_LEFT,event->xbutton.x,event->xbutton.y,down);
        else if (down && (event->xbutton.button==4 || event->xbutton.button==5))
            nk_input_scroll(ctx,nk_vec2(0,event->xbutton.button==4?1:-1));
    }
}
static void nk_xlib_shutdown(void) {
    XSurface *s=xlib.surf;
    if (s) {nk_xsurf_free_image(s);free(s->previous.data);free(s->current.data);XFreeGC(s->dpy,s->gc);free(s);}
    nk_free(&xlib.ctx);memset(&xlib,0,sizeof xlib);
}
#endif
