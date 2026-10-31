/* NX300M 1.15 RGB channel gain bridge.
 *
 * The stock color nodes build a fresh row-major 3x3 matrix for each
 * parameter update and pass it to the original IPCS/IPCM setters. This bridge
 * keeps that matrix as the restoration base, scales its output rows, and then
 * calls the same original setters. It never writes WB/AWB state.
 */
extern void *dlsym(void *, const char *);
extern void *fopen(const char *, const char *);
extern int fscanf(void *, const char *, ...);
extern int fprintf(void *, const char *, ...);
extern int fclose(void *);
extern int rename(const char *, const char *);
extern int unlink(const char *);
extern int getpid(void);
extern int kill(int, int);
extern int *__errno_location(void);
extern int snprintf(char *, unsigned long, const char *, ...);
extern long time(long *);
extern void *stderr;

typedef unsigned int u32;
typedef signed int i32;
typedef struct { u32 col_mat[9]; } color_matrix;
typedef int (*color_setter)(color_matrix *);
typedef void *(*ecore_timer_add_fn)(double, int (*)(void *), void *);

enum { NEUTRAL = 100, MATRIX_MIN = -1024, MATRIX_MAX = 1023 };
static volatile int color_lock;
static unsigned int percent[3] = {NEUTRAL, NEUTRAL, NEUTRAL};
static unsigned int row_rgb_generation=1;
static unsigned int last_request;
static unsigned int capture_calls, preview_calls;
static unsigned int capture_range_bypass, preview_range_bypass;
static int capture_last_rc, preview_last_rc;
static int capture_base_valid, preview_base_valid;
static int capture_modified, preview_modified;
static color_matrix capture_base, preview_base;
static int attached, ready_written;
static color_setter stock_ipcs, stock_ipcm;
static int color_process_pid;
static int color_process_current(void) { return color_process_pid == getpid(); }

static int bridge_marker_pid(const char *path) {
    int pid=0;
    void *file=fopen(path,"r");
    if(file) { if(fscanf(file,"%d",&pid)!=1) pid=0; fclose(file); }
    return pid;
}
static int bridge_marker_owned(const char *path) {
    return color_process_current() && bridge_marker_pid(path)==getpid();
}
static int bridge_write_ready(const char *path) {
    if(!color_process_current()) return 0;
    int pid=getpid(), previous=bridge_marker_pid(path);
    if(previous==pid) return 1;
    /* A second Ecore consumer must not replace a running bridge's marker.
     * A new process can publish readiness over an exited process's marker. */
    if(previous>0 && (!kill(previous,0) || *__errno_location()!=3)) return 0;
    char temporary[160];
    if(snprintf(temporary,sizeof temporary,"%s.%d.tmp",path,pid)>=(int)sizeof temporary) return 0;
    void *file=fopen(temporary,"w");
    if(!file) return 0;
    int written=fprintf(file,"%d\n",pid), closed=fclose(file);
    if(written>0 && !closed && !rename(temporary,path)) return 1;
    unlink(temporary); return 0;
}
static void bridge_cleanup(const char *stem) {
    char ready[128], path[160];
    snprintf(ready,sizeof ready,"/var/run/nx300m-%s-ready",stem);
    if(!bridge_marker_owned(ready)) return;
    const char *suffixes[]={"ready.tmp","request","request.tmp","response","response.tmp","context.json"};
    for(unsigned int i=0;i<sizeof suffixes/sizeof suffixes[0];++i) {
        snprintf(path,sizeof path,"/var/run/nx300m-%s-%s",stem,suffixes[i]);
        unlink(path);
    }
    /* Keep the ownership proof until all files in this channel are gone. */
    unlink(ready);
}

static void lock_color(void) {
    if(!color_process_current()) return;
    while (__sync_lock_test_and_set(&color_lock, 1)) { }
}
static void unlock_color(void) { if(color_process_current()) __sync_lock_release(&color_lock); }

static color_setter resolve_ipcs(void) {
    color_setter setter = __atomic_load_n(&stock_ipcs, __ATOMIC_ACQUIRE);
    if (!setter) {
        setter = (color_setter)dlsym((void *)-1, "ipcs_color_correction_param");
        if (setter) __atomic_store_n(&stock_ipcs, setter, __ATOMIC_RELEASE);
    }
    return __atomic_load_n(&stock_ipcs, __ATOMIC_ACQUIRE);
}
static color_setter resolve_ipcm(void) {
    color_setter setter = __atomic_load_n(&stock_ipcm, __ATOMIC_ACQUIRE);
    if (!setter) {
        setter = (color_setter)dlsym((void *)-1, "ipcm_color_correction_param");
        if (setter) __atomic_store_n(&stock_ipcm, setter, __ATOMIC_RELEASE);
    }
    return __atomic_load_n(&stock_ipcm, __ATOMIC_ACQUIRE);
}

#include "source_rgb_style.h"
#include "sharpness_style.h"
#include "contrast_style.h"
#include "chroma_style.h"
#include "recipe_style.h"

static int is_neutral(void) {
    return percent[0] == NEUTRAL && percent[1] == NEUTRAL && percent[2] == NEUTRAL;
}

/* Round to nearest symmetrically; leave the native coefficient scale unchanged. */
static int scale_coefficient(i32 value, unsigned int gain, i32 *result) {
    int negative = value < 0;
    unsigned int magnitude = (unsigned int)(negative ? -value : value);
    unsigned int scaled = (magnitude * gain + 50u) / 100u;
    int signed_value = negative ? -(int)scaled : (int)scaled;
    if (signed_value < MATRIX_MIN || signed_value > MATRIX_MAX) return 0;
    *result = signed_value;
    return 1;
}

/* R/G/B are output rows of the stock row-major transform: M' = diag(g) M. */
static int make_styled(const color_matrix *base, color_matrix *styled) {
    if (is_neutral()) return 0;
    for (int row = 0; row < 3; ++row) {
        for (int column = 0; column < 3; ++column) {
            int index = row * 3 + column;
            i32 result;
            if (!scale_coefficient((i32)base->col_mat[index], percent[row], &result))
                return 0;
            styled->col_mat[index] = (u32)result;
        }
    }
    return 1;
}

static int forward_matrix(color_setter setter, const color_matrix *base,
                          color_matrix *scratch, int *modified,
                          int *range_bypassed) {
    if (!setter) return 1;
    *range_bypassed = 0;
    if (is_neutral()) {
        int result = setter((color_matrix *)base);
        if (!result) *modified = 0;
        return result;
    }
    if (!make_styled(base, scratch)) {
        *range_bypassed = 1;
        int result = setter((color_matrix *)base);
        if (!result) *modified = 0;
        return result;
    }
    int result = setter(scratch);
    if (!result) *modified = 1;
    return result;
}

static void restore_cached(void) {
    color_setter setter;
    if (capture_base_valid && (setter = resolve_ipcs())) {
        int result = setter(&capture_base);
        source_rgb_cached_setter_result_locked(1,result);
        capture_last_rc = result;
        if (!result) capture_modified = 0;
    }
    if (preview_base_valid && (setter = resolve_ipcm())) {
        int result = setter(&preview_base);
        source_rgb_cached_setter_result_locked(0,result);
        preview_last_rc = result;
        if (!result) preview_modified = 0;
    }
}

static void apply_cached(void) {
    color_matrix scratch;
    color_setter setter;
    int range_bypassed;
    if (capture_base_valid && (setter = resolve_ipcs())) {
        capture_last_rc = forward_matrix(setter, &capture_base, &scratch,
                                         &capture_modified, &range_bypassed);
        source_rgb_cached_setter_result_locked(1,capture_last_rc);
        if (range_bypassed) ++capture_range_bypass;
    }
    if (preview_base_valid && (setter = resolve_ipcm())) {
        preview_last_rc = forward_matrix(setter, &preview_base, &scratch,
                                         &preview_modified, &range_bypassed);
        source_rgb_cached_setter_result_locked(0,preview_last_rc);
        if (range_bypassed) ++preview_range_bypass;
    }
}

static void write_response(unsigned int id, int result) {
    unsigned int r, g, b, cap_calls, prev_calls, cap_bypass, prev_bypass;
    int active, cap_rc, prev_rc, cap_valid, prev_valid, cap_modified, prev_modified;
    lock_color();
    r = percent[0]; g = percent[1]; b = percent[2];
    active = !is_neutral();
    cap_calls = capture_calls; prev_calls = preview_calls;
    cap_bypass = capture_range_bypass; prev_bypass = preview_range_bypass;
    cap_rc = capture_last_rc; prev_rc = preview_last_rc;
    cap_valid = capture_base_valid; prev_valid = preview_base_valid;
    cap_modified = capture_modified; prev_modified = preview_modified;
    unlock_color();

    void *file = fopen("/var/run/nx300m-color-style-response.tmp", "w");
    if (!file) return;
    fprintf(file, "%u %d %u %u %u %d %u %u %u %u %d %d %d %d %d %d %d\n",
        id, result, r, g, b, active, cap_calls, prev_calls, cap_bypass,
        prev_bypass, cap_rc, prev_rc, cap_valid, prev_valid,
        cap_modified, prev_modified, getpid());
    fclose(file);
    rename("/var/run/nx300m-color-style-response.tmp",
           "/var/run/nx300m-color-style-response");
}

static int write_ready(void);
static int poll_request(void *unused) {
    /* Ecore timers are copied by fork. Stop the child's inherited callback
     * before it takes locks, consumes requests, or publishes parent state. */
    if(!color_process_current() || (int)(long)unused!=getpid()) return 0;
    ready_written=write_ready();
    if(!ready_written) return 1;
    chroma_write_ready();
    source_rgb_poll_request();
    sharpness_poll_request();
    (void)unused;
    contrast_poll_request();
    chroma_poll_request();
    recipe_poll_request();
    void *file = fopen("/var/run/nx300m-color-style-request", "r");
    if (!file) return 1;
    unsigned int id = 0, expires = 0, operation = 0;
    unsigned int requested[3] = {NEUTRAL, NEUTRAL, NEUTRAL};
    int fields = fscanf(file, "%u %u %u %u %u %u", &id, &expires, &operation,
                        requested, requested + 1, requested + 2);
    fclose(file);
    if (fields != 6 || !id || id == last_request) return 1;
    last_request = id;

    int result = 0;
    if ((unsigned int)time((long *)0) > expires) result = -108;
    else if (operation > 2u) result = -101;
    else if (operation == 1u &&
             (requested[0] > 200u || requested[1] > 200u || requested[2] > 200u))
        result = -102;

    if (!result && operation != 2u) {
        lock_color();
        if(++row_rgb_generation==0) row_rgb_generation=1;
        if (operation == 0u) {
            percent[0] = percent[1] = percent[2] = NEUTRAL;
            apply_cached();
        } else {
            percent[0] = requested[0];
            percent[1] = requested[1];
            percent[2] = requested[2];
            apply_cached();
        }
        unlock_color();
    }
    write_response(id, result);
    fprintf(stderr, "[NX RGB] request=%u op=%u result=%d rgb=%u/%u/%u active=%d\n",
        id, operation, result, percent[0], percent[1], percent[2], !is_neutral());
    return 1;
}

static int write_ready(void) {
    return bridge_write_ready("/var/run/nx300m-color-style-ready");
}

int ipcs_color_correction_param(color_matrix *matrix) {
    color_setter setter = resolve_ipcs();
    if(!color_process_current()) return setter?setter(matrix):1;
    if (!setter) { source_rgb_missing_setter(1);return 1; }
    if (!matrix) {
        lock_color();
        int result=setter(matrix);source_rgb_setter_result_locked(1,matrix,result,0);
        unlock_color();return result;
    }
    color_matrix styled;
    color_matrix *forwarded=matrix;
    int result;
    lock_color();
    capture_base = *matrix;
    source_rgb_cache_matrix_locked(1);
    capture_base_valid = 1;
    ++capture_calls;
    if (!is_neutral() && !make_styled(&capture_base, &styled)) {
        ++capture_range_bypass;
        result = setter(matrix);
        if (!result) capture_modified = 0;
    } else if (is_neutral()) {
        result = setter(matrix); /* strict pointer/data passthrough */
        if (!result) capture_modified = 0;
    } else {
        forwarded=&styled;
        result = setter(&styled);
        if (!result) capture_modified = 1;
    }
    capture_last_rc = result;
    source_rgb_setter_result_locked(1,forwarded,result,forwarded==&styled);
    unlock_color();
    return result;
}

int ipcm_color_correction_param(color_matrix *matrix) {
    color_setter setter = resolve_ipcm();
    if(!color_process_current()) return setter?setter(matrix):1;
    if (!setter) { source_rgb_missing_setter(0);return 1; }
    if (!matrix) {
        lock_color();
        int result=setter(matrix);source_rgb_setter_result_locked(0,matrix,result,0);
        unlock_color();return result;
    }
    color_matrix styled;
    color_matrix *forwarded=matrix;
    int result;
    lock_color();
    preview_base = *matrix;
    source_rgb_cache_matrix_locked(0);
    preview_base_valid = 1;
    ++preview_calls;
    if (!is_neutral() && !make_styled(&preview_base, &styled)) {
        ++preview_range_bypass;
        result = setter(matrix);
        if (!result) preview_modified = 0;
    } else if (is_neutral()) {
        result = setter(matrix); /* strict pointer/data passthrough */
        if (!result) preview_modified = 0;
    } else {
        forwarded=&styled;
        result = setter(&styled);
        if (!result) preview_modified = 1;
    }
    preview_last_rc = result;
    source_rgb_setter_result_locked(0,forwarded,result,forwarded==&styled);
    unlock_color();
    return result;
}

/* Startup may fork after preload constructors but before its first Ecore
 * loop. Discard copied caches rather than restoring a possibly half-written
 * parent baseline, and reset the copied lock without trying to acquire it. */
static void color_reset_local(void) {
    color_lock=0; attached=ready_written=0;
    percent[0]=percent[1]=percent[2]=NEUTRAL;
    row_rgb_generation=1; last_request=0;
    capture_calls=preview_calls=capture_range_bypass=preview_range_bypass=0;
    capture_last_rc=preview_last_rc=0;
    capture_base_valid=preview_base_valid=capture_modified=preview_modified=0;
    source_rgb_initialize(); chroma_initialize();
    sharpness_capture=(sharpness_path){0}; sharpness_preview=(sharpness_path){0};
    sharpness_capture_hardware=(sharpness_hardware_state){0}; sharpness_preview_hardware=(sharpness_hardware_state){0};
    sharpness_capture_node=(sharpness_node){0}; sharpness_preview_node=(sharpness_node){0};
    sharpness_level=10; sharpness_enabled=0; sharpness_generation=1; sharpness_last_request=0;
    sharpness_ready_written=0; sharpness_current=(void *)0;
    contrast_capture=(contrast_path){0}; contrast_preview=(contrast_path){0};
    contrast_capture_hardware=(contrast_hardware_state){0}; contrast_preview_hardware=(contrast_hardware_state){0};
    contrast_capture_node=(contrast_node){0}; contrast_preview_node=(contrast_node){0};
    contrast_level=10; contrast_enabled=0; contrast_generation=1; contrast_last_request=0;
    contrast_ready_written=0; contrast_current=(void *)0;
    recipe_generation=1; recipe_enabled=recipe_last_request=recipe_expected_rows=0;
    recipe_committed=recipe_ready_written=0;
    for(int i=0;i<7;i++) recipe_values[i]=i<3?100:10;
    for(int i=0;i<4;i++) recipe_expected[i]=0;
}
static int color_enter_ecore(void) {
    if(color_process_current()) return 1;
    /* Once the parent attached, a child is a helper with inherited timers,
     * never a continuation of pre-Ecore camera startup. */
    if(attached) return 0;
    int pid=getpid(), previous=bridge_marker_pid("/var/run/nx300m-color-style-ready");
    if(previous>0 && previous!=pid && (!kill(previous,0) || *__errno_location()!=3)) return 0;
    int inherited=color_process_pid;
    color_reset_local();
    color_process_pid=pid;
    fprintf(stderr,"[NX RGB] startup fork adopted pid=%d inherited_pid=%d\n",pid,inherited);
    return 1;
}

void ecore_main_loop_begin(void) {
    ecore_timer_add_fn timer_add =
        (ecore_timer_add_fn)dlsym((void *)-1, "ecore_timer_add");
    void (*begin)(void) = dlsym((void *)-1, "ecore_main_loop_begin");
    if(!color_enter_ecore()) { if(begin) begin(); return; }
    if (!attached && resolve_ipcs() && resolve_ipcm() && timer_add &&
        timer_add(0.1, poll_request, (void *)(long)getpid())) {
        attached = 1;
        fprintf(stderr, "[NX RGB] original color setters and Ecore request timer attached\n");
    }
    if (attached && !ready_written) {
        ready_written = write_ready();
        if (ready_written) fprintf(stderr, "[NX RGB] ready pid=%d\n", getpid());
    }
    if (attached && ready_written) chroma_write_ready();
    if (begin) begin();
}

__attribute__((constructor)) static void initialize(void) {
    color_process_pid=getpid();
    /* Loading through an exec helper does not confer ownership of another
     * process's ready markers. Initialize only this process's local state. */
    color_reset_local();
}

__attribute__((destructor)) static void shutdown_color_style(void) {
    if(!color_process_current()) return;
    recipe_cleanup();
    source_rgb_shutdown();
    bridge_cleanup("sharpness");
    lock_color();
    percent[0] = percent[1] = percent[2] = NEUTRAL;
    restore_cached();
    chroma_shutdown();
    unlock_color();
    bridge_cleanup("color-style");
    bridge_cleanup("contrast");
    if(capture_base_valid || preview_base_valid || chroma_capture.base_valid || chroma_preview.base_valid)
        fprintf(stderr, "[NX RGB] shutdown restoration attempted pid=%d\n",getpid());
}
