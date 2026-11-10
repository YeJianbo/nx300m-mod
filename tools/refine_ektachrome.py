"""Review a small Ektachrome refinement against the approved 85% master."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import zipfile

import numpy as np
from PIL import Image, ImageCms, ImageDraw, ImageFont, ImageOps

from audit_camera_blend_ratios import lab, metrics, pictures
from build_lumix_lightroom_profiles import ProfileSource, _encode_table, _resample, _verify_xmp, _xmp
from look_library import LookLibrary
from lut_resample import sample_table
from lut_table_data import read_tables
from manage_local_lut_library import sync
from nx300m_lut_core import Lut3D, apply_lut, cube_bytes, parse_lut_file
from render_cxo_ratio_directions import ROOT, image_of, save_srgb


OUT = ROOT / 'outputs/looks/camera-curation/ektachrome-refinement'
BASE = ROOT / 'looks/blend-metol-ektachrome-cxo85-mild/source.cube'
KEY = 'blend-metol-ektachrome-cxo85-refined'
LABEL = 'Ektachrome 100 · CXO 85% 细修'
PARAMETERS = dict(warm_a_offset=-.0025, warm_b_offset=.0007,
                  shadow_L_offset=-.0045, upper_mid_L_offset=.001,
                  cyan_chroma_reduction=.025)
LOCAL_SCENES = [(43,'逆光绿叶'),(52,'夕阳高反差'),(58,'暮色云层'),(60,'夜景灯光'),
                (64,'红果与绿叶'),(78,'江面蓝色'),(91,'街头人物与红衣'),(92,'高饱和红叶'),
                (100,'阴天天空与建筑'),(113,'红屋顶与绿树'),(122,'暗背景白鹭'),(41,'粉紫花卉')]
M1 = np.array([[.4122214708,.5363325363,.0514459929],
               [.2119034982,.6806995451,.1073969566],
               [.0883024619,.2817188376,.6299787005]])
M2 = np.array([[.2104542553,.7936177850,-.0040720468],
               [1.9779984951,-2.4285922050,.4505937099],
               [.0259040371,.7827717662,-.8086757660]])


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ramp(x, lo, hi):
    t = np.clip((x-lo)/(hi-lo), 0, 1)
    return t*t*(3-2*t)


def bump(x, lo, hi):
    t = np.clip((x-lo)/(hi-lo), 0, 1)
    return 16*t*t*(1-t)*(1-t)


def refine(lut):
    rgb = lut.table.astype(np.float64)
    linear = np.where(rgb <= .04045, rgb/12.92, ((rgb+.055)/1.055)**2.4)
    value = np.cbrt(linear @ M1.T) @ M2.T
    light, a, b = np.moveaxis(value, -1, 0)
    chroma = np.hypot(a,b)
    hue = np.degrees(np.arctan2(b,a))
    warm = (np.exp(-.5*((hue-40)/24)**2)
            * ramp(chroma,.015,.035)*(1-ramp(chroma,.12,.18))
            * ramp(light,.25,.45)*(1-ramp(light,.87,.98)))
    cyan_distance = (hue-218+180)%360-180
    cyan = np.exp(-.5*(cyan_distance/13)**2)*ramp(chroma,.02,.05)
    value[...,0] += (PARAMETERS['shadow_L_offset']*bump(light,.10,.62)
                     + PARAMETERS['upper_mid_L_offset']*bump(light,.50,.95))
    scale = 1-PARAMETERS['cyan_chroma_reduction']*cyan
    value[...,1] = a*scale+PARAMETERS['warm_a_offset']*warm
    value[...,2] = b*scale+PARAMETERS['warm_b_offset']*warm
    linear = ((value @ np.linalg.inv(M2).T)**3) @ np.linalg.inv(M1).T
    output = np.where(linear <= .0031308, 12.92*linear,
                      1.055*np.maximum(linear,0)**(1/2.4)-.055)
    if np.any(output < 0) or np.any(output > 1) or not np.isfinite(output).all():
        raise ValueError('Refinement produced out-of-gamut/nonfinite master nodes')
    return Lut3D(lut.size, output.astype(np.float32), LABEL)


def decode(path):
    with Image.open(path) as source:
        exif=source.getexif()
        model=str(exif.get(272,'')).strip()
        software=str(exif.get(305,'')).strip()
        if model != 'NX300M' or software != '1.15':
            raise ValueError('Selected source is not the checked camera JPEG: '+str(path))
        image=ImageOps.exif_transpose(source).convert('RGB')
        if source.info.get('icc_profile'):
            image=ImageCms.profileToProfile(image,ImageCms.ImageCmsProfile(io.BytesIO(source.info['icc_profile'])),
                                            ImageCms.createProfile('sRGB'),outputMode='RGB')
        elif exif.get_ifd(34665).get(40961) != 1:
            raise ValueError('Camera JPEG does not identify sRGB: '+str(path))
    image.thumbnail((1000,800),Image.Resampling.LANCZOS)
    return np.asarray(image,dtype=np.float32)/255, dict(
        decoder='original camera JPEG, EXIF orientation and sRGB color management; identical pixels for all LUTs',
        camera_model=model,software=software)


def render(photos, base, refined, path, title, original_style=None, cxo=None):
    width,height,margin,header,row_height = 350,225,26,106,284
    canvas = Image.new('RGB',(margin*2+width*4,header+row_height*len(photos)),'#11161b')
    draw=ImageDraw.Draw(canvas)
    font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',17)
    title_font=ImageFont.truetype('C:/Windows/Fonts/msyhbd.ttc',27)
    draw.text((margin,14),title,font=title_font,fill='#f5f1e8')
    subtitle=('原图 / 原机内风格 / CXO 100% / 85%细修定版'
              if original_style is not None else '原图 / 85%微柔基础 / 细修定版 / 差异放大×6（中灰为无变化）')
    draw.text((margin,57),subtitle,font=font,fill='#aeb8c2')
    for row,(name,pixels) in enumerate(photos):
        top=header+row*row_height
        draw.text((margin,top-24),name,font=font,fill='#d9b86a')
        old,new=apply_lut(pixels,base),apply_lut(pixels,refined)
        variants=[('原图',pixels),('现版 85% · 反差/饱和0.96',old),
                  ('85% · 暖色/暗部/青色细修定版',new),
                  ('差异×6，仅辅助辨认改动',np.clip(.5+6*(new-old),0,1))]
        if original_style is not None:
            variants=[('原图',pixels),('原机内 Ektachrome',apply_lut(pixels,original_style)),
                      ('CXO 100%',apply_lut(pixels,cxo)),('原版15% + CXO85% · 细修定版',new)]
        for col,(label,array) in enumerate(variants):
            left=margin+col*width
            canvas.paste(image_of(array,(width-5,height)),(left,top))
            draw.text((left+5,top+height+7),label,font=font,fill='#f5f1e8')
    save_srgb(canvas,path)


def main():
    OUT.mkdir(exist_ok=True)
    baseline=parse_lut_file(BASE).lut
    refined=refine(baseline)
    cube=OUT/(KEY+'.cube');cube.write_bytes(cube_bytes(refined))
    reloaded=parse_lut_file(cube).lut
    round_trip=float(np.max(np.abs(reloaded.table-refined.table)))
    if round_trip > 2e-7:
        raise ValueError('Refined CUBE round trip failed')
    contacts=json.loads((OUT/'local-contact-sources.json').read_text(encoding='utf-8'))
    jpeg_paths=[p for folder in ('jpg','SRW') for p in (Path('F:/Pictures')/folder).rglob('*')
                if p.suffix.lower() in ('.jpg','.jpeg')]
    chosen=[]
    for i,label in LOCAL_SCENES:
        raw=Path(contacts[i]);stem=raw.stem.split('-')[0]
        matches=[p for p in jpeg_paths if p.stem==stem]
        if len(matches)!=1:
            raise ValueError('Expected exactly one original JPEG counterpart: '+stem)
        chosen.append(dict(path=str(matches[0]),raw_counterpart=str(raw),label=label,contact_index=i))
    (OUT/'local-selection.json').write_text(json.dumps(chosen,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    local,provenance=[],[]
    cache=OUT/'local-inputs';cache.mkdir(exist_ok=True)
    for index,entry in enumerate(chosen):
        path=Path(entry['path'])
        pixels,decoder=decode(path)
        original=cache/(f'{index:02d}-'+path.stem+'.png')
        Image.fromarray(np.rint(pixels*255).astype(np.uint8)).save(original)
        local.append((entry['label']+' · '+path.stem.split('-')[0],pixels))
        provenance.append(dict(**entry,**decoder,input_pixels=original.relative_to(ROOT).as_posix()))
        print('decoded '+path.name,flush=True)
    existing=pictures()
    render(existing,baseline,reloaded,OUT/'six-scenes.jpg','Ektachrome 85% · 原六组微调对比')
    source_recipe=json.loads((BASE.parent/'recipe.json').read_text(encoding='utf-8'))
    original_style=parse_lut_file(ROOT/source_recipe['source_a']).lut
    cxo=parse_lut_file(ROOT/source_recipe['source_b']).lut
    for start in range(0,len(local),6):
        render(local[start:start+6],baseline,reloaded,OUT/f'local-scenes-{start//6+1}.jpg',
               'Ektachrome 85% · F盘本地直出JPEG比较',original_style,cxo)
        render(local[start:start+6],baseline,reloaded,OUT/f'local-refinement-{start//6+1}.jpg',
               'Ektachrome 85% · F盘本地现版与细修比较')
    library=LookLibrary()
    if not (library.directory(KEY)/'look.json').exists():
        library.add_cube(cube,KEY,label=LABEL,input_space='srgb',
                         description='原版15%+CXO85%，反差/饱和0.96；暖色、暗部、青色细修定版。',method='fit')
    if np.max(np.abs(parse_lut_file(library.directory(KEY)/'source.cube').lut.table-refined.table)) > 2e-7:
        raise ValueError('Published candidate differs from generated master')
    recipe=dict(id=KEY,base_cube=BASE.relative_to(ROOT).as_posix(),base_sha256=sha(BASE),
                parameters=PARAMETERS,space='OKLab D65',
                scope='continuous color/luminance selection; no semantic face detection',
                base_mix='15% existing metol Ektachrome + 85% CXO; saturation=.96; contrast=.96')
    (library.directory(KEY)/'recipe.json').write_text(json.dumps(recipe,ensure_ascii=False,indent=2),encoding='utf-8')
    with library._lock(KEY):
        metadata=library.get(KEY)
        metadata.update(source_kind='blend-srgb-refined',source_grid=33,recipe='recipe.json')
        library._save(KEY,metadata)
    tables=read_tables(library.directory(KEY),library.get(KEY))
    base_tables=read_tables(BASE.parent,library.get(BASE.parent.name))
    table=_resample(reloaded,32)
    fingerprint,encoded,raw_size=_encode_table(table)
    xmp=OUT/(LABEL+'.xmp')
    xmp.write_text(_xmp(LABEL,'原版15%+CXO85%，反差/饱和0.96；暖色/暗部/青色细修定版。',
                        fingerprint,encoded,group_name='NX300M 精选配方'),encoding='utf-8')
    source=ProfileSource('1',LABEL,'sRGB',cube,KEY,sha(cube),reloaded)
    lr_check=_verify_xmp(xmp,source,table,fingerprint,raw_size,group_name='NX300M 精选配方')
    checks=[]
    for name,pixels in existing+local:
        old,new=apply_lut(pixels,baseline),apply_lut(pixels,reloaded)
        points=pixels.reshape(-1,3)
        native_old=sample_table(base_tables['table.d4lut'],points,interpolation='trilinear').reshape(pixels.shape)
        native_new=sample_table(tables['table.d4lut'],points,interpolation='trilinear').reshape(pixels.shape)
        delta=lab(new)-lab(old)
        checks.append(dict(scene=name,baseline=metrics(pixels,old,name),refined=metrics(pixels,new,name),
                           delta_E76_mean=float(np.mean(np.linalg.norm(delta,axis=-1))),
                           delta_E76_p99=float(np.percentile(np.linalg.norm(delta,axis=-1),99)),
                           native_rgb_rmse_8bit=float(np.sqrt(np.mean((native_new-new)**2))*255),
                           native_refinement_rms_8bit=float(np.sqrt(np.mean((native_new-native_old)**2))*255)))
    # Neutral input ramps also exercise the complete creative LUT, not just the finish.
    grey=np.repeat(np.linspace(0,1,4097,dtype=np.float32)[:,None],3,axis=1)
    ramp_output=apply_lut(grey[:,None,:],reloaded)[:,0,:]
    light=lab(ramp_output)[...,0]
    if np.any(np.diff(light)<-1e-5):
        raise ValueError('Refinement has a reversing neutral luminance ramp')
    sync_result=sync(compile_camera=False)
    if sync_result['preserved_edits'] or sync_result['camera']['errors']:
        raise ValueError('Local LUT sync failed')
    machine=json.loads((ROOT/'lut-library/machine-catalog.json').read_text(encoding='utf-8'))
    if KEY not in machine['ids'] or BASE.parent.name in machine['ids']:
        raise ValueError('Approved refined Ektachrome is not selected')
    report=dict(schema=1,recipe=recipe,local_sources=provenance,
                selection='user-approved refined 85% is the selected Ektachrome version',
                cube_grid=33,lightroom_grid=32,native_grid=9,round_trip_error=round_trip,
                grey_luminance_nondecreasing=True,camera_runtime_verified=False,
                lightroom_runtime_verified=False,lightroom_check=lr_check,scenes=checks)
    (OUT/'refinement-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    bundle=OUT/'Ektachrome-85-Final.zip'
    files=[(cube,'CUBE33/'+cube.name),(xmp,'Lightroom/'+xmp.name),
           (OUT/'refinement-verification.json','refinement-verification.json'),
           (ROOT/'mod/nxconnect/third_party/cxo-filmpack/LICENSE','licenses/CXO-FilmPack-LICENSE.txt')]
    files += [(p,'camera/looks/'+KEY+'/'+p.relative_to(library.directory(KEY)).as_posix())
              for p in library.directory(KEY).rglob('*') if p.is_file()]
    files += [(p,'previews/'+p.name) for p in OUT.glob('*.jpg') if p.name=='six-scenes.jpg'
              or p.name.startswith(('local-scenes-','local-refinement-'))]
    with zipfile.ZipFile(bundle,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for p,name in files:
            archive.write(p,name)
    with zipfile.ZipFile(bundle) as archive:
        if archive.testzip() is not None or any(archive.read(name)!=p.read_bytes() for p,name in files):
            raise ValueError('Final ZIP differs from checked files')
    print(json.dumps(dict(local_camera_JPEGs=len(local),comparison_scenes=len(checks),
                          mean_delta_E76=max(s['delta_E76_mean'] for s in checks),
                          refined_selected=True,round_trip_error=round_trip)),flush=True)


if __name__=='__main__':
    main()
