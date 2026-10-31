"""Build the passive observer and its bounded isolated ABI/negative probe.

No camera is started or existing namespace entered. The stock case executes
only original libsif WriteChunks' empty-path branch on allocated layout RAM.
"""
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

os.environ.setdefault('CONDA_PREFIX', sys.prefix)
from elftools.elf.elffile import ELFFile
from inspect_gamma_hook import Stock
from inspect_capture_save import inspect as inspect_capture_save

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'outputs/firmware-runtime'
PROBE = ROOT/'outputs/metol/capture-observer-probe'
GUEST = '/opt/nx300m-lab/stock-1.15'
SYMBOL_BACKEND = '_ZN10CDriveMode17BackEndCompletionEii'
SYMBOL_WRITE = '_ZN14DscFileHandler11WriteChunksEv'


def abi_evidence():
    rootfs = Path('//wsl.localhost/Debian'+GUEST)
    evidence = []
    for library, symbol, address, anchors in [
        ('libcapture-fw-slpcam-nx300.so', SYMBOL_BACKEND, 0x24c320,
         {0x24c32c: ('str', 'r0, [fp, #-0x10]'),
          0x24c330: ('str', 'r1, [fp, #-0x14]'),
          0x24c334: ('str', 'r2, [fp, #-0x18]')}),
        ('libsif.so', SYMBOL_WRITE, 0x274a4,
         {0x275e0: ('add', 'r3, r3, #0xc'),
          0x27508: ('mov', 'r4, #1'),
          0x27640: ('cmp', 'r3, #2'),
          0x27140: ('mov', 'r1, #0'),
          0x27144: ('mov', 'r2, #0')})]:
        stock = Stock(rootfs/'usr/lib'/library)
        with stock.path.open('rb') as file:
            elf = ELFFile(file)
            found = elf.get_section_by_name('.dynsym').get_symbol_by_name(symbol)
            if not found or found[0]['st_value'] != address:
                raise ValueError(f'Original ABI symbol changed: {library}:{symbol}')
        for location, expected in anchors.items():
            instruction = stock.instruction(location)
            if (instruction.mnemonic, instruction.op_str) != expected:
                raise ValueError(f'Original ABI instruction changed at {library}:{location:#x}')
        evidence.append({'library': library, 'symbol': symbol, 'elf_address': hex(address),
                         'anchors': {hex(k): list(v) for k, v in anchors.items()}})
    save, _ = inspect_capture_save(rootfs/'usr/lib/libsif.so')
    evidence.append({'library': 'libsif.so', 'save_functions': save['functions'],
                     'save_plt_calls': save['plt_calls'], 'save_anchors': save['anchors']})
    return evidence


def probe_save():
    output = OUT/'capture-save/probe'
    output.mkdir(parents=True, exist_ok=True)
    base = '/mnt/'+ROOT.drive[0].lower()+ROOT.as_posix()[2:]
    destination = base+'/outputs/firmware-runtime/capture-save/probe'
    script = f'''set -eu
cd {shlex.quote(base)}
tc=/opt/nx300m-toolchain/usr
rf={GUEST}
base={shlex.quote(base)}
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=c99 -O2 -Wall -Wextra -Werror -mfloat-abi=soft -fno-pic -no-pie \\
 -fno-stack-protector -nostdlib -nostartfiles -Wl,-e,_start -Wl,-z,nocombreloc \\
 -Wl,--dynamic-linker=/lib/ld-linux.so.3 -Wl,--allow-shlib-undefined \\
 runtime/capture_save_probe.c work/reference/metol/test_server/filmlab/src/crt0_arm.S \\
 "$rf/lib/libc-2.13.so" "$rf/lib/libdl.so.2" -Wl,--no-as-needed "$rf/lib/librt.so.1" -lgcc \\
 -o outputs/firmware-runtime/capture-save/probe/probe.arm
unset LD_LIBRARY_PATH
exec unshare --mount --net --ipc sh -c '
 mount --make-rprivate /
 mount -t tmpfs -o mode=0777,nosuid,nodev tmpfs /var/run
 mount -t tmpfs -o mode=0755,nosuid tmpfs /dev
 mknod -m 666 /dev/full c 1 7
 mknod -m 666 /dev/null c 1 3
 test -c /dev/full
 stat -c "PRIVATE_FULL type=%F major_minor=%t:%T" /dev/full
 exec timeout 15 "$1/qemu-arm-static" -cpu cortex-a9 -L "$1" \\
  -E LD_LIBRARY_PATH="$2/usr/lib:$1/usr/lib:$1/lib" \\
  -E LD_PRELOAD="$2/outputs/firmware-runtime/capture-observer.so" \\
  "$2/outputs/firmware-runtime/capture-save/probe/probe.arm" "$3"
' sh "$rf" "$base" {shlex.quote(destination)}
'''
    process = subprocess.run(['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'sh', '-c', script],
                             capture_output=True, timeout=45)
    (output/'stdout.log').write_bytes(process.stdout)
    (output/'stderr.log').write_bytes(process.stderr)
    if process.returncode:
        raise RuntimeError(f'Save probe exit {process.returncode}: {process.stderr.decode("utf-8", "replace")}\n{process.stdout.decode("utf-8", "replace")}')
    cases = {name: json.loads((output/f'{name}.json').read_text()) for name in ('normal', 'stop', 'full')}
    normal = cases['normal']['write_io_observation']
    if (not cases['normal']['write_completion_confirmed'] or normal['actual_write_bytes'] != 21
            or normal['expected_bytes'] != 21 or normal['write_calls'] != 2
            or normal['normal_close_calls'] != 1 or normal['fflush_calls'] != 1
            or normal['fclose_calls'] != 1 or normal['fflush_result'] or normal['fclose_result']):
        raise ValueError('Original normal WriteChunks did not establish exact writing and successful close')
    for name in ('stop', 'full'):
        if cases[name]['write_completion_confirmed']:
            raise ValueError(f'{name} must never confirm successful writing and closing')
    full = cases['full']['write_io_observation']
    if full['fflush_calls'] != 1 or full['fflush_result'] == 0 or cases['full']['return_code_raw'] != 1:
        raise ValueError('Real /dev/full flush error was not distinguished from stock true return')
    report = {'exit_code': 0, 'camera_started': False, 'main_session_accessed': False,
            'data': '21 generated text bytes; not a photo', 'private_dev_full': 'character 1:7 on private tmpfs /dev',
            'normal_write_confirmed': True, 'stop_write_confirmed': False,
            'full_write_confirmed': False, 'stock_full_return': cases['full']['return_code_raw'],
            'full_flush_result': full['fflush_result'], 'full_flush_errno': full['fflush_errno'],
            'stdout': process.stdout.decode('utf-8', 'replace').splitlines()}
    (output/'report.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    return report


def compile_module(source, target):
    compiler = shutil.which('clang')
    if not compiler:
        raise RuntimeError('clang is required')
    command = [compiler, '--target=arm-linux-gnueabi', '-march=armv7-a', '-mfloat-abi=soft',
               '-std=c99', '-O2', '-Wall', '-Wextra', '-Werror', '-fPIC', '-ffreestanding',
               '-fno-builtin', '-fno-stack-protector', '-nostdlib', '-shared', '-fuse-ld=lld',
               '-Wl,--hash-style=sysv', '-Wl,-z,nocombreloc', str(source), '-o', str(target)]
    process = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=45)
    target.with_suffix('.build.log').write_bytes(process.stdout+process.stderr)
    if process.returncode:
        raise RuntimeError(f'Build exit {process.returncode}: {process.stderr.decode("utf-8", "replace")}')
    return {'command': command, 'exit_code': 0, 'output': str(target.relative_to(ROOT))}


def probe():
    PROBE.mkdir(parents=True, exist_ok=True)
    fixture = compile_module('runtime/capture_observer_fixture.c', PROBE/'capture-observer-fixture.so')
    base = '/mnt/'+ROOT.drive[0].lower()+ROOT.as_posix()[2:]
    script = f'''set -eu
cd {shlex.quote(base)}
tc=/opt/nx300m-toolchain/usr
rf={GUEST}
base={shlex.quote(base)}
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=c99 -O2 -Wall -Wextra -Werror -mfloat-abi=soft -fno-pic -no-pie \\
 -fno-stack-protector -nostdlib -nostartfiles -Wl,-e,_start -Wl,-z,nocombreloc \\
 -Wl,--dynamic-linker=/lib/ld-linux.so.3 -Wl,--allow-shlib-undefined \\
 runtime/capture_observer_probe.c work/reference/metol/test_server/filmlab/src/crt0_arm.S \\
 "$rf/lib/libc-2.13.so" "$rf/lib/libdl.so.2" -Wl,--no-as-needed "$rf/lib/librt.so.1" -lgcc \\
 -o outputs/metol/capture-observer-probe/probe.arm
unset LD_LIBRARY_PATH
exec unshare --mount --net --ipc sh -c '
 mount --make-rprivate /
 mount -t tmpfs -o mode=0777,nosuid,nodev tmpfs /var/run
 for mode in stock fixture; do
  preload="$2/outputs/firmware-runtime/capture-observer.so"
  if [ "$mode" = fixture ]; then preload="$preload:$2/outputs/metol/capture-observer-probe/capture-observer-fixture.so"; fi
  timeout 15 "$1/qemu-arm-static" -cpu cortex-a9 -L "$1" \\
   -E LD_LIBRARY_PATH="$2/outputs/metol/capture-observer-probe:$1/usr/lib:$1/lib" \\
   -E LD_PRELOAD="$preload" \\
   "$2/outputs/metol/capture-observer-probe/probe.arm" "$mode" "$2/outputs/metol/capture-observer-probe"
 done
' sh "$rf" "$base"
'''
    process = subprocess.run(['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'sh', '-c', script],
                             capture_output=True, timeout=50)
    for kind, data in [('stdout', process.stdout), ('stderr', process.stderr)]:
        (PROBE/f'{kind}.log').write_bytes(data)
    if process.returncode:
        raise RuntimeError(f'Probe exit {process.returncode}: {process.stderr.decode("utf-8", "replace")}\n{process.stdout.decode("utf-8", "replace")}')
    stock = json.loads((PROBE/'stock-event-1.json').read_text())
    fixture_events = [json.loads(path.read_text()) for path in PROBE.glob('fixture-event-*.json')]
    by_id = {event['sequence']: event for event in fixture_events}
    if len(fixture_events) != 127 or set(by_id) != set(range(1, 129))-{4}:
        raise ValueError('Event cap or deliberately failed record differs')
    for mode in ('stock', 'fixture'):
        ready = json.loads((PROBE/f'{mode}-ready.json').read_text())
        if ready['kind'] != 'observer-ready' or ready['completion_event'] is not False:
            raise ValueError('Readiness marker is not explicitly non-completion')
    if (stock['kind'] != 'write-chunks' or stock['return_code_raw'] != 1
            or not stock['next_provider'].endswith('/libsif.so')
            or stock['next_relative_offset'] != '0x274a4'
            or stock['before']['path_bytes_latin1'] != ''
            or stock['before']['file_metadata_observation']['attempted']):
        raise ValueError('Original empty-path branch observation differs')
    changed, unreadable = by_id[2], by_id[3]
    path = '/var/run/probe-quote"-slash\\-line\n-\x80.dat'
    if (changed['before']['path_bytes_latin1'] != path
            or changed['after']['path_bytes_latin1'] != path
            or changed['before']['file_metadata_observation']['exists'] is not False
            or changed['after']['file_metadata_observation']['size_bytes'] != 13
            or changed['before']['stop_raw'] != 0 or changed['after']['stop_raw'] != 2
            or changed['return_code_raw'] != 0 or changed['errno_after_original'] != 72
            or unreadable['before']['stop_read'] or unreadable['before']['path_read']):
        raise ValueError('Fixture metadata, byte escaping, return, or invalid-pointer handling differs')
    if by_id[1]['return_code_raw'] != 47 or by_id[1]['completion_arg_raw'] != -4 or by_id[1]['errno_after_original'] != 71:
        raise ValueError('Backend ABI passthrough differs')
    if any(not event['observation_only'] or event['request_associated']
           or event['monotonic_before_original_ns'] <= 0
           or event['monotonic_after_original_ns'] < event['monotonic_before_original_ns']
           for event in [stock, *fixture_events]):
        raise ValueError('Observation boundary/timing differs')
    return {'exit_code': 0, 'fixture_build': fixture,
            'stock_library_case': 'unmodified libsif WriteChunks on allocated empty-path layout',
            'stock_case_file_written': False,
            'fixture_cases': ['exact backend arguments and original return/errno', 'path byte escaping',
                              'before/after file metadata and Stop', 'unreadable optional object',
                              'record file-open failure leaves call intact', '128-event cap'],
            'fixture_event_count': len(fixture_events), 'image_verified': False,
            'camera_started': False, 'main_namespace_accessed': False,
            'stdout': process.stdout.decode('utf-8', 'replace').splitlines()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-only', action='store_true')
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    report = {'abi': abi_evidence(),
              'build': compile_module('runtime/capture_observer.c', OUT/'capture-observer.so'),
              'probe': {'executed': False} if args.build_only else probe(),
              'save_probe': {'executed': False} if args.build_only else probe_save()}
    (OUT/'capture-observer-build.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
