/* Small, saved output adjustments in the native LUT code domain.
 * These controls are not AE, Kelvin WB, or a calibrated exposure-in-stops API.
 * Build once on confirmation; the camera's frame path stays unchanged. */
#ifndef NX_LUT_TUNE_H
#define NX_LUT_TUNE_H
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <stdlib.h>
#define NX_TUNE_FIELDS 7
struct nx_lut_tune { int value[NX_TUNE_FIELDS]; };
static inline const char *nx_tune_label(int i) {
    static const char *labels[]={"亮度", "对比度", "饱和度", "色温", "色调", "阴影", "高光", "强度"};
    return labels[i];
}
static inline int nx_tune_valid(const struct nx_lut_tune *t) {
    for (int i=0;i<NX_TUNE_FIELDS;i++) if(t->value[i]<-10 || t->value[i]>10) return 0;
    return 1;
}
static inline int nx_tune_zero(const struct nx_lut_tune *t) {
    for (int i=0;i<NX_TUNE_FIELDS;i++) if(t->value[i]) return 0;
    return 1;
}
/* Strict bounded parsing; a partial/corrupt preference never applies partly. */
static inline int nx_tune_parse(const char *body,char id[64],struct nx_lut_tune *t,unsigned *strength) {
    char extra;unsigned version,s;struct nx_lut_tune next;char key[64];
    int fields=sscanf(body,"%u %63s %d %d %d %d %d %d %d %u %c",&version,key,
        &next.value[0],&next.value[1],&next.value[2],&next.value[3],&next.value[4],&next.value[5],&next.value[6],&s,&extra);
    if(fields!=10||version!=1||!nx_tune_valid(&next)||s>100) return 0;
    strcpy(id,key);*t=next;if(strength)*strength=s;return 1;
}
static inline int nx_tune_read(const char *file,char id[64],struct nx_lut_tune *t,unsigned *strength) {
    FILE *in=fopen(file,"r");if(!in) return 0;
    char body[256];
    size_t n=fread(body,1,sizeof body-1,in);int overflow=fgetc(in)!=EOF,failed=ferror(in);fclose(in);
    body[n]=0;if(overflow||failed||strlen(body)!=n) return 0;
    return nx_tune_parse(body,id,t,strength);
}
static inline int nx_tune_snprint(char *out,size_t size,const char *id,const struct nx_lut_tune *t,unsigned strength) {
    int n=snprintf(out,size,"1 %s %d %d %d %d %d %d %d %u\n",id,t->value[0],t->value[1],t->value[2],
        t->value[3],t->value[4],t->value[5],t->value[6],strength);
    return n>0&&(size_t)n<size;
}
static inline int nx_tune_print(FILE *out,const char *id,const struct nx_lut_tune *t,unsigned strength) {
    char line[192];
    return nx_tune_snprint(line,sizeof line,id,t,strength)&&fputs(line,out)!=EOF;
}
#include "lut_tune_store.h"
static inline int nx_tune_load(const char *root,const char *id,struct nx_lut_tune *t,unsigned *strength) {
    if(nx_tune_preset_load(root,id,t,strength))return 1;
    char file[1024],key[64];struct nx_lut_tune next;unsigned s;
    if(snprintf(file,sizeof file,"%s/.state/lut-tune-%s",root,id)>=(int)sizeof file) return 0;
    if(!nx_tune_read(file,key,&next,&s)||strcmp(key,id)) return 0;
    *t=next;if(strength)*strength=s;return 1;
}
static inline void nx_tune_table(const uint16_t *source,uint16_t *out,const struct nx_lut_tune *t) {
    if(nx_tune_zero(t)){memcpy(out,source,4380);return;}
    for(unsigned i=0;i<729;i++) {
        double rgb[3]={source[i]/1023.0,source[730+i]/1023.0,source[1460+i]/1023.0};
        double y=.2126*rgb[0]+.7152*rgb[1]+.0722*rgb[2];
        double low=(1-y)*(1-y),high=y*y;
        double tone=(y-.5)*(1+.05*t->value[1])+.5+.02*t->value[0]
            +.02*t->value[5]*low+.02*t->value[6]*high;
        double sat=1+.05*t->value[2];
        for(int c=0;c<3;c++) rgb[c]=tone+(rgb[c]-y)*sat;
        /* Warm/cool and tint use zero-luma opponent offsets, not WB gains. */
        rgb[0]+=.01*t->value[3]+.005*t->value[4];
        rgb[1]+=-.01*(.2126-.0722)/.7152*t->value[3]-.005*(.2126+.0722)/.7152*t->value[4];
        rgb[2]+=-.01*t->value[3]+.005*t->value[4];
        for(int c=0;c<3;c++) {
            double v=rgb[c]*1023;
            out[c*730+i]=(uint16_t)(v<0?0:v>1023?1023:(unsigned)(v+.5));
        }
    }
    out[729]=out[1459]=out[2189]=0;
}
#endif
