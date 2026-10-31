#ifndef NX300M_MOD_PATHS_H
#define NX300M_MOD_PATHS_H

extern char *getenv(const char *);

/* Selection files contain whitespace-delimited paths. Keep the SD directory
 * representable there and leave room for a 63-byte look ID in a 256-byte path. */
static int nx_mod_root_valid(const char *root) {
    if (!root || root[0] != '/') return 0;
    unsigned int length = 0, component = 0;
    for (const char *p = root; *p; p++, length++) {
        unsigned char c = (unsigned char)*p;
        if (length >= 160 || !((c >= 'a' && c <= 'z') ||
            (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') ||
            c == '/' || c == '_' || c == '-' || c == '.')) return 0;
        if (c == '/') {
            if (p != root && (!component || (component == 1 && p[-1] == '.') ||
                (component == 2 && p[-1] == '.' && p[-2] == '.'))) return 0;
            component = 0;
        } else component++;
    }
    if (length < 2 || !component || (component == 1 && root[length-1] == '.') ||
        (component == 2 && root[length-1] == '.' && root[length-2] == '.')) return 0;
    return 1;
}

static const char *nx_mod_root(void) {
    const char *root = getenv("NX300M_MOD_ROOT");
    if (!root || !*root) return "/mod";
    return nx_mod_root_valid(root) ? root : (const char *)0;
}

#endif
