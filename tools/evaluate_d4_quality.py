"""All-look D4 compiler comparison against fetched upstream desktop C kernels."""
import argparse
import hashlib
import html
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageCms, ImageDraw, ImageFont

from cube_to_d4 import compile_cube, load_cube
from lut_resample import compile_table, sample_cube, sample_table, target, unpack
from look_library import LookLibrary
from look_preview import srgb_image
from stock_lut import factory_3d_lut
import compare_lut_backends as desktop

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/looks/d4-optimization'
SOURCES=[ROOT/'outputs/quickshare/photo-send-20261021T034223Z/SAM_4262.JPG',
         ROOT/'scratch/lut-verify/SAM_5283.JPG']
BACKENDS={'legacy':'原编译器 9³','round':'四舍五入 9³','fit':'全局拟合 9³',
          'perceptual':'感知联合拟合 9³',
          'integral':'连续积分拟合 9³',
          'host18':'new_nx300m 18³','host65':'new_nx300m 65³','nx300lut':'nx300-lut 原像素算法'}


def lab(rgb):
    linear=desktop.decode(np.clip(rgb,0,1))
    lms=linear@np.array([[.4122214708,.5363325363,.0514459929],
                        [.2119034982,.6806995451,.1073969566],
                        [.0883024619,.2817188376,.6299787005]]).T
    return np.cbrt(lms)@np.array([[.2104542553,.7936177850,-.0040720468],
                                [1.9779984951,-2.4285922050,.4505937099],
                                [.0259040371,.7827717662,-.8086757660]]).T


def metrics(x,goal,pred):
    error=pred-goal;absolute=np.abs(error)*255
    a,b=lab(goal),lab(pred);delta=b-a
    ac=np.linalg.norm(a[:,1:],axis=1);bc=np.linalg.norm(b[:,1:],axis=1)
    valid=(ac>.02)&(bc>.02)
    angle=np.abs(np.angle(np.exp(1j*(np.arctan2(b[:,2],b[:,1])-np.arctan2(a[:,2],a[:,1])))))*180/np.pi
    wanted=goal-x;actual=pred-x
    norm=float(np.sum(wanted*wanted));dot=float(np.sum(wanted*actual))
    return {'samples':len(x),'rgb_rmse':float(np.sqrt(np.mean(error**2))*255),
            'rgb_mae':float(absolute.mean()),'rgb_p95':float(np.quantile(absolute.mean(axis=1),.95)),
            'rgb_p99':float(np.quantile(absolute.mean(axis=1),.99)), 'rgb_channel_max':float(absolute.max()),
            'oklab_mean_x100':float(np.linalg.norm(delta,axis=1).mean()*100),
            'oklab_p95_x100':float(np.quantile(np.linalg.norm(delta,axis=1),.95)*100),
            'lightness_bias_x100':float(delta[:,0].mean()*100),
            'chroma_bias_x100':float((bc-ac).mean()*100),
            'hue_mean_degrees':float(angle[valid].mean()) if valid.any() else None,
            'hue_samples':int(valid.sum()),'signed_rgb_bias':(error.mean(axis=0)*255).tolist(),
            'source_effect_rms_codes':float(np.sqrt(np.mean(wanted**2))*255),
            'style_projection_gain':dot/norm if norm>1e-10 else None,
            'style_direction_cosine':dot/np.sqrt(norm*np.sum(actual*actual)) if norm>1e-10 and np.sum(actual*actual)>1e-10 else None}


def region_metrics(x,goal,pred):
    y=x@np.array([.2126,.7152,.0722])
    masks={'all':np.ones(len(x),bool),'shadow':y<.2,'highlight':y>.8,
           'near_gray':np.ptp(x,axis=1)<.04,
           'skin_like':(x[:,0]>x[:,1])&(x[:,1]>x[:,2])&(x[:,0]>.25)&(x[:,0]-x[:,2]<.55),
           'green':(x[:,1]>x[:,0]+.08)&(x[:,1]>x[:,2]+.04),
           'blue':(x[:,2]>x[:,0]+.12)&(x[:,2]>x[:,1]+.03)}
    return {name:metrics(x[m],goal[m],pred[m]) for name,m in masks.items() if m.any()}


def byte_output(x):
    return np.floor(np.clip(x,0,1)*255+.5)/255


def save_png(path,rgb,shape):
    array=np.floor(np.clip(rgb,0,1)*255+.5).astype(np.uint8).reshape(shape)
    Image.fromarray(array).save(path,icc_profile=ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes())


def write_index(report,output):
    data=json.dumps(report,ensure_ascii=False).replace('</','<\\/')
    rows=[]
    columns = [key for key in ('legacy', 'fit', 'integral', 'host18', 'host65', 'nx300lut')
               if all(key in r['rgb8_holdout'] for r in report['looks'])]
    headers = ''.join('<th>'+html.escape(BACKENDS[key])+'</th>' for key in columns)
    candidate = 'fit'
    for r in report['looks']:
        s=r['rgb8_holdout']
        vals=[s[k]['all']['rgb_rmse'] for k in columns]
        improvement = f'{(1-s[candidate]["all"]["rgb_rmse"]/vals[0])*100:.1f}%' if vals[0] else '—'
        rows.append('<tr><th>'+html.escape(r['label'])+'</th>'+''.join(f'<td>{v:.3f}</td>' for v in vals)+
                    f'<td>{improvement}</td></tr>')
    page='''<!doctype html><meta charset="utf-8"><title>NX300M 拍照 LUT 编译对照</title>
<style>body{font:15px system-ui;margin:24px;background:#141719;color:#eee}p{max-width:1100px;line-height:1.7}select{font:inherit;margin:8px;padding:7px}table{border-collapse:collapse}td,th{padding:7px 14px;border-bottom:1px solid #384048;text-align:right}th{text-align:left}.photos{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}img{width:100%;height:auto}figcaption{padding:10px 0}figure{margin:0}a{color:#b9d5ff}#summary{color:#bac3ca}@media(max-width:850px){.photos{grid-template-columns:repeat(2,minmax(0,1fr))}}</style>
<h1>拍照 LUT：同源、同图、100% 效果</h1>
<p>同一源 LUT、同一 RGB888 输入，图片统一转 sRGB 并保存 PNG。9³ 使用离线 RGB／三线性模型。包含主机后端时，运行对应原项目 C 像素代码。</p>
<label>风格<select id="look"></select></label><label>照片<select id="scene"></select></label><label>后端<select id="backend"></select></label>
<div class="photos"><figure><figcaption>原图</figcaption></figure><figure><figcaption>源 .cube 直接处理</figcaption></figure><figure><figcaption>原编译器 9³</figcaption></figure><figure><figcaption id="prediction-label"></figcaption></figure></div>
<p id="summary"></p><h2>独立 RGB888 样本：RMSE / 8-bit 通道单位</h2>
<table><thead><tr><th>风格</th>'''+headers+'''<th>9³ RMSE下降</th></tr></thead><tbody>'''+''.join(rows)+'''</tbody></table>
<p>实际相机 LUT 色彩域和插值尚未标定。这里统一假设 RGB code / 三线性模型，不代表真实 JPEG 色差；区域统计、灰阶、组合表和四面体敏感性见 <a href="report.json">完整数据</a>。此页不做 JPEG 再编码，因此没有把二次压缩误差归到 LUT 算法。</p>
<script>const R='''+data+''';const B='''+json.dumps(BACKENDS,ensure_ascii=False)+''';
const L=document.querySelector('#look'),S=document.querySelector('#scene'),E=document.querySelector('#backend');
R.looks.forEach((r,i)=>L.add(new Option(r.label,i)));R.sources.forEach((r,i)=>S.add(new Option(r.name,i)));Object.entries(B).filter(([k])=>k in R.looks[0].rgb8_holdout).forEach(([k,v])=>E.add(new Option(v,k)));E.value='fit';
function show(){const r=R.looks[+L.value],s=r.scenes[+S.value],k=E.value,base='images/'+r.id+'/'+S.value+'-';document.querySelector('#original').src=base+'original.png';document.querySelector('#target').src=base+'target.png';document.querySelector('#baseline').src=base+'legacy.png';document.querySelector('#prediction').src=base+k+'.png';document.querySelector('#prediction-label').textContent=B[k];const m=s.backends[k];document.querySelector('#summary').textContent='照片 RGB RMSE '+m.rgb_rmse.toFixed(3)+'；平均 OKLab 距离×100 '+m.oklab_mean_x100.toFixed(3)+'；风格方向投影 '+(m.style_projection_gain*100).toFixed(1)+'%。投影衡量沿源 LUT 改变方向的分量。'}[L,S,E].forEach(x=>x.onchange=show);show();</script>'''
    (output/'index.html').write_text(page,encoding='utf-8')


def transitions(cube, data):
    t = np.linspace(0, 1, 1025)
    ramps = [np.repeat(t[:, None], 3, axis=1)]
    for axis in range(3):
        for c0, c1 in ((0., 0.), (0., 1.), (1., 0.), (1., 1.)):
            path = np.empty((len(t), 3))
            path[:, axis] = t
            path[:, [c for c in range(3) if c != axis]] = (c0, c1)
            ramps.append(path)
    x = np.stack(ramps)
    goal = sample_cube(cube, x)
    predicted = sample_table(data, x)
    residual = predicted-goal
    step_error = np.abs(np.diff(residual, axis=1))*255
    curvature = np.abs(np.diff(residual, n=2, axis=1))*255
    gray = np.floor(predicted[0]*255+.5).astype(np.uint8)
    cuts = np.flatnonzero(np.any(gray[1:] != gray[:-1], axis=1))+1
    flat = np.diff(np.r_[0, cuts, len(gray)])
    source_gray = np.floor(goal[0]*255+.5).astype(np.uint8)
    source_cuts = np.flatnonzero(np.any(source_gray[1:] != source_gray[:-1], axis=1))+1
    source_flat = np.diff(np.r_[0, source_cuts, len(source_gray)])
    luma = predicted[0]@np.array([.2126, .7152, .0722])
    source_luma = goal[0]@np.array([.2126, .7152, .0722])
    return {'ramps': 13, 'samples_per_ramp': len(t),
            'step_error_p99_codes': float(np.quantile(step_error, .99)),
            'step_error_max_codes': float(step_error.max()),
            'curvature_error_p99_codes': float(np.quantile(curvature, .99)),
            'curvature_error_max_codes': float(curvature.max()),
            'gray_rgb8_distinct': int(len(np.unique(gray, axis=0))),
            'gray_rgb8_longest_flat_run': int(flat.max()),
            'source_gray_rgb8_distinct': int(len(np.unique(source_gray, axis=0))),
            'source_gray_rgb8_longest_flat_run': int(source_flat.max()),
            'gray_code_luma_max_backtrack': float(np.max(np.maximum.accumulate(luma)-luma)*255),
            'source_gray_code_luma_max_backtrack': float(np.max(np.maximum.accumulate(source_luma)-source_luma)*255),
            'scope': 'RGB-code software ramps; not a hardware banding measurement'}


def write_review_sheets(report, output):
    keys = ('nx300-fuji-classic-negative', 'ricoh-positive-exact',
            'nx300-fuji-classic-chrome', 'nx300-fuji-eterna')
    rows = [row for key in keys for row in report['looks'] if row['id'] == key]
    names = [('original', '原图'), ('target', '源 LUT 33³'), ('legacy', '原 9³'),
             ('fit', '全局拟合 9³'), ('perceptual', '感知联合 9³')]
    if all('integral' in row['rgb8_holdout'] for row in rows):
        names.append(('integral', '连续积分 9³'))
    font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 18)
    for scene in range(len(report['sources'])):
        width, height = 320, 280
        canvas = Image.new('RGB', (width*len(names), height*len(rows)), '#181b1e')
        draw = ImageDraw.Draw(canvas)
        for row_index, row in enumerate(rows):
            for column, (key, label) in enumerate(names):
                path = output/'images'/row['id']/f'{scene}-{key}.png'
                if not path.exists():
                    continue
                with Image.open(path) as opened:
                    im = opened.copy()
                im.thumbnail((width-10, height-40))
                x, y = column*width, row_index*height
                draw.text((x+5, y+4), row['label']+' / '+label, font=font, fill='white')
                canvas.paste(im, (x+(width-im.width)//2, y+34))
        canvas.save(output/f'comparison-{scene}.png',
                    icc_profile=ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes())


def extend(output, keys=None, refresh_fit=False, candidate='fit-perceptual'):
    """Add a new candidate to the saved run without rerunning passing kernels."""
    output = Path(output)
    report = json.loads((output/'report.json').read_text(encoding='utf-8'))
    rng = np.random.default_rng(40719)
    continuous = rng.random((24000, 3))
    colors = np.floor(rng.random((16000, 3))*256).astype(np.uint8)
    gray = np.repeat(np.arange(256, dtype=np.uint8)[:, None], 3, axis=1)
    rgb8 = np.concatenate([colors, gray]); hold = rgb8.astype(float)/255
    photos = []
    for path, recorded in zip(SOURCES, report['sources']):
        if hashlib.sha256(path.read_bytes()).hexdigest() != recorded['sha256']:
            raise ValueError('Evaluation photograph changed: '+path.name)
        im, _, _ = srgb_image(path, 480)
        photos.append(np.asarray(im).reshape(-1, 3).astype(np.uint8))
    pixels = np.concatenate([rgb8, *photos]); inputs = pixels.astype(float)/255
    stock_words = factory_3d_lut()
    stock = np.array(stock_words).reshape(3, 730)[:, :729].T/1023
    library = LookLibrary()
    for row in report['looks']:
        key = row['id']; directory = library.directory(key)
        if keys and key not in keys:
            continue
        source = directory/'source.cube'
        if hashlib.sha256(source.read_bytes()).hexdigest() != row['source_sha256']:
            raise ValueError('Evaluation LUT changed: '+key)
        cube = load_cube(source); destination = output/'profiles'/key
        goal = sample_cube(cube, continuous)
        goals = byte_output(sample_cube(cube, inputs))
        methods = [('fit', 'fit')] if refresh_fit else []
        candidate_name = {'fit-perceptual': 'perceptual', 'fit-integral': 'integral'}[candidate]
        methods.append((candidate_name, candidate))
        for name, method in methods:
            data, detail = compile_table(cube, method=method)
            (destination/f'{method}.d4lut').write_bytes(data)
            row['compiler' if name == 'fit' else name+'_compiler'] = detail
            row['continuous_holdout'][name] = region_metrics(continuous, goal, sample_table(data, continuous))
            row['tetrahedral_sensitivity'][name] = metrics(
                continuous, goal, sample_table(data, continuous, interpolation='tetrahedral'))
            predictions = byte_output(sample_table(data, inputs))
            row['rgb8_holdout'][name] = region_metrics(hold, goals[:len(hold)], predictions[:len(hold)])
            row['gray'][name] = metrics(hold[-256:], goals[len(hold)-256:len(hold)], predictions[len(hold)-256:len(hold)])
            at = len(hold)
            for i, photo in enumerate(photos):
                end = at+len(photo)
                save_png(output/'images'/key/f'{i}-{name}.png', predictions[at:end], report['sources'][i]['shape'])
                row['scenes'][i]['backends'][name] = metrics(photo.astype(float)/255, goals[at:end], predictions[at:end])
                at = end
            if 'composed' in row:
                composed, composed_detail = compile_table(cube, stock_words, method=method)
                (destination/f'{method}-stock3d.d4lut').write_bytes(composed)
                row['composed']['compiler' if name == 'fit' else name+'_compiler'] = composed_detail
                row['composed']['after' if name == 'fit' else name] = region_metrics(
                    continuous, target(cube, continuous, stock), sample_table(composed, continuous))
        (destination/'analysis.json').write_text(json.dumps(row, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        # Persist each completed look, so an interrupted run retains its data.
        (output/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        print(json.dumps({'look': key, 'candidate': candidate_name,
                          'rgb_rmse': row['rgb8_holdout'][candidate_name]['all']['rgb_rmse'],
                          'oklab': row['rgb8_holdout'][candidate_name]['all']['oklab_mean_x100']}, ensure_ascii=False), flush=True)
    for row in report['looks']:
        cube = load_cube(library.directory(row['id'])/'source.cube')
        destination = output/'profiles'/row['id']
        row['transitions'] = {name: transitions(cube, (destination/f'{method}.d4lut').read_bytes())
                             for name, method in [('legacy', 'legacy'), ('round', 'round'),
                                                  ('fit', 'fit'), ('perceptual', 'fit-perceptual'),
                                                  ('integral', 'fit-integral')]
                             if (destination/f'{method}.d4lut').exists()}
        (destination/'analysis.json').write_text(json.dumps(row, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    for metric in ('rgb_rmse', 'oklab_mean_x100'):
        old = np.array([row['continuous_holdout']['legacy']['all'][metric] for row in report['looks']])
        new = np.array([row['continuous_holdout']['fit']['all'][metric] for row in report['looks']])
        report['summary'][metric] = {
            'macro_before': float(old.mean()), 'macro_after': float(new.mean()),
            'relative_improvement_percent': float((1-new.mean()/old.mean())*100),
            'regressions': [row['id'] for row, a, b in zip(report['looks'], old, new) if b > a+1e-9]}
    report['summary']['rgb8_backends'] = {
        name: {metric: float(np.mean([row['rgb8_holdout'][name]['all'][metric] for row in report['looks']]))
               for metric in ('rgb_rmse', 'oklab_mean_x100', 'rgb_p95', 'style_projection_gain')}
        for name in BACKENDS if all(name in row['rgb8_holdout'] for row in report['looks'])}
    report['summary']['perceptual_vs_rgb_fit'] = {
        metric: {'macro_rgb_fit': float(np.mean([row['rgb8_holdout']['fit']['all'][metric] for row in report['looks']])),
                 'macro_perceptual_fit': float(np.mean([row['rgb8_holdout']['perceptual']['all'][metric] for row in report['looks']])),
                 'regressions': [row['id'] for row in report['looks'] if row['rgb8_holdout']['perceptual']['all'][metric] > row['rgb8_holdout']['fit']['all'][metric]+1e-9]}
        for metric in ('rgb_rmse', 'oklab_mean_x100')}
    if all('integral' in row['rgb8_holdout'] for row in report['looks']):
        report['summary']['integral_vs_rgb_fit'] = {
            metric: {'macro_rgb_fit': float(np.mean([row['rgb8_holdout']['fit']['all'][metric] for row in report['looks']])),
                     'macro_integral': float(np.mean([row['rgb8_holdout']['integral']['all'][metric] for row in report['looks']])),
                     'regressions': [row['id'] for row in report['looks'] if row['rgb8_holdout']['integral']['all'][metric] > row['rgb8_holdout']['fit']['all'][metric]+1e-9]}
            for metric in ('rgb_rmse', 'oklab_mean_x100')}
    (output/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    write_index(report, output)
    write_review_sheets(report, output)
    return report


def run(output,keys=None):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    host=output/'host-build';desktop.build(host)
    library=LookLibrary();looks=library.list()
    if keys:looks=[m for m in looks if m['id'] in keys]
    rng=np.random.default_rng(40719)
    continuous=rng.random((24000,3))
    colors=np.floor(rng.random((16000,3))*256).astype(np.uint8)
    gray=np.repeat(np.arange(256,dtype=np.uint8)[:,None],3,axis=1)
    rgb8=np.concatenate([colors,gray]);hold=rgb8.astype(float)/255
    photos=[];sources=[]
    for p in SOURCES:
        im,scope,color=srgb_image(p,480)
        photos.append(np.asarray(im).reshape(-1,3).astype(np.uint8))
        sources.append({'name':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),
                        'shape':[im.height,im.width,3],'scope':scope,'color':color})
    all_pixels=np.concatenate([rgb8,*photos])
    stock_words=factory_3d_lut();stock=np.array(stock_words).reshape(3,730)[:,:729].T/1023
    report={'schema':1,'seed':40719,'fit_training_seed':61183,
            'model':'RGB code, trilinear; native color coordinates and hardware interpolation not measured',
            'strength':1.,'default_compiler_changed':False,'camera_runtime_changed':False,
            'photos_used_for_training':False,'sources':sources,'looks':[]}
    for meta in looks:
        key=meta['id'];directory=library.directory(key);source=directory/meta['master'];cube=load_cube(source)
        result=output/'profiles'/key;result.mkdir(parents=True,exist_ok=True)
        before=compile_cube(cube)
        rounded,round_detail=compile_table(cube,method='round')
        after,detail=compile_table(cube,method='fit')
        variants={'legacy':before,'round':rounded,'fit':after}
        for name,data in variants.items():(result/f'{name}.d4lut').write_bytes(data)
        row={'id':key,'label':meta['label'],'declared_input_space':meta['input_space'],
             'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
             'source_grid':cube[0],'compiler':detail,'round_compiler':round_detail,
             'stored_table_equals_legacy':(directory/'table.d4lut').read_bytes()==before,
             'continuous_holdout':{},'rgb8_holdout':{},'scenes':[]}
        goal=sample_cube(cube,continuous)
        for name,data in variants.items():
            row['continuous_holdout'][name]=region_metrics(continuous,goal,sample_table(data,continuous))
        row['tetrahedral_sensitivity']={name:metrics(continuous,goal,sample_table(data,continuous,interpolation='tetrahedral'))
                                        for name,data in variants.items()}
        row['source_interpolation_sensitivity']=metrics(continuous,goal,sample_cube(cube,continuous,interpolation='tetrahedral'))
        native_composed=directory/'table-stock3d.d4lut'
        if native_composed.exists():
            old=compile_cube(cube,stock_words)
            fitted,composed_detail=compile_table(cube,stock_words,method='fit')
            (result/'fit-stock3d.d4lut').write_bytes(fitted)
            composed_goal=target(cube,continuous,stock)
            row['composed']={'compiler':composed_detail,'stored_equals_legacy':native_composed.read_bytes()==old,
                'before':region_metrics(continuous,composed_goal,sample_table(old,continuous)),
                'after':region_metrics(continuous,composed_goal,sample_table(fitted,continuous))}
        outputs=desktop.compare(host,result/'desktop',source,cube,all_pixels)
        inputs=all_pixels.astype(float)/255
        outputs.update({name:byte_output(sample_table(data,inputs)) for name,data in variants.items()})
        goals=byte_output(sample_cube(cube,inputs))
        for name,pred in outputs.items():
            row['rgb8_holdout'][name]=region_metrics(hold,goals[:len(hold)],pred[:len(hold)])
        row['gray']={name:metrics(hold[-256:],goals[len(hold)-256:len(hold)],pred[len(hold)-256:len(hold)])
                     for name,pred in outputs.items()}
        at=len(hold)
        for i,(pixels,spec) in enumerate(zip(photos,sources)):
            end=at+len(pixels);x=pixels.astype(float)/255
            image_dir=output/'images'/key;image_dir.mkdir(parents=True,exist_ok=True)
            save_png(image_dir/f'{i}-original.png',x,spec['shape'])
            save_png(image_dir/f'{i}-target.png',goals[at:end],spec['shape'])
            scene={'name':spec['name'],'backends':{}}
            for name,pred in outputs.items():
                save_png(image_dir/f'{i}-{name}.png',pred[at:end],spec['shape'])
                scene['backends'][name]=metrics(x,goals[at:end],pred[at:end])
            row['scenes'].append(scene);at=end
        (result/'analysis.json').write_text(json.dumps(row,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        report['looks'].append(row)
        print(json.dumps({'look':key,'rmse_before':row['continuous_holdout']['legacy']['all']['rgb_rmse'],
            'rmse_after':row['continuous_holdout']['fit']['all']['rgb_rmse'],
            'oklab_before':row['continuous_holdout']['legacy']['all']['oklab_mean_x100'],
            'oklab_after':row['continuous_holdout']['fit']['all']['oklab_mean_x100'],
            'host18':row['rgb8_holdout']['host18']['all']['rgb_rmse'],
            'host65':row['rgb8_holdout']['host65']['all']['rgb_rmse']},ensure_ascii=False),flush=True)
    summary={}
    for metric in ('rgb_rmse','oklab_mean_x100'):
        old=np.array([r['continuous_holdout']['legacy']['all'][metric] for r in report['looks']])
        new=np.array([r['continuous_holdout']['fit']['all'][metric] for r in report['looks']])
        summary[metric]={'macro_before':float(old.mean()),'macro_after':float(new.mean()),
                         'relative_improvement_percent':float((1-new.mean()/old.mean())*100),
                         'regressions':[r['id'] for r,a,b in zip(report['looks'],old,new) if b>a+1e-9]}
    report['summary']=summary
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    write_index(report,output)
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,default=OUT)
    p.add_argument('--looks',nargs='*');p.add_argument('--extend',action='store_true')
    p.add_argument('--refresh-fit',action='store_true')
    p.add_argument('--candidate',choices=('fit-perceptual','fit-integral'),default='fit-perceptual')
    args=p.parse_args()
    if args.refresh_fit and not args.extend:
        p.error('--refresh-fit requires --extend')
    r=extend(args.output,args.looks,args.refresh_fit,args.candidate) if args.extend else run(args.output,args.looks)
    print(json.dumps(r['summary'],ensure_ascii=False,indent=2))
