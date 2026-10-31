/* Read kernel state without opening the media tree. Observation is not an
 * acknowledgement from the stock exporter, nor proof of superblock teardown. */
#include <sys/prctl.h>
#include <sys/socket.h>
#include <sys/wait.h>
#ifndef NX_USB_SYSFS_PREFIX
#define NX_USB_SYSFS_PREFIX "/sys"
#endif
#ifndef NX_STORAGE_MOUNTINFO
#define NX_STORAGE_MOUNTINFO "/proc/self/mountinfo"
#endif
struct usb_storage_sample {
    int backing,cable,mode,mount_known,mount_id,nodes_found,read_errors;
    char device[32],point[PATH_MAX],filesystem[64];
};
enum { USB_STORAGE_INTERVAL_MS=1000,USB_STORAGE_DEADLINE_MS=1500,USB_STORAGE_RETRY_MS=5000 };
static struct {
    struct usb_storage_sample sample;
    long long sampled_at,next_sample,deadline;
    pid_t pid;
    int fd,active,retiring,sampled,seen_open,initial_mount_id,mount_changed;
    unsigned int timeouts,failures;
    const char *reason;
} usb_storage={.sample={.backing=-1,.cable=-1,.mode=-1},.fd=-1,.reason="pending"};

/* 1 readable, 0 absent, -1 error. Empty sysfs backing is a valid closed state. */
static int usb_storage_read(const char *suffix,char *value,size_t capacity) {
    char path[PATH_MAX];
    int n=snprintf(path,sizeof path,"%s%s",NX_USB_SYSFS_PREFIX,suffix);
    if (n<0 || n>=(int)sizeof path) return -1;
    int fd=open(path,O_RDONLY|O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC);
    if (fd<0) return errno==ENOENT || errno==ENOTDIR?0:-1;
    ssize_t got=read(fd,value,capacity-1);
    int failed=got<0;
    if (!failed && got==(ssize_t)capacity-1) {
        char extra;failed=read(fd,&extra,1)!=0;
    }
    close(fd);
    if (failed) return -1;
    value[got]=0;
    while (got>0 && isspace((unsigned char)value[got-1])) value[--got]=0;
    return 1;
}
static int usb_storage_number(const char *suffix) {
    char value[64],*end;
    int result=usb_storage_read(suffix,value,sizeof value);
    if (result!=1) return -1;
    errno=0;long number=strtol(value,&end,10);
    return *value && !errno && !*end && number>=0 && number<=INT_MAX?(int)number:-1;
}
static int usb_storage_mode(void) {
    char value[128],*end;
    if (usb_storage_read("/devices/platform/usb_mode/UsbMenuSel",value,sizeof value)!=1) return -1;
    /* The Samsung driver reports a label, not a decimal sysfs enum. Keep the
     * numeric form for older drivers; UMS is USBSTATUS_UMS (zero). */
    if (!strcmp(value,"[UsbMenuSel] UMS")) return 0;
    errno=0;long number=strtol(value,&end,10);
    return *value && !errno && !*end && number>=0 && number<=INT_MAX?(int)number:-1;
}
static int usb_storage_unescape(const char *source,char *target,size_t capacity) {
    size_t n=0;
    for (size_t i=0;source[i];i++) {
        unsigned char ch=(unsigned char)source[i];
        if (ch=='\\') {
            if (!source[i+1] || !source[i+2] || !source[i+3] ||
                source[i+1]<'0' || source[i+1]>'7' || source[i+2]<'0' || source[i+2]>'7' ||
                source[i+3]<'0' || source[i+3]>'7') return 0;
            unsigned decoded=(unsigned)(source[i+1]-'0')*64+(unsigned)(source[i+2]-'0')*8+(unsigned)(source[i+3]-'0');
            if (!decoded || decoded>255) return 0;
            ch=(unsigned char)decoded;i+=3;
        }
        if (n+1>=capacity) return 0;
        target[n++]=(char)ch;
    }
    target[n]=0;return 1;
}
static void usb_storage_mount(struct usb_storage_sample *sample) {
    FILE *file=fopen(NX_STORAGE_MOUNTINFO,"r");
    if (!file) return;
    char line[16384];size_t longest=0;
    while (fgets(line,sizeof line,file)) {
        if (!strchr(line,'\n') && !feof(file)) {
            int ch;while ((ch=fgetc(file))!=EOF && ch!='\n') {}
            continue;
        }
        char *fields[128],*save=NULL;int count=0;
        for (char *p=strtok_r(line," \n",&save);p && count<128;p=strtok_r(NULL," \n",&save)) fields[count++]=p;
        if (count<10) continue;
        int separator=6;while (separator<count && strcmp(fields[separator],"-")) separator++;
        if (separator+3>=count) continue;
        char mountpoint[PATH_MAX],*end;
        if (!usb_storage_unescape(fields[4],mountpoint,sizeof mountpoint)) continue;
        size_t length=strlen(mountpoint);
        if (!length || mountpoint[0]!='/' || strncmp(media_root,mountpoint,length) ||
            (length>1 && media_root[length] && media_root[length]!='/') || length<longest) continue;
        errno=0;long id=strtol(fields[0],&end,10);
        if (errno || *end || id<=0 || id>INT_MAX || strlen(fields[2])>=sizeof sample->device ||
            strlen(fields[separator+1])>=sizeof sample->filesystem) continue;
        longest=length;sample->mount_known=1;sample->mount_id=(int)id;
        strcpy(sample->point,mountpoint);strcpy(sample->device,fields[2]);
        strcpy(sample->filesystem,fields[separator+1]);
    }
    int failed=ferror(file);fclose(file);
    if (failed) sample->mount_known=0;
}
static void usb_storage_collect(struct usb_storage_sample *sample) {
    memset(sample,0,sizeof *sample);sample->backing=sample->cable=sample->mode=-1;
    const char *nodes[]={"/devices/platform/usb_mass_storage/lun1/file","/class/usb_mode/usb0/f_mass_storage/lun1/file"};
    int readable=0,opened=0;
    for (unsigned i=0;i<sizeof nodes/sizeof *nodes;i++) {
        char value[PATH_MAX];int result=usb_storage_read(nodes[i],value,sizeof value);
        if (result==1) { readable++;sample->nodes_found++;if (*value) opened=1; }
        else if (result<0) sample->read_errors++;
    }
    sample->backing=opened?1:readable && !sample->read_errors?0:-1;
    int cable=usb_storage_number("/devices/platform/jack/usb_online");
    sample->cable=cable<0?-1:!!cable;
    sample->mode=usb_storage_mode();
    usb_storage_mount(sample);
}
static void usb_storage_child(int fd,pid_t parent) {
    signal(SIGINT,SIG_DFL);signal(SIGTERM,SIG_DFL);signal(SIGCHLD,SIG_DFL);
    if (prctl(PR_SET_PDEATHSIG,SIGKILL) || getppid()!=parent) _exit(125);
    prctl(PR_SET_NAME,"nx-usb-worker",0,0,0);
    if (chdir("/")) _exit(120);
    int channel=dup(fd);
    if (channel<0 || dup2(channel,3)<0) _exit(120);
    if (channel!=3) close(channel);
    DIR *fds=opendir("/proc/self/fd");if (!fds) _exit(120);
    int scan=dirfd(fds);struct dirent *entry;
    while ((entry=readdir(fds))) {
        char *end;long descriptor=strtol(entry->d_name,&end,10);
        if (*entry->d_name && !*end && descriptor>=0 && descriptor<=INT_MAX && descriptor!=3 && descriptor!=scan)
            close((int)descriptor);
    }
    closedir(fds);fcntl(3,F_SETFD,FD_CLOEXEC);
    for (;;) {
        unsigned char command;ssize_t size=recv(3,&command,1,0);
        if (size<0 && errno==EINTR) continue;
        if (size!=1 || command!='S') _exit(0);
        struct usb_storage_sample sample;usb_storage_collect(&sample);
        if (send(3,&sample,sizeof sample,MSG_NOSIGNAL|MSG_DONTWAIT)!=sizeof sample) _exit(0);
    }
}
static void usb_storage_unknown(const char *reason) {
    memset(&usb_storage.sample,0,sizeof usb_storage.sample);
    usb_storage.sample.backing=usb_storage.sample.cable=usb_storage.sample.mode=-1;
    usb_storage.sampled=0;usb_storage.reason=reason;
}
static void usb_storage_retire(const char *reason) {
    if (usb_storage.fd>=0) close(usb_storage.fd);
    usb_storage.fd=-1;usb_storage.active=0;
    if (usb_storage.pid && !usb_storage.retiring) kill(usb_storage.pid,SIGKILL);
    usb_storage.retiring=usb_storage.pid!=0;
    usb_storage.next_sample=now_ms()+USB_STORAGE_RETRY_MS;
    usb_storage_unknown(reason);
}
static void usb_storage_shutdown(void) {
    usb_storage_retire("stopped");
    /* Reap a normally killable child before exit, but never wait on a task
     * stuck in a kernel driver. PDEATHSIG also covers an unexpected exit. */
    for (unsigned attempt=0;usb_storage.pid && attempt<50;attempt++) {
        int status;pid_t found=waitpid(usb_storage.pid,&status,WNOHANG);
        if (found==usb_storage.pid || (found<0 && errno==ECHILD)) {
            usb_storage.pid=0;usb_storage.retiring=0;
        } else usleep(1000);
    }
}
static int usb_storage_start(void) {
    int sockets[2];
    if (socketpair(AF_UNIX,SOCK_DGRAM,0,sockets)) return 0;
    pid_t parent=getpid(),pid=fork();
    if (!pid) {close(sockets[0]);usb_storage_child(sockets[1],parent);_exit(125);}
    close(sockets[1]);
    if (pid<0) {close(sockets[0]);return 0;}
    usb_storage.pid=pid;usb_storage.fd=sockets[0];
    if (fcntl(usb_storage.fd,F_SETFD,FD_CLOEXEC)<0) {usb_storage_retire("worker-failed");return 0;}
    return 1;
}
static void usb_storage_poll(int force) {
    (void)force;
    long long now=now_ms();
    /* A task stuck in an uninterruptible driver read keeps its PID slot until
     * reaped. Never pile up replacement workers while SIGKILL is pending. */
    if (usb_storage.pid) {
        int status;pid_t found=waitpid(usb_storage.pid,&status,WNOHANG);
        if (found==usb_storage.pid || (found<0 && errno==ECHILD)) {
            int expected=usb_storage.retiring;
            usb_storage.pid=0;usb_storage.retiring=0;
            if (!expected) {usb_storage.failures++;usb_storage_retire("worker-exited");}
        }
    }
    if (usb_storage.retiring) return;
    if (usb_storage.active) {
        if (now>=usb_storage.deadline) {
            usb_storage.timeouts++;usb_storage_retire("timeout");return;
        }
        struct usb_storage_sample sample;
        ssize_t size=recv(usb_storage.fd,&sample,sizeof sample,MSG_DONTWAIT|MSG_TRUNC);
        if (size==(ssize_t)sizeof sample) {
            usb_storage.sample=sample;usb_storage.sampled=1;usb_storage.sampled_at=now;
            usb_storage.active=0;usb_storage.reason="ready";
            if (!sample.mount_known) usb_storage.mount_changed=1;
            else if (!usb_storage.initial_mount_id) usb_storage.initial_mount_id=sample.mount_id;
            else if (usb_storage.initial_mount_id!=sample.mount_id) usb_storage.mount_changed=1;
            if (sample.backing==1) {
                usb_storage.seen_open=1;
                if (sample.cable!=0) media_io_begin_pause("usb_backing_open");
            }
        } else if (size>=0 || (errno!=EAGAIN && errno!=EWOULDBLOCK && errno!=EINTR)) {
            usb_storage.failures++;usb_storage_retire("worker-failed");return;
        }
    }
    if (usb_storage.active || now<usb_storage.next_sample) return;
    if (!usb_storage.pid && !usb_storage_start()) {
        usb_storage.failures++;usb_storage.next_sample=now+USB_STORAGE_RETRY_MS;
        usb_storage_unknown("worker-unavailable");return;
    }
    unsigned char command='S';
    if (send(usb_storage.fd,&command,1,MSG_DONTWAIT|MSG_NOSIGNAL)!=1) {
        usb_storage.failures++;usb_storage_retire("worker-failed");return;
    }
    usb_storage.active=1;usb_storage.deadline=now+USB_STORAGE_DEADLINE_MS;
    usb_storage.next_sample=now+USB_STORAGE_INTERVAL_MS;
}
static void usb_storage_init(void) {
    usb_storage_poll(1);
}
static void usb_storage_request(struct mg_connection *c,struct http_message *h) {
    if (mg_vcmp(&h->method,"GET")) { reply(c,405,"{\"error\":\"GET required\"}");return; }
    usb_storage_poll(1);media_io_poll(c->mgr);
    mg_send_head(c,200,-1,"Content-Type: application/json; charset=utf-8\r\nCache-Control: no-store");
    const struct usb_storage_sample *sample=&usb_storage.sample;
    mg_printf_http_chunk(c,"{\"raw_backing\":\"%s\",\"usb_cable\":\"%s\",\"usb_mode\":",
        sample->backing<0?"unknown":sample->backing?"open":"closed",
        sample->cable<0?"unknown":sample->cable?"connected":"disconnected");
    if (sample->mode<0) mg_printf_http_chunk(c,"null");else mg_printf_http_chunk(c,"%d",sample->mode);
    mg_printf_http_chunk(c,",\"observation\":\"%s\",\"worker_running\":%s,\"sample_pending\":%s,\"timeouts\":%u,\"failures\":%u,\"sample_age_ms\":",
        usb_storage.reason,usb_storage.pid?"true":"false",usb_storage.active?"true":"false",usb_storage.timeouts,usb_storage.failures);
    if (usb_storage.sampled) mg_printf_http_chunk(c,"%lld",now_ms()-usb_storage.sampled_at);else mg_printf_http_chunk(c,"null");
    mg_printf_http_chunk(c,",\"lun_nodes_found\":%d,\"lun_read_errors\":%d,\"backing_seen_open\":%s,"
        "\"media_paused\":%s,\"mount\":{\"known\":%s,\"id\":",
        sample->nodes_found,sample->read_errors,usb_storage.seen_open?"true":"false",
        media_paused?"true":"false",sample->mount_known?"true":"false");
    if (sample->mount_known) mg_printf_http_chunk(c,"%d",sample->mount_id);else mg_printf_http_chunk(c,"null");
    mg_printf_http_chunk(c,",\"point\":");json_string(c,sample->point);
    mg_printf_http_chunk(c,",\"device\":");json_string(c,sample->device);
    mg_printf_http_chunk(c,",\"filesystem\":");json_string(c,sample->filesystem);
    mg_printf_http_chunk(c,",\"changed_since_start\":%s},\"observation_only\":true,"
        "\"stock_usb_handoff_verified\":false,\"resume_supported\":false}",usb_storage.mount_changed?"true":"false");
    mg_send_http_chunk(c,"",0);
}
