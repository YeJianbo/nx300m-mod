"""Build ABI4 SmartFilter, or the separately output ABI7 IPCS-session preview."""
import argparse
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
    '_ZN15CLiveviewCommon18GetSmartFilterModeEv',
    '_ZN24CLiveviewNodeSmartFilter14getWorkAddressE15E_LIVEVIEW_MODE',
]
S1_HOOKS = 5
S2_SLOTS = {5: 0x49037c, 6: 0x4936c8}
MODE_LR = [0x292c24, 0x27d06c, 0x2932bc, 0x2934fc]
MODE_PLT = 0x13a918
WORK_PLT = 0x1442fc
# Configuration offsets are C offsetof expressions, not a second handwritten ABI.
BINDINGS = [
    ('b2y_instance',0,'_ZN18CDDWrapperIPCS_B2Y11GetInstanceEv'),
    ('common_instance',0,'_ZN15CLiveviewCommon11GetInstanceEv'),
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
SESSION_HOOKS = [
    (0,0,'_ZN18CDDWrapperIPCS_B2Y4OpenEv'),
    (0,0,'_ZN18CDDWrapperIPCS_B2Y5CloseEv'),
    (0,0,'_ZN9CLiveview11stopProcessENS_15eLIVEVIEW_STATEEb'),
    (0,0,'_ZN9CLiveview5StartEij'),
    (3,3,'d4_udd_open'), (3,5,'pthread_mutex_lock'), (3,4,'open'),
    (3,3,'ipcs_open_reg_base'), (3,3,'ipcs_open_irq'), (3,3,'ipcs_init'),
    (0,0,'_ZN17CLiveviewCallback15processCallbackEiP9CCapImagePvj'),
    (0,0,'_ZN15CCaptureMessage6HandleEv'),
    (0,0,'_ZN9CLiveview25changeStopCallbackProcessEi'),
]
SESSION_BINDINGS = [
    ('get_lifecycle',0,'_ZN18CDDWrapperIPCS_B2Y12GetSemaphoreEv'),
    ('thread_create',5,'pthread_create'), ('attr_init',5,'pthread_attr_init'),
    ('attr_stack',5,'pthread_attr_setstacksize'), ('attr_destroy',5,'pthread_attr_destroy'),
    ('wait_syscall',4,'syscall'),
    ('capture_queue',0,'_ZN15CCaptureMessage9m_pcMyPOBE'),
    ('queue_send',4,'msgsnd'),
    ('liveview_instance',0,'_ZN9CLiveview11GetInstanceEv'),
    ('liveview_changed',0,'_ZN9CLiveview24checkDataChangeToRestartEi'),
    ('liveview_change_state',0,'_ZN9CLiveview11changeStateENS_15eLIVEVIEW_STATEE'),
    ('liveview_memory_restart',0,'_ZN9CLiveview30changeMemoryAreaRestartProcessEi'),
]


def arm_bl_target(address, word):
    # Stock call sites are unconditional ARM BL, not Thumb or BLX.
    if word & 0xff000000 != 0xeb000000:
        return None
    displacement = ((word & 0xffffff) ^ 0x800000) - 0x800000
    return (address + 8 + displacement * 4) & 0xffffffff


def build(session=False, output=None):
    OUT = Path(output) if output else ROOT/'outputs/deployment'/('smartfilter-session' if session else 'smartfilter-preview')
    hooks = [(0,6 if i==1 else 0,name) for i,name in enumerate(HOOKS)] + (SESSION_HOOKS if session else [])
    bindings = BINDINGS + (SESSION_BINDINGS if session else [])
    abi, max_bytes = (7,262144) if session else (4,131072)
    mode_lr = MODE_LR + ([0x287864,0x288f34] if session else [])
    OUT.mkdir(parents=True,exist_ok=True)
    rootfs = Path(json.loads((ROOT/'project.local.json').read_text(encoding='utf-8-sig'))['rootfs'])
    elfs, records = [], []
    for name in LIBS:
        elf,_,record = elf_record(rootfs/name,{})
        elfs.append(elf); records.append(dict(name=Path(name).name,**record))
    _,_,app = elf_record(rootfs/'usr/bin/di-camera-app-nx300',{})
    dyns = [elf.get_section_by_name('.dynsym') for elf in elfs]
    definitions = [{s.name:s for s in dyn.iter_symbols() if s['st_shndx']!='SHN_UNDEF'} for dyn in dyns]
    relocations = [[(r,dyns[image].get_symbol(r['r_info_sym']).name)
                    for sec in elfs[image].iter_sections() if sec['sh_type']=='SHT_REL'
                    for r in sec.iter_relocations() if r['r_info_type']==22] for image in range(len(elfs))]
    def symbol(image,name):
        if name not in definitions[image]: raise ValueError(f'Undefined native binding {LIBS[image]}:{name}')
        return definitions[image][name]
    header = ['/* Generated from the original NX300M 1.15 ELF. */',
              f'#define STOCK_APP_SIZE {app["bytes"]}U',f'#define STOCK_APP_SHA "{app["sha256"]}"',
              f'#define STOCK_IMAGES {len(records)}U',
              'static const struct stock_image {const char *name; unsigned size; const char *sha;} stock_images[]={']
    header += [f'{{"{v["name"]}",{v["bytes"]}U,"{v["sha256"]}"}},' for v in records]
    header += ['};','static const struct stock_slot {unsigned slot,image,address,resolver,patch_image;} stock_slots[]={']
    hook_records = []
    for i,(patch_image,owner,name) in enumerate(hooks):
        sym = symbol(owner,name)
        matches = [r for r,relname in relocations[patch_image] if relname==name]
        if len(matches)!=1: raise ValueError(f'Expected one native PLT slot for {name}, found {len(matches)}')
        slot=int(matches[0]['r_offset']); address=int(sym['st_value'])
        if i in S2_SLOTS and slot!=S2_SLOTS[i]:
            raise ValueError(f'Unexpected S2 GOT slot for {name}: {slot:#x}')
        resolver=struct.unpack('<I',bytes_at(elfs[patch_image],slot,4))[0]
        if resolver!=int(elfs[patch_image].get_section_by_name('.plt')['sh_addr']):
            raise ValueError('Unexpected ARM PLT resolver')
        header += [f'{{{slot}U,{owner}U,{address}U,{resolver}U,{patch_image}U}},']
        hook_records.append(dict(symbol=name,slot=hex(slot),owner=records[owner]['name'],patch_image=patch_image,address=hex(address)))
    header += ['};','static const struct stock_binding {unsigned offset,image,address;} stock_bindings[]={']
    checks = {}
    for field,image,name in bindings:
        sym=symbol(image,name); address=int(sym['st_value'])
        header += [f'{{offsetof(struct nx_sfr_config,{field}),{image}U,{address}U}},']
        if sym['st_info']['type']=='STT_FUNC': checks[image,address]=min(int(sym['st_size']),32)
    if session:
        # Read-only address, proved by inspect_ipcs_lifecycle.py. Never modified.
        header += ['{offsetof(struct nx_sfr_config,udd_count),3U,0x126744U},']
        header += ['{offsetof(struct nx_sfr_config,udd_mutex_outer),3U,0x1012ecU},',
                   '{offsetof(struct nx_sfr_config,udd_mutex_device),3U,0x12714cU},']
    for _,image,name in hooks:
        sym=symbol(image,name)
        checks[image,int(sym['st_value'])]=min(int(sym['st_size']),32)
    header += ['};']
    slot_alternatives = []
    if session:
        # glibc 2.13 may resolve open through libpthread's cancellation wrapper.
        # Admit its verified implementation without changing the resolved target.
        sym = symbol(5,'open'); address = int(sym['st_value'])
        checks[5,address] = min(int(sym['st_size']),32)
        header += ['static const struct stock_slot_alternative {unsigned slot,image,address;} stock_slot_alternatives[]={',
                   f'{{NX_SFR_DEVICE_OPEN,5U,{address}U}},','};']
        slot_alternatives.append(dict(slot=13,symbol='open',owner=records[5]['name'],address=hex(address)))
    # Keep ELF-relative return addresses separate: the loader adds Capture bias.
    # Fingerprint the original BL instructions, not only the callee prologues.
    for lr in mode_lr:
        address=lr-4
        word=struct.unpack('<I',bytes_at(elfs[0],address,4))[0]
        if arm_bl_target(address,word)!=MODE_PLT:
            raise ValueError(f'Expected mode BL to {MODE_PLT:#x} at {address:#x}')
        checks[0,address]=4
    prepare=symbol(0,'_ZN24CLiveviewNodeSmartFilter7PrepareEv')
    address=int(prepare['st_value']); size=int(prepare['st_size'])
    if address%4 or not size or size%4:
        raise ValueError('Unexpected ARM SmartFilter Prepare function bounds')
    code=bytes_at(elfs[0],address,size)
    work_lr=[address+i+4 for i in range(0,len(code),4)
             if arm_bl_target(address+i,struct.unpack_from('<I',code,i)[0])==WORK_PLT]
    if len(work_lr)!=1:
        raise ValueError(f'Expected one Prepare BL to {WORK_PLT:#x}, found {len(work_lr)}')
    checks[0,work_lr[0]-4]=4
    execute_work_lr=0x27cb3c
    word=struct.unpack('<I',bytes_at(elfs[0],execute_work_lr-4,4))[0]
    if arm_bl_target(execute_work_lr-4,word)!=WORK_PLT:
        raise ValueError('Unexpected Execute work-address retry call')
    checks[0,execute_work_lr-4]=4
    header += ['static const unsigned stock_mode_lr[]={'+','.join(f'{lr}U' for lr in mode_lr)+'};',
               f'static const unsigned stock_work_lr={work_lr[0]}U;',
               f'static const unsigned stock_work_execute_lr={execute_work_lr}U;']
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
             'smartfilter_lut_bridge.h','smartfilter_ipcs.c','smartfilter_ipcs.h',
             'smartfilter_timing.c','smartfilter_timing.h']
    if session:
        sources += ['smartfilter_session.inc','ipcs_session.c','ipcs_session.h','ipcs_calltrace.c','ipcs_calltrace.h']
    for name in sources: digest.update((ROOT/'runtime'/name).read_bytes())
    digest.update('\n'.join(header).encode())
    build_id=int.from_bytes(digest.digest()[:4],'little') or 1
    header += [f'#define NX_SFR_BUILD {build_id}U']
    (OUT/'smartfilter_stock.h').write_text('\n'.join(header)+'\n',encoding='ascii',newline='\n')
    elf_path=OUT/'smartfilter_ram.elf'
    linker=ROOT/'runtime/smartfilter_ram.ld'
    if session:
        script=linker.read_text().replace('LONG(4)', 'LONG(7)',1).replace(
            'LONG(nx_sfr_config)',
            'LONG(nx_sfr_b2y_open) LONG(nx_sfr_b2y_close) LONG(nx_sfr_stop) LONG(nx_sfr_start)\n'
            '    LONG(nx_sfr_udd_open) LONG(nx_sfr_mutex) LONG(nx_sfr_device_open)\n'
            '    LONG(nx_sfr_regmap) LONG(nx_sfr_irq) LONG(nx_sfr_init)\n'
            '    LONG(nx_sfr_frame_callback) LONG(nx_sfr_message_handle) LONG(nx_sfr_change_stop_callback)\n    LONG(nx_sfr_config)',1)
        script=script.replace('0x20000','0x40000').replace('128 KiB','256 KiB experimental')
        linker=OUT/'smartfilter_session.ld'; linker.write_text(script,encoding='ascii')
    defines=['-DNX_SFR_SESSION=1'] if session else []
    result=subprocess.run([shutil.which('clang'),'--target=arm-linux-gnueabi','-march=armv7-a','-marm',
        '-mfloat-abi=soft','-std=gnu99','-O2','-Wall','-Wextra','-Werror','-ffreestanding','-fpie',
        '-fvisibility=hidden','-fno-builtin','-fno-stack-protector','-fno-unwind-tables',
        '-fno-asynchronous-unwind-tables','-nostdlib','-static','-fuse-ld=lld',
        '-Wl,--no-undefined','-Wl,--build-id=none','-Wl,--emit-relocs',f'-Wl,--defsym=nx_sfr_build={build_id}',
        *defines,'-Wl,-T,'+str(linker),
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
    words=struct.unpack('<'+str(11+len(hooks))+'I',elf.get_section_by_name('.header').data())
    if words[0]!=0x5246534e or words[1]!=abi or words[-1]!=build_id:
        raise ValueError('RAM header magic/ABI/build differs')
    symbols={s.name:s for s in elf.get_section_by_name('.symtab').iter_symbols()}
    if words[-4]!=symbols['nx_sfr_config']['st_size'] or words[-2]!=symbols['nx_sfr_runtime']['st_size']:
        raise ValueError('RAM config/runtime size differs')
    if words[2]>max_bytes:
        raise ValueError('RAM blob exceeds its selected ABI size limit')
    binary=bytearray(words[2])
    for sec in elf.iter_sections():
        if sec['sh_flags']&2 and sec['sh_type']!='SHT_NOBITS':
            start=int(sec['sh_addr']); binary[start:start+sec['sh_size']]=sec.data()
    (OUT/'smartfilter_ram.bin').write_bytes(binary)
    control_sources = sorted([ROOT/'runtime/smartfilter_loader.c',
        ROOT/'mod/deployment/mfw/smartfilter-probe-launcher.c', *list((ROOT/'runtime').glob('*.h'))])
    control_hashes_before = {path.relative_to(ROOT).as_posix():
        hashlib.sha256(path.read_bytes()).hexdigest() for path in control_sources}
    script=f'''set -eu
tc=/opt/nx300m-toolchain/usr
rf=/opt/nx300m-lab/stock-1.15
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=gnu99 -O2 -Wall -Wextra -Werror -Wno-unused-function -march=armv7-a -mfloat-abi=soft \\
 -D_TIME_BITS=32 -D_FILE_OFFSET_BITS=64 {' '.join(defines)} -fno-stack-protector -fno-pic -no-pie \\
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 \\
 -Wl,-z,defs -Wl,--strip-all -Wl,-rpath-link,"$rf/lib" -I{shlex.quote(wsl(OUT))} \\
 {shlex.quote(wsl(ROOT/'runtime/smartfilter_loader.c'))} \\
 {shlex.quote(wsl(ROOT/'work/reference/metol/test_server/filmlab/src/crt0_arm.S'))} \\
 "$rf/lib/libc-2.13.so" -lgcc -o {shlex.quote(wsl(OUT/'smartfilter_loader.arm'))}
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=gnu99 -Os -Wall -Wextra -Werror -march=armv7-a -mfloat-abi=soft \\
 -D_TIME_BITS=32 -D_FILE_OFFSET_BITS=64 -fno-stack-protector -fno-pic -no-pie \\
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 -Wl,--strip-all \\
 -Wl,-z,defs -Wl,-rpath-link,"$rf/lib" -I{shlex.quote(wsl(ROOT/'runtime'))} \\
 {shlex.quote(wsl(ROOT/'mod/deployment/mfw/smartfilter-probe-launcher.c'))} \\
 {shlex.quote(wsl(ROOT/'work/reference/metol/test_server/filmlab/src/crt0_arm.S'))} \\
 "$rf/lib/libc-2.13.so" -lgcc -o {shlex.quote(wsl(OUT/'smartfilter_probe_launcher.arm'))}
'''
    result=subprocess.run(['wsl.exe','-d','Debian','-u','root','-e','sh','-s'],input=script.encode(),capture_output=True)
    (OUT/'loader-build.stderr.log').write_bytes(result.stderr)
    if result.returncode: raise RuntimeError(f'Loader build exit {result.returncode}: {result.stderr.decode(errors="replace")}')
    versions_by_artifact={}
    for artifact in ('smartfilter_loader.arm','smartfilter_probe_launcher.arm'):
        built=ELFFile(io.BytesIO((OUT/artifact).read_bytes()))
        versions=built.get_section_by_name('.gnu.version_r')
        required=[aux.name for _,items in versions.iter_versions() for aux in items] if versions else []
        if any(v.startswith('GLIBC_') and tuple(map(int,v[6:].split('.')))>(2,13) for v in required):
            raise ValueError('New glibc dependency in '+artifact)
        versions_by_artifact[artifact]=required
    required=versions_by_artifact['smartfilter_loader.arm']
    report=dict(build_exit_code=0,build=build_id,abi=words[1],hook_count=len(hooks),
        s1_hook_count=S1_HOOKS,s2_hook_count=len(hooks),session_experiment=session,
        blob_bytes=len(binary),config_bytes=words[-4],runtime_bytes=words[-2],
        stock=records,hooks=hook_records,slot_alternatives=slot_alternatives,
        mode_lr=[hex(lr) for lr in mode_lr],work_lr=hex(work_lr[0]),
        required_versions=required,launcher_required_versions=versions_by_artifact['smartfilter_probe_launcher.arm'],
        launcher_sha256=hashlib.sha256((OUT/'smartfilter_probe_launcher.arm').read_bytes()).hexdigest(),
        host_liveview_mode=1,host_still_mode=2,
        automatic_installation=False,global_userdata_changed=False,jpeg_lut_hook_changed=False,absolute_relocations_rejected=True,
        vm_started=False,physical_pixels_verified=False,hardware_isp_pixels_verified=False,raw_menu_restrictions_resolved=False)
    control_sources = sorted([ROOT/'runtime/smartfilter_loader.c',
        ROOT/'mod/deployment/mfw/smartfilter-probe-launcher.c', *list((ROOT/'runtime').glob('*.h'))])
    report['control_sources'] = {path.relative_to(ROOT).as_posix():
        hashlib.sha256(path.read_bytes()).hexdigest() for path in control_sources}
    if report['control_sources'] != control_hashes_before:
        raise ValueError('Control sources changed during ARM compilation')
    report['control_artifacts'] = {name: hashlib.sha256((OUT/name).read_bytes()).hexdigest()
        for name in ('smartfilter_loader.arm', 'smartfilter_probe_launcher.arm')}
    (OUT/'ram-build-report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session',action='store_true',help='Build independent ABI7 Session preview; preserve ABI4 artifacts')
    args=parser.parse_args()
    print(json.dumps(build(session=args.session),indent=2))
