/* Exercise diagnostic sample failures with generated 16x8 planes only. */
#include "jpeg_cpu_adapter.c"
static unsigned char probe_y[128],probe_c[128],probe_output[65536];
static unsigned int probe_commits,probe_copies;
int nx300m_copy_live_virtual(uint32_t address,uint32_t length,void *output) {
    (void)address;(void)length;(void)output;++probe_copies;return -14;
}
uint64_t nx300m_live_virtual_generation(uint32_t address,uint32_t length,int writable) {
    return address==(uint32_t)(uintptr_t)probe_output && length==sizeof(probe_output) && writable?1:0;
}
int nx300m_write_live_virtual_if_generation(uint32_t address,uint32_t length,
        const void *input,uint64_t token) {
    if(address!=(uint32_t)(uintptr_t)probe_output || length>sizeof(probe_output) || token!=1) return -14;
    ++probe_commits;memcpy(probe_output,input,length);return 0;
}
int main(int argc,char **argv) {
    if(argc!=3) return 2;
    memset(probe_y,0x3c,sizeof(probe_y));memset(probe_c,0x66,sizeof(probe_c));
    memset(probe_output,0xa5,sizeof(probe_output));
    setenv("NX300M_DIAGNOSTIC_SAMPLE_DIR",argv[1],1);
    struct nx300m_jpeg_task task={0};
    task.width=16;task.height=8;task.stride_y=task.stride_c=16;
    task.y_even=(uint32_t)(uintptr_t)probe_y;task.cb_even=(uint32_t)(uintptr_t)probe_c;
    task.output=(uint32_t)(uintptr_t)probe_output;
    int error=nx_diagnostic_encode(&task,sizeof(probe_output));
    if(probe_copies) return 3;
    int expect_success=!strcmp(argv[2],"success");
    if(expect_success) {
        if(error || probe_commits!=1 || !task.size || probe_output[0]!=255 || probe_output[1]!=216) return 4;
    } else {
        if(!error || probe_commits || task.size) return 5;
        for(unsigned int i=0;i<sizeof(probe_output);++i) if(probe_output[i]!=0xa5) return 6;
    }
    for(unsigned int i=0;i<128;++i) if(probe_y[i]!=0x3c || probe_c[i]!=0x66) return 7;
    printf("{\"error\":%d,\"commits\":%u,\"bytes\":%u,\"live_snapshot_copies\":%u,\"source_unchanged\":true}\n",
           error,probe_commits,task.size,probe_copies);
    return 0;
}
