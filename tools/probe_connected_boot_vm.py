"""Narrow native ARM regression of the published combined MFW boot entry.

Only private QEMU sockets and output files are used. No physical camera, card,
main emulator, panel, exposure or restoration test is involved.
"""
import argparse
import base64
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import socket
import struct
import subprocess
import tarfile
import threading
import time
import urllib.request
import zipfile

import arm_vm
import probe_mfw_ram_vm as mfw

PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT / 'outputs/deployment/mfw-connected'
OUT = ROOT / 'arm-vm'
SOCKET = '/tmp/nx300m-connected-private'
KERNEL_RELEASE = '6.12.107+deb13-armmp'
INITRD_URL = arm_vm.DEBIAN + 'dists/trixie/main/installer-armhf/current/images/netboot/initrd.gz'

INIT = r'''#!/rescue/busybox sh
export PATH=/bin:/sbin:/usr/bin:/usr/sbin
/rescue/busybox mount -t devtmpfs devtmpfs /dev
exec </dev/ttyAMA0 >/dev/ttyAMA0 2>&1
/rescue/busybox mount -t proc proc /proc
/rescue/busybox mount -t sysfs sysfs /sys
/rescue/busybox mkdir -p /tmp /dev/pts /mnt/mmc /arm-vm/connected-results
/rescue/busybox mount -t devpts devpts /dev/pts
/rescue/busybox mount -t tmpfs -o rw,exec,mode=1777,size=16m connected-runtime /tmp
/rescue/busybox ifconfig lo 127.0.0.1 up
/rescue/busybox mount --bind /arm-vm/mfw-source /mnt/mmc
/bin/sh /arm-vm/load-network.sh > /arm-vm/connected-results/network-init.log 2>&1
echo CONNECTED_PRIVATE_SHELL_READY
while true; do /rescue/busybox setsid /bin/sh -i < /dev/ttyAMA0 > /dev/ttyAMA0 2>&1; done
'''

PROBE = r'''#!/bin/sh
set -eu
OUT=/arm-vm/connected-results
RAM=/tmp/nx300m-mfw
gather() {
    result=$?
    trap - 0
    [ ! -f "$RAM/bootstrap.log" ] || cp "$RAM/bootstrap.log" "$OUT/bootstrap.log"
    for item in remote.log supervisor.log mfw-entry.log; do
        [ ! -f "$RAM/state/$item" ] || cp "$RAM/state/$item" "$OUT/$item"
    done
    [ ! -d /mnt/mmc/nx300m-lens-debug/logs ] || cp -R /mnt/mmc/nx300m-lens-debug/logs "$OUT/wifi-logs"
    [ ! -f /mnt/mmc/nx300m-lens-debug/wifi-mem-entry.log ] ||
        cp /mnt/mmc/nx300m-lens-debug/wifi-mem-entry.log "$OUT/wifi-entry.log"
    df -Pk /tmp > "$OUT/tmpfs.txt"
    printf '%s\n' "$result" > "$OUT/probe-exit.txt"
    exit "$result"
}
trap gather 0
export NX_START_TIMEOUT=30 NX_STOP_TIMEOUT=10
uname -a > "$OUT/uname.txt"
ifconfig -a > "$OUT/interface.txt"
if [ -f /arm-vm/network-enabled ]; then grep -q '192.0.2.2' "$OUT/interface.txt"; fi
# The package's root entry supplies the production environment itself.
/bin/sh /mnt/mmc/autoexec.sh > "$OUT/first-entry.log" 2>&1
attempt=0
until [ -f "$RAM/state/remote.pid" ] && grep -q '^ready$' "$RAM/state/service.state"; do
    attempt=$((attempt+1)); [ "$attempt" -lt 45 ]; sleep 1
done
wait_stage() {
    exec 6> "$RAM/stage.lock"
    attempt=0
    until flock -n 6; do
        attempt=$((attempt+1)); [ "$attempt" -lt 45 ]; sleep 1
    done
    exec 6>&-
}
wait_stage
export NX300M_MOD_ROOT="$RAM/runtime" NX_STATE_DIR="$RAM/state"
. "$RAM/runtime/common.sh"
record_alive
first=$REC_PID
capture() {
    label=$1
    record_alive
    [ "$(readlink /proc/$REC_PID/exe)" = "$REMOTE" ]
    [ "$(stat -L -c '%d:%i' /proc/$REC_PID/exe)" = "$(stat -c '%d:%i' "$REMOTE")" ]
    cat "$PIDFILE" > "$OUT/$label.pid-record"
    tr '\000' '\n' < /proc/$REC_PID/environ > "$OUT/$label.environment"
    readlink /proc/$REC_PID/exe > "$OUT/$label.exe"
    count=0
    : > "$OUT/$label.helpers"
    for entry in /proc/[0-9]*/exe; do
        executable=$(readlink "$entry" 2>/dev/null) || continue
        case $executable in */nx-remote-controller.arm)
            pid=${entry#/proc/}; pid=${pid%/exe}
            if [ "$pid" = "$REC_PID" ]; then count=$((count+1)); continue; fi
            IFS= read -r comm < /proc/$pid/comm
            # The USB sampler is separate from the disabled X11 shortcuts.
            [ "$comm" = nx-usb-worker ]
            IFS= read -r fields < /proc/$pid/stat
            fields=${fields##*) }; set -- $fields
            [ "$2" = "$REC_PID" ]
            [ "$(stat -L -c '%d:%i' /proc/$pid/exe)" = "$REC_EXE_ID" ]
            printf '%s %s\n' "$pid" "$comm" >> "$OUT/$label.helpers" ;;
        esac
    done
    [ "$count" -eq 1 ]
    printf '%s\n' "$count" > "$OUT/$label.remote-count"
}
capture first
wget -T 5 -Y off -O "$OUT/health.json" http://127.0.0.1:8781/api/v1/health
wget -T 5 -Y off -O "$OUT/camera-info.json" http://127.0.0.1:8781/api/v1/camera/info
if [ -f /arm-vm/network-enabled ]; then
    wget -T 5 -Y off -O "$OUT/interface-health.json" http://192.0.2.2:8781/api/v1/health
fi
wget -T 5 -Y off -O "$OUT/network-first.json" http://127.0.0.1:8781/api/v1/network/status
run_id=$(cat /mnt/mmc/nx300m-lens-debug/WIFI_MEM_RUN_ID)
attempt=0
until [ -f "/mnt/mmc/nx300m-lens-debug/logs/$run_id/complete.txt" ]; do
    attempt=$((attempt+1)); [ "$attempt" -lt 20 ]; sleep 1
done
cp "/mnt/mmc/nx300m-lens-debug/logs/$run_id/before.json" "$OUT/wifi-before.json"
cp "/mnt/mmc/nx300m-lens-debug/logs/$run_id/session.txt" "$OUT/wifi-session.txt"
# Invoke the unchanged root entry again, rather than start.sh directly.
/bin/sh /mnt/mmc/autoexec.sh > "$OUT/repeat-entry.log" 2>&1
sleep 2
wait_stage
record_alive
[ "$REC_PID" = "$first" ]
capture repeat
cmp -s "$OUT/first.pid-record" "$OUT/repeat.pid-record"
wget -T 5 -Y off -O "$OUT/repeat-health.json" http://127.0.0.1:8781/api/v1/health
wget -T 5 -Y off -O "$OUT/network-repeat.json" http://127.0.0.1:8781/api/v1/network/status
/bin/sh "$RAM/runtime/stop.sh" > "$OUT/stop.log" 2>&1
! record_alive
sleep 2
! record_alive
echo CONNECTED_ARM_PASSED
'''


def configure_reuse():
    mfw.OUT = OUT
    mfw.SOCKET = SOCKET
    mfw.INIT = INIT
    mfw.PROBE = PROBE


def cpio_files(data):
    """Read the installer newc archive; trailing zero alignment is allowed."""
    at = 0
    while at + 110 <= len(data):
        if data[at:at+6] not in (b'070701', b'070702'):
            if not data[at:].strip(b'\0'): return
            raise ValueError('Unexpected installer CPIO header')
        values = [int(data[at+6+i*8:at+14+i*8], 16) for i in range(13)]
        size, namesize = values[6], values[11]
        name = data[at+110:at+110+namesize-1].decode()
        content = (at + 110 + namesize + 3) & ~3
        if name == 'TRAILER!!!':
            at = (content + size + 3) & ~3
            continue
        yield name.removeprefix('./'), values[1], data[content:content+size]
        at = (content + size + 3) & ~3


def network_modules():
    cache = OUT / 'network-cache'
    cache.mkdir(parents=True, exist_ok=True)
    installer = cache / 'initrd.gz'
    if not installer.exists():
        temporary = cache / 'initrd-stream.gz.part'
        print('Streaming official installer NIC modules', flush=True)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(INITRD_URL, timeout=30) as response, temporary.open('wb') as target:
            expected = int(response.headers['Content-Length'])
            copied = 0
            while chunk := response.read(1024 * 1024):
                target.write(chunk)
                copied += len(chunk)
                print(f'Installer download {copied}/{expected} bytes', flush=True)
        if copied != expected: raise ValueError('Truncated official installer download')
        temporary.replace(installer)
    files = {name: content for name, mode, content in cpio_files(gzip.decompress(installer.read_bytes()))
             if '/modules/' in name and mode & 0o170000 == 0o100000}
    prefix = 'lib/modules/' + KERNEL_RELEASE + '/'
    deptext = files.get(prefix + 'modules.dep')
    if deptext is None:
        raise ValueError('Official installer modules do not match the cached kernel ' + KERNEL_RELEASE)
    dependencies = dict(line.split(':', 1) for line in deptext.decode().splitlines())
    selected, ordered = set(), []
    def add(name):
        if name in selected: return
        selected.add(name)
        for dependency in dependencies.get(name, '').split(): add(dependency)
        if prefix + name not in files: raise ValueError('Installer module unavailable: ' + name)
        ordered.append(name)
    for stem in ('virtio_mmio', 'virtio_net'):
        matches = [name for name in dependencies if Path(name).name in (stem + '.ko', stem + '.ko.xz')]
        if len(matches) != 1: raise ValueError('Expected installer driver module: ' + stem)
        add(matches[0])
    return [(prefix + name, files[prefix + name]) for name in ordered]


def prepare(with_nic=False):
    if (OUT / 'execution.json').exists():
        raise ValueError('Existing run evidence must be preserved before prepare')
    configure_reuse()
    OUT.mkdir(parents=True, exist_ok=True)
    inventory = {item['path']: item['bytes'] for item in json.loads((ROOT / 'build-report.json').read_text())['files']}
    stage = OUT / 'source-files'
    if stage.exists():
        assert stage.parent.resolve() == OUT.resolve() and not stage.is_symlink()
        shutil.rmtree(stage)
    archive = ROOT / 'nx300m-connected.zip'
    with zipfile.ZipFile(archive) as package:
        if mfw.archive_inventory(package) != dict(sorted(inventory.items())):
            raise ValueError('Published ZIP differs from build inventory')
        for name in inventory:
            if name.startswith('/') or '\\' in name or any(p in ('', '.', '..') for p in name.split('/')):
                raise ValueError('Invalid package path: ' + name)
        package.extractall(stage)
        for name in inventory:
            if (stage / name).read_bytes() != package.read(name): raise ValueError('Candidate differs: ' + name)
    runtime = (stage / 'nx300m-mod/runtime-files.list').read_text().splitlines()
    for name in runtime:
        if not (stage / 'nx300m-mod' / name).is_file(): raise ValueError('Runtime asset missing: ' + name)
    arm_vm.write_json(OUT / 'candidate.json', {'archive': str(archive), 'archive_bytes': archive.stat().st_size,
                       'archive_modified_epoch': archive.stat().st_mtime, 'inventory': inventory,
                       'runtime_files': len(runtime), 'root_entry': 'autoexec.sh', 'with_nic': with_nic})
    (stage / 'DCIM').mkdir(exist_ok=True)
    modules = network_modules() if with_nic else []
    mfw.initramfs()
    extra = io.BytesIO()
    directories = {'arm-vm', 'arm-vm/network-modules'}
    for name, _ in modules:
        directories.update(p.as_posix() for p in Path(name).parents if str(p) != '.')
    for name in sorted(directories): arm_vm.cpio_add(extra, name, 0o40755)
    commands = ['#!/rescue/busybox sh', 'set -eu']
    for index, (name, data) in enumerate(modules):
        # Installer modules can be xz-compressed; BusyBox insmod needs ELF bytes.
        import lzma
        if name.endswith('.xz'): data = lzma.decompress(data)
        target = 'arm-vm/network-modules/' + str(index) + '.ko'
        arm_vm.cpio_add(extra, target, 0o100644, data)
        commands.append('/rescue/busybox insmod /' + target)
    if with_nic:
        commands += ['/rescue/busybox ifconfig eth0 192.0.2.2 netmask 255.255.255.0 broadcast 192.0.2.255 up']
        arm_vm.cpio_add(extra, 'arm-vm/network-enabled', 0o100644, b'private virtual Ethernet\n')
    arm_vm.cpio_add(extra, 'arm-vm/load-network.sh', 0o100755, ('\n'.join(commands) + '\n').encode())
    arm_vm.cpio_add(extra, 'TRAILER!!!', 0)
    with (OUT / 'initramfs.cpio.gz').open('ab') as stream: stream.write(gzip.compress(extra.getvalue(), mtime=0))
    arm_vm.write_json(OUT / 'network-module-provenance.json', {'url': INITRD_URL, 'kernel_release': KERNEL_RELEASE,
                                                            'modules': [name for name, _ in modules]})
    print('Prepared actual connected ZIP; private NIC enabled=' + str(with_nic))


def network_receiver(stop):
    frames, beacons = 0, []
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(SOCKET + '-net.sock')
        sock.settimeout(0.5)
        buffer = b''
        while not stop.is_set():
            try: chunk = sock.recv(65536)
            except socket.timeout: continue
            if not chunk: break
            buffer += chunk
            while len(buffer) >= 4:
                length = struct.unpack('!I', buffer[:4])[0]
                if length > 65536: raise ValueError('Invalid QEMU network frame length')
                if len(buffer) < 4 + length: break
                packet, buffer = buffer[4:4+length], buffer[4+length:]
                frames += 1
                if len(packet) < 42 or packet[12:14] != b'\x08\x00': continue
                ihl = (packet[14] & 15) * 4
                if packet[23] != 17 or len(packet) < 14 + ihl + 8: continue
                srcport, dstport, size, checksum = struct.unpack('!HHHH', packet[14+ihl:22+ihl])
                if dstport != 5681: continue
                payload = packet[22+ihl:14+ihl+size]
                beacons.append({'source_ipv4': socket.inet_ntoa(packet[26:30]),
                                'destination_ipv4': socket.inet_ntoa(packet[30:34]),
                                'destination_mac': packet[:6].hex(':'), 'udp_destination_port': dstport,
                                'payload_bytes': len(payload), 'payload_hex': payload.hex(),
                                'text': payload.rstrip(b'\0').decode('ascii', 'replace')})
    except Exception as exc:
        arm_vm.write_json(OUT / 'beacons.json', {'error': str(exc), 'frames': frames, 'beacons': beacons})
    else: arm_vm.write_json(OUT / 'beacons.json', {'frames': frames, 'beacons': beacons})
    finally: sock.close()


def linux_worker(action, payload):
    configure_reuse()
    arm_vm.SERIAL, arm_vm.QMP = SOCKET + '-serial.sock', SOCKET + '-qmp.sock'
    if action == 'start':
        try:
            arm_vm.qmp('query-status')
        except (FileNotFoundError, ConnectionRefusedError): pass
        else: raise RuntimeError('Private connected VM is already running')
        for suffix in ('-serial.sock', '-qmp.sock', '-net.sock'): Path(SOCKET + suffix).unlink(missing_ok=True)
        with_nic = json.loads((OUT / 'candidate.json').read_text())['with_nic']
        network = ['-netdev', f'stream,id=probe,server=on,addr.type=unix,addr.path={SOCKET}-net.sock',
                   '-device', 'virtio-net-device,netdev=probe,mac=02:00:00:30:0d:01'] if with_nic else ['-nic', 'none']
        command = [str(arm_vm.WORK / 'host/usr/bin/qemu-system-arm'), '-M', 'virt', '-cpu', 'cortex-a15',
                   '-m', '2048M', '-smp', '2', '-kernel', str(arm_vm.WORK / 'downloads/vmlinuz'),
                   '-initrd', str(OUT / 'initramfs.cpio.gz'), '-append', 'console=ttyAMA0 rdinit=/init panic=-1',
                   '-display', 'none', '-monitor', 'none', '-serial',
                   f'unix:{arm_vm.SERIAL},server=on,wait=off,logfile={OUT / "serial.log"}',
                   '-qmp', f'unix:{arm_vm.QMP},server=on,wait=off', *network, '-no-reboot']
        log = (OUT / 'qemu.log').open('ab', buffering=0)
        process = subprocess.Popen(command, env=dict(os.environ, LD_LIBRARY_PATH=str(arm_vm.WORK / 'host/usr/lib/x86_64-linux-gnu')),
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        arm_vm.write_json(OUT / 'handle.json', {'pid': process.pid, 'command': command})
        return {'pid': process.pid}
    if action == 'receive':
        if not json.loads((OUT / 'candidate.json').read_text())['with_nic']:
            return arm_vm.native_exec('/bin/sh /arm-vm/mfw-probe.sh', timeout=180)
        stop = threading.Event()
        receiver = threading.Thread(target=network_receiver, args=(stop,))
        receiver.start()
        try: return arm_vm.native_exec('/bin/sh /arm-vm/mfw-probe.sh', timeout=180)
        finally:
            stop.set(); receiver.join(3)
    if action == 'stop':
        response = arm_vm.qmp('quit')
        pid = json.loads((OUT / 'handle.json').read_text())['pid']
        for _ in range(30):
            if not Path('/proc/' + str(pid)).exists(): return {'qmp': response, 'private_vm_pid_absent': True}
            time.sleep(.1)
        raise RuntimeError('Private VM PID did not disappear after QMP quit')
    return arm_vm.native_exec(base64.b64decode(payload).decode(), timeout=60)


def call(action, command=''):
    result = subprocess.run(['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'python3',
                             arm_vm.wsl_path(__file__), '--linux', action, base64.b64encode(command.encode()).decode()],
                            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=200)
    if result.returncode: raise RuntimeError(result.stderr or result.stdout)
    return json.loads(result.stdout)


def collect():
    result = call('exec', "set -e; /rescue/busybox dmesg -n 1; /rescue/busybox tar -czf /arm-vm/evidence.tar.gz -C /arm-vm connected-results; echo CONNECTED_EVIDENCE_BEGIN; /rescue/busybox base64 /arm-vm/evidence.tar.gz; echo CONNECTED_EVIDENCE_END")
    arm_vm.write_json(OUT / 'collection.json', result)
    lines = result['output'].splitlines()
    begin = next(i for i, line in enumerate(lines) if line.strip() == 'CONNECTED_EVIDENCE_BEGIN')
    end = next(i for i, line in enumerate(lines) if line.strip() == 'CONNECTED_EVIDENCE_END')
    raw = base64.b64decode(''.join(line.strip() for line in lines[begin+1:end]), validate=True)
    (OUT / 'guest-evidence.tar.gz').write_bytes(raw)
    unpacked = gzip.decompress(raw)
    with tarfile.open(fileobj=io.BytesIO(unpacked)) as archive:
        if len(archive.getnames()) != len(set(archive.getnames())): raise ValueError('Duplicate evidence member')
        archive.extractall(OUT / 'guest', filter='data')


def verify():
    guest = OUT / 'guest/connected-results'
    candidate = json.loads((OUT / 'candidate.json').read_text())
    with_nic = candidate['with_nic']
    def read(name): return (guest / name).read_text(encoding='utf-8')
    def data(name): return json.loads(read(name))
    wifi = data('wifi-before.json')
    if (wifi.get('ok') is not False or wifi.get('exit_code') != 2 or wifi.get('pid') != 0 or
            wifi.get('reason') != 'camera_process_not_found' or wifi.get('byte_writes_attempted') != 0):
        raise ValueError('Wi-Fi tool did not honestly refuse absent stock camera: ' + repr(wifi))
    if 'before_exit=2' not in read('wifi-session.txt') or 'worker_exit=2' not in read('wifi-session.txt'):
        raise ValueError('Wi-Fi worker result was not preserved')
    if 'stage=worker_launched' not in read('wifi-entry.log'): raise ValueError('Root entry did not try Wi-Fi')
    for name in ('health.json', 'repeat-health.json') + (('interface-health.json',) if with_nic else ()):
        if data(name).get('ok') is not True: raise ValueError('HTTP health failed: ' + name)
    info = data('camera-info.json')
    if info.get('model') != 'NX300M': raise ValueError('Camera info unavailable')
    if info.get('custom_key', {}).get('enabled') is not False:
        raise ValueError('Custom key was not disabled')
    if info.get('x11') != {'background_enabled': False, 'helper_running': False, 'held_input': False}:
        raise ValueError('Unexpected X11 activity')
    for label in ('first', 'repeat'):
        env = read(label + '.environment').splitlines()
        if any(value not in env for value in ('NX_REMOTE_CUSTOM_KEY=0', 'NX_REMOTE_PANEL_SHORTCUT=0', 'NX_REMOTE_LISTEN=0.0.0.0:8781')):
            raise ValueError('Published root did not provide disabled shortcuts and public listener')
        if read(label + '.remote-count').strip() != '1': raise ValueError('Duplicate remote')
    if read('first.pid-record') != read('repeat.pid-record'): raise ValueError('Repeated root entry replaced remote')
    first, repeat = data('network-first.json'), data('network-repeat.json')
    for status in (first, repeat):
        if not with_nic:
            if (status.get('http_address') != '0.0.0.0' or status.get('http_port') != 8781 or
                    status.get('broadcast_enabled') is not False or status.get('packets_sent') != 0 or
                    status.get('interfaces') != []):
                raise ValueError('Networkless VM status incorrectly claimed interface/discovery evidence')
            continue
        if (status.get('http_address') != '0.0.0.0' or status.get('http_port') != 8781 or
                status.get('broadcast_port') != 5681 or status.get('broadcast_enabled') is not True or
                status.get('packets_sent', 0) < 1 or status.get('send_failures') != 0 or
                not any(item.get('ipv4') == '192.0.2.2' and item.get('broadcast_enabled') is True for item in status.get('interfaces', []))):
            raise ValueError('Virtual NIC discovery status incorrect: ' + repr(status))
    if with_nic and repeat['packets_sent'] <= first['packets_sent']: raise ValueError('Discovery counter did not advance')
    packets = json.loads((OUT / 'beacons.json').read_text()) if with_nic else {}
    beacons = [item for item in packets.get('beacons', []) if item['payload_bytes'] == 64 and
               item['text'].startswith('NX_REMOTE|0.9|NX300M|1.15|') and item['source_ipv4'] == '192.0.2.2' and
               item['destination_ipv4'] == '192.0.2.255' and item['destination_mac'] == 'ff:ff:ff:ff:ff:ff']
    if with_nic and not beacons: raise ValueError('No actual UDP5681 broadcast reached the private network peer')
    archive = Path(candidate['archive'])
    with zipfile.ZipFile(archive) as package:
        if mfw.archive_inventory(package) != dict(sorted(candidate['inventory'].items())):
            raise ValueError('Published package changed during run')
        for name in candidate['inventory']:
            if (OUT / 'source-files' / name).read_bytes() != package.read(name): raise ValueError('Candidate bytes changed: ' + name)
    return {'passed': True, 'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
            'scope': 'published combined root entry and native ARM HTTP service' +
                     (' with isolated virtual Ethernet discovery' if with_nic else '; networkless VM'),
            'package_files': len(candidate['inventory']), 'runtime_files': candidate['runtime_files'],
            'wifi_attempted': True, 'wifi_result': wifi, 'wifi_failure_did_not_block_http': True,
            'health_loopback': True, 'health_nonloopback_verified': with_nic, 'camera_info': info,
            'duplicate_entry_same_process': True, 'custom_and_fn_disabled_no_x11_helpers': True,
            'network_first': first, 'network_repeat': repeat, 'actual_udp5681_beacons_received': len(beacons),
            'remote_stopped': True, 'main_emulator_touched': False, 'physical_camera_or_card_touched': False,
            'not_verified': ['physical Wi-Fi lens patch/RVF', 'physical phone connection', 'camera application boot', 'whole-camera memory pressure'] +
                            ([] if with_nic else ['nonloopback HTTP reachability and UDP5681 broadcast; matching NIC modules unavailable locally and external download too slow'])}


def run():
    if (OUT / 'execution.json').exists(): raise ValueError('Existing run evidence must be preserved before rerun')
    call('start')
    report = {'passed': False}
    try:
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline:
            path = OUT / 'serial.log'
            if path.exists() and 'CONNECTED_PRIVATE_SHELL_READY' in path.read_text(errors='replace'): break
            time.sleep(1)
        else: raise RuntimeError('Private ARM boot timed out')
        execution = call('receive')
        arm_vm.write_json(OUT / 'execution.json', execution)
        collect()
        if execution['exit_code'] or 'CONNECTED_ARM_PASSED' not in execution['output']:
            raise RuntimeError('Connected guest probe failed; inspect current evidence')
        report = verify()
    except Exception as exc:
        report['failure'] = str(exc)
        raise
    finally:
        try: report.update(call('stop'))
        finally: arm_vm.write_json(OUT / 'report.json', report)
    print('PASS narrow connected ARM boot: ' + str(OUT / 'report.json'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--linux', action='store_true')
    parser.add_argument('--with-nic', action='store_true', help='Optional virtual Ethernet check; requires matching installer NIC modules')
    parser.add_argument('action', choices=('prepare', 'run', 'start', 'stop', 'exec', 'receive'))
    parser.add_argument('payload', nargs='?', default='')
    args = parser.parse_args()
    if args.linux: print(json.dumps(linux_worker(args.action, args.payload)))
    elif args.action == 'prepare': prepare(args.with_nic)
    elif args.action == 'run': run()
    else: print(json.dumps(call(args.action, args.payload)))


if __name__ == '__main__': main()
