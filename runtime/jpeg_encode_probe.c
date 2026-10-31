/* Standalone ARM fixture; supplies its own synthetic YCC producer and mappings.
 * It deliberately does not link libjpeg, exercising the adapter's dlopen lifetime. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stddef.h>
#include <jpeglib.h>
#include "jpeg_cpu_adapter.h"
typedef char stock_component_abi[sizeof(jpeg_component_info)==0x58?1:-1];
typedef char stock_comp_info_offset[offsetof(struct jpeg_compress_struct,comp_info)==0x54?1:-1];
typedef char stock_raw_offset[offsetof(struct jpeg_compress_struct,raw_data_in)==0xd0?1:-1];
typedef char stock_dct_offset[offsetof(struct jpeg_compress_struct,dct_method)==0xe8?1:-1];
enum { W=34,H=18,P=48,CAP=65536 };
static unsigned char y[P*H+16],cb[P*H+16],cr[P*H+16],output[CAP+16];
static struct { unsigned char *data;uint32_t bytes; } maps[4];
static int authorized=1,byte_order=1;
static void *map(uint32_t a,uint32_t n,int writable) {
    for(unsigned i=0;i<4;i++) {
        uint32_t base=(uint32_t)(uintptr_t)maps[i].data;
        if(a>=base && a-base<=maps[i].bytes && n<=maps[i].bytes-(a-base) && (!writable || i==3))
            return maps[i].data+(a-base);
    }
    return NULL;
}
static int ready(const struct nx300m_jpeg_task *task) { (void)task;return authorized?byte_order:0; }
static uint32_t address(void *p) {return (uint32_t)(uintptr_t)p;}
static struct nx300m_jpeg_task make_task(int sampling,int pack,int split,int order,int quality) {
    memset(y,0xa5,sizeof y);memset(cb,0xa5,sizeof cb);memset(cr,0xa5,sizeof cr);
    memset(output,0xa5,sizeof output);byte_order=order;
    unsigned rows=H/(sampling?2:1);
    for(unsigned row=0;row<H;row++)for(unsigned x=0;x<W;x++)y[row*P+x]=32+(x*3+row*5)%192;
    for(unsigned row=0;row<rows;row++)for(unsigned x=0;x<W/2;x++) {
        unsigned char u=64+(x*3+row*7)%128,v=64+(x*7+row*3)%128;
        if(pack){cb[row*P+x]=u;cr[row*P+x]=v;}
        else {cb[row*P+2*x+(order==NX_JPEG_INPUT_CRCB)]=u;cb[row*P+2*x+(order==NX_JPEG_INPUT_CBCR)]=v;}
    }
    maps[0].data=y;maps[0].bytes=P*H;maps[1].data=cb;maps[1].bytes=P*rows;
    maps[2].data=cr;maps[2].bytes=P*rows;maps[3].data=output;maps[3].bytes=CAP;
    struct nx300m_jpeg_task t={0};t.width=W;t.height=H;t.y_even=address(y);t.cb_even=address(cb);
    t.cr_even=pack?address(cr):0;t.stride_y=t.stride_c=P*(split?2:1);
    if(split){t.y_odd=address(y+P);t.cb_odd=address(cb+P);t.cr_odd=pack?address(cr+P):0;}
    t.output=address(output);t.ycc_type=sampling;t.pack_type=pack;t.quality=quality;t.timeout=2000;return t;
}
static int save(const char *directory,const char *name,uint32_t size) {
    char path[1024];if(snprintf(path,sizeof path,"%s/%s",directory,name)>=(int)sizeof path)return 0;
    FILE *f=fopen(path,"wb");if(!f)return 0;int ok=fwrite(output,1,size,f)==size;return fclose(f)==0&&ok;
}
static int pristine(void) {for(unsigned i=0;i<sizeof output;i++)if(output[i]!=0xa5)return 0;return 1;}
int main(int argc,char **argv) {
    if(argc!=2)return 2;
    fprintf(stderr,"compress offsets comp_info=%x raw_data_in=%x dct_method=%x component=%x\n",
        (unsigned)offsetof(struct jpeg_compress_struct,comp_info),(unsigned)offsetof(struct jpeg_compress_struct,raw_data_in),
        (unsigned)offsetof(struct jpeg_compress_struct,dct_method),(unsigned)sizeof(jpeg_component_info));
    struct nx300m_jpeg_memory_ops ops={map,ready};struct nx300m_jpeg_cpu_result result;
    unsigned char reference[CAP];unsigned cases=0;
    for(int sampling=0;sampling<2;sampling++) {
        uint32_t reference_size=0;
        for(int pack=0;pack<2;pack++)for(int split=0;split<2;split++)for(int order=1;order<3;order++) {
            struct nx300m_jpeg_task t=make_task(sampling,pack,split,order,0);
            int rc=nx300m_jpeg_cpu_encode(&t,CAP,&ops,0,&result);
            if(rc || !t.size || result.bytes!=t.size || result.quant_table_index!=10 ||
                    result.sampling!=(sampling?420u:422u)){fprintf(stderr,"encode case=%u rc=%d size=%u\n",cases,rc,t.size);return 10;}
            if(!reference_size){reference_size=t.size;memcpy(reference,output,t.size);}
            else if(t.size!=reference_size || memcmp(reference,output,t.size)) {
                unsigned at=0;while(at<t.size&&at<reference_size&&reference[at]==output[at])at++;
                fprintf(stderr,"layout mismatch sampling=%d pack=%d split=%d order=%d size=%u ref=%u offset=%u bytes=%u/%u\n",
                    sampling,pack,split,order,t.size,reference_size,at,at<reference_size?reference[at]:0,at<t.size?output[at]:0);return 11;
            }
            for(unsigned i=t.size;i<sizeof output;i++)if(output[i]!=0xa5)return 12;
            cases++;
        }
        for(int quality=0;quality<3;quality++) {
            struct nx300m_jpeg_task t=make_task(sampling,0,1,1,quality);
            if(nx300m_jpeg_cpu_encode(&t,CAP,&ops,0,&result))return 13;
            char name[64];snprintf(name,sizeof name,"sampling%d-quality%d.jpg",sampling?420:422,quality);
            if(!save(argv[1],name,t.size))return 14;
        }
    }
    struct nx300m_jpeg_task t=make_task(1,0,1,1,0);authorized=0;t.size=123;
    if(nx300m_jpeg_cpu_encode(&t,CAP,&ops,0,&result)!=-61 || t.size || !pristine())return 20;
    authorized=1;t=make_task(1,0,1,1,0);t.size=123;
    if(nx300m_jpeg_cpu_encode(&t,32,&ops,0,&result)!=-5 || t.size || !pristine())return 21;
    t=make_task(1,0,1,1,0);maps[0].bytes=(H-1)*P+W-1;
    if(nx300m_jpeg_cpu_encode(&t,CAP,&ops,0,&result)!=-14 || t.size || !pristine())return 22;
    t=make_task(1,0,1,1,0);
    if(nx300m_jpeg_cpu_encode(&t,CAP,&ops,90,&result) || result.quant_table_index!=UINT32_MAX ||
            strcmp(result.quality_policy,"explicit-ijg-software-quality"))return 23;
    if(!save(argv[1],"explicit-ijg90.jpg",t.size))return 24;
    printf("{\"layout_cases\":%u,\"layout_bytes_equal\":true,\"unknown_input_rejected\":true,\"short_capacity_rejected\":true,\"short_mapping_rejected\":true,\"failed_output_unchanged\":true,\"explicit_quality\":true,\"compress_abi_accepted\":432}\n",cases);
    return 0;
}
