"""Render the broad-screen shortlist against its nearest approved camera look."""
from __future__ import annotations

import json
import numpy as np

from compare_film_luts_current import ROOT, OUT, inputs, read_json, write_json, grid
from nx300m_lut_core import apply_lut, parse_lut_file
from audit_camera_blend_ratios import lab, metrics

DEST=OUT/'remaining-library'
PICKS=[
 ('cxo','cxo-cinedesaturatedgreen'),
 ('local','lr-custom-4546d428bd19332a9f69b40c3fdaa762'),
 ('local','lr-custom-e71ed7965c33bf99261bb1a4881c06af'),
 ('local','lr-custom-bb2eed1b68c4d7183ac6bb60020fba2b'),
 ('local','sigma-forest-green'),
 ('local','sigma-warm-gold'),
 ('local','sigma-powder-blue'),
 ('github','film-luts-negative-new-fuji-400h--'),
 ('github','film-luts-negative-color-fuji-superia-reala-100'),
 ('github','film-luts-negative-new-kodak-portra-800'),
 ('github','film-luts-bw-kodak-t-max-400'),
 ('local','lr-custom-1a2f4da956b6bfc56675b85e8b456da6'),
 ('cxo','cxo-fujisensia100'),
 ('github','film-luts-negative-color-kodak-portra-160-nc'),
 ('cxo','cxo-fujipro400h'),
 ('local','lr-custom-720de2dc0ef97b8c94c90b5073455e8b'),
]


def main():
    photos=inputs()
    all_rows=read_json(DEST/'all-luts-screen.json')['entries']
    lookup={(e['provider'],e['id']):e for e in all_rows}
    labels={e['id']:e['label'] for e in all_rows if e['provider']=='local'}
    records=[]
    overviews={i:[] for i in [0,6,10]}
    for provider,key in PICKS:
        e=lookup[provider,key]
        lut=parse_lut_file(ROOT/e['path']).lut
        nearest=e['closest_current'][0]['id']
        current=parse_lut_file(ROOT/'looks'/nearest/'source.cube').lut
        all_metrics=[]
        image_rows=[]
        for i in [0,1,2,6,9,10,14]:
            name,source,_=photos[i]
            output,prior=apply_lut(source,lut),apply_lut(source,current)
            cells=[('原图 / 同底图',source),('机内参照 · '+labels[nearest],prior),(provider+' · '+e['label'],output)]
            image_rows.append((name,cells))
            if i in overviews: overviews[i].append((e['label'],cells))
            m=metrics(source,output,name)
            all_metrics.append(dict(scene=name,**m))
        path=DEST/'shortlist-review'/f'{provider}-{key}.jpg'
        grid(e['label']+' · 入机价值复核', '原图 / 数值最近的当前机内款 / 候选100%；最近不等于风格相同',
             image_rows,path,390,250)
        records.append(dict(source=e,review=path.relative_to(OUT).as_posix(),scenes=all_metrics))
        print('reviewed '+key,flush=True)
    for i,values in overviews.items():
        for start in range(0,len(values),8):
            grid('新增风格复核 · '+photos[i][0], '原图 / 最接近的现有机内款 / 新候选100%',
                 values[start:start+8],DEST/f'shortlist-scene-{i:02}-{start//8+1}.jpg',370,238)
    # Include the three 400H choices together, without blending them.
    family=[lookup[p,k] for p,k in [('cxo','cxo-fujipro400h'),
                                  ('github','film-luts-negative-new-fuji-400h'),
                                  ('github','film-luts-negative-new-fuji-400h--')]]
    luts=[parse_lut_file(ROOT/e['path']).lut for e in family]
    family_rows=[]
    for i in [0,1,2,6,10,14]:
        name,a,_=photos[i]
        family_rows.append((name,[('原图',a)]+[(e['provider']+' '+e['label'],apply_lut(a,lut)) for e,lut in zip(family,luts)]))
    grid('Pro 400H · 三种现成来源比较','原图 / CXO 400H / GitHub 400H / GitHub 400H -；没有新增混合',
         family_rows,DEST/'400H-source-comparison.jpg',382,250)
    write_json(DEST/'shortlist-verification.json',dict(count=len(records),scene_count=7,entries=records))
    print(json.dumps(dict(reviewed=len(records))))


if __name__=='__main__': main()
