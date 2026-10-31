"""Send one generated JPEG from the stock ARM gallery through real Quick Share.

Camera and controlled receiver use separate private net namespaces joined only
by a veth pair. No discovery or transfer event fixture is used.
"""
import argparse
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import struct
import sys
import threading
import time

from PIL import Image, ImageDraw
import check_stock_gallery_share as gallery

ROOT = Path(__file__).resolve().parents[1]
GUEST = gallery.GUEST
CAMERA_IP = '192.0.2.1'
RECEIVER_IP = '192.0.2.2'


def wsl(*args, timeout=20, **kwargs):
    return subprocess.run(['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', *map(str, args)],
                          check=True, capture_output=True, timeout=timeout, **kwargs).stdout


def python(code, *args, **kwargs):
    return wsl('python3', '-c', code, *args, **kwargs)


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')


def qemu_path(value):
    if value is None:
        return GUEST+'/qemu-arm-static'
    return value if value.startswith('/') else gallery.wsl_path(Path(value).resolve())


def identity(pid):
    return json.loads(python('''import json,pathlib,sys
p=pathlib.Path('/proc')/sys.argv[1]
try:
 s=(p/'stat').read_text(); fields=s[s.rfind(')')+2:].split()
 print(json.dumps({'pid':int(sys.argv[1]),'start_ticks':int(fields[19]),
 'mount_namespace':str((p/'ns/mnt').readlink()),'network_namespace':str((p/'ns/net').readlink()),
 'argv':(p/'cmdline').read_bytes().decode(errors='replace').split('\\0')[:-1]}))
except (FileNotFoundError,ProcessLookupError): print('null')
''', str(pid)))


def generated_exif(image):
    """Standard DCF EXIF, including a real generated JPEG in IFD1.

    The stock gallery needs thumbnail/ExifIFD data before it submits a decode
    task. All values here are constants; no user-photo metadata is copied.
    """
    thumbnail = image.resize((160, 120))
    buffer = io.BytesIO()
    thumbnail.save(buffer, format='JPEG', quality=85, subsampling=2)
    thumbnail = buffer.getvalue()
    ascii_value = lambda value: value.encode('ascii')+b'\0'
    short = lambda value: struct.pack('<H', value)
    long = lambda value: struct.pack('<I', value)
    rational = lambda numerator, denominator: struct.pack('<II', numerator, denominator)
    date = ascii_value('2026:10:21 12:00:00')
    ifd0 = [(0x010e,2,ascii_value('Generated Quick Share test pixels')),
            (0x010f,2,ascii_value('SAMSUNG')), (0x0110,2,ascii_value('NX300M')),
            (0x0112,3,short(1)), (0x011a,5,rational(72,1)), (0x011b,5,rational(72,1)),
            (0x0128,3,short(2)), (0x0131,2,ascii_value('NX300M generated probe')),
            (0x0132,2,date), (0x8769,4,long(0))]
    exif = [(0x829a,5,rational(1,125)), (0x829d,5,rational(28,10)),
            (0x8822,3,short(3)), (0x8827,3,short(100)), (0x9000,7,b'0230'),
            (0x9003,2,date), (0x9004,2,date), (0x9101,7,b'\1\2\3\0'),
            (0xa000,7,b'0100'), (0xa001,3,short(1)),
            (0xa002,4,long(image.width)), (0xa003,4,long(image.height)), (0xa005,4,long(0))]
    interoperability = [(0x0001,2,b'R98\0'), (0x0002,7,b'0100')]
    ifd1 = [(0x0100,4,long(160)), (0x0101,4,long(120)), (0x0103,3,short(6)),
            (0x011a,5,rational(72,1)), (0x011b,5,rational(72,1)), (0x0128,3,short(2)),
            (0x0201,4,long(0)), (0x0202,4,long(len(thumbnail)))]
    directories = [ifd0, exif, interoperability, ifd1]
    offsets, cursor = [], 8
    for directory in directories:
        offsets.append(cursor)
        cursor += 2+12*len(directory)+4
    ifd0[-1] = (0x8769,4,long(offsets[1]))
    exif[-1] = (0xa005,4,long(offsets[2]))
    result = bytearray(b'II'+short(42)+long(8))
    result.extend(b'\0'*(cursor-8))
    widths = {2:1, 3:2, 4:4, 5:8, 7:1}
    thumbnail_pointer = None
    for index, directory in enumerate(directories):
        at = offsets[index]
        struct.pack_into('<H',result,at,len(directory))
        for n, (tag, kind, value) in enumerate(sorted(directory)):
            position = at+2+n*12
            struct.pack_into('<HHI',result,position,tag,kind,len(value)//widths[kind])
            if len(value)<=4:
                result[position+8:position+12] = value.ljust(4,b'\0')
            else:
                struct.pack_into('<I',result,position+8,len(result))
                result.extend(value)
                if len(result)&1:
                    result.append(0)
            if index==3 and tag==0x0201:
                thumbnail_pointer = position+8
        struct.pack_into('<I',result,at+2+12*len(directory),offsets[3] if index==0 else 0)
    struct.pack_into('<I',result,thumbnail_pointer,len(result))
    result.extend(thumbnail)
    return b'Exif\0\0'+result


def generated_jpeg(path):
    image = Image.new('RGB', (640, 480))
    pixels = image.load()
    for y in range(480):
        for x in range(640):
            pixels[x, y] = (x*255//639, y*255//479, 48 if (x//40+y//40)%2 else 208)
    draw = ImageDraw.Draw(image)
    draw.rectangle((30, 25, 610, 115), fill='white')
    draw.text((50, 55), 'GENERATED NX300M QUICK SHARE TEST', fill='black')
    image.save(path, format='JPEG', quality=91, subsampling=2, exif=generated_exif(image))
    with Image.open(path) as decoded:
        decoded.load()
        return {'generated':True, 'bytes':path.stat().st_size, 'size':list(decoded.size),
                'format':decoded.format, 'thumbnail_size':[160,120],
                'exif_pixel_dimensions':[decoded.getexif().get_ifd(0x8769)[tag] for tag in (0xa002,0xa003)],
                'user_photo_used':False}


def task_status(output):
    data = gallery.namespace("""python3 - <<'PY'
import http.client
connection=http.client.HTTPConnection('127.0.0.1',8781,timeout=4)
connection.request('GET','/api/v1/quickshare/status')
response=connection.getresponse()
if response.status!=200: raise RuntimeError('Unexpected task status HTTP '+str(response.status))
print(response.read().decode())
connection.close()
PY
""")
    status = json.loads(data)
    with (output/'task-status.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(status)+'\n')
    return status


def protocol_events(status):
    return [json.loads(item['line']) for item in status.get('events', [])]


def panel_present():
    info = json.loads(gallery.input_action('info', []))
    return any(item['title']=='NX300M Quick Share' and item['viewable'] for item in info['windows'])


def wait_until(predicate, seconds, description):
    deadline = time.monotonic()+seconds
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(.25)
    raise TimeoutError(description)


def configure_network(camera, peer):
    def net(pid, *args):
        return wsl('nsenter', '-t', pid, '--net', *args)
    # Create the pair directly inside the camera's already isolated namespace.
    net(camera, 'ip', 'link', 'add', 'qs-camera', 'type', 'veth', 'peer', 'name', 'qs-receiver')
    net(camera, 'ip', 'link', 'set', 'qs-receiver', 'netns', peer)
    for pid, name, address in ((camera, 'qs-camera', CAMERA_IP), (peer, 'qs-receiver', RECEIVER_IP)):
        net(pid, 'ip', 'link', 'set', 'lo', 'up')
        net(pid, 'ip', 'addr', 'add', address+'/30', 'dev', name)
        net(pid, 'ip', 'link', 'set', name, 'up')
        net(pid, 'ip', 'route', 'add', '224.0.0.0/4', 'dev', name)
    topology = {}
    for label, pid in (('camera', camera), ('receiver', peer)):
        addresses = json.loads(net(pid, 'ip', '-j', 'addr', 'show'))
        routes = json.loads(net(pid, 'ip', '-j', 'route', 'show'))
        if any(item.get('dst')=='default' for item in routes):
            raise RuntimeError('Unexpected default route in isolated test network')
        if set(item['ifname'] for item in addresses) != {'lo', 'qs-'+label}:
            raise RuntimeError('Unexpected interface in isolated test network')
        topology[label] = {'interfaces':addresses, 'routes':routes}
    return topology


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path,
                        default=ROOT/'outputs/quickshare/gallery/stock-protocol-transfer')
    parser.add_argument('--startup-seconds', type=int, default=20)
    parser.add_argument('--qemu', help='Use this host runner and bind it only into the private camera namespace')
    parser.add_argument('--cancel-at-pin', action='store_true', help='Close the real panel while waiting for receiver consent; verify cancellation and gallery return')
    parser.add_argument('--cancel-key', choices=('Escape', 'Super_L'), default='Escape',
                        help='Real panel key used by --cancel-at-pin; Super_L is the stock half-press')
    args = parser.parse_args()
    if not 5 <= args.startup_seconds <= 60:
        parser.error('Use a bounded startup wait of 5..60 seconds')
    if os.name != 'nt':
        parser.error('Launch using the project Conda Python on Windows')
    output = args.output_dir.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error('Use a fresh output directory to preserve prior evidence')
    output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
    card = '/opt/nx300m-lab/media/nx300m-qs-generated-'+stamp+'.img'
    receiver_name = 'NX300M-Controlled-'+stamp
    runner = qemu_path(args.qemu)
    receiver_binary = ROOT/'outputs/quickshare/controlled_receiver-armv7'
    sender_binary = ROOT/'outputs/quickshare/nx-quickshare-armv7'
    for path in (receiver_binary, sender_binary):
        if not path.is_file():
            raise RuntimeError('Build the required ARM binary first: '+str(path))
    if python('import pathlib,sys; print(pathlib.Path(sys.argv[1]).exists())', card).strip()!=b'False':
        raise RuntimeError('The generated-only card must be new')
    photo = output/'generated.JPG'
    received = output/'received'
    received.mkdir()
    main_session = json.loads((ROOT/'outputs/firmware-runtime/session.json').read_text())
    protected = int(main_session['namespace_pid'])
    protected_before = identity(protected)
    if not protected_before:
        raise RuntimeError('Cannot identify the protected main simulator')
    report = {'generated_photo':generated_jpeg(photo), 'passed':False,
              'actual_stock_gallery':True, 'actual_arm_panel':True, 'actual_arm_service':True,
              'actual_arm_protocol':True, 'protocol_fixture_used':False,
              'protected_main_before':protected_before, 'card_image':card,
              'qemu_runner':runner, 'qemu_version':wsl(runner,'--version').decode().strip(),
              'started_utc':datetime.now(timezone.utc).isoformat()}
    gallery.OUTPUT = output
    gallery.CARD = card
    peer = None
    peer_identity = None
    receiver = None
    receiver_identity = None
    receiver_reader = None
    receiver_messages = []
    receiver_stdout = (output/'receiver.jsonl').open('w', encoding='utf-8')
    receiver_stderr = (output/'receiver.stderr.log').open('wb')
    try:
        gallery.start(software_playback=True, generated_photo=photo, qemu=args.qemu)
        camera = int(gallery.state()['namespace_pid'])
        if camera == protected:
            raise RuntimeError('Independent gallery launch selected the protected namespace')
        report['camera_qemu_version'] = gallery.namespace(shlex.quote(GUEST+'/qemu-arm-static')+' --version\n').decode().strip()
        if report['camera_qemu_version']!=report['qemu_version']:
            raise RuntimeError('Private camera runner does not match the requested protocol runner')
        gallery.deploy(photo)
        gallery.namespace('set -eu\ncp '+shlex.quote(gallery.wsl_path(sender_binary))+' '+
                          shlex.quote(GUEST+'/mnt/mmc/nx300m-mod/bin/nx-quickshare-armv7')+'\n')
        seeded = json.loads(gallery.namespace("""python3 - <<'PY'
import json,pathlib
root=pathlib.Path('/opt/nx300m-lab/stock-1.15')
print(json.dumps({'runtime':json.loads((root/'var/run/nx300m-gallery-runtime.json').read_text()),
 'files':[str(p.relative_to(root/'mnt/mmc/DCIM')) for p in (root/'mnt/mmc/DCIM').rglob('*') if p.is_file()]}))
PY
"""))
        if seeded['files'] != ['100PHOTO/SAM_4262.JPG']:
            raise RuntimeError('Generated-only media check failed: '+repr(seeded['files']))
        report['media'] = seeded
        keeper_marker = gallery.wsl_path(output/'receiver-namespace.pid')
        peer = int(python('''import pathlib,subprocess,sys,time
marker=pathlib.Path(sys.argv[1])
p=subprocess.Popen(['unshare','--net','sh','-c','echo $$ > "$1"; exec sleep 300','sh',str(marker)],
 stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
for _ in range(100):
 if marker.exists(): print(marker.read_text().strip()); break
 if p.poll() is not None: raise RuntimeError('Receiver namespace launch failed')
 time.sleep(.05)
else: raise RuntimeError('Receiver namespace marker missing')
''', keeper_marker))
        peer_identity = identity(peer)
        report['network'] = configure_network(camera, peer)
        write_json(output/'network.json', report['network'])
        command = 'echo $$ > '+shlex.quote(gallery.wsl_path(output/'receiver.pid'))+'; exec '+shlex.join([
            'nsenter', '-t', str(peer), '--net', 'env', 'NX_QS_DEVICE_NAME='+receiver_name,
            runner, '-cpu', 'cortex-a9', str(gallery.wsl_path(receiver_binary)),
            gallery.wsl_path(received)])
        receiver = subprocess.Popen(['wsl.exe','-d','Debian','-u','root','-e','sh','-c',command],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=receiver_stderr,
                                    text=True, encoding='utf-8', bufsize=1)

        def read_receiver():
            for line in receiver.stdout:
                receiver_stdout.write(line)
                receiver_stdout.flush()
                try:
                    receiver_messages.append(json.loads(line))
                except ValueError:
                    pass
        receiver_reader = threading.Thread(target=read_receiver, daemon=True)
        receiver_reader.start()
        def receiver_process():
            marker = output/'receiver.pid'
            if not marker.is_file():
                return None
            actual = identity(int(marker.read_text()))
            return actual if actual and gallery.wsl_path(receiver_binary) in actual['argv'] else None
        receiver_identity = wait_until(receiver_process, 8, 'Controlled receiver process did not exec')
        wait_until(lambda:any(item.get('event')=='ready' for item in receiver_messages), 20,
                   'Controlled ARM receiver did not become ready')
        report['receiver_ready'] = next(item for item in receiver_messages if item.get('event')=='ready')
        print('Controlled receiver ready; waiting for stock gallery', flush=True)
        time.sleep(args.startup_seconds)
        gallery.input_action('key', ['XF86Tools'])
        time.sleep(2)
        gallery.frame('01-stock-playback')
        gallery.input_action('key', ['Menu'])
        time.sleep(.7)
        gallery.frame('02-stock-menu')
        gallery.input_action('key', ['KP_Enter'])
        wait_until(panel_present, 12, 'Original gallery Share(Wi-Fi) did not launch the ARM panel')
        report['stock_menu_opened_panel'] = True

        def discovered():
            status = task_status(output)
            for item in protocol_events(status):
                endpoint = item.get('endpoint', {})
                if (item.get('event')=='discovered' and endpoint.get('name')==receiver_name and
                        endpoint.get('ip')==RECEIVER_IP and endpoint.get('present')):
                    return {'status':status, 'endpoint':endpoint}
            return None
        discovery = wait_until(discovered, 25, 'Real ARM mDNS discovery did not find the controlled receiver')
        write_json(output/'discovery.json', discovery)
        report['discovered_endpoint'] = discovery['endpoint']
        gallery.frame('03-real-discovery')
        # The one receiver is already the selected row. This key goes to the
        # actual panel; the harness never submits an HTTP send request.
        gallery.input_action('key', ['Return'])
        send_pin = receive_pin = consent_id = None
        approved = False
        sender_finished = False
        deadline = time.monotonic()+70
        while time.monotonic() < deadline:
            status = task_status(output)
            for item in protocol_events(status):
                if item.get('event')!='transfer':
                    continue
                message = item['message']
                pin = (message.get('meta') or {}).get('pin_code')
                if pin:
                    send_pin = pin
                if message.get('state')=='Finished':
                    sender_finished = True
            for item in list(receiver_messages):
                if item.get('event')!='protocol':
                    continue
                message = item.get('message', {})
                pin = (message.get('meta') or {}).get('pin_code')
                if pin:
                    receive_pin = pin
                if message.get('state')=='WaitingForUserConsent':
                    consent_id = message['id']
            if send_pin and receive_pin and consent_id and not approved:
                if send_pin != receive_pin:
                    raise RuntimeError('Independently derived sender/receiver PIN mismatch')
                gallery.frame('04-real-pin')
                if args.cancel_at_pin:
                    report['consent'] = {'pins_equal':True, 'approved':False}
                    gallery.input_action('key', [args.cancel_key])
                    report['cancellation_key'] = args.cancel_key
                    def cancelled_task():
                        current = task_status(output)
                        return current if not current['running'] else None
                    final = wait_until(cancelled_task, 8, 'Cancellation did not stop the sender')
                    events = protocol_events(final)
                    if final['state']!='cancelled' or not final['cancel_requested']:
                        raise RuntimeError('Accepted cancellation lost its terminal state: '+repr(final))
                    if any(e.get('message', {}).get('state')=='Finished' for e in events):
                        raise RuntimeError('Cancelled transfer unexpectedly reported Finished')
                    if final.get('signal') is not None or final.get('exit_code') in (None, 0):
                        raise RuntimeError('Sender did not exit cooperatively with a nonzero code: '+repr(final))
                    report['sender_final'] = final
                    report['cancelled_at_pin'] = True
                    break
                receiver.stdin.write(json.dumps({'action':'accept','id':consent_id,'pin':send_pin})+'\n')
                receiver.stdin.flush()
                approved = True
                report['consent'] = {'pins_equal':True, 'pin':send_pin, 'id':consent_id}
            if sender_finished and not status['running']:
                if status['exit_code']!=0:
                    raise RuntimeError('Real ARM sender failed after Finished: '+repr(status))
                report['sender_final'] = status
                break
            if receiver.poll() is not None:
                raise RuntimeError('Controlled receiver exited before sender completion')
            time.sleep(.25)
        else:
            raise TimeoutError('Stock-panel protocol transfer did not finish')
        if args.cancel_at_pin:
            wait_until(lambda:any(item.get('event')=='protocol' and
                       item.get('message', {}).get('state')=='Cancelled'
                       for item in receiver_messages), 8,
                       'Receiver did not observe the real protocol cancellation')
            report['receiver_observed_cancel'] = True
            if any(path.is_file() for path in received.rglob('*')):
                raise RuntimeError('Receiver wrote a file without consent')
            report['received'] = {'files':0, 'consent_granted':False}
        else:
            files = [path for path in received.rglob('*') if path.is_file()]
            if len(files)!=1 or files[0].name!='SAM_4262.JPG' or files[0].read_bytes()!=photo.read_bytes():
                raise RuntimeError('Receiver files differ from the generated JPEG')
            with Image.open(files[0]) as decoded:
                decoded.load()
                if decoded.format!='JPEG' or decoded.size!=(640,480):
                    raise RuntimeError('Received JPEG did not decode to the generated size')
            report['received'] = {'file':str(files[0]), 'bytes':files[0].stat().st_size,
                                  'bytes_equal':True, 'decoded_size':[640,480], 'decoded_format':'JPEG'}
            receiver.stdin.write(json.dumps({'action':'finish'})+'\n')
            receiver.stdin.flush()
            if receiver.wait(timeout=15)!=0:
                raise RuntimeError('Controlled receiver did not stop successfully')
            wait_until(lambda:any(item.get('event')=='receiver_stopped' for item in receiver_messages), 3,
                       'Receiver completion marker missing')
            gallery.frame('05-receiver-confirmed')
        if not args.cancel_at_pin:
            gallery.input_action('key', ['Escape'])
        wait_until(lambda:not panel_present(), 8, 'Panel did not close back to the original gallery')
        gallery.frame('06-returned-gallery')
        gallery.input_action('key', ['Menu'])
        time.sleep(.6)
        gallery.frame('07-stock-menu-after-return')
        report['panel_returned_to_stock_ui'] = True
        if not args.cancel_at_pin or args.cancel_key=='Escape':
            report['panel_returned_to_stock_gallery'] = True
        report['passed'] = True
    except Exception as exc:
        report['error'] = type(exc).__name__+': '+str(exc)
        raise
    finally:
        if receiver is not None and receiver.poll() is None:
            if receiver_identity and identity(receiver_identity['pid'])==receiver_identity:
                python('import os,signal,sys; os.kill(int(sys.argv[1]),signal.SIGTERM)', receiver_identity['pid'])
            receiver.terminate()
            try:
                receiver.wait(timeout=5)
            except subprocess.TimeoutExpired:
                receiver.kill()
        if peer_identity and identity(peer_identity['pid'])==peer_identity:
            python('import os,signal,sys; os.kill(int(sys.argv[1]),signal.SIGTERM)', peer_identity['pid'])
        if (output/'session.json').is_file():
            try:
                gallery.collect()
                write_json(output/'stopped-session.json', gallery.state())
                gallery.stop()
                report['independent_camera_stopped'] = True
            except Exception as exc:
                report['cleanup_error'] = type(exc).__name__+': '+str(exc)
                report['passed'] = False
        report['protected_main_after'] = identity(protected)
        report['protected_main_identity_unchanged'] = report['protected_main_after']==protected_before
        report['passed'] = report['passed'] and report['protected_main_identity_unchanged']
        report['completed_utc'] = datetime.now(timezone.utc).isoformat()
        if receiver_reader is not None:
            receiver_reader.join(timeout=3)
        receiver_stdout.close()
        receiver_stderr.close()
        write_json(output/'report.json', report)
    print(json.dumps({'passed':report['passed'], 'received':report.get('received'),
                      'protected_main_identity_unchanged':report['protected_main_identity_unchanged']}, indent=2))
    raise SystemExit(0 if report['passed'] else 1)


if __name__=='__main__':
    main()
