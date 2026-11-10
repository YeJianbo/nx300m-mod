/* Single-window feature navigation. Readiness is sampled on entry, not polled. */
#include "mode_dial_x11.h"
#include "settings_animation_status.h"
struct nx_ui_lens {char id[64],label[128];int min,max;};
static struct nx_ui_lens feature_lenses[64];
static int feature_lens_count,feature_lens_selected=-1,feature_focal;
static int feature_fw_loaded,feature_iso_loaded,feature_preview_enabled,feature_mode_locked=-1;
static int feature_x11_mode_ready;
static int feature_animation_ram_ready;
static int feature_animation_mode=-1,feature_iso_lock=-1,feature_current_mode=-1;
static int feature_auto_iso_max;
static int feature_saved_mode_lock,feature_saved_animation,feature_saved_iso_lock;
static char feature_values[NXUI_MAX_ITEMS][128];
static unsigned char feature_disabled[NXUI_MAX_ITEMS];
static int feature_saved_value(const char *name,int fallback) {
    char path[NXUI_PATH],extra;int value;
    snprintf(path,sizeof path,"%s/.state/%s",nx_ui_data_root(),name);
    FILE *in=fopen(path,"r");if(!in)return fallback;
    int fields=fscanf(in,"%d %c",&value,&extra);fclose(in);
    return fields==1?value:fallback;
}
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
    feature_saved_mode_lock=feature_saved_value("fw-mode-lock",0);
    if(feature_saved_mode_lock!=0&&feature_saved_mode_lock!=1)feature_saved_mode_lock=0;
    feature_saved_animation=feature_saved_value("fw-animation",0);
    if(feature_saved_animation<0||feature_saved_animation>2)feature_saved_animation=0;
    feature_saved_iso_lock=feature_saved_value("fw-auto-iso-lock",0);
    if(feature_saved_iso_lock!=0&&feature_saved_iso_lock!=6400)feature_saved_iso_lock=0;
    feature_fw_loaded=feature_ready("nx300m-firmware-mod","/camera_firmware_mod.so");
    feature_iso_loaded=feature_ready("nx300m-exposure","/exposure_controls.so");
    feature_mode_locked=feature_animation_mode=feature_iso_lock=feature_current_mode=-1;
    FILE *in=fopen("/var/run/nx300m-firmware-mod-response","r");
    if(in){long values[26];int i=0;while(i<26&&fscanf(in,"%ld",&values[i])==1)i++;fclose(in);
        if(i==26&&values[1]==0&&values[2]==feature_fw_loaded){
            /* Response layout per FIRMWARE_ENTRY_CONTROLS.md: values[3]=current
             * mode, [13]=dial lock, [14]=animation, [16]=ISO lock. -1 elsewhere
             * means the bridge never answered, never a fabricated default. */
            feature_mode_locked=(int)values[13];
            feature_animation_mode=(int)values[14];
            feature_iso_lock=(int)values[16];
            feature_current_mode=(int)values[3];
        }}
    snprintf(path,sizeof path,"%s/bin/mode_dial_x11.arm",root);
    feature_x11_mode_ready=access(path,X_OK)==0;
    if(feature_x11_mode_ready){
        int locked=nx_mode_dial_status(display);
        feature_x11_mode_ready=locked>=0;
        if(locked>=0)feature_mode_locked=locked;
    }
    snprintf(path,sizeof path,"%s/bin/settings_animation.arm",root);
    feature_animation_ram_ready=mfw_preview==5&&access(path,X_OK)==0;
    if(feature_animation_ram_ready){
        int mode=nx_animation_query(path);
        feature_animation_ram_ready=mode>=0;
        if(mode>=0)feature_animation_mode=mode;
    }
    fprintf(stderr,"FW_STATE loaded=%d current=%d locked=%d animation=%d iso=%d\n",
        feature_fw_loaded,feature_current_mode,feature_mode_locked,feature_animation_mode,feature_iso_lock);
    in=fopen("/var/run/nx300m-preview-backend","r");feature_preview_enabled=0;
    if(in){if(fgets(line,sizeof line,in))feature_preview_enabled=!strncmp(line,"smartfilter",11);fclose(in);}
    /* Persisted Auto ISO ceiling preference (exposure bridge domain). Zero
     * means no saved extension; the live ceiling is only shown by the action's
     * real read-back, never fabricated here. */
    feature_auto_iso_max=0;
    snprintf(path,sizeof path,"%s/.state/fw-auto-iso-max",nx_ui_data_root());
    in=fopen(path,"r");
    if(in){int ceiling=0;if(fscanf(in,"%d",&ceiling)==1&&
        (ceiling==3200||ceiling==6400||ceiling==12800||ceiling==25600))feature_auto_iso_max=ceiling;
        fclose(in);}
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
static const char *feature_mode_name(int mode) {
    switch(mode){case 1:return "SMART";case 2:return "P";case 3:return "A";case 4:return "S";
    case 5:return "M";case 6:return "BULB";case 7:return "iFn";case 8:return "SMARTPRO";
    case 30:return "Wi-Fi";default:return mode<0?"未读回":"未知";}
}
static void feature_add(const char *label,const char *action,const char *value,int enabled) {
    if(item_count>=NXUI_MAX_ITEMS)return;
    struct nx_item *item=&items[item_count];memset(item,0,sizeof *item);
    snprintf(item->label,sizeof item->label,"%s",label);snprintf(item->command,sizeof item->command,"%s",action);
    snprintf(feature_values[item_count],sizeof feature_values[0],"%s",value);feature_disabled[item_count]=!enabled;item_count++;
}
static void feature_page(int page_id) {
    mfw_preview=page_id;feature_snapshot();item_count=0;mfw_selected=0;menu=1;mfw_services=1;mfw_quick=0;
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
        feature_add("Auto ISO 6400 上限锁定","fw-auto-iso",
            feature_iso_lock==6400?"开":feature_iso_lock==0?"关":feature_fw_loaded?"未读回":feature_saved_iso_lock?"已存开 · 待加载":"已存关 · 待加载",1);
        /* Cycle the extension ceiling over verified stock enums only; the
         * shown value is the saved preference, and each action's FW_MOD line
         * carries the real bridge read-back. */
        {const char *row_action,*row_value;
         if(feature_auto_iso_max==12800){row_action="fw-auto-iso-max-25600";row_value="已存 12800 · 点按升 25600";}
         else if(feature_auto_iso_max==25600){row_action="fw-auto-iso-max-3200";row_value="已存 25600 · 点按恢复 3200";}
         else if(feature_auto_iso_max==3200){row_action="fw-auto-iso-max-6400";row_value="已存 3200 · 点按升 6400";}
         else {row_action="fw-auto-iso-max-12800";row_value=feature_auto_iso_max==6400?"已存 6400 · 点按升 12800":feature_iso_loaded?"未读回 · 点按设 12800":"模块未加载";}
         if(!feature_iso_loaded){snprintf(value,sizeof value,"已存 %d · 待加载",feature_auto_iso_max?feature_auto_iso_max:3200);row_value=value;}
         feature_add("Auto ISO 扩展上限",row_action,row_value,1);}
        feature_add("曝光模块状态","status","查看实际加载状态",1);
    }else if(page_id==5){
        feature_add("顶部模式拨盘锁定","fw-mode-lock",
            feature_mode_locked==1?"开":feature_mode_locked==0?"关":feature_fw_loaded?"未读回":feature_saved_mode_lock?"已存开 · 待加载":"已存关 · 待加载",1);
        feature_add("模式切换动画","fw-animation",
            feature_animation_mode==0?"原厂":feature_animation_mode==1?"瞬切":feature_animation_mode==2?"隐藏":feature_fw_loaded?"未读回":feature_saved_animation==1?"已存瞬切 · 待加载":feature_saved_animation==2?"已存隐藏 · 待加载":"已存原厂 · 待加载",1);
        feature_add("Auto ISO 6400 上限锁定","fw-auto-iso",
            feature_iso_lock==6400?"开":feature_iso_lock==0?"关":feature_fw_loaded?"未读回":feature_saved_iso_lock?"已存开 · 待加载":"已存关 · 待加载",1);
        {char mode_value[128];snprintf(mode_value,sizeof mode_value,"当前 %s · 点按切 P/A/S/M",feature_mode_name(feature_current_mode));
         feature_add("软件切档","fw-mode",mode_value,feature_fw_loaded&&feature_current_mode>=0);}
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
    /* Firmware control toggles: the concrete request value comes from the
     * read-back bridge state, never from the label. */
    if(!strncmp(action,"fw-",3)) {
        if(mfw_readonly){message("截图模式不执行操作");return 1;}
        /* The Auto ISO ceiling lives in the exposure module, not the firmware
         * control bridge; check the module that actually owns the action. */
        if(!strncmp(action,"fw-auto-iso-max-",16)) {
            if(!feature_iso_loaded){
                int ceiling=atoi(action+16);
                if(ceiling!=3200&&ceiling!=6400&&ceiling!=12800&&ceiling!=25600)return 1;
                char text[32];snprintf(text,sizeof text,"%d\n",ceiling);
                int saved=nx_ui_pref_write("fw-auto-iso-max",text);
                if(saved&&ceiling!=6400)saved=nx_ui_pref_write("fw-auto-iso-lock","0\n");
                if(saved){feature_page(4);message("已保存 · 曝光模块待加载，尚未生效");}
                else message("保存失败 · 请重试");
                return 1;
            }
        }
        else if(!feature_fw_loaded&&!(feature_x11_mode_ready&&!strcmp(action,"fw-mode-lock"))&&
                !(feature_animation_ram_ready&&!strcmp(action,"fw-animation"))){
            const char *pref=NULL;int value=0;
            if(!strcmp(action,"fw-mode-lock")){pref="fw-mode-lock";value=!feature_saved_mode_lock;}
            else if(!strcmp(action,"fw-animation")){pref="fw-animation";value=(feature_saved_animation+1)%3;}
            else if(!strcmp(action,"fw-auto-iso")){pref="fw-auto-iso-lock";value=feature_saved_iso_lock?0:6400;}
            if(pref){char text[32];snprintf(text,sizeof text,"%d\n",value);
                int page=mfw_preview;
                int saved=nx_ui_pref_write(pref,text);
                if(saved&&!strcmp(pref,"fw-auto-iso-lock")&&value==6400)saved=nx_ui_pref_write("fw-auto-iso-max",text);
                if(saved){feature_page(page);message("已保存 · 固件模块待加载，尚未生效");}
                else message("保存失败 · 请重试");
            }else message("固件控制模块未加载");
            return 1;
        }
        int retry_slot=mfw_fw_slot(action);
        if(retry_slot>=0&&mfw_fw_retry[retry_slot][0]) {
            start_mfw_action(mfw_fw_retry[retry_slot]);return 1;
        }
        if(!strcmp(action,"fw-mode-lock"))
            start_mfw_action(feature_mode_locked==1?"fw-mode-lock-off":"fw-mode-lock-on");
        else if(!strcmp(action,"fw-animation")) {
            int next=feature_animation_mode<0?1:(feature_animation_mode+1)%3;
            char request[32];snprintf(request,sizeof request,"fw-animation-%d",next);
            start_mfw_action(request);
        }
        else if(!strcmp(action,"fw-auto-iso"))
            start_mfw_action(feature_iso_lock==6400?"fw-auto-iso-0":"fw-auto-iso-6400");
        else if(!strcmp(action,"fw-auto-iso-max-6400")||!strcmp(action,"fw-auto-iso-max-12800")||!strcmp(action,"fw-auto-iso-max-25600")||
                !strcmp(action,"fw-auto-iso-max-3200"))
            start_mfw_action(action);
        else if(!strcmp(action,"fw-mode")) {
            if(feature_current_mode<0){message("模式未读回");return 1;}
            /* Cycle the P/A/S/M photo modes only; SMART/BULB/iFn/Wi-Fi keep
             * their physical dial positions. */
            static const char *names[]={"P","A","S","M"};
            static const int values[]={2,3,4,5};
            int next=0;
            for(int i=0;i<4;i++)if(feature_current_mode==values[i])next=(i+1)%4;
            char request[32];snprintf(request,sizeof request,"fw-mode-%s",names[next]);
            start_mfw_action(request);
        }
        return 1;
    }
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
