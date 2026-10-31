"""Build the NX300M 1.15 RAM blob loader and read-only status inspector.

The build verifies original ELF identities and links only the stock glibc ABI.
It neither changes a running camera nor starts/reuses any ARM VM.
"""
import hashlib
import io
import json
from pathlib import Path
import shlex
import struct
import subprocess

from elftools.elf.elffile import ELFFile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/deployment/lut-live-loader'
EXPECTED = {'update': 0x350DC0, 'reset': 0x350E0C,
            'vtable_slot': 0x3BFD08, 'lut_got': 0x3C2AB4, 'lut': 0x955C8}


def wsl(path):
    path = Path(path).resolve()
    return '/mnt/' + path.drive[0].lower() + path.as_posix()[2:]


def elf_record(path, wanted):
    data = path.read_bytes()
    elf = ELFFile(io.BytesIO(data))
    if elf.elfclass != 32 or not elf.little_endian or elf['e_machine'] != 'EM_ARM':
        raise ValueError('Not a little-endian ARM32 ELF: ' + str(path))
    symbols = {s.name: s for s in elf.get_section_by_name('.dynsym').iter_symbols()}
    result = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    for key, name in wanted.items():
        sym = symbols[name]
        if sym['st_shndx'] == 'SHN_UNDEF':
            raise ValueError('Undefined stock symbol: ' + name)
        result[key] = int(sym['st_value'])
    return elf, data, result


def bytes_at(elf, address, length):
    for seg in elf.iter_segments():
        if seg['p_type'] == 'PT_LOAD' and seg['p_vaddr'] <= address and address + length <= seg['p_vaddr'] + seg['p_filesz']:
            return seg.data()[address - seg['p_vaddr']:address - seg['p_vaddr'] + length]
    raise ValueError('No file-backed bytes at ' + hex(address))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    config = json.loads((ROOT / 'project.local.json').read_text(encoding='utf-8'))
    stock = ROOT / config['rootfs']
    iq, _, iq_info = elf_record(stock / 'usr/lib/libiq-fw-slpcam.so', {
        'update': '_ZN17CIpcParameterNode6UpdateEPv',
        'reset': '_ZN17CIpcParameterNode5ResetEv',
        'vtable': '_ZTV12CIpcs3DLutD4'})
    udd, _, udd_info = elf_record(stock / 'usr/lib/libudd.so.3.1', {'lut': 'ipcs_3d_lut'})
    libc, _, libc_info = elf_record(stock / 'lib/libc-2.13.so', {'syscall': 'syscall'})
    _, _, app_info = elf_record(stock / 'usr/bin/di-camera-app-nx300', {})
    syscall = libc.get_section_by_name('.dynsym').get_symbol_by_name('syscall')[0]
    syscall_data = bytes_at(libc, syscall['st_value'], syscall['st_size'])
    svc = next(syscall['st_value'] + i for i in range(0, len(syscall_data), 4)
               if syscall_data[i:i+4] == b'\x00\x00\x00\xef')
    libc_info['svc'] = svc
    iq_info['vtable_slot'] = iq_info['vtable'] + 8
    rel = next(r for sec in iq.iter_sections() if sec['sh_type'] == 'SHT_REL'
               for r in sec.iter_relocations() if r['r_offset'] == EXPECTED['lut_got'])
    dynsym = iq.get_section_by_name('.dynsym')
    if dynsym.get_symbol(rel['r_info_sym']).name != 'ipcs_3d_lut' or rel['r_info_type'] != 22:
        raise ValueError('Expected ipcs_3d_lut R_ARM_JUMP_SLOT not found')
    iq_info['lut_got'] = int(rel['r_offset'])
    actual = {k: iq_info[k] if k in iq_info else udd_info[k] for k in EXPECTED}
    if actual != EXPECTED:
        raise ValueError(f'Stock ABI changed: {actual}')
    vrel = next(r for sec in iq.iter_sections() if sec['sh_type'] == 'SHT_REL'
                for r in sec.iter_relocations() if r['r_offset'] == EXPECTED['vtable_slot'])
    if (vrel['r_info_type'] != 2 or
        dynsym.get_symbol(vrel['r_info_sym']).name != '_ZN17CIpcParameterNode6UpdateEPv' or
        struct.unpack('<I', bytes_at(iq, EXPECTED['vtable_slot'], 4))[0] != 0):
        raise ValueError('Unexpected unrelocated stock vtable Update')
    checks = [('update', iq, EXPECTED['update']), ('reset', iq, EXPECTED['reset']),
              ('lut', udd, EXPECTED['lut'])]
    header = ['/* Generated from original NX300M 1.15 ELF; do not edit. */']
    for key, elf, address in checks:
        raw = bytes_at(elf, address, 32)
        header.append(f'static const unsigned char stock_{key}[32] = {{' + ','.join(str(x) for x in raw) + '};')
    for key, rec in [('iq', iq_info), ('udd', udd_info), ('libc', libc_info), ('app', app_info)]:
        header += [f'#define STOCK_{key.upper()}_SIZE {rec["bytes"]}U',
                   f'#define STOCK_{key.upper()}_SHA "{rec["sha256"]}"']
    factory = dynsym.get_symbol_by_name('_ZN12CIpcs3DLutD47m_sRLutE')[0]['st_value']
    header += [f'#define STOCK_SVC {svc}U', f'#define STOCK_FACTORY_RED {factory}U']
    svc_bytes = bytes_at(libc, svc, 32)
    header.append('static const unsigned char stock_svc[32] = {' + ','.join(str(x) for x in svc_bytes) + '};')
    manifest = OUT / 'lut_live_stock.h'
    manifest.write_text('\n'.join(header) + '\n', encoding='ascii', newline='\n')
    target = OUT / 'lut_live_loader.arm'
    guest_stock = '/opt/nx300m-lab/stock-1.15'
    script = f'''set -eu
tc=/opt/nx300m-toolchain/usr
rf={shlex.quote(guest_stock)}
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=gnu99 -O2 -Wall -Wextra -Werror -march=armv7-a -mfloat-abi=soft \\
 -D_TIME_BITS=32 -D_FILE_OFFSET_BITS=64 -fno-stack-protector -fno-pic -no-pie \\
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 \\
 -Wl,-z,defs -Wl,-rpath-link,"$rf/lib" -I{shlex.quote(wsl(OUT))} \\
 {shlex.quote(wsl(ROOT / 'runtime/lut_live_loader.c'))} \\
 {shlex.quote(wsl(ROOT / 'work/reference/metol/test_server/filmlab/src/crt0_arm.S'))} \\
 "$rf/lib/libc-2.13.so" -lgcc -o {shlex.quote(wsl(target))}
"$tc/bin/arm-linux-gnueabi-readelf" -h -A -d -V {shlex.quote(wsl(target))}
'''
    result = subprocess.run(['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'sh', '-s'],
                            input=script.encode(), capture_output=True)
    (OUT / 'build.stdout.log').write_bytes(result.stdout)
    (OUT / 'build.stderr.log').write_bytes(result.stderr)
    if result.returncode:
        raise RuntimeError(f'ARM build exit {result.returncode}: {result.stderr.decode("utf-8", "replace")}')
    built = ELFFile(io.BytesIO(target.read_bytes()))
    versions = built.get_section_by_name('.gnu.version_r')
    required = [aux.name for _, auxiliaries in versions.iter_versions() for aux in auxiliaries] if versions else []
    if any(name.startswith('GLIBC_') and tuple(map(int, name[6:].split('.'))) > (2, 13) for name in required):
        raise ValueError('Newer glibc ABI leaked into standalone binary')
    report = {'artifact': target.relative_to(ROOT).as_posix(), 'build_exit_code': 0,
              'machine': built['e_machine'], 'required_versions': required,
              'stock': {'iq': iq_info, 'udd': udd_info, 'libc': libc_info, 'app': app_info}, 'offsets': actual,
              'default': 'status (read only; PID required)', 'apply_enabled': True,
              'restore_enabled': True, 'target_memory_writes': 'new private blob, two verified RW slots; restore invalidates verified LUT node cache',
              'repeat_apply': 'reuse same PID/starttime/root and byte-identical RX image; restored disabled mapping requires empty contexts; installed hooks are idempotent',
              'remote_execution': 'pure mmap2/cacheflush/mprotect syscalls using verified stock libc SVC; no remote C/dlopen',
              'timeout': '15 second supervisor deadline; recovery tracer remains alive until borrowed registers are restored',
              'vm_started': False, 'camera_or_card_accessed': False}
    (OUT / 'build-report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
