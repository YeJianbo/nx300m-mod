/* Isolated timer scheduler only; matrix setters remain genuine stock libudd. */
static int (*callback)(void *);
static void *callback_data;

void *ecore_timer_add(double interval, int (*function)(void *), void *data) {
    (void)interval;
    if (!function || callback) return (void *)0;
    callback = function;
    callback_data = data;
    return &callback;
}

void ecore_main_loop_begin(void) { }

int color_probe_tick(void) {
    return callback ? callback(callback_data) : 0;
}
