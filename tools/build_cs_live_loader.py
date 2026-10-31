"""Build fixture-only CS live loader against the original NX300M glibc ABI."""
import io
import json
from pathlib import Path
import shlex
import subprocess

from elftools.elf.elffile import ELFFile
from build_lut_live_loader import elf_record, bytes_at, wsl

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/deployment/cs-live-loader'
SPECS = {
    'iq': ('usr/lib/libiq-fw-slpcam.so', {
        'update': ('_ZN15CPreProcessorD413UpdateCsParamEb', 0x343e74, 1940),
        'init': ('_ZN15CPreProcessorD411InitCsParamEv', 0x34316c, 3192),
        'iq_slot': ('_ZN13CIpcSingletonI11CIQLensInfoE11m_pInstanceE', 0x45f3ec, 4),
        'pp_slot': ('_ZN13CIpcSingletonI15CPreProcessorD4E11m_pInstanceE', 0x463fc8, 4),
    }),
    'udd': ('usr/lib/libudd.so.3.1', {
        'common': ('pp_core_common_lut_init', 0x21f10, 88),
        'info': ('pp_core_cs_set_info', 0x22de0, 60),
        'onoff': ('pp_core_cs_onoff', 0x22e1c, 60),
    }),
    'lens': ('usr/lib/liblens-fw-prod.so', {
        'ready': ('_ZN14CLensOperation13IsModuleReadyEv', 0x2b250, 64),
        'operation_slot': ('_ZN14CLensOperation11m_pInstanceE', 0x88164, 4),
    }),
    'libc': ('lib/libc-2.13.so', {'syscall': ('syscall', None, None)}),
    'app': ('usr/bin/di-camera-app-nx300', {}),
}
GOTS = {'update': 0x3c1158, 'common': 0x3c0718, 'info': 0x3c3208, 'onoff': 0x3c1228}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    stock = ROOT / json.loads((ROOT / 'project.local.json').read_text())['rootfs']
    records, elves = {}, {}
    header = ['/* Generated only from original NX300M 1.15 ELF. */']
    checks = []
    for kind, (relative, specs) in SPECS.items():
        elf, _, record = elf_record(stock / relative, {k: s[0] for k, s in specs.items()})
        records[kind], elves[kind] = record, elf
        dynsym = elf.get_section_by_name('.dynsym')
        header += [f'#define STOCK_{kind.upper()}_SIZE {record["bytes"]}U',
                   f'#define STOCK_{kind.upper()}_SHA "{record["sha256"]}"']
        for key, (name, address, size) in specs.items():
            symbol = dynsym.get_symbol_by_name(name)[0]
            if address is not None and (symbol['st_value'], symbol['st_size']) != (address, size):
                raise ValueError(f'Stock ABI differs: {name}')
            if key.endswith('_slot'):
                if elf.get_section(symbol['st_shndx'])['sh_type'] != 'SHT_NOBITS':
                    raise ValueError(f'Expected BSS singleton: {name}')
            if address is None:
                continue
            header.append(f'#define STOCK_{key.upper()} {address}U')
            if key.endswith('_slot'):
                continue
            raw = bytes_at(elf, address, size)
            header.append(f'static const unsigned char stock_{key}[]={{' + ','.join(map(str, raw)) + '};')
            checks.append((('iq', 'udd', 'lens').index(kind), address, size, f'stock_{key}', 1))
    iq = elves['iq']
    dynsym = iq.get_section_by_name('.dynsym')
    names = []
    for key, address in GOTS.items():
        name = SPECS['iq'][1]['update'][0] if key == 'update' else SPECS['udd'][1][key][0]
        relocations = [r for sec in iq.iter_sections() if sec['sh_type'] == 'SHT_REL'
                       for r in sec.iter_relocations() if r['r_offset'] == address]
        if len(relocations) != 1 or relocations[0]['r_info_type'] != 22 or dynsym.get_symbol(relocations[0]['r_info_sym']).name != name:
            raise ValueError('Unreviewed R_ARM_JUMP_SLOT: ' + key)
        header.append(f'#define STOCK_{key.upper()}_GOT {address}U')
        names.append(name)
    header.append('static const char *stock_slot_names[4]={' + ','.join(json.dumps(n) for n in names) + '};')
    table_address = 0x6fb54
    table = bytes_at(elves['lens'], table_address, 23 * 4)
    header.append('static const unsigned char stock_ready_table[]={' + ','.join(map(str, table)) + '};')
    checks.append((2, table_address, len(table), 'stock_ready_table', 0))
    header.append('struct stock_check {unsigned image,address,size;const unsigned char *bytes;int executable;};')
    header.append('static const struct stock_check stock_checks[]={' + ','.join(
        '{' + f'{image},{address},{size},{data},{executable}' + '}' for image, address, size, data, executable in checks) + '};')
    libc = elves['libc']
    symbol = libc.get_section_by_name('.dynsym').get_symbol_by_name('syscall')[0]
    raw = bytes_at(libc, symbol['st_value'], symbol['st_size'])
    svc = next(symbol['st_value'] + i for i in range(0, len(raw), 4) if raw[i:i+4] == b'\x00\x00\x00\xef')
    header += [f'#define STOCK_SVC {svc}U', 'static const unsigned char stock_svc[32]={' +
               ','.join(map(str, bytes_at(libc, svc, 32))) + '};']
    (OUT / 'cs_live_stock.h').write_text('\n'.join(header) + '\n', encoding='ascii', newline='\n')
    target = OUT / 'cs_live_loader.arm'
    script = f'''set -eu
tc=/opt/nx300m-toolchain/usr
rf=/opt/nx300m-lab/stock-1.15
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=gnu99 -O2 -Wall -Wextra -Werror -march=armv7-a -mfloat-abi=soft \\
 -D_TIME_BITS=32 -D_FILE_OFFSET_BITS=64 -fno-stack-protector -fno-pic -no-pie \\
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 \\
 -Wl,-z,defs -Wl,-rpath-link,"$rf/lib" -I{shlex.quote(wsl(OUT))} \\
 {shlex.quote(wsl(ROOT / 'runtime/cs_live_loader.c'))} \\
 {shlex.quote(wsl(ROOT / 'work/reference/metol/test_server/filmlab/src/crt0_arm.S'))} \\
 "$rf/lib/libc-2.13.so" -lgcc -o {shlex.quote(wsl(target))}
"$tc/bin/arm-linux-gnueabi-readelf" -h -A -d -V {shlex.quote(wsl(target))}
'''
    result = subprocess.run(['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'sh', '-s'], input=script.encode(), capture_output=True)
    (OUT / 'build.stdout.log').write_bytes(result.stdout)
    (OUT / 'build.stderr.log').write_bytes(result.stderr)
    if result.returncode:
        raise RuntimeError(f'CS ARM build exit {result.returncode}: {result.stderr.decode("utf-8", "replace")}')
    built = ELFFile(io.BytesIO(target.read_bytes()))
    versions = built.get_section_by_name('.gnu.version_r')
    required = [a.name for _, auxiliaries in versions.iter_versions() for a in auxiliaries] if versions else []
    if any(n.startswith('GLIBC_') and tuple(map(int, n[6:].split('.'))) > (2, 13) for n in required):
        raise ValueError('Newer glibc leaked into loader')
    report = dict(artifact=target.relative_to(ROOT).as_posix(), build_exit_code=0, stock=records,
                  required_versions=required, offsets=GOTS, default='read-only status',
                  production_mutations_enabled=False, mutation_target='--fixture with exact cs_live_fixture.arm executable',
                  remote_execution='pure verified SVC mmap2/cacheflush/mprotect; no remote C/dlopen',
                  restore='disable, drain inflight/busy, restore four GOT slots; retain mapping',
                  request='one-shot command; acceptance is not submission or color verification',
                  vm_started=False, camera_or_card_accessed=False)
    (OUT / 'build-report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
