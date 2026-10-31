/* Attach inherited TIFF orientation to embedded JPEGs without decoding pixels. */
#ifndef NX_MP_PREVIEW_EXIF_H
#define NX_MP_PREVIEW_EXIF_H
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

enum { MPX_OK=0, MPX_INVALID=-2, MPX_NOMEM=-3, MPX_LIMIT=-4 };
typedef struct {
    int metadata_changed;
    unsigned int existing_orientation, effective_orientation;
} mp_preview_exif_info;
typedef struct {
    size_t segment, segment_length, base, tiff_length;
    uint32_t root;
    unsigned int count;
    int le, present;
} mpx_exif;

static uint16_t mpx_u16(const unsigned char *p,int le) {
    return le?(uint16_t)(p[0]|p[1]<<8):(uint16_t)(p[0]<<8|p[1]);
}
static uint32_t mpx_u32(const unsigned char *p,int le) {
    return le?((uint32_t)p[0]|(uint32_t)p[1]<<8|(uint32_t)p[2]<<16|(uint32_t)p[3]<<24):
        ((uint32_t)p[0]<<24|(uint32_t)p[1]<<16|(uint32_t)p[2]<<8|(uint32_t)p[3]);
}
static void mpx_put16(unsigned char *p,unsigned int value,int le) {
    p[le?0:1]=(unsigned char)value;p[le?1:0]=(unsigned char)(value>>8);
}
static void mpx_put32(unsigned char *p,uint32_t value,int le) {
    for(unsigned int i=0;i<4;++i) p[le?i:3-i]=(unsigned char)(value>>(i*8));
}
static int mpx_range(size_t start,size_t count,size_t length) {
    return start<=length && count<=length-start;
}
static int mpx_scan(const unsigned char *data,size_t length,mpx_exif *first,
        unsigned int *orientation) {
    if(length<4 || data[0]!=255 || data[1]!=216) return MPX_INVALID;
    size_t position=2;
    for(unsigned int markers=0;markers<256;++markers) {
        if(!mpx_range(position,2,length) || data[position]!=255) return MPX_INVALID;
        unsigned int marker=data[position+1];
        if(marker==255) {++position;continue;}
        if(marker==218 || marker==217) return MPX_OK;
        if(marker==1 || (marker>=208 && marker<=215)) {position+=2;continue;}
        if(!marker || marker==216 || !mpx_range(position,4,length)) return MPX_INVALID;
        unsigned int size=mpx_u16(data+position+2,0);
        if(size<2 || !mpx_range(position+2,size,length)) return MPX_INVALID;
        if(marker==225 && size>=8 && !memcmp(data+position+4,"Exif\0\0",6)) {
            mpx_exif value;memset(&value,0,sizeof(value));
            value.segment=position;value.segment_length=size+2;
            value.base=position+10;value.tiff_length=size-8;value.present=1;
            if(value.tiff_length<8) return MPX_INVALID;
            const unsigned char *tiff=data+value.base;
            value.le=tiff[0]=='I' && tiff[1]=='I';
            if(!value.le && !(tiff[0]=='M' && tiff[1]=='M')) return MPX_INVALID;
            if(mpx_u16(tiff+2,value.le)!=42) return MPX_INVALID;
            value.root=mpx_u32(tiff+4,value.le);
            if(value.root) {
                if(!mpx_range(value.root,2,value.tiff_length)) return MPX_INVALID;
                value.count=mpx_u16(tiff+value.root,value.le);
                if(value.count>1024) return MPX_LIMIT;
                if(!mpx_range((size_t)value.root+2,(size_t)value.count*12+4,value.tiff_length)) return MPX_INVALID;
                for(unsigned int i=0;i<value.count;++i) {
                    const unsigned char *entry=tiff+value.root+2+i*12;
                    if(mpx_u16(entry,value.le)==274 && mpx_u16(entry+2,value.le)==3 &&
                            mpx_u32(entry+4,value.le)==1) {
                        unsigned int found=mpx_u16(entry+8,value.le);
                        if(found>=1 && found<=8) {*orientation=found;return MPX_OK;}
                    }
                }
            }
            if(!first->present) *first=value;
        }
        position+=size+2;
    }
    return MPX_LIMIT;
}
static void mpx_orientation_entry(unsigned char *entry,unsigned int orientation,int le) {
    memset(entry,0,12);mpx_put16(entry,274,le);mpx_put16(entry+2,3,le);
    mpx_put32(entry+4,1,le);mpx_put16(entry+8,orientation,le);
}

/* Caller passes a malloc-owned JPEG buffer (e.g. mp_extract output).
 * Existing valid EXIF orientation, including 1, always wins over the parent.
 * Only embedded JPEGs lacking orientation inherit parent values 2..8.
 * On error the original buffer and length are untouched. Metadata may grow;
 * dimensions/pixels stay unchanged. Caller still owns and frees the buffer. */
static int mp_preview_apply_orientation(unsigned char **bytes,size_t *length,
        int embedded,unsigned int parent_orientation,mp_preview_exif_info *info) {
    if(!info) return MPX_INVALID;
    memset(info,0,sizeof(*info));
    if(!bytes || !*bytes || !length) return MPX_INVALID;
    mpx_exif exif;memset(&exif,0,sizeof(exif));
    unsigned int existing=0;
    int status=mpx_scan(*bytes,*length,&exif,&existing);if(status) return status;
    info->existing_orientation=existing;info->effective_orientation=existing?existing:1;
    if(existing || !embedded || parent_orientation<2 || parent_orientation>8) return MPX_OK;
    size_t extra;
    unsigned int kept=0;
    if(exif.present) {
        const unsigned char *tiff=*bytes+exif.base;
        for(unsigned int i=0;i<exif.count;++i)
            if(mpx_u16(tiff+exif.root+2+i*12,exif.le)!=274) ++kept;
        if(kept>=1024) return MPX_LIMIT;
        extra=(exif.tiff_length&1)+2+(size_t)(kept+1)*12+4;
        if(exif.segment_length-2+extra>65535) return MPX_LIMIT;
    } else extra=36;
    if(*length>SIZE_MAX-extra) return MPX_LIMIT;
    unsigned char *output=malloc(*length+extra);if(!output) return MPX_NOMEM;
    if(!exif.present) {
        /* SOI, APP1(length=34), Exif\0\0, little-endian TIFF, one IFD entry. */
        memcpy(output,*bytes,2);unsigned char *app=output+2;
        memset(app,0,36);app[0]=255;app[1]=225;mpx_put16(app+2,34,0);
        memcpy(app+4,"Exif\0\0II",8);mpx_put16(app+12,42,1);mpx_put32(app+14,8,1);
        mpx_put16(app+18,1,1);mpx_orientation_entry(app+20,parent_orientation,1);
        memcpy(output+38,*bytes+2,*length-2);
    } else {
        /* Appending a replacement IFD0 keeps every existing TIFF-relative
         * pointer valid and retains other EXIF fields in a single APP1. */
        size_t end=exif.segment+exif.segment_length;
        memcpy(output,*bytes,end);memset(output+end,0,extra);
        memcpy(output+end+extra,*bytes+end,*length-end);
        size_t new_root=exif.tiff_length+(exif.tiff_length&1);
        unsigned char *table=output+exif.base+new_root;
        mpx_put16(output+exif.segment+2,(unsigned int)(exif.segment_length-2+extra),0);
        mpx_put32(output+exif.base+4,(uint32_t)new_root,exif.le);
        mpx_put16(table,kept+1,exif.le);
        unsigned int target=0;int inserted=0;
        const unsigned char *tiff=*bytes+exif.base;
        for(unsigned int i=0;i<exif.count;++i) {
            const unsigned char *entry=tiff+exif.root+2+i*12;
            unsigned int tag=mpx_u16(entry,exif.le);if(tag==274) continue;
            if(!inserted && tag>274) {
                mpx_orientation_entry(table+2+target++*12,parent_orientation,exif.le);inserted=1;
            }
            memcpy(table+2+target++*12,entry,12);
        }
        if(!inserted) mpx_orientation_entry(table+2+target++*12,parent_orientation,exif.le);
        if(exif.root) memcpy(table+2+target*12,tiff+exif.root+2+exif.count*12,4);
    }
    free(*bytes);*bytes=output;*length+=extra;
    info->metadata_changed=1;info->effective_orientation=parent_orientation;
    return MPX_OK;
}
#endif
