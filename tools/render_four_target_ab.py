"""Render four user-requested A/B candidates without changing approved looks."""
from __future__ import annotations

import json
import shutil

import numpy as np

from compare_film_luts_current import ROOT, OUT, entries, inputs, grid, digest, read_json, write_json
from nx300m_lut_core import (Lut3D, apply_lut, blend_luts, cube_bytes, identity_lut,
                             parse_lut_file, sample_lut, BLEND_WORKING_SPACE)
from audit_camera_blend_ratios import lab, metrics, patch, SKIN


DEST = OUT / 'four-target-ab'
SPECS = {
    'portra-400': (60, 50),
    'velvia-50': (80, 70),
    'classic-chrome': (50, 40),
    'tri-x-400': (50, 50),
}


def trix_curve(x):
    # Compact smooth change: exact identity below .14 and above .84.
    t = np.clip((x - .14) / .70, 0, 1)
    return x - .012 * np.sin(2*np.pi*t) * np.sin(np.pi*t)**2


def source_diagnostics(lut):
    v = np.linspace(0, 1, 4097, dtype=np.float32)
    neutral = np.repeat(v[:, None], 3, axis=1)
    grey = sample_lut(lut, neutral)
    grey_l = lab(grey)[:, 0]
    dense = sample_lut(lut, identity_lut(65).table)
    # Deliberately labelled equal code-value inputs, not equal luminance.
    swatches = np.array([[.5,.5,.5],[.7,.1,.1],[.1,.7,.1],[.1,.1,.7],
                         [.7,.7,.1],[.1,.7,.7],[.7,.1,.7]], dtype=np.float32)
    return dict(table_min=float(lut.table.min()), table_max=float(lut.table.max()),
                dense_probe_grid=65, dense_probe_out_of_range_count=int(((dense<0)|(dense>1)).sum()),
                max_neutral_L_decrease=float(np.maximum(-np.diff(grey_l),0).max()),
                neutral_endpoint_RGB=grey[[0,-1]].tolist(),
                output_channel_spread_max=float(np.ptp(dense,axis=-1).max()),
                swatch_input_RGB=swatches.tolist(),swatch_output_RGB=sample_lut(lut,swatches).tolist(),
                neutral_code_values=v[::128].tolist(),neutral_L=grey_l[::128].tolist())


def scene_measurement(a, b, source, name):
    la, lb = lab(a), lab(b)
    m = dict(scene=name,A=metrics(source,a,name),B=metrics(source,b,name),
             mean_delta_E76_A_B=float(np.linalg.norm(lb-la,axis=-1).mean()),
             max_RGB_change_8bit=float(np.abs(a-b).max()*255),
             new_high_clip_vs_A_percent=float(((b.max(-1)>=254.5/255)&(a.max(-1)<254.5/255)).mean()*100),
             new_low_clip_vs_A_percent=float(((b.min(-1)<=.5/255)&(a.min(-1)>.5/255)).mean()*100))
    if name in SKIN:
        sa, sb = patch(la,SKIN[name]), patch(lb,SKIN[name])
        high = sa[:,0] >= np.percentile(sa[:,0],75)
        m['skin_highlight_A_Lab'] = sa[high].mean(axis=0).tolist()
        m['skin_highlight_B_Lab'] = sb[high].mean(axis=0).tolist()
    shadow = la[...,0] < 20
    mid = (la[...,0]>=25)&(la[...,0]<=75)
    m['A_defined_shadow_mean_delta_L'] = float((lb-la)[...,0][shadow].mean()) if shadow.any() else None
    m['A_defined_mid_L10_L90_A'] = np.percentile(la[...,0][mid],[10,90]).tolist() if mid.any() else None
    m['A_defined_mid_L10_L90_B'] = np.percentile(lb[...,0][mid],[10,90]).tolist() if mid.any() else None
    return m


def quick_summary():
    report=read_json(DEST/'verification.json')
    photos=inputs()
    selected={'portra-400':0,'velvia-50':6,'classic-chrome':13,'tri-x-400':1}
    display=[]
    for e in report['entries']:
        i=selected[e['family']]
        name,source,_=photos[i]
        a=parse_lut_file(DEST/e['A_cube']).lut
        b=parse_lut_file(DEST/e['B_cube']).lut
        al=f"A · 现有 CXO{e['A_cxo_percent']}%"
        bl=(f"B · 测试 CXO{e['B_cxo_percent']}%" if e['family']!='tri-x-400'
            else 'B · CXO50% + 中间调微曲线')
        display.append((e['label']+' · '+name,[(al,apply_lut(source,a)),(bl,apply_lut(source,b))]))
    grid('四款定向 A/B · 快速预览','左A现版 / 右B测试版；每款选择不同重点场景，完整18场景见分款图',
         display,DEST/'00-ab-summary.jpg',560,310)


def main():
    DEST.mkdir(parents=True,exist_ok=True)
    (DEST/'cubes33').mkdir(exist_ok=True)
    (DEST/'source-cubes').mkdir(exist_ok=True)
    rows={r['family']:r for r in entries() if 'github' in r['sources']}
    photos=inputs()
    protected=dict(read_json(OUT/'measurements.json')['protected_files'])
    for row in rows.values():
        for key in ('local','current'):
            path=row['sources'][key]
            protected[path.relative_to(ROOT).as_posix()]=digest(path)
    for name,value in read_json(OUT/'measurements.json')['protected_files'].items():
        assert digest(ROOT/name)==value,name
    report=dict(schema=1,status='A/B candidates only; no adoption',working_space=BLEND_WORKING_SPACE,
                interpolation='existing nx300m_lut_core.sample_lut trilinear',scene_count=len(photos),
                scenes=[dict(meta,display_label=name) for name,_,meta in photos],
                protected_files=protected,frozen_families=[k for k in rows if k not in SPECS],entries=[])
    outputs={}
    for family,(pa,pb) in SPECS.items():
        row=rows[family]
        a_path=DEST/'cubes33'/f'{family}-A.cube'
        shutil.copyfile(row['sources']['current'],a_path)
        a=parse_lut_file(a_path).lut
        local=parse_lut_file(row['sources']['local']).lut
        cxo=parse_lut_file(row['sources']['cxo']).lut
        rebuilt=blend_luts(local,cxo,pa/100)
        rebuild_error=float(np.abs(a.table-rebuilt.table).max())
        if rebuild_error>2e-6: raise ValueError(f'Current recipe differs from stated blend: {family} {rebuild_error}')
        if family=='tri-x-400':
            b=Lut3D(33,trix_curve(a.table).astype(np.float32),'Tri-X CXO50 midtone curve candidate')
            b_label='B · CXO50% + 中间调微曲线'
        else:
            b=blend_luts(local,cxo,pb/100,title=f'{row["label"]} CXO{pb} A-B candidate')
            b_label=f'B · 测试 CXO{pb}%'
        b_path=DEST/'cubes33'/f'{family}-B.cube'
        b_path.write_bytes(cube_bytes(b))
        readback=parse_lut_file(b_path).lut
        error=float(np.abs(readback.table-b.table).max())
        assert error<1e-6 and np.isfinite(readback.table).all()
        b=readback
        source_info={}
        for kind in ('local','cxo'):
            target=DEST/'source-cubes'/f'{family}-{kind}.cube'
            shutil.copyfile(row['sources'][kind],target)
            source_info[kind]=dict(file=target.relative_to(DEST).as_posix(),
                                   original_path=row['sources'][kind].relative_to(ROOT).as_posix(),sha256=digest(target))
        ao=[apply_lut(x,a) for _,x,_ in photos]
        bo=[apply_lut(x,b) for _,x,_ in photos]
        outputs[family]=(row['label'],f'A · 现有 CXO{pa}%',b_label,ao,bo)
        record=dict(family=family,label=row['label'],A_cxo_percent=pa,B_cxo_percent=pb,
                    A_cube=a_path.relative_to(DEST).as_posix(),B_cube=b_path.relative_to(DEST).as_posix(),
                    A_sha256=digest(a_path),B_sha256=digest(b_path),sources=source_info,
                    A_rebuilt_blend_max_error=rebuild_error,B_roundtrip_error=error,
                    A_diagnostics=source_diagnostics(a),B_diagnostics=source_diagnostics(b),
                    scenes=[scene_measurement(aa,bb,src,n) for (n,src,_),aa,bb in zip(photos,ao,bo)])
        for group,indices in [('old-six',range(6)),('local-a',range(6,12)),('local-b',range(12,18))]:
            image_rows=[(photos[i][0],[('原图 / 同底图',photos[i][1]),
                       (f'A · 现有 CXO{pa}%',ao[i]),(b_label,bo[i])]) for i in indices]
            grid(row['label']+' · 定向 A/B','原图 / A现版 / B测试版；同底图与三线性插值，未替换正式版本',
                 image_rows,DEST/'by-look'/f'{family}-{group}.jpg',420,270)
        focus=[0,1,2,6,10,14] if family in ('portra-400','classic-chrome') else [0,3,6,7,9,14]
        grid(row['label']+' · 重点场景 A/B','原图 / A现版 / B测试版',
             [(photos[i][0],[('原图',photos[i][1]),(f'A · 现有 CXO{pa}%',ao[i]),(b_label,bo[i])]) for i in focus],
             DEST/f'{family}-focus.jpg',420,270)
        report['entries'].append(record)
        print('rendered '+family,flush=True)
    # A separate reference prevents the locked Ektachrome from looking like another candidate.
    ref_source=rows['ektachrome-100']['sources']['current']
    ref_path=DEST/'cubes33/ektachrome-100-locked-reference.cube'
    shutil.copyfile(ref_source,ref_path)
    ref=parse_lut_file(ref_path).lut
    ref_output=[apply_lut(x,ref) for _,x,_ in photos]
    for group,indices in [('old-six',range(6)),('local-a',range(6,12)),('local-b',range(12,18))]:
        grid('Ektachrome 100 · 已定版参考','原图 / CXO85%最新细修定版；保持原字节',
             [(photos[i][0],[('原图',photos[i][1]),('Ektachrome · 锁定参考',ref_output[i])]) for i in indices],
             DEST/'reference'/f'ektachrome-{group}.jpg',420,270)
    for page,indices in [('portraits',[0,1,2]),('colors',[6,10,14]),('shadows',[7,9,16])]:
        display=[]
        for family,(label,al,bl,ao,bo) in outputs.items():
            for i in indices:display.append((label+' · '+photos[i][0],[(al,ao[i]),(bl,bo[i])]))
        # Two pages per subject keep every tile readable at normal desktop zoom.
        for start in (0,6):
            grid('四款 A/B · '+page+' · '+str(start//6+1),'左A现版 / 右B测试版；没有GitHub参与',
                 display[start:start+6],DEST/f'overview-{page}-{start//6+1}.jpg',520,300)
    values=np.linspace(0,1,65537)
    curve=trix_curve(values)
    assert np.diff(curve).min()>0
    assert np.abs(curve[(values<=.14)|(values>=.84)]-values[(values<=.14)|(values>=.84)]).max()<1e-12
    report['trix_curve']=dict(formula='y=x-0.012*sin(2*pi*t)*sin(pi*t)^2; t=clip((x-0.14)/0.70,0,1)',
                              applied_to='each output encoded RGB channel, then baked on 33^3 lattice',
                              max_change_8bit=float(np.abs(curve-values).max()*255),
                              min_analytic_sampled_slope=float(np.diff(curve).min()/(values[1]-values[0])),
                              input_color_to_grey_remix=False,
                              note='Output curve changes only midtones; table interpolation can slightly affect boundary intervals.')
    assert all(digest(ROOT/p)==v for p,v in protected.items())
    report.update(protected_files_unchanged=True,reference_cube=ref_path.relative_to(DEST).as_posix(),
                  camera_runtime_verified=False,lightroom_runtime_verified=False)
    write_json(DEST/'verification.json',report)
    quick_summary()
    print(json.dumps(dict(scenes=len(photos),families=len(SPECS),frozen=len(report['frozen_families']),
                          protected_files_unchanged=True)),flush=True)


if __name__=='__main__': main()
