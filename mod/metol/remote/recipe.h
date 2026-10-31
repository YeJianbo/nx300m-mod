/* Session recipes use the same catalogue parser as the native camera menu.
 * Included after looks.h for the configured mod root and camera identity. */
#include "nxui_model.h"
static const char *rp_value_names[]={"red","green","blue","hue","saturation","sharpness","contrast"};
static const char *rp_stages[]={"rgb","chroma","sharpness","contrast"};
static const char *rp_locks[]={"nx300m-recipe","nx300m-color-style","nx300m-source-rgb","nx300m-chroma-style","nx300m-sharpness","nx300m-contrast"};
static struct {
    struct mg_connection *connection;
    int active,phase,operation,host_pid,guest_pid,locks[6],requested[7],has_response;
    unsigned int request_id;
    long long values[25],deadline,next_poll;
} rp_task;
static int rp_library_available(void) { char path[256];return nx_lut_file("recipes.txt",path); }
static int rp_catalog(struct nx_recipe *recipes) {
    char path[256];return nx_lut_file("recipes.txt",path)?nx_load_recipes(path,recipes,NXUI_MAX_RECIPES):-1;
}
static int rp_ready(int *host) {
    FILE *file=fopen("/var/run/nx300m-recipe-ready","r");if (!file) return 0;
    int guest=0;char extra;int fields=fscanf(file,"%d %c",&guest,&extra);fclose(file);
    return fields==1?ex_ready_pid(guest,host):0;
}
static void rp_values_json(struct mg_connection *c,const int *values) {
    mg_printf_http_chunk(c,"{");
    for (int i=0;i<7;i++) { if (i) mg_printf_http_chunk(c,",");json_string(c,rp_value_names[i]);mg_printf_http_chunk(c,":%d",values[i]); }
    mg_printf_http_chunk(c,"}");
}
static void rp_release(void) {
    for (int i=0;i<6;i++) if (rp_task.locks[i]>=0) { flock(rp_task.locks[i],LOCK_UN);close(rp_task.locks[i]);rp_task.locks[i]=-1; }
    rp_task.connection=NULL;rp_task.active=0;
}
static void rp_state_json(struct mg_connection *c) {
    const long long *v=rp_task.values;
    struct nx_recipe recipes[NXUI_MAX_RECIPES];int count=rp_catalog(recipes),selected=-1,actual[7];
    for (int i=0;i<7;i++) actual[i]=(int)v[i+4];
    if (v[3] && !v[23]) for (int i=0;i<count;i++) if (!memcmp(actual,recipes[i].value,sizeof actual)) { selected=i;break; }
    int preview=v[11]==15 && !v[23],capture=v[12]==15 && !v[23];
    mg_printf_http_chunk(c,"\"request_id\":%u,\"bridge_result\":%lld,\"generation\":%lld,\"enabled\":%s,\"values\":",rp_task.request_id,v[1],v[2],v[3]?"true":"false");rp_values_json(c,actual);
    mg_printf_http_chunk(c,",\"preview_applied_mask\":%lld,\"capture_applied_mask\":%lld,\"preview_available_mask\":%lld,\"capture_available_mask\":%lld,\"preview_applied\":%s,\"capture_applied\":%s,\"applied\":%s,\"superseded\":%s,\"guest_pid\":%lld,\"stages\":{",v[11],v[12],v[13],v[14],preview?"true":"false",capture?"true":"false",preview && capture?"true":"false",v[23]?"true":"false",v[24]);
    for (int stage=0;stage<2;stage++) {
        mg_printf_http_chunk(c,"%s\"%s\":{",stage?",":"",stage?"capture":"preview");
        for (int i=0;i<4;i++) {
            int bit=1<<i;long long error=v[15+stage*4+i];
            if (i) mg_printf_http_chunk(c,",");
            json_string(c,rp_stages[i]);
            mg_printf_http_chunk(c,":{\"available\":%s,\"applied\":%s,\"error\":%lld}",(v[13+stage]&bit)?"true":"false",!v[23] && (v[11+stage]&bit)?"true":"false",error);
        }
        mg_printf_http_chunk(c,"}");
    }
    mg_printf_http_chunk(c,"},\"selection\":{\"stock\":%s,\"matches\":%s,\"id\":",!v[3] && !v[23]?"true":"false",selected>=0?"true":"false");
    if (selected>=0) json_string(c,recipes[selected].key);else mg_printf_http_chunk(c,"null");
    mg_printf_http_chunk(c,",\"label\":");if (selected>=0) json_string(c,recipes[selected].label);else mg_printf_http_chunk(c,"null");
    mg_printf_http_chunk(c,"}");
}
static void rp_finish(int status,const char *error) {
    struct mg_connection *c=rp_task.connection;
    if (c) {
        mg_printf(c,"HTTP/1.1 %d %s\r\nContent-Type: application/json; charset=utf-8\r\nTransfer-Encoding: chunked\r\nCache-Control: no-store\r\n\r\n",status,status==200?"OK":"Error");
        mg_printf_http_chunk(c,"{\"native_service\":true,\"persistent\":false,\"image_verified\":false,\"operation\":\"%s\",\"camera_pid\":%d",rp_task.operation==2?"read":rp_task.operation?"set":"reset",rp_task.guest_pid);
        if (error) { mg_printf_http_chunk(c,",\"error\":");json_string(c,error);mg_printf_http_chunk(c,",\"mutation_may_have_been_applied\":%s",rp_task.operation!=2 && rp_task.phase==2?"true":"false"); }
        if (rp_task.has_response) { mg_printf_http_chunk(c,error?",\"readback\":{":",");rp_state_json(c);if (error) mg_printf_http_chunk(c,"}"); }
        mg_printf_http_chunk(c,"}");mg_send_http_chunk(c,"",0);
    }
    rp_release();
}
static int rp_parse_response(char *line) {
    const char *p=line;
    for (int i=0;i<25;i++) {
        ex_space(&p);if (!*p) return 0;
        char *end;errno=0;long long value=strtoll(p,&end,10);
        if (errno || end==p || value<INT_MIN || value>UINT_MAX || (*end && !isspace((unsigned char)*end))) return 0;
        rp_task.values[i]=value;p=end;
    }
    ex_space(&p);return !*p;
}
static void rp_poll(void) {
    if (!rp_task.active || now_ms()<rp_task.next_poll) return;
    rp_task.next_poll=now_ms()+50;
    if (now_ms()>rp_task.deadline) { rp_finish(504,"配方桥未在期限内返回；请读取状态确认机身设置");return; }
    if (rp_ready(&rp_task.host_pid)!=rp_task.guest_pid) { rp_finish(503,"当前相机的配方桥身份已改变");return; }
    if (!rp_task.phase) {
        for (int i=0;i<6;i++) {
            char path[128];snprintf(path,sizeof path,"/var/run/%s-lock",rp_locks[i]);
            rp_task.locks[i]=open(path,O_RDONLY|O_CREAT,0600);
            if (rp_task.locks[i]<0) { rp_finish(500,"无法打开配方控制锁");return; }
            if (flock(rp_task.locks[i],LOCK_EX|LOCK_NB)) { rp_finish(errno==EAGAIN || errno==EWOULDBLOCK?409:500,"配方或独立色彩控件正在被其他客户端使用");return; }
        }
        FILE *random=fopen("/dev/urandom","rb");unsigned int id=0;
        int valid=random && fread(&id,1,sizeof id,random)==sizeof id;if (random) fclose(random);
        if (!valid) { rp_finish(500,"无法生成配方请求标识");return; }
        rp_task.request_id=id%0x7ffffffeU+1;rp_task.phase=1;
        FILE *out=fopen("/var/run/nx300m-recipe-request.tmp","w");int written=0;
        if (out) {
            written=fprintf(out,"%u %u %d %d %d %d %d %d %d %d\n",rp_task.request_id,(unsigned int)time(NULL)+6,rp_task.operation,rp_task.requested[0],rp_task.requested[1],rp_task.requested[2],rp_task.requested[3],rp_task.requested[4],rp_task.requested[5],rp_task.requested[6])>0;
            if (fclose(out)) written=0;
        }
        if (!written || rename("/var/run/nx300m-recipe-request.tmp","/var/run/nx300m-recipe-request")) { unlink("/var/run/nx300m-recipe-request.tmp");rp_finish(500,"无法发布配方请求");return; }
        rp_task.phase=2;return;
    }
    FILE *file=fopen("/var/run/nx300m-recipe-response","r");if (!file) return;
    char line[1024];int read=fgets(line,sizeof line,file)!=NULL,extra=fgetc(file),failed=ferror(file);fclose(file);if (!read) return;
    char *end;errno=0;unsigned long id=strtoul(line,&end,10);if (errno || end==line || id!=rp_task.request_id) return;
    if (failed || extra!=EOF || !rp_parse_response(line)) { rp_finish(502,"配方桥返回格式无效");return; }
    long long *v=rp_task.values;
    if (v[24]!=rp_task.guest_pid || rp_ready(&rp_task.host_pid)!=rp_task.guest_pid) { rp_finish(503,"配方响应不属于当前相机");return; }
    int valid=v[2]>=0 && v[3]>=0 && v[3]<=1 && v[23]>=0 && v[23]<=1;
    for (int i=0;i<7;i++) if (v[i+4]<0 || v[i+4]>(i<3?200:20)) valid=0;
    for (int i=11;i<15;i++) if (v[i]<0 || v[i]>15) valid=0;
    for (int i=15;i<23;i++) if (v[i]>INT_MAX) valid=0;
    if ((v[11]&~v[13]) || (v[12]&~v[14])) valid=0;
    for (int i=0;i<8;i++) if (v[15+i] && (v[11+i/4]&(1<<(i%4)))) valid=0;
    if (!valid) { rp_finish(502,"配方桥返回的七维或阶段状态无效");return; }
    rp_task.has_response=1;
    if (v[1]) { rp_finish(422,"机身拒绝配方批次；请检查拍摄模式、原厂设置及各阶段错误");return; }
    if (rp_task.operation!=2) {
        int matches=v[3]==(rp_task.operation==1) && !v[23];
        for (int i=0;i<7;i++) if (v[i+4]!=rp_task.requested[i]) matches=0;
        if (!matches) { rp_finish(502,"机身回读与配方请求不一致；请重新读取状态");return; }
    }
    rp_finish(200,NULL);
}
static int rp_parse_selection(struct http_message *h,char *id,int *operation) {
    if (!h->body.len || h->body.len>=256) return 0;
    char body[256],key[32],value[64];memcpy(body,h->body.p,h->body.len);body[h->body.len]=0;if (strlen(body)!=h->body.len) return 0;
    const char *p=body;int quoted;ex_space(&p);if (*p++!='{') return 0;
    if (!ex_token(&p,key,sizeof key,&quoted) || !quoted) return 0;
    ex_space(&p);if (*p++!=':') return 0;
    if (!ex_token(&p,value,sizeof value,&quoted)) return 0;
    if (!strcmp(key,"id") && quoted && nx_key_valid(value)) { strcpy(id,value);*operation=1; }
    else if (!strcmp(key,"reset") && !quoted && !strcmp(value,"true")) *operation=0;
    else return 0;
    ex_space(&p);if (*p++!='}') return 0;ex_space(&p);return !*p;
}
static void rp_request(struct mg_connection *c,struct http_message *h,int catalogue) {
    int setting=!mg_vcmp(&h->method,"POST");
    if ((catalogue && mg_vcmp(&h->method,"GET")) || (!catalogue && !setting && mg_vcmp(&h->method,"GET"))) { reply(c,405,"{\"error\":\"GET catalogue, or GET/POST recipe required\"}");return; }
    struct nx_recipe recipes[NXUI_MAX_RECIPES];int count=rp_catalog(recipes);
    if (catalogue) {
        if (count<0) { reply(c,503,"{\"error\":\"当前 mod 根目录的 recipes.txt 不可用或格式无效\"}");return; }
        int ready=rp_ready(NULL);
        mg_printf(c,"HTTP/1.1 200 OK\r\nContent-Type: application/json; charset=utf-8\r\nTransfer-Encoding: chunked\r\nCache-Control: no-store\r\n\r\n");
        mg_printf_http_chunk(c,"{\"native_service\":true,\"persistent\":false,\"image_verified\":false,\"ready\":%s,\"recipes\":[",ready?"true":"false");
        for (int i=0;i<count;i++) {
            if (i) mg_printf_http_chunk(c,",");
            mg_printf_http_chunk(c,"{\"id\":");json_string(c,recipes[i].key);mg_printf_http_chunk(c,",\"label\":");json_string(c,recipes[i].label);mg_printf_http_chunk(c,",\"values\":");rp_values_json(c,recipes[i].value);mg_printf_http_chunk(c,"}");
        }
        mg_printf_http_chunk(c,"]}");mg_send_http_chunk(c,"",0);return;
    }
    if (ex_task.active || rp_task.active) { reply(c,409,"{\"error\":\"原生相机控制任务正在运行\"}");return; }
    char id[64]="";int operation=2,requested[7]={100,100,100,10,10,10,10};
    if (setting && !rp_parse_selection(h,id,&operation)) { reply(c,400,"{\"error\":\"只接受 id 字符串或 reset:true\"}");return; }
    if (operation==1) {
        if (count<0) { reply(c,503,"{\"error\":\"当前 recipes.txt 不可用或格式无效\"}");return; }
        int index=-1;for (int i=0;i<count;i++) if (!strcmp(id,recipes[i].key)) { index=i;break; }
        if (index<0) { reply(c,404,"{\"error\":\"id 不在当前已部署的 recipes.txt 中\"}");return; }
        memcpy(requested,recipes[index].value,sizeof requested);
    }
    int host=0,guest=rp_ready(&host);if (!guest) { reply(c,503,"{\"error\":\"当前相机的配方桥尚未就绪\"}");return; }
    memset(&rp_task,0,sizeof rp_task);for (int i=0;i<6;i++) rp_task.locks[i]=-1;
    rp_task.connection=c;rp_task.active=1;rp_task.operation=operation;rp_task.host_pid=host;rp_task.guest_pid=guest;rp_task.deadline=now_ms()+10000;
    memcpy(rp_task.requested,requested,sizeof requested);rp_poll();
}
static void rp_closed(struct mg_connection *c) { if (rp_task.connection==c) rp_task.connection=NULL; }
