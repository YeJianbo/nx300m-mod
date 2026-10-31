/* Two targeted state regressions; production headers, explicit SDK fixtures.
 * No firmware consumer, original ARM math or physical register execution. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <time.h>
#include "chroma_pair.h"
typedef unsigned int u32;
typedef signed int i32;
enum { MATRIX_MIN=-1024,MATRIX_MAX=1023 };
static volatile int color_lock;
static void lock_color(void) { while(__sync_lock_test_and_set(&color_lock,1)) {} }
static void unlock_color(void) { __sync_lock_release(&color_lock); }
static int failures,tone_calls,sdk_calls,sdk_fail_at,supported=1;
static nx_chroma_parameter last_chm;
static const int native_x[7]={0,32,64,128,192,224,255};
static int native_y[63];
static void original_apply(void *self,int *output,const int *input,const int *x,
        const int *y,int count,int knots) {
    (void)self;(void)x;(void)y;(void)knots;
    for(int i=0;i<count;++i) output[i]=input[i];
}
static void original_tone(void *parameters) { if(parameters) ++tone_calls; }
static void *mode_instance(void) { return &supported; }
static int mode_get(void *instance,int key,int *value) {
    (void)instance;
    *value=key==0x41?(supported?2:0):key==0x49?(supported?1:0):0;
    return 1;
}
static int original_chroma(nx_chroma_parameter *parameters) {
    ++sdk_calls;
    if(sdk_calls==sdk_fail_at) return 2;
    if(parameters && parameters->sel==1) last_chm=*parameters;
    return 0;
}
static void *fixture_symbol(void *handle,const char *name) {
    (void)handle;
    if(strstr(name,"8ApplyLutE")) return (void *)original_apply;
    if(strstr(name,"m_iContrastXE")) return (void *)native_x;
    if(strstr(name,"m_iContrastYE")) return (void *)native_y;
    if(!strcmp(name,"ipcs_tone_lut") || !strcmp(name,"ipcm_tone_lut")) return (void *)original_tone;
    if(!strcmp(name,"ipcs_rgb2ycc_param") || !strcmp(name,"ipcm_rgb2ycc_param")) return (void *)original_chroma;
    if(!strcmp(name,"_ZN15CIQDataRepeater11GetInstanceEv")) return (void *)mode_instance;
    if(!strcmp(name,"_ZN15CIQDataRepeater12GetParameterEiPi")) return (void *)mode_get;
    return 0;
}
static void sharpness_prepare_update(void *self,void (*reset)(void *)) { (void)self;(void)reset; }
static void source_rgb_run_update(void *self,void *parameters,void (*original)(void *,void *),void (*reset)(void *)) {
    (void)reset;original(self,parameters);
}
static void sharpness_publish_context(void *file) { (void)file; }
static void source_rgb_publish_context(void *file) { (void)file; }
#define dlsym fixture_symbol
#include "contrast_style.h"
#include "chroma_style.h"
#undef dlsym
#define CHECK(condition) do { if(!(condition)) { \
    fprintf(stderr,"RECIPE_STATE_FAIL line=%d condition=%s\n",__LINE__,#condition);++failures; } } while(0)

static void matched_curve(contrast_transaction *transaction) {
    int input[15]={0},output[15]={0};
    contrast_current=transaction;
    contrast_preview_apply(0,output,input,native_x,native_y,15,7);
}
static void contrast_case(void) {
    contrast_transaction transaction={.generation=7,.level=10,.enabled=0,.capture=0,.eligible=1};
    unsigned char tone[256]={0};
    matched_curve(&transaction);
    CHECK(transaction.saw_curve && !transaction.error && tone_calls==0);
    CHECK(!contrast_preview_hardware.available);
    ipcm_tone_lut(tone);
    CHECK(tone_calls==1 && contrast_preview_hardware.generation==7 &&
          contrast_preview_hardware.state_known && !contrast_preview_hardware.modified && !contrast_preview_hardware.error);
    transaction=(contrast_transaction){.generation=8,.level=10,.enabled=1,.capture=0,.eligible=0};
    matched_curve(&transaction);
    CHECK(transaction.saw_curve && transaction.error==-112);
    transaction.enabled=0;
    matched_curve(&transaction);
    CHECK(transaction.error==-112);
    ipcm_tone_lut(tone);
    CHECK(tone_calls==2 && contrast_preview_hardware.generation==8 && contrast_preview_hardware.error==-112);
    contrast_current=0;
    printf("RECIPE_STATE_CASE name=matched_curve_requires_real_tone_and_keeps_error failures=%d\n",failures);
}
static void init_chroma(void) {
    static const int values[9]={77,150,29,-43,-85,128,128,-107,-21};
    memset(&chroma_preview,0,sizeof chroma_preview);
    chroma_preview.setter=original_chroma;chroma_preview.base_valid=1;
    nx_chroma_parameter chm={0},ycc={0};
    chm.sel=1;chm.chm_mat[0]=chm.chm_mat[3]=128;ycc.sel=3;
    for(int i=0;i<9;++i) ycc.ycc_mat[i]=(u32)values[i];
    nx_chroma_remember(&chroma_preview.stock_inputs,&chm);
    nx_chroma_remember(&chroma_preview.stock_inputs,&ycc);
    chroma_preview.base=chm;
    chroma_generation=9;chroma_enabled=1;chroma_complete_required=1;
    chroma_hue=12;chroma_saturation=10;
}
static void strict_chroma_case(void) {
    init_chroma();
    lock_color();int result=chroma_replay(&chroma_preview);unlock_color();
    CHECK(!result && sdk_calls==2 && chroma_preview.state_known && chroma_preview.hardware_modified);
    nx_chroma_parameter nonunit=chroma_preview.base;
    nonunit.chm_mat[0]=120;nonunit.chm_mat[1]=8;
    ipcm_rgb2ycc_param(&nonunit);
    CHECK(chroma_preview.hardware_error==-114 && !chroma_preview.state_known);
    CHECK(!memcmp(last_chm.chm_mat,nonunit.chm_mat,sizeof nonunit.chm_mat));
    /* Re-enter a compatible base, then lose mode qualification. */
    nonunit.chm_mat[0]=128;nonunit.chm_mat[1]=0;
    supported=1;ipcm_rgb2ycc_param(&nonunit);
    CHECK(chroma_preview.state_known && !chroma_preview.hardware_error);
    supported=0;ipcm_rgb2ycc_param(&nonunit);
    CHECK(chroma_preview.hardware_error==-112 && !chroma_preview.state_known);
    CHECK(!memcmp(last_chm.chm_mat,nonunit.chm_mat,sizeof nonunit.chm_mat));
    /* Reset succeeds only after two genuine selector results; failing its
     * second write cannot retain the preceding successful pair marker. */
    supported=1;chroma_hue=chroma_saturation=10;chroma_complete_required=0;chroma_enabled=0;++chroma_generation;
    sdk_fail_at=sdk_calls+2;
    lock_color();result=chroma_replay(&chroma_preview);unlock_color();
    CHECK(result==2 && !chroma_preview.state_known && chroma_preview.hardware_error==2);
    sdk_fail_at=0;
    lock_color();result=chroma_replay(&chroma_preview);unlock_color();
    CHECK(!result && chroma_preview.state_known && !chroma_preview.hardware_modified && !chroma_preview.hardware_error);
    printf("RECIPE_STATE_CASE name=strict_chroma_no_fallback_and_true_pair_restore failures=%d\n",failures);
}
int main(void) {
    contrast_case();strict_chroma_case();
    printf("RECIPE_RUNTIME_STATE cases=2 failures=%d tone_calls=%d selector_calls=%d\n",failures,tone_calls,sdk_calls);
    return failures?1:0;
}
