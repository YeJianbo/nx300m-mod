/* Bounded, read-only syscall observation. No IPCS call or target write is
 * injected. These stops must be excluded from normal performance statistics. */
#ifndef NX_IPCS_KDD_PROBE_H
#define NX_IPCS_KDD_PROBE_H
#include <sys/ioctl.h>
#define NX_KDD_PID_COUNT 100U
#define NX_KDD_SAMPLES 6U
struct nx_kdd_list { int dev,count,pid[NX_KDD_PID_COUNT]; };
struct nx_kdd_sample {
    unsigned syscall,fd,completed;
    int result;
    struct nx_sft_time begin,end;
    struct nx_kdd_list before,after;
};
struct nx_kdd_probe {
    unsigned pid,tid,fixture,samples,opens,detached,steps,attached;
    unsigned build,generation,window,wait_ms,preflight_ms,first_frame_ready;
    unsigned long long start;
    const char *stage;
    int error,opener_errno;
    struct nx_sft_time begin,end;
    struct nx_kdd_sample call[NX_KDD_SAMPLES];
};
static int kdd_snapshot(int fd,int fixture,struct nx_kdd_list *list) {
    memset(list,0,sizeof *list); list->dev=6; list->count=-1;
    if(fixture) { list->count=0; return 1; }
    /* Verified stock opener_ioctl: GET_PID_LIST copies count + owner list
     * under the KDD mutex. RESET_OPEN_CNT (0x4004620b) is never issued. */
    if(ioctl(fd,0x4198620dU,list)||list->dev!=6||list->count<0||list->count>100) return 0;
    return 1;
}
static void kdd_list_export(FILE *out,const struct nx_kdd_list *p) {
    fprintf(out,"{\"count\":%d,\"owners\":[",p->count);
    unsigned n=0;
    for(unsigned i=0;i<NX_KDD_PID_COUNT;++i) if(p->pid[i]) {
        fprintf(out,"%s%d",n?",":"",p->pid[i]); ++n;
    }
    fputs("]}",out);
}
static void kdd_export(FILE *out,const struct nx_kdd_probe *p) {
    fprintf(out,"{\"type\":\"ipcs_kdd_window\",\"measurement_source\":\"%s\",\"pid\":%u,\"tid\":%u,"
            "\"detached\":%s,\"error\":%d,\"opener_errno\":%d,\"steps\":%u,"
            "\"stage\":\"%s\",\"start\":%llu,\"build\":%u,\"generation\":%u,\"window\":%u,"
            "\"timing_perturbed\":%s,\"kernel_branch_probed\":false,\"preflight_ms\":%u,\"identity_reused\":true,"
            "\"begin\":[%u,%u],\"end\":[%u,%u]}\n",
            p->fixture?"arm_fixture":"physical_camera",p->pid,p->tid,p->detached?"true":"false",
            p->error,p->opener_errno,p->steps,p->stage,p->start,p->build,p->generation,p->window,
            p->attached?"true":"false",p->preflight_ms,p->begin.sec,p->begin.nsec,p->end.sec,p->end.nsec);
    for(unsigned i=0;i<p->samples;++i) {
        const struct nx_kdd_sample *r=&p->call[i];
        fprintf(out,"{\"type\":\"ipcs_kdd_call\",\"tid\":%u,\"syscall\":%u,\"fd\":%u,"
                "\"completed\":%s,\"result\":%d,\"begin\":[%u,%u],\"end\":[%u,%u],\"before\":",
                p->tid,r->syscall,r->fd,r->completed?"true":"false",r->result,
                r->begin.sec,r->begin.nsec,r->end.sec,r->end.nsec);
        kdd_list_export(out,&r->before); fputs(",\"after\":",out); kdd_list_export(out,&r->after);
        fputs("}\n",out);
    }
}
static void kdd_ready_export(FILE *out,const struct nx_kdd_probe *p) {
    char token[128]; probe_token(token,p->pid,p->start,p->build,p->window,p->generation);
    fprintf(out,"{\"type\":\"ipcs_kdd_ready\",\"status\":\"%s\",\"stage\":\"%s\",\"error\":%d,"
           "\"pid\":%u,\"tid\":%u,\"token\":\"%s\",\"wait_ms\":%u,\"timing_perturbed\":false}\n",
           p->first_frame_ready?"ready":"not_ready",p->first_frame_ready?"first_frame_ready":p->stage,
           p->first_frame_ready?0:p->error,p->pid,p->tid,token,p->wait_ms);
}
static int kdd_persist(const struct nx_kdd_probe *p) {
    kdd_export(stdout,p);
    if(!diag_log_path) return 1;
    char path[]="/var/run/nx300m-sf-diag-kdd-XXXXXX";
    int fd=mkstemp(path); if(fd<0) return 0;
    FILE *out=fdopen(fd,"w"); if(!out) { close(fd); return 0; }
    kdd_ready_export(out,p); kdd_export(out,p);
    int ok=!ferror(out);
    if(fflush(out)||fsync(fd)) ok=0;
    if(fclose(out)) ok=0;
    struct nx_sfm_identity source;
    const char *id=getenv("NX300M_MOD_SOURCE_ID");
    int bound=id&&probe_number(&id,&source.device,UINT64_MAX,':')&&probe_number(&id,&source.inode,UINT64_MAX,0);
    int saved=ok&&bound&&diag_persist_expected(path,diag_log_path,0,&diag_save_result,&source);
    printf("{\"command\":\"kdd-probe\",\"persisted\":%s,\"detached\":%s",
           saved?"true":"false",p->detached?"true":"false");
    diag_save_fields(stdout,&diag_save_result); puts("}");
    return saved;
}
static int kdd_path(int mem,unsigned address,const char *expected) {
    char path[32]; unsigned length=(unsigned)strlen(expected)+1;
    return length<=sizeof path&&read_exact(mem,path,length,address)&&!memcmp(path,expected,length);
}
static int kdd_fail(struct nx_kdd_probe *p,const char *stage,int error) {
    p->stage=stage; p->error=error; return 0;
}
static int kdd_prepare(unsigned pid,const char *blob_path,int fixture,struct target *t,
                       struct ram_state *state,struct nx_sfr_header *h,unsigned char **blob,
                       struct nx_sfr_config *cfg,struct nx_sfr_runtime *runtime,struct nx_kdd_probe *p) {
    char path[128];
    if(!state_path(pid,path,sizeof path)) return kdd_fail(p,"state_path",EINVAL);
    if(!blob_read(blob_path,blob,h)) return kdd_fail(p,"blob_identity",EINVAL);
    if(!target_open(pid,fixture,t)) return kdd_fail(p,"target_identity",ESTALE);
    if(!state_read(path,pid,state)) return kdd_fail(p,"resident_state",ESTALE);
    if(!resident(t,state,h,*blob,cfg,runtime)) return kdd_fail(p,"resident_validation",ESTALE);
    p->start=t->start; p->build=h->build; p->generation=cfg->generation; p->window=runtime->timing.window;
    if(!cfg->enabled||cfg->session_mode||cfg->probe_once||!cfg->calltrace_enabled)
        return kdd_fail(p,"preview_not_armed",ECANCELED);
    if(!probe_expect_matches(pid,t->start,h->build,cfg,runtime))
        return kdd_fail(p,"selection_changed",ESTALE);
    return 1;
}
static int kdd_find_thread(struct target *t,const struct ram_state *state,
                           const struct nx_sfr_config *expected,struct nx_kdd_probe *p) {
    struct nx_sfr_config current;
    struct nx_ipct trace;
    if(!read_exact(t->mem,&current,sizeof current,state->config)) return kdd_fail(p,"config_read",EIO);
    if(current.generation!=expected->generation||!current.enabled||current.session_mode||
       current.probe_once||!current.calltrace_enabled) return kdd_fail(p,"selection_changed",ECANCELED);
    if(!read_exact(t->mem,&trace,sizeof trace,state->runtime+offsetof(struct nx_sfr_runtime,open_trace)))
        return kdd_fail(p,"trace_read",EIO);
    p->tid=0;
    for(unsigned i=0;i<NX_IPCT_RECORDS;++i) {
        const struct nx_ipct_record *r=&trace.records[i];
        if(r->committed==1&&r->role==NX_IPCT_PREVIEW&&r->tid) p->tid=r->tid;
    }
    /* A concurrent soft stop/re-arm can reset the record array. Do not carry
     * its old thread identity into a new selection. */
    if(!read_exact(t->mem,&current,sizeof current,state->config)) return kdd_fail(p,"config_read",EIO);
    if(current.generation!=expected->generation||!current.enabled||current.session_mode||
       current.probe_once||!current.calltrace_enabled) return kdd_fail(p,"selection_changed",ECANCELED);
    return 1;
}
static int kdd_wait(struct target *t,const struct ram_state *state,
                    const struct nx_sfr_config *cfg,struct nx_kdd_probe *p) {
    int ready=0;
    unsigned long long started=now_ms();
    /* No preview, media or loader control lease is held here. The user may
     * return from a menu/playback or soft-stop while the first frame is absent.
     * Only this bounded diagnostic process polls; the camera gains no loop. */
    p->stage="waiting_first_frame";
    printf("{\"type\":\"ipcs_kdd_wait\",\"status\":\"waiting\",\"pid\":%u,\"generation\":%u,\"wait_budget_ms\":60000}\n",p->pid,cfg->generation);
    fflush(stdout);
    unsigned long long until=now_ms()+60000;
    for(;;) {
        if(abort_requested) { kdd_fail(p,"wait_cancelled",EINTR); break; }
        if(!kdd_find_thread(t,state,cfg,p)) break;
        if(p->tid) { p->stage="first_frame_ready"; p->first_frame_ready=ready=1; break; }
        if(now_ms()>=until) { kdd_fail(p,"first_frame_timeout",ETIMEDOUT); break; }
        struct timespec pause={0,100000000}; nanosleep(&pause,NULL);
    }
    p->wait_ms=(unsigned)(now_ms()-started);
    kdd_ready_export(stdout,p); fflush(stdout); return ready;
}
static int kdd_lock_identity(int fd,const char *path) {
    struct stat64 held,named;
    return !__fxstat64(3,fd,&held)&&!__lxstat64(3,path,&named)&&S_ISREG(named.st_mode)&&
        S_ISREG(held.st_mode)&&held.st_uid==geteuid()&&named.st_uid==geteuid()&&
        held.st_dev==named.st_dev&&held.st_ino==named.st_ino;
}
static int kdd_leases(unsigned pid,int fixture,int *control,struct nx_kdd_probe *p) {
    const char *state=getenv("NX_STATE_DIR"),*source=getenv("NX300M_MOD_SOURCE_ROOT");
    const char *id=getenv("NX300M_MOD_SOURCE_ID");
    struct nx_sfm_identity expected; struct stat64 actual;
    char path[1024]; int media=0,preview=0,ok=0;
    if(!state||state[0]!='/'||!source||strcmp(source,fixture?"/perf":"/mnt/mmc/nx300m-mod")||
       !id||!probe_number(&id,&expected.device,UINT64_MAX,':')||!probe_number(&id,&expected.inode,UINT64_MAX,0))
        return kdd_fail(p,"collector_identity",EINVAL);
    if(snprintf(path,sizeof path,"%s/media-io.lock",state)>=(int)sizeof path||
       !kdd_lock_identity(6,path)||!kdd_lock_identity(9,"/tmp/nx300m-smartfilter-preview.lock"))
        return kdd_fail(p,"collector_locks",EINVAL);
    if(flock(6,LOCK_SH|LOCK_NB)) { kdd_fail(p,"media_busy",EBUSY); goto done; } media=1;
    if(flock(9,LOCK_EX|LOCK_NB)) { kdd_fail(p,"preview_busy",EBUSY); goto done; } preview=1;
    snprintf(path,sizeof path,"%s/media-paused",state);
    if(access(path,F_OK)==0) { kdd_fail(p,"media_paused",EBUSY); goto done; }
    if(__lxstat64(3,source,&actual)||!S_ISDIR(actual.st_mode)||
       actual.st_dev!=expected.device||actual.st_ino!=expected.inode) {
        kdd_fail(p,"source_changed",ESTALE); goto done;
    }
    snprintf(path,sizeof path,"/var/run/nx300m-smartfilter-control-%u.lock",pid);
    *control=open(path,O_RDWR|O_CREAT|O_CLOEXEC|O_NOFOLLOW,0600);
    if(*control<0||!diag_regular(*control,0)||flock(*control,LOCK_EX|LOCK_NB)) {
        kdd_fail(p,"control_busy",EBUSY); goto done;
    }
    ok=1;
done:
    if(!ok) {
        if(*control>=0) { close(*control); *control=-1; }
        if(preview) flock(9,LOCK_UN);
        if(media) flock(6,LOCK_UN);
    }
    return ok;
}
static int kdd_observe(unsigned pid,const char *blob_path,int fixture) {
    struct target t; target_init(&t);
    struct ram_state state={0}; struct nx_sfr_header h; struct nx_sfr_config cfg;
    struct nx_sfr_runtime runtime;
    struct nx_kdd_probe p={0}; p.pid=pid; p.fixture=fixture; p.detached=1; p.stage="prepare";
    unsigned char *blob=NULL; int fd=-1,rc=3,control=-1,leased=0;
    memset(&diag_save_result,0,sizeof diag_save_result);
    unsigned long long preflight=now_ms();
    int prepared=kdd_prepare(pid,blob_path,fixture,&t,&state,&h,&blob,&cfg,&runtime,&p);
    p.preflight_ms=(unsigned)(now_ms()-preflight);
    if(!prepared||!kdd_wait(&t,&state,&cfg,&p)) goto save;
    if(!(leased=kdd_leases(pid,fixture,&control,&p))) goto save;
    /* Keep the authenticated file descriptors from preflight. Recheck their
     * identities, process lifetime, mappings and code, without hashing the
     * entire stock executable and six libraries again during liveview. */
    if(!target_refresh(pid,fixture,&t)||!resident(&t,&state,&h,blob,&cfg,&runtime)) {
        kdd_fail(&p,"resident_recheck",ESTALE); goto save;
    }
    if(cfg.generation!=p.generation||runtime.timing.window!=p.window) {
        kdd_fail(&p,"selection_changed",ECANCELED); goto save;
    }
    if(!kdd_find_thread(&t,&state,&cfg,&p)) goto save;
    if(!p.tid) { kdd_fail(&p,"first_frame_missing",EAGAIN); goto save; }
    if(abort_requested) { kdd_fail(&p,"observation_cancelled",EINTR); goto save; }
    struct thread identity;
    if(!task_identity(pid,p.tid,&identity)||identity.tracer||identity.state=='T'||identity.state=='t') {
        kdd_fail(&p,"thread_identity",EBUSY); goto save;
    }
    if(!fixture) {
        struct stat64 st;
        fd=open("/dev/d4_opener",O_RDONLY|O_CLOEXEC|O_NOFOLLOW);
        struct nx_kdd_list list;
        if(fd<0||__fxstat64(3,fd,&st)||!S_ISCHR(st.st_mode)||!kdd_snapshot(fd,0,&list)) {
            p.opener_errno=errno; kdd_fail(&p,"opener_query",errno?errno:EIO); goto save;
        }
    }
    struct nx_sft_ledger ledger; diag_ledger_load(&ledger,pid,t.start,h.build);
    p.begin=diag_now();
    nstops=1; struct stopped *s=&stops[0]; memset(s,0,sizeof *s); s->tid=p.tid;
    p.stage="ptrace_seize";
    if(ptrace(PTRACE_SEIZE,p.tid,NULL,(void *)(long)PTRACE_O_TRACESYSGOOD)<0) { p.error=errno; goto ledger; }
    s->attached=p.attached=1; p.detached=0; p.stage="ptrace_interrupt";
    if(ptrace(PTRACE_INTERRUPT,p.tid,NULL,NULL)<0||!wait_stop(s,1000)) { p.error=errno?errno:ETIMEDOUT; goto detach; }
    unsigned tracked[3]={~0U,~0U,~0U}; int pending=-1;
    unsigned long long until=now_ms()+2000;
    p.stage="syscall_observation";
    while(!abort_requested&&now_ms()<until&&(p.samples<NX_KDD_SAMPLES||pending>=0)) {
        if(ptrace(PTRACE_SYSCALL,p.tid,NULL,NULL)<0) { p.error=errno; break; }
        s->stopped=0;
        if(!wait_stop(s,250)) { p.error=ETIMEDOUT; break; }
        ++p.steps;
        if(s->signal!=(SIGTRAP|0x80)||s->event) { p.error=EINTR; break; }
        struct arm_regs regs;
        if(ptrace(PTRACE_GETREGS,p.tid,NULL,&regs)<0) { p.error=errno; break; }
        /* ARM Linux syscall_trace exposes entry/exit in r12, without writing
         * registers. Unknown markers abort rather than guessing stop parity. */
        if(regs.r[12]>1) { p.error=EPROTO; break; }
        if(regs.r[12]==0) {
            pending=-1;
            unsigned nr=regs.r[7],arg=regs.r[17]; int matched=0;
            if(nr==5&&p.opens<3&&kdd_path(t.mem,arg,fixture?"/dev/null":"/dev/d4_ipcs")) matched=1;
            if(nr==6) for(unsigned i=0;i<3;++i) if(arg==tracked[i]&&arg!=~0U) matched=1;
            if(!matched) continue;
            struct nx_kdd_sample *r=&p.call[p.samples];
            r->syscall=nr; r->fd=nr==6?arg:~0U; r->begin=diag_now(); r->after.count=-1;
            if(!kdd_snapshot(fd,fixture,&r->before)) { p.error=errno?errno:EIO; break; }
            pending=(int)p.samples++;
        } else if(pending>=0) {
            struct nx_kdd_sample *r=&p.call[pending];
            if(regs.r[7]!=r->syscall) { p.error=EPROTO; break; }
            r->end=diag_now(); r->result=(int)regs.r[0];
            if(!kdd_snapshot(fd,fixture,&r->after)) { p.error=errno?errno:EIO; break; }
            r->completed=1;
            if(r->syscall==5&&r->result>=0) { r->fd=(unsigned)r->result; tracked[p.opens++]=r->fd; }
            if(r->syscall==6&&r->result==0) for(unsigned i=0;i<3;++i) if(tracked[i]==r->fd) tracked[i]=~0U;
            pending=-1;
        }
    }
    if(!p.error&&(p.samples!=NX_KDD_SAMPLES||pending>=0)) kdd_fail(&p,"sample_window_expired",ETIMEDOUT);
detach:
    if(s->attached&&!s->stopped) {
        if(ptrace(PTRACE_INTERRUPT,p.tid,NULL,NULL)>=0) wait_stop(s,1000);
    }
    p.detached=detach_all();
    if(!p.detached) kdd_fail(&p,"ptrace_detach",EIO);
    else if(!p.error) p.stage="complete";
ledger:
    p.end=diag_now();
    if(!p.detached) ledger.complete=0;
    diag_ledger_save(&ledger,p.begin,p.end,0);
save:
    if(!leased) diag_log_path=NULL;
    if(kdd_persist(&p)&&p.detached&&!p.error) rc=0;
    if(leased&&p.detached) { flock(control,LOCK_UN); flock(9,LOCK_UN); flock(6,LOCK_UN); }
    if(control>=0) close(control);
    if(fd>=0) close(fd);
    target_close(&t); free(blob);
    if(rc) fputs("KDD observation incomplete; no target registers, code or reference count written.\n",stderr);
    return rc;
}
static int kdd_text(const char *path,char *text,unsigned size) {
    int fd=open(path,O_RDONLY|O_CLOEXEC|O_NOFOLLOW);
    struct stat64 st;
    int ok=fd>=0&&diag_regular(fd,size-1)&&!__fxstat64(3,fd,&st)&&st.st_size>0&&
        read(fd,text,(unsigned)st.st_size)==st.st_size;
    if(ok) text[st.st_size]=0;
    if(fd>=0) close(fd);
    return ok;
}
static int kdd_guard_refuse(const char *stage) {
    fprintf(stderr,"KDD guard recovery refused: stage=%s errno=%d\n",stage,errno); return 0;
}
static int kdd_recover_guard(unsigned pid,int fixture) {
    const char *guard="/tmp/nx300m-smartfilter-preview.uncertain";
    char text[256],expected[256],path[256],proof[16384];
    struct stat64 original,current;
    unsigned controller=0; unsigned long long controller_start=0;
    if(__lxstat64(3,guard,&original)) return errno==ENOENT?1:kdd_guard_refuse("guard_stat");
    if(!kdd_text(guard,text,sizeof text)||
       sscanf(text,"timeout child=%u start=%llu;",&controller,&controller_start)!=2||!controller) return kdd_guard_refuse("guard_format");
    snprintf(expected,sizeof expected,"timeout child=%u start=%llu; no process killed; reboot before another preview operation\n",controller,controller_start);
    if(strcmp(text,expected)) return kdd_guard_refuse("guard_content");
    struct thread identity;
    if(task_identity(controller,controller,&identity)&&
       (!controller_start||identity.start==controller_start)&&identity.state!='Z'&&identity.state!='X') return kdd_guard_refuse("controller_alive");
    if(!task_identity(pid,pid,&identity)) return kdd_guard_refuse("camera_identity");
    unsigned long long camera_start=identity.start;
    unsigned count=0;
    if(!threads(pid,before,&count)) return kdd_guard_refuse("thread_inventory");
    for(unsigned i=0;i<count;++i) if(before[i].tracer||before[i].state=='T'||before[i].state=='t') return kdd_guard_refuse("tracer_active");
    /* The old supervisor's exclusive worker log is named after precisely the
     * controller recorded in the guard. Accept only its cancelled-before-
     * attach read-only KDD result. Mutation/unknown guards remain in place. */
    snprintf(path,sizeof path,"/var/run/nx300m-smartfilter-worker-%u.log",controller);
    if(!kdd_text(path,proof,sizeof proof)) return kdd_guard_refuse("worker_receipt");
    int proven=0;
    for(char *line=proof;line&&*line;) {
        char *end=strchr(line,'\n'); if(end) *end=0;
        char source[32]; unsigned observed_pid=0,observed_tid=0;
        unsigned long long observed_start=0;
        if(sscanf(line,"{\"type\":\"ipcs_kdd_window\",\"measurement_source\":\"%31[^\"]\",\"pid\":%u,\"tid\":%u,\"detached\":true,\"error\":4,\"opener_errno\":0,\"steps\":0,\"stage\":\"observation_cancelled\",\"start\":%llu,",
                  source,&observed_pid,&observed_tid,&observed_start)==4&&
           !strcmp(source,fixture?"arm_fixture":"physical_camera")&&observed_pid==pid&&observed_tid&&
           observed_start==camera_start&&strstr(line,"\"timing_perturbed\":false")&&
           strstr(line,"\"begin\":[0,0],\"end\":[0,0]")) proven=1;
        line=end?end+1:NULL;
    }
    if(!proven) return kdd_guard_refuse("worker_not_cancelled_before_attach");
    if(__lxstat64(3,guard,&current)||original.st_dev!=current.st_dev||original.st_ino!=current.st_ino) return kdd_guard_refuse("guard_changed");
    /* Keep the receipt beside the guard: /tmp and /var/run can be separate
     * tmpfs mounts, and recovery must remain one atomic rename. */
    snprintf(path,sizeof path,"/tmp/nx300m-kdd-recovered-%u-%llu.guard",controller,controller_start);
    if(access(path,F_OK)==0||rename(guard,path)) return kdd_guard_refuse("guard_archive");
    printf("{\"command\":\"recover-kdd-guard\",\"recovered\":true,\"pid\":%u,\"reason\":\"cancelled_before_attach\"}\n",pid);
    return 1;
}
#endif
