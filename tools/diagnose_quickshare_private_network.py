"""Bounded real-ARM discovery diagnosis in two private veth-only namespaces."""
import argparse
import json
from pathlib import Path
import shlex
import subprocess
import time

from probe_stock_quickshare_transfer import (
    ROOT, CAMERA_IP, configure_network, gallery, identity, python, qemu_path, wait_until, write_json, wsl,
)


def host_multicast_probe(pid, address):
    return json.loads(wsl('nsenter','-t',str(pid),'--net','python3','-c','''import json,socket,sys
ip=sys.argv[1]; results=[]
for name,option,value in [('IP_MULTICAST_IF',socket.IP_MULTICAST_IF,socket.inet_aton(ip)),
 ('IP_ADD_MEMBERSHIP',socket.IP_ADD_MEMBERSHIP,socket.inet_aton('224.0.0.251')+socket.inet_aton(ip))]:
 with socket.socket(socket.AF_INET,socket.SOCK_DGRAM,socket.IPPROTO_IP) as sock:
  result={'domain':'AF_INET','level':socket.IPPROTO_IP,'option':option,'name':name,'length':len(value)}
  try: sock.setsockopt(socket.IPPROTO_IP,option,value); result['errno']=0
  except OSError as error: result.update(errno=error.errno,error=str(error))
  results.append(result)
print(json.dumps(results))
''',address))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path,
                        default=ROOT/'outputs/quickshare/gallery/network-discovery-diagnostic')
    parser.add_argument('--strace', action='store_true', help='Record real ARM socket syscalls through QEMU')
    parser.add_argument('--host-sockets-only', action='store_true', help='Run only the native socket-option comparison')
    parser.add_argument('--qemu', help='Use this alternate host QEMU without changing the shared rootfs')
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error('Use a fresh output directory')
    output.mkdir(parents=True, exist_ok=True)
    received = output/'received'
    received.mkdir()
    owners = []
    receiver = None
    receiver_owner = None
    receiver_stderr = (output/'receiver.stderr.log').open('wb')
    receiver_stdout = (output/'receiver.jsonl').open('wb')
    report = {'actual_arm_sender':True, 'actual_arm_receiver':True, 'camera_started':False,
              'private_network_only':True, 'transfer_attempted':False}
    report['host_kernel'] = wsl('uname','-srvmo').decode().strip()
    runner = qemu_path(args.qemu)
    report['qemu_runner'] = runner
    report['qemu_version'] = wsl(runner,'--version').decode().strip()
    qemu_trace = ['-strace'] if args.strace else []
    try:
        for label in ('camera', 'receiver'):
            marker = gallery.wsl_path(output/(label+'-namespace.pid'))
            pid = int(python('''import pathlib,subprocess,sys,time
marker=pathlib.Path(sys.argv[1])
p=subprocess.Popen(['unshare','--net','sh','-c','echo $$ > "$1"; exec sleep 90','sh',str(marker)],
 stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
for _ in range(100):
 if marker.exists(): print(marker.read_text().strip()); break
 if p.poll() is not None: raise RuntimeError('Namespace launch failed')
 time.sleep(.05)
else: raise RuntimeError('Namespace marker missing')
''', marker))
            owners.append(identity(pid))
        camera, peer = [owner['pid'] for owner in owners]
        report['network'] = configure_network(camera, peer)
        write_json(output/'network.json', report['network'])
        report['host_socket_options'] = {
            'camera':host_multicast_probe(camera,'192.0.2.1'),
            'receiver':host_multicast_probe(peer,'192.0.2.2')}
        write_json(output/'host-socket-options.json',report['host_socket_options'])
        if args.host_sockets_only:
            print(json.dumps(report['host_socket_options'],indent=2),flush=True)
            return
        command = 'echo $$ > '+shlex.quote(gallery.wsl_path(output/'receiver.pid'))+'; exec '+shlex.join([
            'nsenter', '-t', str(peer), '--net', 'env', 'NX_QS_DEVICE_NAME=NX300M-Network-Diagnostic',
            'RUST_LOG=mdns_sd=debug,rqs_lib=debug', runner, '-cpu', 'cortex-a9',
            *qemu_trace,
            gallery.wsl_path(ROOT/'outputs/quickshare/controlled_receiver-armv7'), gallery.wsl_path(received)])
        receiver = subprocess.Popen(['wsl.exe','-d','Debian','-u','root','-e','sh','-c',command],
                                    stdin=subprocess.PIPE, stdout=receiver_stdout, stderr=receiver_stderr)
        def receiver_process():
            marker = output/'receiver.pid'
            if not marker.is_file():
                return None
            actual = identity(int(marker.read_text()))
            binary = gallery.wsl_path(ROOT/'outputs/quickshare/controlled_receiver-armv7')
            return actual if actual and binary in actual['argv'] else None
        receiver_owner = wait_until(receiver_process, 8, 'Controlled receiver process did not exec')
        def ready():
            lines = (output/'receiver.jsonl').read_text().splitlines()
            return any(json.loads(line).get('event')=='ready' for line in lines if line.strip())
        wait_until(ready, 15, 'ARM receiver did not become ready')
        print('Private ARM receiver ready; running real ARM discovery for 10 seconds', flush=True)
        process = subprocess.run(['wsl.exe','-d','Debian','-u','root','-e','nsenter','-t',str(camera),'--net',
                                  'env','RUST_LOG=mdns_sd=debug,rqs_lib=debug',runner,
                                  '-cpu','cortex-a9',*qemu_trace,gallery.wsl_path(ROOT/'outputs/quickshare/nx-quickshare-armv7'),
                                  'discover','10'], capture_output=True, timeout=25)
        (output/'sender.jsonl').write_bytes(process.stdout)
        (output/'sender.stderr.log').write_bytes(process.stderr)
        report['sender_exit_code'] = process.returncode
        report['sender_events'] = [json.loads(line) for line in process.stdout.splitlines() if line.strip()]
        # A separate real DNS query distinguishes advertisement failure from
        # the production ARM browser's filtering or socket behavior.
        query = subprocess.run(['wsl.exe','-d','Debian','-u','root','-e','nsenter','-t',str(camera),'--net',
                                'python3',gallery.wsl_path(ROOT/'tools/quickshare_mdns_probe.py'),
                                '--interface',CAMERA_IP,'--seconds','3'],capture_output=True,timeout=12)
        (output/'dns-query.jsonl').write_bytes(query.stdout)
        (output/'dns-query.stderr.log').write_bytes(query.stderr)
        report['dns_query_exit_code'] = query.returncode
        report['dns_query_events'] = [json.loads(line) for line in query.stdout.splitlines() if line.strip()]
        report['receiver_still_running'] = receiver.poll() is None
    finally:
        if receiver_owner and identity(receiver_owner['pid'])==receiver_owner:
            python('import os,signal,sys; os.kill(int(sys.argv[1]),signal.SIGTERM)',receiver_owner['pid'])
        if receiver is not None:
            try:
                receiver.wait(timeout=4)
            except subprocess.TimeoutExpired:
                receiver.terminate()
                receiver.wait(timeout=4)
        for owner in owners:
            if identity(owner['pid'])==owner:
                python('import os,signal,sys; os.kill(int(sys.argv[1]),signal.SIGTERM)',owner['pid'])
        receiver_stdout.close()
        receiver_stderr.close()
        write_json(output/'report.json',report)
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    main()
