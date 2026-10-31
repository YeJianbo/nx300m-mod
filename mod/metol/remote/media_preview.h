/* Bounded, byte-preserving JPEG previews from JPEG EXIF and classic TIFF/SRW.
 * No RAW decoding, image scaling, shell commands, or original-file writes. */
#ifndef NX_MEDIA_PREVIEW_H
#define NX_MEDIA_PREVIEW_H
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

enum { MP_OK=0, MP_NOT_FOUND=1, MP_UNSUPPORTED=2,
       MP_IO=-1, MP_INVALID=-2, MP_NOMEM=-3, MP_LIMIT=-4 };
typedef struct {
    const char *source;
    uint64_t file_offset;
    unsigned int width, height, orientation, candidate_count;
    int embedded; /* Bytes are never resized or re-encoded. */
} mp_preview_info;

#define MP_MAX_IFDS 64u
#define MP_MAX_ENTRIES 1024u
#define MP_MAX_JPEG (32u*1024u*1024u)
#define MP_METADATA_BUDGET (1024u*1024u)
typedef struct {
    uint64_t offset, length;
    unsigned int width, height, orientation;
    const char *source;
    int embedded;
} mp_candidate;
typedef struct {
    FILE *file;
    uint64_t size;
    size_t budget;
    mp_candidate candidates[MP_MAX_IFDS+1];
    unsigned int count;
    int invalid;
} mp_reader;

static int mp_range(uint64_t offset,uint64_t length,uint64_t bound) {
    return offset<=bound && length<=bound-offset;
}
static int mp_read(mp_reader *r,uint64_t offset,void *bytes,size_t length) {
    if(!mp_range(offset,length,r->size)) return MP_INVALID;
    if(length>r->budget) return MP_LIMIT;
    r->budget-=length;
    if(fseeko(r->file,(off_t)offset,SEEK_SET)) return MP_IO;
    return fread(bytes,1,length,r->file)==length?MP_OK:MP_IO;
}
static uint16_t mp_u16(const unsigned char *p,int le) {
    return le?(uint16_t)(p[0]|p[1]<<8):(uint16_t)(p[0]<<8|p[1]);
}
static uint32_t mp_u32(const unsigned char *p,int le) {
    return le?((uint32_t)p[0]|(uint32_t)p[1]<<8|(uint32_t)p[2]<<16|(uint32_t)p[3]<<24):
        ((uint32_t)p[0]<<24|(uint32_t)p[1]<<16|(uint32_t)p[2]<<8|(uint32_t)p[3]);
}

/* Parse markers only until SOF/SOS, skipping APP payloads with checked offsets. */
static int mp_jpeg_dimensions(mp_reader *r,uint64_t start,uint64_t length,
        unsigned int *width,unsigned int *height) {
    unsigned char b[9];
    if(length<4 || !mp_range(start,length,r->size)) return MP_INVALID;
    int status=mp_read(r,start,b,2);
    if(status) return status;
    if(b[0]!=255 || b[1]!=216) return MP_INVALID;
    uint64_t pos=2;
    for(unsigned int markers=0;markers<256 && pos<length;++markers) {
        status=mp_read(r,start+pos,b,1);if(status) return status;
        if(b[0]!=255) return MP_INVALID;
        do { if(++pos>=length) return MP_INVALID;
             status=mp_read(r,start+pos,b,1);if(status) return status;
        } while(b[0]==255);
        unsigned int marker=b[0];++pos;
        if(marker==0 || marker==216 || marker==217 || marker==218) return MP_INVALID;
        if(marker==1 || (marker>=208 && marker<=215)) continue;
        if(!mp_range(pos,2,length)) return MP_INVALID;
        status=mp_read(r,start+pos,b,2);if(status) return status;
        unsigned int n=mp_u16(b,0);
        if(n<2 || !mp_range(pos,n,length)) return MP_INVALID;
        if(marker>=192 && marker<=207 && marker!=196 && marker!=200 && marker!=204) {
            if(n<8) return MP_INVALID;
            status=mp_read(r,start+pos+2,b,5);if(status) return status;
            *height=mp_u16(b+1,0);*width=mp_u16(b+3,0);
            return *width && *height?MP_OK:MP_INVALID;
        }
        pos+=n;
    }
    return MP_LIMIT;
}
static int mp_add_candidate(mp_reader *r,uint64_t offset,uint64_t length,
        unsigned int orientation,const char *source,int embedded) {
    if(length>MP_MAX_JPEG) { r->invalid=1;return MP_OK; }
    for(unsigned int i=0;i<r->count;++i)
        if(r->candidates[i].offset==offset && r->candidates[i].length==length) return MP_OK;
    mp_candidate value={offset,length,0,0,orientation,source,embedded};
    int status=mp_jpeg_dimensions(r,offset,length,&value.width,&value.height);
    if(status==MP_INVALID) { r->invalid=1;return MP_OK; }
    if(status) return status;
    /* Samsung's full SRW JPEG range includes alignment padding after EOI.
     * Inspect only the last 4 KiB and allow zero/FF padding, not arbitrary data. */
    unsigned char end[4096];size_t tail=length<sizeof(end)?(size_t)length:sizeof(end);
    status=mp_read(r,offset+length-tail,end,tail);if(status) return status;
    size_t last=tail;
    while(last && (end[last-1]==0 || end[last-1]==255)) --last;
    if(last<2 || end[last-2]!=255 || end[last-1]!=217) {r->invalid=1;return MP_OK;}
    if(r->count>=MP_MAX_IFDS+1) return MP_LIMIT;
    r->candidates[r->count++]=value;
    return MP_OK;
}

static int mp_tiff(mp_reader *r,uint64_t base,uint64_t length,const char *source,
        unsigned int *orientation) {
    unsigned char header[8];
    if(length<8 || !mp_range(base,length,r->size)) return MP_INVALID;
    int status=mp_read(r,base,header,8);if(status) return status;
    int le=header[0]=='I' && header[1]=='I';
    if(!le && !(header[0]=='M' && header[1]=='M')) return MP_INVALID;
    if(mp_u16(header+2,le)!=42) return MP_UNSUPPORTED;
    uint32_t queue[MP_MAX_IFDS],seen[MP_MAX_IFDS];unsigned int head=0,tail=1,used=0;
    queue[0]=mp_u32(header+4,le);
    while(head<tail) {
        uint32_t offset=queue[head++];if(!offset) continue;
        unsigned int i;for(i=0;i<used;++i) if(seen[i]==offset) break;
        if(i<used) continue;
        if(used==MP_MAX_IFDS) return MP_LIMIT;
        seen[used++]=offset;
        if(!mp_range(offset,2,length)) return MP_INVALID;
        unsigned char count_bytes[2];status=mp_read(r,base+offset,count_bytes,2);if(status) return status;
        unsigned int count=mp_u16(count_bytes,le);
        if(count>MP_MAX_ENTRIES) return MP_LIMIT;
        uint64_t table_size=(uint64_t)count*12+4;
        if(!mp_range((uint64_t)offset+2,table_size,length)) return MP_INVALID;
        unsigned char *table=malloc((size_t)table_size);if(!table) return MP_NOMEM;
        status=mp_read(r,base+offset+2,table,(size_t)table_size);
        uint32_t jpeg_offset=0,jpeg_length=0,strip_offset=0,strip_length=0,compression=0;
        unsigned int local_orientation=*orientation;
        if(!status) for(i=0;i<count && !status;++i) {
            const unsigned char *entry=table+i*12;
            unsigned int tag=mp_u16(entry,le),type=mp_u16(entry+2,le);
            uint32_t number=mp_u32(entry+4,le);
            if(tag!=330 && tag!=34665 && tag!=513 && tag!=514 && tag!=273 && tag!=279 && tag!=259 && tag!=274) continue;
            unsigned int unit=type==3?2:(type==4 || type==13)?4:0;
            if(!unit || !number) continue;
            if(tag!=330 && tag!=34665 && number!=1) continue;
            if(number>MP_MAX_IFDS) { status=MP_LIMIT;break; }
            unsigned char values[MP_MAX_IFDS*4];size_t size=(size_t)number*unit;
            if(size<=4) memcpy(values,entry+8,size);
            else {
                uint32_t pointer=mp_u32(entry+8,le);
                if(!mp_range(pointer,size,length)) { status=MP_INVALID;break; }
                status=mp_read(r,base+pointer,values,size);if(status) break;
            }
            uint32_t value=unit==2?mp_u16(values,le):mp_u32(values,le);
            if(tag==330 || tag==34665) {
                for(unsigned int j=0;j<number;++j) {
                    uint32_t child=unit==2?mp_u16(values+j*unit,le):mp_u32(values+j*unit,le);
                    if(!child) continue;
                    unsigned int k;for(k=0;k<tail;++k) if(queue[k]==child) break;
                    if(k==tail) { if(tail==MP_MAX_IFDS) {status=MP_LIMIT;break;}queue[tail++]=child; }
                }
            } else if(tag==513) jpeg_offset=value;
            else if(tag==514) jpeg_length=value;
            else if(tag==273) strip_offset=value;
            else if(tag==279) strip_length=value;
            else if(tag==259) compression=value;
            else if(tag==274 && value>=1 && value<=8) {local_orientation=value;if(offset==queue[0]) *orientation=value;}
        }
        if(status) {free(table);return status;}
        uint32_t next=mp_u32(table+count*12,le);free(table);
        if(next) {
            for(i=0;i<tail;++i) if(queue[i]==next) break;
            if(i==tail) {if(tail==MP_MAX_IFDS) return MP_LIMIT;queue[tail++]=next;}
        }
        if(!jpeg_offset && (compression==6 || compression==7)) {jpeg_offset=strip_offset;jpeg_length=strip_length;}
        if(jpeg_offset && jpeg_length) {
            if(!mp_range(jpeg_offset,jpeg_length,length)) {r->invalid=1;continue;}
            status=mp_add_candidate(r,base+jpeg_offset,jpeg_length,local_orientation,source,1);
            if(status) return status;
        }
    }
    return MP_OK;
}

/* 0: success (caller frees *bytes); 1: no preview; 2: unsupported format;
 * negative: I/O, malformed data, allocation failure, or bounded-work limit.
 * On all non-success returns *bytes=NULL, *length=0 and info is cleared.
 * Caller owns path authorization and cache invalidation. Full JPEG requests
 * retain the original JPEG; small requests prefer the smallest embedded JPEG. */
static int mp_extract(const char *path,int prefer_small,unsigned char **bytes,
        size_t *length,mp_preview_info *info) {
    if(!bytes || !length || !info) return MP_INVALID;
    *bytes=NULL;*length=0;memset(info,0,sizeof(*info));
    if(!path) return MP_INVALID;
    FILE *file=fopen(path,"rb");if(!file) return MP_IO;
    struct stat st;
    if(fstat(fileno(file),&st) || !S_ISREG(st.st_mode) || st.st_size<0) {fclose(file);return MP_IO;}
    mp_reader r;memset(&r,0,sizeof(r));r.file=file;r.size=(uint64_t)st.st_size;r.budget=MP_METADATA_BUDGET;
    unsigned char header[8];int status=r.size>=8?mp_read(&r,0,header,8):MP_UNSUPPORTED;
    unsigned int orientation=1;
    int jpeg=!status && header[0]==255 && header[1]==216;
    if(!status && jpeg) {
        uint64_t pos=2;unsigned int markers=0;
        while(!status && pos<r.size && markers++<256) {
            unsigned char b[4];status=mp_read(&r,pos,b,2);if(status) break;
            if(b[0]!=255) {status=MP_INVALID;break;}
            if(b[1]==255) {++pos;continue;}
            if(b[1]==218 || b[1]==217) break;
            if(b[1]==1 || (b[1]>=208 && b[1]<=215)) {pos+=2;continue;}
            status=mp_read(&r,pos+2,b+2,2);if(status) break;
            unsigned int size=mp_u16(b+2,0);
            if(size<2 || !mp_range(pos+2,size,r.size)) {status=MP_INVALID;break;}
            if(b[1]==225 && size>=8) {
                unsigned char magic[6];status=mp_read(&r,pos+4,magic,6);if(status) break;
                if(!memcmp(magic,"Exif\0\0",6)) {
                    status=mp_tiff(&r,pos+10,size-8,"jpeg-exif-thumbnail",&orientation);
                    if(status==MP_UNSUPPORTED) status=MP_OK;
                }
            }
            pos+=2+size;
        }
        if(!status && markers>256) status=MP_LIMIT;
        if(!status) status=mp_add_candidate(&r,0,r.size,orientation,"original-jpeg",0);
    } else if(!status && ((!memcmp(header,"II",2)) || !memcmp(header,"MM",2))) {
        status=mp_tiff(&r,0,r.size,"tiff-embedded-jpeg",&orientation);
    } else if(!status) status=MP_UNSUPPORTED;
    if(!status && !r.count) status=r.invalid?MP_INVALID:MP_NOT_FOUND;
    if(!status) {
        unsigned int selected=0;
        for(unsigned int i=1;i<r.count;++i) {
            mp_candidate *a=r.candidates+i,*b=r.candidates+selected;
            /* The original is a full-view fallback, never preferred over an
             * embedded thumbnail in the small view, regardless of byte count. */
            if(prefer_small?((a->embedded && !b->embedded) ||
                (a->embedded==b->embedded && a->length<b->length)):(a->length>b->length)) selected=i;
        }
        mp_candidate *chosen=r.candidates+selected;
        unsigned char *data=malloc((size_t)chosen->length);
        if(!data) status=MP_NOMEM;
        else if(fseeko(file,(off_t)chosen->offset,SEEK_SET) ||
                fread(data,1,(size_t)chosen->length,file)!=(size_t)chosen->length) {free(data);status=MP_IO;}
        else {
            *bytes=data;*length=(size_t)chosen->length;
            info->source=chosen->source;info->file_offset=chosen->offset;
            info->width=chosen->width;info->height=chosen->height;
            info->orientation=chosen->orientation;info->candidate_count=r.count;info->embedded=chosen->embedded;
        }
    }
    fclose(file);return status;
}
#endif
