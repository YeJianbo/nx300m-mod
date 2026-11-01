"""All-library IPCM fit, colour diagnosis and four-way offline comparison.

No photos are training data. All profiles use the same algorithm and selection
limits; the runtime ABI, IPCS and RAW are untouched.
"""
import argparse
import hashlib
import html
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageCms, ImageDraw, ImageFont

from fit_ipcm_preview import (ROOT, PROFILES, apply_profile, fit_model, load_cube,
                              read_profile, sample_cube, write_profile)
from ipcm_color_model import ColorModel
from ipcm_preview_fit import forward, from_model, perceptual_features
from ipcm_preview_tonal import fit_tonal
from look_library import LookLibrary
from look_preview import srgb_image

OUT=ROOT/'outputs/deployment/ipcm-preview/evaluation/tonal'
STRENGTHS=(.25,.5,.75,1.)
SOURCES=[ROOT/'outputs/quickshare/photo-send-20261021T034223Z/SAM_4262.JPG',ROOT/'scratch/lut-verify/SAM_5283.JPG']
OPPONENT=np.array([[1.,-1.,0.],[0.,-1.,1.]])


def lab(x):
    return perceptual_features(x)[:,3:]/np.array([1.,2.,2.])


def metrics(target,output):
    e=output-target; absolute=np.abs(e)*255
    tl,pl=lab(target),lab(output);delta=pl-tl
    tc=np.linalg.norm(tl[:,1:],axis=1);pc=np.linalg.norm(pl[:,1:],axis=1)
    tmax=np.max(target,axis=1);pmax=np.max(output,axis=1)
    tsat=np.divide(np.ptp(target,axis=1),tmax,out=np.zeros_like(tmax),where=tmax>1e-9)
    psat=np.divide(np.ptp(output,axis=1),pmax,out=np.zeros_like(pmax),where=pmax>1e-9)
    tspan=float(np.diff(np.quantile(tl[:,0],[.1,.9]))[0])
    pspan=float(np.diff(np.quantile(pl[:,0],[.1,.9]))[0])
    mask=(tc>.03)&(pc>.03)
    angle=np.abs(np.angle(np.exp(1j*(np.arctan2(pl[:,2],pl[:,1])-np.arctan2(tl[:,2],tl[:,1])))))*180/np.pi
    return {'samples':len(target),'rgb_mae':float(absolute.mean()),'rgb_p95':float(np.quantile(absolute.mean(axis=1),.95)),
            'rgb_rmse':float(np.sqrt(np.mean(absolute**2))),'oklab_mean_x100':float(np.mean(np.linalg.norm(delta,axis=1))*100),
            'oklab_p95_x100':float(np.quantile(np.linalg.norm(delta,axis=1),.95)*100),
            'lightness_signed_x100':float(delta[:,0].mean()*100),'lightness_mae_x100':float(np.abs(delta[:,0]).mean()*100),
            'chroma_signed_x100':float((pc-tc).mean()*100),'chroma_mae_x100':float(np.abs(pc-tc).mean()*100),
            'hsv_saturation_signed_percent':float((psat-tsat).mean()*100),
            'hsv_saturation_mae_percent':float(np.abs(psat-tsat).mean()*100),
            'lightness_p10_p90_span_ratio':pspan/tspan if tspan>1e-6 else None,
            'hue_mean_degrees':float(angle[mask].mean()) if mask.any() else None,
            'hue_p95_degrees':float(np.quantile(angle[mask],.95)) if mask.any() else None,
            'hue_valid_samples':int(mask.sum()),'target_colored_prediction_achromatic':int(((tc>.03)&(pc<=.03)).sum()),
            'rgb_signed_channels':(e.mean(axis=0)*255).tolist(),
            'clipped_output_channel_fraction':float(np.mean((output<=0)|(output>=1)))}


def regions(x,target,output):
    code_y=x@np.array([.2126,.7152,.0722])
    masks={'all':np.ones(len(x),bool),'shadow':code_y<1/3,'midtone':(code_y>=1/3)&(code_y<2/3),'highlight':code_y>=2/3,
        'near_gray':np.ptp(x,axis=1)<.04,'green':(x[:,1]>x[:,0]+.08)&(x[:,1]>x[:,2]+.04),
        'blue':(x[:,2]>x[:,0]+.12)&(x[:,2]>x[:,1]+.03),
        'skin_like':(x[:,0]>x[:,1])&(x[:,1]>x[:,2])&(x[:,0]>.25)&(x[:,0]-x[:,2]<.55)&(x[:,1]-x[:,2]>.03)}
    return {k:metrics(target[m],output[m]) for k,m in masks.items() if m.any()}


def gray_stats(cube,model,context,amount):
    codes=np.linspace(0,1,1024);x=np.repeat(codes[:,None],3,axis=1)
    target=x+amount*(sample_cube(cube,x)-x);out=context.apply(x,model,amount)
    desired=target@OPPONENT.T*255;actual=out@OPPONENT.T*255
    result={}
    for name,mask in {'all':np.ones(len(x),bool),'shadow':codes<1/3,'midtone':(codes>=1/3)&(codes<2/3),'highlight':codes>=2/3}.items():
        valid=np.abs(desired[mask])>=1
        wrong=(desired[mask]*actual[mask]<0)&valid
        result[name]={'opponent_mae_codes':float(np.abs(actual[mask]-desired[mask]).mean()),
                      'opponent_sign_wrong_fraction':float(wrong.sum()/valid.sum()) if valid.any() else 0.,
                      'sign_tested_components':int(valid.sum()),'rgb':metrics(target[mask],out[mask])}
    anchors=np.array([16,32,64,96,128,160,192,224,240]);a=np.repeat((anchors/255)[:,None],3,axis=1)
    result['anchors']={'input':anchors.tolist(),'target_rgb':(a+amount*(sample_cube(cube,a)-a)).__mul__(255).tolist(),
                       'prediction_rgb':(context.apply(a,model,amount)*255).tolist()}
    return result


def software_lut_references(cube):
    """Compare grid representation only; no assumption about camera throughput."""
    x=np.random.default_rng(73091).random((24000,3))
    gray=np.repeat(np.linspace(0,1,1024)[:,None],3,axis=1)
    target=sample_cube(cube,x);gray_target=sample_cube(cube,gray)
    result={}
    for size in (5,7,9):
        # .cube ordering is R fastest. Quantize in RAM without changing the
        # source LUT or the table used by the IPCS runtime.
        axis=np.linspace(0,1,size)
        nodes=np.stack(np.meshgrid(axis,axis,axis,indexing='ij'),-1).reshape(-1,3)[:,::-1]
        values=np.floor(sample_cube(cube,nodes)*1023+.5)/1023
        reduced=(size,(0.,)*3,(1.,)*3,values)
        output=sample_cube(reduced,x);gray_output=sample_cube(reduced,gray)
        desired=gray_target@OPPONENT.T*255;actual=gray_output@OPPONENT.T*255
        valid=np.abs(desired)>=1
        result[str(size)]={'grid':size,'output_bits':10,'unpacked_rgb_uint16_bytes':size**3*3*2,
            'interpolation':'software trilinear in RGB code coordinates',
            'rgb_regions':regions(x,target,output),'gray':metrics(gray_target,gray_output),
            'gray_opponent_mae_codes':float(np.abs(actual-desired).mean()),
            'gray_opponent_sign_wrong_fraction':float(np.sum((desired*actual<0)&valid)/valid.sum()) if valid.any() else 0.,
            'scope':'representation comparison; no camera pixel, CPU throughput or frame-rate measurement'}
    return result


def baseline_for(key,cube,source_sha,output,iterations):
    existing=PROFILES/key
    if (existing/'profile.bin').exists():
        old=json.loads((existing/'profile.json').read_text(encoding='utf-8'))
        if old['source_sha256']!=source_sha:raise ValueError('Existing profile source changed: '+key)
        return read_profile(existing/'profile.bin'),'existing-profile'
    cache=output/'baselines'/key
    if (cache/'profile.bin').exists():
        old=json.loads((cache/'profile.json').read_text(encoding='utf-8'))
        if old['source_sha256']!=source_sha:raise ValueError('Baseline cache source changed: '+key)
        return read_profile(cache/'profile.bin'),'new-baseline-no-previous-preview'
    model,fit=fit_model(cube,iterations=iterations,method='native-ycc')
    write_profile(cache/'profile.bin',model)
    (cache/'profile.json').write_text(json.dumps({'source_sha256':source_sha,'fit':fit},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return model,'new-baseline-no-previous-preview'


def run(key,output,iterations,gamma,measure_only=False):
    library=LookLibrary();meta=library.get(key);source=library.directory(key)/meta['master']
    source_sha=hashlib.sha256(source.read_bytes()).hexdigest();cube=load_cube(source)
    context=ColorModel(gamma)
    if measure_only:
        saved=json.loads((output/'profiles'/key/'analysis.json').read_text(encoding='utf-8'))
        if saved['source_sha256']!=source_sha or saved['pixel_model']['gamma_assumption']!=gamma:
            raise ValueError('Measurement context changed: '+key)
        before,origin,after,fit=saved['baseline_model'],saved['baseline_origin'],saved['model'],saved['fit']
    else:
        before,origin=baseline_for(key,cube,source_sha,output,iterations)
        after,fit=fit_tonal(cube,before,context,iterations)
    model_path=output/'profiles'/key/'profile.bin';write_profile(model_path,after)
    if read_profile(model_path)!=after:raise ValueError('Profile readback differs: '+key)
    x=np.random.default_rng(73091).random((24000,3));y=sample_cube(cube,x)
    parameters=np.asarray(fit['float_parameters'])
    best_continuous=np.asarray(fit['best_continuous_parameters'])
    report={'id':key,'label':meta['label'],'source_sha256':source_sha,'declared_input_space':meta['input_space'],
        'space_assumption':'sRGB for metrics; master declares unspecified' if meta['input_space']!='srgb' else 'master declares sRGB; native pipeline uncalibrated',
        'baseline_origin':origin,'baseline_model':before,'model':after,'fit':fit,
        'native_pixels_verified':False,'pixel_model':context.describe(),'measurements':{}}
    for amount in STRENGTHS:
        goal=x+amount*(y-x);old=context.apply(x,before,amount);new=context.apply(x,after,amount)
        continuous=forward(x,parameters,amount,color_model=context)
        report['measurements'][str(int(amount*100))]={
            'stock':regions(x,goal,x),'before':regions(x,goal,old),'after':regions(x,goal,new),
            'float_selected':regions(x,goal,continuous),'quantization_difference':metrics(continuous,new),
            'best_continuous_fit':regions(x,goal,forward(x,best_continuous,amount,color_model=context)),
            'gray_before':gray_stats(cube,before,context,amount),'gray_after':gray_stats(cube,after,context,amount)}
    report['model_sensitivity']={}
    for alternate in ('code','srgb','stock-knots'):
        c=ColorModel(alternate)
        report['model_sensitivity'][alternate]={'before':metrics(y,c.apply(x,before)),'after':metrics(y,c.apply(x,after))}
    report['legacy_proxy_difference']=metrics(apply_profile(x,before),context.apply(x,before))
    report['scenes']=[]
    for path in SOURCES:
        photo,scope,color=srgb_image(path,600);px=np.asarray(photo,dtype=float).reshape(-1,3)/255
        goal=sample_cube(cube,px)
        report['scenes'].append({'source':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'scope':scope,'color':color,
            'stock':regions(px,goal,px),'before':regions(px,goal,context.apply(px,before)),
            'after':regions(px,goal,context.apply(px,after))})
    destination=output/'profiles'/key
    (destination/'analysis.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    # Metadata remains usable by the manual cache/profile tools. It describes
    # this exact reference model; it is not a claim of calibrated camera colour.
    metadata={'id':key,'label':meta['label'],'source_sha256':source_sha,'source':str(source),
        'compiler_version':'ipcm-tonal-native-words-v1','proxy_model_version':'tonal-'+gamma+'-native-parameter-reference',
        'status':'offline-evaluated-camera-unverified','native_model_verified':False,'color_model':context.describe(),
        'profile_file':'profile.bin','quantized_model':after,'fit':fit}
    (destination/'profile.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    v=report['measurements']['100']
    print(json.dumps({'id':key,'selected':fit['selected'],'rgb_before':v['before']['all']['rgb_mae'],
        'rgb_after':v['after']['all']['rgb_mae'],'gray_opponent_before':v['gray_before']['all']['opponent_mae_codes'],
        'gray_opponent_after':v['gray_after']['all']['opponent_mae_codes']},ensure_ascii=False),flush=True)
    return report


def write_comparison(output,reports):
    lines=['# 全库 IPCM 离线对照','',
        '100% 强度，24,000 个独立 RGB 点；RGB 数值为 0～255 码值误差。OKLab 距离×100，不是 ΔE00。',
        '“旧”指保存的8款预览配置或其余12款首次生成的原算法基线。新旧使用同一个原生参数舍入模型。','',
        '| 风格 | 原厂直通 RGB | 旧 → 新 RGB | 新 P95 | 旧 → 新感知距离 | 旧 → 新灰阶色度 | 9³软件参考 RGB | 选择 |',
        '|---|---:|---:|---:|---:|---:|---:|---|']
    for r in reports:
        m=r['measurements']['100'];b=m['before']['all'];a=m['after']['all']
        lines.append(f'| {r["label"]} | {m["stock"]["all"]["rgb_mae"]:.3f} | '
            f'{b["rgb_mae"]:.3f} → {a["rgb_mae"]:.3f} | {a["rgb_p95"]:.3f} | '
            f'{b["oklab_mean_x100"]:.3f} → {a["oklab_mean_x100"]:.3f} | '
            f'{m["gray_before"]["all"]["opponent_mae_codes"]:.3f} → {m["gray_after"]["all"]["opponent_mae_codes"]:.3f} | '
            f'{r["software_lut_references"]["9"]["rgb_regions"]["all"]["rgb_mae"]:.3f} | {r["fit"]["selected"]} |')
    lines+=['','## 浮点、量化和三维表参考','',
        '连续候选来自本轮返回的分阶段解，不代表全局最优。量化差是选中连续解与原生参数模型输出之间的差，不是像素内部定点误差。',
        '5³／7³／9³统一采用10-bit节点与软件三线性插值，比较表示精度，没有执行相机逐帧软件后端。','',
        '| 风格 | 选中连续解 RGB | 最佳感知候选连续解 RGB | 量化输出差 RGB | 5³ RGB | 7³ RGB | 9³ RGB |',
        '|---|---:|---:|---:|---:|---:|---:|']
    for r in reports:
        m=r['measurements']['100'];refs=r['software_lut_references']
        lines.append(f'| {r["label"]} | {m["float_selected"]["all"]["rgb_mae"]:.3f} | '
            f'{m["best_continuous_fit"]["all"]["rgb_mae"]:.3f} | {m["quantization_difference"]["rgb_mae"]:.3f} | '
            +' | '.join(f'{refs[str(n)]["rgb_regions"]["all"]["rgb_mae"]:.3f}' for n in (5,7,9))+' |')
    lines+=['','## 各区域的最大退步','',
        '下表从25／50／75／100%及全部RGB码值区域中，取新结果减旧结果的最大RGB均值差。区域为合成码值条件，不是语义分割。',
        '负数表示所有检查的区域均改善；0表示至少一个区域相同。','',
        '| 风格 | 最差强度 | 区域 | RGB均值变化 |','|---|---:|---|---:|']
    for r in reports:
        changes=[(m['after'][region]['rgb_mae']-m['before'][region]['rgb_mae'],amount,region)
                 for amount,m in r['measurements'].items() for region in m['after']]
        delta,amount,region=max(changes)
        lines.append(f'| {r["label"]} | {amount}% | {region} | {delta:+.3f} |')
    lines+=['','[四组图像对照](index.html) · [完整数据](report.json)','',
        '完整数据另含色相角、色度、HSV饱和度、明度与对比度、P95、灰阶冷暖方向、两张独立NX照片及Gamma假设敏感性。','']
    (output/'comparison.md').write_text('\n'.join(lines),encoding='utf-8')


def render(output):
    library=LookLibrary();reports=[];rows=[]
    font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',15)
    icc=ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
    for meta in library.list():
        path=output/'profiles'/meta['id']/'analysis.json'
        if not path.exists():raise FileNotFoundError('Missing library evaluation: '+meta['id'])
        r=json.loads(path.read_text(encoding='utf-8'))
        context=ColorModel(r['pixel_model']['gamma_assumption'])
        source=library.directory(meta['id'])/meta['master']
        if hashlib.sha256(source.read_bytes()).hexdigest()!=r['source_sha256']:
            raise ValueError('Source changed since evaluation: '+meta['id'])
        cube=load_cube(source)
        r['software_lut_references']=software_lut_references(cube)
        reports.append(r)
        canvas=Image.new('RGB',(320*4,240*len(SOURCES)+100),'#181a19');draw=ImageDraw.Draw(canvas)
        draw.text((12,8),meta['label']+' · 100% · 离线参数模型',font=font,fill='white')
        baseline_title='当前 IPCM' if r['baseline_origin']=='existing-profile' else '新建 IPCM 基线'
        for i,title in enumerate(('原图／原厂直通','源 LUT 目标',baseline_title,'优化后 IPCM')):
            draw.text((i*320+12,38),title,font=font,fill='#d9ded9')
        for j,path in enumerate(SOURCES):
            photo,_,_=srgb_image(path,320);x=np.asarray(photo,dtype=float)/255
            images=[x,sample_cube(cube,x),context.apply(x,r['baseline_model']),context.apply(x,r['model'])]
            for i,img in enumerate(images):
                im=Image.fromarray(np.floor(np.clip(img,0,1)*255+.5).astype('uint8'))
                canvas.paste(im,(i*320,70+j*240))
        name=meta['id']+'.jpg';canvas.save(output/name,quality=94,icc_profile=icc)
        m=r['measurements']['100'];before=m['before']['all'];after=m['after']['all']
        cells=[r['label'],f'{before["rgb_mae"]:.2f} → {after["rgb_mae"]:.2f}',
            f'{before["oklab_mean_x100"]:.2f} → {after["oklab_mean_x100"]:.2f}',
            f'{m["gray_before"]["shadow"]["opponent_mae_codes"]:.2f} → {m["gray_after"]["shadow"]["opponent_mae_codes"]:.2f}',
            f'{r["software_lut_references"]["9"]["rgb_regions"]["all"]["rgb_mae"]:.2f}',
            '保留基线' if r['fit']['baseline_retained'] else r['fit']['selected']]
        rows.append('<tr><th><a href="'+name+'">'+html.escape(cells[0])+'</a></th>'+''.join('<td>'+html.escape(v)+'</td>' for v in cells[1:])+'</tr>')
    summary={'looks':reports,'count':len(reports),'native_pixels_verified':False,
        'protocol':{'test_seed':73091,'test_rgb_points':24000,'gray_points':1024,'strengths':[25,50,75,100],
          'scene_training':False,'all_styles_same_algorithm':True,
          'metrics':'RGB code errors; OKLab distance x100, not DeltaE00; chroma/hue on sRGB proxy',
          'region_masks':'RGB-code ranges, not semantic segmentation',
          'float_result':'Best returned continuous stage candidate, not a proof of the hardware/global optimum',
          'small_lut_references':'5/7/9 cubes, 10-bit node outputs, same RGB holdout and source; software trilinear only, no runtime installation',
          'gamma_sensitivity':'Same fixed parameters evaluated under alternative assumptions, not competing measurements of the camera'}}
    (output/'report.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>IPCM 通用拟合审计</title><style>body{background:#181a19;color:#e6e8e5;font:16px/1.65 system-ui,"Microsoft YaHei";max-width:1200px;margin:40px auto;padding:0 20px}a{color:#bdd8ad}table{border-collapse:collapse;width:100%}td,th{padding:12px;border-bottom:1px solid #394039;text-align:left}p{color:#b8beb6}img{max-width:100%}</style><h1>IPCM 通用拟合审计</h1><p>COUNT 款源 LUT，同一算法；训练不使用照片。点击风格查看原图、目标、当前与优化后四栏。现有8款使用保存的配置作为基线，其他款首次建立基线。数值采用相同的原厂参数舍入模型，尚未标定真实像素处理顺序。</p><p>RGB 为0～255码值误差；感知距离为OKLab距离×100；灰阶比较源LUT自己的冷暖倾向，不要求源LUT保持中性。9³一列只比较三维表的软件表达精度，不代表相机运行速度。<a href="report.json">完整数据、5³／7³／9³参考与Gamma假设敏感性</a></p><table><thead><tr><th>风格</th><th>RGB误差</th><th>感知距离</th><th>暗部灰阶色度误差</th><th>9³参考</th><th>选择</th></tr></thead><tbody>ROWS</tbody></table>'''
    (output/'index.html').write_text(page.replace('COUNT',str(len(reports))).replace('ROWS',''.join(rows)),encoding='utf-8')
    write_comparison(output,reports)
    print(json.dumps({'report':str(output/'report.json'),'count':len(reports),'changed':sum(not r['fit']['baseline_retained'] for r in reports)},ensure_ascii=False))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--look',action='append');parser.add_argument('--all',action='store_true')
    parser.add_argument('--output',type=Path,default=OUT);parser.add_argument('--iterations',type=int,default=60)
    parser.add_argument('--gamma',choices=('code','srgb','stock-knots'),default='code')
    parser.add_argument('--part',type=int,default=0);parser.add_argument('--parts',type=int,default=1)
    parser.add_argument('--render',action='store_true')
    parser.add_argument('--measure-only',action='store_true',help='Add/report metrics from saved fits without optimizing again')
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    if args.render:render(args.output);return
    if not 0<=args.part<args.parts:parser.error('--part must be within --parts')
    keys=[m['id'] for m in LookLibrary().list()] if args.all else args.look
    if not keys:parser.error('Specify --all or --look')
    for i,key in enumerate(keys):
        if i%args.parts==args.part:run(key,args.output,args.iterations,args.gamma,args.measure_only)


if __name__=='__main__':main()
