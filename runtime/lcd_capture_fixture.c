/* Controlled forwarding fixture, never a physical framebuffer driver. */
typedef unsigned int u32;
extern int *__errno_location(void);
static int result, error_code, calls, maps;
void fixture_config(int value, int error) { result=value; error_code=error; }
int fixture_calls(void) { return calls; }
int fixture_maps(void) { return maps; }
int ioctl(int fd, u32 request, void *argument) {
    (void)fd;
    calls++;
    if (request==0x80047304U) { *(u32 *)argument=0xc6800000U; return 0; }
    if (request==0x80047301U) { *(u32 *)argument=0x18b00000U; return 0; }
    *__errno_location()=error_code;
    return result;
}
void *mmap64(void *address, u32 bytes, int prot, int flags, int fd, unsigned long long offset) {
    (void)address; (void)bytes; (void)prot; (void)flags; (void)fd; (void)offset;
    maps++; *__errno_location()=22; return (void *)-1;
}
