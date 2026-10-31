/* Passive Fn or MENU hold/release entry, independent of the capture X client.
 * One selected entry only. No grab, injection or camera-memory access. */
#ifndef NX_REMOTE_MENU_KEY_H
#define NX_REMOTE_MENU_KEY_H
#include <linux/input.h>
#include <sys/ioctl.h>

struct menu_key_message { unsigned char type, reason; unsigned int code; long long triggered_at; };
/* Physical key entry, selected by NX_REMOTE_PANEL_KEY: f=Fn, m=MENU, c=Custom.
 * Fn keeps its stock function untouched; Custom carries the mod gestures. */
static struct {
    int enabled, fd, terminating;
    char mode;
    pid_t pid, cycle_pid;
    unsigned int reason, code, triggers, cycles, failures;
    long long heartbeat, retry;
} menu_key_service = {.fd = -1, .mode = 'f'};

static int menu_key_bit(const unsigned char *bits, unsigned code) {
    return !!(bits[code/8] & (1u << (code%8)));
}
static int menu_key_device(const char *path, unsigned code, unsigned char *held, size_t held_size) {
    unsigned char capabilities[(KEY_MAX+8)/8] = {0};
    int fd = open(path, O_RDONLY|O_NONBLOCK|O_CLOEXEC);
    if (fd < 0) return -1;
    if (ioctl(fd, EVIOCGBIT(EV_KEY,sizeof capabilities), capabilities) < 0 ||
        !menu_key_bit(capabilities,code) || ioctl(fd,EVIOCGKEY(held_size),held) < 0) {
        close(fd);return -1;
    }
    return fd;
}
static void menu_key_child(int channel, pid_t parent) {
    struct menu_key_message message = {.type='H', .reason=1};
    char mode=menu_key_service.mode;
    signal(SIGINT,SIG_DFL);signal(SIGTERM,SIG_DFL);signal(SIGCHLD,SIG_DFL);
    struct sigaction deadline = {0};deadline.sa_handler=custom_key_deadline;
    sigemptyset(&deadline.sa_mask);sigaction(SIGALRM,&deadline,NULL);alarm(2);
    if (prctl(PR_SET_PDEATHSIG,SIGKILL) || getppid()!=parent) _exit(125);
    prctl(PR_SET_NAME,"nx-menu-key",0,0,0);
    fn_log("helper start mode=%c",mode);
    int duplicate=dup(channel);
    if (duplicate<0 || dup2(duplicate,3)<0) _exit(120);
    if (duplicate!=3) close(duplicate);
    DIR *fds=opendir("/proc/self/fd");if(!fds)_exit(120);
    int scan=dirfd(fds);struct dirent *entry;
    while((entry=readdir(fds))) {
        char *end;long fd=strtol(entry->d_name,&end,10);
        if(*entry->d_name && !*end && fd>3 && fd<=INT_MAX && fd!=scan)close((int)fd);
    }
    closedir(fds);channel=3;fcntl(channel,F_SETFD,FD_CLOEXEC);
    /* Xorg's evdev driver maps Linux codes at +8. Resolve the current Menu
     * keysym once, then close X11; no polling or focus operations follow. */
    Display *mapping=XOpenDisplay(NULL);
    if(!mapping){fn_log("helper x11 open failed display=%s",getenv("DISPLAY")?getenv("DISPLAY"):"(unset)");goto failed;}
    const char *sym_names[]={"XF86HomePage","Menu","KP_Delete","Super_L","Super_R"};
    KeySym symbols[5];unsigned nsyms=0;
    symbols[nsyms++]=menu_key_service.mode=='c' ? XStringToKeysym("KP_Delete")
                   : menu_key_service.mode=='m' ? XK_Menu : XStringToKeysym("XF86HomePage");
    if(menu_key_service.mode=='c')
        symbols[nsyms++]=XStringToKeysym("Super_L"),symbols[nsyms++]=XStringToKeysym("Super_R");
    KeyCode mapped[5]={0};unsigned ncodes=0;
    for(unsigned i=0;i<nsyms;i++) {
        if(!symbols[i]) continue;
        KeyCode code=XKeysymToKeycode(mapping,symbols[i]);
        if(code>=8) mapped[ncodes++]=(unsigned)(code-8);
    }
    XCloseDisplay(mapping);
    if(!ncodes){fn_log("helper keysym unresolved candidates=%u",nsyms);goto failed;}
    const char *paths[]={"/dev/input/event0","/dev/event0","/dev/input/event1","/dev/event1"};
    int device=-1;
    unsigned char held[(KEY_MAX+8)/8]={0};
    message.reason=2;
    for(unsigned c=0;c<ncodes && device<0;c++) {
        for(unsigned i=0;i<sizeof paths/sizeof *paths;i++) {
            int saved=errno;
            device=menu_key_device(paths[i],mapped[c],held,sizeof held);
            if(device>=0)break;
            fn_log("helper device %s rejected errno=%d code=%u",paths[i],errno,mapped[c]);
            errno=saved;
        }
        if(device>=0) message.code=mapped[c];
    }
    if(device<0){fn_log("helper no evdev device carries code=%u (first candidate)",mapped[0]);goto failed;}
    fprintf(stderr,"%s code=%u hold_ms=1200 input=passive\n",
        menu_key_service.mode=='c'?"NX_CUSTOM_KEY_READY":menu_key_service.mode=='m'?"NX_MENU_KEY_READY":"NX_FN_KEY_READY",message.code);
    fn_log("helper ready mode=%c code=%u",mode,message.code);
    message.reason=0;
    int pressed=0,cancelled=0,wait_release=menu_key_bit(held,message.code),suppress_trigger=0;
    long long pressed_at=0,prev_press=0,prev_release=0,beat=0;
    for(;;) {
        alarm(2);
        long long now=now_ms();
        if(now-beat>=250) {
            message.type='H';
            if(send(channel,&message,sizeof message,MSG_DONTWAIT|MSG_NOSIGNAL)!=(ssize_t)sizeof message)break;
            beat=now;
        }
        struct pollfd descriptors[2]={{channel,POLLIN,0},{device,POLLIN,0}};
        alarm(0);int result=poll(descriptors,2,200);alarm(2);
        if(result<0){if(errno==EINTR)continue;break;}
        if(descriptors[0].revents || (descriptors[1].revents&(POLLHUP|POLLERR|POLLNVAL)))break;
        /* Bound each batch even under a noisy device. Each reader has its own
         * evdev queue, so these reads never consume stock application's keys. */
        for(unsigned i=0;i<64;i++) {
            struct input_event event;ssize_t size=read(device,&event,sizeof event);
            if(size<0 && (errno==EAGAIN || errno==EWOULDBLOCK))break;
            if(size<0 && errno==EINTR)continue;
            if(size!=(ssize_t)sizeof event)goto closed;
            if(event.type==EV_SYN && event.code==SYN_DROPPED) {
                pressed=0;wait_release=1;memset(held,0,sizeof held);continue;
            }
            if(event.type!=EV_KEY)continue;
            if(event.code>KEY_MAX)continue;
            if(event.value==1)held[event.code/8]|=1u<<(event.code%8);
            else if(event.value==0)held[event.code/8]&=~(1u<<(event.code%8));
            if(event.code!=message.code) {
                if(event.value==1)cancelled=1;
                continue;
            }
            if(wait_release){if(event.value==0)wait_release=0;continue;}
            if(event.value==1 && !pressed) {
                pressed=1;cancelled=0;pressed_at=now_ms();
                for(unsigned key=0;key<=KEY_MAX;key++)
                    if(key!=message.code && menu_key_bit(held,key)){cancelled=1;break;}
                /* Fn mode: double short tap dispatches zero-UI cycling. In
                 * custom mode every short release cycles, so no press-time
                 * decision is needed. */
                if(mode!='c' && !cancelled && !wait_release && prev_release &&
                    pressed_at-prev_release<=500 && prev_release-prev_press<=350) {
                    message.type='D';message.triggered_at=pressed_at;
                    if(send(channel,&message,sizeof message,MSG_DONTWAIT|MSG_NOSIGNAL)==(ssize_t)sizeof message) {
                        fn_log("double tap dispatched");
                        suppress_trigger=1;prev_press=prev_release=0;
                    }
                } else if(mode!='c') { prev_press=pressed_at;prev_release=0; }
                fn_log("key press held_others=%d",cancelled);
            } else if(event.value==0) {
                long long released=now_ms(),duration=released-pressed_at;
                int tap=pressed && !cancelled && !suppress_trigger && duration>=40 && duration<1200;
                if(pressed && !cancelled && !suppress_trigger && duration>=1200 && duration<=10000) {
                    message.type='T';message.triggered_at=released;
                    if(send(channel,&message,sizeof message,MSG_DONTWAIT|MSG_NOSIGNAL)!=(ssize_t)sizeof message)goto closed;
                    fprintf(stderr,"%s held_ms=%lld release_ms=%lld\n",
                        mode=='c'?"NX_CUSTOM_KEY_TRIGGER":mode=='m'?"NX_MENU_KEY_TRIGGER":"NX_FN_KEY_TRIGGER",duration,released);
                    fn_log("trigger held_ms=%lld",duration);
                } else if(tap && mode=='c') {
                    message.type='D';message.triggered_at=released;
                    if(send(channel,&message,sizeof message,MSG_DONTWAIT|MSG_NOSIGNAL)==(ssize_t)sizeof message)
                        fn_log("custom tap dispatched cycle held_ms=%lld",duration);
                    else goto closed;
                } else if(mode!='c')
                    fn_log("key release held_ms=%lld cancelled=%d suppressed=%d in_window=%d",
                        pressed?duration:-1,cancelled,suppress_trigger,duration>=1200&&duration<=10000);
                if(mode!='c' && !suppress_trigger) prev_release=pressed?released:prev_release;
                suppress_trigger=0;
                pressed=0;
            }
        }
    }
closed:
    close(device);message.reason=3;
failed:
    fprintf(stderr,"%s reason=%u code=%u errno=%d\n",
        menu_key_service.mode=='c'?"NX_CUSTOM_KEY_UNAVAILABLE":menu_key_service.mode=='m'?"NX_MENU_KEY_UNAVAILABLE":"NX_FN_KEY_UNAVAILABLE",message.reason,message.code,errno);
    fn_log("helper exit reason=%u code=%u errno=%d",message.reason,message.code,errno);
    message.type='F';send(channel,&message,sizeof message,MSG_DONTWAIT|MSG_NOSIGNAL);
    close(channel);_exit(0);
}
static int menu_key_allowed(void) {
    return menu_key_service.enabled && !media_paused && !stopping && panel_shortcut.launcher[0] &&
        access(panel_shortcut.disabled,F_OK) && access(panel_shortcut.no_shortcut,F_OK);
}
static void menu_key_shutdown(void) {
    if(menu_key_service.fd>=0){close(menu_key_service.fd);menu_key_service.fd=-1;}
    if(menu_key_service.pid && !menu_key_service.terminating) {
        kill(menu_key_service.pid,SIGKILL);menu_key_service.terminating=1;
    }
}
static void lut_cycle_launch(void) {
    if(menu_key_service.cycle_pid) return;
    const char *root=getenv("NX300M_MOD_SOURCE_ROOT");
    char script[PATH_MAX];
    if(!root || !*root || snprintf(script,sizeof script,"%s/lut-cycle.sh",root)>=(int)sizeof script) {
        fn_log("lut cycle skipped: no source root");return;
    }
    if(access(script,R_OK)) { fn_log("lut cycle skipped: script missing path=%s",script);return; }
    pid_t parent=getpid(),pid=fork();
    if(pid<0) { fn_log("lut cycle fork failed errno=%d",errno);return; }
    if(!pid) {
        signal(SIGINT,SIG_DFL);signal(SIGTERM,SIG_DFL);signal(SIGCHLD,SIG_DFL);
        if(prctl(PR_SET_PDEATHSIG,SIGTERM) || getppid()!=parent) _exit(125);
        close_inherited_fds();
        fn_log("lut cycle exec script=%s",script);
        execl("/bin/sh","sh",script,(char *)0);
        fn_log("lut cycle exec failed errno=%d",errno);
        _exit(127);
    }
    menu_key_service.cycle_pid=pid;
    fn_log("lut cycle forked pid=%ld",(long)pid);
}
static void menu_key_init(void) {
    const char *enabled=getenv("NX_REMOTE_MENU_KEY");
    const char *key=getenv("NX_REMOTE_PANEL_KEY");
    menu_key_service.mode = key && !strcmp(key,"custom") ? 'c'
                          : key && !strcmp(key,"menu") ? 'm' : 'f';
    menu_key_service.enabled=panel_shortcut.enabled || (enabled && !strcmp(enabled,"1"));
    fn_log("menu key init mode=%c enabled=%d key_env=%s",
        menu_key_service.mode,menu_key_service.enabled,key?key:"(unset)");
}
static void menu_key_poll(void) {
    long long now=now_ms();
    if(menu_key_service.cycle_pid) {
        int status;pid_t done=waitpid(menu_key_service.cycle_pid,&status,WNOHANG);
        if(done==menu_key_service.cycle_pid || (done<0 && errno==ECHILD)) {
            if(done>0) {
                fn_log("lut cycle exited status=%d",status);
                if(WIFEXITED(status) && !WEXITSTATUS(status)) menu_key_service.cycles++;
            }
            menu_key_service.cycle_pid=0;
        }
    }
    if(menu_key_service.pid) {
        int status;pid_t reaped=waitpid(menu_key_service.pid,&status,WNOHANG);
        if(reaped==menu_key_service.pid || (reaped<0 && errno==ECHILD)) {
            struct menu_key_message final;
            for(unsigned i=0;menu_key_service.fd>=0 && i<16;i++) {
                if(recv(menu_key_service.fd,&final,sizeof final,MSG_DONTWAIT)!=(ssize_t)sizeof final)break;
                if(final.type=='F'){menu_key_service.reason=final.reason;menu_key_service.code=final.code;}
            }
            menu_key_service.pid=0;menu_key_service.terminating=0;menu_key_shutdown();
            menu_key_service.failures++;menu_key_service.retry=now+5000;
            fn_log("helper exited pid=%ld status=%d failures=%u retry_ms=%lld",(long)reaped,status,menu_key_service.failures,menu_key_service.retry);
        }
    }
    if(!menu_key_allowed()){menu_key_shutdown();return;}
    if(menu_key_service.pid && !menu_key_service.terminating) {
        for(unsigned i=0;i<16;i++) {
            struct menu_key_message message;
            ssize_t size=recv(menu_key_service.fd,&message,sizeof message,MSG_DONTWAIT);
            if(size<0 && (errno==EAGAIN || errno==EWOULDBLOCK))break;
            if(size!=(ssize_t)sizeof message){menu_key_shutdown();break;}
            menu_key_service.heartbeat=now;menu_key_service.reason=message.reason;menu_key_service.code=message.code;
            if(message.type=='T') {
                if(!panel_shortcut.pid) {
                    fn_log("trigger received dispatching panel");
                    custom_key_launch_panel(message.triggered_at);
                    if(panel_shortcut.pid)menu_key_service.triggers++;
                } else fn_log("trigger received while panel pid=%ld alive",(long)panel_shortcut.pid);
            } else if(message.type=='D') {
                if(!menu_key_service.cycle_pid) {
                    lut_cycle_launch();
                } else fn_log("double tap ignored: cycle pid=%ld running",(long)menu_key_service.cycle_pid);
            } else if(message.type=='F'){menu_key_shutdown();break;}
        }
        if(now-menu_key_service.heartbeat>2500){fn_log("helper heartbeat timeout");menu_key_shutdown();}
    }
    if(menu_key_service.pid || now<menu_key_service.retry)return;
    int sockets[2];
    if(socketpair(AF_UNIX,SOCK_DGRAM,0,sockets)){menu_key_service.retry=now+5000;return;}
    fcntl(sockets[0],F_SETFD,FD_CLOEXEC);fcntl(sockets[1],F_SETFD,FD_CLOEXEC);
    pid_t parent=getpid(),child=fork();
    if(!child){close(sockets[0]);menu_key_child(sockets[1],parent);}
    close(sockets[1]);
    if(child<0){fn_log("helper fork failed errno=%d",errno);close(sockets[0]);menu_key_service.retry=now+5000;return;}
    menu_key_service.pid=child;menu_key_service.fd=sockets[0];menu_key_service.heartbeat=now;
    menu_key_service.code=menu_key_service.reason=0;
    fn_log("helper spawned pid=%ld mode=%c",(long)child,menu_key_service.mode);
}
static void hold_key_status(char *json,size_t size,int want_menu) {
    int enabled=menu_key_service.enabled &&
        (want_menu ? menu_key_service.mode=='m' : menu_key_service.mode!='m');
    const char *state=!enabled?"disabled":!menu_key_allowed()?"paused":
        menu_key_service.terminating?"terminating":!menu_key_service.pid?"unavailable":menu_key_service.code?"ready":"starting";
    const char *entry=menu_key_service.mode=='c'?"tap-custom-cycle-or-hold-panel":
                     want_menu?"hold-menu-release":"hold-fn-release";
    snprintf(json,size,"{\"enabled\":%s,\"state\":\"%s\",\"entry\":\"%s\",\"hold_ms\":1200,\"linux_keycode\":%u,\"helper_pid\":%ld,\"triggers\":%u,\"failures\":%u,\"reason\":%u}",
        enabled?"true":"false",state,entry,enabled?menu_key_service.code:0,
        enabled?(long)menu_key_service.pid:0,enabled?menu_key_service.triggers:0,enabled?menu_key_service.failures:0,
        enabled?menu_key_service.reason:0);
}
static void menu_key_status(char *json,size_t size) {hold_key_status(json,size,0);}
/* The primary non-menu entry (Fn or Custom, by NX_REMOTE_PANEL_KEY). */
static void fn_key_status(char *json,size_t size) {hold_key_status(json,size,0);}
#endif
