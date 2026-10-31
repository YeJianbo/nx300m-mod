/* Reproduce the missing finite PWM wait using the original libudd API. */
#include "pwm.h"
extern int printf(const char *, ...), open(const char *, int, ...), write(int, const void *, unsigned int), close(int);
extern void *stdout;
extern int setvbuf(void *, char *, int, unsigned int), usleep(unsigned int);
struct instant { long seconds, nanoseconds; };
extern int clock_gettime(int, struct instant *);
#define CHECK(x) do { if (!(x)) { printf("FAIL line=%d\n", __LINE__); return 1; } } while (0)
static long long now(void) {
    struct instant time; clock_gettime(1, &time);
    return (long long)time.seconds*1000000000LL+time.nanoseconds;
}
int main(void) {
    setvbuf(stdout, (char *)0, 2, 0);
    struct pwm_ch_data *channel = pwm_open(PWM_CH3);
    CHECK(channel);
    pwm_irq_set(channel);
    struct pwm_conf_info config = {0};
    config.trigtype = TP_HW_TRIGGER;
    config.freq2.period = config.freq2.duty = 60000000ULL;
    config.freq2.count = 1;
    config.resource = SOR_EXT_TRIGGER; config.starttype = S_FALLING_EDGE;
    pwm_extinput_set(channel, EXT_INPUT_3);
    pwm_config(channel, &config); pwm_int_enable(channel, INT_MIX_LAST_END); pwm_enable(channel);
    CHECK(pwm_wait_irq(channel, 25) == 0);
    int input = open("/var/run/nx300m-pwm-events", 1|04000);
    CHECK(input >= 0);
    CHECK(write(input, "5\n4\n", 4) == 4); /* wrong external source */
    CHECK(pwm_wait_irq(channel, 25) == 0);
    CHECK(write(input, "7\n7\n", 4) == 4); /* baseline and unchanged level */
    CHECK(pwm_wait_irq(channel, 25) == 0);
    long long started = now();
    CHECK(write(input, "6\n", 2) == 2);
    CHECK(pwm_wait_irq(channel, 1000) == 1);
    long long elapsed = now()-started;
    CHECK(elapsed >= 50000000LL && elapsed < 900000000LL);
    CHECK(pwm_wait_irq(channel, 10) == 0);
    printf("PWM external falling: no edge/no source match held wait; measured_ns=%lld; single IRQ consumed\n", elapsed);
    config.trigtype = TP_SW_TRIGGER;
    pwm_config(channel, &config); pwm_int_enable(channel, INT_MIX_LAST_END);
    started = now(); pwm_enable(channel);
    CHECK(pwm_wait_irq(channel, 1000) == 1);
    CHECK(now()-started >= 50000000LL);
    pwm_config(channel, &config); pwm_int_enable(channel, INT_MIX_LAST_END); pwm_enable(channel);
    usleep(10000); pwm_disable(channel);
    CHECK(pwm_wait_irq(channel, 100) == 0);
    pwm_config(channel, &config); pwm_int_disable(channel); pwm_enable(channel);
    CHECK(pwm_wait_irq(channel, 100) == 0);
    printf("PWM software trigger, disable cancellation and masked IRQ passed\n");
    close(input); pwm_close(channel);
    return 0;
}
