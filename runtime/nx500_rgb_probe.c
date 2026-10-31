#include <stdio.h>
#include "nx500_rgb_math.h"
extern int nx500_original_rgb_coefficient(int,unsigned int);
extern void nx500_original_ccm(int16_t *,const int16_t *,const int16_t *,int);
extern const int16_t nx500_original_rgb_final[9];

int main(void) {
    unsigned int codes[3],count=0;
    int recipe;
    int16_t input[9],output[9],inplace[9];
    for(int i=0;i<9;++i) if(nx500_original_rgb_final[i]!=nx500_rgb_final_q7[i]) return 10;
    while(scanf("%d %u %u %u",&recipe,codes,codes+1,codes+2)==4) {
        if(recipe>=0) {
            unsigned int values[3]={(unsigned int)recipe,200u-(unsigned int)recipe,100},encoded[3];
            if(nx500_rgb_recipe_codes(encoded,values)) return 8;
            for(int i=0;i<3;++i) if(encoded[i]!=codes[i]) return 9;
        }
        for(int i=0;i<9;++i) {
            int value;
            if(scanf("%d",&value)!=1 || value < -32768 || value > 32767) return 2;
            input[i]=inplace[i]=(int16_t)value;
        }
        if(nx500_rgb_columns(output,input,codes) || nx500_rgb_columns(inplace,inplace,codes)) return 3;
        for(int i=0;i<9;++i) {
            int original=nx500_original_rgb_coefficient(input[i],codes[i%3]);
            if(original!=output[i] || original!=inplace[i]) {
                fprintf(stderr,"RGB mismatch case=%u coefficient=%d code=%u original=%d port=%d inplace=%d\n",
                    count,input[i],codes[i%3],original,output[i],inplace[i]);
                return 4;
            }
        }
        int16_t final[9],reference[9];
        nx500_original_ccm(reference,output,nx500_original_rgb_final,128);
        nx500_rgb_final_matrix(final,output);
        nx500_rgb_final_matrix(inplace,inplace);
        for(int i=0;i<9;++i) if(reference[i]!=final[i] || reference[i]!=inplace[i]) {
            fprintf(stderr,"Final CCM mismatch case=%u index=%d original=%d port=%d\n",count,i,reference[i],final[i]);
            return 11;
        }
        ++count;
    }
    unsigned int invalid[3]={128,257,128};
    for(int i=0;i<9;++i) output[i]=input[i]=123;
    if(nx500_rgb_columns(output,input,invalid)!=-1) return 5;
    for(int i=0;i<9;++i) if(output[i]!=123) return 6;
    printf("%u\n",count);
    return ferror(stdin)?7:0;
}
