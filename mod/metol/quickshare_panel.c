/* Native selected-files Quick Share page. HTTP stays on camera loopback;
 * the existing service owns discovery and transfer, including cancellation. */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <strings.h>
#include <dirent.h>
#include <signal.h>
#include <fcntl.h>
#include <unistd.h>
#include <limits.h>
#include <errno.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/resource.h>
#include <time.h>
#include <X11/Xlib.h>
#include <X11/keysym.h>
#define NK_INCLUDE_FIXED_TYPES
#define NK_INCLUDE_STANDARD_IO
#define NK_INCLUDE_STANDARD_VARARGS
#define NK_INCLUDE_DEFAULT_ALLOCATOR
#define NK_IMPLEMENTATION
#include "vendor/nuklear.h"
#define NK_XLIB_USE_XFT
#define NK_XLIB_IMPLEMENTATION
#include "vendor/nuklear_xlib.h"
#include "mongoose.h"
#include "quickshare_json.h"
#include "panel_input.h"

static struct mg_mgr manager;
static struct mg_connection *request;
static const char *error_message;
#define SHARE_FILES 16
#define SHARE_PATH 1024
static char action[16],operation[16],relative[SHARE_FILES][SHARE_PATH],label[256],status[256]="正在连接分享服务…",phase[80],pin[32],client_token[64];
static unsigned file_count;
static char media_root[PATH_MAX],selected[SHARE_FILES][SHARE_PATH],format_error[256];
static unsigned selected_count;
enum file_format { ORIGINAL, JPEG, RAW, JPEG_RAW };
static enum file_format file_format;
static const char *format_names[]={"原文件","JPEG","RAW","JPEG+RAW"};
static unsigned task,owned,sequence;
static int running,available,started,closing,quit,send_after_cancel,dirty=1,device_count,chosen;
static struct nx_panel_input panel_input = {.ev_fd = -1};
static int pending_launch,cancel_sent,target_lost,scroll_selection=1;
static int queued_send,queued_ready;
static int request_replied;
static int media_lock=-1,media_pause_seen;
static char media_pause_marker[PATH_MAX];
static unsigned launch_sequence;
static long long requested_at,poll_at,close_at,reconcile_at;
static unsigned long long sent,total;
static volatile sig_atomic_t interrupted;
static struct receiver { char endpoint[80],name[256]; } devices[32],target;
static XFont *ui_title,*ui_caption;
static long long now(void) { struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return (long long)t.tv_sec*1000+t.tv_nsec/1000000; }
static void stop(int sig) { (void)sig;interrupted=1; }
static void message(const char *s) { snprintf(status,sizeof status,"%s",s);dirty=1; }
static void call(const char *op,const char *body);
static void drop_queued_send(void) {if (queued_send || queued_ready) dirty=1;queued_send=queued_ready=0;}
static void cancel(void) { char b[64];snprintf(b,sizeof b,"task_id=%u",owned);cancel_sent=1;call("cancel",b); }
static int can_choose(void) { return !error_message && !closing && !queued_send && !pending_launch && !send_after_cancel && (!running || (owned==task && !strcmp(operation,"discover"))); }
static void choose(int index) { if (can_choose() && device_count) {chosen=(index+device_count)%device_count;scroll_selection=dirty=1;} }
static void begin_launch(void) {
    snprintf(client_token,sizeof client_token,"%ld-%lld-%u",(long)getpid(),now(),++launch_sequence);
    pending_launch=1;reconcile_at=0;cancel_sent=0;
}
static void close_panel(void) {
    if (!closing) {drop_queued_send();closing=1;close_at=0;send_after_cancel=0;cancel_sent=0;}
    nx_panel_input_hide(&panel_input);
}
static int media_pause_requested(void) {
    struct stat st;
    if (!media_pause_marker[0]) return 0;
    if (!lstat(media_pause_marker,&st) || errno!=ENOENT) media_pause_seen=1;
    return media_pause_seen;
}
static int lock_media(void) {
    const char *state=getenv("NX_STATE_DIR");char path[PATH_MAX];struct stat st;
    /* Standalone deployments without RAM pause support keep their API. The
     * launcher exports its state directory so managed panels always join it. */
    if (!state) return 1;
    if (!*state || snprintf(path,sizeof path,"%s/media-io.lock",state)>=(int)sizeof path ||
        snprintf(media_pause_marker,sizeof media_pause_marker,"%s/media-paused",state)>=(int)sizeof media_pause_marker) return 0;
    media_lock=open(path,O_RDWR|O_CREAT|O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC,0600);
    if (media_lock<0 || fstat(media_lock,&st) || !S_ISREG(st.st_mode) ||
        flock(media_lock,LOCK_SH|LOCK_NB) || media_pause_requested()) {
        if (media_lock>=0) close(media_lock);media_lock=-1;return 0;
    }
    return 1;
}
static void encode(const char *in,char *out) {
    const char *hex="0123456789ABCDEF";
    for (;*in;in++) { unsigned c=(unsigned char)*in;
        if ((c>='a'&&c<='z')||(c>='A'&&c<='Z')||(c>='0'&&c<='9')||strchr("-_.~",c)) *out++=(char)c;
        else {*out++='%';*out++=hex[c>>4];*out++=hex[c&15];}
    }*out=0;
}
static int format_failed(const char *text) {snprintf(format_error,sizeof format_error,"%s",text);message(text);return 0;}
static int append_file(const char *path,char files[][SHARE_PATH],struct stat *identities,unsigned *count) {
    char canonical[PATH_MAX];struct stat st;size_t prefix=strlen(media_root);
    if (!realpath(path,canonical) || stat(canonical,&st) || !S_ISREG(st.st_mode) || access(canonical,R_OK) ||
        strncmp(canonical,media_root,prefix) || canonical[prefix]!='/' || strlen(canonical+prefix+1)>=SHARE_PATH)
        return format_failed("文件不可用，请重新选择");
    for (unsigned i=0;i<*count;i++)
        if (!strcmp(files[i],canonical+prefix+1) || (identities[i].st_dev==st.st_dev && identities[i].st_ino==st.st_ino)) return 1;
    if (*count==SHARE_FILES) return format_failed("实际文件超过16个，请减少勾选或更改格式");
    strcpy(files[*count],canonical+prefix+1);identities[*count]=st;(*count)++;return 1;
}
static int paired_file(const char *source,const char *extension,char *path) {
    char directory[PATH_MAX];const char *base=strrchr(source,'/');base=base?base+1:source;
    const char *dot=strrchr(base,'.');
    if (!dot || (strcasecmp(dot,".JPG") && strcasecmp(dot,".SRW")))
        return format_failed("所选项目不支持 JPEG/RAW 格式");
    size_t stem=(size_t)(dot-base),parent=(size_t)(base-source);
    if (snprintf(directory,sizeof directory,"%s/%.*s",media_root,(int)parent,source)>=(int)sizeof directory)
        return format_failed("文件路径过长，请重新选择");
    DIR *folder=opendir(directory);if (!folder) return format_failed("文件不可用，请重新选择");
    struct dirent *entry;struct stat identity;int found=0,ok=1;
    while ((entry=readdir(folder))) {
        if (strlen(entry->d_name)!=stem+4 || strncmp(entry->d_name,base,stem) || strcasecmp(entry->d_name+stem,extension)) continue;
        char candidate[PATH_MAX];struct stat st;
        if (snprintf(candidate,sizeof candidate,"%s%s",directory,entry->d_name)>=(int)sizeof candidate || stat(candidate,&st)) {ok=0;break;}
        if (found && (identity.st_dev!=st.st_dev || identity.st_ino!=st.st_ino)) {ok=-1;break;}
        if (!found) {strcpy(path,candidate);identity=st;found=1;}
    }
    closedir(folder);
    if (ok<0) return format_failed("同名格式不唯一，请重新选择");
    if (!ok) return format_failed("配对文件不可用，请重新选择");
    if (!found) return format_failed(!strcmp(extension,".JPG")?"部分项目缺少 JPEG，请更改格式":"部分项目缺少 RAW，请更改格式");
    return 1;
}
static void update_label(void) {
    if (!file_count) {label[0]=0;return;}
    const char *first=strrchr(relative[0],'/');first=first?first+1:relative[0];
    if (file_count==1) snprintf(label,sizeof label,"%s",first);
    else {
        const char *second=strrchr(relative[1],'/');second=second?second+1:relative[1];
        snprintf(label,sizeof label,"%.100s / %.100s%s",first,second,file_count>2?" / …":"");
    }
}
static int prepare_files(void) {
    if (media_pause_requested()) {close_panel();return 0;}
    char files[SHARE_FILES][SHARE_PATH],path[PATH_MAX];struct stat identities[SHARE_FILES];unsigned count=0;
    /* Always rebuild from the initial selection, never a previous conversion. */
    for (unsigned f=0;f<selected_count;f++) {
        if (file_format==ORIGINAL) {
            if (snprintf(path,sizeof path,"%s/%s",media_root,selected[f])>=(int)sizeof path || !append_file(path,files,identities,&count)) goto failed;
        } else {
            if ((file_format==JPEG || file_format==JPEG_RAW) && (!paired_file(selected[f],".JPG",path) || !append_file(path,files,identities,&count))) goto failed;
            if ((file_format==RAW || file_format==JPEG_RAW) && (!paired_file(selected[f],".SRW",path) || !append_file(path,files,identities,&count))) goto failed;
        }
    }
    memcpy(relative,files,count*sizeof files[0]);file_count=count;update_label();format_error[0]=0;dirty=1;return 1;
failed:
    file_count=0;update_label();dirty=1;return 0;
}
static void choose_format(enum file_format value) {
    if (value==file_format || !can_choose()) return;
    file_format=value;
    phase[0]=pin[0]=0;sent=total=0;
    /* A completed task belongs to the old attachment list. Discovery remains owned. */
    if (!running) {owned=0;sequence=0;}
    if (prepare_files()) message("格式已选择，请选择设备后发送");
}
static void send_file(void) {
    int i;for (i=0;i<device_count;i++) if (!strcmp(devices[i].endpoint,target.endpoint)) break;
    if (i==device_count) {target_lost=1;message("所选设备已离线，请重新选择");return;}
    char path[SHARE_PATH*3],name[768],body[65536];
    begin_launch();encode(target.name,name);
    size_t used=(size_t)snprintf(body,sizeof body,"path_count=%u&name=%s&endpoint=%s&client_token=%s",file_count,name,target.endpoint,client_token);
    for (unsigned f=0;f<file_count;f++) {
        encode(relative[f],path);
        used+=(size_t)snprintf(body+used,sizeof body-used,"&path%u=%s",f,path);
    }
    sent=total=0;pin[0]=phase[0]=0;call("send",body);
}
static void request_send(void) {
    if ((request && strcmp(action,"status")) || !available || !device_count || !can_choose()) return;
    if (selected_count && !prepare_files()) return;
    target=devices[chosen];target_lost=0;
    if (request) {queued_send=1;queued_ready=0;message("正在确认分享状态…");return;}
    if (running) {send_after_cancel=1;cancel();}else send_file();
}
static void event_line(const char *line) {
    struct json j;if (!parse(&j,line,strlen(line))) return;
    int type=field(&j,0,"event");
    if (eq(&j,type,"discovered")) {
        int e=field(&j,0,"endpoint");char ip[64],name[256],address[80];
        if (!string(&j,field(&j,e,"ip"),ip,sizeof ip) || !string(&j,field(&j,e,"name"),name,sizeof name)) return;
        struct in_addr ipv4;if (inet_pton(AF_INET,ip,&ipv4)!=1) return;
        unsigned port=(unsigned)number(&j,field(&j,e,"port"));if (!port || port>65535) return;
        snprintf(address,sizeof address,"%s:%u",ip,port);
        int i;for (i=0;i<device_count;i++) if (!strcmp(devices[i].endpoint,address)) break;
        if (eq(&j,field(&j,e,"present"),"false")) {
            if (i<device_count) {
                if (send_after_cancel && !strcmp(target.endpoint,address)) {send_after_cancel=0;target_lost=1;}
                if (queued_send && !strcmp(target.endpoint,address)) {drop_queued_send();target_lost=1;}
                memmove(devices+i,devices+i+1,(device_count-i-1)*sizeof devices[0]);device_count--;
                if (chosen>i) chosen--;else if (chosen>=device_count) chosen=device_count?device_count-1:0;
                scroll_selection=1;
            }
        } else if (i<32) {
            strcpy(devices[i].endpoint,address);strcpy(devices[i].name,name);if (i==device_count) device_count++;
        }
    } else if (eq(&j,type,"transfer")) {
        int m=field(&j,0,"message"),meta=field(&j,m,"meta");char value[80];
        if (string(&j,field(&j,m,"state"),value,sizeof value)) strcpy(phase,value);
        if (string(&j,field(&j,meta,"pin_code"),value,sizeof value)) snprintf(pin,sizeof pin,"%.20s",value);
        int s=field(&j,meta,"sent_bytes"),t=field(&j,meta,"total_bytes");
        if (s>=0) sent=number(&j,s);if (t>=0) total=number(&j,t);
    }
}
static int valid_status_reply(struct json *j) {
    int a=field(j,0,"available"),r=field(j,0,"running"),id=field(j,0,"task_id");
    if (j->t[0].type!=JSMN_OBJECT || next(j,0)!=j->n || a<0 || r<0 || id<0 ||
        j->t[a].type!=JSMN_PRIMITIVE || j->t[r].type!=JSMN_PRIMITIVE || j->t[id].type!=JSMN_PRIMITIVE ||
        (!eq(j,a,"true") && !eq(j,a,"false")) || (!eq(j,r,"true") && !eq(j,r,"false"))) return 0;
    int length=j->t[id].end-j->t[id].start;if (length<=0 || length>10) return 0;
    for (int i=j->t[id].start;i<j->t[id].end;i++) if (j->s[i]<'0' || j->s[i]>'9') return 0;
    char op[sizeof operation],token[sizeof client_token];
    if (number(j,id)>UINT_MAX || !string(j,field(j,0,"operation"),op,sizeof op) ||
        !string(j,field(j,0,"client_token"),token,sizeof token)) return 0;
    return !eq(j,r,"true") || (number(j,id)>0 && (!strcmp(op,"discover") || !strcmp(op,"send")));
}
static void failed_reply(const char *text) {
    drop_queued_send();
    if (!strcmp(action,"cancel")) {cancel_sent=0;send_after_cancel=0;}
    message(text);
}
static void accept_status(struct http_message *h) {
    struct json j;if (!parse(&j,h->body.p,h->body.len)) {failed_reply("服务返回了无效数据");return;}
    if (h->resp_code!=200 && h->resp_code!=202) {
        char error[200];if (string(&j,field(&j,0,"error"),error,sizeof error)) fprintf(stderr,"Quick Share HTTP %d: %s\n",h->resp_code,error);
        failed_reply(h->resp_code==409?"另一个分享任务正在运行":"分享请求失败，请返回后重试");send_after_cancel=0;
        if (!strcmp(action,"discover") || !strcmp(action,"send")) pending_launch=0;
        return;
    }
    if (!valid_status_reply(&j) || (queued_send && strcmp(action,"status"))) {failed_reply("无法确认分享状态，请重试");return;}
    unsigned id=(unsigned)number(&j,field(&j,0,"task_id"));
    if (pending_launch) {
        /* A task number alone cannot identify a launch whose reply was lost. */
        if (eq(&j,field(&j,0,"client_token"),client_token)) {owned=id;sequence=0;cancel_sent=0;}
        else owned=0;
        pending_launch=0;reconcile_at=0;
    }
    task=id;running=eq(&j,field(&j,0,"running"),"true");available=eq(&j,field(&j,0,"available"),"true");
    if (!string(&j,field(&j,0,"operation"),operation,sizeof operation)) operation[0]=0;
    if (owned && (id!=owned || !eq(&j,field(&j,0,"client_token"),client_token))) {
        owned=0;phase[0]=pin[0]=0;send_after_cancel=0;
    }
    if (!strcmp(action,"cancel")) cancel_sent=owned && id==owned && eq(&j,field(&j,0,"cancel_requested"),"true");
    if (id==owned && owned) {
        int array=field(&j,0,"events");
        if (array>=0) for (int i=array+1;i<j.n && j.t[i].start<j.t[array].end;i=next(&j,i)) {
            unsigned n=(unsigned)number(&j,field(&j,i,"sequence"));char line[8192];
            if (n>sequence && string(&j,field(&j,i,"line"),line,sizeof line)) {sequence=n;event_line(line);}
        }
    }
    if (queued_send) {
        if (!available || (running && (!owned || id!=owned || strcmp(operation,"discover")))) drop_queued_send();
        else queued_ready=1;
    }
    if (format_error[0]) message(format_error);
    else if (!available) message("Quick Share 尚未安装");
    else if (running && id!=owned) message("其他页面正在使用 Quick Share");
    else if (target_lost) message("所选设备已离线，请重新选择");
    else if (running && eq(&j,field(&j,0,"cancel_requested"),"true")) message("正在取消…");
    else if (running && !strcmp(operation,"discover")) message("请打开接收设备的 Quick Share，保持屏幕亮起");
    else if (running) {
        if (!strcmp(phase,"AwaitingReceiverAck") || (total && sent>=total)) message("文件已发出，正在等待接收端确认完成…");
        else if (!strcmp(phase,"SendingFiles")) message("正在发送…");
        else message("请在接收设备上接受分享，并核对验证码");
    } else if (owned && eq(&j,field(&j,0,"state"),"cancelled")) message("任务已取消，可重新分享或返回相册");
    else if (owned && !strcmp(phase,"Finished") && eq(&j,field(&j,0,"exit_code"),"0")) message("接收端已确认完成");
    else if (owned && eq(&j,field(&j,0,"operation"),"send")) message("传输未完成，可重试或返回相册");
    else if (device_count) message("选择接收设备，再点发送");
    else message("未找到设备，请确认双方已连接同一 Wi-Fi");
    dirty=1;
}
static void http(struct mg_connection *c,int event,void *data) {
    if (event==MG_EV_HTTP_REPLY) {request_replied=1;accept_status(data);c->flags|=MG_F_CLOSE_IMMEDIATELY;}
    else if (event==MG_EV_CONNECT && *(int *)data) {failed_reply("无法连接机身分享服务");}
    else if (event==MG_EV_CLOSE && request==c) {
        if (!request_replied) failed_reply("无法连接机身分享服务");
        if (queued_send && !queued_ready) {drop_queued_send();message("无法确认分享状态，请重试");}
        request=NULL;poll_at=now()+500;
        if (pending_launch && !reconcile_at) reconcile_at=now();
    }
}
static void call(const char *op,const char *body) {
    if (error_message || request) return;
    char url[128];snprintf(url,sizeof url,"http://127.0.0.1:8781/api/v1/quickshare/%s",op);
    snprintf(action,sizeof action,"%s",op);requested_at=now();request_replied=0;
    request=mg_connect_http(&manager,http,url,"Content-Type: application/x-www-form-urlencoded\r\n",body);
    if (!request) {failed_reply("无法连接机身分享服务");if (pending_launch && !reconcile_at) reconcile_at=now();}
    dirty=1;
}
static void scan(void) {
    char body[100];device_count=chosen=0;target_lost=0;scroll_selection=1;phase[0]=pin[0]=0;
    begin_launch();snprintf(body,sizeof body,"duration=15&client_token=%s",client_token);call("discover",body);
}
static void idle(long long tick) {
    if (error_message) {if (closing) quit=1;return;}
    if (request) return;
    if (closing) {
        if (pending_launch) {
            if (reconcile_at && tick-reconcile_at>=10000) {fprintf(stderr,"Quick Share launch status unresolved; no foreign task cancelled\n");quit=1;}
            else if (tick>=poll_at) call("status",NULL);
        } else if (running && owned && owned==task) {
            /* Begin the wait after the first cancel, even if launch took seconds. */
            if (!close_at) {close_at=tick;if (!cancel_sent) cancel();}
            else if (tick-close_at>=4000) {fprintf(stderr,"Quick Share cancellation not confirmed for task %u\n",owned);quit=1;}
            else if (tick>=poll_at) {if (!cancel_sent) cancel();else call("status",NULL);}
        } else quit=1;
    } else if (pending_launch) {if (tick>=poll_at) call("status",NULL);}
    else if (queued_send) {
        int confirmed=queued_ready;drop_queued_send();
        if (!confirmed || !available || (running && (!owned || task!=owned || strcmp(operation,"discover")))) message("无法确认分享状态，请重试");
        else if (running) {send_after_cancel=1;cancel();}
        else send_file();
    }
    else if (!started && available && !running) {started=1;scan();}
    else if (send_after_cancel && !running) {send_after_cancel=0;send_file();}
    else if (tick>=poll_at) call("status",NULL);
}
static struct nk_color ui_ink(void) {return nk_rgb(31,31,33);}
static struct nk_color ui_muted(void) {return nk_rgb(108,108,114);}
static struct nk_color ui_blue(void) {return nk_rgb(38,93,196);}
static void ui_text(struct nk_command_buffer *canvas,struct nk_rect box,const char *text,XFont *font,struct nk_color color) {
    char shortened[768];size_t length=strlen(text);
    if (font->handle.width(font->handle.userdata,font->handle.height,text,(int)length)>box.w) {
        if (length>sizeof shortened-4) length=sizeof shortened-4;
        while (length && ((unsigned char)text[length]&0xc0)==0x80) length--;
        memcpy(shortened,text,length);memcpy(shortened+length,"…",4);
        while (length && font->handle.width(font->handle.userdata,font->handle.height,shortened,(int)length+3)>box.w) {
            do {length--;} while (length && ((unsigned char)shortened[length]&0xc0)==0x80);
            memcpy(shortened+length,"…",4);
        }
        text=shortened;
    }
    box.y+=(box.h-font->handle.height)/2;
    nk_draw_text(canvas,box,text,(int)strlen(text),&font->handle,nk_rgba(0,0,0,0),color);
}
static void ui_position(struct nk_context *ctx,float x,float y,float w,float h) {
    nk_layout_space_push(ctx,nk_rect(x,y,w,h));
}
static int ui_button(struct nk_context *ctx,struct nk_rect box,const char *text,int enabled,int primary,int selected) {
    struct nk_style_button saved=ctx->style.button;
    struct nk_color background=primary?ui_blue():selected?nk_rgb(230,235,246):nk_rgb(252,251,254);
    struct nk_color foreground=primary?nk_rgb(255,255,255):selected?ui_blue():ui_ink();
    if (!enabled) {background=selected?nk_rgb(225,230,240):nk_rgb(232,232,236);foreground=selected?nk_rgb(113,130,163):nk_rgb(158,158,165);}
    ctx->style.button.normal=nk_style_item_color(background);
    ctx->style.button.hover=nk_style_item_color(background);
    ctx->style.button.active=nk_style_item_color(enabled?(primary?nk_rgb(26,72,165):nk_rgb(213,222,242)):background);
    ctx->style.button.text_normal=foreground;ctx->style.button.text_hover=foreground;ctx->style.button.text_active=foreground;
    ctx->style.button.border=0;ctx->style.button.rounding=box.h/2;
    ui_position(ctx,box.x,box.y,box.w,box.h);
    int clicked=nk_button_label(ctx,text);
    ctx->style.button=saved;
    return enabled && clicked;
}
static void ui_phone(struct nk_command_buffer *canvas,float x,float y,int selected) {
    struct nk_color color=selected?ui_blue():ui_muted();
    nk_fill_circle(canvas,nk_rect(x,y,36,36),selected?nk_rgb(221,230,249):nk_rgb(238,238,242));
    nk_stroke_rect(canvas,nk_rect(x+12,y+7,12,22),3,1.5f,color);
    nk_stroke_line(canvas,x+16,y+25,x+20,y+25,1.5f,color);
}
static void ui_devices(struct nk_context *ctx,XFont *font) {
    if (scroll_selection) {
        nk_group_set_scroll(ctx,"devices",0,(nk_uint)(chosen/2)*60);
        scroll_selection=0;
    }
    ui_position(ctx,24,250,712,116);
    if (nk_group_begin(ctx,"devices",0)) {
        struct nk_command_buffer *canvas=nk_window_get_canvas(ctx);
        nk_layout_row_dynamic(ctx,52,2);
        for (int i=0;i<device_count;i++) {
            struct nk_rect box=nk_widget_bounds(ctx);
            int selected=i==chosen;
            struct nk_style_button saved=ctx->style.button;
            struct nk_color fill=selected?nk_rgb(230,235,246):nk_rgb(252,251,254);
            ctx->style.button.normal=ctx->style.button.hover=nk_style_item_color(fill);
            ctx->style.button.active=nk_style_item_color(nk_rgb(213,222,242));
            ctx->style.button.rounding=18;ctx->style.button.border=0;
            if (nk_button_label(ctx,"") && can_choose()) choose(i);
            ctx->style.button=saved;
            ui_phone(canvas,box.x+10,box.y+8,selected);
            ui_text(canvas,nk_rect(box.x+58,box.y+3,box.w-72,25),devices[i].name,font,selected?ui_blue():ui_ink());
            ui_text(canvas,nk_rect(box.x+58,box.y+28,box.w-72,20),selected?"已选择":"点按选择",ui_caption,ui_muted());
        }
        nk_group_end(ctx);
    }
}
static void ui_transfer(struct nk_context *ctx,XFont *font) {
    struct nk_command_buffer *canvas=nk_window_get_canvas(ctx);
    int preparing=queued_send || send_after_cancel || pending_launch;
    int awaiting=!preparing && (!strcmp(phase,"AwaitingReceiverAck") || (total && sent>=total));
    int sending=!preparing && !strcmp(phase,"SendingFiles");
    int show_pin=pin[0] && !preparing && !awaiting && !sending;
    nk_fill_rect(canvas,nk_rect(24,250,712,116),24,nk_rgb(252,251,254));
    ui_phone(canvas,42,266,1);
    ui_text(canvas,nk_rect(92,260,420,28),target.name[0]?target.name:"接收设备",font,ui_ink());
    ui_text(canvas,nk_rect(92,290,420,24),preparing?"正在准备发送":awaiting?"请保持接收设备在线":sending?"正在向此设备发送文件":show_pin?"请确认两端验证码相同":"正在建立连接",ui_caption,ui_muted());
    if (show_pin) {
        ui_text(canvas,nk_rect(554,258,156,20),"验证码",ui_caption,ui_muted());
        ui_text(canvas,nk_rect(554,280,156,34),pin,ui_title,ui_blue());
    }
    if (total && !preparing) {
        char progress[96];
        double ratio=sent>=total?1.0:(double)sent/(double)total;
        snprintf(progress,sizeof progress,"已发送 %.0f%%",ratio*100.0);
        ui_text(canvas,nk_rect(44,327,140,22),progress,ui_caption,ui_muted());
        nk_fill_rect(canvas,nk_rect(190,336,520,5),2.5f,nk_rgb(228,230,235));
        if (ratio>0) nk_fill_rect(canvas,nk_rect(190,336,(float)(520*ratio),5),2.5f,ui_blue());
    }
}
static void ui_page(struct nk_context *ctx,XFont *font) {
    struct nk_command_buffer *canvas=nk_window_get_canvas(ctx);
    const struct nk_color card=nk_rgb(252,251,254);
    nk_layout_space_begin(ctx,NK_STATIC,440,32);
    nk_fill_circle(canvas,nk_rect(24,18,42,42),ui_blue());
    nk_stroke_line(canvas,37,39,51,31,2,nk_rgb(255,255,255));
    nk_stroke_line(canvas,37,39,51,47,2,nk_rgb(255,255,255));
    nk_fill_circle(canvas,nk_rect(32,35,8,8),nk_rgb(255,255,255));
    nk_fill_circle(canvas,nk_rect(48,27,8,8),nk_rgb(255,255,255));
    nk_fill_circle(canvas,nk_rect(48,43,8,8),nk_rgb(255,255,255));
    ui_text(canvas,nk_rect(82,18,400,42),"Quick Share",ui_title,ui_ink());
    if (error_message) {
        nk_fill_rect(canvas,nk_rect(24,88,712,228),24,card);
        ui_text(canvas,nk_rect(48,112,664,40),"无法分享所选文件",ui_title,ui_ink());
        ui_text(canvas,nk_rect(48,168,664,40),error_message,font,ui_muted());
        if (ui_button(ctx,nk_rect(552,382,184,42),"返回相册",1,1,0)) close_panel();
        nk_layout_space_end(ctx);return;
    }
    char count[80];snprintf(count,sizeof count,"%u 项 · %u 个文件",selected_count,file_count);
    ui_text(canvas,nk_rect(528,24,208,28),count,ui_caption,ui_muted());
    nk_fill_rect(canvas,nk_rect(24,72,712,106),24,card);
    nk_stroke_rect(canvas,nk_rect(46,92,27,34),4,1.5f,ui_muted());
    nk_stroke_line(canvas,52,102,67,102,1.5f,ui_muted());
    nk_stroke_line(canvas,52,109,67,109,1.5f,ui_muted());
    nk_stroke_line(canvas,52,116,62,116,1.5f,ui_muted());
    ui_text(canvas,nk_rect(92,83,620,30),label[0]?label:"所选文件",font,ui_ink());
    for (int i=0;i<4;i++)
        if (ui_button(ctx,nk_rect(92+i*155,127,145,34),format_names[i],can_choose(),0,file_format==i)) choose_format((enum file_format)i);
    int previous_transfer=owned && !strcmp(operation,"send");
    int transfer=(previous_transfer && running) || queued_send || send_after_cancel || (pending_launch && !strcmp(action,"send"));
    int confirmed=!running && owned && !strcmp(phase,"Finished") && !strcmp(status,"接收端已确认完成");
    const char *heading="附近的设备";
    if (format_error[0]) heading="请检查所选文件";
    else if (queued_send || send_after_cancel) heading="正在准备发送";
    else if (running && owned!=task) heading="分享正在使用中";
    else if (running && cancel_sent) heading="正在取消分享";
    else if (running && !strcmp(operation,"send")) {
        if (!strcmp(phase,"AwaitingReceiverAck") || (total && sent>=total)) heading="等待接收端确认";
        else if (!strcmp(phase,"SendingFiles")) heading="正在发送";
        else heading=pin[0]?"核对验证码":"正在连接设备";
    } else if (confirmed) heading="分享已完成";
    else if (previous_transfer && !running && !pending_launch) heading="分享已结束";
    else if (device_count) heading="选择接收设备";
    ui_text(canvas,nk_rect(28,185,704,34),heading,ui_title,ui_ink());
    ui_text(canvas,nk_rect(28,219,704,25),status,ui_caption,ui_muted());
    if (transfer) ui_transfer(ctx,font);
    else if (device_count) ui_devices(ctx,font);
    else {
        int discovering=running && owned==task && !strcmp(operation,"discover");
        nk_fill_rect(canvas,nk_rect(24,250,712,116),24,card);
        ui_phone(canvas,46,284,0);
        ui_text(canvas,nk_rect(104,271,606,30),discovering?"正在查找附近的设备…":"没有可接收的设备",font,ui_ink());
        ui_text(canvas,nk_rect(104,307,606,24),discovering?"设备出现后，点按名称即可选择":"打开接收页面后，点按「重新查找」",ui_caption,ui_muted());
    }
    if (ui_button(ctx,nk_rect(24,382,160,42),"返回相册",1,0,0)) close_panel();
    if (ui_button(ctx,nk_rect(200,382,160,42),"重新查找",!request && !queued_send && !pending_launch && !running && available,0,0)) scan();
    if (running && owned && owned==task)
        if (ui_button(ctx,nk_rect(376,382,160,42),"取消",!request && !cancel_sent,0,0)) {send_after_cancel=0;cancel();}
    if (ui_button(ctx,nk_rect(552,382,184,42),"发送",available && device_count && can_choose() && (!request || !strcmp(action,"status")),1,0)) request_send();
    nk_layout_space_end(ctx);
}
int main(int argc,char **argv) {
    if (argc>1 && !strcmp(argv[1],"--error")) {
        if (argc!=3) return 2;
        if (!strcmp(argv[2],"selection-limit")) error_message="最多16项，请减少勾选";
        else if (!strcmp(argv[2],"selection-unavailable")) error_message="文件不可用，请重新选择";
        else if (!strcmp(argv[2],"launch-failed")) error_message="无法打开分享，请重试";
        else return 2;
    } else {
    if (argc<2 || argc>SHARE_FILES+1) return 2;
    if (!lock_media()) return 2;
    char absolute[PATH_MAX],root[PATH_MAX];struct stat identities[SHARE_FILES];
    const char *media=getenv("NX_MEDIA_ROOT");if (!media) media="/mnt/mmc/DCIM";
    if (!realpath(media,root)) return 2;
    strcpy(media_root,root);
    size_t prefix=strlen(root);
    file_count=(unsigned)argc-1;
    for (unsigned f=0;f<file_count;f++) {
        struct stat *st=&identities[f];
        if (!realpath(argv[f+1],absolute) || stat(absolute,st) || !S_ISREG(st->st_mode) || access(absolute,R_OK)) return 2;
        if (strncmp(absolute,root,prefix) || absolute[prefix]!='/' || strlen(absolute+prefix+1)>=SHARE_PATH) return 2;
        for (unsigned j=0;j<f;j++) if (st->st_dev==identities[j].st_dev && st->st_ino==identities[j].st_ino) return 2;
        strcpy(relative[f],absolute+prefix+1);
        strcpy(selected[f],relative[f]);
    }
    selected_count=file_count;
    update_label();
    }
    int lock=open("/tmp/nxwa-nxui.lock",O_RDWR|O_CREAT,0600);if (lock<0 || flock(lock,LOCK_EX|LOCK_NB)) return 3;
    signal(SIGTERM,stop);signal(SIGINT,stop);signal(SIGPIPE,SIG_IGN);setpriority(PRIO_PROCESS,0,19);
    Display *d=XOpenDisplay(NULL);if (!d) return 4;
    int screen=DefaultScreen(d);
    const int width=760,height=440;
    if (DisplayWidth(d,screen)<height || DisplayHeight(d,screen)<width) return 4;
    XSetWindowAttributes a;memset(&a,0,sizeof a);
    a.override_redirect=True;a.event_mask=ExposureMask|KeyPressMask|KeyReleaseMask|ButtonPressMask|ButtonReleaseMask;
    Window w=XCreateWindow(d,DefaultRootWindow(d),(DisplayWidth(d,screen)-height)/2,(DisplayHeight(d,screen)-width)/2,height,width,0,DefaultDepth(d,screen),InputOutput,DefaultVisual(d,screen),CWOverrideRedirect|CWEventMask,&a);
    XStoreName(d,w,"NX300M Quick Share");
    XFont *font=nk_xfont_create(d,"SDIC_GP_US:pixelsize=20");if (!font || !font->ft) return 5;
    struct nk_context *ctx=nk_xlib_init(font,d,screen,w,DefaultVisual(d,screen),DefaultColormap(d,screen),width,height);
    if (!ctx) return 5;
    ui_title=nk_xfont_create(d,"SDIC_GP_US:pixelsize=28:weight=bold");
    ui_caption=nk_xfont_create(d,"SDIC_GP_US:pixelsize=16");
    if (!ui_title || !ui_title->ft) {free(ui_title);ui_title=font;}
    if (!ui_caption || !ui_caption->ft) {free(ui_caption);ui_caption=font;}
    XFont *extra_fonts[]={ui_title,ui_caption};
    for (unsigned i=0;i<2;i++) {
        extra_fonts[i]->handle.userdata=nk_handle_ptr(extra_fonts[i]);
        extra_fonts[i]->handle.height=(float)extra_fonts[i]->height;
        extra_fonts[i]->handle.width=nk_xfont_get_text_width;
    }
    ctx->style.window.padding=nk_vec2(0,0);ctx->style.window.group_padding=nk_vec2(0,0);
    ctx->style.window.spacing=nk_vec2(12,8);ctx->style.window.border=0;
    ctx->style.window.background=nk_rgb(242,242,242);
    ctx->style.window.fixed_background=nk_style_item_color(nk_rgb(242,242,242));
    ctx->style.window.scrollbar_size=nk_vec2(4,4);
    ctx->style.scrollv.border=0;ctx->style.scrollv.rounding=2;ctx->style.scrollv.rounding_cursor=2;
    ctx->style.scrollv.normal=nk_style_item_color(nk_rgb(242,242,242));
    ctx->style.scrollv.cursor_normal=nk_style_item_color(nk_rgb(193,195,201));
    nx_panel_input_init(&panel_input,d,w);
    XSetGraphicsExposures(d,xlib.surf->gc,False);XMapRaised(d,w);XSync(d,False);XSetInputFocus(d,w,RevertToPointerRoot,CurrentTime);
    nx_panel_input_open_evdev(&panel_input);
    if (!error_message) {mg_mgr_init(&manager,NULL);call("status",NULL);}
    long long frame=0;
    while (!quit) {
        long long tick=now();
        if (interrupted) close_panel();
        if (!error_message && media_pause_requested()) close_panel();
        nx_panel_input_read_evdev(&panel_input,close_panel);
        if (!error_message) mg_mgr_poll(&manager,0);
        if (request && tick-requested_at>5000) {drop_queued_send();request->flags|=MG_F_CLOSE_IMMEDIATELY;message("机身分享服务无响应");}
        idle(tick);
        if (closing) nx_panel_input_drain_s1(&panel_input,close_panel);
        if (!closing && tick>=frame) {
            nk_input_begin(ctx);
            for (int n=0;n<64 && !closing && XPending(d);n++) {
                XEvent e;XNextEvent(d,&e);if (e.type==NoExpose) continue;dirty=1;
                if (nx_panel_input_handle_s1(&panel_input,&e,close_panel)) continue;
                /* The physical X window is portrait; the retained surface is
                 * landscape. The upstream resize handler swaps these back. */
                if (e.type==Expose || e.type==ConfigureNotify) continue;
                if (e.type==KeyPress) {
                    KeySym k=XLookupKeysym(&e.xkey,0);
                    if (k==XK_Escape || k==XK_Menu) close_panel();
                    else if (k==XK_Down || k==XK_KP_Down) choose(chosen+1);
                    else if (k==XK_Up || k==XK_KP_Up) choose(chosen-1);
                    else if (k==XK_Right || k==XK_KP_Right) choose_format((enum file_format)((file_format+1)%4));
                    else if (k==XK_Left || k==XK_KP_Left) choose_format((enum file_format)((file_format+3)%4));
                    else if (k==XK_Return || k==XK_KP_Enter) {if (error_message) close_panel();else request_send();}
                }
                if (e.type==ButtonPress || e.type==ButtonRelease) {int x=e.xbutton.x;e.xbutton.x=e.xbutton.y;e.xbutton.y=height-1-x;nk_input_motion(ctx,e.xbutton.x,e.xbutton.y);}
                nk_xlib_handle_event(d,screen,w,&e);
                if (e.type==ButtonPress || e.type==ButtonRelease) break;
            }nk_input_end(ctx);
            if (dirty && !closing) {
                dirty=0;
                if (nk_begin(ctx,"Quick Share",nk_rect(0,0,width,height),NK_WINDOW_NO_SCROLLBAR)) ui_page(ctx,font);
                nk_end(ctx);nk_xlib_render(w,nk_rgb(242,242,242));XFlush(d);
            }frame=now()+100;
        }
        /* A short tap may have queued both edges before the next 10Hz frame.
         * Closing stops normal UI input, but must still forward that release. */
        if (closing) nx_panel_input_drain_s1(&panel_input,close_panel);
        usleep(20000);
    }
    nx_panel_input_hide(&panel_input);
    nx_panel_input_drain_s1(&panel_input,close_panel);
    nx_panel_input_close_evdev(&panel_input);
    if (!error_message) mg_mgr_free(&manager);
    nk_xlib_shutdown();
    if (ui_title!=font) nk_xfont_del(d,ui_title);
    if (ui_caption!=font) nk_xfont_del(d,ui_caption);
    nk_xfont_del(d,font);XDestroyWindow(d,w);XCloseDisplay(d);close(lock);if (media_lock>=0) close(media_lock);return 0;
}
