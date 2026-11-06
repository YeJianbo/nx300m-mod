#ifndef NXUI_PREVIEW_STATE_H
#define NXUI_PREVIEW_STATE_H

enum { NX_PREVIEW_OFF, NX_PREVIEW_ON, NX_PREVIEW_WAIT, NX_PREVIEW_BUSY, NX_PREVIEW_FAILED };

/* Only RAM acknowledgements mean enabled. A saved preference describes intent,
 * and the boot worker's lock distinguishes active restoration from a dead job. */
static int nx_preview_state(const char *data_root) {
    char path[1024], service_state[1024], text[64]="";
    const char *state=getenv("NX_STATE_DIR");
    const char *base=getenv("NX300M_MFW_RAM_BASE");
    /* lut-panel.sh assigns NX_STATE_DIR to its private UI directory. Control
     * actions and boot share the RAM service state under the staging base. */
    if(base&&*base){
        if(snprintf(service_state,sizeof service_state,"%s/state",base)>=(int)sizeof service_state)return NX_PREVIEW_FAILED;
        state=service_state;
    }
    if(!state||!*state)state="/tmp/nx300m-mfw/state";
    if(snprintf(path,sizeof path,"%s/lut-boot.lock",state)>=(int)sizeof path)return NX_PREVIEW_FAILED;
    int fd=open(path,O_RDONLY|O_CLOEXEC|O_NOFOLLOW);
    if(fd>=0){
        int rc=flock(fd,LOCK_SH|LOCK_NB),err=errno;
        close(fd);
        if(rc<0&&(err==EWOULDBLOCK||err==EAGAIN))return NX_PREVIEW_BUSY;
    }
    FILE *f=fopen("/var/run/nx300m-preview-backend","r");
    if(f){if(!fgets(text,sizeof text,f))text[0]=0;fclose(f);}
    if(!strcmp(text,"smartfilter\n")||!strcmp(text,"smartfilter"))return NX_PREVIEW_ON;
    if(snprintf(path,sizeof path,"%s/lut-boot.status",state)>=(int)sizeof path)return NX_PREVIEW_FAILED;
    text[0]=0;f=fopen(path,"r");
    if(f){if(!fgets(text,sizeof text,f))text[0]=0;fclose(f);}
    int finished=*text!=0;
    if(snprintf(path,sizeof path,"%s/.state/lut-preview",data_root)>=(int)sizeof path)return NX_PREVIEW_FAILED;
    text[0]=0;f=fopen(path,"r");
    if(f){if(!fgets(text,sizeof text,f))text[0]=0;fclose(f);}
    if(strcmp(text,"1\n")&&strcmp(text,"1"))return NX_PREVIEW_OFF;
    return finished?NX_PREVIEW_FAILED:NX_PREVIEW_WAIT;
}

static const char *nx_preview_action(int displayed) {
    return displayed==NX_PREVIEW_ON?"preview-stop":"preview-s2-enable";
}

static const char *nx_preview_label(int state) {
    switch(state){
        case NX_PREVIEW_ON:return "预览 开";
        case NX_PREVIEW_WAIT:return "预览待恢复";
        case NX_PREVIEW_BUSY:return "预览恢复中";
        case NX_PREVIEW_FAILED:return "预览未恢复";
        default:return "预览 关";
    }
}
#endif
