/* NX300M remote backend, AGPL-3.0.
 * Uses mewlips/nx-remote-controller-mod's model/status code and OSD wire format,
 * with Metol's web and NXConnect Android clients. The stock LCD video plane
 * and X11 supply screen pixels; XTest supplies the original application's input.
 */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include <arpa/inet.h>
#include <ctype.h>
#include <dirent.h>
#include <limits.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include <sys/resource.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <fcntl.h>
#include <errno.h>
#include <time.h>
#include <unistd.h>
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include "mongoose.h"
#include "nx_model.h"
#include "status.h"

extern Bool XTestFakeKeyEvent(Display *, unsigned int, Bool, unsigned long);
extern Bool XTestFakeButtonEvent(Display *, unsigned int, Bool, unsigned long);
extern Bool XTestFakeMotionEvent(Display *, int, int, int, unsigned long);
static Display *display;
static const char *media_root;
static int media_paused;
static struct mg_serve_http_opts assets;
static volatile sig_atomic_t stopping;
static int trace_http;
static unsigned int trace_http_events;
static volatile unsigned char *held_keys, *held_buttons;
static long long input_alive;
static void close_inherited_fds(void) {
    /* MFW launches from the stock app, whose device and pipe descriptors are
     * not all CLOEXEC. Release only our inherited copies before opening X11. */
    DIR *fds=opendir("/proc/self/fd");
    if (!fds) return;
    int scan_fd=dirfd(fds);
    struct dirent *entry;
    while ((entry=readdir(fds))) {
        char *end;long fd=strtol(entry->d_name,&end,10);
        if (*entry->d_name && !*end && fd>2 && fd<=INT_MAX && fd!=scan_fd) close((int)fd);
    }
    closedir(fds);
}
static long long now_ms(void) {
    struct timespec value; clock_gettime(CLOCK_MONOTONIC, &value);
    return (long long)value.tv_sec*1000+value.tv_nsec/1000000;
}
static int publish_ready(void) {
    const char *path=getenv("NX_REMOTE_READY_FILE");
    if (!path || !*path) return 0;
    char temporary[PATH_MAX];
    if (*path!='/' || snprintf(temporary,sizeof temporary,"%s.XXXXXX",path)>=(int)sizeof temporary) {
        errno=EINVAL;return -1;
    }
    int fd=mkstemp(temporary);
    if (fd<0) return -1;
    const char ready[]="ready\n";
    size_t offset=0;
    while (offset<sizeof ready-1) {
        ssize_t count=write(fd,ready+offset,sizeof ready-1-offset);
        if (count<0 && errno==EINTR) continue;
        if (count<=0) {
            int error=count<0?errno:EIO;close(fd);unlink(temporary);errno=error;return -1;
        }
        offset+=(size_t)count;
    }
    if (close(fd)<0 || rename(temporary,path)<0) {
        int error=errno;unlink(temporary);errno=error;return -1;
    }
    return 0;
}
static void stop(int signal_number) { (void)signal_number; stopping = 1; }
static void reply(struct mg_connection *c, int code, const char *json) {
    mg_send_head(c, code, strlen(json), "Content-Type: application/json; charset=utf-8\r\nCache-Control: no-store");
    mg_send(c, json, strlen(json));
}
static void release_inputs_x11(void) {
    int dirty=0;
    for (int i=1; i<256; i++) if (held_keys[i]) { dirty=1;XTestFakeKeyEvent(display, i, False, 0); }
    for (int i=1; i<8; i++) if (held_buttons[i]) { dirty=1;XTestFakeButtonEvent(display, i, False, 0); }
    if (!dirty) return;
    /* A dead X connection or killed helper must leave the recovery ledger
     * intact until the server has acknowledged every release. */
    XSync(display,False);
    memset((void *)held_keys,0,256);memset((void *)held_buttons,0,8);
}
static void focus_input(void) {
    Window focus, root = DefaultRootWindow(display); int revert;
    XGetInputFocus(display, &focus, &revert);
    XWindowAttributes a;
    if (focus != None && focus != PointerRoot && focus != root && XGetWindowAttributes(display, focus, &a) && a.map_state == IsViewable) return;
    Window parent, returned_root, *children = NULL, chosen = None; unsigned int count;
    unsigned long area=0;
    if (XQueryTree(display, root, &returned_root, &parent, &children, &count)) {
        for (unsigned int i=0; i<count; i++) {
            if (XGetWindowAttributes(display, children[i], &a) && a.map_state == IsViewable && (unsigned long)a.width*a.height>area) {
                area=(unsigned long)a.width*a.height; chosen=children[i];
            }
        }
        if (children) XFree(children);
    }
    if (chosen) XSetInputFocus(display, chosen, RevertToPointerRoot, CurrentTime);
}
static void refresh_input_mapping(void) {
    XEvent event;
    while (XCheckTypedEvent(display,MappingNotify,&event)) XRefreshKeyboardMapping(&event.xmapping);
}
static int inject_x11(const char *query) {
    struct mg_str query_string=mg_mk_str(query);
    char value[80]; const char *actions[]={"key","keydown","keyup"};
    refresh_input_mapping();focus_input();
    for (int action=0; action<3; action++) {
        int length=mg_get_http_var(&query_string, actions[action], value, sizeof value);
        if (length<0) continue;
        if (!length) return 400;
        for (int i=0; i<length; i++) if (!isalnum((unsigned char)value[i]) && value[i]!='_') return 400;
        KeySym symbol=XStringToKeysym(value);
        if (symbol==NoSymbol) return 400;
        KeyCode key=XKeysymToKeycode(display,symbol);
        /* Match the standalone simulator's spare-key binding, only when the
         * laboratory launcher explicitly enables it. Camera mappings stay put. */
        char bind[8];const char *lab=getenv("NX_REMOTE_LAB_INPUT");
        if (!key && action==0 && lab && !strcmp(lab,"1") && !held_keys[255] &&
            mg_get_http_var(&query_string,"bind",bind,sizeof bind)==1 && bind[0]=='1') {
            key=255;XChangeKeyboardMapping(display,key,1,&symbol,1);XSync(display,False);refresh_input_mapping();
        }
        if (!key) return 400;
        if (action!=2) { held_keys[key]=1;XTestFakeKeyEvent(display,key,True,0); }
        if (action!=1) XTestFakeKeyEvent(display,key,False,0);
        input_alive=now_ms(); XSync(display,False);
        if (action!=1) held_keys[key]=0;
        fprintf(stderr,"[NX REMOTE] input action=%s key=%s code=%u\n",actions[action],value,key);
        return 200;
    }
    if (mg_get_http_var(&query_string,"click",value,sizeof value)>=0) {
        int x,y;char extra;
        if (sscanf(value,"%d-%d%c",&x,&y,&extra)!=2 || x<0 || x>=800 || y<0 || y>=480) return 400;
        int w=DisplayWidth(display,DefaultScreen(display)),hgt=DisplayHeight(display,DefaultScreen(display));
        XTestFakeMotionEvent(display,-1,w==480 && hgt==800?479-y:x,w==480 && hgt==800?x:y,0);
        /* One queued command keeps motion/down/up together. Preserve the
         * standalone viewer's press/release delays and recovery ledger. */
        held_buttons[1]=1;
        XTestFakeButtonEvent(display,1,True,15);XTestFakeButtonEvent(display,1,False,50);
        input_alive=now_ms();XSync(display,False);held_buttons[1]=0;
        return 200;
    }
    int released_button=0;
    if (mg_get_http_var(&query_string,"mousemove",value,sizeof value)>=0) {
        int x,y;char extra;
        if (sscanf(value,"%d-%d%c",&x,&y,&extra)!=2 || x<0 || x>=800 || y<0 || y>=480) return 400;
        int w=DisplayWidth(display,DefaultScreen(display)), hgt=DisplayHeight(display,DefaultScreen(display));
        if (w==480 && hgt==800) XTestFakeMotionEvent(display,-1,479-y,x,0);
        else XTestFakeMotionEvent(display,-1,x,y,0);
    } else {
        const char *buttons[]={"mousedown","mouseup"};
        int found=0;
        for (int action=0; action<2; action++) if (mg_get_http_var(&query_string,buttons[action],value,sizeof value)>=0) {
            int button;char extra;
            if (sscanf(value,"%d%c",&button,&extra)!=1 || button<1 || button>7) return 400;
            if (!action) held_buttons[button]=1;
            else released_button=button;
            XTestFakeButtonEvent(display,button,action==0,0);found=1;break;
        }
        if (!found) return 400;
    }
    input_alive=now_ms();XSync(display,False);
    if (released_button) held_buttons[released_button]=0;
    return 200;
}

enum { WIDTH=800, HEIGHT=480, BW=80, BH=40, BLOCKS=WIDTH*HEIGHT/BW/BH };
struct block { uint16_t x,y; uint32_t hash; unsigned char pixels[BW*BH*4]; };
static struct block blocks[BLOCKS];
static uint32_t frame_hashes[BLOCKS];
static long long frame_time;
static int frame_ready;
#include "screen.h"
static int capture(void) {
    int width=DisplayWidth(display,DefaultScreen(display)), height=DisplayHeight(display,DefaultScreen(display));
    if (!((width==480 && height==800) || (width==800 && height==480))) return 0;
    if (!lcd_video_read(width,height)) return 0;
    opaque_windows(width,height);
    XImage *image=XGetImage(display,DefaultRootWindow(display),0,0,width,height,AllPlanes,ZPixmap);
    if (!image) return 0;
    if (image->bits_per_pixel!=32 || image->byte_order!=LSBFirst || image->red_mask!=0xff0000 || image->green_mask!=0xff00 || image->blue_mask!=0xff) { XDestroyImage(image);return 0; }
    for (int i=0; i<BLOCKS; i++) { blocks[i].x=htons((i%(WIDTH/BW))*BW);blocks[i].y=htons((i/(WIDTH/BW))*BH);frame_hashes[i]=0; }
    for (int y=0; y<HEIGHT; y++) for (int x=0; x<WIDTH; x++) {
        int sx=width==480?479-y:x,sy=width==480?x:y;
        unsigned char *pixel=(unsigned char *)image->data+sy*image->bytes_per_line+sx*4;
        int bi=(y/BH)*(WIDTH/BW)+x/BW,at=((y%BH)*BW+x%BW)*4;
        unsigned char *video=screen_video+(sy*width+sx)*3;
        int alpha=screen_opaque[sy*width+sx]?255:pixel[3];
        /* Evas RGB is premultiplied. Opaque black menus must cover video;
         * the unused alpha byte of Metol's RGB window is handled above. */
        unsigned char rgba[4]={clamp_rgb(pixel[2]+(video[0]*(255-alpha)+127)/255),
                               clamp_rgb(pixel[1]+(video[1]*(255-alpha)+127)/255),
                               clamp_rgb(pixel[0]+(video[2]*(255-alpha)+127)/255),255};
        for (int k=0;k<4;k++) { blocks[bi].pixels[at+k]=rgba[k];frame_hashes[bi]+=rgba[k]+x+y; }
    }
    XDestroyImage(image);
    for (int i=0; i<BLOCKS; i++) blocks[i].hash=htonl(frame_hashes[i]);
    frame_time=now_ms();frame_ready=1;return 1;
}
static void osd_reply(struct mg_connection *c,const uint32_t previous[BLOCKS]) {
    int count=0;for (int i=0;i<BLOCKS;i++) if (previous[i]!=frame_hashes[i]) count++;
    uint16_t header[4]={htons(WIDTH),htons(HEIGHT),htons(BW),htons(BH)};
    uint32_t end=0xffffffffU;
    mg_send_head(c,200,8+count*sizeof(struct block)+4,"Content-Type: text/plain; charset=x-user-defined\r\nCache-Control: no-store");
    mg_send(c,header,sizeof header);
    for (int i=0;i<BLOCKS;i++) if (previous[i]!=frame_hashes[i]) mg_send(c,&blocks[i],sizeof blocks[i]);
    mg_send(c,&end,sizeof end);
}
#include "x11_worker.h"

static void json_string(struct mg_connection *c,const char *value) {
    mg_printf_http_chunk(c,"\"");
    const unsigned char *p=(const unsigned char *)value,*run=p;
    for (;*p;p++) {
        if (*p!='"' && *p!='\\' && *p>=32) continue;
        if (p!=run) mg_send_http_chunk(c,(const char *)run,(size_t)(p-run));
        if (*p=='"' || *p=='\\') mg_printf_http_chunk(c,"\\%c",*p);
        else mg_printf_http_chunk(c,"\\u%04x",*p);
        run=p+1;
    }
    if (p!=run) mg_send_http_chunk(c,(const char *)run,(size_t)(p-run));
    mg_printf_http_chunk(c,"\"");
}
static int media_path_from_base(const char *base,const char *relative,char *result) {
    char candidate[PATH_MAX];
    if (*relative=='/' || strchr(relative,'\\')) return 0;
    int n=snprintf(candidate,sizeof candidate,"%s/%s",base,relative);
    if (n<0 || n>=(int)sizeof candidate || !realpath(candidate,result)) return 0;
    size_t size=strlen(base);
    return !strncmp(result,base,size) && (!result[size] || result[size]=='/');
}
static int media_path(const char *relative,char *result) {
    char base[PATH_MAX];
    return !media_paused && realpath(media_root,base) && media_path_from_base(base,relative,result);
}
static void dirlist(struct mg_connection *c,struct http_message *h) {
    char relative[1024]="",base[PATH_MAX],path[PATH_MAX];
    if (mg_get_http_var(&h->query_string,"p",relative,sizeof relative)<-1 || media_paused ||
        !realpath(media_root,base) || !media_path_from_base(base,relative,path)) { reply(c,404,"{\"ok\":false,\"err\":\"media directory unavailable\"}");return; }
    struct dirent **entries;int count=scandir(path,&entries,NULL,alphasort);
    if (count<0) { reply(c,404,"{\"ok\":false,\"err\":\"directory unavailable\"}");return; }
    struct directory_item { int kind;off_t size; };
    struct directory_item *items=calloc(count?count:1,sizeof *items);
    if (!items) {
        for (int i=0;i<count;i++) free(entries[i]);
        free(entries);reply(c,503,"{\"ok\":false,\"err\":\"directory memory unavailable\"}");return;
    }
    /* Resolve and stat each visible entry once. All three JSON sections use
     * this snapshot, preserving alphabetical order and symlink containment.
     * Ordinary files and subdirectories only need metadata: one lstat on the
     * already-verified parent path, no per-entry realpath() (a large
     * directory previously paid one full path resolution per entry). Only
     * symlinks keep the full containment check, because their target can
     * resolve anywhere, including outside the media root. */
    for (int i=0;i<count;i++) {
        if (entries[i]->d_name[0]=='.') continue;
        char joined[PATH_MAX],full[PATH_MAX];struct stat st;
        int n=snprintf(joined,sizeof joined,"%s%s%s",relative,*relative?"/":"",entries[i]->d_name);
        if (n<0 || n>=(int)sizeof joined) continue;
        if (snprintf(full,sizeof full,"%s/%s",path,entries[i]->d_name)>=(int)sizeof full) continue;
        if (!lstat(full,&st) && !S_ISLNK(st.st_mode)) {
            if (!strchr(entries[i]->d_name,'\\')) {
                items[i].kind=S_ISDIR(st.st_mode)?1:S_ISREG(st.st_mode)?2:0;
                items[i].size=st.st_size;
                continue;
            }
        }
        char child[PATH_MAX];
        if (media_path_from_base(base,joined,child) && !stat(child,&st)) {
            items[i].kind=S_ISDIR(st.st_mode)?1:S_ISREG(st.st_mode)?2:0;
            items[i].size=st.st_size;
        }
    }
    mg_printf(c,"HTTP/1.1 200 OK\r\nContent-Type: application/json; charset=utf-8\r\nTransfer-Encoding: chunked\r\nCache-Control: no-store\r\n\r\n");
    mg_printf_http_chunk(c,"{\"ok\":true,\"path\":");json_string(c,relative);
    for (int isdir=1;isdir>=0;isdir--) {
        mg_printf_http_chunk(c,isdir?",\"dirs\":[":",\"files\":[");int comma=0;
        for (int i=0;i<count;i++) {
            if (items[i].kind!=(isdir?1:2)) continue;
            if (comma++) mg_printf_http_chunk(c,",");
            json_string(c,entries[i]->d_name);
        }
        mg_printf_http_chunk(c,"]");
    }
    mg_printf_http_chunk(c,",\"sizes\":{");int comma=0;
    for (int i=0;i<count;i++) {
        if (items[i].kind!=2) continue;
        if (comma++) mg_printf_http_chunk(c,",");
        json_string(c,entries[i]->d_name);mg_printf_http_chunk(c,":%lld",(long long)items[i].size);
    }
    mg_printf_http_chunk(c,"}");
    for (int i=0;i<count;i++) free(entries[i]);
    free(entries);free(items);
    mg_printf_http_chunk(c,"}");mg_send_http_chunk(c,"",0);
}
static void serve_media_path(struct mg_connection *c,struct http_message *h,const char *path,const char *type) {
    struct mg_str mime=mg_mk_str(type);
    /* Mongoose keeps a FILE until transfer completion or connection teardown. */
    c->flags|=MG_F_USER_1|MG_F_USER_2;
    /* Its generated Connection header follows request metadata, not extra
     * response headers. Force both its bookkeeping and header to close. */
    struct http_message request=*h;
    request.proto=mg_mk_str("HTTP/1.0");
    struct mg_str *connection=mg_get_http_header(&request,"Connection");
    if (connection) *connection=mg_mk_str("close");
    mg_http_serve_file(c,&request,path,mime,mg_mk_str("Cache-Control: no-store"));
}
static void serve_media(struct mg_connection *c,struct http_message *h,const char *relative) {
    char path[PATH_MAX];struct stat st;
    if (!media_path(relative,path) || stat(path,&st) || !S_ISREG(st.st_mode)) { reply(c,404,"{\"error\":\"media file unavailable\"}");return; }
    const char *ext=strrchr(path,'.'),*type="application/octet-stream";
    if (ext) {
        if (!strcasecmp(ext,".jpg") || !strcasecmp(ext,".jpeg")) type="image/jpeg";
        else if (!strcasecmp(ext,".png")) type="image/png";
        else if (!strcasecmp(ext,".gif")) type="image/gif";
        else if (!strcasecmp(ext,".mp4")) type="video/mp4";
        else if (!strcasecmp(ext,".mov")) type="video/quicktime";
    }
    serve_media_path(c,h,path,type);
}
/* --- upload receive gating (2026-11-12) -----------------------------------
 * Upload bodies are received into the connection's own buffer before the
 * request event fires, so the per-connection receive limit below is also the
 * per-upload memory bound. The gate decides from the request header as soon
 * as that header is fully buffered: oversized uploads are refused before
 * their payload arrives, chunked uploads are refused (this first version
 * receives fixed-length bodies only), and at most one upload is buffered at
 * a time so several individually legal bodies cannot stack into an unbounded
 * total receive budget. The listener's recv_mbuf_limit bounds every other
 * receive path, including a stalled client that stops mid-body. */
#define NX_UPLOAD_MAX_BYTES (24U*1024U*1024U)
/* Receive headroom over the body limit: request line plus headers parse from
 * the same buffer (Mongoose's own request-header cap is 8 KiB). */
#define NX_RECV_LIMIT_BYTES (NX_UPLOAD_MAX_BYTES+(512U*1024U))
static int upload_sessions_active;
static void upload_release(struct mg_connection *c) {
    if (c->flags&MG_F_USER_3) {
        c->flags&=~MG_F_USER_3;
        upload_sessions_active--;
    }
}
static void upload_reject(struct mg_connection *c,int code,const char *message) {
    c->flags|=MG_F_USER_4;
    reply(c,code,message);
    c->flags|=MG_F_SEND_AND_CLOSE;
}
static int upload_uri(const struct mg_str *uri) {
    size_t n=sizeof "/api/v1/gallery/upload"-1;
    return uri->len>=n && !memcmp(uri->p,"/api/v1/gallery/upload",n) &&
        (uri->len==n || uri->p[n]=='/' || uri->p[n]=='?');
}
static int upload_parse_length(const char *value,size_t size,unsigned long long *length) {
    if (!size) return 0;
    unsigned long long result=0;
    for (size_t i=0;i<size;i++) {
        if (value[i]<'0' || value[i]>'9' || result>(ULLONG_MAX-(unsigned)(value[i]-'0'))/10) return 0;
        result=result*10+(unsigned)(value[i]-'0');
    }
    *length=result;return 1;
}
static void upload_gate(struct mg_connection *c) {
    /* A connection acquires its slot once, when the header first arrives.
     * Later body segments must neither reparse megabytes nor reject itself. */
    if (c->flags&(MG_F_USER_3|MG_F_USER_4)) return;
    struct mbuf *io=&c->recv_mbuf;
    const char *header_end=NULL;
    for (size_t i=0;i+3<io->len;i++) {
        if (!memcmp(io->buf+i,"\r\n\r\n",4)) {header_end=io->buf+i+2;break;}
    }
    if (!header_end) return;   /* header still arriving in segments */
    const char *line_end=memchr(io->buf,'\r',io->len);
    if (!line_end) return;
    const char *method_end=memchr(io->buf,' ',(size_t)(line_end-io->buf));
    if (!method_end) return;
    const char *target=method_end+1;
    const char *target_end=memchr(target,' ',(size_t)(line_end-target));
    if (!target_end) return;
    struct mg_str uri={target,(size_t)(target_end-target)};
    unsigned long long content_length=0;int have_length=0,have_encoding=0;
    for (const char *p=line_end+2;p<header_end;) {
        const char *end=memchr(p,'\r',(size_t)(header_end-p));
        if (!end) return;
        const char *colon=memchr(p,':',(size_t)(end-p));
        if (!colon) {upload_reject(c,400,"{\"ok\":false,\"err\":\"invalid header\"}");return;}
        const char *value=colon+1,*value_end=end;
        while (value<value_end && (*value==' '||*value=='\t')) value++;
        while (value_end>value && (value_end[-1]==' '||value_end[-1]=='\t')) value_end--;
        if (colon-p==14 && !strncasecmp(p,"content-length",14)) {
            if (have_length || !upload_parse_length(value,(size_t)(value_end-value),&content_length)) {
                fprintf(stderr,"NX_REMOTE_UPLOAD reject stage=bad-length active=%d\n",upload_sessions_active);
                upload_reject(c,400,"{\"ok\":false,\"err\":\"invalid Content-Length\"}");
                return;
            }
            have_length=1;
        }
        if (colon-p==17 && !strncasecmp(p,"transfer-encoding",17)) have_encoding=1;
        p=end+2;
    }
    if (have_length && content_length>NX_UPLOAD_MAX_BYTES) {
        fprintf(stderr,"NX_REMOTE_UPLOAD reject stage=oversize bytes=%llu active=%d\n",
                content_length,upload_sessions_active);
        upload_reject(c,413,"{\"ok\":false,\"err\":\"upload exceeds the 24 MiB receive limit\"}");
        return;
    }
    int body_method=(method_end-io->buf==4 && !memcmp(io->buf,"POST",4)) ||
        (method_end-io->buf==3 && !memcmp(io->buf,"PUT",3));
    if (upload_uri(&uri) && body_method) {
        if (have_encoding) {
            fprintf(stderr,"NX_REMOTE_UPLOAD reject stage=chunked active=%d\n",upload_sessions_active);
            upload_reject(c,411,"{\"ok\":false,\"err\":\"Transfer-Encoding unsupported; send Content-Length\"}");
            return;
        }
        if (!have_length) {upload_reject(c,411,"{\"ok\":false,\"err\":\"Content-Length required\"}");return;}
        if (have_length && content_length>0) {
            if (upload_sessions_active>=1) {
                fprintf(stderr,"NX_REMOTE_UPLOAD reject stage=busy bytes=%llu active=%d\n",
                        content_length,upload_sessions_active);
                upload_reject(c,503,"{\"ok\":false,\"err\":\"another upload is in progress\"}");
                return;
            }
            upload_sessions_active++;
            c->flags|=MG_F_USER_3;
            /* This connection's own upload request: it must be handled now even
             * though the slot is held. Cleared as soon as the request has been
             * routed, so anything after it is queued in order. */
            c->flags|=MG_F_USER_6;
            fprintf(stderr,"NX_REMOTE_UPLOAD accept stage=header bytes=%llu active=%d\n",
                    content_length,upload_sessions_active);
        }
    }
}
static void gp_clear(void);   /* gallery.h, included below */

/* --- asynchronous upload write stage (2026-11-14) --------------------------
 * The card write (preserve the original as a backup, then replace it) used to
 * run synchronously inside the request handler and blocked the whole event
 * loop for its duration (measured 632 ms on a slow backing with a 20 MiB
 * preserve). The write now advances in bounded slices on MG_EV_POLL, so
 * health, input and other requests are served between slices. Everything
 * else is unchanged: the receive slot stays held until the response has
 * left, a second upload is still 503, the edit lock is held across the whole
 * stage, and an interrupted client never disturbs the original — the temp
 * files are unlinked and nothing is renamed. The body stays in the
 * connection's own receive buffer (bounded by NX_UPLOAD_MAX_BYTES); the
 * header is removed so the body starts at offset 0 and survives later
 * buffer growth without copy. */
struct upload_stage {
    struct mg_connection *c;
    int edit_lock;                /* flock fd held for the whole stage */
    int src_fd,dst_fd,fd;         /* preserve: original -> backup temp; replace: body -> upload temp */
    int mode;                     /* original file mode for the replacement */
    char backup_temp[PATH_MAX],temp[PATH_MAX],path[PATH_MAX],backup[PATH_MAX];
    size_t body_len,written;
    int stage;                    /* 0 idle, 1 preserve, 2 replace */
    int err,unconfirmed;          /* preserved errno; committed-but-unsynced flag */
    unsigned stage_started;
    unsigned char *body_buf;      /* stolen recv allocation: body at offset 0 */
    size_t body_cap;
};
static struct upload_stage upload_stage_state={0,-1,-1,-1,-1,0,{0},{0},{0},{0},0,0,0,0,0,0,NULL,0};
/* A pipelined request can share the packet with the upload body. The body
 * steal below takes the whole receive allocation, so those trailing bytes
 * must be copied out first and replayed after the upload response has fully
 * left (the receive slot is still held until then). Without this the second
 * request was silently dropped and the client waited forever. */
#define NX_UPLOAD_TAIL_MAX (1024U*1024U)
static unsigned char *upload_tail;
static size_t upload_tail_len;
static struct mg_connection *upload_tail_conn;
/* Request that arrived after the upload body on the same connection, while the
 * upload response is still outstanding. HTTP/1.1 requires responses in request
 * order, so these are queued verbatim and replayed only after the upload
 * response has fully left the socket. */
#define NX_DEFER_MAX_BYTES (256U*1024U)
struct nx_deferred { struct nx_deferred *next; size_t len; unsigned char data[1]; };
static struct nx_deferred *defer_head,*defer_tail;
static size_t defer_bytes;
static struct mg_connection *defer_conn;
static void upload_tail_drop(void) {
    free(upload_tail);upload_tail=NULL;upload_tail_len=0;upload_tail_conn=NULL;
}
static void defer_drop(void) {
    while (defer_head) { struct nx_deferred *next=defer_head->next;free(defer_head);defer_head=next; }
    defer_tail=NULL;defer_bytes=0;defer_conn=NULL;
}
static void defer_flush(struct mg_connection *c);
/* Returns 1 when the request was queued (caller must not answer it), 0 when it
 * can be handled now. */
static int defer_request(struct mg_connection *c,const struct http_message *h) {
    if (!(c->flags&MG_F_USER_3) || (c->flags&MG_F_USER_6)) return 0;
    if (defer_conn && defer_conn!=c) return 0;      /* per-connection order only */
    size_t length=h->message.len;
    if (!length || defer_bytes+length>NX_DEFER_MAX_BYTES) {
        /* Out of queue budget: answer in order by closing instead of letting a
         * later response overtake the pending upload. */
        c->flags|=MG_F_CLOSE_IMMEDIATELY;
        return 1;
    }
    struct nx_deferred *entry=(struct nx_deferred *)malloc(sizeof *entry+length);
    if (!entry) { c->flags|=MG_F_CLOSE_IMMEDIATELY;return 1; }
    entry->next=NULL;entry->len=length;
    memcpy(entry->data,h->message.p,length);
    if (defer_tail) defer_tail->next=entry; else defer_head=entry;
    defer_tail=entry;defer_bytes+=length;defer_conn=c;
    fprintf(stderr,"NX_REMOTE_DEFER queued=%zu pending=%zu\n",length,defer_bytes);
    return 1;
}
static void upload_tail_dispatch(struct mg_connection *c) {
    if (upload_tail_conn!=c) return;
    size_t length=upload_tail_len;unsigned char *data=upload_tail;
    upload_tail=NULL;upload_tail_len=0;upload_tail_conn=NULL;
    if (!c->recv_mbuf.len) {
        c->recv_mbuf.buf=(char *)data;c->recv_mbuf.len=length;c->recv_mbuf.size=length;
    } else if (!mbuf_insert(&c->recv_mbuf,0,data,length)) {
        free(data);c->flags|=MG_F_CLOSE_IMMEDIATELY;return;
    } else free(data);
    fprintf(stderr,"NX_REMOTE_UPLOAD tail bytes=%zu dispatched=1\n",length);
    if (c->proto_handler) c->proto_handler(c,MG_EV_RECV,NULL);
}
/* Replay queued requests in arrival order, one message at a time. Each entry is
 * a complete HTTP message, so the parser cannot merge them; the loop stops as
 * soon as another upload takes the slot, and resumes on that release. */
static void defer_flush(struct mg_connection *c) {
    if (defer_conn && defer_conn!=c) { if (!defer_head) defer_conn=NULL;return; }
    for (unsigned round=0;round<8 && defer_head && !(c->flags&MG_F_USER_3);round++) {
        struct nx_deferred *entry=defer_head;
        defer_head=entry->next;
        if (!defer_head) { defer_tail=NULL;defer_conn=NULL; }
        defer_bytes-=entry->len;
        /* Always copy through mbuf_insert: the queued bytes live inside the
         * queue node, and Mongoose frees recv_mbuf.buf as its own allocation. */
        if (!mbuf_insert(&c->recv_mbuf,0,entry->data,entry->len)) {
            free(entry);c->flags|=MG_F_CLOSE_IMMEDIATELY;defer_drop();return;
        }
        free(entry);
        fprintf(stderr,"NX_REMOTE_DEFER replayed=1 pending=%zu\n",defer_bytes);
        if (c->proto_handler) c->proto_handler(c,MG_EV_RECV,NULL);
    }
}
#define NX_UPLOAD_SLICE_BYTES (512U*1024U)
#define NX_UPLOAD_STAGE_DEADLINE_MS 60000
static char upload_slice_buffer[NX_UPLOAD_SLICE_BYTES];
static void upload_stage_reset(void) {
    struct upload_stage *s=&upload_stage_state;
    free(s->body_buf);
    if (s->src_fd>=0) close(s->src_fd);
    if (s->dst_fd>=0) close(s->dst_fd);
    if (s->fd>=0) close(s->fd);
    if (s->backup_temp[0]) unlink(s->backup_temp);
    if (s->temp[0]) unlink(s->temp);
    if (s->edit_lock>=0) close(s->edit_lock);
    memset(s,0,sizeof *s);
    s->src_fd=s->dst_fd=s->fd=s->edit_lock=-1;
}
/* Returns 0 while more slices remain, 1 when the response was sent, -1 on
 * failure (response sent, original untouched). */
static int upload_stage_step(struct mg_connection *c) {
    struct upload_stage *s=&upload_stage_state;
    if (now_ms()-s->stage_started>NX_UPLOAD_STAGE_DEADLINE_MS) {
        fprintf(stderr,"NX_REMOTE_UPLOAD write stage=timeout bytes=%zu/%zu\n",s->written,s->body_len);
        upload_stage_reset();
        reply(c,500,"{\"ok\":false,\"err\":\"upload failed; original preserved\"}");
        return -1;
    }
    if (s->stage==1) {
        /* EOF and error are distinct: an error must never fall through to the
         * completion path below, or a truncated temp would be published as
         * the backup and the original then replaced with the new body. */
        int failed=0;size_t done=0;
        while (done<NX_UPLOAD_SLICE_BYTES) {
            size_t space=NX_UPLOAD_SLICE_BYTES-done;
            ssize_t n=read(s->src_fd,upload_slice_buffer+done,space);
            if (n<0 && errno==EINTR) continue;
            if (n<0) { failed=1;s->err=errno;break; }
            if (!n) break;   /* EOF only */
            const unsigned char *p=(const unsigned char *)upload_slice_buffer+done;
            size_t left=(size_t)n;
            while (left) {
                ssize_t w=write(s->dst_fd,p,left);
                if (w<0 && errno==EINTR) continue;
                if (w<=0) { failed=1;s->err=w<0?errno:EIO;break; }
                p+=w;left-=(size_t)w;
            }
            if (failed) break;
            done+=(size_t)n;
        }
        if (failed) {
            fprintf(stderr,"NX_REMOTE_UPLOAD write stage=preserve-error errno=%d\n",s->err);
            upload_stage_reset();
            reply(c,500,"{\"ok\":false,\"err\":\"upload failed; original preserved\"}");
            return -1; }
        if (done) return 0;   /* rest of the preserve next poll */
        /* Original fully copied: durable temp, then publish the backup. */
        {int sync_err=fsync(s->dst_fd)?errno:0;
         int close_err=close(s->dst_fd)?errno:0;
         s->dst_fd=-1;
         if (sync_err||close_err) {
            fprintf(stderr,"NX_REMOTE_UPLOAD write stage=preserve-sync errno=%d\n",sync_err?sync_err:close_err);
            upload_stage_reset();
            reply(c,500,"{\"ok\":false,\"err\":\"upload failed; original preserved\"}");
            return -1; }}
        if (rename(s->backup_temp,s->backup)) {
            fprintf(stderr,"NX_REMOTE_UPLOAD write stage=preserve-rename errno=%d\n",errno);
            upload_stage_reset();
            reply(c,500,"{\"ok\":false,\"err\":\"upload failed; original preserved\"}");
            return -1; }
        s->backup_temp[0]=0;
        char parent[PATH_MAX];
        const char *slash=strrchr(s->backup,'/');
        int dirfsync=0;
        if (slash && (size_t)(slash-s->backup)<sizeof parent) {
            memcpy(parent,s->backup,(size_t)(slash-s->backup));parent[slash-s->backup]=0;
            int dir=open(parent,O_RDONLY|O_DIRECTORY);
            if (dir>=0) { dirfsync=!fsync(dir);close(dir); }
        }
        if (!dirfsync) {
            /* The backup rename already committed: keep going and report the
             * durability as unconfirmed instead of failing a committed state. */
            fprintf(stderr,"NX_REMOTE_UPLOAD write stage=preserve-dirsync errno=%d\n",errno);
            s->unconfirmed=1;
        }
        close(s->src_fd);s->src_fd=-1;
        s->stage=2;s->written=0;
        return 0;
    }
    if (s->stage==2) {
        if (s->body_buf==NULL) {   /* must not happen: ownership taken at start */
            fprintf(stderr,"NX_REMOTE_UPLOAD write stage=body-gone\n");
            upload_stage_reset();
            reply(c,500,"{\"ok\":false,\"err\":\"upload failed; original preserved\"}");
            return -1;
        }
        if (s->fd<0) {
            snprintf(s->temp,sizeof s->temp,"%s.upload-XXXXXX",s->path);
            s->fd=mkstemp(s->temp);
            if (s->fd<0 || fchmod(s->fd,s->mode)) {
                fprintf(stderr,"NX_REMOTE_UPLOAD write stage=replace-open errno=%d\n",errno);
                upload_stage_reset();
                reply(c,500,"{\"ok\":false,\"err\":\"upload failed; original preserved\"}");
                return -1;
            }
        }
        size_t left=s->body_len-s->written;
        size_t take=left<NX_UPLOAD_SLICE_BYTES?left:NX_UPLOAD_SLICE_BYTES;
        const unsigned char *p=s->body_buf+s->written;
        size_t left2=take;
        while (left2) {
            ssize_t w=write(s->fd,p,left2);
            if (w<0 && errno==EINTR) continue;
            if (w<=0) { p=NULL;break; }
            p+=w;left2-=(size_t)w;
        }
        if (p==NULL) {
            fprintf(stderr,"NX_REMOTE_UPLOAD write stage=replace-error errno=%d\n",errno);
            upload_stage_reset();
            reply(c,500,"{\"ok\":false,\"err\":\"upload failed; original preserved\"}");
            return -1; }
        s->written+=take;
        if (s->written<s->body_len) return 0;   /* rest next poll */
        {int sync_err=fsync(s->fd)?errno:0;
         int close_err=close(s->fd)?errno:0;
         s->fd=-1;
         if (sync_err||close_err) {
            fprintf(stderr,"NX_REMOTE_UPLOAD write stage=replace-sync errno=%d\n",sync_err?sync_err:close_err);
            upload_stage_reset();
            reply(c,500,"{\"ok\":false,\"err\":\"upload failed; original preserved\"}");
            return -1; }}
        if (rename(s->temp,s->path)) {
            fprintf(stderr,"NX_REMOTE_UPLOAD write stage=replace-rename errno=%d\n",errno);
            upload_stage_reset();
            reply(c,500,"{\"ok\":false,\"err\":\"upload failed; original preserved\"}");
            return -1; }
        s->temp[0]=0;
        char parent[PATH_MAX];
        const char *slash=strrchr(s->path,'/');
        int dirfsync=0;
        if (slash && (size_t)(slash-s->path)<sizeof parent) {
            memcpy(parent,s->path,(size_t)(slash-s->path));parent[slash-s->path]=0;
            int dir=open(parent,O_RDONLY|O_DIRECTORY);
            if (dir>=0) { dirfsync=!fsync(dir);close(dir); }
        }
        size_t delivered=s->body_len;   /* reset below clears the state */
        int unconfirmed=s->unconfirmed;
        if (!dirfsync) {
            /* The replacement rename already committed: report success with
             * durability unconfirmed, never "original preserved". */
            fprintf(stderr,"NX_REMOTE_UPLOAD write stage=replace-dirsync errno=%d\n",errno);
            unconfirmed=1;
        }
        upload_stage_reset();
        gp_clear();
        char json[192];
        snprintf(json,sizeof json,"{\"ok\":true,\"backup_available\":true,\"bytes\":%zu,%s\"message\":\"media updated\"}",
            delivered,unconfirmed?"\"durability\":\"unconfirmed\",":"");
        reply(c,200,json);
        return 1;
    }
    return 0;
}
static void upload_stage_poll(struct mg_connection *c) {
    while (upload_stage_state.c==c && upload_stage_state.stage) {
        if (upload_stage_step(c)) break;   /* response sent or failed */
        /* Slice bound: one slice per poll keeps health/input gaps bounded. */
        break;
    }
}
static void upload_closed(struct mg_connection *c) {
    c->flags&=~MG_F_USER_6;
    if (upload_tail_conn==c) upload_tail_drop();
    /* Queued requests belong to this connection's order; they die with it. */
    if (defer_conn==c) defer_drop();
    if (upload_stage_state.c==c && upload_stage_state.stage) {
        fprintf(stderr,"NX_REMOTE_UPLOAD write stage=aborted reason=disconnect bytes=%zu/%zu\n",
                upload_stage_state.written,upload_stage_state.body_len);
        upload_stage_reset();   /* original untouched: temps unlinked, nothing renamed */
    }
    upload_release(c);
}

#include "gallery.h"
#include "media_replace.h"

static int gallery_edit_origin(struct mg_connection *c,struct http_message *h) {
    struct mg_str *origin=mg_get_http_header(h,"Origin"),*host=mg_get_http_header(h,"Host");
    if (origin && (!host || origin->len!=host->len+7 || memcmp(origin->p,"http://",7) || memcmp(origin->p+7,host->p,host->len))) {
        reply(c,403,"{\"ok\":false,\"err\":\"origin mismatch\"}");return 0;
    }
    return 1;
}
static int gallery_edit_target(struct http_message *h,char *path,char *backup) {
    char relative[1024];
    int n=mg_get_http_var(&h->query_string,"p",relative,sizeof relative);
    if (n<=0 || !media_path(relative,path)) return 0;
    const char *leaf=strrchr(path,'/'),*extension=strrchr(path,'.');
    struct stat state;
    return leaf && leaf[1]!='.' && extension &&
        (!strcasecmp(extension,".jpg") || !strcasecmp(extension,".jpeg")) &&
        !stat(path,&state) && S_ISREG(state.st_mode) &&
        !nx_media_backup_path(path,backup,PATH_MAX);
}
static void gallery_upload_capabilities(struct mg_connection *c,struct http_message *h) {
    if (mg_vcmp(&h->method,"GET")) { reply(c,405,"{\"ok\":false,\"err\":\"GET required\"}");return; }
    char json[192];
    /* The receive limit, not the on-card backup size cap: uploads are held
     * in memory during reception, originals are streamed to disk. */
    snprintf(json,sizeof json,"{\"ok\":true,\"backup_available\":true,\"restore_available\":true,"
        "\"original_download_available\":true,\"jpeg_only\":true,\"max_bytes\":%u,"
        "\"chunked_upload\":false}",NX_UPLOAD_MAX_BYTES);
    reply(c,200,json);
}
static void gallery_original(struct mg_connection *c,struct http_message *h) {
    if (mg_vcmp(&h->method,"GET")) { reply(c,405,"{\"ok\":false,\"err\":\"GET required\"}");return; }
    char path[PATH_MAX],backup[PATH_MAX];struct stat info;
    if (!gallery_edit_target(h,path,backup)) { reply(c,400,"{\"ok\":false,\"err\":\"existing JPEG required\"}");return; }
    int available=nx_media_backup_available(backup);
    if (available!=1 || lstat(backup,&info) || info.st_size>120*1024*1024) {
        reply(c,available==0?404:409,"{\"ok\":false,\"err\":\"original backup unavailable or invalid\"}");return;
    }
    serve_media_path(c,h,backup,"image/jpeg");
}
static void gallery_edit_status(struct mg_connection *c,struct http_message *h) {
    if (mg_vcmp(&h->method,"GET")) { reply(c,405,"{\"ok\":false,\"err\":\"GET required\"}");return; }
    char path[PATH_MAX],backup[PATH_MAX];struct stat state;
    if (!gallery_edit_target(h,path,backup) || stat(path,&state)) { reply(c,400,"{\"ok\":false,\"err\":\"existing JPEG required\"}");return; }
    int available=nx_media_backup_available(backup);
    if (available<0) { reply(c,409,"{\"ok\":false,\"err\":\"invalid original backup\"}");return; }
    char json[160];snprintf(json,sizeof json,"{\"ok\":true,\"backup_available\":%s,\"size\":%lld}",available?"true":"false",(long long)state.st_size);reply(c,200,json);
}
static void gallery_restore(struct mg_connection *c,struct http_message *h) {
    if (mg_vcmp(&h->method,"POST")) { reply(c,405,"{\"ok\":false,\"err\":\"POST required\"}");return; }
    if (!gallery_edit_origin(c,h)) return;
    char path[PATH_MAX],backup[PATH_MAX];
    if (!gallery_edit_target(h,path,backup)) { reply(c,400,"{\"ok\":false,\"err\":\"existing JPEG required\"}");return; }
    int lock=nx_media_edit_lock(media_root);
    if (lock<0) { reply(c,409,"{\"ok\":false,\"err\":\"media editing unavailable or busy\"}");return; }
    int available=nx_media_backup_available(backup);
    if (available!=1 || nx_media_copy(backup,path)) {
        close(lock);reply(c,500,"{\"ok\":false,\"err\":\"restore failed or backup unavailable\"}");return;
    }
    struct stat restored;int stated=stat(path,&restored);close(lock);gp_clear();
    if (stated) { reply(c,500,"{\"ok\":false,\"err\":\"restored file unavailable\"}");return; }
    char json[180];snprintf(json,sizeof json,"{\"ok\":true,\"backup_available\":true,\"bytes\":%lld,\"message\":\"original restored\"}",(long long)restored.st_size);reply(c,200,json);
}
static void gallery_upload(struct mg_connection *c,struct http_message *h) {
    char path[PATH_MAX],backup[PATH_MAX],required[8];
    if (mg_vcmp(&h->method,"POST") && mg_vcmp(&h->method,"PUT")) { reply(c,405,"{\"ok\":false,\"err\":\"POST or PUT required\"}");return; }
    if (!gallery_edit_origin(c,h)) return;
    /* Backstop only: upload_gate refuses oversized or chunked bodies at the
     * header stage, so a request event here always carries a bounded body. */
    struct mg_str *length=mg_get_http_header(h,"Content-Length");
    unsigned long long expected=0;
    /* This Mongoose also emits HTTP_REQUEST on close for an incomplete
     * request. A partial body, even one ending in JPEG EOI, must not write. */
    if (!length || !upload_parse_length(length->p,length->len,&expected) ||
        !h->body.len || expected!=h->body.len || h->body.len>NX_UPLOAD_MAX_BYTES) {
        reply(c,400,"{\"ok\":false,\"err\":\"complete bounded upload required\"}");return;
    }
    int n=mg_get_http_var(&h->query_string,"require_backup",required,sizeof required);
    if ((n!=-1 && (n!=1 || required[0]!='1')) || !gallery_edit_target(h,path,backup) ||
            h->body.len<4 || (unsigned char)h->body.p[0]!=0xff || (unsigned char)h->body.p[1]!=0xd8 ||
            (unsigned char)h->body.p[h->body.len-2]!=0xff || (unsigned char)h->body.p[h->body.len-1]!=0xd9) {
        reply(c,400,"{\"ok\":false,\"err\":\"existing JPEG and complete JPEG body required\"}");return;
    }
    int lock=nx_media_edit_lock(media_root);
    if (lock<0) { reply(c,409,"{\"ok\":false,\"err\":\"media editing unavailable or busy\"}");return; }
    /* Start the staged write: slices run on MG_EV_POLL so the event loop keeps
     * serving other requests; the original is never touched until the final
     * rename. An existing valid backup is kept (same rule as before). */
    if (upload_stage_state.stage) {
        close(lock);
        reply(c,409,"{\"ok\":false,\"err\":\"another upload is being written\"}");return;
    }
    int available=nx_media_backup_available(backup);
    struct stat original_state;
    if (available<0 || stat(path,&original_state) || !S_ISREG(original_state.st_mode)) {
        close(lock);
        reply(c,500,"{\"ok\":false,\"err\":\"upload failed; original preserved\"}");return;
    }
    struct upload_stage *s=&upload_stage_state;
    memset(s,0,sizeof *s);
    s->src_fd=s->dst_fd=s->fd=-1;
    s->c=c;s->edit_lock=lock;s->mode=original_state.st_mode&0777;
    snprintf(s->path,sizeof s->path,"%s",path);
    snprintf(s->backup,sizeof s->backup,"%s",backup);
    s->body_len=h->body.len;
    s->stage_started=now_ms();
    if (available) {
        s->stage=2;
        char parent[PATH_MAX];
        const char *slash=strrchr(backup,'/');
        int dirfsync=0;
        if (slash && (size_t)(slash-backup)<sizeof parent) {
            memcpy(parent,backup,(size_t)(slash-backup));parent[slash-backup]=0;
            int dir=open(parent,O_RDONLY|O_DIRECTORY);
            if (dir>=0) { dirfsync=!fsync(dir);close(dir); }
        }
        if (!dirfsync) {
            upload_stage_reset();
            reply(c,500,"{\"ok\":false,\"err\":\"upload failed; original preserved\"}");return;
        }
    } else {
        s->stage=1;
        s->src_fd=open(path,O_RDONLY|O_NOFOLLOW);
        snprintf(s->backup_temp,sizeof s->backup_temp,"%s.copy-XXXXXX",backup);
        s->dst_fd=s->src_fd>=0?mkstemp(s->backup_temp):-1;
        if (s->dst_fd<0 || fchmod(s->dst_fd,s->mode)) {
            fprintf(stderr,"NX_REMOTE_UPLOAD write stage=preserve-open errno=%d\n",errno);
            upload_stage_reset();
            reply(c,500,"{\"ok\":false,\"err\":\"upload failed; original preserved\"}");return;
        }
    }
    /* Copy any request bytes that share the packet with the body's tail. They
     * are older than anything that arrives during the staged write, so the
     * dispatch prepends them. The 1 MiB bound keeps a pipelined 24 MiB body
     * from doubling the receive footprint; such a request is refused with a
     * visible status and the connection closes. */
    size_t buffered=c->recv_mbuf.len;
    size_t header_len=(size_t)(h->body.p-c->recv_mbuf.buf);
    size_t tail_len=buffered>(header_len+h->body.len)?buffered-(header_len+h->body.len):0;
    if (tail_len>NX_UPLOAD_TAIL_MAX || (tail_len && upload_tail_conn)) {
        int too_large=tail_len>NX_UPLOAD_TAIL_MAX;
        upload_stage_reset();
        fprintf(stderr,"NX_REMOTE_UPLOAD reject stage=%s bytes=%zu active=%d\n",
                too_large?"tail-overflow":"tail-busy",tail_len,upload_sessions_active);
        reply(c,too_large?413:409,too_large?
              "{\"ok\":false,\"err\":\"pipelined request too large after the upload body\"}":
              "{\"ok\":false,\"err\":\"previous pipelined request still pending\"}");
        c->flags|=MG_F_SEND_AND_CLOSE;return;
    }
    if (tail_len) {
        upload_tail=(unsigned char *)malloc(tail_len);
        if (!upload_tail) {
            upload_stage_reset();
            reply(c,500,"{\"ok\":false,\"err\":\"upload failed; original preserved\"}");
            c->flags|=MG_F_SEND_AND_CLOSE;return;
        }
        memcpy(upload_tail,c->recv_mbuf.buf+header_len+h->body.len,tail_len);
        upload_tail_len=tail_len;upload_tail_conn=c;
        fprintf(stderr,"NX_REMOTE_UPLOAD tail captured bytes=%zu\n",tail_len);
    }
    /* Take explicit ownership of the body: pinned mongoose removes the whole
     * parsed message (header+body) from recv_mbuf right after this handler
     * returns (mg_http_handler2 -> mbuf_remove(io, hm->message.len)), which
     * would discard or reshuffle a body left in the connection buffer once
     * pipelined or trailing bytes arrive. Steal the allocation instead: the
     * body already sits at offset 0 after the header removal, the connection
     * gets a fresh empty mbuf, and the stage frees it at reset. The
     * per-upload bound stays NX_UPLOAD_MAX_BYTES with no extra copy. */
    mbuf_remove(&c->recv_mbuf,header_len);
    s->body_buf=(unsigned char *)c->recv_mbuf.buf;
    s->body_cap=c->recv_mbuf.size;
    c->recv_mbuf.buf=NULL;c->recv_mbuf.len=0;c->recv_mbuf.size=0;
    fprintf(stderr,"NX_REMOTE_UPLOAD write stage=begin bytes=%zu preserve=%d\n",s->body_len,s->stage==1);
}

#include "status_values.h"
#include "quickshare.h"
#include "panel_shortcut.h"
#include "custom_key.h"
#include "menu_key.h"
#include "exposure.h"
#include "dial.h"
#include "looks.h"
#include "recipe.h"
#include "mod_manager.h"
#include "diagnostics.h"
#include "media_io.h"
#include "usb_storage.h"
#include "discovery.h"
static int connection_token(const struct mg_str *header,const char *token) {
    if (!header) return 0;
    size_t start=0,length=strlen(token);
    while (start<header->len) {
        size_t end=start;
        while (end<header->len && header->p[end]!=',') end++;
        size_t first=start,last=end;
        while (first<last && isspace((unsigned char)header->p[first])) first++;
        while (last>first && isspace((unsigned char)header->p[last-1])) last--;
        if (last-first==length && !strncasecmp(header->p+first,token,length)) return 1;
        start=end+1;
    }
    return 0;
}
static void handler(struct mg_connection *c,int event,void *data) {
    if (trace_http && trace_http_events<32 &&
        (event==MG_EV_ACCEPT || event==MG_EV_RECV || event==MG_EV_SEND || event==MG_EV_HTTP_REQUEST || event==MG_EV_CLOSE)) {
        fprintf(stderr,"NX_REMOTE_HTTP event=%d socket=%d received=%lu pending=%lu\n",
                event,(int)c->sock,(unsigned long)c->recv_mbuf.len,(unsigned long)c->send_mbuf.len);
        trace_http_events++;
    }
    if (event==MG_EV_CLOSE) { ex_closed(c);rp_closed(c);xw_closed(c);upload_closed(c);return; }
    /* Early receive decision from the request header, before any body byte
     * of an oversized or chunked upload is buffered. */
    if (event==MG_EV_RECV) upload_gate(c);
    /* Staged upload write: bounded slices between polls keep the event loop
     * serving other requests while the card write is in progress. */
    if (event==MG_EV_POLL && upload_stage_state.stage) upload_stage_poll(c);
    /* Do not close when an asynchronous request has not produced a response.
     * SEND runs after bytes leave the buffer; Mongoose refills file transfers
     * before invoking this handler, so streaming files also finish normally. */
    if (event==MG_EV_SEND && (c->flags&MG_F_USER_1) && !c->send_mbuf.len) {
        c->flags|=MG_F_SEND_AND_CLOSE;
    }
    /* The upload's receive slot is held from its accepted header until the
     * response has fully left, or the connection dies mid-body. */
    if (event==MG_EV_SEND && (c->flags&MG_F_USER_3) && !c->send_mbuf.len) {
        c->flags&=~MG_F_USER_6;
        upload_release(c);
        /* Requests that shared the packet with the body wait until the slot
         * is free, so the second upload cannot bypass the busy accounting. */
        if (!(c->flags&MG_F_USER_1)) upload_tail_dispatch(c);
        /* Then anything that arrived later on this connection, in order. */
        if (!(c->flags&MG_F_USER_1)) defer_flush(c);
    }
    if (event!=MG_EV_HTTP_REQUEST) return;
    if (c->flags&MG_F_USER_4) return; /* rejected at receive time, including close-time callbacks */
    if (xw_pending(c)) { c->flags|=MG_F_CLOSE_IMMEDIATELY;return; }
    /* One file stream per connection: this Mongoose version overwrites its
     * FILE on a second pipelined request without closing the previous one. */
    if (c->flags&MG_F_USER_2) { c->flags|=MG_F_CLOSE_IMMEDIATELY;return; }
    struct http_message *h=data;
    if (defer_request(c,h)) return;
    const struct mg_str *connection=mg_get_http_header(h,"Connection");
    if (connection_token(connection,"close") ||
        (mg_vcmp(&h->proto,"HTTP/1.1") && !connection_token(connection,"keep-alive"))) c->flags|=MG_F_USER_1;
    char uri[2048];
    if (h->uri.len>=sizeof uri || mg_url_decode(h->uri.p,h->uri.len,uri,sizeof uri,0)<0) { reply(c,400,"{\"error\":\"invalid URI\"}");return; }
    usb_storage_poll(1);media_io_poll(c->mgr);
    if (!strncmp(uri,"/api/v1/media/",14)) { media_io_request(c,h,uri);return; }
    if (!strcmp(uri,"/api/v1/storage/status")) { usb_storage_request(c,h);return; }
    if (media_paused && !media_io_allowed(uri)) {
        reply(c,503,"{\"error\":\"media access paused\",\"code\":\"media_paused\"}");return;
    }
    /* Recipe batches and the existing nonblocking controls share one HTTP
     * execution slot. Catalogue reads are independent of camera mutations. */
    if (rp_task.active && (!strncmp(uri,"/api/v1/exposure/",17) ||
        (!strncmp(uri,"/api/v1/color/",14) && strcmp(uri,"/api/v1/color/recipes")))) {
        reply(c,409,"{\"error\":\"原生配方控制任务正在运行\"}");return;
    }
    if (!strcmp(uri,"/api/v1/health")) {
        reply(c,200,"{\"ok\":true,\"service\":\"nx300m-remote\"}");
    } else if (!strcmp(uri,"/api/v1/camera/info")) {
        /* Capability checks must not open the source card without a live hook. */
        int lut_ready=lk_ready(),recipe_ready=rp_ready(NULL);
        int lut_available=lut_ready && lk_library_available();
        int recipe_available=recipe_ready && rp_library_available();
        int pw_ready=ex_ready(3,NULL);
        int exposure_ready=ex_ready(0,NULL);
        int exposure_writable=exposure_ready && !ex_read_only(exposure_ready);
        nx_discovery_refresh(now_ms());
        char mac_json[20]="null",instance_json[44]="null";
        if (nx_discovery.mac[0]) snprintf(mac_json,sizeof mac_json,"\"%s\"",nx_discovery.mac);
        if (nx_discovery.instance[0]) snprintf(instance_json,sizeof instance_json,"\"%s\"",nx_discovery.instance);
        char custom_json[768],menu_json[384];custom_key_status(custom_json,sizeof custom_json);menu_key_status(menu_json,sizeof menu_json);
        char json[4096];snprintf(json,sizeof json,"{\"service_revision\":\"20261031-fn-evdev\",\"model\":\"%s\",\"fw_ver\":\"%s\",\"mac_address\":%s,\"instance_id\":%s,\"media_paused\":%s,\"capabilities\":{\"osd\":true,\"input\":true,\"gallery\":true,\"gallery_preview\":true,\"gallery_writeback\":true,\"gallery_original_backup\":true,\"screen_mirroring\":true,\"sensor_liveview\":false,\"liveview\":false,\"lcd\":false,\"lcd_control\":false,\"silent_shutter\":false,\"key_input\":true,\"keep_alive\":true,\"time_sync\":false,\"quickshare_native\":%s,\"exposure_status\":%s,\"iso_controls\":%s,\"shutter_limit\":%s,\"color_style\":%s,\"chroma_style\":%s,\"contrast_style\":%s,\"sharpness_style\":%s,\"source_rgb_style\":%s,\"picture_wizard\":%s,\"dial_lock\":%s,\"looks\":%s,\"lut_selection\":%s,\"recipes\":%s,\"recipe_style\":%s},\"osd\":{\"width\":800,\"height\":480,\"block_width\":80,\"block_height\":40},\"screen_source\":\"%s\",\"video_source\":\"%s\",\"custom_key\":%s,\"menu_key\":%s,\"x11\":{\"background_enabled\":%s,\"helper_running\":%s,\"held_input\":%s}}",get_nx_model_name(),get_nx_model_version(),mac_json,instance_json,media_paused?"true":"false",qs_available()?"true":"false",exposure_ready?"true":"false",exposure_writable?"true":"false",ex_ready(1,NULL)?"true":"false",ex_ready(2,NULL)?"true":"false",ex_ready(4,NULL)?"true":"false",ex_ready(5,NULL)?"true":"false",ex_ready(6,NULL)?"true":"false",ex_ready(7,NULL)?"true":"false",pw_ready?"true":"false",pw_ready?"true":"false",lut_available?"true":"false",lut_ready?"true":"false",recipe_available?"true":"false",recipe_ready?"true":"false",lcd_screen_source(),lcd_video_source(),custom_json,menu_json,panel_shortcut_allowed()?"true":"false",xw.pid && !xw.retiring?"true":"false",xw_has_held()?"true":"false");reply(c,200,json);
    } else if (!strcmp(uri,"/api/v1/mods/status")) mm_request(c,h,0);
    else if (!strcmp(uri,"/api/v1/panel/entry")) {
        if (mg_vcmp(&h->method,"GET")) reply(c,405,"{\"error\":\"GET required\"}");
        else { char json[512];fn_key_status(json,sizeof json);reply(c,200,json); }
    }
    else if (!strcmp(uri,"/api/v1/diagnostics")) diagnostic_request(c,h);
    else if (!strcmp(uri,"/api/v1/network/status")) nx_discovery_status(c,h,media_paused);
    else if (!strcmp(uri,"/api/v1/mods/install-plan")) mm_request(c,h,1);
    else if (!strcmp(uri,"/api/v1/camera/status")) {
        /* Upstream refreshes this cache through its input listener. This
         * backend uses XTest instead, so each status query rereads the dial. */
        set_dial_mode(MODE_UNKNOWN);
        char json[512];snprintf(json,sizeof json,"{\"battery_charging\":%s,\"battery_percent\":%d,\"battery_level\":%d,\"drive\":\"%s\",\"mode\":\"%s\",\"hevc\":\"%s\",\"cameras\":[]}",camera_charging_json(),get_battery_percent(),camera_battery_level(),get_dial_drive_string(),camera_mode_string(),get_hevc_state_string());reply(c,200,json);
    } else if (!strcmp(uri,"/api/v1/input/inject")) inject(c,h);
    else if (!strcmp(uri,"/api/v1/input/inject_keep_alive")) { input_alive=now_ms();reply(c,200,"{\"ok\":true}"); }
    else if (!strcmp(uri,"/api/v1/osd/get")) osd(c,h);
    else if (!strncmp(uri,"/api/v1/quickshare/",19)) qs_request(c,h,uri);
    else if (!strcmp(uri,"/api/v1/exposure/iso")) ex_request(c,h,0);
    else if (!strcmp(uri,"/api/v1/exposure/shutter")) ex_request(c,h,1);
    else if (!strcmp(uri,"/api/v1/color/recipes")) rp_request(c,h,1);
    else if (!strcmp(uri,"/api/v1/color/recipe")) rp_request(c,h,0);
    else if (!strcmp(uri,"/api/v1/color/rgb")) ex_request(c,h,2);
    else if (!strcmp(uri,"/api/v1/color/chroma")) ex_request(c,h,4);
    else if (!strcmp(uri,"/api/v1/color/contrast")) ex_request(c,h,5);
    else if (!strcmp(uri,"/api/v1/color/source-rgb")) ex_request(c,h,7);
    else if (!strcmp(uri,"/api/v1/color/sharpness")) ex_request(c,h,6);
    else if (!strcmp(uri,"/api/v1/color/picture-wizard")) ex_request(c,h,3);
    else if (!strcmp(uri,"/api/v1/controls/dial-lock")) dl_request(c,h);
    else if (!strcmp(uri,"/api/v1/looks")) lk_request(c,h,0);
    else if (!strcmp(uri,"/api/v1/looks/select")) lk_request(c,h,1);
    else if (!strcmp(uri,"/api/v1/looks/stock")) lk_request(c,h,2);
    else if (!strcmp(uri,"/api/v1/gallery/preview")) gallery_preview(c,h);
    else if (!strcmp(uri,"/api/v1/gallery/original")) gallery_original(c,h);
    else if (!strcmp(uri,"/api/v1/gallery/upload")) { c->flags&=~MG_F_USER_6;gallery_upload(c,h); }
    else if (!strcmp(uri,"/api/v1/gallery/upload/capabilities")) gallery_upload_capabilities(c,h);
    else if (!strcmp(uri,"/api/v1/gallery/edit-status")) gallery_edit_status(c,h);
    else if (!strcmp(uri,"/api/v1/gallery/restore")) gallery_restore(c,h);
    else if (!strcmp(uri,"/cgi-bin/dirlist")) dirlist(c,h);
    else if (!strncmp(uri,"/DCIM/",6)) serve_media(c,h,uri+6);
    else if (!strncmp(uri,"/api/",5) || !strncmp(uri,"/cgi-bin/",9)) reply(c,501,"{\"error\":\"capability not connected\"}");
    else mg_serve_http(c,h,assets);
}
int main(int argc,char **argv) {
    close_inherited_fds();
    /* Initialize the card log before anything can fail: an X11 or media
     * failure must still leave evidence, and the log records whether the
     * environment variable reached this process at all. */
    fn_log_init();
    fn_log("daemon start pid=%ld listen_env=%s", (long)getpid(),
        getenv("NX_REMOTE_LISTEN")?getenv("NX_REMOTE_LISTEN"):"(unset)");
    const char *listen="127.0.0.1:8781",*web="/mod/remote/web_root";media_root="/mnt/mmc/DCIM";
    for (int i=1;i<argc;i++) {
        if (!strcmp(argv[i],"--listen") && i+1<argc) listen=argv[++i];
        else if (!strcmp(argv[i],"--web-root") && i+1<argc) web=argv[++i];
        else if (!strcmp(argv[i],"--media-root") && i+1<argc) media_root=argv[++i];
        else if (!strcmp(argv[i],"--quickshare-binary") && i+1<argc) qs_binary=argv[++i];
        else if (!strcmp(argv[i],"--quickshare-runner") && i+1<argc) qs_runner=argv[++i];
        else return 2;
    }
    trace_http=getenv("NX_REMOTE_TRACE_HTTP") && !strcmp(getenv("NX_REMOTE_TRACE_HTTP"),"1");
    signal(SIGPIPE,SIG_IGN);signal(SIGINT,stop);signal(SIGTERM,stop);signal(SIGCHLD,SIG_DFL);
    setpriority(PRIO_PROCESS,0,19);
    nx_model_init();
    if (!xw_init()) { fputs("NX REMOTE: X worker memory unavailable\n",stderr);fn_log_exit("xw-init");return 3; }
    panel_shortcut_init();
    struct mg_mgr manager;mg_mgr_init(&manager,NULL);
    assets.document_root=web;assets.enable_directory_listing="no";
    if (!media_io_init(web)) {
        fputs("NX REMOTE: invalid RAM media-pause state\n",stderr);fn_log_exit("media-io-init");
        mg_mgr_free(&manager);xw_shutdown();return 5;
    }
    usb_storage_init();media_io_poll(&manager);
    struct mg_connection *server=mg_bind(&manager,listen,handler);
    if (!server) { usb_storage_shutdown();mg_mgr_free(&manager);xw_shutdown();fn_log_exit("http-bind");return 4; }
    mg_set_protocol_http_websocket(server);
    /* Accepted connections inherit this receive bound; upload_gate refuses
     * oversized or chunked uploads long before it is reached, and it stops
     * reads from a stalled or malicious sender at a fixed ceiling. */
    server->recv_mbuf_limit=NX_RECV_LIMIT_BYTES;
    nx_discovery_init(server->sock,get_nx_model_name(),get_nx_model_version());
    fprintf(stderr,"NX_REMOTE_READY model=%s version=%s listen=%s media=%s source=stock-LCD-and-X11\n",get_nx_model_name(),get_nx_model_version(),listen,media_root);
    custom_key_init();
    menu_key_init();
    input_alive=now_ms();
    /* The supervisor creates "starting" before fork. Publish only after the
     * HTTP listener and key helpers are initialized; readers still verify the
     * exact daemon identity before accepting this RAM-only notification. */
    int result=0;
    if (publish_ready()<0) { perror("NX REMOTE: readiness notification");stopping=1;result=6; }
    unsigned int traced_polls=0;
    while (!stopping) {
        int trace_poll=trace_http && traced_polls<3;
        if (trace_poll) fprintf(stderr,"NX_REMOTE_HTTP poll_begin=%u\n",traced_polls);
        mg_mgr_poll(&manager,50);
        if (trace_poll) { fprintf(stderr,"NX_REMOTE_HTTP poll_end=%u\n",traced_polls);traced_polls++; }
        qs_poll();ex_poll();rp_poll();usb_storage_poll(0);media_io_poll(&manager);xw_poll();panel_shortcut_poll();custom_key_poll();menu_key_poll();
        nx_discovery_poll(media_paused);
        xw_input_watchdog();
    }
    usb_storage_shutdown();custom_key_shutdown();menu_key_shutdown();ex_release();if (rp_task.active) rp_release();qs_shutdown();gp_clear();mg_mgr_free(&manager);xw_shutdown();return result;
}
