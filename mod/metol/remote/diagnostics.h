/* RAM logs stay available over the existing connection without exporting
 * arbitrary paths or writing diagnostic snapshots back to the SD card. */
#include <fcntl.h>
#include <sys/vfs.h>

static const char *diagnostic_names[]={
    "mfw-entry.log", "remote.log", "supervisor.log", "service.state",
    "remote.pid", "latest-diagnostic", "bootstrap.log"
};
static int diagnostic_open(const char *state,const char *name,struct stat *info) {
    char path[PATH_MAX];
    const char *base=state;
    if (!strcmp(name,"bootstrap.log")) {
        base=getenv("NX300M_MFW_RAM_BASE");
        if (!base || *base!='/') return -1;
    }
    int size=snprintf(path,sizeof path,"%s/%s",base,name);
    if (size<0 || size>=(int)sizeof path) return -1;
    int fd=open(path,O_RDONLY|O_NOFOLLOW|O_NONBLOCK);
    if (fd<0) return -1;
    struct statfs fs;
    if (fstat(fd,info) || !S_ISREG(info->st_mode) || fstatfs(fd,&fs) ||
        (unsigned long)fs.f_type!=0x01021994UL) { close(fd);return -1; }
    return fd;
}
static void diagnostic_request(struct mg_connection *c,struct http_message *h) {
    if (mg_vcmp(&h->method,"GET")) { reply(c,405,"{\"error\":\"GET required\"}");return; }
    const char *configured=getenv("NX_STATE_DIR");char state[PATH_MAX];struct statfs fs;
    if (!configured || !realpath(configured,state) || statfs(state,&fs) ||
        (unsigned long)fs.f_type!=0x01021994UL) {
        reply(c,503,"{\"error\":\"RAM diagnostics unavailable\"}");return;
    }
    char name[64];
    int length=mg_get_http_var(&h->query_string,"file",name,sizeof name);
    if (length==-1) {
        mg_send_head(c,200,-1,"Content-Type: application/json; charset=utf-8\r\nCache-Control: no-store");
        mg_printf_http_chunk(c,"{\"ok\":true,\"storage\":\"tmpfs\",\"files\":[");
        for (unsigned i=0;i<sizeof diagnostic_names/sizeof *diagnostic_names;i++) {
            struct stat info;int fd=diagnostic_open(state,diagnostic_names[i],&info);
            if (i) mg_printf_http_chunk(c,",");
            mg_printf_http_chunk(c,"{\"name\":");json_string(c,diagnostic_names[i]);
            mg_printf_http_chunk(c,",\"available\":%s,\"bytes\":%lld}",fd>=0?"true":"false",fd>=0?(long long)info.st_size:0LL);
            if (fd>=0) close(fd);
        }
        mg_printf_http_chunk(c,"]}");mg_send_http_chunk(c,"",0);return;
    }
    if (length<=0 || (int)strlen(name)!=length) {
        reply(c,400,"{\"error\":\"invalid diagnostic name\"}");return;
    }
    unsigned i;
    for (i=0;i<sizeof diagnostic_names/sizeof *diagnostic_names;i++) if (!strcmp(name,diagnostic_names[i])) break;
    if (i==sizeof diagnostic_names/sizeof *diagnostic_names) {
        reply(c,404,"{\"error\":\"diagnostic not listed\"}");return;
    }
    struct stat info;int fd=diagnostic_open(state,name,&info);
    if (fd<0) { reply(c,404,"{\"error\":\"diagnostic unavailable\"}");return; }
    /* Bound memory and send time even if a long-running log has grown. */
    off_t offset=info.st_size>262144?info.st_size-262144:0;
    if (lseek(fd,offset,SEEK_SET)<0) { close(fd);reply(c,503,"{\"error\":\"diagnostic read failed\"}");return; }
    char headers[256];snprintf(headers,sizeof headers,
        "Content-Type: text/plain; charset=utf-8\r\nCache-Control: no-store\r\nX-Log-Offset: %lld",(long long)offset);
    mg_send_head(c,200,-1,headers);
    char block[4096];off_t left=info.st_size-offset;
    while (left>0) {
        ssize_t got=read(fd,block,left<(off_t)sizeof block?(size_t)left:sizeof block);
        if (got<=0) break;
        mg_send_http_chunk(c,block,(size_t)got);left-=got;
    }
    close(fd);mg_send_http_chunk(c,"",0);
}
