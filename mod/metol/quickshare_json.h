/* Token navigation and JSON string decoding for the local task API.
 * jsmn is MIT, pinned at 25647e692c7906b96ffd2b05ca54c097948e879c. */
#define JSMN_STATIC
#include "vendor/jsmn.h"
struct json { const char *s;jsmntok_t t[1024];int n; };
static int parse(struct json *j,const char *s,size_t n) {
    jsmn_parser p;jsmn_init(&p);j->s=s;j->n=jsmn_parse(&p,s,n,j->t,1024);return j->n>0;
}
static int eq(struct json *j,int i,const char *s) {
    return i>=0 && i<j->n && j->t[i].end-j->t[i].start==(int)strlen(s) && !memcmp(j->s+j->t[i].start,s,strlen(s));
}
static int next(struct json *j,int i) {
    int end=j->t[i].end;while (++i<j->n && j->t[i].start<end) {}return i;
}
static int field(struct json *j,int object,const char *key) {
    if (object<0 || object>=j->n || j->t[object].type!=JSMN_OBJECT) return -1;
    for (int i=object+1;i<j->n && j->t[i].start<j->t[object].end;) {
        int value=i+1;if (value>=j->n || j->t[value].start>=j->t[object].end) return -1;
        if (eq(j,i,key)) return value;i=next(j,value);
    }return -1;
}
static unsigned hex4(const char *p) {
    unsigned n=0;for (int i=0;i<4;i++) { unsigned c=(unsigned char)p[i];n=n*16+(c<='9'?c-'0':(c|32)-'a'+10); }return n;
}
static int string(struct json *j,int i,char *out,size_t cap) {
    size_t used=0;if (!cap) return 0;out[0]=0;
    if (i<0 || i>=j->n || j->t[i].type!=JSMN_STRING) return 0;
    const char *p=j->s+j->t[i].start,*end=j->s+j->t[i].end;
    while (p<end) {
        unsigned c=(unsigned char)*p++;char b[4];int n=1;b[0]=(char)c;
        if (c=='\\') {
            if (p>=end) return 0;c=(unsigned char)*p++;
            if (c=='u') {
                if (end-p<4) return 0;c=hex4(p);p+=4;
                if (c>=0xd800 && c<=0xdbff) {
                    if (end-p<6 || p[0]!='\\' || p[1]!='u') return 0;
                    unsigned low=hex4(p+2);if (low<0xdc00 || low>0xdfff) return 0;
                    c=0x10000+((c-0xd800)<<10)+low-0xdc00;p+=6;
                } else if (c>=0xdc00 && c<=0xdfff) return 0;
                if (c<128) b[0]=(char)c;
                else if (c<2048) {n=2;b[0]=0xc0|(c>>6);b[1]=0x80|(c&63);}
                else if (c<65536) {n=3;b[0]=0xe0|(c>>12);b[1]=0x80|((c>>6)&63);b[2]=0x80|(c&63);}
                else {n=4;b[0]=0xf0|(c>>18);b[1]=0x80|((c>>12)&63);b[2]=0x80|((c>>6)&63);b[3]=0x80|(c&63);}
            } else {const char *codes="\"\\/bfnrt",*values="\"\\/\b\f\n\r\t",*found=strchr(codes,c);if (!found) return 0;b[0]=values[found-codes];}
        }
        if (!b[0] || used+n>=cap) return 0;
        memcpy(out+used,b,n);used+=n;
    }out[used]=0;return 1;
}
static unsigned long long number(struct json *j,int i) {
    if (i<0 || i>=j->n) return 0;char b[32];int n=j->t[i].end-j->t[i].start;
    if (n<=0 || n>20) return 0;memcpy(b,j->s+j->t[i].start,n);b[n]=0;return strtoull(b,NULL,10);
}
