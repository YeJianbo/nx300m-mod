/* Explicit ARM test process. Genuine IQ Update/Reset and UDD setters execute.
 * GetIndex/SetParam are deliberate fixture functions; all MMIO is private RAM.
 * This exercises parameter submission, never pixels, a sensor or ISP DMA. */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <unistd.h>
#include <dlfcn.h>
#include <pthread.h>
#include <sys/mman.h>
#include <sys/syscall.h>
/* Toolchain headers target a newer libc; retain the stock 2.13 ABI. */
extern int stock_fscanf(FILE *,const char *,...) __asm__("fscanf");

static volatile unsigned running=1, heartbeat;
static unsigned *got[4], index_value, selector_mask, calls[3];
static unsigned source_ccm[9], source_chm[19], source_ycc[19];
static unsigned source_pcse[2][13];
static unsigned char source_tone[2048];
static int result_ccm,result_chm,result_ycc,result_pcse[2];
static void *worker(void *unused) {
    (void)unused;
    while(running){++heartbeat;usleep(10000);}
    return NULL;
}
static unsigned fixture_index(void *self,void *parameters) {
    (void)self;(void)parameters;return index_value;
}
static int fixture_color(void *self,unsigned index) {
    (void)self;(void)index;++calls[0];
    result_ccm=((int (*)(void *))(uintptr_t)*got[0])(source_ccm);
    if(selector_mask&1)result_chm=((int (*)(void *))(uintptr_t)*got[2])(source_chm);
    if(selector_mask&2)result_ycc=((int (*)(void *))(uintptr_t)*got[2])(source_ycc);
    return 1; /* Original node Update consumes a bool, not the UDD status. */
}
static int fixture_gamma(void *self,unsigned index) {
    (void)self;(void)index;++calls[1];
    ((void (*)(void *))(uintptr_t)*got[1])(source_tone);
    return 1;
}
static int fixture_pcse(void *self,unsigned index) {
    (void)self;(void)index;++calls[2];
    if(selector_mask&4)result_pcse[0]=((int (*)(void *))(uintptr_t)*got[3])(source_pcse[0]);
    if(selector_mask&8)result_pcse[1]=((int (*)(void *))(uintptr_t)*got[3])(source_pcse[1]);
    return 1;
}
static int dump(const char *tag,const char *suffix,const void *data,unsigned size) {
    char path[256];snprintf(path,sizeof path,"/arm-vm/mfw-results/%s.%s",tag,suffix);
    FILE *f=fopen(path,"wb");if(!f)return 0;
    return fwrite(data,1,size,f)==size && !fclose(f);
}
static void sources(unsigned variant) {
    const int ccm[9]={145,-13,-4,-7,139,-4,-3,-9,140};
    memset(source_chm,0,sizeof source_chm);
    for(unsigned i=0;i<9;i++)source_ccm[i]=(unsigned)(ccm[i]+((i%4==0)?(int)variant*7:0));
    source_chm[0]=1;source_chm[6]=128+variant*4;source_chm[7]=7;
    source_chm[8]=(unsigned)-5;source_chm[9]=127+variant*3;
    memset(source_ycc,0,sizeof source_ycc);source_ycc[0]=3;
    const int ycc[9]={77,150,29,-43,-85,128,128,-107,-21};
    for(unsigned i=0;i<9;i++)source_ycc[10+i]=(unsigned)(ycc[i]+(i%4==0?(int)variant:0));
    for(unsigned i=0;i<2048;i++){
        unsigned value=i*255/2047+variant*2;source_tone[i]=(unsigned char)(value>255?255:value);
    }
    for(unsigned sel=0;sel<2;sel++) {
        source_pcse[sel][0]=sel;
        for(unsigned i=0;i<12;i++)source_pcse[sel][i+1]=(unsigned)((int)i*3-16+(int)variant*7+(int)sel*40);
    }
}
int main(void) {
    void *iq=dlopen("libiq-fw-slpcam.so",RTLD_LAZY|RTLD_GLOBAL);
    if(!iq){fprintf(stderr,"IQ dlopen: %s\n",dlerror());return 2;}
    void *udd=dlopen("libudd.so.3.1",RTLD_NOW|RTLD_GLOBAL);
    if(!udd){fprintf(stderr,"UDD dlopen: %s\n",dlerror());return 2;}
    unsigned *vt[3]={dlsym(iq,"_ZTV12CIpcmColorD4"),dlsym(iq,"_ZTV12CIpcmGammaD4"),dlsym(iq,"_ZTV11CIpcmPcseD4")};
    void (*reset)(void *)=dlsym(iq,"_ZN17CIpcParameterNode5ResetEv");
    Dl_info info;if(!vt[0]||!vt[1]||!vt[2]||!reset||!dladdr(vt[0],&info))return 3;
    uintptr_t bias=(uintptr_t)info.dli_fbase;
    got[0]=(unsigned *)(bias+0x3c0c68);got[1]=(unsigned *)(bias+0x3c0418);got[2]=(unsigned *)(bias+0x3c1bec);
    got[3]=(unsigned *)(bias+0x3c3088);
    for(unsigned k=0;k<3;k++){
        uintptr_t page=(uintptr_t)vt[k]&~4095U;
        if(mprotect((void *)page,4096,PROT_READ|PROT_WRITE))return 4;
        vt[k][6]=(unsigned)(uintptr_t)(k==2?fixture_pcse:k==1?fixture_gamma:fixture_color);
        vt[k][7]=(unsigned)(uintptr_t)fixture_index;
    }
    void **core=dlsym(udd,"ipcm_reg_core_base"),**top=dlsym(udd,"ipcm_reg_top_base");
    void **ipcs=dlsym(udd,"ipcs_reg_base");
    unsigned char *ram=calloc(1,0x20000),*expected=calloc(1,0x20000),*capture=malloc(0xb000);
    if(!core||!top||!ipcs||!ram||!expected||!capture)return 6;
    *core=ram;*top=ram+0x10000;*ipcs=capture;
    int (*original_ccm)(void *)=dlsym(udd,"ipcm_color_correction_param");
    int (*original_chm)(void *)=dlsym(udd,"ipcm_rgb2ycc_param");
    void (*original_tone)(void *)=dlsym(udd,"ipcm_tone_lut");
    int (*original_pcse)(void *)=dlsym(udd,"ipcm_pcse_primary_gain_param");
    if(!original_ccm||!original_chm||!original_tone||!original_pcse)return 7;
    /* Resolve each genuine IQ PLT entry before making GOT pages read-only.
     * The full application exports unrelated lazy symbols absent here. */
    sources(0);
    ((int (*)(void *))(bias+0x59318))(source_ccm);
    ((void (*)(void *))(bias+0x57a28))(source_tone);
    ((int (*)(void *))(bias+0x5c1a4))(source_chm);
    ((int (*)(void *))(bias+0x5c1a4))(source_ycc);
    ((int (*)(void *))(bias+0x5ff78))(source_pcse[0]);
    ((int (*)(void *))(bias+0x5ff78))(source_pcse[1]);
    for(unsigned k=0;k<3;k++)if(mprotect((void *)((uintptr_t)vt[k]&~4095U),4096,PROT_READ))return 4;
    for(unsigned k=0;k<4;k++)if(mprotect((void *)((uintptr_t)got[k]&~4095U),4096,PROT_READ))return 5;
    unsigned nodes[3][64]={{0}},parameters[39]={0};
    for(unsigned k=0;k<3;k++){nodes[k][0]=(unsigned)(uintptr_t)(vt[k]+2);reset(nodes[k]);}
    pthread_t thread;if(pthread_create(&thread,NULL,worker,NULL))return 8;
    FILE *ready=fopen("/tmp/ipcm-fixture.ready","w");if(!ready)return 9;
    fprintf(ready,"%u %u %u %u %u %u %u %u\n",(unsigned)getpid(),(unsigned)(uintptr_t)got[0],
        (unsigned)(uintptr_t)got[1],(unsigned)(uintptr_t)got[2],
        (unsigned)(uintptr_t)got[3],(unsigned)(uintptr_t)(vt[0]+2),(unsigned)(uintptr_t)(vt[1]+2),
        (unsigned)(uintptr_t)(vt[2]+2));fclose(ready);
    unsigned last=0;
    while(running){
        unsigned seq=0,variant=0,force=0,mask=0;char tag[64],expect_tag[64];
        FILE *request=fopen("/tmp/ipcm-request","r");
        if(!request){usleep(10000);continue;}
        int parsed=stock_fscanf(request,"%u %u %u %u %63s %63s",&seq,&variant,&force,&mask,tag,expect_tag);fclose(request);
        if(parsed!=6||seq<=last){usleep(10000);continue;}last=seq;
        if(variant==99)break;if(variant>1||force>2||mask>15)return 10;
        sources(variant);index_value=variant+1;selector_mask=mask;
        result_ccm=result_chm=result_ycc=result_pcse[0]=result_pcse[1]=0;
        unsigned char source_before[sizeof source_ccm+sizeof source_chm+sizeof source_ycc+sizeof source_tone+sizeof source_pcse];
        memcpy(source_before,source_ccm,sizeof source_ccm);
        memcpy(source_before+sizeof source_ccm,source_chm,sizeof source_chm);
        memcpy(source_before+sizeof source_ccm+sizeof source_chm,source_ycc,sizeof source_ycc);
        memcpy(source_before+sizeof source_ccm+sizeof source_chm+sizeof source_ycc,source_tone,sizeof source_tone);
        memcpy(source_before+sizeof source_ccm+sizeof source_chm+sizeof source_ycc+sizeof source_tone,source_pcse,sizeof source_pcse);
        memset(ram,0xa5,0x20000);memset(expected,0xa5,0x20000);memset(capture,0x5a,0xb000);
        if(force==1)for(unsigned k=0;k<3;k++)reset(nodes[k]);
        if(force==2)reset(nodes[1]); /* Leave Color cache unchanged for retry tests. */
        unsigned before[3]={calls[0],calls[1],calls[2]},beat=heartbeat;
        for(unsigned k=0;k<3;k++)((void (*)(void *,void *))(uintptr_t)vt[k][2])(nodes[k],parameters);
        char path[256];snprintf(path,sizeof path,"/mnt/mmc/expected/%s.bin",expect_tag);
        unsigned char input[sizeof source_before];FILE *ef=fopen(path,"rb");
        if(!ef||fread(input,1,sizeof input,ef)!=sizeof input||fclose(ef))return 11;
        *core=expected;*top=expected+0x10000;
        int erc0=original_ccm(input),erc2=0,erc3=0;
        if(mask&1)erc2=original_chm(input+sizeof source_ccm);
        if(mask&2)erc3=original_chm(input+sizeof source_ccm+sizeof source_chm);
        original_tone(input+sizeof source_ccm+sizeof source_chm+sizeof source_ycc);
        int erc4=0,erc5=0;
        if(mask&4)erc4=original_pcse(input+sizeof source_ccm+sizeof source_chm+sizeof source_ycc+sizeof source_tone);
        if(mask&8)erc5=original_pcse(input+sizeof source_ccm+sizeof source_chm+sizeof source_ycc+sizeof source_tone+sizeof source_pcse[0]);
        *core=ram;*top=ram+0x10000;
        unsigned source_unchanged=!memcmp(source_before,source_ccm,sizeof source_ccm)&&
            !memcmp(source_before+sizeof source_ccm,source_chm,sizeof source_chm)&&
            !memcmp(source_before+sizeof source_ccm+sizeof source_chm,source_ycc,sizeof source_ycc)&&
            !memcmp(source_before+sizeof source_ccm+sizeof source_chm+sizeof source_ycc,source_tone,sizeof source_tone)&&
            !memcmp(source_before+sizeof source_ccm+sizeof source_chm+sizeof source_ycc+sizeof source_tone,source_pcse,sizeof source_pcse);
        if(!dump(tag,"actual.bin",ram,0x20000)||!dump(tag,"expected.bin",expected,0x20000)||
            !dump(tag,"source.bin",source_before,sizeof source_before)||!dump(tag,"ipcs.bin",capture,0xb000))return 12;
        snprintf(path,sizeof path,"/arm-vm/mfw-results/%s.fixture.json",tag);
        FILE *f=fopen(path,"w");if(!f)return 13;
        fprintf(f,"{\"variant\":%u,\"selector_mask\":%u,\"source_unchanged\":%u,\"source_pointer\":[%u,%u,%u,%u,%u,%u],"
            "\"setparam_calls\":[%u,%u,%u],\"calls_before\":[%u,%u,%u],\"setter_result\":[%d,%d,%d,%d,%d],"
            "\"expected_setter_result\":[%d,%d,%d,%d,%d],\"update_tid\":%ld,\"heartbeat\":[%u,%u]}\n",
            variant,mask,source_unchanged,(unsigned)(uintptr_t)source_ccm,(unsigned)(uintptr_t)source_tone,
            (unsigned)(uintptr_t)source_chm,(unsigned)(uintptr_t)source_ycc,(unsigned)(uintptr_t)source_pcse[0],(unsigned)(uintptr_t)source_pcse[1],
            calls[0],calls[1],calls[2],before[0],before[1],before[2],result_ccm,result_chm,result_ycc,result_pcse[0],result_pcse[1],
            erc0,erc2,erc3,erc4,erc5,syscall(SYS_gettid),beat,heartbeat);if(fclose(f))return 14;
        FILE *response=fopen("/tmp/ipcm-response.tmp","w");if(!response)return 15;
        fprintf(response,"%u %s\n",seq,tag);if(fclose(response)||rename("/tmp/ipcm-response.tmp","/tmp/ipcm-response"))return 16;
    }
    running=0;pthread_join(thread,NULL);
    /* The isolated process owns synthetic library state. Skip unrelated IQ
     * process teardown registered by global constructors. */
    fflush(NULL);_exit(0);
}
