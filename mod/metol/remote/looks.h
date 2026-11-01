/* Native LUT catalogue and staging. Included after exposure.h to share the
 * current-camera identity verifier; no child process or second HTTP backend. */
#include "../lut_engine_shared.h"
struct lk_observed {
    unsigned int generation,mask,strength;int active,pid;
    char stage[64],composition[64],root[161],path[256];
};
static int lk_hook(int *camera_pid,int *root_matches) {
    *camera_pid=0;*root_matches=0;
    FILE *file=fopen("/var/run/nx300m-lut-hook","r");if (!file) return 0;
    int guest=0;char root[161],extra;int fields=fscanf(file,"%d %160s %c",&guest,root,&extra);fclose(file);
    const char *configured=nx_lut_data_root();
    if (fields!=2 || !nx_mod_root_valid(root) || !configured) return 0;
    *root_matches=!strcmp(root,configured);
    int host=0;if (!ex_ready_pid(guest,&host)) return 0;
    *camera_pid=guest;return *root_matches && nx_lut_hook_binding_ready(guest,host,configured);
}
static int lk_library_available(void) { char path[256];return nx_lut_file("looks.txt",path); }
static int lk_ready(void) { int pid,root_matches;return lk_hook(&pid,&root_matches); }
/* The verified camera hook can belong to an older adapter. Only its matching
 * version marker establishes that selections below full strength are honored. */
static int lk_strength_ready(int camera_pid) {
    if (camera_pid<=0) return 0;
    FILE *file=fopen("/var/run/nx300m-lut-strength","r");if (!file) return 0;
    int pid=0;char root[161],version[32],extra;
    int fields=fscanf(file,"%d %160s %31s %c",&pid,root,version,&extra);fclose(file);
    unsigned int protocol=0;const char *configured=nx_lut_data_root();
    return fields==3 && pid==camera_pid && configured && !strcmp(root,configured) &&
        nx_lut_uint(version,&protocol) && protocol==1;
}
static int lk_strength_allowed(int camera_pid,unsigned int strength) {
    return strength==100 || lk_strength_ready(camera_pid);
}
static int lk_uint(const char *value,unsigned int *out) {
    if (*value<'0' || *value>'9') return 0;
    char *end;errno=0;unsigned long n=strtoul(value,&end,10);
    if (errno || *end || n>UINT_MAX) return 0;
    *out=(unsigned int)n;return 1;
}
static int lk_observe(struct lk_observed *out,int camera_pid) {
    memset(out,0,sizeof *out);out->strength=100;
    FILE *file=fopen("/var/run/nx300m-lut-status.json","r");if (!file) return 0;
    char body[2048];size_t bytes=fread(body,1,sizeof body-1,file);int extra=fgetc(file),failed=ferror(file);fclose(file);
    if (extra!=EOF || failed || !bytes) return 0;
    body[bytes]=0;if (strlen(body)!=bytes) return 0;
    const char *p=body;ex_space(&p);if (*p++!='{') return 0;
    unsigned int seen=0;
    for (;;) {
        char key[64],value[256];int quoted;
        if (!ex_token(&p,key,sizeof key,&quoted) || !quoted) return 0;
        ex_space(&p);if (*p++!=':') return 0;ex_space(&p);
        if (*p=='"' && p[1]=='"') { value[0]=0;quoted=1;p+=2; }
        else if (!ex_token(&p,value,sizeof value,&quoted)) return 0;
        const char *names[]={"generation","active","forwarded_mask","stage","composition","guest_pid","root","selection_path","strength"};
        int index=-1;for (int i=0;i<9;i++) if (!strcmp(key,names[i])) { index=i;break; }
        if (index>=0) {
            if (seen&(1U<<index)) return 0;
            seen|=1U<<index;
            unsigned int number=0;
            if (index==0 || index==1 || index==2 || index==5 || index==8) {
                if (quoted || !lk_uint(value,&number)) return 0;
                if (index==0) out->generation=number;
                else if (index==1) { if (number>1) return 0;out->active=(int)number; }
                else if (index==2) { if (number>7) return 0;out->mask=number; }
                else if (index==8) { if (number>100) return 0;out->strength=number; }
                else { if (number>INT_MAX) return 0;out->pid=(int)number; }
            } else {
                if (!quoted) return 0;
                if (index==3) { if (strlen(value)>=sizeof out->stage) return 0;strcpy(out->stage,value); }
                else if (index==4) { if (strlen(value)>=sizeof out->composition) return 0;strcpy(out->composition,value); }
                else if (index==6) { if (!nx_mod_root_valid(value)) return 0;strcpy(out->root,value); }
                else strcpy(out->path,value);
            }
        }
        ex_space(&p);if (*p=='}') { p++;break; }if (*p++!=',') return 0;
    }
    ex_space(&p);const char *root=nx_lut_data_root();
    return !*p && (seen&255U)==255U && camera_pid>0 && out->pid==camera_pid && root && !strcmp(out->root,root);
}
static void lk_response(struct mg_connection *c,int code,struct nx_lut_look *looks,int count,const char *operation) {
    const char *configured=nx_lut_data_root();
    if (!configured) { reply(c,503,"{\"error\":\"LUT source directory is unavailable or its identity changed\"}");return; }
    int pid=0,root_matches=0,ready=lk_hook(&pid,&root_matches);
    unsigned int generation=0,strength=100;char selected_path[256]="";
    int exists=access(NX_LUT_SELECTION,F_OK)==0;
    int parsed=exists && nx_lut_read_selection_with_strength(NX_LUT_SELECTION,&generation,selected_path,&strength) && generation;
    int selected=-1;
    if (parsed) for (int i=0;i<count;i++) {
        char path[256];snprintf(path,sizeof path,"%.160s/looks/%.63s/table.d4lut",configured,looks[i].id);
        if (!strcmp(path,selected_path)) { selected=i;break; }
    }
    int selection_valid=!exists || (selected>=0 && looks[selected].table_valid);
    struct lk_observed observed;int observed_valid=ready && lk_observe(&observed,pid);
    int matches=observed_valid && (exists?(parsed && observed.generation==generation && observed.strength==strength && !strcmp(observed.path,selected_path)):(!observed.active && !*observed.path));
    const char *stage=!ready?"hook-unavailable":!selection_valid?"invalid-selection":!exists?(matches && !strcmp(observed.stage,"stock")?"stock":"stock-restore-awaiting-stock-call"):matches?observed.stage:"staged-awaiting-stock-call";
    int forwarded=selection_valid && exists && matches && observed.active && observed.mask==7 && !strcmp(observed.stage,"forwarded-to-stock-api");
    mg_printf(c,"HTTP/1.1 %d %s\r\nContent-Type: application/json; charset=utf-8\r\nTransfer-Encoding: chunked\r\nCache-Control: no-store\r\n\r\n",code,code==202?"Accepted":"OK");
    mg_printf_http_chunk(c,"{\"native_service\":true,\"strength_supported\":%s,\"persistence_supported\":true,\"persistent\":%s,\"image_verified\":false,\"operation\":",ready && lk_strength_ready(pid)?"true":"false",nx_lut_selection_persisted()?"true":"false");json_string(c,operation);
    mg_printf_http_chunk(c,",\"root\":");json_string(c,configured);
    mg_printf_http_chunk(c,",\"hook\":{\"ready\":%s,\"root_matches\":%s,\"camera_pid\":",ready?"true":"false",root_matches?"true":"false");
    if (pid) mg_printf_http_chunk(c,"%d",pid);else mg_printf_http_chunk(c,"null");
    mg_printf_http_chunk(c,"},\"looks\":[");
    for (int i=0;i<count;i++) {
        if (i) mg_printf_http_chunk(c,",");
        mg_printf_http_chunk(c,"{\"id\":");json_string(c,looks[i].id);mg_printf_http_chunk(c,",\"label\":");json_string(c,looks[i].label);
        mg_printf_http_chunk(c,",\"table_valid\":%s,\"composed_table_available\":%s,\"selectable\":%s}",looks[i].table_valid?"true":"false",looks[i].composed_available?"true":"false",ready && looks[i].table_valid?"true":"false");
    }
    mg_printf_http_chunk(c,"],\"selection\":{\"stock\":%s,\"valid\":%s,\"id\":",exists?"false":"true",selection_valid?"true":"false");
    if (selected>=0) json_string(c,looks[selected].id);else mg_printf_http_chunk(c,"null");
    mg_printf_http_chunk(c,",\"generation\":");if (parsed) mg_printf_http_chunk(c,"%u",generation);else mg_printf_http_chunk(c,"null");
    mg_printf_http_chunk(c,",\"strength\":");if (parsed || !exists) mg_printf_http_chunk(c,"%u",strength);else mg_printf_http_chunk(c,"null");
    mg_printf_http_chunk(c,"},\"status\":{\"stage\":");json_string(c,stage);
    mg_printf_http_chunk(c,",\"current_camera\":%s,\"selection_matches\":%s,\"forwarded\":%s,\"forwarded_mask\":",observed_valid?"true":"false",matches?"true":"false",forwarded?"true":"false");
    if (observed_valid) mg_printf_http_chunk(c,"%u",observed.mask);else mg_printf_http_chunk(c,"null");
    mg_printf_http_chunk(c,",\"observed_stage\":");if (observed_valid) json_string(c,observed.stage);else mg_printf_http_chunk(c,"null");
    mg_printf_http_chunk(c,",\"observed_strength\":");if (observed_valid) mg_printf_http_chunk(c,"%u",observed.strength);else mg_printf_http_chunk(c,"null");
    mg_printf_http_chunk(c,",\"composition\":");if (observed_valid) json_string(c,observed.composition);else mg_printf_http_chunk(c,"null");
    mg_printf_http_chunk(c,"}}");mg_send_http_chunk(c,"",0);
}
static int lk_select_id(struct http_message *h,char *id,unsigned int *strength) {
    if (!h->body.len || h->body.len>=256) return 0;
    char body[256],key[32];memcpy(body,h->body.p,h->body.len);body[h->body.len]=0;
    if (strlen(body)!=h->body.len) return 0;
    const char *p=body;int quoted;unsigned int seen=0;*strength=100;
    ex_space(&p);if (*p++!='{') return 0;
    for (;;) {
        if (!ex_token(&p,key,sizeof key,&quoted) || !quoted) return 0;
        ex_space(&p);if (*p++!=':') return 0;
        if (!strcmp(key,"id")) {
            if ((seen&1U) || !ex_token(&p,id,64,&quoted) || !quoted || !nx_lut_id_valid(id)) return 0;
            seen|=1U;
        } else if (!strcmp(key,"strength")) {
            char value[32];
            if ((seen&2U) || !ex_token(&p,value,sizeof value,&quoted) || quoted ||
                !nx_lut_strength(value,strength) || (value[0]=='0' && value[1])) return 0;
            seen|=2U;
        } else return 0;
        ex_space(&p);if (*p=='}') { p++;break; }if (*p++!=',') return 0;
    }
    ex_space(&p);return (seen&1U) && !*p;
}
static int lk_empty_body(struct http_message *h) {
    if (!h->body.len) return 1;
    if (h->body.len>=64) return 0;
    char body[64];memcpy(body,h->body.p,h->body.len);body[h->body.len]=0;if (strlen(body)!=h->body.len) return 0;
    const char *p=body;ex_space(&p);if (*p++!='{') return 0;ex_space(&p);if (*p++!='}') return 0;ex_space(&p);return !*p;
}
static void lk_request(struct mg_connection *c,struct http_message *h,int action) {
    if ((action && mg_vcmp(&h->method,"POST")) || (!action && mg_vcmp(&h->method,"GET"))) { reply(c,405,"{\"error\":\"unsupported LUT method\"}");return; }
    char id[64]="";unsigned int strength=100;
    if ((action==1 && !lk_select_id(h,id,&strength)) || (action==2 && !lk_empty_body(h))) { reply(c,400,"{\"error\":\"provide a valid id and optional integer strength from 0 to 100, or use the separate stock action\"}");return; }
    char root[PATH_MAX];if (!nx_lut_root(root)) { reply(c,503,"{\"error\":\"configured LUT root is invalid or unavailable\"}");return; }
    /* The first read after startup may restore a saved selection. */
    int lock=nx_lut_lock(1,1);
    if (lock<0) { reply(c,errno==EWOULDBLOCK || errno==EAGAIN?409:500,"{\"error\":\"LUT engine lock unavailable\"}");return; }
    struct nx_lut_look looks[NX_LUT_MAX_LOOKS];int count=0,result=nx_lut_catalog(looks,&count);
    if (result) { close(lock);reply(c,503,"{\"error\":\"deployed looks.txt is missing or invalid\"}");return; }
    int request_pid=0,root_matches=0;
    if (action && !lk_hook(&request_pid,&root_matches)) { close(lock);reply(c,503,"{\"error\":\"current camera has no matching LUT hook and root\"}");return; }
    if (!action && lk_ready()) {
        int restored=nx_lut_restore_selection();
        if (restored) fprintf(stderr,"LUT saved selection not restored: %d\n",restored);
    }
    if (action==1) {
        if (!lk_strength_allowed(request_pid,strength)) { close(lock);reply(c,409,"{\"error\":\"current camera LUT hook does not support adjustable strength\"}");return; }
        int index=-1;for (int i=0;i<count;i++) if (!strcmp(id,looks[i].id)) { index=i;break; }
        if (index<0) { close(lock);reply(c,404,"{\"error\":\"id is not in deployed looks.txt\"}");return; }
        char path[256];int composed;
        if (!looks[index].table_valid || !nx_lut_validate_look(id,path,&composed)) { close(lock);reply(c,422,"{\"error\":\"look table is missing, outside root, or invalid\"}");return; }
        result=nx_lut_stage_with_strength(path,strength,NULL);
    } else if (action==2) result=nx_lut_stock();
    if (result) { close(lock);reply(c,500,"{\"error\":\"could not stage LUT selection\",\"mutation_may_have_been_applied\":true}");return; }
    if (action) {
        int after_pid=0;
        if (!lk_hook(&after_pid,&root_matches) || after_pid!=request_pid) { close(lock);reply(c,503,"{\"error\":\"camera LUT hook changed while staging\",\"mutation_may_have_been_applied\":true}");return; }
    }
    lk_response(c,action?202:200,looks,count,action==1?"select":action==2?"stock":"read");close(lock);
}
