/* Loader-only probe identity and explicitly acknowledged evidence persistence.
 * This is not a claim that capture counters prove global media quiescence. */
#ifndef NX_SMARTFILTER_PROBE_CONTROL_H
#define NX_SMARTFILTER_PROBE_CONTROL_H
struct nx_sfr_expect {
    unsigned pid,build,window,generation;
    unsigned long long start;
    int present;
};
static struct nx_sfr_expect probe_expect;
static int probe_once_requested,probe_contract_ack;
static const char *probe_file_path,*probe_source_id;
static int probe_number(const char **text,unsigned long long *value,unsigned long long limit,char end) {
    const char *p=*text; unsigned long long n=0;
    if(*p<'0'||*p>'9') return 0;
    do { unsigned digit=(unsigned)(*p-'0'); if(n>(limit-digit)/10) return 0; n=n*10+digit; ++p; }
    while(*p>='0'&&*p<='9');
    if(*p!=end) return 0;
    *value=n; *text=p+(end!=0); return 1;
}
static int probe_parse_expect(const char *text) {
    unsigned long long pid,start,build,window,generation;
    if(!probe_number(&text,&pid,UINT32_MAX,':')||!pid||
       !probe_number(&text,&start,UINT64_MAX,':')||
       !probe_number(&text,&build,UINT32_MAX,':')||!build||
       !probe_number(&text,&window,UINT32_MAX,':')||!window||
       !probe_number(&text,&generation,UINT32_MAX,0)||!generation) return 0;
    probe_expect=(struct nx_sfr_expect){(unsigned)pid,(unsigned)build,(unsigned)window,(unsigned)generation,start,1};
    return 1;
}
static void probe_token(char token[128],unsigned pid,unsigned long long start,unsigned build,unsigned window,unsigned generation) {
    snprintf(token,128,"%u:%llu:%u:%u:%u",pid,start,build,window,generation);
}
static int probe_expect_matches(unsigned pid,unsigned long long start,unsigned build,
                                const struct nx_sfr_config *cfg,const struct nx_sfr_runtime *runtime) {
    return !probe_expect.present||(probe_expect.pid==pid&&probe_expect.start==start&&probe_expect.build==build&&
        probe_expect.window==runtime->timing.window&&probe_expect.generation==cfg->generation);
}
static void probe_failure(const char *command,const char *status,int snapshot) {
    printf("{\"command\":\"%s\",\"status\":\"%s\",\"snapshot_created\":%s,\"persisted\":false",
        command,status,snapshot?"true":"false");
    diag_save_fields(stdout,&diag_save_result); fputs("}\n",stdout);
    if(diag_save_result.stage) diag_save_stderr(&diag_save_result);
    else {
        fputs("Diagnostic save refused: command=",stderr); diag_save_quote(stderr,command,32);
        fputs(" status=",stderr); diag_save_quote(stderr,status,48); fputc('\n',stderr);
    }
}
static void probe_identity(unsigned short table[NX_SF_TABLE_WORDS]) {
    for(unsigned c=0;c<3;++c) {
        for(unsigned i=0;i<729;++i) {
            unsigned coord=c==0?i/81U:c==1?(i/9U)%9U:i%9U;
            table[c*NX_SF_CHANNEL_WORDS+i]=(unsigned short)(coord*1023U/8U);
        }
        table[c*NX_SF_CHANNEL_WORDS+729]=0;
    }
}
static void probe_frozen_path(char path[192],unsigned pid,unsigned long long start,unsigned build,unsigned window) {
    snprintf(path,192,"/var/run/nx300m-sf-diag-%u-%llu-%u-%u.ndjson",pid,start,build,window);
}
static int probe_source_identity(struct nx_sfm_identity *identity) {
    const char *p=probe_source_id;
    return p&&probe_number(&p,&identity->device,UINT64_MAX,':')&&probe_number(&p,&identity->inode,UINT64_MAX,0);
}
static int probe_destination(int fixture) {
    const char *prefix=fixture?"/perf/sf-probe-":"/mnt/mmc/nx300m-mod/logs/sf-probe-";
    if(!probe_file_path||strncmp(probe_file_path,prefix,strlen(prefix))) return 0;
    const char *hex=probe_file_path+strlen(prefix);
    if(strlen(hex)!=39||strcmp(hex+32,".ndjson")) return 0;
    for(unsigned i=0;i<32;++i) if(!((hex[i]>='0'&&hex[i]<='9')||(hex[i]>='a'&&hex[i]<='f'))) return 0;
    return 1; /* Actual source identity is checked on the held openat root FD. */
}
static int probe_save(unsigned pid,unsigned long long start,unsigned build,int fixture,
                      const struct nx_sfr_config *cfg,const struct nx_sfr_runtime *runtime) {
    char path[192],token[128];
    probe_token(token,pid,start,build,runtime->timing.window,cfg->generation);
    probe_frozen_path(path,pid,start,build,runtime->timing.window);
    if(!probe_contract_ack||!probe_expect.present||!cfg->probe_once||!runtime->timing.frozen||
       !probe_expect_matches(pid,start,build,cfg,runtime)) {
        probe_failure("probe-save","probe_contract_missing",0); return 0;
    }
    if(!diag_frozen_hash_result(path,0,&diag_save_result)) { probe_failure("probe-save","no_valid_snapshot",0); return 0; }
    if(runtime->captures||runtime->prepare_calls!=runtime->probe.prepare||runtime->finish_calls!=runtime->probe.finish) {
        probe_failure("probe-save","capture_observed",1); return 0;
    }
    struct nx_sfm_identity expected;
    if(!probe_destination(fixture)||!probe_source_identity(&expected)) { probe_failure("probe-save","media_identity_changed",1); return 0; }
    if(!diag_persist_expected(path,probe_file_path,1,&diag_save_result,&expected)) {
        int identity=diag_save_result.stage&&!strcmp(diag_save_result.stage,"media_identity");
        probe_failure("probe-save",identity?"media_identity_changed":"persistence_failed",1); return 0;
    }
    printf("{\"command\":\"probe-save\",\"status\":\"persisted\",\"token\":\"%s\","
        "\"probe_contract\":\"idle-no-capture\",\"observed_capture_unchanged\":true,"
        "\"backend_pending\":%s,\"snapshot_created\":true,\"persisted\":true,\"probe_file\":\"%s\"",
        token,runtime->backend_active?"true":"false",probe_file_path);
    diag_save_fields(stdout,&diag_save_result); fputs("}\n",stdout);
    return 1;
}
#endif
