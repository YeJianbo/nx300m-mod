/* Optional userspace GPIO register substitute for stock UI startup research.
 * gpio_init executes stock code, then reapplies an explicitly selected external
 * hotshoe input: an input pin is not driven low by an output-latch reset.
 * Input levels are synthetic; this does not model camera electronics or ISP.
 */
typedef unsigned int word;
extern void *dlsym(void *, const char *);
extern char *getenv(const char *);
extern void abort(void);
typedef struct { int fd; unsigned short enabled, pending; } irq_state;
typedef struct {
    int fd;
    word group;
    volatile word *registers;
    word reserved;
    irq_state *irq;
} gpio_handle;
typedef char gpio_handle_must_be_20_bytes[sizeof(gpio_handle) == 20 ? 1 : -1];

static gpio_handle handles[28];
static irq_state interrupts[28];
static volatile word banks[28][1024];
static word initialized[28];

/* Device-level models use this marker before calling stock GPIO operations. */
int nx300m_gpio_preview_active(void) { return 1; }

static void announce(void) {
    static const char text[] = "[GPIO SUBSTITUTE] synthetic register bank; input levels are not hardware evidence\n";
    register int r0 __asm__("r0") = 2;
    register const char *r1 __asm__("r1") = text;
    register int r2 __asm__("r2") = sizeof(text) - 1;
    register int r7 __asm__("r7") = 4;
    __asm__ volatile("svc 0" : "+r"(r0) : "r"(r1), "r"(r2), "r"(r7) : "memory");
}

gpio_handle *gpio_open(word group) {
    if (group >= 28) return (gpio_handle *)0;
    if (!initialized[group]) {
        handles[group].fd = -1;
        handles[group].group = group;
        handles[group].registers = banks[group];
        handles[group].irq = &interrupts[group];
        interrupts[group].fd = -1;
        banks[group][0x3fc >> 2] = 0xffffffffU;
        initialized[group] = 1;
        announce();
    }
    return &handles[group];
}

int gpio_close(gpio_handle *handle) {
    (void)handle;
    return 0;
}

void gpio_init(gpio_handle *handle, word pin) {
    void (*original)(gpio_handle *, word) = dlsym((void *)-1, "gpio_init");
    if (!original) abort();
    original(handle, pin);
    const char *absent = getenv("NX300M_HOTSHOE_ABSENT");
    if (absent && absent[0] == '1' && !absent[1] && handle == handles+7 && pin == 0) {
        /* NX300M GPIO_EXT_STR_DET = GPIO7(0). Stock libstrobe treats 0
         * as attached. Its GPIO input setup clears DATA's output latch;
         * preserve the separately supplied high input of an empty shoe. */
        __sync_fetch_and_or(banks[7]+(0x3fc >> 2), 1U);
    }
}
