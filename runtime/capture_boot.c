/* An explicit simulator scenario: select stock capture memory mode when the
 * first liveview request arrives without a board-service boot-mode event.
 * This forwards buffer lookup and memory allocation to the stock library.
 * It does not fill buffers, bypass assertions or emulate ISP output.
 */
extern void *dlsym(void *, const char *);
extern int fprintf(void *, const char *, ...);
extern void *stderr;
extern void abort(void);
static void *next(const char *name) {
    void *function = dlsym((void *)-1, name);
    if (!function) { fprintf(stderr, "[BOOT INPUT] unresolved %s\n", name); abort(); }
    return function;
}
void liveview_address(void *, int, int, unsigned long *, unsigned long *)
    __asm__("_ZN15CLiveviewBuffer14getBaseAddressEiiPmS0_");
void liveview_address(void *self, int type, int index, unsigned long *a, unsigned long *b) {
    void *(*instance)(void) = next("_ZN17CMemoryManager_Nx11GetInstanceEv");
    int (*current)(void *) = next("_ZN14CMemoryManager13GetCurrentMapEv");
    void (*change)(void *, int) = next("_ZN17CMemoryManager_Nx10ChangeModeE19eMEMORY_MAP_MODE_ID");
    int (*select_pool)(const char *) = next("sysmmap_change_opmode");
    void (*show_pool)(int, const char *) = next("sysmmap_show");
    void (*original)(void *, int, int, unsigned long *, unsigned long *) = next("_ZN15CLiveviewBuffer14getBaseAddressEiiPmS0_");
    static int supplied;
    void *manager = instance();
    if (current(manager) == -1 && __sync_bool_compare_and_swap(&supplied, 0, 1)) {
        fprintf(stderr, "[BOOT INPUT] selecting stock capture memory mode=1; no sensor/ISP data supplied\n");
        int status = select_pool("capture");
        fprintf(stderr, "[BOOT INPUT] stock sysmmap capture selection status=%d\n", status);
        if (status == -5) {
            int intermediate = select_pool("web-b");
            fprintf(stderr, "[BOOT INPUT] stock web-b intermediate selection status=%d\n", intermediate);
            if (!intermediate) status = select_pool("capture");
            fprintf(stderr, "[BOOT INPUT] stock capture reselection status=%d\n", status);
        }
        show_pool(2, (void *)0);
        show_pool(3, "capture");
        if (!status) change(manager, 1);
        fprintf(stderr, "[BOOT INPUT] stock memory map after selection=%d\n", current(manager));
    }
    original(self, type, index, a, b);
}
