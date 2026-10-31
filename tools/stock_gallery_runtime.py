"""Prepare native gallery assets inside an existing private stock namespace.

The host executable below only supplies explicit QEMU execution for original
BusyBox applets. It does not emulate a gallery callback or replace ARM panels.
"""
import argparse
import json
from pathlib import Path, PureWindowsPath
import shutil
import subprocess

PROJECT=Path(__file__).resolve().parents[1]
GUEST='/opt/nx300m-lab/stock-1.15'
MOD_ROOT='/mnt/mmc/nx300m-mod'

BUSYBOX_RUNNER=r'''
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
int main(int argc,char **argv) {
 const char *name=strrchr(argv[0],'/');name=name?name+1:argv[0];
 char **args=calloc((size_t)argc+10,sizeof(char*));if(!args)return 125;
 args[0]="/qemu-arm-static";args[1]="-cpu";args[2]="cortex-a9";
 args[3]="-L";args[4]="/";args[5]="-0";args[6]="busybox";args[7]="/tmp/stock-busybox.arm";
 int n=8;if(strcmp(name,"busybox"))args[n++]=(char*)name;
 for(int i=1;i<argc;i++)args[n++]=argv[i];
 setenv("NX300M_ARM_RUNNER","/qemu-arm-static",1);
 execv(args[0],args);return 126;
}
'''


def install_busybox_bridge(guest):
    guest=Path(guest)
    if str(guest)!=GUEST or not (guest/'.stock-source').is_file():
        raise ValueError('Expected the prepared private stock root')
    runtime=guest/'tmp'
    original=runtime/'stock-busybox.arm'
    marker=guest/'var/run/nx300m-busybox-bridge'
    if marker.is_file():
        return
    if not original.exists():
        shutil.copyfile(guest/'bin/busybox',original)
    source=runtime/'busybox-runner.c'
    binary=runtime/'busybox-runner'
    source.write_text(BUSYBOX_RUNNER,encoding='utf-8',newline='\n')
    subprocess.run(['gcc','-static',str(source),'-o',str(binary)],check=True,capture_output=True)
    original.chmod(0o755);binary.chmod(0o755)
    subprocess.run(['mount','--bind',str(binary),str(guest/'bin/busybox')],check=True,capture_output=True)
    marker.write_text('explicit-qemu-stock-busybox\n',encoding='ascii')


def linux_source(value):
    path=PureWindowsPath(value)
    if path.drive:
        return Path('/mnt')/path.drive[0].lower()/Path(*path.parts[1:])
    return Path(value)


def seed_card(guest, photo=None):
    """Copy only missing lab references; preserve every existing card file."""
    media=Path(guest)/'mnt/mmc/DCIM/100PHOTO'
    media.mkdir(parents=True,exist_ok=True)
    reference=Path(photo) if photo is not None else PROJECT/'outputs/looks/photo-reference.jpg'
    sources=[('SAM_4262.JPG',reference,'explicit gallery JPEG' if photo is not None else 'original SRW embedded JPEG')]
    if photo is not None and (not reference.is_file() or reference.suffix.lower() not in ('.jpg','.jpeg')):
        raise ValueError('An explicit existing JPEG is required')
    info=PROJECT/'outputs/looks/photo-reference.json'
    if photo is None and info.is_file():
        metadata=json.loads(info.read_text(encoding='utf-8'))
        original=linux_source(metadata.get('external_jpeg',{}).get('path',''))
        if original.is_file():sources[0]=('SAM_4262.JPG',original,'original camera JPEG with EXIF')
        raw=linux_source(metadata.get('raw',{}).get('path',''))
        if raw.is_file() and raw.suffix.lower()=='.srw':sources.append(('SAM_4262.SRW',raw,'original camera SRW'))
    report=[]
    for name,source,kind in sources:
        destination=media/name
        if not source.is_file():continue
        copied=False
        try:
            # Exclusive creation prevents an existing original or user copy
            # from being overwritten when this persistent card is reused.
            with source.open('rb') as incoming,destination.open('xb') as outgoing:
                shutil.copyfileobj(incoming,outgoing)
            copied=True
        except FileExistsError:
            pass
        report.append({'source':str(source),'destination':'/mnt/mmc/DCIM/100PHOTO/'+name,
                       'kind':kind,'copied':copied,'preserved_existing':not copied,'bytes':destination.stat().st_size})
    return report


def prepare_gallery(guest=GUEST, photo=None):
    guest=Path(guest)
    card=guest/'var/run/nx300m-sdcard.json'
    if str(guest)!=GUEST or not card.is_file():
        raise RuntimeError('Gallery development runtime requires the private lab SD mount')
    assets=((PROJECT/'mod/deployment/quickshare-panel.sh','quickshare-panel.sh'),
            (PROJECT/'mod/deployment/fonts.conf.in','fonts/fonts.conf.in'),
            (PROJECT/'outputs/quickshare/gallery/quickshare-panel.arm','bin/quickshare-panel.arm'),
            (guest/'usr/share/fonts/SDIC_GP_US_20120720.ttf','fonts/SDIC_GP_US_20120720.ttf'))
    for source,_ in assets:
        if not source.is_file():raise RuntimeError(f'Missing native gallery asset: {source}')
    install_busybox_bridge(guest)
    root=guest/MOD_ROOT.lstrip('/')
    for source,relative in assets:
        target=root/relative;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,target)
    report={'mod_root':MOD_ROOT,'media_root':'/mnt/mmc/DCIM','busybox_execution':'explicit QEMU of original ARM applets',
            'media':seed_card(guest,photo),'sd_supervisor_installed':False}
    (guest/'var/run/nx300m-gallery-runtime.json').write_text(json.dumps(report,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False),flush=True)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bootstrap',required=True,choices=(GUEST,))
    parser.add_argument('--photo',type=Path,help='Seed only this explicit JPEG, without the default photo references')
    args=parser.parse_args();prepare_gallery(args.bootstrap,args.photo)


if __name__=='__main__':main()
