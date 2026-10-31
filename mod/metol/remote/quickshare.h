/* Async bridge to the camera's native Quick Share protocol executable.
 * No HTTP photo relay: the child opens a file from the existing gallery.
 */
#include <errno.h>
#include <fcntl.h>
#include <sys/wait.h>

enum { QS_EVENTS=32, QS_LINE=8192, QS_STDERR=32768, QS_FILES=16, QS_PATH=1024 };
static const char *qs_binary="/mod/quickshare/nx-quickshare-armv7",*qs_runner;
static struct {
    pid_t pid;
    unsigned int task_id,event_sequence,event_count,event_start,events_dropped;
    unsigned int file_count;
    uint64_t total_size;
    unsigned int sequences[QS_EVENTS];
    int output_fd,error_fd,exit_code,exit_signal,cancel_requested,output_truncated;
    long long cancelled_at;
    char operation[12],state[16],client_token[64];
    char events[QS_EVENTS][QS_LINE],line[QS_LINE],errors[QS_STDERR];
    size_t line_used,error_used;
    int dropping_line;
} qs={.output_fd=-1,.error_fd=-1,.exit_code=-1,.state="idle"};

static int qs_available(void) {
    return !access(qs_binary,X_OK) && (!qs_runner || !access(qs_runner,X_OK));
}
static void qs_event(void) {
    if (!qs.dropping_line && qs.line_used) {
        if (qs.line[qs.line_used-1]=='\r') qs.line_used--;
        qs.line[qs.line_used]=0;
        unsigned int index;
        if (qs.event_count<QS_EVENTS) index=(qs.event_start+qs.event_count++)%QS_EVENTS;
        else { index=qs.event_start;qs.event_start=(qs.event_start+1)%QS_EVENTS;qs.events_dropped++; }
        memcpy(qs.events[index],qs.line,qs.line_used+1);
        qs.sequences[index]=++qs.event_sequence;
    } else if (qs.dropping_line) { qs.events_dropped++;qs.output_truncated=1; }
    qs.line_used=0;qs.dropping_line=0;
}
static void qs_drain(int *fd,int output) {
    if (*fd<0) return;
    char data[4096];ssize_t bytes;
    while ((bytes=read(*fd,data,sizeof data))>0) {
        if (output) for (ssize_t i=0;i<bytes;i++) {
            if (data[i]=='\n') qs_event();
            else if (!qs.dropping_line) {
                if (qs.line_used<QS_LINE-1) qs.line[qs.line_used++]=data[i];
                else qs.dropping_line=1;
            }
        } else {
            size_t remove=qs.error_used+(size_t)bytes>=QS_STDERR ? qs.error_used+(size_t)bytes-QS_STDERR+1 : 0;
            if (remove) { memmove(qs.errors,qs.errors+remove,qs.error_used-remove);qs.error_used-=remove;qs.output_truncated=1; }
            memcpy(qs.errors+qs.error_used,data,(size_t)bytes);qs.error_used+=(size_t)bytes;qs.errors[qs.error_used]=0;
        }
    }
    if (!bytes || (bytes<0 && errno!=EAGAIN && errno!=EWOULDBLOCK && errno!=EINTR)) {
        close(*fd);*fd=-1;
        if (output && (qs.line_used || qs.dropping_line)) qs_event();
    }
}
static void qs_poll(void) {
    qs_drain(&qs.output_fd,1);qs_drain(&qs.error_fd,0);
    if (!qs.pid) return;
    if (qs.cancel_requested && now_ms()-qs.cancelled_at>2000) kill(qs.pid,SIGKILL);
    int status;pid_t result=waitpid(qs.pid,&status,WNOHANG);
    if (result==qs.pid) {
        qs.pid=0;
        if (WIFEXITED(status)) { qs.exit_code=WEXITSTATUS(status);strcpy(qs.state,qs.cancel_requested?"cancelled":"exited"); }
        else if (WIFSIGNALED(status)) { qs.exit_signal=WTERMSIG(status);strcpy(qs.state,qs.cancel_requested?"cancelled":"signaled"); }
        qs_drain(&qs.output_fd,1);qs_drain(&qs.error_fd,0);
    }
}
static void qs_status(struct mg_connection *c,int code) {
    qs_poll();
    mg_printf(c,"HTTP/1.1 %d %s\r\nContent-Type: application/json; charset=utf-8\r\nTransfer-Encoding: chunked\r\nCache-Control: no-store\r\n\r\n",code,code==202?"Accepted":"OK");
    mg_printf_http_chunk(c,"{\"available\":%s,\"running\":%s,\"task_id\":%u,\"operation\":",qs_available()?"true":"false",qs.pid?"true":"false",qs.task_id);
    json_string(c,qs.operation);
    mg_printf_http_chunk(c,",\"client_token\":");json_string(c,qs.client_token);
    mg_printf_http_chunk(c,",\"file_count\":%u,\"total_size\":%llu",qs.file_count,(unsigned long long)qs.total_size);
    mg_printf_http_chunk(c,",\"state\":");json_string(c,qs.state);
    mg_printf_http_chunk(c,",\"exit_code\":");
    if (qs.exit_code<0) mg_printf_http_chunk(c,"null");else mg_printf_http_chunk(c,"%d",qs.exit_code);
    mg_printf_http_chunk(c,",\"signal\":");
    if (!qs.exit_signal) mg_printf_http_chunk(c,"null");else mg_printf_http_chunk(c,"%d",qs.exit_signal);
    mg_printf_http_chunk(c,",\"cancel_requested\":%s,\"events_dropped\":%u,\"output_truncated\":%s,\"events\":[",qs.cancel_requested?"true":"false",qs.events_dropped,qs.output_truncated?"true":"false");
    for (unsigned int i=0;i<qs.event_count;i++) {
        unsigned int index=(qs.event_start+i)%QS_EVENTS;
        mg_printf_http_chunk(c,"%s{\"sequence\":%u,\"line\":",i?",":"",qs.sequences[index]);json_string(c,qs.events[index]);mg_printf_http_chunk(c,"}");
    }
    mg_printf_http_chunk(c,"],\"stderr\":");
    const unsigned char *errors=(unsigned char *)qs.errors;
    while ((*errors&0xc0)==0x80) errors++; /* Tail may begin inside a UTF-8 codepoint. */
    json_string(c,(const char *)errors);mg_printf_http_chunk(c,"}");mg_send_http_chunk(c,"",0);
}
static int qs_endpoint(const char *value) {
    char ip[64];const char *colon=strchr(value,':');
    if (!colon || colon==value || colon-value>=(int)sizeof ip) return 0;
    memcpy(ip,value,(size_t)(colon-value));ip[colon-value]=0;
    struct in_addr address;if (inet_pton(AF_INET,ip,&address)!=1 || !colon[1]) return 0;
    for (const char *p=colon+1;*p;p++) if (!isdigit((unsigned char)*p)) return 0;
    char *end;errno=0;long port=strtol(colon+1,&end,10);
    return !errno && !*end && port>0 && port<=65535;
}
static int qs_launch(const char *operation,char *const arguments[]) {
    int output[2],errors[2];
    if (pipe(output)) return 0;
    if (pipe(errors)) { close(output[0]);close(output[1]);return 0; }
    pid_t pid=fork();
    if (pid<0) { close(output[0]);close(output[1]);close(errors[0]);close(errors[1]);return 0; }
    if (!pid) {
        signal(SIGINT,SIG_DFL);signal(SIGTERM,SIG_DFL);signal(SIGPIPE,SIG_DFL);
        dup2(output[1],STDOUT_FILENO);dup2(errors[1],STDERR_FILENO);
        int input=open("/dev/null",O_RDONLY);if (input>=0) { dup2(input,STDIN_FILENO);if (input>2) close(input); }
        DIR *fds=opendir("/proc/self/fd");
        if (fds) { struct dirent *entry;int keep=dirfd(fds);while ((entry=readdir(fds))) { int fd=atoi(entry->d_name);if (fd>2 && fd!=keep) close(fd); }closedir(fds); }
        else { long limit=sysconf(_SC_OPEN_MAX);for (int fd=3;fd<limit;fd++) close(fd); }
        execv(arguments[0],arguments);
        fprintf(stderr,"Quick Share exec failed: %s\n",strerror(errno));_exit(127);
    }
    close(output[1]);close(errors[1]);
    if (qs.output_fd>=0) close(qs.output_fd);
    if (qs.error_fd>=0) close(qs.error_fd);
    unsigned int task_id=qs.task_id+1;memset(&qs,0,sizeof qs);
    qs.task_id=task_id;qs.pid=pid;qs.output_fd=output[0];qs.error_fd=errors[0];qs.exit_code=-1;
    strcpy(qs.operation,operation);strcpy(qs.state,"running");
    fcntl(qs.output_fd,F_SETFL,O_NONBLOCK);fcntl(qs.error_fd,F_SETFL,O_NONBLOCK);
    return 1;
}
/* Send accepts legacy path OR path_count=1..16 plus exactly path0..pathN-1.
 * Keys/values are decoded with the existing Mongoose form decoder. Reject
 * ambiguous fields before media validation or fork; no partially valid batch
 * is submitted. Duplicate resolved files (including hardlink aliases) fail. */
static int qs_paths(struct http_message *h,char relative[QS_FILES][QS_PATH]) {
    if (!h->body.len || h->body.len>65536 || memchr(h->body.p,0,h->body.len)) return 0;
    unsigned int mask=0;int single=0,count_seen=0,count=0,endpoint=0,name=0,token=0;
    const char *cursor=h->body.p,*end=cursor+h->body.len;
    while (cursor<end) {
        const char *stop=memchr(cursor,'&',(size_t)(end-cursor));if (!stop) stop=end;
        const char *equal=memchr(cursor,'=',(size_t)(stop-cursor));
        char key[32],value[QS_PATH];
        if (!equal) return 0;
        int key_length=mg_url_decode(cursor,(int)(equal-cursor),key,sizeof key,1);
        if (key_length<=0 || (size_t)key_length!=strlen(key)) return 0;
        for (int i=0;i<key_length;i++) key[i]=(char)tolower((unsigned char)key[i]);
        if (!strcmp(key,"endpoint")) { if (endpoint++) return 0; }
        else if (!strcmp(key,"name")) { if (name++) return 0; }
        else if (!strcmp(key,"client_token")) { if (token++) return 0; }
        else if (!strncmp(key,"path",4)) {
            int length=mg_url_decode(equal+1,(int)(stop-equal-1),value,sizeof value,1);
            if (length<=0 || (size_t)length!=strlen(value)) return 0;
            if (!strcmp(key,"path")) {
                if (single || count_seen || mask) return 0;
                single=1;strcpy(relative[0],value);
            } else if (!strcmp(key,"path_count")) {
                if (single || count_seen++) return 0;
                for (int i=0;i<length;i++) {
                    if (!isdigit((unsigned char)value[i])) return 0;
                    count=count*10+(value[i]-'0');if (count>QS_FILES) return 0;
                }
                if (!count) return 0;
            } else {
                const char *index=key+4;unsigned int number=0;
                if (single || !*index || (*index=='0' && index[1])) return 0;
                for (;*index;index++) {
                    if (!isdigit((unsigned char)*index)) return 0;
                    number=number*10+(unsigned int)(*index-'0');if (number>=QS_FILES) return 0;
                }
                if (mask&(1u<<number)) return 0;
                mask|=1u<<number;strcpy(relative[number],value);
            }
        }
        cursor=stop+(stop<end);
    }
    if (single) return 1;
    return count_seen && mask==((1u<<count)-1u)?count:0;
}
static void qs_request(struct mg_connection *c,struct http_message *h,const char *uri) {
    qs_poll();
    if (!strcmp(uri,"/api/v1/quickshare/status")) {
        if (mg_vcmp(&h->method,"GET")) reply(c,405,"{\"error\":\"GET required\"}");else qs_status(c,200);
        return;
    }
    if (mg_vcmp(&h->method,"POST")) { reply(c,405,"{\"error\":\"POST required\"}");return; }
    if (!strcmp(uri,"/api/v1/quickshare/cancel")) {
        char expected[24];int length=mg_get_http_var(&h->body,"task_id",expected,sizeof expected);
        if (length!=-1) {
            char *end;unsigned long id;
            if (length<=0) { reply(c,400,"{\"error\":\"invalid task_id\"}");return; }
            errno=0;id=strtoul(expected,&end,10);
            if (errno || *end || id!=qs.task_id) { reply(c,409,"{\"error\":\"Quick Share task changed\"}");return; }
        }
        if (qs.pid && !qs.cancel_requested) { qs.cancel_requested=1;qs.cancelled_at=now_ms();kill(qs.pid,SIGTERM); }
        qs_status(c,200);return;
    }
    if (strcmp(uri,"/api/v1/quickshare/discover") && strcmp(uri,"/api/v1/quickshare/send")) { reply(c,404,"{\"error\":\"unknown Quick Share operation\"}");return; }
    if (!qs_available()) { reply(c,503,"{\"error\":\"native Quick Share executable unavailable\"}");return; }
    if (qs.pid) { reply(c,409,"{\"error\":\"Quick Share task already running\"}");return; }
    /* Correlate a lost launch reply without claiming another client's task.
     * This is optional for existing clients, and is not an authorization token. */
    char client_token[64]="";
    int token_length=mg_get_http_var(&h->body,"client_token",client_token,sizeof client_token);
    if (token_length!=-1) {
        if (token_length<=0 || (size_t)token_length!=strlen(client_token)) { reply(c,400,"{\"error\":\"invalid client_token\"}");return; }
        for (int i=0;i<token_length;i++) if (!isalnum((unsigned char)client_token[i]) && client_token[i]!='-') { reply(c,400,"{\"error\":\"invalid client_token\"}");return; }
    }
    char *arguments[QS_FILES+16];int count=0;
    if (qs_runner) { arguments[count++]=(char *)qs_runner;arguments[count++]="-cpu";arguments[count++]="cortex-a9";arguments[count++]="-L";arguments[count++]="/"; }
    arguments[count++]=(char *)qs_binary;
    char endpoint[80],name[256],relative[QS_FILES][QS_PATH],paths[QS_FILES][PATH_MAX],duration[16]="15";
    struct stat identities[QS_FILES];int files=0;uint64_t total_size=0;
    const char *operation;
    if (!strcmp(uri,"/api/v1/quickshare/discover")) {
        int n=mg_get_http_var(&h->body,"duration",duration,sizeof duration);
        if (n>=0) {
            char *end;long seconds=strtol(duration,&end,10);
            if (!n || *end || seconds<1 || seconds>60) { reply(c,400,"{\"error\":\"duration must be 1..60 seconds\"}");return; }
        } else if (n!=-1) { reply(c,400,"{\"error\":\"invalid duration\"}");return; }
        else strcpy(duration,"15");
        operation="discover";arguments[count++]="discover";arguments[count++]=duration;
    } else {
        int a=mg_get_http_var(&h->body,"endpoint",endpoint,sizeof endpoint),b=mg_get_http_var(&h->body,"name",name,sizeof name);
        files=qs_paths(h,relative);
        if (a<=0 || (size_t)a!=strlen(endpoint) || !qs_endpoint(endpoint) || b<=0 || b>255 || (size_t)b!=strlen(name) || !files) { reply(c,400,"{\"error\":\"endpoint, receiver name and path or path_count with1..16 indexed paths required; do not mix or repeat fields\"}");return; }
        for (int i=0;i<b;i++) if ((unsigned char)name[i]<32 || name[i]==127) { reply(c,400,"{\"error\":\"invalid receiver name\"}");return; }
        for (int i=0;i<files;i++) {
            struct stat *st=&identities[i];
            if (!media_path(relative[i],paths[i]) || stat(paths[i],st) || !S_ISREG(st->st_mode) || access(paths[i],R_OK) || st->st_size<0 || (uint64_t)st->st_size>INT64_MAX-total_size) { reply(c,400,"{\"error\":\"each path must be a readable regular gallery file; total size must fit signed64 bits\"}");return; }
            for (int j=0;j<i;j++) if (!strcmp(paths[i],paths[j]) || (st->st_dev==identities[j].st_dev && st->st_ino==identities[j].st_ino)) { reply(c,400,"{\"error\":\"duplicate gallery file\"}");return; }
            total_size+=(uint64_t)st->st_size;
        }
        operation="send";arguments[count++]="send";arguments[count++]=endpoint;arguments[count++]=name;
        for (int i=0;i<files;i++) arguments[count++]=paths[i];
    }
    arguments[count]=NULL;
    if (!qs_launch(operation,arguments)) { reply(c,500,"{\"error\":\"Quick Share process could not start\"}");return; }
    strcpy(qs.client_token,client_token);
    qs.file_count=(unsigned int)files;qs.total_size=total_size;
    qs_status(c,202);
}
static void qs_shutdown(void) {
    if (qs.pid) {
        kill(qs.pid,SIGTERM);
        long long deadline=now_ms()+2000;
        while (qs.pid && now_ms()<deadline) { qs_poll();usleep(10000); }
        if (qs.pid) { kill(qs.pid,SIGKILL);int status;while (waitpid(qs.pid,&status,0)<0 && errno==EINTR) {}qs.pid=0; }
    }
    if (qs.output_fd>=0) close(qs.output_fd);
    if (qs.error_fd>=0) close(qs.error_fd);
}
