/* Native, nonblocking client of the original Ecore-thread file bridges. */
#include <sys/file.h>

static const char *ex_names[]={"iso_step","iso_expansion","auto_iso_max","fastest","red","green","blue","reset","saturation","sharpness","contrast","color_tone","hue"};
static const int ex_iso_max[]={125,160,200,250,320,400,500,640,800,1000,1250,1600,2000,2500,3200,6400,12800,25600};
static const char *ex_fastest[]={"off","1/60","1/125","1/250","1/500","1/1000","1/2000","1/4000","1/6000","1/8000"};
struct ex_fields { unsigned int seen;int json,quoted[13];char values[13][32]; };
/* Non-neutral SAT is checked by the runtime against the current native mode. */
static const int ex_chroma_hue_supported=1,ex_chroma_saturation_supported=1;
static struct {
    struct mg_connection *connection;
    int active,kind,setting,phase,has_response,lock_fd,count,index,ids[3],applied[3],notified[3];
    unsigned int requested[4],before[3],actual[3],request_id;
    unsigned int choice,microseconds,calls,capped,last_choice;
    int tv16,stock,result,host_pid,guest_pid;
    int ae_mask,ae_iso_setting,ae_max_iso,ae_mode,ae_max_sv,ae_current_sv,extended_iso_ready,ae_capture_mode;
    unsigned int stock_actual[3];
    unsigned int rgb[3],capture_calls,preview_calls,capture_bypass,preview_bypass;
    int color_operation,color_active,capture_rc,preview_rc,capture_base,preview_base,capture_modified,preview_modified,response_pid;
    unsigned int pw_mask,pw_actual[4],pw_iq[4];
    int pw_decoded[4],pw_mode;
    unsigned int style_level,style_enabled,style_generation,style_capture_generation,style_preview_generation;
    int style_capture_error,style_preview_error;
    long long lock_deadline,response_deadline,deadline,next_poll;
} ex_task={.lock_fd=-1};

static int ex_style_kind(int kind) { return kind==5 || kind==6; }
static int ex_stage_kind(int kind) { return ex_style_kind(kind) || kind==7; }
static const char *ex_style_name(int kind) { return kind==6?"sharpness":"contrast"; }
static const char *ex_prefix(int kind) {
    return kind==7?"nx300m-source-rgb":kind==6?"nx300m-sharpness":kind==5?"nx300m-contrast":kind==4?"nx300m-chroma-style":kind==3?"nx300m-pw":kind==2?"nx300m-color-style":kind==1?"nx300m-shutter-limit":"nx300m-exposure";
}

static int ex_camera_identity(int host_pid,int guest_pid,const char *mount_namespace) {
    char path[96],data[8192],ns[96];
    snprintf(path,sizeof path,"/proc/%d/ns/mnt",host_pid);
    ssize_t size=readlink(path,ns,sizeof ns-1);
    if (*mount_namespace) {
        if (size<0) return 0;
        ns[size]=0;if (strcmp(ns,mount_namespace)) return 0;
    } else {
        /* Old stock kernels can lack namespace symlinks. A native daemon and
         * its camera must still share the same filesystem root. */
        struct stat own_root,camera_root;
        snprintf(path,sizeof path,"/proc/%d/root",host_pid);
        if (stat("/",&own_root) || stat(path,&camera_root) || own_root.st_dev!=camera_root.st_dev || own_root.st_ino!=camera_root.st_ino) return 0;
    }
    snprintf(path,sizeof path,"/proc/%d/cmdline",host_pid);
    FILE *file=fopen(path,"rb");if (!file) return 0;
    size_t bytes=fread(data,1,sizeof data-1,file);fclose(file);data[bytes]=0;
    const char *basename=strrchr(data,'/');basename=basename?basename+1:data;
    int native=!strcmp(basename,"di-camera-app-nx300") || !strcmp(basename,"di-camera-app");
    int emulated=!strcmp(basename,"qemu-arm-static") || !strcmp(basename,"qemu-arm");
    snprintf(path,sizeof path,"/proc/%d/exe",host_pid);
    ssize_t exe_length=readlink(path,ns,sizeof ns-1);
    if (exe_length>=0) {
        ns[exe_length]=0;
        /* Linux keeps a replaced executable running and adds this suffix to
         * /proc/PID/exe. Its command line, namespace and guest PID must still
         * match below; replacing the lab runner must not hide a live camera. */
        if (exe_length>=10 && !strcmp(ns+exe_length-10," (deleted)")) ns[exe_length-10]=0;
        const char *executable=strrchr(ns,'/');executable=executable?executable+1:ns;
        int native_exe=!strcmp(executable,"di-camera-app-nx300");
        int emulated_exe=!strcmp(executable,"qemu-arm-static") || !strcmp(executable,"qemu-arm");
        if (!((native && native_exe) || (emulated && emulated_exe))) return 0;
    }
    if (!native && !emulated) return 0;
    int camera=native;
    for (size_t offset=0;offset<bytes;) {
        if (!strcmp(data+offset,"/usr/bin/di-camera-app-nx300") || !strcmp(data+offset,"di-camera-app-nx300") || !strcmp(data+offset,"/usr/bin/di-camera-app") || !strcmp(data+offset,"di-camera-app")) camera=1;
        offset+=strlen(data+offset)+1;
    }
    if (!camera) return 0;
    snprintf(path,sizeof path,"/proc/%d/status",host_pid);
    file=fopen(path,"r");if (!file) return 0;
    int actual=host_pid;
    while (fgets(data,sizeof data,file)) if (!strncmp(data,"NSpid:",6)) {
        char *p=data+6,*end;long value;
        while ((value=strtol(p,&end,10))>0 && end!=p) { actual=(int)value;p=end; }
        break;
    }
    fclose(file);
    return actual==guest_pid;
}
static int ex_ready_pid(int guest,int *host_pid) {
    if (guest<=0) return 0;
    char ns[96];ssize_t length=readlink("/proc/self/ns/mnt",ns,sizeof ns-1);
    if (length<0) ns[0]=0;else ns[length]=0;
    if (host_pid && *host_pid>0) return ex_camera_identity(*host_pid,guest,ns)?guest:0;
    DIR *directory=opendir("/proc");if (!directory) return 0;
    struct dirent *entry;int found=0;
    while ((entry=readdir(directory))) {
        char *end;long pid=strtol(entry->d_name,&end,10);
        if (pid<=0 || *end || pid>INT_MAX) continue;
        if (ex_camera_identity((int)pid,guest,ns)) { found=guest;if (host_pid) *host_pid=(int)pid;break; }
    }
    closedir(directory);return found;
}
static int ex_ready(int kind,int *host_pid) {
    char path[128];snprintf(path,sizeof path,"/var/run/%s-ready",ex_prefix(kind));
    FILE *file=fopen(path,"r");if (!file) return 0;
    int guest=0;char extra;int fields=fscanf(file,"%d %c",&guest,&extra);fclose(file);
    return fields==1?ex_ready_pid(guest,host_pid):0;
}
static int ex_read_only(int ready_pid) {
    if (ready_pid<=0) return 0;
    FILE *file=fopen("/var/run/nx300m-exposure-readonly","r");
    if (!file) return 0;
    int pid=0;char extra;int fields=fscanf(file,"%d %c",&pid,&extra);fclose(file);
    return fields==1 && pid==ready_pid;
}
static int ex_key(const char *key) {
    for (int i=0;i<13;i++) if (!strcmp(key,ex_names[i])) return i;
    return -1;
}
static void ex_space(const char **p) { while (**p && isspace((unsigned char)**p)) (*p)++; }
static int ex_token(const char **p,char *out,size_t capacity,int *quoted) {
    ex_space(p);size_t count=0;*quoted=**p=='"';if (*quoted) (*p)++;
    while (**p && (*quoted?**p!='"':(!isspace((unsigned char)**p) && **p!=',' && **p!='}'))) {
        unsigned char value=(unsigned char)*(*p)++;
        if (value<32 || value>=127 || value=='\\' || count+1>=capacity) return 0;
        out[count++]=(char)value;
    }
    if (*quoted) { if (**p!='"') return 0;(*p)++; }
    out[count]=0;return count>0;
}
static int ex_parse(struct http_message *h,struct ex_fields *fields) {
    memset(fields,0,sizeof *fields);
    if (!h->body.len || h->body.len>=1024) return 0;
    char body[1024];memcpy(body,h->body.p,h->body.len);body[h->body.len]=0;
    if (strlen(body)!=h->body.len) return 0;
    const char *p=body;ex_space(&p);
    if (*p=='{') {
        fields->json=1;p++;ex_space(&p);if (*p=='}') { p++;ex_space(&p);return !*p; }
        for (;;) {
            char key[48];int quoted=0;
            if (!ex_token(&p,key,sizeof key,&quoted) || !quoted) return 0;
            ex_space(&p);if (*p++!=':') return 0;
            int index=ex_key(key);
            if (index<0 || fields->seen&(1U<<index) || !ex_token(&p,fields->values[index],sizeof fields->values[index],&fields->quoted[index])) return 0;
            fields->seen|=1U<<index;ex_space(&p);
            if (*p=='}') { p++;ex_space(&p);return !*p; }
            if (*p++!=',') return 0;
        }
    }
    while (*p) {
        const char *end=strchr(p,'&');if (!end) end=p+strlen(p);
        const char *equal=memchr(p,'=',(size_t)(end-p));if (!equal) return 0;
        char key[48];int n=mg_url_decode(p,(size_t)(equal-p),key,sizeof key,1);
        if (n<=0 || (size_t)n!=strlen(key)) return 0;
        int index=ex_key(key);
        if (index<0 || fields->seen&(1U<<index)) return 0;
        n=mg_url_decode(equal+1,(size_t)(end-equal-1),fields->values[index],sizeof fields->values[index],1);
        if (n<=0 || (size_t)n!=strlen(fields->values[index])) return 0;
        fields->seen|=1U<<index;p=*end?end+1:end;
    }
    return 1;
}
static void ex_release(void) {
    if (ex_task.lock_fd>=0) { flock(ex_task.lock_fd,LOCK_UN);close(ex_task.lock_fd); }
    ex_task.lock_fd=-1;ex_task.connection=NULL;ex_task.active=0;
}
static void ex_color_json(struct mg_connection *c) {
    mg_printf_http_chunk(c,"\"request_id\":%u,\"bridge_result\":%d,",ex_task.request_id,ex_task.result);
    if (ex_task.kind==4) mg_printf_http_chunk(c,"\"hue\":%u,\"saturation\":%u,\"supported_hue\":%s,\"supported_saturation\":%s,",ex_task.rgb[0],ex_task.rgb[1],ex_chroma_hue_supported?"true":"false",ex_chroma_saturation_supported?"true":"false");
    else mg_printf_http_chunk(c,"\"red\":%u,\"green\":%u,\"blue\":%u,",ex_task.rgb[0],ex_task.rgb[1],ex_task.rgb[2]);
    mg_printf_http_chunk(c,"\"active\":%d,\"capture_calls\":%u,\"preview_calls\":%u,\"capture_range_bypasses\":%u,\"preview_range_bypasses\":%u,\"capture_setter_result\":%d,\"preview_setter_result\":%d,\"capture_base_available\":%d,\"preview_base_available\":%d,\"capture_modified\":%d,\"preview_modified\":%d,\"guest_pid\":%d",ex_task.color_active,ex_task.capture_calls,ex_task.preview_calls,ex_task.capture_bypass,ex_task.preview_bypass,ex_task.capture_rc,ex_task.preview_rc,ex_task.capture_base,ex_task.preview_base,ex_task.capture_modified,ex_task.preview_modified,ex_task.response_pid);
}
static void ex_style_json(struct mg_connection *c) {
    int preview_applied=ex_task.style_preview_generation==ex_task.style_generation && !ex_task.style_preview_error && ex_task.preview_modified==(int)ex_task.style_enabled;
    int capture_applied=ex_task.style_capture_generation==ex_task.style_generation && !ex_task.style_capture_error && ex_task.capture_modified==(int)ex_task.style_enabled;
    mg_printf_http_chunk(c,"\"request_id\":%u,\"bridge_result\":%d,",ex_task.request_id,ex_task.result);
    if (ex_task.kind==7) mg_printf_http_chunk(c,"\"red\":%u,\"green\":%u,\"blue\":%u,",ex_task.rgb[0],ex_task.rgb[1],ex_task.rgb[2]);
    else mg_printf_http_chunk(c,"\"%s\":%u,\"level\":%u,",ex_style_name(ex_task.kind),ex_task.style_level,ex_task.style_level);
    mg_printf_http_chunk(c,"\"enabled\":%s,\"generation\":%u,\"capture_generation\":%u,\"preview_generation\":%u,\"capture_modified\":%d,\"preview_modified\":%d,\"capture_error\":%d,\"preview_error\":%d,\"guest_pid\":%d,\"preview_applied\":%s,\"capture_applied\":%s,\"pending\":%s",
        ex_task.style_enabled?"true":"false",ex_task.style_generation,ex_task.style_capture_generation,ex_task.style_preview_generation,ex_task.capture_modified,ex_task.preview_modified,ex_task.style_capture_error,ex_task.style_preview_error,ex_task.response_pid,preview_applied?"true":"false",capture_applied?"true":"false",!preview_applied && (ex_task.style_preview_generation!=ex_task.style_generation || !ex_task.style_preview_error)?"true":"false");
}
static int ex_integer(const char *text,int minimum,int maximum,int *value) {
    const char *p=text;if (*p=='-') p++;
    if (!*p || (*p=='0' && p[1])) return 0;
    for (;*p;p++) if (*p<'0' || *p>'9') return 0;
    char *end;errno=0;long number=strtol(text,&end,10);
    if (errno || *end || number<minimum || number>maximum) return 0;
    *value=(int)number;return 1;
}
static unsigned int ex_pw_pack(int value) { return ((unsigned int)value<<20)|0xff004U; }
static int ex_pw_unpack(unsigned int raw,int tone,int *value) {
    if (tone) { if (raw!=0xffffffffU && raw>359) return 0;*value=raw==0xffffffffU?-1:(int)raw;return 1; }
    int decoded=(int)(raw>>20);if (decoded&0x800) decoded-=4096;
    if (decoded < -4 || decoded > 4 || ex_pw_pack(decoded)!=raw) return 0;
    *value=decoded;return 1;
}
static void ex_pw_json(struct mg_connection *c) {
    static const char *names[]={"color_tone","saturation","sharpness","contrast"};
    mg_printf_http_chunk(c,"\"values\":{");
    for (int i=0;i<4;i++) {
        if (i) mg_printf_http_chunk(c,",");
        json_string(c,names[i]);mg_printf_http_chunk(c,":");
        int value;if (ex_pw_unpack(ex_task.pw_actual[i],i==0,&value)) mg_printf_http_chunk(c,"%d",value);else mg_printf_http_chunk(c,"null");
    }
    mg_printf_http_chunk(c,"},\"raw\":[");
    for (int i=0;i<4;i++) mg_printf_http_chunk(c,"%s\"0x%08x\"",i?",":"",ex_task.pw_actual[i]);
    mg_printf_http_chunk(c,"],\"iq_raw\":[");
    for (int i=0;i<4;i++) mg_printf_http_chunk(c,"%s\"0x%08x\"",i?",":"",ex_task.pw_iq[i]);
    mg_printf_http_chunk(c,"],\"iq_matches\":%s,\"decoded_indices\":{",memcmp(ex_task.pw_actual,ex_task.pw_iq,sizeof ex_task.pw_actual)?"false":"true");
    for (int i=0;i<4;i++) { if (i) mg_printf_http_chunk(c,",");json_string(c,names[i]);mg_printf_http_chunk(c,":%d",ex_task.pw_decoded[i]); }
    int mode=ex_task.pw_mode,enabled=mode==1 || mode==2 || mode==3 || mode==4 || mode==8;
    mg_printf_http_chunk(c,"},\"iq_mode\":%d,\"scalar_mode_enabled\":%s",mode,enabled?"true":"false");
}
static void ex_error(int code,const char *error,int bridge_code) {
    struct mg_connection *c=ex_task.connection;
    if (c) {
        mg_printf(c,"HTTP/1.1 %d Error\r\nContent-Type: application/json; charset=utf-8\r\nTransfer-Encoding: chunked\r\nCache-Control: no-store\r\n\r\n",code);
        mg_printf_http_chunk(c,"{\"error\":");json_string(c,error);
        mg_printf_http_chunk(c,",\"bridge_code\":%d,\"camera_pid\":%d,\"native_service\":true,\"completed_items\":%d,\"mutation_may_have_been_applied\":%s",bridge_code,ex_task.guest_pid,ex_task.index,ex_task.setting && (ex_task.index>0 || ex_task.phase==2)?"true":"false");
        if (ex_task.has_response) {
            if (ex_stage_kind(ex_task.kind)) { mg_printf_http_chunk(c,",\"readback\":{");ex_style_json(c);mg_printf_http_chunk(c,"}"); }
            else if (ex_task.kind==3) { mg_printf_http_chunk(c,",\"readback\":{");ex_pw_json(c);mg_printf_http_chunk(c,"}"); }
            else if (ex_task.kind==2 || ex_task.kind==4) { mg_printf_http_chunk(c,",\"readback\":{");ex_color_json(c);mg_printf_http_chunk(c,"}"); }
            else if (ex_task.kind==1) mg_printf_http_chunk(c,",\"readback\":{\"choice\":%u,\"limit_tv16\":%d,\"limit_microseconds\":%u}",ex_task.choice,ex_task.tv16,ex_task.microseconds);
            else mg_printf_http_chunk(c,",\"readback\":{\"user_data_id\":\"0x%02x\",\"before_raw\":\"0x%08x\",\"actual_raw\":\"0x%08x\",\"set_accepted\":%s,\"notify_path\":%s}",ex_task.ids[ex_task.index],ex_task.before[ex_task.index],ex_task.actual[ex_task.index],ex_task.applied[ex_task.index]?"true":"false",ex_task.notified[ex_task.index]?"true":"false");
        }
        mg_printf_http_chunk(c,",\"persistent\":false}");
        mg_send_http_chunk(c,"",0);
    }
    ex_release();
}
static void ex_iso_value(struct mg_connection *c,int id,unsigned int actual) {
    if (id==0x37 && (actual==0x00370000 || actual==0x00370001)) json_string(c,actual&1?"one-third":"one-stop");
    else if (id==0x38 && (actual==0x00380000 || actual==0x00380001)) mg_printf_http_chunk(c,actual&1?"true":"false");
    else if (id==0x3b && (actual&0xffff0000U)==0x003b0000U && (actual&0xffffU)<18) mg_printf_http_chunk(c,"%d",ex_iso_max[actual&0xffff]);
    else mg_printf_http_chunk(c,"null");
}
static int ex_sv_iso(int sv) {
    static const int values[]={50,64,80,100,125,160,200,250,320,400,500,640,800,1000,1250,1600,2000,2500,3200,4000,5000,6400,8000,10000,12800,16000,20000,25600};
    static const int svalues[]={64,69,75,80,85,91,96,101,107,112,117,123,128,133,139,144,149,155,160,165,171,176,181,187,192,197,203,208};
    for (unsigned int i=0;i<sizeof(values)/sizeof(values[0]);i++) if (svalues[i]==sv) return values[i];
    return 0;
}
static void ex_ae_policy_json(struct mg_connection *c) {
    int requested=0;
    for (int i=0;i<ex_task.count;i++) if (ex_task.ids[i]==0x3b && (ex_task.actual[i]&0xffff0000U)==0x003b0000U && (ex_task.actual[i]&0xffffU)<18)
        requested=ex_iso_max[ex_task.actual[i]&0xffff];
    int current=(ex_task.ae_mask&16)?ex_sv_iso(ex_task.ae_current_sv):0;
    int maximum=(ex_task.ae_mask&8)?ex_sv_iso(ex_task.ae_max_sv):0;
    int verified=requested && (ex_task.ae_mask&10)==10 && ex_task.ae_max_iso==requested && maximum==requested;
    mg_printf_http_chunk(c,"\"ae_policy_consumption_verified\":%s,\"ae_policy\":{\"available\":%s,\"available_mask\":%d,\"iso_mode\":",verified?"true":"false",ex_task.ae_mask?"true":"false",ex_task.ae_mask);
    json_string(c,!(ex_task.ae_mask&1) || ex_task.ae_iso_setting<0?"unknown":ex_task.ae_iso_setting==0?"auto":"manual");
    mg_printf_http_chunk(c,",\"iso_setting\":");if (ex_task.ae_mask&1) mg_printf_http_chunk(c,"%d",ex_task.ae_iso_setting);else mg_printf_http_chunk(c,"null");
    mg_printf_http_chunk(c,",\"current_iso\":");if (current) mg_printf_http_chunk(c,"%d",current);else mg_printf_http_chunk(c,"null");
    mg_printf_http_chunk(c,",\"max_iso\":");if (maximum) mg_printf_http_chunk(c,"%d",maximum);else mg_printf_http_chunk(c,"null");
    mg_printf_http_chunk(c,",\"iq_max_iso\":");if (ex_task.ae_mask&2) mg_printf_http_chunk(c,"%d",ex_task.ae_max_iso);else mg_printf_http_chunk(c,"null");
    mg_printf_http_chunk(c,",\"requested_max_iso\":");if (requested) mg_printf_http_chunk(c,"%d",requested);else mg_printf_http_chunk(c,"null");
    mg_printf_http_chunk(c,",\"shooting_mode\":");if (ex_task.ae_mask&4) mg_printf_http_chunk(c,"%d",ex_task.ae_mode);else mg_printf_http_chunk(c,"null");
    mg_printf_http_chunk(c,",\"iq_drive_mode\":");if (ex_task.ae_mask&4) mg_printf_http_chunk(c,"%d",ex_task.ae_mode);else mg_printf_http_chunk(c,"null");
    mg_printf_http_chunk(c,",\"ae_capture_mode\":");if (ex_task.ae_capture_mode>=0) mg_printf_http_chunk(c,"%d",ex_task.ae_capture_mode);else mg_printf_http_chunk(c,"null");
    mg_printf_http_chunk(c,",\"current_sv16\":");if (ex_task.ae_mask&16) mg_printf_http_chunk(c,"%d",ex_task.ae_current_sv);else mg_printf_http_chunk(c,"null");
    mg_printf_http_chunk(c,",\"max_sv16\":");if (ex_task.ae_mask&8) mg_printf_http_chunk(c,"%d",ex_task.ae_max_sv);else mg_printf_http_chunk(c,"null");
    mg_printf_http_chunk(c,",\"policy_matches_requested\":");if (requested && (ex_task.ae_mask&10)==10) mg_printf_http_chunk(c,verified?"true":"false");else mg_printf_http_chunk(c,"null");
    mg_printf_http_chunk(c,",\"ae_solver_verified\":false,\"physical_exposure_verified\":false,\"current_iso_source\":\"original AE current Svalue, not a sensor measurement\"}");
}
static void ex_success(void) {
    struct mg_connection *c=ex_task.connection;
    if (!c) { ex_release();return; }
    mg_printf(c,"HTTP/1.1 200 OK\r\nContent-Type: application/json; charset=utf-8\r\nTransfer-Encoding: chunked\r\nCache-Control: no-store\r\n\r\n");
    mg_printf_http_chunk(c,"{\"namespace_pid\":null,\"camera_pid\":%d,\"native_service\":true,",ex_task.guest_pid);
    if (!ex_task.kind) {
        mg_printf_http_chunk(c,"\"operation\":\"%s\",\"read_only\":%s,\"controls\":{",ex_task.setting?"set":"read",ex_read_only(ex_task.guest_pid)?"true":"false");
        for (int i=0;i<ex_task.count;i++) {
            int id=ex_task.ids[i],name=id==0x37?0:id==0x38?1:2;
            if (i) mg_printf_http_chunk(c,",");
            json_string(c,ex_names[name]);
            unsigned int actual=ex_task.actual[i];
            int valid=(id==0x37 && (actual==0x00370000 || actual==0x00370001)) || (id==0x38 && (actual==0x00380000 || actual==0x00380001)) || (id==0x3b && (actual&0xffff0000U)==0x003b0000U && (actual&0xffffU)<18);
            mg_printf_http_chunk(c,":{\"user_data_id\":\"0x%02x\",\"before_raw\":\"0x%08x\",\"actual_raw\":\"0x%08x\",\"value\":",id,ex_task.before[i],actual);
            ex_iso_value(c,id,actual);
            mg_printf_http_chunk(c,",\"set_accepted\":%s,\"notify_path\":%s,\"value_mapping_verified\":%s",ex_task.setting?(ex_task.applied[i]?"true":"false"):"null",ex_task.setting?(ex_task.notified[i]?"true":"false"):"null",valid?"true":"false");
            if (id==0x3b) {
                mg_printf_http_chunk(c,",\"stock_actual_raw\":\"0x%08x\",\"value_source\":\"%s\",\"supported_values\":[",ex_task.stock_actual[i],actual>0x003b000fU && valid?"mod_iq_parameter":"stock_userdata");
                int count=ex_task.extended_iso_ready?18:16;
                for (int n=0;n<count;n++) mg_printf_http_chunk(c,"%s%d",n?",":"",ex_iso_max[n]);
                mg_printf_http_chunk(c,"],\"extended_ready\":%s",ex_task.extended_iso_ready?"true":"false");
            }
            mg_printf_http_chunk(c,"}");
        }
        mg_printf_http_chunk(c,"},");ex_ae_policy_json(c);mg_printf_http_chunk(c,",\"image_verified\":false,\"persistent\":false}");
    } else if (ex_stage_kind(ex_task.kind)) {
        ex_style_json(c);
        mg_printf_http_chunk(c,",\"operation\":\"%s\",\"source_model\":\"NX500\",",ex_task.color_operation==2?"read":ex_task.color_operation==0?"reset":"set");
        if (ex_task.kind==7) mg_printf_http_chunk(c,"\"applied_to\":\"source RGB columns before native PSS and ColorTone\",");
        else if (ex_task.kind==6) mg_printf_http_chunk(c,"\"applied_to\":\"NX500 level factor on NX300M stock sharpening\",");
        else mg_printf_http_chunk(c,"\"preview_quantization\":\"8-to-10-to-8-bit nearest rounding\",");
        mg_printf_http_chunk(c,"\"image_verified\":false,\"persistent\":false}");
    } else if (ex_task.kind==3) {
        ex_pw_json(c);
        mg_printf_http_chunk(c,",\"image_verified\":false,\"persistent\":false}");
    } else if (ex_task.kind==4) {
        ex_color_json(c);
        mg_printf_http_chunk(c,",\"operation\":\"%s\",\"source_model\":\"NX500\",\"image_verified\":false,\"persistent\":false}",ex_task.color_operation==2?"read":ex_task.color_operation==0?"reset":"set");
    } else if (ex_task.kind==2) {
        ex_color_json(c);
        mg_printf_http_chunk(c,",\"operation\":\"%s\",\"image_verified\":false,\"persistent\":false,\"applied_to\":\"row gains on the latest original Q8 color matrix\"}",ex_task.color_operation==2?"read":ex_task.color_operation==0?"reset":"set");
    } else {
        mg_printf_http_chunk(c,"\"fastest_target\":");json_string(c,ex_fastest[ex_task.choice]);
        mg_printf_http_chunk(c,",\"enabled\":%s,\"limit_tv16\":",ex_task.choice?"true":"false");
        if (ex_task.choice) mg_printf_http_chunk(c,"%d",ex_task.tv16);else mg_printf_http_chunk(c,"null");
        mg_printf_http_chunk(c,",\"limit_microseconds\":");
        if (ex_task.choice) mg_printf_http_chunk(c,"%u",ex_task.microseconds);else mg_printf_http_chunk(c,"null");
        mg_printf_http_chunk(c,",\"last_stock_tv16\":");
        if (ex_task.calls) mg_printf_http_chunk(c,"%d",ex_task.stock);else mg_printf_http_chunk(c,"null");
        mg_printf_http_chunk(c,",\"last_result_tv16\":");
        if (ex_task.calls) mg_printf_http_chunk(c,"%d",ex_task.result);else mg_printf_http_chunk(c,"null");
        mg_printf_http_chunk(c,",\"getter_calls\":%u,\"capped_calls\":%u,\"configured_choice_observed_at_getter\":%s,\"ae_solver_and_physical_exposure_verified\":false,\"persistent\":false}",ex_task.calls,ex_task.capped,ex_task.calls && ex_task.last_choice==ex_task.choice?"true":"false");
    }
    mg_send_http_chunk(c,"",0);ex_release();
}
static void ex_poll(void) {
    if (!ex_task.active || now_ms()<ex_task.next_poll) return;
    ex_task.next_poll=now_ms()+50;
    if (now_ms()>ex_task.deadline) { ex_error(504,"native camera control task deadline exceeded",-108);return; }
    if (ex_ready(ex_task.kind,&ex_task.host_pid)!=ex_task.guest_pid) { ex_error(503,"current camera bridge is no longer ready",-109);return; }
    const char *prefix=ex_prefix(ex_task.kind);
    char path[128];
    if (!ex_task.phase) {
        if (now_ms()>ex_task.lock_deadline) { ex_error(409,"camera control bridge is locked by another client",-111);return; }
        if (flock(ex_task.lock_fd,LOCK_EX|LOCK_NB)) { if (errno!=EAGAIN && errno!=EWOULDBLOCK) ex_error(500,"camera control bridge lock failed",-112);return; }
        ex_task.phase=1;
    }
    if (ex_task.phase==1) {
        ex_task.has_response=0;
        FILE *random=fopen("/dev/urandom","rb");unsigned int id=0;
        int random_ok=random && fread(&id,1,sizeof id,random)==sizeof id;if (random) fclose(random);
        if (!random_ok) { ex_error(500,"request identity source unavailable",-113);return; }
        ex_task.request_id=id%0x7ffffffeU+1;
        snprintf(path,sizeof path,"/var/run/%s-request.tmp",prefix);
        FILE *file=fopen(path,"w");if (!file) { ex_error(500,"cannot write camera control bridge request",-114);return; }
        unsigned int expires=(unsigned int)time(NULL)+6;int index=ex_task.index,written;
        if (ex_style_kind(ex_task.kind)) written=fprintf(file,"%u %u %d %u\n",ex_task.request_id,expires,ex_task.color_operation,ex_task.requested[0]);
        else if (ex_task.kind==4) written=fprintf(file,"%u %u %d %u %u\n",ex_task.request_id,expires,ex_task.color_operation,ex_task.requested[0],ex_task.requested[1]);
        else if (ex_task.kind==3) written=fprintf(file,"%u %u %u %08x %08x %08x %08x\n",ex_task.request_id,ex_task.pw_mask,expires,ex_task.requested[0],ex_task.requested[1],ex_task.requested[2],ex_task.requested[3]);
        else if (ex_task.kind==2 || ex_task.kind==7) written=fprintf(file,"%u %u %d %u %u %u\n",ex_task.request_id,expires,ex_task.color_operation,ex_task.requested[0],ex_task.requested[1],ex_task.requested[2]);
        else if (ex_task.kind==1) written=fprintf(file,"%u %u %d %u\n",ex_task.request_id,expires,ex_task.setting,ex_task.requested[0]);
        else written=fprintf(file,"%u %u %d %08x %08x\n",ex_task.request_id,expires,ex_task.setting,ex_task.ids[index],ex_task.requested[index]);
        int closed=fclose(file);char target[128];snprintf(target,sizeof target,"/var/run/%s-request",prefix);
        if (written<=0 || closed || rename(path,target)) { unlink(path);ex_error(500,"cannot publish camera control bridge request",-114);return; }
        ex_task.response_deadline=now_ms()+8000;ex_task.phase=2;return;
    }
    if (now_ms()>ex_task.response_deadline) { ex_error(504,"original camera bridge did not respond before deadline",-108);return; }
    snprintf(path,sizeof path,"/var/run/%s-response",prefix);FILE *file=fopen(path,"r");if (!file) return;
    char line[512];int read=fgets(line,sizeof line,file)!=NULL;fclose(file);if (!read) return;
    unsigned int id=0;if (sscanf(line,"%u",&id)!=1 || id!=ex_task.request_id) return;
    int code=0,index=ex_task.index,fields;char extra;
    if (ex_task.kind==7) fields=sscanf(line,"%u %d %u %u %u %u %u %u %u %d %d %d %d %d %c",&id,&code,&ex_task.rgb[0],&ex_task.rgb[1],&ex_task.rgb[2],&ex_task.style_enabled,&ex_task.style_generation,&ex_task.style_capture_generation,&ex_task.style_preview_generation,&ex_task.capture_modified,&ex_task.preview_modified,&ex_task.style_capture_error,&ex_task.style_preview_error,&ex_task.response_pid,&extra);
    else if (ex_style_kind(ex_task.kind)) fields=sscanf(line,"%u %d %u %u %u %u %u %d %d %d %d %d %c",&id,&code,&ex_task.style_level,&ex_task.style_enabled,&ex_task.style_generation,&ex_task.style_capture_generation,&ex_task.style_preview_generation,&ex_task.capture_modified,&ex_task.preview_modified,&ex_task.style_capture_error,&ex_task.style_preview_error,&ex_task.response_pid,&extra);
    else if (ex_task.kind==4) fields=sscanf(line,"%u %d %u %u %d %u %u %u %u %d %d %d %d %d %d %d %c",&id,&code,&ex_task.rgb[0],&ex_task.rgb[1],&ex_task.color_active,&ex_task.capture_calls,&ex_task.preview_calls,&ex_task.capture_bypass,&ex_task.preview_bypass,&ex_task.capture_rc,&ex_task.preview_rc,&ex_task.capture_base,&ex_task.preview_base,&ex_task.capture_modified,&ex_task.preview_modified,&ex_task.response_pid,&extra);
    else if (ex_task.kind==3) fields=sscanf(line,"%u %d %x %x %x %x %x %x %x %x %d %d %d %d %d %c",&id,&code,&ex_task.pw_actual[0],&ex_task.pw_actual[1],&ex_task.pw_actual[2],&ex_task.pw_actual[3],&ex_task.pw_iq[0],&ex_task.pw_iq[1],&ex_task.pw_iq[2],&ex_task.pw_iq[3],&ex_task.pw_decoded[0],&ex_task.pw_decoded[1],&ex_task.pw_decoded[2],&ex_task.pw_decoded[3],&ex_task.pw_mode,&extra);
    else if (ex_task.kind==2) fields=sscanf(line,"%u %d %u %u %u %d %u %u %u %u %d %d %d %d %d %d %d %c",&id,&code,&ex_task.rgb[0],&ex_task.rgb[1],&ex_task.rgb[2],&ex_task.color_active,&ex_task.capture_calls,&ex_task.preview_calls,&ex_task.capture_bypass,&ex_task.preview_bypass,&ex_task.capture_rc,&ex_task.preview_rc,&ex_task.capture_base,&ex_task.preview_base,&ex_task.capture_modified,&ex_task.preview_modified,&ex_task.response_pid,&extra);
    else if (ex_task.kind==1) fields=sscanf(line,"%u %d %u %d %u %d %d %u %u %u %c",&id,&code,&ex_task.choice,&ex_task.tv16,&ex_task.microseconds,&ex_task.stock,&ex_task.result,&ex_task.calls,&ex_task.capped,&ex_task.last_choice,&extra);
    else {
        ex_task.ae_capture_mode=-1;
        fields=sscanf(line,"%u %d %x %x %d %d %d %d %d %d %d %d %x %d %d %c",&id,&code,&ex_task.before[index],&ex_task.actual[index],&ex_task.applied[index],&ex_task.notified[index],&ex_task.ae_mask,&ex_task.ae_iso_setting,&ex_task.ae_max_iso,&ex_task.ae_mode,&ex_task.ae_max_sv,&ex_task.ae_current_sv,&ex_task.stock_actual[index],&ex_task.extended_iso_ready,&ex_task.ae_capture_mode,&extra);
        if (fields==6 || fields==12) { ex_task.stock_actual[index]=ex_task.actual[index];ex_task.extended_iso_ready=0; }
    }
    if (ex_task.kind?fields!=(ex_task.kind==7?14:ex_style_kind(ex_task.kind)?12:ex_task.kind==4?16:ex_task.kind==3?15:ex_task.kind==2?17:10):(fields!=6 && fields!=12 && fields!=14 && fields!=15)) { ex_error(502,"invalid original camera control response",-115);return; }
    if (!ex_task.kind && (ex_task.extended_iso_ready<0 || ex_task.extended_iso_ready>1)) { ex_error(502,"invalid extended ISO availability",-115);return; }
    if (!ex_task.kind && ((ex_task.ae_mask&~31) || ex_task.ae_mask<0)) { ex_error(502,"invalid AE availability mask",-115);return; }
    if (ex_stage_kind(ex_task.kind)) {
        ex_task.result=code;
        if (ex_task.response_pid!=ex_task.guest_pid || ex_ready(ex_task.kind,&ex_task.host_pid)!=ex_task.guest_pid) { ex_error(503,"style bridge camera identity changed",-109);return; }
        if ((ex_task.kind==7?(ex_task.rgb[0]>200 || ex_task.rgb[1]>200 || ex_task.rgb[2]>200):ex_task.style_level>20) || ex_task.style_enabled>1 || !ex_task.style_generation || (unsigned int)ex_task.capture_modified>1 || (unsigned int)ex_task.preview_modified>1) { ex_error(502,"invalid original style state",-115);return; }
    }
    if (ex_task.kind==3 && ex_ready(3,&ex_task.host_pid)!=ex_task.guest_pid) { ex_error(503,"Picture Wizard camera identity changed",-109);return; }
    if (ex_task.kind==2 || ex_task.kind==4) {
        ex_task.result=code;
        if (ex_task.response_pid!=ex_task.guest_pid || ex_ready(ex_task.kind,&ex_task.host_pid)!=ex_task.guest_pid) { ex_error(503,"color bridge camera identity changed",-109);return; }
        int chroma=ex_task.kind==4;unsigned int maximum=chroma?20:200,neutral=chroma?10:100;
        if (ex_task.rgb[0]>maximum || ex_task.rgb[1]>maximum || (!chroma && ex_task.rgb[2]>maximum) ||
            (unsigned int)ex_task.color_active>1 || (unsigned int)ex_task.capture_base>1 || (unsigned int)ex_task.preview_base>1 ||
            (unsigned int)ex_task.capture_modified>1 || (unsigned int)ex_task.preview_modified>1 ||
            ex_task.color_active!=(ex_task.rgb[0]!=neutral || ex_task.rgb[1]!=neutral || (!chroma && ex_task.rgb[2]!=neutral))) { ex_error(502,"invalid original color state",-115);return; }
    }
    ex_task.has_response=1;
    if (code) {
        const char *message="original camera control bridge returned an error";
        if(ex_task.kind==4) {
            if(code==-110) message="waiting for original YCC and CHM matrices";
            else if(code==-111) message="color coefficients exceed the camera range";
            else if(code==-112) message="saturation requires P/A/S/M, Standard/Custom1 Picture Wizard and 2D mode";
            else if(code==-114) message="reset native Picture Wizard chroma adjustments before applying saturation";
        }
        if(ex_stage_kind(ex_task.kind) && code==-112) message=ex_task.kind==7?"source RGB requires P/A/S/M, Standard/Custom1 Picture Wizard and 2D mode":ex_task.kind==6?"sharpness requires P/A/S/M, Standard/Custom1 Picture Wizard and 2D mode":"contrast requires P/A/S/M, Standard/Custom1 Picture Wizard and 2D mode";
        ex_error(502,message,code);return;
    }
    if (ex_style_kind(ex_task.kind) && ex_task.setting && (ex_task.style_level!=ex_task.requested[0] || ex_task.style_enabled!=(unsigned int)(ex_task.color_operation==1))) { ex_error(502,"style readback differs from requested state",-116);return; }
    if (ex_task.kind==7 && ex_task.setting && ex_task.style_enabled!=(unsigned int)(ex_task.color_operation==1)) { ex_error(502,"source RGB enabled state differs from request",-116);return; }
    if (ex_task.kind==4 && ex_task.setting && (ex_task.rgb[0]!=ex_task.requested[0] || ex_task.rgb[1]!=ex_task.requested[1])) { ex_error(502,"chroma readback differs from requested steps",-116);return; }
    if (ex_task.kind==3 && ex_task.setting) for (int i=0;i<4;i++) {
        int response_index=i==3?0:i+1;
        if ((ex_task.pw_mask&(1U<<i)) && ex_task.pw_actual[response_index]!=ex_task.requested[i]) { ex_error(502,"Picture Wizard readback differs from requested value",-116);return; }
    }
    if ((ex_task.kind==2 || ex_task.kind==7) && ex_task.setting && (ex_task.rgb[0]!=ex_task.requested[0] || ex_task.rgb[1]!=ex_task.requested[1] || ex_task.rgb[2]!=ex_task.requested[2])) { ex_error(502,"RGB readback differs from requested gains",-116);return; }
    if (ex_task.kind==1 && (ex_task.choice>=10 || (ex_task.setting && ex_task.choice!=ex_task.requested[0]))) { ex_error(502,"shutter readback differs from requested selection",-116);return; }
    if (!ex_task.kind && ex_task.setting && ex_task.actual[index]!=ex_task.requested[index]) { ex_error(502,"UserData readback differs from requested value",-116);return; }
    ex_task.index++;
    if (ex_task.index==ex_task.count) ex_success();else { ex_task.phase=1;ex_task.has_response=0; }
}
static void ex_request(struct mg_connection *c,struct http_message *h,int kind) {
    int setting=!mg_vcmp(&h->method,"POST");
    if (!setting && mg_vcmp(&h->method,"GET")) { reply(c,405,"{\"error\":\"GET or POST required\"}");return; }
    if (!kind && setting && ex_read_only(ex_ready(0,NULL))) {
        reply(c,403,"{\"error\":\"current exposure bridge is read-only\",\"read_only\":true}");return;
    }
    if (ex_task.active) { reply(c,409,"{\"error\":\"native camera control task already running\"}");return; }
    struct ex_fields fields;memset(&fields,0,sizeof fields);
    unsigned int allowed=kind==7?0xf0U:kind==6?0x280U:kind==5?0x480U:kind==4?0x1180U:kind==3?0xf00U:kind==2?0xf0U:kind==1?8U:7U;
    if (setting && (!ex_parse(h,&fields) || !fields.seen || (fields.seen & ~allowed))) { reply(c,400,"{\"error\":\"invalid or unknown camera control parameters\"}");return; }
    int ids[3]={0x37,0x38,0x3b},count=0;unsigned int requested[4]={0};
    int color_operation=2;
    if (ex_style_kind(kind)) {
        int field=kind==6?9:10;
        count=1;requested[0]=10;
        if (setting) {
            if (fields.json && fields.seen==0x80U && !fields.quoted[7] && !strcmp(fields.values[7],"true")) color_operation=0;
            else if (fields.json && fields.seen==(1U<<field) && !fields.quoted[field]) {
                int value;
                if (!ex_integer(fields.values[field],0,20,&value)) { reply(c,400,kind==6?"{\"error\":\"sharpness must be a JSON integer from 0 to 20\"}":"{\"error\":\"contrast must be a JSON integer from 0 to 20\"}");return; }
                requested[0]=(unsigned int)value;color_operation=1;
            } else { reply(c,400,kind==6?"{\"error\":\"provide sharpness:0..20 or reset:true exclusively\"}":"{\"error\":\"provide contrast:0..20 or reset:true exclusively\"}");return; }
        }
    } else if (kind==4) {
        count=1;requested[0]=requested[1]=10;
        if (setting) {
            if (fields.json && fields.seen==0x80U && !fields.quoted[7] && !strcmp(fields.values[7],"true")) color_operation=0;
            else if (fields.json && fields.seen==0x1100U) {
                color_operation=1;
                for (int i=0;i<2;i++) {
                    int field=i?8:12,value;
                    if (fields.quoted[field] || !ex_integer(fields.values[field],0,20,&value)) { reply(c,400,"{\"error\":\"hue and saturation must both be JSON integers from 0 to 20\"}");return; }
                    requested[i]=(unsigned int)value;
                }
            } else { reply(c,400,"{\"error\":\"provide both hue and saturation, or reset:true exclusively\"}");return; }
        }
    } else if (kind==3) {
        count=1;
        for (int i=0;i<4;i++) {
            int value=i==3?-1:0;
            if (setting && (fields.seen&(1U<<(i+8))) && (!fields.json || fields.quoted[i+8] || !ex_integer(fields.values[i+8],i==3?-1:-4,i==3?359:4,&value))) { reply(c,400,"{\"error\":\"Picture Wizard requires JSON integers: scalar -4..4, color_tone -1..359\"}");return; }
            requested[i]=i==3?(unsigned int)value:ex_pw_pack(value);
        }
    } else if (kind==2 || kind==7) {
        count=1;requested[0]=requested[1]=requested[2]=100;
        if (setting) {
            if (fields.seen==0x80U && !strcmp(fields.values[7],"true") && (!fields.json || !fields.quoted[7])) color_operation=0;
            else if (fields.seen==0x70U) {
                color_operation=1;
                for (int i=0;i<3;i++) {
                    int found=0;
                    for (int n=0;n<=200;n++) { char decimal[8];snprintf(decimal,sizeof decimal,"%d",n);if (!strcmp(fields.values[i+4],decimal)) { requested[i]=(unsigned int)n;found=1;break; } }
                    if (!found || (fields.json && fields.quoted[i+4])) { reply(c,400,"{\"error\":\"red, green and blue must all be integers from 0 to 200\"}");return; }
                }
            } else { reply(c,400,"{\"error\":\"provide all RGB gains or reset:true exclusively\"}");return; }
        }
    } else if (kind==1) {
        count=1;
        if (setting) {
            int found=0;for (int i=0;i<10;i++) if (!strcmp(fields.values[3],ex_fastest[i])) { requested[0]=(unsigned int)i;found=1;break; }
            if (!found || (fields.json && !fields.quoted[3])) { reply(c,400,"{\"error\":\"fastest must be off or a supported 1/N target\"}");return; }
        }
    } else for (int i=0;i<3;i++) {
        if (setting && !(fields.seen&(1U<<i))) continue;
        unsigned int raw=0;
        if (setting) {
            const char *value=fields.values[i];int found=0;
            if (i==0) {
                if (!strcmp(value,"one-third")) { raw=0x00370001;found=1; }
                else if (!strcmp(value,"one-stop")) { raw=0x00370000;found=1; }
                if (fields.json && !fields.quoted[i]) found=0;
            } else if (i==1) {
                if (!strcmp(value,"true")) { raw=0x00380001;found=1; }
                else if (!strcmp(value,"false")) { raw=0x00380000;found=1; }
                if (fields.json && fields.quoted[i]) found=0;
            } else {
                for (int n=0;n<18;n++) { char decimal[16];snprintf(decimal,sizeof decimal,"%d",ex_iso_max[n]);if (!strcmp(value,decimal)) { raw=0x003b0000U+(unsigned int)n;found=1;break; } }
                if (fields.json && fields.quoted[i]) found=0;
            }
            if (!found) { reply(c,400,"{\"error\":\"exposure value is outside the verified mapping\"}");return; }
        }
        ids[count]=i==0?0x37:i==1?0x38:0x3b;requested[count++]=raw;
    }
    int host=0,guest=ex_ready(kind,&host);
    if (!guest) { reply(c,503,"{\"error\":\"current camera process has no ready control bridge\"}");return; }
    memset(&ex_task,0,sizeof ex_task);ex_task.lock_fd=-1;ex_task.active=1;ex_task.connection=c;ex_task.kind=kind;ex_task.setting=setting;ex_task.count=count;ex_task.host_pid=host;ex_task.guest_pid=guest;
    memcpy(ex_task.ids,ids,sizeof ids);memcpy(ex_task.requested,requested,sizeof requested);
    ex_task.color_operation=color_operation;
    ex_task.pw_mask=kind==3?fields.seen>>8:0;
    char path[128];snprintf(path,sizeof path,"/var/run/%s-lock",ex_prefix(kind));
    /* Linux local-filesystem flock permits an exclusive read-only descriptor.
     * Host diagnostics may have created this same lock as root with mode 0644. */
    ex_task.lock_fd=open(path,O_RDONLY|O_CREAT,0600);
    if (ex_task.lock_fd<0) { ex_error(500,"cannot open camera control bridge lock",-112);return; }
    ex_task.lock_deadline=now_ms()+2000;ex_task.deadline=now_ms()+12000;
    ex_poll();
}
static void ex_closed(struct mg_connection *c) {
    if (ex_task.connection==c) ex_task.connection=NULL;
}
