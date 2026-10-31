"""Build only the stock-gallery Quick Share hook, native panel and task service.

Does not restart the simulator, regenerate web/colour assets or install firmware.
"""
import io
import argparse
import json
from pathlib import Path
import shlex
import struct
import subprocess
from elftools.elf.elffile import ELFFile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/quickshare/gallery'

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    target=parser.add_mutually_exclusive_group()
    target.add_argument('--package-only',action='store_true',help='Package current native builds into an isolated SD bundle and persistent rootfs candidate')
    target.add_argument('--hook-only',action='store_true',help='Rebuild only the original gallery hook')
    target.add_argument('--panel-only',action='store_true',help='Rebuild only the native Quick Share panel')
    target.add_argument('--remote-only',action='store_true',help='Rebuild only the gallery-specific native remote service')
    args=parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if args.package_only:
        import build_sd_bundle as sd
        import deployment_boot_chain as firmware
        sd.OUTPUT=OUT/'sd-bundle'
        firmware.OUT=OUT/'firmware'
        bundle=sd.build(remote=OUT/'nx-remote-controller.arm')
        candidate=firmware.persistent_candidate()
        print(json.dumps({'sd_bundle':bundle['bundle'],'firmware_candidate':str(firmware.OUT/'persistent-candidate'),
                          'hooks':len(candidate['hooks']),'installed':False},ensure_ascii=False))
        return
    stock = ROOT / 'outputs/deployment/stock/usr/bin/di-camera-app-nx300'
    elf = ELFFile(io.BytesIO(stock.read_bytes()))
    symbol = elf.get_section_by_name('.dynsym').get_symbol_by_name('_Z18UI_PB_Share_Singlev')[0]
    address, size = symbol['st_value'], symbol['st_size']
    if (address, size) != (0x1e9ef8, 500):
        raise ValueError('Not the inspected NX300M 1.15 share entry')
    segment = next(s for s in elf.iter_segments() if s['p_type']=='PT_LOAD' and s['p_vaddr']<=address<s['p_vaddr']+s['p_filesz'])
    offset=address-segment['p_vaddr']
    code=segment.data()[offset:offset+size]
    if struct.unpack_from('<II',code) != (0xe92d4800,0xe28db004):
        raise ValueError('Cannot relocate share entry prologue')
    (OUT/'gallery_share_stock.h').write_text(
        '#define NX_SHARE_ADDRESS 0x1e9ef8u\nstatic const unsigned char nx_share_stock[]={\n'+
        ','.join(f'0x{b:02x}' for b in code)+'\n};\n',encoding='ascii')
    upload=elf.get_section_by_name('.dynsym').get_symbol_by_name('_Z25UI_PB_Single_upload_Filesv')[0]
    if (upload['st_value'],upload['st_size']) != (0x1e73c4,500):
        raise ValueError('Not the inspected NX300M 1.15 direct-touch share entry')
    segment=next(s for s in elf.iter_segments() if s['p_type']=='PT_LOAD' and s['p_vaddr']<=upload['st_value']<s['p_vaddr']+s['p_filesz'])
    offset=upload['st_value']-segment['p_vaddr'];upload_code=segment.data()[offset:offset+upload['st_size']]
    if struct.unpack_from('<II',upload_code)!=(0xe92d4800,0xe28db004):
        raise ValueError('Cannot relocate direct-touch share entry prologue')
    with (OUT/'gallery_share_stock.h').open('a',encoding='ascii') as stream:
        stream.write('#define NX_UPLOAD_ADDRESS 0x1e73c4u\nstatic const unsigned char nx_upload_stock[]={\n'+
                     ','.join(f'0x{b:02x}' for b in upload_code)+'\n};\n')
    depth2=elf.get_section_by_name('.dynsym').get_symbol_by_name('_ZN22CAPPGUIMenuDepth2State7onEnterEv')[0]
    multi=elf.get_section_by_name('.dynsym').get_symbol_by_name('_Z25UI_Thumbnail_Upload_Filesv')[0]
    if (multi['st_value'],multi['st_size']) != (0x1e2a70,572):
        raise ValueError('Not the inspected NX300M 1.15 multi-selection confirmation')
    segment=next(s for s in elf.iter_segments() if s['p_type']=='PT_LOAD' and s['p_vaddr']<=multi['st_value']<s['p_vaddr']+s['p_filesz'])
    offset=multi['st_value']-segment['p_vaddr'];multi_code=segment.data()[offset:offset+multi['st_size']]
    if struct.unpack_from('<II',multi_code)!=(0xe92d4800,0xe28db004):
        raise ValueError('Cannot relocate multi-selection confirmation prologue')
    with (OUT/'gallery_share_stock.h').open('a',encoding='ascii') as stream:
        stream.write('#define NX_MULTI_ADDRESS 0x1e2a70u\nstatic const unsigned char nx_multi_stock[]={\n'+
                     ','.join(f'0x{b:02x}' for b in multi_code)+'\n};\n')
    menu_address,menu_size=depth2['st_value'],depth2['st_size']
    if (menu_address,menu_size)!=(0x11a52c,528): raise ValueError('Not the inspected playback submenu entry')
    segment=next(s for s in elf.iter_segments() if s['p_type']=='PT_LOAD' and s['p_vaddr']<=menu_address<s['p_vaddr']+s['p_filesz'])
    offset=menu_address-segment['p_vaddr'];menu_code=segment.data()[offset:offset+menu_size]
    if struct.unpack_from('<II',menu_code)!=(0xe92d4810,0xe28db008):
        raise ValueError('Cannot relocate the playback submenu entry')
    with (OUT/'gallery_share_stock.h').open('a',encoding='ascii') as stream:
        stream.write('#define NX_DEPTH2_ADDRESS 0x11a52cu\nstatic const unsigned char nx_depth2_stock[]={\n'+
                     ','.join(f'0x{b:02x}' for b in menu_code)+'\n};\n')
    project='/mnt/'+ROOT.drive[0].lower()+ROOT.as_posix()[2:]
    script=f'''set -eu
cd {shlex.quote(project)}
tc=/opt/nx300m-toolchain/usr
rf=/opt/nx300m-lab/stock-1.15
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
cc="$tc/bin/arm-linux-gnueabi-gcc"
base="-B$tc/lib/gcc-cross/arm-linux-gnueabi/14/ -std=gnu99 -O2 -Wall -Wextra -Wno-unused-function -Wno-unused-parameter -Wno-misleading-indentation -march=armv7-a -mfloat-abi=soft -fno-stack-protector -D_GNU_SOURCE -D_TIME_BITS=32 -D_FILE_OFFSET_BITS=64 -include mod/metol/remote/compat.h"
link="-nostdlib -Wl,--allow-shlib-undefined -Wl,-rpath-link,$rf/usr/lib -Wl,-rpath-link,$rf/lib"
exe="-fno-pic -no-pie -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3"
crt=work/reference/metol/test_server/filmlab/src/crt0_arm.S
mg=work/reference/nx-remote-controller/externals/mongoose
src=work/reference/nx-remote-controller/nx-remote-controller-daemon
ref=work/reference/metol/test_server/nxui
"$cc" $base $link -shared -fPIC -Ioutputs/quickshare/gallery -Wl,-soname,gallery-share.so \\
 runtime/gallery_share.c "$rf/lib/libc-2.13.so" "$rf/lib/libdl.so.2" -lgcc -o outputs/quickshare/gallery/gallery-share.so
"$cc" $base $link $exe -I"$tc/include" -I"$tc/include/freetype2" -I"$ref" -I"$mg" \\
 -DMG_DISABLE_HTTP_DIGEST_AUTH -DMG_DISABLE_MQTT -DMG_DISABLE_MD5 -DMG_DISABLE_JSON_RPC -DMG_DISABLE_SOCKETPAIR \\
 mod/metol/quickshare_panel.c mod/metol/remote/compat.c "$mg/mongoose.c" "$crt" \\
 "$rf/usr/lib/libX11.so.6" "$rf/usr/lib/libXft.so.2" "$rf/usr/lib/libXrender.so.1" \\
 "$rf/lib/libc-2.13.so" "$rf/lib/librt.so.1" "$rf/lib/libpthread.so.0" -lgcc -o outputs/quickshare/gallery/quickshare-panel.arm
"$cc" $base $link $exe -I"$tc/include" -I"$src" -I"$mg" -I"$ref" \\
 -DMG_DISABLE_HTTP_DIGEST_AUTH -DMG_DISABLE_MQTT -DMG_DISABLE_MD5 -DMG_DISABLE_JSON_RPC -DMG_DISABLE_SOCKETPAIR \\
 mod/metol/remote/remote.c mod/metol/remote/compat.c "$src/nx_model.c" "$src/status.c" "$src/util.c" "$src/command.c" "$mg/mongoose.c" \\
 "$ref/nxui_model.c" "$crt" "$rf/usr/lib/libX11.so.6" "$rf/usr/lib/libXtst.so.6" \\
 "$rf/lib/libpthread.so.0" "$rf/lib/librt.so.1" "$rf/lib/libc-2.13.so" -lgcc -o outputs/quickshare/gallery/nx-remote-controller.arm
'''
    if args.hook_only:
        script=script.split('"$cc" $base $link $exe',1)[0]
    elif args.panel_only:
        commands=script.split('"$cc" $base $link ')
        if len(commands)!=4: raise ValueError('Unexpected native build command layout')
        script=commands[0]+'"$cc" $base $link '+commands[2]
    elif args.remote_only:
        commands=script.split('"$cc" $base $link ')
        if len(commands)!=4: raise ValueError('Unexpected native build command layout')
        script=commands[0]+'"$cc" $base $link '+commands[3]
    result=subprocess.run(['wsl.exe','-u','root','-e','sh','-s'],input=script.encode(),capture_output=True)
    (OUT/'build.stdout.log').write_bytes(result.stdout)
    (OUT/'build.stderr.log').write_bytes(result.stderr)
    if result.returncode:
        raise RuntimeError(f'Build exit {result.returncode}: '+result.stderr.decode('utf-8','replace')[-6000:])
    records=[]
    for name in (('gallery-share.so',) if args.hook_only else ('quickshare-panel.arm',) if args.panel_only else ('nx-remote-controller.arm',) if args.remote_only else ('gallery-share.so','quickshare-panel.arm','nx-remote-controller.arm')):
        data=(OUT/name).read_bytes();binary=ELFFile(io.BytesIO(data))
        versions=binary.get_section_by_name('.gnu.version_r')
        required=[a.name for _,aux in versions.iter_versions() for a in aux] if versions else []
        if any(v.startswith('GLIBC_') and tuple(map(int,v[6:].split('.')))>(2,13) for v in required):
            raise ValueError(f'{name} requires a newer glibc: {required}')
        records.append({'name':name,'bytes':len(data),'machine':binary['e_machine'],'versions':required})
    report={'build_exit_code':0,'build_scope':'hook-only' if args.hook_only else 'panel-only' if args.panel_only else 'remote-only' if args.remote_only else 'gallery-panel-service','artifacts':records,'stock_entry':hex(address),'original_bytes_checked':size,
            'direct_touch_entry':hex(upload['st_value']),'direct_touch_bytes_checked':upload['st_size'],
            'menu_entry':hex(menu_address),'menu_bytes_checked':menu_size,
            'multi_entry':hex(multi['st_value']),'multi_bytes_checked':multi['st_size'],
            'physical_camera_verified':False,'main_simulator_restarted':False}
    (OUT/'build-report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
