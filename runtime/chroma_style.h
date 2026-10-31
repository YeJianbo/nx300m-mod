/* Source chroma controls in the existing color-style.so and Ecore timer.
 * Supported modes use the original SAT/HUE order on YCC and manage both
 * target selectors together. HUE alone can fall back to current CHM when
 * complete bases or the corresponding mode mapping are unavailable.
 * Current complete-path mode scope is Standard/Custom1 P/A/S/M, 2D, unit stock CHM.
 */
#include "chroma_pair.h"
#include "nx500_color_operator.h"
typedef nx_chroma_parameter chroma_param;
typedef int (*chroma_setter)(chroma_param *);
typedef struct {
    chroma_param base;
    chroma_setter setter;
    u32 calls, range_bypasses;
    int base_valid, modified, last_rc;
    nx_chroma_pair stock_inputs;
    u32 ycc_calls;
    int ycc_last_rc;
    int complete_touched, complete_error;
    u32 generation, setter_calls, seen_mask, successful_mask, operation_calls;
    int state_known, hardware_modified, hardware_error, operation_error;
} chroma_path;

static chroma_path chroma_capture, chroma_preview;
static u32 chroma_hue = 10, chroma_saturation = 10, chroma_last_request;
static u32 chroma_generation=1,chroma_enabled;
static int chroma_complete_required;
static int chroma_ready_written;

/* Every pair write, including recovery and cached replay, passes here while
 * color_lock is held. A source generation needs successful writes to both
 * selectors; a failed first/second write cannot inherit an old success. */
static int chroma_submit(chroma_path *path,chroma_param *parameter) {
    if(path->generation!=chroma_generation) {
        path->generation=chroma_generation;path->successful_mask=0;
    }
    int result=path->setter?path->setter(parameter):-120;
    if(path->setter) {
        ++path->setter_calls;++path->operation_calls;
        unsigned int bit=parameter && parameter->sel==1?1:parameter && parameter->sel==3?2:0;
        path->seen_mask|=bit;
        if(!result) path->successful_mask|=bit;
        else path->successful_mask=0;
    }
    if(result && !path->operation_error) path->operation_error=result;
    return result;
}
static int chroma_capture_submit(chroma_param *parameter) { return chroma_submit(&chroma_capture,parameter); }
static int chroma_preview_submit(chroma_param *parameter) { return chroma_submit(&chroma_preview,parameter); }
static chroma_setter chroma_submitter(chroma_path *path) {
    return path==&chroma_capture?chroma_capture_submit:chroma_preview_submit;
}
static void chroma_begin_write(chroma_path *path) {
    if(path->generation!=chroma_generation) {
        path->generation=chroma_generation;path->successful_mask=0;
    }
    path->operation_calls=0;path->operation_error=0;
}
static void chroma_finish_write(chroma_path *path,int result) {
    int error=path->operation_error?path->operation_error:result?result:path->complete_error;
    if(error) {
        path->state_known=0;path->hardware_error=error;path->successful_mask=0;
    } else if(path->operation_calls && path->successful_mask==3) {
        path->state_known=1;path->hardware_modified=path->modified;path->hardware_error=0;
    } else if(path->operation_calls) { path->state_known=0;path->hardware_error=-124; }
}


static int chroma_read_modes(int values[5], int results[5]) {
    void *(*instance)(void) = dlsym((void *)0, "_ZN15CIQDataRepeater11GetInstanceEv");
    int (*get)(void *, int, int *) = dlsym((void *)0, "_ZN15CIQDataRepeater12GetParameterEiPi");
    const int keys[5] = {0x41, 0x49, 0x4b, 0x6c, 0x2c};
    void *repeater = instance && get ? instance() : (void *)0;
    int available = repeater != (void *)0;
    for (int i = 0; i < 5; ++i) {
        values[i] = 0;
        results[i] = repeater ? get(repeater, keys[i], values+i) : 0;
        if (results[i] != 1) available = 0;
    }
    return available;
}

static int chroma_supported_pasm(void) {
    int values[5], results[5];
    /* NX500 names the dial value CAPTURE_MODE and the shooting value APP_MODE.
     * Source P/A/S/M excludes auto(0)/SmartPro(6); Standard/Custom1 effect=0/16 excludes
     * effect=15. Both source builders then select mix/offset/delta = 0. */
    return chroma_read_modes(values, results) && values[0] >= 2 && values[0] <= 5 &&
           values[1] == values[0]-1 && values[2] == 0 && (values[4] == 0 || values[4] == 16);
}

static int chroma_complete_coefficients_for_mode(chroma_path *path, u32 hue, u32 sat, int16_t out[9],int eligible) {
    nx_chroma_pair *pair = &path->stock_inputs;
    if (!pair->have_chm || !pair->have_ycc) return -110;
    if (!eligible) return -112;
    /* The source SAT replaces its chroma stage. A non-unit target CHM would
     * otherwise be silently discarded; other native styles need a separate
     * source mapping before entering this complete path. */
    if (pair->chm.chm_mat[0] != 128 || pair->chm.chm_mat[1] != 0 ||
        pair->chm.chm_mat[2] != 0 || pair->chm.chm_mat[3] != 128) return -114;
    int16_t base[9];
    for (int i = 0; i < 9; ++i) {
        i32 value = (i32)pair->ycc.ycc_mat[i];
        if (value < MATRIX_MIN || value > MATRIX_MAX) return -111;
        base[i] = (int16_t)value;
    }
    int result = nx500_compose_color(out, base, hue, sat, 0, 0, 0);
    if (result) return result;
    for (int i = 0; i < 9; ++i)
        if (out[i] < MATRIX_MIN || out[i] > MATRIX_MAX) return -111;
    return 0;
}

static int chroma_complete_coefficients(chroma_path *path,u32 hue,u32 sat,int16_t out[9]) {
    return chroma_complete_coefficients_for_mode(path,hue,sat,out,chroma_supported_pasm());
}

static int chroma_restore_complete(chroma_path *path) {
    if (!path->complete_touched) return 0;
    int result = nx_chroma_restore(&path->stock_inputs, chroma_submitter(path));
    if (!result) { path->complete_touched = 0; path->modified = 0; }
    return result;
}

static chroma_setter chroma_resolve(chroma_path *path, const char *name) {
    chroma_setter setter = __atomic_load_n(&path->setter, __ATOMIC_ACQUIRE);
    if (!setter) {
        setter = (chroma_setter)dlsym((void *)-1, name);
        if (setter) __atomic_store_n(&path->setter, setter, __ATOMIC_RELEASE);
    }
    return __atomic_load_n(&path->setter, __ATOMIC_ACQUIRE);
}

static int chroma_neutral(void) { return chroma_hue == 10 && chroma_saturation == 10; }

static int chroma_transform(const chroma_param *base, chroma_param *styled) {
    const short *hue = chroma_hue_q8[chroma_hue];
    *styled = *base;
    /* A malformed/out-of-range stock word must not enter signed multiply. */
    for (int index = 0; index < 4; ++index) {
        i32 value = (i32)base->chm_mat[index];
        if (value < MATRIX_MIN || value > MATRIX_MAX) return 0;
    }
    for (int row = 0; row < 2; ++row) {
        for (int column = 0; column < 2; ++column) {
            i32 sum = hue[row * 2] * (i32)base->chm_mat[column] +
                      hue[row * 2 + 1] * (i32)base->chm_mat[column + 2];
            /* Original 0x80381c84: same +128 on either sign, then ASR8.
             * Valid signed11 inputs bound this dot product well within i32. */
            i32 value = (sum + 128) >> 8;
            if (value < MATRIX_MIN || value > MATRIX_MAX) return 0;
            styled->chm_mat[row * 2 + column] = (u32)value;
        }
    }
    return 1;
}

static int chroma_forward_impl(chroma_path *path, chroma_param *original) {
    chroma_param styled;
    int result;
    int16_t coefficients[9];
    int complete_result = chroma_neutral() && !chroma_complete_required ? -110 :
        chroma_complete_coefficients(path, chroma_hue, chroma_saturation, coefficients);
    /* Keep the source matrix order as SAT crosses its neutral step. Legacy
     * HUE-only CHM composition remains available when complete bases or mode
     * mapping are unavailable, but is not used to quantize a supported path. */
    if (chroma_complete_required || chroma_saturation != 10 || complete_result == 0) {
        result = complete_result;
        path->complete_error = result;
        if (!result) {
            path->complete_touched = 1;
            result = nx_chroma_apply_complete(&path->stock_inputs, chroma_submitter(path), coefficients);
            path->complete_error = result;
            path->modified = path->stock_inputs.modified;
            path->last_rc = result;
            return result;
        }
        if (result == -111) ++path->range_bypasses;
        /* Mode changes and a newly incompatible base suspend the complete
         * operator and restore both stock stages, including the unchanged one. */
        result = chroma_restore_complete(path);
        if (!result) result = chroma_submit(path,original);
        if (!result) path->modified = 0;
        path->last_rc = result ? result : (path->complete_error == -110 ? 0 : path->complete_error);
        return result;
    }
    result = chroma_restore_complete(path);
    if (result) { path->last_rc = result; return result; }
    path->complete_error = 0;
    if (original->sel == 3u) {
        result = chroma_submit(path,original);
        path->ycc_last_rc = result;
        return result;
    }
    if (chroma_neutral()) {
        result = chroma_submit(path,original); /* strict pointer/data passthrough */
        if (!result) path->modified = 0;
    } else if (!chroma_transform(original, &styled)) {
        ++path->range_bypasses;
        result = chroma_submit(path,original); /* whole matrix fallback */
        if (!result) path->modified = 0;
    } else {
        result = chroma_submit(path,&styled);
        if (!result) path->modified = 1;
    }
    path->last_rc = result;
    return result;
}

static int chroma_forward(chroma_path *path,chroma_param *original) {
    chroma_begin_write(path);
    int result=chroma_forward_impl(path,original);
    chroma_finish_write(path,result);
    return result;
}
static int chroma_replay(chroma_path *path) {
    if(chroma_neutral() && !chroma_complete_required && path->stock_inputs.have_chm && path->stock_inputs.have_ycc) {
        chroma_begin_write(path);path->complete_error=0;
        int result=nx_chroma_restore(&path->stock_inputs,chroma_submitter(path));
        if(!result) { path->modified=0;path->complete_touched=0; }
        path->last_rc=result;chroma_finish_write(path,result);return result;
    }
    return chroma_forward(path,&path->base);
}

static int chroma_hook(chroma_path *path, const char *name, chroma_param *param) {
    chroma_setter setter = chroma_resolve(path, name);
    if (!setter) return 1;
    if(!color_process_current()) return setter(param);
    if (!param || (param->sel != 1u && param->sel != 3u)) return setter(param);
    lock_color();
    nx_chroma_remember(&path->stock_inputs, param);
    if (param->sel == 3u) {
        /* Cache only the incoming stock value; complete transforms never feed
         * their own output back into this cache. */
        ++path->ycc_calls;
        int result = chroma_forward(path, param);
        path->ycc_last_rc = result;
        unlock_color();
        return result;
    }
    path->base = *param;
    path->base_valid = 1;
    ++path->calls;
    int result = chroma_forward(path, param);
    unlock_color();
    return result;
}

int ipcs_rgb2ycc_param(chroma_param *param) {
    return chroma_hook(&chroma_capture, "ipcs_rgb2ycc_param", param);
}
int ipcm_rgb2ycc_param(chroma_param *param) {
    return chroma_hook(&chroma_preview, "ipcm_rgb2ycc_param", param);
}

static int chroma_apply_cached(void) {
    if (chroma_capture.base_valid && chroma_resolve(&chroma_capture, "ipcs_rgb2ycc_param"))
        chroma_replay(&chroma_capture);
    if (chroma_preview.base_valid && chroma_resolve(&chroma_preview, "ipcm_rgb2ycc_param"))
        chroma_replay(&chroma_preview);
    return chroma_capture.last_rc ? chroma_capture.last_rc : chroma_preview.last_rc;
}

static void chroma_publish_path(void *file, const chroma_path *path) {
    fprintf(file, "{\"chm_available\":%s,\"ycc_available\":%s,"
            "\"chm_calls\":%u,\"ycc_calls\":%u,\"chm_setter_result\":%d,"
            "\"ycc_setter_result\":%d,\"complete_error\":%d,\"complete_modified\":%s,"
            "\"restore_result\":%d,\"complete_state_known\":%s,\"chm_q7\":",
            path->stock_inputs.have_chm ? "true" : "false",
            path->stock_inputs.have_ycc ? "true" : "false",
            path->calls, path->ycc_calls, path->last_rc, path->ycc_last_rc,
            path->complete_error, path->stock_inputs.modified ? "true" : "false",
            path->stock_inputs.restore_result,
            path->stock_inputs.state_known ? "true" : "false");
    if (path->stock_inputs.have_chm) {
        fprintf(file, "[");
        for (int i = 0; i < 4; ++i)
            fprintf(file, "%s%d", i ? "," : "", (i32)path->stock_inputs.chm.chm_mat[i]);
        fprintf(file, "]");
    } else fprintf(file, "null");
    fprintf(file, ",\"ycc_q8\":");
    if (path->stock_inputs.have_ycc) {
        fprintf(file, "[");
        for (int i = 0; i < 9; ++i)
            fprintf(file, "%s%d", i ? "," : "", (i32)path->stock_inputs.ycc.ycc_mat[i]);
        fprintf(file, "]");
    } else fprintf(file, "null");
    fprintf(file,",\"generation\":%u,\"setter_calls\":%u,\"selector_seen_mask\":%u,"
        "\"state_known\":%s,\"hardware_modified\":%s,\"hardware_error\":%d}",
        path->generation,path->setter_calls,path->seen_mask,path->state_known?"true":"false",
        path->hardware_modified?"true":"false",path->hardware_error);
}

static void chroma_publish_context(u32 id, const chroma_path *cap, const chroma_path *prev) {
    int values[5] = {0}, results[5] = {0};
    /* Original GetCaptureMode/Get3DMode/GetSmartProMode read these same
     * repeater slots. CCapParamIf::convertParamID maps USERDATA_DIALMODE
     * to 0x49 and USERDATA_PW to 0x2c; neither is a source effect-mode enum.
     * Values remain raw until cross-model mode meanings are established. */
    int available = chroma_read_modes(values, results);
    int mapped = available && values[0] >= 2 && values[0] <= 5 &&
                 values[1] == values[0]-1 && values[2] == 0 && (values[4] == 0 || values[4] == 16);
    void *file = fopen("/var/run/nx300m-chroma-style-context.tmp", "w");
    if (!file) return;
    fprintf(file, "{\"request_id\":%u,\"guest_pid\":%d,\"available\":%s,"
            "\"capture_mode_raw\":%d,\"dial_mode_raw\":%d,\"mode3d_raw\":%d,"
            "\"smartpro_mode_raw\":%d,\"picture_wizard_raw\":%d,\"read_results\":[%d,%d,%d,%d,%d],"
            "\"source_mode_mapping_verified\":%s,\"source_mode_scope\":\"Standard/Custom1 P/A/S/M 2D\",\"source_modes\":",
            id, getpid(), available ? "true" : "false",
            values[0], values[1], values[2], values[3], values[4], results[0], results[1], results[2], results[3], results[4],
            mapped ? "true" : "false");
    if (mapped) fprintf(file, "{\"capture_mode\":%d,\"app_mode\":%d,\"effect_mode\":%d}", values[1], values[0], values[4]);
    else fprintf(file, "null");
    fprintf(file, ",\"stock_inputs\":{\"capture\":");
    chroma_publish_path(file, cap);
    fprintf(file, ",\"preview\":");
    chroma_publish_path(file, prev);
    fprintf(file, "}");
    contrast_publish_context(file);
    sharpness_publish_context(file);
    source_rgb_publish_context(file);
    fprintf(file, "}\n");
    fclose(file);
    rename("/var/run/nx300m-chroma-style-context.tmp", "/var/run/nx300m-chroma-style-context.json");
}

static void chroma_write_response(u32 id, int result) {
    chroma_path cap, prev;
    u32 hue, sat;
    int active;
    lock_color();
    cap = chroma_capture; prev = chroma_preview;
    hue = chroma_hue; sat = chroma_saturation; active = !chroma_neutral();
    unlock_color();
    chroma_publish_context(id, &cap, &prev);
    void *file = fopen("/var/run/nx300m-chroma-style-response.tmp", "w");
    if (!file) return;
    fprintf(file, "%u %d %u %u %d %u %u %u %u %d %d %d %d %d %d %d\n",
        id, result, hue, sat, active, cap.calls, prev.calls,
        cap.range_bypasses, prev.range_bypasses, cap.last_rc, prev.last_rc,
        cap.base_valid, prev.base_valid, cap.modified, prev.modified, getpid());
    fclose(file);
    rename("/var/run/nx300m-chroma-style-response.tmp", "/var/run/nx300m-chroma-style-response");
}

static void chroma_poll_request(void) {
    void *file = fopen("/var/run/nx300m-chroma-style-request", "r");
    if (!file) return;
    u32 id = 0, expires = 0, operation = 0, hue = 10, sat = 10;
    char trailing;
    int fields = fscanf(file, "%u %u %u %u %u %c", &id, &expires, &operation, &hue, &sat, &trailing);
    fclose(file);
    if (!id || id == chroma_last_request) return;
    chroma_last_request = id;
    int result = 0;
    if (fields != 5) result = -100;
    else if ((u32)time((long *)0) > expires) result = -108;
    else if (operation > 2u) result = -101;
    else if (hue > 20u || sat > 20u) result = -102;
    if (!result && operation != 2u) {
        lock_color();
        if (operation == 1u && sat != 10u) {
            int16_t coefficients[9];
            result = chroma_complete_coefficients(&chroma_preview, hue, sat, coefficients);
            if (!result && chroma_capture.stock_inputs.have_chm && chroma_capture.stock_inputs.have_ycc)
                result = chroma_complete_coefficients(&chroma_capture, hue, sat, coefficients);
        }
        if (!result) {
            chroma_hue = operation == 0u ? 10u : hue;
            chroma_saturation = operation == 0u ? 10u : sat;
            chroma_enabled=operation==1;
            chroma_complete_required=0;
            if(++chroma_generation==0) chroma_generation=1;
            result = chroma_apply_cached();
        }
        unlock_color();
    }
    chroma_write_response(id, result);
}

static void chroma_write_ready(void) {
    if(!chroma_resolve(&chroma_capture,"ipcs_rgb2ycc_param") ||
       !chroma_resolve(&chroma_preview,"ipcm_rgb2ycc_param")) return;
    chroma_ready_written=bridge_write_ready("/var/run/nx300m-chroma-style-ready");
}

static void chroma_initialize(void) {
    chroma_capture=(chroma_path){0}; chroma_preview=(chroma_path){0};
    chroma_last_request=0; chroma_generation=1; chroma_enabled=0;
    chroma_complete_required=chroma_ready_written=0;
    chroma_hue = chroma_saturation = 10;
}

/* Caller holds the shared bridge lock, as for the RGB restoration. */
static void chroma_shutdown(void) {
    chroma_hue = chroma_saturation = 10;
    chroma_apply_cached();
    bridge_cleanup("chroma-style");
}
