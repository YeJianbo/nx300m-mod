/* Embedded JPEG previews share the media-root confinement used by downloads.
 * Cache only requested images; changes invalidate an entry without rescanning
 * the card or writing sidecars alongside the user's photos. */
#include "media_preview.h"
#include "mp_preview_exif.h"

#define GP_SLOTS 64
#define GP_BYTES (8U*1024U*1024U)
struct gp_entry {
    char path[PATH_MAX];
    struct stat identity;
    int small, result, metadata_changed;
    unsigned long used;
    unsigned char *data;
    size_t length;
    mp_preview_info info;
};
static struct gp_entry gp_cache[GP_SLOTS];
static size_t gp_bytes;
static unsigned long gp_clock;

static int gp_same(const struct stat *a,const struct stat *b) {
    return a->st_dev==b->st_dev && a->st_ino==b->st_ino &&
        a->st_size==b->st_size && a->st_mtim.tv_sec==b->st_mtim.tv_sec &&
        a->st_mtim.tv_nsec==b->st_mtim.tv_nsec &&
        a->st_ctim.tv_sec==b->st_ctim.tv_sec && a->st_ctim.tv_nsec==b->st_ctim.tv_nsec;
}
static void gp_drop(struct gp_entry *entry) {
    gp_bytes-=entry->length;free(entry->data);memset(entry,0,sizeof *entry);
}
static void gp_clear(void) {
    for (int i=0;i<GP_SLOTS;i++) gp_drop(&gp_cache[i]);
}
static struct gp_entry *gp_oldest(int occupied) {
    struct gp_entry *old=NULL;
    for (int i=0;i<GP_SLOTS;i++) {
        if (!gp_cache[i].path[0]) { if (!occupied) return &gp_cache[i];else continue; }
        if (!old || gp_cache[i].used<old->used) old=&gp_cache[i];
    }
    return old;
}
static void gallery_preview(struct mg_connection *c,struct http_message *h) {
    char relative[1024],size[16]="grid",path[PATH_MAX];struct stat before,after;
    int head=!mg_vcmp(&h->method,"HEAD");
    if (mg_vcmp(&h->method,"GET") && !head) { reply(c,405,"{\"error\":\"GET or HEAD required\"}");return; }
    int n=mg_get_http_var(&h->query_string,"p",relative,sizeof relative);
    int size_n=mg_get_http_var(&h->query_string,"size",size,sizeof size);
    if (size_n==-1) strcpy(size,"grid");
    if (n<=0 || size_n<-1 || (strcmp(size,"grid") && strcmp(size,"full"))) {
        reply(c,400,"{\"error\":\"provide media path and grid or full size\"}");return;
    }
    if (!media_path(relative,path) || stat(path,&before) || !S_ISREG(before.st_mode)) {
        reply(c,404,"{\"error\":\"media file unavailable\"}");return;
    }
    int small=!strcmp(size,"grid"),hit=0,result=1,metadata_changed=0;
    unsigned char *data=NULL;size_t length=0;mp_preview_info info={0};
    struct gp_entry *entry=NULL;
    for (int i=0;i<GP_SLOTS;i++) if (!strcmp(gp_cache[i].path,path) && gp_cache[i].small==small) {
        if (gp_same(&gp_cache[i].identity,&before)) entry=&gp_cache[i];
        else gp_drop(&gp_cache[i]);
        break;
    }
    if (entry) {
        hit=1;entry->used=++gp_clock;data=entry->data;length=entry->length;
        info=entry->info;result=entry->result;metadata_changed=entry->metadata_changed;
    } else {
        result=mp_extract(path,small,&data,&length,&info);
        if (!result) {
            mp_preview_exif_info orientation={0};
            result=mp_preview_apply_orientation(&data,&length,info.embedded,info.orientation,&orientation);
            if (!result) {
                info.orientation=orientation.effective_orientation;
                metadata_changed=orientation.metadata_changed;
            }
        }
        if (stat(path,&after) || !gp_same(&before,&after)) {
            free(data);reply(c,409,"{\"error\":\"media changed while reading; retry\"}");return;
        }
        /* IO and allocation failures may be transient; do not cache them. */
        if (result>=0 && length<=GP_BYTES) {
            while (gp_bytes+length>GP_BYTES) gp_drop(gp_oldest(1));
            entry=gp_oldest(0);gp_drop(entry);
            strcpy(entry->path,path);entry->identity=before;entry->small=small;
            entry->result=result;entry->data=data;entry->length=length;entry->metadata_changed=metadata_changed;
            entry->info=info;entry->used=++gp_clock;gp_bytes+=length;
        }
    }
    if (result) {
        if (!entry) free(data);
        reply(c,result<0?422:404,"{\"error\":\"embedded JPEG preview unavailable\"}");return;
    }
    char headers[512];
    snprintf(headers,sizeof headers,
        "Content-Type: image/jpeg\r\nCache-Control: no-store\r\n"
        "X-Preview-Source: %s\r\nX-Preview-Width: %u\r\nX-Preview-Height: %u\r\n"
        "X-Preview-Orientation: %u\r\nX-Preview-Cache: %s\r\nX-Preview-Resized: false\r\nX-Preview-Metadata-Changed: %s",
        info.source,info.width,info.height,info.orientation,hit?"hit":"miss",metadata_changed?"true":"false");
    mg_send_head(c,200,length,headers);
    if (!head) mg_send(c,data,length);
    if (!entry) free(data);
}
