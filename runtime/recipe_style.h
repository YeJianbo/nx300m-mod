/* One configuration commit for the four source stages. Native updates and
 * cached chroma submissions retain their own timing; this is not frame atomic. */
static unsigned int recipe_generation=1,recipe_enabled,recipe_last_request;
static unsigned int recipe_values[7]={100,100,100,10,10,10,10};
static unsigned int recipe_expected[4],recipe_expected_rows;
static int recipe_committed,recipe_ready_written;
static void apply_cached(void);

static unsigned int recipe_next_generation(unsigned int generation) {
    return generation==0xffffffffu?1:generation+1;
}
static int recipe_superseded(void) {
    if(!recipe_committed)
        return source_rgb_enabled || chroma_enabled || sharpness_enabled || contrast_enabled ||
            source_rgb_values[0]!=100 || source_rgb_values[1]!=100 || source_rgb_values[2]!=100 ||
            chroma_hue!=10 || chroma_saturation!=10 || sharpness_level!=10 || contrast_level!=10 ||
            percent[0]!=100 || percent[1]!=100 || percent[2]!=100;
    return recipe_committed && (source_rgb_generation!=recipe_expected[0] ||
        chroma_generation!=recipe_expected[1] || sharpness_generation!=recipe_expected[2] ||
        contrast_generation!=recipe_expected[3] || row_rgb_generation!=recipe_expected_rows ||
        percent[0]!=100 || percent[1]!=100 || percent[2]!=100);
}
static int recipe_preflight(const unsigned int values[7],int eligible) {
    for(int i=0;i<7;++i) if(values[i]>(i<3?200u:20u)) return -102;
    if(!eligible) return -112;
    int16_t coefficients[9];
    int result=chroma_complete_coefficients_for_mode(&chroma_preview,values[3],values[4],coefficients,1);
    if(!result && (chroma_capture.stock_inputs.have_chm || chroma_capture.stock_inputs.have_ycc))
        result=chroma_complete_coefficients_for_mode(&chroma_capture,values[3],values[4],coefficients,1);
    return result;
}
static void recipe_stage_status(int available,int known,unsigned int actual_generation,
        int modified,int error,unsigned int expected_generation,int expected_modified,
        unsigned int bit,int batch_current,unsigned int *available_mask,unsigned int *applied_mask,int *stage_error) {
    *stage_error=0;
    if(!available) return;
    *available_mask|=bit;
    *stage_error=known?error:error?error:-124;
    if(batch_current && known && !error && actual_generation==expected_generation && modified==expected_modified)
        *applied_mask|=bit;
}
static void recipe_path_status(int capture,int batch_current,unsigned int *available_mask,
        unsigned int *applied_mask,int errors[4]) {
    source_rgb_hardware *rgb=capture?&source_rgb_capture_hardware:&source_rgb_preview_hardware;
    source_rgb_path *rgb_path=capture?&source_rgb_capture:&source_rgb_preview;
    chroma_path *chroma=capture?&chroma_capture:&chroma_preview;
    sharpness_hardware_state *sharp=capture?&sharpness_capture_hardware:&sharpness_preview_hardware;
    contrast_hardware_state *contrast=capture?&contrast_capture_hardware:&contrast_preview_hardware;
    int rgb_available=capture?rgb_path->available:source_rgb_preview_seen;
    recipe_stage_status(rgb_available,rgb->state_known,rgb->generation,rgb->modified,rgb->error,
        recipe_expected[0],recipe_enabled,1,batch_current,available_mask,applied_mask,errors);
    recipe_stage_status(chroma->seen_mask==3,chroma->state_known,chroma->generation,chroma->hardware_modified,
        chroma->hardware_error,recipe_expected[1],recipe_enabled,2,
        batch_current,available_mask,applied_mask,errors+1);
    recipe_stage_status(sharp->available,sharp->state_known,sharp->generation,sharp->modified,sharp->error,
        recipe_expected[2],recipe_enabled,4,batch_current,available_mask,applied_mask,errors+2);
    recipe_stage_status(contrast->available,contrast->state_known,contrast->generation,contrast->modified,contrast->error,
        recipe_expected[3],recipe_enabled,8,batch_current,available_mask,applied_mask,errors+3);
}
static void recipe_write_response(unsigned int id,int result) {
    unsigned int preview_applied=0,capture_applied=0,preview_available=0,capture_available=0;
    int preview_errors[4]={0},capture_errors[4]={0};
    lock_color();
    int superseded=recipe_superseded(),current=recipe_committed && !superseded;
    recipe_path_status(0,current,&preview_available,&preview_applied,preview_errors);
    recipe_path_status(1,current,&capture_available,&capture_applied,capture_errors);
    unsigned int generation=recipe_generation,enabled=recipe_enabled,values[7];
    for(int i=0;i<7;++i) values[i]=recipe_values[i];
    if(!current) {
        for(int i=0;i<3;++i) values[i]=source_rgb_values[i];
        values[3]=chroma_hue;values[4]=chroma_saturation;
        values[5]=sharpness_level;values[6]=contrast_level;
        enabled=source_rgb_enabled || chroma_enabled || sharpness_enabled || contrast_enabled ||
            percent[0]!=100 || percent[1]!=100 || percent[2]!=100;
    }
    unlock_color();
    void *file=fopen("/var/run/nx300m-recipe-response.tmp","w");
    if(!file) return;
    /* Fixed 25-integer protocol; preview errors precede capture errors. */
    int written=fprintf(file,"%u %d %u %u %u %u %u %u %u %u %u %u %u %u %u %d %d %d %d %d %d %d %d %d %d\n",
        id,result,generation,enabled,values[0],values[1],values[2],values[3],values[4],values[5],values[6],
        preview_applied,capture_applied,preview_available,capture_available,
        preview_errors[0],preview_errors[1],preview_errors[2],preview_errors[3],
        capture_errors[0],capture_errors[1],capture_errors[2],capture_errors[3],superseded,getpid());
    int closed=fclose(file);
    if(written>0 && !closed) rename("/var/run/nx300m-recipe-response.tmp","/var/run/nx300m-recipe-response");
    else unlink("/var/run/nx300m-recipe-response.tmp");
}
static void recipe_poll_request(void) {
    lock_color();
    int available=source_rgb_preview_seen && chroma_preview.seen_mask==3 &&
        sharpness_preview.available && contrast_preview.available &&
        sharpness_preview_hardware.available && contrast_preview_hardware.available;
    unlock_color();
    if(available) recipe_ready_written=bridge_write_ready("/var/run/nx300m-recipe-ready");
    void *file=fopen("/var/run/nx300m-recipe-request","r");
    if(!file) return;
    unsigned int id=0,expires=0,operation=0,values[7]={100,100,100,10,10,10,10};char trailing;
    int fields=fscanf(file,"%u %u %u %u %u %u %u %u %u %u %c",&id,&expires,&operation,
        values,values+1,values+2,values+3,values+4,values+5,values+6,&trailing);
    fclose(file);
    if(!id || id==recipe_last_request) return;
    recipe_last_request=id;
    int result=fields!=10?-100:(unsigned int)time((long *)0)>expires?-108:operation>2?-101:0;
    int eligible=operation==1?chroma_supported_pasm():1;
    if(!result && operation!=2) {
        lock_color();
        if(operation==1) result=recipe_preflight(values,eligible);
        if(!result) {
            recipe_generation=recipe_next_generation(recipe_generation);recipe_enabled=operation==1;recipe_committed=1;
            for(int i=0;i<7;++i) recipe_values[i]=operation?values[i]:(i<3?100:10);
            for(int i=0;i<3;++i) { source_rgb_values[i]=recipe_values[i];percent[i]=100; }
            source_rgb_enabled=sharpness_enabled=contrast_enabled=chroma_enabled=recipe_enabled;
            chroma_complete_required=recipe_enabled;
            chroma_hue=recipe_values[3];chroma_saturation=recipe_values[4];
            sharpness_level=recipe_values[5];contrast_level=recipe_values[6];
            recipe_expected[0]=source_rgb_generation=recipe_next_generation(source_rgb_generation);
            recipe_expected[1]=chroma_generation=recipe_next_generation(chroma_generation);
            recipe_expected[2]=sharpness_generation=recipe_next_generation(sharpness_generation);
            recipe_expected[3]=contrast_generation=recipe_next_generation(contrast_generation);
            recipe_expected_rows=row_rgb_generation=recipe_next_generation(row_rgb_generation);
            /* Reset is performed at each next genuine native Update entrance;
             * no original parameter pointer is retained or replayed here. */
            apply_cached();
            result=chroma_apply_cached();
        }
        unlock_color();
    }
    recipe_write_response(id,result);
}
static void recipe_cleanup(void) { bridge_cleanup("recipe"); }
