"""Build an independent, RAM-only mode-animation helper from stock 1.15 evidence."""
import io
import json
from pathlib import Path
import shutil
import struct
import subprocess
import shlex
from elftools.elf.elffile import ELFFile
from build_lut_live_loader import elf_record, bytes_at, wsl

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/controls/settings-animation'
SPECS={
    'DRAW': '_ZN15CAPPGUIModeView12DrawModeDialEv',
    'START': '_ZN15CAPPGUIModeView15ani_timer_startEd',
    'STOP': '_ZN15CAPPGUIModeView14ani_timer_stopEv',
    'CLEAR': '_ZN15CAPPGUIModeView13ClearModeDialEv',
    'UPDATE': '_ZN15CAPPGUIModeView23update_center_mode_iconEv',
    'FRAME': '_ZN15CAPPGUIModeView17draw_modedial_aniEv',
    'GEOMETRY': 'osd_get_geometry', 'MOVE':'osd_move_item_direct',
}

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    stock=Path(json.loads((ROOT/'project.local.json').read_text())['rootfs'])
    app,_,record=elf_record(stock/'usr/bin/di-camera-app-nx300',SPECS)
    if app['e_type']!='ET_EXEC': raise ValueError('Expected fixed stock executable')
    header=[];checks=[]
    for key,name in SPECS.items():
        sym=app.get_section_by_name('.dynsym').get_symbol_by_name(name)[0]
        address,size=int(sym['st_value']),int(sym['st_size'])
        if not size: raise ValueError('No stock function body: '+name)
        raw=bytes_at(app,address,size)
        header.append(f'#define NX_{key} 0x{address:x}U')
        header.append(f'static const unsigned char stock_{key.lower()}[]={{'+','.join(map(str,raw))+'};')
        checks.append((address,size,key.lower()))
        if key in ('DRAW','START'):
            prologues={
                (0xe92d4800,0xe28db004):'push {fp, lr}\\nadd fp, sp, #4',
                (0xe92d4810,0xe28db008):'push {r4, fp, lr}\\nadd fp, sp, #8',
                (0xe92d4830,0xe28db00c):'push {r4, r5, fp, lr}\\nadd fp, sp, #12',
            }
            prologue=prologues.get(struct.unpack_from('<II',raw))
            if prologue is None: raise ValueError('Unreviewed prologue: '+name)
            header += [f'#define NX_{key}_PROLOGUE "{prologue}"',f'#define NX_{key}_RESUME "0x{address+8:x}"']
    if record['DRAW']!=0x9d36c or record['START']!=0x9e328: raise ValueError('Stock version differs')
    libc,_,libc_record=elf_record(stock/'lib/libc-2.13.so',{'syscall':'syscall'})
    sym=libc.get_section_by_name('.dynsym').get_symbol_by_name('syscall')[0]
    raw=bytes_at(libc,sym['st_value'],sym['st_size'])
    svc=next(sym['st_value']+i for i in range(0,len(raw),4) if raw[i:i+4]==b'\0\0\0\xef')
    header += [f'#define STOCK_APP_SIZE {record["bytes"]}U',f'#define STOCK_APP_SHA "{record["sha256"]}"',
               f'#define STOCK_LIBC_SIZE {libc_record["bytes"]}U',f'#define STOCK_LIBC_SHA "{libc_record["sha256"]}"',
               f'#define STOCK_SVC {svc}U', 'static const unsigned char stock_svc[32]={'+','.join(map(str,bytes_at(libc,svc,32)))+'};',
               'struct animation_check {unsigned address,size;const unsigned char *bytes;};',
               'static const struct animation_check animation_checks[]={'+','.join('{'+f'{a},{n},stock_{k}'+'}' for a,n,k in checks)+'};']
    (OUT/'settings_animation_stock.h').write_text('\n'.join(header)+'\n',encoding='ascii')
    elf_path=OUT/'settings_animation_blob.elf'
    clang=shutil.which('clang') or 'D:/Program Files/LLVM/bin/clang.exe'
    subprocess.run([clang,'--target=arm-linux-gnueabi','-march=armv7-a','-marm','-mfloat-abi=soft',
        '-std=c99','-O2','-Wall','-Wextra','-Werror','-Wno-unused-const-variable','-ffreestanding',
        '-fpie','-fvisibility=hidden','-fno-builtin','-fno-stack-protector','-fno-unwind-tables',
        '-fno-asynchronous-unwind-tables','-nostdlib','-static','-fuse-ld=lld','-Wl,--no-undefined',
        '-Wl,--build-id=none','-I'+str(OUT),'-Wl,-T,'+str(ROOT/'runtime/settings_animation_blob.ld'),
        str(ROOT/'runtime/settings_animation_blob.c'),'-o',str(elf_path)],check=True)
    elf=ELFFile(io.BytesIO(elf_path.read_bytes()))
    if any(s['sh_type'] in ('SHT_REL','SHT_RELA','SHT_DYNAMIC') for s in elf.iter_sections()): raise ValueError('Runtime relocations')
    syms={s.name:s for s in elf.get_section_by_name('.symtab').iter_symbols()}
    blob=bytearray(syms['__blob_end']['st_value'])
    for sec in elf.iter_sections():
        if sec['sh_flags']&2 and sec['sh_type']!='SHT_NOBITS':
            start=sec['sh_addr'];blob[start:start+sec['sh_size']]=sec.data()
    values={k:int(syms[k]['st_value']) for k in ('nx_animation_draw','nx_animation_start','nx_animation_config','__data_start')}
    embedded=['static const unsigned char animation_blob[]={'+','.join(map(str,blob))+'};']
    for key,value in values.items(): embedded.append(f'#define BLOB_{key.upper()} {value}U')
    (OUT/'settings_animation_blob.h').write_text('\n'.join(embedded)+'\n',encoding='ascii')
    (OUT/'settings_animation_blob.bin').write_bytes(blob)
    target=OUT/'settings_animation.arm'
    script=f'''set -eu
tc=/opt/nx300m-toolchain/usr
rf=/opt/nx300m-lab/stock-1.15
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=gnu99 -O2 -Wall -Wextra -Werror -Wno-unused-function -march=armv7-a -mfloat-abi=soft \\
 -D_TIME_BITS=32 -D_FILE_OFFSET_BITS=64 -fno-stack-protector -fno-pic -no-pie \\
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 \\
 -Wl,-z,defs -Wl,-rpath-link,"$rf/lib" -I{shlex.quote(wsl(OUT))} \\
 {shlex.quote(wsl(ROOT/'runtime/settings_animation.c'))} \\
 {shlex.quote(wsl(ROOT/'work/reference/metol/test_server/filmlab/src/crt0_arm.S'))} \\
 "$rf/lib/libc-2.13.so" -lgcc -o {shlex.quote(wsl(target))}
'''
    result=subprocess.run(['wsl.exe','-d','Debian','-u','root','-e','sh','-s'],input=script.encode(),capture_output=True)
    (OUT/'build.log').write_bytes(result.stdout+result.stderr)
    if result.returncode: raise RuntimeError(f'ARM build exit {result.returncode}: '+result.stderr.decode(errors='replace'))
    report=dict(build_exit_code=0,artifact=str(target),stock=record,offsets=values,blob_bytes=len(blob),
                no_firmware_writes=True,no_exposure_code=True,physical_camera_verified=False)
    (OUT/'build-report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
