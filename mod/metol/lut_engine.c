/* Metol backend protocol for NX300M's native LUT-call adapter.
 * Selection is staged in private runtime storage. Firmware owns reload timing.
 */
#define _XOPEN_SOURCE 700
#include <dirent.h>
#include "lut_engine_shared.h"

static int stage(const char *path,unsigned int strength) {
    unsigned int generation=0;int result=nx_lut_stage_with_strength(path,strength,&generation);
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
    if (!strcmp(argv[1],"persist-status")) {
        if (argc!=2) return 2;
        puts(nx_lut_selection_persisted()?"PERSISTED":"NOT_SAVED");return 0;
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
    if (strcmp(argv[1],"apply") || (argc!=3 && argc!=4) || !nx_lut_id_valid(argv[2]) ||
        (argc==4 && !nx_lut_strength(argv[3],&strength))) return 2;
    if (!nx_lut_hook_ready()) { puts("REJECT native LUT adapter is unavailable or its live mapping cannot be verified; RAM hooks require readable process memory");return 3; }
    struct nx_lut_look looks[NX_LUT_MAX_LOOKS];int count=0;
    int result=nx_lut_catalog(looks,&count);if (result) return result;
    for (int i=0;i<count;i++) if (!strcmp(looks[i].id,argv[2])) {
        char path[256];int composed;
        if (!looks[i].table_valid || !nx_lut_validate_look(argv[2],path,&composed)) return 5;
        return stage(path,strength);
    }
    return 2;
}
int main(int argc,char **argv) {
    char root[PATH_MAX];if (!nx_lut_root(root)) { fputs("Invalid or missing NX300M_MOD_ROOT\n",stderr);return 2; }
    if (argc>=2 && !strcmp(argv[1],"ready")) return action(argc,argv);
    if (argc>=2 && !strcmp(argv[1],"stage-state")) return stage_state();
    /* Panel and HTTP requests share the same private runtime state. */
    int lock=nx_lut_lock(0,1);if (lock<0) return 4;
    int result=action(argc,argv);close(lock);return result;
}
