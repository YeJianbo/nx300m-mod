"""Update an existing SD installation with the tested X11-only dial lock."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import zipfile

from nx300m_installer import safe_path, volume, write_checked

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/controls/mode-dial-x11'
FILES={
    'bin/mode_dial_x11.arm':'outputs/controls/mode-dial-x11/mode_dial_x11.arm',
    'bin/nxui.arm':'outputs/metol/nxui.arm',
    'fn-panel/bin/nxui.arm':'outputs/metol/nxui.arm',
    'mfw-panel-actions.sh':'mod/deployment/mfw/panel-actions.sh',
    'mfw-services.sh':'mod/deployment/mfw/services.sh',
    'mode-dial-restore.sh':'mod/deployment/mode-dial-restore.sh',
}

def sha(data):return hashlib.sha256(data).hexdigest()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--card',type=Path,required=True)
    parser.add_argument('--serial',required=True)
    args=parser.parse_args()
    card=args.card
    identity=volume(str(card))
    if identity['serial']!=args.serial:raise ValueError('Card identity differs')
    if safe_path(card,'nx300m.bin').exists():raise ValueError('Unexpected root firmware file: preserve and inspect it')
    tested=json.loads((OUT/'arm-vm/report.json').read_text(encoding='utf-8'))
    if not tested['passed']:raise ValueError('Mapping VM did not pass')
    if (sha((ROOT/FILES['bin/nxui.arm']).read_bytes())!=tested['menu_sha256'] or
        sha((ROOT/FILES['bin/mode_dial_x11.arm']).read_bytes())!=tested['helper_sha256']):
        raise ValueError('Menu/helper differ from tested files')
    runtime=safe_path(card,'nx300m-mod/runtime-files.list')
    old_list=runtime.read_text(encoding='ascii').splitlines()
    names=list(dict.fromkeys([*old_list,*FILES]))
    writes={'nx300m-mod/'+name:(ROOT/source).read_bytes() for name,source in FILES.items()}
    writes['nx300m-mod/runtime-files.list']=('\n'.join(names)+'\n').encode('ascii')
    writes['nx300m-mod/RESTART_SERVICE']=b'mode-dial-x11-no-flash\n'
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup=OUT/('card-'+stamp)
    backup.mkdir()
    protected={}
    for prefix in ('nx300m-mod/.state','nx300m-mod/logs'):
        directory=safe_path(card,prefix)
        if directory.is_dir():
            for path in directory.rglob('*'):
                if path.is_file():
                    name=path.relative_to(card).as_posix()
                    data=safe_path(card,name).read_bytes()
                    target=safe_path(backup/'before',name)
                    target.parent.mkdir(parents=True,exist_ok=True)
                    write_checked(target,data)
                    protected[name]=sha(data)
    records={}
    for name,data in writes.items():
        target=safe_path(card,name)
        if target.exists() and not target.is_file():raise ValueError('Abnormal target: '+name)
        old=target.read_bytes() if target.is_file() else None
        if old is not None:
            saved=safe_path(backup/'before',name)
            saved.parent.mkdir(parents=True,exist_ok=True)
            write_checked(saved,old)
        records[name]={'before_sha256':sha(old) if old is not None else None,'sha256':sha(data),'bytes':len(data)}
    report={'status':'prepared','card':identity,'backup':str(backup),'files':records,
            'firmware_written':False,'physical_camera_verified':False,'written':[]}
    save=lambda p:p.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    save(backup/'result.json')
    try:
        with zipfile.ZipFile(OUT/'nx300m-mode-dial-no-flash-update.zip','w',zipfile.ZIP_DEFLATED) as archive:
            for name,data in writes.items():archive.writestr(name,data)
        for name,data in writes.items():
            if volume(str(card))!=identity:raise ValueError('Card changed during write')
            path=safe_path(card,name)
            path.parent.mkdir(parents=True,exist_ok=True)
            write_checked(path,data)
            report['written'].append(name)
        for name,record in records.items():
            if sha(safe_path(card,name).read_bytes())!=record['sha256']:raise ValueError('Readback mismatch: '+name)
        if any(sha(safe_path(card,name).read_bytes())!=expected for name,expected in protected.items()):
            raise ValueError('Preferences/logs changed during write')
        if safe_path(card,'nx300m.bin').exists():raise ValueError('Unexpected root firmware file')
        report.update(status='readback_passed',preferences_and_logs_preserved=True,
                      no_root_firmware_file=True,runtime_file_count=len(names))
    except Exception as error:
        report.update(status='incomplete',error=str(error));raise
    finally:
        save(backup/'result.json');save(OUT/'card-deployment.json')
    print(json.dumps({key:report[key] for key in ('status','backup','firmware_written','no_root_firmware_file')},ensure_ascii=False))

if __name__=='__main__':main()
