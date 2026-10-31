/* Adapted from WApaperplane/metol ddbe8b1, AGPL-3.0; see ATTRIBUTION.md.
 * NX300M port: rotated panel, stock key names, native LUT staging backend.
 * NX-KS menus + persistent FilmLab strip. Nuklear / CPU XImage, no EFL.
 * The viewfinder stays on its existing camera plane: no frame capture or blit.
 * Rendering is event driven; the only timer is backend/debounce housekeeping.
 */
#define _POSIX_C_SOURCE 200809L
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
extern Bool XTestFakeKeyEvent(Display *, unsigned int, Bool, unsigned long);

static struct nx_recipe recipes[NXUI_MAX_RECIPES];
static struct nx_item items[NXUI_MAX_ITEMS];
static struct nx_queue queue;
static const char *root, *engine, *arm_runner;
static int count, item_count, selected, menu, page, live, dirty = 1, quit;
/* LUT mode stages a table for the NX300M firmware's next LUT call. MENU
 * restores the entry selection; OK, retain and half-press keep it staged. */
static int lut_mode = 0;
static int mfw_services, mfw_selected, mfw_readonly, mfw_open_lut;
static const char *mfw_stock_key;
static char mfw_status[1024] = "正在读取机身服务状态…";
static int lut_keep = 0;
static int lut_strength = 100;
static int touch_x, touch_active;
static long long close_flag_check;
static struct nx_panel_input panel_input = {.ev_fd = -1};
static int busy_kind, child_fd = -1, output_len, child_timed_out;
static pid_t child;
static pid_t mfw_retired_child;
static int mfw_child_readonly;
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
static int panel_visibility = VisibilityFullyObscured, panel_raise_retry, panel_yielded;
/* Do not rely on a window manager to restore input after this panel closes.
 * Stock windows can also raise themselves or reclaim focus during a transition.
 * Camera navigation arrives as X KeyPress; an invisible panel must yield it. */
static void request_s1_close(void) {
    /* Closing keeps the applied session state and waits for an in-flight
     * backend. It must not enqueue a second recipe application. */
    if (lut_mode) lut_keep = 1;
    panel_yielded = 1;
    queue.close_requested = 1;
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
static Window panel_stock_parent(void) {
    Window candidate; int revert;
    XGetInputFocus(display, &candidate, &revert);
    XErrorHandler old = XSetErrorHandler(nx_panel_input_ignore_error);
    Window found = None;
    for (unsigned depth = 0; depth < 16 && candidate != None && candidate != PointerRoot &&
            candidate != RootWindow(display, screen); depth++) {
        XClassHint hint; XWindowAttributes attributes;
        memset(&hint, 0, sizeof hint);
        int match = 0;
        if (XGetClassHint(display, candidate, &hint)) {
            match = (hint.res_name && (!strcmp(hint.res_name, "di-camera-app") || !strcmp(hint.res_name, "di-camera-app-nx300"))) ||
                (hint.res_class && (!strcmp(hint.res_class, "di-camera-app") || !strcmp(hint.res_class, "di-camera-app-nx300")));
            if (hint.res_name) XFree(hint.res_name);
            if (hint.res_class) XFree(hint.res_class);
        }
        if (match && XGetWindowAttributes(display, candidate, &attributes) && attributes.map_state == IsViewable &&
                attributes.depth == DefaultDepth(display, screen) && attributes.width >= height && attributes.height >= width) {
            found = candidate; panel_parent_height = attributes.height; break;
        }
        Window tree_root, parent, *children = NULL; unsigned count;
        if (!XQueryTree(display, candidate, &tree_root, &parent, &children, &count)) break;
        if (children) XFree(children);
        candidate = parent;
    }
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
    if (child && busy_kind == 1 && queue.desired >= 0 && queue.desired != queue.running)
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
    int next = mfw_services ? 464 : (menu ? 336 : (lut_mode ? 272 : 216));
    if (next > DisplayWidth(display, screen)) next = DisplayWidth(display, screen);
    dirty = 1;
    if (next == height) return;
    height = next;
    XMoveResizeWindow(display, window, 0, (panel_parent_height-width)/2,
        (unsigned)height, (unsigned)width);
    /* Resize only our CPU image alongside the native window. */
    nk_xsurf_resize(xlib.surf, (unsigned)width, (unsigned)height);
    dirty = 1;
}
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
static void choose(int index) {
    if (count <= 0 || queue.close_requested) return;
    selected = (index + count) % count;
    /* An explicit tap also reapplies after a camera/legacy-menu effect change. */
    queue.applied = -1;
    nx_queue_select(&queue, selected, now_ms());
    preview_status();
}
static void change_lut_strength(int delta) {
    /* The queue identifies work by recipe index. Keep strength fixed while a
     * child owns that index so its completion cannot mark a newer strength
     * as applied without sending it to the engine. */
    int next;
    if (!lut_mode || child || queue.close_requested || count <= 0) return;
    next = lut_strength + delta;
    if (next < 0) next = 0;
    if (next > 100) next = 100;
    if (next == lut_strength) return;
    lut_strength = next;
    choose(selected);
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
    if (lut_mode && !strcmp(action, "apply")) {
        char strength[4];
        snprintf(strength, sizeof strength, "%d", lut_strength);
        if (arm_runner && *arm_runner)
            execl(arm_runner, arm_runner, "-cpu", "cortex-a9", "-L", "/", engine, action, key, strength, (char *)0);
        else execl(engine, engine, action, key, strength, (char *)0);
        return;
    }
    if (arm_runner && *arm_runner)
        execl(arm_runner, arm_runner, "-cpu", "cortex-a9", "-L", "/", engine, action, key, (char *)0);
    else execl(engine, engine, action, key, (char *)0);
}
static int start_child(int kind, int index, const char *command) {
    int pipefd[2]; pid_t pid;
    if (child) return 0;
    if (!live) {
        if (kind == 1) {
            nx_queue_done(&queue, 1);
            message("演示模式 · 已选择，未写入相机");
            if (queue.committing) quit = 1;
        } else message("演示模式 · 未执行菜单命令");
        return 1;
    }
    if (pipe(pipefd) < 0) return 0;
    pid = fork();
    if (pid < 0) { close(pipefd[0]); close(pipefd[1]); return 0; }
    if (pid == 0) {
        char envpath[1024]; const char *old = getenv("PATH");
        setpriority(PRIO_PROCESS, 0, 19);
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
    child_started = now_ms(); child_timed_out = 0; output_len = 0;
    output[0] = 0;
    if (kind == 1) preview_status(); else message("正在执行…");
    return 1;
}
static int mfw_action_allowed(const char *action) {
    const char *allowed[] = {"status", "network-info", "lut", "menu", "gallery", "disable-autostart", "enable-autostart", "close"};
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
    int pipefd[2]; pid_t pid; char launcher[NXUI_PATH];
    if (child || mfw_retired_child || !mfw_action_allowed(action)) return 0;
    if (snprintf(launcher, sizeof launcher, "%s/mfw-panel-actions.sh", root) >= (int)sizeof launcher || pipe(pipefd) < 0) return 0;
    pid_t parent = getpid();
    pid = fork();
    if (pid < 0) { close(pipefd[0]); close(pipefd[1]); return 0; }
    if (!pid) {
        if (setpgid(0, 0) || prctl(PR_SET_PDEATHSIG, SIGKILL) || getppid() != parent) _exit(126);
        setpriority(PRIO_PROCESS, 0, 19);
        signal(SIGINT, SIG_DFL); signal(SIGTERM, SIG_DFL); signal(SIGCHLD, SIG_DFL);
        close(pipefd[0]);
        dup2(pipefd[1], STDOUT_FILENO); dup2(pipefd[1], STDERR_FILENO);
        close(pipefd[1]); mfw_child_close_fds();
        execl("/bin/sh", "sh", launcher, action, (char *)0);
        _exit(127);
    }
    setpgid(pid, pid); close(pipefd[1]); fcntl(pipefd[0], F_SETFL, O_NONBLOCK);
    child_fd = pipefd[0]; child = pid; busy_kind = 5;
    mfw_child_readonly = !strcmp(action, "status") || !strcmp(action, "network-info");
    mfw_cancel_started = 0;
    pipe_eof = child_timed_out = output_len = 0; output[0] = 0; child_started = now_ms();
    if (mfw_child_readonly)
        mfw_status_message(!strcmp(action, "status") ? "正在读取机身服务状态…" : "正在读取网络连接信息…");
    return 1;
}
static void activate_mfw_item(int index) {
    if (index < 0 || index >= item_count) return;
    const char *action = items[index].command;
    if (!mfw_action_allowed(action)) { message("此操作不可用"); return; }
    if (!strcmp(action, "close")) { queue.close_requested = 1; return; }
    if (child) return;
    if (mfw_readonly && strcmp(action, "status") && strcmp(action, "network-info")) { message("截图模式不执行操作"); return; }
    if (!strcmp(action, "lut")) {
        mfw_open_lut = 1; queue.close_requested = 1; return;
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
        if (age > 12000 && !child_timed_out) {
            child_timed_out = 1; kill(-child, SIGTERM);
            message("后端超时 · 正在停止，请检查相机状态");
        }
        if (age > 13000) kill(-child, SIGKILL);
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
        if (success) {
            snprintf(mfw_status, sizeof mfw_status, "%.*s", (int)sizeof(mfw_status)-1, output);
            message("");
        } else if (mfw_child_readonly) {
            mfw_status_message(mfw_cancel_started
                ? (queue.close_requested ? "读取已取消" : "读取超时 · 请重试")
                : "服务信息读取失败 · 请重试");
        } else message("服务操作失败 · 请查看面板日志");
    } else if (busy_kind == 1) {
        was_commit = queue.committing;
        nx_queue_done(&queue, success);
        if (queue.desired >= 0 && queue.desired != busy_recipe) preview_status();
        else if (success) {
            if (lut_mode) snprintf(status, sizeof status, "已暂存 %s · 强度 %d%% · 等待原厂 LUT 重载", recipes[busy_recipe].label, lut_strength);
            else snprintf(status, sizeof status, "预览已应用：%s", recipes[busy_recipe].label);
            dirty = 1;
        } else message("应用失败 · 可重新选择以重试");
        if (was_commit && success) quit = 1;
        if (!success && !panel_yielded) {
            lut_keep = 0;
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
static void draw(void) {
    char line[320]; int i, start;
    struct nk_color accent = nk_rgb(203, 233, 153);
    if (mfw_services) {
        if (nk_begin(ctx, "NXWA", nk_rect(0, 0, (float)width, (float)height), NK_WINDOW_NO_SCROLLBAR)) {
            nk_layout_row_dynamic(ctx, 26, 1);
            nk_label_colored(ctx, "NX300M  /  机身服务", NK_TEXT_LEFT, accent);
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
            nk_layout_row_dynamic(ctx, 48, 2);
            for (i = 0; i < item_count; i++) {
                snprintf(line, sizeof line, "%s%s", i == mfw_selected ? "› " : "", items[i].label);
                if (nk_button_label(ctx, line)) { mfw_selected = i; activate_mfw_item(i); }
            }
            nk_layout_row_dynamic(ctx, 23, 1);
            nk_label_colored(ctx, child ? "正在读取…" : status, NK_TEXT_LEFT, nk_rgb(171, 182, 190));
        }
        nk_end(ctx); nk_xlib_render(window, nk_rgb(24, 29, 33)); XFlush(display);
        return;
    }
    if (nk_begin(ctx, "NXWA", nk_rect(0, 0, (float)width, (float)height), NK_WINDOW_NO_SCROLLBAR)) {
        nk_layout_row_begin(ctx, NK_STATIC, 24, 3);
        nk_layout_row_push(ctx, 460);
        nk_label_colored(ctx, menu ? "NX300M  /  系统设置"
                            : (lut_mode ? "LUT  /  3D 滤镜" : (live ? "FILMLAB  /  滤镜预览" : "FILMLAB  /  配方浏览")),
                         NK_TEXT_LEFT, accent);
        nk_layout_row_push(ctx, 104);
        if (nk_button_label(ctx, menu ? "胶片" : "设置")) {
            if (menu) { menu = 0; resize_panel(); }
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
                /* A tap always applies, including on the highlighted row: the
                 * camera or the legacy menu may have changed ISP state since
                 * the preview, so the marker is not proof the value is live.
                 * Nuklear reports a press once and flips *picked, so the old
                 * "&& idx != selected" guard made the highlighted row inert. */
                if (nk_selectable_label(ctx, line, NK_TEXT_CENTERED, &picked))
                    choose(idx);
            }
            snprintf(line, sizeof line, "%d/%d   R%d G%d B%d   H%d S%d 锐%d 对比%d", selected+1, count,
                recipes[selected].value[0], recipes[selected].value[1], recipes[selected].value[2],
                recipes[selected].value[3], recipes[selected].value[4], recipes[selected].value[5], recipes[selected].value[6]);
            if (lut_mode) snprintf(line, sizeof line, "%d/%d   NX300M 9×9×9 LUT · 待实机校色", selected+1, count);
            nk_layout_row_dynamic(ctx, 16, 1);
            nk_label_colored(ctx, line, NK_TEXT_LEFT, nk_rgb(171, 182, 190));
            if (lut_mode) {
                int locked = child || queue.close_requested;
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
            if (nk_button_label(ctx, lut_mode ? "保留并关闭" : (live ? "应用并关闭" : "选择并关闭")) && !queue.close_requested) {
                if (lut_mode) {
                    lut_keep = 1;
                    nx_queue_select(&queue, selected, now_ms()-NXUI_DEBOUNCE_MS);
                    queue.commit_requested = 1; queue.close_requested = 1;
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
    const char *font_name = "SDIC_GP_US:pixelsize=19";
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
        initial_menu = "gui_mfw.NX300M";
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
        if (launcher_lock < 0 || flock(launcher_lock, LOCK_EX|LOCK_NB) != 0) return 3;
        fcntl(launcher_lock, F_SETFD, FD_CLOEXEC);
    }
    lockfd = open(ui_path, O_CREAT|O_RDWR, 0600);
    if (lockfd < 0 || flock(lockfd, LOCK_EX|LOCK_NB) != 0) return 3;
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
    count = mfw_services ? 0 : nx_load_recipes(recipe_path, recipes, NXUI_MAX_RECIPES);
    if (count < 0) count = 0;
    if (lut_mode) {
        FILE *selection = fopen("/var/run/nx300m-lut-selection", "r");
        if (selection) {
            unsigned int generation, strength = 100; char path[256], candidate[256];
            if (fscanf(selection, "%u %255s %u", &generation, path, &strength) >= 2) {
                for (i = 0; i < count; i++) {
                    int length = snprintf(candidate, sizeof candidate, "%s/looks/%.63s/table.d4lut", root, recipes[i].key);
                    if (length < 0 || length >= (int)sizeof candidate) continue;
                    if (!strcmp(path, candidate)) {
                        selected = i;
                        lut_strength = strength <= 100 ? (int)strength : 100;
                        snprintf(status, sizeof status, "当前选择 %s · 强度 %d%% · 在原厂重载时加载", recipes[i].label, lut_strength);
                        break;
                    }
                }
            }
            fclose(selection);
        }
    }
    recipes_ready = now_ms();
    display = XOpenDisplay(NULL);
    if (!display) { fprintf(stderr, "XOpenDisplay failed\n"); return 4; }
    display_ready = now_ms();
    screen = DefaultScreen(display); width = 720;
    fprintf(stderr, "PANEL_DISPLAY vendor=%s release=%d raw=%dx%d depth=%d\n", ServerVendor(display), VendorRelease(display),
        DisplayWidth(display, screen), DisplayHeight(display, screen), DefaultDepth(display, screen));
    /* Allocate the actual entry layout once, rather than create a small
     * pixmap and immediately discard it when load_menu resizes the panel. */
    height = mfw_services ? 464 : (initial_menu ? 336 : (lut_mode ? 272 : 216));
    if (height > DisplayWidth(display, screen)) height = DisplayWidth(display, screen);
    if (DisplayHeight(display, screen) < width || DisplayWidth(display, screen) < height) {
        fprintf(stderr, "NX300M raw display must be at least %dx720\n", height); XCloseDisplay(display); return 4;
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
    /* This panel has buttons, no draggable controls. Touch coordinates arrive
     * on press/release; subscribing to motion needlessly repaints while sliding. */
    attr.event_mask = ExposureMask|KeyPressMask|KeyReleaseMask|ButtonPressMask|ButtonReleaseMask|StructureNotifyMask|
        VisibilityChangeMask|FocusChangeMask;
    window = XCreateWindow(display, panel_parent, 0, (panel_parent_height-width)/2,
        (unsigned)height, (unsigned)width, 0, DefaultDepth(display, screen), InputOutput,
        DefaultVisual(display, screen), CWOverrideRedirect|CWBackPixel|CWEventMask, &attr);
    XStoreName(display, window, "Metol-NX300M");
    wm_delete = XInternAtom(display, "WM_DELETE_WINDOW", False);
    XSetWMProtocols(display, window, &wm_delete, 1);
    window_ready = now_ms();
    font = font_file ? panel_font_file(font_file, font_metadata) : nk_xfont_create(display, font_name);
    if (!font || !font->ft) { fprintf(stderr, "font unavailable\n"); return 5; }
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
    ctx->style.window.background = nk_rgb(24, 29, 33);
    ctx->style.window.fixed_background = nk_style_item_color(nk_rgb(24, 29, 33));
    ctx->style.button.normal = nk_style_item_color(nk_rgb(45, 53, 59));
    ctx->style.button.hover = ctx->style.button.normal;
    ctx->style.button.active = nk_style_item_color(nk_rgb(88, 111, 79));
    ctx->style.button.text_normal = nk_rgb(234, 239, 231);
    ctx->style.button.rounding = 4;
    if (initial_menu && !load_menu(initial_menu)) return 6;
    if (mfw_services) {
        for (i = 0; i < item_count; i++)
            if (items[i].checkbox || !mfw_action_allowed(items[i].command)) return 6;
    }
    menu_ready = now_ms();
    nx_panel_input_init(&panel_input, display, window);
    focus_ready = now_ms();
    /* Prepare glyphs and the retained image while the stock view is still
     * visible. Mapping an empty black window before this work obscures it. */
    first_draw_started = now_ms();
    panel_stage("first-draw-begin");
    draw();
    if (xlib.surf->present_failed) {
        fprintf(stderr, "Panel image unavailable; stock focus was not changed\n");
        nk_xlib_shutdown(); nk_xfont_del(display, font);
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
        nk_xlib_shutdown(); nk_xfont_del(display, font);
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
    if (mfw_services && !start_mfw_action("status")) message("服务状态读取未能启动");
    /* NX500 capdtm command numbers do not apply to DRIMeIV. */
    while (!quit) {
        alarm(5);
        XEvent event; fd_set fds; struct timeval wait; int index, pending, events = 0, refresh = 0, painted = 0;
        long long tick = now_ms(), delay = 60000, input_dequeued = -1;
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
         * and render to 10 Hz, including an expose/key-repeat event storm. */
        if (tick >= next_frame) {
          nk_input_begin(ctx);
          while (events < 64 && XPending(display)) {
            XNextEvent(display, &event);
            events++;
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
            if (event.type == MotionNotify || event.type == NoExpose) continue;
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
            if (event.type == ClientMessage && (Atom)event.xclient.data.l[0] == wm_delete) queue.close_requested = 1;
            if (nx_panel_input_handle_s1(&panel_input, &event, request_s1_close)) continue;
            if (queue.close_requested || panel_visibility != VisibilityUnobscured) continue;
            if (event.type == KeyPress || event.type == KeyRelease)
                fprintf(stderr, "PANEL_KEY type=%d code=%u symbol=0x%lx\n", event.type, event.xkey.keycode, XLookupKeysym(&event.xkey, 0));
            if (event.type == KeyPress) {
                KeySym key = XLookupKeysym(&event.xkey, 0);
                /* Only standard keysyms are asserted. Camera wheel mappings need measurement. */
                if (key == XK_Escape || key == XK_Menu) queue.close_requested = 1;
                else if (mfw_services) {
                    if (key == XK_Right || key == XK_KP_Right) mfw_selected = (mfw_selected+1)%item_count;
                    else if (key == XK_Left || key == XK_KP_Left) mfw_selected = (mfw_selected+item_count-1)%item_count;
                    else if (key == XK_Down || key == XK_KP_Down) mfw_selected = (mfw_selected+2)%item_count;
                    else if (key == XK_Up || key == XK_KP_Up) mfw_selected = (mfw_selected+item_count-2)%item_count;
                    else if (key == XK_Return || key == XK_KP_Enter) activate_mfw_item(mfw_selected);
                }
                else if (!menu && (key == XK_Right || key == XK_Down || key == XK_KP_Right || key == XK_KP_Down)) choose(selected+1);
                else if (!menu && (key == XK_Left || key == XK_Up || key == XK_KP_Left || key == XK_KP_Up)) choose(selected-1);
                else if (!menu && (key == XK_Return || key == XK_KP_Enter) && !child && count > 0) {
                    if (lut_mode) lut_keep = 1;
                    nx_queue_select(&queue, selected, now_ms()-NXUI_DEBOUNCE_MS);
                    queue.commit_requested = 1; queue.close_requested = 1;
                }
            }
            if (event.type == ButtonPress && !menu && (event.xbutton.button == 4 || event.xbutton.button == 5))
                choose(selected + (event.xbutton.button == 4 ? -1 : 1));
            /* Touchscreens can send a tap without a preceding MotionNotify. */
            if (event.type == ButtonPress || event.type == ButtonRelease) {
                int raw_x = event.xbutton.x;
                event.xbutton.x = event.xbutton.y;
                event.xbutton.y = height - 1 - raw_x;
                nk_input_motion(ctx, event.xbutton.x, event.xbutton.y);
            }
            /* 左右滑动手势：超阈值切换选中；release 移出控件避免被当成点击 */
            if (event.type == ButtonPress && !menu) { touch_x = event.xbutton.x; touch_active = 1; }
            if (event.type == ButtonRelease && touch_active) {
                int dx = event.xbutton.x - touch_x;
                int step = (dx >= 0 ? dx : -dx) / 45;   /* 每 45px 切一项：滑得远切得快 */
                touch_active = 0;
                if (step < 1 && (dx >= 30 || dx <= -30)) step = 1;   /* 轻扫阈值 30px */
                if (!menu && step > 0) {
                    choose(selected + (dx < 0 ? step : -step));
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
        }
        panel_check_visibility();
        poll_child();
        refresh_services(0);
        /* Unmap as soon as a close is requested: while the backend finishes
         * (up to the 12 s timeout) the panel must not keep covering the frame
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
            if (dirty) {
                long long draw_started = now_ms(), elapsed;
                dirty = 0; draw(); render_count++; painted = 1;
                if (xlib.surf->present_failed) queue.close_requested = 1;
                elapsed = now_ms() - draw_started;
                if (elapsed > max_draw_ms) max_draw_ms = elapsed;
            } else if (refresh && render_count) {
                /* Retained image: expose needs one upload, no layout/font work. */
                XSetClipMask(display, xlib.surf->gc, None);
                nk_xsurf_blit(window, xlib.surf, (unsigned)width, (unsigned)height);
                XFlush(display); blits++;
            }
            if (events || painted || refresh) next_frame = now_ms() + 100;
            if (trace_frames && (events || painted || refresh))
                fprintf(stderr, "PANEL_FRAME pid=%ld dequeued_ms=%lld submitted_ms=%lld events=%d painted=%d exposed=%d\n",
                    (long)getpid(), input_dequeued, now_ms(), events, painted, refresh);
        }
        if (shot && render_count) { snapshot(shot); break; }
        tick = now_ms(); pending = XPending(display);
        if (child) delay = 100; /* only a running backend needs periodic reaping */
        if (services_visible() && service_due - tick < delay) delay = service_due - tick;
        if (!child && queue.desired >= 0 && (queue.desired != queue.applied || queue.commit_requested))
            if (queue.due - tick < delay) delay = queue.due - tick;
        if (lifetime > 0 && !queue.close_requested && started + lifetime - tick < delay)
            delay = started + lifetime - tick;
        if ((dirty || pending) && next_frame - tick < delay) delay = next_frame - tick;
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
    fprintf(stderr, "EXIT renders=%d elapsed_ms=%lld wakeups=%d blits=%d max_draw_ms=%lld\n",
        render_count, now_ms()-started, wakeups, blits, max_draw_ms);
    log_resources("exit");
    alarm(5);
    nx_panel_input_restore_focus(&panel_input);
    nx_panel_input_drain_s1(&panel_input, request_s1_close);
    nk_xlib_shutdown(); nk_xfont_del(display, font);
    alarm(0);
    /* MENU/Close restores the selection captured on entry. OK, the retain
     * button and half-press leave the staged selection for firmware reload. */
    if (lut_mode && live && !lut_keep) {
            pid_t restore_pid = fork();
            if (restore_pid == 0) {
                close(ConnectionNumber(display));
                exec_lut_engine("off", NULL);
                _exit(127);
            }
            if (restore_pid > 0) waitpid(restore_pid, NULL, 0);
    }
    nx_panel_input_close_evdev(&panel_input);
    alarm(5);
    XDestroyWindow(display, window); XSync(display, False);
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
    if (mfw_open_lut) {
        char launcher[NXUI_PATH];
        /* The Fn panel roots nxui at its six-item menu directory, which has no
         * LUT assets. The service root (NX_FN_SERVICE_ROOT) owns lut-panel.sh
         * and its engine, so resolve the launch from there when it is set. */
        const char *service_root = getenv("NX_FN_SERVICE_ROOT");
        const char *lut_base = service_root && *service_root ? service_root : root;
        if (snprintf(launcher,sizeof launcher,"%s/lut-panel.sh",lut_base)>=(int)sizeof launcher) return 6;
        /* exec drops the CLOEXEC service-panel lock before LUT acquires it.
         * The previous camera focus and X11 resources have already returned. */
        execl("/bin/sh","sh",launcher,(char *)0);
        fprintf(stderr,"LUT panel launch failed errno=%d\n",errno);return 127;
    }
    return 0;
}
