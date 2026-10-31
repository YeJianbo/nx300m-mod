#include <assert.h>
#include <stdio.h>
#include "../mod/metol/remote/panel_shortcut_state.h"

static int press(struct nx_panel_shortcut_state *state,long long start,int samples) {
    int launches=0;
    for (int i=0;i<samples;i++) launches+=nx_panel_shortcut_update(state,start+i*100,1,0,0);
    return launches;
}
int main(void) {
    struct nx_panel_shortcut_state state={0};
    assert(!nx_panel_shortcut_update(&state,0,0,0,0));
    assert(!press(&state,100,5));
    assert(!nx_panel_shortcut_update(&state,600,0,0,0)); /* ordinary Fn */
    assert(!press(&state,700,13));
    assert(nx_panel_shortcut_update(&state,2000,0,0,0)==1);
    assert(!nx_panel_shortcut_update(&state,2100,0,0,0)); /* no repeated launch */
    state=(struct nx_panel_shortcut_state){0};
    assert(!press(&state,0,20));
    assert(!nx_panel_shortcut_update(&state,2000,0,0,0)); /* held at startup */
    assert(!press(&state,2100,8));
    assert(!nx_panel_shortcut_update(&state,2900,1,1,0));
    assert(!press(&state,3000,15));
    assert(!nx_panel_shortcut_update(&state,4500,0,0,0)); /* shutter cancels */
    assert(!press(&state,4600,13));
    assert(!nx_panel_shortcut_update(&state,6000,1,0,1));
    assert(!nx_panel_shortcut_update(&state,6100,0,0,0)); /* panel already open */
    assert(!press(&state,6200,13));
    assert(!nx_panel_shortcut_update(&state,10000,0,0,0)); /* suspend/gap */
    assert(!press(&state,10100,13));
    assert(!nx_panel_shortcut_update(&state,500,0,0,0)); /* clock reset */
    puts("Fn hold/release: short press, launch once, startup hold, shutter, open panel, gap and clock reset passed");
    return 0;
}
