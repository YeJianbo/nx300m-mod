"""Assemble the public first-install payload with original procedural LUTs."""
from pathlib import Path
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone

from installer_backend import verify_backend
from nx300m_installer import safe_path
from manual_lens_wifi_payload import build_wifi_payload, verify_wifi_payload
from release_payload import verify_complete, is_core

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'outputs/releases/windows-updater-20261106'


def build():
    source = BASE / 'first-install'
    manifest = json.loads((source/'manifest.json').read_text(encoding='utf-8'))
    original = ROOT/'outputs/looks/original-release'
    if not (original/'manifest.json').is_file():
        raise ValueError('请先运行 build_original_looks.py')
    files = {}
    for name, rec in manifest['files'].items():
        if name.startswith('nx300m-mod/looks/') or name in ('nx300m-mod/looks.txt','nx300m-mod/looks-cycle.txt'):
            continue
        data = safe_path(source/'payload',name).read_bytes()
        if hashlib.sha256(data).hexdigest()!=rec['sha256']:
            raise ValueError('Source differs: '+name)
        files[name]=data
    for folder in original.iterdir():
        if not folder.is_dir(): continue
        meta=json.loads((folder/'look.json').read_text(encoding='utf-8'))
        if meta.get('source_kind')!='original-procedural' or meta.get('license')!='CC0-1.0':
            raise ValueError('Unapproved look: '+folder.name)
        for name in ('look.json','table.d4lut'):
            files['nx300m-mod/looks/'+folder.name+'/'+name]=(folder/name).read_bytes()
    files['nx300m-mod/looks.txt']=(original/'looks.txt').read_bytes()
    files['nx300m-mod/looks-cycle.txt']=files['nx300m-mod/looks.txt']
    files.update(build_wifi_payload(ROOT))
    wifi=verify_wifi_payload(files.__getitem__)
    backend=verify_backend(files.__getitem__)
    complete=verify_complete(files.__getitem__)
    if any(files[name] != safe_path(source/'payload',name).read_bytes()
           for name in files if is_core(name)):
        raise ValueError('公开首装与完整首装的非 LUT 程序不同。')
    destination=BASE/'public-first-install'
    staging=Path(tempfile.mkdtemp(prefix='public-first-install-build-',dir=BASE))
    entries={}
    for name,data in files.items():
        target=safe_path(staging/'payload',name);target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
        if target.read_bytes()!=data:
            raise OSError('Public first-install readback differs: '+name)
        entries[name]={'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
    result={'schema':1,'kind':'nx300m-first-install-local-candidate','distribution':'public-original-looks',
            'files':entries,'backend':backend,'manual_lens_wifi':wifi,
            'complete_runtime':complete,'physical_first_install_verified':False}
    (staging/'manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    if destination.exists():
        history=BASE/'history'/('public-first-install-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
        history.parent.mkdir(parents=True,exist_ok=True)
        if not history.resolve().is_relative_to(BASE.resolve()):
            raise ValueError('Public first-install history path escaped release directory')
        os.rename(destination,history)
    os.rename(staging,destination)
    print('PUBLIC_FIRST_INSTALL='+str(destination))
    return destination


if __name__=='__main__': build()
