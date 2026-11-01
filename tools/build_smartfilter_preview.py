"""Build the independent, explicit single-slot SmartFilter RAM candidate."""
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
OUT = ROOT/'outputs/deployment/smartfilter-preview'
HOOKS = [
    '_ZN24CLiveviewNodeSmartFilter11smartFilterEP9CCapImagePv22E_LIVEVIEW_SMARTFILTER',
    '_Z26ADE_SmartFilter_YccProcess9eADE_MODEP9ADE_Image',
    '_ZN18CDDWrapperIPCS_B2Y16PrepareOperationEPFvvE',
    '_ZN18CDDWrapperIPCS_B2Y15FinishOperationEv',
    '_ZNK17CProcessParameter13GetEffectModeEv',
]
# Configuration offsets are C offsetof expressions, not a second handwritten ABI.
BINDINGS = [
    ('b2y_instance',0,'_ZN18CDDWrapperIPCS_B2Y11GetInstanceEv'),
    ('get_semaphore',0,'_ZN18CDDWrapperIPCS_B2Y12GetSemaphoreE18eIPCS_SEMAPHORE_ID'),
    ('api.trywait',5,'sem_trywait'), ('api.post',5,'sem_post'),
    ('api.open',3,'ipcs_open'), ('api.close',3,'ipcs_close'), ('api.reset',3,'ipcs_init'),
    ('api.write_lut',3,'ipcs_3d_lut'),
    ('api.ade_init',1,'_Z19ADE_Ipcs_param_initv'),
    ('api.ycc',1,'_Z30ADE_Ipcs_Ycc_correct_Param_SETP9ADE_Image'),
    ('api.iq_initialize',2,'_Z17IQ_IPC_Initializei'), ('api.iq_process',2,'_Z14IQ_IPC_Processi'),
    ('api.width',1,'_ZN9ADE_Image8GetWidthEv'),
    ('api.height',1,'_ZN9ADE_Image9GetHeightEv'),
    ('api.stride',1,'_ZN9ADE_Image9GetStrideEv'),
    ('api.sampling',1,'_ZN9ADE_Image10GetSamTypeEv'),
    ('api.address',1,'_ZN9ADE_Image10GetAddressE17eADE_ADDRESS_TYPE'),
    ('api.flush',1,'_ZN9ADE_Image11FlushDCacheEjj'),
] + [(f'api.ade_switch[{i}]',1,name) for i,name in enumerate([
    'ADE_IP_switch_pcse_GSHC','ADE_IP_switch_pcse_PSHC','ADE_IP_switch_pcse_SE',
    'ADE_IP_switch_RGB3D_LUT','ADE_IP_switch_YCNR','ADE_IP_switch_YLUT'])]
LIBS = ['usr/lib/libcapture-fw-slpcam-nx300.so','usr/lib/libdi-alg-ipplatform.so',
        'usr/lib/libiq-fw-slpcam.so','usr/lib/libudd.so.3.1','lib/libc-2.13.so','lib/libpthread-2.13.so',
        'usr/lib/libdi-alg-smartfilter.so']


def build():
    OUT.mkdir(parents=True,exist_ok=True)
    rootfs = Path(json.loads((ROOT/'project.local.json').read_text(encoding='utf-8-sig'))['rootfs'])
    elfs, records = [], []
    for name in LIBS:
        elf,_,record = elf_record(rootfs/name,{})
        elfs.append(elf); records.append(dict(name=Path(name).name,**record))
    _,_,app = elf_record(rootfs/'usr/bin/di-camera-app-nx300',{})
    dyns = [elf.get_section_by_name('.dynsym') for elf in elfs]
    definitions = [{s.name:s for s in dyn.iter_symbols() if s['st_shndx']!='SHN_UNDEF'} for dyn in dyns]
    relocations = [(r,dyns[0].get_symbol(r['r_info_sym']).name)
                   for sec in elfs[0].iter_sections() if sec['sh_type']=='SHT_REL'
                   for r in sec.iter_relocations() if r['r_info_type']==22]
    def symbol(image,name):
        if name not in definitions[image]: raise ValueError(f'Undefined native binding {LIBS[image]}:{name}')
        return definitions[image][name]
    header = ['/* Generated from the original NX300M 1.15 ELF. */',
              f'#define STOCK_APP_SIZE {app["bytes"]}U',f'#define STOCK_APP_SHA "{app["sha256"]}"',
              f'#define STOCK_IMAGES {len(records)}U',
              'static const struct stock_image {const char *name; unsigned size; const char *sha;} stock_images[]={']
    header += [f'{{"{v["name"]}",{v["bytes"]}U,"{v["sha256"]}"}},' for v in records]
    header += ['};','static const struct stock_slot {unsigned slot,image,address,resolver;} stock_slots[]={']
    hook_records = []
    for i,name in enumerate(HOOKS):
        owner = 6 if i==1 else 0
        sym = symbol(owner,name)
        matches = [r for r,relname in relocations if relname==name]
        if len(matches)!=1: raise ValueError(f'Expected one native PLT slot for {name}, found {len(matches)}')
        slot=int(matches[0]['r_offset']); address=int(sym['st_value'])
        resolver=struct.unpack('<I',bytes_at(elfs[0],slot,4))[0]
        if resolver!=int(elfs[0].get_section_by_name('.plt')['sh_addr']):
            raise ValueError('Unexpected ARM PLT resolver')
        header += [f'{{{slot}U,{owner}U,{address}U,{resolver}U}},']
        hook_records.append(dict(symbol=name,slot=hex(slot),owner=records[owner]['name'],address=hex(address)))
    header += ['};','static const struct stock_binding {unsigned offset,image,address;} stock_bindings[]={']
    checks = {}
    for field,image,name in BINDINGS:
        sym=symbol(image,name); address=int(sym['st_value'])
        header += [f'{{offsetof(struct nx_sfr_config,{field}),{image}U,{address}U}},']
        if sym['st_info']['type']=='STT_FUNC': checks[image,address]=min(int(sym['st_size']),32)
    for i,name in enumerate(HOOKS):
        image=6 if i==1 else 0; sym=symbol(image,name)
        checks[image,int(sym['st_value'])]=min(int(sym['st_size']),32)
    header += ['};']
    libc_syscall=symbol(4,'syscall'); address=int(libc_syscall['st_value'])
    code=bytes_at(elfs[4],address,int(libc_syscall['st_size']))
    svc=next(address+i for i in range(0,len(code),4) if code[i:i+4]==b'\0\0\0\xef')
    header += [f'#define STOCK_SVC {svc}U']
    checks[4,svc]=32
    header += ['static const struct stock_check {unsigned image,address,size; unsigned char bytes[32];} stock_checks[]={']
    for (image,address),size in sorted(checks.items()):
        header += [f'{{{image}U,{address}U,{size}U,{{'+','.join(str(v) for v in bytes_at(elfs[image],address,size))+'}},']
    header += ['};']
    digest=hashlib.sha256()
    sources=['smartfilter_ram.c','smartfilter_ram.h','smartfilter_ram.ld','smartfilter_lut_bridge.c',
             'smartfilter_lut_bridge.h','smartfilter_ipcs.c','smartfilter_ipcs.h']
    for name in sources: digest.update((ROOT/'runtime'/name).read_bytes())
    digest.update('\n'.join(header).encode())
    build_id=int.from_bytes(digest.digest()[:4],'little') or 1
    header += [f'#define NX_SFR_BUILD {build_id}U']
    (OUT/'smartfilter_stock.h').write_text('\n'.join(header)+'\n',encoding='ascii',newline='\n')
    elf_path=OUT/'smartfilter_ram.elf'
    result=subprocess.run([shutil.which('clang'),'--target=arm-linux-gnueabi','-march=armv7-a','-marm',
        '-mfloat-abi=soft','-std=gnu99','-O2','-Wall','-Wextra','-Werror','-ffreestanding','-fpie',
        '-fvisibility=hidden','-fno-builtin','-fno-stack-protector','-fno-unwind-tables',
        '-fno-asynchronous-unwind-tables','-nostdlib','-static','-fuse-ld=lld',
        '-Wl,--no-undefined','-Wl,--build-id=none','-Wl,--emit-relocs',f'-Wl,--defsym=nx_sfr_build={build_id}',
        '-Wl,-T,'+str(ROOT/'runtime/smartfilter_ram.ld'),
        *[str(ROOT/'runtime'/name) for name in sources if name.endswith('.c')],'-o',str(elf_path)],capture_output=True)
    (OUT/'ram-build.stderr.log').write_bytes(result.stderr)
    if result.returncode: raise RuntimeError(f'RAM module build exit {result.returncode}: {result.stderr.decode(errors="replace")}')
    elf=ELFFile(io.BytesIO(elf_path.read_bytes()))
    if any(sec['sh_type'] in ('SHT_RELA','SHT_DYNAMIC') for sec in elf.iter_sections()):
        raise ValueError('RAM module contains dynamic relocations')
    # Static relocation metadata is kept in the ELF for this audit, then omitted
    # from the flat RAM image. No absolute callback/GOT fixups may be required.
    for sec in elf.iter_sections():
        if sec['sh_type']=='SHT_REL':
            for rel in sec.iter_relocations():
                if rel['r_info_type'] in (2,21,22,26,55,96):
                    raise ValueError(f'Absolute/GOT relocation in RAM code: {sec.name} {rel.entry}')
    words=struct.unpack('<16I',elf.get_section_by_name('.header').data())
    symbols={s.name:s for s in elf.get_section_by_name('.symtab').iter_symbols()}
    if words[12]!=symbols['nx_sfr_config']['st_size'] or words[14]!=symbols['nx_sfr_runtime']['st_size']:
        raise ValueError('RAM config/runtime size differs')
    binary=bytearray(words[2])
    for sec in elf.iter_sections():
        if sec['sh_flags']&2 and sec['sh_type']!='SHT_NOBITS':
            start=int(sec['sh_addr']); binary[start:start+sec['sh_size']]=sec.data()
    (OUT/'smartfilter_ram.bin').write_bytes(binary)
    script=f'''set -eu
tc=/opt/nx300m-toolchain/usr
rf=/opt/nx300m-lab/stock-1.15
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=gnu99 -O2 -Wall -Wextra -Werror -Wno-unused-function -march=armv7-a -mfloat-abi=soft \\
 -D_TIME_BITS=32 -D_FILE_OFFSET_BITS=64 -fno-stack-protector -fno-pic -no-pie \\
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 \\
 -Wl,-z,defs -Wl,-rpath-link,"$rf/lib" -I{shlex.quote(wsl(OUT))} \\
 {shlex.quote(wsl(ROOT/'runtime/smartfilter_loader.c'))} \\
 {shlex.quote(wsl(ROOT/'work/reference/metol/test_server/filmlab/src/crt0_arm.S'))} \\
 "$rf/lib/libc-2.13.so" -lgcc -o {shlex.quote(wsl(OUT/'smartfilter_loader.arm'))}
'''
    result=subprocess.run(['wsl.exe','-d','Debian','-u','root','-e','sh','-s'],input=script.encode(),capture_output=True)
    (OUT/'loader-build.stderr.log').write_bytes(result.stderr)
    if result.returncode: raise RuntimeError(f'Loader build exit {result.returncode}: {result.stderr.decode(errors="replace")}')
    built=ELFFile(io.BytesIO((OUT/'smartfilter_loader.arm').read_bytes()))
    versions=built.get_section_by_name('.gnu.version_r')
    required=[aux.name for _,items in versions.iter_versions() for aux in items] if versions else []
    if any(v.startswith('GLIBC_') and tuple(map(int,v[6:].split('.')))>(2,13) for v in required):
        raise ValueError('New glibc dependency in native loader')
    report=dict(build_exit_code=0,build=build_id,blob_bytes=len(binary),config_bytes=words[12],runtime_bytes=words[14],
        stock=records,hooks=hook_records,required_versions=required,host_liveview_mode=1,host_still_mode=2,
        automatic_installation=False,global_userdata_changed=False,jpeg_lut_hook_changed=False,absolute_relocations_rejected=True,
        physical_pixels_verified=False,raw_menu_restrictions_resolved=False)
    (OUT/'ram-build-report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return report


if __name__=='__main__':
    print(json.dumps(build(),indent=2))
