/* Verify the reference-image optimization against the former exact sum. */
#include <stdio.h>
#include "../mod/metol/lut_reference_interp.h"
static uint32_t state=7;
static unsigned next(void) {state=1664525*state+1013904223;return state;}
static unsigned previous(const uint16_t *t,unsigned r,unsigned g,unsigned b) {
    uint64_t sum=0;
    for(unsigned a=0;a<2;a++)for(unsigned c=0;c<2;c++)for(unsigned d=0;d<2;d++)
        sum+=(uint64_t)t[a*81+c*9+d]*(a?r:255-r)*(c?g:255-g)*(d?b:255-b);
    return (unsigned)((sum+8290687)/16581375);
}
int main(void) {
    uint16_t table[729];
    unsigned edge[]={0,1,127,254,255};
    for(unsigned i=0;i<20000;i++) {
        if(!(i%125))for(unsigned j=0;j<729;j++)table[j]=next()>>22;
        unsigned index=(next()%8)*81+(next()%8)*9+next()%8;
        unsigned r=i<1000?edge[i%5]:next()%256;
        unsigned g=i<1000?edge[(i/5)%5]:next()%256;
        unsigned b=i<1000?edge[(i/25)%5]:next()%256;
        unsigned a=previous(table+index,r,g,b),n=nx_ref_interpolate(table+index,r,g,b);
        if(a!=n){fprintf(stderr,"Mismatch %u: %u != %u\n",i,a,n);return 1;}
    }
    puts("PASS: 20000 reference interpolation cases are bit-exact");return 0;
}
