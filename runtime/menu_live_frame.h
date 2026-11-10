#ifndef NX_MENU_LIVE_FRAME_H
#define NX_MENU_LIVE_FRAME_H

#define NX_MENU_FRAME_PATH "/var/run/nx300m-menu-frame"
#define NX_MENU_FRAME_MAGIC 0x31464d4eU
#define NX_MENU_FRAME_W 192U
#define NX_MENU_FRAME_H 112U
#define NX_MENU_FRAME_PIXELS (NX_MENU_FRAME_W*NX_MENU_FRAME_H)
#define NX_MENU_FRAME_INTERVAL 40U
/* The UI leases this RAM mailbox for one second at a time. The camera never
 * waits for the reader; an odd sequence means a thumbnail is being replaced.
 * Pixels are the pre-LUT Y/CbCr420 input, not a screenshot of the menu. */
struct nx_menu_frame {
    unsigned magic, size;
    volatile unsigned owner, heartbeat, sequence;
    unsigned timestamp, width, height;
    unsigned char y[NX_MENU_FRAME_PIXELS];
    unsigned char uv[NX_MENU_FRAME_PIXELS/2];
};
#endif
