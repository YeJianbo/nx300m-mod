/* NX300M 1.15: wrap Ecore's real key callbacks, leaving stock UI rendering intact.
 * Ecore_Event_Key starts with keyname, verified in UI_Event_Convert at 0x1a4070.
 * No executable patches or camera hardware accesses are performed here.
 */
extern void *dlsym(void *, const char *);
extern int fprintf(void *, const char *, ...);
extern int strcmp(const char *, const char *);
extern int setvbuf(void *, char *, int, unsigned int);
extern void *fopen(const char *, const char *);
extern int fscanf(void *, const char *, ...);
extern int fclose(void *);
extern void *stdout, *stderr;
#include "pw_control.h"
typedef int (*KeyCallback)(void *, int, void *);
typedef struct { const char *keyname; } KeyEvent;
typedef struct { KeyCallback callback; void *data; void *handler; } KeySlot;
static KeySlot slots[16];
static int key_down, key_up;

static int dial_locked(void) {
    int value = 0;
    void *file = fopen("/var/run/nx300m-dial-lock", "r");
    if (file) { if (fscanf(file, "%d", &value) != 1) value = 0; fclose(file); }
    return value == 1;
}
static int key_bridge(void *data, int type, void *event) {
    KeySlot *slot = data;
    const char *name = event ? ((KeyEvent *)event)->keyname : 0;
    int blocked = name && dial_locked() &&
        (!strcmp(name, "XF86ScrollUp") || !strcmp(name, "XF86ScrollDown"));
    fprintf(stderr, "[NX MOD] key type=%d name=%s blocked=%d\n", type, name ? name : "(null)", blocked);
    if (blocked) return 0;
    int result = slot->callback(slot->data, type, event);
    fprintf(stderr, "[NX MOD] key handled result=%d\n", result);
    return result;
}
void *ecore_event_handler_add(int type, KeyCallback callback, void *data) {
    void *(*original)(int, KeyCallback, void *) = dlsym((void *)-1, "ecore_event_handler_add");
    if (!key_down) {
        int *down = dlsym((void *)0, "ECORE_EVENT_KEY_DOWN");
        int *up = dlsym((void *)0, "ECORE_EVENT_KEY_UP");
        if (down) key_down = *down;
        if (up) key_up = *up;
    }
    if (type == key_down || type == key_up) {
        for (unsigned int i = 0; i < 16; i++) if (!slots[i].handler) {
            slots[i].callback = callback; slots[i].data = data;
            slots[i].handler = original(type, key_bridge, &slots[i]);
            return slots[i].handler;
        }
    }
    return original(type, callback, data);
}
void *ecore_event_handler_del(void *handler) {
    void *(*original)(void *) = dlsym((void *)-1, "ecore_event_handler_del");
    void *result = original(handler);
    for (unsigned int i = 0; i < 16; i++) if (slots[i].handler == handler) {
        result = slots[i].data; slots[i].handler = 0; break;
    }
    return result;
}
__attribute__((constructor)) static void initialize(void) {
    /* Stock stdout is otherwise block buffered, hiding the last UI transition. */
    setvbuf(stdout, (char *)0, 2, 0);
    fprintf(stderr, "[NX MOD] stock key bridge loaded; dial lock defaults off\n");
}
