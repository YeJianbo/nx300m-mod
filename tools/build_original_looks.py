"""Generate original CC0 creative curves; no imported profiles or stock tables."""
from pathlib import Path
import hashlib
import json
import struct

from cube_to_d4 import compile_cube

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/looks/original-release'
RECIPES = (
    ('nx300-adjust-base', '中性微调', 1.0, 1.0, (0., 0., 0.), 0.),
    ('nx300-hard-mono', '硬调黑白', 1.05, 0.0, (0., 0., 0.), 0.),
    ('nx-original-sunlight', '暖阳', 1.04, 0.92, (0.026, 0.008, -0.020), 0.010),
    ('nx-original-clearblue', '晴蓝', 1.03, 0.90, (-0.012, 0.009, 0.025), 0.012),
    ('nx-original-softcolor', '柔彩', 0.90, 0.82, (0.012, 0.002, 0.006), 0.025),
    ('nx-original-coolnight', '冷夜', 1.10, 0.80, (-0.020, 0.002, 0.023), 0.006),
)


def transform(rgb, contrast, saturation, tint, lift):
    luma = sum(a*b for a,b in zip(rgb, (0.2126, 0.7152, 0.0722)))
    result = []
    for value, bias in zip(rgb, tint):
        value = luma + (value-luma)*saturation
        value = (value-.5)*contrast+.5
        value += bias*4*max(0., value*(1-value))
        value += lift*(1-max(0., min(1., value)))**2
        result.append(max(0., min(1., value)))
    return tuple(result)


def build():
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    report = []
    for key, label, contrast, saturation, tint, lift in RECIPES:
        folder = OUT / key
        folder.mkdir(exist_ok=True)
        values = [transform((r/16,g/16,b/16), contrast,saturation,tint,lift)
                  for b in range(17) for g in range(17) for r in range(17)]
        master = 'TITLE "'+label+'"\nLUT_3D_SIZE 17\n' + ''.join(' '.join(f'{v:.9f}' for v in rgb)+'\n' for rgb in values)
        (folder/'source.cube').write_text(master, encoding='utf-8', newline='\n')
        # The identity lattice is exact; other recipes use the normal fit path.
        if key == 'nx300-adjust-base':
            words=[]
            for channel in range(3):
                words.extend(round((r,g,b)[channel]*1023/8) for r in range(9) for g in range(9) for b in range(9))
                words.append(0)
            table=struct.pack('<2190H',*words)
        else:
            table=compile_cube((17,(0.,)*3,(1.,)*3,values),method='fit')
        (folder/'table.d4lut').write_bytes(table)
        metadata={'id':key,'label':label,'source_kind':'original-procedural', 'license':'CC0-1.0',
                  'input_space':'srgb','output_space':'srgb','native_table':'table.d4lut',
                  'native_format':{'grid':9,'channels':3,'bits':10,'bytes':4380},
                  'recipe':{'contrast':contrast,'saturation':saturation,'tint':tint,'lift':lift},
                  'compiler':{'method':'identity' if key=='nx300-adjust-base' else 'fit',
                              'tables':{'table.d4lut':hashlib.sha256(table).hexdigest()}},
                  'calibration':{'camera':'NX300M','status':'unmeasured'}}
        (folder/'look.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        if key != 'nx300-adjust-base':
            rows.append(key+'|'+label+'|0|0|0|0|0|0|0\n')
        report.append({'id':key,'label':label,'sha256':hashlib.sha256(table).hexdigest()})
    (OUT/'looks.txt').write_text(''.join(rows),encoding='utf-8',newline='\n')
    (OUT/'LICENSE.txt').write_text('Original procedural LUT recipes and generated LUT data are dedicated to the public domain under CC0 1.0.\nhttps://creativecommons.org/publicdomain/zero/1.0/\n',encoding='utf-8')
    (OUT/'manifest.json').write_text(json.dumps({'license':'CC0-1.0','looks':report},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
    return OUT


if __name__ == '__main__': build()
