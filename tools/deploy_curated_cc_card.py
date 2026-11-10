"""Deploy the approved palette plus the isolated CC color candidate to the identified card."""
import hashlib
import json
import shutil
import sys
from pathlib import Path

import deploy_local_lut_library as deployment
from nx300m_installer import volume, catalog
from lut_table_data import read_tables

ROOT=Path(__file__).resolve().parents[1]
CARD=Path('H:/')
SERIAL='LOCAL_REDACTED'
OUT=ROOT/'outputs/deployment/lut-library-card/curated-cc-source'
KEY='cc50-red-refinement-candidate'


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def preserved_files():
    targets={'nx300m-mod/looks.txt','nx300m-mod/looks-cycle.txt',
             'nx300m-mod/.state/lut-groups.json','nx300m-mod/.state/lut-groups.txt'}
    return {p.relative_to(CARD).as_posix():digest(p) for p in CARD.rglob('*')
            if p.is_file() and p.relative_to(CARD).as_posix() not in targets
            and not p.relative_to(CARD).as_posix().startswith(('nx300m-mod/looks/','System Volume Information/'))}


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    identity=volume(str(CARD.resolve()))
    if identity['serial']!=SERIAL or identity['filesystem'].upper()!='EXFAT':
        raise ValueError('Inserted card does not match the checked NX300M card')
    manifest=json.loads((ROOT/'outputs/looks/camera-curation/selected-manifest.json').read_text(encoding='utf-8'))
    verification=json.loads((ROOT/'lut-library/camera-verification.json').read_text(encoding='utf-8'))
    if manifest['selected_creative_count']!=58 or not verification['camera_ready']:
        raise ValueError('The approved 58-look stage is not ready')
    source=ROOT/'outputs/looks/camera-curation/cc-only/color-refinement'
    cc=json.loads((source/'verification.json').read_text(encoding='utf-8'))
    if cc['compiler']!='existing fit, unchanged; both direct and stock-composed 9^3 tables generated':
        raise ValueError('Unexpected CC candidate')
    stage=OUT/'camera'
    stage.mkdir(parents=True,exist_ok=True)
    shutil.copytree(ROOT/'lut-library/camera',stage,dirs_exist_ok=True)
    candidate=source/'native'/KEY
    meta=json.loads((candidate/'look.json').read_text(encoding='utf-8'))
    meta.pop('source_path',None)
    tables=read_tables(candidate,meta)
    target=stage/'looks'/KEY
    target.mkdir(exist_ok=True)
    for name,data in tables.items():(target/name).write_bytes(data)
    (target/'look.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    raw=(ROOT/'lut-library/camera/looks.txt').read_text(encoding='utf-8')
    raw+=KEY+'|'+meta['label']+'|0|0|0|0|0|0|0\n'
    rows=[line.split('|') for line in raw.splitlines() if line]
    if len(rows)!=60 or len({r[0] for r in rows})!=60:
        raise ValueError('Expected 58 approved looks, one candidate and original-adjustment entry')
    (stage/'looks.txt').write_text(raw,encoding='utf-8',newline='\n')
    selected=[dict(id=e['id'],group=e['group']) for e in manifest['entries']]
    selected.append(dict(id=KEY,group='独立候选'))
    local=dict(groups=[g['name'] for g in manifest['groups']]+['独立候选'],entries=selected)
    (OUT/'catalog.json').write_text(json.dumps(local,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (OUT/'camera-verification.json').write_text(json.dumps(dict(camera_ready=True,rows=60,errors=[]))+'\n',encoding='utf-8')
    # Save evidence for all files outside the LUT deployment scope, including photographs.
    preserved=preserved_files()
    _,old_rows=catalog(CARD)
    print(json.dumps(dict(card=identity,previous_rows=len(old_rows),new_rows=60,
                          outside_scope_files=len(preserved)),ensure_ascii=False),flush=True)
    if volume(str(CARD.resolve()))!=identity:
        raise ValueError('Card changed before deployment')
    deployment.LOCAL=OUT
    deployment.STAGE=stage
    result=deployment.deploy(CARD,reset_groups=True)
    if volume(str(CARD.resolve()))!=identity:
        raise ValueError('Card changed during deployment')
    changed=[name for name,h in preserved.items() if not (CARD/name).is_file() or digest(CARD/name)!=h]
    if changed:
        raise ValueError('Unexpected outside-scope changes: '+json.dumps(changed))
    result.update(card_identity=identity,outside_scope_files_verified=len(preserved),
                  photograph_files_verified=sum(n.startswith('DCIM/') for n in preserved),
                  outside_scope_unchanged=True,official_creative_looks=58,standalone_cc_candidate=KEY,
                  physical_camera_color_verified=False)
    report=Path(result['backup']).parent/'cc-curated-deployment.json'
    report.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(result,report=str(report)),ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':main()
