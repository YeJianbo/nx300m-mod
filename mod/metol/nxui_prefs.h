/* Small card preferences; atomic publication, never a per-frame path. */
static const char *nx_ui_data_root(void) {
    const char *data=getenv("NX300M_MOD_SOURCE_ROOT");return data&&*data?data:root;
}
static int nx_ui_pref_write(const char *name,const char *text) {
    char directory[NXUI_PATH],file[NXUI_PATH],temporary[NXUI_PATH];struct stat64 st;
    const char *data=nx_ui_data_root();
    if(!nx_mod_root_valid(data)||strchr(name,'/')||strstr(name,".."))return 0;
    snprintf(directory,sizeof directory,"%s/.state",data);
    if((mkdir(directory,0700)&&errno!=EEXIST)||__lxstat64(3,directory,&st)||!S_ISDIR(st.st_mode))return 0;
    if(snprintf(file,sizeof file,"%s/%s",directory,name)>=(int)sizeof file||
       snprintf(temporary,sizeof temporary,"%s/%s.ui-next",directory,name)>=(int)sizeof temporary)return 0;
    int fd=open(temporary,O_WRONLY|O_CREAT|O_TRUNC|O_NOFOLLOW,0600);if(fd<0)return 0;
    size_t length=strlen(text);int ok=write(fd,text,length)==(ssize_t)length;
    if(fsync(fd))ok=0;
    if(close(fd))ok=0;
    if(!ok||rename(temporary,file)){unlink(temporary);return 0;}
    fd=open(directory,O_RDONLY|O_DIRECTORY);if(fd<0)return 0;
    ok=!fsync(fd);if(close(fd))ok=0;return ok;
}
