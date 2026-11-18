/* Optional camera-local credentials. Missing configuration preserves legacy
 * clients; a damaged existing configuration fails closed until locally fixed. */
#ifndef NX_REMOTE_HTTP_AUTH_H
#define NX_REMOTE_HTTP_AUTH_H
#include "../nx_atomic_write.h"
#define NX_AUTH_KEY_BYTES 16
static int nx_http_auth_enabled;
static int nx_http_auth_in_daemon;
static char nx_http_auth_expected[80];
static volatile sig_atomic_t nx_http_auth_reload;

static int nx_auth_directory(char *path,size_t size,int create) {
    const char *root=getenv("NX300M_MOD_SOURCE_ROOT");
    if(!root||!*root)root=getenv("NX300M_MOD_ROOT");
    if(!root||!*root)root=getenv("NX_MOD_ROOT");
    if(!root||!*root)root="/mnt/mmc/nx300m-mod";
    if(*root!='/'||snprintf(path,size,"%s/.state",root)>=(int)size)return 0;
    if(create&&mkdir(path,0700)&&errno!=EEXIST)return 0;
    struct stat st;
    if(lstat(path,&st))return !create&&errno==ENOENT?2:0;
    return S_ISDIR(st.st_mode)?1:0;
}
/* Strict bounded reads also reject FIFOs, directories and symbolic links. */
static int nx_auth_read(const char *directory,const char *name,char *out,size_t capacity) {
    char path[PATH_MAX];
    if(snprintf(path,sizeof path,"%s/%s",directory,name)>=(int)sizeof path)return -1;
    int fd=open(path,O_RDONLY|O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC);
    if(fd<0)return errno==ENOENT?0:-1;
    struct stat st;int ok=!fstat(fd,&st)&&S_ISREG(st.st_mode)&&st.st_size>0&&st.st_size<(off_t)capacity;
    size_t used=0;
    while(ok&&used<capacity-1) {
        ssize_t n=read(fd,out+used,capacity-1-used);
        if(n<0&&errno==EINTR)continue;
        if(n<0){ok=0;break;}if(!n)break;used+=(size_t)n;
    }
    close(fd);out[used]=0;
    return ok&&(off_t)used==st.st_size?1:-1;
}
static int nx_auth_key_valid(const char *key) {
    if(strlen(key)!=NX_AUTH_KEY_BYTES+1||key[NX_AUTH_KEY_BYTES]!='\n')return 0;
    for(int i=0;i<NX_AUTH_KEY_BYTES;i++)if(!strchr("0123456789abcdef",key[i]))return 0;
    return 1;
}
static int nx_auth_key(const char *directory,char key[NX_AUTH_KEY_BYTES+2],int create) {
    int found=nx_auth_read(directory,"remote-auth.key",key,NX_AUTH_KEY_BYTES+2);
    if(found)return found>0&&nx_auth_key_valid(key);
    if(!create)return 0;
    unsigned char random[NX_AUTH_KEY_BYTES/2];
    int fd=open("/dev/urandom",O_RDONLY|O_CLOEXEC);if(fd<0)return 0;
    size_t used=0;
    while(used<sizeof random){ssize_t n=read(fd,random+used,sizeof random-used);
        if(n<0&&errno==EINTR)continue;
        if(n<=0){close(fd);return 0;}
        used+=(size_t)n;}
    close(fd);
    for(size_t i=0;i<sizeof random;i++){key[2*i]="0123456789abcdef"[random[i]>>4];key[2*i+1]="0123456789abcdef"[random[i]&15];}
    key[NX_AUTH_KEY_BYTES]='\n';key[NX_AUTH_KEY_BYTES+1]=0;
    /* The key is immutable once created. O_EXCL prevents concurrent local
     * enables from replacing a credential another process already loaded. */
    char path[PATH_MAX];if(snprintf(path,sizeof path,"%s/remote-auth.key",directory)>=(int)sizeof path)return 0;
    fd=open(path,O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600);
    if(fd<0)return errno==EEXIST&&nx_auth_key(directory,key,0);
    used=0;int ok=1;
    while(used<NX_AUTH_KEY_BYTES+1){ssize_t n=write(fd,key+used,NX_AUTH_KEY_BYTES+1-used);
        if(n<0&&errno==EINTR)continue;
        if(n<=0){ok=0;break;}
        used+=(size_t)n;}
    if(fsync(fd))ok=0;
    if(close(fd))ok=0;
    int dir=open(directory,O_RDONLY|O_DIRECTORY|O_CLOEXEC);
    if(dir<0)return 0;
    if(fsync(dir))ok=0;
    if(close(dir))ok=0;
    return ok;
}
static void nx_auth_publish_status(int valid) {
    if(!nx_http_auth_in_daemon)return;
    const char *state=getenv("NX_STATE_DIR");
    if(state&&*state) {
        const char *value=valid?(nx_http_auth_enabled?"on\n":"off\n"):"failed\n";
        if(nx_atomic_text_write(state,"remote-auth.status",value,strlen(value))!=1)
            fputs("NX_REMOTE_AUTH status publication failed\n",stderr);
    }
}
static int nx_auth_load(void) {
    char directory[PATH_MAX],mode[8],key[NX_AUTH_KEY_BYTES+2];
    nx_http_auth_enabled=1;nx_http_auth_expected[0]=0;
    int directory_state=nx_auth_directory(directory,sizeof directory,0);
    if(!directory_state){nx_auth_publish_status(0);return 0;}
    int found=directory_state==2?0:nx_auth_read(directory,"remote-auth.enabled",mode,sizeof mode);
    if(!found||(found>0&&!strcmp(mode,"0\n"))){nx_http_auth_enabled=0;nx_auth_publish_status(1);return 1;}
    if(found<0||strcmp(mode,"1\n")||!nx_auth_key(directory,key,0)){nx_auth_publish_status(0);return 0;}
    key[NX_AUTH_KEY_BYTES]=0;char plain[32],encoded[64];
    snprintf(plain,sizeof plain,"nxmod:%s",key);
    mg_base64_encode((const unsigned char *)plain,(int)strlen(plain),encoded);
    snprintf(nx_http_auth_expected,sizeof nx_http_auth_expected,"Basic %s",encoded);
    nx_auth_publish_status(1);return 1;
}
static int nx_auth_configure(const char *operation) {
    char directory[PATH_MAX],key[NX_AUTH_KEY_BYTES+2];
    if(!strcmp(operation,"status")) {
        if(!nx_auth_load()){fputs("连接鉴权配置异常\n",stderr);return 1;}
    }else{
        if(strcmp(operation,"on")&&strcmp(operation,"off"))return 64;
        if(nx_auth_directory(directory,sizeof directory,1)!=1)return 1;
        if(!strcmp(operation,"on")&&!nx_auth_key(directory,key,1))return 1;
        const char *mode=!strcmp(operation,"on")?"1\n":"0\n";
        /* Reject abnormal targets instead of renaming over them: a symlinked
         * or non-regular remote-auth.enabled stays untouched and the operator
         * sees the failure rather than a silent replacement. */
        char path[PATH_MAX];
        if(snprintf(path,sizeof path,"%s/remote-auth.enabled",directory)>=(int)sizeof path)return 1;
        struct stat target;
        if(!lstat(path,&target)&&!S_ISREG(target.st_mode))return 1;
        int saved=nx_atomic_text_write(directory,"remote-auth.enabled",mode,2);
        if(saved==0)return 1;
        /* -1 means the new content is already live but directory durability
         * is unconfirmed: keep going and let the runtime confirmation via
         * remote-auth.status tell the panel, instead of claiming a plain
         * success or a failure. */
        if(saved<0)fputs("连接鉴权：已写入，耐久未确认\n",stderr);
        if(!nx_auth_load())return 1;
    }
    printf("连接鉴权：%s\n",nx_http_auth_enabled?"开":"关（兼容旧版）");
    if(nx_http_auth_enabled) {
        if(nx_auth_directory(directory,sizeof directory,0)!=1||!nx_auth_key(directory,key,0))return 1;
        key[NX_AUTH_KEY_BYTES]=0;
        /* nxui redacts password= fields from its backend logs. */
        printf("账户 nxmod\n连接密钥 password=%s\n",key);
    }
    return 0;
}
static void nx_auth_hup(int number) {(void)number;nx_http_auth_reload=1;}
static int nx_auth_public(const struct http_message *h) {
    return !mg_vcmp(&h->method,"GET")&&(!mg_vcmp(&h->uri,"/api/v1/health")||!mg_vcmp(&h->uri,"/api/v1/camera/info"));
}
static int nx_auth_request(struct mg_connection *c,struct http_message *h) {
    if(!nx_http_auth_enabled||nx_auth_public(h))return 1;
    struct mg_str *given=mg_get_http_header(h,"Authorization");
    size_t length=strlen(nx_http_auth_expected);unsigned difference=0;
    if(given&&length&&given->len==length) {
        for(size_t i=0;i<length;i++)difference|=(unsigned char)given->p[i]^(unsigned char)nx_http_auth_expected[i];
        if(!difference) {
            /* Browsers attach cached Basic credentials automatically. Apply
             * Origin checks to all private routes, including GET controls. */
            struct mg_str *origin=mg_get_http_header(h,"Origin"),*host=mg_get_http_header(h,"Host");
            if(!origin||(host&&origin->len==host->len+7&&!memcmp(origin->p,"http://",7)&&!memcmp(origin->p+7,host->p,host->len)))return 1;
            const char *body="{\"error\":\"origin mismatch\"}";
            mg_send_head(c,403,strlen(body),"Content-Type: application/json\r\nCache-Control: no-store");mg_send(c,body,strlen(body));return 0;
        }
    }
    const char *body="{\"error\":\"camera authentication required\",\"code\":\"auth_required\"}";
    mg_send_head(c,401,strlen(body),"Content-Type: application/json\r\nCache-Control: no-store\r\nWWW-Authenticate: Basic realm=\"NX300M\"");
    mg_send(c,body,strlen(body));return 0;
}
#endif
