"""Compare the reference arithmetic with the existing C hook, and its Jacobian."""
import json
from pathlib import Path
import subprocess

import numpy as np

from build_lut_live_loader import wsl
from fit_ipcm_preview import ROOT, identity_model, round_away, strength_model
from ipcm_color_model import ColorModel
from ipcm_preview_fit import from_model, forward, tonal_features

OUT=ROOT/'outputs/deployment/ipcm-preview/evaluation/tonal/math'


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    source=OUT/'native-arithmetic.c'
    source.write_text('#include <stdio.h>\n#include "'+wsl(ROOT/'runtime/ipcm_preview_blob.c')+'"\n'+r'''
int main(void) {
    unsigned strength, stock[9], result[9]; int profile[9];
    while(scanf("%u",&strength)==1) {
        for(int i=0;i<9;i++) if(scanf("%d",&profile[i])!=1)return 2;
        for(int i=0;i<9;i++) if(scanf("%u",&stock[i])!=1)return 2;
        nx_ipcm_config.strength=strength;
        int ok=matrix(result,stock,profile,3);
        printf("%d",ok);
        for(int i=0;i<9;i++)printf(" %u",ok?result[i]:stock[i]);
        for(int i=0;i<256;i++)printf(" %d",coefficient(255-i,i));
        printf("\n");
    }
    return 0;
}
''',encoding='utf-8')
    binary=OUT/'native-arithmetic.host'
    runner=['wsl.exe','-d','Debian','-e']
    built=subprocess.run([*runner,'gcc','-std=c99','-O2','-Wno-int-to-pointer-cast','-Wno-pointer-to-int-cast',wsl(source),'-o',wsl(binary)],capture_output=True,text=True)
    if built.returncode:raise RuntimeError(f'gcc exit {built.returncode}: {built.stderr}')
    rng=np.random.default_rng(85219);cases=[]
    for index in range(303):
        strength=index%101
        stock=rng.integers(-600,601,(3,3))
        profile=rng.integers(-16384,16385,(3,3))
        if index%3==0:stock=np.rint(ColorModel().stock_ycc).astype(int)
        cases.append((strength,stock,profile))
    payload='\n'.join(' '.join(map(str,[s,*p.ravel(),*(m.ravel().astype(np.int64)&0xffffffff)])) for s,m,p in cases)+'\n'
    ran=subprocess.run([*runner,wsl(binary)],input=payload,capture_output=True,text=True)
    if ran.returncode:raise RuntimeError(f'C arithmetic exit {ran.returncode}: {ran.stderr}')
    rows=ran.stdout.splitlines()
    if len(rows)!=len(cases):raise ValueError('Missing arithmetic output')
    for row,(s,stock,p) in zip(rows,cases):
        words=np.array([int(n) for n in row.split()],dtype=np.int64)
        effective=round_away((np.eye(3)*4096*(100-s)+p*s)/100)
        expected,fallback=ColorModel.compose(effective,stock)
        actual=words[1:10].astype(np.uint32).view(np.int32).reshape(3,3)
        if bool(words[0])==fallback or not np.array_equal(actual,expected):raise ValueError('C/Python matrix mismatch')
        model=identity_model();model['tone']=list(range(255,-1,-1))
        if words[10:].tolist()!=strength_model(model,s/100)['tone']:raise ValueError('Strength rounding mismatch')
    x=rng.uniform(.05,.9,(21,3))
    p=from_model(identity_model(),full_post=True)
    p[:9]=(np.eye(3)+rng.normal(0,.02,(3,3))).ravel()
    p[9:18]=(np.eye(3)+rng.normal(0,.02,(3,3))).ravel()
    p[18:]=np.linspace(0,1,33)**1.1
    direction=rng.normal(0,.005,len(p));eps=1e-6
    derivatives={}
    for gamma in ('code','srgb','stock-knots'):
        context=ColorModel(gamma)
        y,j=forward(x,p,.75,True,color_model=context)
        _,jcolor=tonal_features(y,x,True)
        prediction=jcolor@j@direction
        plus=tonal_features(forward(x,p+eps*direction,.75,color_model=context),x)
        minus=tonal_features(forward(x,p-eps*direction,.75,color_model=context),x)
        error=float(np.max(np.abs(prediction-(plus-minus)/(2*eps))))
        if error>1e-6:raise ValueError('Tonal chain Jacobian mismatch: '+gamma)
        derivatives[gamma]=error
    result={'passed':True,'c_source':'runtime/ipcm_preview_blob.c included unmodified',
            'arithmetic_cases':len(cases),'tone_strength_comparisons':len(cases)*256,
            'derivative_max_error':derivatives,'hardware_pixels_tested':False}
    (OUT/'report.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result),flush=True)


if __name__=='__main__':main()
