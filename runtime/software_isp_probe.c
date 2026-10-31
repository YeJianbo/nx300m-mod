/* Native-host diagnostic of the same explicit software ISP equations. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define NX_ISP_CORE_ONLY
#include "software_isp.h"
int main(int argc,char **argv) {
    if(argc!=7) return 2;
    struct nx_isp_source source;unsigned char *raw=NULL;
    int error=nx_isp_load(argv[1],argv[2],&source,&raw);if(error) return 3;
    if(nx_isp_denoise(raw,&source)) return 11;
    FILE *params=fopen(argv[3],"rb");if(!params) return 4;
    if(fread(&nx_isp,1,sizeof(nx_isp),params)!=sizeof(nx_isp) || fgetc(params)!=EOF) return 5;
    fclose(params);
    unsigned int width=(unsigned int)strtoul(argv[5],NULL,10),height=(unsigned int)strtoul(argv[6],NULL,10);
    if(!width || !height || width>8192 || height>8192 || ((width|height)&1)) return 6;
    if(nx_isp_reduce(&raw,&source,width,height)) return 12;
    size_t pixels=width*height;unsigned char *planes=malloc(pixels*2);
    if(!planes) return 7;
    error=nx_isp_render(raw,&source,&nx_isp,width,height,planes,planes+pixels);
    if(error) return 8;
    FILE *out=fopen(argv[4],"wb");if(!out) return 9;
    if(fwrite(planes,1,pixels*2,out)!=pixels*2 || fclose(out)) return 10;
    free(planes);free(raw);return 0;
}
