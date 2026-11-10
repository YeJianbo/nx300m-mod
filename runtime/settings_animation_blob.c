/* Freestanding NX300M 1.15 mode-animation callbacks. No exposure or IPCS code. */
#include "settings_animation_stock.h"
struct animation_config { unsigned magic, abi; volatile unsigned mode, active, active_mode; };
__attribute__((visibility("hidden"))) struct animation_config nx_animation_config={0x4e58414e,1,0,0,0};

/* Only these two reviewed, PC-independent A32 prologues are relocated. */
__attribute__((naked)) static void original_draw(void *self __attribute__((unused))) {
    __asm__ volatile(NX_DRAW_PROLOGUE "\nldr pc, 1f\n1: .word " NX_DRAW_RESUME);
}
__attribute__((naked)) static void original_start(void *self __attribute__((unused)), double interval __attribute__((unused))) {
    __asm__ volatile(NX_START_PROLOGUE "\nldr pc, 1f\n1: .word " NX_START_RESUME);
}
void nx_animation_start(void *self,double interval) {
    /* Suppress only a timer created by our own DrawModeDial invocation.
     * A preference change halfway through stock drawing cannot strand it. */
    if (__atomic_load_n(&nx_animation_config.active,__ATOMIC_ACQUIRE) &&
        __atomic_load_n(&nx_animation_config.active_mode,__ATOMIC_RELAXED)) {
        ((void (*)(void *))NX_STOP)(self);
        return;
    }
    original_start(self,interval);
}
void nx_animation_draw(void *self) {
    unsigned mode=__atomic_load_n(&nx_animation_config.mode,__ATOMIC_RELAXED);
    if (mode==2) {
        ((void (*)(void *))NX_STOP)(self);
        ((void (*)(void *))NX_CLEAR)(self);
        return;
    }
    __atomic_store_n(&nx_animation_config.active_mode,mode,__ATOMIC_RELAXED);
    __atomic_store_n(&nx_animation_config.active,1,__ATOMIC_RELEASE);
    original_draw(self);
    __atomic_store_n(&nx_animation_config.active,0,__ATOMIC_RELEASE);
    if (mode==1) {
        int x=0,y=0;
        ((void (*)(int,int *,int *,int *,int *))NX_GEOMETRY)(0x35c,&x,&y,(int *)0,(int *)0);
        ((void (*)(int,int,int))NX_MOVE)(0x35c,*((unsigned char *)self+8)?-22:103,y);
        ((void (*)(void *))NX_UPDATE)(self);
        *((int *)((char *)self+0x14))=18;
        ((void (*)(void *))NX_FRAME)(self);
    }
}
