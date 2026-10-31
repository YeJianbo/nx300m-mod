/* Read-only installation inventory. Loading a file and enabling its effect
 * are different states; there is no rootfs writer or firmware updater here. */
static int mm_regular(const char *path) {
    struct stat info;return !stat(path,&info) && S_ISREG(info.st_mode);
}
static int mm_loadable(const char *path) {
    struct stat info;
    return !stat(path,&info) && S_ISREG(info.st_mode) && info.st_size>0 && !access(path,R_OK);
}
static const char *mm_names[]={"lut_port.so","ui_mod.so","exposure_controls.so","shutter_limit.so","color-style.so","lcd-capture.so","service-loader.so","gallery-share.so","camera_firmware_mod.so"};
static unsigned mm_profile_mask(const char *profile) {
    if (!strcmp(profile,"services")) return 1U<<6;
    if (!strcmp(profile,"controls")) return (1U<<2)|(1U<<3)|(1U<<5)|(1U<<6)|(1U<<8);
    if (!strcmp(profile,"color")) return (1U<<0)|(1U<<1)|(1U<<4)|(1U<<6);
    if (!strcmp(profile,"full")) return (1U<<9)-1;
    return 0;
}
static int mm_camera_host(void) {
    int host=0;
    if (ex_ready(0,&host) || ex_ready(1,&host) || ex_ready(2,&host)) return host;
    FILE *file=fopen("/var/run/nx300m-firmware-mod-ready","r");
    int guest=0;char extra;int fields=0;
    if (file) { fields=fscanf(file,"%d %c",&guest,&extra);fclose(file); }
    if (fields==1 && ex_ready_pid(guest,&host)) return host;
    /* services-only has no exposure or firmware marker. Still verify the PID
     * against the executable before using its maps, including after resume. */
    file=fopen("/var/run/nx300m-service-loader-status.json","r");
    if (!file) return 0;
    fields=fscanf(file,"{\"owner_pid\":%d,",&guest);fclose(file);
    return fields==1 && ex_ready_pid(guest,&host)?host:0;
}
static int mm_marker(const char *path) {
    struct stat info;char link_byte;
    return !stat(path,&info) || readlink(path,&link_byte,1)>=0;
}
static const char *mm_launch_profile(void) {
    const char *path=mm_marker("/mnt/mmc/nx300m-mod/launch-profile")?
        "/mnt/mmc/nx300m-mod/launch-profile":"/usr/lib/nx300m-mod/launch-profile";
    if (!mm_regular(path)) return "stock";
    char text[64];FILE *file=fopen(path,"r");
    if (!file) return "stock";
    int got=fgets(text,sizeof text,file)!=NULL;fclose(file);
    if (!got) return "stock";
    char *end=strchr(text,'\n');if (end) *end=0;
    const char *profiles[]={"services","controls","color","full"};
    for (unsigned i=0;i<sizeof profiles/sizeof *profiles;i++) if (!strcmp(text,profiles[i])) return profiles[i];
    return "stock";
}
static int mm_mapped(int host,const char *name) {
    if (!host) return -1;
    char path[80],line[2048],suffix[128];
    snprintf(path,sizeof path,"/proc/%d/maps",host);
    FILE *file=fopen(path,"r");if (!file) return -1;
    snprintf(suffix,sizeof suffix,"/%s",name);int found=0;
    while (fgets(line,sizeof line,file)) {
        char *match=strstr(line,suffix);
        if (match && (!match[strlen(suffix)] || isspace((unsigned char)match[strlen(suffix)]))) { found=1;break; }
    }
    fclose(file);return found;
}
static void mm_request(struct mg_connection *c,struct http_message *h,int plan) {
    if (mg_vcmp(&h->method,"GET")) { reply(c,405,"{\"ok\":false,\"error\":\"GET required\"}");return; }
    const char *root=nx_mod_root();
    if (!root) { reply(c,503,"{\"ok\":false,\"error\":\"invalid resource root\"}");return; }
    const char *required[]={"start.sh","stop.sh","common.sh","service.sh","bin/nx-remote-controller.arm","bin/nx-quickshare-armv7","web_root/index.html"};
    int complete=1;char path[256];
    for (unsigned i=0;i<sizeof required/sizeof *required;i++) {
        snprintf(path,sizeof path,"%s/%s",root,required[i]);if (!mm_regular(path)) complete=0;
    }
    mg_send_head(c,200,-1,"Content-Type: application/json; charset=utf-8\r\nCache-Control: no-store");
    mg_printf_http_chunk(c,"{\"ok\":true,\"model\":");json_string(c,get_nx_model_name());
    mg_printf_http_chunk(c,",\"firmware\":");json_string(c,get_nx_model_version());
    mg_printf_http_chunk(c,",\"resource_root\":");json_string(c,root);
    mg_printf_http_chunk(c,",\"sd_service_complete\":%s,\"remote_install_supported\":false,\"hot_unload_supported\":false",complete?"true":"false");
    int disabled=mm_marker("/usr/lib/nx300m-mod/DISABLED") || mm_marker("/mnt/mmc/nx300m-mod/DISABLED");
    const char *configured=mm_launch_profile();
    unsigned selected=mm_profile_mask(configured),missing=0;
    for (unsigned i=0;i<sizeof mm_names/sizeof *mm_names;i++) if (selected & (1U<<i)) {
        snprintf(path,sizeof path,"/usr/lib/nx300m-mod/%s",mm_names[i]);
        if (!mm_loadable(path)) missing|=1U<<i;
    }
    int helper=mm_loadable("/usr/lib/nx300m-mod/preload.sh");
    mg_printf_http_chunk(c,",\"launch_policy\":{\"disabled_marker_present\":%s,\"configured_profile\":",disabled?"true":"false");
    json_string(c,configured);
    mg_printf_http_chunk(c,",\"next_exec_profile\":");
    json_string(c,disabled || missing || !helper?"stock":configured);
    mg_printf_http_chunk(c,",\"selected_files_complete\":%s,\"loader_available\":%s,\"unavailable_modules\":[",missing?"false":"true",helper?"true":"false");
    int comma=0;
    for (unsigned i=0;i<sizeof mm_names/sizeof *mm_names;i++) if (missing & (1U<<i)) {
        if (comma++) mg_printf_http_chunk(c,",");json_string(c,mm_names[i]);
    }
    mg_printf_http_chunk(c,"]");
    mg_printf_http_chunk(c,",\"applies_at\":\"next_app_exec\",\"snapshot_rollback_supported\":false}");
    if (plan) {
        mg_printf_http_chunk(c,",\"stock_firmware_file_present\":%s,\"mod_steps\":[",mm_regular("/mnt/mmc/nx300m.bin")?"true":"false");
        json_string(c,"导入开发安装候选，检查九模块、原厂恢复文件和配套 SD 服务包；默认 stock 不注入模块，按 launch-profile 分组启用。");
        mg_printf_http_chunk(c,",");json_string(c,"将完整 nx300m-mod 目录准备到 SD 根目录；仅复制 SD 资源不会加载机内模块。");
        mg_printf_http_chunk(c,",");json_string(c,"rootfs 增量与休眠快照重建需要已验证的机身安装入口；当前 HTTP 服务不提供写入或刷机执行器。");
        mg_printf_http_chunk(c,"],\"stock_steps\":[");json_string(c,"从三星对应 NX300M 机型支持页取得未修改的原厂固件，确认型号与升级说明。");
        mg_printf_http_chunk(c,",");json_string(c,"为电池充足充电，备份照片，按原厂说明将 nx300m.bin 放到 SD 根目录。");
        mg_printf_http_chunk(c,",");json_string(c,"在相机原厂菜单的固件更新入口执行；更新期间保持供电。手机不会代替机身执行更新。");
        mg_printf_http_chunk(c,"]}");
    } else {
        const char *titles[]={"LUT 色彩","机内扩展界面","ISO 控制","快门限制","配方与色彩","屏幕采集","服务自动加载","相册 Quick Share","机身扩展控制"};
        int host=mm_camera_host();
        mg_printf_http_chunk(c,",\"camera_process_verified\":%s,\"modules\":[",host?"true":"false");
        for (unsigned i=0;i<sizeof mm_names/sizeof *mm_names;i++) {
            snprintf(path,sizeof path,"/usr/lib/nx300m-mod/%s",mm_names[i]);
            int mapped=mm_mapped(host,mm_names[i]);
            int ready=i==0?lk_ready():i==2?(ex_ready(0,NULL) && !ex_read_only(ex_ready(0,NULL))):i==3?!!ex_ready(1,NULL):i==4?!!ex_ready(2,NULL):-1;
            if (i) mg_printf_http_chunk(c,",");
            mg_printf_http_chunk(c,"{\"id\":");json_string(c,mm_names[i]);
            mg_printf_http_chunk(c,",\"name\":");json_string(c,titles[i]);
            mg_printf_http_chunk(c,",\"installed_file\":%s,\"mapped\":%s,\"ready\":%s}",
                mm_regular(path)?"true":"false",mapped<0?"null":mapped?"true":"false",ready<0?"null":ready?"true":"false");
        }
        mg_printf_http_chunk(c,"]}");
    }
    mg_send_http_chunk(c,"",0);
}
