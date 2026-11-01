"""Calibrate a local native-PCSE response and jointly fit source LUTs on CPU.

The response uses measured RGB differences per native parameter unit. No HSV
sector geometry or physical Q format is assumed. Its output-coordinate model
is a local surrogate; full ISP stage composition still needs hardware checks.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from fit_ipcm_preview import (apply_profile, read_profile, write_profile, round_away,
                              sample_cube, error_metrics, identity_model)
from ipcm_preview_fit import (forward, from_model, to_model, project, perceptual_features)
from ipcm_preview_general import samples, STRENGTHS
from cube_to_d4 import load_cube
from look_library import LookLibrary

ROOT = Path(__file__).resolve().parents[1]
FIELDS = ['r','g','b','c','m','y','o','cg','v','yg','cb','p']


def basis(rgb, degree, derivatives=False):
    rgb = np.asarray(rgb, dtype=float).reshape(-1, 3)
    if not np.isfinite(rgb).all() or np.any((rgb<0)|(rgb>1)):
        raise ValueError('Response coordinates must be finite normalized RGB')
    def terms(n):
        return np.stack([math.comb(n,i)*rgb**i*(1-rgb)**(n-i) for i in range(n+1)],axis=2)
    t=terms(degree)
    b=np.einsum('ni,nj,nk->nijk',t[:,0],t[:,1],t[:,2]).reshape(len(rgb),-1)
    if not derivatives:return b
    lower=terms(degree-1)
    dt=degree*(np.pad(lower,((0,0),(0,0),(1,0)))-np.pad(lower,((0,0),(0,0),(0,1))))
    jac=np.stack([np.einsum('ni,nj,nk->nijk',dt[:,0] if k==0 else t[:,0],
                            dt[:,1] if k==1 else t[:,1],dt[:,2] if k==2 else t[:,2]).reshape(len(rgb),-1)
                  for k in range(3)],axis=2)
    return b,jac


class Response:
    def __init__(self, coefficients, step, metadata):
        self.coefficients=np.asarray(coefficients,dtype=float)
        self.step=np.asarray(step,dtype=float)
        self.metadata=metadata
        self.degree=int(metadata['degree'])
        if not 1<=self.degree<=7 or self.coefficients.shape!=((self.degree+1)**3,24,3):
            raise ValueError('PCSE response coefficient shape differs')
        if self.step.shape!=(24,) or not np.isfinite(self.step).all() or np.any(self.step<1) or np.any(self.step>4095):
            raise ValueError('PCSE probe steps must be 24 native magnitudes in 1..4095')
        if not np.isfinite(self.coefficients).all():raise ValueError('Nonfinite response')

    @classmethod
    def load(cls,path):
        with np.load(path,allow_pickle=False) as data:
            return cls(data['coefficients'],data['step'],json.loads(str(data['metadata'])))

    def save(self,path):
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
        np.savez_compressed(path,coefficients=self.coefficients,step=self.step,
                            metadata=json.dumps(self.metadata,ensure_ascii=False))

    def response(self,rgb,derivatives=False):
        flat=self.coefficients.reshape(len(self.coefficients),72)
        if not derivatives:return (basis(rgb,self.degree)@flat).reshape(-1,24,3)
        b,j=basis(rgb,self.degree,True)
        return (b@flat).reshape(-1,24,3),np.einsum('nbi,bpo->nipo',j,self.coefficients)

    def apply(self,rgb,delta):
        delta=np.asarray(delta,dtype=float)
        if delta.shape!=(24,) or not np.isfinite(delta).all():raise ValueError('24 finite native deltas required')
        # Independent probes only identify a local model. Keep a combined
        # perturbation within the sum of individually measured excursions.
        if np.sum(np.abs(delta)/self.step)>1+1e-8:
            raise ValueError('Combined PCSE perturbation exceeds calibrated local budget')
        return np.clip(rgb+np.einsum('npo,p->no',self.response(rgb),delta),0,1)


def calibrate(path,degree=5):
    with np.load(path,allow_pickle=False) as data:
        before=np.asarray(data['baseline_before'],dtype=float)
        after=np.asarray(data['baseline_after'],dtype=float)
        plus=np.asarray(data['plus_rgb'],dtype=float)
        minus=np.asarray(data['minus_rgb'],dtype=float)
        step=np.asarray(data['step'],dtype=float)
        provenance=json.loads(str(data['metadata']))
    if provenance.get('camera')!='NX300M' or provenance.get('firmware')!='1.15' or provenance.get('source_kind') not in ('camera','synthetic'):
        raise ValueError('Capture metadata must identify NX300M 1.15 and source_kind camera or synthetic')
    if not 2<=degree<=7:raise ValueError('Response basis degree must be 2..7')
    if before.ndim!=2 or before.shape[1]!=3 or len(before)<4*(degree+1)**3 or after.shape!=before.shape:
        raise ValueError('Need matched Nx3 baseline samples, at least four times the response basis size')
    if plus.shape!=(24,len(before),3) or minus.shape!=plus.shape or step.shape!=(24,):
        raise ValueError('Need matched + and - captures for all 24 native parameters')
    for a in (before,after,plus,minus):
        if not np.isfinite(a).all() or np.any((a<0)|(a>1)):
            raise ValueError('Capture RGB must be finite and normalized; convert encoding explicitly first')
    drift=np.mean(np.abs(before-after))*255
    if drift>1 or np.quantile(np.mean(np.abs(before-after),axis=1)*255,.95)>2:
        raise ValueError('Baseline drift exceeds 1 mean / 2 P95 RGB code values; fix capture alignment, exposure and WB')
    center=(before+after)/2
    if not np.isfinite(step).all() or np.any(step<1) or np.any(step>4095) or np.any(step!=np.trunc(step)):
        raise ValueError('24 finite integer native steps in 1..4095 required')
    derivative=((plus-minus)/(2*step[:,None,None])).transpose(1,0,2)
    rng=np.random.default_rng(19471);order=rng.permutation(len(center));split=int(len(center)*.8)
    train,hold=order[:split],order[split:]
    b=basis(center[train],degree)
    gram=b.T@b
    ridge=max(np.trace(gram)/len(gram)*1e-4,1e-10)
    coefficients=np.linalg.solve(gram+np.eye(len(gram))*ridge,b.T@derivative[train].reshape(len(train),72)).reshape(-1,24,3)
    meta={'format':1,'degree':degree,'camera':'NX300M','firmware':'1.15',
          'native_model_verified':False,'model':'local-output-rgb-response-per-native-unit',
          'field_order':FIELDS,'selector_order':['PHC','PSC'],'source':provenance,
          'capture_sha256':hashlib.sha256(Path(path).read_bytes()).hexdigest(),
          'baseline_drift_mean_rgb8':float(drift),'train_samples':len(train),'holdout_samples':len(hold),
          'combined_native_budget':'sum(abs(delta)/step)<=1',
          'composition_limit':'Response measured around stock output; changed CCM/Tone and multi-parameter interactions require independent frame verification.'}
    response=Response(coefficients,step,meta)
    predicted=response.response(center[hold]).transpose(1,0,2)*step[:,None,None]
    measured=(plus[:,hold]-minus[:,hold])/2
    meta['heldout_symmetric_response_mae_rgb8']=float(np.mean(np.abs(predicted-measured))*255)
    meta['nonlinearity_midpoint_mae_rgb8']=float(np.mean(np.abs((plus+minus)/2-center[None]))*255)
    return response


def joint_forward(rgb,p,base_size,response,amount,jacobian=False):
    native=p[base_size:]*response.step*amount
    if not jacobian:
        return response.apply(forward(rgb,p[:base_size],amount),native)
    base,j=forward(rgb,p[:base_size],amount,True)
    r,dr=response.response(base,True)
    raw=base+np.einsum('npo,p->no',r,native)
    local=np.eye(3)[None]+np.einsum('nipo,p->noi',dr,native)
    jb=local@j
    jp=r.transpose(0,2,1)*(response.step*amount)[None,None]
    jac=np.concatenate([jb,jp],axis=2)*((raw>0)&(raw<1))[:,:,None]
    return np.clip(raw,0,1),jac


def project_joint(p,base_size):
    result=np.concatenate([project(p[:base_size]),np.clip(p[base_size:],-1,1)])
    norm=np.sum(np.abs(result[base_size:]))
    if norm>1:result[base_size:]/=norm
    return result


def quantized(p,base_size,response):
    model=to_model(p[:base_size])
    delta=round_away(p[base_size:]*response.step).astype(int)
    # Rounding multiple independent axes may cross the local budget. Scale
    # toward zero before serialization; never enlarge measured excursions.
    if np.sum(np.abs(delta)/response.step)>1:
        delta=np.trunc(p[base_size:]*response.step).astype(int)
    model['pcse_delta']=delta.reshape(2,12).tolist()
    return model


def fit(cube,baseline,response,iterations=30):
    rgb,weights=samples();target=sample_cube(cube,rgb)
    base=from_model(baseline,full_post=True);nb=len(base)
    p=np.concatenate([base,np.zeros(24)])
    roots=np.sqrt(weights/(6*len(STRENGTHS)))[:,None]
    target_features={a:perceptual_features(rgb+a*(target-rgb)) for a in STRENGTHS}
    # Smooth shared Tone; normalized native deltas have common ridge weights.
    reg=np.zeros((len(base)-18-2+24,len(p)))
    reg[:len(base)-20,18:nb]=np.diff(np.eye(nb-18),2,axis=0)*np.sqrt(.002)
    reg[len(base)-20:,nb:]=np.eye(24)*np.sqrt(1e-6)
    def loss(v):
        return sum(np.sum((perceptual_features(joint_forward(rgb,v,nb,response,a))-target_features[a])**2*roots**2)
                   for a in STRENGTHS)+np.sum((reg@v)**2)
    def qscore(model,x,w):
        y=sample_cube(cube,x)
        return float(sum(np.sum(np.mean((perceptual_features(apply_profile(x,model,a,response))-
                                perceptual_features(x+a*(y-x)))**2,axis=1)*w) for a in STRENGTHS)/len(STRENGTHS))
    check,cw=samples(selection=True)
    candidates=[baseline,quantized(p,nb,response)]
    current=loss(p);damping=.001;accepted=0
    for _ in range(iterations):
        h=reg.T@reg;g=reg.T@(reg@p)
        for a in STRENGTHS:
            output,j=joint_forward(rgb,p,nb,response,a,True)
            features,color_j=perceptual_features(output,True)
            j=(color_j@j*roots[:,:,None]).reshape(-1,len(p))
            residual=(features-target_features[a])*roots
            h+=j.T@j;g+=j.T@residual.ravel()
        step=np.linalg.solve(h+np.diag(damping*np.maximum(np.diag(h),1e-7)),-g)
        improved=False
        for amount in (1.,.5,.25,.125):
            proposed=project_joint(p+amount*step,nb);value=loss(proposed)
            if value<current-1e-12:
                p,current=proposed,value;accepted+=1;improved=True;damping=max(damping/3,1e-7);break
        if not improved:
            damping*=10
            if damping>1e6:break
        candidates.append(quantized(p,nb,response))
    scores=[qscore(m,check,cw) for m in candidates]
    uniform=check[:3072];reference=sample_cube(cube,uniform)
    def uniform_metrics(m):
        rows=[]
        for a in STRENGTHS:
            error=np.mean(np.abs(apply_profile(uniform,m,a,response)-(uniform+a*(reference-uniform))),axis=1)*255
            rows.append([error.mean(),np.quantile(error,.95)])
        return np.asarray(rows)
    baseline_metrics=uniform_metrics(baseline)
    limits=baseline_metrics+np.maximum([.1,.2],baseline_metrics*[.02,.03])
    eligible=[i for i,m in enumerate(candidates) if np.all(uniform_metrics(m)<=limits)]
    selected=min(eligible,key=lambda i:scores[i]);model=candidates[selected]
    test=np.random.default_rng(24973).random((10000,3));goal=sample_cube(cube,test)
    errors={str(int(a*100)): {'baseline':error_metrics(test+a*(goal-test),apply_profile(test,baseline,a)),
                             'candidate':error_metrics(test+a*(goal-test),apply_profile(test,model,a,response))}
            for a in STRENGTHS}
    return model,{'accepted_steps':accepted,'selection_score_baseline':scores[0],
                  'selection_score_candidate':scores[selected],'baseline_retained':selected==0,
                  'selection_uniform_baseline':baseline_metrics.tolist(),
                  'selection_uniform_candidate':uniform_metrics(model).tolist(),
                  'native_model_verified':False,'errors':errors}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    c=commands.add_parser('calibrate');c.add_argument('captures',type=Path);c.add_argument('--output',type=Path,required=True)
    c.add_argument('--degree',type=int,choices=range(2,8),default=5)
    probes=commands.add_parser('probes');probes.add_argument('--output',type=Path,required=True)
    probes.add_argument('--step',type=int,default=8)
    f=commands.add_parser('fit');f.add_argument('--response',type=Path,required=True)
    f.add_argument('--look',action='append',default=[]);f.add_argument('--all',action='store_true')
    f.add_argument('--iterations',type=int,default=30)
    f.add_argument('--baseline-root',type=Path,default=ROOT/'outputs/deployment/ipcm-preview/profiles')
    f.add_argument('--profiles-root',type=Path,default=ROOT/'outputs/deployment/ipcm-preview/pcse-candidates/profiles')
    args=parser.parse_args()
    if args.command=='probes':
        if not 1<=args.step<=4095:parser.error('--step must be 1..4095 native units')
        model=identity_model();model.pop('chroma_q12');model['ycc_q12']=(np.eye(3,dtype=int)*4096).ravel().tolist()
        model['pcse_delta']=np.zeros((2,12),dtype=int).tolist()
        args.output.mkdir(parents=True,exist_ok=True);write_profile(args.output/'baseline.bin',model)
        entries=[]
        for selector in range(2):
            for i,field in enumerate(FIELDS):
                for sign,label in ((1,'plus'),(-1,'minus')):
                    delta=np.zeros((2,12),dtype=int);delta[selector,i]=args.step*sign
                    model['pcse_delta']=delta.tolist()
                    name=f'{"phc" if selector==0 else "psc"}-{field}-{label}.bin'
                    write_profile(args.output/name,model)
                    entries.append({'file':name,'selector':selector,'field':field,'native_delta':int(delta[selector,i])})
        (args.output/'probes.json').write_text(json.dumps({'camera':'NX300M','firmware':'1.15','step':args.step,
            'baseline':'baseline.bin','profiles':entries,'native_model_verified':False,
            'note':'Parameter-only probes; captures must use stable matched scene, encoding, WB and exposure.'},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps({'profiles':len(entries)+1,'output':str(args.output),'step':args.step}));return
    if args.command=='calibrate':
        response=calibrate(args.captures,args.degree);response.save(args.output)
        args.output.with_suffix('.json').write_text(json.dumps(response.metadata,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(response.metadata,ensure_ascii=False));return
    response=Response.load(args.response);library=LookLibrary()
    if args.profiles_root.resolve()==args.baseline_root.resolve():
        parser.error('Experimental profiles must be written separately from baseline profiles')
    keys=sorted(p.name for p in args.baseline_root.iterdir() if p.is_dir()) if args.all else args.look
    if not keys:parser.error('Specify --look or --all')
    for key in keys:
        metadata=library.get(key);source=library.directory(key)/metadata['master']
        baseline=read_profile(args.baseline_root/key/'profile.bin')
        old=json.loads((args.baseline_root/key/'profile.json').read_text(encoding='utf-8'))
        if hashlib.sha256(source.read_bytes()).hexdigest()!=old['source_sha256']:
            raise ValueError('Source changed since baseline compilation: '+key)
        if 'pcse_delta' in baseline:raise ValueError('Baseline must be an unmodified v1/v2 profile')
        model,report=fit(load_cube(source),baseline,response,args.iterations)
        destination=args.profiles_root/key;destination.mkdir(parents=True,exist_ok=True)
        write_profile(destination/'profile.bin',model)
        # The precise integer payload, including all 24 deltas, must survive.
        if read_profile(destination/'profile.bin')!=model:raise ValueError('Profile readback differs')
        report.update(id=key,source_sha256=old['source_sha256'],response=response.metadata,
                      response_sha256=hashlib.sha256(args.response.read_bytes()).hexdigest(),
                      model=model,profile_file='profile.bin',status='experimental-local-response-fit')
        (destination/'profile.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps({'id':key,'baseline_retained':report['baseline_retained'],'errors':report['errors']['100']},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
