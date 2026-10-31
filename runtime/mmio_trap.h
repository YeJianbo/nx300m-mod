/* Linux ARM EABI signal layouts from sys/ucontext.h and bits/sigaction.h.
 * Protect only registered peripheral mappings. Execute their A32 single-memory
 * instruction against a device callback, then resume the original next PC.
 * Other faults and unsupported instructions retain the previous SIGSEGV action.
 */
struct mmio_action {
    union {
        void (*handler)(int, void *, void *);
        void (*simple)(int);
    } callback;
    u32 mask[32];
    int flags;
    void (*restorer)(void);
};
extern int sigaction(int, const struct mmio_action *, struct mmio_action *);
struct mmio_context {
    u32 flags, link, stack_pointer, stack_flags, stack_size;
    u32 trap_no, error_code, oldmask;
    u32 registers[16], cpsr, fault_address;
};
typedef int (*mmio_callback)(int, u32, int, int, u32 *);
struct mmio_region { u32 base, size; int device; mmio_callback access; };
static struct mmio_region mmio_regions[32];
static struct mmio_action mmio_previous_action;
static int mmio_installed;

static u32 mmio_shift(u32 value, u32 type, u32 amount, u32 cpsr) {
    if (type == 0) return value << amount;
    if (type == 1) return amount ? value >> amount : 0;
    if (type == 2) return (u32)((int)value >> (amount ? amount : 31));
    if (!amount) return (value >> 1) | ((cpsr & (1u << 29)) << 2);
    return (value >> amount) | (value << (32-amount));
}

static int mmio_instruction(struct mmio_context *context, struct mmio_region *region, u32 fault) {
    u32 *r = context->registers, pc = r[15];
    if (context->cpsr & 0x20) return 0; /* Thumb is not an A32 instruction. */
    u32 instruction = *(const u32 *)pc;
    u32 rn = (instruction >> 16) & 15, rd = (instruction >> 12) & 15;
    u32 displacement;
    int width, sign = 0, load = !!(instruction & (1u << 20));
    if ((instruction & 0x0c000000) == 0x04000000) {
        width = instruction & (1u << 22) ? 1 : 4;
        if (instruction & (1u << 25)) {
            if (instruction & 0x10 || (instruction & 15) == 15) return 0;
            displacement = mmio_shift(r[instruction & 15], (instruction >> 5) & 3,
                                      (instruction >> 7) & 31, context->cpsr);
        } else displacement = instruction & 0xfff;
    } else if ((instruction & 0x0e000090) == 0x90) {
        u32 sh = (instruction >> 5) & 3;
        if (!sh || (!load && sh != 1)) return 0; /* Multiply/LDRD/STRD are not single accesses. */
        width = sh == 2 ? 1 : 2;
        sign = sh != 1;
        if (instruction & (1u << 22)) displacement = ((instruction >> 4) & 0xf0) | (instruction & 15);
        else {
            if ((instruction & 15) == 15) return 0;
            displacement = r[instruction & 15];
        }
    } else return 0;
    if (rd == 15) return 0;
    u32 base = rn == 15 ? pc+8 : r[rn];
    u32 indexed = instruction & (1u << 23) ? base+displacement : base-displacement;
    int pre = !!(instruction & (1u << 24));
    int writeback = !pre || !!(instruction & (1u << 21));
    if (writeback && (rn == 15 || (load && rn == rd))) return 0;
    u32 address = pre ? indexed : base;
    if ((u32)width > region->size || address != fault || address < region->base ||
        address-region->base > region->size-(u32)width) return 0;
    u32 value = r[rd];
    if (!region->access(region->device, address-region->base, width, !load, &value)) return 0;
    if (load) {
        if (width < 4) {
            u32 bits = (u32)width*8;
            value &= (1u << bits)-1;
            if (sign) value = (u32)((int)(value << (32-bits)) >> (32-bits));
        }
        r[rd] = value;
    }
    if (writeback) r[rn] = indexed;
    r[15] = pc+4;
    return 1;
}

static void mmio_fault(int signal, void *information, void *saved) {
    u32 fault = ((u32 *)information)[3]; /* siginfo_t.si_addr, ARM EABI. */
    struct mmio_context *context = saved;
    for (unsigned int i = 0; i < 32; ++i) {
        struct mmio_region *region = mmio_regions+i;
        if (region->base && fault >= region->base && fault-region->base < region->size) {
            if (mmio_instruction(context, region, fault)) return;
            event("unsupported MMIO instruction", fault, context->registers[15],
                  *(const u32 *)context->registers[15]);
            break;
        }
    }
    if ((u32)mmio_previous_action.callback.handler > 1) {
        if (mmio_previous_action.flags & 4) mmio_previous_action.callback.handler(signal, information, saved);
        else mmio_previous_action.callback.simple(signal);
        return;
    }
    /* Restore before returning. The unchanged original instruction faults
     * again, allowing the default core behavior to run. */
    sigaction(signal, &mmio_previous_action, (void *)0);
    mmio_installed = 0;
}

static int mmio_register(void *address, u32 size, int device, mmio_callback access) {
    if (!mmio_installed) {
        struct mmio_action action = {{mmio_fault}, {0}, 0x10000004, (void *)0};
        if (sigaction(11, &action, &mmio_previous_action)) return -1;
        mmio_installed = 1;
    }
    for (unsigned int i = 0; i < 32; ++i) if (!mmio_regions[i].base) {
        mmio_regions[i].size = size;
        mmio_regions[i].device = device;
        mmio_regions[i].access = access;
        __sync_synchronize();
        mmio_regions[i].base = (u32)address;
        if (checked(raw(125, (long)address, (size+4095u)&~4095u, 0, 0, 0, 0))) {
            mmio_regions[i].base = 0; return -1;
        }
        return 0;
    }
    return error(12);
}
static void mmio_forget(void *address, u32 size) {
    unsigned long long start = (u32)address, end = start+size;
    for (unsigned int i = 0; i < 32; ++i) {
        struct mmio_region *region = mmio_regions+i;
        if (region->base && start < (unsigned long long)region->base+region->size && region->base < end)
            region->base = 0;
    }
}
