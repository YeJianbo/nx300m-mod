"""Describe every available LUT and compare its coverage with the current palette."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np

from compare_film_luts_current import ROOT, OUT, inputs, resize, read_json, write_json, grid
from audit_camera_blend_ratios import lab, patch, SKIN
from nx300m_lut_core import apply_lut, parse_lut_file

DEST=OUT/'remaining-library'


def available():
    rows=[]
    for e in read_json(ROOT/'lut-library/catalog.json')['entries']:
        path=ROOT/'looks'/e['id']/'source.cube'
        if not path.exists(): path=ROOT/'lut-library'/e['file']
        rows.append(dict(provider='local',id=e['id'],label=e['label'],category=e['group'],path=path))
    for e in read_json(ROOT/'outputs/looks/cxo-import/all-masters/manifest.json')['entries']:
        rows.append(dict(provider='cxo',id=e['camera_id'],label=e['label'],category=e['cxo_category'],
                         path=ROOT/'outputs/looks/cxo-import/all-masters'/e['cube']))
    path=ROOT/'outputs/looks/library-review/20261110T130143-FILM_LUTS_REFERENCE/selection.tsv'
    with path.open(encoding='utf-8-sig',newline='') as f:
        for e in csv.DictReader(f,delimiter='\t'):
            rows.append(dict(provider='github',id=e['stable_id'],label=e['label'],category=e['category'],
                             path=ROOT/'scratch/film-luts-reference'/e['source_file']))
    return rows


def main():
    DEST.mkdir(exist_ok=True)
    photos=inputs()
    selected=[0,1,2,3,4,5,6,7,10,16]
    arrays=[resize(photos[i][1],64) for i in selected]
    sizes=[a.shape for a in arrays]
    flat=np.concatenate([a.reshape(-1,3) for a in arrays]).reshape(-1,1,3)
    source_lab=lab(flat).reshape(-1,3)
    cuts=np.cumsum([0]+[s[0]*s[1] for s in sizes])
    sample_indices=np.concatenate([np.linspace(cuts[i],cuts[i+1]-1,96).astype(int) for i in range(len(arrays))])
    machine=read_json(ROOT/'lut-library/machine-catalog.json')['ids']
    current=[]
    for key in machine:
        lut=parse_lut_file(ROOT/'looks'/key/'source.cube').lut
        current.append(lab(apply_lut(flat,lut)).reshape(-1,3)[sample_indices])
    current=np.stack(current)
    records=[]
    for n,e in enumerate(available()):
        lut=parse_lut_file(e['path']).lut
        output=apply_lut(flat,lut).reshape(-1,3)
        olab=lab(output).reshape(-1,3)
        d=np.linalg.norm(current-olab[sample_indices][None,...],axis=-1).mean(axis=-1)
        nearest=np.argsort(d)[:3]
        scenes=[]
        skin_hues=[]
        for j,(idx,shape) in enumerate(zip(selected,sizes)):
            a=source_lab[cuts[j]:cuts[j+1]]
            b=olab[cuts[j]:cuts[j+1]]
            original=flat[cuts[j]:cuts[j+1],0]
            graded=output[cuts[j]:cuts[j+1]]
            ca=np.percentile(a[:,0],90)-np.percentile(a[:,0],10)
            cb=np.percentile(b[:,0],90)-np.percentile(b[:,0],10)
            cha=np.linalg.norm(a[:,1:],axis=-1).mean()
            chb=np.linalg.norm(b[:,1:],axis=-1).mean()
            high=((graded.max(-1)>=254.5/255)&(original.max(-1)<254.5/255)).mean()*100
            low=((graded.min(-1)<=.5/255)&(original.min(-1)>.5/255)).mean()*100
            scenes.append(dict(scene=photos[idx][0],delta_mean_L=float((b-a)[:,0].mean()),
                               contrast_ratio=float(cb/ca),chroma_ratio=float(chb/cha),
                               new_high_clip_percent=float(high),new_low_clip_percent=float(low)))
            if photos[idx][0] in SKIN and chb>1:
                pa=patch(a.reshape(shape),SKIN[photos[idx][0]]).mean(axis=0)
                pb=patch(b.reshape(shape),SKIN[photos[idx][0]]).mean(axis=0)
                delta=(np.degrees(np.arctan2(pb[2],pb[1])-np.arctan2(pa[2],pa[1]))+180)%360-180
                skin_hues.append(float(delta))
        record={k:v for k,v in e.items() if k!='path'}
        record.update(path=e['path'].relative_to(ROOT).as_posix(),native_grid=lut.size,
                      in_camera=e['id'] in machine,scenes=scenes,
                      mean_L_delta=float(np.mean([s['delta_mean_L'] for s in scenes])),
                      median_contrast_ratio=float(np.median([s['contrast_ratio'] for s in scenes])),
                      mean_chroma_ratio=float(np.mean([s['chroma_ratio'] for s in scenes])),
                      max_new_high_clip_percent=max(s['new_high_clip_percent'] for s in scenes),
                      max_new_low_clip_percent=max(s['new_low_clip_percent'] for s in scenes),
                      max_skin_hue_shift=float(max(map(abs,skin_hues))) if skin_hues else None,
                      closest_current=[dict(id=machine[k],mean_sampled_delta_E76=float(d[k])) for k in nearest])
        records.append(record)
        if n%50==0: print('screened '+str(n+1),flush=True)
    report=dict(schema=1,count=len(records),counts={key:sum(e['provider']==key for e in records)
                                                 for key in ['local','cxo','github']},
                scene_count=len(selected),measurement_long_edge=64,
                interpretation='Descriptive coarse screening. Color distance is not a quality score. '
                'Counts include same-ID CXO entries in local and full CXO, and GitHub exposure variants.',entries=records)
    write_json(DEST/'all-luts-screen.json',report)
    summary=[]
    for e in records:
        summary.append({k:v for k,v in e.items() if k not in ['scenes','closest_current']}|
                       dict(nearest_current=e['closest_current'][0]['id'],
                            nearest_current_deltaE=e['closest_current'][0]['mean_sampled_delta_E76']))
    with (DEST/'all-luts-screen.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(summary[0]));writer.writeheader();writer.writerows(summary)
    filtered=[e for e in records if not e['in_camera'] and 0<=e['mean_L_delta']<=9
              and .75<e['median_contrast_ratio']<1.1 and .55<e['mean_chroma_ratio']<1.12
              and e['max_skin_hue_shift'] is not None and e['max_skin_hue_shift']<16
              and e['max_new_high_clip_percent']<2 and e['max_new_low_clip_percent']<2
              and e['closest_current'][0]['mean_sampled_delta_E76']>2]
    write_json(DEST/'soft-style-candidates.json',filtered)
    print(json.dumps(dict(total=len(records),soft_candidates=len(filtered))))


if __name__=='__main__': main()
