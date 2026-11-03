/* Control-process only: bounded snapshots are encoded after safe detach. */
#ifndef NX_SMARTFILTER_TIMING_EXPORT_H
#define NX_SMARTFILTER_TIMING_EXPORT_H
#include <stdarg.h>
#include "smartfilter_persistence.h"
#include "smartfilter_timing.h"
#define NX_SFT_CONTROLS 128U
#define NX_SFT_LEDGER_MAGIC 0x43544653U
struct nx_sft_control {
    unsigned epoch, complete;
    struct nx_sft_time begin, end;
};
struct nx_sft_ledger {
    unsigned magic, pid, build, count, complete;
    unsigned long long start;
    struct nx_sft_control controls[NX_SFT_CONTROLS];
};
static struct nx_sft_time diag_now(void) {
    struct timespec t; struct nx_sft_time value={0,0,0};
    if(!syscall(263,CLOCK_MONOTONIC,&t)&&t.tv_sec>=0&&t.tv_nsec>=0&&t.tv_nsec<1000000000L) {
        value.sec=(unsigned)t.tv_sec; value.nsec=(unsigned)t.tv_nsec; value.valid=1;
    }
    return value;
}
static void diag_ledger_path(char *path,unsigned size,unsigned pid,unsigned long long start) {
    snprintf(path,size,"/var/run/nx300m-sf-controls-%u-%llu.bin",pid,start);
}
static void diag_ledger_load(struct nx_sft_ledger *l,unsigned pid,unsigned long long start,unsigned build) {
    char path[128]; memset(l,0,sizeof *l);
    l->magic=NX_SFT_LEDGER_MAGIC; l->pid=pid; l->start=start; l->build=build; l->complete=1;
    diag_ledger_path(path,sizeof path,pid,start);
    int fd=open(path,O_RDONLY|O_CLOEXEC|O_NOFOLLOW);
    if(fd<0) { if(errno!=ENOENT) l->complete=0; return; }
    struct nx_sft_ledger old;
    int ok=diag_regular(fd,sizeof old)&&read(fd,&old,sizeof old)==sizeof old;
    close(fd);
    if(!ok||old.magic!=l->magic||old.pid!=pid||old.start!=start||old.build!=build||old.count>NX_SFT_CONTROLS) {
        l->complete=0; return;
    }
    *l=old;
}
static int diag_ledger_save(struct nx_sft_ledger *l,struct nx_sft_time begin,
                            struct nx_sft_time end,unsigned epoch) {
    char path[128]; diag_ledger_path(path,sizeof path,l->pid,l->start);
    if(l->count<NX_SFT_CONTROLS) {
        struct nx_sft_control *c=&l->controls[l->count++];
        c->begin=begin; c->end=end; c->epoch=epoch;
        c->complete=begin.valid&&end.valid&&(end.sec>begin.sec||(end.sec==begin.sec&&end.nsec>=begin.nsec));
        if(!c->complete) l->complete=0;
    } else l->complete=0;
    int fd=open(path,O_RDWR|O_CREAT|O_CLOEXEC|O_NOFOLLOW,0600);
    if(fd<0) { l->complete=0; return 0; }
    int ok=diag_regular(fd,sizeof *l)&&!flock(fd,LOCK_EX|LOCK_NB)&&
        pwrite64(fd,l,sizeof *l,0)==sizeof *l&&!ftruncate(fd,sizeof *l);
    if(close(fd)) ok=0;
    if(!ok) l->complete=0;
    return ok;
}
struct nx_sft_writer { FILE *file; struct sha hash; unsigned bytes,seq; int error; };
static void diag_line(struct nx_sft_writer *w,int payload,const char *format,...) {
    char line[4096]; va_list args; va_start(args,format);
    int n=vsnprintf(line,sizeof line,format,args); va_end(args);
    if(n<0||n>4094) {
        diag_save_logic(&diag_save_result,"snapshot","format",n<0?"format_failed":"line_too_long");
        w->error=1; return;
    }
    line[n++]='\n';
    if(!diag_save_io(&diag_save_result,"snapshot","fwrite",fwrite(line,1,(unsigned)n,w->file),(unsigned)n)) {
        w->error=1; return;
    }
    if(payload) { sha_feed(&w->hash,line,(unsigned)n); w->bytes+=(unsigned)n; ++w->seq; }
}
static void diag_add(char *line,unsigned *used,int *error,const char *format,...) {
    if(*error) return;
    va_list args; va_start(args,format);
    int n=vsnprintf(line+*used,4095-*used,format,args); va_end(args);
    if(n<0||(unsigned)n>=4095-*used) {
        diag_save_logic(&diag_save_result,"snapshot","format",n<0?"format_failed":"line_too_long");
        *error=1; return;
    }
    *used+=(unsigned)n;
}
static const char *diag_stage_name(unsigned stage) {
    static const char *names[]={"preview","effect","ensure","initialize","select","begin",
        "try0","try1","try2","try3","geometry","open","ade_init","lut0","lut1","lut2",
        "flush_in","ycc","flush_out","reset","iq_init","iq_process","close","unlock",
        "copy","drain","prepare","finish","calibration"};
    return stage<NX_SFT_STAGES?names[stage]:"unknown";
}
static const char *diag_kind(unsigned kind) {
    return kind==1?"frame":kind==2?"prepare":kind==3?"finish":kind==4?"calibration":"initialize";
}
static const char *diag_path(unsigned path) {
    if(path==NX_SFT_PATH_RENDER) return "render";
    if(path==NX_SFT_PATH_FALLBACK) return "fallback";
    return (path>=NX_SFT_PATH_STRENGTH_ZERO&&path<=NX_SFT_PATH_FAULT)||path==NX_SFT_PATH_NO_CONTEXT||
        path==NX_SFT_PATH_PROBE_EXHAUSTED||path==NX_SFT_PATH_PROBE_BLOCKED?"passthrough":"unknown";
}
static void diag_time_json(char text[64],const struct nx_sft_time *t) {
    if(t->valid) snprintf(text,64,"[%u,%u]",t->sec,t->nsec);
    else strcpy(text,"null");
}
static void diag_record(struct nx_sft_writer *w,const char *id,const struct nx_sft_record *r) {
    if(r->state==NX_SFT_EMPTY) return;
    char line[4096],begin[64],end[64],current[64]; unsigned used=0; int error=0;
    diag_time_json(begin,&r->start); diag_time_json(end,&r->end); diag_time_json(current,&r->current_time);
    diag_add(line,&used,&error,"{\"type\":\"sf_diag_record\",\"dump_id\":\"%s\",\"seq\":%u,\"kind\":\"%s\","
        "\"state\":\"%s\",\"tid\":%u,\"generation\":%u,\"strength\":%u,\"epoch_begin\":%u,\"epoch_end\":%u,"
        "\"geometry\":[%u,%u,%u,%u],\"begin\":%s,\"end\":%s,\"current_stage\":%u,\"current_at\":%s,"
        "\"flags\":%u,\"path\":\"%s\",\"reason\":%u,\"fault\":%u,\"rc\":%d,\"stage_mask\":%u,\"invalid_mask\":%u,"
        "\"self\":%u,\"captures_begin\":%u,\"captures_end\":%u,\"stages\":{",
        id,w->seq,diag_kind(r->kind),r->state==NX_SFT_COMMITTED?"committed":"pending",r->tid,r->generation,r->strength,
        r->epoch_begin,r->epoch_end,r->width,r->height,r->stride,r->sampling,begin,end,r->current_stage,current,
        r->flags,diag_path(r->path),r->path,r->fault,r->rc,r->stage_mask,r->invalid_mask,r->self,r->captures_begin,r->captures_end);
    int comma=0;
    for(unsigned i=0;i<NX_SFT_STAGES;++i) if((r->valid_mask&(1U<<i))&&r->count[i]) {
        diag_add(line,&used,&error,"%s\"%s\":{\"sec\":%u,\"nsec\":%u,\"calls\":%u,\"rc\":%d}",
            comma?",":"",diag_stage_name(i),r->duration[i].sec,r->duration[i].nsec,r->count[i],r->stage_rc[i]); comma=1;
    }
    diag_add(line,&used,&error,"}}");
    if(error) w->error=1; else diag_line(w,1,"%s",line);
}
static const char *diag_log_path;
static const char *diag_command="diag-export";
static int diag_receipt(const char *id,const char *token,const char *path,int reused,unsigned captures,unsigned active,unsigned inflight) {
    if(!diag_frozen_hash_result(path,0,&diag_save_result)) return 0;
    int safe=!captures&&!active&&!inflight,persisted=0;
    if(diag_log_path&&safe) {
        if(!diag_persist(path,diag_log_path,0,&diag_save_result)) return 0;
        persisted=1;
    }
    printf("{\"command\":\"%s\",\"status\":\"%s\",\"token\":\"%s\",\"snapshot_created\":true,"
        "\"dump_id\":\"%s\",\"diag_file\":\"%s\",\"reused\":%s,\"safe_to_persist\":%s,\"persisted\":%s",
        diag_command,persisted?"persisted":"ram_only",token,id,path,reused?"true":"false",safe?"true":"false",persisted?"true":"false");
    diag_save_fields(stdout,&diag_save_result); fputs("}\n",stdout);
    return 1;
}
/* This helper accepts only a deterministic root-owned RAM path, never a target
 * address or user-selected output pathname. Existing frozen dumps are retained. */
static int diag_export(unsigned pid,unsigned long long start,unsigned build,int fixture,
                       const struct nx_sfr_config *cfg,const struct nx_sfr_runtime *runtime,
                       const struct nx_sft_ledger *l,struct nx_sft_time snapshot) {
    const struct nx_sft_timing *t=&runtime->timing;
    const struct nx_sfr_probe *p=&runtime->probe;
    unsigned captures=runtime->captures,active=runtime->backend_active,inflight=runtime->inflight;
    char id[128],token[128],path[192],temp[208],stamp[64];
    snprintf(token,sizeof token,"%u:%llu:%u:%u:%u",pid,start,build,t->window,cfg->generation);
    snprintf(id,sizeof id,"%u-%llu-%u-%u",pid,start,build,t->window);
    snprintf(path,sizeof path,"/var/run/nx300m-sf-diag-%s.ndjson",id);
    snprintf(temp,sizeof temp,"%s.tmp",path);
    int existing=open(path,O_RDONLY|O_CLOEXEC|O_NOFOLLOW);
    if(existing>=0) {
        int ok=diag_regular_result(existing,1048576,"snapshot",&diag_save_result); close(existing);
        if(!ok) return 0;
        return diag_receipt(id,token,path,1,captures,active,inflight);
    }
    if(errno!=ENOENT) return diag_save_system(&diag_save_result,"snapshot","open");
    int fd=open(temp,O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC|O_NOFOLLOW,0600);
    if(fd<0) return diag_save_system(&diag_save_result,"snapshot","open");
    FILE *file=fdopen(fd,"w");
    if(!file) { diag_save_system(&diag_save_result,"snapshot","fdopen"); close(fd); unlink(temp); return 0; }
    struct nx_sft_writer w={file,{{0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19},{0},0,0},0,0,0};
    diag_time_json(stamp,&snapshot);
    diag_line(&w,0,"{\"type\":\"sf_diag_begin\",\"schema\":%u,\"dump_id\":\"%s\",\"pid\":%u,\"start\":%llu,\"build\":%u,"
        "\"window\":%u,\"look_id\":\"%s\",\"generation\":%u,\"strength\":%u,\"clock\":\"CLOCK_MONOTONIC\",\"unit\":\"sec_nsec\","
        "\"measurement_source\":\"%s\",\"dropped\":%u,\"clock_errors\":%u,\"control_intervals_complete\":%s,"
        "\"snapshot_at\":%s,\"frozen\":true,\"captures\":%u,\"backend_active\":%u,\"inflight\":%u,"
        "\"probe\":{\"mode\":%u,\"state\":%u,\"slot\":%u,\"outcome\":%u,\"claimed\":%s,\"returned\":%s,"
        "\"prepare_baseline\":%u,\"finish_baseline\":%u}}",
        NX_SFT_SCHEMA,id,pid,start,build,t->window,t->look_id,t->generation,t->strength,fixture?"vm":"camera",
        t->dropped,t->clock_errors,l->complete?"true":"false",stamp,captures,active,inflight,
        cfg->probe_once,p->state,p->slot,p->outcome,
        p->state==NX_SFR_PROBE_CLAIMED||p->state==NX_SFR_PROBE_RETURNED?"true":"false",
        p->state==NX_SFR_PROBE_RETURNED?"true":"false",p->prepare,p->finish);
    for(unsigned i=0;i<NX_SFT_FRAMES;++i) diag_record(&w,id,&t->frames[i]);
    for(unsigned i=0;i<NX_SFT_CAPTURES;++i) diag_record(&w,id,&t->captures[i]);
    diag_record(&w,id,&t->calibration);
    for(unsigned i=0;i<l->count;++i) {
        char begin[64],end[64]; diag_time_json(begin,&l->controls[i].begin); diag_time_json(end,&l->controls[i].end);
        diag_line(&w,1,"{\"type\":\"sf_diag_control\",\"dump_id\":\"%s\",\"seq\":%u,\"epoch\":%u,\"begin\":%s,\"end\":%s,\"complete\":%s}",
            id,w.seq,l->controls[i].epoch,begin,end,l->controls[i].complete?"true":"false");
    }
    char hash[65]; diag_sha_hex(&w.hash,hash);
    diag_line(&w,0,"{\"type\":\"sf_diag_end\",\"schema\":%u,\"dump_id\":\"%s\",\"records\":%u,\"bytes\":%u,\"sha256\":\"%s\"}",
        NX_SFT_SCHEMA,id,w.seq,w.bytes,hash);
    int ok=!w.error;
    if(ok&&fflush(file)) ok=diag_save_system(&diag_save_result,"snapshot","fflush");
    if(ok&&fsync(fd)) ok=diag_save_system(&diag_save_result,"snapshot","fsync");
    if(fclose(file)) { diag_save_system(&diag_save_result,"snapshot","fclose"); ok=0; }
    if(ok&&rename(temp,path)) ok=diag_save_system(&diag_save_result,"snapshot","rename");
    if(!ok) { unlink(temp); return 0; }
    if(!diag_frozen_hash_result(path,1,&diag_save_result)) return 0;
    return diag_receipt(id,token,path,0,captures,active,inflight);
}
#endif
