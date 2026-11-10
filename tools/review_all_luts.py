"""Review every current source, with duplicates and monochrome accounted for."""
import argparse
from collections import Counter
import hashlib

import numpy as np

from compare_film_luts_current import ROOT, OUT, inputs, grid, resize, read_json, write_json, digest
from screen_remaining_luts import available
from nx300m_lut_core import parse_lut_bytes, apply_lut, identity_lut, sample_lut
from audit_camera_blend_ratios import lab

DEST=OUT/'full-recheck'


def inventory():
    DEST.mkdir(parents=True,exist_ok=True)
    photos=inputs()
    # All eighteen scenes contribute to similarity; these values do not rank quality.
    small=[resize(p,72).reshape(-1,3) for _,p,_ in photos]
    probes=np.concatenate([a[np.linspace(0,len(a)-1,128).astype(int)] for a in small])
    color_probes=identity_lut(9).table.reshape(-1,3)
    records=[]
    signatures={}
    vectors=[]
    for index,e in enumerate(available()):
        lut=parse_lut_bytes(e['path'].read_bytes(),e['path'].name)
        native=np.round(lut.table.astype(np.float64),7).astype('<f4')
        signature=hashlib.sha256(str((lut.size,lut.domain_min,lut.domain_max)).encode()+native.tobytes()).hexdigest()
        out=sample_lut(lut,color_probes)
        singular=np.linalg.svd(out-out.mean(0),compute_uv=False)
        rank_one=float(np.sum(singular[1:]**2)/max(np.sum(singular**2),1e-15))
        mono=float(np.max(np.ptp(out,axis=-1)))<.003
        duotone=rank_one<.0001 and not mono
        record={k:v for k,v in e.items() if k!='path'}
        record.update(number=index+1,path=e['path'].relative_to(ROOT).as_posix(),
                      sha256=digest(e['path']),grid=lut.size,monochrome=mono,
                      single_color_tone=duotone,chromatic_rank_residual=rank_one,
                      native_signature=signature)
        if signature in signatures:
            record['same_native_table_as']=signatures[signature]
        else:
            signatures[signature]=record['number']
        records.append(record)
        vectors.append(lab(sample_lut(lut,probes)).astype(np.float32))
        if (index+1)%100==0:print('inventoried '+str(index+1),flush=True)
    vector=np.stack(vectors)
    np.save(DEST/'scene-signatures.npy',vector)
    for i,e in enumerate(records):
        delta=np.linalg.norm(vector-vector[i],axis=-1)
        mean=delta.mean(-1)
        mean[i]=np.inf
        closest=np.argsort(mean)[:4]
        e['closest_sources']=[dict(number=records[j]['number'],mean_delta_E76=float(mean[j]),
                                   p95_delta_E76=float(np.percentile(delta[j],95))) for j in closest]
    machine=read_json(ROOT/'lut-library/machine-catalog.json')['ids']
    report=dict(count=len(records),counts=dict(Counter(e['provider'] for e in records)),
                scene_count=len(photos),current_camera_ids=machine,
                current_master_hashes={k:digest(ROOT/'looks'/k/'source.cube') for k in machine},
                policy='No numerical brightness/saturation/contrast gate, no shortlist size limit. '
                       'Native-grid duplicate signatures are rounded to 1e-7; visual similarity uses 18 scenes. '
                       'Monochrome and single-color tone are accounted for but excluded per user preference.',
                entries=records)
    write_json(DEST/'inventory.json',report)
    print(str(dict(Counter('duplicate' if 'same_native_table_as' in e else
                          'monochrome' if e['monochrome'] else 'single-tone' if e['single_color_tone'] else
                          'color-to-review' for e in records))),flush=True)


def sheets():
    report=read_json(DEST/'inventory.json')
    photos=inputs()
    scene_ids=[0,1,3,6]
    pages=[]
    for provider in ['local','cxo','github']:
        rows=[e for e in report['entries'] if e['provider']==provider and
              'same_native_table_as' not in e and not e['monochrome'] and not e['single_color_tone']]
        for start in range(0,len(rows),7):
            page=rows[start:start+7]
            display=[('原图 · 暖光人像 / 自然光人像 / 城市 / 绿叶',
                      [(photos[i][0],photos[i][1]) for i in scene_ids])]
            for e in page:
                source=ROOT/e['path']
                lut=parse_lut_bytes(source.read_bytes(),source.name)
                display.append((f"{e['number']:03d} · {e['label']}",
                                [('',apply_lut(photos[i][1],lut)) for i in scene_ids]))
            name=f'{provider}-{start//7+1:02d}.jpg'
            grid('全库重新审片 · '+provider+' · '+str(start//7+1),
                 '每项源配方100%；只合并数值相同的源表，保留全部彩色风格及曝光变体审片。',
                 display,DEST/'sheets'/name,330,205)
            pages.append(dict(file='sheets/'+name,numbers=[e['number'] for e in page]))
            print(name,flush=True)
    write_json(DEST/'pages.json',pages)


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('stage',choices=['inventory','sheets'])
    args=p.parse_args()
    {'inventory':inventory,'sheets':sheets}[args.stage]()
