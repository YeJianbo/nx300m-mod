/* NX300M 1.15 RGB channel gain bridge.
 *
 * The stock color nodes build a fresh row-major 3x3 matrix for each
 * parameter update and pass it to the original IPCS/IPCM setters. This bridge
 * keeps that matrix as the restoration base, scales its output rows, and then
 * calls the same original setters. It never writes WB/AWB state.
 */
#define _GNU_SOURCE 1
#include <pthread.h>
#include <stdint.h>

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
/* pthread.h uses the toolchain's time64 ABI; the camera exports time32. */
extern long color_time(long *) __asm__("time");
extern void *stderr;
extern void abort(void);

typedef unsigned int u32;
typedef signed int i32;
typedef struct { u32 col_mat[9]; } color_matrix;
typedef int (*color_setter)(color_matrix *);
typedef void *(*ecore_timer_add_fn)(double, int (*)(void *), void *);

enum { NEUTRAL = 100, MATRIX_MIN = -1024, MATRIX_MAX = 1023 };
static pthread_mutex_t color_lock = PTHREAD_MUTEX_INITIALIZER;
enum color_io_kind { COLOR_MATRIX, COLOR_CHROMA, COLOR_TONE, COLOR_EDGE, COLOR_IO_KINDS };
static pthread_mutex_t color_io[2] = {
    PTHREAD_RECURSIVE_MUTEX_INITIALIZER_NP, PTHREAD_RECURSIVE_MUTEX_INITIALIZER_NP
};
typedef struct {
    uint64_t issued, completed, successful;
    unsigned int generation;
    int known;
} color_row_state;
static color_row_state color_rows[2];
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
    if(pthread_mutex_lock(&color_lock)) abort();
}
static void unlock_color(void) {
    if(color_process_current() && pthread_mutex_unlock(&color_lock)) abort();
}
/* Lock order is always submission -> state. UI requests take only state;
 * no driver, native getter or file I/O may execute while state is held. */
static void color_io_enter(int capture, enum color_io_kind kind) {
    (void)kind;
    if(pthread_mutex_lock(&color_io[!!capture])) abort();
}
static void color_io_leave(int capture, enum color_io_kind kind) {
    (void)kind;
    if(pthread_mutex_unlock(&color_io[!!capture])) abort();
}
static void source_rgb_invalidate_native_locked(void);
static void color_rows_pending_locked(void) {
    if(capture_base_valid) capture_last_rc=-123;
    if(preview_base_valid) preview_last_rc=-123;
    source_rgb_invalidate_native_locked();
}

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

static void restore_cached(void) {
    /* Exit-only restoration. Ordinary UI changes wait for a real native
     * update, rather than replaying an old base from the Ecore thread. */
    for(int capture=0;capture<2;++capture) {
        color_setter setter=capture?resolve_ipcs():resolve_ipcm();
        color_io_enter(capture,COLOR_MATRIX);
        lock_color();
        int valid=capture?capture_base_valid:preview_base_valid;
        color_matrix base=capture?capture_base:preview_base;
        unlock_color();
        if(valid && setter) {
            int result=setter(&base);
            lock_color();
            source_rgb_cached_setter_result_locked(capture,result);
            if(capture) { capture_last_rc=result;if(!result) capture_modified=0; }
            else { preview_last_rc=result;if(!result) preview_modified=0; }
            unlock_color();
        }
        color_io_leave(capture,COLOR_MATRIX);
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
    if ((unsigned int)color_time((long *)0) > expires) result = -108;
    else if (operation > 2u) result = -101;
    else if (operation == 1u &&
             (requested[0] > 200u || requested[1] > 200u || requested[2] > 200u))
        result = -102;

    if (!result && operation != 2u) {
        lock_color();
        if(++row_rgb_generation==0) row_rgb_generation=1;
        if (operation == 0u) {
            percent[0] = percent[1] = percent[2] = NEUTRAL;
        } else {
            percent[0] = requested[0];
            percent[1] = requested[1];
            percent[2] = requested[2];
        }
        color_rows_pending_locked();
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

static int color_forward_matrix(color_matrix *matrix,int capture) {
    color_setter setter = capture?resolve_ipcs():resolve_ipcm();
    if(!color_process_current()) return setter?setter(matrix):1;
    if (!setter) { source_rgb_missing_setter(capture);return 1; }
    color_matrix styled;
    color_matrix *forwarded=matrix;
    int modified=0;
    /* Serialize before taking the snapshot: an older queued call cannot
     * replay configuration captured before a newer hardware submission. */
    color_io_enter(capture,COLOR_MATRIX);
    lock_color();
    color_row_state *row=&color_rows[!!capture];
    uint64_t ticket=++row->issued;
    unsigned int generation=row_rgb_generation;
    if(matrix) {
        if(capture) { capture_base=*matrix;capture_base_valid=1;++capture_calls; }
        else { preview_base=*matrix;preview_base_valid=1;++preview_calls; }
        source_rgb_cache_matrix_locked(capture);
        if(!is_neutral()) {
            if(make_styled(matrix,&styled)) { forwarded=&styled;modified=1; }
            else if(capture) ++capture_range_bypass;
            else ++preview_range_bypass;
        }
    }
    unlock_color();
    int result=setter(forwarded); /* retain pointer identity for stock passthrough */
    lock_color();
    /* A reentrant setter can complete a nested write first. Its hardware
     * ownership cannot be attributed to the outer transaction. */
    int current=ticket==row->issued;
    if(current) {
        row->completed=ticket;row->known=!result && matrix!=0;
        if(!result && matrix) { row->successful=ticket;row->generation=generation; }
        if(capture) {
            capture_last_rc=generation==row_rgb_generation?result:-123;
            if(!result && matrix) capture_modified=modified;
        } else {
            preview_last_rc=generation==row_rgb_generation?result:-123;
            if(!result && matrix) preview_modified=modified;
        }
    } else {
        row->known=0;
        if(capture) capture_last_rc=-124;else preview_last_rc=-124;
    }
    source_rgb_setter_result_locked(capture,forwarded,result,modified);
    if(!current) {
        source_rgb_hardware *hardware=capture?&source_rgb_capture_hardware:&source_rgb_preview_hardware;
        hardware->state_known=0;hardware->error=-124;
    }
    unlock_color();
    color_io_leave(capture,COLOR_MATRIX);
    return result;
}
int ipcs_color_correction_param(color_matrix *matrix) { return color_forward_matrix(matrix,1); }
int ipcm_color_correction_param(color_matrix *matrix) {
    return color_forward_matrix(matrix,0);
}

/* Startup may fork after preload constructors but before its first Ecore
 * loop. Discard copied caches rather than restoring a possibly half-written
 * parent baseline, and reset the copied lock without trying to acquire it. */
static void color_reset_local(void) {
    /* A pre-Ecore fork adopts fresh local locks; inherited parent locks are
     * never used by helpers or restored by their destructors. */
    color_lock=(pthread_mutex_t)PTHREAD_MUTEX_INITIALIZER;
    for(int capture=0;capture<2;++capture) {
        color_rows[capture]=(color_row_state){0};
        color_io[capture]=(pthread_mutex_t)PTHREAD_RECURSIVE_MUTEX_INITIALIZER_NP;
    }
    nx_sharpness_native_lock=(pthread_mutex_t)PTHREAD_MUTEX_INITIALIZER;
    nx_sharpness_native_depth=0;
    attached=ready_written=0;
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
    unlock_color();
    restore_cached();
    chroma_shutdown();
    bridge_cleanup("color-style");
    bridge_cleanup("contrast");
    if(capture_base_valid || preview_base_valid || chroma_capture.base_valid || chroma_preview.base_valid)
        fprintf(stderr, "[NX RGB] shutdown restoration attempted pid=%d\n",getpid());
}
