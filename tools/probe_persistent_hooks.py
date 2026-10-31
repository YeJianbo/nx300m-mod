"""Co-load persistent hooks with original ARM loader/Ecore in an independent root."""
import io
import argparse
import json
from pathlib import Path
import posixpath
import shlex
import subprocess
import tarfile
from elftools.elf.elffile import ELFFile
from build_stock_app_loader import PERSISTENT_HOOKS

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/deployment/app-loader/co-load'
CANDIDATE = ROOT / 'outputs/deployment/app-loader/boot-chain/persistent-candidate'
HOOKS = tuple(PERSISTENT_HOOKS)
PROVIDERS = ('usr/bin/di-camera-app-nx300', 'usr/lib/libcapture-fw-slpcam-nx300.so',
             'usr/lib/libiq-fw-slpcam.so', 'usr/lib/libudd.so.3.1')
CRT = '''.syntax unified
.arm
.text
.globl _start
.type _start,%function
_start:
 mov r5,r0
 mov fp,#0
 mov lr,#0
 ldr r1,[sp]
 add r2,sp,#4
 ldr r0,=main
 mov r3,#0
 mov r4,#0
 mov r6,sp
 mov r7,#0
 push {r4,r5,r6,r7}
 bl __libc_start_main
 bl abort
.size _start,.-_start
.section .note.GNU-stack,"",%progbits
'''


def wsl(path):
    return '/mnt/' + path.drive[0].lower() + path.as_posix()[2:]


def stage():
    destination = OUT / 'stock-dependencies'
    destination.mkdir(parents=True, exist_ok=True)
    provenance = []
    provider_symbols = {}
    with tarfile.open(ROOT / 'work/stock-1.15/rootfs.tar') as archive:
        names = {m.name: m for m in archive}
        def resolve(name):
            for _ in range(16):
                member = names[name]
                if member.isfile():
                    return name, archive.extractfile(member).read()
                if member.issym():
                    name = posixpath.normpath(member.linkname.lstrip('/') if member.linkname.startswith('/') else
                                             posixpath.join(posixpath.dirname(name), member.linkname))
                elif member.islnk():
                    name = member.linkname.lstrip('/')
                else:
                    raise ValueError('Unexpected stock dependency member type: ' + name)
            raise ValueError('Stock link cycle: ' + name)
        pending = ['lib/ld-linux.so.3', 'lib/libc.so.6', 'lib/libdl.so.2',
                   'usr/lib/libecore.so.1', 'usr/lib/libudd.so.3.1']
        seen = set()
        while pending:
            name = pending.pop()
            if name in seen:
                continue
            seen.add(name)
            member, data = resolve(name)
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            elf = ELFFile(io.BytesIO(data))
            dynamic = elf.get_section_by_name('.dynamic')
            needed = [t.needed for t in dynamic.iter_tags() if t.entry.d_tag == 'DT_NEEDED'] if dynamic else []
            soname = next((t.soname for t in dynamic.iter_tags() if t.entry.d_tag == 'DT_SONAME'), None) if dynamic else None
            provenance.append({'destination': name, 'stock_member': member, 'bytes': len(data), 'needed': needed})
            if soname and Path(name).name != soname:
                alias = posixpath.join(posixpath.dirname(name), soname)
                if alias in names:
                    pending.append(alias)
            for dependency in needed:
                candidate = next((folder + dependency for folder in ('lib/', 'usr/lib/') if folder + dependency in names), None)
                if candidate is None:
                    raise ValueError('Missing original dependency: ' + dependency)
                pending.append(candidate)
        for name in PROVIDERS:
            if name not in names:
                provider_symbols[name] = {'absent_from_tar': True}
                continue
            member, data = resolve(name)
            elf = ELFFile(io.BytesIO(data))
            symbols = elf.get_section_by_name('.dynsym')
            provider_symbols[name] = {'stock_member': member, 'defined': sorted({s.name for s in symbols.iter_symbols()
                    if s['st_shndx'] != 'SHN_UNDEF' and s.name})}
    (OUT / 'stock-provenance.json').write_text(json.dumps(provenance, indent=2) + '\n', encoding='utf-8')
    source_records = json.loads((CANDIDATE / 'report.json').read_text(encoding='utf-8'))['hooks']
    hook_records = []
    for name in HOOKS:
        source = CANDIDATE / 'rootfs/usr/lib/nx300m-mod' / name
        record = next(x for x in source_records if x['destination'] == '/usr/lib/nx300m-mod/' + name)
        data = source.read_bytes()
        if data != (ROOT / record['source']).read_bytes():
            raise ValueError('Persistent candidate is stale: ' + name)
        target = destination / 'usr/lib/nx300m-mod' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        hook_records.append({'name': name, 'candidate': source.relative_to(ROOT).as_posix(),
                             'current_build': record['source'], 'bytes': len(data), 'bytes_equal': True})
    return destination, provenance, provider_symbols, hook_records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-preload', action='store_true', help='Failure diagnosis: same stock Ecore fixture without hooks')
    parser.add_argument('--trace', action='store_true', help='Record target instruction/syscall trace for a concrete failure')
    parser.add_argument('--lazy', action='store_true', help='Match production: omit LD_BIND_NOW')
    parser.add_argument('--reuse-build', action='store_true', help='Reuse the already compiled fixture during a bounded loader comparison')
    args = parser.parse_args()
    label = 'control' if args.no_preload else 'lazy' if args.lazy else 'probe'
    OUT.mkdir(parents=True, exist_ok=True)
    root, provenance, providers, hooks = stage()
    (OUT / 'crt0.S').write_text(CRT, encoding='utf-8', newline='\n')
    guest_out = wsl(OUT)
    guest_root = wsl(root)
    compile_script = f'''set -eu
tc=/opt/nx300m-toolchain/usr
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=c99 -O0 -Wall -Wextra -Werror -mfloat-abi=soft -fno-pic -no-pie -fno-stack-protector \\
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 \\
 -Wl,--allow-shlib-undefined -Wl,-rpath-link,{shlex.quote(guest_root + '/lib')} \\
 -Wl,-rpath-link,{shlex.quote(guest_root + '/usr/lib')} -Wl,--no-as-needed \\
 {shlex.quote(wsl(ROOT / 'runtime/persistent_hooks_probe.c'))} {shlex.quote(guest_out + '/crt0.S')} \\
 {shlex.quote(guest_root + '/usr/lib/libecore.so.1')} {shlex.quote(guest_root + '/usr/lib/libudd.so.3.1')} \\
 {shlex.quote(guest_root + '/lib/libc.so.6')} {shlex.quote(guest_root + '/lib/libdl.so.2')} \\
 {shlex.quote(guest_root + '/lib/libgcc_s.so.1')} \\
 -lgcc -o {shlex.quote(guest_out + '/persistent-hooks-probe.arm')}
'''
    if not args.reuse_build:
        result = subprocess.run(['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'sh', '-s'],
                                input=compile_script.encode(), capture_output=True)
        (OUT / 'compile.stdout.log').write_bytes(result.stdout)
        (OUT / 'compile.stderr.log').write_bytes(result.stderr)
        if result.returncode:
            raise RuntimeError(f'Compile exited {result.returncode}: {result.stderr.decode("utf-8", "replace")}')
    elif not (OUT / 'persistent-hooks-probe.arm').is_file():
        raise ValueError('No existing compiled fixture to reuse')
    mount_root = OUT / 'probe-root'
    mount_root.mkdir(exist_ok=True)
    preload = ':'.join('/usr/lib/nx300m-mod/' + n for n in HOOKS)
    trace = OUT / 'trace'
    trace.mkdir(exist_ok=True)
    qemu_options = '-strace -d in_asm -D /probe-output/' + label + '.qemu.log' if args.trace else ''
    preload_option = '' if args.no_preload else '-E LD_PRELOAD=' + shlex.quote(preload)
    bind_now_option = '' if args.lazy else '-E LD_BIND_NOW=1'
    run_script = f'''set -eu
mount --make-rprivate /
root={shlex.quote(wsl(mount_root))}
mount -t tmpfs -o mode=0755,nosuid,nodev tmpfs "$root"
cp -a {shlex.quote(guest_root)}/. "$root"/
mkdir -p "$root/var/run" "$root/tmp" "$root/proc" "$root/dev" "$root/mnt/mmc/nx300m-mod"
mkdir -p "$root/probe-output"
chmod 777 {shlex.quote(wsl(trace))}
mount --bind {shlex.quote(wsl(trace))} "$root/probe-output"
chmod 777 "$root/var/run" "$root/tmp"
cp {shlex.quote(guest_out + '/persistent-hooks-probe.arm')} "$root/persistent-hooks-probe.arm"
cp {shlex.quote(wsl(ROOT / 'work/qemu/qemu-arm-static'))} "$root/qemu-arm-static"
chmod -R a+rX "$root"
chmod 755 "$root/persistent-hooks-probe.arm" "$root/qemu-arm-static"
mount -t proc proc "$root/proc"
touch "$root/dev/null" "$root/dev/urandom"
mount --bind /dev/null "$root/dev/null"
mount --bind /dev/urandom "$root/dev/urandom"
exec timeout 12 chroot --userspec=65534:65534 "$root" /qemu-arm-static {qemu_options} -cpu cortex-a9 -L / \\
 -E NX300M_MOD_ROOT=/mnt/mmc/nx300m-mod {bind_now_option} \\
 {preload_option} /persistent-hooks-probe.arm
'''
    (OUT / (label + '-host.sh')).write_text(run_script, encoding='utf-8', newline='\n')
    result = subprocess.run(['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'unshare',
                             '--mount', '--net', '--ipc', '--pid', '--fork', '--mount-proc', 'sh', '-s'],
                            input=run_script.encode(), capture_output=True, timeout=25)
    (OUT / (label + '.stdout.log')).write_bytes(result.stdout)
    (OUT / (label + '.stderr.log')).write_bytes(result.stderr)
    events = [json.loads(line) for line in result.stdout.decode('utf-8', 'replace').splitlines() if line.startswith('{"event":')]
    symbols = [e for e in events if e['event'] == 'symbol']
    exact_dependencies = []
    for entry in symbols:
        exact_dependencies.append({**entry, 'static_stock_providers': [name for name, info in providers.items()
                                  if entry['name'] in info.get('defined', [])]})
    report = {'exit_code': result.returncode, 'events': [e for e in events if e['event'] != 'symbol'],
              'hook_count': len(HOOKS), 'expected_hook_mask': (1 << len(HOOKS)) - 1,
              'hooks': hooks, 'stock_dependency_files': len(provenance), 'dynamic_symbol_observations': exact_dependencies,
              'preload_chain': '' if args.no_preload else preload, 'eager_binding_requested': not args.lazy, 'stock_loader_libc_ecore_udd': True,
              'fixture_defines_camera_symbols': False, 'fixture_camera_cmdline_spoofed': False,
              'lut_readiness': 'Expected absent: honest fixture cmdline is not a camera; maps establish co-load only',
              'service_loader_readiness': 'Expected absent: honest fixture executable is not a camera; no service timer registration or SD start execution',
              'scope': 'real ARM loader and real Ecore short loop, under user-mode QEMU; no camera objects or imaging',
              'isolation': 'fresh mount/net/IPC/PID namespace, private tmpfs root, uid65534; only null/urandom devices',
              'physical_camera_verified': False, 'snapshot_verified': False, 'main_runtime_root_changed': False}
    report_name = 'control-report.json' if args.no_preload else 'lazy-report.json' if args.lazy else 'report.json'
    (OUT / report_name).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'exit_code': result.returncode, 'events': report['events'],
                      'missing_from_loaded_stock_scope': [e['name'] for e in exact_dependencies if not e['present_in_loaded_stock_scope']],
                      'report': (OUT / report_name).relative_to(ROOT).as_posix()}, ensure_ascii=False, indent=2))
    if result.returncode:
        print(result.stderr.decode('utf-8', 'replace')[-5000:])
        raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
