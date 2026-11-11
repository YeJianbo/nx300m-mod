/* Input-driven controls and a static reference image. */
#include "assets/lut_reference_rgb.h"
static uint32_t lut_reference_pixels[NX_REF_W*NX_REF_H];
static struct nx_cpu_image lut_reference={NX_REF_W,NX_REF_H,lut_reference_pixels,0};
static uint16_t lut_reference_table[2190];
static uint16_t lut_reference_tuned[2190];
static struct nx_lut_tune lut_reference_cached_tune;
static int lut_reference_tuned_index=-1;
static unsigned lut_reference_position[256],lut_reference_fraction[256];
static int lut_reference_axes_ready;
static int lut_reference_index=-1,lut_reference_valid;
static int lut_reference_mono,lut_nav_dirty;
/* Full-strength LUT colour before the strength mix. Strength dialing and
 * original-image comparison reuse this shared buffer; a zero-strength view
 * defers interpolation until the effect is needed again. */
static unsigned char lut_reference_unmixed[NX_REF_W*NX_REF_H*3];
static int lut_reference_unmixed_dirty=1;
static int lut_filtered[NXUI_MAX_RECIPES],lut_filtered_count,lut_filtered_category=-1;
/* H5: expected favorite state while a write is published but its directory
 * durability is unconfirmed; re-tapping the same look retries the same
 * value instead of toggling the live state back. The visible-feedback flag
 * lut_favorite_notice lives in nxui.c (defined before this header) so the
 * leave/switch paths can clear it; it stays distinct from lut_submit_failed. */
static char lut_favorite_pending_key[64];
static int lut_favorite_pending_add=-1;
static int lut_field_supported(int field) {return selected&&!(field==2&&lut_reference_valid&&lut_reference_mono);}
static int lut_custom_group_index(const char *group_id) {
    for(int i=0;i<lut_custom_group_count;i++)if(!strcmp(lut_custom_group_id[i],group_id))return i;
    return -1;
}
static const char *lut_category_label(int group) {
    if(group<NX_LUT_CUSTOM_GROUP_BASE)return lut_categories[group];
    int index=group-NX_LUT_CUSTOM_GROUP_BASE;
    return index>=0&&index<lut_custom_group_count?lut_custom_group_label[index]:"";
}
static int lut_category_available(int group) {
    if(group<0||group>=NX_LUT_CATEGORY_COUNT)return 0;
    if(group<=1)return 1;
    return lut_category_count[group]>0;
}
static void lut_save_navigation(void) {
    if(!lut_nav_dirty)return;
    char value[64];
    if(lut_category<NX_LUT_CUSTOM_GROUP_BASE)snprintf(value,sizeof value,"%d\n",lut_category);
    else if(lut_category>=NX_LUT_CUSTOM_GROUP_BASE&&
            lut_category<NX_LUT_CUSTOM_GROUP_BASE+lut_custom_group_count)
        snprintf(value,sizeof value,"%s\n",lut_custom_group_id[lut_category-NX_LUT_CUSTOM_GROUP_BASE]);
    else return;
    if(nx_ui_pref_write("lut-category",value))lut_nav_dirty=0;
}
static void lut_request_leave(int target) {
    /* Exit intent follows the request snapshot that owns the transaction.
     * With a queued or in-flight LUT request, a MENU leave (target 1/2) also
     * schedules the remaining per-look drafts: after the current task
     * completes they are flushed, and only full success honors the exit —
     * a failure keeps the panel open with the drafts. Fn (target 0) keeps
     * its narrower scope: save the current look only and stay. */
    if(child){if(busy_kind==1){lut_exit_after_save=target;if(lut_request_inflight_valid)lut_request_inflight.exit_after=target;
        if(target&&lut_look_draft_count)lut_leave_flush_after=target;
        fprintf(stderr,"LUT_LEAVE_BUSY stage=%s target=%d flush_after=%d drafts=%d\n",
            "inflight",target,lut_leave_flush_after,lut_look_draft_count);}return;}
    if(queue.commit_requested){lut_exit_after_save=target;if(lut_request_pending_valid)lut_request_pending.exit_after=target;
        if(target&&lut_look_draft_count)lut_leave_flush_after=target;
        fprintf(stderr,"LUT_LEAVE_BUSY stage=%s target=%d flush_after=%d drafts=%d\n",
            "queued",target,lut_leave_flush_after,lut_look_draft_count);return;}
    if(queue.close_requested)return;
    if(lut_has_changes()){lut_leave_dialog=1;lut_leave_target=target;lut_leave_option=2;dirty=1;return;}
    lut_save_navigation();
    if(target==2)mfw_return_requested=dirty=1;else queue.close_requested=1;
}
static void lut_leave_pick(int option) {
    if(option==0){
        /* Save scope is explicit: every stashed draft goes to the card, and
         * the displayed choice is committed to the camera. */
        if(!lut_draft_stash()||lut_drafts_flush()) {
            lut_submit_failed=1;dirty=1;
            fprintf(stderr,"LUT_LEAVE_FLUSH_FAILED remaining=%d\n",lut_look_draft_count);
            message("部分调整保存未确认 · 请重试或继续编辑");
            return;
        }
        lut_leave_dialog=0;
        confirm_lut(lut_leave_target);
    }
    else if(option==1){
        /* Discard scope is explicit: all drafts of all looks; the displayed
         * look reloads its persisted values. */
        fprintf(stderr,"LUT_DRAFT_DISCARD_ALL count=%d\n",lut_look_draft_count);
        lut_leave_dialog=0;lut_look_draft_count=0;
        lut_load_tune();lut_baseline_load(selected);lut_draft=0;
        lut_save_navigation();
        if(lut_leave_target==2)mfw_return_requested=dirty=1;else queue.close_requested=1;
    }
    else {lut_leave_dialog=0;dirty=1;}
}
static void lut_toggle_view(void) {
    if(child||queue.commit_requested||lut_leave_dialog)return;
    lut_category_focus=0;lut_toolbar_focus=-1;
    /* Fn leaving the tuning view saves the displayed look only and stays in
     * the panel; other looks' drafts stay untouched in their own slots. */
    if(!lut_list_open){
        if(lut_current_dirty()){
            if(!lut_draft_stash())return;
            int slot=lut_draft_slot(recipes[selected].key);
            if(slot>=0&&!lut_draft_persist_one(&lut_look_drafts[slot])){
                lut_submit_failed=1;dirty=1;message("当前调整保存未确认 · 请重试");return;
            }
            lut_draft_drop(recipes[selected].key);lut_baseline_load(selected);
            lut_draft=0;lut_submit_failed=0;lut_saved_config=1;
            fprintf(stderr,"LUT_PRESET_SAVED key=%s applied=0\n",recipes[selected].key);
        }
        lut_list_open=1;lut_edit=0;
    }
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
    /* These ids are imported from the film-stock recipe library. Keep them
     * together instead of classifying Superia/Velvia as camera picture styles. */
    if(!strncmp(key,"metol-",6)||strstr(key,"kodak")||strstr(key,"ilford")||
       strstr(label,"柯达")||strstr(label,"伊尔福")||strstr(label,"CineStill"))return 9;
    if(strstr(key,"fuji")||strstr(label,"富士"))return 2;
    if(strstr(key,"olympus")||strstr(label,"奥巴")||strstr(label,"奥林巴斯"))return 3;
    if(strstr(key,"pentax")||strstr(label,"宾得"))return 4;
    if(strstr(key,"panasonic")||strstr(label,"松下")||strstr(label,"Pana"))return 5;
    if(strstr(key,"ricoh")||strstr(label,"理光")||strstr(label,"GR "))return 6;
    if(strstr(key,"sony")||strstr(label,"索尼"))return 7;
    return 8;
}
static void lut_load_custom_groups(void) {
    lut_custom_group_count=0;
    memset(lut_custom_group_mask,0,sizeof lut_custom_group_mask);
    memset(lut_custom_group_id,0,sizeof lut_custom_group_id);
    memset(lut_custom_group_label,0,sizeof lut_custom_group_label);
    char file[NXUI_PATH],line[256];
    snprintf(file,sizeof file,"%s/.state/lut-groups.txt",root);
    FILE *in=fopen(file,"r");
    if(!in)return;
    while(fgets(line,sizeof line,in)) {
        line[strcspn(line,"\r\n")]=0;
        if(line[0]=='G'&&line[1]=='|') {
            char *group_id=line+2,*label=strchr(group_id,'|');
            if(!label||lut_custom_group_count>=NX_LUT_CUSTOM_GROUP_MAX)continue;
            *label++=0;
            if(strncmp(group_id,"group-",6)||strlen(group_id)!=18||!*label||lut_custom_group_index(group_id)>=0)continue;
            int index=lut_custom_group_count++;
            snprintf(lut_custom_group_id[index],sizeof lut_custom_group_id[index],"%s",group_id);
            snprintf(lut_custom_group_label[index],sizeof lut_custom_group_label[index],"%s",label);
        }
        else if(line[0]=='L'&&line[1]=='|') {
            char *look_id=line+2,*groups=strchr(look_id,'|');
            if(!groups)continue;
            *groups++=0;
            int look=-1;
            for(int i=1;i<count;i++)if(!strcmp(recipes[i].key,look_id)){look=i;break;}
            if(look<0)continue;
            char *save=NULL;
            for(char *group_id=strtok_r(groups,"|",&save);group_id;group_id=strtok_r(NULL,"|",&save)) {
                int group=lut_custom_group_index(group_id);
                if(group>=0)lut_custom_group_mask[look]|=1U<<group;
            }
        }
    }
    fclose(in);
    fprintf(stderr,"LUT_CUSTOM_GROUPS count=%d\n",lut_custom_group_count);
}
static void lut_init_groups(void) {
    memset(lut_category_count,0,sizeof lut_category_count);
    memset(lut_favorite,0,sizeof lut_favorite);
    lut_load_custom_groups();
    char file[NXUI_PATH],key[64];snprintf(file,sizeof file,"%s/.state/lut-favorites",root);
    FILE *in=fopen(file,"r");
    if(in) {
        while(fscanf(in,"%63s",key)==1)for(int i=1;i<count;i++)if(!strcmp(key,recipes[i].key)){lut_favorite[i]=1;break;}
        fclose(in);
    }
    for(int i=0;i<count;i++) {
        lut_brand[i]=i?lut_detect_brand(i):0;
        /* Explicit card groups own classified looks; name-based categories
         * only classify looks without an assigned group. */
        if(i&&lut_custom_group_mask[i])for(int group=0;group<lut_custom_group_count;group++)
            if(lut_custom_group_mask[i]&(1U<<group)){lut_brand[i]=NX_LUT_CUSTOM_GROUP_BASE+group;break;}
        lut_category_count[0]++;
        if(i&&lut_brand[i]<NX_LUT_CUSTOM_GROUP_BASE)lut_category_count[lut_brand[i]]++;
        if(lut_favorite[i])lut_category_count[1]++;
        if(i)for(int group=0;group<lut_custom_group_count;group++)
            if(lut_custom_group_mask[i]&(1U<<group))lut_category_count[NX_LUT_CUSTOM_GROUP_BASE+group]++;
    }
    lut_filtered_category=-1;
}
static int lut_item_in_category(int i,int category) {
    if(i<=0)return 0;
    if(category==0)return 1;
    if(category==1)return lut_favorite[i];
    if(category<NX_LUT_CUSTOM_GROUP_BASE)return lut_brand[i]==category;
    int group=category-NX_LUT_CUSTOM_GROUP_BASE;
    return group>=0&&group<lut_custom_group_count&&(lut_custom_group_mask[i]&(1U<<group));
}
static int lut_in_group(int i) {return lut_item_in_category(i,lut_category);}
static void lut_ensure_filtered(void) {
    if(lut_filtered_category==lut_category)return;
    lut_filtered_count=0;
    for(int i=1;i<count;i++)if(lut_in_group(i))lut_filtered[lut_filtered_count++]=i;
    lut_filtered_category=lut_category;
}
static void lut_step(int delta) {
    if(child||queue.commit_requested||queue.close_requested)return;
    int next=selected;
    for(int i=0;i<count;i++) {
        next=(next+delta+count)%count;
        if(lut_in_group(next)){choose(next);return;}
    }
}
static void lut_set_category(int group) {
    if(child||queue.commit_requested||queue.close_requested)return;
    if(group!=lut_category&&!lut_draft_stash())return;
    lut_category=group;lut_category_focus=0;lut_toolbar_focus=-1;lut_edit=0;lut_tab_start=-1;lut_nav_dirty=dirty=1;
    /* Changing a group is local navigation, not a confirmed LUT request. */
    if(!lut_in_group(selected))for(int i=group>=2?1:0;i<count;i++)if(lut_in_group(i)) {
        choose(i);break;
    }
    lut_list_cursor=0;for(int i=1;i<selected;i++)if(lut_in_group(i))lut_list_cursor++;
}
static void lut_next_category(int delta) {
    int next=lut_category;
    for(int i=0;i<NX_LUT_CATEGORY_COUNT;i++) {
        next=(next+delta+NX_LUT_CATEGORY_COUNT)%NX_LUT_CATEGORY_COUNT;
        if(lut_category_available(next)){lut_set_category(next);lut_category_focus=1;fprintf(stderr,"LUT_GROUP focus=1 group=%d\n",lut_category);return;}
    }
}
static void lut_adjust(int delta) {
    if(child||queue.commit_requested||queue.close_requested||!lut_field_supported(lut_field))return;
    if(lut_field==7) {change_lut_strength(5*delta);return;}
    int next=lut_tune.value[lut_field]+delta;
    if(next<-10||next>10)return;
    lut_tune.value[lut_field]=next;lut_draft=1;lut_reference_dirty=dirty=1;
}
static void lut_toolbar_activate(int index) {
    if(child||queue.commit_requested||queue.close_requested)return;
    fprintf(stderr,"LUT_TOOLBAR action=%d\n",index);
    if(index==0){choose(0);confirm_lut(1);}
    else if(index==1&&live){
        if(nx_preview_state(root)==NX_PREVIEW_BUSY){message("正在恢复预览 · 请稍候");dirty=1;return;}
        /* Submit the displayed intent, never a backend toggle whose meaning
         * can reverse when the boot worker finishes between draw and click.
         * With the LUT off the toggle only records the preference. */
        const char *action=nx_preview_action(lut_preview_shown,nx_preview_lut_applied(root));
        if(!start_mfw_action(action))message("预览操作未启动 · 请重试");
    }
    else if(index==2)lut_request_leave(mfw_menu_root?2:1);
    else if(index==3)confirm_lut(1);
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
    if(lut_toolbar_focus>=0){
        if(left||right)lut_toolbar_focus=(lut_toolbar_focus+(right?1:3))%4;
        if(down){lut_toolbar_focus=-1;lut_category_focus=1;}
        if(ok&&lut_toolbar_focus>=0)lut_toolbar_activate(lut_toolbar_focus);
        if(key==XK_Menu||key==XK_Escape)lut_request_leave(mfw_menu_root?2:1);
        dirty=1;return;
    }
    if(lut_category_focus){
        if(left||right)lut_next_category(right?1:-1);
        if(up){lut_category_focus=0;lut_toolbar_focus=1;}
        if(down||ok){lut_category_focus=0;fprintf(stderr,"LUT_GROUP focus=0 group=%d\n",lut_category);}
        if(key==XK_Menu||key==XK_Escape)lut_request_leave(mfw_menu_root?2:1);
        dirty=1;return;
    }
    if(lut_list_open){
        lut_ensure_filtered();int n=lut_filtered_count;
        if(up&&lut_list_cursor==0){lut_category_focus=1;fprintf(stderr,"LUT_GROUP focus=1 group=%d\n",lut_category);}
        else if(n&&(left||right||up||down)){lut_list_cursor=(lut_list_cursor+(right||down?1:n-1))%n;choose(lut_filtered[lut_list_cursor]);}
        if(ok&&n){choose(lut_filtered[lut_list_cursor%n]);confirm_lut(1);}
        if(key==XK_Menu||key==XK_Escape)lut_request_leave(mfw_menu_root?2:1);
        dirty=1;return;
    }
    if(key==XK_space){lut_reference_scene=(lut_reference_scene+1)%NX_REF_COUNT;lut_reference_unmixed_dirty=1;lut_reference_dirty=dirty=1;return;}
    /* Fn owns view changes. Reference-image taps and the top parameter must
     * never silently turn a tuning-wheel event into selection of another LUT. */
    if(!selected)return;
    lut_edit=1;
    if(left||right)lut_adjust(right?1:-1);
    if(down){do{lut_field=(lut_field+1)%8;}while(!lut_field_supported(lut_field));}
    if(up){do{lut_field=(lut_field+7)%8;}while(!lut_field_supported(lut_field));}
    if(ok)confirm_lut(1);
    dirty=1;
}
static void lut_toggle_favorite(void) {
    if(!live||!selected||child||queue.commit_requested||queue.close_requested)return;
    char directory[NXUI_PATH];struct stat64 st;
    snprintf(directory,sizeof directory,"%s/.state",root);
    if((mkdir(directory,0700)&&errno!=EEXIST)||__lxstat64(3,directory,&st)||!S_ISDIR(st.st_mode)){
        lut_favorite_notice=1;fprintf(stderr,"LUT_FAVORITES state directory unavailable dir=%s\n",directory);
        message("收藏未保存");return;}
    /* Build the candidate list first: memory state flips only after the card
     * accepted it, so a failed or truncated write keeps the old favorites.
     * An unconfirmed write keeps its expected state pending: re-tapping the
     * same look retries that value rather than reversing it. */
    static char text[NXUI_MAX_RECIPES*66];
    int pending_here=lut_favorite_pending_add>=0&&!strcmp(recipes[selected].key,lut_favorite_pending_key);
    size_t length=0;int overflow=0,adding=pending_here?lut_favorite_pending_add:!lut_favorite[selected];
    for(int i=1;i<count&&!overflow;i++) {
        int include=i==selected?adding:lut_favorite[i];
        if(!include)continue;
        int n=snprintf(text+length,sizeof text-length,"%s\n",recipes[i].key);
        if(n<0||n>=(int)(sizeof text-length))overflow=1;
        else length+=(size_t)n;
    }
    if(overflow){message("收藏列表过大 · 未保存");return;}
    int saved=nx_atomic_text_write(directory,"lut-favorites",text,length);
    if(!saved){lut_favorite_notice=1;fprintf(stderr,"LUT_FAVORITES card write failed dir=%s\n",directory);message("收藏未保存，请重试");return;}
    if(saved<0) {
        snprintf(lut_favorite_pending_key,sizeof lut_favorite_pending_key,"%s",recipes[selected].key);
        lut_favorite_pending_add=adding;lut_favorite_notice=1;
        fprintf(stderr,"LUT_FAVORITES durability unconfirmed key=%s add=%d\n",recipes[selected].key,adding);
        message("收藏已写入，耐久未确认 · 再点一次确认");
    }
    else {lut_favorite_pending_key[0]=0;lut_favorite_pending_add=-1;lut_favorite_notice=0;}
    lut_favorite[selected]=adding;
    lut_init_groups();dirty=1;
}
static void lut_update_reference(void) {
    if(!lut_reference_dirty)return;
    lut_reference_dirty=0;
    if(lut_reference_index!=selected) {
        lut_reference_index=selected;lut_reference_valid=0;
        lut_reference_mono=0;
        lut_reference_unmixed_dirty=1;
        if(selected) {
            char file[NXUI_PATH];snprintf(file,sizeof file,"%s/looks/%s/table.d4lut",root,recipes[selected].key);
            FILE *in=fopen(file,"rb");
            if(in){lut_reference_valid=fread(lut_reference_table,1,4380,in)==4380 && fgetc(in)==EOF && !ferror(in);fclose(in);}
            if(lut_reference_valid)for(unsigned i=0;i<2190;i++)if(lut_reference_table[i]>1023||(i%730==729&&lut_reference_table[i]))lut_reference_valid=0;
            if(lut_reference_valid){lut_reference_mono=1;for(unsigned i=0;i<729;i++)if(lut_reference_table[i]!=lut_reference_table[730+i]||lut_reference_table[i]!=lut_reference_table[1460+i]){lut_reference_mono=0;break;}}
        }
    }
    if(lut_reference_valid&&(lut_reference_tuned_index!=selected||
       memcmp(&lut_reference_cached_tune,&lut_tune,sizeof lut_tune))) {
        nx_tune_table(lut_reference_table,lut_reference_tuned,&lut_tune);
        lut_reference_cached_tune=lut_tune;lut_reference_tuned_index=selected;
        lut_reference_unmixed_dirty=1;
    }
    if(!lut_reference_axes_ready) {
        for(unsigned i=0;i<256;i++) {
            unsigned n=i*8,pos=n/255;
            lut_reference_position[i]=pos==8?7:pos;
            lut_reference_fraction[i]=pos==8?255:n%255;
        }
        lut_reference_axes_ready=1;
    }
    if(lut_reference_valid&&!lut_reference_original&&lut_strength&&lut_reference_unmixed_dirty) {
        for(unsigned i=0;i<NX_REF_W*NX_REF_H;i++) {
            const unsigned char *src=nx_reference_rgb[lut_reference_scene]+3*i;
            unsigned pos[3],f[3];
            for(int c=0;c<3;c++){pos[c]=lut_reference_position[src[c]];f[c]=lut_reference_fraction[src[c]];}
            unsigned index=pos[0]*81+pos[1]*9+pos[2];
            for(int c=0;c<3;c++) {
                unsigned v=nx_ref_interpolate(lut_reference_tuned+c*730+index,f[0],f[1],f[2]);
                lut_reference_unmixed[3*i+c]=(unsigned char)((v*255+511)/1023);
            }
        }
        lut_reference_unmixed_dirty=0;
    }
    for(unsigned i=0;i<NX_REF_W*NX_REF_H;i++) {
        const unsigned char *src=nx_reference_rgb[lut_reference_scene]+3*i;
        unsigned output[3]={src[0],src[1],src[2]};
        if(lut_reference_valid&&!lut_reference_original&&lut_strength) {
            const unsigned char *unmixed=lut_reference_unmixed+3*i;
            for(int c=0;c<3;c++) {
                unsigned color=unmixed[c];
                /* strength 100 is the default and divides out exactly; keep
                 * the general formula for every other value unchanged. */
                output[c]=lut_strength==100?color:
                    (color*lut_strength+src[c]*(100-lut_strength)+50)/100;
            }
        }
        lut_reference_pixels[i]=0xff000000U|(output[0]<<16)|(output[1]<<8)|output[2];
    }
    lut_reference.revision++;
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
static int lut_toolbar_button(const char *label,int index,int active) {
    struct nk_style_button saved=ctx->style.button;
    if(lut_toolbar_focus==index){ctx->style.button.border=3;ctx->style.button.border_color=nk_rgb(191,224,255);}
    int clicked=lut_button(label,active);
    ctx->style.button=saved;
    if(clicked){lut_toolbar_focus=index;lut_category_focus=0;dirty=1;}
    return clicked;
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
    lut_place(x+124,y, w-210,42);
    nk_widget(&bounds,ctx);
    if(supported&&!lut_leave_dialog&&!child&&!queue.commit_requested&&!queue.close_requested&&nk_input_is_mouse_down(&ctx->input,NK_BUTTON_LEFT)&&
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
    lut_place(x+w-82,y,74,42);nk_label_colored(ctx,supported?number:"—",NK_TEXT_RIGHT,nk_rgb(225,232,241));
}
static void draw_lut_panel(void) {
    /* Keep a stable cover while the engine installs and synchronizes the new
     * choice. Do not expose the old look or rebuild the reference each tick. */
    if(child || queue.commit_requested) {
        if(nk_begin(ctx,"LUT-applying",nk_rect(0,0,(float)width,(float)height),NK_WINDOW_NO_SCROLLBAR)) {
            const char *dots[]={"·", "·  ·", "·  ·  ·", "·  ·"};
            nk_layout_row_dynamic(ctx,138,1);nk_spacing(ctx,1);
            nk_layout_row_dynamic(ctx,48,1);nk_label_colored(ctx,"正在应用",NK_TEXT_CENTERED,nk_rgb(226,235,247));
            nk_layout_row_dynamic(ctx,42,1);nk_label(ctx,selected?recipes[selected].label:"关闭 LUT",NK_TEXT_CENTERED);
            nk_layout_row_dynamic(ctx,40,1);nk_label_colored(ctx,dots[(now_ms()/250)%4],NK_TEXT_CENTERED,nk_rgb(76,154,232));
        }
        nk_end(ctx);nk_xlib_render(window,nk_rgb(23,24,26));XFlush(display);return;
    }
    lut_update_reference();char line[256];
    struct nk_style_button saved_button=ctx->style.button;
    ctx->style.button.rounding=7;ctx->style.button.border=0;
    if(nk_begin(ctx,"NXWA",nk_rect(0,0,(float)width,(float)height),NK_WINDOW_NO_SCROLLBAR)) {
        if(lut_leave_dialog||child)nk_widget_disable_begin(ctx);
        nk_layout_space_begin(ctx,NK_STATIC,(float)height-18,100);
        float right=(float)width-24;
        lut_place(4,0,100,42);nk_label(ctx,"LUT",NK_TEXT_LEFT);
        lut_place(108,0,132,42);
        struct nk_style_button danger=ctx->style.button;
        ctx->style.button.normal=ctx->style.button.hover=nk_style_item_color(nk_rgb(116,48,48));
        if(lut_toolbar_button(selected?"关闭 LUT":"LUT 已关",0,0))lut_toolbar_activate(0);
        ctx->style.button=danger;
        int preview_observed=nx_preview_state(root);
        if(preview_observed!=lut_preview_shown)fprintf(stderr,"LUT_PREVIEW_STATE state=%d label=%s\n",preview_observed,nx_preview_label(preview_observed));
        lut_preview_shown=preview_observed;
        if(live&&(lut_preview_shown==NX_PREVIEW_WAIT||lut_preview_shown==NX_PREVIEW_BUSY)&&!lut_preview_until){
            lut_preview_until=now_ms()+60000;lut_preview_due=now_ms()+500;
        }
        snprintf(line,sizeof line,"%s",nx_preview_label(lut_preview_shown));
        lut_place(248,0,130,42);if(lut_toolbar_button(line,1,0))lut_toolbar_activate(1);
        lut_place(390,0,140,42);nk_label_colored(ctx,lut_submit_failed?"保存失败":lut_has_changes()?"已修改":!lut_matches_applied()?"待应用":lut_saved_config?"已保存":"当前会话",NK_TEXT_LEFT,nk_rgb(140,180,221));
        lut_place(right-240,0,112,42);
        if(lut_toolbar_button("返回",2,0))lut_toolbar_activate(2);
        lut_place(right-120,0,120,42);
        if(lut_toolbar_button("保存",3,1))lut_toolbar_activate(3);
        int groups[NX_LUT_CATEGORY_COUNT],ng=0;
        for(int g=0;g<NX_LUT_CATEGORY_COUNT;g++)if(lut_category_available(g))groups[ng++]=g;
        int visible=ng<6?ng:6,category_position=0;for(int j=0;j<ng;j++)if(groups[j]==lut_category)category_position=j;
        if(lut_tab_start<0)lut_tab_start=category_position>=visible?ng-visible:0;
        float tab=(right-88-(visible-1)*4)/visible;
        lut_place(0,50,40,38);if(nk_button_label(ctx,"‹")){lut_tab_start-=visible;if(lut_tab_start<0)lut_tab_start=0;dirty=1;}
        for(int slot=0;slot<visible;slot++) {
            int j=lut_tab_start+slot;
            lut_place(44+slot*(tab+4),50,tab,38);
            struct nk_style_button category_button=ctx->style.button;
            if(lut_category_focus&&lut_category==groups[j]) {
                ctx->style.button.border=3;
                ctx->style.button.border_color=nk_rgb(191,224,255);
            }
            if(lut_button(lut_category_label(groups[j]),lut_category==groups[j]))lut_set_category(groups[j]);
            ctx->style.button=category_button;
        }
        lut_place(right-40,50,40,38);if(nk_button_label(ctx,"›")){lut_tab_start+=visible;if(lut_tab_start>ng-visible)lut_tab_start=ng-visible;dirty=1;}
        lut_place(0,100,308,180);
        nk_image(ctx,nk_image_ptr(&lut_reference));
        lut_place(0,284,308,46);nk_label_wrap(ctx,selected?recipes[selected].label:"LUT 已关闭 · 原厂拍摄");
        lut_place(0,336,100,44);if(nk_button_label(ctx,"上一款"))lut_step(-1);
        lut_place(104,336,100,44);
        if(lut_button(lut_list_open?"微调":"列表",lut_list_open))lut_toggle_view();
        lut_place(208,336,100,44);if(nk_button_label(ctx,"下一款"))lut_step(1);
        lut_place(0,388,150,44);
        if(nk_button_label(ctx,selected?(lut_favorite[selected]?"已收藏":"收藏"):"原厂拍摄"))lut_toggle_favorite();
        lut_place(158,388,150,44);
        if(nk_button_label(ctx,"重置当前")&&!child&&!queue.commit_requested&&selected){memset(&lut_tune,0,sizeof lut_tune);lut_strength=100;lut_draft=1;lut_reference_dirty=dirty=1;}
        if(lut_list_open){
            lut_ensure_filtered();int total=lut_filtered_count;
            if(lut_list_cursor>=total)lut_list_cursor=total?total-1:0;
            int first=lut_list_cursor-3;if(first<0)first=0;if(first>total-7)first=total>7?total-7:0;
            for(int j=0;j<7&&first+j<total;j++){
                int index=lut_filtered[first+j];snprintf(line,sizeof line,"%s%s",lut_favorite[index]?"★ ":"",recipes[index].label);
                lut_place(338,100+j*48,right-338,44);
                if(lut_button(line,selected==index)){lut_toolbar_focus=-1;lut_category_focus=0;lut_list_cursor=first+j;choose(index);dirty=1;}
            }
            if(!total){lut_place(338,100,right-338,50);nk_label(ctx,"暂无收藏",NK_TEXT_LEFT);}
        }else{
            for(int i=0;i<8;i++)lut_slider_row(i,338,94+i*44,right-338);
        }
        const struct nk_user_font *main_font=ctx->style.font;
        nk_style_set_font(ctx,panel_hint_font());
        lut_place(338,444,right-338,30);
        /* Favorite feedback uses its own notice flag so a card-write problem
         * is visible without being misread as a LUT submission failure. */
        nk_label_colored(ctx,(lut_submit_failed||lut_favorite_notice)?status:lut_toolbar_focus>=0?"左右／转盘选择  OK操作  ↓分组":lut_category_focus?"左右切组  ↑开关  ↓回列表":lut_list_open?(lut_list_cursor?"↑↓选风格  OK应用  Fn微调":"↑选分组  再↑开关  Fn微调"):"↑↓选项  转盘调整  Fn保存回列表",NK_TEXT_LEFT,nk_rgb(158,171,188));
        static const char *scenes[]={"人像","街景","风景"};
        snprintf(line,sizeof line,"%s·%s  点按切图／按住原图",lut_reference_original?"原图":"参考",scenes[lut_reference_scene]);
        lut_place(0,436,308,32);nk_label_wrap(ctx,line);
        nk_style_set_font(ctx,main_font);
        nk_layout_space_end(ctx);
        if(lut_leave_dialog||child)nk_widget_disable_end(ctx);
    }
    nk_end(ctx);
    if(lut_leave_dialog){
        if(nk_begin(ctx,"LUT-save-question",nk_rect(110,148,580,196),NK_WINDOW_NO_SCROLLBAR)){
            nk_layout_row_dynamic(ctx,38,1);nk_label(ctx,"保存当前 LUT 调整？",NK_TEXT_LEFT);
            /* H5: while the dialog covers the panel footer, surface the save
             * failure reason here instead of only in the hidden status line. */
            nk_layout_row_dynamic(ctx,38,1);
            nk_label(ctx,lut_submit_failed&&*status?status:"保存会把全部未存调整写入卡并应用当前选择，硬件效果等待原厂更新。",NK_TEXT_LEFT);
            nk_layout_row_dynamic(ctx,50,3);
            const char *choices[]={"保存并退出","放弃修改","继续编辑"};
            for(int i=0;i<3;i++)if(lut_button(choices[i],lut_leave_option==i)){lut_leave_pick(i);break;}
        }nk_end(ctx);
    }
    ctx->style.button=saved_button;nk_xlib_render(window,nk_rgb(23,24,26));XFlush(display);
}
