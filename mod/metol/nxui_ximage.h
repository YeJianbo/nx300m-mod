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
#include <sys/stat.h>
#include <unistd.h>

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
    /* Four-way bounded cache, shared by measurement and rasterization. */
    unsigned clock;
    struct nx_cpu_glyph glyphs[128][4];
} XFont;
/* Faces and glyph caches keep their own sizes; the UI thread shares only the
 * FreeType library, which stays alive until the last font is released. */
static FT_Library nx_cpu_library;
static unsigned nx_cpu_library_users;
struct nx_cpu_box { int left,top,right,bottom; };
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
    int rotated_ready, image_valid, force_full;
    unsigned full_uploads, partial_uploads, skipped_uploads;
    unsigned long long uploaded_pixels;
    unsigned long long raster_pixels;
    struct nx_cpu_frame previous,current;
    struct nx_cpu_box damage;
    uint32_t clear_color;
    uint32_t *pixels;
    XImage *image;
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
    struct nx_cpu_glyph *set=font->glyphs[(unsigned)rune%128],*slot=&set[0];
    for(unsigned i=0;i<4;i++) {
        if(set[i].valid&&set[i].rune==rune){set[i].used=++font->clock;return &set[i];}
        if(!set[i].valid||(slot->valid&&set[i].used<slot->used))slot=&set[i];
    }
    free(slot->coverage);memset(slot,0,sizeof *slot);
    slot->rune=rune;slot->used=++font->clock;slot->valid=1;
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
        offset += nx_cpu_rune(text + offset, length - offset, &rune);
        advance += nx_cpu_get_glyph(font,rune)->advance;
    }
    return (float)advance;
}
static XFont *nx_cpu_font_sized(const char *path, unsigned pixels) {
    XFont *font = calloc(1, sizeof *font);
    if (!font) return NULL;
    /* FreeType's file stream seeks and reads for individual glyphs. A private
     * read-only mapping shares cached font pages across sizes and panel opens
     * without copying the complete font into each process. */
    int fd=open(path,O_RDONLY|O_CLOEXEC);
    if(fd<0){free(font);return NULL;}
    off_t bytes=lseek(fd,0,SEEK_END);
    if(bytes<=0||bytes>LONG_MAX){close(fd);free(font);return NULL;}
    font->mapping_size=(size_t)bytes;
    font->mapping=mmap(NULL,font->mapping_size,PROT_READ,MAP_PRIVATE,fd,0);
    close(fd);
    if(font->mapping==MAP_FAILED){free(font);return NULL;}
    if (!nx_cpu_library && FT_Init_FreeType(&nx_cpu_library)) {
        munmap(font->mapping,font->mapping_size);free(font);return NULL;
    }
    font->library=nx_cpu_library;
    ++nx_cpu_library_users;
    if (FT_New_Memory_Face(font->library, font->mapping, (FT_Long)bytes, 0, &font->ft) ||
        FT_Set_Pixel_Sizes(font->ft, 0, pixels)) {
        if (font->ft) FT_Done_Face(font->ft);
        munmap(font->mapping,font->mapping_size);
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
    for(unsigned i=0;i<128;i++)for(unsigned j=0;j<4;j++)free(font->glyphs[i][j].coverage);
    FT_Done_Face(font->ft);
    munmap(font->mapping,font->mapping_size);
    if (!--nx_cpu_library_users) { FT_Done_FreeType(nx_cpu_library);nx_cpu_library=NULL; }
    free(font);
}
static void nk_xsurf_resize(XSurface *s, unsigned int w, unsigned int h) {
    if (s->w == w && s->h == h && s->image) return;
    if (!w || !h || w > 800 || h > 800) { s->present_failed = 1; return; }
    if (s->image) XDestroyImage(s->image);
    free(s->pixels); s->pixels = NULL; s->image = NULL;
    s->w = w; s->h = h; s->rotated_ready = s->image_valid = 0;
    s->pixels = calloc((size_t)w * h, sizeof *s->pixels);
    s->image = XCreateImage(s->dpy, DefaultVisual(s->dpy, s->screen),
        DefaultDepth(s->dpy, s->screen), ZPixmap, 0, NULL, h, w, 32, 0);
    if (!s->pixels || !s->image) { s->present_failed = 1; return; }
    /* Verified camera layout; reject other storage instead of uploading a
     * misencoded frame. Xvfb also uses this format in the ARM checks. */
    if (s->image->bits_per_pixel != 32 || s->image->byte_order != LSBFirst ||
        s->image->red_mask != 0xff0000 || s->image->green_mask != 0xff00 || s->image->blue_mask != 0xff ||
        s->image->bytes_per_line < (int)h * 4) { s->present_failed = 1; return; }
    s->image->data = calloc(w, (size_t)s->image->bytes_per_line);
    if (!s->image->data) s->present_failed = 1;
    s->left = s->top = 0; s->right = w; s->bottom = h;
}
static void nx_cpu_pixel(XSurface *s, int x, int y, struct nk_color c, unsigned coverage) {
    if (x < s->left || y < s->top || x >= s->right || y >= s->bottom) return;
    uint32_t *pixel = s->pixels + (size_t)y * s->w + x;
    unsigned a = (coverage * c.a + 127) / 255, inv = 255 - a;
    if (!a) return;
    if (a == 255) { *pixel = 0xff000000u | ((uint32_t)c.r<<16) | ((uint32_t)c.g<<8) | c.b; return; }
    unsigned r = (c.r*a + ((*pixel>>16)&255)*inv + 127) / 255;
    unsigned g = (c.g*a + ((*pixel>>8)&255)*inv + 127) / 255;
    unsigned b = (c.b*a + (*pixel&255)*inv + 127) / 255;
    *pixel = 0xff000000u | (r<<16) | (g<<8) | b;
}
static void nx_cpu_rect(XSurface *s, int x, int y, int w, int h, int round, struct nk_color c) {
    int r = NK_MIN(round, NK_MIN(w/2, h/2));
    if (!c.a) return;
    for (int j=NK_MAX(y,s->top); j<NK_MIN(y+h,s->bottom); j++) {
        int left=NK_MAX(x,s->left), right=NK_MIN(x+w,s->right);
        int dy = j < y+r ? y+r-1-j : (j >= y+h-r ? j-(y+h-r) : 0);
        /* The interior and straight edges cover almost the entire menu.
         * Clip once per row and blend only its translucent/rounded pixels. */
        if (c.a == 255 && (!r || !dy)) {
            uint32_t color=0xff000000u|((uint32_t)c.r<<16)|((uint32_t)c.g<<8)|c.b;
            for (int i=left; i<right; i++) s->pixels[(size_t)j*s->w+i]=color;
            continue;
        }
        for (int i=left; i<right; i++) {
            int dx = i < x+r ? x+r-1-i : (i >= x+w-r ? i-(x+w-r) : 0);
            if (!r || dx*dx+dy*dy < r*r) nx_cpu_pixel(s,i,j,c,255);
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
    while (offset<t->length) {
        offset+=nx_cpu_rune(t->string+offset,t->length-offset,&rune);
        struct nx_cpu_glyph *glyph=nx_cpu_get_glyph(font,rune);
        int gx=pen+glyph->left,gy=t->y+font->ascent-glyph->top;
        if(glyph->coverage)for(int y=NK_MAX(s->top-gy,0);y<NK_MIN(s->bottom-gy,(int)glyph->h);y++)
            for(int x=NK_MAX(s->left-gx,0);x<NK_MIN(s->right-gx,(int)glyph->w);x++) {
                unsigned cover=glyph->coverage[(size_t)y*glyph->w+x];
                if(cover)nx_cpu_pixel(s,gx+x,gy+y,t->foreground,cover);
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
/* Keep owned copies: Nuklear reuses its command storage after nk_clear().
 * Compare command payloads and effective clips exactly, without pixel hashes.
 * Repainting the union through every intersecting command preserves overlaps,
 * removed objects and translucent edges in the original painter order. */
static struct nx_cpu_box nx_cpu_damage(XSurface *s,struct nk_color clear) {
    const struct nk_command *cmd;
    struct nx_cpu_frame *frame=&s->current,*old=&s->previous;
    struct nx_cpu_box full={0,0,(int)s->w,(int)s->h},clip=full;
    struct nx_cpu_box damage={(int)s->w,(int)s->h,0,0};
    size_t previous=0;frame->size=0;
    uint32_t color=0xff000000u|((uint32_t)clear.r<<16)|((uint32_t)clear.g<<8)|clear.b;
    int complete=!s->image_valid||s->force_full||s->clear_color!=color;
    s->clear_color=color;
    nk_foreach(cmd,&xlib.ctx) {
        struct nx_cpu_record record;memset(&record,0,sizeof record);
        record.type=cmd->type;record.clip=clip;
        size_t bytes=0;struct nx_cpu_box bounds=full;
        switch(cmd->type) {
        case NK_COMMAND_NOP:continue;
        case NK_COMMAND_SCISSOR: {
            const struct nk_command_scissor *c=(const void *)cmd;
            clip=nx_cpu_intersect(full,(struct nx_cpu_box){c->x,c->y,c->x+c->w,c->y+c->h});continue;
        }
        case NK_COMMAND_RECT_FILLED: {
            const struct nk_command_rect_filled *c=(const void *)cmd;bytes=sizeof *c;
            bounds=(struct nx_cpu_box){c->x,c->y,c->x+c->w,c->y+c->h};
        } break;
        case NK_COMMAND_RECT: {
            const struct nk_command_rect *c=(const void *)cmd;bytes=sizeof *c;
            bounds=(struct nx_cpu_box){c->x,c->y,c->x+c->w,c->y+c->h};
        } break;
        case NK_COMMAND_LINE: {
            const struct nk_command_line *c=(const void *)cmd;bytes=sizeof *c;
            int pad=NK_MAX(c->line_thickness,1);
            bounds=(struct nx_cpu_box){NK_MIN(c->begin.x,c->end.x)-pad,NK_MIN(c->begin.y,c->end.y)-pad,
                                      NK_MAX(c->begin.x,c->end.x)+pad+1,NK_MAX(c->begin.y,c->end.y)+pad+1};
        } break;
        case NK_COMMAND_TRIANGLE_FILLED: {
            const struct nk_command_triangle_filled *c=(const void *)cmd;bytes=sizeof *c;
            bounds=(struct nx_cpu_box){NK_MIN(c->a.x,NK_MIN(c->b.x,c->c.x)),NK_MIN(c->a.y,NK_MIN(c->b.y,c->c.y)),
                                      NK_MAX(c->a.x,NK_MAX(c->b.x,c->c.x))+1,NK_MAX(c->a.y,NK_MAX(c->b.y,c->c.y))+1};
        } break;
        case NK_COMMAND_TEXT: {
            const struct nk_command_text *c=(const void *)cmd;
            bytes=offsetof(struct nk_command_text,string)+(size_t)c->length+1;
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
            const struct nk_command_image *c=(const void *)cmd;bytes=sizeof *c;
            const struct nx_cpu_image *image=c->img.handle.ptr;
            bounds=(struct nx_cpu_box){c->x,c->y,c->x+c->w,c->y+c->h};
            if(image)record.revision=image->revision;
        } break;
        default:complete=1;continue;
        }
        record.bounds=nx_cpu_intersect(bounds,clip);
        record.bytes=(unsigned)(bytes-sizeof *cmd);
        record.span=(unsigned)((sizeof record+record.bytes+7)&~(size_t)7);
        if(frame->size+record.span>frame->capacity) {
            size_t capacity=(frame->size+record.span+16383)&~(size_t)16383;
            unsigned char *data=realloc(frame->data,capacity);
            if(!data){old->size=0;frame->size=0;return full;}
            frame->data=data;frame->capacity=capacity;
        }
        unsigned char *data=frame->data+frame->size;
        memcpy(data,&record,sizeof record);memcpy(data+sizeof record,cmd+1,record.bytes);
        const struct nx_cpu_record *before=previous<old->size?(const void *)(old->data+previous):NULL;
        if(!before||before->bytes!=record.bytes||memcmp(before,&record,sizeof record)||
           memcmp((const unsigned char *)before+sizeof record,cmd+1,record.bytes)) {
            nx_cpu_damage_add(&damage,record.bounds);
            if(before)nx_cpu_damage_add(&damage,before->bounds);
        }
        if(before)previous+=before->span;
        frame->size+=record.span;
    }
    while(previous<old->size) {
        const struct nx_cpu_record *before=(const void *)(old->data+previous);
        nx_cpu_damage_add(&damage,before->bounds);previous+=before->span;
    }
    struct nx_cpu_frame swap=*old;*old=*frame;*frame=swap;
    return complete?full:damage;
}
static void nx_cpu_upload(Drawable target, XSurface *s, unsigned int w, unsigned int h, int changes_only) {
    if (s->present_failed) return;
    if (w!=s->w || h!=s->h) {s->present_failed=1;return;}
    int left=0,top=0,right=(int)h,bottom=(int)w;
    int partial=changes_only&&s->image_valid&&!s->force_full;
    if (!s->rotated_ready) {
        /* A tiled transpose keeps both buffers local on the camera's CPU. */
        if(partial){left=(int)h;top=(int)w;right=bottom=0;}
        unsigned x0=partial?(unsigned)s->damage.left:0,y0=partial?(unsigned)s->damage.top:0;
        unsigned x1=partial?(unsigned)s->damage.right:w,y1=partial?(unsigned)s->damage.bottom:h;
        for (unsigned by=y0;by<y1;by+=16) for (unsigned bx=x0;bx<x1;bx+=16)
            for (unsigned y=by;y<NK_MIN(by+16,y1);y++) for (unsigned x=bx;x<NK_MIN(bx+16,x1);x++) {
                uint32_t value=s->pixels[(size_t)y*w+x];
                uint32_t *destination=(uint32_t *)(s->image->data+(size_t)x*s->image->bytes_per_line)+h-1-y;
                if(partial) {
                    if(*destination==value)continue;
                    int rx=(int)(h-1-y),ry=(int)x;
                    if(rx<left)left=rx;
                    if(ry<top)top=ry;
                    if(rx+1>right)right=rx+1;
                    if(ry+1>bottom)bottom=ry+1;
                }
                *destination=value;
            }
        s->rotated_ready=1;
    }
    s->image_valid=1;s->force_full=0;
    if(right<=left||bottom<=top){s->skipped_uploads++;return;}
    if(left||top||right!=(int)h||bottom!=(int)w)s->partial_uploads++;else s->full_uploads++;
    s->uploaded_pixels+=(unsigned)(right-left)*(unsigned)(bottom-top);
    XPutImage(s->dpy,target,s->gc,s->image,left,top,left,top,(unsigned)(right-left),(unsigned)(bottom-top));
    if (!s->presents++) fprintf(stderr,"PANEL_PRESENT mode=cpu-ximage raw=%ux%u alpha=255\n",h,w);
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
    if (s->present_failed) {nk_clear(&xlib.ctx);return;}
    s->damage=nx_cpu_damage(s,clear);
    if(s->damage.right<=s->damage.left||s->damage.bottom<=s->damage.top) {
        s->skipped_uploads++;nk_clear(&xlib.ctx);return;
    }
    s->rotated_ready=0;
    uint32_t background=0xff000000u|((uint32_t)clear.r<<16)|((uint32_t)clear.g<<8)|clear.b;
    for(int y=s->damage.top;y<s->damage.bottom;y++)for(int x=s->damage.left;x<s->damage.right;x++)
        s->pixels[(size_t)y*s->w+x]=background;
    s->raster_pixels+=(unsigned)(s->damage.right-s->damage.left)*(unsigned)(s->damage.bottom-s->damage.top);
    s->left=s->damage.left;s->top=s->damage.top;s->right=s->damage.right;s->bottom=s->damage.bottom;
    nk_foreach(cmd,&xlib.ctx) switch(cmd->type) {
    case NK_COMMAND_NOP: break;
    case NK_COMMAND_SCISSOR: {
        const struct nk_command_scissor *c=(const void *)cmd;
        s->left=NK_MAX(c->x,s->damage.left);s->top=NK_MAX(c->y,s->damage.top);
        s->right=NK_MIN(c->x+c->w,s->damage.right);s->bottom=NK_MIN(c->y+c->h,s->damage.bottom);
    } break;
    case NK_COMMAND_RECT_FILLED: {
        const struct nk_command_rect_filled *c=(const void *)cmd;
        nx_cpu_rect(s,c->x,c->y,c->w,c->h,c->rounding,c->color);
    } break;
    case NK_COMMAND_RECT: {
        const struct nk_command_rect *c=(const void *)cmd;int n=c->line_thickness;
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
    nk_clear(&xlib.ctx);nx_cpu_upload(target,s,s->w,s->h,1);
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
    if (s) {if(s->image)XDestroyImage(s->image);free(s->pixels);free(s->previous.data);free(s->current.data);XFreeGC(s->dpy,s->gc);free(s);}
    nk_free(&xlib.ctx);memset(&xlib,0,sizeof xlib);
}
#endif
