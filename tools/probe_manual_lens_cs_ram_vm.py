"""Bounded CS RAM loader check in a private ARM Linux VM, with no camera IO."""
import argparse
import base64
import io
import json
from pathlib import Path
import posixpath
import re
import shlex
import shutil
import subprocess
import tarfile
import time

import arm_vm
import probe_mfw_ram_vm as vm

PROJECT = Path(__file__).resolve().parents[1]
OUT = PROJECT / 'outputs/controls/manual-lens/cs-ram/arm-vm'
SOCKET = '/tmp/nx300m-cs-ram-private'
FIELDS = '''magic abi enabled inflight busy request_seq request_action done_seq phase reason
original_update original_init original_common original_info original_onoff original_ready iq_slot pp_slot operation_slot
detached_state active_tid active_pp update_tid operation_ptr operation_state ready_result iq_ptr iq_ready iq_shading
flags_before flags_after mode three_dimensional width height common_calls common_rc info_calls info_rc onoff_calls
onoff_rc onoff_arg source_ptr source_bytes source_lut_kind order ready_after iq_ready_after iq_shading_after total_updates'''.split()
INIT = r'''#!/rescue/busybox sh
export PATH=/bin:/sbin:/usr/bin:/usr/sbin
/rescue/busybox mount -t devtmpfs devtmpfs /dev
exec </dev/ttyAMA0 >/dev/ttyAMA0 2>&1
/rescue/busybox mount -t proc proc /proc
/rescue/busybox mount -t sysfs sysfs /sys
/rescue/busybox mkdir -p /tmp /dev/pts /mnt/mmc /var/run /arm-vm/mfw-results
/rescue/busybox mount -t devpts devpts /dev/pts
/rescue/busybox mount -t tmpfs -o rw,exec,mode=1777,size=32m cs-runtime /tmp
/rescue/busybox mount --bind /arm-vm/mfw-source /mnt/mmc
/rescue/busybox mount -o remount,bind,ro /mnt/mmc
echo CS_RAM_PRIVATE_VM_SHELL_READY
while true; do /rescue/busybox setsid /bin/sh -i < /dev/ttyAMA0 > /dev/ttyAMA0 2>&1; done
'''
PROBE = r'''#!/bin/sh
set -eux
OUT=/arm-vm/mfw-results
LOADER=/mnt/mmc/bin/cs_live_loader.arm
BLOB=/mnt/mmc/manual_lens_cs_ram.bin
finish() {
    result=$?; trap - EXIT; set +e
    if [ "$result" -ne 0 ] && [ -n "${pid:-}" ] && [ -r /proc/$pid/status ]; then
        cat /proc/$pid/status > "$OUT/failure.status"
        cat /proc/$pid/maps > "$OUT/failure.maps"
        "$LOADER" status --pid "$pid" --fixture --blob "$BLOB" > "$OUT/status-failure.json" 2> "$OUT/status-failure.stderr"
    fi
    printf '%s\n' "$result" > "$OUT/probe.exit"; exit "$result"
}
trap finish EXIT
uname -a > "$OUT/uname.txt"
/mnt/mmc/bin/cs_live_fixture.arm --policy "$BLOB" > "$OUT/policy.stdout" 2> "$OUT/policy.stderr"
/mnt/mmc/bin/cs_live_fixture.arm > "$OUT/fixture.stdout" 2> "$OUT/fixture.stderr" &
pid=$!
attempt=0
while [ ! -s "$OUT/fixture.ready" ]; do
    attempt=$((attempt+1)); [ "$attempt" -lt 40 ]; /rescue/busybox usleep 100000
done
read ready_pid < "$OUT/fixture.ready"; [ "$ready_pid" = "$pid" ]
cat /proc/$pid/stat > "$OUT/before.stat"
seq=0
rpc() {
    mode=$1; tag=$2; seq=$((seq+1))
    printf '%s %s %s\n' "$seq" "$mode" "$tag" > /var/run/cs-live-request.tmp
    mv /var/run/cs-live-request.tmp /var/run/cs-live-request
    attempt=0
    while :; do
        response=0
        [ ! -r /var/run/cs-live-response ] || read response ignored < /var/run/cs-live-response
        [ "$response" != "$seq" ] || break
        attempt=$((attempt+1)); [ "$attempt" -lt 40 ]; /rescue/busybox usleep 100000
    done
}
loader() {
    command=$1; label=$2
    "$LOADER" "$command" --pid "$pid" --fixture --blob "$BLOB" > "$OUT/$label.json" 2> "$OUT/$label.stderr"
}
restore() {
    label=$1; attempt=0
    while :; do
        set +e
        "$LOADER" restore --pid "$pid" --fixture --blob "$BLOB" > "$OUT/$label.json" 2> "$OUT/$label.stderr"
        result=$?
        set -e
        [ "$result" -ne 0 ] || break
        [ "$result" -eq 4 ]; attempt=$((attempt+1)); [ "$attempt" -lt 20 ]; /rescue/busybox usleep 100000
    done
}
loader status before
loader apply apply
cat /proc/$pid/maps > "$OUT/installed.maps"
loader apply idempotent
cat /proc/$pid/maps > "$OUT/idempotent.maps"
loader status installed
rpc 0 no-request
loader request-default request-real
# Publication cannot execute Init on the loader's thread or while Update is idle.
/rescue/busybox usleep 100000
loader status queued
rpc 0 real-failed
loader status failed
rpc 0 no-repeat
loader request-default request-electronic
rpc 2 electronic
loader request-default request-iq
rpc 1 iq-not-empty
loader request-default request-dma
rpc 3 dma-pending
restore restore
loader status restored
rpc 0 restored
loader apply reapply
cat /proc/$pid/maps > "$OUT/reapplied.maps"
loader status reapplied
rpc 0 reapplied-no-request
restore rerestore
loader status rerestored
cat /proc/$pid/stat > "$OUT/after.stat"
cat /proc/$pid/status > "$OUT/after.status"
ls /proc/$pid/task > "$OUT/after.tasks"
seq=$((seq+1)); printf '%s 9 quit\n' "$seq" > /var/run/cs-live-request.tmp
mv /var/run/cs-live-request.tmp /var/run/cs-live-request
wait "$pid"
echo CS_RAM_FULL_ARM_PASSED
'''


def configure():
    vm.OUT, vm.SOCKET, vm.INIT, vm.PROBE = OUT, SOCKET, INIT, PROBE


def compile_fixture():
    target = OUT / 'cs_live_fixture.arm'
    # Read the real ELF relocations rather than duplicating the loader's GOT constants.
    from elftools.elf.elffile import ELFFile
    config = json.loads((PROJECT / 'project.local.json').read_text(encoding='utf-8'))
    with (Path(config['rootfs']) / 'usr/lib/libiq-fw-slpcam.so').open('rb') as stream:
        elf = ELFFile(stream)
        dynsym = elf.get_section_by_name('.dynsym')
        symbols = {s.name: s['st_value'] for s in dynsym.iter_symbols()}
        relocation_rows = list(elf.get_section_by_name('.rel.plt').iter_relocations())
        rels = {dynsym.get_symbol(r['r_info_sym']).name: r['r_offset'] for r in relocation_rows}
        update = '_ZN15CPreProcessorD413UpdateCsParamEb'
        constants = {'UPDATE_ADDRESS': symbols[update], 'UPDATE_GOT': rels[update],
                     'COMMON_GOT': rels['pp_core_common_lut_init'],
                     'INFO_GOT': rels['pp_core_cs_set_info'], 'ONOFF_GOT': rels['pp_core_cs_onoff']}
        update_index = next(i for i, r in enumerate(relocation_rows)
                            if dynsym.get_symbol(r['r_info_sym']).name == update)
        constants['UPDATE_PLT'] = elf.get_section_by_name('.plt')['sh_addr'] + 20 + update_index * 12
    definitions = ' '.join(f'-DNX_{key}={value}U' for key, value in constants.items())
    source = PROJECT / 'runtime/manual_lens_cs_fixture.c'
    script = f'''set -eu
tc=/opt/nx300m-toolchain/usr
rf=/opt/nx300m-lab/stock-1.15
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=gnu99 -O1 -Wall -Wextra -Werror -march=armv7-a -mfloat-abi=soft -fno-pic -no-pie -fno-stack-protector {definitions} \\
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 \\
 -Wl,--allow-shlib-undefined -Wl,-rpath-link,"$rf/usr/lib" -Wl,-rpath-link,"$rf/lib" -L"$rf/usr/lib" \\
 {shlex.quote(arm_vm.wsl_path(source))} \\
 {shlex.quote(arm_vm.wsl_path(PROJECT / 'work/reference/metol/test_server/filmlab/src/crt0_arm.S'))} \\
 -Wl,--no-as-needed -Wl,-l:libiq-fw-slpcam.so -Wl,-l:libdiosal.so -Wl,-l:libproduction-mode.so -Wl,-l:liblens-fw-prod.so -Wl,-l:libcapture-fw-plat.so \\
 "$rf/usr/lib/libudd.so.3.1" "$rf/lib/libpthread.so.0" "$rf/lib/libc-2.13.so" "$rf/lib/libdl.so.2" -lgcc \\
 -o {shlex.quote(arm_vm.wsl_path(target))}
'''
    result = subprocess.run(['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'sh', '-s'],
                            input=script.encode(), capture_output=True)
    (OUT / 'fixture-build.stdout.log').write_bytes(result.stdout)
    (OUT / 'fixture-build.stderr.log').write_bytes(result.stderr)
    arm_vm.write_json(OUT / 'fixture-build.json', {'exit_code': result.returncode, 'elf_constants': constants})
    if result.returncode:
        raise RuntimeError(f'Fixture build exit {result.returncode}: {result.stderr.decode(errors="replace")}')
    return target


def prepare():
    OUT.mkdir(parents=True, exist_ok=True)
    if not (arm_vm.WORK / 'initramfs.cpio.gz').is_file():
        raise ValueError('Cached ARM initramfs required; this probe never rebuilds shared caches')
    fixture = compile_fixture()
    startup_preflight(fixture)
    inputs = {'bin/cs_live_fixture.arm': fixture,
              'bin/cs_live_loader.arm': PROJECT / 'outputs/deployment/cs-live-loader/cs_live_loader.arm',
              'manual_lens_cs_ram.bin': PROJECT / 'outputs/controls/manual-lens/cs-ram/manual_lens_cs_ram.bin'}
    stage = OUT / 'source-files'
    stage.mkdir(exist_ok=True)
    inventory = {}
    for name, source in inputs.items():
        target = stage / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        inventory[name] = target.stat().st_size
    arm_vm.write_json(OUT / 'candidate.json', {'inventory': inventory,
        'target': 'cs_live_fixture.arm; real stock Init/Update/UDD/lens, synthetic singleton objects and backing',
        'shared_cached_initramfs_modified': False})
    vm.initramfs()


def startup_preflight(fixture):
    """Check mandatory GLOB_DAT providers, then run only dynamic startup."""
    from elftools.elf.elffile import ELFFile
    images = {'fixture': fixture.read_bytes()}
    pending = ['fixture']
    with tarfile.open(PROJECT / 'work/stock-1.15/rootfs.tar') as archive:
        members = {member.name: member for member in archive}
        def original(name):
            path = next((candidate for candidate in ('usr/lib/' + name, 'lib/' + name)
                         if candidate in members), None)
            if path is None:
                raise ValueError('Missing original dependency: ' + name)
            for _ in range(30):
                member = members[path]
                if member.isfile():
                    return archive.extractfile(member).read()
                if member.issym():
                    path = posixpath.normpath(member.linkname.lstrip('/') if member.linkname.startswith('/')
                                             else posixpath.join(posixpath.dirname(path), member.linkname))
                elif member.islnk():
                    path = member.linkname.lstrip('/')
                else:
                    raise ValueError('Unsupported original dependency: ' + path)
            raise ValueError('Original dependency symlink loop: ' + name)
        while pending:
            name = pending.pop(0)
            elf = ELFFile(io.BytesIO(images[name]))
            dynamic = elf.get_section_by_name('.dynamic')
            for tag in dynamic.iter_tags() if dynamic else ():
                if tag.entry.d_tag == 'DT_NEEDED' and tag.needed not in images:
                    images[tag.needed] = original(tag.needed)
                    pending.append(tag.needed)
    definitions, references = {}, []
    for name, data in images.items():
        elf = ELFFile(io.BytesIO(data))
        symbols = elf.get_section_by_name('.dynsym')
        if symbols is None:
            continue
        for symbol in symbols.iter_symbols():
            if symbol.name and symbol['st_shndx'] != 'SHN_UNDEF':
                definitions.setdefault(symbol.name, []).append(name)
        for section in elf.iter_sections():
            if section['sh_type'] != 'SHT_REL':
                continue
            linked = elf.get_section(section['sh_link'])
            for relocation in section.iter_relocations():
                if relocation['r_info_type'] == 21:
                    symbol = linked.get_symbol(relocation['r_info_sym'])
                    if symbol['st_shndx'] == 'SHN_UNDEF':
                        references.append({'image': name, 'symbol': symbol.name,
                                           'weak': symbol['st_info']['bind'] == 'STB_WEAK'})
    for reference in references:
        reference['providers'] = definitions.get(reference['symbol'], [])
    unresolved = [row for row in references if not row['weak'] and not row['providers']]
    report = {'dependency_images': sorted(images), 'glob_dat_references': references,
              'unresolved_mandatory_glob_dat': unresolved,
              'target_init_update_executed': False}
    arm_vm.write_json(OUT / 'startup-preflight.json', report)
    if unresolved:
        raise ValueError('Unresolved original GLOB_DAT providers: ' + repr(unresolved))
    command = ['wsl.exe', '-d', 'Debian', '-u', 'root', '-e',
               '/opt/nx300m-lab/stock-1.15/qemu-arm-static', '-L', '/opt/nx300m-lab/stock-1.15',
               arm_vm.wsl_path(fixture), '--startup-check']
    result = subprocess.run(command, capture_output=True, timeout=30)
    (OUT / 'startup-preflight.stdout.log').write_bytes(result.stdout)
    (OUT / 'startup-preflight.stderr.log').write_bytes(result.stderr)
    report['qemu_user_startup_exit_code'] = result.returncode
    arm_vm.write_json(OUT / 'startup-preflight.json', report)
    if result.returncode:
        raise RuntimeError(f'Startup preflight exit {result.returncode}: {result.stderr.decode(errors="replace")}')


def call(action, command=''):
    result = subprocess.run(['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'python3', arm_vm.wsl_path(__file__),
                             '--linux', action, base64.b64encode(command.encode()).decode()],
                            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=375)
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    return json.loads(result.stdout)


def collect():
    result = call('exec', r"/rescue/busybox tar -czf /tmp/cs-evidence.tar.gz -C /arm-vm mfw-results; printf 'CS_EVIDENCE_BEGIN\n'; /rescue/busybox base64 /tmp/cs-evidence.tar.gz; printf 'CS_EVIDENCE_END\n'")
    arm_vm.write_json(OUT / 'collection.json', result)
    match = re.search(r'CS_EVIDENCE_BEGIN\r?\n([A-Za-z0-9+/=\r\n]+)CS_EVIDENCE_END', result['output'])
    if result['exit_code'] or not match:
        raise ValueError('Guest evidence collection failed')
    raw = base64.b64decode(re.sub(r'\s', '', match[1]), validate=True)
    (OUT / 'guest-evidence.tar.gz').write_bytes(raw)
    guest = OUT / 'guest'
    if guest.exists():
        if guest.is_symlink() or guest.resolve().parent != OUT.resolve():
            raise ValueError('Unexpected private guest evidence path')
        shutil.rmtree(guest)
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        archive.extractall(guest, filter='data')
    return guest / 'mfw-results'


def verify(guest):
    def read(name):
        return json.loads((guest / (name + '.json')).read_text())
    def require(condition, detail):
        if not condition:
            raise ValueError(detail)
    cases = {}
    for tag in ('no-request', 'real-failed', 'no-repeat', 'electronic', 'iq-not-empty',
                'dma-pending', 'reapplied-no-request'):
        item = read(tag + '.fixture')
        require(len(item['config']) == len(FIELDS), f'{tag}: config ABI differs')
        item['config'] = dict(zip(FIELDS, item['config']))
        cases[tag] = item
    baseline, stock = read('baseline.fixture'), read('stock-baseline')
    require(stock['common_unopened_rc'] == -3, 'Unopened real UDD did not return -3')
    require(baseline['uniform_0x2000'] == 1, 'Stock Init did not generate the uniform fixed-gain default')
    require(read('policy')['passed'] == 1 and read('policy')['fake_callbacks'] is True,
            'Separate policy-only success fixture failed')
    require(cases['no-request']['backing_untouched'] == 1 and
            cases['no-request']['config']['common_calls'] == 0, 'No-request Update invoked Init')
    queued = read('queued')
    require(queued['request_seq'] == 1 and queued['done_seq'] == 0 and queued['phase'] == 1,
            'Published request executed before fixture Update')
    real = cases['real-failed']; c = real['config']
    require(c['phase'] == 6 and c['reason'] == 11 and c['common_rc'] == 0xfffffffd,
            'Real UDD failure was not classified as failed submission')
    require(real['uniform_0x2000'] == 1 and c['source_bytes'] == 96 and
            c['common_calls'] == c['info_calls'] == c['onoff_calls'] == 1 and c['order'] == 27,
            'Real Init table or observed call sequence differs')
    require(c['update_tid'] == real['update_tid'] and c['active_tid'] == 0 and
            c['info_rc'] == c['onoff_rc'] == 0xffffffff, 'Wrong thread or fabricated void result')
    require(cases['no-repeat']['backing_untouched'] == 1 and
            cases['no-repeat']['config']['done_seq'] == 1, 'Completed request repeated Init')
    for tag, phase, reason in (('electronic', 4, 2), ('iq-not-empty', 4, 3), ('dma-pending', 5, 8)):
        case = cases[tag]; c = case['config']
        require(c['phase'] == phase and c['reason'] == reason and c['common_calls'] == 0
                and case['backing_untouched'] == 1, f'{tag}: refusal/defer called Init or wrong reason')
    statuses = {name: read(name) for name in ('before', 'installed', 'restored', 'reapplied', 'rerestored')}
    for name, hooked in (('before', False), ('installed', True), ('restored', False), ('reapplied', True), ('rerestored', False)):
        status = statuses[name]
        require(status['stock_admission'] is True and status['ram_hooks_installed'] is hooked and
                status['rw_slots_original'] is (not hooked), f'{name}: four GOT state differs')
    region = read('apply')['region']
    require(region == read('idempotent')['region'] == read('reapply')['region'], 'Repeated apply changed mapping')
    require((guest / 'installed.maps').read_text() == (guest / 'idempotent.maps').read_text(), 'Idempotent apply changed mappings')
    require(read('baseline.fixture')['slots'] == read('restored.fixture')['slots'], 'Four original GOT values not restored')
    require(cases['reapplied-no-request']['backing_untouched'] == 1, 'Reapply executed an unrequested Init')
    def start(path):
        text = path.read_text(); return text[text.rfind(')') + 2:].split()[19]
    require(start(guest / 'before.stat') == start(guest / 'after.stat'), 'Fixture identity changed')
    require(re.search(r'^TracerPid:\s+0$', (guest / 'after.status').read_text(), re.M), 'Fixture remained traced')
    require(len((guest / 'after.tasks').read_text().split()) == 2, 'Fixture worker not preserved')
    return {'passed': True, 'native_arm_kernel': True, 'real_ptrace': True,
            'real_stock_init_default_executed': True, 'default_mode': 1,
            'default_fixed_gain': 256, 'default_uniform_word': 8192, 'default_bytes': 96,
            'unopened_real_udd_rc': -3, 'real_submission_failed_not_success': True,
            'request_only_executes_on_update_thread': True, 'no_request_no_init': True,
            'electronic_and_nonempty_iq_default_refused': True, 'dma_pending_no_init': True,
            'four_got_slots_installed_and_restored': True, 'same_mapping_reused': True,
            'target_identity_and_two_threads_preserved': True, 'no_tracer_after_restore': True,
            'policy_success_uses_fake_callbacks': True, 'stock_success_verified': False,
            'synthetic_singletons_and_backing': True, 'ae_capture_gain_mode0_tested': False,
            'dma_completion_verified': False, 'image_verified': False,
            'main_simulator_modified': False, 'physical_camera_or_card_accessed': False,
            'cases': cases}


def run():
    call('start')
    report = {'passed': False, 'scope': 'private ARM VM, real stock functions, synthetic singleton objects and backing'}
    try:
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            log = OUT / 'serial.log'
            if log.exists() and 'CS_RAM_PRIVATE_VM_SHELL_READY' in log.read_text(errors='replace'):
                break
            time.sleep(1)
        else:
            raise RuntimeError('Private ARM boot timed out')
        execution = call('exec', '/bin/sh /arm-vm/mfw-probe.sh')
        arm_vm.write_json(OUT / 'execution.json', execution)
        guest = collect()
        if execution['exit_code'] or 'CS_RAM_FULL_ARM_PASSED' not in execution['output']:
            raise RuntimeError(f'Guest probe exit {execution["exit_code"]}; inspect execution.json and guest evidence')
        report = verify(guest)
    except Exception as exc:
        report['error'] = str(exc)
        raise
    finally:
        arm_vm.write_json(OUT / 'shutdown.json', call('stop'))
        report['private_vm_stopped'] = True
        arm_vm.write_json(OUT / 'report.json', report)
    print(json.dumps({key: value for key, value in report.items() if key != 'cases'}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--linux', choices=['start', 'stop', 'exec'])
    parser.add_argument('payload', nargs='?', default='')
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    configure()
    if args.linux:
        print(json.dumps(vm.linux_worker(args.linux, args.payload)))
        return
    prepare()
    if not args.prepare_only:
        run()


if __name__ == '__main__':
    main()
