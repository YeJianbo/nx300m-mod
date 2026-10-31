/* IPCM/IPCS read ABI from Samsung's released d4_ipc{m,s}_ioctl.c.
 * FIFO inputs are explicit post-ISR queue notifications, not hardware IRQs,
 * DMA completions or evidence that an image operation has executed.
 */
struct ipc_event_queue {
    int initialized, input, notification, still;
    volatile int lock;
    unsigned char values[512];
    u32 head, count, seen, read_traces;
};
static struct ipc_event_queue ipc_queues[2];
static int ipc_dma_done[3] = {-1, -1, -1};
static void ipc_lock(struct ipc_event_queue *queue) {
    while (__sync_lock_test_and_set(&queue->lock, 1)) raw(158, 0, 0, 0, 0, 0, 0);
}
static void ipc_unlock(struct ipc_event_queue *queue) { __sync_lock_release(&queue->lock); }
static void ipc_enqueue(struct ipc_event_queue *queue, u32 value) {
    ipc_lock(queue);
    ++queue->seen;
    u32 capacity = queue->still ? 512 : 3;
    if (queue->count < capacity) {
        queue->values[(queue->head+queue->count)%capacity] = value;
        ++queue->count;
        ssif_complete(queue->notification);
    } else if (!queue->still) {
        /* IPCM completes even when its three-entry batch is already full. */
        ssif_complete(queue->notification);
    }
    ipc_unlock(queue);
}
static void *ipc_event_worker(void *value) {
    struct ipc_event_queue *queue = value;
    u32 number = 0; int digits = 0, invalid = 0;
    for (;;) {
        struct allegro_poll descriptor = {queue->input, 1, 0};
        if (raw(168, (long)&descriptor, 1, -1, 0, 0, 0) <= 0) continue;
        char input[64];
        int size = raw(3, queue->input, (long)input, sizeof(input), 0, 0, 0);
        for (int i = 0; i < size; i++) {
            unsigned char byte = input[i];
            if (byte >= '0' && byte <= '9') {
                if (number > 26) invalid = 1;
                if (!invalid) number = number*10+byte-'0';
                digits = 1;
            } else if (byte == '\n' || byte == '\r' || byte == ' ' || byte == '\t') {
                u32 first = queue->still ? 23 : 18, last = queue->still ? 26 : 20;
                if (digits && !invalid && number >= first && number <= last) ipc_enqueue(queue, number);
                number = 0; digits = invalid = 0;
            } else invalid = 1;
        }
    }
    return (void *)0;
}
static int ipc_event_initialize(int still) {
    struct ipc_event_queue *queue = ipc_queues+still;
    ipc_lock(queue);
    if (queue->initialized) { ipc_unlock(queue); return 0; }
    queue->still = still;
    const char *path = still ? "/var/run/nx300m-ipcs-events" : "/var/run/nx300m-ipcm-events";
    long result = raw(14, (long)path, 0010600, 0, 0, 0, 0);
    if (result < 0 && result != -17) { ipc_unlock(queue); return checked(result); }
    queue->input = checked(raw(5, (long)path, 2|04000|02000000, 0, 0, 0, 0));
    if (queue->input < 0) { ipc_unlock(queue); return -1; }
    /* Aggregate IPCM completions into one batch; IPCS consumes one per read. */
    queue->notification = eventfd(0, (still ? 1 : 0)|04000|02000000);
    int failure = queue->notification < 0 ? *__errno_location() : 0;
    if (!still) for (int channel = 0; channel < 3 && !failure; channel++) {
        ipc_dma_done[channel] = eventfd(0, 1|04000|02000000);
        if (ipc_dma_done[channel] < 0) failure = *__errno_location();
    }
    if (!failure) {
        unsigned long thread;
        failure = pthread_create(&thread, (void *)0, ipc_event_worker, queue);
        if (!failure) pthread_detach(thread);
    }
    if (failure) {
        raw(6, queue->input, 0, 0, 0, 0, 0);
        if (queue->notification >= 0) raw(6, queue->notification, 0, 0, 0, 0, 0);
        if (!still) for (int channel = 0; channel < 3; channel++) if (ipc_dma_done[channel] >= 0) {
            raw(6, ipc_dma_done[channel], 0, 0, 0, 0, 0); ipc_dma_done[channel] = -1;
        }
        ipc_unlock(queue); return error(failure);
    }
    queue->initialized = 1;
    ipc_unlock(queue);
    event("IPC explicit queue FIFO ready", still, still ? 512 : 3, 0);
    return 0;
}
static int ipc_read_interrupt(int fd, void *buffer, int still) {
    if (!buffer) return error(14);
    struct ipc_event_queue *queue = ipc_queues+still;
    /* The released IPCM driver ignores O_NONBLOCK; IPCS supports it. */
    int timeout = still && (raw(55, fd, 3, 0, 0, 0, 0)&04000) ? 0 : -1;
    for (;;) {
        struct allegro_poll descriptor = {queue->notification, 1, 0};
        int ready = checked(raw(168, (long)&descriptor, 1, timeout, 0, 0, 0));
        if (ready < 0) return -1;
        if (!ready) return error(11);
        ipc_lock(queue);
        unsigned long long value;
        long consumed = raw(3, queue->notification, (long)&value, 8, 0, 0, 0);
        if (consumed != 8) { ipc_unlock(queue); if (consumed == -11) continue; return checked(consumed); }
        if (still) {
            *(unsigned char *)buffer = queue->values[queue->head];
            queue->head = (queue->head+1)%512;
            --queue->count;
        } else {
            /* put_user(char, buf+i*4): only the low byte of each word changes. */
            for (u32 i = 0; i < 3; i++) {
                ((unsigned char *)buffer)[i*4] = queue->values[i];
                queue->values[i] = 0;
            }
            queue->head = queue->count = 0;
        }
        int remaining = queue->count;
        if (queue->read_traces++ < 8) event("IPC original reader consumed", still, *(unsigned char *)buffer, remaining);
        ipc_unlock(queue);
        return remaining; /* Samsung returns queue depth, not bytes copied. */
    }
}
u32 nx300m_ipc_events_seen(int still) {
    if (still != 0 && still != 1) return 0;
    struct ipc_event_queue *queue = ipc_queues+still;
    ipc_lock(queue); u32 result = queue->seen; ipc_unlock(queue); return result;
}
/* Pixel-processing backends call this only after their output writes finish.
 * The explicit queue FIFO deliberately does not produce these completions. */
void nx300m_ipcm_complete(u32 channel) {
    if (channel < 3 && ipc_dma_done[channel] >= 0) {
        ssif_complete(ipc_dma_done[channel]); ipc_enqueue(ipc_queues, 18+channel);
    }
}
static void ipc_reset_dma(u32 mask) {
    for (int channel = 0; channel < 3; channel++) if (mask&(1U<<channel)) ssif_drain(ipc_dma_done[channel]);
}
static int ipc_wait_dma(u32 channel, void *arg) {
    u32 milliseconds = *(u32 *)arg;
    int timeout = milliseconds > 2147483640U ? 2147483645 : (int)((milliseconds+4)/5*5);
    int result = ssif_wait(ipc_dma_done[channel], timeout);
    if (result < 0) return -1;
    *(int *)arg = result ? 0 : -1;
    return 0;
}
