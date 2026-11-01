"""Build the separate IPCM RAM preview runtime against original NX300M ELF."""
import hashlib
import io
import json
from pathlib import Path
import shlex
import shutil
import struct
import subprocess

from elftools.elf.elffile import ELFFile
from build_lut_live_loader import elf_record, bytes_at, wsl

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/deployment/ipcm-preview'
UPDATE = '_ZN17CIpcParameterNode6UpdateEPv'
RESET = '_ZN17CIpcParameterNode5ResetEv'
SETTERS = ['ipcm_color_correction_param', 'ipcm_tone_lut', 'ipcm_rgb2ycc_param', 'ipcm_pcse_primary_gain_param']


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    # A running older blob must be restored with its matching loader.
    previous = OUT/'previous-runtime/abi2'
    if (OUT/'build-report.json').exists() and json.loads((OUT/'build-report.json').read_text())['runtime_abi']==2 and not previous.exists():
        previous.mkdir(parents=True)
        for name in ('ipcm_preview_loader.arm','ipcm_preview_blob.bin','build-report.json'):
            shutil.copy2(OUT/name,previous/name)
    stock = ROOT / json.loads((ROOT / 'project.local.json').read_text())['rootfs']
    iq, _, iq_rec = elf_record(stock / 'usr/lib/libiq-fw-slpcam.so', {
        'update': UPDATE, 'reset': RESET, 'color': '_ZTV12CIpcmColorD4', 'gamma': '_ZTV12CIpcmGammaD4', 'pcse': '_ZTV11CIpcmPcseD4'})
    udd, _, udd_rec = elf_record(stock / 'usr/lib/libudd.so.3.1', dict(zip(['ccm', 'tone', 'chm', 'pcse'], SETTERS)))
    libc, _, libc_rec = elf_record(stock / 'lib/libc-2.13.so', {'syscall': 'syscall'})
    _, _, app_rec = elf_record(stock / 'usr/bin/di-camera-app-nx300', {})
    expected = {'update': 0x350dc0, 'reset': 0x350e0c, 'color': 0x3bffc0, 'gamma': 0x3c0040, 'pcse': 0x3c0070}
    if any(iq_rec[k] != v for k, v in expected.items()):
        raise ValueError('Unreviewed IPCM IQ ABI')
    syms = iq.get_section_by_name('.dynsym')
    rels = [r for sec in iq.iter_sections() if sec['sh_type'] == 'SHT_REL' for r in sec.iter_relocations()]
    slots = []
    for name in SETTERS:
        matches = [r for r in rels if r['r_info_type'] == 22 and syms.get_symbol(r['r_info_sym']).name == name]
        if len(matches) != 1:
            raise ValueError('Unreviewed setter GOT: ' + name)
        slots.append(matches[0]['r_offset'])
    for key in ('color', 'gamma', 'pcse'):
        address = iq_rec[key] + 8
        match = [r for r in rels if r['r_offset'] == address]
        if len(match) != 1 or match[0]['r_info_type'] != 2 or syms.get_symbol(match[0]['r_info_sym']).name != UPDATE:
            raise ValueError('Unreviewed dedicated Update slot')
        slots.append(address)
    header = ['/* Generated from the original NX300M 1.15 binaries. */']
    for kind, rec in [('iq', iq_rec), ('udd', udd_rec), ('libc', libc_rec), ('app', app_rec)]:
        header += [f'#define STOCK_{kind.upper()}_SIZE {rec["bytes"]}U', f'#define STOCK_{kind.upper()}_SHA "{rec["sha256"]}"']
    checks = []
    for image, elf, names in [(0, iq, [UPDATE, RESET]), (1, udd, SETTERS)]:
        for name in names:
            symbol = elf.get_section_by_name('.dynsym').get_symbol_by_name(name)[0]
            raw = bytes_at(elf, symbol['st_value'], symbol['st_size'])
            ident = f'stock_code_{len(checks)}'
            header.append('static const unsigned char ' + ident + '[]={' + ','.join(map(str, raw)) + '};')
            checks.append((image, symbol['st_value'], len(raw), ident))
    header += ['struct stock_check {unsigned image,address,size;const unsigned char *bytes;};',
               'static const struct stock_check stock_checks[]={' + ','.join('{' + f'{i},{a},{s},{n}' + '}' for i,a,s,n in checks) + '};',
               'static const unsigned stock_slots[7]={' + ','.join(map(str, slots)) + '};',
               'static const unsigned stock_originals[7]={' + ','.join(map(str,[udd_rec['ccm'],udd_rec['tone'],udd_rec['chm'],udd_rec['pcse']]+[iq_rec['update']]*3)) + '};',
               f'#define STOCK_UPDATE {iq_rec["update"]}U', f'#define STOCK_RESET {iq_rec["reset"]}U',
               f'#define STOCK_COLOR_VPTR {iq_rec["color"]+8}U', f'#define STOCK_GAMMA_VPTR {iq_rec["gamma"]+8}U',
               f'#define STOCK_PCSE_VPTR {iq_rec["pcse"]+8}U']
    symbol = libc.get_section_by_name('.dynsym').get_symbol_by_name('syscall')[0]
    raw = bytes_at(libc, symbol['st_value'], symbol['st_size'])
    svc = next(symbol['st_value']+i for i in range(0,len(raw),4) if raw[i:i+4] == b'\0\0\0\xef')
    header += [f'#define STOCK_SVC {svc}U', 'static const unsigned char stock_svc[32]={' + ','.join(map(str,bytes_at(libc,svc,32))) + '};']
    digest = hashlib.sha256()
    for name in ['ipcm_preview_blob.c', 'ipcm_preview_blob.h', 'ipcm_preview_blob.ld']:
        digest.update((ROOT / 'runtime' / name).read_bytes())
    build = int.from_bytes(digest.digest()[:4], 'little') or 1
    header.append(f'#define NX_IPCM_BUILD {build}U')
    (OUT / 'ipcm_preview_stock.h').write_text('\n'.join(header)+'\n',encoding='ascii',newline='\n')
    # Header sizes are linker values verified against the actual ARM objects.
    config_size = 512
    stats_size = (1+10*6+5*3+9+19+19+26)*4 + 2048
    elf_path = OUT / 'ipcm_preview_blob.elf'
    subprocess.run([shutil.which('clang'), '--target=arm-linux-gnueabi', '-march=armv7-a', '-marm', '-mfloat-abi=soft',
                    '-std=c99', '-O2', '-Wall', '-Wextra', '-Werror', '-ffreestanding', '-fpie', '-fvisibility=hidden',
                    '-fno-builtin', '-fno-stack-protector', '-fno-unwind-tables', '-fno-asynchronous-unwind-tables',
                    '-nostdlib', '-static', '-fuse-ld=lld', '-Wl,--no-undefined', '-Wl,--build-id=none',
                    f'-Wl,--defsym=nx_ipcm_build={build}', f'-Wl,--defsym=nx_ipcm_config_size={config_size}',
                    f'-Wl,--defsym=nx_ipcm_stats_size={stats_size}', '-Wl,-T,'+str(ROOT/'runtime/ipcm_preview_blob.ld'),
                    str(ROOT/'runtime/ipcm_preview_blob.c'), '-o', str(elf_path)],check=True)
    elf = ELFFile(io.BytesIO(elf_path.read_bytes()))
    if any(s['sh_type'] in ('SHT_REL','SHT_RELA','SHT_DYNAMIC') for s in elf.iter_sections()):
        raise ValueError('RAM blob must have no dynamic relocations')
    symbols = {s.name:s for s in elf.get_section_by_name('.symtab').iter_symbols()}
    if symbols['nx_ipcm_config']['st_size'] != config_size or symbols['nx_ipcm_stats']['st_size'] != stats_size:
        raise ValueError(f'Runtime layout differs: config={symbols["nx_ipcm_config"]["st_size"]}, stats={symbols["nx_ipcm_stats"]["st_size"]}')
    words = struct.unpack('<22I',elf.get_section_by_name('.header').data())
    binary = bytearray(words[2])
    for sec in elf.iter_sections():
        if sec['sh_flags']&2 and sec['sh_type'] != 'SHT_NOBITS':
            binary[sec['sh_addr']:sec['sh_addr']+sec['sh_size']] = sec.data()
    (OUT/'ipcm_preview_blob.bin').write_bytes(binary)
    script = f'''set -eu
tc=/opt/nx300m-toolchain/usr
rf=/opt/nx300m-lab/stock-1.15
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=gnu99 -O2 -Wall -Wextra -Werror -Wno-unused-function -march=armv7-a -mfloat-abi=soft \\
 -D_TIME_BITS=32 -D_FILE_OFFSET_BITS=64 -fno-stack-protector -fno-pic -no-pie \\
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 \\
 -Wl,-z,defs -Wl,-rpath-link,"$rf/lib" -I{shlex.quote(wsl(OUT))} \\
 {shlex.quote(wsl(ROOT/'runtime/ipcm_preview_loader.c'))} \\
 {shlex.quote(wsl(ROOT/'work/reference/metol/test_server/filmlab/src/crt0_arm.S'))} \\
 "$rf/lib/libc-2.13.so" -lgcc -o {shlex.quote(wsl(OUT/'ipcm_preview_loader.arm'))}
'''
    result = subprocess.run(['wsl.exe','-d','Debian','-u','root','-e','sh','-s'],input=script.encode(),capture_output=True)
    (OUT/'build.stdout.log').write_bytes(result.stdout)
    (OUT/'build.stderr.log').write_bytes(result.stderr)
    if result.returncode:
        raise RuntimeError(f'IPCM loader build exit {result.returncode}: {result.stderr.decode(errors="replace")}')
    built = ELFFile(io.BytesIO((OUT/'ipcm_preview_loader.arm').read_bytes()))
    versions = built.get_section_by_name('.gnu.version_r')
    required = [a.name for _,items in versions.iter_versions() for a in items] if versions else []
    if any(v.startswith('GLIBC_') and tuple(map(int,v[6:].split('.'))) > (2,13) for v in required):
        raise ValueError('New glibc ABI leaked into IPCM loader')
    report = dict(build_exit_code=0,build=build,blob_bytes=len(binary),config_bytes=config_size,stats_bytes=stats_size,
                  slots=slots,stock=dict(iq=iq_rec,udd=udd_rec,libc=libc_rec,app=app_rec),required_versions=required,
                  runtime_abi=3,profile_bytes=[340,360,456],status_order=['ccm','tone','chm','ycc','phc','psc'],
                  still_config_abi_changed=False,pixel_verified=False,camera_or_card_accessed=False)
    (OUT/'build-report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__ == '__main__':
    main()
