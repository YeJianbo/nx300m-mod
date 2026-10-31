/* Exercise the Allegro ABI with original NX300M GPIO/board libraries.
 * The input FIFO is explicitly synthetic; no sensor or ISP output is used.
 */
typedef unsigned int u32;
extern int open(const char *, int, ...), close(int), ioctl(int, u32, ...);
extern int write(int, const void *, unsigned int), usleep(unsigned int);
extern int printf(const char *, ...), pthread_create(unsigned long *, const void *, void *(*)(void *), void *);
extern char *getenv(const char *);
extern int pthread_join(unsigned long, void **), *__errno_location(void);
struct instant { long seconds, nanoseconds; };
extern int clock_gettime(int, struct instant *);
extern int get_gpio_number(int);
extern void *gpio_open(u32);
extern unsigned int gpio_data_get(void *, u32);
extern void gpio_data_set(void *, u32, int);
extern void gpio_init(void *, u32), gpio_port_set(void *, u32, int);
struct param { int level, count; u32 time; int job; };
struct timer { int channel; u32 time; int job; };
static volatile int completed;
static int irq_result;
static long long now(void) {
    struct instant value;
    if (clock_gettime(1, &value)) return -1;
    return (long long)value.seconds*1000000000LL+value.nanoseconds;
}
static int gpio(int name) {
    int pin=get_gpio_number(name);
    if (pin < 0 || pin >= 224) return -1;
    return gpio_data_get(gpio_open((u32)pin/8), (u32)pin%8);
}
static void *wait_irq(void *arg) {
    irq_result=ioctl(*(int *)arg, 0x7305U);
    __sync_synchronize(); completed=1;
    return (void *)0;
}
static void set_gpio(int name, int value) {
    int pin = get_gpio_number(name);
    gpio_data_set(gpio_open((u32)pin/8), (u32)pin%8, value);
}
static int motion_probe(int fd) {
    printf("MOTION_BOARD power=%d mot1=%d mot2=%d pi1=%d pi2=%d\n",
        get_gpio_number(31),get_gpio_number(36),get_gpio_number(37),get_gpio_number(32),get_gpio_number(33));
    set_gpio(31,1);set_gpio(36,1);set_gpio(37,1);
    struct param p={1,2,0,1};
    if (ioctl(fd,0x40107303U,&p) || ioctl(fd,0x40107304U,&p)) return 21;
    unsigned long thread;
    completed=0;
    if (pthread_create(&thread,(void *)0,wait_irq,&fd)) return 22;
    usleep(60000);
    if (completed || gpio(33)!=0 || gpio(32)!=1) return 23;
    printf("MOTION_IRQ armed_with_brake_no_edges=1\n");
    long long before=now();
    set_gpio(36,0);set_gpio(37,1);
    for (int i=0;i<100 && !completed;i++) usleep(2000);
    if (!completed || pthread_join(thread,(void *)0) || irq_result) return 24;
    long long elapsed=now()-before;
    if (elapsed<30000000LL || gpio(36)!=1 || gpio(37)!=1 || gpio(33)!=0) return 25;
    printf("MOTION_IRQ gpio_driven_edges=2 wait_woken=1 stock_brake_job=1 elapsed_ns=%lld\n",elapsed);
    int pi1=gpio(32), pi2=gpio(33);
    usleep(70000);
    if (gpio(32)!=pi1 || gpio(33)!=pi2) return 26;
    printf("MOTION_BRAKE positions_unchanged=1\n");
    /* No IRQ is armed: rotation must continue independently of driver demand. */
    set_gpio(36,0);set_gpio(37,1);
    usleep(50000);
    set_gpio(36,1);set_gpio(37,1);
    usleep(3000);
    if (gpio(32)==pi1 && gpio(33)==pi2) return 27;
    printf("MOTION_WITHOUT_IRQ independent_position_change=1 pi1=%d pi2=%d\n",gpio(32),gpio(33));
    return 0;
}
int main(void) {
    if (getenv("NX300M_HOTSHOE_ABSENT")) {
        void *bank = gpio_open(7);
        gpio_data_set(bank, 0, 0);
        gpio_init(bank, 0);
        gpio_port_set(bank, 0, 0);
        gpio_data_set(bank, 1, 0);
        if (gpio_data_get(bank, 0) != 1 || gpio_data_get(bank, 1) != 0) return 28;
        printf("GPIO_INPUT empty_hotshoe_after_stock_init=1 adjacent_output_preserved=1\n");
    }
    int fd=open("/dev/allegro", 04002);
    if (fd < 0 || ioctl(fd,0x730eU)) return 2;
    printf("ALLEGRO_INIT ok=1 pi2_pin=%d mg1_pin=%d mg2_pin=%d\n",get_gpio_number(33),get_gpio_number(34),get_gpio_number(35));
    if (getenv("NX300M_SHUTTER_MOTION")) return motion_probe(fd);
    struct param p={0,0,0,0};
    if (ioctl(fd,0x80107301U,&p) || p.level!=1 || gpio(33)!=1) return 3;
    struct timer timer={0,60000,2};
    long long before=now();
    if (ioctl(fd,0x400c7306U,&timer) || ioctl(fd,0x400c7308U,&timer)) return 4;
    long long elapsed=now()-before;
    if (elapsed < 55000000LL || gpio(34)!=1) return 5;
    printf("ALLEGRO_TIMER elapsed_ns=%lld mg1_after_on=%d\n",elapsed,gpio(34));
    int mg1=get_gpio_number(34);
    gpio_data_set(gpio_open((u32)mg1/8), (u32)mg1%8, 0);
    timer.time=0;timer.job=2;
    if (ioctl(fd,0x400c7306U,&timer)) return 6;
    usleep(10000);
    if (gpio(34)!=1 || ioctl(fd,0x400c7308U,&timer)) return 6;
    printf("ALLEGRO_TIMER zero_duration_fired=1 action_before_wait=1 mg1_after_on=%d\n",gpio(34));
    p.level=0;p.count=2;p.time=0;p.job=0;
    if (ioctl(fd,0x40107303U,&p)) return 7;
    p.level=1;
    if (ioctl(fd,0x40107304U,&p)) return 8;
    unsigned long thread;
    if (pthread_create(&thread,(void *)0,wait_irq,&fd)) return 9;
    usleep(30000);
    if (completed) return 10;
    printf("ALLEGRO_IRQ before_explicit_input_waiting=1\n");
    int input=open("/var/run/nx300m-allegro-pi2",04001);
    if (input < 0 || write(input,"0",1)!=1) return 11;
    usleep(10000);
    if (completed || gpio(33)!=0) return 12;
    printf("ALLEGRO_IRQ first_edge_still_waiting=1 pi2=%d\n",gpio(33));
    if (write(input,"1",1)!=1) return 13;
    if (pthread_join(thread,(void *)0) || !completed || irq_result || gpio(33)!=1) return 14;
    printf("ALLEGRO_IRQ two_explicit_edges_completed=1 pi2=%d\n",gpio(33));
    if (ioctl(fd,0x73ffU)!=-1 || *__errno_location()!=25) return 15;
    printf("ALLEGRO_UNKNOWN errno=%d\n",*__errno_location());
    close(input);close(fd);
    return 0;
}
