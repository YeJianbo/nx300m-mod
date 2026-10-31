/* Exercise the production state transitions without a receiver or an X server.
 * The enclosing isolated gallery probe builds this temporary native executable. */
#define main quickshare_panel_main
#define mg_connect_http probe_connect_http
#include "../mod/metol/quickshare_panel.c"
#undef main
#undef mg_connect_http
#include <assert.h>

static struct mg_connection connection;
static unsigned calls;
static char posted[16384],called[128];
struct mg_connection *probe_connect_http(struct mg_mgr *mgr,mg_event_handler_t handler,
                                        const char *url,const char *headers,const char *body) {
    (void)mgr;(void)handler;(void)headers;calls++;
    snprintf(called,sizeof called,"%s",url);snprintf(posted,sizeof posted,"%s",body?body:"");
    return &connection;
}
static void reset(void) {
    request=NULL;running=available=started=1;closing=quit=pending_launch=cancel_sent=target_lost=send_after_cancel=0;
    queued_send=queued_ready=request_replied=0;selected_count=0;file_format=ORIGINAL;format_error[0]=action[0]=0;
    owned=task=7;sequence=0;chosen=1;device_count=2;calls=0;reconcile_at=poll_at=close_at=0;
    phase[0]=client_token[0]=0;strcpy(operation,"discover");
    strcpy(devices[0].endpoint,"127.0.0.1:9998");strcpy(devices[0].name,"A");
    strcpy(devices[1].endpoint,"127.0.0.1:9999");strcpy(devices[1].name,"B");
    file_count=1;strcpy(relative[0],"100PHOTO/generated.txt");
}
static void reply(const char *body) {
    struct http_message h;memset(&h,0,sizeof h);h.resp_code=200;h.body.p=body;h.body.len=strlen(body);
    accept_status(&h);request=NULL;
}
static void queue_status(void) {
    reset();poll_at=now()+10000;call("status",NULL);request_send();
    assert(queued_send && !queued_ready && calls==1 && !strcmp(target.name,"B"));
}
int main(void) {
    reset();request_send();assert(send_after_cancel && !strcmp(target.name,"B"));
    request=NULL;choose(0);assert(chosen==1);
    event_line("{\"event\":\"discovered\",\"endpoint\":{\"ip\":\"127.0.0.1\",\"port\":9998,\"name\":\"A\",\"present\":false}}");
    running=0;idle(now());assert(strstr(called,"/send") && strstr(posted,"endpoint=127.0.0.1:9999"));
    reset();request_send();request=NULL;
    event_line("{\"event\":\"discovered\",\"endpoint\":{\"ip\":\"127.0.0.1\",\"port\":9999,\"name\":\"B\",\"present\":false}}");
    assert(!send_after_cancel && target_lost);running=0;poll_at=now()+10000;idle(now());assert(calls==1);

    reset();strcpy(operation,"send");request_send();choose(0);assert(!calls && chosen==1);
    running=0;choose(0);assert(chosen==0 && scroll_selection);

    reset();begin_launch();char body[512];
    snprintf(body,sizeof body,"{\"available\":true,\"running\":true,\"task_id\":8,\"operation\":\"send\",\"client_token\":\"%s\",\"events\":[]}",client_token);
    close_panel();reconcile_at=now()-5000;reply(body);idle(now());
    assert(owned==8 && cancel_sent && !quit && strstr(called,"/cancel") && strstr(posted,"task_id=8"));

    reset();begin_launch();close_panel();
    reply("{\"available\":true,\"running\":true,\"task_id\":8,\"operation\":\"send\",\"client_token\":\"another-page\",\"events\":[]}");
    idle(now());assert(!owned && quit && !calls);

    queue_status();request_send();choose(0);choose_format(RAW);
    assert(calls==1 && chosen==1 && file_format==ORIGINAL && file_count==1);
    reply("{\"available\":true,\"running\":true,\"task_id\":7,\"operation\":\"discover\",\"client_token\":\"\",\"events\":[]}");
    assert(queued_send && queued_ready);idle(now());
    assert(!queued_send && send_after_cancel && calls==2 && strstr(called,"/cancel") && strstr(posted,"task_id=7"));
    reply("{\"available\":true,\"running\":false,\"task_id\":7,\"operation\":\"discover\",\"client_token\":\"\",\"events\":[]}");
    idle(now());request_send();idle(now());
    assert(calls==3 && strstr(called,"/send") && strstr(posted,"endpoint=127.0.0.1:9999") && strstr(posted,"path0=100PHOTO%2Fgenerated.txt"));

    const char *bad[]={"not-json","{}","{\"available\":true,\"running\":false,\"task_id\":\"7\"}"};
    for (unsigned i=0;i<sizeof bad/sizeof bad[0];i++) {
        queue_status();reply(bad[i]);idle(now());assert(!queued_send && !queued_ready && calls==1);
    }
    queue_status();struct http_message error;memset(&error,0,sizeof error);error.resp_code=503;error.body.p="{}";error.body.len=2;
    accept_status(&error);request=NULL;idle(now());assert(!queued_send && calls==1);

    queue_status();close_panel();
    reply("{\"available\":true,\"running\":false,\"task_id\":7,\"operation\":\"discover\",\"client_token\":\"\",\"events\":[]}");
    idle(now());assert(!queued_send && quit && calls==1);

    queue_status();reply("{\"available\":true,\"running\":true,\"task_id\":8,\"operation\":\"send\",\"client_token\":\"other\",\"events\":[]}");
    idle(now());assert(!queued_send && !owned && calls==1);

    queue_status();event_line("{\"event\":\"discovered\",\"endpoint\":{\"ip\":\"127.0.0.1\",\"port\":9999,\"name\":\"B\",\"present\":false}}");
    assert(!queued_send && target_lost && calls==1);
    queue_status();int failed=1;http(&connection,MG_EV_CONNECT,&failed);http(&connection,MG_EV_CLOSE,NULL);idle(now());
    assert(!queued_send && calls==1);
    queue_status();http(&connection,MG_EV_CLOSE,NULL);idle(now());assert(!queued_send && calls==1);

    const char *invalid_status[]={"{}","{\"available\"}","{\"available\":true,\"running\":false,\"task_id\":7}",
        "{\"available\":true,\"running\":true,\"task_id\":0,\"operation\":\"send\",\"client_token\":\"\"}"};
    for (unsigned i=0;i<sizeof invalid_status/sizeof invalid_status[0];i++) {
        reset();strcpy(action,"status");reply(invalid_status[i]);
        assert(running && task==7 && owned==7);
        begin_launch();reply(invalid_status[i]);assert(pending_launch && owned==7);
    }
    reset();close_panel();idle(now());assert(cancel_sent && calls==1);
    error.resp_code=503;accept_status(&error);request=NULL;
    assert(!cancel_sent && owned==7 && running);poll_at=0;
    long long first_cancel=close_at;idle(now());assert(cancel_sent && calls==2 && close_at==first_cancel);
    http(&connection,MG_EV_CLOSE,NULL);assert(!cancel_sent);
    poll_at=0;idle(now());assert(cancel_sent && calls==3 && close_at==first_cancel);
    reply("{\"available\":true,\"running\":true,\"task_id\":7,\"operation\":\"discover\",\"client_token\":\"\",\"cancel_requested\":true}");
    assert(cancel_sent);idle(first_cancel+4001);assert(quit && calls==3);

    char directory[]="/tmp/nx-qs-state-XXXXXX",lock_path[PATH_MAX],marker_path[PATH_MAX];
    assert(mkdtemp(directory));assert(!setenv("NX_STATE_DIR",directory,1));
    snprintf(lock_path,sizeof lock_path,"%s/media-io.lock",directory);
    snprintf(marker_path,sizeof marker_path,"%s/media-paused",directory);
    assert(lock_media());int exclusive=open(lock_path,O_RDWR);assert(exclusive>=0);
    assert(flock(exclusive,LOCK_EX|LOCK_NB)<0 && (errno==EWOULDBLOCK || errno==EAGAIN));
    reset();selected_count=1;strcpy(selected[0],"must-not-be-read");
    int marker=open(marker_path,O_WRONLY|O_CREAT|O_EXCL,0600);assert(marker>=0);close(marker);
    assert(!prepare_files() && closing && !format_error[0]);
    assert(flock(exclusive,LOCK_EX|LOCK_NB)<0);
    close(media_lock);media_lock=-1;assert(!flock(exclusive,LOCK_EX|LOCK_NB));close(exclusive);
    assert(!lock_media() && media_lock<0);
    assert(!unlink(marker_path));media_pause_seen=0;
    assert(!unlink(lock_path) && !symlink("target",lock_path));
    assert(!lock_media() && media_lock<0);
    assert(!unlink(lock_path) && !rmdir(directory));unsetenv("NX_STATE_DIR");media_pause_marker[0]=0;media_pause_seen=0;
    puts("{\"frozen_receiver\":true,\"removed_receiver_aborts\":true,\"no_double_send\":true,\"selection_guard\":true,\"lost_reply_owned_cancel\":true,\"foreign_task_retained\":true,\"queued_send_once\":true,\"queued_bad_reply_dropped\":true,\"queued_return_dropped\":true,\"queued_foreign_task_retained\":true,\"queued_offline_dropped\":true,\"queued_connection_failure_dropped\":true,\"invalid_status_retains_ownership\":true,\"cancel_failure_retried\":true,\"cancel_wait_bounded\":true,\"media_shared_lock_blocks_release\":true,\"media_pause_stops_local_reads\":true,\"media_pause_rejects_launch\":true,\"media_lock_symlink_rejected\":true}");
    return 0;
}
