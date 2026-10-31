"""Linux-only targeted probe of production ARM media pause and Mongoose FD cleanup.

Uses QEMU user mode and stock libc, not camera process-identity validation.
The Quick Share child is a fixture holding a photo FD and ignoring SIGTERM;
this checks cancellation/reaping, not the sharing protocol.
USB sysfs and mountinfo are regular-file fixtures; their parser checks do not
verify physical USB timing, a kernel mount or exFAT teardown.
"""
import fcntl
import http.client
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time

PROJECT = Path(__file__).resolve().parents[1]
OUT = PROJECT / 'outputs/deployment/mfw-services/ram-runtime'
STOCK = Path('/opt/nx300m-lab/stock-1.15')


def main():
    source = (PROJECT / 'mod/metol/remote/remote.c').read_text()

    def section(start, end):
        return source[source.index(start):source.index(end)]

    prefix = '''#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <ctype.h>
#include <stdint.h>
#include <signal.h>
#include <limits.h>
#include <unistd.h>
#include <dirent.h>
#include <time.h>
#include <sys/stat.h>
#include "mongoose.h"
static const char *media_root;
static int media_paused;
static const char *nx_mod_root(void) { return getenv("NX300M_MOD_ROOT"); }
static void gp_clear(void) {}
static void release_inputs(void) {}
'''
    prefix += section('static long long now_ms(', 'static void stop(')
    prefix += section('static void reply(', 'static void release_inputs(')
    prefix += section('static void json_string(', 'static void dirlist(')
    prefix += section('static void serve_media_path(', '#include "gallery.h"')
    prefix += '\n#include "' + str(PROJECT / 'mod/metol/remote/media_replace.h') + '"\n'
    prefix += section('static int gallery_edit_origin(', 'static void gallery_edit_status(')
    prefix += '\n#include "' + str(PROJECT / 'mod/metol/remote/quickshare.h') + '"\n'
    prefix += '#include "' + str(PROJECT / 'mod/metol/remote/media_io.h') + '"\n'
    prefix += '#define NX_USB_SYSFS_PREFIX getenv("NX_PROBE_SYSFS")\n'
    prefix += '#define NX_STORAGE_MOUNTINFO getenv("NX_PROBE_MOUNTINFO")\n'
    prefix += '#include "' + str(PROJECT / 'mod/metol/remote/usb_storage.h') + '"\n'
    gate = section('    usb_storage_poll(1);media_io_poll(c->mgr);', '    /* Recipe batches')
    prefix += '''
static void handler(struct mg_connection *c,int event,void *data) {
''' + section('    if (event==MG_EV_SEND &&', '    struct http_message *h=data;') + '''
 struct http_message *h=data;char uri[2048];
 if (mg_url_decode(h->uri.p,h->uri.len,uri,sizeof uri,0)<0) { reply(c,400,"{}");return; }
''' + gate + '''
 if (!strncmp(uri,"/DCIM/",6)) serve_media(c,h,uri+6);
 else if (!strcmp(uri,"/api/v1/gallery/original")) gallery_original(c,h);
 else if (!strcmp(uri,"/api/v1/gallery/upload/capabilities")) gallery_upload_capabilities(c,h);
 else if (!strcmp(uri,"/probe/start-child")) {
  char *args[]={"/usr/bin/python3","-c",
   "import os,signal,time; f=open(os.environ['NX_PROBE_PHOTO'],'rb'); signal.signal(signal.SIGTERM,signal.SIG_IGN); open(os.environ['NX_PROBE_CHILD'],'w').write(str(os.getpid())); time.sleep(60)",NULL};
  reply(c,qs_launch("send",args)?202:500,"{}");
 } else if (!strcmp(uri,"/api/v1/health")) reply(c,200,"{}");
 else reply(c,404,"{}");
}
int main(void) {
 struct mg_mgr mgr;signal(SIGPIPE,SIG_IGN);mg_mgr_init(&mgr,NULL);
 media_root=getenv("NX_PROBE_MEDIA");
 if (!media_io_init(getenv("NX300M_MOD_ROOT"))) return 2;
 struct mg_connection *server=mg_bind(&mgr,getenv("NX_PROBE_LISTEN"),handler);
 if (!server) return 1;mg_set_protocol_http_websocket(server);
 for (;;) { mg_mgr_poll(&mgr,20);qs_poll();usb_storage_poll(0);media_io_poll(&mgr); }
}
'''
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'media-pause.c').write_text(prefix)
    build = (PROJECT / 'scratch/http-timeout-review/build.sh').read_text()
    build = build.replace('scratch/http-timeout-review/minimal.c', str(OUT / 'media-pause.c'))
    build = build.replace('scratch/http-timeout-review/minimal.arm', str(OUT / 'media-pause.arm'))
    result = subprocess.run(['sh', '-c', build], capture_output=True, text=True)
    (OUT / 'media-pause-build.log').write_text(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError(f'ARM build exit {result.returncode}: {result.stderr[-2000:]}')
    report = {'scope': __doc__, 'build_exit_code': result.returncode, 'passed': False, 'cases': []}

    def check(name, passed):
        report['cases'].append({'name': name, 'passed': bool(passed)})
        if not passed:
            raise AssertionError(name)

    with tempfile.TemporaryDirectory(prefix='nx-pause-', dir='/dev/shm') as temp:
        root = Path(temp)
        state = root / 'state'
        state.mkdir()
        shutil.copyfile(PROJECT / 'mod/deployment/common.sh', root / 'common.sh')
        shutil.copyfile(PROJECT / 'mod/deployment/mfw/panel-actions.sh', root / 'mfw-panel-actions.sh')
        media = root / 'DCIM'
        media.mkdir()
        sysfs = root / 'sysfs'
        primary = sysfs / 'devices/platform/usb_mass_storage/lun1/file'
        secondary = sysfs / 'class/usb_mode/usb0/f_mass_storage/lun1/file'
        cable = sysfs / 'devices/platform/jack/usb_online'
        mode = sysfs / 'devices/platform/usb_mode/UsbMenuSel'
        for path in (primary, secondary, cable, mode):
            path.parent.mkdir(parents=True, exist_ok=True)
        mountinfo = root / 'mountinfo'
        mountinfo.write_text(f'1 0 0:1 / / rw - tmpfs tmpfs rw\n2 1 179:1 / {root} ro - exfat /dev/mmcblk0 ro\n')
        photo = media / 'large.jpg'
        small = bytes(range(256)) * 256
        (media / 'small.jpg').write_bytes(small)
        (media / '.small.jpg.nxoriginal').write_bytes(small[::-1])
        with photo.open('wb') as file:
            file.truncate(32 * 1024 * 1024)
        child_file = root / 'child.pid'
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
        env = dict(os.environ, NX_STATE_DIR=str(state), NX300M_MOD_ROOT=str(root),
                   NX_PROBE_MEDIA=str(media), NX_PROBE_PHOTO=str(photo),
                   NX_PROBE_SYSFS=str(sysfs), NX_PROBE_MOUNTINFO=str(mountinfo),
                   NX_PROBE_CHILD=str(child_file), NX_PROBE_LISTEN=f'127.0.0.1:{port}')

        def request(path, method='GET'):
            conn = http.client.HTTPConnection('127.0.0.1', port, timeout=4)
            try:
                conn.request(method, path, headers={'Connection': 'close'})
                response = conn.getresponse()
                return response.status, json.loads(response.read())
            finally:
                conn.close()

        def wait_for(function, seconds=5):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                try:
                    value = function()
                    if value:
                        return value
                except (OSError, http.client.HTTPException):
                    pass
                time.sleep(.03)
            raise TimeoutError('probe condition not reached')

        process = None
        slow = None
        child_pid = None
        lock = (state / 'media-io.lock').open('a+b')
        with (OUT / 'media-pause-server.log').open('w') as log:
            def start():
                worker = subprocess.Popen([str(STOCK / 'qemu-arm-static'), '-L', str(STOCK),
                                           str(OUT / 'media-pause.arm')], env=env, stdout=log, stderr=log)
                wait_for(lambda: request('/api/v1/health')[0] == 200)
                return worker

            try:
                process = start()
                check('pause API method checked', request('/api/v1/media/pause')[0] == 405)
                check('active RAM service detected', request('/api/v1/media/status')[1]['available'])
                observation = request('/api/v1/storage/status')[1]
                check('absent USB nodes stay unknown', observation['raw_backing'] == 'unknown' and
                      observation['usb_cable'] == 'unknown' and observation['usb_mode'] is None)
                check('mountinfo resolves deepest matching mount without SD stat', observation['mount']['id'] == 2 and
                      observation['mount']['filesystem'] == 'exfat' and not observation['mount']['changed_since_start'])
                check('storage observation is GET only', request('/api/v1/storage/status', 'POST')[0] == 405)
                primary.write_text('')
                cable.write_text('0\n')
                mode.write_text('2\n')
                observation = request('/api/v1/storage/status')[1]
                check('empty readable LUN is closed, cable is disconnected', observation['raw_backing'] == 'closed' and
                      observation['usb_cable'] == 'disconnected' and observation['usb_mode'] == 2)
                secondary.symlink_to(primary)
                check('LUN read failure does not become closed', request('/api/v1/storage/status')[1]['raw_backing'] == 'unknown')
                secondary.unlink()
                check('backup download capability explicit', request('/api/v1/gallery/upload/capabilities')[1]['original_download_available'])
                original = http.client.HTTPConnection('127.0.0.1', port, timeout=4)
                try:
                    original.request('GET', '/api/v1/gallery/original?p=small.jpg')
                    response = original.getresponse()
                    check('original backup returns exact immutable bytes', response.status == 200 and response.read() == small[::-1])
                finally:
                    original.close()
                check('original backup route rejects POST and path escape', request('/api/v1/gallery/original?p=small.jpg', 'POST')[0] == 405 and
                      request('/api/v1/gallery/original?p=../outside.jpg')[0] == 400)
                backup = media / '.small.jpg.nxoriginal'
                backup.unlink()
                backup.symlink_to(media / 'small.jpg')
                check('symlink original backup refused', request('/api/v1/gallery/original?p=small.jpg')[0] == 409)
                backup.unlink()
                backup.write_bytes(small[::-1])
                download = http.client.HTTPConnection('127.0.0.1', port, timeout=4)
                try:
                    download.request('GET', '/DCIM/small.jpg')
                    response = download.getresponse()
                    check('ordinary download complete with Connection close', response.status == 200 and
                          response.getheader('Connection') == 'close' and response.read() == small)
                finally:
                    download.close()

                def photo_fds():
                    return [fd.name for fd in Path(f'/proc/{process.pid}/fd').iterdir()
                            if fd.is_symlink() and os.readlink(fd) == str(photo)]

                pipeline = socket.socket()
                try:
                    pipeline.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
                    pipeline.connect(('127.0.0.1', port))
                    pipeline.sendall(b'GET /DCIM/large.jpg HTTP/1.1\r\nHost: localhost\r\n\r\n')
                    wait_for(lambda: bool(photo_fds()))
                    pipeline.sendall(b'GET /DCIM/large.jpg HTTP/1.1\r\nHost: localhost\r\n\r\n')
                    wait_for(lambda: request('/api/v1/media/status')[1]['file_connections'] == 0)
                    check('second pipelined file request cannot leak original photo FD', not photo_fds())
                finally:
                    pipeline.close()
                slow = socket.socket()
                slow.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
                slow.connect(('127.0.0.1', port))
                slow.sendall(b'GET /DCIM/large.jpg HTTP/1.1\r\nHost: localhost\r\n\r\n')
                wait_for(lambda: request('/api/v1/media/status')[1]['file_connections'] == 1)

                check('slow download really holds photo FD', bool(photo_fds()))
                check('fixture child launched through production qs_launch', request('/probe/start-child', 'POST')[0] == 202)
                wait_for(child_file.exists)
                child_pid = int(child_file.read_text())
                fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
                status, paused = request('/api/v1/media/pause', 'POST')
                check('pause accepted but not released during panel/child drain', status == 202 and paused['paused'] and not paused['service_media_released'])
                check('new media and remote mutations blocked', all(request(p, 'POST')[0] == 503 for p in
                      ['/DCIM/large.jpg', '/cgi-bin/dirlist', '/api/v1/gallery/upload', '/api/v1/quickshare/send', '/api/v1/input/inject']))
                check('RAM health remains available', request('/api/v1/health')[0] == 200)
                wait_for(lambda: request('/api/v1/media/status')[1]['file_connections'] == 0)
                check('download photo FD actually closed', not photo_fds())
                check('panel shared lock delays release', not request('/api/v1/media/status')[1]['service_media_released'])
                fcntl.flock(lock, fcntl.LOCK_UN)
                released = wait_for(lambda: (v if (v := request('/api/v1/media/status')[1])['service_media_released'] else None))
                check('SIGTERM ignoring child killed and reaped before release', not Path(f'/proc/{child_pid}').exists() and not released['quickshare_running'])
                check('release does not claim stock USB handoff', not released['stock_usb_handoff_verified'] and not released['resume_supported'])
                panel = subprocess.run(['sh', str(root / 'mfw-panel-actions.sh'), 'disable-autostart'],
                                       env=env, capture_output=True, text=True)
                check('production panel action blocked before source write', panel.returncode != 0 and
                      'paused' in panel.stderr and not (root / 'DISABLED').exists())
                check('pause idempotent', request('/api/v1/media/pause', 'POST')[1]['paused'])
                process.terminate()
                process.wait(timeout=5)
                process = start()
                check('pause survives service restart', request('/api/v1/media/status')[1]['paused'] and request('/DCIM/large.jpg')[0] == 503)
                check('backup download blocked while paused', request('/api/v1/gallery/original?p=small.jpg')[0] == 503)
                process.terminate()
                process.wait(timeout=5)
                # Reset only the private fixture, modelling a fresh test case;
                # there is deliberately no production resume API.
                (state / 'media-paused').unlink()
                process = start()
                primary.write_text('/dev/mmcblk0\n')
                cable.write_text('1\n')
                observation = request('/api/v1/storage/status')[1]
                paused = request('/api/v1/media/status')[1]
                check('observed open backing with cable auto-pauses and persists', observation['raw_backing'] == 'open' and
                      paused['paused'] and paused['pause_persisted'] and paused['pause_reason'] == 'usb_backing_open')
                primary.write_text('')
                mountinfo.write_text(f'1 0 0:1 / / rw - tmpfs tmpfs rw\n3 1 179:1 / {root} ro - exfat /dev/mmcblk0 ro\n')
                observation = request('/api/v1/storage/status')[1]
                check('new mount and closed LUN never automatically resume', observation['raw_backing'] == 'closed' and
                      observation['mount']['changed_since_start'] and observation['media_paused'] and
                      not observation['stock_usb_handoff_verified'] and request('/DCIM/small.jpg')[0] == 503)
                report['passed'] = True
            finally:
                if slow:
                    slow.close()
                lock.close()
                if process and process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
                if child_pid and Path(f'/proc/{child_pid}').exists():
                    os.kill(child_pid, 9)
                (OUT / 'media-pause-report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
