#ifndef NX_IPCS_CALLTRACE_H
#define NX_IPCS_CALLTRACE_H
#define NX_IPCT_RECORDS 64U
#define NX_IPCT_SCOPES 4U
enum nx_ipct_phase { NX_IPCT_TOTAL, NX_IPCT_MUTEX_OUTER, NX_IPCT_MUTEX_UDD, NX_IPCT_UDD,
                     NX_IPCT_DEVICE_OPEN, NX_IPCT_REGMAP, NX_IPCT_IRQ,
                     NX_IPCT_INIT, NX_IPCT_PHASES };
enum nx_ipct_role { NX_IPCT_PREVIEW=1, NX_IPCT_SESSION=2 };
struct nx_ipct_time { unsigned sec,nsec; };
struct nx_ipct_clock { struct nx_ipct_time wall,cpu; unsigned valid; };
struct nx_ipct_phase_data {
    unsigned wall_us,cpu_us,count,valid;
    int rc;
    unsigned argument;
};
struct nx_ipct_record {
    volatile unsigned committed;
    unsigned sequence,tid,role,frame,udd_count_before,udd_count_after;
    struct nx_ipct_clock start;
    struct nx_ipct_time end;
    struct nx_ipct_phase_data phase[NX_IPCT_PHASES];
};
struct nx_ipct_scope { volatile unsigned tid; struct nx_ipct_record *record; };
struct nx_ipct {
    volatile unsigned enabled,next,dropped;
    struct nx_ipct_scope scopes[NX_IPCT_SCOPES];
    struct nx_ipct_record records[NX_IPCT_RECORDS];
};
struct nx_ipct_mark { struct nx_ipct_clock clock; struct nx_ipct_record *record; unsigned phase; };
struct nx_ipct_api { unsigned (*tid)(void); int (*clock)(unsigned,struct nx_ipct_time *); };
void nx_ipct_start(struct nx_ipct *,const struct nx_ipct_api *,unsigned,unsigned,unsigned);
struct nx_ipct_mark nx_ipct_enter(struct nx_ipct *,const struct nx_ipct_api *,unsigned,unsigned);
void nx_ipct_leave(const struct nx_ipct_api *,struct nx_ipct_mark,int);
void nx_ipct_finish(struct nx_ipct *,const struct nx_ipct_api *,int,unsigned);
#endif
