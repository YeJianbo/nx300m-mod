/* One-shot mode-dial control. No camera memory, AE API, firmware or key grabs. */
#define _POSIX_C_SOURCE 200809L
#include "mode_dial_x11.h"
#include <stdio.h>
#include <unistd.h>
#include <signal.h>
#include <fcntl.h>
#include <sys/file.h>

static int xerror;
static int on_error(Display *d,XErrorEvent *e){(void)d;(void)e;xerror=1;return 0;}
static void deadline(int sig){(void)sig;_exit(124);}

int main(int argc,char **argv) {
    int requested=-1;
    if(argc!=2)return 64;
    if(!strcmp(argv[1],"on"))requested=1;
    else if(!strcmp(argv[1],"off"))requested=0;
    else if(strcmp(argv[1],"status"))return 64;
    signal(SIGALRM,deadline);alarm(3);
    int lock=open("/var/run/nx300m-mode-dial-x11.lock",O_RDONLY|O_CREAT|O_NOFOLLOW,0600);
    if(lock<0||flock(lock,LOCK_EX|LOCK_NB))return 75;
    Display *d=XOpenDisplay(NULL);if(!d)return 69;
    XSetErrorHandler(on_error);
    int before=nx_mode_dial_status(d),result=before,rc=0;
    struct nx_mode_dial_map current={0},original={0};
    if(before<0){rc=70;goto done;}
    if(requested<0||requested==before)goto done;
    Atom atom=XInternAtom(d,NX_MODE_DIAL_PROPERTY,False);
    if(!nx_mode_dial_get(d,&current)){rc=70;goto done;}
    if(requested) {
        unsigned long words[2+NX_MODE_DIAL_COUNT*NX_MODE_DIAL_COLUMNS];
        words[0]=NX_MODE_DIAL_MAGIC;words[1]=(unsigned)current.columns;
        for(int i=0;i<NX_MODE_DIAL_COUNT*current.columns;i++)words[i+2]=current.keys[i];
        XChangeProperty(d,DefaultRootWindow(d),atom,XA_CARDINAL,32,PropModeReplace,
                        (unsigned char *)words,2+NX_MODE_DIAL_COUNT*current.columns);
        XSync(d,False);if(xerror){rc=70;goto done;}
        KeySym empty[NX_MODE_DIAL_COUNT*NX_MODE_DIAL_COLUMNS]={0};
        XChangeKeyboardMapping(d,NX_MODE_DIAL_FIRST,current.columns,empty,NX_MODE_DIAL_COUNT);
    } else {
        if(!nx_mode_dial_original(d,atom,&original)){rc=70;goto done;}
        XChangeKeyboardMapping(d,NX_MODE_DIAL_FIRST,original.columns,original.keys,NX_MODE_DIAL_COUNT);
    }
    XSync(d,False);result=nx_mode_dial_status(d);
    if(xerror||result!=requested) {
        /* Restore the exact pre-request map after a rejected/partial update.
         * Retain the saved property so a failed rollback remains recoverable. */
        XChangeKeyboardMapping(d,NX_MODE_DIAL_FIRST,current.columns,current.keys,NX_MODE_DIAL_COUNT);
        XSync(d,False);rc=70;goto done;
    }
    if(!requested) {
        struct nx_mode_dial_map verified={0};
        if(!nx_mode_dial_get(d,&verified)||!nx_mode_dial_equal(&verified,&original))rc=70;
        if(verified.keys)XFree(verified.keys);
        if(!rc){XDeleteProperty(d,DefaultRootWindow(d),atom);XSync(d,False);if(xerror)rc=70;}
    }
done:
    if(current.keys)XFree(current.keys);
    free(original.keys);
    if(!rc&&!xerror&&result>=0)printf("%d\n",result);
    else {fprintf(stderr,"Mode dial mapping not acknowledged (before=%d after=%d Xerror=%d)\n",before,result,xerror);if(!rc)rc=70;}
    /* Closing an X client does not remove a server property or keymap. Avoid
     * changing the server reset policy: the camera already owns its client. */
    XCloseDisplay(d);close(lock);alarm(0);return rc;
}
