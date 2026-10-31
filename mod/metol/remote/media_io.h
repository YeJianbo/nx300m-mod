/* Quiesce this service's SD users, not the stock app or USB controller.
 * The pause persists in RAM across service restarts. No automatic resume is
 * offered until the stock unmount/UMS/remount handoff can be verified. */
#include <sys/file.h>
#include <sys/vfs.h>
static struct {
    int available,lock_fd,locked,persisted;
    char marker[PATH_MAX];
    const char *reason;
} media_io={.lock_fd=-1};

static int media_io_tmpfs(const char *path) {
    struct statfs fs;
    return path && !statfs(path,&fs) && (unsigned long)fs.f_type==0x01021994UL;
}
static int media_io_init(const char *web) {
    const char *state=getenv("NX_STATE_DIR");char path[PATH_MAX];
    /* A non-RAM deployment retains its existing API, but cannot claim release. */
    if (!media_io_tmpfs(state) || !media_io_tmpfs(nx_mod_root()) || !media_io_tmpfs(web)) return 1;
    int n=snprintf(media_io.marker,sizeof media_io.marker,"%s/media-paused",state);
    if (n<0 || n>=(int)sizeof media_io.marker) return 0;
    struct stat info;
    if (!lstat(media_io.marker,&info)) {
        if (!S_ISREG(info.st_mode)) return 0;
        media_paused=1;media_io.persisted=1;media_io.reason="restored";
    } else if (errno!=ENOENT) return 0;
    n=snprintf(path,sizeof path,"%s/media-io.lock",state);
    if (n<0 || n>=(int)sizeof path) return 0;
    media_io.lock_fd=open(path,O_RDWR|O_CREAT|O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC,0600);
    struct statfs fs;
    if (media_io.lock_fd<0 || fstat(media_io.lock_fd,&info) || !S_ISREG(info.st_mode) ||
        fstatfs(media_io.lock_fd,&fs) || (unsigned long)fs.f_type!=0x01021994UL) return 0;
    media_io.available=1;return 1;
}
static int media_io_begin_pause(const char *reason) {
    if (!media_paused) { media_paused=1;gp_clear();release_inputs(); }
    media_io.reason=reason;
    if (!media_io.available) return 0;
    if (!media_io.persisted) {
        int fd=open(media_io.marker,O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600);
        if (fd<0) return 0;
        close(fd);media_io.persisted=1;
    }
    return 1;
}
static int media_io_allowed(const char *uri) {
    return !strcmp(uri,"/api/v1/health") || !strcmp(uri,"/api/v1/diagnostics") ||
        !strcmp(uri,"/api/v1/storage/status") || !strcmp(uri,"/api/v1/network/status") ||
        (media_io.available && !strcmp(uri,"/api/v1/camera/info")) ||
        !strcmp(uri,"/api/v1/camera/status") ||
        !strcmp(uri,"/api/v1/quickshare/status") || !strcmp(uri,"/api/v1/quickshare/cancel") ||
        (strncmp(uri,"/api/",5) && strncmp(uri,"/cgi-bin/",9) && strncmp(uri,"/DCIM/",6));
}
static unsigned media_io_connections(struct mg_mgr *manager) {
    unsigned count=0;
    for (struct mg_connection *c=manager->active_connections;c;c=c->next)
        if (c->flags&MG_F_USER_2) count++;
    return count;
}
static void media_io_poll(struct mg_mgr *manager) {
    if (!media_paused) return;
    if (media_io.lock_fd>=0 && !media_io.locked && !flock(media_io.lock_fd,LOCK_EX|LOCK_NB)) media_io.locked=1;
    for (struct mg_connection *c=manager->active_connections;c;c=c->next)
        if (c->flags&MG_F_USER_2) c->flags|=MG_F_CLOSE_IMMEDIATELY;
    if (qs.pid && !qs.cancel_requested) {
        qs.cancel_requested=1;qs.cancelled_at=now_ms();kill(qs.pid,SIGTERM);
    }
}
static void media_io_status(struct mg_connection *c,int code) {
    unsigned connections=media_io_connections(c->mgr);
    /* Do not report from MG_EV_CLOSE: Mongoose frees its file AFTER that
     * callback. Here the closed connection has left the event loop entirely. */
    int released=media_paused && media_io.persisted && media_io.locked && !connections && !qs.pid;
    char json[640];
    snprintf(json,sizeof json,
        "{\"available\":%s,\"paused\":%s,\"service_media_released\":%s,"
        "\"file_connections\":%u,\"quickshare_running\":%s,\"panel_actions_blocked\":%s,"
        "\"pause_persisted\":%s,\"pause_reason\":\"%s\","
        "\"scope\":\"remote_service_only\",\"stock_usb_handoff_verified\":false,\"resume_supported\":false}",
        media_io.available?"true":"false",media_paused?"true":"false",released?"true":"false",
        connections,qs.pid?"true":"false",media_io.locked?"true":"false",
        media_io.persisted?"true":"false",media_io.reason?media_io.reason:"none");
    reply(c,code,json);
}
static void media_io_request(struct mg_connection *c,struct http_message *h,const char *uri) {
    if (!strcmp(uri,"/api/v1/media/status")) {
        if (mg_vcmp(&h->method,"GET")) reply(c,405,"{\"error\":\"GET required\"}");
        else media_io_status(c,200);
        return;
    }
    if (strcmp(uri,"/api/v1/media/pause")) { reply(c,404,"{\"error\":\"unknown media operation\"}");return; }
    if (mg_vcmp(&h->method,"POST")) { reply(c,405,"{\"error\":\"POST required\"}");return; }
    if (!gallery_edit_origin(c,h)) return;
    if (!media_io.available) { reply(c,503,"{\"error\":\"RAM runtime required\"}");return; }
    if ((!media_paused || !media_io.persisted) &&
        !media_io_begin_pause(media_io.reason ? media_io.reason : "manual")) {
        media_io_poll(c->mgr);
        reply(c,503,"{\"error\":\"cannot persist RAM pause; access remains blocked\"}");return;
    }
    media_io_poll(c->mgr);
    media_io_status(c,202);
}
