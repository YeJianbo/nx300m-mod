/* Event-driven LUT browser. No sensor readback or frame timer. */
#include "assets/lut_reference_rgb.h"
static uint32_t lut_reference_pixels[NX_REF_W*NX_REF_H];
static struct nx_cpu_image lut_reference={NX_REF_W,NX_REF_H,lut_reference_pixels};
static uint16_t lut_reference_table[2190];
static int lut_reference_index=-1,lut_reference_valid;
static int lut_reference_mono,lut_nav_dirty;
static int lut_field_supported(int field) {return selected&&!(field==2&&lut_reference_valid&&lut_reference_mono);}
static void lut_save_navigation(void) {
    if(!lut_nav_dirty)return;
    char value[16];snprintf(value,sizeof value,"%d\n",lut_category);
    if(nx_ui_pref_write("lut-category",value))lut_nav_dirty=0;
}
static void lut_request_leave(int target) {
    if(child){if(busy_kind==1)lut_exit_after_save=target;return;}
    if(queue.commit_requested){lut_exit_after_save=target;return;}
    if(queue.close_requested)return;
    if(lut_has_changes()){lut_leave_dialog=1;lut_leave_target=target;lut_leave_option=2;dirty=1;return;}
    lut_save_navigation();
    if(target==2)mfw_return_requested=dirty=1;else queue.close_requested=1;
}
static void lut_leave_pick(int option) {
    if(option==0){lut_leave_dialog=0;lut_exit_after_save=lut_leave_target;confirm_lut();}
    else if(option==1){lut_draft=lut_leave_dialog=0;lut_save_navigation();if(lut_leave_target==2)mfw_return_requested=dirty=1;else queue.close_requested=1;}
    else {lut_leave_dialog=0;dirty=1;}
}
static void lut_toggle_view(void) {
    if(child||lut_leave_dialog)return;
    lut_category_focus=0;
    if(!lut_list_open){lut_list_open=1;lut_edit=0;if(lut_has_changes())confirm_lut();}
    else {
        if(!selected)for(int i=1;i<count;i++)if(!strcmp(recipes[i].key,"nx300-adjust-base")){choose(i);break;}
        lut_list_open=0;lut_edit=selected!=0;lut_field=0;
    }
    lut_list_cursor=0;for(int i=1;i<selected;i++)if(lut_in_group(i))lut_list_cursor++;
    fprintf(stderr,"LUT_VIEW list=%d key=%s saving=%d\n",lut_list_open,recipes[selected].key,queue.commit_requested);
    dirty=1;
}

static void lut_load_tune(void) {
    struct nx_lut_tune next={{0}};unsigned strength=100;
    if(selected>0)nx_tune_load(root,recipes[selected].key,&next,&strength);
    lut_tune=next;lut_strength=(int)strength;lut_reference_dirty=1;
}
static int lut_detect_brand(int i) {
    const char *key=recipes[i].key,*label=recipes[i].label;
    if(strstr(key,"fuji")||strstr(label,"富士"))return 2;
    if(strstr(key,"olympus")||strstr(label,"奥巴")||strstr(label,"奥林巴斯"))return 3;
    if(strstr(key,"pentax")||strstr(label,"宾得"))return 4;
    if(strstr(key,"panasonic")||strstr(label,"松下")||strstr(label,"Pana"))return 5;
    if(strstr(key,"ricoh")||strstr(label,"理光")||strstr(label,"GR "))return 6;
    if(strstr(key,"sony")||strstr(label,"索尼"))return 7;
    return 8;
}
static void lut_init_groups(void) {
    memset(lut_category_count,0,sizeof lut_category_count);
    memset(lut_favorite,0,sizeof lut_favorite);
    char file[NXUI_PATH],key[64];snprintf(file,sizeof file,"%s/.state/lut-favorites",root);
    FILE *in=fopen(file,"r");
    if(in) {
        while(fscanf(in,"%63s",key)==1)for(int i=1;i<count;i++)if(!strcmp(key,recipes[i].key)){lut_favorite[i]=1;break;}
        fclose(in);
    }
    for(int i=0;i<count;i++) {
        lut_brand[i]=i?lut_detect_brand(i):0;
        lut_category_count[0]++;
        if(i)lut_category_count[lut_brand[i]]++;
        if(lut_favorite[i])lut_category_count[1]++;
    }
}
static int lut_in_group(int i) {
    return i>0&&(lut_category==0 || (lut_category==1?lut_favorite[i]:lut_brand[i]==lut_category));
}
static void lut_step(int delta) {
    if(child||queue.close_requested)return;
    int next=selected;
    for(int i=0;i<count;i++) {
        next=(next+delta+count)%count;
        if(lut_in_group(next)){choose(next);return;}
    }
}
static void lut_set_category(int group) {
    if(child||queue.close_requested)return;
    lut_category=group;lut_category_focus=0;lut_edit=0;lut_tab_start=-1;lut_nav_dirty=dirty=1;
    /* Changing a group is local navigation, not a confirmed LUT request. */
    if(!lut_in_group(selected))for(int i=group>=2?1:0;i<count;i++)if(lut_in_group(i)) {
        choose(i);break;
    }
    lut_list_cursor=0;for(int i=1;i<selected;i++)if(lut_in_group(i))lut_list_cursor++;
}
static void lut_next_category(int delta) {
    int next=lut_category;
    for(int i=0;i<9;i++) {
        next=(next+delta+9)%9;
        if(next<=1||lut_category_count[next]){lut_set_category(next);lut_category_focus=1;return;}
    }
}
static void lut_adjust(int delta) {
    if(child||queue.close_requested||!lut_field_supported(lut_field))return;
    if(lut_field==7) {change_lut_strength(5*delta);return;}
    int next=lut_tune.value[lut_field]+delta;
    if(next<-10||next>10)return;
    lut_tune.value[lut_field]=next;lut_draft=1;lut_reference_dirty=dirty=1;
}
static void lut_key(KeySym key) {
    int left=key==XK_Left||key==XK_KP_Left||key==XF86XK_ScrollDown;
    int right=key==XK_Right||key==XK_KP_Right||key==XF86XK_ScrollUp;
    int up=key==XK_Up||key==XK_KP_Up,down=key==XK_Down||key==XK_KP_Down;
    int ok=key==XK_Return||key==XK_KP_Enter;
    if(key==XF86XK_HomePage&&!lut_leave_dialog&&!child){lut_toggle_view();return;}
    if(child||queue.commit_requested){if(key==XK_Menu||key==XK_Escape)lut_request_leave(mfw_menu_root?2:1);return;}
    if(lut_leave_dialog){
        if(left||right)lut_leave_option=(lut_leave_option+(right?1:2))%3;
        if(ok)lut_leave_pick(lut_leave_option);
        if(key==XK_Menu||key==XK_Escape)lut_leave_dialog=0;
        dirty=1;return;
    }
    if(lut_category_focus){
        if(left||right)lut_next_category(right?1:-1);
        if(down||ok)lut_category_focus=0;
        if(key==XK_Menu||key==XK_Escape)lut_request_leave(mfw_menu_root?2:1);
        dirty=1;return;
    }
    if(lut_list_open){
        int options[NXUI_MAX_RECIPES],n=0;for(int i=1;i<count;i++)if(lut_in_group(i))options[n++]=i;
        if(up&&lut_list_cursor==0)lut_category_focus=1;
        else if(n&&(left||right||up||down)){lut_list_cursor=(lut_list_cursor+(right||down?1:n-1))%n;choose(options[lut_list_cursor]);}
        if(ok&&n){choose(options[lut_list_cursor%n]);confirm_lut();}
        if(key==XK_Menu||key==XK_Escape)lut_request_leave(mfw_menu_root?2:1);
        dirty=1;return;
    }
    if(key==XK_space){lut_reference_scene=(lut_reference_scene+1)%NX_REF_COUNT;lut_reference_dirty=dirty=1;return;}
    /* Fn owns view changes. Reference-image taps and the top parameter must
     * never silently turn a tuning-wheel event into selection of another LUT. */
    if(!selected)return;
    lut_edit=1;
    if(left||right)lut_adjust(right?1:-1);
    if(down){do{lut_field=(lut_field+1)%8;}while(!lut_field_supported(lut_field));}
    if(up){do{lut_field=(lut_field+7)%8;}while(!lut_field_supported(lut_field));}
    if(ok)confirm_lut();
    dirty=1;
}
static void lut_toggle_favorite(void) {
    if(!live||!selected||child||queue.close_requested)return;
    char directory[NXUI_PATH],file[NXUI_PATH];struct stat64 st;
    snprintf(directory,sizeof directory,"%s/.state",root);
    if((mkdir(directory,0700)&&errno!=EEXIST)||__lxstat64(3,directory,&st)||!S_ISDIR(st.st_mode)){message("收藏未保存");return;}
    snprintf(file,sizeof file,"%s/.state/lut-favorites",root);
    int fd=open(file,O_WRONLY|O_CREAT|O_TRUNC|O_NOFOLLOW,0600),ok=fd>=0;
    FILE *out=ok?fdopen(fd,"w"):NULL;
    if(out) {
        lut_favorite[selected]=!lut_favorite[selected];
        for(int i=1;i<count;i++)if(lut_favorite[i]&&fprintf(out,"%s\n",recipes[i].key)<0)ok=0;
        if(fflush(out)||fsync(fd))ok=0;
        if(fclose(out))ok=0;
    } else {if(fd>=0)close(fd);ok=0;}
    if(ok) {
        int fd=open(directory,O_RDONLY|O_DIRECTORY);if(fd<0)ok=0;
        else {if(fsync(fd))ok=0;close(fd);}
    }
    lut_init_groups();dirty=1;
    if(!ok)message("收藏未保存，请重试");
}
static void lut_update_reference(void) {
    if(!lut_reference_dirty)return;
    lut_reference_dirty=0;
    if(lut_reference_index!=selected) {
        lut_reference_index=selected;lut_reference_valid=0;
        lut_reference_mono=0;
        if(selected) {
            char file[NXUI_PATH];snprintf(file,sizeof file,"%s/looks/%s/table.d4lut",root,recipes[selected].key);
            FILE *in=fopen(file,"rb");
            if(in){lut_reference_valid=fread(lut_reference_table,1,4380,in)==4380 && fgetc(in)==EOF && !ferror(in);fclose(in);}
            if(lut_reference_valid)for(unsigned i=0;i<2190;i++)if(lut_reference_table[i]>1023||(i%730==729&&lut_reference_table[i]))lut_reference_valid=0;
            if(lut_reference_valid){lut_reference_mono=1;for(unsigned i=0;i<729;i++)if(lut_reference_table[i]!=lut_reference_table[730+i]||lut_reference_table[i]!=lut_reference_table[1460+i]){lut_reference_mono=0;break;}}
        }
    }
    uint16_t tuned[2190];if(lut_reference_valid)nx_tune_table(lut_reference_table,tuned,&lut_tune);
    for(unsigned i=0;i<NX_REF_W*NX_REF_H;i++) {
        const unsigned char *src=nx_reference_rgb[lut_reference_scene]+3*i;unsigned output[3]={src[0],src[1],src[2]};
        if(lut_reference_valid&&!lut_reference_original) {
            unsigned pos[3],f[3];
            for(int c=0;c<3;c++){unsigned n=src[c]*8;pos[c]=n/255;f[c]=n%255;if(pos[c]==8){pos[c]=7;f[c]=255;}}
            for(int c=0;c<3;c++) {
                uint64_t sum=0;
                for(unsigned a=0;a<2;a++)for(unsigned b=0;b<2;b++)for(unsigned d=0;d<2;d++) {
                    unsigned index=(pos[0]+a)*81+(pos[1]+b)*9+pos[2]+d;
                    sum+=(uint64_t)tuned[c*730+index]*(a?f[0]:255-f[0])*(b?f[1]:255-f[1])*(d?f[2]:255-f[2]);
                }
                unsigned v=(unsigned)((sum+8290687)/16581375);
                unsigned color=(v*255+511)/1023;
                output[c]=(color*lut_strength+src[c]*(100-lut_strength)+50)/100;
            }
        }
        lut_reference_pixels[i]=0xff000000U|(output[0]<<16)|(output[1]<<8)|output[2];
    }
}
static void lut_place(float x,float y,float w,float h) {nk_layout_space_push(ctx,nk_rect(x,y,w,h));}
static int lut_button(const char *label,int active) {
    struct nk_style_button saved=ctx->style.button;
    if(active) {
        ctx->style.button.normal=nk_style_item_color(nk_rgb(35,111,188));
        ctx->style.button.hover=ctx->style.button.normal;
        ctx->style.button.text_normal=nk_rgb(255,255,255);
    }
    int clicked=nk_button_label(ctx,label);ctx->style.button=saved;return clicked;
}
static void lut_slider_row(int field,float x,float y,float w) {
    struct nk_rect bounds;
    const int strength=field==7,min=strength?0:-10,max=strength?100:10,step=strength?5:1;
    int value=strength?lut_strength:lut_tune.value[field];
    int focused=lut_edit&&lut_field==field;
    int supported=lut_field_supported(field);
    struct nk_command_buffer *canvas=nk_window_get_canvas(ctx);
    struct nk_rect row=nk_layout_space_rect_to_screen(ctx,nk_rect(x,y,w,42));
    nk_fill_rect(canvas,row,7,focused?nk_rgb(29,51,75):nk_rgb(31,34,39));
    struct nk_style_button button=ctx->style.button;
    ctx->style.button.normal=ctx->style.button.hover=nk_style_item_color(nk_rgba(0,0,0,0));
    ctx->style.button.text_alignment=NK_TEXT_LEFT;
    if(!supported)ctx->style.button.text_normal=nk_rgb(115,123,133);
    lut_place(x+10,y,112,42);
    if(nk_button_label(ctx,nx_tune_label(field))&&supported){lut_field=field;lut_edit=1;lut_category_focus=0;dirty=1;}
    ctx->style.button=button;
    lut_place(x+124,y, w-194,42);
    nk_widget(&bounds,ctx);
    if(supported&&!lut_leave_dialog&&!child&&!queue.close_requested&&nk_input_is_mouse_down(&ctx->input,NK_BUTTON_LEFT)&&
       nk_input_has_mouse_click_down_in_rect(&ctx->input,NK_BUTTON_LEFT,bounds,1)) {
        float t=(ctx->input.mouse.pos.x-bounds.x-8)/(bounds.w-16);
        if(t<0)t=0;
        if(t>1)t=1;
        int next=min+(int)(t*(max-min)/step+0.5f)*step;
        if(next!=value) {
            if(strength)lut_strength=next;else lut_tune.value[field]=next;
            value=next;lut_draft=1;lut_reference_dirty=dirty=1;
        }
    }
    float fraction=(float)(value-min)/(max-min),travel=bounds.w-16;
    nk_fill_rect(canvas,nk_rect(bounds.x+8,bounds.y+19,travel,4),2,nk_rgb(69,75,84));
    float zero=strength?0:travel/2,position=travel*fraction;
    if(supported)nk_fill_rect(canvas,nk_rect(bounds.x+8+(position<zero?position:zero),bounds.y+19,
                 position<zero?zero-position:position-zero,4),2,nk_rgb(76,154,232));
    if(!strength)nk_fill_rect(canvas,nk_rect(bounds.x+8+travel/2,bounds.y+15,1,12),0,nk_rgb(128,137,148));
    if(supported)nk_fill_rect(canvas,nk_rect(bounds.x+position+3,bounds.y+11,10,20),4,nk_rgb(219,234,249));
    char number[24];snprintf(number,sizeof number,strength?"%d%%":"%+d",value);
    lut_place(x+w-66,y,58,42);nk_label_colored(ctx,supported?number:"—",NK_TEXT_RIGHT,nk_rgb(225,232,241));
}
static void draw_lut_panel(void) {
    lut_update_reference();char line[256];
    struct nk_style_button saved_button=ctx->style.button;
    ctx->style.button.rounding=7;ctx->style.button.border=0;
    if(nk_begin(ctx,"NXWA",nk_rect(0,0,(float)width,(float)height),NK_WINDOW_NO_SCROLLBAR)) {
        if(lut_leave_dialog||child)nk_widget_disable_begin(ctx);
        nk_layout_space_begin(ctx,NK_STATIC,(float)height-18,100);
        float right=(float)width-24;
        lut_place(4,0,92,42);nk_label(ctx,"LUT 滤镜",NK_TEXT_LEFT);
        lut_place(100,0,122,42);
        struct nk_style_button danger=ctx->style.button;
        ctx->style.button.normal=ctx->style.button.hover=nk_style_item_color(nk_rgb(116,48,48));
        if(nk_button_label(ctx,selected?"关闭 LUT":"LUT 已关")&&!child){choose(0);confirm_lut();}
        ctx->style.button=danger;
        char backend[32]="";FILE *marker=fopen("/var/run/nx300m-preview-backend","r");
        if(marker){if(!fgets(backend,sizeof backend,marker))backend[0]=0;fclose(marker);}
        snprintf(line,sizeof line,"实时预览 %s",!strncmp(backend,"smartfilter",11)?"开":"关");
        lut_place(230,0,164,42);if(nk_button_label(ctx,line)&&live&&!child)start_mfw_action("preview-toggle");
        lut_place(400,0,144,42);nk_label_colored(ctx,child?"正在提交":lut_submit_failed?"提交失败":lut_has_changes()?"已修改":lut_saved_config?"配置已保存":"当前会话",NK_TEXT_LEFT,nk_rgb(140,180,221));
        lut_place(right-224,0,104,42);
        if(nk_button_label(ctx,mfw_menu_root?"返回":"关闭")) {
            lut_request_leave(mfw_menu_root?2:1);
        }
        lut_place(right-112,0,112,42);
        if(lut_button("保存",1)&&(!selected||lut_in_group(selected)))confirm_lut();
        int groups[9],ng=0;
        for(int g=0;g<9;g++)if(g<=1||lut_category_count[g])groups[ng++]=g;
        int visible=ng<6?ng:6,category_position=0;for(int j=0;j<ng;j++)if(groups[j]==lut_category)category_position=j;
        if(lut_tab_start<0)lut_tab_start=category_position>=visible?ng-visible:0;
        float tab=(right-88-(visible-1)*4)/visible;
        lut_place(0,50,40,38);if(nk_button_label(ctx,"‹")){lut_tab_start-=visible;if(lut_tab_start<0)lut_tab_start=0;dirty=1;}
        for(int slot=0;slot<visible;slot++) {
            int j=lut_tab_start+slot;
            lut_place(44+slot*(tab+4),50,tab,38);
            if(lut_button(lut_categories[groups[j]],lut_category==groups[j]))lut_set_category(groups[j]);
        }
        lut_place(right-40,50,40,38);if(nk_button_label(ctx,"›")){lut_tab_start+=visible;if(lut_tab_start>ng-visible)lut_tab_start=ng-visible;dirty=1;}
        lut_place(0,100,308,180);nk_image(ctx,nk_image_ptr(&lut_reference));
        lut_place(0,284,308,46);nk_label_wrap(ctx,selected?recipes[selected].label:"LUT 已关闭 · 原厂拍摄");
        lut_place(0,336,88,44);if(nk_button_label(ctx,"上一款"))lut_step(-1);
        lut_place(94,336,120,44);
        if(lut_button(lut_list_open?"参数微调":"风格列表",lut_list_open))lut_toggle_view();
        lut_place(220,336,88,44);if(nk_button_label(ctx,"下一款"))lut_step(1);
        lut_place(0,388,150,44);
        if(nk_button_label(ctx,selected?(lut_favorite[selected]?"已收藏":"收藏"):"原厂拍摄"))lut_toggle_favorite();
        lut_place(158,388,150,44);
        if(nk_button_label(ctx,"重置当前")&&!child&&selected){memset(&lut_tune,0,sizeof lut_tune);lut_strength=100;lut_draft=1;lut_reference_dirty=dirty=1;}
        if(lut_list_open){
            int options[NXUI_MAX_RECIPES],total=0;for(int i=1;i<count;i++)if(lut_in_group(i))options[total++]=i;
            if(lut_list_cursor>=total)lut_list_cursor=total?total-1:0;
            int first=lut_list_cursor-3;if(first<0)first=0;if(first>total-7)first=total>7?total-7:0;
            for(int j=0;j<7&&first+j<total;j++){
                int index=options[first+j];snprintf(line,sizeof line,"%s%s",lut_favorite[index]?"★ ":"",recipes[index].label);
                lut_place(338,100+j*48,right-338,44);
                if(lut_button(line,first+j==lut_list_cursor)){lut_list_cursor=first+j;choose(index);confirm_lut();dirty=1;}
            }
            if(!total){lut_place(338,100,right-338,50);nk_label(ctx,"暂无收藏",NK_TEXT_LEFT);}
        }else{
            for(int i=0;i<8;i++)lut_slider_row(i,338,94+i*44,right-338);
        }
        lut_place(338,448,right-338,22);
        nk_label_colored(ctx,lut_submit_failed?status:child?"正在提交，完成后可继续选择":lut_list_open?"滚轮选风格  OK确认  Fn微调  MENU返回":"上下选项  滚轮调整  Fn保存返回",NK_TEXT_LEFT,nk_rgb(158,171,188));
        static const char *scenes[]={"人像","风景","夜景"};
        snprintf(line,sizeof line,"%s · %s  点按换图／长按原图",lut_reference_original?"原图":"参考预览",scenes[lut_reference_scene]);
        lut_place(0,436,308,32);nk_label_wrap(ctx,line);
        nk_layout_space_end(ctx);
        if(lut_leave_dialog||child)nk_widget_disable_end(ctx);
    }
    nk_end(ctx);
    if(lut_leave_dialog){
        if(nk_begin(ctx,"LUT-save-question",nk_rect(110,148,580,196),NK_WINDOW_NO_SCROLLBAR)){
            nk_layout_row_dynamic(ctx,38,1);nk_label(ctx,"保存当前 LUT 调整？",NK_TEXT_LEFT);
            nk_layout_row_dynamic(ctx,38,1);nk_label(ctx,"保存会提交设置，硬件效果等待原厂更新。",NK_TEXT_LEFT);
            nk_layout_row_dynamic(ctx,50,3);
            const char *choices[]={"保存并退出","放弃修改","继续编辑"};
            for(int i=0;i<3;i++)if(lut_button(choices[i],lut_leave_option==i)){lut_leave_pick(i);break;}
        }nk_end(ctx);
    }
    ctx->style.button=saved_button;nk_xlib_render(window,nk_rgb(23,24,26));XFlush(display);
}
