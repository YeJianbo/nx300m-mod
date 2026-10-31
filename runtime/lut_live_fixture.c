/* Explicit test process: genuine stock IQ vtable and UDD writer, RAM MMIO.
 * It is never installed under the original camera executable's identity.
 */
extern void *calloc(unsigned int, unsigned int);
extern void free(void *);
extern void *fopen(const char *, const char *);
extern unsigned int fwrite(const void *, unsigned int, unsigned int, void *);
extern int fclose(void *), fscanf(void *, const char *, ...);
extern int fprintf(void *, const char *, ...), snprintf(char *, unsigned int, const char *, ...);
extern int rename(const char *, const char *), getpid(void), usleep(unsigned int);
extern long syscall(long, ...);
extern void *memset(void *, int, unsigned int), *dlsym(void *, const char *);
extern int pthread_create(unsigned long *, void *, void *(*)(void *), void *);
extern int pthread_join(unsigned long, void **);
extern void lut_construct(void *) __asm__("_ZN12CIpcs3DLutD4C1Ev");
extern void lut_destroy(void *) __asm__("_ZN12CIpcs3DLutD4D1Ev");
extern void node_reset(void *) __asm__("_ZN17CIpcParameterNode5ResetEv");
static volatile unsigned running=1, heartbeat;
static void *worker(void *unused) {
    (void)unused;
    while(running) {++heartbeat;usleep(10000);}
    return (void *)0;
}
static int tag_valid(const char *s) {
    if(!*s)return 0;
    for(;*s;s++)if(!((*s>='a'&&*s<='z')||(*s>='0'&&*s<='9')||*s=='-'))return 0;
    return 1;
}
int main(void) {
    const char *out="/arm-vm/mfw-results";
    unsigned char *registers=calloc(1,0xb000);
    unsigned node[9]={0},params[39]={0};unsigned long thread;
    void **base=dlsym((void *)0,"ipcs_reg_base");
    void **memory_instance=dlsym((void *)0,"_ZN13CIpcSingletonI17CIpcMemoryManagerE11m_pInstanceE");
    unsigned memory[3]={0};
    if(!registers||!base||!memory_instance||*memory_instance)return 2;
    /* The unity-table branch uses MemoryManager's first scratch pointer.
     * Supply explicit RAM scratch instead of running its physical BMA/sysmmap
     * constructor. The original SetParam still generates every unity word. */
    memory[0]=(unsigned)calloc(1,16384);
    if(!memory[0])return 2;
    *memory_instance=memory;
    *base=registers;lut_construct(node);
    params[0x58/4]=9;params[0x40/4]=256;
    void *trace=fopen("/var/run/nx300m-lut-trace-enabled","w");
    if(!trace||fclose(trace))return 12;
    if(pthread_create(&thread,(void *)0,worker,(void *)0))return 3;
    void *ready=fopen("/arm-vm/mfw-results/fixture.ready","w");
    if(!ready)return 4;
    fprintf(ready,"%d %u\n",getpid(),heartbeat);fclose(ready);
    unsigned last=0;
    while(running) {
        unsigned seq=0,mode=0,reset=0;char tag[64];
        void *request=fopen("/var/run/lut-live-request","r");
        if(!request){usleep(10000);continue;}
        int parsed=fscanf(request,"%u %u %u %63s",&seq,&mode,&reset,tag);fclose(request);
        if(parsed!=4||seq<=last||mode>2||reset>1||!tag_valid(tag)){usleep(10000);continue;}
        last=seq;
        if(mode==2) {running=0;break;}
        ((unsigned char *)params)[0x83]=(unsigned char)mode;
        if(reset)node_reset(node);
        unsigned cache_before=node[1],beat_before=heartbeat;
        unsigned update_tid=(unsigned)syscall(224);
        memset(registers+0x9000,0,0x1600);
        void (**vtable)(void *,void *)=*(void (***)(void *,void *))node;
        vtable[0](node,params);
        char path[256];snprintf(path,sizeof path,"%s/%s.bin",out,tag);
        void *dump=fopen(path,"wb");if(!dump)return 5;
        for(unsigned c=0;c<3;c++)if(fwrite(registers+0x9000+c*0x700,1,1460,dump)!=1460)return 6;
        if(fclose(dump))return 7;
        snprintf(path,sizeof path,"%s/%s.fixture.json",out,tag);
        void *observation=fopen(path,"w");if(!observation)return 10;
        fprintf(observation,"{\"mode\":%u,\"update_tid\":%u}\n",mode,update_tid);
        if(fclose(observation))return 11;
        void *response=fopen("/var/run/lut-live-response.tmp","w");if(!response)return 8;
        fprintf(response,"%u %u %u %u %u %u %s\n",seq,mode,cache_before,node[1],beat_before,heartbeat,tag);
        if(fclose(response)||rename("/var/run/lut-live-response.tmp","/var/run/lut-live-response"))return 9;
    }
    running=0;pthread_join(thread,(void *)0);lut_destroy(node);
    *memory_instance=(void *)0;free((void *)memory[0]);free(registers);return 0;
}
