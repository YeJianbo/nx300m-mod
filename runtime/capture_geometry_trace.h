/* Stock NX300M 1.15 ABI, checked by build_capture_geometry_trace.py.
 * Observe the internal PLT edges of CCpoMnF::bayerSplit, rather than assuming
 * that its caller's virtual dispatch is interposable. Never edit camera RAM.
 */
extern char *getenv(const char *);
extern int *__errno_location(void);
extern int pipe(int *), read(int, void *, unsigned int);
extern int write(int, const void *, unsigned int), close(int), getpid(void);
extern char *fgets(char *, int, void *);
extern int sscanf(const char *, const char *, ...);
extern int strcmp(const char *, const char *);

struct geometry_context {
    void *wrapper;
    unsigned int input, output, sequence, split_height;
};
static __thread struct geometry_context geometry_context;
/* RAW processing and the frontend PP call can run on different threads.
 * Match all four programmed physical bases before sharing a split sequence. */
static struct geometry_context geometry_finished;
static volatile int geometry_finished_lock;
static unsigned int geometry_sequence;
static unsigned int geometry_map_failures, geometry_set_diagnostics, geometry_finish_diagnostics;

static int geometry_enabled(void) {
    const char *value = getenv("NX300M_CAPTURE_GEOMETRY");
    return value && value[0] == '1' && !value[1];
}

__attribute__((constructor)) static void geometry_loaded(void) {
    fprintf(stderr, "[STOCK TRACE] geometry revision=4 loaded enabled=%d\n", geometry_enabled());
}

/* Check real readable RAM mappings before asking the kernel to copy. Stock
 * executable stacks are writable RAM, not code pages. Read-only executable
 * mappings and device registers are excluded; SMA/BMA allocations are allowed.
 * The subsequent pipe copy also handles a mapping disappearing after lookup.
 */
static int geometry_mapped(unsigned int address, unsigned int bytes) {
    if (!address || !bytes || address > ~0U-bytes) return 0;
    void *maps = fopen("/proc/self/maps", "r");
    if (!maps) {
        if (__sync_fetch_and_add(&geometry_map_failures, 1) < 16)
            fprintf(stderr, "[STOCK TRACE] geometry map-check address=%08x bytes=%u status=maps_open_failed errno=%d\n", address, bytes, *__errno_location());
        return 0;
    }
    char line[1024], permissions[5];
    unsigned int begin, end, cursor = address;
    unsigned int lines = 0;
    const char *reason = "no_matching_readable_mapping";
    for (; lines < 4096 && fgets(line, sizeof line, maps); lines++) {
        if (sscanf(line, "%x-%x %4s", &begin, &end, permissions) != 3) continue;
        if (cursor < begin || cursor >= end) continue;
        if (permissions[0] != 'r' || (permissions[2] == 'x' && permissions[1] != 'w')) {
            reason = "mapping_permissions"; break;
        }
        if (strstr(line, "/dev/") && !strstr(line, "/dev/sma") &&
            !strstr(line, "/dev/bma") && !strstr(line, "/dev/zero")) { reason = "device_mapping_excluded"; break; }
        if (address+bytes <= end) { fclose(maps); return 1; }
        cursor = end;
    }
    fclose(maps);
    if (__sync_fetch_and_add(&geometry_map_failures, 1) < 16)
        fprintf(stderr, "[STOCK TRACE] geometry map-check address=%08x bytes=%u status=%s lines=%u\n", address, bytes, reason, lines);
    return 0;
}

static int geometry_pipe_copy(void *destination, unsigned int address, unsigned int bytes) {
    if (bytes > 1024) return 0;
    int descriptors[2];
    if (pipe(descriptors)) return 0;
    /* Each private pipe is empty, and every write is <= Linux PIPE_BUF.
     * No direct dereference of an untrusted camera pointer occurs here. */
    int written = write(descriptors[1], (const void *)address, bytes);
    int copied = written == (int)bytes ? read(descriptors[0], destination, bytes) : -1;
    close(descriptors[0]); close(descriptors[1]);
    return copied == (int)bytes;
}

static int geometry_copy(void *destination, unsigned int address, unsigned int bytes) {
    return geometry_mapped(address, bytes) && geometry_pipe_copy(destination, address, bytes);
}

static int geometry_caller_at(void *caller, unsigned int address, struct trace_library *library) {
    library->path = library->symbol = (const char *)0;
    library->base = library->address = (void *)0;
    if (!dladdr(caller, library) || !library->path) return 0;
    const char *basename = library->path;
    for (const char *p = library->path; *p; p++) if (*p == '/') basename = p+1;
    /* The builder resolves these stock filenames to the same real ELF. The
     * loader reports the generic symlink name during actual camera capture. */
    if (strcmp(basename, "libcapture-fw-slpcam-nx300.so") &&
        strcmp(basename, "libcapture-fw-slpcam.so")) return 0;
    /* Stock's load-relative instruction is authoritative. dladdr's nearest
     * symbol can be an alias; do not require that optional label to match. */
    return (unsigned int)caller-(unsigned int)library->base == address;
}

static int geometry_caller(void *caller, unsigned int relative, struct trace_library *library) {
    return geometry_caller_at(caller, 0x18d67c+relative, library);
}

static void geometry_bytes(void *metadata, const char *phase, const char *role,
        unsigned int plane, const char *boundary, unsigned int address,
        unsigned int row, unsigned int stride, unsigned int rows) {
    unsigned int bytes = stride*rows;
    if (!rows || rows > 64 || stride > 32768 || !stride ||
        row > (~0U-address)/stride || bytes > ~0U-(address+row*stride)) {
        fprintf(metadata, "sample role=%s plane=%u boundary=%s status=invalid_extent\n", role, plane, boundary);
        return;
    }
    address += row*stride;
    if (!geometry_mapped(address, bytes)) {
        fprintf(metadata, "sample role=%s plane=%u boundary=%s address=%08x bytes=%u status=unmapped_or_excluded\n",
            role, plane, boundary, address, bytes);
        return;
    }
    char path[224], temporary[240];
    sprintf(path, "/var/run/nx300m-geometry-%d-%u-%s-%s-p%u-%s.bin",
        getpid(), geometry_context.sequence, phase, role, plane, boundary);
    sprintf(temporary, "%s.tmp", path);
    void *file = fopen(temporary, "wb");
    unsigned int offset = 0;
    unsigned char chunk[1024];
    int ok = file != (void *)0;
    while (ok && offset < bytes) {
        unsigned int count = bytes-offset < sizeof chunk ? bytes-offset : sizeof chunk;
        /* The complete extent was checked above; the kernel copy still
         * protects each chunk against an intervening unmap. */
        ok = geometry_pipe_copy(chunk, address+offset, count) && fwrite(chunk, 1, count, file) == count;
        offset += count;
    }
    if (file && fclose(file)) ok = 0;
    if (ok && rename(temporary, path)) ok = 0;
    fprintf(metadata, "sample role=%s plane=%u boundary=%s address=%08x row=%u stride=%u rows=%u bytes=%u status=%s path=%s\n",
        role, plane, boundary, address, row, stride, rows, bytes, ok ? "saved" : "copy_or_io_failed", path);
}

static void geometry_image(void *metadata, const char *phase, const char *role, unsigned int object) {
    unsigned int image[0x8c/4];
    if (!geometry_copy(image, object, sizeof image)) {
        fprintf(metadata, "image role=%s object=%08x status=unreadable\n", role, object);
        return;
    }
    fprintf(metadata, "image role=%s object=%08x GWidth=%u GHeight=%u Width=%u Height=%u OffsetX=%u OffsetY=%u ImageFormat=%u RawPackBit=%u RawStride=%u AddrType=%u\n",
        role, object, image[0], image[1], image[2], image[3], image[4], image[5],
        image[0x20/4], image[0x28/4], image[0x38/4], image[0x58/4]);
    for (unsigned int p = 0; p < 4; p++)
        fprintf(metadata, "base role=%s slot=%u physical=%08x virtual=%08x\n",
            role, p, image[0x5c/4+p], image[0x6c/4+p]);
    /* Exact raw branches only: GetAddrFromCapImage applies no XY offset.
     * Type 1 has one interleaved mosaic buffer; type 2 has four SECC planes.
     * Do not turn missing virtual bases into guessed or physical pointers. */
    unsigned int type = image[0x58/4], stride = image[0x38/4];
    if (image[0x20/4] != 1 || (type != 1 && type != 2) ||
        !image[1] || !image[3] || image[1] < image[3] || image[1] > 16384 ||
        !stride || stride > 32768 || (type == 2 && ((image[1] | image[3] | stride) & 1))) {
        fprintf(metadata, "samples role=%s status=unsupported_geometry_or_format\n", role);
        return;
    }
    /* calcImageOffset (0x172150..0x172168) reserves RawStride*GHeight/4
     * per slot, i.e. RawStride/2 for each of GHeight/2 plane rows. */
    if (type == 2) stride >>= 1;
    unsigned int effective = image[3] >> (type == 2), global = image[1] >> (type == 2);
    fprintf(metadata, "layout role=%s storage=%s rows_effective=%u rows_global=%u row_bytes=%u offset_applied=0\n",
        role, type == 2 ? "SECC_four_slots" : "interleaved_mosaic", effective, global, stride);
    for (unsigned int p = 0; p < (type == 2 ? 4U : 1U); p++) {
        unsigned int base = image[0x6c/4+p];
        geometry_bytes(metadata, phase, role, p, "first", base, 0, stride, 1);
        geometry_bytes(metadata, phase, role, p, "effective_last", base, effective-1, stride, 1);
        geometry_bytes(metadata, phase, role, p, "global_last", base, global-1, stride, 1);
        if (global > effective)
            geometry_bytes(metadata, phase, role, p, "reserved_tail", base, effective, stride, global-effective);
        if (type == 2 && !strcmp(role, "output") && geometry_context.split_height &&
            !(geometry_context.split_height & 1) && geometry_context.split_height/2 <= global) {
            unsigned int written_rows = geometry_context.split_height/2;
            geometry_bytes(metadata, phase, role, p, "split_effective_last", base, written_rows-1, stride, 1);
            if (global > written_rows)
                geometry_bytes(metadata, phase, role, p, "split_reserved_tail", base, written_rows, stride, global-written_rows);
        }
    }
}

static void geometry_snapshot(const char *phase) {
    char path[160], temporary[176];
    sprintf(path, "/var/run/nx300m-geometry-%d-%u-%s.txt", getpid(), geometry_context.sequence, phase);
    sprintf(temporary, "%s.tmp", path);
    void *metadata = fopen(temporary, "w");
    if (!metadata) { fprintf(stderr, "[STOCK TRACE] geometry metadata open failed path=%s\n", path); return; }
    fprintf(metadata, "version=1 phase=%s wrapper=%p sequence=%u boundary=internal_bayerSplit_PLT physical_dma_extent_proved=0\n",
        phase, geometry_context.wrapper, geometry_context.sequence);
    geometry_image(metadata, phase, "input", geometry_context.input);
    geometry_image(metadata, phase, "output", geometry_context.output);
    int closed = fclose(metadata);
    if (!closed && !rename(temporary, path))
        fprintf(stderr, "[STOCK TRACE] geometry snapshot phase=%s path=%s\n", phase, path);
}

static void geometry_output_snapshot(const char *phase, const unsigned int *rdma) {
    char path[160], temporary[176];
    sprintf(path, "/var/run/nx300m-geometry-%d-%u-%s.txt", getpid(), geometry_context.sequence, phase);
    sprintf(temporary, "%s.tmp", path);
    void *metadata = fopen(temporary, "w");
    if (!metadata) return;
    fprintf(metadata, "version=1 phase=%s wrapper=%p sequence=%u boundary=stock_buffer_producer physical_dma_extent_proved=0\n",
        phase, geometry_context.wrapper, geometry_context.sequence);
    geometry_image(metadata, phase, "output", geometry_context.output);
    if (rdma) {
        /* These values are read after stock ppcore setup and before start.
         * They describe actual source RAM, independently of CCapImage fields. */
        unsigned int width = (rdma[0]&8191U)*4, height = rdma[1]>>16;
        fprintf(metadata, "pp_rdma width=%u height=%u width_word=%08x height_word=%08x format=%08x secc=%08x common=%08x\n",
            width, height, rdma[0], rdma[1], rdma[2], rdma[3], rdma[4]);
        void *(*map_physical)(unsigned int, unsigned int) = dlsym((void *)0, "nx300m_map_physical");
        for (unsigned int p = 0; p < 4; p++) {
            unsigned int address = rdma[5+p], stride = rdma[9+(p&1U)];
            unsigned int rows = height/2, effective = geometry_context.split_height/2;
            fprintf(metadata, "pp_base plane=%u physical=%08x stride=%u\n", p, address, stride);
            if (!map_physical || !height || (height&1U) || height > 16384 ||
                (rdma[2]&0x01130ff3U) != 0x663U || ((rdma[3]>>4)&7U) != 7 ||
                (rdma[0]&8191U) != ((rdma[0]>>16)&8191U) ||
                !stride || stride > 32768 || rows > ~0U/stride || !effective || effective > rows) {
                fprintf(metadata, "pp_samples plane=%u status=unsupported_geometry\n", p); continue;
            }
            unsigned int base = (unsigned int)map_physical(address, rows*stride);
            if (!base) { fprintf(metadata, "pp_samples plane=%u status=unmapped\n", p); continue; }
            geometry_bytes(metadata, phase, "pp_rdma", p, "first", base, 0, stride, 1);
            geometry_bytes(metadata, phase, "pp_rdma", p, "effective_last", base, effective-1, stride, 1);
            geometry_bytes(metadata, phase, "pp_rdma", p, "global_last", base, rows-1, stride, 1);
            if (rows > effective)
                geometry_bytes(metadata, phase, "pp_rdma", p, "reserved_tail", base, effective, stride, rows-effective);
        }
    }
    if (!fclose(metadata) && !rename(temporary, path))
        fprintf(stderr, "[STOCK TRACE] geometry snapshot phase=%s path=%s\n", phase, path);
}

unsigned long geometry_pool_assigned(void *self, void *address)
    __asm__("_ZN9CCapImage17setVirtualAddressEPv");
unsigned long geometry_pool_assigned(void *self, void *address) {
    int incoming_errno = *__errno_location();
    unsigned long (*original)(void *, void *) = next("_ZN9CCapImage17setVirtualAddressEPv");
    struct trace_library library;
    int watched = geometry_enabled() && geometry_caller_at(__builtin_return_address(0), 0x2ae17c, &library);
    *__errno_location() = incoming_errno;
    unsigned long result = original(self, address);
    int outgoing_errno = *__errno_location();
    if (watched) {
        geometry_context.wrapper = (void *)0;
        geometry_context.input = 0;
        geometry_context.output = (unsigned int)self;
        geometry_context.split_height = 0;
        geometry_context.sequence = __sync_add_and_fetch(&geometry_sequence, 1);
        fprintf(stderr, "[STOCK TRACE] geometry pool assigned sequence=%u object=%p returned_buffer=%p\n",
            geometry_context.sequence, self, address);
        geometry_output_snapshot("after_pool_assignment", (const unsigned int *)0);
    }
    *__errno_location() = outgoing_errno;
    return result;
}

unsigned long cf_pp_capture_frame_read_write_start(void) {
    int incoming_errno = *__errno_location();
    unsigned long (*original)(void) = next("cf_pp_capture_frame_read_write_start");
    struct trace_library library;
    if (geometry_enabled() && geometry_caller_at(__builtin_return_address(0), 0x1a902c, &library)) {
        unsigned int (*read_register)(unsigned int) = next("read_pp_core_register");
        static const unsigned int offsets[11] = {0x3004,0x3008,0x3000,0x3038,8,
            0x300c,0x3018,0x3010,0x301c,0x3014,0x3020};
        unsigned int rdma[11], image[0x8c/4];
        for (unsigned int i = 0; i < 11; i++) rdma[i] = read_register(offsets[i]);
        while (__sync_lock_test_and_set(&geometry_finished_lock, 1)) {}
        struct geometry_context finished = geometry_finished;
        __sync_lock_release(&geometry_finished_lock);
        int matched = finished.sequence && geometry_copy(image, finished.output, sizeof image);
        for (unsigned int i = 0; i < 4 && matched; i++)
            if (image[0x5c/4+i] != rdma[5+i]) matched = 0;
        fprintf(stderr, "[STOCK TRACE] geometry second PP before start matched=%d sequence=%u output=%08x\n",
            matched, finished.sequence, finished.output);
        if (matched) {
            struct geometry_context previous = geometry_context;
            geometry_context = finished;
            geometry_output_snapshot("before_second_PP", rdma);
            geometry_context = previous;
        }
    }
    *__errno_location() = incoming_errno;
    return original();
}

unsigned long geometry_set_op(void *self, const void *parameter)
    __asm__("_ZN13CDDWrapperMnF10SetOpParamEP15stMnF_Geo_Param");
unsigned long geometry_set_op(void *self, const void *parameter) {
    int saved_errno = *__errno_location();
    unsigned long (*original)(void *, const void *) = next("_ZN13CDDWrapperMnF10SetOpParamEP15stMnF_Geo_Param");
    unsigned int words[4] = {0};
    int enabled = geometry_enabled();
    void *caller = __builtin_return_address(0);
    struct trace_library library;
    int caller_ok = geometry_caller(caller, 0x118, &library);
    int copied = enabled && geometry_copy(words, (unsigned int)parameter, sizeof words);
    if (__sync_fetch_and_add(&geometry_set_diagnostics, 1) < 16 || (copied && words[0] == 17))
        fprintf(stderr, "[STOCK TRACE] geometry gate=SetOpParam enabled=%d caller=%p library=%s base=%p load_offset=%x symbol=%s symbol_offset=%x caller_ok=%d parameter=%p copied=%d mode=%u\n",
            enabled, caller, library.path ? library.path : "unknown", library.base,
            (unsigned int)caller-(unsigned int)library.base, library.symbol ? library.symbol : "unknown",
            (unsigned int)caller-(unsigned int)library.address, caller_ok, parameter, copied, words[0]);
    if (enabled && caller_ok && copied && words[0] == 17) {
        int from_pool = !geometry_context.wrapper && geometry_context.output == words[3] && geometry_context.sequence;
        if (geometry_context.wrapper)
            fprintf(stderr, "[STOCK TRACE] geometry abandoned unmatched sequence=%u\n", geometry_context.sequence);
        geometry_context.wrapper = self;
        geometry_context.input = words[1]; geometry_context.output = words[3];
        geometry_context.split_height = 0;
        unsigned int input_geometry[4];
        if (geometry_copy(input_geometry, words[1], sizeof input_geometry) && input_geometry[3] <= 16384)
            geometry_context.split_height = input_geometry[3];
        if (!from_pool)
            geometry_context.sequence = __sync_add_and_fetch(&geometry_sequence, 1);
        geometry_snapshot("before_SetOpParam");
    }
    *__errno_location() = saved_errno;
    return original(self, parameter);
}

unsigned long geometry_finish(void *self) __asm__("_ZN13CDDWrapperMnF15FinishOperationEv");
unsigned long geometry_finish(void *self) {
    int entry_errno = *__errno_location();
    unsigned long (*original)(void *) = next("_ZN13CDDWrapperMnF15FinishOperationEv");
    void *caller = __builtin_return_address(0);
    struct trace_library library;
    int caller_ok = geometry_caller(caller, 0x154, &library);
    int watched = geometry_context.wrapper == self && caller_ok;
    if (__sync_fetch_and_add(&geometry_finish_diagnostics, 1) < 16 || watched)
        fprintf(stderr, "[STOCK TRACE] geometry gate=FinishOperation caller=%p library=%s load_offset=%x symbol=%s caller_ok=%d paired=%d\n",
            caller, library.path ? library.path : "unknown", (unsigned int)caller-(unsigned int)library.base,
            library.symbol ? library.symbol : "unknown", caller_ok, geometry_context.wrapper == self);
    *__errno_location() = entry_errno;
    unsigned long result = original(self);
    int saved_errno = *__errno_location();
    if (watched) {
        geometry_snapshot("after_FinishOperation");
        fprintf(stderr, "[STOCK TRACE] geometry FinishOperation raw_r0=%lx sequence=%u\n", result, geometry_context.sequence);
        while (__sync_lock_test_and_set(&geometry_finished_lock, 1)) {}
        geometry_finished = geometry_context;
        __sync_lock_release(&geometry_finished_lock);
        geometry_context.wrapper = (void *)0;
    }
    *__errno_location() = saved_errno;
    return result;
}
