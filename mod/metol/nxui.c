/* Adapted from WApaperplane/metol ddbe8b1, AGPL-3.0; see ATTRIBUTION.md.
 * NX300M port: rotated panel, stock key names, native LUT staging backend.
 * NX-KS menus + persistent FilmLab strip. Nuklear / CPU XImage, no EFL.
 * The viewfinder stays on its existing camera plane: no frame capture or blit.
 * Rendering is event driven; the only timer is backend/debounce housekeeping.
 */
#define _POSIX_C_SOURCE 200809L
#define _LARGEFILE64_SOURCE 1
#define NK_INCLUDE_FIXED_TYPES
#define NK_INCLUDE_STANDARD_IO
#define NK_INCLUDE_STANDARD_VARARGS
#define NK_INCLUDE_DEFAULT_ALLOCATOR
#define NK_IMPLEMENTATION
#include "vendor/nuklear.h"
#include "nxui_ximage.h"
#include <X11/keysym.h>
#include <errno.h>
#include <dirent.h>
#include <fcntl.h>
#include <signal.h>
#include <time.h>
#include <sys/file.h>
#include <sys/resource.h>
#include <sys/select.h>
#include <sys/stat.h>
#include <sys/prctl.h>
#include <sys/wait.h>
#include "nxui_model.h"
#include "panel_input.h"
#include "nxui_services.h"
#include "mod_paths.h"
#include "lut_tune.h"
#include "nx_atomic_write.h"
#include "lut_reference_interp.h"
#include "nxui_preview_state.h"
extern Bool XTestFakeKeyEvent(Display *, unsigned int, Bool, unsigned long);
/* glibc 2.13 exposes the versioned ARM stat64 entry points. */
extern int __xstat64(int, const char *, struct stat64 *);
extern int __lxstat64(int, const char *, struct stat64 *);

static struct nx_recipe recipes[NXUI_MAX_RECIPES];
static struct nx_item items[NXUI_MAX_ITEMS];
static struct nx_queue queue;
static const char *root, *engine, *arm_runner;
static int count, item_count, selected, menu, page, live, dirty = 1, quit;
/* A successful LUT choice stays selected when the panel closes. */
static int lut_mode = 0;
static int mfw_services, mfw_selected, mfw_readonly, mfw_open_lut, mfw_preview, mfw_quick;
static const char *mfw_menu_root, *mfw_menu_engine;
static int mfw_lut_quick, mfw_return_requested;
static char mfw_lut_root[NXUI_PATH], mfw_lut_engine[NXUI_PATH];
static long long lut_page_requested;
/* Continuous-input rasterization gate: paints are held to ~15 Hz during a
 * drag/wheel burst; 0 means no hold (single keys paint immediately). */
static long long lut_paint_hold;
static const char *mfw_stock_key;
static char mfw_status[1024];
/* An applied control with a failed preference write must retry the same value,
 * rather than toggling the now-changed firmware state back again. */
static char mfw_fw_action[64],mfw_fw_retry[4][64];
static int mfw_fw_slot(const char *action) {
    if(!strncmp(action,"fw-mode-lock",12))return 0;
    if(!strncmp(action,"fw-animation",12))return 1;
    if(!strncmp(action,"fw-auto-iso-max",15))return 3;
    if(!strncmp(action,"fw-auto-iso",11))return 2;
    return -1;
}
static int lut_strength = 100;
static int lut_draft;
static int lut_leave_dialog,lut_leave_target,lut_leave_option,lut_submit_failed,lut_saved_config;
static int lut_exit_after_save;
/* Leave intent scheduled behind a queued/in-flight request: after the current
 * task completes, all remaining drafts flush and only full success honors
 * this target (1=quit to viewfinder, 2=return to menu). 0=none scheduled. */
static int lut_leave_flush_after;
/* Favorite feedback state, defined here because leave/switch paths above the
 * panel header include clear it. Distinct from lut_submit_failed. */
static int lut_favorite_notice;
static long long lut_apply_tick;
/* lut_origin_* is the last request the camera accepted, not a row number. */
static int lut_origin_selected,lut_origin_strength;
static struct nx_lut_tune lut_origin_tune;
static int lut_reference_scene,lut_reference_original,lut_reference_press,lut_list_open,lut_list_cursor;
static int lut_fn_down;
static int lut_tab_start=-1;
static long long lut_reference_pressed_at;
static void resize_panel(void);
static struct nx_lut_tune lut_tune;
static int lut_edit, lut_field, lut_category, lut_category_focus, lut_reference_dirty=1;
static int lut_toolbar_focus=-1;
static int lut_preview_shown=-1;
static long long lut_preview_due,lut_preview_until;
static int lut_brand[NXUI_MAX_RECIPES],lut_favorite[NXUI_MAX_RECIPES];
static const char *lut_categories[]={"全部","收藏","富士","奥巴","宾得","松下","理光","索尼","自制","胶片模拟"};
enum { NX_LUT_CATEGORY_COUNT=sizeof lut_categories/sizeof *lut_categories };
static int lut_category_count[NX_LUT_CATEGORY_COUNT];
/* Per-look edit drafts keyed by the stable look id, never by row order. Only
 * looks whose buffer differs from their persisted baseline occupy a slot. */
#define NX_LUT_DRAFT_MAX 64
struct nx_lut_look_draft { char key[64]; struct nx_lut_tune tune; int strength,durability_pending; };
static struct nx_lut_look_draft lut_look_drafts[NX_LUT_DRAFT_MAX];
static int lut_look_draft_count;
static struct nx_lut_tune lut_base_tune;  /* persisted baseline of the selected look */
static int lut_base_strength;
/* Confirmed LUT requests are immutable snapshots: pending = confirmed and
 * waiting for dispatch, inflight = owned by the running child. Parameters are
 * copied at confirm time and never re-read from the live edit buffer. */
struct nx_lut_request { char key[64]; struct nx_lut_tune tune; int strength,exit_after,is_off; unsigned generation; };
static struct nx_lut_request lut_request_pending;
static struct nx_lut_request lut_request_inflight;
static int lut_request_pending_valid,lut_request_inflight_valid;
static unsigned lut_request_serial;
static void lut_load_tune(void);
static void lut_init_groups(void);
static void lut_step(int delta);
static void confirm_lut(int target);
static void lut_request_leave(int target);
static void lut_save_navigation(void);
static int lut_field_supported(int field);
static int lut_in_group(int index);
static void feature_snapshot(void);
static void message(const char *s);
static int lut_draft_slot(const char *key);
static int lut_current_dirty(void) {
    if(selected<=0)return 0;
    int slot=lut_draft_slot(recipes[selected].key);
    return lut_strength!=lut_base_strength||memcmp(&lut_tune,&lut_base_tune,sizeof lut_tune)||
        (slot>=0&&lut_look_drafts[slot].durability_pending);
}
static int lut_has_changes(void) {
    return lut_current_dirty()||lut_look_draft_count>0;
}
static int lut_matches_applied(void) {
    return selected==lut_origin_selected&&(!selected||
        (lut_strength==lut_origin_strength&&!memcmp(&lut_tune,&lut_origin_tune,sizeof lut_tune)));
}
static void close_choice(void) {
    if (lut_mode && lut_has_changes() && !queue.close_requested) lut_request_leave(1);
    else queue.close_requested = 1;
}
static int lut_draft_slot(const char *key) {
    for(int i=0;i<lut_look_draft_count;i++)if(!strcmp(lut_look_drafts[i].key,key))return i;
    return -1;
}
static void lut_draft_drop(const char *key) {
    int slot=lut_draft_slot(key);
    if(slot<0)return;
    memmove(lut_look_drafts+slot,lut_look_drafts+slot+1,(size_t)(lut_look_draft_count-slot-1)*sizeof *lut_look_drafts);
    lut_look_draft_count--;
}
/* Write one draft to the card with the same atomic layout the engine reads.
 * Keys that cannot be a safe filename stay memory-only. */
static int lut_draft_persist_one(struct nx_lut_look_draft *draft) {
    int saved=nx_tune_preset_save(root,draft->key,&draft->tune,(unsigned)draft->strength);
    if(saved)draft->durability_pending=saved!=1;
    if(saved!=1)fprintf(stderr,"LUT_DRAFT_PERSIST key=%s saved=%d\n",draft->key,saved);
    return saved==1;
}
/* Persist every stashed draft; successfully saved ones leave the table. */
static int lut_drafts_flush(void) {
    int failed=0;
    for(int i=0;i<lut_look_draft_count;) {
        if(lut_draft_persist_one(&lut_look_drafts[i]))
            lut_draft_drop(lut_look_drafts[i].key);
        else {failed++;i++;}
    }
    return failed;
}
/* H2: the engine reported PERSIST-UNCONFIRMED — content was published by
 * rename but directory durability is unproven. Keep the request's own draft
 * with durability_pending so an identical retry performs a real write, and
 * never treat the save as confirmed. */
static void lut_draft_mark_unconfirmed(const struct nx_lut_request *request) {
    int slot;
    if(request->is_off||strchr(request->key,'/')||strstr(request->key,".."))return;
    slot=lut_draft_slot(request->key);
    if(slot<0) {
        if(lut_look_draft_count>=NX_LUT_DRAFT_MAX)return;
        slot=lut_look_draft_count++;
        snprintf(lut_look_drafts[slot].key,sizeof lut_look_drafts[slot].key,"%s",request->key);
    }
    lut_look_drafts[slot].tune=request->tune;lut_look_drafts[slot].strength=request->strength;
    lut_look_drafts[slot].durability_pending=1;
    if(!strcmp(recipes[selected].key,request->key))lut_draft=1;
}
static void lut_baseline_load(int index) {
    struct nx_lut_tune disk={{0}};unsigned strength=100;
    if(index>0)nx_tune_load(root,recipes[index].key,&disk,&strength);
    lut_base_tune=disk;lut_base_strength=(int)strength;
}
/* Stash the selected look's edit buffer when navigation moves elsewhere. A
 * buffer matching its persisted baseline removes any draft instead. */
static int lut_draft_stash(void) {
    if(!lut_mode||selected<=0)return 1;
    const char *key=recipes[selected].key;
    int slot=lut_draft_slot(key);
    if(lut_strength==lut_base_strength&&!memcmp(&lut_tune,&lut_base_tune,sizeof lut_tune)&&
       !(slot>=0&&lut_look_drafts[slot].durability_pending)) {
        lut_draft_drop(key);return 1;
    }
    if(slot<0) {
        if(lut_look_draft_count>=NX_LUT_DRAFT_MAX) {
            /* Retain every edit if the card cannot accept the oldest one.
             * Navigation stays on the current buffer until saving can retry. */
            if(!lut_draft_persist_one(&lut_look_drafts[0])) {
                fprintf(stderr,"LUT_DRAFT_OVERFLOW retained key=%s\n",lut_look_drafts[0].key);
                message("草稿已满且保存未确认 · 请先保存或放弃调整");
                return 0;
            }
            lut_draft_drop(lut_look_drafts[0].key);
        }
        slot=lut_look_draft_count++;
        lut_look_drafts[slot].durability_pending=0;
        snprintf(lut_look_drafts[slot].key,sizeof lut_look_drafts[slot].key,"%s",key);
    }
    lut_look_drafts[slot].tune=lut_tune;lut_look_drafts[slot].strength=lut_strength;
    return 1;
}
/* Keep the outgoing look's edit, then restore the incoming look's own draft
 * or its persisted values. Browsing never commits anything to the camera.
 * The persisted state is read once per switch and doubles as the baseline. */
static int lut_select_look(int index) {
    if(index==selected)return 1;
    if(!lut_draft_stash())return 0;
    selected=index;
    struct nx_lut_tune disk={{0}};unsigned strength=100;
    if(index>0)nx_tune_load(root,recipes[index].key,&disk,&strength);
    lut_base_tune=disk;lut_base_strength=(int)strength;
    int slot=index>0?lut_draft_slot(recipes[index].key):-1;
    if(slot>=0) {
        lut_tune=lut_look_drafts[slot].tune;lut_strength=lut_look_drafts[slot].strength;
        fprintf(stderr,"LUT_DRAFT_RESTORE key=%s strength=%d values=%d,%d,%d,%d,%d,%d,%d\n",recipes[index].key,lut_strength,
            lut_tune.value[0],lut_tune.value[1],lut_tune.value[2],lut_tune.value[3],
            lut_tune.value[4],lut_tune.value[5],lut_tune.value[6]);
    } else {lut_tune=disk;lut_strength=(int)strength;}
    lut_reference_dirty=1;
    if(!lut_current_dirty()&&selected>0)lut_draft_drop(recipes[selected].key);
    lut_draft=lut_current_dirty();
    lut_favorite_notice=0;
    return 1;
}
static int lut_request_index(const struct nx_lut_request *request) {
    for(int i=0;i<count;i++)if(!strcmp(recipes[i].key,request->key))return i;
    return -1;
}
/* Parameter identity only: exit intent and generation may differ on reuse. */
static int lut_request_same(const struct nx_lut_request *a,const struct nx_lut_request *b) {
    return !strcmp(a->key,b->key)&&a->strength==b->strength&&a->is_off==b->is_off&&
        !memcmp(&a->tune,&b->tune,sizeof a->tune);
}
static int touch_x, touch_active;
static long long close_flag_check;
static struct nx_panel_input panel_input = {.ev_fd = -1};
static int busy_kind, child_fd = -1, output_len, child_timed_out, child_slow;
static pid_t child;
static pid_t mfw_retired_child;
static int mfw_child_readonly, mfw_child_preview;
static long long mfw_cancel_started;
static long long child_started, lifetime;
static long long service_due;
static struct nx_services services;
static const char *net_dir = "/proc/net";
static int backend_phase, busy_recipe = -1, pipe_eof;
static char output[4096], status[384] = "选择配方以预览 · 关闭后保留当前会话设置";
static char menu_path[NXUI_PATH];
static char pending_menu[NXUI_PATH];
static volatile sig_atomic_t interrupted;
static Display *display;
static Window window, panel_parent;
static int screen, width, height, panel_parent_height, panel_focus_revert = RevertToPointerRoot;
static struct nk_context *ctx;
static XFont *hint_font;
static const struct nk_user_font *panel_hint_font(void) {
    return hint_font?&hint_font->handle:ctx->style.font;
}
static int panel_visibility = VisibilityFullyObscured, panel_raise_retry, panel_yielded;
/* Do not rely on a window manager to restore input after this panel closes.
 * Stock windows can also raise themselves or reclaim focus during a transition.
 * Camera navigation arrives as X KeyPress; an invisible panel must yield it. */
static void request_s1_close(void) {
    /* Shutter/focus preemption discards local edits and yields immediately.
     * The discard scope is explicit: every per-look draft, not just the
     * displayed one. */
    panel_yielded = 1;
    lut_leave_dialog=lut_draft=0;lut_look_draft_count=0;
    queue.close_requested=1;
    nx_panel_input_hide(&panel_input);
}

static long long now_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (long long)t.tv_sec * 1000 + t.tv_nsec / 1000000;
}
static void panel_stage(const char *stage) {
    fprintf(stderr, "PANEL_STAGE pid=%ld name=%s at_ms=%lld\n", (long)getpid(), stage, now_ms());
}
static void panel_visibility_event(const XEvent *event) {
    panel_visibility = event->xvisibility.state;
    fprintf(stderr, "PANEL_VISIBILITY state=%d serial=%lu\n", panel_visibility, event->xany.serial);
}
static void panel_drain_visibility(void) {
    XEvent event;
    while (XCheckTypedWindowEvent(display, window, VisibilityNotify, &event)) panel_visibility_event(&event);
}
static int panel_is_stock_parent(Window candidate) {
    XClassHint hint; XWindowAttributes attributes;
    memset(&hint, 0, sizeof hint);
    int match = 0;
    if (XGetClassHint(display, candidate, &hint)) {
        match = (hint.res_name && (!strcmp(hint.res_name, "di-camera-app") || !strcmp(hint.res_name, "di-camera-app-nx300"))) ||
            (hint.res_class && (!strcmp(hint.res_class, "di-camera-app") || !strcmp(hint.res_class, "di-camera-app-nx300")));
        if (hint.res_name) XFree(hint.res_name);
        if (hint.res_class) XFree(hint.res_class);
    }
    if (!match || !XGetWindowAttributes(display, candidate, &attributes) || attributes.map_state != IsViewable ||
            attributes.class != InputOutput || attributes.depth != DefaultDepth(display, screen) ||
            attributes.width < height || attributes.height < width) return 0;
    panel_parent_height = attributes.height;
    return 1;
}
static Window panel_stock_in_stack(Window parent, unsigned depth) {
    Window tree_root, returned_parent, *children = NULL, found = None; unsigned count = 0;
    if (depth >= 4 || !XQueryTree(display, parent, &tree_root, &returned_parent, &children, &count)) return None;
    /* XQueryTree orders siblings bottom to top. Prefer the currently raised
     * stock window when a dismissed transient left X focus at PointerRoot. */
    for (unsigned i = count; i > 0 && found == None; i--) {
        XWindowAttributes attributes;
        Window candidate = children[i-1];
        if (!XGetWindowAttributes(display, candidate, &attributes) || attributes.map_state != IsViewable) continue;
        if (panel_is_stock_parent(candidate)) found = candidate;
        else found = panel_stock_in_stack(candidate, depth+1);
    }
    if (children) XFree(children);
    return found;
}
static Window panel_stock_parent(void) {
    Window candidate, focus; int revert;
    XGetInputFocus(display, &focus, &revert);
    candidate = focus;
    XErrorHandler old = XSetErrorHandler(nx_panel_input_ignore_error);
    Window found = None;
    const char *source = "focus";
    for (unsigned depth = 0; depth < 16 && candidate != None && candidate != PointerRoot &&
            candidate != RootWindow(display, screen); depth++) {
        if (panel_is_stock_parent(candidate)) { found = candidate; break; }
        Window tree_root, parent, *children = NULL; unsigned count;
        if (!XQueryTree(display, candidate, &tree_root, &parent, &children, &count)) break;
        if (children) XFree(children);
        candidate = parent;
    }
    /* An actual focused foreign dialog still owns the display. Only recover
     * through the stock window tree when X has no concrete focused client. */
    if (found == None && (focus == None || focus == PointerRoot || focus == RootWindow(display, screen))) {
        source = "stock-stack";
        found = panel_stock_in_stack(RootWindow(display, screen), 0);
    }
    fprintf(stderr, "PANEL_PARENT_LOOKUP source=%s focus=0x%lx stock=0x%lx\n", source, focus, found);
    XSync(display, False); XSetErrorHandler(old);
    return found;
}
static void panel_window_trace(const char *stage) {
    Window current, tree_root, parent, *children = NULL; unsigned count = 0, above = 0;
    int revert, found = 0; XWindowAttributes attributes;
    XErrorHandler old = XSetErrorHandler(nx_panel_input_ignore_error);
    XGetInputFocus(display, &current, &revert);
    if (current != None && current != PointerRoot) {
        char *name = NULL; XClassHint hint;
        memset(&hint, 0, sizeof hint);
        XFetchName(display, current, &name); XGetClassHint(display, current, &hint);
        fprintf(stderr, "PANEL_FOCUS_TARGET stage=%s window=0x%lx name=%.96s class=%.96s\n",
            stage, current, name ? name : "", hint.res_class ? hint.res_class : "");
        if (name) XFree(name);
        if (hint.res_name) XFree(hint.res_name);
        if (hint.res_class) XFree(hint.res_class);
    }
    memset(&attributes, 0, sizeof attributes);
    XGetWindowAttributes(display, window, &attributes);
    if (XQueryTree(display, panel_parent, &tree_root, &parent, &children, &count)) {
        for (unsigned i = 0; i < count; i++) {
            if (children[i] == window) {found = 1; continue;}
            if (found) {
                XWindowAttributes sibling;
                if (XGetWindowAttributes(display, children[i], &sibling) && sibling.map_state == IsViewable) above++;
            }
        }
        if (children) XFree(children);
    }
    XSync(display, False);
    XSetErrorHandler(old);
    fprintf(stderr, "PANEL_WINDOW stage=%s window=0x%lx map=%d visibility=%d focus=0x%lx previous=0x%lx mapped_above=%u depth=%d\n",
        stage, window, attributes.map_state, panel_visibility, current, panel_input.previous_focus, above, attributes.depth);
}
static int panel_show(void) {
    panel_visibility = VisibilityFullyObscured;
    panel_input.unmapped = 0;
    XMapRaised(display, window);
    XSetClipMask(display, xlib.surf->gc, None);
    nk_xsurf_blit(window, xlib.surf, (unsigned)width, (unsigned)height);
    panel_stage("visible-draw-fence-begin");
    XSync(display, False);
    panel_drain_visibility();
    panel_window_trace("before-focus");
    /* XSync confirms request processing, not window visibility or LCD scanout.
     * A hidden window must not take the camera's keyboard focus. */
    if (panel_visibility != VisibilityUnobscured && !panel_raise_retry++) {
        panel_stage("entry-single-raise");
        XRaiseWindow(display, window); XSync(display, False);
        panel_drain_visibility();
        panel_window_trace("entry-retry");
        if (panel_visibility == VisibilityUnobscured) {
            nk_xsurf_blit(window, xlib.surf, (unsigned)width, (unsigned)height);
            XSync(display, False);
        }
    }
    if (panel_visibility != VisibilityUnobscured) return 0;
    panel_stage("visible-draw-ready");
    panel_input.unmapped = panel_input.focus_restored = 0;
    XSetInputFocus(display, window, panel_focus_revert, CurrentTime);
    panel_stage("focus-fence-begin");
    XSync(display, False);
    panel_drain_visibility();
    panel_window_trace("after-focus");
    panel_stage("focus-ready");
    return panel_visibility == VisibilityUnobscured;
}
static void panel_check_visibility(void) {
    if (panel_input.unmapped || queue.close_requested || panel_visibility == VisibilityUnobscured) return;
    Window current; int revert;
    XGetInputFocus(display, &current, &revert);
    /* A native MENU transition can raise its window just after ours maps.
     * Retry once, only while we still own focus. Never fight a stock dialog
     * that has taken focus, and never maintain a periodic raise loop. */
    if (current == window && !panel_raise_retry++) {
        panel_stage("occlusion-single-raise");
        XRaiseWindow(display, window); XSync(display, False);
        panel_drain_visibility();
        panel_window_trace("occlusion-retry");
        if (panel_visibility == VisibilityUnobscured) {
            XSetClipMask(display, xlib.surf->gc, None);
            nk_xsurf_blit(window, xlib.surf, (unsigned)width, (unsigned)height);
            XFlush(display); return;
        }
    }
    panel_stage("yield-obscured");
    request_s1_close();
}
static void log_resources(const char *stage) {
    struct rusage usage;
    if (!getrusage(RUSAGE_SELF, &usage))
        fprintf(stderr, "PANEL_RESOURCE pid=%ld stage=%s user_us=%lld system_us=%lld maxrss_kb=%ld voluntary=%ld involuntary=%ld\n",
            (long)getpid(), stage,
            (long long)usage.ru_utime.tv_sec*1000000 + usage.ru_utime.tv_usec,
            (long long)usage.ru_stime.tv_sec*1000000 + usage.ru_stime.tv_usec,
            usage.ru_maxrss, usage.ru_nvcsw, usage.ru_nivcsw);
}
static long long uptime_elapsed_ms(const char *value, long long boot_ms) {
    char *end; double seconds;
    if (!value || boot_ms < 0) return -1;
    errno = 0; seconds = strtod(value, &end);
    if (errno || end == value || *end || !(seconds >= 0 && seconds <= boot_ms/1000.0)) return -1;
    return boot_ms - (long long)(seconds*1000);
}
static void on_signal(int sig) { (void)sig; interrupted = 1; }
/* A stuck X11 request must not retain this panel indefinitely. Do not call
 * Xlib from a signal handler; closing the client destroys its own window.
 * This bounds a client wait, not a hung kernel/display server. */
static void on_display_timeout(int sig) {
    static const char message[] = "PANEL_DISPLAY_TIMEOUT exit=124\n";
    (void)sig; write(STDERR_FILENO, message, sizeof message-1); _exit(124);
}
static void on_child(int sig) { (void)sig; } /* interrupt select; waitpid stays in the main loop */
static void message(const char *s) { snprintf(status, sizeof status, "%s", s); dirty = 1; }
static void mfw_status_message(const char *s) {
    snprintf(mfw_status, sizeof mfw_status, "%s", s);
    message("");
    fprintf(stderr, "MFW status=%s\n", mfw_status);
    resize_panel();
}
static const char *base_name(const char *p) {
    const char *last = strrchr(p, '/'); return last ? last + 1 : p;
}
static int services_visible(void) {
    int i;
    if (!menu) return 0;
    for (i = page*6; i < item_count && i < page*6+6; i++)
        if (!strcmp(base_name(items[i].command), "telnet_toggle.sh")) return 1;
    return 0;
}
static void refresh_services(int force) {
    struct nx_services next; char label[192];
    if (!force && (!services_visible() || now_ms() < service_due)) return;
    nx_services_read(&next, net_dir);
    service_due = now_ms() + 1000;
    if (!nx_services_equal(&services, &next)) {
        services = next; dirty = 1;
        nx_services_label(&services, label, sizeof label);
        fprintf(stderr, "SERVICES known=%d telnet=%d ftp=%d label=%s\n", services.known, services.telnet, services.ftp, label);
    }
}
static void preview_status(void) {
    if (lut_mode && !lut_matches_applied() && !queue.close_requested)
        snprintf(status, sizeof status, "已选择 %s · 按 OK 应用", recipes[selected].label);
    else if (child && busy_kind == 1 && queue.desired >= 0 && queue.desired != queue.running)
        snprintf(status, sizeof status, "已选择 %s · 等待当前预览完成", recipes[queue.desired].label);
    else if (child && busy_kind == 1 && busy_recipe >= 0)
        snprintf(status, sizeof status, "%s：%s", backend_phase == 2 ? "正在确认预览" : "正在应用", recipes[busy_recipe].label);
    else snprintf(status, sizeof status, "%s：%s", live ? "准备预览" : "演示选择", recipes[selected].label);
    dirty = 1;
}
static int checked(const char *name) {
    const char *base = base_name(name);
    char path[NXUI_PATH];
    /* This is the existing instruction wheel lock, separate from the physical
     * mode dial. Other acknowledged controls use explicit actions, not markers. */
    if (!strcmp(base, "toggle_dial_lock.sh")) {
        FILE *f = fopen("/var/run/nx300m-dial-lock", "r");
        if (f) { int v = 0; if (fscanf(f, "%d", &v) == 1) { fclose(f); return v == 1; } fclose(f); }
        return 0;
    }
    snprintf(path, sizeof path, "%s/auto/%s", root, base);
    return access(path, F_OK) == 0;
}
static void resize_panel(void) {
    int next_width = mfw_quick ? 560 : 800;
    if (next_width > panel_parent_height) next_width = panel_parent_height;
    int next = mfw_services ? (mfw_quick ? 240 : mfw_preview>=3?480:(*mfw_status ? 336 : mfw_preview?296:264)) : (menu ? 336 : (lut_mode ? 480 : 216));
    if (next > DisplayWidth(display, screen)) next = DisplayWidth(display, screen);
    dirty = 1;
    if (next == height && next_width == width) return;
    height = next; width = next_width;
    XMoveResizeWindow(display, window, mfw_quick ? (DisplayWidth(display,screen)-height)/2 : 0, (panel_parent_height-width)/2,
        (unsigned)height, (unsigned)width);
    /* Resize only our CPU image alongside the native window. */
    nk_xsurf_resize(xlib.surf, (unsigned)width, (unsigned)height);
    dirty = 1;
}
#include "nxui_prefs.h"
static int load_menu(const char *source) {
    static struct nx_item pending[NXUI_MAX_ITEMS];
    char next[NXUI_PATH]; int n;
    if (!nx_menu_path(root, source, next, sizeof next) ||
        (n = nx_load_menu(next, pending, NXUI_MAX_ITEMS)) <= 0) {
        message("菜单读取失败 · 详情见日志"); return 0;
    }
    memcpy(items, pending, (size_t)n * sizeof *items);
    snprintf(menu_path, sizeof menu_path, "%s", next);
    item_count = n; page = 0; menu = 1;
    fprintf(stderr, "MENU file=%s items=%d\n", menu_path, item_count);
    resize_panel(); return 1;
}
/* This only reads the catalogue and choice. Loading the adapter, restoring a
 * saved look and synchronizing preview belong to explicit control events. */
static int load_lut_choices(const char *path) {
    int n = nx_load_recipes(path, recipes+1, NXUI_MAX_RECIPES-1);
    if (n < 0) return 0;
    memset(recipes, 0, sizeof *recipes);
    strcpy(recipes[0].key, "__off"); strcpy(recipes[0].label, "关");
    count = n+1; selected = lut_draft = lut_edit = lut_category_focus = 0; lut_strength = 100;lut_toolbar_focus=-1;
    lut_preview_shown=-1;lut_preview_due=lut_preview_until=0;
    memset(&lut_tune,0,sizeof lut_tune);
    nx_queue_init(&queue);
    snprintf(status, sizeof status, "当前选择：关 · 原厂拍摄");
    char session[NXUI_PATH] = "", choice[NXUI_PATH];
    FILE *in = fopen("/var/run/nx300m-lut-selection-session", "r");
    int session_present=in!=NULL;
    if(in){if(!fgets(session,sizeof session,in))session[0]=0;fclose(in);}
    session[strcspn(session,"\r\n")]=0;
    int own_session=session_present&&!strcmp(session,root);
    FILE *selection = (!session_present||own_session)?fopen("/var/run/nx300m-lut-selection", "r"):NULL;
    int saved = 0;
    if (!selection) {
        if (!own_session) {
            snprintf(choice, sizeof choice, "%s/.state/lut-choice", root);
            selection = fopen(choice, "r");
            if (!selection) {
                snprintf(choice, sizeof choice, "%s/.state/lut-selection", root);
                selection = fopen(choice, "r");
            }
            saved = 1;
        }
    }
    int matched=0;
    if (selection) {
        unsigned int generation, strength = 100; char table[256], candidate[256], base_id[64]="";
        if (fscanf(selection, "%u %255s %u", &generation, table, &strength) >= 2) {
            char *slash=strrchr(table,'/');
            if(!strncmp(table,root,strlen(root))&&table[strlen(root)]=='/'&&slash && !strcmp(slash,"/table.d4lut")) {
                char meta[512];snprintf(meta,sizeof meta,"%.*s/tune.txt",(int)(slash-table),table);
                nx_tune_read(meta,base_id,&lut_tune,NULL);
            }
            for (int i = 1; i < count; i++) {
                int length = snprintf(candidate, sizeof candidate, "%s/looks/%.63s/table.d4lut", root, recipes[i].key);
                if (length < 0 || length >= (int)sizeof candidate) continue;
                if (!strcmp(table, candidate) || !strcmp(base_id,recipes[i].key)) {
                    selected = i; matched=1; lut_strength = strength <= 100 ? (int)strength : 100;
                    snprintf(status, sizeof status, "%s %s · 强度 %d%%%s", saved ? "上次选择" : "当前选择",
                        recipes[i].label, lut_strength, saved ? " · 按 OK 应用" : "");
                    break;
                }
            }
        }
        fclose(selection);
    }
    /* Direct tables mean zero adjustments, including when a different preset
     * has since been saved with Fn. Never infer the active parameters from
     * that preset. Snapshot the camera choice before restoring edit drafts. */
    lut_origin_selected=saved?-1:selected;
    lut_origin_strength=lut_strength;lut_origin_tune=lut_tune;
    if(saved&&!matched)lut_origin_selected=-1;
    /* Fn saves a preset without changing the active table. Show that preset
     * on entry while keeping the camera snapshot above separate. */
    if(selected>0){unsigned preset_strength=(unsigned)lut_strength;
        if(nx_tune_load(root,recipes[selected].key,&lut_tune,&preset_strength))lut_strength=(int)preset_strength;}
    /* The persisted card state is the tuning baseline; a surviving draft from
     * an earlier panel visit still wins for its own look. */
    lut_baseline_load(selected);
    if(selected>0) {
        int slot=lut_draft_slot(recipes[selected].key);
        if(slot>=0) {lut_tune=lut_look_drafts[slot].tune;lut_strength=lut_look_drafts[slot].strength;}
    }
    lut_init_groups();
    lut_category=selected?lut_brand[selected]:0;
    char category_file[NXUI_PATH];int category_value=-1;snprintf(category_file,sizeof category_file,"%s/.state/lut-category",root);
    FILE *category_in=fopen(category_file,"r");
    if(category_in){if(fscanf(category_in,"%d",&category_value)!=1)category_value=-1;fclose(category_in);}
    if(selected&&category_value>=0&&category_value<NX_LUT_CATEGORY_COUNT&&
       (category_value==0||(category_value==1?lut_favorite[selected]:lut_brand[selected]==category_value)))lut_category=category_value;
    /* The remembered group/page must never highlight a different row or hide
     * the current look on entry. Opening the panel is not an apply operation. */
    lut_tab_start=-1;
    lut_draft=lut_current_dirty();
    lut_leave_dialog=lut_submit_failed=0;lut_list_open=1;lut_saved_config=saved;
    lut_list_cursor=0;for(int i=1;i<selected;i++)if(lut_in_group(i))lut_list_cursor++;
    if(!saved){char saved_file[NXUI_PATH],saved_path[256],runtime_path[256];unsigned sg,ss,rg,rs;
        snprintf(saved_file,sizeof saved_file,"%s/.state/lut-choice",root);
        FILE *sf=fopen(saved_file,"r"),*rf=fopen("/var/run/nx300m-lut-selection","r");
        if(sf&&rf&&fscanf(sf,"%u %255s %u",&sg,saved_path,&ss)==3&&fscanf(rf,"%u %255s %u",&rg,runtime_path,&rs)==3)
            lut_saved_config=sg==rg&&ss==rs&&!strcmp(saved_path,runtime_path);
        if(sf)fclose(sf);
        if(rf)fclose(rf);
    }
    lut_reference_dirty=1;
    const char *notice = getenv("NXUI_LUT_NOTICE");
    if (notice && *notice) snprintf(status, sizeof status, "%s", notice);
    fprintf(stderr, "LUT_ENTRY index=%d key=%s strength=%d saved=%d group=%d active=%d\n", selected, recipes[selected].key, lut_strength, saved,lut_category,lut_origin_selected);
    fprintf(stderr,"LUT_TUNE key=%s values=%d,%d,%d,%d,%d,%d,%d\n",recipes[selected].key,
        lut_tune.value[0],lut_tune.value[1],lut_tune.value[2],lut_tune.value[3],lut_tune.value[4],lut_tune.value[5],lut_tune.value[6]);
    return 1;
}
static void open_mfw_lut(void) {
    char catalogue[NXUI_PATH], identity[64]; struct stat64 source, asset;
    const char *service = getenv("NX_FN_SERVICE_ROOT"), *data = getenv("NX300M_MOD_SOURCE_ROOT");
    mfw_open_lut = 0;
    if (!service || !*service) service = root;
    if (!data || !*data) data = service;
    if (!nx_mod_root_valid(data) || __xstat64(3, data, &source) || !S_ISDIR(source.st_mode) ||
            snprintf(catalogue, sizeof catalogue, "%s/looks.txt", data) >= (int)sizeof catalogue ||
            snprintf(mfw_lut_engine, sizeof mfw_lut_engine, "%s/bin/lut_engine.arm", service) >= (int)sizeof mfw_lut_engine ||
            __lxstat64(3, mfw_lut_engine, &asset) || !S_ISREG(asset.st_mode) || access(mfw_lut_engine, X_OK)) {
        message("LUT 文件不可用 · 请检查卡上的滤镜库"); return;
    }
    snprintf(mfw_lut_root, sizeof mfw_lut_root, "%s", data);
    mfw_menu_root = root; mfw_menu_engine = engine; root = mfw_lut_root;
    if (!load_lut_choices(catalogue)) { root = mfw_menu_root; mfw_menu_root = NULL; message("LUT 列表读取失败"); return; }
    snprintf(identity, sizeof identity, "%llu:%llu", (unsigned long long)source.st_dev, (unsigned long long)source.st_ino);
    setenv("NX300M_MOD_ROOT", root, 1);
    setenv("NX300M_MOD_SOURCE_ROOT", root, 1); setenv("NX300M_MOD_SOURCE_ID", identity, 1);
    engine = mfw_lut_engine; live = !mfw_readonly; lut_mode = 1;
    mfw_lut_quick = mfw_quick; mfw_services = mfw_quick = menu = 0;
    touch_active = 0; resize_panel();
}
static void return_mfw_menu(void) {
    if (!mfw_menu_root || child || queue.close_requested) return;
    root = mfw_menu_root; engine = mfw_menu_engine; mfw_menu_root = NULL;
    setenv("NX300M_MOD_ROOT", root, 1);
    mfw_services = menu = 1; mfw_quick = mfw_lut_quick;
    lut_mode = live = lut_draft = 0; nx_queue_init(&queue);
    status[0] = 0; touch_active = 0; resize_panel();
    feature_snapshot();
}
static void choose(int index) {
    if (count <= 0 || queue.close_requested) return;
    if (lut_mode) {
        /* While a confirmed request waits for its child, browsing is paused so
         * the applying indicator always names the request being applied. */
        if (queue.commit_requested || child) return;
        int next=(index+count)%count;
        if(next!=selected&&!lut_select_look(next))return;
        fprintf(stderr, "LUT_CHOICE event=select index=%d key=%s running=%d\n",
                          selected, recipes[selected].key, queue.running);
        /* Browsing is local: drafts move with their own look and nothing is
         * committed here. */
        lut_list_cursor=0;
        for(int i=1;i<selected;i++)if(lut_in_group(i))lut_list_cursor++;
        lut_reference_dirty=1;
        preview_status();
        return;
    }
    selected = (index + count) % count;
    queue.applied = -1;
    nx_queue_select(&queue, selected, now_ms());
    preview_status();
}
static void confirm_lut(int target) {
    if (count <= 0 || queue.close_requested) return;
    if(!lut_draft_stash())return;
    /* H1: every exit-intent confirmation (OK/top save, close-LUT, return to
     * menu) first persists ALL unsaved drafts; only the displayed request is
     * committed to the camera. Fn (target 0) still saves the current look
     * only. A partial flush failure keeps the remaining drafts and stays in
     * the panel for an explicit retry. */
    if(target&&lut_look_draft_count&&lut_drafts_flush()) {
        lut_submit_failed=1;dirty=1;
        message("部分调整保存未确认 · 请重试或继续编辑");
        fprintf(stderr,"LUT_CONFIRM flush_failed remaining=%d target=%d\n",lut_look_draft_count,target);
        return;
    }
    /* Freeze the confirmed request: identity, parameters and exit intent are
     * copied here and never re-read from the live edit buffer afterwards. */
    snprintf(lut_request_pending.key,sizeof lut_request_pending.key,"%s",recipes[selected].key);
    lut_request_pending.tune=lut_tune;
    lut_request_pending.strength=lut_strength;
    lut_request_pending.exit_after=target;
    lut_request_pending.is_off=!strcmp(recipes[selected].key,"__off");
    lut_request_pending.generation=++lut_request_serial;
    lut_request_pending_valid=1;
    lut_exit_after_save=target;
    fprintf(stderr, "LUT_CHOICE event=confirm index=%d key=%s strength=%d tune=%d,%d,%d,%d,%d,%d,%d exit=%d generation=%u running=%d desired=%d\n",
            selected, lut_request_pending.key, lut_request_pending.strength,
            lut_request_pending.tune.value[0],lut_request_pending.tune.value[1],lut_request_pending.tune.value[2],
            lut_request_pending.tune.value[3],lut_request_pending.tune.value[4],lut_request_pending.tune.value[5],
            lut_request_pending.tune.value[6], target, lut_request_pending.generation, queue.running, queue.desired);
    /* OK confirms the displayed choice even while an earlier control drains.
     * Reuse only the identical request (same look AND parameters); the newest
     * exit intent wins. The same look with different tuning is a new task and
     * waits for the running one to finish. */
    if (child && busy_kind == 1 && lut_request_inflight_valid &&
        lut_request_same(&lut_request_inflight,&lut_request_pending)) {
        queue.committing = 1;
        queue.commit_requested = 0;
        queue.desired = -1;
        lut_request_inflight.exit_after = target;
    } else {
        queue.applied = -1;
        nx_queue_select(&queue, selected, now_ms()-NXUI_DEBOUNCE_MS);
        queue.commit_requested = 1;
    }
    queue.close_requested = 0;
    lut_draft = lut_current_dirty();
    lut_submit_failed=0;lut_leave_dialog=0;
}
static void change_lut_strength(int delta) {
    /* Strength edits stay in the selected look's own draft; a confirmed
     * request owns its parameter copy, so no live value can reach it. */
    int next;
    if (!lut_mode || child || queue.commit_requested || queue.close_requested || count <= 0 ||
        !strcmp(recipes[selected].key, "__off")) return;
    next = lut_strength + delta;
    if (next < 0) next = 0;
    if (next > 100) next = 100;
    if (next == lut_strength) return;
    lut_strength = next;
    lut_draft=lut_current_dirty();lut_reference_dirty=1;
    preview_status();
}
static void read_output(void) {
    char buf[512]; ssize_t n;
    if (child_fd < 0) return;
    while ((n = read(child_fd, buf, sizeof buf)) > 0) {
        int keep = (int)n;
        if (keep > (int)sizeof(output)-1-output_len) keep = (int)sizeof(output)-1-output_len;
        if (keep > 0) { memcpy(output + output_len, buf, (size_t)keep); output_len += keep; }
    }
    if (n == 0) pipe_eof = 1;
    output[output_len] = 0;
    if (busy_kind == 1 && backend_phase != 2 && strstr(output, "NXUI_STAGE preview")) {
        backend_phase = 2; preview_status();
    }
}
static void exec_lut_engine(const char *action, const char *key) {
    lut_save_navigation();
    if (lut_mode && !strcmp(action, "apply") && key && !strcmp(key, "__off")) {
        if (arm_runner && *arm_runner)
            execl(arm_runner, arm_runner, "-cpu", "cortex-a9", "-L", "/", engine, "disable", (char *)0);
        else execl(engine, engine, "disable", (char *)0);
        return;
    }
    if (lut_mode && !strcmp(action, "apply")) {
        /* Only the immutable inflight snapshot reaches the engine argv. */
        const struct nx_lut_request *request=&lut_request_inflight;
        char strength[4],v[NX_TUNE_FIELDS][8];
        snprintf(strength, sizeof strength, "%d", request->strength);
        for(int i=0;i<NX_TUNE_FIELDS;i++)snprintf(v[i],sizeof v[i],"%d",request->tune.value[i]);
        if (arm_runner && *arm_runner)
            execl(arm_runner, arm_runner, "-cpu", "cortex-a9", "-L", "/", engine, action, request->key, strength,
                v[0],v[1],v[2],v[3],v[4],v[5],v[6],(char *)0);
        else execl(engine, engine, action, request->key, strength,v[0],v[1],v[2],v[3],v[4],v[5],v[6],(char *)0);
        return;
    }
    if (arm_runner && *arm_runner)
        execl(arm_runner, arm_runner, "-cpu", "cortex-a9", "-L", "/", engine, action, key, (char *)0);
    else execl(engine, engine, action, key, (char *)0);
}
static int start_child(int kind, int index, const char *command) {
    int pipefd[2]; pid_t pid;
    if (child) return 0;
    if (kind == 1 && lut_mode) {
        /* The dispatch adopts the confirmed snapshot before forking. A row
         * index that disagrees with the request is refused, never guessed. */
        if (!lut_request_pending_valid || strcmp(recipes[index].key, lut_request_pending.key)) {
            fprintf(stderr, "LUT_SUBMIT request_mismatch index=%d key=%s request=%s\n",
                    index, recipes[index].key, lut_request_pending_valid ? lut_request_pending.key : "(none)");
            queue.commit_requested = 0; queue.desired = -1;
            nx_queue_done(&queue, 0);
            message("确认请求与所选不一致 · 请重新确认");
            return 1;
        }
        lut_request_inflight = lut_request_pending;
        lut_request_inflight_valid = 1;
        fprintf(stderr, "LUT_SUBMIT index=%d key=%s strength=%d tune=%d,%d,%d,%d,%d,%d,%d committing=%d generation=%u\n",
                index, lut_request_inflight.key, lut_request_inflight.strength,
                lut_request_inflight.tune.value[0],lut_request_inflight.tune.value[1],lut_request_inflight.tune.value[2],
                lut_request_inflight.tune.value[3],lut_request_inflight.tune.value[4],lut_request_inflight.tune.value[5],
                lut_request_inflight.tune.value[6], queue.committing, lut_request_inflight.generation);
    }
    if (!live) {
        if (kind == 1) {
            nx_queue_done(&queue, 1);
            message("演示模式 · 已选择，未写入相机");
            if (queue.committing) {
                if(!lut_mode||lut_request_inflight.exit_after==1)quit=1;
                else if(lut_request_inflight.exit_after==2)mfw_return_requested=1;
                if(lut_mode){
                    lut_draft_drop(lut_request_inflight.key);
                    lut_baseline_load(index);
                    lut_origin_selected=index;lut_origin_strength=lut_request_inflight.strength;
                    lut_origin_tune=lut_request_inflight.tune;
                    lut_draft=lut_current_dirty();lut_exit_after_save=0;
                }
            }
        } else message("演示模式 · 未执行菜单命令");
        return 1;
    }
    if (pipe(pipefd) < 0) return 0;
    pid = fork();
    if (pid < 0) { close(pipefd[0]); close(pipefd[1]); return 0; }
    if (pid == 0) {
        char envpath[1024]; const char *old = getenv("PATH");
        setpriority(PRIO_PROCESS, 0, kind == 1 && lut_mode ? 0 : 19);
        setpgid(0, 0);
        close(pipefd[0]); dup2(pipefd[1], STDOUT_FILENO); dup2(pipefd[1], STDERR_FILENO);
        close(pipefd[1]); close(ConnectionNumber(display));
        if (chdir(root) != 0) _exit(126);
        snprintf(envpath, sizeof envpath, "%s:%s", root, old ? old : "/usr/bin:/bin");
        setenv("PATH", envpath, 1);
        if (kind == 1) {
            /* Both native engines apply session state. Dry-run exits above
             * before forking, including when the close action is selected. */
            const char *action = lut_mode ? "apply"
                                : (queue.committing ? "commit" : "preview");
            exec_lut_engine(action, recipes[index].key);
        }
        else execl("/bin/bash", "bash", "-c", command, (char *)0);
        _exit(127);
    }
    setpgid(pid, pid);
    close(pipefd[1]); fcntl(pipefd[0], F_SETFL, O_NONBLOCK);
    child_fd = pipefd[0]; child = pid; busy_kind = kind;
    busy_recipe = index; backend_phase = 0; pipe_eof = 0;
    child_started = now_ms(); child_timed_out = child_slow = 0; output_len = 0;
    output[0] = 0;
    if (kind == 1) preview_status(); else message("正在执行…");
    return 1;
}
static int mfw_action_allowed(const char *action) {
    const char *allowed[] = {"status", "network-info", "lut", "menu", "gallery", "disable-autostart", "enable-autostart", "close",
        "lenses","exposure","connection","settings", "preview", "debug", "back", "preview-toggle", "preview-enable", "preview-s2-enable", "preview-probe-once", "preview-identity", "preview-monochrome", "preview-monochrome-timing", "preview-sync", "preview-stop", "preview-restore", "preview-status", "preview-diag-export",
        "fw-mode-lock-on","fw-mode-lock-off","fw-animation-0","fw-animation-1","fw-animation-2",
        "fw-auto-iso-0","fw-auto-iso-6400","fw-auto-iso-max-12800","fw-auto-iso-max-25600","fw-auto-iso-max-3200","fw-mode-P","fw-mode-A","fw-mode-S","fw-mode-M"};
    for (unsigned int i = 0; i < sizeof allowed / sizeof *allowed; i++)
        if (!strcmp(action, allowed[i])) return 1;
    return 0;
}
static void mfw_child_close_fds(void) {
    /* The launcher keeps fd 8 locked across exec into the UI. A status shell
     * and its subprocesses must not inherit that lock or other UI resources. */
    DIR *directory = opendir("/proc/self/fd");
    if (!directory) _exit(126);
    int scan_fd = dirfd(directory); struct dirent *entry;
    while ((entry = readdir(directory))) {
        char *end; long fd = strtol(entry->d_name, &end, 10);
        if (*entry->d_name && !*end && fd > 2 && fd != scan_fd) close((int)fd);
    }
    closedir(directory);
}
static int start_mfw_action(const char *action) {
    int pipefd[2]; pid_t pid; char launcher[NXUI_PATH], runtime[NXUI_PATH], state_dir[NXUI_PATH];
    const char *base=getenv("NX300M_MFW_RAM_BASE"), *service=getenv("NX_FN_SERVICE_ROOT");
    int readonly = !strcmp(action, "status") || !strcmp(action, "network-info") || !strcmp(action, "preview-status");
    if (child || mfw_retired_child || !mfw_action_allowed(action)) return 0;
    /* The LUT root is SD data, while actions belong to the verified RAM
     * service generation. Never run the data directory's preview wrapper. */
    if(base&&*base) {
        if(!nx_mod_root_valid(base)||snprintf(runtime,sizeof runtime,"%s/runtime",base)>=(int)sizeof runtime||
           snprintf(state_dir,sizeof state_dir,"%s/state",base)>=(int)sizeof state_dir)return 0;
        service=runtime;
    }
    if(!service||!*service)service=root;
    if (snprintf(launcher, sizeof launcher, "%s/mfw-panel-actions.sh", service) >= (int)sizeof launcher || pipe(pipefd) < 0) return 0;
    pid_t parent = getpid();
    pid = fork();
    if (pid < 0) { close(pipefd[0]); close(pipefd[1]); return 0; }
    if (!pid) {
        if (setpgid(0, 0) || (readonly && prctl(PR_SET_PDEATHSIG, SIGKILL)) || getppid() != parent) _exit(126);
        setpriority(PRIO_PROCESS, 0, readonly ? 19 : 0);
        signal(SIGINT, SIG_DFL); signal(SIGTERM, SIG_DFL); signal(SIGCHLD, SIG_DFL);
        close(pipefd[0]);
        dup2(pipefd[1], STDOUT_FILENO); dup2(pipefd[1], STDERR_FILENO);
        close(pipefd[1]); mfw_child_close_fds();
        if(setenv("NX300M_MOD_ROOT",service,1)||setenv("NX_MOD_ROOT",service,1)||
           setenv("NX_FN_SERVICE_ROOT",service,1)||
           (base&&*base&&setenv("NX_STATE_DIR",state_dir,1)))_exit(126);
        execl("/bin/sh", "sh", launcher, action, (char *)0);
        _exit(127);
    }
    setpgid(pid, pid); close(pipefd[1]); fcntl(pipefd[0], F_SETFL, O_NONBLOCK);
    snprintf(mfw_fw_action,sizeof mfw_fw_action,"%s",!strncmp(action,"fw-",3)?action:"");
    child_fd = pipefd[0]; child = pid; busy_kind = 5;
    mfw_child_preview = !strncmp(action, "preview-", 8);
    mfw_child_readonly = readonly;
    mfw_cancel_started = 0;
    pipe_eof = child_timed_out = child_slow = output_len = 0; output[0] = 0; child_started = now_ms();
    if (mfw_child_preview) mfw_status_message(mfw_child_readonly ? "正在读取预览状态…" : "正在更新预览，完成后返回取景…");
    else if (mfw_child_readonly)
        mfw_status_message(!strcmp(action, "status") ? "正在读取机身服务状态…" : "正在读取网络连接信息…");
    return 1;
}
#include "nxui_feature_pages.h"
static void mfw_step(int delta) {
    if(item_count<=0||child)return;
    mfw_selected=(mfw_selected+delta+item_count)%item_count;
    fprintf(stderr,"MFW_SELECT page=%d index=%d action=%s\n",mfw_preview,mfw_selected,items[mfw_selected].command);
    dirty=1;
}
static void activate_mfw_item(int index) {
    if (index < -1 || index >= item_count) return;
    const char *action = index == -1 ? "back" : items[index].command;
    if(child)return;
    if(feature_action(action))return;
    if(!strcmp(action,"back")&&mfw_preview>=2){
        int parent=mfw_preview==6||mfw_preview==9?3:mfw_preview==2||mfw_preview==8?5:0;
        if(parent){feature_page(parent);return;}
    }
    if (!mfw_action_allowed(action)) { message("此操作不可用"); return; }
    if (!strcmp(action, "close")) { queue.close_requested = 1; return; }
    if (child) return;
    if (!strcmp(action, "preview") || !strcmp(action, "debug") || !strcmp(action, "back")) {
        int next_preview = !strcmp(action, "debug") ? 2 : !strcmp(action, "preview");
        int was_quick = mfw_quick;
        mfw_quick = 0;
        if (load_menu(next_preview == 2 ? "gui_debug.NX300M" : next_preview ? "gui_preview.NX300M" : "gui_mfw.NX300M")) {
            mfw_preview = next_preview; mfw_selected = 0;
            mfw_status_message("");
        } else mfw_quick = was_quick;
        return;
    }
    if (mfw_readonly && strcmp(action, "status") && strcmp(action, "network-info") && strcmp(action, "preview-status")) { message("截图模式不执行操作"); return; }
    if (!strcmp(action, "lut")) {
        lut_page_requested = now_ms(); mfw_open_lut = dirty = 1; return;
    }
    if (!strcmp(action, "menu") || !strcmp(action, "gallery")) {
        mfw_stock_key = !strcmp(action, "menu") ? "Menu" : "XF86Tools";
        queue.close_requested = 1;
        return;
    }
    if (!start_mfw_action(action)) {
        if (!strcmp(action, "status") || !strcmp(action, "network-info"))
            mfw_status_message("信息读取未能启动 · 请重试");
        else message("操作未能启动");
    }
}
static void poll_child(void) {
    int code, success, was_commit; pid_t result;
    if (mfw_retired_child) {
        result = waitpid(mfw_retired_child, &code, WNOHANG);
        if (result == mfw_retired_child || (result < 0 && errno == ECHILD)) {
            fprintf(stderr, "MFW cancelled_child_reaped pid=%ld\n", (long)mfw_retired_child);
            mfw_retired_child = 0;
        }
    }
    if (!child) return;
    read_output(); result = waitpid(child, &code, WNOHANG);
    if (result == 0) {
        long long age = now_ms() - child_started;
        if (busy_kind == 5 && mfw_child_readonly) {
            if (queue.close_requested || age > 12000) {
                if (!mfw_cancel_started) {
                    mfw_cancel_started = now_ms(); child_timed_out = 1;
                    kill(-child, SIGTERM);
                    fprintf(stderr, "MFW readonly_cancel_requested pid=%ld\n", (long)child);
                }
                long long cancelled_for = now_ms() - mfw_cancel_started;
                if (cancelled_for >= 250) kill(-child, SIGKILL);
                if (cancelled_for >= 500) {
                    /* An uninterruptible kernel read may not reap promptly.
                     * It owns no UI lock. Keep one PID for later reaping and
                     * reject another task until that completion is observed. */
                    fprintf(stderr, "MFW readonly_cancel_pending_kernel pid=%ld signal=KILL\n", (long)child);
                    mfw_retired_child = child; child = 0;
                    close(child_fd); child_fd = -1;
                    mfw_status_message(queue.close_requested ? "读取已取消 · 等待系统结束任务" : "读取超时 · 等待系统结束任务");
                }
            }
            return;
        }
        /* LUT/preview controls own drain and ptrace recovery. A UI deadline
         * must not kill their process group while the camera is paused. */
        if ((busy_kind == 1 && lut_mode) || (busy_kind == 5 && mfw_child_preview)) {
            if (age > 12000 && !child_slow) {
                child_slow = 1;
                fprintf(stderr, "BACKEND waiting_for_control pid=%ld elapsed_ms=%lld\n", (long)child, age);
                message("切换仍在完成，可关闭面板返回取景");
            }
            return;
        }
        long long timeout = busy_kind == 5 && mfw_child_preview ? 20000 : 12000;
        if (age > timeout && !child_timed_out) {
            child_timed_out = 1; kill(-child, SIGTERM);
            message("后端超时 · 正在停止，请检查相机状态");
        }
        if (age > timeout + 1000) kill(-child, SIGKILL);
        return;
    }
    if (result < 0 && errno == EINTR) return;
    if (busy_kind == 5 && mfw_child_readonly && mfw_cancel_started)
        kill(-child, SIGKILL); /* Reap/stop descendants even if their shell exited first. */
    read_output(); close(child_fd); child_fd = -1; child = 0;
    success = result > 0 && !child_timed_out && WIFEXITED(code) && WEXITSTATUS(code) == 0 &&
        !strstr(output, "VERIFY-FAIL") && !strstr(output, "REJECT");
    fprintf(stderr, "BACKEND kind=%d ok=%d elapsed_ms=%lld output=%s\n", busy_kind, success, now_ms()-child_started, output);
    if (busy_kind == 5) {
        int fw_slot=mfw_fw_slot(mfw_fw_action);
        /* H3: the fixed FW_MOD result line may legally be preceded by shell
         * diagnostics; locate the protocol marker anywhere in the output
         * instead of trusting the first line. Exit 75 plus the marker pair
         * still means "applied, preference not saved". */
        const char *fw_result=strstr(output,"FW_MOD ");
        int applied_unsaved=result>0&&!child_timed_out&&WIFEXITED(code)&&WEXITSTATUS(code)==75&&
            fw_slot>=0&&fw_result&&strstr(output,"PERSIST-WARNING");
        if(applied_unsaved) {
            snprintf(mfw_fw_retry[fw_slot],sizeof mfw_fw_retry[fw_slot],"%s",mfw_fw_action);
            if(mfw_preview==4||mfw_preview==5)feature_page(mfw_preview);
            snprintf(mfw_status,sizeof mfw_status,"%.*s",(int)sizeof mfw_status-1,output);
            message("已应用、未保存 · 再选此项重试保存");
        } else
        if (success) {
            if(fw_slot>=0)mfw_fw_retry[fw_slot][0]=0;
            snprintf(mfw_status, sizeof mfw_status, "%.*s", (int)sizeof mfw_status-1, output);
            message("");
            /* A firmware control answered with read-back state: rebuild the
             * settings/exposure page from the fresh response, keeping the
             * result text visible. */
            if ((mfw_preview==4 || mfw_preview==5) && fw_result) {
                char keep[sizeof mfw_status];snprintf(keep,sizeof keep,"%s",mfw_status);
                feature_page(mfw_preview);
                snprintf(mfw_status,sizeof mfw_status,"%s",keep);
            }
            if (mfw_child_preview && !mfw_child_readonly) {
                if(lut_mode){mfw_status[0]=0;message("LUT 预览设置已更新");}
                else queue.close_requested = 1;
            }
        } else if (mfw_child_readonly) {
            mfw_status_message(mfw_cancel_started
                ? (queue.close_requested ? "读取已取消" : "读取超时 · 请重试")
                : "服务信息读取失败 · 请重试");
        } else if (mfw_child_preview) {
            snprintf(mfw_status, sizeof mfw_status, "%.*s", (int)sizeof(mfw_status)-1, output);
            if (strstr(output, "LUT_OFF ")) {
                for (int i = 0; i < item_count; ++i)
                    if (!strcmp(items[i].command, "lut")) { mfw_selected = i; break; }
                message("当前 LUT 为关 · 请先选择 LUT，再开启预览");
                fprintf(stderr, "MFW preview_needs_selection status=%s\n", status);
            } else message("预览操作失败 · 详情见面板日志");
        } else message("服务操作失败 · 请查看面板日志");
    } else if (busy_kind == 1) {
        was_commit = queue.committing;
        int request_index = lut_mode && lut_request_inflight_valid ? lut_request_index(&lut_request_inflight) : busy_recipe;
        if (request_index < 0) request_index = busy_recipe;
        /* H2: four distinct persistence outcomes — applied+saved (plain
         * success), PERSIST-WARNING (not saved), PERSIST-UNCONFIRMED
         * (published by rename, directory durability unproven) and plain
         * failure. Each keeps its own draft/retry semantics. */
        int persist_unconfirmed=lut_mode&&strstr(output,"PERSIST-UNCONFIRMED");
        nx_queue_done(&queue, success);
        if (queue.desired >= 0 && queue.desired != request_index) preview_status();
        else if (success) {
            if (lut_mode && lut_request_inflight_valid && lut_request_inflight.is_off) {
                snprintf(status, sizeof status, "LUT 已关闭 · 原厂拍摄%s",
                    persist_unconfirmed ? " · 保存耐久未确认" : strstr(output, "PERSIST-WARNING") ? " · 未保存到卡" : "");
            }
            else if (lut_mode && persist_unconfirmed) snprintf(status, sizeof status, "已应用 %s · 保存耐久未确认，可重试", recipes[request_index].label);
            else if (lut_mode && strstr(output, "PERSIST-WARNING")) snprintf(status, sizeof status, "已暂存 %s · 未保存到卡，仅本次有效", recipes[request_index].label);
            else if (lut_mode) snprintf(status, sizeof status, "已暂存 %s · 强度 %d%% · 等待原厂 LUT 重载", recipes[request_index].label, lut_request_inflight.strength);
            else snprintf(status, sizeof status, "预览已应用：%s", recipes[busy_recipe].label);
            dirty = 1;
        } else message(lut_mode && strstr(output, "PREVIEW-WARNING")
                       ? "照片 LUT 已选择，预览更新失败 · 可重新选择以重试"
                       : "应用失败 · 可重新选择以重试");
        if(lut_mode&&!success){
            lut_submit_failed=1;lut_exit_after_save=0;lut_leave_flush_after=0;
            /* Failure keeps every draft for an explicit retry and drops any
             * queued redispatch, so stale parameters can never run alone. */
            queue.desired=-1;queue.commit_requested=0;
            if(lut_request_inflight_valid)lut_draft_mark_unconfirmed(&lut_request_inflight);
        }
        if(was_commit&&success&&lut_mode&&!strstr(output,"PERSIST-WARNING")&&!persist_unconfirmed&&lut_request_inflight_valid) {
            /* The engine applied and persisted exactly this request; the
             * writeback credits the request, never the live edit buffer. */
            lut_draft_drop(lut_request_inflight.key);
            lut_baseline_load(request_index);
            lut_origin_selected=request_index;lut_origin_strength=lut_request_inflight.strength;
            lut_origin_tune=lut_request_inflight.tune;
            lut_draft=lut_current_dirty();lut_submit_failed=0;lut_saved_config=1;dirty=1;
            /* A MENU leave scheduled during this task flushes the remaining
             * drafts first; only full success honors the exit target. */
            if(lut_leave_flush_after){
                if(lut_drafts_flush()){
                    lut_submit_failed=1;dirty=1;
                    fprintf(stderr,"LUT_LEAVE_FLUSH_FAILED remaining=%d target=%d\n",lut_look_draft_count,lut_leave_flush_after);
                    message("部分调整保存未确认 · 请重试或继续编辑");
                } else {
                    fprintf(stderr,"LUT_LEAVE_FLUSH_OK target=%d\n",lut_leave_flush_after);
                    if(lut_leave_flush_after==1)quit=1;
                    else if(lut_leave_flush_after==2)mfw_return_requested=1;
                }
                lut_leave_flush_after=0;
            } else {
                if(lut_request_inflight.exit_after==1)quit=1;
                else if(lut_request_inflight.exit_after==2)mfw_return_requested=1;
            }
            lut_exit_after_save=0;
            lut_request_inflight_valid=0;
        }else if(was_commit&&success&&!lut_mode)quit=1;
        if(lut_mode&&success&&strstr(output,"PERSIST-WARNING")){
            lut_submit_failed=2;lut_exit_after_save=0;lut_leave_flush_after=0;
            /* The engine could not save the per-look tuning: keep the
             * committed look's own draft recoverable for an explicit retry. */
            if(lut_request_inflight_valid)lut_draft_mark_unconfirmed(&lut_request_inflight);
        }
        if(lut_mode&&success&&persist_unconfirmed&&lut_request_inflight_valid) {
            /* H2: the camera received the request and the file content is
             * live, but directory durability is unproven. Credit the applied
             * request, keep its draft as durability_pending for a real
             * same-value retry, and stay in the panel. */
            lut_draft_mark_unconfirmed(&lut_request_inflight);
            lut_baseline_load(request_index);
            lut_origin_selected=request_index;lut_origin_strength=lut_request_inflight.strength;
            lut_origin_tune=lut_request_inflight.tune;
            lut_submit_failed=1;lut_exit_after_save=0;lut_leave_flush_after=0;lut_saved_config=0;dirty=1;
            queue.close_requested=0;queue.commit_requested=0;
            fprintf(stderr,"LUT_PERSIST_UNCONFIRMED key=%s generation=%u\n",
                lut_request_inflight.key,lut_request_inflight.generation);
            if (panel_input.unmapped && !panel_show()) request_s1_close();
        }
        if (was_commit && success && lut_mode && strstr(output, "PERSIST-WARNING") && !panel_yielded) {
            queue.close_requested = 0; queue.commit_requested = 0;
            if (panel_input.unmapped && !panel_show()) request_s1_close();
        }
        if (!success && !panel_yielded) {
            queue.close_requested = 0; queue.commit_requested = 0;
            if (panel_input.unmapped) {
                if (!panel_show()) request_s1_close();
            }
        }
    } else if (busy_kind == 3) {
        if (success && load_menu(pending_menu)) message("请选择菜单项");
        else message("子菜单生成失败 · 可重新进入");
        pending_menu[0] = 0;
    } else if (busy_kind == 4) {
        refresh_services(1);
        if (!services.known) message("服务状态读取失败 · 请重试");
        else {
            snprintf(status, sizeof status, "%sTelnet 已%s · FTP 已%s", success ? "" : "未完全成功 · ",
                services.telnet ? "开启" : "关闭", services.ftp ? "开启" : "关闭");
            dirty = 1;
        }
    } else {
        message(success ? "命令已完成" : "命令失败 · 详情见日志");
    }
    dirty = 1;
}
static int copy_file(const char *from, const char *to) {
    FILE *in = fopen(from, "rb"), *out; char buf[4096]; size_t n; int ok = 1;
    if (!in) return 0;
    out = fopen(to, "wb");
    if (!out) { fclose(in); return 0; }
    while ((n = fread(buf, 1, sizeof buf, in)) > 0)
        if (fwrite(buf, 1, n, out) != n) { ok = 0; break; }
    if (ferror(in)) ok = 0;
    fclose(in); if (fclose(out) != 0) ok = 0;
    if (ok && chmod(to, 0755) != 0) ok = 0;
    if (!ok) unlink(to);
    return ok;
}
static void activate_item(int index) {
    struct nx_item *it = &items[index];
    char source[NXUI_PATH], target[NXUI_PATH], command[1024];
    const char *name = base_name(it->command); size_t i;
    fprintf(stderr, "ACTION item=%d checkbox=%d\n", index, it->checkbox);
    if (child) { message("请等待当前操作完成"); return; }
    if (strstr(it->command, "gui_exit.sh")) { queue.close_requested = 1; return; }
    if (it->command[0] == '@') {
        if (strstr(it->command, "gui_filmlab1b")) { menu = 0; resize_panel(); return; }
        if (live && (!strncmp(it->command, "@/tmp/gui_br.", 13) ||
                     !strncmp(it->command, "@/tmp/gui_ramp.", 15))) {
            /* The legacy generator runs six global syncs. These volatile menu
             * files need no disk flush, and need not delay every UI opening. */
            snprintf(pending_menu, sizeof pending_menu, "%s", it->command);
            if (!start_child(3, -1, "sed '/^sync;/d' ./br_menu.sh | /bin/bash"))
                message("无法生成子菜单");
            return;
        }
        load_menu(it->command); return;
    }
    if (!strcmp(it->command, " ") || !*it->command) return;
    /* A menu operation supersedes any browsing request not yet dispatched. */
    queue.desired = queue.applied = -1;
    if (!it->checkbox && !strcmp(name, "telnet_toggle.sh")) {
        if (!live) { message("演示模式 · 未更改服务"); return; }
        refresh_services(1);
        if (!services.known) { message("无法读取服务状态 · 请重试"); return; }
        snprintf(command, sizeof command, "./telnet_toggle.sh --ui %s", services.telnet && services.ftp ? "disable" : "enable");
        if (start_child(4, -1, command)) message(services.telnet && services.ftp ? "正在关闭 Telnet/FTP…" : "正在开启 Telnet/FTP…");
        else message("服务操作启动失败");
        return;
    }
    if (!it->checkbox) { start_child(2, -1, it->command); return; }
    if (!strcmp(name, "toggle_dial_lock.sh")) {
        if (!live) { message("演示模式 · 未更改开关"); return; }
        snprintf(command, sizeof command, "./%stoggle_dial_lock.sh", checked(name) ? "off_" : "");
        start_child(2, -1, command);
        return;
    }
    /* Legacy checkbox semantics: auto/<script> is a copy, off_<script> stops it. */
    for (i = 0; name[i]; i++)
        if (!((name[i] >= 'a' && name[i] <= 'z') || (name[i] >= 'A' && name[i] <= 'Z') ||
            (name[i] >= '0' && name[i] <= '9') || name[i] == '_' || name[i] == '-' || name[i] == '.')) return;
    if (!i || strstr(name, "..") || !strcmp(name, "a_init.sh")) return;
    if (!live) { message("演示模式 · 未更改开关"); return; }
    snprintf(target, sizeof target, "%s/auto/%s", root, name);
    if (checked(name)) {
        if (unlink(target) != 0) { message("无法关闭开关"); return; }
        snprintf(source, sizeof source, "%s/off_%s", root, name);
        snprintf(command, sizeof command, "./off_%s", name);
        if (access(source, R_OK) == 0) start_child(2, -1, command);
        else message("已关闭自动执行");
    } else {
        snprintf(source, sizeof source, "%s/%s", root, name);
        snprintf(command, sizeof command, "./auto/%s", name);
        if (!copy_file(source, target)) { fprintf(stderr, "COPY failed errno=%d\n", errno); message("无法启用开关"); return; }
        start_child(2, -1, command);
    }
    dirty = 1;
}
#include "nxui_lut_panel.h"
#include "nxui_feature_draw.h"
static void draw(void) {
    char line[320]; int i, start;
    struct nk_color accent = nk_rgb(88, 174, 255);
    if(lut_mode){draw_lut_panel();return;}
    if (mfw_services) {
        if(mfw_preview>=3){draw_feature_page();return;}
        if(!mfw_quick&&!mfw_preview&&!*mfw_status) {
            struct nk_style_button saved=ctx->style.button;
            ctx->style.button.rounding=8;ctx->style.button.border=0;
            if(nk_begin(ctx,"NXWA",nk_rect(0,0,(float)width,(float)height),NK_WINDOW_NO_SCROLLBAR)) {
                nk_layout_space_begin(ctx,NK_STATIC,(float)height-18,12);
                lut_place(4,0,480,42);nk_label(ctx,"NX300M",NK_TEXT_LEFT);
                lut_place((float)width-136,0,112,42);
                if(nk_button_label(ctx,"关闭"))queue.close_requested=1;
                float card_width=((float)width-24-(item_count-1)*10)/item_count;
                for(i=0;i<item_count;i++) {
                    if(!strcmp(items[i].command,"preview-toggle")) {
                        char backend[32]="";FILE *marker=fopen("/var/run/nx300m-preview-backend","r");
                        if(marker){if(!fgets(backend,sizeof backend,marker))backend[0]=0;fclose(marker);}
                        snprintf(line,sizeof line,"取景预览  %s",!strcmp(backend,"smartfilter\n")||!strcmp(backend,"smartfilter")?"开":"关");
                    } else snprintf(line,sizeof line,"%s",items[i].label);
                    lut_place(i*(card_width+10),58,card_width,110);
                    if(lut_button(line,i==mfw_selected)){mfw_selected=i;activate_mfw_item(i);break;}
                }
                char lens_state[40];if(feature_lens_selected<0)snprintf(lens_state,sizeof lens_state,"未记录");else snprintf(lens_state,sizeof lens_state,"%dmm",feature_focal);
                snprintf(line,sizeof line,"LUT预览：%s     模式拨盘：%s     镜头：%s",feature_preview_enabled?"开":"关",feature_mode_locked<0?"未确认":feature_mode_locked?"锁定":"未锁定",lens_state);
                lut_place(4,180,(float)width-28,30);nk_label_colored(ctx,line,NK_TEXT_LEFT,nk_rgb(185,196,209));
                lut_place(4,214,(float)width-28,30);
                nk_label_colored(ctx,child?"正在执行…":*status?status:"左右选择    OK / 下键进入    MENU 返回",NK_TEXT_LEFT,nk_rgb(158,171,188));
                nk_layout_space_end(ctx);
            }
            nk_end(ctx);ctx->style.button=saved;
            nk_xlib_render(window,nk_rgb(23,24,26));XFlush(display);return;
        }
        if (nk_begin(ctx, "NXWA", nk_rect(0, 0, (float)width, (float)height), NK_WINDOW_NO_SCROLLBAR)) {
            nk_layout_row_begin(ctx, NK_STATIC, 30, 2);
            nk_layout_row_push(ctx, (float)(width-138));
            nk_label_colored(ctx, mfw_preview == 2 ? "调试" : "NX300M", NK_TEXT_LEFT, accent);
            nk_layout_row_push(ctx, 100);
            if (nk_button_label(ctx, mfw_preview ? "返回" : "关闭")) {
                if (mfw_preview && !child) activate_mfw_item(-1);
                else if (!mfw_preview) queue.close_requested = 1;
            }
            nk_layout_row_end(ctx);
            if (!mfw_quick && *mfw_status) {
              nk_layout_row_dynamic(ctx, 154, 1);
              if (nk_group_begin(ctx, "mfw-status", 0)) {
                const char *begin = mfw_status;
                while (*begin) {
                    const char *end = strchr(begin, '\n');
                    int length = end ? (int)(end - begin) : (int)strlen(begin);
                    if (length && begin[length-1] == '\r') length--;
                    nk_layout_row_dynamic(ctx, 24, 1);
                    nk_text(ctx, begin, length, NK_TEXT_LEFT);
                    if (!end) break;
                    begin = end + 1;
                }
                nk_group_end(ctx);
              }
            }
            nk_layout_row_dynamic(ctx, mfw_quick ? 32 : 42, mfw_quick ? 1 : 2);
            for (i = 0; i < item_count; i++) {
                if (!strcmp(items[i].command, "preview-toggle")) {
                    char backend[32] = "";
                    FILE *marker = fopen("/var/run/nx300m-preview-backend", "r");
                    if (marker) { if (!fgets(backend, sizeof backend, marker)) backend[0] = 0; fclose(marker); }
                    snprintf(line, sizeof line, "%s取景预览  %s", i == mfw_selected ? "› " : "",
                        !strcmp(backend, "smartfilter\n") || !strcmp(backend, "smartfilter") ? "开" : "关");
                } else snprintf(line, sizeof line, "%s%s", i == mfw_selected ? "› " : "", items[i].label);
                if (lut_button(line,i==mfw_selected)) { mfw_selected = i; activate_mfw_item(i); break; }
            }
            nk_layout_row_dynamic(ctx, 23, 1);
            nk_label_colored(ctx, child ? (mfw_child_readonly ? "正在读取…" : "正在执行…")
                : (mfw_quick && !*status ? "方向键选择 · OK 确认 · MENU 关闭" : status), NK_TEXT_LEFT, nk_rgb(171, 182, 190));
        }
        nk_end(ctx); nk_xlib_render(window, nk_rgb(23, 24, 26)); XFlush(display);
        return;
    }
    if (nk_begin(ctx, "NXWA", nk_rect(0, 0, (float)width, (float)height), NK_WINDOW_NO_SCROLLBAR)) {
        nk_layout_row_begin(ctx, NK_STATIC, 24, 3);
        nk_layout_row_push(ctx, (float)(width-260));
        nk_label_colored(ctx, menu ? "NX300M  /  系统设置"
                            : (lut_mode ? "LUT  /  3D 滤镜" : (live ? "FILMLAB  /  滤镜预览" : "FILMLAB  /  配方浏览")),
                         NK_TEXT_LEFT, accent);
        nk_layout_row_push(ctx, 104);
        if (nk_button_label(ctx, mfw_menu_root ? "返回" : menu ? "胶片" : "设置")) {
            if (mfw_menu_root) { mfw_return_requested = dirty = 1; }
            else if (menu) { menu = 0; resize_panel(); }
            else { char p[64]; snprintf(p, sizeof p, "gui_ini.NX300M"); load_menu(p); }
        }
        nk_layout_row_push(ctx, 104);
        if (nk_button_label(ctx, "关闭")) queue.close_requested = 1;
        nk_layout_row_end(ctx);
        if (menu) {
            start = page * 6;
            nk_layout_row_dynamic(ctx, 55, 2);
            for (i = start; i < start + 6; i++) {
                if (i >= item_count) { nk_spacing(ctx, 1); continue; }
                if (!strcmp(base_name(items[i].command), "telnet_toggle.sh"))
                    nx_services_label(&services, line, sizeof line);
                else if (!strcmp(base_name(items[i].command), "cycle_shutter_limit.sh")) {
                    char cur[32] = "关闭";
                    FILE *f = fopen("/var/run/nx300m-shutter-limit-request", "r");
                    if (f) {
                        char tmp[32];
                        if (fscanf(f, "%31s", tmp) == 1 && strcmp(tmp, "off") != 0) {
                            snprintf(cur, sizeof cur, "%s", tmp);
                        }
                        fclose(f);
                    }
                    snprintf(line, sizeof line, "最快快门: %s", cur);
                }
                else snprintf(line, sizeof line, "%s%s", items[i].checkbox ? (checked(items[i].command) ? "[开] " : "[关] ") : "", items[i].label);
                if (nk_button_label(ctx, line)) { activate_item(i); break; }
            }
            nk_layout_row_dynamic(ctx, 32, 3);
            if (nk_button_label(ctx, "上一页")) { page = page > 0 ? page - 1 : (item_count-1)/6; dirty = 1; }
            snprintf(line, sizeof line, "%d / %d", page + 1, (item_count+5)/6);
            nk_label(ctx, line, NK_TEXT_CENTERED);
            if (nk_button_label(ctx, "下一页")) { page = (page+1) % ((item_count+5)/6); dirty = 1; }
        } else if (count > 0) {
            int gi, win = 5, first, idx;
            nk_layout_row_dynamic(ctx, 84, win);
            first = selected - win / 2;
            if (first < 0) first = 0;
            if (first > count - win) first = count - win;
            if (first < 0) first = 0;
            for (gi = 0; gi < win; gi++) {
                idx = first + gi;
                if (idx >= count) { nk_spacing(ctx, 1); continue; }
                nk_bool picked = (idx == selected);
                snprintf(line, sizeof line, "%s%s", idx == selected ? "● " : "", recipes[idx].label);
                /* A repeated tap also selects a fresh application on OK. */
                if (nk_selectable_label(ctx, line, NK_TEXT_CENTERED, &picked))
                    choose(idx);
            }
            snprintf(line, sizeof line, "%d/%d   R%d G%d B%d   H%d S%d 锐%d 对比%d", selected+1, count,
                recipes[selected].value[0], recipes[selected].value[1], recipes[selected].value[2],
                recipes[selected].value[3], recipes[selected].value[4], recipes[selected].value[5], recipes[selected].value[6]);
            if (lut_mode) snprintf(line, sizeof line, "%d/%d%s", selected+1, count,
                !strcmp(recipes[selected].key, "__off") ? "   原厂拍摄" : "");
            nk_layout_row_dynamic(ctx, 16, 1);
            nk_label_colored(ctx, line, NK_TEXT_LEFT, nk_rgb(171, 182, 190));
            if (lut_mode) {
                int locked = child || queue.close_requested || !strcmp(recipes[selected].key, "__off");
                int minus_disabled = locked || lut_strength == 0;
                nk_layout_row_dynamic(ctx, 34, 3);
                if (minus_disabled) nk_widget_disable_begin(ctx);
                if (nk_button_label(ctx, "− 5") && !locked) change_lut_strength(-5);
                if (minus_disabled) nk_widget_disable_end(ctx);
                snprintf(line, sizeof line, "LUT 强度  %d%%", lut_strength);
                nk_label_colored(ctx, line, NK_TEXT_CENTERED, accent);
                int plus_disabled = locked || lut_strength == 100;
                if (plus_disabled) nk_widget_disable_begin(ctx);
                if (nk_button_label(ctx, "+ 5") && !locked) change_lut_strength(5);
                if (plus_disabled) nk_widget_disable_end(ctx);
            }
            nk_layout_row_dynamic(ctx, 26, 3);
            if (nk_button_label(ctx, "‹ 上一个")) choose(selected-1);
            if (nk_button_label(ctx, lut_mode ? "确定" : (live ? "应用并关闭" : "选择并关闭")) && !queue.close_requested) {
                if (lut_mode) {
                    confirm_lut(1);
                } else {
                /* Confirm the selected recipe through the same batch path,
                 * including after a previous preview. No flash save occurs. */
                    nx_queue_select(&queue, selected, now_ms()-NXUI_DEBOUNCE_MS);
                    queue.commit_requested = 1;
                    queue.close_requested = 1;
                }
            }
            if (nk_button_label(ctx, "下一个 ›")) choose(selected+1);
        } else {
            /* Say where to look. The generic message cost an hour of guessing
             * once: three uppercase look names had silently voided the list. */
            nk_layout_row_dynamic(ctx, 60, 1);
            nk_label(ctx, lut_mode ? "滤镜库为空 · 见 /tmp/nxui.log 与 /mnt/mmc/looks"
                                   : "配方表不可用 · 见 /tmp/nxui.log",
                     NK_TEXT_LEFT);
        }
        nk_layout_row_dynamic(ctx, 23, 1);
        nk_label_colored(ctx, live ? status : "配方浏览 · 当前会话不可应用", NK_TEXT_LEFT, nk_rgb(171, 182, 190));
    }
    nk_end(ctx);
    nk_xlib_render(window, nk_rgb(24, 29, 33));
    XFlush(display);
}
static void snapshot(const char *path) {
    FILE *f; int x, y;
    if (xlib.surf->present_failed || !(f = fopen(path, "wb"))) return;
    fprintf(f, "P6\n%d %d\n255\n", width, height);
    for (y = 0; y < height; y++) for (x = 0; x < width; x++) {
        uint32_t p = xlib.surf->pixels[(size_t)y*width+x];
        fputc((p>>16)&255, f); fputc((p>>8)&255, f); fputc(p&255, f);
    }
    fclose(f);
}
/* The MFW menu uses one original font, rasterized entirely in this process.
 * Retain the metadata argument for compatibility with deployed launchers. */
static XFont *panel_font_file(const char *path, const char *metadata) {
    (void)metadata;
    return nx_cpu_font(path);
}

int main(int argc, char **argv) {
    const char *recipe_path = NULL, *initial_menu = NULL, *shot = NULL;
    char default_recipes[NXUI_PATH], default_engine[NXUI_PATH];
    const char *font_name = "SDIC_GP_US:pixelsize=28";
    const char *font_file = NULL;
    const char *font_metadata = NULL;
    XSetWindowAttributes attr; XFont *font; Atom wm_delete; int i, lockfd, render_count = 0;
    long long started, next_frame = 0, max_draw_ms = 0, process_started = now_ms();
    long long first_frame_boot_ms = -1;
    long long recipes_ready, display_ready, window_ready, font_ready, backend_ready;
    long long menu_ready, focus_ready, first_draw_started, first_draw_ready;
    int wakeups = 0, blits = 0, explicit_dry_run = 0;
    const char *trace = getenv("NX_PANEL_TRACE");
    int trace_frames = trace && !strcmp(trace, "1");
    for (i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--live")) live = 1;
        else if (!strcmp(argv[i], "--dry-run")) { live = 0; explicit_dry_run = 1; }
        else if (!strcmp(argv[i], "--lut")) lut_mode = 1;
        else if (!strcmp(argv[i], "--mfw-services")) mfw_services = 1;
        else if (!strcmp(argv[i], "--mfw-quick-preview")) { mfw_services = 1; mfw_quick = 1; }
        else if (i+1 < argc && !strcmp(argv[i], "--root")) root = argv[++i];
        else if (i+1 < argc && !strcmp(argv[i], "--engine")) engine = argv[++i];
        else if (i+1 < argc && !strcmp(argv[i], "--arm-runner")) arm_runner = argv[++i];
        else if (i+1 < argc && !strcmp(argv[i], "--recipes")) recipe_path = argv[++i];
        else if (i+1 < argc && !strcmp(argv[i], "--menu")) initial_menu = argv[++i];
        else if (i+1 < argc && !strcmp(argv[i], "--font")) font_name = argv[++i];
        else if (i+1 < argc && !strcmp(argv[i], "--font-file")) font_file = argv[++i];
        else if (i+1 < argc && !strcmp(argv[i], "--font-metadata")) font_metadata = argv[++i];
        else if (i+1 < argc && !strcmp(argv[i], "--snapshot")) shot = argv[++i];
        else if (i+1 < argc && !strcmp(argv[i], "--lifetime-ms")) lifetime = atol(argv[++i]);
        else { fprintf(stderr, "Usage: nxui [--live|--dry-run] [--root dir] [--recipes file] [--menu gui_file] [--engine file] [--arm-runner qemu] [--snapshot ppm] [--lifetime-ms n]\n"); return 2; }
    }
    if (!root) root = nx_mod_root();
    if (!!font_file != !!font_metadata) { fprintf(stderr, "font file and coverage metadata must be supplied together\n"); return 2; }
    if (!nx_mod_root_valid(root)) { fprintf(stderr, "Invalid mod root directory\n"); return 2; }
    if (setenv("NX300M_MOD_ROOT", root, 1)) return 2;
    if (mfw_services) {
        if (live || lut_mode || initial_menu || recipe_path || engine || arm_runner) {
            fprintf(stderr, "MFW services mode does not accept recipe/control options\n"); return 2;
        }
        initial_menu = mfw_quick ? "gui_preview_quick.NX300M" : "gui_mfw.NX300M";
        status[0] = 0;
        mfw_readonly = shot != NULL || explicit_dry_run;
    }
    if (!recipe_path) {
        snprintf(default_recipes, sizeof default_recipes, "%s/%s", root, lut_mode ? "looks.txt" : "recipes.txt");
        recipe_path = default_recipes;
    }
    if (!engine) {
        snprintf(default_engine, sizeof default_engine, "%s/%s", root, lut_mode ? "lut_engine.arm" : "recipe_engine.arm");
        engine = default_engine;
    }
    if (getenv("NXUI_NET_DIR")) net_dir = getenv("NXUI_NET_DIR"); /* isolated service-state tests */
    if (lut_mode) snprintf(status, sizeof status, "选择 LUT · 在原厂重载时加载");
    /* A screenshot is always read-only, regardless of option order. */
    if (shot) live = 0;
    /* The cycle flash opens a short-lived panel while the user may already be
     * long-pressing for the real one; a lock tag keeps the two independent. */
    char launch_path[96],ui_path[96];
    const char *lock_tag=getenv("NXUI_LOCK_TAG");lock_tag=lock_tag?lock_tag:"";
    snprintf(launch_path,sizeof launch_path,"/tmp/nx300m-panel-launch%s.lock",lock_tag);
    snprintf(ui_path,sizeof ui_path,"/tmp/nxwa-nxui%s.lock",lock_tag);
    if (mfw_services) {
        /* Acquire both existing locks in-process. Neither backend exec nor a
         * cancelled status reader should retain a copy of the launcher lock. */
        int launcher_lock = open(launch_path, O_CREAT|O_WRONLY, 0600);
        if (launcher_lock < 0 || flock(launcher_lock, LOCK_EX|LOCK_NB) != 0) {
            fprintf(stderr,"NXUI launcher lock unavailable path=%s errno=%d\n",launch_path,errno);
            return 3;
        }
        fcntl(launcher_lock, F_SETFD, FD_CLOEXEC);
    }
    lockfd = open(ui_path, O_CREAT|O_RDWR, 0600);
    if (lockfd < 0 || flock(lockfd, LOCK_EX|LOCK_NB) != 0) {
        fprintf(stderr,"NXUI ui lock unavailable path=%s errno=%d\n",ui_path,errno);
        return 3;
    }
    fcntl(lockfd, F_SETFD, FD_CLOEXEC);
    signal(SIGINT, on_signal); signal(SIGTERM, on_signal);
    signal(SIGALRM, on_display_timeout);
    alarm(5);
    { struct sigaction action; memset(&action, 0, sizeof action); action.sa_handler = on_child;
      sigemptyset(&action.sa_mask); action.sa_flags = SA_NOCLDSTOP; sigaction(SIGCHLD, &action, NULL); }
    /* The resident service stays in the background; this short-lived
     * interactive window needs normal CPU priority to produce its first frame.
     * Backend children lower their own priority before doing work. */
    if (setpriority(PRIO_PROCESS, 0, 0) != 0)
        fprintf(stderr, "setpriority failed errno=%d\n", errno);
    nx_queue_init(&queue);
    count = mfw_services || lut_mode ? 0 : nx_load_recipes(recipe_path, recipes, NXUI_MAX_RECIPES);
    if (count < 0) count = 0;
    if (lut_mode && !load_lut_choices(recipe_path)) count = 0;
    recipes_ready = now_ms();
    display = XOpenDisplay(NULL);
    if (!display) { fprintf(stderr, "XOpenDisplay failed\n"); return 4; }
    display_ready = now_ms();
    screen = DefaultScreen(display); width = mfw_quick ? 560 : 800;
    fprintf(stderr, "PANEL_DISPLAY vendor=%s release=%d raw=%dx%d depth=%d\n", ServerVendor(display), VendorRelease(display),
        DisplayWidth(display, screen), DisplayHeight(display, screen), DefaultDepth(display, screen));
    /* Allocate the actual entry layout once, rather than create a small
     * pixmap and immediately discard it when load_menu resizes the panel. */
    height = mfw_services ? 264 : (initial_menu ? 336 : (lut_mode ? 480 : 216));
    if (height > DisplayWidth(display, screen)) height = DisplayWidth(display, screen);
    if (DisplayHeight(display, screen) < width || DisplayWidth(display, screen) < height) {
        fprintf(stderr, "NX300M raw display must be at least %dx%d\n", height, width); XCloseDisplay(display); return 4;
    }
    panel_parent_height = DisplayHeight(display, screen);
    panel_parent = panel_stock_parent();
    if (panel_parent != None) panel_focus_revert = RevertToParent;
    else if (shot || (!mfw_services && !lut_mode && !live)) panel_parent = RootWindow(display, screen);
    else {
        fprintf(stderr, "PANEL_PARENT no-native-parent: active panel requires a focused, viewable stock camera window\n");
        XCloseDisplay(display); alarm(0); return 4;
    }
    /* As a child of the stock window, even SIGKILL/timeout/disconnection
     * destroys our window with RevertToParent and returns focus server-side.
     * Do not change the parent's properties, event masks or program code. */
    fprintf(stderr, "PANEL_PARENT mode=%s window=0x%lx focus_revert=%d\n",
        panel_focus_revert == RevertToParent ? "stock-child" : "dry-run-root", panel_parent, panel_focus_revert);
    memset(&attr, 0, sizeof attr);
    attr.override_redirect = True; attr.background_pixel = BlackPixel(display, screen);
    /* Only held-touch motion is needed for LUT parameter rows. Hover stays idle. */
    attr.event_mask = ExposureMask|KeyPressMask|KeyReleaseMask|ButtonPressMask|ButtonReleaseMask|StructureNotifyMask|
        VisibilityChangeMask|FocusChangeMask|Button1MotionMask;
    window = XCreateWindow(display, panel_parent, mfw_quick ? (DisplayWidth(display,screen)-height)/2 : 0, (panel_parent_height-width)/2,
        (unsigned)height, (unsigned)width, 0, DefaultDepth(display, screen), InputOutput,
        DefaultVisual(display, screen), CWOverrideRedirect|CWBackPixel|CWEventMask, &attr);
    XStoreName(display, window, "Metol-NX300M");
    wm_delete = XInternAtom(display, "WM_DELETE_WINDOW", False);
    XSetWMProtocols(display, window, &wm_delete, 1);
    window_ready = now_ms();
    font = font_file ? panel_font_file(font_file, font_metadata) : nk_xfont_create(display, font_name);
    if (!font || !font->ft) { fprintf(stderr, "font unavailable\n"); return 5; }
    hint_font=nx_cpu_font_sized(font_file?font_file:"/usr/share/fonts/SDIC_GP_US_20120720.ttf",22);
    font_ready = now_ms();
    fprintf(stderr, "PANEL_FONT mode=%s source=%s\n",
        font_file ? "file" : "match", font_file ? font_file : font_name);
    ctx = nk_xlib_init(font, display, screen, window, DefaultVisual(display, screen),
        DefaultColormap(display, screen), (unsigned)width, (unsigned)height);
    if (!ctx) { fprintf(stderr, "Nuklear initialization failed\n"); return 5; }
    backend_ready = now_ms();
    /* XCopyArea otherwise emits NoExpose after every blit. Treating that as a
     * reason to redraw creates a self-sustaining busy loop on the camera. */
    XSetGraphicsExposures(display, xlib.surf->gc, False);
    ctx->style.window.padding = nk_vec2(12, 8); ctx->style.window.spacing = nk_vec2(8, 5);
    ctx->style.window.background = nk_rgb(23, 24, 26);
    ctx->style.window.fixed_background = nk_style_item_color(nk_rgb(23, 24, 26));
    ctx->style.button.normal = nk_style_item_color(nk_rgb(39, 41, 44));
    ctx->style.button.hover = ctx->style.button.normal;
    ctx->style.button.active = nk_style_item_color(nk_rgb(29, 102, 164));
    ctx->style.button.text_normal = nk_rgb(235, 237, 240);
    ctx->style.button.rounding = 2;
    if (initial_menu && !load_menu(initial_menu)) return 6;
    if (mfw_services) {
        feature_snapshot();
        for (i = 0; i < item_count; i++)
            if (items[i].checkbox || !mfw_action_allowed(items[i].command)) return 6;
    }
    menu_ready = now_ms();
    nx_panel_input_init(&panel_input, display, window);
    if (panel_focus_revert == RevertToParent) nx_panel_input_set_stock_parent(&panel_input, panel_parent);
    focus_ready = now_ms();
    /* Prepare glyphs and the retained image while the stock view is still
     * visible. Mapping an empty black window before this work obscures it. */
    first_draw_started = now_ms();
    panel_stage("first-draw-begin");
    draw();
    if (xlib.surf->present_failed) {
        fprintf(stderr, "Panel image unavailable; stock focus was not changed\n");
        nk_xlib_shutdown(); nk_xfont_del(display, font);nk_xfont_del(display,hint_font);
        XDestroyWindow(display, window); XCloseDisplay(display); return 5;
    }
    /* Prove the hidden image completed before exposing a window or taking
     * stock input. A submitted draw is not evidence the server finished it. */
    panel_stage("hidden-draw-fence-begin");
    XSync(display, False);
    panel_stage("hidden-draw-ready");
    first_draw_ready = now_ms();
    max_draw_ms = first_draw_ready-first_draw_started;
    if (!panel_show()) {
        panel_stage("entry-not-visible");
        nx_panel_input_hide(&panel_input);
        nk_xlib_shutdown(); nk_xfont_del(display, font);nk_xfont_del(display,hint_font);
        XDestroyWindow(display, window); XCloseDisplay(display); alarm(0); return 5;
    }
    dirty = 0; render_count = 1;
    started = now_ms();
    {
        struct timespec boot;
        if (clock_gettime(CLOCK_BOOTTIME, &boot) == 0)
            first_frame_boot_ms = (long long)boot.tv_sec*1000 + boot.tv_nsec/1000000;
    }
    fprintf(stderr, "READY backend=nuklear-cpu-ximage visibility_guard=1 live=%d recipes=%d panel=%dx%d nice=%d\n",
        live, count, width, height, getpriority(PRIO_PROCESS, 0));
    fprintf(stderr, "INIT recipes_ms=%lld display_ms=%lld font_ms=%lld backend_ms=%lld map_ms=%lld first_draw_ms=%lld\n",
        recipes_ready-process_started, display_ready-recipes_ready, font_ready-display_ready,
        backend_ready-font_ready, started-backend_ready, first_draw_ready-first_draw_started);
    fprintf(stderr, "PANEL_INIT pid=%ld recipes_ms=%lld xopen_ms=%lld window_ms=%lld font_ms=%lld backend_ms=%lld menu_ms=%lld focus_snapshot_ms=%lld draw_submit_ms=%lld map_focus_fence_ms=%lld\n",
        (long)getpid(), recipes_ready-process_started, display_ready-recipes_ready,
        window_ready-display_ready, font_ready-window_ready, backend_ready-font_ready,
        menu_ready-backend_ready, focus_ready-menu_ready, first_draw_ready-first_draw_started,
        started-first_draw_ready);
    {
        const char *launch = getenv("NXUI_LAUNCH_UPTIME"), *trigger = getenv("NX_PANEL_TRIGGER_MS");
        const char *forked = getenv("NX_PANEL_FORK_MS"), *script = getenv("NX_PANEL_SCRIPT_UPTIME");
        fprintf(stderr, "FIRST_FRAME process_ms=%lld launch_ms=%lld trigger_elapsed_ms=%lld fork_elapsed_ms=%lld script_elapsed_ms=%lld pid=%ld prep_elapsed_ms=%lld\n",
            started-process_started, launch ? uptime_elapsed_ms(launch, first_frame_boot_ms) : started-process_started,
            trigger ? started-strtoll(trigger, NULL, 10) : -1,
            forked ? started-strtoll(forked, NULL, 10) : -1,
            uptime_elapsed_ms(script, first_frame_boot_ms), (long)getpid(),
            uptime_elapsed_ms(getenv("NX_PANEL_PREP_UPTIME"), first_frame_boot_ms));
    }
    log_resources("first-frame");
    if (live || mfw_services) nx_panel_input_open_evdev(&panel_input);
    if (mfw_services && panel_input.ev_fd < 0) {
        panel_input.ev_fd = open("/dev/event0", O_RDONLY | O_NONBLOCK);
        if (panel_input.ev_fd >= 0) fcntl(panel_input.ev_fd, F_SETFD, FD_CLOEXEC);
    }
    /* Opening a panel does not need a service process or network scan.
     * Status is fetched only by the explicit refresh action. */
    /* NX500 capdtm command numbers do not apply to DRIMeIV. */
    while (!quit) {
        alarm(5);
        XEvent event; fd_set fds; struct timeval wait; int index, pending, events = 0, refresh = 0, painted = 0;
        long long tick = now_ms(), delay = 60000, input_dequeued = -1;
        /* Observe only a finite boot transition. Idle, restored panels retain
         * their event-driven sleep; an external completion redraws once. */
        if(lut_mode&&live&&tick>=lut_preview_due&&tick<lut_preview_until){
            int observed=nx_preview_state(root);
            if(observed!=lut_preview_shown)dirty=1;
            lut_preview_due=tick+500;
            if(observed!=NX_PREVIEW_WAIT&&observed!=NX_PREVIEW_BUSY)lut_preview_until=0;
        }
        if (interrupted || (lifetime > 0 && now_ms()-started >= lifetime)) queue.close_requested = 1;
        /* 半按快门 S1：/dev/input/event0 code 125 (KEY_LEFTMETA) press ⇒ 关闭 */
        nx_panel_input_read_evdev(&panel_input, request_s1_close);
        /* EV_S1.sh 通道（备用）：每 100ms 检查并消费标记 */
        if (tick - close_flag_check >= 100) {
            close_flag_check = tick;
            if (access("/tmp/nxui-close", F_OK) == 0) {
                unlink("/tmp/nxui-close");
                queue.close_requested = 1;
            }
        }
        /* Leave unconsumed input in Xlib until its frame. Resetting Nuklear's
         * input while deferring draw would discard fast taps. Bound each batch
         * without extending a pending paint on each new input batch.
         * continuous/discrete live at loop scope: the paint gate and the
         * select deadline below need the latest burst classification. */
        int continuous = 0, discrete = 0;
        if (tick >= next_frame) {
          nk_input_begin(ctx);
          while (events < 64 && XPending(display)) {
            XNextEvent(display, &event);
            events++;
            /* Continuous drag/wheel bursts rasterize at ~15 Hz; input is still
             * drained every frame and the final state always paints on the
             * first frame after the burst ends. Single keys stay immediate. */
            if (event.type == MotionNotify) continuous++;
            else if (event.type == KeyPress) {
                KeySym burst = XLookupKeysym(&event.xkey, 0);
                if (burst == XF86XK_ScrollUp || burst == XF86XK_ScrollDown) continuous++;
                else discrete++;
            } else if (event.type == ButtonPress || event.type == ButtonRelease) discrete++;
            if (event.type == VisibilityNotify) {panel_visibility_event(&event); continue;}
            if (event.type == FocusIn || event.type == FocusOut) {
                fprintf(stderr, "PANEL_FOCUS_EVENT type=%d mode=%d detail=%d\n", event.type, event.xfocus.mode, event.xfocus.detail);
                if (event.type == FocusOut && !panel_input.unmapped && !queue.close_requested && event.xfocus.detail != NotifyInferior) {
                    Window current; int revert;
                    XGetInputFocus(display, &current, &revert);
                    if (current != window || event.xfocus.mode == NotifyGrab) {
                        panel_stage("yield-focus-lost"); request_s1_close();
                    }
                }
                continue;
            }
            if (event.type == NoExpose) continue;
            if (event.type == MappingNotify) {XRefreshKeyboardMapping(&event.xmapping);continue;}
            if (trace_frames && input_dequeued < 0 &&
                (event.type == KeyPress || event.type == KeyRelease || event.type == ButtonPress || event.type == ButtonRelease))
                input_dequeued = now_ms();
            if (event.type == Expose) { refresh = 1; continue; }
            if (event.type == ConfigureNotify) {
                /* The event already contains the size. Avoid the upstream
                 * synchronous XGetWindowAttributes on every expose/resize. */
                if (width != event.xconfigure.height || height != event.xconfigure.width) {
                    width = event.xconfigure.height; height = event.xconfigure.width;
                    nk_xsurf_resize(xlib.surf, (unsigned)width, (unsigned)height);
                    dirty = 1;
                }
                continue;
            }
            if (event.type == KeyPress || event.type == ButtonPress || event.type == ButtonRelease) dirty = 1;
            if (event.type == ClientMessage && (Atom)event.xclient.data.l[0] == wm_delete) close_choice();
            if (nx_panel_input_handle_s1(&panel_input, &event, request_s1_close)) continue;
            if (queue.close_requested || panel_visibility != VisibilityUnobscured) continue;
            if (event.type == MotionNotify) {
                if(lut_mode&&lut_reference_press){
                    int dx=event.xmotion.y-touch_x;
                    if(dx>=30||dx<=-30){lut_reference_press=lut_reference_original=0;lut_reference_dirty=dirty=1;}
                }
                if(lut_mode&&lut_edit&&nk_input_is_mouse_down(&ctx->input,NK_BUTTON_LEFT)) {
                    nk_input_motion(ctx,event.xmotion.y,height-1-event.xmotion.x);dirty=1;
                }
                continue;
            }
            if (event.type == KeyPress || event.type == KeyRelease)
                fprintf(stderr, "PANEL_KEY type=%d code=%u symbol=0x%lx\n", event.type, event.xkey.keycode, XLookupKeysym(&event.xkey, 0));
            if(lut_mode&&(event.type==KeyPress||event.type==KeyRelease)&&XLookupKeysym(&event.xkey,0)==XF86XK_HomePage){
                /* X11 autorepeat emits a release/press pair with one timestamp.
                 * One physical Fn press must cause only one view transition. */
                if(event.type==KeyRelease){
                    XEvent next;
                    if(XPending(display)){XPeekEvent(display,&next);
                        if(next.type==KeyPress&&next.xkey.keycode==event.xkey.keycode&&next.xkey.time==event.xkey.time)continue;
                    }
                    lut_fn_down=0;
                }else if(!lut_fn_down){lut_fn_down=1;lut_key(XF86XK_HomePage);}
                continue;
            }
            if (event.type == KeyPress) {
                KeySym key = XLookupKeysym(&event.xkey, 0);
                /* Samsung's main wheel uses multimedia keysyms; its events
                 * only affect this focused panel, never the stock camera UI. */
                if(lut_mode&&(lut_leave_dialog||lut_list_open||lut_toolbar_focus>=0)){lut_key(key);}
                else if (key == XK_Escape || key == XK_Menu) {
                    if (mfw_services && mfw_preview && !child) activate_mfw_item(-1);
                    else if(lut_mode)lut_request_leave(mfw_menu_root?2:1);
                    else close_choice();
                }
                else if (mfw_services) {
                    if (key == XK_Right || key == XK_KP_Right || key == XF86XK_ScrollUp) mfw_step(1);
                    else if (key == XK_Left || key == XK_KP_Left || key == XF86XK_ScrollDown) mfw_step(-1);
                    else if (key == XK_Down || key == XK_KP_Down) {
                        if(!mfw_quick&&!mfw_preview&&!*mfw_status)activate_mfw_item(mfw_selected);
                        else mfw_selected = (mfw_selected+(mfw_quick||mfw_preview>=3?1:2))%item_count;
                    }
                    else if (key == XK_Up || key == XK_KP_Up) mfw_selected = (mfw_selected+item_count-(mfw_quick||mfw_preview>=3?1:2))%item_count;
                    else if (key == XK_Return || key == XK_KP_Enter) activate_mfw_item(mfw_selected);
                }
                else if(lut_mode)lut_key(key);
                else if (!menu && (key == XK_Right || key == XK_Down || key == XK_KP_Right || key == XK_KP_Down)) choose(selected+1);
                else if (!menu && (key == XK_Left || key == XK_Up || key == XK_KP_Left || key == XK_KP_Up)) choose(selected-1);
                else if (!menu && (key == XK_Return || key == XK_KP_Enter) && (lut_mode || !child) && count > 0) {
                    if (lut_mode) confirm_lut(1);
                    else {
                        nx_queue_select(&queue, selected, now_ms()-NXUI_DEBOUNCE_MS);
                        queue.commit_requested = 1; queue.close_requested = 1;
                    }
                }
            }
            if (event.type == ButtonPress && (event.xbutton.button == 4 || event.xbutton.button == 5)) {
                if(lut_mode)lut_key(event.xbutton.button==4?XF86XK_ScrollUp:XF86XK_ScrollDown);
                else if(mfw_services)mfw_step(event.xbutton.button==4?1:-1);
                else if(!menu)choose(selected + (event.xbutton.button == 4 ? -1 : 1));
            }
            /* Touchscreens can send a tap without a preceding MotionNotify. */
            if (event.type == ButtonPress || event.type == ButtonRelease) {
                int raw_x = event.xbutton.x;
                event.xbutton.x = event.xbutton.y;
                event.xbutton.y = height - 1 - raw_x;
                nk_input_motion(ctx, event.xbutton.x, event.xbutton.y);
            }
            /* 左右滑动手势：超阈值切换选中；release 移出控件避免被当成点击 */
            if (event.type == ButtonPress && !menu && lut_mode && selected &&!lut_list_open&&!lut_leave_dialog&&
                event.xbutton.x >= 350 && event.xbutton.y >= 102 && event.xbutton.y < 454) {
                int field=(event.xbutton.y-102)/44;
                if(lut_field_supported(field)){lut_field=field;lut_edit=1;lut_category_focus=0;dirty=1;}
            }
            if(event.type==ButtonPress&&lut_mode&&!lut_leave_dialog&&!child&&event.xbutton.button==1&&
               event.xbutton.x>=12&&event.xbutton.x<320&&event.xbutton.y>=108&&event.xbutton.y<288){
                lut_reference_press=1;lut_reference_pressed_at=now_ms();lut_edit=0;
                touch_x=event.xbutton.x;touch_active=1;
            }
            if(event.type==ButtonRelease&&lut_reference_press&&event.xbutton.button==1){
                int dx=event.xbutton.x-touch_x;
                if(dx>-30&&dx<30&&now_ms()-lut_reference_pressed_at<300)lut_reference_scene=(lut_reference_scene+1)%3;
                lut_reference_press=lut_reference_original=0;lut_reference_dirty=dirty=1;
            }
            if (event.type == ButtonPress && !menu && !lut_edit &&
                (!lut_mode || (!lut_list_open&&!lut_leave_dialog&&event.xbutton.x < 338 && event.xbutton.y >= 100 && event.xbutton.y < 288))) {
                touch_x = event.xbutton.x; touch_active = 1;
            }
            if (event.type == ButtonRelease && touch_active) {
                int dx = event.xbutton.x - touch_x;
                int step = (dx >= 0 ? dx : -dx) / 45;   /* 每 45px 切一项：滑得远切得快 */
                touch_active = 0;
                if (step < 1 && (dx >= 30 || dx <= -30)) step = 1;   /* 轻扫阈值 30px */
                if (!menu && step > 0) {
                    if(lut_mode){for(int swipe=0;swipe<step;swipe++)lut_step(dx<0?1:-1);}
                    else choose(selected + (dx < 0 ? step : -step));
                    nk_input_motion(ctx, -10000, -10000);
                    dirty = 1;
                }
            }
            nk_xlib_handle_event(display, screen, window, &event);
            /* Keep press/release transitions in separate frames, even when a
             * complete short tap was queued while the camera was busy. */
            if (event.type == ButtonPress || event.type == ButtonRelease) break;
          }
          nk_input_end(ctx);
          /* Burst scheduling: the hold arms once per window and the paint gate
           * below re-arms it AFTER painting, so sustained input still
           * rasterizes at ~15 Hz instead of deferring forever. A discrete key
           * breaks the hold and paints immediately. */
          if (discrete) lut_paint_hold = 0;
          else if (continuous >= 2 && lut_paint_hold <= 0) lut_paint_hold = tick + 66;
        }
        if(lut_mode&&lut_reference_press&&!lut_reference_original&&now_ms()-lut_reference_pressed_at>=300){lut_reference_original=1;lut_reference_dirty=dirty=1;}
        if (mfw_return_requested) { mfw_return_requested = 0; return_mfw_menu(); }
        if (mfw_open_lut && !queue.close_requested) open_mfw_lut();
        panel_check_visibility();
        poll_child();
        refresh_services(0);
        /* Animate only an active control transaction, never the idle panel.
         * Completion comes from the backend, not a cosmetic timer. */
        if (lut_mode && (child || queue.commit_requested) && tick >= lut_apply_tick) {
            lut_apply_tick = tick + 250; dirty = 1;
        }
        /* Unmap as soon as a close is requested: while the backend finishes
         * its control transaction the panel must not keep covering the frame
         * or hold the keyboard focus away from the camera UI. */
        if (queue.close_requested) {
            nx_panel_input_hide(&panel_input);
            nx_panel_input_drain_s1(&panel_input, request_s1_close);
        }
        /* Closing waits for an in-flight write; it discards unapplied browsing requests. */
        if (queue.close_requested && !queue.commit_requested && !child) break;
        if (!child && (!queue.close_requested || queue.commit_requested) && (index = nx_queue_take(&queue, now_ms())) >= 0)
            if (!start_child(1, index, NULL)) { nx_queue_done(&queue, 0); message("后端启动失败"); }
        if (tick >= next_frame && !quit && !queue.close_requested) {
            /* A continuous drag/wheel burst defers rasterization to ~15 Hz;
             * dirty stays set, so the frame after the burst always paints the
             * final state. Single keys never enter this hold. */
            if (dirty && tick >= lut_paint_hold) {
                long long draw_started = now_ms(), elapsed;
                if(refresh)xlib.surf->force_full=1;
                dirty = 0; draw(); render_count++; painted = 1;
                /* Paint first, then re-arm: sustained input keeps rasterizing
                 * periodically instead of pushing the deadline forward. */
                if (continuous >= 2 && !discrete) lut_paint_hold = draw_started + 66;
                else lut_paint_hold = 0;
                if (lut_mode && lut_page_requested) {
                    XSync(display, False);
                    fprintf(stderr, "LUT_PAGE pid=%ld shown_ms=%lld elapsed_ms=%lld\n",
                        (long)getpid(), now_ms(), now_ms()-lut_page_requested);
                    lut_page_requested = 0;
                }
                if (xlib.surf->present_failed) queue.close_requested = 1;
                elapsed = now_ms() - draw_started;
                if (elapsed > max_draw_ms) max_draw_ms = elapsed;
            } else if (refresh && render_count) {
                /* Retained image: expose needs one upload, no layout/font work. */
                XSetClipMask(display, xlib.surf->gc, None);
                nk_xsurf_blit(window, xlib.surf, (unsigned)width, (unsigned)height);
                XFlush(display); blits++;
            }
            /* Include rasterization in the frame budget. Adding another
             * 33 ms after a slow draw unnecessarily delays queued input. */
            if (events || painted || refresh) next_frame = tick + 33;
            if (trace_frames && (events || painted || refresh))
                fprintf(stderr, "PANEL_FRAME pid=%ld dequeued_ms=%lld submitted_ms=%lld events=%d painted=%d exposed=%d\n",
                    (long)getpid(), input_dequeued, now_ms(), events, painted, refresh);
        }
        if (shot && render_count) { snapshot(shot); break; }
        if (quit) break;
        tick = now_ms(); pending = XPending(display);
        if (child) delay = 100; /* only a running backend needs periodic reaping */
        if (lut_mode && (child || queue.commit_requested) && lut_apply_tick-tick < delay)
            delay = lut_apply_tick-tick;
        if(lut_mode&&live&&tick<lut_preview_until&&lut_preview_due-tick<delay)
            delay=lut_preview_due-tick;
        if(lut_mode&&lut_reference_press&&!lut_reference_original&&lut_reference_pressed_at+300-tick<delay)
            delay=lut_reference_pressed_at+300-tick;
        if (services_visible() && service_due - tick < delay) delay = service_due - tick;
        if (!child && queue.desired >= 0 && (queue.desired != queue.applied || queue.commit_requested))
            if (queue.due - tick < delay) delay = queue.due - tick;
        if (lifetime > 0 && !queue.close_requested && started + lifetime - tick < delay)
            delay = started + lifetime - tick;
        /* A hidden panel waiting for its backend cannot draw. Its stale frame
         * deadline must not turn the child wait into a zero-timeout spin. */
        if (!queue.close_requested) {
            if (pending && next_frame - tick < delay) delay = next_frame - tick;
            /* A paint needs both deadlines. Waking on the older frame
             * deadline while the hold is active would busy-loop in select. */
            long long paint_due = next_frame > lut_paint_hold ? next_frame : lut_paint_hold;
            if (dirty && paint_due - tick < delay) delay = paint_due - tick;
        }
        if (delay < 0) delay = 0;
        FD_ZERO(&fds);
        /* Pending events are already in the client queue. Watching a readable
         * server fd before the frame deadline would spin under an input burst. */
        if (!pending) FD_SET(ConnectionNumber(display), &fds);
        if (child_fd >= 0 && !pipe_eof) FD_SET(child_fd, &fds);
        if (panel_input.ev_fd >= 0) FD_SET(panel_input.ev_fd, &fds);
        wait.tv_sec = (long)(delay/1000); wait.tv_usec = (long)(delay%1000)*1000;
        {
            int highest = ConnectionNumber(display);
            if (child_fd > highest) highest = child_fd;
            if (panel_input.ev_fd > highest) highest = panel_input.ev_fd;
            alarm(0);
            select(highest+1, &fds, NULL, NULL, &wait);
        }
        wakeups++;
    }
    if (trace_frames && lut_mode)
        fprintf(stderr,"PANEL_FINAL selected=%d field=%d strength=%d tune=%d,%d,%d,%d,%d,%d,%d\n",
            selected,lut_field,lut_strength,lut_tune.value[0],lut_tune.value[1],lut_tune.value[2],
            lut_tune.value[3],lut_tune.value[4],lut_tune.value[5],lut_tune.value[6]);
    fprintf(stderr, "EXIT renders=%d elapsed_ms=%lld wakeups=%d blits=%d max_draw_ms=%lld\n",
        render_count, now_ms()-started, wakeups, blits, max_draw_ms);
    fprintf(stderr,"PANEL_UPLOAD full=%u partial=%u skipped=%u pixels=%llu\n",xlib.surf->full_uploads,
        xlib.surf->partial_uploads,xlib.surf->skipped_uploads,xlib.surf->uploaded_pixels);
    log_resources("exit");
    alarm(5);
    nx_panel_input_restore_focus(&panel_input);
    nx_panel_input_drain_s1(&panel_input, request_s1_close);
    nk_xlib_shutdown(); nk_xfont_del(display, font);nk_xfont_del(display,hint_font);
    alarm(0);
    nx_panel_input_close_evdev(&panel_input);
    alarm(5);
    XDestroyWindow(display, window); XSync(display, False);
    lut_save_navigation();
    if (mfw_stock_key) {
        Window target; int revert; XWindowAttributes target_attr;
        XGetInputFocus(display, &target, &revert);
        KeyCode code = XKeysymToKeycode(display, XStringToKeysym(mfw_stock_key));
        if (code && target != None && target != PointerRoot && target != window &&
            XGetWindowAttributes(display, target, &target_attr) && target_attr.map_state == IsViewable) {
            int down = XTestFakeKeyEvent(display, code, True, 0);
            int up = XTestFakeKeyEvent(display, code, False, 0);
            XSync(display, False);
            fprintf(stderr, "MFW stock_input key=%s press_sent=%d release_sent=%d delivery_only=1\n", mfw_stock_key, down, up);
        } else fprintf(stderr, "MFW stock_input key=%s unavailable\n", mfw_stock_key);
    }
    XCloseDisplay(display); close(lockfd); alarm(0);
    return 0;
}
