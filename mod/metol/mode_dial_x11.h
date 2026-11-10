/* NX300M stock Xmodmap assigns only mode-dial positions to keycodes 67..76.
 * Keep the original symbols on the X server: their lifetime then matches the
 * mapping itself, including a camera snapshot, without a polling daemon.
 */
#ifndef NX_MODE_DIAL_X11_H
#define NX_MODE_DIAL_X11_H
#include <X11/Xlib.h>
#include <X11/Xatom.h>
#include <X11/keysym.h>
#include <stdlib.h>
#include <string.h>

#define NX_MODE_DIAL_FIRST 67
#define NX_MODE_DIAL_COUNT 10
#define NX_MODE_DIAL_COLUMNS 32
#define NX_MODE_DIAL_MAGIC 0x4e584431UL
#define NX_MODE_DIAL_PROPERTY "_NX300M_MODE_DIAL_ORIGINAL_V1"

struct nx_mode_dial_map { int columns; KeySym *keys; };

static int nx_mode_dial_get(Display *d,struct nx_mode_dial_map *m) {
    int first,last;memset(m,0,sizeof *m);XDisplayKeycodes(d,&first,&last);
    if(first>NX_MODE_DIAL_FIRST||last<NX_MODE_DIAL_FIRST+NX_MODE_DIAL_COUNT-1)return 0;
    m->keys=XGetKeyboardMapping(d,NX_MODE_DIAL_FIRST,NX_MODE_DIAL_COUNT,&m->columns);
    return m->keys&&m->columns>0&&m->columns<=NX_MODE_DIAL_COLUMNS;
}
static int nx_mode_dial_stock(const struct nx_mode_dial_map *m) {
    for(int i=0;i<NX_MODE_DIAL_COUNT;i++)if(m->keys[i*m->columns]!=(KeySym)(XK_F1+i))return 0;
    return 1;
}
static int nx_mode_dial_empty(const struct nx_mode_dial_map *m) {
    for(int i=0;i<NX_MODE_DIAL_COUNT*m->columns;i++)if(m->keys[i]!=NoSymbol)return 0;
    return 1;
}
static int nx_mode_dial_equal(const struct nx_mode_dial_map *a,const struct nx_mode_dial_map *b) {
    int columns=a->columns>b->columns?a->columns:b->columns;
    for(int row=0;row<NX_MODE_DIAL_COUNT;row++)for(int col=0;col<columns;col++) {
        KeySym x=col<a->columns?a->keys[row*a->columns+col]:NoSymbol;
        KeySym y=col<b->columns?b->keys[row*b->columns+col]:NoSymbol;
        if(x!=y)return 0;
    }
    return 1;
}
static int nx_mode_dial_original(Display *d,Atom atom,struct nx_mode_dial_map *m) {
    Atom type;int format;unsigned long count,remaining;unsigned char *data=NULL;
    memset(m,0,sizeof *m);
    if(atom==None)return 0;
    int rc=XGetWindowProperty(d,DefaultRootWindow(d),atom,0,2+NX_MODE_DIAL_COUNT*NX_MODE_DIAL_COLUMNS,
                             False,XA_CARDINAL,&type,&format,&count,&remaining,&data);
    unsigned long *words=(unsigned long *)data;
    int ok=rc==Success&&type==XA_CARDINAL&&format==32&&remaining==0&&count>=2&&
        words&&words[0]==NX_MODE_DIAL_MAGIC&&words[1]>0&&words[1]<=NX_MODE_DIAL_COLUMNS&&
        count==2+NX_MODE_DIAL_COUNT*words[1];
    if(ok) {
        m->columns=(int)words[1];m->keys=malloc(NX_MODE_DIAL_COUNT*m->columns*sizeof(KeySym));
        if(!m->keys)ok=0;
        else {for(int i=0;i<NX_MODE_DIAL_COUNT*m->columns;i++)m->keys[i]=words[i+2];ok=nx_mode_dial_stock(m);}
    }
    if(data)XFree(data);
    if(!ok){free(m->keys);memset(m,0,sizeof *m);}return ok;
}
/* -1 means an unknown mapping or missing valid original; never report a
 * successful lock just because a preference or an empty keymap exists. */
static int nx_mode_dial_status(Display *d) {
    struct nx_mode_dial_map current,original;int result=-1;
    if(!nx_mode_dial_get(d,&current)){if(current.keys)XFree(current.keys);return -1;}
    if(nx_mode_dial_stock(&current))result=0;
    else if(nx_mode_dial_empty(&current)) {
        Atom atom=XInternAtom(d,NX_MODE_DIAL_PROPERTY,True);
        if(nx_mode_dial_original(d,atom,&original)){result=1;free(original.keys);}
    }
    XFree(current.keys);return result;
}
#endif
