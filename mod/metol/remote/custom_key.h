#ifndef NX_REMOTE_CUSTOM_KEY_H
#define NX_REMOTE_CUSTOM_KEY_H
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/prctl.h>
#include <sys/wait.h>
#include <unistd.h>
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <X11/keysym.h>
#include "../mod_paths.h"
#include "custom_key_context.h"

/* The parent owns panel launch and its existing lock/resource checks. No X11
 * operation or camera-memory read runs in the HTTP process. */
static void custom_key_launch_panel(long long triggered_at);
extern Bool XkbSetDetectableAutoRepeat(Display *,Bool,Bool *);
#define NX_CUSTOM_KEY_INIT_MS 2000
#define NX_CUSTOM_KEY_HEARTBEAT_MS 1500
enum { CK_STARTING=1,CK_WAIT_RELEASE,CK_READY,CK_HOLDING,CK_FAILED };
struct custom_key_message {
    unsigned int type,state,fallbacks,consumed,cancelled,forwarded,reason;
    unsigned int context_stage,context_errno,context_address;
    long long triggered_at;
};
static struct {
    int enabled,fd,terminating,state,reason;
    pid_t pid;
    long long started,heartbeat,retry;
    unsigned int fallbacks,consumed,cancelled,forwarded,triggers,timeouts,failures;
    unsigned int failure_streak,context_stage,context_errno,context_address;
    char root[192];
} custom_key_service;

static int custom_key_x_error;
static int custom_key_error_handler(Display *display,XErrorEvent *error) {
    (void)display;(void)error;custom_key_x_error=1;return 0;
}
static int custom_key_send(int fd,const struct custom_key_message *message) {
    return send(fd,message,sizeof *message,MSG_DONTWAIT|MSG_NOSIGNAL)==(ssize_t)sizeof *message;
}
static int custom_key_class(Display *display,Window window) {
    XClassHint hint;int match=0;
    if(XGetClassHint(display,window,&hint)) {
        match=(hint.res_name && (!strcmp(hint.res_name,"di-camera-app") || !strcmp(hint.res_name,"di-camera-app-nx300"))) ||
            (hint.res_class && (!strcmp(hint.res_class,"di-camera-app") || !strcmp(hint.res_class,"di-camera-app-nx300")));
        if(hint.res_name)XFree(hint.res_name);if(hint.res_class)XFree(hint.res_class);
    }
    return match;
}
static void custom_key_find(Display *display,Window root,int depth,Window *found,unsigned *count) {
    if(depth>4)return;
    Window returned,parent,*children=NULL;unsigned number=0;
    if(!XQueryTree(display,root,&returned,&parent,&children,&number))return;
    for(unsigned i=0;i<number;i++) {
        XWindowAttributes attributes;
        if(!XGetWindowAttributes(display,children[i],&attributes) || attributes.map_state!=IsViewable)continue;
        if(custom_key_class(display,children[i])){*found=children[i];(*count)++;}
        else custom_key_find(display,children[i],depth+1,found,count);
    }
    if(children)XFree(children);
}
static int custom_key_focus(Display *display,Window window) {
    Window focus;int revert;XGetInputFocus(display,&focus,&revert);
    for(unsigned i=0;i<16;i++) {
        if(focus==window)return 1;
        if(focus==None || focus==PointerRoot || focus==DefaultRootWindow(display))return 0;
        Window root,parent,*children=NULL;unsigned count;
        if(!XQueryTree(display,focus,&root,&parent,&children,&count))return 0;
        if(children)XFree(children);focus=parent;
    }
    return 0;
}
static int custom_key_window_pid(Display *display,Window window,int pid) {
    Atom property=XInternAtom(display,"_NET_WM_PID",True);
    if(property==None)return 1;
    Atom type;int format;unsigned long count,remaining;unsigned char *value=NULL;
    int ok=XGetWindowProperty(display,window,property,0,1,False,AnyPropertyType,
                             &type,&format,&count,&remaining,&value)==Success;
    if(ok && type!=None)ok=format==32 && count==1 && *(unsigned long *)value==(unsigned long)pid;
    if(value)XFree(value);return ok;
}
static int custom_key_grab(Display *display,Window window,KeyCode code) {
    custom_key_x_error=0;
    XGrabKey(display,code,AnyModifier,window,False,GrabModeAsync,GrabModeAsync);
    XSync(display,False);return !custom_key_x_error;
}
struct custom_key_mapping { KeyCode code[3];KeySym level0[3];unsigned int modifiers[3]; };
static int custom_key_mapping_snapshot(Display *display,struct custom_key_mapping *snapshot) {
    const KeySym symbols[3]={XK_KP_Delete,XK_Super_L,XK_Super_R};
    memset(snapshot,0,sizeof *snapshot);
    for(unsigned i=0;i<3;i++) {
        snapshot->code[i]=XKeysymToKeycode(display,symbols[i]);
        if(!snapshot->code[i])return 0;
        for(unsigned j=0;j<i;j++)if(snapshot->code[i]==snapshot->code[j])return 0;
        int width;KeySym *mapping=XGetKeyboardMapping(display,snapshot->code[i],1,&width);
        if(!mapping)return 0;
        if(width>0)snapshot->level0[i]=mapping[0];
        XFree(mapping);if(snapshot->level0[i]!=symbols[i])return 0;
    }
    XModifierKeymap *modifiers=XGetModifierMapping(display);if(!modifiers)return 0;
    for(unsigned i=0;i<3;i++)for(int group=0;group<8;group++)
        for(int key=0;key<modifiers->max_keypermod;key++)
            if(modifiers->modifiermap[group*modifiers->max_keypermod+key]==snapshot->code[i])
                snapshot->modifiers[i]|=1u<<group;
    XFreeModifiermap(modifiers);return !custom_key_x_error;
}
static int custom_key_mapping_unchanged(Display *display,XEvent *event,const struct custom_key_mapping *initial) {
    if(event->xmapping.request==MappingPointer)return 1;
    if(event->xmapping.request!=MappingKeyboard && event->xmapping.request!=MappingModifier)return 0;
    /* Refresh first: XKeysymToKeycode otherwise consults an obsolete cache.
     * Unrelated or repeated notifications must not discard a physical key. */
    XRefreshKeyboardMapping(&event->xmapping);
    struct custom_key_mapping current;
    if(!custom_key_mapping_snapshot(display,&current))return 0;
    for(unsigned i=0;i<3;i++)
        if(current.code[i]!=initial->code[i] || current.level0[i]!=initial->level0[i] ||
            current.modifiers[i]!=initial->modifiers[i])return 0;
    return 1;
}
static int custom_key_forward(Display *display,Window window,XEvent *event) {
    XEvent forwarded=*event;forwarded.xkey.window=window;
    return XSendEvent(display,window,True,event->type==KeyPress?KeyPressMask:KeyReleaseMask,&forwarded)!=0;
}
static int custom_key_shutter_clear(Display *display,KeyCode s1,KeyCode s2) {
    char held[32];XQueryKeymap(display,held);
    return !((unsigned char)held[s1>>3]&(1u<<(s1&7))) &&
        !((unsigned char)held[s2>>3]&(1u<<(s2&7))) && !custom_key_x_error;
}
/* Only physical Custom transactions emit these markers. The existing RAM
 * remote log can then distinguish a state-check stall from a UI launch stall;
 * idle polling never produces a stream of diagnostic writes. */
static void custom_key_trace(const char *stage) {
    fprintf(stderr,"NX_CUSTOM_STAGE pid=%ld stage=%s at_ms=%lld\n",
            (long)getpid(),stage,now_ms());
}
static int custom_key_ready(Display *display,Window window,struct custom_key_context *context,
                            KeyCode s1,KeyCode s2) {
    custom_key_trace("focus-check");
    if(!custom_key_focus(display,window))return 0;
    custom_key_trace("preview-check");
    if(!custom_key_context_preview(context))return 0;
    custom_key_trace("shutter-state-check");
    return custom_key_shutter_clear(display,s1,s2);
}
/* Closing this client's connection releases its passive/active keyboard grab.
 * Do not enter Xlib cleanup here: the request being timed out may be stuck in
 * that same connection. This deadline also works when HTTP dispatch is busy. */
static void custom_key_deadline(int signal_number) {
    (void)signal_number;_exit(124);
}
static void custom_key_child(int fd,pid_t parent) {
    struct custom_key_message message={0};struct custom_key_context context;
    Display *display=NULL;Window window=None;KeyCode code=0,s1=0,s2=0;
    XEvent pressed={0};
    int active=0,cancelled=0,waiting=0,registered=0;long long beat=0,window_check=0;
    signal(SIGINT,SIG_DFL);signal(SIGTERM,SIG_DFL);signal(SIGCHLD,SIG_DFL);
    struct sigaction deadline={0};deadline.sa_handler=custom_key_deadline;
    sigemptyset(&deadline.sa_mask);sigaction(SIGALRM,&deadline,NULL);alarm(2);
    if(prctl(PR_SET_PDEATHSIG,SIGKILL) || getppid()!=parent)_exit(125);
    prctl(PR_SET_NAME,"nx-custom-key",0,0,0);
    if(chdir("/"))_exit(120);
    /* Keep only the supervisor socket after dropping inherited camera/HTTP FDs. */
    int channel=dup(fd);if(channel<0)_exit(120);
    if(dup2(channel,3)<0)_exit(120);if(channel!=3)close(channel);
    DIR *inherited=opendir("/proc/self/fd");
    if(!inherited)_exit(120);
    int scan=dirfd(inherited);struct dirent *entry;
    while((entry=readdir(inherited))) {
        char *end;long descriptor=strtol(entry->d_name,&end,10);
        if(*entry->d_name && !*end && descriptor>3 && descriptor<=INT_MAX && descriptor!=scan)close((int)descriptor);
    }
    closedir(inherited);
    fd=3;fcntl(fd,F_SETFD,FD_CLOEXEC);
    memset(&context,0,sizeof context);context.fd=-1;
    message.reason=1;if(!custom_key_context_open(&context))goto done;
    message.reason=2;
    display=XOpenDisplay(NULL);if(!display)goto done;
    XSetErrorHandler(custom_key_error_handler);
    Bool supported=False;
    if(!XkbSetDetectableAutoRepeat(display,True,&supported) || !supported)goto done;
    message.reason=3;
    unsigned windows=0;custom_key_find(display,DefaultRootWindow(display),0,&window,&windows);
    if(windows!=1 || custom_key_x_error || !custom_key_window_pid(display,window,context.pid))goto done;
    message.reason=4;struct custom_key_mapping initial_mapping;
    if(!custom_key_mapping_snapshot(display,&initial_mapping))goto done;
    code=initial_mapping.code[0];s1=initial_mapping.code[1];s2=initial_mapping.code[2];
    /* Exactly one startup sample. A held Custom key must reach stock release
     * before a new passive grab can own a subsequent, complete transaction. */
    XSelectInput(display,window,StructureNotifyMask|KeyReleaseMask);
    char held[32];XQueryKeymap(display,held);
    waiting=((unsigned char)held[code>>3]&(1u<<(code&7)))!=0;
    XSelectInput(display,window,StructureNotifyMask|(waiting?KeyReleaseMask:0));
    if(!waiting){if(!custom_key_grab(display,window,code))goto done;registered=1;}
    message.state=waiting?CK_WAIT_RELEASE:CK_READY;message.reason=0;
    /* The initial find already validated this window; wait a full interval
     * before repeating that synchronous tree walk. */
    window_check=now_ms();
    for(;;) {
        alarm(2);
        long long now=now_ms();
        if(now-beat>=250) {
            message.context_stage=context.failure;message.context_errno=context.error;
            message.context_address=context.failed_address;
            message.type='H';if(!custom_key_send(fd,&message))break;beat=now;
        }
        struct pollfd descriptors[2]={{fd,POLLIN,0},{ConnectionNumber(display),POLLIN,0}};
        if(!XPending(display)) {
            alarm(0); /* poll has its own finite timeout; do not time idle input. */
            int result=poll(descriptors,2,200);
            alarm(2);
            if(result<0){if(errno==EINTR)continue;break;}
            if(descriptors[0].revents&(POLLIN|POLLHUP|POLLERR|POLLNVAL))break;
            if(descriptors[1].revents&(POLLHUP|POLLERR|POLLNVAL))break;
        }
        while(XPending(display)) {
            XEvent event;XNextEvent(display,&event);
            if(event.type==MappingNotify) {
                if(custom_key_mapping_unchanged(display,&event,&initial_mapping))continue;
                message.reason=4;goto done;
            }
            if(event.type==DestroyNotify || event.type==UnmapNotify)goto done;
            if(event.type!=KeyPress && event.type!=KeyRelease)continue;
            if(event.xkey.send_event)continue;
            if(waiting) {
                if(event.type==KeyRelease && event.xkey.keycode==code) {
                    XSelectInput(display,window,StructureNotifyMask);
                    if(!custom_key_grab(display,window,code))goto done;
                    registered=1;waiting=0;message.state=CK_READY;
                }
                continue;
            }
            if(!active) {
                if(event.type!=KeyPress || event.xkey.keycode!=code)continue;
                /* Asynchronous passive grabs never freeze key delivery. Do
                 * not perform state reads or synchronous X11 queries while
                 * holding this transaction. The server ends it on release,
                 * independently of this helper and its HTTP supervisor. */
                pressed=event;active=1;cancelled=0;message.state=CK_HOLDING;
                custom_key_trace("press-captured-async");continue;
            }
            if(event.xkey.keycode!=code) {
                /* Queue the captured edge before ungrab on this connection.
                 * The server must deliver a captured S1 press before it can
                 * deliver a later physical release directly to stock. */
                if(custom_key_forward(display,window,&event))message.forwarded++;
                XUngrabKeyboard(display,CurrentTime);XSync(display,False);
                while(XPending(display)) {
                    XEvent queued;XNextEvent(display,&queued);
                    if((queued.type==KeyPress || queued.type==KeyRelease) && !queued.xkey.send_event && queued.xkey.keycode!=code) {
                        if(custom_key_forward(display,window,&queued))message.forwarded++;
                    } else if(queued.type==KeyPress && queued.xkey.keycode==code) {
                        /* A second asynchronous grab may already be active. Closing
                         * this connection releases it without forwarding the
                         * consumed Custom press from the cancelled sequence. */
                        goto done;
                    } else if(queued.type==MappingNotify) {
                        if(!custom_key_mapping_unchanged(display,&queued,&initial_mapping)){message.reason=4;goto done;}
                    } else if(queued.type==DestroyNotify || queued.type==UnmapNotify)goto done;
                }
                XFlush(display);active=0;message.cancelled++;message.state=CK_READY;continue;
            }
            if(event.type==KeyPress){cancelled=1;continue;}
            active=0;message.state=CK_READY;message.consumed++;message.triggered_at=now_ms();
            alarm(1);custom_key_trace("release-received");
            /* The release terminates the passive grab in the server. Fence
             * before any possibly blocking state read; no grab remains while
             * checking context or launching the UI. */
            XSync(display,False);custom_key_trace("release-fenced");
            unsigned modifiers=ShiftMask|LockMask|ControlMask|Mod1Mask|Mod2Mask|Mod3Mask|Mod4Mask|Mod5Mask;
            if(!cancelled && !(pressed.xkey.state&modifiers) &&
                custom_key_ready(display,window,&context,s1,s2)) {
                long long sent=now_ms();
                fprintf(stderr,"NX_CUSTOM_TRIGGER trigger_ms=%lld sent_ms=%lld checks_ms=%lld\n",
                        message.triggered_at,sent,sent-message.triggered_at);
                message.type='T';if(!custom_key_send(fd,&message))goto done;
                custom_key_trace("panel-dispatch-sent");
            } else if(!cancelled) {
                /* Async grabs cannot use ReplayKeyboard. Restore exactly the
                 * saved press/release pair outside eligible preview, in order. */
                if(custom_key_forward(display,window,&pressed))message.forwarded++;
                if(custom_key_forward(display,window,&event))message.forwarded++;
                XFlush(display);message.fallbacks++;custom_key_trace("stock-pair-forwarded");
            } else {message.cancelled++;custom_key_trace("transaction-cancelled");}
            alarm(2);
        }
        if(custom_key_x_error)break;
        if(now-window_check>=5000 && !active) {
            Window current=None;unsigned count=0;
            custom_key_find(display,DefaultRootWindow(display),0,&current,&count);
            if(count!=1 || current!=window || !custom_key_context_identity(&context))break;
            window_check=now;
        }
    }
done:
    if(display) {
        /* Cleanup never forwards a consumed initial Custom press. */
        if(registered)XUngrabKey(display,code,AnyModifier,window);
        XUngrabKeyboard(display,CurrentTime);XSync(display,False);XCloseDisplay(display);
    }
    message.context_stage=context.failure;message.context_errno=context.error;
    message.context_address=context.failed_address;
    custom_key_context_close(&context);
    message.type='F';message.state=CK_FAILED;if(!message.reason)message.reason=5;
    custom_key_send(fd,&message);close(fd);_exit(0);
}
static int custom_key_resources(void) {
    char launcher[256],binary[256],disabled[256],no_shortcut[256];const char *root=custom_key_service.root;
    if(!nx_mod_root_valid(root))return 0;
    snprintf(launcher,sizeof launcher,"%s/mfw-panel.sh",root);
    snprintf(binary,sizeof binary,"%s/bin/nxui.arm",root);
    snprintf(disabled,sizeof disabled,"%s/DISABLED",root);
    snprintf(no_shortcut,sizeof no_shortcut,"%s/NO_PANEL_SHORTCUT",root);
    return !access(launcher,R_OK) && !access(binary,X_OK) && access(disabled,F_OK) && access(no_shortcut,F_OK);
}
static void custom_key_shutdown(void) {
    if(custom_key_service.fd>=0){close(custom_key_service.fd);custom_key_service.fd=-1;}
    if(custom_key_service.pid && !custom_key_service.terminating) {
        kill(custom_key_service.pid,SIGKILL);custom_key_service.terminating=1;
    }
}
static void custom_key_init(void) {
    custom_key_service.fd=-1;
    const char *enabled=getenv("NX_REMOTE_CUSTOM_KEY"),*root=getenv("NX300M_MOD_ROOT");
    if(!enabled || strcmp(enabled,"1") || !nx_mod_root_valid(root))return;
    snprintf(custom_key_service.root,sizeof custom_key_service.root,"%s",root);
    custom_key_service.enabled=1;
}
static void custom_key_poll(void) {
    long long now=now_ms();
    if(custom_key_service.pid) {
        int status;pid_t reaped=waitpid(custom_key_service.pid,&status,WNOHANG);
        if(reaped==custom_key_service.pid || (reaped<0 && errno==ECHILD)) {
            /* A fast startup failure can exit before the first parent poll.
             * Preserve its final reason, but never launch from a dead child's
             * queued trigger or retain its PID after waitpid has released it. */
            struct custom_key_message final;
            for(unsigned i=0;custom_key_service.fd>=0 && i<32;i++) {
                ssize_t size=recv(custom_key_service.fd,&final,sizeof final,MSG_DONTWAIT);
                if(size!=(ssize_t)sizeof final)break;
                if(final.type=='F') {
                    custom_key_service.reason=(int)final.reason;
                    custom_key_service.fallbacks=final.fallbacks;custom_key_service.consumed=final.consumed;
                    custom_key_service.cancelled=final.cancelled;custom_key_service.forwarded=final.forwarded;
                    custom_key_service.context_stage=final.context_stage;custom_key_service.context_errno=final.context_errno;
                    custom_key_service.context_address=final.context_address;
                    custom_key_service.failures++;
                }
            }
            if(reaped==custom_key_service.pid && WIFEXITED(status) && WEXITSTATUS(status)==124) {
                custom_key_service.timeouts++;custom_key_service.reason=7;
                fputs("NX_CUSTOM_DEADLINE helper exited; X11 connection closed\n",stderr);
            }
            custom_key_service.pid=0;custom_key_service.terminating=0;custom_key_shutdown();
            custom_key_service.state=CK_FAILED;
            /* A missing context/X server must not cause a full reinitialization
             * every two seconds throughout a shooting session. */
            if(custom_key_service.failure_streak<6)custom_key_service.failure_streak++;
            unsigned delay=2000u<<(custom_key_service.failure_streak?custom_key_service.failure_streak-1:0);
            if(delay>60000)delay=60000;
            custom_key_service.retry=now+delay;
        }
    }
    if(!custom_key_service.enabled || stopping || media_paused || !custom_key_resources()) {
        custom_key_shutdown();return;
    }
    if(custom_key_service.pid && !custom_key_service.terminating) {
        struct custom_key_message message;
        for(unsigned i=0;i<32;i++) {
            ssize_t size=recv(custom_key_service.fd,&message,sizeof message,MSG_DONTWAIT);
            if(size<0 && (errno==EAGAIN || errno==EWOULDBLOCK))break;
            if(size!=(ssize_t)sizeof message){custom_key_service.failures++;custom_key_shutdown();break;}
            custom_key_service.heartbeat=now;custom_key_service.state=(int)message.state;
            custom_key_service.reason=(int)message.reason;
            custom_key_service.context_stage=message.context_stage;custom_key_service.context_errno=message.context_errno;
            custom_key_service.context_address=message.context_address;
            if(message.state==CK_READY && now-custom_key_service.started>=5000)
                custom_key_service.failure_streak=0;
            custom_key_service.fallbacks=message.fallbacks;custom_key_service.consumed=message.consumed;
            custom_key_service.cancelled=message.cancelled;custom_key_service.forwarded=message.forwarded;
            if(message.type=='T') {
                long long received=now_ms();
                fprintf(stderr,"NX_CUSTOM_DISPATCH trigger_ms=%lld received_ms=%lld elapsed_ms=%lld\n",
                        message.triggered_at,received,received-message.triggered_at);
                custom_key_service.triggers++;custom_key_launch_panel(message.triggered_at);
            } else if(message.type=='F'){custom_key_service.failures++;custom_key_shutdown();break;}
        }
        if(!custom_key_service.terminating &&
            ((custom_key_service.state==CK_STARTING && now-custom_key_service.started>NX_CUSTOM_KEY_INIT_MS) ||
             (custom_key_service.state!=CK_STARTING && now-custom_key_service.heartbeat>NX_CUSTOM_KEY_HEARTBEAT_MS))) {
            custom_key_service.timeouts++;custom_key_service.reason=6;custom_key_shutdown();
        }
    }
    if(custom_key_service.pid || now<custom_key_service.retry)return;
    int sockets[2];
    if(socketpair(AF_UNIX,SOCK_DGRAM,0,sockets)){custom_key_service.retry=now+2000;return;}
    fcntl(sockets[0],F_SETFD,FD_CLOEXEC);fcntl(sockets[1],F_SETFD,FD_CLOEXEC);
    pid_t parent=getpid(),child=fork();
    if(!child){close(sockets[0]);custom_key_child(sockets[1],parent);}
    close(sockets[1]);
    if(child<0){close(sockets[0]);custom_key_service.retry=now+2000;return;}
    custom_key_service.pid=child;custom_key_service.fd=sockets[0];custom_key_service.state=CK_STARTING;custom_key_service.reason=0;
    custom_key_service.started=custom_key_service.heartbeat=now;
}
static void custom_key_status(char *json,size_t capacity) {
    const char *state=!custom_key_service.enabled?"disabled":stopping?"stopping":media_paused?"paused":
        custom_key_service.terminating?"terminating":!custom_key_service.pid?"unavailable":
        custom_key_service.state==CK_STARTING?"starting":custom_key_service.state==CK_WAIT_RELEASE?"waiting-release":
        custom_key_service.state==CK_HOLDING?"holding":custom_key_service.state==CK_READY?"ready":"failed";
    const char *reason=custom_key_service.reason==1?"context-unavailable":custom_key_service.reason==2?"x11-unavailable":
        custom_key_service.reason==3?"window-unavailable":custom_key_service.reason==4?"keymap-unavailable":
        custom_key_service.reason==5?"connection-lost":custom_key_service.reason==6?"helper-timeout":
        custom_key_service.reason==7?"helper-deadline":"none";
    const char *stages[]={"none","stock-executable","proc-scan","stock-process-count","starttime","mem-open","mem-read","accessor-mismatch","identity"};
    const char *context=custom_key_service.context_stage<sizeof stages/sizeof *stages?stages[custom_key_service.context_stage]:"unknown";
    long long retry=custom_key_service.retry-now_ms();if(retry<0 || custom_key_service.pid)retry=0;
    snprintf(json,capacity,"{\"enabled\":%s,\"mode\":\"async\",\"state\":\"%s\",\"reason\":\"%s\",\"helper_pid\":%ld,\"triggers\":%u,\"fallbacks\":%u,\"consumed\":%u,\"cancelled\":%u,\"forwarded\":%u,\"timeouts\":%u,\"failures\":%u,\"failure_streak\":%u,\"retry_in_ms\":%lld,\"context_stage\":\"%s\",\"context_errno\":%u,\"context_address\":%u}",
        custom_key_service.enabled?"true":"false",state,reason,(long)custom_key_service.pid,
        custom_key_service.triggers,custom_key_service.fallbacks,custom_key_service.consumed,
        custom_key_service.cancelled,custom_key_service.forwarded,custom_key_service.timeouts,custom_key_service.failures,
        custom_key_service.failure_streak,retry,context,custom_key_service.context_errno,custom_key_service.context_address);
}
#endif
