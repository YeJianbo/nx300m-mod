/* Single-window feature navigation. Readiness is sampled on entry, not polled. */
struct nx_ui_lens {char id[64],label[128];int min,max;};
static struct nx_ui_lens feature_lenses[64];
static int feature_lens_count,feature_lens_selected=-1,feature_focal;
static int feature_fw_loaded,feature_iso_loaded,feature_preview_enabled,feature_mode_locked=-1;
static char feature_values[NXUI_MAX_ITEMS][128];
static unsigned char feature_disabled[NXUI_MAX_ITEMS];
static int feature_ready(const char *prefix,const char *module) {
    char path[NXUI_PATH],exe[256],line[512],extra;int pid=0;
    snprintf(path,sizeof path,"/var/run/%s-ready",prefix);FILE *in=fopen(path,"r");if(!in)return 0;
    int n=fscanf(in,"%d %c",&pid,&extra);fclose(in);if(n!=1||pid<2||kill(pid,0))return 0;
    snprintf(path,sizeof path,"/proc/%d/exe",pid);ssize_t bytes=readlink(path,exe,sizeof exe-1);
    if(bytes<0)return 0;
    exe[bytes]=0;
    if(strcmp(exe,"/usr/bin/di-camera-app-nx300"))return 0;
    snprintf(path,sizeof path,"/proc/%d/maps",pid);in=fopen(path,"r");if(!in)return 0;
    int found=0;while(fgets(line,sizeof line,in))if(strstr(line,module)){found=pid;break;}fclose(in);return found;
}
static void feature_snapshot(void) {
    char path[NXUI_PATH],line[256];
    feature_fw_loaded=feature_ready("nx300m-firmware-mod","/camera_firmware_mod.so");
    feature_iso_loaded=feature_ready("nx300m-exposure","/exposure_controls.so");
    feature_mode_locked=-1;
    FILE *in=fopen("/var/run/nx300m-firmware-mod-response","r");
    if(in){long values[26];int i=0;while(i<26&&fscanf(in,"%ld",&values[i])==1)i++;fclose(in);
        if(i==26&&values[1]==0&&values[2]==feature_fw_loaded)feature_mode_locked=(int)values[13];}
    in=fopen("/var/run/nx300m-preview-backend","r");feature_preview_enabled=0;
    if(in){if(fgets(line,sizeof line,in))feature_preview_enabled=!strncmp(line,"smartfilter",11);fclose(in);}
    feature_lens_count=0;feature_lens_selected=-1;feature_focal=0;
    snprintf(path,sizeof path,"%s/lenses.txt",nx_ui_data_root());in=fopen(path,"r");
    if(in){while(feature_lens_count<64&&fgets(line,sizeof line,in)){
        struct nx_ui_lens *lens=&feature_lenses[feature_lens_count];
        if(sscanf(line,"%63[^|]|%127[^|]|%d|%d",lens->id,lens->label,&lens->min,&lens->max)==4&&
            lens->min>=1&&lens->max>=lens->min&&lens->max<=2000)feature_lens_count++;
    }fclose(in);}
    snprintf(path,sizeof path,"%s/.state/lens-choice",nx_ui_data_root());in=fopen(path,"r");
    if(in){char id[64];int focal;
        if(fscanf(in,"%63s %d",id,&focal)==2)for(int i=0;i<feature_lens_count;i++)if(!strcmp(id,feature_lenses[i].id)&&focal>=feature_lenses[i].min&&focal<=feature_lenses[i].max){feature_lens_selected=i;feature_focal=focal;break;}
        fclose(in);
    }
}
static void feature_add(const char *label,const char *action,const char *value,int enabled) {
    if(item_count>=NXUI_MAX_ITEMS)return;
    struct nx_item *item=&items[item_count];memset(item,0,sizeof *item);
    snprintf(item->label,sizeof item->label,"%s",label);snprintf(item->command,sizeof item->command,"%s",action);
    snprintf(feature_values[item_count],sizeof feature_values[0],"%s",value);feature_disabled[item_count]=!enabled;item_count++;
}
static void feature_page(int page_id) {
    feature_snapshot();item_count=0;mfw_selected=0;mfw_preview=page_id;menu=1;mfw_services=1;mfw_quick=0;
    mfw_status[0]=status[0]=0;
    char value[128];
    if(page_id==3){
        feature_add("当前镜头","lens-info",feature_lens_selected<0?"未记录":feature_lenses[feature_lens_selected].label,1);
        feature_add("选择镜头","lens-select",feature_lens_count?"选择预设":"暂无预设",feature_lens_count>0);
        snprintf(value,sizeof value,"%d mm",feature_focal);
        feature_add("记录焦距","lens-focal",feature_lens_selected<0?"先选择镜头":value,feature_lens_selected>=0&&feature_lenses[feature_lens_selected].max>feature_lenses[feature_lens_selected].min);
        feature_add("手动镜头泛绿修复","unavailable","未完成实机验证",0);
        feature_add("照片 EXIF 自动关联","unavailable","尚未接入拍摄事件",0);
    }else if(page_id==4){
        feature_add("A 挡自动快门范围","unavailable","尚未接入",0);
        feature_add("P 挡自动快门范围","unavailable","尚未接入",0);
        feature_add("Auto ISO 6400","unavailable",feature_iso_loaded?"控制入口待接入":"模块未加载",0);
        feature_add("Auto ISO 12800","unavailable",feature_iso_loaded?"控制入口待接入":"模块未加载",0);
        feature_add("Auto ISO 25600","unavailable","尚未实机验证",0);
        feature_add("曝光模块状态","status","查看实际加载状态",1);
    }else if(page_id==5){
        feature_add("顶部模式拨盘锁定","unavailable",feature_fw_loaded?"机内控制入口待接入":"模块未加载",0);
        feature_add("模式切换动画","unavailable",feature_fw_loaded?"机内控制入口待接入":"模块未加载",0);
        char flag[NXUI_PATH];snprintf(flag,sizeof flag,"%s/DISABLED",nx_ui_data_root());
        feature_add("MOD 开机自动启动",access(flag,F_OK)?"disable-autostart":"enable-autostart",access(flag,F_OK)?"开":"关",1);
        feature_add("模块状态","modules","查看",1);feature_add("调试与日志","debug","进入",1);
    }else if(page_id==6){
        feature_add("不记录手动镜头","lens-clear",feature_lens_selected<0?"当前":"",1);
        for(int i=0;i<feature_lens_count;i++){char action[80];snprintf(action,sizeof action,"lens-%d",i);
            feature_add(feature_lenses[i].label,action,i==feature_lens_selected?"当前":"",1);}
        mfw_selected=feature_lens_selected+1;
    }else if(page_id==7||page_id==8){
        feature_add("刷新状态",page_id==7?"network-info":"status","读取一次",1);
    }else if(page_id==9){
        feature_add("镜头标注","unavailable",feature_lens_selected<0?"未记录":feature_lenses[feature_lens_selected].label,0);
        feature_add("保存范围","unavailable","仅记录预设和焦距",0);
        feature_add("照片 EXIF","unavailable","尚未关联拍摄事件",0);
    }
    resize_panel();dirty=1;
}
static int feature_action(const char *action) {
    if(!strcmp(action,"lenses")){feature_page(3);return 1;}
    if(!strcmp(action,"exposure")){feature_page(4);return 1;}
    if(!strcmp(action,"settings")){feature_page(5);return 1;}
    if(!strcmp(action,"lens-select")){feature_page(6);return 1;}
    if(!strcmp(action,"lens-info")){feature_page(9);return 1;}
    if(!strcmp(action,"connection")){feature_page(7);start_mfw_action("network-info");return 1;}
    if(!strcmp(action,"modules")){feature_page(8);start_mfw_action("status");return 1;}
    if(!strcmp(action,"unavailable")){message("当前不可用");return 1;}
    if(!strncmp(action,"lens-",5)&&strcmp(action,"lens-focal")){
        char *end;long index=strtol(action+5,&end,10);int clear=!strcmp(action,"lens-clear");
        if(!clear&&(*end||index<0||index>=feature_lens_count))return 0;
        char value[128];if(clear)snprintf(value,sizeof value,"none 0\n");
        else snprintf(value,sizeof value,"%s %d\n",feature_lenses[index].id,feature_lenses[index].min);
        if(mfw_readonly){message("截图模式不保存设置");return 1;}
        if(nx_ui_pref_write("lens-choice",value)){feature_page(3);message("镜头记录已保存");}
        else message("镜头记录保存失败");
        return 1;
    }
    if(!strcmp(action,"lens-focal")){
        if(feature_lens_selected<0||mfw_readonly)return 1;
        struct nx_ui_lens *lens=&feature_lenses[feature_lens_selected];
        int next=feature_focal+1;if(next>lens->max)next=lens->min;
        char value[128];snprintf(value,sizeof value,"%s %d\n",lens->id,next);
        if(nx_ui_pref_write("lens-choice",value))feature_page(3);else message("焦距保存失败");return 1;
    }
    return 0;
}
