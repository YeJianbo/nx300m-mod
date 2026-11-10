"""Build the two-module settings overlay and optional local firmware candidate.

Preserves the camera-exported MFW autoexec library. Never writes a camera/card
or starts an updater. The SD companion updates existing installs only.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import zipfile

from build_stock_app_loader import persistent_launch_files, verify_native
import deployment_ubifs as ubifs
import build_slp_candidate as slp
import mfw_runtime
from wsl_paths import wsl_path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'outputs/deployment/settings-candidate'
MODULES = {
    'exposure_controls.so': 'outputs/controls/exposure_controls.so',
    'camera_firmware_mod.so': 'outputs/controls/camera_firmware_mod.so',
}
SD_FILES = {
    'nx300m-mod/launch-profile': 'mod/deployment/app-loader/settings-profile',
    'nx300m-mod/bin/nxui.arm': 'outputs/metol/nxui.arm',
    'nx300m-mod/fn-panel/bin/nxui.arm': 'outputs/metol/nxui.arm',
    'nx300m-mod/mfw-panel-actions.sh': 'mod/deployment/mfw/panel-actions.sh',
    'nx300m-mod/firmware-mod-restore.sh': 'mod/deployment/firmware-mod-restore.sh',
    'nx300m-mod/camera_control.sh': 'mod/deployment/camera_control.sh',
    'nx300m-mod/mfw-services.sh': 'mod/deployment/mfw/services.sh',
}


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def companion():
    target=OUT/'nx300m-settings-sd-update.zip'
    with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for name, source in SD_FILES.items():
            archive.write(ROOT/source,name)
    with zipfile.ZipFile(target) as archive:
        if set(archive.namelist())!=set(SD_FILES) or any(archive.read(name)!=(ROOT/source).read_bytes() for name,source in SD_FILES.items()):
            raise ValueError('SD companion readback differs')
    return target


def build(firmware=False):
    OUT.mkdir(parents=True, exist_ok=True)
    overlay=OUT/'overlay/rootfs'
    items=persistent_launch_files()
    for item in items:
        if item['destination']=='/usr/lib/nx300m-mod/launch-profile':
            item['content']=b'settings\n'
    for name, source in MODULES.items():
        items.append({'destination':'/usr/lib/nx300m-mod/'+name, 'content':verify_native(ROOT/source),
                      'change':'new', 'source':source,
                      'metadata':{'mode':'0o644','uid':0,'gid':0,'mtime':1403327571}})
    mfw=(mfw_runtime.LIBRARY if mfw_runtime.LIBRARY.is_file() else mfw_runtime.SOURCE).read_bytes()
    if hashlib.sha256(mfw).hexdigest()!=mfw_runtime.MFW_SHA256:
        raise ValueError('Camera-exported MFW library identity differs')
    with tarfile.open(ROOT/'work/stock-1.15/rootfs.tar') as archive:
        member=archive.getmember('usr/lib/libmisc.so')
        items.append({'destination':'/usr/lib/libmisc.so','content':mfw,'change':'modified',
                      'source':'camera-exported MFW 1.1 libmisc.so',
                      'metadata':{'mode':oct(member.mode),'uid':member.uid,'gid':member.gid,'mtime':member.mtime}})
    modes={}
    for item in items:
        name=item['destination'].lstrip('/')
        target=overlay/name
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(item['content'])
        item['bytes']=len(item['content'])
        item['sha256']=hashlib.sha256(item.pop('content')).hexdigest()
        item.pop('original',None)
        modes[name]=None if item['change']=='modified' else int(item['metadata']['mode'],8)
    if {p.relative_to(overlay).as_posix() for p in overlay.rglob('*') if p.is_file()}!=set(modes):
        raise ValueError('Unexpected old file in settings overlay')
    save(overlay.parent/'report.json',{'rootfs_changes':items})
    scripts=[overlay/'usr/bin/hib2.sh', overlay/'usr/bin/nx300m-camera-launch',
             overlay/'usr/lib/nx300m-mod/preload.sh',
             *(ROOT/path for path in SD_FILES.values() if path.endswith('.sh'))]
    checked=subprocess.run(['wsl.exe','-d','Debian','-e','sh','-c',
                            'for script do sh -n "$script" || exit; done','sh',
                            *(wsl_path(p) for p in scripts)],capture_output=True,text=True)
    (OUT/'shell-check.log').write_text(checked.stdout+checked.stderr,encoding='utf-8')
    if checked.returncode:
        raise RuntimeError('Shell syntax check failed: '+checked.stderr)
    sd_companion=companion()
    report={'status':'prepared','profile':'settings','modules':list(MODULES),
            'rootfs_overlay_files':list(modes),'mfw_autoexec_preserved':True,
            'mfw_sha256':mfw_runtime.MFW_SHA256,'sd_update':str(sd_companion),
            'shell_syntax_passed':True,'camera_or_card_written':False,
            'firmware_package_created':False,'physical_camera_verified':False,
            'release_ready':False,
            'source_identity':{path:hashlib.sha256((ROOT/path).read_bytes()).hexdigest()
                               for path in [*MODULES.values(),*SD_FILES.values()]}}
    save(OUT/'report.json',report)
    if firmware:
        comparison=ubifs.build(overlay,output_dir=OUT/'ubifs',overlay_modes=modes)
        if not comparison['passed'] or comparison['unexpected_difference_count']:
            raise ValueError('Unexpected rootfs difference')
        image=OUT/'firmware/nx300m-settings-1.15-candidate.slp'
        built=slp.build(OUT/'ubifs/platform-mod-candidate.ubi',image)
        report.update(status='offline_candidate_built',firmware_package_created=True,
                      firmware=str(image),firmware_bytes=image.stat().st_size,
                      rootfs_readback_passed=True,unchanged_partitions=built['unchanged_partitions'])
        save(OUT/'report.json',report)
    return report


def refresh_sd():
    report=json.loads((OUT/'report.json').read_text(encoding='utf-8'))
    for source in MODULES.values():
        if hashlib.sha256((ROOT/source).read_bytes()).hexdigest()!=report['source_identity'][source]:
            raise ValueError('Firmware module changed; rebuild the candidate')
    for name,source in SD_FILES.items():
        if source.endswith('.sh'):
            check=subprocess.run(['wsl.exe','-d','Debian','-e','sh','-n',wsl_path(ROOT/source)],capture_output=True,text=True)
            if check.returncode:
                raise RuntimeError(check.stderr)
    companion()
    report['source_identity'].update({source:hashlib.sha256((ROOT/source).read_bytes()).hexdigest() for source in SD_FILES.values()})
    save(OUT/'report.json',report)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firmware',action='store_true',help='Also build and verify a local UBI/SLP candidate; never install it')
    parser.add_argument('--refresh-sd',action='store_true',help='Update the SD companion without rebuilding unchanged firmware')
    args=parser.parse_args()
    if args.firmware and args.refresh_sd:
        parser.error('Select one action')
    print(json.dumps(refresh_sd() if args.refresh_sd else build(args.firmware),ensure_ascii=False,indent=2))
