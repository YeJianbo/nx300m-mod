"""Exercise measured-response plumbing and joint fitting using synthetic data.

No hardware colour claim: this checks calculus, fitting, integer profile IO,
and capture validation before collecting genuine native response samples.
"""
import json
from pathlib import Path
import numpy as np

from ipcm_pcse import calibrate, fit, joint_forward, Response
from ipcm_preview_fit import from_model
from fit_ipcm_preview import identity_model, write_profile, read_profile, apply_profile

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/deployment/ipcm-preview/pcse-fit-fixture'


def true_response(rgb):
    r,g,b=rgb.T
    result=np.zeros((len(rgb),24,3))
    result[:,0,0]=.008*r*(1-r)*(2*g-1)
    result[:,1,1]=.008*g*(1-g)*(2*b-1)
    result[:,2,2]=.008*b*(1-b)*(2*r-1)
    return result


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    x=np.random.default_rng(48521).random((3072,3))
    response=true_response(x);step=np.full(24,64.)
    plus=x[None]+response.transpose(1,0,2)*step[:,None,None]
    minus=x[None]-response.transpose(1,0,2)*step[:,None,None]
    capture=OUT/'synthetic-captures.npz'
    np.savez_compressed(capture,baseline_before=x,baseline_after=x,plus_rgb=plus,minus_rgb=minus,step=step,
        metadata=json.dumps({'camera':'NX300M','firmware':'1.15','source_kind':'synthetic','meaning':'Polynomial response fixture; no ISP pixels'}))
    calibrated=calibrate(capture,degree=2);calibrated.save(OUT/'synthetic-response.npz')
    calibrated=Response.load(OUT/'synthetic-response.npz')
    model=identity_model();model.pop('chroma_q12');model['ycc_q12']=(np.eye(3,dtype=int)*4096).ravel().tolist()
    base=from_model(model,full_post=True);nb=len(base)
    p=np.concatenate([base,np.array([.2,-.1,.1]+[0.]*21)])
    points=np.random.default_rng(67231).uniform(.1,.9,(19,3))
    direction=np.random.default_rng(87121).normal(0,.02,len(p));eps=1e-6
    output,jac=joint_forward(points,p,nb,calibrated,.75,True)
    finite=(joint_forward(points,p+eps*direction,nb,calibrated,.75)-
            joint_forward(points,p-eps*direction,nb,calibrated,.75))/(2*eps)
    derivative_error=float(np.max(np.abs(finite-jac@direction)))
    if derivative_error>1e-7:raise ValueError('Joint Jacobian differs: '+str(derivative_error))
    axis=np.linspace(0,1,17)
    grid=np.array([(r,g,b) for b in axis for g in axis for r in axis])
    desired=np.array([20.,-12.,8.]+[0.]*21)
    target=np.clip(grid+np.einsum('npo,p->no',true_response(grid),desired),0,1)
    fitted,report=fit((17,(0.,)*3,(1.,)*3,target),model,calibrated,iterations=12)
    write_profile(OUT/'profile.bin',fitted)
    if read_profile(OUT/'profile.bin')!=fitted:raise ValueError('Profile serialization mismatch')
    full=report['errors']['100']
    if report['baseline_retained'] or not np.any(fitted.get('pcse_delta',[])) or full['candidate']['mean_absolute_rgb_8bit']>=.8*full['baseline']['mean_absolute_rgb_8bit']:
        raise ValueError('Joint solver did not recover useful synthetic native response')
    # Evaluation must not silently ignore the new native stage.
    try:apply_profile(points,fitted)
    except ValueError:pass
    else:raise ValueError('PCSE pixel evaluation silently ignored missing response')
    result={'passed':True,'source_kind':'synthetic','hardware_pixels_tested':False,
            'joint_directional_derivative_max_error':derivative_error,
            'response_fit':calibrated.metadata,'fit':report,'model':fitted}
    (OUT/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'passed':True,'source_kind':'synthetic','derivative_error':derivative_error,
                      'error100':full,'native_deltas':fitted['pcse_delta']}),flush=True)


if __name__=='__main__':main()
