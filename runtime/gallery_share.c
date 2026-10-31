/* NX300M 1.15 single-photo and thumbnail share entries, before Ecore dispatch.
 * The copied prologue has no PC-relative instructions. Original bytes are
 * checked in full; an unknown app keeps its original share implementation. */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/file.h>
#include <limits.h>
#include <fcntl.h>
#include <unistd.h>
#include <dirent.h>
#include <signal.h>
#include <sys/syscall.h>
#include <errno.h>
#include "../mod/metol/mod_paths.h"
#include "close_inherited_fds.h"
#include "gallery_share_stock.h"

extern char **environ;
/* Stock glibc 2.13 exposes the ARM large-file stat ABI via __xstat64. Modern
 * headers otherwise emit stat64, which is absent from the camera library. */
extern int nx_stock_stat64(int,const char *,struct stat *) __asm__("__xstat64");
extern int nx_stock_lstat64(int,const char *,struct stat *) __asm__("__lxstat64");
extern int nx_stock_fstat64(int,int,struct stat *) __asm__("__fxstat64");
static void (*stock_share)(void), (*stock_upload)(void), (*stock_multi)(void);
static void (*stock_depth2)(void);
static int (*current_index)(void), (*get_state)(int), (*get_value)(int);
static const char *(*filename)(int);
static int (*selected_count)(void), (*selected_first)(void), (*selected_next)(unsigned char);
static const char *(*current_file)(unsigned char);
static void (*set_file)(char *);
static int (*database_mode)(void);
struct menu_info { int event,source,item,value; };
static int (*menu_svs)(void),(*confirm_open)(void);
static void *(*application)(void),*(*current_gui)(void *),*(*depth2_instance)(void);
static void (*manage_menu)(struct menu_info,unsigned char);
static void (*thumbnail_share)(void), (*menu_destruct)(void);
static int (*wifi_value)(int);
static void (*set_value)(int,int);
static void *(*add_job)(void (*)(void *),void *);
static int menu_job_pending;

/* The camera keeps its SD/LUT root. Only a verified live RAM service supplies
 * the panel location; absent/stale state retains the original share action. */
static int service_location(char root[PATH_MAX],char state[PATH_MAX]) {
    char base[PATH_MAX],path[PATH_MAX],line[PATH_MAX],record[5][PATH_MAX];
    if (!realpath("/tmp/nx300m-mfw",base) || !nx_mod_root_valid(base) ||
        snprintf(root,PATH_MAX,"%s/runtime",base)>=PATH_MAX ||
        snprintf(state,PATH_MAX,"%s/state",base)>=PATH_MAX) return 0;
    FILE *f=fopen("/proc/mounts","r");if(!f)return 0;
    size_t best=0;int tmpfs=0;char device[PATH_MAX],point[PATH_MAX],type[64],options[4096];
    while(fgets(line,sizeof line,f)) {
        if(sscanf(line,"%4095s %4095s %63s %4095s",device,point,type,options)!=4)continue;
        size_t n=strlen(point);
        if(n>=best && (!strcmp(point,"/") || (!strncmp(base,point,n) && (!base[n] || base[n]=='/')))) {
            best=n;tmpfs=!strcmp(type,"tmpfs") && !strstr(options,"noexec");
        }
    }
    fclose(f);if(!tmpfs)return 0;
    snprintf(path,sizeof path,"%s/service.state",state);f=fopen(path,"r");
    if(!f)return 0;int ready=fgets(line,sizeof line,f) && !strcmp(line,"ready\n");fclose(f);if(!ready)return 0;
    snprintf(path,sizeof path,"%s/remote.pid",state);f=fopen(path,"r");if(!f)return 0;
    int valid=1;for(int i=0;i<5;i++) {
        if(!fgets(record[i],sizeof record[i],f) || !strchr(record[i],'\n')){valid=0;break;}
        record[i][strcspn(record[i],"\n")]=0;
    }
    fclose(f);if(!valid)return 0;
    char *end;long pid=strtol(record[0],&end,10);if(*end || pid<=1 || pid>INT_MAX)return 0;
    snprintf(path,sizeof path,"%s/bin/nx-remote-controller.arm",root);if(strcmp(path,record[3]))return 0;
    struct stat expected,actual;
    if(nx_stock_stat64(3,path,&expected))return 0;
    char identity[96];snprintf(identity,sizeof identity,"%llu:%llu",(unsigned long long)expected.st_dev,(unsigned long long)expected.st_ino);
    if(strcmp(identity,record[4]))return 0;
    snprintf(path,sizeof path,"/proc/%ld/exe",pid);
    ssize_t bytes=readlink(path,line,sizeof line-1);if(bytes<=0)return 0;line[bytes]=0;
    if(strcmp(line,record[3]) || nx_stock_stat64(3,path,&actual) ||
        expected.st_dev!=actual.st_dev || expected.st_ino!=actual.st_ino)return 0;
    f=fopen("/proc/sys/kernel/random/boot_id","r");if(!f)return 0;
    valid=fgets(line,sizeof line,f)!=NULL;fclose(f);if(!valid)return 0;
    line[strcspn(line,"\n")]=0;if(strcmp(line,record[2]))return 0;
    snprintf(path,sizeof path,"/proc/%ld/stat",pid);f=fopen(path,"r");if(!f)return 0;
    valid=fgets(line,sizeof line,f)!=NULL;fclose(f);if(!valid)return 0;
    char *p=strrchr(line,')');if(!p || p[1]!=' ')return 0;p+=2;
    if(*p=='Z')return 0;
    for(int field=3;field<22;field++){p=strchr(p,' ');if(!p)return 0;p++;}
    p[strcspn(p," \n")]=0;if(strcmp(p,record[1]))return 0;
    DIR *directory=opendir("/proc");if(!directory)return 0;
    unsigned count=0;struct dirent *entry;
    while((entry=readdir(directory))) {
        if(entry->d_name[0]<'0' || entry->d_name[0]>'9')continue;
        snprintf(path,sizeof path,"/proc/%s/exe",entry->d_name);
        bytes=readlink(path,line,sizeof line-1);if(bytes<=0)continue;line[bytes]=0;
        const char *name=strrchr(line,'/');if(name && (!strcmp(name+1,"nx-remote-controller.arm") ||
            !strcmp(name+1,"nx-remote-controller.arm (deleted)")))count++;
    }
    closedir(directory);return count==1 && !kill((pid_t)pid,0);
}

/* Join the service's release barrier before calling ASL or resolving any SD
 * path. Pause is persistent; a failed check retains the gallery without
 * starting the stock network share action. */
static int gallery_media_lock(const char *state) {
    char marker[PATH_MAX],path[PATH_MAX];struct stat st;
    if (snprintf(marker,sizeof marker,"%s/media-paused",state)>=(int)sizeof marker ||
        snprintf(path,sizeof path,"%s/media-io.lock",state)>=(int)sizeof path) return -1;
    if (!nx_stock_lstat64(3,marker,&st) || errno!=ENOENT) return -1;
    int fd=open(path,O_RDWR|O_CREAT|O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC,0600);
    if (fd<0) return -1;
    if (nx_stock_fstat64(3,fd,&st) || !S_ISREG(st.st_mode) || flock(fd,LOCK_SH|LOCK_NB) ||
        !nx_stock_lstat64(3,marker,&st) || errno!=ENOENT) { close(fd);return -1; }
    return fd;
}

static int spawn_panel(char **args,const char *root,const char *state) {
    char root_env[PATH_MAX+32],mod_env[PATH_MAX+32],state_env[PATH_MAX+32];
    snprintf(root_env,sizeof root_env,"NX300M_MOD_ROOT=%s",root);
    snprintf(mod_env,sizeof mod_env,"NX_MOD_ROOT=%s",root);
    snprintf(state_env,sizeof state_env,"NX_STATE_DIR=%s",state);
    size_t count=0;while (environ[count]) count++;
    char **environment=calloc(count+4,sizeof(char *));
    if (!environment) return -1;
    size_t n=0;
    for (size_t i=0;i<count;i++)
        if (strncmp(environ[i],"LD_PRELOAD=",11) && strncmp(environ[i],"LD_AUDIT=",9) &&
            strncmp(environ[i],"NX300M_MOD_ROOT=",16) && strncmp(environ[i],"NX_MOD_ROOT=",12) &&
            strncmp(environ[i],"NX_STATE_DIR=",13)) environment[n++]=environ[i];
    environment[n++]=root_env;environment[n++]=mod_env;environment[n++]=state_env;
    pid_t child=fork();
    if (!child) {
        /* No allocator, stdio or loader work between fork and exec. */
        int fd=open("/dev/null",O_RDWR);if(fd<0)_exit(126);
        if(dup2(fd,0)<0 || dup2(fd,1)<0 || dup2(fd,2)<0 || chdir(root))_exit(126);
        if(nx_close_inherited_fds())_exit(126);
        execve(args[0],args,environment);_exit(127);
    }
    free(environment);
    /* Ecore owns child reaping. Never block the camera event loop. */
    return child;
}
static void selection_error(const char *code) {
    char launcher[PATH_MAX],panel[PATH_MAX],root[PATH_MAX],state[PATH_MAX];
    fprintf(stderr,"[NX QS] selection rejected: %s; gallery retained\n",code);
    if (!service_location(root,state) || snprintf(launcher,sizeof launcher,"%s/quickshare-panel.sh",root)>=(int)sizeof launcher || access(launcher,R_OK) ||
        snprintf(panel,sizeof panel,"%s/bin/quickshare-panel.arm",root)>=(int)sizeof panel || access(panel,X_OK)) return;
    char *args[]={"/bin/sh",launcher,"--error",(char *)code,NULL};
    if (spawn_panel(args,root,state)<0) fprintf(stderr,"[NX QS] error panel unavailable; gallery retained\n");
}
static void selection_unavailable(void) { selection_error("selection-unavailable"); }

/* Caller holds gallery_media_lock across ASL selection and these SD checks. */
static int launch_paths(const char *const *sources,unsigned files,const char *root,const char *state) {
    char launcher[PATH_MAX], panel[PATH_MAX], path[1024], absolute[16][PATH_MAX], media[PATH_MAX];
    if (snprintf(launcher,sizeof launcher,"%s/quickshare-panel.sh",root)>=(int)sizeof launcher ||
        access(launcher,R_OK) ||
        snprintf(panel,sizeof panel,"%s/bin/quickshare-panel.arm",root)>=(int)sizeof panel ||
        access(panel,X_OK) || !files || files>16) {
        fprintf(stderr,"[NX QS] launcher unavailable at root=%s\n",root);
        return 0;
    }
    /* Catch stale ASL selections and incomplete SD installs before suppressing
     * the original action. The page/service repeat this check at use time. */
    struct stat file;
    const char *media_root=getenv("NX_MEDIA_ROOT");
    if (!media_root) media_root="/mnt/mmc/DCIM";
    if (!realpath(media_root,media)) return 0;
    for (unsigned i=0;i<files;i++) {
        const char *source=sources[i];
        if (!source || !*source || strnlen(source,sizeof path)>=sizeof path) return 0;
        if (source[0]=='/') snprintf(path,sizeof path,"%s",source);
        else if (snprintf(path,sizeof path,"%s/%s",media_root,source)>=(int)sizeof path) return 0;
        if (!realpath(path,absolute[i]) || strncmp(absolute[i],media,strlen(media)) ||
            absolute[i][strlen(media)]!='/' || nx_stock_stat64(3,absolute[i],&file) ||
            !S_ISREG(file.st_mode) || access(absolute[i],R_OK)) {
            fprintf(stderr,"[NX QS] selected file unavailable\n");
            return 0;
        }
        for (unsigned j=0;j<i;j++) if (!strcmp(absolute[i],absolute[j])) return 0;
    }
    char *args[19]={"/bin/sh",launcher};
    for (unsigned i=0;i<files;i++) args[i+2]=absolute[i];
    args[files+2]=NULL;
    int child=spawn_panel(args,root,state);
    if (child<0) return 0;
    fprintf(stderr,"[NX QS] panel launch requested pid=%d files=%u first=%s\n",child,files,absolute[0]);
    return 1;
}
static void launch(void (*fallback)(void)) {
    if (!current_index || !filename) { fallback();return; }
    char root[PATH_MAX],state[PATH_MAX];
    if (!service_location(root,state)) { fallback();return; }
    int lock=gallery_media_lock(state);if(lock<0)return;
    const char *source=filename(current_index());
    int launched=launch_paths(&source,1,root,state);
    close(lock);if(!launched)fallback();
}
static void upload_multi(void) {
    if (get_state(0xce)!=0xa || get_state(0xc9)!=2) { stock_multi();return; }
    char root[PATH_MAX],state[PATH_MAX];
    if (!service_location(root,state)) { stock_multi();return; }
    int lock=gallery_media_lock(state);if(lock<0)return;
    if (database_mode()!=0) { close(lock);stock_multi();return; }
    int count=selected_count();
    if (count>16) { close(lock);selection_error("selection-limit");return; }
    if (count<1) { close(lock);selection_unavailable();return; }
    char saved[1024], paths[16][1024];const char *sources[16];
    const char *current=current_file(0);
    if (!current || !*current || strnlen(current,sizeof saved)>=sizeof saved) { close(lock);selection_unavailable();return; }
    strcpy(saved,current);
    int valid=1;
    for (int i=0;i<count;i++) {
        /* ASL changes the current file while iterating. Copy its shared path
         * immediately; returned indices differ between DB and DCM modes. */
        int index=!i?selected_first():selected_next(0);
        if (index<0) { valid=0;break; }
        current=current_file(0);
        if (!current || !*current || strnlen(current,sizeof paths[i])>=sizeof paths[i]) { valid=0;break; }
        strcpy(paths[i],current);sources[i]=paths[i];
    }
    set_file(saved);
    current=current_file(0);
    if (!valid || !current || strcmp(current,saved) || selected_count()!=count) { close(lock);selection_unavailable();return; }
    int launched=launch_paths(sources,(unsigned)count,root,state);
    close(lock);if(!launched)selection_unavailable();
}
static void share(void) {
    int state=get_state(0xd9);
    fprintf(stderr,"[NX QS] menu share requested playback_type=%d\n",state);
    if (state==1) launch(stock_share);
}
static void upload(void) {
    /* Direct photo touch uses the current playback value, while menu sharing
     * uses the display type saved before opening the menu. */
    int value=get_value(0x1fb);
    fprintf(stderr,"[NX QS] direct share requested selection=%d\n",value);
    if (value==0xf) launch(stock_upload);
}
static int share_menu_active(void) {
    int type=get_state?get_state(0xd9):0;
    int supported=type==1 || (type==2 && thumbnail_share && menu_destruct && wifi_value && set_value &&
                             stock_multi && wifi_value(0x70f)<=0);
    return menu_svs && confirm_open && application && current_gui && depth2_instance &&
        supported && menu_svs()==0x1fb && !confirm_open() &&
        current_gui(application())==depth2_instance();
}
static void open_menu_share(void *unused) {
    (void)unused;menu_job_pending=0;
    if (!share_menu_active()) return;
    if (get_state(0xd9)==2) {
        /* Original thumbnail share transition destroys the menu before it
         * constructs the selection page. Do not construct normal thumbnails
         * first or replace its selection state with a synthetic list. */
        /* Target 0xf deliberately disables thumbnail checkboxes. Target 0xa
         * has the same stock file policy but retains the native multi-select
         * UI; its upload callback is intercepted separately above. */
        menu_destruct();set_value(0x1fb,0xa);thumbnail_share();
        fprintf(stderr,"[NX QS] Share(Wi-Fi) opened original thumbnail selection\n");
        return;
    }
    /* Defer until onEnter returns: closing the active menu from within its
     * construction would destroy objects still in use by the original call. */
    struct menu_info close_menu={6,0,0,0};
    manage_menu(close_menu,0);
    fprintf(stderr,"[NX QS] Share(Wi-Fi) menu activated Quick Share\n");
    launch(stock_share);
}
static void enter_depth2(void *self) {
    ((void (*)(void *))stock_depth2)(self);
    if (!menu_job_pending && share_menu_active()) {
        menu_job_pending=1;
        if (!add_job(open_menu_share,NULL)) menu_job_pending=0;
    }
}

static void patch_entry(const char *symbol,uintptr_t address,const unsigned char *expected,size_t bytes,
                        void (*replacement)(void),void (**fallback)(void)) {
    unsigned char *entry=symbol?dlsym(RTLD_DEFAULT,symbol):(unsigned char *)address;
    if (!entry || (uintptr_t)entry!=address || memcmp(entry,expected,bytes)) {
        fprintf(stderr,"[NX QS] unsupported share entry; original retained\n");return;
    }
    uint32_t *bridge=mmap(NULL,4096,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
    if (bridge==MAP_FAILED) return;
    memcpy(bridge,entry,8);bridge[2]=0xe51ff004;bridge[3]=(uint32_t)(uintptr_t)(entry+8);
    __builtin___clear_cache((char *)bridge,(char *)(bridge+4));
    if (mprotect(bridge,4096,PROT_READ|PROT_EXEC)) { munmap(bridge,4096);return; }
    void *page=(void *)((uintptr_t)entry&~4095u);
    if (mprotect(page,4096,PROT_READ|PROT_WRITE|PROT_EXEC)) { munmap(bridge,4096);return; }
    *fallback=(void (*)(void))bridge;
    uint32_t patch[]={0xe51ff004,(uint32_t)(uintptr_t)replacement};memcpy(entry,patch,sizeof patch);
    __builtin___clear_cache((char *)entry,(char *)entry+8);
    if (mprotect(page,4096,PROT_READ|PROT_EXEC)) fprintf(stderr,"[NX QS] cannot restore text protection\n");
    fprintf(stderr,"[NX QS] gallery entry installed: %s\n",symbol?symbol:"UI_PB_Menu_Share");
}
static void install(void) {
    char exe[256];ssize_t len=readlink("/proc/self/exe",exe,sizeof exe-1);
    if (len<0) return;
    exe[len]=0;const char *base=strrchr(exe,'/');base=base?base+1:exe;
    if (strcmp(base,"di-camera-app-nx300") && strcmp(base,"di-camera-app")) return;
    current_index=dlsym(RTLD_DEFAULT,"_Z26ASLFileManager_getCurIndexv");
    filename=dlsym(RTLD_DEFAULT,"_Z26ASLFileManager_getFilenamei");
    get_state=dlsym(RTLD_DEFAULT,"_Z12UI_Get_State14eUI_STATE_ITEM");
    get_value=dlsym(RTLD_DEFAULT,"_Z12UI_Get_Value14eUI_VALUE_ITEM");
    selected_count=dlsym(RTLD_DEFAULT,"_Z29ASLFileManager_getSelectedNumv");
    selected_first=dlsym(RTLD_DEFAULT,"_Z41ASLFileManager_getSelectedFirsetFileIndexv");
    selected_next=dlsym(RTLD_DEFAULT,"_Z39ASLFileManager_getNextSelectedFileIndexb");
    current_file=dlsym(RTLD_DEFAULT,"_Z22ASLFileManager_getCurrb");
    set_file=dlsym(RTLD_DEFAULT,"_Z22ASLFileManager_setFilePc");
    database_mode=dlsym(RTLD_DEFAULT,"_Z17ASLDCM_getDBStatev");
    if (!current_index || !filename) return;
    if (get_state) patch_entry("_Z18UI_PB_Share_Singlev",NX_SHARE_ADDRESS,nx_share_stock,sizeof nx_share_stock,share,&stock_share);
    if (get_value) patch_entry("_Z25UI_PB_Single_upload_Filesv",NX_UPLOAD_ADDRESS,nx_upload_stock,sizeof nx_upload_stock,upload,&stock_upload);
    if (get_state && selected_count && selected_first && selected_next && current_file && set_file && database_mode)
        patch_entry("_Z25UI_Thumbnail_Upload_Filesv",NX_MULTI_ADDRESS,nx_multi_stock,sizeof nx_multi_stock,upload_multi,&stock_multi);
    menu_svs=dlsym(RTLD_DEFAULT,"_ZN15CAPPGUIMenuView6getSVSEv");
    confirm_open=dlsym(RTLD_DEFAULT,"_Z24Is_Common_Confirm_Windowv");
    application=dlsym(RTLD_DEFAULT,"_ZN18CAPPGUIApplication11getInstanceEv");
    current_gui=dlsym(RTLD_DEFAULT,"_ZNK16CSAFStateMachine15getCurrentStateEv");
    depth2_instance=dlsym(RTLD_DEFAULT,"_ZN22CAPPGUIMenuDepth2State11getInstanceEv");
    manage_menu=dlsym(RTLD_DEFAULT,"_Z29UI_Manage_PB_Menu_Info_By_GUI13tag_MENU_INFOb");
    thumbnail_share=dlsym(RTLD_DEFAULT,"_Z21UI_PB_Share_Thumbnailv");
    menu_destruct=dlsym(RTLD_DEFAULT,"_Z19UI_PB_Menu_Destructv");
    wifi_value=dlsym(RTLD_DEFAULT,"_Z22UI_Get_Value_WiFi_Info14eUI_VALUE_ITEM");
    set_value=dlsym(RTLD_DEFAULT,"_Z12UI_Set_Value14eUI_VALUE_ITEMi");
    add_job=dlsym(RTLD_DEFAULT,"ecore_job_add");
    if (get_state && menu_svs && confirm_open && application && current_gui && depth2_instance && manage_menu && add_job && stock_share)
        patch_entry("_ZN22CAPPGUIMenuDepth2State7onEnterEv",NX_DEPTH2_ADDRESS,nx_depth2_stock,sizeof nx_depth2_stock,(void (*)(void))enter_depth2,&stock_depth2);
}
void ecore_main_loop_begin(void) {
    static int once;
    void (*original)(void)=dlsym(RTLD_NEXT,"ecore_main_loop_begin");
    if (!once++) install();
    if (original) original();
}
