/* Diagnostic input files are complete planes, not partial overlays. Reject
 * malformed files before any compression or shared output commit. */
static int nx_diag_read_sample(const char *directory,unsigned int width,
        unsigned int height,const char *role,unsigned char *destination,
        unsigned int expected,const char **stage) {
    char path[512];
    *stage="sample-path";
    int count=snprintf(path,sizeof(path),"%s/sample_%ux%u_%s.bin",
                       directory,width,height,role);
    if(count<0 || (size_t)count>=sizeof(path)) return -36;
    *stage="sample-open";
    FILE *input=fopen(path,"rb");
    if(!input) return -2;
    int error=-5;
    *stage="sample-length";
    if(fseek(input,0,SEEK_END)) goto done;
    long bytes=ftell(input);
    if(bytes<0) goto done;
    if((unsigned long)bytes!=expected) {error=-22;goto done;}
    if(fseek(input,0,SEEK_SET)) goto done;
    *stage="sample-read";
    if(fread(destination,1,expected,input)!=expected || ferror(input)) goto done;
    /* Detect growth after the length check as well as read failures at EOF. */
    *stage="sample-eof";
    if(fgetc(input)!=EOF || ferror(input)) goto done;
    error=0;
done:
    if(fclose(input) && !error) {error=-5;*stage="sample-close";}
    return error;
}
