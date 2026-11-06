static void draw_feature_page(void) {
    struct nk_style_button saved=ctx->style.button;
    ctx->style.button.rounding=7;ctx->style.button.border=0;ctx->style.button.text_alignment=NK_TEXT_LEFT;
    static const char *titles[]={"","","","镜头管理","曝光增强","系统设置","选择镜头","手机连接","模块状态","镜头信息"};
    if(nk_begin(ctx,"NXWA",nk_rect(0,0,(float)width,(float)height),NK_WINDOW_NO_SCROLLBAR)){
        nk_layout_space_begin(ctx,NK_STATIC,(float)height-18,40);
        lut_place(4,0,560,42);nk_label(ctx,titles[mfw_preview],NK_TEXT_LEFT);
        ctx->style.button.text_alignment=NK_TEXT_CENTERED;
        lut_place((float)width-136,0,112,42);if(nk_button_label(ctx,"返回"))activate_mfw_item(-1);
        ctx->style.button.text_alignment=NK_TEXT_LEFT;
        if(mfw_preview==7||mfw_preview==8){
            lut_place(4,64,(float)width-36,316);nk_label_wrap(ctx,*mfw_status?mfw_status:child?"正在读取…":"尚未读取");
            lut_place(4,390,180,48);if(lut_button("刷新状态",mfw_selected==0))activate_mfw_item(0);
        }else{
            int first=mfw_selected-3;if(first<0)first=0;if(first>item_count-7)first=item_count>7?item_count-7:0;
            for(int row=0;row<7&&first+row<item_count;row++){
                int index=first+row;lut_place(0,58+row*48,(float)width-24,44);
                struct nk_rect bounds=nk_widget_bounds(ctx);
                if(feature_disabled[index])nk_widget_disable_begin(ctx);
                int clicked=lut_button(items[index].label,index==mfw_selected);
                if(feature_disabled[index])nk_widget_disable_end(ctx);
                struct nk_rect value=nk_rect(bounds.x+330,bounds.y+(bounds.h-ctx->style.font->height)/2,bounds.w-346,ctx->style.font->height);
                nk_draw_text(nk_window_get_canvas(ctx),value,feature_values[index],(int)strlen(feature_values[index]),
                    ctx->style.font,nk_rgba(0,0,0,0),feature_disabled[index]?nk_rgb(128,137,148):nk_rgb(207,221,237));
                if(clicked&&!feature_disabled[index]){mfw_selected=index;activate_mfw_item(index);break;}
            }
        }
        const struct nk_user_font *main_font=ctx->style.font;
        nk_style_set_font(ctx,panel_hint_font());
        lut_place(4,440,(float)width-32,30);nk_label_colored(ctx,*status?status:"上下选择    OK 确认    MENU 返回",NK_TEXT_LEFT,nk_rgb(158,171,188));
        nk_style_set_font(ctx,main_font);
        nk_layout_space_end(ctx);
    }nk_end(ctx);ctx->style.button=saved;nk_xlib_render(window,nk_rgb(23,24,26));XFlush(display);
}
