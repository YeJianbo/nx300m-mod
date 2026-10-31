"""Build the standalone service trigger against original ARM glibc 2.13."""
import io
import json
from pathlib import Path
import posixpath
import shlex
import subprocess
import tarfile
from elftools.elf.elffile import ELFFile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/deployment/service-loader'


def wsl(path):
    return '/mnt/' + path.drive[0].lower() + path.as_posix()[2:]


def stage():
    destination = OUT / 'stock-dependencies'
    destination.mkdir(parents=True, exist_ok=True)
    records = []
    with tarfile.open(ROOT / 'work/stock-1.15/rootfs.tar') as archive:
        members = {m.name: m for m in archive}
        def resolve(name):
            for _ in range(32):
                member = members[name]
                if member.isfile():
                    return name, archive.extractfile(member).read()
                if member.issym():
                    name = posixpath.normpath(member.linkname.lstrip('/') if member.linkname.startswith('/') else
                                             posixpath.join(posixpath.dirname(name), member.linkname))
                elif member.islnk():
                    name = member.linkname.lstrip('/')
                else:
                    raise ValueError('Unexpected member: ' + name)
            raise ValueError('Link cycle')
        pending = [('lib/ld-linux.so.3', 'lib/ld-linux.so.3'), ('lib/libc.so.6', 'lib/libc.so.6'),
                   ('lib/libdl.so.2', 'lib/libdl.so.2'), ('usr/lib/libecore.so.1', 'usr/lib/libecore.so.1'),
                   ('bin/arm/busybox', 'bin/busybox')]
        seen = set()
        while pending:
            path, source = pending.pop()
            if path in seen:
                continue
            seen.add(path)
            member, data = resolve(source)
            target = destination / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            elf = ELFFile(io.BytesIO(data))
            dynamic = elf.get_section_by_name('.dynamic')
            tags = list(dynamic.iter_tags()) if dynamic else []
            needed = [t.needed for t in tags if t.entry.d_tag == 'DT_NEEDED']
            soname = next((t.soname for t in tags if t.entry.d_tag == 'DT_SONAME'), None)
            records.append({'destination': path, 'stock_member': member, 'bytes': len(data), 'needed': needed})
            if soname and Path(path).name != soname:
                alias = posixpath.join(posixpath.dirname(path), soname)
                pending.append((alias, source))
            for library in needed:
                origin = next((p + library for p in ('lib/', 'usr/lib/') if p + library in members), None)
                if not origin:
                    raise ValueError('Missing original library: ' + library)
                pending.append((origin, origin))
    (OUT / 'stock-provenance.json').write_text(json.dumps(records, indent=2) + '\n', encoding='utf-8')
    return destination


def compile_arm(source, output, shared=False, extra=()):
    stock = OUT / 'stock-dependencies'
    root = wsl(stock)
    script = f'''set -eu
tc=/opt/nx300m-toolchain/usr
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=gnu99 -O2 -Wall -Wextra -Werror -march=armv7-a -mfloat-abi=soft \\
 -D_TIME_BITS=32 -D_FILE_OFFSET_BITS=64 -fno-stack-protector -nostdlib \\
 {'-shared -fPIC -Wl,-soname,service-loader.so' if shared else '-fno-pic -no-pie -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3'} \\
 -Wl,-z,defs -Wl,-rpath-link,{shlex.quote(root + '/lib')} -Wl,-rpath-link,{shlex.quote(root + '/usr/lib')} \\
 {shlex.quote(wsl(source))} {' '.join(shlex.quote(wsl(p)) for p in extra)} \\
 -Wl,--no-as-needed {shlex.quote(root + '/usr/lib/libecore.so.1') if not shared else ''} \\
 {shlex.quote(root + '/lib/libc.so.6')} {shlex.quote(root + '/lib/libdl.so.2')} \\
 {shlex.quote(root + '/lib/libgcc_s.so.1')} -lgcc -o {shlex.quote(wsl(output))}
"$tc/bin/arm-linux-gnueabi-readelf" -h -A -d -V -Ws {shlex.quote(wsl(output))}
'''
    result = subprocess.run(['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'sh', '-s'],
                            input=script.encode(), capture_output=True)
    (OUT / (output.stem + '-build.stdout.log')).write_bytes(result.stdout)
    (OUT / (output.stem + '-build.stderr.log')).write_bytes(result.stderr)
    if result.returncode:
        raise RuntimeError(f'ARM compile exit {result.returncode}: {result.stderr.decode("utf-8", "replace")}')
    return result


def main():
    stage()
    output = OUT / 'service-loader.so'
    compile_arm(ROOT / 'runtime/service_loader.c', output, shared=True)
    elf = ELFFile(io.BytesIO(output.read_bytes()))
    dynamic = elf.get_section_by_name('.dynamic')
    report = {'artifact': output.relative_to(ROOT).as_posix(), 'bytes': output.stat().st_size,
              'machine': elf['e_machine'], 'type': elf['e_type'], 'build_exit_code': 0,
              'needed': [t.needed for t in dynamic.iter_tags() if t.entry.d_tag == 'DT_NEEDED'],
              'stock_glibc': '2.13', 'arm_float_abi': 'soft', 'integrated_into_candidate': False}
    (OUT / 'build-report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
