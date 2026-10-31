/* Bounded, external NX300M 1.15 observation. Never calls camera code.
 * /proc/PID/mem is opened O_RDONLY; the target is not stopped or locked.
 * SPDX-License-Identifier: AGPL-3.0-or-later
 */
#include <stddef.h>
#include <stdint.h>
extern int open(const char *, int, ...);
extern int close(int);
extern int read(int, void *, unsigned int);
extern int pread64(int, void *, unsigned int, long long);
extern int readlink(const char *, char *, unsigned int);
extern long syscall(long, ...);
extern int snprintf(char *, unsigned int, const char *, ...);
extern int sscanf(const char *, const char *, ...);
extern int printf(const char *, ...);
extern int fflush(void *);
extern int strcmp(const char *, const char *);
extern unsigned int strlen(const char *);
extern char *strchr(const char *, int);
extern char *strrchr(const char *, int);
extern unsigned long strtoul(const char *, char **, int);
extern unsigned long long strtoull(const char *, char **, int);
extern int memcmp(const void *, const void *, unsigned int);
extern void *memcpy(void *, const void *, unsigned int);
extern int *__errno_location(void);

struct load_segment { uint32_t vaddr, offset, filesz, memsz, flags; };
struct stock_check { unsigned int module; uint32_t address, size; const unsigned char *bytes; const char *name; };
#include "manual_lens_probe_stock.h"

#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define MAP_CAP 2048u
#define MAP_TEXT_CAP (512u * 1024u)
struct mapping { uint32_t start, end, offset; int readable, executable, module, anonymous; };
static struct mapping maps[MAP_CAP];
static unsigned char device_maps[MAP_CAP];
static unsigned int map_count;
static char map_text[MAP_TEXT_CAP];
static uint32_t biases[4];
static int bias_found[4], mem_fd = -1, saved_errno;
static unsigned int pid, process_matches, verified_checks;
static int lens_table_verified;
static unsigned long long start_ticks;
static const char *reason = "observed", *stage = "complete";
static int result_code;

static void problem(int code, const char *where, const char *why) {
    if (!result_code) { result_code = code; stage = where; reason = why; saved_errno = *__errno_location(); }
}

static int path_for(char path[96], const char *leaf) {
    int n = snprintf(path, 96, "/proc/%u/%s", pid, leaf);
    return n > 0 && n < 96;
}

static int text_file(const char *path, char *out, unsigned int cap) {
    int fd = open(path, 0);
    if (fd < 0) return -1;
    unsigned int used = 0;
    while (used + 1 < cap) {
        int n = read(fd, out + used, cap - used - 1);
        if (n < 0) { int error = *__errno_location(); close(fd); *__errno_location() = error; return -1; }
        if (!n) { out[used] = 0; close(fd); return (int)used; }
        used += (unsigned int)n;
    }
    char extra;
    int n = read(fd, &extra, 1);
    close(fd);
    if (n != 0) return -2;
    out[used] = 0;
    return (int)used;
}

static int executable_matches(unsigned int candidate) {
    char path[96], target[256];
    int n = snprintf(path, sizeof path, "/proc/%u/exe", candidate);
    if (n <= 0 || n >= (int)sizeof path) return 0;
    n = readlink(path, target, sizeof target - 1);
    if (n < 0 || n >= (int)sizeof target - 1) return 0;
    target[n] = 0;
    return strcmp(target, probe_module_paths[0]) == 0;
}

static int find_process(void) {
    int fd = open("/proc", 0);
    if (fd < 0) { problem(3, "identity", "proc_open_failed"); return 0; }
    unsigned char entries[4096];
    for (unsigned int batch = 0; batch < 256; batch++) {
        /* A32 Linux getdents64: packed header is ino64, off64, reclen16, type8. */
        long count = syscall(217, fd, entries, sizeof entries);
        if (count < 0) { problem(3, "identity", "proc_enumeration_failed"); close(fd); return 0; }
        if (!count) {
            close(fd);
            if (process_matches != 1) problem(2, "identity", process_matches ? "multiple_camera_processes" : "camera_process_not_found");
            return process_matches == 1;
        }
        for (unsigned int at = 0; at < (unsigned int)count;) {
            uint16_t length;
            if ((unsigned int)count - at < 20) { problem(2, "identity", "invalid_proc_directory_record"); close(fd); return 0; }
            memcpy(&length, entries + at + 16, 2);
            if (length < 20 || length > (unsigned int)count - at) { problem(2, "identity", "invalid_proc_directory_record"); close(fd); return 0; }
            char *name = (char *)entries + at + 19;
            unsigned int available = length - 19, digits = 0;
            while (digits < available && name[digits] >= '0' && name[digits] <= '9') digits++;
            if (digits && digits < available && !name[digits]) {
                unsigned long candidate = strtoul(name, (char **)0, 10);
                if (candidate && candidate <= 0x7fffffffu && executable_matches((unsigned int)candidate)) {
                    process_matches++; pid = (unsigned int)candidate;
                }
            }
            at += length;
        }
    }
    close(fd); problem(2, "identity", "proc_enumeration_limit"); return 0;
}

static int process_start(unsigned long long *ticks) {
    char path[96], text[4096];
    if (!path_for(path, "stat") || text_file(path, text, sizeof text) < 0) return 0;
    char *p = strrchr(text, ')');
    if (!p) return 0;
    p++;
    for (unsigned int field = 3; field < 22; field++) {
        while (*p == ' ') p++;
        if (!*p) return 0;
        while (*p && *p != ' ') p++;
    }
    while (*p == ' ') p++;
    char *end;
    *ticks = strtoull(p, &end, 10);
    return end != p && (*end == ' ' || !*end);
}

static int read_maps(void) {
    char path[96];
    if (!path_for(path, "maps")) return 0;
    int count = text_file(path, map_text, sizeof map_text);
    if (count < 0) { problem(3, "maps", count == -2 ? "maps_exceeded_bound" : "maps_read_failed"); return 0; }
    char *line = map_text;
    while (*line) {
        char *next = strchr(line, '\n');
        if (next) *next++ = 0;
        if (map_count == MAP_CAP) { problem(2, "maps", "too_many_mappings"); return 0; }
        struct mapping *m = &maps[map_count];
        char permissions[5]; int consumed = 0;
        if (sscanf(line, "%x-%x %4s %x %*s %*s %n", &m->start, &m->end, permissions, &m->offset, &consumed) != 4 ||
            consumed <= 0 || m->end <= m->start) { problem(2, "maps", "invalid_map_record"); return 0; }
        m->readable = permissions[0] == 'r'; m->executable = permissions[2] == 'x'; m->module = -1;
        const char *name = line + consumed;
        while (*name == ' ') name++;
        m->anonymous = !*name;
        device_maps[map_count] = strlen(name) >= 5 && !memcmp(name, "/dev/", 5);
        for (unsigned int mod = 0; mod < 4; mod++) if (!strcmp(name, probe_module_paths[mod])) m->module = (int)mod;
        if (m->module >= 0 && m->executable) {
            unsigned int mod = (unsigned int)m->module;
            for (unsigned int seg = 0; seg < probe_segment_counts[mod]; seg++) {
                const struct load_segment *s = &probe_segments[mod][seg];
                uint32_t file_page = s->offset & ~4095u, virtual_page = s->vaddr & ~4095u;
                uint32_t file_end = (s->offset + s->filesz + 4095u) & ~4095u;
                if (!(s->flags & 1) || m->offset < file_page || m->offset >= file_end) continue;
                uint32_t relative = virtual_page + m->offset - file_page;
                if (m->start < relative) { problem(2, "maps", "invalid_load_bias"); return 0; }
                uint32_t bias = m->start - relative;
                if (bias_found[mod] && biases[mod] != bias) { problem(2, "maps", "ambiguous_module_load_bias"); return 0; }
                biases[mod] = bias; bias_found[mod] = 1;
            }
        }
        map_count++;
        if (!next) break;
        line = next;
    }
    for (unsigned int mod = 0; mod < 4; mod++) if (!bias_found[mod]) { problem(2, "maps", "required_module_not_mapped"); return 0; }
    if (biases[0] != 0) { problem(2, "maps", "unexpected_executable_bias"); return 0; }
    return 1;
}

static int readable(uint32_t address, unsigned int size, int module) {
    if (!address || !size || address > UINT32_MAX - size) return 0;
    uint32_t end = address + size;
    if (module >= 0) {
        int within_segment = 0;
        if (module >= 4 || !bias_found[module]) return 0;
        for (unsigned int seg = 0; seg < probe_segment_counts[module]; seg++) {
            const struct load_segment *s = &probe_segments[module][seg];
            uint64_t begin = (uint64_t)biases[module] + s->vaddr;
            uint64_t limit = begin + s->memsz;
            if (begin <= address && end <= limit && (s->flags & 4)) within_segment = 1;
        }
        if (!within_segment) return 0;
    }
    while (address < end) {
        unsigned int i;
        for (i = 0; i < map_count; i++) {
            const struct mapping *m = &maps[i];
            int matches_module = module < 0 || m->module == module;
            /* ELF BSS tail pages have no pathname in /proc/PID/maps. Accept
             * them only inside this verified module's PT_LOAD memory extent. */
            if (!matches_module && m->anonymous && module >= 0) {
                for (unsigned int seg = 0; seg < probe_segment_counts[module]; seg++) {
                    const struct load_segment *s = &probe_segments[module][seg];
                    uint64_t bss_page = (uint64_t)biases[module] + (((uint64_t)s->vaddr + s->filesz + 4095u) & ~(uint64_t)4095u);
                    uint64_t limit = (uint64_t)biases[module] + s->vaddr + s->memsz;
                    if (bss_page <= address && end <= limit && (s->flags & 4)) matches_module = 1;
                }
            }
            if (m->readable && matches_module && m->start <= address && address < m->end) {
                address = m->end < end ? m->end : end; break;
            }
        }
        if (i == map_count) return 0;
    }
    return 1;
}

static int memory(uint32_t address, void *out, unsigned int size, int module) {
    if (!readable(address, size, module)) { *__errno_location() = 14; return 0; }
    int count = pread64(mem_fd, out, size, (long long)(unsigned long long)address);
    if (count == (int)size) return 1;
    if (count >= 0) *__errno_location() = 5;
    return 0;
}

static int device_mapping(uint32_t address) {
    for (unsigned int i = 0; i < map_count; i++)
        if (maps[i].start <= address && address < maps[i].end) return device_maps[i] != 0;
    return 0;
}

static int verify_stock(void) {
    char path[96]; unsigned char bytes[4096];
    if (!path_for(path, "exe")) return 0;
    int fd = open(path, 0);
    if (fd < 0) { problem(3, "verification", "executable_open_failed"); return 0; }
    int n = read(fd, bytes, sizeof probe_exe_header); close(fd);
    if (n != (int)sizeof probe_exe_header || memcmp(bytes, probe_exe_header, sizeof probe_exe_header)) {
        problem(2, "verification", "executable_header_mismatch"); return 0;
    }
    for (unsigned int i = 0; i < ARRAY_SIZE(probe_checks); i++) {
        const struct stock_check *check = &probe_checks[i];
        if (check->size > sizeof bytes || biases[check->module] > UINT32_MAX - check->address ||
            !memory(biases[check->module] + check->address, bytes, check->size, (int)check->module)) {
            problem(3, "verification", "stock_function_read_failed"); return 0;
        }
        if (memcmp(bytes, check->bytes, check->size)) { problem(2, "verification", "stock_bytes_mismatch"); return 0; }
        verified_checks++;
    }
    if (biases[2] > UINT32_MAX - PROBE_LENS_STATE_TABLE_ADDRESS ||
        !memory(biases[2] + PROBE_LENS_STATE_TABLE_ADDRESS, bytes, sizeof probe_lens_state_table, 2)) {
        problem(3, "verification", "stock_readiness_table_read_failed"); return 0;
    }
    if (memcmp(bytes, probe_lens_state_table, sizeof probe_lens_state_table)) {
        problem(2, "verification", "stock_readiness_table_mismatch"); return 0;
    }
    lens_table_verified = 1;
    return 1;
}

static uint32_t word(const unsigned char *data, unsigned int at) { uint32_t value; memcpy(&value, data + at, 4); return value; }

/* Exact A32 IsModuleReady switch: ADD wraps at 32 bits, CMP/BHI is unsigned.
 * Both the method and its return table were checked against the stock ELF. */
static uint32_t lens_module_result(uint32_t raw) {
    uint32_t index = raw + 3u;
    return index < ARRAY_SIZE(probe_lens_state_results) ? probe_lens_state_results[index] : 0x20000018u;
}

static int gpio_value(int *raw) {
    char path[80], value = 0;
    int n = snprintf(path, sizeof path, "/sys/class/gpio/gpio%u/value", PROBE_GPIO_NUMBER);
    if (n <= 0 || n >= (int)sizeof path) return 0;
    int fd = open(path, 0x800);
    if (fd < 0) return 0;
    n = read(fd, &value, 1); int error = *__errno_location(); close(fd); *__errno_location() = error;
    if (n != 1 || (value != '0' && value != '1')) return 0;
    *raw = value - '0'; return 1;
}

int probe_main(void) {
    uint32_t iq = 0, pp = 0; unsigned char iq_data[0xfc], pp_data[0x9c];
    uint16_t nodes[5][4]; unsigned int xs[5], ys[5], columns = 0, rows = 0;
    uint32_t wifi_lens[3] = {0,0,0};
    uint32_t lens = 0, lens_raw = 0, lens_result = 0;
    int lens_pointer_read = 0, lens_state_read = 0;
    int wifi_read = 0, cs_device = 0;
    int iq_read = 0, pp_read = 0, cs_read = 0, gpio_known = 0, gpio_raw = -1, identity_rechecked = 0, pointers_rechecked = 0;
    if (!find_process()) goto output;
    if (!process_start(&start_ticks)) { problem(3, "identity", "process_stat_read_failed"); goto output; }
    if (!read_maps()) goto output;
    char path[96];
    if (!path_for(path, "mem")) { problem(2, "memory", "invalid_process_path"); goto output; }
    mem_fd = open(path, 0);
    if (mem_fd < 0) { problem(3, "memory", "process_memory_open_failed"); goto output; }
    if (!verify_stock()) goto output;
    gpio_known = gpio_value(&gpio_raw);
    lens_pointer_read = memory(biases[2] + PROBE_LENS_SINGLETON, &lens, 4, 2);
    if (!lens_pointer_read) problem(3, "lens_operation", "lens_singleton_pointer_read_failed");
    else if (lens) {
        lens_state_read = lens <= UINT32_MAX - PROBE_LENS_STATE_OFFSET &&
                          memory(lens + PROBE_LENS_STATE_OFFSET, &lens_raw, 4, -1);
        if (!lens_state_read) problem(3, "lens_operation", "lens_state_read_failed");
        else lens_result = lens_module_result(lens_raw);
    }
    /* This fixed object is in the executable BSS, labelled [heap] by Linux.
     * The getters are verified above; these are cached values, not live ioctls. */
    wifi_read = memory(PROBE_WIFI_LENS_CACHE, wifi_lens, sizeof wifi_lens, -1);
    if (!wifi_read) problem(3, "wifi_cache", "wifi_lens_cache_read_failed");
    if (!memory(biases[1] + PROBE_IQ_SINGLETON, &iq, 4, 1) || !memory(biases[1] + PROBE_PP_SINGLETON, &pp, 4, 1)) {
        problem(3, "singletons", "singleton_pointer_read_failed"); goto output;
    }
    if (iq) iq_read = memory(iq, iq_data, sizeof iq_data, -1);
    if (pp) pp_read = memory(pp, pp_data, sizeof pp_data, -1);
    if (!iq || !pp) problem(4, "singletons", "singleton_not_initialized");
    else if (!iq_read || !pp_read) problem(3, "singletons", "object_read_failed");
    if (pp_read) {
        unsigned int width = word(pp_data, 0x44) & 0xffffu, height = word(pp_data, 0x48) & 0xffffu;
        columns = (width >> 6) + 1u + ((width & 63u) != 0);
        rows = ((height >> 6) + 2u + ((height & 63u) != 0)) & ~1u;
        uint32_t source = word(pp_data, 0);
        cs_device = device_mapping(source);
        if (pp_data[0x14]) problem(4, "cs", "three_dimensional_mode_not_sampled");
        else if (!(word(pp_data, 0x18) & 1u) || !width || !height || !source) problem(4, "cs", "cs_not_initialized");
        else if (columns > 256 || rows > 256 || columns * rows * 8u > 0x27c00u) problem(4, "cs", "cs_geometry_outside_bound");
        else if (cs_device) { *__errno_location() = 0; problem(4, "cs", "device_cs_buffer_not_observed"); }
        else {
            xs[0] = 0; ys[0] = 0; xs[1] = columns - 1; ys[1] = 0;
            xs[2] = 0; ys[2] = rows - 1; xs[3] = columns - 1; ys[3] = rows - 1;
            xs[4] = columns / 2; ys[4] = rows / 2; cs_read = 1;
            for (unsigned int i = 0; i < 5; i++) {
                unsigned int stored_row = (ys[i] & 1u ? rows / 2 : 0) + ys[i] / 2;
                uint32_t offset = (stored_row * columns + xs[i]) * 8u;
                if (source > UINT32_MAX - offset || !memory(source + offset, nodes[i], 8, -1)) { cs_read = 0; problem(3, "cs", "cs_node_read_failed"); break; }
            }
        }
    }
    uint32_t iq_after = 0, pp_after = 0, lens_after = 0; unsigned long long ticks_after = 0;
    identity_rechecked = executable_matches(pid) && process_start(&ticks_after) && ticks_after == start_ticks;
    if (!identity_rechecked) problem(5, "consistency", "process_identity_changed_or_unreadable");
    pointers_rechecked = memory(biases[1] + PROBE_IQ_SINGLETON, &iq_after, 4, 1) &&
                         memory(biases[1] + PROBE_PP_SINGLETON, &pp_after, 4, 1) &&
                         lens_pointer_read && memory(biases[2] + PROBE_LENS_SINGLETON, &lens_after, 4, 2) &&
                         iq_after == iq && pp_after == pp && lens_after == lens;
    if (!pointers_rechecked) problem(5, "consistency", "singleton_pointer_changed_or_unreadable");
output:
    if (mem_fd >= 0) close(mem_fd);
    printf("{\"schema\":1,\"ok\":%s,\"exit_code\":%d,\"reason\":\"%s\",\"stage\":\"%s\",\"errno\":%d,", result_code ? "false" : "true", result_code, reason, stage, saved_errno);
    printf("\"scope\":\"unlocked_read_only_observation\",\"atomic\":false,\"full_pipeline_consistency_verified\":false,\"hardware_lut_observed\":false,\"physical_color_verified\":false,");
    printf("\"pid\":%u,\"matching_processes\":%u,\"process_start_ticks\":%llu,\"identity_rechecked\":%s,\"singleton_pointers_rechecked\":%s,", pid, process_matches, start_ticks, identity_rechecked ? "true" : "false", pointers_rechecked ? "true" : "false");
    printf("\"verification\":{\"scope\":\"executable_header_and_selected_stock_symbols\",\"checked_symbols\":%u,\"required_symbols\":%u,\"load_biases\":[", verified_checks, (unsigned int)ARRAY_SIZE(probe_checks));
    for (unsigned int i = 0; i < 4; i++) { if (i) printf(","); if (bias_found[i]) printf("\"0x%08x\"", biases[i]); else printf("null"); }
    printf("],\"readiness_table_verified\":%s},\"gpio\":{\"diagnostic_only\":true,\"pin\":%u,\"known\":%s,\"raw\":", lens_table_verified ? "true" : "false", PROBE_GPIO_NUMBER, gpio_known ? "true" : "false");
    if (gpio_known) printf("%d,\"physical_attached\":%s", gpio_raw, gpio_raw ? "false" : "true"); else printf("null,\"physical_attached\":null");
    printf("},\"lens_operation\":{\"address\":\"0x%08x\",\"instance_pointer_read\":%s,\"initialized\":", lens, lens_pointer_read ? "true" : "false");
    if (lens_pointer_read) printf("%s", lens ? "true" : "false"); else printf("null");
    printf(",\"state_offset\":%u,\"state_read\":%s,\"state_raw\":", PROBE_LENS_STATE_OFFSET, lens_state_read ? "true" : "false");
    if (lens_state_read) printf("%u,\"state_signed\":%d,\"is_module_ready_result\":\"0x%08x\",\"electronic_ready\":%s", lens_raw, (int32_t)lens_raw, lens_result, lens_result == 0x20000019u ? "true" : "false");
    else printf("null,\"state_signed\":null,\"is_module_ready_result\":null,\"electronic_ready\":null");
    printf(",\"decode\":\"verified_IsModuleReady_switch_table\"},\"iq\":{\"address\":\"0x%08x\",\"read\":%s", iq, iq_read ? "true" : "false");
    if (iq_read) printf(",\"ready\":%u,\"counts\":[%u,%u,%u],\"distortion\":[\"0x%08x\",\"0x%08x\"],\"shading\":\"0x%08x\",\"shading_3d\":[\"0x%08x\",\"0x%08x\"],\"zoom_type\":%u,\"zoom_point\":%u", iq_data[8], word(iq_data, 0xc), word(iq_data, 0x10), word(iq_data, 0x14), word(iq_data, 0x18), word(iq_data, 0x1c), word(iq_data, 0x20), word(iq_data, 0x24), word(iq_data, 0x28), word(iq_data, 0xf0), word(iq_data, 0xf4));
    printf("},\"pp\":{\"address\":\"0x%08x\",\"read\":%s", pp, pp_read ? "true" : "false");
    if (pp_read) printf(",\"cs_source\":\"0x%08x\",\"flags\":%u,\"mode\":%d,\"mode_word_30\":%d,\"three_dimensional\":%u,\"geometry\":{\"width\":%u,\"height\":%u,\"x\":%u,\"y\":%u},\"lut_mode\":%u", word(pp_data, 0), word(pp_data, 0x18), (int)word(pp_data, 0x2c), (int)word(pp_data, 0x30), pp_data[0x14], word(pp_data, 0x44) & 0xffffu, word(pp_data, 0x48) & 0xffffu, word(pp_data, 0x4c) & 0xffffu, word(pp_data, 0x50) & 0xffffu, word(pp_data, 0x54));
    printf("},\"wifi_lens_cache\":{\"read\":%s,\"authoritative_driver_state\":false", wifi_read ? "true" : "false");
    if (wifi_read) printf(",\"kmount\":%u,\"connection\":%u,\"lock\":%u", wifi_lens[0], wifi_lens[1], wifi_lens[2]);
    printf("},\"cs\":{\"sampled\":%s,\"device_mapping\":%s,\"columns\":%u,\"rows\":%u,\"lane_color_order_verified\":false,\"nodes\":[", cs_read ? "true" : "false", cs_device ? "true" : "false", columns, rows);
    if (cs_read) for (unsigned int i = 0; i < 5; i++) printf("%s{\"x\":%u,\"y\":%u,\"lanes\":[%u,%u,%u,%u]}", i ? "," : "", xs[i], ys[i], nodes[i][0], nodes[i][1], nodes[i][2], nodes[i][3]);
    printf("]}}\n"); fflush((void *)0);
    return result_code;
}

#ifndef NX300M_LENS_PROBE_NO_START
__asm__(".text\n.arm\n.global _start\n.type _start,%function\n_start:\nbl probe_main\nmov r7,#1\nsvc #0\n");
#endif
