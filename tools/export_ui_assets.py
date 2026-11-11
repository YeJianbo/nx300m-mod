"""Rebuild embedded references and the camera lens list from local sources."""
from pathlib import Path
import re
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
REFERENCE_SIZE=(144,84)
REFERENCE_FILES=('lut-reference.png','lut-reference-street.png','lut-reference-landscape.png')

def export_references():
    assets=ROOT/'mod/metol/assets'
    images=[]
    for name in REFERENCE_FILES:
        with Image.open(assets/name) as image:
            if image.size!=REFERENCE_SIZE:raise ValueError('Reference dimensions: '+name)
            images.append(image.convert('RGB').tobytes())
    width,height=REFERENCE_SIZE
    header=f'/* Photographic references; source credits in README.md. */\n#define NX_REF_W {width}\n#define NX_REF_H {height}\n#define NX_REF_COUNT {len(images)}\nstatic const unsigned char nx_reference_rgb[NX_REF_COUNT][NX_REF_W*NX_REF_H*3] = {{\n'
    for data in images:
        header+='{\n'+''.join(','.join(map(str,data[i:i+32]))+',\n' for i in range(0,len(data),32))+'},\n'
    (assets/'lut_reference_rgb.h').write_text(header+'};\n',encoding='ascii')

def build():
    export_references()
    source=(ROOT/'mod/nxconnect/NXConnect/app/src/main/java/com/nxwa/nxconnect/ui/LensPresets.kt').read_text(encoding='utf-8')
    records=re.findall(r'LensPreset\("([^"]+)", "[^"]+", "([^"]+)", ([\d.]+), ([\d.]+), [\d.]+, "[^"]+"\)',source)
    if not records:raise ValueError('No NXConnect lens presets')
    text=''.join(f'{key}|{label}|{int(float(lo))}|{int(float(hi))}\n' for key,label,lo,hi in records)
    (ROOT/'mod/deployment/mfw/lenses.txt').write_text(text,encoding='utf-8',newline='\n')
    return len(records)

if __name__=='__main__':print(f'Exported references and {build()} NXConnect lens presets')
