#ifndef NX_SMARTFILTER_IPCS_H
#define NX_SMARTFILTER_IPCS_H
#include "smartfilter_lut_bridge.h"

#define NX_SF_IPCS_SEMAPHORES 4U
#define NX_SF_ADE_SWITCHES 6U

/* Addresses are bound from the original 1.15 ELF, never guessed MMIO.
 * The four semaphores coordinate the original B2Y and Resize wrappers;
 * participation by other IPCS clients still requires on-device tracing. */
struct nx_sf_ipcs_api {
    void *semaphore[NX_SF_IPCS_SEMAPHORES];
    int (*trywait)(void *);
    int (*post)(void *);
    int (*open)(void);
    int (*close)(void);
    int (*reset)(void);
    void (*ade_init)(void);
    void (*write_lut)(unsigned, const unsigned short *);
    int (*ycc)(void *);
    void (*iq_initialize)(int);
    void (*iq_process)(int);
    unsigned (*width)(void *), (*height)(void *), (*stride)(void *);
    unsigned (*sampling)(void *), (*address)(void *, unsigned);
    int (*flush)(void *, unsigned, unsigned);
    unsigned char *ade_switch[NX_SF_ADE_SWITCHES];
    unsigned rgb3d_index;
};

struct nx_sf_ipcs {
    struct nx_sf_ipcs_api api;
    struct nx_sf_bridge *bridge;
    unsigned held, opened, touched;
    unsigned char saved_switch[NX_SF_ADE_SWITCHES];
    unsigned transactions, uploads, operations, reloads, busy, failures;
    int last_error;
};

int nx_sf_ipcs_init(struct nx_sf_ipcs *, const struct nx_sf_ipcs_api *, struct nx_sf_bridge *);
int nx_sf_ipcs_begin(void *);
int nx_sf_ipcs_render(void *, void *, const unsigned short *);
int nx_sf_ipcs_restore(void *);
void nx_sf_ipcs_end(void *);
int nx_sf_ipcs_passthrough(void *, void *);
#endif
