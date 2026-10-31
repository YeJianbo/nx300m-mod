/* Explicit private-VM fixture. Stock IQ/UDD/lens text stays unmodified.
 * Existing singleton slots point to synthetic objects and CS backing is RAM.
 * No pp_core_open, camera lifecycle, DMA completion or pixel claim. */
#include "manual_lens_cs_ram.h"
extern void *calloc(unsigned, unsigned), *memset(void *, int, unsigned);
extern void *fopen(const char *, const char *), *dlsym(void *, const char *);
extern unsigned fread(void *, unsigned, unsigned, void *), fwrite(const void *, unsigned, unsigned, void *);
extern int fclose(void *), fprintf(void *, const char *, ...), fscanf(void *, const char *, ...);
extern int snprintf(char *, unsigned, const char *, ...), rename(const char *, const char *);
extern int getpid(void), usleep(unsigned), strcmp(const char *, const char *);
extern long syscall(long, ...);
extern void *mmap(void *, unsigned, int, int, int, long);
extern int mprotect(void *, unsigned, int);
extern int pthread_create(unsigned long *, void *, void *(*)(void *), void *);
extern int pthread_join(unsigned long, void **);

#define UPDATE "_ZN15CPreProcessorD413UpdateCsParamEb"
#define INIT "_ZN15CPreProcessorD411InitCsParamEv"
#define IQ_SLOT "_ZN13CIpcSingletonI11CIQLensInfoE11m_pInstanceE"
#define PARAM_SLOT "_ZN13CIpcSingletonI12CIQParamInfoE11m_pInstanceE"
#define PP_SLOT "_ZN13CIpcSingletonI15CPreProcessorD4E11m_pInstanceE"
#define OP_SLOT "_ZN14CLensOperation11m_pInstanceE"
static unsigned char pp[128], iq[252], param[128], operation[128], backing[0x27c00];
static volatile unsigned running=1, heartbeat;
static unsigned iq_base;
static void put(unsigned char *p,unsigned offset,unsigned value) { *(unsigned *)(p+offset)=value; }
static unsigned get(const unsigned char *p,unsigned offset) { return *(const unsigned *)(p+offset); }
static void setup(unsigned mode) {
    memset(pp,0,sizeof pp);memset(iq,0,sizeof iq);memset(param,0,sizeof param);
    memset(operation,0,sizeof operation);memset(backing,0xa5,sizeof backing);
    put(pp,0,(unsigned)backing);put(pp,0x18,mode==3?3:1);put(pp,0x2c,1);
    *(unsigned short *)(pp+0x44)=128;*(unsigned short *)(pp+0x48)=128;
    put(operation,0x70,mode==2?12:0xffffffffU);
    /* Keep profile null: the genuine Update must take its safe early return
     * after the blob refuses a nonempty IQ-ready state. */
    if(mode==1) iq[8]=1;
}
static void *worker(void *unused) {
    (void)unused;while(running){++heartbeat;usleep(10000);}return (void *)0;
}
static struct nx_cs_ram_config *installed_config(void) {
    unsigned entry=*(unsigned *)(iq_base+NX_UPDATE_GOT);
    struct nx_cs_ram_header *h=(void *)(entry&~4095U);
    if(h->magic!=NX_CS_RAM_MAGIC||h->abi!=NX_CS_RAM_ABI)return (void *)0;
    return (void *)((unsigned)h+h->config_offset);
}
static int valid_tag(const char *p) {
    if(!*p)return 0;
    for(;*p;p++)if(!((*p>='a'&&*p<='z')||(*p>='0'&&*p<='9')||*p=='-'))return 0;
    return 1;
}
static int observe(const char *tag,unsigned update_tid,unsigned mode) {
    char path[256];snprintf(path,sizeof path,"/arm-vm/mfw-results/%s.fixture.json",tag);
    void *out=fopen(path,"w");if(!out)return 1;
    unsigned uniform=1,untouched=1;
    for(unsigned i=0;i<96;i+=2)if(*(unsigned short *)(backing+i)!=0x2000)uniform=0;
    for(unsigned i=0;i<96;i++)if(backing[i]!=0xa5)untouched=0;
    struct nx_cs_ram_config *c=installed_config();
    fprintf(out,"{\"mode\":%u,\"update_tid\":%u,\"heartbeat\":%u,\"flags\":%u,\"uniform_0x2000\":%u,\"backing_untouched\":%u,\"config\":[",
            mode,update_tid,heartbeat,get(pp,0x18),uniform,untouched);
    if(c)for(unsigned i=0;i<sizeof(*c)/4;i++)fprintf(out,"%s%u",i?",":"",((unsigned *)c)[i]);
    fprintf(out,"],\"slots\":[%u,%u,%u,%u]}\n",*(unsigned *)(iq_base+NX_UPDATE_GOT),
            *(unsigned *)(iq_base+NX_COMMON_GOT),*(unsigned *)(iq_base+NX_INFO_GOT),*(unsigned *)(iq_base+NX_ONOFF_GOT));
    return fclose(out);
}

/* Separate process policy-only success example: these callbacks are fakes.
 * It exercises blob admission/accounting, never stands in for stock success. */
static struct nx_cs_ram_config *policy_config;
static int (*policy_common_entry)(unsigned,const void *);
static void (*policy_info_entry)(const void *),(*policy_onoff_entry)(unsigned);
static unsigned policy_init_calls,policy_init_tid,policy_update_calls;
static int policy_common(unsigned kind,const void *p) {(void)kind;(void)p;return 0;}
static void policy_info(const void *p) {(void)p;}
static void policy_onoff(unsigned on) {(void)on;}
static unsigned policy_ready(void *p) {(void)p;return 0x20000018U;}
static void policy_update(void *p,unsigned force) {(void)p;(void)force;++policy_update_calls;}
static void policy_init(void *p) {
    unsigned source[3]={(unsigned)backing,96,10};(void)p;
    ++policy_init_calls;policy_init_tid=(unsigned)syscall(224);
    policy_common_entry(3,source);policy_info_entry(source);policy_onoff_entry(1);
}
static int policy(const char *path) {
    void *f=fopen(path,"rb");struct nx_cs_ram_header h;
    if(!f||fread(&h,1,sizeof h,f)!=sizeof h||h.magic!=NX_CS_RAM_MAGIC||h.config_size!=sizeof *policy_config)return 20;
    fclose(f);unsigned char *image=mmap(0,h.blob_size,3,0x22,-1,0);
    if(image==(void *)-1)return 21;
    f=fopen(path,"rb");if(!f||fread(image,1,h.blob_size,f)!=h.blob_size||fclose(f))return 22;
    if(mprotect(image,h.code_size,5))return 23;
    policy_config=(void *)(image+h.config_offset);struct nx_cs_ram_config *c=policy_config;
    unsigned iq_ptr=(unsigned)iq,pp_ptr=(unsigned)pp,op_ptr=(unsigned)operation;
    setup(0);c->enabled=1;c->original_update=(unsigned)policy_update;c->original_init=(unsigned)policy_init;
    c->original_common=(unsigned)policy_common;c->original_info=(unsigned)policy_info;c->original_onoff=(unsigned)policy_onoff;
    c->original_ready=(unsigned)policy_ready;c->iq_slot=(unsigned)&iq_ptr;c->pp_slot=(unsigned)&pp_ptr;
    c->operation_slot=(unsigned)&op_ptr;c->detached_state=0xffffffffU;
    policy_common_entry=(void *)(image+h.common_entry);policy_info_entry=(void *)(image+h.info_entry);
    policy_onoff_entry=(void *)(image+h.onoff_entry);
    void (*update)(void *,unsigned)=(void *)(image+h.update_entry);
    update(pp,0);if(policy_init_calls)return 24;
    c->request_action=NX_CS_DEFAULT;c->request_seq=1;
    if(policy_init_calls)return 25;
    unsigned caller=(unsigned)syscall(224);update(pp,0);update(pp,0);
    int passed=c->phase==NX_CS_SUBMITTED&&c->done_seq==1&&c->reason==NX_CS_OK&&
        policy_init_calls==1&&policy_update_calls==3&&policy_init_tid==caller&&c->update_tid==caller&&
        c->common_calls==1&&c->common_rc==0&&c->info_calls==1&&c->onoff_calls==1&&
        c->order==27&&c->info_rc==0xffffffffU&&c->onoff_rc==0xffffffffU;
    f=fopen("/arm-vm/mfw-results/policy.json","w");if(!f)return 26;
    fprintf(f,"{\"passed\":%u,\"fake_callbacks\":true,\"stock_success_verified\":false,\"phase\":%u,\"init_calls\":%u,\"update_tid\":%u,\"init_tid\":%u,\"common_rc\":%u,\"info_rc\":%u,\"onoff_rc\":%u}\n",
            passed,c->phase,policy_init_calls,c->update_tid,policy_init_tid,c->common_rc,c->info_rc,c->onoff_rc);
    fclose(f);return passed?0:27;
}

int main(int argc,char **argv) {
    if(argc==2&&!strcmp(argv[1],"--startup-check"))return 0;
    if(argc==3&&!strcmp(argv[1],"--policy"))return policy(argv[2]);
    void **slots[4]={dlsym(0,IQ_SLOT),dlsym(0,PARAM_SLOT),dlsym(0,PP_SLOT),dlsym(0,OP_SLOT)};
    void *objects[4]={iq,param,pp,operation};
    for(unsigned i=0;i<4;i++){if(!slots[i]||*slots[i])return 2;*slots[i]=objects[i];}
    void (*init)(void *)=dlsym(0,INIT);void *original_update=dlsym(0,UPDATE);
    int (*common)(unsigned,const void *)=dlsym(0,"pp_core_common_lut_init");
    if(!init||!original_update||!common)return 3;
    iq_base=(unsigned)original_update-NX_UPDATE_ADDRESS;
    /* Resolve just the reviewed Update PLT slot through the real dynamic
     * linker. Init below resolves its three UDD slots; unused lens methods
     * may depend on executable-only symbols, so global BIND_NOW is unsuitable. */
    setup(0);void (*prime_update)(void *,unsigned)=(void *)(iq_base+NX_UPDATE_PLT);
    prime_update(pp,0);
    unsigned source[3]={(unsigned)backing,96,10};int unopened_rc=common(3,source);
    init(pp);if(observe("baseline",(unsigned)syscall(224),0))return 4;
    void *proof=fopen("/arm-vm/mfw-results/stock-baseline.json","w");if(!proof)return 5;
    fprintf(proof,"{\"common_unopened_rc\":%d,\"real_stock_init_executed\":true,\"mode\":1,\"fixed_gain\":256,\"bytes\":96}\n",unopened_rc);fclose(proof);
    unsigned long thread;if(pthread_create(&thread,0,worker,0))return 6;
    void *ready=fopen("/arm-vm/mfw-results/fixture.ready","w");if(!ready)return 7;
    fprintf(ready,"%d\n",getpid());fclose(ready);
    unsigned last=0;
    while(running) {
        unsigned seq,mode;char tag[64];void *request=fopen("/var/run/cs-live-request","r");
        if(!request){usleep(10000);continue;}
        int parsed=fscanf(request,"%u %u %63s",&seq,&mode,tag);fclose(request);
        if(parsed!=3||seq<=last||mode>9||!valid_tag(tag)){usleep(10000);continue;}
        last=seq;if(mode==9){running=0;break;}setup(mode);
        unsigned caller=(unsigned)syscall(224);
        void (*update)(void *,unsigned)=*(void **)(iq_base+NX_UPDATE_GOT);update(pp,0);
        if(observe(tag,caller,mode))return 8;
        void *response=fopen("/var/run/cs-live-response.tmp","w");if(!response)return 9;
        fprintf(response,"%u %u %s\n",seq,caller,tag);
        if(fclose(response)||rename("/var/run/cs-live-response.tmp","/var/run/cs-live-response"))return 10;
    }
    pthread_join(thread,0);for(unsigned i=0;i<4;i++)*slots[i]=0;return 0;
}
