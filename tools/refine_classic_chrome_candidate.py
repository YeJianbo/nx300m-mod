"""Standalone CC color candidate; keep the approved master and compiler unchanged."""
import hashlib
import json
import sys
import zipfile
from pathlib import Path
import numpy as np

from compare_film_luts_current import ROOT, inputs, grid
from nx300m_lut_core import Lut3D, parse_lut_bytes, cube_bytes, apply_lut
from refine_ektachrome import M1, M2, ramp
from look_library import LookLibrary
from lut_resample import sample_table
from audit_camera_blend_ratios import lab, metrics
from build_lumix_lightroom_profiles import ProfileSource, _encode_table, _resample, _verify_xmp, _xmp

sys.stdout.reconfigure(encoding='utf-8')
BASE_KEY='nx300-fuji-classic-chrome-cxo50'
KEY='cc50-red-refinement-candidate'
LABEL='富士 Classic Chrome 红色细修候选'
OUT=ROOT/'outputs/looks/camera-curation/cc-only/color-refinement'
PARAMETERS=dict(max_hue_rotation_degrees=2.0,hue_rise=[-10,5],hue_fall=[25,38],
                chroma_rise=[.06,.12],lightness_rise=[.18,.32],lightness_fall=[.85,.98],
                gamut_strategy='reduce rotation by bisection; preserve OKLab lightness and chroma')


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def to_oklab(rgb):
    rgb=np.asarray(rgb,dtype=np.float64)
    linear=np.where(rgb<=.04045,rgb/12.92,((rgb+.055)/1.055)**2.4)
    return np.cbrt(linear@M1.T)@M2.T


def from_oklab(v):
    linear=((v@np.linalg.inv(M2).T)**3)@np.linalg.inv(M1).T
    return np.where(linear<=.0031308,12.92*linear,1.055*np.maximum(linear,0)**(1/2.4)-.055)


def refine(lut):
    rgb=lut.table.astype(np.float64)
    value=to_oklab(rgb)
    light,a,b=np.moveaxis(value,-1,0)
    chroma=np.hypot(a,b)
    hue=np.degrees(np.arctan2(b,a))
    weight=(ramp(hue,*PARAMETERS['hue_rise'])*(1-ramp(hue,*PARAMETERS['hue_fall']))
            *ramp(chroma,*PARAMETERS['chroma_rise'])
            *ramp(light,*PARAMETERS['lightness_rise'])*(1-ramp(light,*PARAMETERS['lightness_fall'])))
    angle=np.deg2rad(PARAMETERS['max_hue_rotation_degrees'])*weight
    active=angle>1e-12
    def rotated(fraction):
        v=value.copy();t=angle*fraction
        v[...,1]=a*np.cos(t)-b*np.sin(t)
        v[...,2]=a*np.sin(t)+b*np.cos(t)
        output=from_oklab(v)
        return np.where(active[...,None],output,rgb)
    full=rotated(1.)
    outside=active&np.any((full<0)|(full>1),axis=-1)
    low=np.zeros_like(angle);high=np.ones_like(angle)
    for _ in range(24):
        mid=(low+high)/2
        p=rotated(mid)
        valid=np.all((p>=0)&(p<=1),axis=-1)
        low=np.where(valid,mid,low);high=np.where(valid,high,mid)
    fraction=np.where(outside,low,1.)
    output=rotated(fraction)
    assert np.isfinite(output).all() and output.min()>=0 and output.max()<=1
    modified=to_oklab(output)
    d_h=np.degrees(np.arctan2(modified[...,2],modified[...,1]))-hue
    d_h=(d_h+180)%360-180
    detail=dict(modified_nodes=int(active.sum()),total_nodes=int(active.size),
                gamut_limited_nodes=int(outside.sum()),
                max_hue_rotation_degrees=float(d_h[active].max()),
                max_lightness_change=float(np.abs(modified[...,0]-light).max()),
                max_chroma_change=float(np.abs(np.linalg.norm(modified[...,1:],axis=-1)-chroma).max()),
                max_rgb_change_codes=float(np.abs(output-rgb).max()*255))
    return Lut3D(lut.size,output.astype(np.float32),LABEL),detail


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    base_dir=ROOT/'looks'/BASE_KEY
    protected=[base_dir/n for n in ['source.cube','table.d4lut','table-stock3d.d4lut','look.json']]
    protected += [ROOT/'looks/catalog.json',ROOT/'lut-library/machine-catalog.json']
    before={str(p):digest(p) for p in protected}
    base=parse_lut_bytes((base_dir/'source.cube').read_bytes(),'source.cube')
    candidate,detail=refine(base)
    cube=OUT/'CC50-red-refinement-candidate33.cube'
    cube.write_bytes(cube_bytes(candidate))
    loaded=parse_lut_bytes(cube.read_bytes(),cube.name)
    assert loaded.size==33 and np.max(np.abs(loaded.table-candidate.table))<2e-7
    library=LookLibrary(OUT/'native')
    if not (library.directory(KEY)/'look.json').exists():
        library.add_cube(cube,KEY,label=LABEL,input_space='srgb',method='fit',
                         description='CXO50已定版基础；仅饱和偏洋红红色平滑转正红，最高2度；独立候选。')
    assert (library.directory(KEY)/'source.cube').read_bytes()==cube.read_bytes()
    native=(library.directory(KEY)/'table.d4lut').read_bytes()
    native_base=(base_dir/'table.d4lut').read_bytes()
    photos=inputs();records=[];rendered=[]
    for name,pixels,_ in photos:
        old=apply_lut(pixels,base);new=apply_lut(pixels,loaded)
        native_new=sample_table(native,pixels.reshape(-1,3)).reshape(pixels.shape)
        native_old=sample_table(native_base,pixels.reshape(-1,3)).reshape(pixels.shape)
        color_distance=np.linalg.norm(lab(new)-lab(old),axis=-1)
        records.append(dict(scene=name,mean_delta_E76=float(color_distance.mean()),
                            p99_delta_E76=float(np.percentile(color_distance,99)),
                            candidate_change_rgb_rmse_codes=float(np.sqrt(np.mean((new-old)**2))*255),
                            candidate_9grid_rmse_codes=float(np.sqrt(np.mean((native_new-new)**2))*255),
                            current_9grid_rmse_codes=float(np.sqrt(np.mean((native_old-old)**2))*255),
                            current=metrics(pixels,old,name),candidate=metrics(pixels,new,name)))
        rendered.append((name,pixels,old,new,native_old,native_new))
    previews=[]
    for start in range(0,len(rendered),6):
        group=rendered[start:start+6]
        path=OUT/f'comparison-{start//6+1:02d}.jpg'
        rows=[(name,[('原图',p),('现定版 CC50 · 母版',a),('红色细修候选 · 母版',b),('候选9³ · 桌面模型',bn)])
              for name,p,a,b,an,bn in group]
        grid('Classic Chrome · 独立红色细修候选','同一50%配方；色相局部细修；9³沿用正式fit算法。',rows,path,360,230)
        previews.append(path)
        path=OUT/f'native-comparison-{start//6+1:02d}.jpg'
        rows=[(name,[('原图',p),('现定版9³ · 桌面模型',an),('候选9³ · 桌面模型',bn)])
              for name,p,a,b,an,bn in group]
        grid('Classic Chrome · 两版9³对比','相同转换算法；仅比较色彩细修在9³后的表现。',rows,path,400,255)
        previews.append(path)
    path=OUT/'00-start-here.jpg'
    rows=[]
    for j in [0,2,10,13]:
        name,p,a,b,an,bn=rendered[j]
        rows.append((name,[('原图',p),('现定版 CC50',a),('红色细修候选',b),('候选9³ · 桌面模型',bn)]))
    grid('Classic Chrome · 人像与红色重点','原图 / CC50定版 / 独立色彩细修 / 候选9³；非算法改版。',rows,path,360,230)
    previews.append(path)
    gray=np.repeat(np.linspace(0,1,4097,dtype=np.float32)[:,None],3,axis=1)
    old_gray=apply_lut(gray[:,None,:],base)[:,0,:]
    new_gray=apply_lut(gray[:,None,:],loaded)[:,0,:]
    assert np.max(np.abs(new_gray-old_gray))<2e-7
    table=_resample(loaded,32);fingerprint,encoded,raw_size=_encode_table(table)
    group='NX300M 独立候选'
    xmp=OUT/'CC50-red-refinement-candidate.xmp'
    xmp.write_text(_xmp(LABEL,'CC50独立红色细修候选；Amount100为当前候选。',fingerprint,encoded,group_name=group),encoding='utf-8')
    profile=ProfileSource('1',LABEL,'sRGB',cube,KEY,digest(cube),loaded)
    lr=_verify_xmp(xmp,profile,table,fingerprint,raw_size,group_name=group)
    assert all(digest(Path(p))==h for p,h in before.items())
    report=dict(id=KEY,label=LABEL,status='standalone color candidate; not adopted',base_id=BASE_KEY,
                source_sha256=before[str(base_dir/'source.cube')],parameters=PARAMETERS,
                working_space='sRGB input/output; local hue rotation in OKLab D65',
                compiler='existing fit, unchanged; both direct and stock-composed 9^3 tables generated',
                cube_grid=33,lr_grid=32,native_grid=9,hardware_verified=False,lr_render_verified=False,
                source_and_catalogs_unchanged=True,gray_axis_unchanged=True,node_changes=detail,
                scenes=records,lightroom_check=lr,previews=[p.name for p in previews])
    (OUT/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (OUT/'recipe.json').write_text(json.dumps(dict(base=BASE_KEY,parameters=PARAMETERS),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    archive=OUT.parent.parent/'NX300M-CC-Color-Candidate.zip'
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for p in OUT.rglob('*'):
            if p.is_file() and not p.name.endswith(('.lock','.tmp')):
                z.write(p,p.relative_to(OUT).as_posix())
    with zipfile.ZipFile(archive) as z:assert z.testzip() is None
    print(json.dumps(dict(archive=str(archive),node_changes=detail,scenes=len(records),
                          worst_candidate_scene_9grid_rmse=max(e['candidate_9grid_rmse_codes'] for e in records),
                          worst_scene_mean_delta_E76=max(e['mean_delta_E76'] for e in records),
                          base_preserved=True),ensure_ascii=False),flush=True)


if __name__=='__main__':main()
