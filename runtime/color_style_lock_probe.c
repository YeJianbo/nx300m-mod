/* Deterministic host-thread fixture for the production preload. No ISP or
 * camera process is used; the four stock setters can be held at a gate. */
#define _GNU_SOURCE 1
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

typedef struct { uint32_t words[9]; } matrix;
enum { MATRIX, CHROMA, TONE, EDGE };

#ifdef FIXTURE_LIBRARY
static pthread_mutex_t gate=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t changed=PTHREAD_COND_INITIALIZER;
static int blocked=-1,entered,released,calls,fail_next,cap_value,prev_value;
static int (*tick)(void *);
static void *tick_data;
void *ecore_timer_add(double interval,int (*callback)(void *),void *data) {
    (void)interval;tick=callback;tick_data=data;return (void *)1;
}
void ecore_main_loop_begin(void) {}
int fixture_tick(void) { return tick?tick(tick_data):0; }
void fixture_block(int kind) {
    pthread_mutex_lock(&gate);blocked=kind;entered=released=0;pthread_mutex_unlock(&gate);
}
void fixture_wait(void) {
    pthread_mutex_lock(&gate);
    while(!entered) pthread_cond_wait(&changed,&gate);
    pthread_mutex_unlock(&gate);
}
void fixture_release(void) {
    pthread_mutex_lock(&gate);released=1;pthread_cond_broadcast(&changed);pthread_mutex_unlock(&gate);
}
int fixture_calls(void) { return calls; }
int fixture_value(int capture) { return capture?cap_value:prev_value; }
void fixture_fail(void) { fail_next=1; }
static int submit(int kind,int capture,void *parameters) {
    pthread_mutex_lock(&gate);
    if(capture && kind==blocked) {
        entered=1;pthread_cond_broadcast(&changed);
        while(!released) pthread_cond_wait(&changed,&gate);
        blocked=-1;
    }
    int result=fail_next?-5:0;fail_next=0;
    if(kind==MATRIX) {
        ++calls;
        if(!result && parameters) {
            int value=(int)((matrix *)parameters)->words[0];
            if(capture) cap_value=value;else prev_value=value;
        }
    }
    pthread_mutex_unlock(&gate);return result;
}
int ipcs_color_correction_param(matrix *p) { return submit(MATRIX,1,p); }
int ipcm_color_correction_param(matrix *p) { return submit(MATRIX,0,p); }
int ipcs_rgb2ycc_param(void *p) { return submit(CHROMA,1,p); }
int ipcm_rgb2ycc_param(void *p) { return submit(CHROMA,0,p); }
int ipcs_edge_enhancement_param(void *p) { return submit(EDGE,1,p); }
void ipcs_tone_lut(void *p) { (void)submit(TONE,1,p); }
#else
extern void ecore_main_loop_begin(void),fixture_block(int),fixture_wait(void),fixture_release(void),fixture_fail(void);
extern int fixture_tick(void),fixture_calls(void),fixture_value(int);
extern int ipcs_color_correction_param(matrix *),ipcm_color_correction_param(matrix *);
extern int ipcs_rgb2ycc_param(void *),ipcs_edge_enhancement_param(void *);
extern void ipcs_tone_lut(void *);
#define CHECK(x) do { if(!(x)) { fprintf(stderr,"LOCK_FAIL line=%d: %s\n",__LINE__,#x);exit(1); } } while(0)
static unsigned int request_id;
static void request(int operation,int red,int response[17]) {
    FILE *f=fopen("/var/run/nx300m-color-style-request","w");CHECK(f);
    fprintf(f,"%u 2147483647 %d %d 100 100\n",++request_id,operation,red);CHECK(!fclose(f));
    CHECK(fixture_tick()==1);
    f=fopen("/var/run/nx300m-color-style-response","r");CHECK(f);
    for(int i=0;i<17;i++) CHECK(fscanf(f,"%d",response+i)==1);
    CHECK(!fclose(f));CHECK(response[0]==(int)request_id && !response[1]);
}
struct work { int kind,value,result; };
static void *worker(void *pointer) {
    struct work *w=pointer;matrix m={{0}};m.words[0]=w->value;
    if(w->kind==MATRIX) w->result=ipcs_color_correction_param(&m);
    else if(w->kind==CHROMA) { uint32_t p[19]={1};p[6]=p[9]=128;w->result=ipcs_rgb2ycc_param(p); }
    else if(w->kind==TONE) ipcs_tone_lut(&m);
    else { uint32_t p[16]={0};w->result=ipcs_edge_enhancement_param(p); }
    return 0;
}
int main(void) {
    alarm(10);ecore_main_loop_begin();int response[17];
    matrix base={{100}};CHECK(!ipcs_color_correction_param(&base));CHECK(!ipcm_color_correction_param(&base));
    fixture_block(MATRIX);
    struct work first={MATRIX,100,0},second={MATRIX,200,0};pthread_t a,b;
    CHECK(!pthread_create(&a,0,worker,&first));fixture_wait();
    int before=fixture_calls();request(1,150,response);
    CHECK(response[10]==-123 && fixture_calls()==before);
    /* Capture's blocked driver must not prevent the preview path or status. */
    CHECK(!ipcm_color_correction_param(&base));CHECK(fixture_value(0)==150);
    CHECK(!pthread_create(&b,0,worker,&second));request(2,100,response);
    fixture_release();CHECK(!pthread_join(a,0));CHECK(!pthread_join(b,0));
    CHECK(!first.result && !second.result && fixture_value(1)==300);
    request(2,100,response);CHECK(!response[10] && !response[11]);
    puts("LOCK_CASE matrix: blocked-driver-ui-responsive, staged-ui-no-write, independent-preview, latest-config-submit=pass");
    fixture_fail();CHECK(ipcs_color_correction_param(&base)==-5);
    request(2,100,response);CHECK(response[10]==-5 && fixture_value(1)==300);
    puts("LOCK_CASE failure: exact-error-forwarded, failed-write-not-applied=pass");
    for(int kind=CHROMA;kind<=EDGE;++kind) {
        fixture_block(kind);struct work work={kind,100,0};
        CHECK(!pthread_create(&a,0,worker,&work));fixture_wait();
        request(2,100,response);fixture_release();CHECK(!pthread_join(a,0));CHECK(!work.result);
    }
    puts("LOCK_CASE shared-modules: chroma-tone-edge-blocked-driver-ui-responsive=pass");
    puts("COLOR_LOCK_PROBE=PASS");return 0;
}
#endif
