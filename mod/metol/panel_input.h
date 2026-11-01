#ifndef NX_PANEL_INPUT_H
#define NX_PANEL_INPUT_H
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <X11/Xlib.h>
#include <X11/keysym.h>
#include <X11/XF86keysym.h>

/* Match the camera's 32-bit evdev ABI without kernel headers. Both panels use
 * the same exclusive UI lock, so only the active panel opens this input. */
struct nx_panel_input_event { long tv_sec, tv_usec; unsigned short type, code; int value; };
struct nx_panel_input {
    Display *display;
    Window window, previous_focus, fallback_focus;
    int previous_revert, focus_saved, focus_restored, unmapped, ev_fd;
};
static int nx_panel_input_ignore_error(Display *display, XErrorEvent *event) {
    (void)display; (void)event; return 0;
}
static void nx_panel_input_init(struct nx_panel_input *input, Display *display, Window window) {
    input->display = display; input->window = window; input->ev_fd = -1;
    input->fallback_focus = None;
    input->previous_revert = RevertToPointerRoot;
    XErrorHandler old = XSetErrorHandler(nx_panel_input_ignore_error);
    XGetInputFocus(display, &input->previous_focus, &input->previous_revert);
    XSetErrorHandler(old);
    input->focus_saved = 1; input->focus_restored = input->unmapped = 0;
}
static inline void nx_panel_input_set_stock_parent(struct nx_panel_input *input, Window parent) {
    input->fallback_focus = parent;
    Window previous = input->previous_focus;
    int transient = 0;
    if (previous != None && previous != PointerRoot && previous != DefaultRootWindow(input->display)) {
        char *name = NULL;
        XErrorHandler old = XSetErrorHandler(nx_panel_input_ignore_error);
        XFetchName(input->display, previous, &name);
        transient = name && !strcmp(name, "Metol-NX300M");
        if (name) XFree(name);
        XSync(input->display, False); XSetErrorHandler(old);
    }
    /* The 1.5 s LUT notice may disappear while the interactive menu is open.
     * Restore the native camera, rather than that other MOD window's XID. */
    if (transient || previous == None || previous == PointerRoot || previous == DefaultRootWindow(input->display)) {
        input->previous_focus = parent;
        input->previous_revert = RevertToParent;
    }
    fprintf(stderr, "PANEL_FOCUS_SNAPSHOT captured=0x%lx restore=0x%lx fallback=0x%lx transient=%d\n",
        previous, input->previous_focus, parent, transient);
}
static void nx_panel_input_restore_focus(struct nx_panel_input *input) {
    if (!input->display || !input->focus_saved || input->focus_restored) return;
    input->focus_restored = 1;
    XErrorHandler old = XSetErrorHandler(nx_panel_input_ignore_error);
    Window current; int revert; XWindowAttributes attributes;
    XGetInputFocus(input->display, &current, &revert);
    /* A stock dialog may already have reclaimed focus. Closing this panel
     * must not move it back to an older stock window in that case. */
    if (current == input->window) {
        Window target = input->previous_focus;
        int target_revert = input->previous_revert;
        if (target == None || (target != PointerRoot &&
            (!XGetWindowAttributes(input->display, target, &attributes) || attributes.map_state != IsViewable))) {
            target = PointerRoot;
            if (input->fallback_focus != None && XGetWindowAttributes(input->display, input->fallback_focus, &attributes) &&
                    attributes.map_state == IsViewable) {
                target = input->fallback_focus;
                target_revert = RevertToParent;
            }
        }
        XSetInputFocus(input->display, target, target_revert, CurrentTime);
        fprintf(stderr, "PANEL_FOCUS_RETURN from=0x%lx to=0x%lx\n", current, target);
    } else fprintf(stderr, "PANEL_FOCUS_RETURN retained=0x%lx\n", current);
    XSync(input->display, False);
    XSetErrorHandler(old);
}
static void nx_panel_input_hide(struct nx_panel_input *input) {
    if (!input->display || input->unmapped) return;
    nx_panel_input_restore_focus(input);
    /* A focus-lost yield can race the window's own destruction; an unmap of
     * a dead window must not abort the panel through the default handler. */
    XErrorHandler old = XSetErrorHandler(nx_panel_input_ignore_error);
    XUnmapWindow(input->display, input->window);
    /* Fence the focus handoff before checking events still addressed here. */
    XSync(input->display, False);
    XSetErrorHandler(old);
    input->unmapped = 1;
}
static Bool nx_panel_input_is_s1(Display *display, XEvent *event, XPointer data) {
    const struct nx_panel_input *input = (const struct nx_panel_input *)data;
    (void)display;
    if (event->xany.window != input->window ||
        (event->type != KeyPress && event->type != KeyRelease)) return False;
    KeySym key = XLookupKeysym(&event->xkey, 0);
    return key == XK_Super_L || key == XF86XK_PowerOff || key == XF86XK_Standby || key == XF86XK_Sleep;
}
static void nx_panel_input_forward_s1(struct nx_panel_input *input, const XEvent *event) {
    Window target; int revert;
    if (event->type == KeyRelease && !input->focus_restored && !input->unmapped) {
        /* The shutter may already have been held when this panel opened.
         * Deliver the captured release without closing or taking its focus. */
        target = input->previous_focus;
    } else {
        nx_panel_input_restore_focus(input);
        XGetInputFocus(input->display, &target, &revert);
    }
    if (target == None || target == PointerRoot || target == input->window) return;
    /* Forward the captured shutter/power edge with its original keycode.
     * Do not synthesize an opposite edge or translate it into another key. */
    XEvent forwarded = *event;
    forwarded.xkey.window = target;
    XErrorHandler old = XSetErrorHandler(nx_panel_input_ignore_error);
    XSendEvent(input->display, target, True,
               event->type == KeyPress ? KeyPressMask : KeyReleaseMask, &forwarded);
    XSync(input->display, False);
    XSetErrorHandler(old);
}
static int nx_panel_input_handle_s1(struct nx_panel_input *input, XEvent *event, void (*close_panel)(void)) {
    if (!nx_panel_input_is_s1(input->display, event, (XPointer)input)) return 0;
    if (event->type == KeyPress) close_panel();
    nx_panel_input_forward_s1(input, event);
    return 1;
}
static void nx_panel_input_drain_s1(struct nx_panel_input *input, void (*close_panel)(void)) {
    XEvent event;
    while (XCheckIfEvent(input->display, &event, nx_panel_input_is_s1, (XPointer)input))
        nx_panel_input_handle_s1(input, &event, close_panel);
}
static void nx_panel_input_open_evdev(struct nx_panel_input *input) {
    input->ev_fd = open("/dev/input/event0", O_RDONLY | O_NONBLOCK);
    if (input->ev_fd >= 0) fcntl(input->ev_fd, F_SETFD, FD_CLOEXEC);
}
static void nx_panel_input_read_evdev(struct nx_panel_input *input, void (*close_panel)(void)) {
    struct nx_panel_input_event event;
    while (input->ev_fd >= 0) {
        ssize_t size = read(input->ev_fd, &event, sizeof event);
        if (size == (ssize_t)sizeof event) {
            /* EV_KEY/KEY_LEFTMETA press only. Physical evdev already reaches
             * stock firmware; this observer never injects an X11 edge. */
            if (event.type == 1 && (event.code == 125 || event.code == 116) && event.value == 1) close_panel();
        } else if (size < 0 && errno == EINTR) continue;
        else if (size < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) return;
        else {
            fprintf(stderr, "S1 input closed bytes=%ld errno=%d\n", (long)size, size < 0 ? errno : 0);
            close(input->ev_fd); input->ev_fd = -1;
        }
    }
}
static void nx_panel_input_close_evdev(struct nx_panel_input *input) {
    if (input->ev_fd >= 0) close(input->ev_fd);
    input->ev_fd = -1;
}
#endif
