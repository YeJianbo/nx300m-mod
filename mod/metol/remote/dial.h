/* ui_mod's existing key filter consumes this session flag directly. */
static int dl_parse(struct http_message *h,int *enabled) {
    if (!h->body.len || h->body.len>=128) return 0;
    char body[128],key[32],value[16];memcpy(body,h->body.p,h->body.len);body[h->body.len]=0;
    if (strlen(body)!=h->body.len) return 0;
    const char *p=body;int quoted;ex_space(&p);if (*p++!='{') return 0;
    if (!ex_token(&p,key,sizeof key,&quoted) || !quoted || strcmp(key,"enabled")) return 0;
    ex_space(&p);if (*p++!=':') return 0;
    if (!ex_token(&p,value,sizeof value,&quoted) || quoted) return 0;
    if (!strcmp(value,"true")) *enabled=1;
    else if (!strcmp(value,"false")) *enabled=0;
    else return 0;
    ex_space(&p);if (*p++!='}') return 0;ex_space(&p);return !*p;
}
/* Missing is the stock ui_mod default. A present malformed flag is an error,
 * even though the stock key filter would otherwise treat it as unlocked. */
static int dl_read(int *enabled) {
    FILE *file=fopen("/var/run/nx300m-dial-lock","r");
    if (!file) { if (errno!=ENOENT) return 2;*enabled=0;return 0; }
    char body[64];size_t size=fread(body,1,sizeof body-1,file);int extra=fgetc(file),failed=ferror(file);fclose(file);
    if (failed) return 2;
    if (extra!=EOF || !size) return 1;
    body[size]=0;if (strlen(body)!=size) return 1;
    const char *p=body;ex_space(&p);if (*p!='0' && *p!='1') return 1;
    int value=*p++=='1';ex_space(&p);if (*p) return 1;
    *enabled=value;return 0;
}
static void dl_error(struct mg_connection *c,int code,const char *error,int pid,int mutation) {
    char json[512],camera[32];
    if (pid>0) snprintf(camera,sizeof camera,"%d",pid);else strcpy(camera,"null");
    snprintf(json,sizeof json,"{\"error\":\"%s\",\"enabled\":null,\"native_service\":true,\"camera_pid\":%s,\"persistent\":false,\"mutation_may_have_been_applied\":%s}",error,camera,mutation?"true":"false");
    reply(c,code,json);
}
static void dl_request(struct mg_connection *c,struct http_message *h) {
    int setting=!mg_vcmp(&h->method,"POST"),requested=0;
    if (!setting && mg_vcmp(&h->method,"GET")) { dl_error(c,405,"GET or POST required",0,0);return; }
    if (setting && !dl_parse(h,&requested)) { dl_error(c,400,"provide only enabled as a JSON boolean",0,0);return; }
    int host=0,guest=ex_ready(3,&host);
    if (!guest) { dl_error(c,503,"current camera has no ready ui_mod hook",0,0);return; }
    int lock=open("/var/run/nx300m-dial-lock.lock",O_RDONLY|O_CREAT,0600);
    if (lock<0) { dl_error(c,500,"cannot open dial lock control lock",guest,0);return; }
    if (flock(lock,LOCK_EX|LOCK_NB)) {
        int busy=errno==EAGAIN || errno==EWOULDBLOCK;close(lock);
        dl_error(c,busy?409:500,busy?"dial lock control is busy":"dial lock control lock failed",guest,0);return;
    }
    if (ex_ready(3,&host)!=guest) { close(lock);dl_error(c,503,"ui_mod camera identity changed",guest,0);return; }
    if (setting) {
        const char *temporary="/var/run/nx300m-dial-lock.tmp";
        FILE *file=fopen(temporary,"w");int written=0;
        if (file) { written=fprintf(file,"%d\n",requested)>0;if (fclose(file)) written=0; }
        if (!written || rename(temporary,"/var/run/nx300m-dial-lock")) { unlink(temporary);close(lock);dl_error(c,500,"cannot publish dial lock state",guest,0);return; }
    }
    int enabled=0,result=dl_read(&enabled);
    if (result) { close(lock);dl_error(c,result==1?502:500,result==1?"dial lock state file is malformed":"cannot read dial lock state",guest,setting);return; }
    if (setting && enabled!=requested) { close(lock);dl_error(c,502,"dial lock readback differs from requested state",guest,1);return; }
    if (ex_ready(3,&host)!=guest) { close(lock);dl_error(c,503,"ui_mod camera identity changed",guest,setting);return; }
    char json[192];snprintf(json,sizeof json,"{\"enabled\":%s,\"native_service\":true,\"camera_pid\":%d,\"persistent\":false}",enabled?"true":"false",guest);
    close(lock);reply(c,200,json);
}
