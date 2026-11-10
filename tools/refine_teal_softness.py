"""Compare stronger CXO teal with explicit low-contrast, low-chroma finishing."""
import json

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from audit_camera_blend_ratios import DEST, metrics, pictures
from nx300m_lut_core import Lut3D, apply_lut, blend_luts, cube_bytes, identity_lut, parse_lut_file
from render_cxo_ratio_directions import image_of, save_srgb
from review_camera_palette import ROOT


def soften(lut, saturation=.85, contrast=.92, mid_lift=.025):
    # Creative finishing in encoded sRGB, with a convex chroma reduction.
    grey=lut.table @ np.array([.2126,.7152,.0722],dtype=np.float32)
    pixels=grey[...,None]+saturation*(lut.table-grey[...,None])
    pixels=(pixels-.5)*contrast+.5+mid_lift
    return Lut3D(lut.size,np.clip(pixels,0,1).astype(np.float32),lut.title+' soft')


def main():
    out=DEST/'teal-softness';out.mkdir(parents=True,exist_ok=True)
    cxo=parse_lut_file(ROOT/'looks/cxo-cinetealandorange/source.cube').lut
    old=parse_lut_file(ROOT/'looks/lr-custom-a7602b1fc8661cdb132d707b6d613007/source.cube').lut
    originals=pictures()
    variants=[('原图',None),('原机内青橙',old),('CXO 100%',cxo)]
    specs=[]
    for amount,soft in [(40,False),(60,False),(55,True),(60,True),(65,True)]:
        lut=blend_luts(identity_lut(),cxo,amount/100,title=f'CXO Teal {amount}')
        if soft:lut=soften(lut)
        key=f'cxo-teal-{amount}'+('-soft' if soft else '-raw')
        path=out/(key+'.cube');path.write_bytes(cube_bytes(lut))
        loaded=parse_lut_file(path).lut
        variants.append((f'CXO {amount}%'+(' 柔和' if soft else ' 原比例'),loaded))
        specs.append(dict(id=key,percent_cxo=amount,cube=path.relative_to(ROOT).as_posix(),
                          saturation=.85 if soft else 1,contrast=.92 if soft else 1,
                          mid_lift_srgb=.025 if soft else 0,
                          measurements=[dict(scene=scene,**metrics(original,apply_lut(original,loaded),scene))
                                        for scene,original in originals]))
    width,height,header,row_height,margin=285,196,100,250,24
    canvas=Image.new('RGB',(margin*2+width*len(variants),header+len(originals)*row_height),'#11161b')
    draw=ImageDraw.Draw(canvas)
    title=ImageFont.truetype('C:/Windows/Fonts/msyhbd.ttc',28)
    font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',17)
    draw.text((margin,14),'青橙提高占比 + 柔和处理',font=title,fill='#f5f1e8')
    draw.text((margin,56),'柔和版：饱和0.85，反差0.92，中灰+0.025；编码sRGB颜色表处理。曝光与白平衡一致。',font=font,fill='#aeb8c2')
    for row,(scene,original) in enumerate(originals):
        top=header+row*row_height
        draw.text((margin,top),scene,font=font,fill='#d9b86a')
        for col,(name,lut) in enumerate(variants):
            left=margin+col*width
            pixels=original if lut is None else apply_lut(original,lut)
            canvas.paste(image_of(pixels,(width-4,height)),(left,top+25))
            draw.text((left+5,top+height+28),name,font=font,fill='#f5f1e8')
    save_srgb(canvas,out/'comparison.jpg')
    (out/'manifest.json').write_text(json.dumps(dict(schema=1,working_space='encoded sRGB',entries=specs),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(out)


if __name__=='__main__':main()
