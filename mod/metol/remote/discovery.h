/* NX_REMOTE v0.9 beacons for NXConnect. Kernel interface state is refreshed
 * every second; no Wi-Fi command, SD access, worker or persistent socket. */
#ifndef NX_REMOTE_DISCOVERY_H
#define NX_REMOTE_DISCOVERY_H
#include <errno.h>
#include <ifaddrs.h>
#include <net/if.h>
#include <sys/ioctl.h>
#include <sys/socket.h>

#define NX_DISCOVERY_PORT 5681
#define NX_DISCOVERY_BYTES 64
#define NX_DISCOVERY_INTERFACES 32
static struct {
    struct sockaddr_in http;
    int initialized,paused,last_error,truncated;
    long long refresh_at,send_at;
    unsigned count;
    unsigned long long sent,failed;
    char model[15],firmware[15],mac[18],instance[40];
    struct nx_discovery_interface {
        char name[IFNAMSIZ],mac[18];
        struct in_addr address,netmask,broadcast;
        unsigned index;
        int http_reachable,broadcast_capable;
    } interfaces[NX_DISCOVERY_INTERFACES];
} nx_discovery;

static void nx_discovery_field(char *out,size_t capacity,const char *text) {
    size_t n=0;
    if (text) for (;*text && n+1<capacity;text++) {
        unsigned char c=(unsigned char)*text;
        if (c>=32 && c<127 && c!='|') out[n++]=(char)c;
    }
    out[n]=0;
}
static void nx_discovery_mac(int socket_fd,const char *name,char *out) {
    struct ifreq request;memset(&request,0,sizeof request);
    snprintf(request.ifr_name,sizeof request.ifr_name,"%s",name);out[0]=0;
    if (socket_fd<0 || ioctl(socket_fd,SIOCGIFHWADDR,&request) || request.ifr_hwaddr.sa_family!=1) return;
    const unsigned char *mac=(const unsigned char *)request.ifr_hwaddr.sa_data;
    unsigned any=0;for (int i=0;i<6;i++) any|=mac[i];
    if (!any || (mac[0]&1)) return;
    snprintf(out,18,"%02x:%02x:%02x:%02x:%02x:%02x",mac[0],mac[1],mac[2],mac[3],mac[4],mac[5]);
}
static int nx_discovery_order(const void *a,const void *b) {
    const struct nx_discovery_interface *left=a,*right=b;
    int by_name=strcmp(left->name,right->name);
    if (by_name) return by_name;
    return ntohl(left->address.s_addr)<ntohl(right->address.s_addr)?-1:
        ntohl(left->address.s_addr)>ntohl(right->address.s_addr);
}
static void nx_discovery_refresh(long long tick) {
    if (tick<nx_discovery.refresh_at) return;
    nx_discovery.refresh_at=tick+1000;
    nx_discovery.count=0;nx_discovery.truncated=0;
    nx_discovery.mac[0]=nx_discovery.instance[0]=0;
    struct ifaddrs *interfaces=NULL;
    if (getifaddrs(&interfaces)) {nx_discovery.last_error=errno;return;}
    int fd=socket(AF_INET,SOCK_DGRAM|SOCK_CLOEXEC,0);
    for (struct ifaddrs *entry=interfaces;entry;entry=entry->ifa_next) {
        if (!entry->ifa_addr || entry->ifa_addr->sa_family!=AF_INET ||
            (entry->ifa_flags&(IFF_UP|IFF_RUNNING))!=(IFF_UP|IFF_RUNNING) || (entry->ifa_flags&IFF_LOOPBACK)) continue;
        struct in_addr address=((struct sockaddr_in *)entry->ifa_addr)->sin_addr;
        uint32_t ip=ntohl(address.s_addr);
        if (!ip || (ip>>24)==127 || (ip>>28)>=14) continue;
        if (nx_discovery.count==NX_DISCOVERY_INTERFACES) {nx_discovery.truncated=1;continue;}
        struct nx_discovery_interface *item=&nx_discovery.interfaces[nx_discovery.count];
        memset(item,0,sizeof *item);
        if (snprintf(item->name,sizeof item->name,"%s",entry->ifa_name)>=(int)sizeof item->name) continue;
        item->index=if_nametoindex(entry->ifa_name);if (!item->index) continue;
        item->address=address;
        if (entry->ifa_netmask) item->netmask=((struct sockaddr_in *)entry->ifa_netmask)->sin_addr;
        item->broadcast.s_addr=address.s_addr|~item->netmask.s_addr;
        item->broadcast_capable=(entry->ifa_flags&IFF_BROADCAST) && item->netmask.s_addr &&
            item->broadcast.s_addr!=item->address.s_addr;
        item->http_reachable=nx_discovery.initialized &&
            (!nx_discovery.http.sin_addr.s_addr || nx_discovery.http.sin_addr.s_addr==address.s_addr);
        nx_discovery_mac(fd,item->name,item->mac);
        nx_discovery.count++;
    }
    if (fd>=0) close(fd);
    freeifaddrs(interfaces);
    qsort(nx_discovery.interfaces,nx_discovery.count,sizeof nx_discovery.interfaces[0],nx_discovery_order);
    /* A MAC-derived identity survives DHCP changes. Prefer a usable HTTP
     * interface; deterministic ordering avoids depending on getifaddrs order. */
    for (unsigned i=0;i<nx_discovery.count;i++) {
        struct nx_discovery_interface *item=&nx_discovery.interfaces[i];
        if (!item->http_reachable || !item->mac[0]) continue;
        strcpy(nx_discovery.mac,item->mac);
        snprintf(nx_discovery.instance,sizeof nx_discovery.instance,"nx-remote-%c%c%c%c%c%c%c%c%c%c%c%c",
            item->mac[0],item->mac[1],item->mac[3],item->mac[4],item->mac[6],item->mac[7],
            item->mac[9],item->mac[10],item->mac[12],item->mac[13],item->mac[15],item->mac[16]);
        break;
    }
}
static void nx_discovery_init(int http_socket,const char *model,const char *firmware) {
    memset(&nx_discovery,0,sizeof nx_discovery);
    struct sockaddr_storage address;socklen_t size=sizeof address;
    if (getsockname(http_socket,(struct sockaddr *)&address,&size) || address.ss_family!=AF_INET) return;
    nx_discovery.http=*(struct sockaddr_in *)&address;
    nx_discovery.initialized=1;
    nx_discovery_field(nx_discovery.model,sizeof nx_discovery.model,model);
    nx_discovery_field(nx_discovery.firmware,sizeof nx_discovery.firmware,firmware);
    nx_discovery_refresh(now_ms());
}
static int nx_discovery_enabled(const struct nx_discovery_interface *item) {
    unsigned port=ntohs(nx_discovery.http.sin_port);
    /* v0.9 has no port field; NXConnect validates these two daemon ports. */
    return !nx_discovery.paused && item->http_reachable && item->broadcast_capable &&
        (port==80 || port==8781);
}
static int nx_discovery_send(const struct nx_discovery_interface *item) {
    int fd=socket(AF_INET,SOCK_DGRAM|SOCK_NONBLOCK|SOCK_CLOEXEC,0);
    if (fd<0) return -1;
    int yes=1,result=-1;
    struct sockaddr_in local={.sin_family=AF_INET,.sin_addr=item->address};
    if (setsockopt(fd,SOL_SOCKET,SO_BROADCAST,&yes,sizeof yes) || bind(fd,(struct sockaddr *)&local,sizeof local)) goto done;
    char packet[NX_DISCOVERY_BYTES]={0};
    /* 14+14+17 bytes of fields leave room for delimiters and NUL padding. */
    int length=snprintf(packet,sizeof packet,"NX_REMOTE|0.9|%s|%s|%s|",nx_discovery.model,nx_discovery.firmware,item->mac);
    if (length<0 || length>=(int)sizeof packet) {errno=EOVERFLOW;goto done;}
    struct sockaddr_in destination={.sin_family=AF_INET,.sin_port=htons(NX_DISCOVERY_PORT),.sin_addr=item->broadcast};
    struct iovec vector={.iov_base=packet,.iov_len=sizeof packet};
    union { struct cmsghdr alignment;char bytes[CMSG_SPACE(sizeof(struct in_pktinfo))]; } control;
    memset(&control,0,sizeof control);
    struct msghdr message={.msg_name=&destination,.msg_namelen=sizeof destination,.msg_iov=&vector,.msg_iovlen=1,
        .msg_control=control.bytes,.msg_controllen=sizeof control.bytes};
    struct cmsghdr *header=CMSG_FIRSTHDR(&message);
    header->cmsg_level=IPPROTO_IP;header->cmsg_type=IP_PKTINFO;header->cmsg_len=CMSG_LEN(sizeof(struct in_pktinfo));
    struct in_pktinfo *info=(struct in_pktinfo *)CMSG_DATA(header);
    info->ipi_ifindex=(int)item->index;info->ipi_spec_dst=item->address;
    result=sendmsg(fd,&message,MSG_DONTWAIT)==sizeof packet?0:-1;
done:
    {int error=errno;close(fd);errno=error;}
    return result;
}
static void nx_discovery_poll(int paused) {
    nx_discovery.paused=paused;
    long long tick=now_ms();nx_discovery_refresh(tick);
    if (!nx_discovery.initialized || paused || tick<nx_discovery.send_at) return;
    nx_discovery.send_at=tick+1000;
    for (unsigned i=0;i<nx_discovery.count;i++) {
        if (!nx_discovery_enabled(&nx_discovery.interfaces[i])) continue;
        if (!nx_discovery_send(&nx_discovery.interfaces[i])) nx_discovery.sent++;
        else {nx_discovery.failed++;nx_discovery.last_error=errno;}
    }
}
static void nx_discovery_identity(struct mg_connection *c) {
    nx_discovery_refresh(now_ms());
    mg_printf_http_chunk(c,"\"mac_address\":");
    if (nx_discovery.mac[0]) json_string(c,nx_discovery.mac);else mg_printf_http_chunk(c,"null");
    mg_printf_http_chunk(c,",\"instance_id\":");
    if (nx_discovery.instance[0]) json_string(c,nx_discovery.instance);else mg_printf_http_chunk(c,"null");
}
static void nx_discovery_status(struct mg_connection *c,struct http_message *h,int paused) {
    if (mg_vcmp(&h->method,"GET")) {reply(c,405,"{\"error\":\"GET required\"}");return;}
    nx_discovery.paused=paused;
    nx_discovery_refresh(now_ms());
    unsigned enabled=0;
    for (unsigned i=0;i<nx_discovery.count;i++) enabled+=nx_discovery_enabled(&nx_discovery.interfaces[i])!=0;
    char bound[INET_ADDRSTRLEN];inet_ntop(AF_INET,&nx_discovery.http.sin_addr,bound,sizeof bound);
    mg_printf(c,"HTTP/1.1 200 OK\r\nContent-Type: application/json; charset=utf-8\r\nTransfer-Encoding: chunked\r\nCache-Control: no-store\r\n\r\n");
    mg_printf_http_chunk(c,"{\"http_address\":");json_string(c,bound);
    mg_printf_http_chunk(c,",\"http_port\":%u,\"broadcast_port\":%u,\"broadcast_enabled\":%s,\"media_paused\":%s,"
        "\"packets_sent\":%llu,\"send_failures\":%llu,\"last_errno\":%d,\"interfaces_truncated\":%s,",
        ntohs(nx_discovery.http.sin_port),NX_DISCOVERY_PORT,enabled?"true":"false",nx_discovery.paused?"true":"false",
        nx_discovery.sent,nx_discovery.failed,nx_discovery.last_error,nx_discovery.truncated?"true":"false");
    nx_discovery_identity(c);mg_printf_http_chunk(c,",\"interfaces\":[");
    for (unsigned i=0;i<nx_discovery.count;i++) {
        struct nx_discovery_interface *item=&nx_discovery.interfaces[i];char ip[INET_ADDRSTRLEN],mask[INET_ADDRSTRLEN],broadcast[INET_ADDRSTRLEN];
        inet_ntop(AF_INET,&item->address,ip,sizeof ip);inet_ntop(AF_INET,&item->netmask,mask,sizeof mask);
        inet_ntop(AF_INET,&item->broadcast,broadcast,sizeof broadcast);
        mg_printf_http_chunk(c,"%s{\"name\":",i?",":"");json_string(c,item->name);
        mg_printf_http_chunk(c,",\"ipv4\":");json_string(c,ip);
        mg_printf_http_chunk(c,",\"netmask\":");json_string(c,mask);
        mg_printf_http_chunk(c,",\"broadcast_address\":");json_string(c,broadcast);
        mg_printf_http_chunk(c,",\"mac\":");if (item->mac[0]) json_string(c,item->mac);else mg_printf_http_chunk(c,"null");
        mg_printf_http_chunk(c,",\"http_reachable\":%s,\"broadcast_enabled\":%s}",
            item->http_reachable?"true":"false",nx_discovery_enabled(item)?"true":"false");
    }
    mg_printf_http_chunk(c,"]}");mg_send_http_chunk(c,"",0);
}
#endif
