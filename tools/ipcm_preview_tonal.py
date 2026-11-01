"""Common staged fitting with signed low-chroma colour and native-word selection."""
import numpy as np

from fit_ipcm_preview import sample_cube
from ipcm_preview_fit import forward, from_model, solve, tonal_features

STRENGTHS=(.25,.5,.75,1.)


def samples(selection=False):
    rng=np.random.default_rng(30583 if selection else 89173)
    if selection:
        volume=rng.random((3072,3))
        gray=np.repeat(rng.random((257,1)),3,axis=1)
    else:
        axis=np.linspace(0,1,13)
        volume=np.stack(np.meshgrid(axis,axis,axis,indexing='ij'),-1).reshape(-1,3)
        gray=np.repeat(np.linspace(0,1,257)[:,None],3,axis=1)
    near=np.clip(rng.uniform(.005,.995,(1024,1))+rng.normal(0,.025,(1024,3)),0,1)
    x=np.concatenate([volume,gray,near])
    weights=np.concatenate([np.full(len(volume),.6/len(volume)),np.full(len(gray),.2/len(gray)),np.full(len(near),.2/len(near))])
    return x,weights


def fit_tonal(cube, baseline, context, iterations=60):
    rgb,weights=samples();target=sample_cube(cube,rgb)
    check,cw=samples(True);goal=sample_cube(cube,check)
    reference={s:tonal_features(check+s*(goal-check),check) for s in STRENGTHS}

    def score(model):
        return float(sum(cw@np.mean((tonal_features(context.apply(check,model,s),check)-reference[s])**2,axis=1)
                         for s in STRENGTHS)/len(STRENGTHS))

    def quality(model):
        result=[]
        for s in STRENGTHS:
            out,submission=context.apply(check,model,s,True)
            err=np.mean(np.abs(out[:3072]-(check+s*(goal-check))[:3072]),axis=1)*255
            result.append([float(err.mean()),float(np.quantile(err,.95))])
            if any(submission[k] for k in ('ccm_fallback','ycc_fallback')):
                return np.full((len(STRENGTHS),2),np.inf)
        return np.asarray(result)

    p=from_model(baseline,full_post=True,knots=65)
    candidates=[('baseline',baseline,{'float_parameters':from_model(baseline,full_post='ycc_q12' in baseline,knots=256).tolist()})]
    # Fit tonal shape first, then the global colour matrices, before allowing
    # joint changes. Stage budgets and weights do not depend on a style name.
    stages=(('tone',max(8,iterations//5)),('matrices',max(8,iterations//5)),('joint',iterations))
    for stage,budget in stages:
        model,detail=solve(rgb,target,weights,p,iterations=budget,strengths=STRENGTHS,
                           objective='tonal',color_model=context,trainable=stage,matrix_ridge=1e-6)
        candidates.append((stage,model,detail))
        p=np.asarray(detail['float_parameters'])
    base=quality(baseline)
    limit=base+np.maximum([.10,.20],base*[.02,.03])
    descriptions=[];eligible=[]
    for name,model,detail in candidates:
        q=quality(model);passed=bool(np.all(np.isfinite(q)) and np.all(q<=limit))
        parameters=np.asarray(detail['float_parameters'])
        floating=[forward(check,parameters,s,color_model=context) for s in STRENGTHS]
        float_score=float(sum(cw@np.mean((tonal_features(v,check)-reference[s])**2,axis=1)
                              for v,s in zip(floating,STRENGTHS))/len(STRENGTHS))
        item={'stage':name,'score':score(model),'uniform_rgb_mean_p95':q.tolist(),'within_nonregression_limit':passed,
              'float_score':float_score,'float_parameters':parameters.tolist(),'model':model}
        descriptions.append(item)
        if passed:eligible.append((item['score'],name,model,detail))
    if not eligible:raise ValueError('Baseline unexpectedly violates native submission constraints')
    _,name,model,detail=min(eligible,key=lambda v:v[0])
    float_best=min(descriptions,key=lambda v:v['float_score'])
    return model,{'algorithm':'staged tone/matrix/joint fit; signed low-chroma objective; exact native parameter selection',
                  'selected':name,'baseline_retained':name=='baseline','style_specific_rules':False,
                  'training_samples':len(rgb),'selection_samples':len(check),'strengths':[25,50,75,100],
                  'training_weights':{'RGB_volume':.6,'gray':.2,'near_gray':.2},
                  'selection_rgb_limits':{'mean':'baseline + max(0.10 code, 2%)','p95':'baseline + max(0.20 code, 3%)'},
                  'candidates':descriptions,'float_parameters':detail['float_parameters'],
                  'best_continuous_stage':float_best['stage'],'best_continuous_parameters':float_best['float_parameters'],
                  'color_model':context.describe()}
