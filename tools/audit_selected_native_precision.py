"""Compare uniform sampling, deployed fits and integral fits of approved masters."""
import hashlib
import json
import sys
from pathlib import Path
import numpy as np
from compare_film_luts_current import ROOT, inputs, grid as image_grid, resize
from nx300m_lut_core import parse_lut_bytes
from lut_resample import compile_table, sample_table, target, grid, unpack, bounded_solve
from stock_lut import factory_3d_lut

sys.stdout.reconfigure(encoding='utf-8')
OUT=ROOT/'outputs/looks/camera-curation/native-precision'
KEYS=['nx300-fuji-classic-chrome-cxo50','nx300-fuji-eterna-cxo40',
      'nx300-fuji-pro-neg-std','nx300-fuji-velvia']


def source(path):
    lut=parse_lut_bytes(path.read_bytes(),path.name)
    return (lut.size,lut.domain_min,lut.domain_max,
            lut.table.transpose(2,1,0,3).reshape(-1,3).astype(np.float64))


def metrics(a,b):
    d=np.abs(a-b)*255
    return dict(rgb_rmse=float(np.sqrt(np.mean(d*d))),
                channel_abs_p95=float(np.quantile(d,.95)),channel_abs_max=float(d.max()))


def gray_check(rgb):
    luminance=rgb@np.array([.2126,.7152,.0722])
    return dict(black=rgb[0].tolist(),white=rgb[-1].tolist(),
                decreasing_steps_over_one_10bit=int(np.count_nonzero(np.diff(luminance)<-1/1023)),
                worst_luma_step_8bit=float(np.min(np.diff(luminance))*255))


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    photos=[(name,resize(p,280)) for name,p,_ in inputs()]
    probes=dict(grid33=grid(33),grid65=grid(65),
                heldout=np.random.default_rng(173281).random((65536,3)),
                gray=np.repeat(np.linspace(0,1,4097)[:,None],3,axis=1))
    records=[]
    protected={key:hashlib.sha256((ROOT/'looks'/key/'source.cube').read_bytes()).hexdigest() for key in KEYS}
    for key in KEYS:
        directory=ROOT/'looks'/key
        meta=json.loads((directory/'look.json').read_text(encoding='utf-8'))
        cube=source(directory/'source.cube')
        # Candidate is isolated; the deployed tables and their compiler metadata stay intact.
        candidate,detail=compile_table(cube,method='fit-integral')
        (OUT/(key+'-integral-candidate.d4lut')).write_bytes(candidate)
        direct={
            'uniform9_round10':compile_table(cube,method='round')[0],
            'deployed_fit':(directory/'table.d4lut').read_bytes(),
            'integral_candidate':candidate,
        }
        row=dict(id=key,label=meta['label'],source_sha256=protected[key],source_grid=cube[0],
                 compiler=meta['compiler']['method'],candidate_compiler=detail,paths={},direct={},stock_composed={})
        for method,data in direct.items():
            check={}
            for name,x in probes.items():
                goal=target(cube,x)
                pred=sample_table(data,x)
                check[name]=metrics(goal,pred)
                if name=='gray':check[name]['axis']=gray_check(pred)
            check['scenes']=[dict(scene=name,**metrics(target(cube,p),sample_table(data,p))) for name,p in photos]
            check['worst_scene_rmse']=max(s['rgb_rmse'] for s in check['scenes'])
            check['range_10bit']=[int(round(unpack(data).min()*1023)),int(round(unpack(data).max()*1023))]
            row['direct'][method]=check
        row['source_gray_axis']=gray_check(target(cube,probes['gray']))
        stock=factory_3d_lut()
        stock_direct={
            'uniform9_round10':compile_table(cube,stock,method='round')[0],
            'deployed_fit':(directory/'table-stock3d.d4lut').read_bytes(),
        }
        for method,data in stock_direct.items():
            row['stock_composed'][method]={name:metrics(target(cube,probes[name],np.asarray(stock).reshape(3,730)[:,:729].T/1023),
                                                       sample_table(data,probes[name])) for name in ['grid33','heldout']}
        worst=max(range(len(photos)),key=lambda j:row['direct']['deployed_fit']['scenes'][j]['rgb_rmse'])
        display=[]
        for j in dict.fromkeys([worst,6]):
            name,p=photos[j]
            display.append((name,[('原图',p),('已定版母版100%',target(cube,p)),
                                  ('均匀抽点9³',sample_table(direct['uniform9_round10'],p)),
                                  ('当前入机拟合9³',sample_table(direct['deployed_fit'],p)),
                                  ('积分拟合候选9³',sample_table(candidate,p))]))
        path=OUT/(key+'.jpg')
        image_grid(meta['label']+' · 9³精度核对','同一母版；后两列只改变网格拟合，不改变风格配方。',display,path,320,225)
        row['preview']=path.name
        records.append(row)
        print(key,json.dumps({k:v['grid33'] for k,v in row['direct'].items()}),flush=True)
    old=ROOT/'outputs/looks/library-review/20261110-TRIPLE_REVIEW/recipe-cubes/cxo-eterna-c30.cube'
    cube=source(old)
    old_round=compile_table(cube,method='round')[0]
    old_reference=dict(source=old.relative_to(ROOT).as_posix(),
                       grid33_uniform9=metrics(target(cube,probes['grid33']),sample_table(old_round,probes['grid33'])))
    assert all(hashlib.sha256((ROOT/'looks'/k/'source.cube').read_bytes()).hexdigest()==v for k,v in protected.items())
    report=dict(model='encoded RGB trilinear; hardware coordinates/interpolation unmeasured',
                units='8-bit channel code values; p95 over individual absolute channel errors, not pixel means',
                scene_count=len(photos),scene_long_edge=280,
                unchanged_approved_masters=True,deployed_tables_not_replaced=True,
                legacy_eterna30_reproduction=old_reference,entries=records)
    (OUT/'audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    lines=['# 入机9³精度核对','',report['model'],'',report['units'],'',
           '18场景缩至长边280进行同口径比较；原完整尺寸误差另见selected-verification.json。',
           '', '| 配方 | 方法 | 33³网格P95 | 33³网格最大 | 独立样本RMSE | 最差场景RMSE |',
           '|---|---|---:|---:|---:|---:|']
    for r in records:
        for method,label in [('uniform9_round10','均匀抽点'),('deployed_fit','当前拟合'),('integral_candidate','积分候选')]:
            m=r['direct'][method]
            lines.append(f"| {r['label']} | {label} | {m['grid33']['channel_abs_p95']:.2f} | {m['grid33']['channel_abs_max']:.2f} | {m['heldout']['rgb_rmse']:.2f} | {m['worst_scene_rmse']:.2f} |")
    lines+=['','积分候选独立保留，未替换入机表或修改任何33³定版母版。','']
    for r in records:lines.append(f"![{r['label']}]({r['preview']})\n")
    (OUT/'review.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps(old_reference,ensure_ascii=False),flush=True)


def limits():
    from lut_integral import volume_terms
    from evaluate_d4_projection import rmse
    path=OUT/'audit.json'
    report=json.loads(path.read_text(encoding='utf-8'))
    x=np.random.default_rng(173281).random((65536,3))
    y=x@np.array([.2126,.7152,.0722])
    masks={
        'skin_like_RGB_not_semantic_faces':(x[:,0]>x[:,1])&(x[:,1]>x[:,2])&(x[:,0]>.25)&(x[:,0]-x[:,2]<.55),
        'green':(x[:,1]>x[:,0]+.08)&(x[:,1]>x[:,2]+.04),
        'blue':(x[:,2]>x[:,0]+.12)&(x[:,2]>x[:,1]+.03),
        'red':(x[:,0]>x[:,1]+.12)&(x[:,0]>x[:,2]+.03),
        'shadow':y<.2,'near_gray':np.ptp(x,axis=1)<.04,
    }
    lines=['','## 同模型下的拟合余量','',
           '下界针对整个RGB立方体的均匀体积L2误差；不是最大误差下界，也不是照片分布或机身误差。',
           '', '| 配方 | 当前拟合RMSE | 积分候选RMSE | 浮点9³范围约束最小RMSE |',
           '|---|---:|---:|---:|']
    for e in report['entries']:
        directory=ROOT/'looks'/e['id']
        assert hashlib.sha256((directory/'source.cube').read_bytes()).hexdigest()==e['source_sha256']
        cube=source(directory/'source.cube')
        h,b,energy,detail=volume_terms(cube)
        assert detail['volume_integral']=='exact for bounded trilinear source'
        optimum,status=bounded_solve(h,b)
        assert all(s['converged'] for s in status)
        deployed=(directory/'table.d4lut').read_bytes()
        candidate=(OUT/(e['id']+'-integral-candidate.d4lut')).read_bytes()
        exact=dict(criterion='uniform RGB-volume L2, trilinear, node range [0,1]',
                   float_bounded_minimum_rmse=rmse(h,b,energy,optimum),
                   deployed_rmse=rmse(h,b,energy,unpack(deployed)),
                   candidate_rmse=rmse(h,b,energy,unpack(candidate)),solver=status)
        e['uniform_volume_limit']=exact
        goal=target(cube,x)
        pred=sample_table(deployed,x)
        e['deployed_holdout_regions']={name:dict(samples=int(mask.sum()),**metrics(goal[mask],pred[mask]))
                                      for name,mask in masks.items()}
        probes=grid(33);goal=target(cube,probes);pred=sample_table(deployed,probes)
        n,c=np.unravel_index(np.argmax(np.abs(goal-pred)),goal.shape)
        e['worst_grid33_channel']=dict(input=probes[n].tolist(),target=goal[n].tolist(),
                                       deployed=pred[n].tolist(),channel=int(c))
        lines.append(f"| {e['label']} | {exact['deployed_rmse']:.3f} | {exact['candidate_rmse']:.3f} | {exact['float_bounded_minimum_rmse']:.3f} |")
        print(e['id'],json.dumps(exact),flush=True)
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    review=OUT/'review.md'
    review.write_text(review.read_text(encoding='utf-8')+'\n'.join(lines)+'\n',encoding='utf-8')


if __name__=='__main__':
    limits() if len(sys.argv)>1 and sys.argv[1]=='limits' else main()
