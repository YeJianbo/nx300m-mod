/* Native Metol client for the shared seven-value session recipe bridge.
 * A batch commits one configuration; native ISP stages retain their timing. */
#define _POSIX_C_SOURCE 200809L
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <time.h>
#include <unistd.h>
#include "nxui_model.h"
#include "mod_paths.h"

static const char *lock_names[]={"nx300m-recipe","nx300m-color-style","nx300m-source-rgb",
    "nx300m-chroma-style","nx300m-sharpness","nx300m-contrast"};
static const char *stage_names[]={"rgb","chroma","sharpness","contrast"};
static int locks[6]={-1,-1,-1,-1,-1,-1},guest_pid;
static long long deadline,response[25];
static int have_response;

static long long now_ms(void) {
    struct timespec value;clock_gettime(CLOCK_MONOTONIC,&value);
    return (long long)value.tv_sec*1000+value.tv_nsec/1000000;
}
static void pause_poll(void) {
    struct timespec delay={0,50000000};nanosleep(&delay,NULL);
}
static int ready_pid(void) {
    FILE *file=fopen("/var/run/nx300m-recipe-ready","r");if (!file) return 0;
    int pid=0;char extra;int count=fscanf(file,"%d %c",&pid,&extra);fclose(file);
    return count==1 && pid>0?pid:0;
}
static int fail(int code,const char *message) {
    fprintf(stderr,"REJECT recipe %s\n",message);
    if (have_response) {
        fprintf(stderr,"READBACK result=%lld generation=%lld enabled=%lld preview=%lld/%lld capture=%lld/%lld superseded=%lld guest_pid=%lld\n",
            response[1],response[2],response[3],response[11],response[13],response[12],response[14],response[23],response[24]);
        for (int path=0;path<2;path++) for (int i=0;i<4;i++)
            fprintf(stderr,"STAGE %s.%s available=%d applied=%d error=%lld\n",path?"capture":"preview",stage_names[i],
                !!(response[13+path]&(1<<i)),!!(response[11+path]&(1<<i)),response[15+path*4+i]);
    }
    return code;
}
static int acquire_locks(void) {
    long long lock_deadline=now_ms()+2000;
    for (int i=0;i<6;i++) {
        char path[128];snprintf(path,sizeof path,"/var/run/%s-lock",lock_names[i]);
        /* Other clients may have created this inode as root with mode 0644. */
        locks[i]=open(path,O_RDONLY|O_CREAT,0600);
        if (locks[i]<0) return fail(4,"cannot open shared control lock");
        while (flock(locks[i],LOCK_EX|LOCK_NB)) {
            if (errno!=EAGAIN && errno!=EWOULDBLOCK && errno!=EINTR)
                return fail(4,"shared control lock failed");
            if (now_ms()>=lock_deadline) return fail(4,"shared control lock busy (2s)");
            pause_poll();
        }
    }
    return 0;
}
static int request_id(unsigned int *id) {
    FILE *random=fopen("/dev/urandom","rb");if (!random) return 0;
    int ok=fread(id,1,sizeof *id,random)==sizeof *id;fclose(random);
    if (ok) *id=*id%0x7ffffffeU+1;
    return ok;
}
static int parse_response(const char *line,long long values[25]) {
    const char *p=line;
    for (int i=0;i<25;i++) {
        while (isspace((unsigned char)*p)) p++;
        char *end;errno=0;long long value=strtoll(p,&end,10);
        if (errno || end==p || value<INT_MIN || value>UINT_MAX ||
            (*end && !isspace((unsigned char)*end))) return 0;
        values[i]=value;p=end;
    }
    while (isspace((unsigned char)*p)) p++;
    if (*p || values[1]>INT_MAX || values[2]<=0 || values[3]<0 || values[3]>1 || values[23]<0 || values[23]>1) return 0;
    for (int i=0;i<7;i++) if (values[i+4]<0 || values[i+4]>(i<3?200:20)) return 0;
    for (int i=11;i<15;i++) if (values[i]<0 || values[i]>15) return 0;
    if ((values[11]&~values[13]) || (values[12]&~values[14])) return 0;
    for (int i=0;i<8;i++) if (values[15+i]>INT_MAX ||
        (values[15+i] && (values[11+i/4]&(1<<(i%4))))) return 0;
    return 1;
}
static int exchange(int operation,const int values[7]) {
    unsigned int id=0;
    if (now_ms()>=deadline) return fail(7,"response/preview deadline exceeded (8s total)");
    if (ready_pid()!=guest_pid) return fail(3,"ready camera identity changed");
    if (!request_id(&id)) return fail(4,"cannot generate request id");
    FILE *file=fopen("/var/run/nx300m-recipe-request.tmp","w");
    if (!file) return fail(4,"cannot create request");
    int written=fprintf(file,"%u %u %d %d %d %d %d %d %d %d\n",id,(unsigned int)time(NULL)+6,
        operation,values[0],values[1],values[2],values[3],values[4],values[5],values[6])>0;
    if (fclose(file)) written=0;
    if (!written || rename("/var/run/nx300m-recipe-request.tmp","/var/run/nx300m-recipe-request")) {
        unlink("/var/run/nx300m-recipe-request.tmp");return fail(4,"cannot publish request");
    }
    while (now_ms()<deadline) {
        if (ready_pid()!=guest_pid) return fail(3,"ready camera identity changed while waiting");
        file=fopen("/var/run/nx300m-recipe-response","r");
        if (file) {
            char line[1024];int read=fgets(line,sizeof line,file)!=NULL,extra=fgetc(file),error=ferror(file);
            fclose(file);
            if (read) {
                char *end;errno=0;unsigned long observed=strtoul(line,&end,10);
                if (!errno && end!=line && observed==id) {
                    long long next[25];
                    if (error || extra!=EOF || !parse_response(line,next)) return fail(5,"malformed 25-field response");
                    if (next[24]!=guest_pid || ready_pid()!=guest_pid) return fail(3,"response camera identity mismatch");
                    memcpy(response,next,sizeof response);have_response=1;
                    if (response[1]) return fail(6,"batch bridge returned an error");
                    return 0;
                }
            }
        }
        pause_poll();
    }
    return fail(7,"response/preview deadline exceeded (8s total)");
}
static int run(int argc,char **argv) {
    if (argc<2) return fail(2,"usage: recipe_engine preview|commit|apply ID, reset, or read");
    int operation;
    if (!strcmp(argv[1],"reset") && argc==2) operation=0;
    else if ((!strcmp(argv[1],"read") || !strcmp(argv[1],"status")) && argc==2) operation=2;
    else if ((!strcmp(argv[1],"preview") || !strcmp(argv[1],"commit") || !strcmp(argv[1],"apply")) && argc==3) operation=1;
    else return fail(2,"unknown action or arguments");
    int values[7]={100,100,100,10,10,10,10};
    if (operation==1) {
        const char *root=nx_mod_root();char path[NXUI_PATH];
        if (!nx_mod_root_valid(root)) return fail(2,"invalid mod root");
        snprintf(path,sizeof path,"%s/recipes.txt",root);
        struct nx_recipe recipes[NXUI_MAX_RECIPES];int count=nx_load_recipes(path,recipes,NXUI_MAX_RECIPES),found=0;
        if (count<=0) return fail(2,"recipe catalogue unavailable");
        for (int i=0;i<count;i++) if (!strcmp(recipes[i].key,argv[2])) {
            memcpy(values,recipes[i].value,sizeof values);found=1;break;
        }
        if (!found) return fail(2,"recipe id not found");
    }
    deadline=now_ms()+8000;
    guest_pid=ready_pid();if (!guest_pid) return fail(3,"seven-value bridge is not ready");
    int result=acquire_locks();if (result) return result;
    result=exchange(operation,values);if (result) return result;
    long long generation=response[2];
    puts("NXUI_STAGE preview");fflush(stdout);
    for (;;) {
        if (response[23]) return fail(6,"batch superseded by another control");
        if (response[2]!=generation) return fail(6,"batch generation replaced while waiting");
        if (operation!=2) {
            if (response[3]!=(operation==1)) return fail(6,"enabled readback differs from request");
            for (int i=0;i<7;i++) if (response[i+4]!=values[i]) return fail(6,"seven-value readback differs from request");
        }
        if (response[11]==15) {
            printf("APPLIED generation=%lld preview_mask=15 capture_mask=%lld persistent=0\n",generation,response[12]);
            return 0;
        }
        if (now_ms()>=deadline) return fail(7,"preview stages not all applied within 8s");
        pause_poll();result=exchange(2,values);if (result) return result;
    }
}
int main(int argc,char **argv) {
    int result=run(argc,argv);
    for (int i=5;i>=0;i--) if (locks[i]>=0) { flock(locks[i],LOCK_UN);close(locks[i]); }
    return result;
}
