"""Recompose the measured source sheets: original, established mix, CXO, GitHub."""
from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from compare_film_luts_current import ROOT, OUT, CURRENT, entries, read_json, digest, write_json
from render_cxo_ratio_directions import save_srgb

W,H,STEP,MARGIN,HEADER=316,210,269,22,100
FONT=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',14)
TITLE=ImageFont.truetype('C:/Windows/Fonts/msyhbd.ttc',25)
LABEL=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',16)


def header(canvas,title,subtitle):
    draw=ImageDraw.Draw(canvas)
    draw.rectangle((0,0,canvas.width,HEADER-1),fill='#11161b')
    draw.text((MARGIN,12),title,font=TITLE,fill='#f6f1e7')
    draw.text((MARGIN,53),subtitle,font=LABEL,fill='#b0bcc7')


def source_row(row,group,idx):
    path=OUT/'sources'/f"{row['family']}-{group}.jpg"
    with Image.open(path) as im:
        if im.size!=(1624,1714): raise ValueError('Unexpected source layout: '+str(path))
        result=Image.new('RGB',(MARGIN*2+W*4,STEP),'#11161b')
        y=HEADER+idx*STEP
        result.paste(im.crop((0,y,im.width,y+23)),(0,0))
        # Local original, approved current recipe, pure CXO, pure GitHub.
        mixed=row['family'] in CURRENT and row['current_id']!=row['local_id']
        for c,src in enumerate([1,4,2,3]):
            x=MARGIN+c*W
            if c==1 and not mixed:
                d=ImageDraw.Draw(result)
                d.text((x+27,110),'无此前既定混合版',font=LABEL,fill='#9ba8b5')
            else:
                result.paste(im.crop((MARGIN+src*W,y+23,MARGIN+(src+1)*W,y+H+23)),(x,23))
            text=(['最初原版' if row['in_camera'] else '最初本地原版 · 未入机',
                   ('此前CXO混合 · 最新细修定版' if row['protected'] else '此前CXO混合处理版')
                   if mixed else '无此前既定混合版','CXO100%','GitHub100%'][c])
            ImageDraw.Draw(result).text((x+4,H+28),text,font=FONT,fill='#f3f0e8')
        return result


def main():
    rows=[e for e in entries() if 'github' in e['sources']]
    dest=OUT/'four-columns'
    (dest/'by-look').mkdir(parents=True,exist_ok=True)
    definitions=[]
    for row in rows:
        mixed=row['family'] in CURRENT and row['current_id']!=row['local_id']
        for group in ['old-six','local-a','local-b']:
            canvas=Image.new('RGB',(MARGIN*2+W*4,HEADER+6*STEP),'#11161b')
            header(canvas,row['label']+' · 四版本对照',
                   '最初原版 / 此前CXO混合处理版 / CXO100% / GitHub100%')
            for i in range(6): canvas.paste(source_row(row,group,i),(0,HEADER+i*STEP))
            save_srgb(canvas,dest/'by-look'/f"{row['family']}-{group}.jpg")
        definitions.append(dict(family=row['family'],label=row['label'],
                                original=row['sources']['local'].relative_to(ROOT).as_posix(),
                                established=row['sources']['current'].relative_to(ROOT).as_posix() if mixed else None,
                                established_sha256=digest(row['sources']['current']) if mixed else None,
                                cxo=row['sources']['cxo'].relative_to(ROOT).as_posix(),
                                github=row['sources']['github'].relative_to(ROOT).as_posix(),
                                new_suggestions_used=False))
    for scene,group,idx in [(0,'old-six',0),(3,'old-six',3),(6,'local-a',0),(10,'local-a',4)]:
        for start in [0,6]:
            canvas=Image.new('RGB',(MARGIN*2+W*4,HEADER+6*STEP),'#11161b')
            header(canvas,'四版本场景总览 · '+str(scene)+' · '+str(start//6+1),
                   '最初原版 / 此前CXO混合处理版 / CXO100% / GitHub100%')
            for j,row in enumerate(rows[start:start+6]):
                tile=source_row(row,group,idx)
                d=ImageDraw.Draw(tile);d.rectangle((0,0,tile.width,22),fill='#11161b')
                d.text((MARGIN,0),row['label'],font=LABEL,fill='#d7b566')
                canvas.paste(tile,(0,HEADER+j*STEP))
            save_srgb(canvas,dest/f'scene-{scene:02}-page-{start//6+1}.jpg')
    mixed=[r for r in rows if r['family'] in CURRENT and r['current_id']!=r['local_id']]
    missing=[r for r in rows if r not in mixed]
    for number,subset in enumerate([mixed[:4],mixed[4:],missing],1):
        canvas=Image.new('RGB',(MARGIN*2+W*4,HEADER+len(subset)*STEP),'#11161b')
        header(canvas,'最初原版与此前CXO处理版 · '+str(number),
               '四列严格固定；本轮新建议不参与此图')
        for j,row in enumerate(subset):
            tile=source_row(row,'old-six',0)
            d=ImageDraw.Draw(tile);d.rectangle((0,0,tile.width,22),fill='#11161b')
            d.text((MARGIN,0),row['label'],font=LABEL,fill='#d7b566')
            canvas.paste(tile,(0,HEADER+j*STEP))
        save_srgb(canvas,OUT/f'four-established-{number}.jpg')
        if number<=2: shutil.copyfile(OUT/f'four-established-{number}.jpg',OUT/f'four-quick-{number}.jpg')
    write_json(dest/'column-definitions.json',dict(columns=['最初原版','此前CXO混合处理版','CXO100%','GitHub100%'],
                                                 entries=definitions,new_suggestions_used=False))
    (dest/'README.md').write_text(
        '四列为最初原版、此前既定CXO混合处理版、CXO100%、GitHub100%。\n'
        '第二列仅使用正式既定的 source.cube，不使用本轮任何新建议。Ektachrome为最新细修定版。\n'
        'Acros、HP5、Pro Neg Std、Provia、数字Velvia没有此前既定CXO混合版，第二列留空。\n'
        '12款，每款旧六组、F盘十二张。F盘底图是原机JPEG，结果为桌面sRGB模拟。\n',encoding='utf-8')
    path=OUT/'Four-Version-Established.zip'
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:
        for p in sorted(dest.rglob('*')):
            if p.is_file(): z.write(p,p.relative_to(dest))
    with zipfile.ZipFile(path) as z:
        if z.testzip(): raise RuntimeError('Bad ZIP')
        count=sum(n.endswith('.jpg') for n in z.namelist())
    shutil.copyfile(path,OUT/'Four-Version-Comparisons.zip')
    print(json.dumps(dict(images=count,established_mixes=len(mixed),no_established_mix=len(missing),
                          new_suggestions_used=False,zip=str(path))))


if __name__=='__main__': main()
