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

typedef struct XFont {
    FT_Library library;
    FT_Face ft;
    int ascent, height;
    struct nk_user_font handle;
} XFont;
typedef struct XSurface {
    Display *dpy;
    int screen, present_failed;
    GC gc;
    unsigned int w, h, presents;
    int rotated_ready;
    uint32_t *pixels;
    XImage *image;
    int left, top, right, bottom;
} XSurface;
static struct { Display *dpy; struct nk_context ctx; XSurface *surf; } xlib;

static int nx_cpu_rune(const char *text, int size, nk_rune *rune) {
    int length = nk_utf_decode(text, rune, size);
    if (length <= 0) { *rune = 0xfffd; return 1; }
    return length;
}
static float nx_cpu_text_width(nk_handle handle, float height, const char *text, int length) {
    XFont *font = handle.ptr; int advance = 0, offset = 0; nk_rune rune;
    (void)height;
    while (text && offset < length) {
        offset += nx_cpu_rune(text + offset, length - offset, &rune);
        if (!FT_Load_Char(font->ft, rune, FT_LOAD_DEFAULT)) advance += font->ft->glyph->advance.x >> 6;
    }
    return (float)advance;
}
static XFont *nx_cpu_font(const char *path) {
    XFont *font = calloc(1, sizeof *font);
    if (!font) return NULL;
    if (FT_Init_FreeType(&font->library) || FT_New_Face(font->library, path, 0, &font->ft) ||
        FT_Set_Pixel_Sizes(font->ft, 0, 19)) {
        if (font->ft) FT_Done_Face(font->ft);
        if (font->library) FT_Done_FreeType(font->library);
        free(font); return NULL;
    }
    font->ascent = (font->ft->size->metrics.ascender + 63) >> 6;
    font->height = (font->ft->size->metrics.height + 63) >> 6;
    font->handle.userdata = nk_handle_ptr(font);
    font->handle.height = (float)font->height;
    font->handle.width = nx_cpu_text_width;
    return font;
}
static XFont *nk_xfont_create(Display *dpy, const char *name) {
    (void)dpy; (void)name;
    return nx_cpu_font("/usr/share/fonts/SDIC_GP_US_20120720.ttf");
}
static void nk_xfont_del(Display *dpy, XFont *font) {
    (void)dpy;
    if (!font) return;
    FT_Done_Face(font->ft); FT_Done_FreeType(font->library); free(font);
}
static void nk_xsurf_resize(XSurface *s, unsigned int w, unsigned int h) {
    if (s->w == w && s->h == h && s->image) return;
    if (!w || !h || w > 800 || h > 800) { s->present_failed = 1; return; }
    if (s->image) XDestroyImage(s->image);
    free(s->pixels); s->pixels = NULL; s->image = NULL;
    s->w = w; s->h = h; s->rotated_ready = 0;
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
    unsigned r = (c.r*a + ((*pixel>>16)&255)*inv + 127) / 255;
    unsigned g = (c.g*a + ((*pixel>>8)&255)*inv + 127) / 255;
    unsigned b = (c.b*a + (*pixel&255)*inv + 127) / 255;
    *pixel = 0xff000000u | (r<<16) | (g<<8) | b;
}
static void nx_cpu_rect(XSurface *s, int x, int y, int w, int h, int round, struct nk_color c) {
    int r = NK_MIN(round, NK_MIN(w/2, h/2));
    for (int j=NK_MAX(y,s->top); j<NK_MIN(y+h,s->bottom); j++)
        for (int i=NK_MAX(x,s->left); i<NK_MIN(x+w,s->right); i++) {
            int dx = i < x+r ? x+r-1-i : (i >= x+w-r ? i-(x+w-r) : 0);
            int dy = j < y+r ? y+r-1-j : (j >= y+h-r ? j-(y+h-r) : 0);
            if (!r || dx*dx+dy*dy < r*r) nx_cpu_pixel(s,i,j,c,255);
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
        if (FT_Load_Char(font->ft,rune,FT_LOAD_RENDER|FT_LOAD_TARGET_NORMAL)) continue;
        FT_GlyphSlot glyph=font->ft->glyph; FT_Bitmap *bitmap=&glyph->bitmap;
        for (unsigned y=0;y<bitmap->rows;y++) for (unsigned x=0;x<bitmap->width;x++) {
            const unsigned char *row=bitmap->buffer+(bitmap->pitch>=0?y:bitmap->rows-1-y)*abs(bitmap->pitch);
            unsigned cover=bitmap->pixel_mode==FT_PIXEL_MODE_MONO ? ((row[x>>3]&(0x80>>(x&7)))?255:0) : row[x];
            nx_cpu_pixel(s,pen+glyph->bitmap_left+x,t->y+font->ascent-glyph->bitmap_top+y,t->foreground,cover);
        }
        pen+=glyph->advance.x>>6;
    }
}
static void nk_xsurf_blit(Drawable target, XSurface *s, unsigned int w, unsigned int h) {
    if (s->present_failed) return;
    if (w!=s->w || h!=s->h) {s->present_failed=1;return;}
    if (!s->rotated_ready) {
        for (unsigned y=0;y<h;y++) for (unsigned x=0;x<w;x++)
            ((uint32_t *)(s->image->data+(size_t)x*s->image->bytes_per_line))[h-1-y]=s->pixels[(size_t)y*w+x];
        s->rotated_ready=1;
    }
    XPutImage(s->dpy,target,s->gc,s->image,0,0,0,0,h,w);
    if (!s->presents++) fprintf(stderr,"PANEL_PRESENT mode=cpu-ximage raw=%ux%u alpha=255\n",h,w);
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
    s->rotated_ready=0;
    uint32_t background=0xff000000u|((uint32_t)clear.r<<16)|((uint32_t)clear.g<<8)|clear.b;
    for (size_t i=0;i<(size_t)s->w*s->h;i++) s->pixels[i]=background;
    s->left=s->top=0;s->right=s->w;s->bottom=s->h;
    nk_foreach(cmd,&xlib.ctx) switch(cmd->type) {
    case NK_COMMAND_NOP: break;
    case NK_COMMAND_SCISSOR: {
        const struct nk_command_scissor *c=(const void *)cmd;
        s->left=NK_MAX(c->x,0);s->top=NK_MAX(c->y,0);
        s->right=NK_MIN(c->x+c->w,(int)s->w);s->bottom=NK_MIN(c->y+c->h,(int)s->h);
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
    default:
        fprintf(stderr,"PANEL_PRESENT unsupported_command=%d\n",cmd->type);s->present_failed=1;break;
    }
    nk_clear(&xlib.ctx);nk_xsurf_blit(target,s,s->w,s->h);
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
    if (s) {if(s->image)XDestroyImage(s->image);free(s->pixels);XFreeGC(s->dpy,s->gc);free(s);}
    nk_free(&xlib.ctx);memset(&xlib,0,sizeof xlib);
}
#endif
