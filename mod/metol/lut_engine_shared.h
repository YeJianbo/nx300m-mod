#ifndef NX300M_LUT_ENGINE_SHARED_H
#define NX300M_LUT_ENGINE_SHARED_H
#include <errno.h>
#include <ctype.h>
#include <fcntl.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>
#include "mod_paths.h"
#include "../../runtime/lut_ram_blob.h"
#include "../../runtime/lut_ram_blob_build.h"

#define NX_LUT_SELECTION "/var/run/nx300m-lut-selection"
#define NX_LUT_BACKUP "/var/run/nx300m-lut-panel-backup"
#define NX_LUT_COUNTER "/var/run/nx300m-lut-generation"
#define NX_LUT_MAX_LOOKS 256
#define NX_LUT_RAM_MARKER "/var/run/nx300m-lut-ram-hook"
struct nx_lut_look { char id[64],label[192];int table_valid,composed_available; };

static inline int nx_lut_id_valid(const char *key) {
    size_t length=strlen(key);
    if (!length || length>63 || !((key[0]>='a' && key[0]<='z') || (key[0]>='0' && key[0]<='9'))) return 0;
    for (size_t i=0;i<length;i++) if (!((key[i]>='a' && key[i]<='z') || (key[i]>='0' && key[i]<='9') || key[i]=='_' || key[i]=='-')) return 0;
    return 1;
}
/* RAM deployment changes executable paths, but the already-running camera
 * hook still owns its original data root. Never substitute a different card. */
static inline const char *nx_lut_data_root(void) {
    const char *source=getenv("NX300M_MOD_SOURCE_ROOT"),*identity=getenv("NX300M_MOD_SOURCE_ID");
    if (!source && !identity) return nx_mod_root();
    if (!source || !identity || !*identity || !nx_mod_root_valid(source)) return NULL;
    char canonical[PATH_MAX],actual[64];struct stat state;
    if (!realpath(source,canonical) || strcmp(source,canonical) || stat(source,&state) || !S_ISDIR(state.st_mode)) return NULL;
    int n=snprintf(actual,sizeof actual,"%llu:%llu",(unsigned long long)state.st_dev,(unsigned long long)state.st_ino);
    return n>0 && n<(int)sizeof actual && !strcmp(actual,identity)?source:NULL;
}
static inline int nx_lut_root(char *canonical) {
    const char *root=nx_lut_data_root();struct stat state;
    if (!root || !realpath(root,canonical) || stat(canonical,&state) || !S_ISDIR(state.st_mode)) return 0;
    return 1;
}
/* Resolve the configured root, then forbid file/ID symlinks escaping it or
 * aliasing a different library entry. Root itself may be a mounted SD path. */
static inline int nx_lut_file(const char *relative,char *path) {
    char root[PATH_MAX],expected[PATH_MAX],actual[PATH_MAX];
    const char *configured=nx_lut_data_root();
    if (!configured || !nx_lut_root(root)) return 0;
    int n=snprintf(path,256,"%s/%s",configured,relative);
    int m=snprintf(expected,sizeof expected,"%s/%s",root,relative);
    if (n<0 || n>=256 || m<0 || m>=(int)sizeof expected || !realpath(path,actual) || strcmp(actual,expected)) return 0;
    struct stat state;return !stat(actual,&state) && S_ISREG(state.st_mode) && !access(actual,R_OK);
}
static inline int nx_lut_table_valid(const char *path) {
    FILE *in=fopen(path,"rb");if (!in) return 0;
    uint16_t values[2190];size_t count=fread(values,sizeof(uint16_t),2190,in);
    int extra=fgetc(in),failed=ferror(in);fclose(in);
    if (count!=2190 || extra!=EOF || failed) return 0;
    for (unsigned int i=0;i<2190;i++) if (values[i]>1023 || (i%730==729 && values[i])) return 0;
    return 1;
}
static inline int nx_lut_validate_look(const char *id,char *path,int *composed_available) {
    char relative[128],variant[256];*composed_available=0;
    if (!nx_lut_id_valid(id)) return 0;
    snprintf(relative,sizeof relative,"looks/%s/table.d4lut",id);
    if (!nx_lut_file(relative,path) || !nx_lut_table_valid(path)) return 0;
    snprintf(relative,sizeof relative,"looks/%s/table-stock3d.d4lut",id);
    const char *root=nx_lut_data_root();if (!root) return 0;
    snprintf(variant,sizeof variant,"%s/%s",root,relative);
    struct stat state;
    if (lstat(variant,&state)) return errno==ENOENT;
    *composed_available=1;
    return nx_lut_file(relative,variant) && nx_lut_table_valid(variant);
}
/* looks.txt uses the same 9-column format as NXUI. Refuse ambiguous IDs and
 * malformed metadata instead of letting CLI and HTTP select different rows. */
static inline int nx_lut_catalog(struct nx_lut_look *looks,int *count) {
    char path[256];*count=0;
    if (!nx_lut_file("looks.txt",path)) return 4;
    FILE *in=fopen(path,"r");if (!in) return 4;
    char line[1024];int result=0;
    while (fgets(line,sizeof line,in)) {
        if (!strchr(line,'\n') && !feof(in)) { result=5;break; }
        line[strcspn(line,"\r\n")]=0;
        if (!*line) continue;
        if (*count>=NX_LUT_MAX_LOOKS) { result=5;break; }
        char *columns[9],*p=line;int n=0;
        for (;;) { columns[n++]=p;char *end=strchr(p,'|');if (!end) break;if (n==9) { result=5;break; }*end=0;p=end+1; }
        if (result || n!=9 || !nx_lut_id_valid(columns[0]) || !*columns[1] || strlen(columns[1])>191) { result=5;break; }
        for (const unsigned char *label=(const unsigned char *)columns[1];*label;label++) if (*label<32 || *label==127) result=5;
        for (int i=2;i<9;i++) { char *end;errno=0;strtol(columns[i],&end,10);if (errno || end==columns[i] || *end) result=5; }
        for (int i=0;i<*count;i++) if (!strcmp(looks[i].id,columns[0])) result=5;
        if (result) break;
        struct nx_lut_look *look=&looks[(*count)++];
        strcpy(look->id,columns[0]);strcpy(look->label,columns[1]);
        look->table_valid=nx_lut_validate_look(look->id,path,&look->composed_available);
    }
    if (ferror(in)) result=4;
    fclose(in);return result;
}
static inline int nx_lut_uint(const char *value,unsigned int *out) {
    if (!*value) return 0;
    unsigned int number=0;
    for (const unsigned char *p=(const unsigned char *)value;*p;p++) {
        if (*p<'0' || *p>'9' || number>(UINT_MAX-(*p-'0'))/10U) return 0;
        number=number*10U+(*p-'0');
    }
    *out=number;return 1;
}
static inline int nx_lut_u64(const char *value,unsigned long long *out) {
    if (!*value) return 0;
    unsigned long long number=0;
    for (const unsigned char *p=(const unsigned char *)value;*p;p++) {
        if (*p<'0' || *p>'9' || number>(ULLONG_MAX-(*p-'0'))/10ULL) return 0;
        number=number*10ULL+(*p-'0');
    }
    *out=number;return 1;
}
static inline int nx_lut_starttime(int pid,unsigned long long *start) {
    char file[64],body[4096];snprintf(file,sizeof file,"/proc/%d/stat",pid);
    FILE *in=fopen(file,"r");if (!in) return 0;
    int ok=fgets(body,sizeof body,in)!=NULL;fclose(in);
    char *p=ok?strrchr(body,')'):NULL;if (!p || p[1]!=' ') return 0;p+=2;
    for (int field=3;field<22;field++) { p=strchr(p,' ');if (!p) return 0;p++; }
    char *end=p;while (*end && !isspace((unsigned char)*end)) end++;*end=0;
    return nx_lut_u64(p,start) && *start;
}
static inline int nx_lut_mem(int fd,unsigned int address,void *data,size_t size) {
    return pread(fd,data,size,(off_t)address)==(ssize_t)size;
}
/* The caller has already verified the camera identity and translated its PID
 * through /proc when running under QEMU. RAM markers add a lifetime, layout,
 * permissions and live binding check; they are not readiness by themselves. */
static inline int nx_lut_ram_hook_ready(int camera_pid,int host_pid,const char *root) {
    const char *diag=getenv("NX_FN_KEY_LOG");
    FILE *in=fopen(NX_LUT_RAM_MARKER,"r");if (!in) return 0;
    char marker[384],*fields[5],*p=marker;int count=0;
    size_t bytes=fread(marker,1,sizeof marker-1,in);int extra=fgetc(in),failed=ferror(in);fclose(in);
    if (extra!=EOF || failed || !bytes) return 0;
    marker[bytes]=0;if (strlen(marker)!=bytes) return 0;
    while (*p) {
        while (isspace((unsigned char)*p)) p++;
        if (!*p) break;
        if (count==5) return 0;
        fields[count++]=p;
        while (*p && !isspace((unsigned char)*p)) p++;
        if (*p) *p++=0;
    }
    unsigned int pid,base,size;unsigned long long start,actual_start;
    if (count!=5 || !nx_lut_uint(fields[0],&pid) || pid!=(unsigned int)camera_pid ||
        !nx_lut_u64(fields[1],&start) || !start || !root || strcmp(root,fields[2]) ||
        !nx_lut_uint(fields[3],&base) || !base || base%4096 || !nx_lut_uint(fields[4],&size) ||
        !size || size>1024*1024 || size%4096 || base>UINT_MAX-size ||
        !nx_lut_starttime(host_pid,&actual_start) || start!=actual_start) return 0;
    char file[64];snprintf(file,sizeof file,"/proc/%d/mem",host_pid);
    int fd=open(file,O_RDONLY);if (fd<0) return 0;
    struct nx_lut_ram_header h;struct nx_lut_ram_config config;int ok=0;const char *why="ok";
    config.size=0;
    if (!nx_lut_mem(fd,base,&h,sizeof h)) {why="header-unreadable";goto done;}
    if (h.magic!=NX_LUT_RAM_MAGIC||h.abi!=NX_LUT_RAM_ABI) {why="header-magic";goto done;}
    if (h.blob_size!=size) {why="header-size";goto done;}
    if (h.code_size!=h.data_offset || h.data_offset<4096 || h.data_offset>=size ||
        h.data_offset%4096 || h.data_size!=size-h.data_offset) {why="header-layout";goto done;}
    if (h.config_size!=sizeof config) {why="config-size";goto done;}
    if (h.config_offset<h.data_offset || h.config_offset%4 ||
        h.config_offset>size-sizeof config || h.update_entry<sizeof h || h.update_entry>=h.code_size ||
        h.ipcs_entry<sizeof h || h.ipcs_entry>=h.code_size || h.update_entry==h.ipcs_entry || h.update_entry%4 || h.ipcs_entry%4) {why="entry-layout";goto done;}
    if (h.reserved[0]<h.data_offset || h.reserved[0]>size ||
        (h.reserved[1]!=NX_LUT_RAM_CONTEXT_STRIDE_V1 && h.reserved[1]!=NX_LUT_RAM_CONTEXT_STRIDE_DIAGNOSTIC_V1) || h.reserved[2]!=8 ||
        h.reserved[1]*h.reserved[2]>size-h.reserved[0]) {why="context-layout";goto done;}
    if (!nx_lut_mem(fd,base+h.config_offset,&config,sizeof config)) {why="config-unreadable";goto done;}
    if (config.abi!=NX_LUT_RAM_ABI || config.size!=sizeof config || config.enabled!=1 ||
        !memchr(config.root,0,sizeof config.root) || strcmp(config.root,root)) {why="config-fields";goto done;}
    if (config.original_update<0x350dc0 || config.original_ipcs<0x955c8) {why="config-originals";goto done;}
    unsigned int iq=config.original_update-0x350dc0,udd=config.original_ipcs-0x955c8;
    if (iq>UINT_MAX-0x3c2ab8 || udd>UINT_MAX-0x956b8 || config.original_reset!=iq+0x350e0c ||
        config.lut_vptr!=iq+0x3bfd08 || config.factory_red!=iq+0x36b184) {why="config-offsets";goto done;}
    unsigned int update,ipcs;
    if (!nx_lut_mem(fd,iq+0x3bfd08,&update,4) || update!=base+h.update_entry ||
        !nx_lut_mem(fd,iq+0x3c2ab4,&ipcs,4) || ipcs!=base+h.ipcs_entry) {why="hook-slots";goto done;}
    snprintf(file,sizeof file,"/proc/%d/maps",host_pid);in=fopen(file,"r");if (!in) goto done;
    char line[1024];unsigned int seen=0;
    while (fgets(line,sizeof line,in)) {
        unsigned long long lo,hi,offset,inode;unsigned int major,minor;char perm[5],path[512]="";
        int n=sscanf(line,"%llx-%llx %4s %llx %x:%x %llu %511[^\n]",&lo,&hi,perm,&offset,&major,&minor,&inode,path);
        if (n<7) continue;
        char *name=path;while (*name==' ') name++;
        int anonymous=!inode && (!*name || !strncmp(name,"[anon:",6));
        if (anonymous && lo<=base && hi>=(unsigned long long)base+h.code_size && !strcmp(perm,"r-xp")) seen|=1;
        if (anonymous && lo<=(unsigned long long)base+h.data_offset && hi>=(unsigned long long)base+size && !strcmp(perm,"rw-p")) seen|=2;
        const char *last=strrchr(name,'/');last=last?last+1:name;
        if (!strcmp(last,"libiq-fw-slpcam.so")) {
            if (perm[0]=='r' && perm[2]=='x' && lo<=config.original_update && hi>=(unsigned long long)config.original_reset+12) seen|=4;
            if (perm[0]=='r' && perm[1]=='w' && perm[2]=='-' && lo<=iq+0x3bfd08 && hi>=(unsigned long long)iq+0x3c2ab8) seen|=8;
            if (perm[0]=='r' && lo<=config.factory_red && hi>=(unsigned long long)config.factory_red+1460) seen|=16;
        }
        if (!strcmp(last,"libudd.so.3.1") && perm[0]=='r' && perm[2]=='x' &&
            lo<=config.original_ipcs && hi>=(unsigned long long)config.original_ipcs+240) seen|=32;
    }
    ok=!ferror(in) && seen==63;fclose(in);
    if (!ok) {why="maps-coverage";goto done;}
    if (ok) ok=nx_lut_starttime(host_pid,&actual_start) && actual_start==start;
    if (!ok) {why="starttime";goto done;}
done:
    if (diag && !ok) {
        FILE *log=fopen(diag,"a");
        if (log) {
            fprintf(log,"%s hook-ready fail why=%s base=%u size=%u config_size=%u resident_config_size=%u build=%u\n",
                "NX_READY_DIAG:",why,base,size,(unsigned)sizeof config,config.size,NX_LUT_BLOB_BUILD);
            fclose(log);
        }
    }
    close(fd);return ok;
}
static inline int nx_lut_hook_binding_ready(int camera_pid,int host_pid,const char *root) {
    struct stat state;
    if (!lstat(NX_LUT_RAM_MARKER,&state)) return nx_lut_ram_hook_ready(camera_pid,host_pid,root);
    return errno==ENOENT; /* Existing preload hook protocol is unchanged. */
}
static inline int nx_lut_so_mapped(int pid) {
    char file[64],line[1024];snprintf(file,sizeof file,"/proc/%d/maps",pid);
    FILE *in=fopen(file,"r");if (!in) return 0;
    int found=0;
    while (fgets(line,sizeof line,in)) {
        unsigned long long lo,hi,offset,inode;unsigned int major,minor;char perm[5],path[512]="";
        if (sscanf(line,"%llx-%llx %4s %llx %x:%x %llu %511[^\n]",&lo,&hi,perm,&offset,&major,&minor,&inode,path)!=8) continue;
        const char *name=strrchr(path,'/');name=name?name+1:path;
        if (!strcmp(name,"lut_port.so") && !strcmp(perm,"r-xp")) { found=1;break; }
    }
    if (ferror(in)) found=0;
    fclose(in);return found;
}
static inline void nx_lut_ready_diag(const char *why) {
    const char *log=getenv("NX_FN_KEY_LOG");
    if (!log) return;
    FILE *out=fopen(log,"a");
    if (!out) return;
    fprintf(out,"%s hook-ready fail why=%s\n","NX_READY_DIAG:",why);
    fclose(out);
}
static inline int nx_lut_hook_ready(void) {
    FILE *in=fopen("/var/run/nx300m-lut-hook","r");if (!in) {nx_lut_ready_diag("no-hook-marker");return 0;}
    unsigned int pid=0;char text[16],root[161],extra;
    int fields=fscanf(in,"%15s %160s %c",text,root,&extra);fclose(in);
    const char *configured=nx_lut_data_root();
    if (fields!=2 || !nx_lut_uint(text,&pid) || !pid || pid>INT_MAX || !configured || strcmp(root,configured)) {
        nx_lut_ready_diag("marker-fields-or-root");return 0;
    }
    char file[64],exe[512];snprintf(file,sizeof file,"/proc/%u/exe",pid);
    ssize_t n=readlink(file,exe,sizeof exe-1);if (n<0) {nx_lut_ready_diag("exe-unreadable");return 0;}exe[n]=0;
    const char *name=strrchr(exe,'/');name=name?name+1:exe;
    if (strcmp(name,"di-camera-app-nx300") && strcmp(name,"di-camera-app")) {nx_lut_ready_diag("wrong-target");return 0;}
    struct stat state;
    if (!lstat(NX_LUT_RAM_MARKER,&state)) {
        /* A snapshot-restored camera can resurrect an older blob mapping with
         * its marker intact. The blob publishes its build in the status file;
         * an absent or mismatched tag means the resident adapter is stale. */
        FILE *status=fopen("/var/run/nx300m-lut-status.json","r");
        int build_ok=0;char raw[960];raw[0]=0;unsigned raw_len=0;
        if (status) {
            raw_len=(unsigned)fread(raw,1,sizeof raw-1,status);fclose(status);raw[raw_len]=0;
            const char *tag=strstr(raw,"\"build\":");
            if (tag) {
                tag+=8;unsigned value=0;
                if (nx_lut_uint(tag,&value)) build_ok=value==NX_LUT_BLOB_BUILD;
            }
        } else nx_lut_ready_diag("no-status-file");
        if (!build_ok) {
            if (raw_len) {
                const char *log=getenv("NX_FN_KEY_LOG");
                if (log) { FILE *out=fopen(log,"a");if (out) {fprintf(out,"%s status build mismatch: %s\n","NX_READY_DIAG:",raw);fclose(out);} }
            }
            return 0;
        }
        return nx_lut_ram_hook_ready((int)pid,(int)pid,configured);
    }
    if (errno==ENOENT && nx_lut_so_mapped((int)pid)) return 1;
    nx_lut_ready_diag("no-ram-marker");return 0;
}
static inline int nx_lut_strength(const char *value,unsigned int *out) {
    unsigned int number;
    if (!nx_lut_uint(value,&number) || number>100) return 0;
    *out=number;return 1;
}
static inline int nx_lut_selection_path(const char *path) {
    if (!path || path[0]!='/' || strlen(path)>255) return 0;
    for (const unsigned char *p=(const unsigned char *)path;*p;p++) if (*p<=32 || *p==127) return 0;
    return 1;
}
/* Two-field selections predate strength and retain their full-strength meaning.
 * Tokenize whole fields so a long path or malformed strength cannot be split
 * into an otherwise valid selection. Publish outputs only after full parsing. */
static inline int nx_lut_read_selection_with_strength(const char *file,unsigned int *generation,char *path,unsigned int *strength) {
    FILE *in=fopen(file,"r");if (!in) return 0;
    char body[512];size_t bytes=fread(body,1,sizeof body-1,in);
    int extra=fgetc(in),failed=ferror(in);fclose(in);
    if (extra!=EOF || failed || !bytes) return 0;
    body[bytes]=0;if (strlen(body)!=bytes) return 0;
    char *fields[3],*p=body;int count=0;
    while (*p) {
        while (isspace((unsigned char)*p)) p++;
        if (!*p) break;
        if (count==3) return 0;
        fields[count++]=p;
        while (*p && !isspace((unsigned char)*p)) p++;
        if (*p) *p++=0;
    }
    unsigned int parsed_generation,parsed_strength=100;
    if (count<2 || !nx_lut_uint(fields[0],&parsed_generation) || !nx_lut_selection_path(fields[1]) ||
        (count==3 && !nx_lut_strength(fields[2],&parsed_strength))) return 0;
    *generation=parsed_generation;strcpy(path,fields[1]);
    if (strength) *strength=parsed_strength;
    return 1;
}
static inline int nx_lut_read_selection(const char *file,unsigned int *generation,char *path) {
    return nx_lut_read_selection_with_strength(file,generation,path,NULL);
}
static inline int nx_lut_stage_with_strength(const char *path,unsigned int strength,unsigned int *staged_generation) {
    if (!nx_lut_selection_path(path) || strength>100) return 2;
    char old[256];unsigned int generation=0;
    nx_lut_read_selection(NX_LUT_SELECTION,&generation,old);
    FILE *in=fopen(NX_LUT_COUNTER,"r");
    if (in) { unsigned int saved=0;if (fscanf(in,"%u",&saved)==1 && saved>generation) generation=saved;fclose(in); }
    if (++generation==0) generation=1;
    FILE *out=fopen(NX_LUT_COUNTER ".tmp","w");if (!out) return 4;
    int written=fprintf(out,"%u\n",generation)>=0;
    if (fclose(out)) written=0;
    if (!written || rename(NX_LUT_COUNTER ".tmp",NX_LUT_COUNTER)) return 4;
    out=fopen(NX_LUT_SELECTION ".tmp","w");if (!out) return 4;
    written=fprintf(out,"%u %s %u\n",generation,path,strength)>=0;
    if (fclose(out)) written=0;
    if (!written || rename(NX_LUT_SELECTION ".tmp",NX_LUT_SELECTION)) return 4;
    if (staged_generation) *staged_generation=generation;
    return 0;
}
static inline int nx_lut_stage(const char *path,unsigned int *staged_generation) {
    return nx_lut_stage_with_strength(path,100,staged_generation);
}
/* Explicit stock is separate from NXUI off, which restores its entry backup. */
static inline int nx_lut_stock(void) { return unlink(NX_LUT_SELECTION) && errno!=ENOENT?4:0; }
static inline int nx_lut_lock(int nonblocking,int exclusive) {
    int fd=open("/var/run/nx300m-lut-engine.lock",O_RDONLY|O_CREAT,0600);if (fd<0) return -1;
    if (flock(fd,(exclusive?LOCK_EX:LOCK_SH)|(nonblocking?LOCK_NB:0))) { int error=errno;close(fd);errno=error;return -1; }
    return fd;
}
#endif
