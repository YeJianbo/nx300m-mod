/* Isolated ARM coverage of tracked mapping identity and protection changes. */
typedef unsigned int u32;
extern int printf(const char *, ...), open(const char *, int, ...), close(int), pipe(int *);
extern int ioctl(int, unsigned long, ...);
extern int write(int, const void *, u32), read(int, void *, u32), munmap(void *, u32), mprotect(void *, u32, int), *__errno_location(void);
extern int sscanf(const char *, const char *, ...), fork(void), waitpid(int, int *, int);
extern void _exit(int);
extern char *getenv(const char *);
extern void (*signal(int, void (*)(int)))(int);
extern void *mmap(void *, u32, int, int, int, long);
extern void *mmap64(void *, u32, int, int, int, unsigned long long);
extern void *mremap(void *, u32, u32, int, ...);
extern void *dlsym(void *, const char *);
enum { PAGE = 4096, SHARED = 1, PRIVATE = 2, FIXED = 0x10, ANON = 0x20, RECORDS = 512 };
#define BMA 0x90000000U
#define SMA 0xc6800000U
#define CHECK(x) do { if (!(x)) { printf("FAIL line=%d errno=%d\n", __LINE__, *__errno_location()); return 1; } } while (0)
static void *(*physical)(u32, u32);
static void *(*virtual_span)(u32, u32, u32 *);
static void *(*physical_access)(u32, u32, int);
static void *(*virtual_access)(u32, u32, u32 *, int);
static int span(unsigned char *p, u32 bytes) {
    u32 remaining = 123;
    return virtual_span((u32)p, 1, &remaining) == p && remaining == bytes;
}
static int absent(unsigned char *p) {
    u32 remaining = 123;
    return !virtual_span((u32)p, 1, &remaining) && !remaining;
}
static int maps_prot(unsigned char *p) {
    char bytes[32768];
    int fd = open("/proc/self/maps", 0), used = 0, got;
    if (fd < 0) return -1;
    while (used < (int)sizeof(bytes)-1 && (got = read(fd, bytes+used, sizeof(bytes)-1-used)) > 0) used += got;
    close(fd); bytes[used] = 0;
    for (char *line = bytes; *line;) {
        u32 lo, hi; char r, w, x;
        if (sscanf(line, "%x-%x %c%c%c", &lo, &hi, &r, &w, &x) == 5 && (u32)p >= lo && (u32)p < hi)
            return (r == 'r') | ((w == 'w') << 1) | ((x == 'x') << 2);
        while (*line && *line != '\n') line++;
        if (*line) line++;
    }
    return -1;
}
static void write_fault(int number) { _exit(200+number); }
static int write_status(unsigned char *p) {
    int child = fork(), status = -1;
    if (child < 0) return -1;
    if (!child) { signal(11, write_fault); *p = 0xa5; _exit(0); }
    return waitpid(child, &status, 0) == child ? status : -1;
}
static int pipe_read_status(const void *p) {
    int fds[2];
    if (pipe(fds)) return -999;
    int result = write(fds[1], p, 1);
    if (result < 0) result = -*__errno_location();
    close(fds[0]); close(fds[1]);
    return result;
}
int main(void) {
    physical = dlsym(0, "nx300m_map_physical");
    virtual_span = dlsym(0, "nx300m_map_virtual");
    physical_access = dlsym(0, "nx300m_map_physical_access");
    virtual_access = dlsym(0, "nx300m_map_virtual_access");
    CHECK(physical && virtual_span && physical_access && virtual_access);
    /* Stock pthread_create allocates its stack through the public mmap hook.
     * PWM open must finish without holding the mapping lock across that call. */
    int pwm = open("/dev/pwmdev.0", 2);
    CHECK(pwm >= 0 && close(pwm) == 0);
    printf("PWM_INIT stock_thread_start_under_mapping_shim=pass\n");
    int bma = open("/dev/d4_bma", 2), sma = open("/dev/d4_sma", 2);
    CHECK(bma >= 0 && sma >= 0);
    if (!getenv("NX300M_MAPPING_RESERVED_FAILURE")) {
        int (*snapshot)(u32,u32,void *) = dlsym(0, "nx300m_copy_live_virtual");
        int (*submit)(u32,u32,const void *,unsigned long long) = dlsym(0, "nx300m_write_live_virtual_if_generation");
        unsigned long long (*generation)(u32,u32,int) = dlsym(0, "nx300m_live_virtual_generation");
        struct { u32 physical, size; } allocation = {0, 2*PAGE};
        unsigned char copied[2*PAGE];
        CHECK(snapshot && submit && generation && ioctl(bma, 0xc0086201U, &allocation) == 0);
        unsigned char *live = mmap(0, 2*PAGE, 3, SHARED, bma, (long)allocation.physical);
        CHECK(live != (void *)-1);
        unsigned long long token = generation((u32)live, 2*PAGE, 1);
        CHECK(token);
        for (u32 i = 0; i < 2*PAGE; i++) live[i] = (unsigned char)(i*17+3);
        CHECK(snapshot((u32)live, 2*PAGE, copied) == 0);
        for (u32 i = 0; i < 2*PAGE; i++) CHECK(copied[i] == (unsigned char)(i*17+3));
        for (u32 i = 0; i < 2*PAGE; i++) copied[i] ^= 0x5a;
        CHECK(submit((u32)live, 2*PAGE, copied, token) == 0);
        for (u32 i = 0; i < 2*PAGE; i++) CHECK(live[i] == copied[i]);
        CHECK(submit((u32)live, PAGE, (void *)1, token) == -14);
        CHECK(snapshot((u32)live, PAGE, live+1) == -22);
        CHECK(snapshot((u32)live, PAGE, (void *)1) == -14);
        CHECK(mprotect(live, 2*PAGE, 2) == 0);
        CHECK(snapshot((u32)live, PAGE, copied) == -14);
        CHECK(mprotect(live, 2*PAGE, 1) == 0);
        CHECK(snapshot((u32)live, 2*PAGE, copied) == 0);
        CHECK(submit((u32)live, PAGE, copied, token) == -14);
        CHECK(ioctl(bma, 0xc0046204U, &allocation.physical) == 0);
        CHECK(snapshot((u32)live, PAGE, copied) == -14);
        CHECK(submit((u32)live, PAGE, copied, token) == -14);
        CHECK(ioctl(bma, 0xc0086201U, &allocation) == 0);
        CHECK(generation((u32)live, PAGE, 0) != token);
        CHECK(mprotect(live, 2*PAGE, 3) == 0);
        CHECK(submit((u32)live, PAGE, copied, token) == -116);
        token = generation((u32)live, PAGE, 1);
        CHECK(token && submit((u32)live, PAGE, copied, token) == 0);
        CHECK(mmap(live, 2*PAGE, 3, FIXED|PRIVATE, bma, (long)allocation.physical) == live);
        CHECK(!generation((u32)live, PAGE, 1));
        CHECK(submit((u32)live, PAGE, copied, token) == -14);
        CHECK(ioctl(bma, 0xc0046204U, &allocation.physical) == 0);
        CHECK(munmap(live, 2*PAGE) == 0);
        printf("LIVE_SNAPSHOT exact_bytes=pass chunks=2 overlap_denied=pass invalid_destination=pass read_permission=pass freed_allocation_denied=pass\n");
        printf("LIVE_SUBMIT exact_bytes=pass chunks=2 invalid_source=pass readonly_denied=pass freed_allocation_denied=pass reused_generation_denied=pass private_alias_denied=pass\n");
    }
    if (getenv("NX300M_MAPPING_RESERVED_FAILURE")) {
        u32 reserved_token = BMA+0x700000;
        unsigned char *source = mmap(0, 2*PAGE, 3, SHARED, bma, (long)reserved_token);
        CHECK(source != (void *)-1); source[0] = 0x58;
        unsigned char *relocated = mremap(source, 2*PAGE, 3*PAGE, 1);
        CHECK(relocated != (void *)-1 && relocated != source && absent(source));
        CHECK(physical(reserved_token, 3*PAGE) == relocated && relocated[0] == 0x58);
        CHECK(munmap(relocated, 3*PAGE) == 0);
        source = mmap(0, 2*PAGE, 3, SHARED, bma, (long)reserved_token);
        CHECK(source != (void *)-1 && mprotect(source+PAGE, PAGE, 1) == 0);
        CHECK(mremap(source, 2*PAGE, 3*PAGE, 1) == (void *)-1);
        int saved = *__errno_location(), actual = pipe_read_status(source);
        CHECK(saved == 14 && actual == -14);
        CHECK(absent(source) && absent(source+PAGE) && !physical(reserved_token, 1));
        CHECK(munmap(source, 2*PAGE) == 0 && close(bma) == 0 && close(sma) == 0);
        printf("MREMAP default_reserved_va_move=pass maymove_failure_errno=%d actual_source_cleared=pass failed_source_revoked=pass\n", saved);
        return 0;
    }
    u32 token = BMA + 0x100000;
    unsigned char *p = mmap(0, 4*PAGE+17, 3, SHARED, bma, (long)token);
    CHECK(p != (void *)-1 && span(p, 5*PAGE));
    CHECK(physical(token, 5*PAGE) == p && !physical(token, 5*PAGE+1));
    for (u32 n = 0; n < 5; n++) p[n*PAGE] = (unsigned char)(n+10);
    CHECK(mmap(p+PAGE, 1, 3, FIXED|PRIVATE, -1, 0) == (void *)-1);
    CHECK(*__errno_location() == 9 && span(p, 5*PAGE) && p[PAGE] == 11);
    CHECK(mmap(p+1, 1, 3, FIXED|PRIVATE|ANON, -1, 0) == (void *)-1);
    CHECK(*__errno_location() == 22 && span(p, 5*PAGE));
    CHECK(mmap(p, 0, 3, FIXED|PRIVATE|ANON, -1, 0) == (void *)-1);
    CHECK(span(p, 5*PAGE));
    CHECK(mmap(p, 0xfffffff1U, 3, FIXED|PRIVATE|ANON, -1, 0) == (void *)-1);
    CHECK(span(p, 5*PAGE) && p[4*PAGE] == 14);
    CHECK(mmap(p+PAGE, PAGE+1, 3, FIXED|PRIVATE|ANON, -1, 0) == p+PAGE);
    CHECK(span(p, PAGE) && absent(p+PAGE) && absent(p+2*PAGE));
    CHECK(span(p+3*PAGE, 2*PAGE) && !physical(token+PAGE, 1));
    CHECK(physical(token+3*PAGE, 2*PAGE) == p+3*PAGE);
    CHECK(!physical(token, PAGE+1) && !virtual_span((u32)p, PAGE+1, 0));
    CHECK(p[0] == 10 && !p[PAGE] && !p[2*PAGE] && p[3*PAGE] == 13);
    printf("MAP_REPLACE anonymous_interior=pass rounded_two_pages=pass failed_syscalls_preserved=pass\n");

    int file = open("/tmp/mapping-replacement", 0102|01000, 0600);
    unsigned char bytes[PAGE];
    for (u32 n = 0; n < PAGE; n++) bytes[n] = 0x5a;
    CHECK(file >= 0 && write(file, bytes, PAGE) == PAGE);
    CHECK(mmap64(p+3*PAGE, 1, 3, FIXED|PRIVATE, file, 0) == p+3*PAGE);
    CHECK(absent(p+3*PAGE) && span(p+4*PAGE, PAGE) && p[3*PAGE] == 0x5a);
    CHECK(!physical(token+3*PAGE, 1) && physical(token+4*PAGE, 1) == p+4*PAGE);
    CHECK(mmap(p+4*PAGE, 1, 3, FIXED|PRIVATE|ANON, -1, 0) == p+4*PAGE);
    CHECK(absent(p+4*PAGE));
    CHECK(mmap(p, 1, 3, FIXED|PRIVATE, file, 0) == p);
    CHECK(absent(p) && !physical(token, 1) && p[0] == 0x5a);
    CHECK(munmap(p, 5*PAGE) == 0 && close(file) == 0);
    printf("MAP_REPLACE ordinary_file_mmap64=pass prefix_suffix=pass physical_offsets=pass\n");

    p = mmap(0, 3*PAGE, 3, SHARED, bma, (long)token);
    CHECK(p != (void *)-1);
    CHECK(mmap(p+PAGE, 1, 3, FIXED|SHARED, sma, (long)SMA) == p+PAGE);
    CHECK(span(p, PAGE) && span(p+PAGE, PAGE) && span(p+2*PAGE, PAGE));
    CHECK(physical(token, 1) == p && !physical(token+PAGE, 1));
    CHECK(physical(SMA, PAGE) == p+PAGE && physical(token+2*PAGE, PAGE) == p+2*PAGE);
    CHECK(munmap(p, 3*PAGE) == 0);
    p = mmap(0, 3*PAGE, 3, SHARED, sma, (long)SMA);
    CHECK(p != (void *)-1 && munmap(p+1, 1) == -1 && span(p, 3*PAGE));
    CHECK(munmap(p+PAGE, 1) == 0);
    CHECK(span(p, PAGE) && absent(p+PAGE) && span(p+2*PAGE, PAGE));
    CHECK(!physical(SMA+PAGE, 1) && physical(SMA+2*PAGE, PAGE) == p+2*PAGE);
    CHECK(munmap(p, 3*PAGE) == 0);
    printf("MAP_REPLACE tracked_bma_to_sma=pass munmap_split_rounding=pass\n");

    /* Retain physical identity through NONE, read-only, and page splits. */
    token = BMA+0x200000;
    p = mmap(0, 3*PAGE, 0, SHARED, bma, (long)token);
    CHECK(p != (void *)-1 && absent(p) && !physical(token, 1));
    CHECK(!physical_access(token, 1, 1) && !physical_access(token, 1, 2));
    CHECK(mprotect(p, 3*PAGE, 3) == 0 && span(p, 3*PAGE));
    CHECK(physical(token, 3*PAGE) == p);
    p[PAGE] = 0x62;
    CHECK(mprotect(p+PAGE, 1, 1) == 0);
    CHECK(span(p, PAGE) && absent(p+PAGE) && span(p+2*PAGE, PAGE));
    CHECK(physical_access(token+PAGE, PAGE, 1) == p+PAGE);
    CHECK(!physical_access(token+PAGE, 1, 2) && !physical(token+PAGE, 1));
    u32 remaining = 0;
    CHECK(virtual_access((u32)(p+PAGE), PAGE, &remaining, 1) == p+PAGE && remaining == PAGE);
    CHECK(!virtual_access((u32)(p+PAGE), PAGE, &remaining, 2) && !remaining);
    CHECK(p[PAGE] == 0x62 && mprotect(p+PAGE, PAGE, 3) == 0 && span(p, 3*PAGE));
    CHECK(mprotect(p+PAGE, PAGE+1, 0) == 0 && absent(p+PAGE) && absent(p+2*PAGE));
    CHECK(physical(token, PAGE) == p && !physical(token+PAGE, 1));
    CHECK(mprotect(p+1, PAGE, 0) == -1 && *__errno_location() == 22 && span(p, PAGE));
    CHECK(mprotect(p, 3*PAGE, 3|0x1000000) == -1 && *__errno_location() == 22);
    CHECK(span(p, PAGE) && mprotect(p, 3*PAGE, 3) == 0 && span(p, 3*PAGE));
    CHECK(munmap(p, 3*PAGE) == 0);
    printf("MPROTECT none_mapping_retained=pass readonly_access=pass rounded_split_restore_merge=pass invalid_preserved=pass\n");

    /* Linux may apply a prefix before EACCES. QEMU's guest maps then retain
     * stale page flags: prove it with an actual isolated child write. */
    int ro = open("/lib/libc-2.13.so", 0);
    p = mmap(0, 3*PAGE, 1, SHARED, bma, (long)token);
    CHECK(ro >= 0 && p != (void *)-1);
    CHECK(mmap(p+PAGE, PAGE, 1, FIXED|SHARED, ro, 0) == p+PAGE);
    CHECK(mprotect(p, 3*PAGE, 3) == -1 && *__errno_location() == 13);
    int reported = maps_prot(p), actual_write = write_status(p);
    CHECK(reported == 1 && actual_write == 0);
    CHECK(!physical_access(token, 1, 1) && !physical_access(token+2*PAGE, 1, 1));
    CHECK(absent(p) && absent(p+2*PAGE));
    CHECK(mprotect(p, PAGE, 1) == 0 && physical_access(token, PAGE, 1) == p);
    CHECK(!physical(token, 1) && write_status(p) == (211 << 8));
    CHECK(mprotect(p, PAGE, 3) == 0 && physical(token, PAGE) == p);
    CHECK(mprotect(p+2*PAGE, PAGE, 1) == 0 && physical_access(token+2*PAGE, PAGE, 1) == p+2*PAGE);
    CHECK(!physical(token+2*PAGE, 1));
    CHECK(mprotect(p+2*PAGE, PAGE, 3) == 0 && physical(token+2*PAGE, PAGE) == p+2*PAGE);
    CHECK(munmap(p, 3*PAGE) == 0 && close(ro) == 0);
    printf("MPROTECT partial_eacces=pass guest_maps_stale_prot=%d actual_prefix_write_status=%d unknown_denied=pass restore_identity=pass actual_readonly_restored=pass\n", reported, actual_write);

    /* QEMU reserves guest holes as host VMAs, preventing in-place growth. */
    unsigned char *room = mmap(0, 4*PAGE, 3, PRIVATE|ANON, -1, 0);
    CHECK(room != (void *)-1 && munmap(room+3*PAGE, PAGE) == 0);
    token = BMA+0x300000;
    p = mmap(room, 3*PAGE, 3, FIXED|SHARED, bma, (long)token);
    CHECK(p == room); p[0] = 0x45; p[PAGE] = 0x67;
    CHECK(physical(token+2*PAGE, PAGE) == p+2*PAGE && p[0] == 0x45);
    CHECK(mremap(p, 3*PAGE, PAGE+1, 0) == p && span(p, 2*PAGE));
    CHECK(!physical(token+2*PAGE, 1) && absent(p+2*PAGE));
    CHECK(mremap(p, 2*PAGE, 3*PAGE, 0) == (void *)-1 && *__errno_location() == 12 && span(p, 2*PAGE));
    /* Occupied suffix forces MAYMOVE; permissions and physical offsets move. */
    CHECK(mmap(p+2*PAGE, PAGE, 3, FIXED|PRIVATE|ANON, -1, 0) == p+2*PAGE);
    CHECK(mprotect(p, 2*PAGE, 1) == 0);
    CHECK(mremap(p, 2*PAGE, 3*PAGE, 0) == (void *)-1 && *__errno_location() == 12);
    CHECK(physical_access(token, 2*PAGE, 1) == p);
    unsigned char *moved = mremap(p, 2*PAGE, 3*PAGE, 1);
    CHECK(moved != (void *)-1 && moved != p && absent(p));
    CHECK(physical_access(token, 3*PAGE, 1) == moved && !physical(token, 1));
    CHECK(moved[0] == 0x45 && moved[PAGE] == 0x67);
    CHECK(write_status(moved) == (211 << 8));
    CHECK(mprotect(moved, 3*PAGE, 3) == 0 && physical(token, 3*PAGE) == moved);
    CHECK(munmap(p+2*PAGE, PAGE) == 0 && munmap(moved, 3*PAGE) == 0);
    printf("MREMAP inplace_shrink=pass inplace_grow_qemu_enomem_preserved=pass maymove_relocated=pass permission_physical_preserved=pass nonfixed_failure_preserved=pass\n");

    p = mmap(0, 2*PAGE, 3, SHARED, bma, (long)token);
    unsigned char *target = mmap(0, 3*PAGE, 3, SHARED, sma, (long)SMA);
    CHECK(p != (void *)-1 && target != (void *)-1);
    p[PAGE] = 0x91;
    CHECK(mremap(p, 2*PAGE, PAGE, 3, p+PAGE) == (void *)-1 && *__errno_location() == 22);
    CHECK(span(p, 2*PAGE) && span(target, 3*PAGE));
    CHECK(mremap(p, 2*PAGE, 2*PAGE, 3, target+PAGE) == target+PAGE);
    CHECK(absent(p) && span(target, PAGE) && span(target+PAGE, 2*PAGE));
    CHECK(physical(SMA, PAGE) == target && !physical(SMA+PAGE, 1));
    CHECK(physical(token, 2*PAGE) == target+PAGE && target[2*PAGE] == 0x91);
    CHECK(munmap(target, 3*PAGE) == 0);
    /* A split source VMA fails only after Linux has unmapped FIXED target. */
    p = mmap(0, 3*PAGE, 3, SHARED, bma, (long)token);
    target = mmap(0, 2*PAGE, 3, SHARED, sma, (long)SMA);
    CHECK(p != (void *)-1 && target != (void *)-1);
    CHECK(mprotect(p+PAGE, PAGE, 1) == 0);
    CHECK(mremap(p, 3*PAGE, 2*PAGE, 3, target) == (void *)-1);
    int remap_error = *__errno_location();
    CHECK(remap_error == 14 && pipe_read_status(target) == -14);
    CHECK(absent(target) && !physical(SMA, 1) && absent(p+2*PAGE));
    CHECK(physical(token, PAGE) == p && physical_access(token+PAGE, PAGE, 1) == p+PAGE && pipe_read_status(p) == 1);
    CHECK(munmap(p, 3*PAGE) == 0 && munmap(target, 2*PAGE) == 0);
    printf("MREMAP fixed_replacement=pass early_invalid_preserved=pass fixed_failure_errno=%d actual_target_unmapped=pass possible_old_tail_revoked=pass\n", remap_error);

    p = mmap(0, PAGE, 3, SHARED, bma, (long)(BMA+0x08000000-PAGE));
    CHECK(p != (void *)-1);
    CHECK(mremap(p, PAGE, 2*PAGE, 1) == (void *)-1 && *__errno_location() == 22);
    CHECK(span(p, PAGE));
    CHECK(mremap(p, 0, PAGE, 1) == (void *)-1 && *__errno_location() == 95);
    CHECK(mremap(p, PAGE, PAGE, 1|4) == (void *)-1 && *__errno_location() == 95 && span(p, PAGE));
    CHECK(munmap(p, PAGE) == 0);
    pwm = open("/dev/pwmdev.0", 2);
    unsigned char *registers = mmap(0, PAGE, 3, SHARED, pwm, 0x10000000);
    CHECK(pwm >= 0 && registers != (void *)-1);
    CHECK(mremap(registers, PAGE, 2*PAGE, 1) == (void *)-1 && *__errno_location() == 95);
    p = mmap(0, PAGE, 3, PRIVATE|ANON, -1, 0);
    CHECK(p != (void *)-1);
    CHECK(mremap(p, PAGE, PAGE, 3, registers) == (void *)-1 && *__errno_location() == 95);
    moved = mremap(p, PAGE, 2*PAGE, 1);
    CHECK(moved != (void *)-1 && absent(moved));
    CHECK(munmap(moved, 2*PAGE) == 0 && munmap(registers, PAGE) == 0 && close(pwm) == 0);
    printf("MREMAP arena_limit=pass tracked_clone_dontunmap_rejected=pass mmio_source_target_rejected=pass ordinary_passthrough=pass\n");

    unsigned char *full[RECORDS];
    for (u32 n = 0; n < RECORDS; n++) {
        full[n] = mmap(0, 3*PAGE, 3, SHARED, bma, (long)(BMA+0x400000+n*4*PAGE));
        CHECK(full[n] != (void *)-1);
    }
    token = BMA+0x400000;
    p = full[0]; p[PAGE] = 0xa7;
    CHECK(mprotect(p+PAGE, PAGE, 0) == -1 && *__errno_location() == 12);
    CHECK(span(p, 3*PAGE) && maps_prot(p+PAGE) == 3 && p[PAGE] == 0xa7);
    CHECK(mprotect(p, 3*PAGE, 0) == 0 && absent(p) && !physical(token, 1));
    CHECK(mprotect(p, 3*PAGE, 3) == 0 && span(p, 3*PAGE) && p[PAGE] == 0xa7);
    printf("MPROTECT full_table_split_rejected_before_syscall=pass whole_record_restore=pass\n");
    CHECK(mremap(full[0], 3*PAGE, PAGE, 3, full[1]+PAGE) == (void *)-1 && *__errno_location() == 12);
    CHECK(span(full[0], 3*PAGE) && span(full[1], 3*PAGE) && full[0][PAGE] == 0xa7);
    CHECK(mremap(full[0], 3*PAGE, 3*PAGE, 3, full[1]) == full[1]);
    CHECK(absent(full[0]) && physical(token, 3*PAGE) == full[1]);
    /* Refill the reclaimed slot with the same provenance before old tests. */
    CHECK(mmap(full[0], 3*PAGE, 3, FIXED|SHARED, bma, (long)token) == full[0]);
    CHECK(mmap(full[1], 3*PAGE, 3, FIXED|SHARED, bma, (long)(token+4*PAGE)) == full[1]);
    printf("MREMAP full_table_split_rejected=pass fixed_record_slots_reused=pass\n");
    CHECK(mmap(0, PAGE, 3, SHARED, sma, (long)SMA) == (void *)-1);
    CHECK(*__errno_location() == 12);
    CHECK(mmap(p+PAGE, 1, 3, FIXED|PRIVATE|ANON, -1, 0) == (void *)-1);
    CHECK(*__errno_location() == 12 && span(p, 3*PAGE) && p[PAGE] == 0xa7);
    CHECK(mmap(p+PAGE, 1, 3, FIXED|SHARED, sma, (long)SMA) == (void *)-1);
    CHECK(*__errno_location() == 12 && physical(token+PAGE, 1) == p+PAGE);
    CHECK(munmap(p+PAGE, 1) == -1 && *__errno_location() == 12 && span(p, 3*PAGE));
    CHECK(mmap(p, 3*PAGE, 3, FIXED|SHARED, sma, (long)SMA) == p);
    CHECK(physical(SMA, 3*PAGE) == p && !physical(token, 1));
    CHECK(mmap(p, 3*PAGE, 3, FIXED|PRIVATE|ANON, -1, 0) == p && absent(p));
    CHECK(!physical(SMA, 1));
    /* One free slot permits two surviving halves, but not an extra tracked
     * middle. Both rejected operations must leave the original pages intact. */
    p = full[1]; p[PAGE] = 0x6e;
    CHECK(mprotect(p+PAGE, PAGE, 1) == -1 && *__errno_location() == 12 && span(p, 3*PAGE));
    CHECK(mprotect(p+PAGE, 2*PAGE, 1) == 0 && span(p, PAGE));
    CHECK(physical_access(BMA+0x400000+4*PAGE+PAGE, 2*PAGE, 1) == p+PAGE);
    CHECK(mprotect(p+PAGE, 2*PAGE, 3) == 0 && span(p, 3*PAGE));
    CHECK(mmap(p+PAGE, 1, 3, FIXED|SHARED, sma, (long)SMA) == (void *)-1);
    CHECK(*__errno_location() == 12 && span(p, 3*PAGE) && p[PAGE] == 0x6e);
    CHECK(mmap(p+PAGE, 1, 3, FIXED|PRIVATE|ANON, -1, 0) == p+PAGE);
    CHECK(span(p, PAGE) && absent(p+PAGE) && span(p+2*PAGE, PAGE) && !p[PAGE]);
    CHECK(mmap(full[2]+PAGE, 1, 3, FIXED|PRIVATE|ANON, -1, 0) == (void *)-1);
    CHECK(*__errno_location() == 12 && span(full[2], 3*PAGE));
    for (u32 n = 0; n < RECORDS; n++) CHECK(munmap(full[n], 3*PAGE) == 0);
    p = mmap(0, PAGE, 3, SHARED, sma, (long)SMA);
    CHECK(p != (void *)-1 && physical(SMA, PAGE) == p && munmap(p, PAGE) == 0);
    CHECK(close(bma) == 0 && close(sma) == 0);
    printf("MAP_REPLACE full_table=512 rejected_split_preserved=pass replacement_slot_reused=pass cleanup=pass\n");
    return 0;
}
