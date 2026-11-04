"""Rebuild embedded references and the camera lens list from local sources."""
from pathlib import Path
import re
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]

def build():
    assets=ROOT/'mod/metol/assets'
    names=('lut-reference.png','lut-reference-landscape.png','lut-reference-night.png')
    images=[Image.open(assets/name).convert('RGB').tobytes() for name in names]
    if any(len(data)!=192*112*3 for data in images):raise ValueError('Reference dimensions')
    header='/* Photographic references; source credits in README.md. */\n#define NX_REF_W 192\n#define NX_REF_H 112\n#define NX_REF_COUNT 3\nstatic const unsigned char nx_reference_rgb[NX_REF_COUNT][NX_REF_W*NX_REF_H*3] = {\n'
    for data in images:
        header+='{\n'+''.join(','.join(map(str,data[i:i+32]))+',\n' for i in range(0,len(data),32))+'},\n'
    (assets/'lut_reference_rgb.h').write_text(header+'};\n',encoding='ascii')
    source=(ROOT/'mod/nxconnect/NXConnect/app/src/main/java/com/nxwa/nxconnect/ui/LensPresets.kt').read_text(encoding='utf-8')
    records=re.findall(r'LensPreset\("([^"]+)", "[^"]+", "([^"]+)", ([\d.]+), ([\d.]+), [\d.]+, "[^"]+"\)',source)
    if not records:raise ValueError('No NXConnect lens presets')
    text=''.join(f'{key}|{label}|{int(float(lo))}|{int(float(hi))}\n' for key,label,lo,hi in records)
    (ROOT/'mod/deployment/mfw/lenses.txt').write_text(text,encoding='utf-8',newline='\n')
    return len(records)

if __name__=='__main__':print(f'Exported references and {build()} NXConnect lens presets')
