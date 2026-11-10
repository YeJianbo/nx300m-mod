"""Build the combined runtime-settings blob and ptrace loader (2026-11-08).

Stock evidence comes from project.local.json rootfs; addresses verified against
the reversed contracts (UI_Set_Power_Timer=0x1d0f88, liveview key table slot
0x2615c0 -> 0x17c748, Ecore select slot 0x21bb4). The blob is freestanding
clang output; the loader is the ptrace installer with fixture mode for the
isolated ARM VM. No firmware writes, no exposure code.
"""
import io
import json
from pathlib import Path
import shlex
import shutil
import struct
import subprocess
from elftools.elf.elffile import ELFFile

from build_lut_live_loader import elf_record, bytes_at, wsl

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/controls/settings-runtime'
SPEC={
    'POWER_TIMER': '_Z18UI_Set_Power_Timerb',
}
PT_ADDR=0x1d0f88
KEY_SLOT=0x2615c0
KEY_ORIG=0x17c748
KEY_TABLE=0x261590
SELECT_SLOT_OFFSET=0x21bb4
ECORE_LIB='usr/lib/libecore.so.1.2.0'

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    stock=Path(json.loads((ROOT/'project.local.json').read_text())['rootfs'])
    app,_,record=elf_record(stock/'usr/bin/di-camera-app-nx300',SPEC)
    if app['e_type']!='ET_EXEC': raise ValueError('Expected fixed stock executable')
    if record['POWER_TIMER']!=PT_ADDR: raise ValueError('Stock version differs: UI_Set_Power_Timer')
    header=[f'#define NX_PT_FUNC 0x{PT_ADDR:x}U',
            f'#define NX_KEY_ENTRY 0x{KEY_SLOT:x}U',
            f'#define NX_KEY_ORIG 0x{KEY_ORIG:x}U',
            f'#define NX_KEY_TABLE 0x{KEY_TABLE:x}U',
            f'#define NX_SELECT_SLOT_OFF 0x{SELECT_SLOT_OFFSET:x}U',
            f'#define STOCK_APP_SIZE {record["bytes"]}U',
            f'#define STOCK_APP_SHA "{record["sha256"]}"']
    checks=[(PT_ADDR,'power_timer'),(KEY_SLOT,'key_slot'),(0x17c748,'custom_handler')]
    checks_bytes=[]
    for addr,tag in checks:
        size=64 if addr!=KEY_SLOT else 8
        raw=bytes_at(app,addr,size)
        checks_bytes.append((addr,size,tag,raw))
    if struct.unpack_from('<I',bytes_at(app,KEY_SLOT,4))[0]!=KEY_ORIG:
        raise ValueError('Custom key slot does not hold the reversed original handler')
    for addr,n,tag,raw in checks_bytes:
        if addr==KEY_SLOT: continue
        header.append('static const unsigned char stock_'+tag+'[]={'+','.join(map(str,raw[:32]))+'};')
    header.append('struct settings_check {unsigned address,size;const unsigned char *bytes;};')
    header.append('static const struct settings_check settings_checks[]={'+
                  ','.join('{'+f'0x{a:x},{n},stock_{t}'+'}' for a,n,t,_ in checks_bytes if t!="key_slot")+'};')
    ecore,_,ecore_record=elf_record(stock/ECORE_LIB,
        {'select_set':'ecore_main_loop_select_func_set',
         'select_get':'ecore_main_loop_select_func_get'})
    ecore_bytes=bytes_at(ecore,SELECT_SLOT_OFFSET,4)
    if struct.unpack('<I',ecore_bytes)[0]!=0:
        raise ValueError('Ecore slot expected to be relocated at runtime, disk value non-zero')
    if ecore_record['select_set']!=0xdb30 or ecore_record['select_get']!=0xdb4c: raise ValueError('Ecore symbols moved')
    if ecore_record['select_set']!=0xdb30 or ecore_record['select_get']!=0xdb4c:
        raise ValueError('Ecore symbols moved')
    libc,_,libc_record=elf_record(stock/'lib/libc-2.13.so',{'syscall':'syscall'})
    sym=libc.get_section_by_name('.dynsym').get_symbol_by_name('syscall')[0]
    raw=bytes_at(libc,sym['st_value'],sym['st_size'])
    svc=next(sym['st_value']+i for i in range(0,len(raw),4) if raw[i:i+4]==bytes((0,0,0,0xef)))
    header += [f'#define STOCK_LIBC_SIZE {libc_record["bytes"]}U',
               f'#define STOCK_LIBC_SHA "{libc_record["sha256"]}"',
               f'#define STOCK_SVC {svc}U',
               'static const unsigned char stock_svc[32]={'+','.join(map(str,bytes_at(libc,svc,32)))+'};',
               f'#define ECORE_SIZE {ecore_record["bytes"]}U',
               f'#define ECORE_SHA "{ecore_record["sha256"]}"']
    (OUT/'settings_runtime_stock.h').write_text('\n'.join(header)+'\n',encoding='ascii')
    elf_path=OUT/'settings_runtime_blob.elf'
    clang=shutil.which('clang') or 'D:/Program Files/LLVM/bin/clang.exe'
    subprocess.run([clang,'--target=arm-linux-gnueabi','-march=armv7-a','-marm','-mfloat-abi=soft',
        '-std=c99','-O2','-Wall','-Wextra','-Werror','-ffreestanding',
        '-fpie','-fvisibility=hidden','-fno-builtin','-fno-stack-protector','-fno-unwind-tables',
        '-fno-asynchronous-unwind-tables','-nostdlib','-static','-fuse-ld=lld','-Wl,--no-undefined',
        '-Wl,--build-id=none','-I'+str(OUT),'-Wl,-T,'+str(ROOT/'runtime/settings_runtime_blob.ld'),
        str(ROOT/'runtime/settings_runtime_blob.c'),'-o',str(elf_path)],check=True)
    elf=ELFFile(io.BytesIO(elf_path.read_bytes()))
    if any(s['sh_type'] in ('SHT_REL','SHT_RELA','SHT_DYNAMIC') for s in elf.iter_sections()): raise ValueError('Runtime relocations')
    syms={s.name:s for s in elf.get_section_by_name('.symtab').iter_symbols()}
    blob=bytearray(syms['__blob_end']['st_value'])
    for sec in elf.iter_sections():
        if sec['sh_flags']&2 and sec['sh_type']!='SHT_NOBITS':
            start=sec['sh_addr'];blob[start:start+sec['sh_size']]=sec.data()
    values={k:int(syms[k]['st_value']) for k in ('nx_ecore_select_wrapper','nx_settings_custom_handler','nx_settings_config','__data_start')}
    embedded=['static const unsigned char settings_blob[]={'+','.join(map(str,blob))+'};']
    for key,value in values.items(): embedded.append(f'#define BLOB_{key.upper()} {value}U')
    (OUT/'settings_runtime_blob.h').write_text('\n'.join(embedded)+'\n',encoding='ascii')
    (OUT/'settings_runtime_blob.bin').write_bytes(blob)
    target=OUT/'settings_runtime.arm'
    script=f'''set -eu
tc=/opt/nx300m-toolchain/usr
rf=/opt/nx300m-lab/stock-1.15
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=gnu99 -O2 -Wall -Wextra -Werror -Wno-unused-function -march=armv7-a -mfloat-abi=soft \\
 -D_TIME_BITS=32 -D_FILE_OFFSET_BITS=64 -fno-stack-protector -fno-pic -no-pie \\
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 \\
 -Wl,-z,defs -Wl,-rpath-link,"$rf/lib" -I{shlex.quote(wsl(OUT))} \\
 {shlex.quote(wsl(ROOT/'runtime/settings_runtime.c'))} \\
 {shlex.quote(wsl(ROOT/'work/reference/metol/test_server/filmlab/src/crt0_arm.S'))} \\
 "$rf/lib/libc-2.13.so" -lgcc -o {shlex.quote(wsl(target))}
'''
    result=subprocess.run(['wsl.exe','-d','Debian','-u','root','-e','sh','-s'],input=script.encode(),capture_output=True)
    (OUT/'build.log').write_bytes(result.stdout+result.stderr)
    if result.returncode: raise RuntimeError(f'ARM build exit {result.returncode}: '+result.stderr.decode(errors='replace'))
    report=dict(build_exit_code=0,artifact=str(target),stock=record,ecore=ecore_record,
                offsets=values,blob_bytes=len(blob),select_slot_offset=SELECT_SLOT_OFFSET,
                key_slot=KEY_SLOT,pt_func=PT_ADDR,
                no_firmware_writes=True,no_exposure_code=True,physical_camera_verified=False,
                runtime_hook_validated=False)
    (OUT/'build-report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
