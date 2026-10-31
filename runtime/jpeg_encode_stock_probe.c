/* Execute the real libsif EncodeJfif caller on generated YCC in isolation.
 * All mappings and authorization belong exclusively to this fixture. */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <dlfcn.h>
#include "jpeg_cpu_adapter.h"
#define ENCODE "_ZN7DscJfif10EncodeJfifEjjjjjjjj12eImgPackMode11eImgYCCType11eImgQualityRj"
#define HOOK "_ZN7DscJfif10JpegEncodeER7sJpegOpb"
enum { W=128,H=96,P=128,CAP=W*H,INPUT=P*H*3/2 };
static unsigned char input[INPUT],output[CAP+16];
static struct nx300m_jpeg_task expected,observed;
static unsigned ready_calls,map_calls;
static uint32_t addr(void *p) {return (uint32_t)(uintptr_t)p;}
void *nx300m_jpeg_map_address(uint32_t address,uint32_t length,int writable) {
    map_calls++;
    if(writable)return address==addr(output)&&length<=CAP?output:NULL;
    uint32_t base=addr(input);
    return address>=base && address-base<INPUT && length<=INPUT-(address-base)?input+(address-base):NULL;
}
int nx300m_jpeg_input_ready(const struct nx300m_jpeg_task *task) {
    observed=*task;ready_calls++;
    return !memcmp(task,&expected,sizeof expected)?NX_JPEG_INPUT_CBCR:NX_JPEG_INPUT_UNKNOWN;
}
#define CHECK(x) do {if(!(x)){fprintf(stderr,"stock caller probe failed line=%d error=%s\n",__LINE__,dlerror());return 1;}}while(0)
int main(int argc,char **argv) {
    CHECK(argc==2);
    CHECK(setenv("NX300M_CPU_JPEG","1",1)==0);
    CHECK(setenv("NX300M_JPEG_TASK_TRACE","1",1)==0);
    CHECK(unsetenv("NX300M_CPU_JPEG_QUALITY")==0);
    void *library=dlopen("libsif.so",RTLD_NOW|RTLD_GLOBAL);CHECK(library);
    void *(*instance)(void)=dlsym(library,"_ZN7DscJfif11GetInstanceEv");
    unsigned int (*encode)(void *,uint32_t,uint32_t,uint32_t,uint32_t,uint32_t,uint32_t,uint32_t,uint32_t,int,int,int,uint32_t *)=dlsym(library,ENCODE);
    void *hook=dlsym(RTLD_DEFAULT,HOOK),*original=dlsym(library,HOOK);
    int (*describe)(const struct nx300m_jpeg_task *,uint32_t,struct nx300m_jpeg_layout *)=
        dlsym(RTLD_DEFAULT,"nx300m_jpeg_describe_task");
    uint32_t (*maximum)(uint32_t,uint32_t,uint32_t,unsigned,int)=dlsym(library,"_ZN8DscUtils16GetMaxEncodeSizeEjj11eImgQualitybi");
    CHECK(instance&&encode&&hook&&original&&hook!=original&&maximum&&describe);
    Dl_info caller_info,hook_info;CHECK(dladdr((void *)encode,&caller_info)&&dladdr(hook,&hook_info));
    CHECK((uintptr_t)encode-(uintptr_t)caller_info.dli_fbase==0x28c50);
    CHECK(maximum(W,H,0,0,12)==CAP);
    void *self=instance();CHECK(self);
    memset(input,0xa5,sizeof input);memset(output,0xa5,sizeof output);
    for(unsigned row=0;row<H;row++)for(unsigned x=0;x<W;x++)input[row*P+x]=32+(x+row)%192;
    for(unsigned row=0;row<H/2;row++)for(unsigned x=0;x<W/2;x++) {
        input[P*H+row*P+2*x]=96+x/2;
        input[P*H+row*P+2*x+1]=144-row/2;
    }
    expected.width=W;expected.height=H;expected.y_even=addr(input);expected.cb_even=addr(input+P*H);
    expected.stride_y=expected.stride_c=P;expected.output=addr(output);expected.ycc_type=1;expected.timeout=2000;
    struct nx300m_jpeg_layout layout;
    CHECK(describe(&expected,CAP,&layout)==0&&layout.count==3&&layout.sampling==420);
    CHECK(layout.ranges[0].writable==1&&layout.ranges[0].length==CAP);
    CHECK(layout.ranges[1].address==addr(input)&&layout.ranges[1].length==W*H);
    CHECK(layout.ranges[2].address==addr(input+W*H)&&layout.ranges[2].length==W*H/2);
    CHECK(map_calls==0&&ready_calls==0&&expected.size==0);
    struct nx300m_jpeg_task planar=expected;planar.pack_type=1;planar.size=123;
    unsigned int (*invoke_hook)(void *,struct nx300m_jpeg_task *,unsigned int)=hook;
    CHECK(invoke_hook(self,&planar,1)==0&&planar.size==0&&map_calls==0&&ready_calls==0);
    for(unsigned i=0;i<sizeof output;i++)CHECK(output[i]==0xa5);
    uint32_t bytes=0;
    /* Exact stock argument ordering recovered from its frame stores. Mode2
     * uses continuous Y and interleaved CbCr; offset and row offset are zero. */
    unsigned int result=encode(self,addr(output),W,H,H,P,addr(input),0,0,2,1,0,&bytes);
    CHECK(result==1&&ready_calls==1&&!memcmp(&observed,&expected,sizeof expected));
    CHECK(bytes>=4&&bytes<CAP&&output[0]==255&&output[1]==216&&output[bytes-2]==255&&output[bytes-1]==217);
    for(unsigned i=bytes;i<sizeof output;i++)CHECK(output[i]==0xa5);
    void **slot=(void **)((uintptr_t)caller_info.dli_fbase+STOCK_JPEG_GOT);
    CHECK(*slot==hook);
    char path[1024];CHECK(snprintf(path,sizeof path,"%s/stock-encodejfif-mode2.jpg",argv[1])<(int)sizeof path);
    FILE *file=fopen(path,"wb");CHECK(file);CHECK(fwrite(output,1,bytes,file)==bytes&&fclose(file)==0);
    printf("{\"stock_caller\":\"%s\",\"stock_offset\":\"0x28c50\",\"stock_provider\":\"%s\",\"hook_provider\":\"%s\",\"jump_slot\":\"0x%x\",\"jump_slot_bound_to_hook\":true,\"stock_getinstance_used\":true,\"pack_mode\":2,\"task_pack\":0,\"sampling\":420,\"width\":%u,\"height\":%u,\"capacity\":%u,\"bytes\":%u,\"stock_return\":%u,\"exact_generated_task_authorized\":true,\"ready_calls\":%u,\"output_guard_unchanged\":true,\"planar_hook_rejected_before_map_ready\":true,\"planar_rejection_output_unchanged\":true,\"camera_started\":false}\n",
        ENCODE,caller_info.dli_fname,hook_info.dli_fname,STOCK_JPEG_GOT,W,H,CAP,bytes,result,ready_calls);
    return 0;
}
