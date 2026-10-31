/* Fn hold/release recognition; no input is consumed or synthesized. */
#ifndef NX_PANEL_SHORTCUT_STATE_H
#define NX_PANEL_SHORTCUT_STATE_H
struct nx_panel_shortcut_state {
    long long sampled_at, pressed_at;
    int sampled, armed, pressed;
};
static int nx_panel_shortcut_update(struct nx_panel_shortcut_state *state,
        long long now, int fn_down, int shutter_down, int panel_running) {
    /* A blocked loop or suspend must not turn a stale held key into an action. */
    if (!state->sampled || now < state->sampled_at || now-state->sampled_at > 500) {
        state->sampled=1;state->armed=0;state->pressed=0;
    }
    state->sampled_at=now;
    if (shutter_down || panel_running) { state->armed=0;state->pressed=0;return 0; }
    if (!fn_down) {
        int launch=state->armed && state->pressed && now-state->pressed_at>=1200;
        state->armed=1;state->pressed=0;
        return launch;
    }
    if (state->armed && !state->pressed) { state->pressed=1;state->pressed_at=now; }
    return 0;
}
#endif
