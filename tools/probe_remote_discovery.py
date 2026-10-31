"""Exercise the production discovery module on isolated generated IPv4 links.

No camera, Wi-Fi command, main simulator, or existing namespace is used.
The HTTP chunk sink is a fixture; interface enumeration and UDP sends are real.
"""
from pathlib import Path
import argparse
import json
import os
import socket
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/deployment/network-discovery"
HARNESS = r'''
#define _GNU_SOURCE
#include <arpa/inet.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
struct mg_connection { int unused; };
struct mg_str { const char *p;size_t len; };
struct http_message { struct mg_str method; };
static char body[32768];static size_t used;
static int mg_printf(struct mg_connection *c,const char *format,...) {return 0;}
static int mg_printf_http_chunk(struct mg_connection *c,const char *format,...) {
    va_list args;va_start(args,format);int count=vsnprintf(body+used,sizeof body-used,format,args);va_end(args);
    if (count<0 || (size_t)count>=sizeof body-used) abort();used+=(size_t)count;return count;
}
static void mg_send_http_chunk(struct mg_connection *c,const char *text,size_t size) {
    if (used+size>=sizeof body) abort();memcpy(body+used,text,size);used+=size;body[used]=0;
}
static int mg_vcmp(const struct mg_str *s,const char *text) {return s->len!=strlen(text) || memcmp(s->p,text,s->len);}
static void reply(struct mg_connection *c,int code,const char *text) {snprintf(body,sizeof body,"{\"code\":%d,\"body\":%s}",code,text);}
static void json_string(struct mg_connection *c,const char *value) {
    mg_printf_http_chunk(c,"\"");
    for (const unsigned char *p=(const unsigned char *)value;*p;p++) {
        if (*p=='"' || *p=='\\') mg_printf_http_chunk(c,"\\%c",*p);
        else if (*p<32) mg_printf_http_chunk(c,"\\u%04x",*p);
        else mg_send_http_chunk(c,(const char *)p,1);
    }
    mg_printf_http_chunk(c,"\"");
}
static long long now_ms(void) {struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return (long long)t.tv_sec*1000+t.tv_nsec/1000000;}
#include "mod/metol/remote/discovery.h"
int main(void) {
    int server=-1;char command[128];setvbuf(stdout,NULL,_IOLBF,0);
    while (fgets(command,sizeof command,stdin)) {
        char ip[32];unsigned port;
        if (sscanf(command,"listen %31s %u",ip,&port)==2) {
            if (server>=0) close(server);server=socket(AF_INET,SOCK_STREAM,0);
            struct sockaddr_in address={.sin_family=AF_INET,.sin_port=htons(port)};
            if (server<0 || inet_pton(AF_INET,ip,&address.sin_addr)!=1 || bind(server,(void *)&address,sizeof address) || listen(server,2)) return 2;
            nx_discovery_init(server,"NX300M","1.15");puts("{}");
        } else if (!strncmp(command,"poll",4)) {
            nx_discovery_poll(0);printf("{\"sent\":%llu,\"failed\":%llu}\n",nx_discovery.sent,nx_discovery.failed);
        } else if (!strncmp(command,"pause",5)) {
            nx_discovery_poll(1);printf("{\"sent\":%llu}\n",nx_discovery.sent);
        } else {
            struct mg_connection c={0};struct http_message h={.method={.p="GET",.len=3}};
            if (!strncmp(command,"post",4)) h.method=(struct mg_str){.p="POST",.len=4};
            used=0;body[0]=0;nx_discovery_status(&c,&h,nx_discovery.paused);puts(body);
        }
    }
    if (server>=0) close(server);return 0;
}
'''


def worker(output):
    def ip(*args):
        subprocess.run(["ip", *args], check=True, capture_output=True)

    ip("link", "set", "lo", "up")
    with tempfile.TemporaryDirectory(prefix="nx-discovery-") as temporary:
        binary = Path(temporary) / "probe"
        subprocess.run(["gcc", "-std=gnu99", "-O2", "-Wall", "-Wextra", "-Wno-unused-parameter",
                        "-I", str(ROOT), str(output / "discovery-probe.c"), "-o", str(binary)], check=True)
        receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        receiver.bind(("0.0.0.0", 5681))
        receiver.settimeout(.15)
        process = subprocess.Popen([str(binary)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)

        def command(text):
            process.stdin.write(text + "\n")
            process.stdin.flush()
            line = process.stdout.readline()
            assert line, (text, process.poll())
            return json.loads(line)

        def packets():
            found = []
            while True:
                try:
                    payload, source = receiver.recvfrom(256)
                    assert len(payload) == 64, (len(payload), payload)
                    fields = payload.rstrip(b"\0").decode("ascii").split("|")
                    assert fields[:4] == ["NX_REMOTE", "0.9", "NX300M", "1.15"], fields
                    assert len(fields[4]) == 17 and fields[5] == "", fields
                    found.append({"ip": source[0], "mac": fields[4], "bytes": len(payload)})
                except socket.timeout:
                    return found

        report = {"production_discovery_module": True, "private_network_namespace": True,
                  "generated_dummy_links_only": True, "physical_camera_tested": False,
                  "http_chunk_sink_fixture": True, "cases": []}
        try:
            command("listen 0.0.0.0 8781")
            initial = command("status")
            assert initial["interfaces"] == [] and initial["mac_address"] is None and initial["instance_id"] is None
            assert not initial["broadcast_enabled"]
            report["cases"].append({"name": "no-network-at-start", "status": initial})
            for name, address, mac in [("camera0", "172.23.41.10/24", "02:11:22:33:44:50"),
                                       ("camera1", "172.23.42.11/24", "02:11:22:33:44:51")]:
                ip("link", "add", name, "type", "dummy")
                ip("link", "set", name, "address", mac)
                ip("address", "add", address, "dev", name)
                ip("link", "set", name, "up")
            time.sleep(1.05)
            sent = command("poll")
            first = packets()
            assert {p["ip"] for p in first} == {"172.23.41.10", "172.23.42.11"}, first
            active = command("status")
            assert active["broadcast_enabled"] and active["http_port"] == 8781 and sent["failed"] == 0
            assert active["mac_address"] == "02:11:22:33:44:50" and active["instance_id"] == "nx-remote-021122334450"
            assert {i["broadcast_address"] for i in active["interfaces"]} == {"172.23.41.255", "172.23.42.255"}
            baseline = len(list(Path(f"/proc/{process.pid}/fd").iterdir()))
            report["cases"].append({"name": "late-network-multiple-real-interfaces", "packets": first, "status": active})
            ip("address", "del", "172.23.41.10/24", "dev", "camera0")
            ip("address", "add", "172.23.41.12/24", "dev", "camera0")
            time.sleep(1.05)
            command("poll")
            changed = packets()
            assert {p["ip"] for p in changed} == {"172.23.41.12", "172.23.42.11"}, changed
            assert command("status")["instance_id"] == active["instance_id"]
            assert len(list(Path(f"/proc/{process.pid}/fd").iterdir())) == baseline
            report["cases"].append({"name": "address-change-with-stable-mac-identity", "packets": changed, "fds_unchanged": True})
            time.sleep(1.05)
            before = command("status")["packets_sent"]
            assert command("pause")["sent"] == before and not packets()
            paused = command("status")
            assert paused["media_paused"] and not paused["broadcast_enabled"] and paused["interfaces"]
            assert command("post")["code"] == 405
            report["cases"].append({"name": "pause-suppresses-broadcast-readonly-status-remains", "status": paused, "post_code": 405})
            command("listen 127.0.0.1 8781")
            command("poll")
            assert not packets()
            private = command("status")
            assert not private["broadcast_enabled"] and private["mac_address"] is None
            report["cases"].append({"name": "loopback-listener-not-advertised", "status": private})
            command("listen 172.23.41.12 8781")
            command("poll")
            specific = packets()
            assert [p["ip"] for p in specific] == ["172.23.41.12"], specific
            report["cases"].append({"name": "specific-listener-advertises-matching-interface-only", "packets": specific})
            command("listen 0.0.0.0 8782")
            command("poll")
            assert not packets() and not command("status")["broadcast_enabled"]
            report["cases"].append({"name": "legacy-undiscoverable-http-port-not-advertised", "http_port": 8782})
            command("listen 0.0.0.0 8781")
            ip("link", "set", "camera0", "down")
            time.sleep(1.05)
            command("poll")
            remaining = packets()
            assert [p["ip"] for p in remaining] == ["172.23.42.11"], remaining
            report["cases"].append({"name": "down-interface-removed", "packets": remaining})
            report["passed"] = True
            (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps(report, indent=2))
        finally:
            process.stdin.close()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            receiver.close()
        assert process.returncode == 0, process.returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    output = args.output.resolve()
    if args.worker:
        worker(output)
        return
    output.mkdir(parents=True, exist_ok=True)
    (output / "discovery-probe.c").write_text(HARNESS, encoding="utf-8", newline="\n")
    def linux(path):
        return "/mnt/" + path.drive[0].lower() + path.as_posix()[2:]
    result = subprocess.run(["wsl.exe", "-d", "Debian", "-u", "root", "-e", "unshare", "--net", "--mount", "--ipc", "--pid",
                             "--fork", "--mount-proc", "--kill-child", "python3", linux(ROOT / "tools/probe_remote_discovery.py"),
                             "--worker", "--output", linux(output)], capture_output=True, timeout=30)
    (output / "stdout.log").write_bytes(result.stdout)
    (output / "stderr.log").write_bytes(result.stderr)
    print(result.stdout.decode("utf-8", "replace"))
    if result.returncode:
        raise RuntimeError(f"Discovery probe exit {result.returncode}: " + result.stderr.decode("utf-8", "replace"))


if __name__ == "__main__":
    main()
