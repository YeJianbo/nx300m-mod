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
extern int __lxstat64(int,const char *,struct stat64 *);
extern int __fxstat64(int,int,struct stat64 *);
/* LUT liveview fps cap (P2). 0/1/2 = old-card default, saved selection,
 * invalid value that the readers fall back to 30 on. The live value is the
 * saved card preference; the running cap only changes through the control
 * chain (enable/sync), never from this page alone. */
static int feature_saved_fps=30,feature_fps_state;
static int feature_saved_mode_lock,feature_saved_animation,feature_saved_iso_lock;
static char feature_values[NXUI_MAX_ITEMS][128];
static unsigned char feature_disabled[NXUI_MAX_ITEMS];
/* Wireless controls (2026-11-11): runtime state markers under /var/run,
 * sampled when the page opens. off/starting:<stage>/on/stopping/failed:<stage>
 * come from the control scripts; the page never starts anything on its own.
 * The hotspot address comes from the address file the script writes on a
 * successful start, never from an assumption. */
static char feature_hotspot_state[32],feature_hotspot_ssid[40],feature_hotspot_address[32];
static char feature_telnet_state[32],feature_telnet_address[64];
static void feature_wireless_snapshot(void) {
    char line[256];FILE *in;
    feature_hotspot_state[0]=feature_hotspot_ssid[0]=feature_hotspot_address[0]=0;
    feature_telnet_state[0]=feature_telnet_address[0]=0;
    in=fopen("/var/run/nx300m-hotspot/state","r");
    if(in){if(fgets(line,sizeof line,in)){line[strcspn(line,"\r\n")]=0;snprintf(feature_hotspot_state,sizeof feature_hotspot_state,"%.*s",31,line);}fclose(in);}
    in=fopen("/var/run/nx300m-hotspot/ssid","r");
    if(in){if(fgets(line,sizeof line,in)){line[strcspn(line,"\r\n")]=0;snprintf(feature_hotspot_ssid,sizeof feature_hotspot_ssid,"%.*s",39,line);}fclose(in);}
    if(*feature_hotspot_state&&!strcmp(feature_hotspot_state,"on")){
        in=fopen("/var/run/nx300m-hotspot/address","r");
        if(in){if(fgets(line,sizeof line,in)){line[strcspn(line,"\r\n")]=0;snprintf(feature_hotspot_address,sizeof feature_hotspot_address,"%.*s",31,line);}fclose(in);}
    }
    in=fopen("/var/run/nx300m-telnet/state","r");
    if(in){if(fgets(line,sizeof line,in)){line[strcspn(line,"\r\n")]=0;snprintf(feature_telnet_state,sizeof feature_telnet_state,"%.*s",31,line);}fclose(in);}
    in=fopen("/var/run/nx300m-telnet/listen","r");
    if(in){if(fgets(line,sizeof line,in)){line[strcspn(line,"\r\n")]=0;snprintf(feature_telnet_address,sizeof feature_telnet_address,"%.*s",63,line);}fclose(in);}
}
static void feature_read_lut_fps(const char *path) {
    struct stat64 path_stat,fd_stat;
    char bytes[4];size_t used=0;int fd,bad=0;
    feature_saved_fps=30;feature_fps_state=0;
    if(__lxstat64(3,path,&path_stat)){
        if(errno!=ENOENT)feature_fps_state=2;
        return;
    }
    if(!S_ISREG(path_stat.st_mode)){feature_fps_state=2;return;}
    fd=open(path,O_RDONLY|O_NOFOLLOW|O_CLOEXEC|O_NONBLOCK);
    if(fd<0){feature_fps_state=2;return;}
    if(__fxstat64(3,fd,&fd_stat)||!S_ISREG(fd_stat.st_mode))bad=1;
    while(!bad&&used<sizeof bytes){
        ssize_t n=read(fd,bytes+used,sizeof bytes-used);
        if(n<0&&errno==EINTR)continue;
        if(n<0){bad=1;break;}
        if(!n)break;
        used+=(size_t)n;
    }
    close(fd);
    if(!bad&&used==3&&bytes[2]=='\n'){
        if(bytes[0]=='3'&&bytes[1]=='0')feature_saved_fps=30;
        else if(bytes[0]=='4'&&bytes[1]=='5')feature_saved_fps=45;
        else if(bytes[0]=='6'&&bytes[1]=='0')feature_saved_fps=60;
        else bad=1;
        if(!bad){feature_fps_state=1;return;}
    }
    feature_fps_state=2;
}
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
    /* Persisted LUT liveview fps cap. A missing file is the old-card default
     * (30); a present but unreadable/invalid value shows its own fallback
     * state instead of pretending it was selected. The same strict enum is
     * re-validated by the preview script and the loader. */
    snprintf(path,sizeof path,"%s/.state/lut-fps",nx_ui_data_root());
    feature_read_lut_fps(path);
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
        {char fps_value[128];
         const char *fps_next=feature_saved_fps==30?"45":feature_saved_fps==45?"60":"30";
         const char *fps_note=feature_fps_state==2?" · 无效已回退":feature_fps_state==0?" · 默认":"";
         snprintf(fps_value,sizeof fps_value,"%d fps%s · 点按切 %s%s",
             feature_saved_fps,fps_note,fps_next,feature_preview_enabled?" · 同步":" · 启用预览后生效");
         feature_add("LUT 取景帧率","lut-fps",fps_value,1);}
        char flag[NXUI_PATH];snprintf(flag,sizeof flag,"%s/DISABLED",nx_ui_data_root());
        feature_add("MOD 开机自动启动",access(flag,F_OK)?"disable-autostart":"enable-autostart",access(flag,F_OK)?"开":"关",1);
        feature_add("模块状态","modules","查看",1);feature_add("调试与日志","debug","进入",1);
    }else if(page_id==6){
        feature_add("不记录手动镜头","lens-clear",feature_lens_selected<0?"当前":"",1);
        for(int i=0;i<feature_lens_count;i++){char action[80];snprintf(action,sizeof action,"lens-%d",i);
            feature_add(feature_lenses[i].label,action,i==feature_lens_selected?"当前":"",1);}
        mfw_selected=feature_lens_selected+1;
    }else if(page_id==7){
        feature_snapshot();feature_wireless_snapshot();
        int hotspot_on=feature_hotspot_state[0]&&!strcmp(feature_hotspot_state,"on");
        int hotspot_failed=!strncmp(feature_hotspot_state,"failed",6);
        int hotspot_busy=!strncmp(feature_hotspot_state,"starting",8)||!strcmp(feature_hotspot_state,"stopping");
        char hotspot_value[128];
        if(hotspot_on)
            snprintf(hotspot_value,sizeof hotspot_value,"%s · %s",*feature_hotspot_ssid?feature_hotspot_ssid:"热点",*feature_hotspot_address?feature_hotspot_address:"192.168.0.1");
        else if(hotspot_failed)
            snprintf(hotspot_value,sizeof hotspot_value,"失败 · %s",feature_hotspot_state);
        else if(hotspot_busy)
            snprintf(hotspot_value,sizeof hotspot_value,"切换中 · %s",feature_hotspot_state);
        else
            snprintf(hotspot_value,sizeof hotspot_value,"关 · 手机连此网络后用 App");
        feature_add(hotspot_on?"关闭 MOD 热点":hotspot_busy?"热点切换中":"开启 MOD 热点",hotspot_on?"hotspot-stop":hotspot_busy?"unavailable":"hotspot-start",hotspot_value,hotspot_busy?0:1);
        if(hotspot_on)
            feature_add("显示热点密码","hotspot-credentials","仅屏幕显示",1);
        else
            feature_add("显示热点密码","unavailable","热点未开启",0);
        feature_add("网络信息","network-info","读取一次",1);
        /* 连接鉴权 state combines the persisted flag with the runtime's own
         * publication (.state/remote-auth.status = on/off/failed, rewritten by
         * the daemon after every load and reload). A flag the daemon has not
         * confirmed shows 待确认 instead of pretending the toggle already
         * applies — e.g. the service is down or the fsync was unconfirmed.
         * Damaged or non-regular files show 配置异常: the daemon fails closed
         * and rejects outside connections until the config is repaired. */
        char auth_path[NXUI_PATH],auth_status_path[NXUI_PATH];
        snprintf(auth_path,sizeof auth_path,"%s/.state/remote-auth.enabled",nx_ui_data_root());
        snprintf(auth_status_path,sizeof auth_status_path,"%s/.state/remote-auth.status",nx_ui_data_root());
        char auth_mode[8]="0\n",auth_status[16]="";
        int auth_bad=0,auth_saved_on=0;
        extern int __fxstat(int,int,struct stat *);   /* glibc 2.13 strict-c99: fstat has no plain symbol */
        int auth_fd=open(auth_path,O_RDONLY|O_NOFOLLOW|O_CLOEXEC);
        if(auth_fd>=0){
            struct stat auth_st;
            ssize_t auth_n=-1;
            if(!__fxstat(3,auth_fd,&auth_st)&&S_ISREG(auth_st.st_mode)&&auth_st.st_size>0&&auth_st.st_size<(off_t)sizeof auth_mode)
                auth_n=read(auth_fd,auth_mode,sizeof auth_mode-1);
            if(auth_n>0){auth_mode[auth_n]=0;
                if(!strcmp(auth_mode,"1\n"))auth_saved_on=1;
                else if(strcmp(auth_mode,"0\n"))auth_bad=1;
            }else{auth_bad=1;strcpy(auth_mode,"0\n");}
            close(auth_fd);
        }else if(errno!=ENOENT)auth_bad=1;
        auth_fd=open(auth_status_path,O_RDONLY|O_NOFOLLOW|O_CLOEXEC);
        if(auth_fd>=0){
            struct stat auth_st;
            ssize_t auth_n=-1;
            if(!__fxstat(3,auth_fd,&auth_st)&&S_ISREG(auth_st.st_mode)&&auth_st.st_size>0&&auth_st.st_size<(off_t)sizeof auth_status)
                auth_n=read(auth_fd,auth_status,sizeof auth_status-1);
            if(auth_n>0)auth_status[auth_n]=0;else auth_status[0]=0;
            close(auth_fd);
        }
        auth_status[strcspn(auth_status,"\r\n")]=0;
        int auth_runtime_failed=!strcmp(auth_status,"failed");
        int auth_confirmed=auth_saved_on?!strcmp(auth_status,"on"):!strcmp(auth_status,"off");
        const char *auth_value=auth_bad?"配置异常 · 已拒绝外部连接":
            auth_runtime_failed?"配置异常 · 服务拒绝外部连接":
            auth_saved_on?auth_confirmed?"开":"开 · 待确认":
            auth_confirmed?"关 · 兼容旧版":"关 · 待确认";
        const char *auth_action=(auth_saved_on||auth_bad)?"remote-auth-off":"remote-auth-on";
        feature_add("连接鉴权",auth_action,auth_value,1);
        if(auth_saved_on&&!auth_bad&&!auth_runtime_failed)
            feature_add("显示连接密钥","remote-auth-key","",1);
    }else if(page_id==8){
        feature_add("刷新状态","status","读取一次",1);
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
    if(!strcmp(action,"lut-fps")){
        /* Save failure and a saved selection stay distinct facts: the card
         * write decides the message, and a running preview re-syncs through
         * the same control chain a LUT change uses. The blob defers the
         * sensor-rate rebuild while capture/encoding is busy. */
        if(mfw_readonly){message("截图模式不保存设置");return 1;}
        if(child){message("上一个操作仍在执行");return 1;}
        int next=feature_saved_fps==30?45:feature_saved_fps==45?60:30;
        char text[32];snprintf(text,sizeof text,"%d\n",next);
        if(!nx_ui_pref_write("lut-fps",text)){message("保存失败 · 请重试");return 1;}
        int page=mfw_preview;feature_page(page);
        if(feature_preview_enabled&&access("/var/run/nx300m-lut-selection",F_OK)==0){
            if(start_mfw_action("preview-sync"))return 1;
            message("已保存 · 预览同步未能启动，下次预览操作生效");
            return 1;
        }
        message(feature_preview_enabled?"已保存 · 当前预览无已选 LUT，下次同步生效":"已保存 · 启用预览后生效");
        return 1;
    }
    if(!strcmp(action,"lens-select")){feature_page(6);return 1;}
    if(!strcmp(action,"lens-info")){feature_page(9);return 1;}
    if(!strcmp(action,"connection")){
        feature_page(7);
        /* Bounded async state query on entry: hotspot.sh status re-validates
         * the recorded workers and address and persists concrete failures
         * (failed:worker-dead / failed:address-lost), so a dead AP never
         * keeps reporting on from a stale state file. The completion handler
         * rebuilds the page from the refreshed files. */
        start_mfw_action("hotspot-status");
        return 1;
    }
    if(!strcmp(action,"modules")){feature_page(8);start_mfw_action("status");return 1;}
    if(!strcmp(action,"unavailable")){message("当前不可用");return 1;}
    if(!strcmp(action,"remote-auth-on")||!strcmp(action,"remote-auth-off")||!strcmp(action,"remote-auth-key")) {
        if(mfw_readonly){message("截图模式不执行操作");return 1;}
        if(child){message("上一个操作仍在执行");return 1;}
        if(start_mfw_action(action))message("正在读取连接设置…");else message("操作未能启动");
        return 1;
    }
    /* Wireless toggles: one request at a time; the scripts themselves are
     * idempotent and serialize on a lock, so a second press while a child is
     * running is rejected here rather than queued. Telnet lives in the debug
     * menu (gui_debug.NX300M) and reaches the same scripts through the
     * standard panel-action path. */
    if(!strcmp(action,"hotspot-start")||!strcmp(action,"hotspot-stop")||
       !strcmp(action,"hotspot-credentials")){
        if(mfw_readonly){message("截图模式不执行操作");return 1;}
        if(child){message("上一个操作仍在执行");return 1;}
        const char *panel_action=!strcmp(action,"hotspot-start")?"hotspot-toggle":
            !strcmp(action,"hotspot-stop")?"hotspot-stop":"hotspot-credentials";
        if(start_mfw_action(panel_action))message(!strcmp(action,"hotspot-credentials")?"正在读取热点密码…":"正在切换热点…");
        else message("操作未能启动");
        return 1;
    }
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
