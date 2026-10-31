/* Control-state fixture only: include the production source RGB header.
 * Getter, Update and SDK outcomes are explicit mocks, not original firmware. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>

typedef int32_t i32;
typedef struct { unsigned int col_mat[9]; } color_matrix;
static int supported=1, failures, reset_calls;
static volatile int color_lock;
static void lock_color(void) { while (__sync_lock_test_and_set(&color_lock,1)) {} }
static void unlock_color(void) { __sync_lock_release(&color_lock); }
static int chroma_supported_pasm(void) { return supported; }
static unsigned char capture_vtable[32], preview_vtable[32];
static int16_t singleton[9]={128,16,-16,-12,128,12,4,-4,128};
static const int16_t original_singleton[9]={128,16,-16,-12,128,12,4,-4,128};
static int16_t *fixture_getter(void) { return singleton; }
static void *fixture_symbol(void *handle,const char *name) {
    (void)handle;
    if (!strcmp(name,"_Z16GetRgb2RgbMatrixv")) return (void *)fixture_getter;
    if (!strcmp(name,"_ZTV12CIpcsColorD4")) return capture_vtable;
    if (!strcmp(name,"_ZTV12CIpcmColorD4")) return preview_vtable;
    return 0;
}

/* Poll/readback exercises the real14-field serializer without /var/run I/O. */
static char request_wire[200], response_wire[600];
static FILE *response_file;
static FILE *fixture_open(const char *path,const char *mode) {
    FILE *file;
    if (!strcmp(path,"/var/run/nx300m-source-rgb-request") && mode[0]=='r') {
        file=tmpfile();
        if (file) { fputs(request_wire,file);rewind(file); }
        return file;
    }
    file=tmpfile();
    if (strstr(path,"response.tmp")) response_file=file;
    return file;
}
static int fixture_close(void *pointer) {
    FILE *file=pointer;
    if (file==response_file) {
        fflush(file);rewind(file);
        size_t count=fread(response_wire,1,sizeof response_wire-1,file);
        response_wire[count]=0;response_file=0;
    }
    return fclose(file);
}
static int fixture_rename(const char *a,const char *b) { (void)a;(void)b;return 0; }
static int fixture_unlink(const char *path) { (void)path;return 0; }
static int fixture_pid(void) { return 123; }
static long fixture_time(long *out) { if(out) *out=100;return 100; }
#define dlsym fixture_symbol
#define fopen fixture_open
#define fclose fixture_close
#define rename fixture_rename
#define unlink fixture_unlink
#define getpid fixture_pid
#define time fixture_time
#include "source_rgb_style.h"
#undef dlsym
#undef fopen
#undef fclose
#undef rename
#undef unlink
#undef getpid
#undef time

#define CHECK(condition) do { if (!(condition)) { \
    fprintf(stderr,"STATE_FAIL line=%d condition=%s\n",__LINE__,#condition);++failures; } } while(0)
struct node { void *vptr; } preview_node, nested_node, unknown_node;
struct plan { int idle, result, raw_only, no_getter, nest_unknown, nest_known, block; };
static color_matrix simulated_hardware;
static pthread_mutex_t gate_mutex=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t gate_condition=PTHREAD_COND_INITIALIZER;
static int worker_written, release_worker;

static void configure(unsigned int generation,int enabled,unsigned int r,unsigned int g,unsigned int b) {
    lock_color();
    source_rgb_generation=generation;source_rgb_enabled=enabled;
    source_rgb_values[0]=r;source_rgb_values[1]=g;source_rgb_values[2]=b;
    unlock_color();
}
static void reset_node(void *node) { (void)node;++reset_calls; }
static void fixture_update(void *self,void *parameters) {
    struct plan *plan=parameters;
    if (plan->idle) return;
    if (plan->raw_only) {
        CHECK(source_rgb_current==0);
        CHECK(source_rgb_get_matrix()==singleton);
        return;
    }
    source_rgb_transaction *context=source_rgb_current;
    CHECK(context!=0);
    if (plan->nest_unknown) {
        struct plan child={.raw_only=1};
        source_rgb_run_update(&unknown_node,&child,fixture_update,reset_node);
        CHECK(source_rgb_current==context);
    }
    color_matrix matrix;
    int16_t *values=plan->no_getter?singleton:source_rgb_get_matrix();
    if (!plan->no_getter && context->observed.enabled && context->observed.eligible)
        CHECK(values==context->scratch && values!=singleton);
    else CHECK(values==singleton);
    for(int i=0;i<9;++i) matrix.col_mat[i]=(unsigned int)(int32_t)values[i];
    lock_color();
    source_rgb_cache_matrix_locked(0);
    if (!plan->result) simulated_hardware=matrix;
    source_rgb_setter_result_locked(0,&matrix,plan->result,0);
    unlock_color();
    if (plan->nest_known) {
        struct plan child={0};
        configure(21,1,200,80,120);
        source_rgb_run_update(&nested_node,&child,fixture_update,reset_node);
        CHECK(source_rgb_current==context);
        CHECK(source_rgb_get_matrix()==context->scratch);
        CHECK(context->observed.generation==20 && context->observed.codes[0]==127);
    }
    if (plan->block) {
        pthread_mutex_lock(&gate_mutex);
        worker_written=1;pthread_cond_broadcast(&gate_condition);
        while(!release_worker) pthread_cond_wait(&gate_condition,&gate_mutex);
        pthread_mutex_unlock(&gate_mutex);
        CHECK(source_rgb_current==context && context->observed.generation==30);
    }
    CHECK(!memcmp(singleton,original_singleton,sizeof singleton));
    (void)self;
}
static void update(struct plan plan) {
    source_rgb_run_update(&preview_node,&plan,fixture_update,reset_node);
    CHECK(source_rgb_current==0);
}
static void *worker(void *unused) {
    struct plan plan={.block=1};
    (void)unused;
    update(plan);
    return 0;
}
struct response { unsigned int generation,actual_generation;int enabled,modified,error,applied; };
static struct response read_response(void) {
    static unsigned int id=1;
    unsigned int fields[14];
    snprintf(request_wire,sizeof request_wire,"%u 1000 2 100 100 100\n",id++);
    response_wire[0]=0;
    source_rgb_poll_request();
    int count=sscanf(response_wire,"%u %u %u %u %u %u %u %u %u %u %u %u %u %u",
        fields,fields+1,fields+2,fields+3,fields+4,fields+5,fields+6,fields+7,
        fields+8,fields+9,fields+10,fields+11,fields+12,fields+13);
    CHECK(count==14 && fields[1]==0 && fields[13]==123);
    struct response response={fields[6],fields[8],(int)fields[5],(int)fields[10],(int)fields[12],0};
    response.applied=response.generation==response.actual_generation &&
        response.enabled==response.modified && response.error==0;
    return response;
}
static void case_done(const char *name) { printf("STATE_CASE name=%s failures=%d\n",name,failures); }

int main(void) {
    preview_node.vptr=preview_vtable+8;nested_node.vptr=preview_vtable+8;unknown_node.vptr=0;
    source_rgb_initialize();
    CHECK(source_rgb_get_matrix()==singleton);
    configure(2,1,100,100,100);update((struct plan){0});
    CHECK(source_rgb_preview.codes[0]==127 && source_rgb_preview.output[0]==127);
    CHECK(source_rgb_preview_hardware.generation==2 && source_rgb_preview_hardware.modified==1);
    CHECK(read_response().applied);
    case_done("100_is_enabled_private_copy");

    configure(3,1,200,80,120);update((struct plan){0});
    CHECK(source_rgb_preview.codes[0]==255 && source_rgb_preview.codes[1]==102 && source_rgb_preview.codes[2]==153);
    CHECK(source_rgb_preview.output[0]==255 && source_rgb_preview.output[4]==102 && source_rgb_preview.output[8]==153);
    source_rgb_path successful=source_rgb_preview;
    update((struct plan){.idle=1});
    CHECK(source_rgb_preview.generation==successful.generation && source_rgb_preview.modified==1 && source_rgb_preview.error==0);
    CHECK(source_rgb_preview.last_update_setter_calls==0 && read_response().applied);
    case_done("asymmetric_and_idle");

    configure(4,1,80,160,200);update((struct plan){.result=2});
    struct response response=read_response();
    CHECK(source_rgb_preview_hardware.generation==3 && source_rgb_preview_hardware.modified==1);
    CHECK(!response.applied && response.actual_generation==3 && response.error==2);
    CHECK(source_rgb_preview_cache_owner.generation==4);
    lock_color();source_rgb_cached_setter_result_locked(0,0);unlock_color();
    response=read_response();
    CHECK(response.applied && response.actual_generation==4 && source_rgb_preview_hardware.state_known);
    case_done("setter_failure_then_owned_cache_replay");

    configure(5,0,100,100,100);update((struct plan){.result=2});
    CHECK(source_rgb_preview_hardware.modified==1 && !read_response().applied);
    update((struct plan){0});response=read_response();
    CHECK(response.applied && !response.modified && response.actual_generation==5);
    case_done("clear_requires_successful_setter");

    configure(6,1,100,100,100);update((struct plan){0});
    supported=0;update((struct plan){0});response=read_response();
    CHECK(!response.applied && !source_rgb_preview_hardware.modified && response.error==-112);
    supported=1;update((struct plan){0});CHECK(read_response().applied);
    case_done("mode_exit_and_return");

    lock_color();
    source_rgb_current=0;source_rgb_cache_matrix_locked(0);
    source_rgb_cached_setter_result_locked(0,0);
    unlock_color();
    response=read_response();CHECK(!response.applied && response.error==-124 && !source_rgb_preview_hardware.state_known);
    update((struct plan){.no_getter=1});response=read_response();
    CHECK(!response.applied && response.error==-124);
    case_done("unowned_and_no_getter_writes_are_unknown");

    configure(20,1,100,100,100);update((struct plan){.nest_unknown=1,.nest_known=1});
    response=read_response();
    CHECK(response.applied && response.actual_generation==21 && source_rgb_preview.generation==21);
    CHECK(source_rgb_preview.codes[0]==255 && source_rgb_current==0);
    case_done("nested_context_and_late_outer_completion");

    configure(30,1,100,100,100);
    pthread_t thread;CHECK(!pthread_create(&thread,0,worker,0));
    pthread_mutex_lock(&gate_mutex);
    while(!worker_written) pthread_cond_wait(&gate_condition,&gate_mutex);
    pthread_mutex_unlock(&gate_mutex);
    CHECK(source_rgb_current==0 && source_rgb_get_matrix()==singleton);
    configure(31,1,200,80,120);update((struct plan){0});
    pthread_mutex_lock(&gate_mutex);release_worker=1;pthread_cond_broadcast(&gate_condition);pthread_mutex_unlock(&gate_mutex);
    CHECK(!pthread_join(thread,0));response=read_response();
    CHECK(response.applied && response.actual_generation==31 && source_rgb_preview.generation==31);
    CHECK(!memcmp(singleton,original_singleton,sizeof singleton));
    case_done("threaded_write_and_completion_order");
    printf("SOURCE_RGB_STATE cases=8 failures=%d reset_calls=%d singleton_unchanged=1\n",failures,reset_calls);
    source_rgb_shutdown();
    return failures?1:0;
}
