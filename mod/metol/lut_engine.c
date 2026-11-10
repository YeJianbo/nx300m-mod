/* Metol backend protocol for NX300M's native LUT-call adapter.
 * Selection is staged in private runtime storage. Firmware owns reload timing.
 */
#define _XOPEN_SOURCE 700
#include <dirent.h>
#include <time.h>
#include <sys/syscall.h>
#include "lut_engine_shared.h"

static unsigned long long control_ms(void) {
    struct timespec t={0,0};syscall(SYS_clock_gettime,CLOCK_MONOTONIC,&t);
    return (unsigned long long)t.tv_sec*1000+(unsigned long long)t.tv_nsec/1000000;
}

/* Runs only on a user control event. The engine lock stays in this process;
 * lut-load must not recursively restore selection through a second engine. */
static int service_control(const char *name,const char *operation,int optional) {
    unsigned long long started=control_ms();
    fprintf(stderr,"LUT_CONTROL script=%s operation=%s phase=begin\n",name,operation);
    const char *data=nx_lut_data_root(),*runtime=nx_mod_root();
    const char *base=getenv("NX300M_MFW_RAM_BASE");
    char directory[PATH_MAX],state_dir[PATH_MAX],script[PATH_MAX],canonical[PATH_MAX],identity[64];
    struct stat source,asset;
    if(base&&*base) {
        if(!nx_mod_root_valid(base)||snprintf(directory,sizeof directory,"%s/runtime",base)>=(int)sizeof directory||
           snprintf(state_dir,sizeof state_dir,"%s/state",base)>=(int)sizeof state_dir) return 4;
        runtime=directory;
    }
    if(!data||!runtime||stat(data,&source)||!S_ISDIR(source.st_mode)||
       !realpath(runtime,canonical)||strcmp(runtime,canonical)||
       snprintf(script,sizeof script,"%s/%s",runtime,name)>=(int)sizeof script) return 4;
    if(lstat(script,&asset)) return optional&&errno==ENOENT?0:4;
    if(!S_ISREG(asset.st_mode)||asset.st_uid!=geteuid()||access(script,R_OK)) return 4;
    snprintf(identity,sizeof identity,"%llu:%llu",(unsigned long long)source.st_dev,(unsigned long long)source.st_ino);
    pid_t child=fork();
    if(!child) {
        if(setenv("NX300M_MOD_ROOT",runtime,1)||setenv("NX_MOD_ROOT",runtime,1)||
           setenv("NX300M_MOD_SOURCE_ROOT",data,1)||setenv("NX300M_MOD_SOURCE_ID",identity,1)||
           setenv("NX300M_LUT_ENGINE_OWNS_LOCK","1",1)||setenv("NX300M_LUT_SKIP_PREVIEW_SYNC","1",1)||
           (base&&*base&&setenv("NX_STATE_DIR",state_dir,1))) _exit(126);
        DIR *fds=opendir("/proc/self/fd");if(!fds) _exit(126);
        int own=dirfd(fds);struct dirent *entry;
        while((entry=readdir(fds))) {
            char *end;long fd=strtol(entry->d_name,&end,10);
            if(*entry->d_name&&!*end&&fd>2&&fd!=own) close((int)fd);
        }
        closedir(fds);
        execl("/bin/sh","sh",script,operation,(char *)0);_exit(127);
    }
    int status=0;pid_t done=-1;
    if(child>0) {
        /* Bound the engine-side wait so a slow chain cannot hang the panel.
         * Recovery state machine: idle -> requested(fork) -> running(within
         * the outer budget below) -> draining/recovering(past this budget:
         * the inner launcher/loader own their own 30s+ budgets, recovery
         * workers and the uncertainty guard) -> confirmed/failed(reaped
         * below) or uncertain(deadline hit). Reaching the deadline NEVER
         * kills the chain: killing only the shell would orphan the loader's
         * recovery worker before the launcher can publish its guard, and the
         * outcome must be reported as uncertain, not failed or recovered. */
        unsigned long long budget=20000;
        { const char *override=getenv("NX_LUT_CONTROL_BUDGET_MS");
          if(override&&*override){long v=atol(override);if(v>=100)budget=(unsigned long long)v;} }
        unsigned long long deadline=control_ms()+budget;
        while(done<0) {
            done=waitpid(child,&status,WNOHANG);
            if(done==child)break;
            if(done<0&&errno!=EINTR)break;
            if(done==0) {
                done=-1;
                if(control_ms()>=deadline)break;
                usleep(20000);
            }
        }
        if(done!=child) {
            /* Abandon, do not signal: the orphaned chain finishes under its
             * own owner (launcher publishes the uncertainty guard on ITS 30s
             * budget; scripts refuse re-entry while the guard exists). The
             * engine lock is not inherited past exec, so it releases with
             * this process. Residual: an orphan that hangs forever outside
             * the preview chain still needs a future janitor. */
            fprintf(stderr,"LUT_CONTROL script=%s operation=%s phase=timeout-uncertain child=%ld\n",
                    name,operation,(long)child);
            fprintf(stderr,"CONTROL-UNCERTAIN script=%s operation=%s; chain left running for its recovery owner\n",
                    name,operation);
            return 4;
        }
    }
    int result=child>0&&done==child&&WIFEXITED(status)?WEXITSTATUS(status):4;
    fprintf(stderr,"LUT_CONTROL script=%s operation=%s phase=end rc=%d elapsed_ms=%llu\n",name,operation,result,control_ms()-started);
    return result;
}
static int ensure_adapter(void) {
    if(nx_lut_hook_ready()) return 0;
    int result=service_control("lut-load.sh","apply",0);
    if(result) return result;
    if(!nx_lut_hook_ready()) { fputs("REJECT native LUT adapter did not become ready\n",stderr);return 3; }
    return 0;
}
static int stage(const char *path,unsigned int strength) {
    unsigned long long started=control_ms();
    int ready=ensure_adapter();if(ready) return ready;
    fprintf(stderr,"LUT_CONTROL phase=adapter-ready elapsed_ms=%llu\n",control_ms()-started);
    unsigned int generation=0;int result=nx_lut_stage_with_strength(path,strength,&generation);
    fprintf(stderr,"LUT_CONTROL phase=selection-complete rc=%d elapsed_ms=%llu\n",result,control_ms()-started);
    if (!result) printf("STAGED generation=%u strength=%u path=%s; awaiting original firmware LUT callback\n",generation,strength,path);
    return result;
}
/* Viewfinder stage evidence. libudd.so.3.1 exports ipcs_reg_base (pointer to
 * the IPCS register block) and ipcs_hl_3d_lut_onoff toggles bit 13 at
 * +0x1040; read that bit from outside the camera to learn whether the 3D LUT
 * stage is running in the current mode (preview vs capture). */
#define UDD_REG_BASE_OFFSET 0x127bd8U
#define UDD_REG_BASE_SIZE 4U
#define IPCS_LUT_ENABLE 0x1040U
#define IPCS_LUT_ENABLE_BIT 13U
static int camera_pid_scan(void) {
    DIR *tasks=opendir("/proc");if (!tasks) return 0;
    struct dirent *entry;int pid=0;
    while ((entry=readdir(tasks))) {
        char *end;long value=strtol(entry->d_name,&end,10);
        if (*entry->d_name && !*end && value>1) {
            char path[64],exe[128];ssize_t len;
            snprintf(path,sizeof path,"/proc/%ld/exe",value);
            len=readlink(path,exe,sizeof exe-1);
            if (len>0) { exe[len]=0;
                char *name=strrchr(exe,'/');name=name?name+1:exe;
                if (!strcmp(name,"di-camera-app-nx300")) {pid=(int)value;break;}
            }
        }
    }
    closedir(tasks);return pid;
}
static int udd_base(int pid,unsigned long *base) {
    char path[64];snprintf(path,sizeof path,"/proc/%d/maps",pid);
    FILE *maps=fopen(path,"r");if (!maps) return 0;
    char line[512];int found=0;
    while (fgets(line,sizeof line,maps)) {
        if (!strstr(line,"libudd.so.3.1") || !strstr(line,"r")) continue;
        unsigned long lo=0,hi;char perms[8],rest[400];
        if (sscanf(line,"%lx-%lx %7s %*s %*s %*s %399s",&lo,&hi,perms,rest)>=3) {*base=lo;found=1;break;}
    }
    fclose(maps);return found;
}
static int stage_state(void) {
    int pid=camera_pid_scan();
    if (!pid) { puts("REJECT original camera process not found");return 3; }
    unsigned long udd=0;
    if (!udd_base(pid,&udd)) { puts("REJECT libudd mapping not found");return 3; }
    char path[64];snprintf(path,sizeof path,"/proc/%d/mem",pid);
    int fd=open(path,O_RDONLY);
    if (fd<0) { printf("REJECT camera memory open failed errno=%d\n",errno);return 3; }
    unsigned int regs=0,control=0,location=0;
    if (pread(fd,&regs,UDD_REG_BASE_SIZE,(off_t)(udd+UDD_REG_BASE_OFFSET))!=UDD_REG_BASE_SIZE || !regs) {
        printf("REJECT ipcs_reg_base unreadable errno=%d\n",errno);close(fd);return 3;
    }
    location=regs+IPCS_LUT_ENABLE;
    if (pread(fd,&control,sizeof control,(off_t)location)!=sizeof control) {
        printf("REJECT stage register unreadable at %08lx errno=%d\n",(unsigned long)location,errno);close(fd);return 3;
    }
    close(fd);
    printf("{\"camera_pid\":%d,\"regs\":\"0x%08x\",\"control\":\"0x%08x\",\"lut_stage_on\":%s}\n",
        pid,regs,control,(control>>IPCS_LUT_ENABLE_BIT)&1?"true":"false");
    return 0;
}
static int action(int argc,char **argv) {
    if (argc<2) return 2;
    if(!strcmp(argv[1],"boot")) {
        if(argc!=2||setenv("NX300M_LUT_SKIP_PREVIEW_SYNC","1",1))return 2;
        char saved[PATH_MAX],path[256],id[64];unsigned gen,strength;struct nx_lut_tune tune;
        if(!nx_lut_saved_path(saved))return 4;
        if(access(saved,F_OK)) {
            if(errno!=ENOENT)return 4;
            snprintf(saved,sizeof saved,"%s/.state/lut-selection",nx_lut_data_root());
            if(access(saved,F_OK))return errno==ENOENT?0:4;
        }
        if(nx_lut_saved_stock(saved)) {
            if(!access(NX_LUT_RAM_MARKER,F_OK)||!access("/var/run/nx300m-preview-backend",F_OK)||nx_lut_hook_ready()) {
                char *off[]={argv[0],"disable",NULL};return action(2,off);
            }
            /* A stock boot is the LUT being off, not the user changing the
             * preview preference; leave the saved preference untouched so a
             * later apply restores preview per that preference. */
            return nx_lut_stock();
        }
        if(!nx_lut_read_selection_with_strength(saved,&gen,path,&strength)||!nx_lut_selection_id(path,id,&tune))return 5;
        struct nx_lut_look looks[NX_LUT_MAX_LOOKS];int count=0,result=nx_lut_catalog_index(looks,&count);
        if(result)return result;
        for(int i=0;i<count;i++)if(!strcmp(id,looks[i].id)) {
            result=nx_tune_cache(id,&tune,strength,path);
            return result?result:stage(path,strength);
        }
        return 5;
    }
    if (!strcmp(argv[1],"ready")) {
        if (argc!=2) return 2;
        int ready=nx_lut_hook_ready();printf("{\"ready\":%s}\n",ready?"true":"false");
        return ready?0:3;
    }
    if (!strcmp(argv[1],"begin")) {
        int restored=nx_lut_restore_selection();
        if (restored) fprintf(stderr,"RESTORE-WARNING saved selection unavailable (%d); keep current selection\n",restored);
        unsigned int generation=0,strength=100;char path[256]="";
        int selected=nx_lut_read_selection_with_strength(NX_LUT_SELECTION,&generation,path,&strength);
        FILE *out=fopen(NX_LUT_BACKUP,"w");if (!out) return 4;
        int written=(selected?fprintf(out,"%u %s %u\n",generation,path,strength):fprintf(out,"0\n"))>=0;
        if (fclose(out)) written=0;
        return written?0:4;
    }
    if (!strcmp(argv[1],"restore-selection")) return argc==2?nx_lut_restore_selection():2;
    if (!strcmp(argv[1],"activate")) {
        if(argc!=2) return 2;
        /* Preview takes its control lease after this action. Preserve the
         * engine -> preview lock order and do not recursively sync. */
        if(setenv("NX300M_LUT_SKIP_PREVIEW_SYNC","1",1)) return 4;
        int result=nx_lut_restore_selection();if(result) return result;
        unsigned int generation,strength;char path[256];
        if(!nx_lut_read_selection_with_strength(NX_LUT_SELECTION,&generation,path,&strength)) {
            fputs("LUT_OFF select a look before enabling its preview\n",stderr);return 3;
        }
        if(!nx_lut_table_valid(path)) return 5;
        result=ensure_adapter();
        if(!result) printf("LUT_ACTIVATED generation=%u strength=%u path=%s\n",generation,strength,path);
        return result;
    }
    if (!strcmp(argv[1],"persist-status")) {
        if (argc!=2) return 2;
        puts(nx_lut_selection_persisted()?"PERSISTED":"NOT_SAVED");return 0;
    }
    if (!strcmp(argv[1],"disable")) {
        if(argc!=2) return 2;
        /* Save Off first, so an interrupted stop cannot revive an old look.
         * Stock here must not start another stock-only preview session. */
        if(setenv("NX300M_LUT_SKIP_PREVIEW_SYNC","1",1)) return 4;
        int result=nx_lut_stock();if(result) return result;
        /* Ordinary Off drains the same native preview session as restore,
         * without attaching a second time just to export diagnostics. The
         * wrapper's `pause` keeps the user's preview preference: suspending
         * preview with the LUT is not the user turning preview off. */
        result=service_control("smartfilter-preview.sh","pause",1);
        if(result) { fputs("LUT_OFF_FAILED preview stop incomplete\n",stderr);return result; }
        if(!access(NX_LUT_RAM_MARKER,F_OK)||nx_lut_hook_ready()) {
            result=service_control("lut-load.sh","restore",0);
            if(result) { fputs("LUT_OFF_FAILED native Still restore incomplete\n",stderr);return result; }
        }
        puts("LUT_DISABLED native_still_restored=true preview_processing=false");return 0;
    }
    if (!strcmp(argv[1],"off") || !strcmp(argv[1],"stock")) {
        unsigned int generation,strength;char path[256];
        if (!strcmp(argv[1],"off") && nx_lut_read_selection_with_strength(NX_LUT_BACKUP,&generation,path,&strength)) return stage(path,strength);
        int result=nx_lut_stock();
        if (!result) puts("STOCK_SELECTED; awaiting original firmware LUT callback");
        return result;
    }
    if (!strcmp(argv[1],"status")) {
        unsigned int generation,strength;char path[256];
        if (nx_lut_read_selection_with_strength(NX_LUT_SELECTION,&generation,path,&strength)) printf("STAGED generation=%u path=%s strength=%u\n",generation,path,strength);
        else puts("STOCK_SELECTED");
        return 0;
    }
    unsigned int strength=100;
    if (strcmp(argv[1],"apply") || (argc!=3 && argc!=4 && argc!=11) || !nx_lut_id_valid(argv[2]) ||
        (argc>=4 && !nx_lut_strength(argv[3],&strength))) return 2;
    struct nx_lut_tune tune={{0}};
    if(argc==11) {
        for(int i=0;i<NX_TUNE_FIELDS;i++) {
            char *end;errno=0;long v=strtol(argv[4+i],&end,10);
            if(errno||!argv[4+i][0]||*end||v<-10||v>10)return 2;
            tune.value[i]=(int)v;
        }
    } else {
        /* CLI/App selection also recalls the camera's per-look tuning. The
         * supplied strength remains explicit and is never silently replaced. */
        nx_tune_load(nx_lut_data_root(),argv[2],&tune,NULL);
    }
    struct nx_lut_look looks[NX_LUT_MAX_LOOKS];int count=0;
    int result=nx_lut_catalog_index(looks,&count);if (result) return result;
    for (int i=0;i<count;i++) if (!strcmp(looks[i].id,argv[2])) {
        char path[256];int composed;
        if (!nx_lut_validate_look(argv[2],path,&composed)) return 5;
        result=nx_tune_cache(argv[2],&tune,strength,path);if(result)return result;
        result=stage(path,strength);
        /* A failed preview sync still leaves the Still selection staged. */
        if(!result||result==6) {
            int saved=nx_tune_save(argv[2],&tune,strength);
            if(!saved)fputs("PERSIST-WARNING per-look tuning was not saved\n",stderr);
            else if(saved<0)fputs("PERSIST-UNCONFIRMED per-look tuning written but directory sync failed\n",stderr);
        }
        return result;
    }
    return 2;
}
int main(int argc,char **argv) {
    setvbuf(stdout,NULL,_IOLBF,0);
    char root[PATH_MAX];if (!nx_lut_root(root)) { fputs("Invalid or missing NX300M_MOD_ROOT\n",stderr);return 2; }
    /* This preference has its own single writer: the serialized preview
     * wrapper. Do not acquire engine -> preview locks in the inverse order. */
    if(argc==3&&!strcmp(argv[1],"save-preview")) {
        if(strcmp(argv[2],"0")&&strcmp(argv[2],"1"))return 2;
        return nx_lut_preview_preference((unsigned)(argv[2][0]-'0'))?0:4;
    }
    if (argc>=2 && !strcmp(argv[1],"ready")) return action(argc,argv);
    if (argc>=2 && !strcmp(argv[1],"stage-state")) return stage_state();
    /* Panel and HTTP requests share the same private runtime state. */
    int lock=nx_lut_lock(0,1);if (lock<0) return 4;
    int result=action(argc,argv);close(lock);return result;
}
