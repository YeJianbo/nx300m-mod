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
static int inject_x11(const char *query) {
    struct mg_str query_string=mg_mk_str(query);
    char value[80]; const char *actions[]={"key","keydown","keyup"};
    focus_input();
    for (int action=0; action<3; action++) {
        int length=mg_get_http_var(&query_string, actions[action], value, sizeof value);
        if (length<0) continue;
        if (!length) return 400;
        for (int i=0; i<length; i++) if (!isalnum((unsigned char)value[i]) && value[i]!='_') return 400;
        KeySym symbol=XStringToKeysym(value);
        if (symbol==NoSymbol) return 400;
        KeyCode key=XKeysymToKeycode(display,symbol);
        if (!key) return 400;
        if (action!=2) { held_keys[key]=1;XTestFakeKeyEvent(display,key,True,0); }
        if (action!=1) XTestFakeKeyEvent(display,key,False,0);
        input_alive=now_ms(); XSync(display,False);
        if (action!=1) held_keys[key]=0;
        fprintf(stderr,"[NX REMOTE] input action=%s key=%s code=%u\n",actions[action],value,key);
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
    for (const unsigned char *p=(const unsigned char *)value;*p;p++) {
        if (*p=='"' || *p=='\\') mg_printf_http_chunk(c,"\\%c",*p);
        else if (*p<32) mg_printf_http_chunk(c,"\\u%04x",*p);
        else mg_send_http_chunk(c,(const char *)p,1);
    }
    mg_printf_http_chunk(c,"\"");
}
static int media_path(const char *relative,char *result) {
    char base[PATH_MAX],candidate[PATH_MAX];
    if (media_paused) return 0;
    if (*relative=='/' || strchr(relative,'\\')) return 0;
    if (!realpath(media_root,base)) return 0;
    int n=snprintf(candidate,sizeof candidate,"%s/%s",base,relative);
    if (n<0 || n>=(int)sizeof candidate || !realpath(candidate,result)) return 0;
    size_t size=strlen(base);
    return !strncmp(result,base,size) && (!result[size] || result[size]=='/');
}
static void dirlist(struct mg_connection *c,struct http_message *h) {
    char relative[1024]="",path[PATH_MAX];
    if (mg_get_http_var(&h->query_string,"p",relative,sizeof relative)<-1 || !media_path(relative,path)) { reply(c,404,"{\"ok\":false,\"err\":\"media directory unavailable\"}");return; }
    struct dirent **entries;int count=scandir(path,&entries,NULL,alphasort);
    if (count<0) { reply(c,404,"{\"ok\":false,\"err\":\"directory unavailable\"}");return; }
    mg_printf(c,"HTTP/1.1 200 OK\r\nContent-Type: application/json; charset=utf-8\r\nTransfer-Encoding: chunked\r\nCache-Control: no-store\r\n\r\n");
    mg_printf_http_chunk(c,"{\"ok\":true,\"path\":");json_string(c,relative);
    for (int isdir=1;isdir>=0;isdir--) {
        mg_printf_http_chunk(c,isdir?",\"dirs\":[":",\"files\":[");int comma=0;
        for (int i=0;i<count;i++) {
            if (entries[i]->d_name[0]=='.') continue;
            char child[PATH_MAX],joined[PATH_MAX];struct stat st;
            int n=snprintf(joined,sizeof joined,"%s%s%s",relative,*relative?"/":"",entries[i]->d_name);
            if (n<0 || n>=(int)sizeof joined || !media_path(joined,child) || stat(child,&st)) continue;
            if ((isdir && !S_ISDIR(st.st_mode)) || (!isdir && !S_ISREG(st.st_mode))) continue;
            if (comma++) mg_printf_http_chunk(c,",");
            json_string(c,entries[i]->d_name);
        }
        mg_printf_http_chunk(c,"]");
    }
    mg_printf_http_chunk(c,",\"sizes\":{");int comma=0;
    for (int i=0;i<count;i++) {
        if (entries[i]->d_name[0]=='.') continue;
        char child[PATH_MAX],joined[PATH_MAX];struct stat st;
        int n=snprintf(joined,sizeof joined,"%s%s%s",relative,*relative?"/":"",entries[i]->d_name);
        if (n<0 || n>=(int)sizeof joined || !media_path(joined,child) || stat(child,&st) || !S_ISREG(st.st_mode)) continue;
        if (comma++) mg_printf_http_chunk(c,",");
        json_string(c,entries[i]->d_name);mg_printf_http_chunk(c,":%lld",(long long)st.st_size);
    }
    mg_printf_http_chunk(c,"}");
    for (int i=0;i<count;i++) free(entries[i]);
    free(entries);
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
    reply(c,200,"{\"ok\":true,\"backup_available\":true,\"restore_available\":true,\"original_download_available\":true,\"jpeg_only\":true,\"max_bytes\":125829120}");
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
    if (!h->body.len || h->body.len>120U*1024U*1024U) { reply(c,400,"{\"ok\":false,\"err\":\"empty or oversized upload\"}");return; }
    int n=mg_get_http_var(&h->query_string,"require_backup",required,sizeof required);
    if ((n!=-1 && (n!=1 || required[0]!='1')) || !gallery_edit_target(h,path,backup) ||
            h->body.len<4 || (unsigned char)h->body.p[0]!=0xff || (unsigned char)h->body.p[1]!=0xd8 ||
            (unsigned char)h->body.p[h->body.len-2]!=0xff || (unsigned char)h->body.p[h->body.len-1]!=0xd9) {
        reply(c,400,"{\"ok\":false,\"err\":\"existing JPEG and complete JPEG body required\"}");return;
    }
    int lock=nx_media_edit_lock(media_root);
    if (lock<0) { reply(c,409,"{\"ok\":false,\"err\":\"media editing unavailable or busy\"}");return; }
    if (nx_media_preserve_original(path,backup) || nx_media_replace(path,h->body.p,h->body.len)) {
        close(lock);
        reply(c,500,"{\"ok\":false,\"err\":\"upload failed; original preserved\"}");return;
    }
    close(lock);
    gp_clear();
    char json[160];snprintf(json,sizeof json,"{\"ok\":true,\"backup_available\":true,\"bytes\":%zu,\"message\":\"media updated\"}",h->body.len);reply(c,200,json);
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
    if (event==MG_EV_CLOSE) { ex_closed(c);rp_closed(c);xw_closed(c);return; }
    /* Do not close when an asynchronous request has not produced a response.
     * SEND runs after bytes leave the buffer; Mongoose refills file transfers
     * before invoking this handler, so streaming files also finish normally. */
    if (event==MG_EV_SEND && (c->flags&MG_F_USER_1) && !c->send_mbuf.len) {
        c->flags|=MG_F_SEND_AND_CLOSE;
    }
    if (event!=MG_EV_HTTP_REQUEST) return;
    if (xw_pending(c)) { c->flags|=MG_F_CLOSE_IMMEDIATELY;return; }
    /* One file stream per connection: this Mongoose version overwrites its
     * FILE on a second pipelined request without closing the previous one. */
    if (c->flags&MG_F_USER_2) { c->flags|=MG_F_CLOSE_IMMEDIATELY;return; }
    struct http_message *h=data;
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
    else if (!strcmp(uri,"/api/v1/gallery/upload")) gallery_upload(c,h);
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
    nx_discovery_init(server->sock,get_nx_model_name(),get_nx_model_version());
    fprintf(stderr,"NX_REMOTE_READY model=%s version=%s listen=%s media=%s source=stock-LCD-and-X11\n",get_nx_model_name(),get_nx_model_version(),listen,media_root);
    custom_key_init();
    menu_key_init();
    input_alive=now_ms();
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
    usb_storage_shutdown();custom_key_shutdown();menu_key_shutdown();ex_release();if (rp_task.active) rp_release();qs_shutdown();gp_clear();mg_mgr_free(&manager);xw_shutdown();return 0;
}
