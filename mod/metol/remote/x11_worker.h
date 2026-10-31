/* A single disposable X client owns all synchronous Xlib calls. The HTTP
 * loop never waits for X: fixed-size datagrams and a shared completed frame
 * preserve the wire protocol without copying a frame through a blocking pipe. */
#include <sys/prctl.h>
enum { XW_CAPTURE=1,XW_INPUT,XW_RELEASE,XW_QUEUE=8,XW_DEADLINE=1500,XW_IDLE=1000 };
struct xw_command { int kind;char query[1024]; };
struct xw_result { int code,fn,shutter; };
struct xw_shared {
    struct block blocks[BLOCKS];
    uint32_t hashes[BLOCKS];
    volatile unsigned char keys[256],buttons[8];
};
struct xw_job {
    struct xw_command command;
    struct mg_connection *connection;
    long long deadline;
    uint32_t previous[BLOCKS];
};
static struct {
    struct xw_shared *shared;
    struct xw_job jobs[XW_QUEUE];
    int count,fd,active,retiring;
    pid_t pid;
    long long retry_at,last_activity;
} xw={.fd=-1};
static int panel_shortcut_allowed(void);
static int xw_has_held(void) {
    if (!xw.shared) return 0;
    for (int i=1;i<256;i++) if (held_keys[i]) return 1;
    for (int i=1;i<8;i++) if (held_buttons[i]) return 1;
    return 0;
}

static void xw_child(int fd,pid_t parent) {
    /* A killed supervisor must not leave an X client or inherited SD files.
     * Recheck PPID after installing PDEATHSIG to close the fork/prctl race. */
    if (prctl(PR_SET_PDEATHSIG,SIGKILL) || getppid()!=parent) _exit(125);
    prctl(PR_SET_NAME,"nx-x11-worker",0,0,0);
    signal(SIGINT,SIG_DFL);signal(SIGTERM,SIG_DFL);signal(SIGCHLD,SIG_DFL);
    if (chdir("/")) _exit(125);
    DIR *directory=opendir("/proc/self/fd");
    if (!directory) _exit(125);
    int scan_fd=dirfd(directory);struct dirent *entry;
    while ((entry=readdir(directory))) {
        char *end;long value=strtol(entry->d_name,&end,10);
        if (*entry->d_name && !*end && value>=0 && value<=INT_MAX && value!=fd && value!=scan_fd) close((int)value);
    }
    closedir(directory);
    int nullfd=open("/dev/null",O_RDWR);
    if (nullfd>=0) {
        for (int i=0;i<3;i++) if (i!=fd) dup2(nullfd,i);
        if (nullfd>2 && nullfd!=fd) close(nullfd);
    }
    display=XOpenDisplay(NULL);
    if (!display) _exit(3);
    /* The previous client can have timed out after a key-down reached X.
     * Keep its held-key record across replacement and release it first. */
    release_inputs_x11();
    for (;;) {
        struct xw_command command;struct xw_result result={.code=503};
        ssize_t received;
        do { received=recv(fd,&command,sizeof command,0); } while (received<0 && errno==EINTR);
        if (received!=sizeof command) _exit(0);
        if (command.kind==XW_CAPTURE) {
            if (capture()) {
                memcpy(xw.shared->blocks,blocks,sizeof blocks);
                memcpy(xw.shared->hashes,frame_hashes,sizeof frame_hashes);
                result.code=200;
            }
        } else if (command.kind==XW_INPUT) result.code=inject_x11(command.query);
        else if (command.kind==XW_RELEASE) { release_inputs_x11();result.code=200; }
        if (send(fd,&result,sizeof result,MSG_NOSIGNAL)!=sizeof result) _exit(0);
    }
}
static int xw_init(void) {
    xw.shared=mmap(NULL,sizeof *xw.shared,PROT_READ|PROT_WRITE,MAP_SHARED|MAP_ANONYMOUS,-1,0);
    if (xw.shared==MAP_FAILED) { xw.shared=NULL;return 0; }
    held_keys=xw.shared->keys;held_buttons=xw.shared->buttons;return 1;
}
static int xw_start(void) {
    if (xw.pid || now_ms()<xw.retry_at) return 0;
    int sockets[2];if (socketpair(AF_UNIX,SOCK_DGRAM,0,sockets)) return 0;
    pid_t parent=getpid(),pid=fork();
    if (!pid) { close(sockets[0]);xw_child(sockets[1],parent);_exit(125); }
    close(sockets[1]);
    if (pid<0) { close(sockets[0]);return 0; }
    xw.fd=sockets[0];xw.pid=pid;
    fcntl(xw.fd,F_SETFD,FD_CLOEXEC);
    int flags=fcntl(xw.fd,F_GETFL,0);
    if (flags<0 || fcntl(xw.fd,F_SETFL,flags|O_NONBLOCK)<0) {
        close(xw.fd);xw.fd=-1;kill(xw.pid,SIGKILL);xw.retiring=1;return 0;
    }
    fprintf(stderr,"NX_X11_WORKER pid=%ld parent=%ld\n",(long)pid,(long)parent);return 1;
}
static void xw_complete(int code,int fn,int shutter) {
    struct xw_job *job=&xw.jobs[0];
    if (job->connection) {
        if (code==200 && job->command.kind==XW_CAPTURE) osd_reply(job->connection,job->previous);
        else if (code==200) reply(job->connection,200,"{\"ok\":true}");
        else if (code==400) reply(job->connection,400,"{\"error\":\"invalid or unmapped input\"}");
        else reply(job->connection,503,"{\"error\":\"X11 operation unavailable or timed out\",\"code\":\"x11_unavailable\"}");
    }
    memmove(xw.jobs,xw.jobs+1,(--xw.count)*sizeof xw.jobs[0]);xw.active=0;xw.last_activity=now_ms();
}
static void xw_fail(void) {
    if (xw.fd>=0) close(xw.fd);
    xw.fd=-1;
    if (xw.pid && !xw.retiring) kill(xw.pid,SIGKILL);
    xw.retiring=xw.pid!=0;xw.retry_at=now_ms()+5000;
    frame_ready=0;
    /* Never replay an input whose acknowledgement was lost. */
    while (xw.count) xw_complete(503,0,0);
}
static void xw_poll(void) {
    if (xw.pid) {
        int status;pid_t found=waitpid(xw.pid,&status,WNOHANG);
        if (found==xw.pid || (found<0 && errno==ECHILD)) {
            int idle_close=xw.retiring==2;
            xw.pid=0;xw.retiring=0;
            if (!idle_close) xw_fail();
        }
    }
    if (xw.active) {
        struct xw_result result;ssize_t size=recv(xw.fd,&result,sizeof result,MSG_DONTWAIT);
        if (size==sizeof result) {
            if (now_ms()>=xw.jobs[0].deadline) { xw_fail();return; }
            if (result.code==200 && xw.jobs[0].command.kind==XW_CAPTURE) {
                memcpy(blocks,xw.shared->blocks,sizeof blocks);
                memcpy(frame_hashes,xw.shared->hashes,sizeof frame_hashes);
                frame_ready=1;frame_time=now_ms();
            }
            xw_complete(result.code,result.fn,result.shutter);
        } else if ((size<0 && errno!=EAGAIN && errno!=EWOULDBLOCK && errno!=EINTR) || size>=0) { xw_fail();return; }
    }
    while (xw.count && !xw.active) {
        struct xw_job *job=&xw.jobs[0];
        if (now_ms()>=job->deadline) { xw_complete(503,0,0);continue; }
        if (job->command.kind==XW_CAPTURE && frame_ready && now_ms()-frame_time<200) { xw_complete(200,0,0);continue; }
        if ((!xw.pid && !xw_start()) || xw.retiring) { xw_complete(503,0,0);continue; }
        ssize_t size=send(xw.fd,&job->command,sizeof job->command,MSG_DONTWAIT|MSG_NOSIGNAL);
        if (size!=sizeof job->command) { xw_fail();return; }
        xw.active=1;
    }
    if (xw.active && now_ms()>=xw.jobs[0].deadline) xw_fail();
    if (xw.pid && !xw.retiring && !xw.count && !xw_has_held() && now_ms()-xw.last_activity>=XW_IDLE) {
        /* No XCloseDisplay round trip: this client has no pending operation
         * or synthetic held input, so closing it cannot strand our key-down. */
        close(xw.fd);xw.fd=-1;kill(xw.pid,SIGKILL);xw.retiring=2;frame_ready=0;
    }
}
static struct xw_job *xw_enqueue(int kind,struct mg_connection *c) {
    if (xw.count==XW_QUEUE || xw.retiring || now_ms()<xw.retry_at) {
        if (c) reply(c,503,"{\"error\":\"X11 worker busy or recovering\",\"code\":\"x11_unavailable\"}");
        return NULL;
    }
    struct xw_job *job=&xw.jobs[xw.count++];memset(job,0,sizeof *job);
    job->command.kind=kind;job->connection=c;job->deadline=now_ms()+XW_DEADLINE;return job;
}
static int xw_pending(struct mg_connection *c) {
    for (int i=0;i<xw.count;i++) if (xw.jobs[i].connection==c) return 1;
    return 0;
}
static void xw_closed(struct mg_connection *c) {
    for (int i=0;i<xw.count;i++) if (xw.jobs[i].connection==c) {
        xw.jobs[i].connection=NULL;
        if (!i && xw.active) continue;
        memmove(xw.jobs+i,xw.jobs+i+1,(xw.count-i-1)*sizeof xw.jobs[0]);xw.count--;i--;
    }
}
static void inject(struct mg_connection *c,struct http_message *h) {
    if (h->query_string.len>=sizeof xw.jobs[0].command.query) { reply(c,400,"{\"error\":\"input query too long\"}");return; }
    struct xw_job *job=xw_enqueue(XW_INPUT,c);if (!job) return;
    memcpy(job->command.query,h->query_string.p,h->query_string.len);
    job->command.query[h->query_string.len]=0;input_alive=now_ms();
}
static void osd(struct mg_connection *c,struct http_message *h) {
    uint32_t previous[BLOCKS]={0};char hashes[BLOCKS*8+1];
    int length=mg_get_http_var(&h->body,"hashs",hashes,sizeof hashes);
    if (length>=0) {
        if (length!=BLOCKS*8) { reply(c,400,"{\"error\":\"invalid OSD hashes\"}");return; }
        for (int i=0;i<BLOCKS;i++) {
            char hex[9];memcpy(hex,hashes+i*8,8);hex[8]=0;
            for (int k=0;k<8;k++) if (!isxdigit((unsigned char)hex[k])) { reply(c,400,"{\"error\":\"invalid OSD hash\"}");return; }
            previous[i]=(uint32_t)strtoul(hex,NULL,16);
        }
    }
    /* Do not retain multiple full frames for a client that stopped reading. */
    if (c->send_mbuf.len) { c->flags|=MG_F_CLOSE_IMMEDIATELY;return; }
    struct xw_job *job=xw_enqueue(XW_CAPTURE,c);
    if (job) memcpy(job->previous,previous,sizeof previous);
}
static void release_inputs(void) {
    /* A media pause must discard controls not yet sent to the camera. The
     * active command, if any, is followed by release in the same FIFO. */
    while (xw.count>(xw.active?1:0)) {
        struct xw_job *job=&xw.jobs[--xw.count];
        if (job->connection) reply(job->connection,503,"{\"error\":\"input cancelled\"}");
    }
    xw_enqueue(XW_RELEASE,NULL);
}
static void xw_input_watchdog(void) {
    if (!xw_has_held()) return;
    if (xw.pid && now_ms()-input_alive<=5000) return;
    for (int i=0;i<xw.count;i++) if (xw.jobs[i].command.kind==XW_RELEASE) return;
    /* A timed-out helper retains its ledger. Recovery sends only key-up;
     * it never repeats the unacknowledged original key-down. */
    xw_enqueue(XW_RELEASE,NULL);
}
static void xw_shutdown(void) {
    /* Normal exit releases held input when X is responsive, with a bounded
     * shutdown wait. A wedged X server can never delay process termination. */
    if (xw.shared && xw.pid && !xw.retiring) {
        release_inputs();long long deadline=now_ms()+XW_DEADLINE;
        while (xw.count && now_ms()<deadline) { xw_poll();usleep(10000); }
    }
    xw_fail();
    if (xw.shared) munmap(xw.shared,sizeof *xw.shared);
}
