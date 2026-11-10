"""Compare Film-Luts references with approved recipes, without changing catalogs."""
from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from audit_camera_blend_ratios import pictures, metrics, lab
from nx300m_lut_core import (Lut3D, apply_lut, blend_luts, cube_bytes,
                             parse_lut_file, resample_lut, BLEND_WORKING_SPACE)
from render_cxo_ratio_directions import image_of, save_srgb

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/looks/library-review/20261110-FILM_LUTS_CURRENT_COMPARISON'
PRIOR = ROOT / 'outputs/looks/library-review/20261110-TRIPLE_REVIEW'
GH = ROOT / 'scratch/film-luts-reference'
CURRENT = {
    'astia': 'nx300-fuji-astia-cxo50',
    'classic-chrome': 'nx300-fuji-classic-chrome-cxo50',
    'ektachrome-100': 'blend-metol-ektachrome-cxo85-refined',
    'hp5': 'metol-hp5',
    'portra-400': 'blend-metol-portra400-cxo60',
    'superia-400': 'blend-metol-superia400-cxo35',
    'tri-x-400': 'metol-trix400-cxo50',
    'velvia-50': 'metol-velvia50-cxo80',
}
CXO_WEIGHTS = {'astia': 50, 'classic-chrome': 50, 'hp5': 0,
               'portra-400': 60, 'superia-400': 35, 'tri-x-400': 50, 'velvia-50': 80}
PROTECTED = ['looks/catalog.json', 'lut-library/machine-catalog.json',
             'looks/blend-metol-ektachrome-cxo85-refined/source.cube']
FONT = 'C:/Windows/Fonts/msyh.ttc'
BOLD = 'C:/Windows/Fonts/msyhbd.ttc'


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resize(array, edge):
    im = Image.fromarray(np.rint(array * 255).astype('uint8'))
    im.thumbnail((edge, edge), Image.Resampling.LANCZOS)
    return np.asarray(im, dtype=np.float32) / 255


def inputs():
    photos = [(name, array, {'kind': 'previous-six'}) for name, array in pictures()]
    path = ROOT / 'outputs/looks/camera-curation/ektachrome-refinement/refinement-verification.json'
    for row in read_json(path)['local_sources']:
        with Image.open(ROOT / row['input_pixels']) as im:
            im = im.convert('RGB')
            im.thumbnail((520, 420), Image.Resampling.LANCZOS)
            array = np.asarray(im, dtype=np.float32) / 255
        photos.append((Path(row['path']).stem + ' · ' + row['label'], array,
                       dict(kind='camera-JPEG', **row)))
    return photos


def entries():
    machine = set(read_json(ROOT / 'lut-library/machine-catalog.json')['ids'])
    rows = []
    for row in read_json(PRIOR / 'review.json')['triple']['rows']:
        family = row['family']
        current = CURRENT.get(family, row['local']['id'])
        sources = {
            'local': ROOT / 'looks' / row['local']['id'] / 'source.cube',
            'cxo': ROOT / 'outputs/looks/cxo-import/all-masters' / (row['cxo']['id'] + '.cube'),
            'github': GH / row['github']['path'],
            'current': ROOT / 'looks' / current / 'source.cube',
        }
        rows.append(dict(family=family, label=row['label'], local_id=row['local']['id'],
                         current_id=current, in_camera=current in machine, sources=sources,
                         protected=family == 'ektachrome-100'))
    for family, label in [('nikon-dream', '尼康 梦幻'), ('nikon-pure', '尼康 纯净')]:
        current = ROOT / 'looks' / family / 'source.cube'
        rows.append(dict(family=family, label=label, local_id=family, current_id=family,
                         in_camera=family in machine, protected=False,
                         sources={'local': current, 'current': current,
                                  'cxo': ROOT / 'outputs/looks/cxo-import/all-masters' /
                                  ('cxo-digital-nikonz-' + family[6:] + '.cube')}))
    return rows


def load(row):
    return {key: resample_lut(parse_lut_file(path).lut, 33)
            for key, path in row['sources'].items()}


def grid(title, subtitle, rows, path, width=316, height=210):
    """Rows are (scene label, list of (column label, RGB array))."""
    path.parent.mkdir(parents=True, exist_ok=True)
    margin, header, rowheight = 22, 100, height + 59
    cols = max(len(cells) for _, cells in rows)
    canvas = Image.new('RGB', (margin*2 + width*cols, header + rowheight*len(rows)), '#11161b')
    draw = ImageDraw.Draw(canvas)
    titlefont = ImageFont.truetype(BOLD, 27)
    font = ImageFont.truetype(FONT, 16)
    small = ImageFont.truetype(FONT, 14)
    draw.text((margin, 12), title, font=titlefont, fill='#f6f1e7')
    draw.text((margin, 52), subtitle, font=font, fill='#b0bcc7')
    for n, (scene, cells) in enumerate(rows):
        y = header + n * rowheight
        draw.text((margin, y), scene, font=font, fill='#d7b566')
        for c, (label, array) in enumerate(cells):
            x = margin + c * width
            canvas.paste(image_of(array, (width-5, height)), (x, y+23))
            draw.text((x+4, y+height+28), label, font=small, fill='#f3f0e8')
    save_srgb(canvas, path)


def current_label(row):
    return '当前机内配方' if row['in_camera'] else '本地母版 · 未入机'


def scan(photos, rows):
    protected = {p: digest(ROOT/p) for p in PROTECTED}
    OUT.mkdir(parents=True, exist_ok=True)
    report = dict(schema=1, working_space=BLEND_WORKING_SPACE,
                  github_commit='af957b631a304e6be94778b96cb3395de3438e9f',
                  protected_files=protected, scenes=[dict(m, display_label=n) for n, _, m in photos],
                  method='All sources resampled to 33^3; output RGB blended in encoded sRGB. '
                  '5 percent ratio scan; descriptive metrics, no objective aesthetic score.', entries=[])
    overview = {i: [] for i in [0, 1, 2, 3, 6, 7, 10, 14]}
    numerical = [(name, resize(a, 180)) for name, a, _ in photos]
    for row in rows:
        luts = load(row)
        sources = {k: dict(path=p.relative_to(ROOT).as_posix(), sha256=digest(p),
                          native_grid=parse_lut_file(p).lut.size) for k, p in row['sources'].items()}
        record = {k: v for k, v in row.items() if k != 'sources'}
        record.update(sources=sources, scans=[])
        rendered = {k: [apply_lut(a, lut) for _, a, _ in photos] for k, lut in luts.items()}
        for i in overview:
            cells = [('原图 / 同底图', photos[i][1]), ('旧母版100%', rendered['local'][i]),
                     ('CXO100%', rendered['cxo'][i])]
            if 'github' in luts:
                cells.append(('GitHub100%', rendered['github'][i]))
            cells.append((current_label(row), rendered['current'][i]))
            overview[i].append((row['label'], cells))
        for group, indices in [('old-six', range(6)), ('local-a', range(6,12)), ('local-b', range(12,18))]:
            source_rows = []
            for i in indices:
                cells = [('原图 / 同底图', photos[i][1]), ('旧母版100%', rendered['local'][i]),
                         ('CXO100%', rendered['cxo'][i])]
                if 'github' in luts:
                    cells.append(('GitHub100%', rendered['github'][i]))
                cells.append((current_label(row), rendered['current'][i]))
                source_rows.append((photos[i][0], cells))
            grid(row['label'] + ' · 来源与现版', '桌面 sRGB 模拟；原图与所有输出使用相同曝光、白平衡和裁切',
                 source_rows, OUT/'sources'/f"{row['family']}-{group}.jpg")
        if not row['protected']:
            targets = ['github'] if 'github' in luts else ['cxo']
            if not row['in_camera'] and 'github' in luts:
                targets.append('cxo')
            for target in targets:
                left = [apply_lut(a, luts['current']) for _, a in numerical]
                right = [apply_lut(a, luts[target]) for _, a in numerical]
                for pct in range(0,101,5):
                    records = []
                    for (name, a), b, c in zip(numerical, left, right):
                        output = b*(1-pct/100)+c*pct/100
                        m = metrics(a, output, name)
                        delta = np.linalg.norm(lab(output)-lab(b), axis=-1)
                        m.update(mean_delta_E76_from_current=float(delta.mean()),
                                 p95_delta_E76_from_current=float(np.percentile(delta,95)))
                        records.append(dict(scene=name, **m))
                    record['scans'].append(dict(target=target, percent=pct, scenes=records))
                image_rows = []
                for i in [0,1,2,3,6,7,10,14]:
                    cells = [('原图', photos[i][1])]
                    for pct in [0,10,20,35,50,70,100]:
                        a, b = rendered['current'][i], rendered[target][i]
                        cells.append((f'{target} {pct}%', a*(1-pct/100)+b*pct/100))
                    image_rows.append((photos[i][0], cells))
                grid(row['label'] + ' · 比例粗筛', f'{current_label(row)} 为基底；标注为新增 {target} 的比例',
                     image_rows, OUT/'ratios'/f"{row['family']}-{target}.jpg", 274, 186)
        report['entries'].append(record)
        print('scanned ' + row['family'], flush=True)
    for i, values in overview.items():
        grid('新来源 / CXO / 当前配方 · ' + photos[i][0], 'GitHub = BigNerdCoding/Film-Luts；未入机的本地母版单独标注',
             values, OUT/'overview'/f'scene-{i:02}.jpg', 340, 212)
    if {p: digest(ROOT/p) for p in PROTECTED} != protected:
        raise RuntimeError('Approved files changed during review')
    write_json(OUT/'measurements.json', report)


def candidate(row, luts, selection):
    cxo = selection.get('base_cxo_percent')
    base = luts['current'] if cxo is None else blend_luts(luts['local'], luts['cxo'], cxo/100)
    target = selection.get('target', 'github' if 'github' in luts else 'cxo')
    pct = selection['percent']
    return blend_luts(base, luts[target], pct/100, title=row['label']+' comparison candidate'), base, target


def finish(photos, rows):
    selections = read_json(OUT/'selections.json')
    scan_report = read_json(OUT/'measurements.json')
    protected = scan_report['protected_files']
    if {p: digest(ROOT/p) for p in PROTECTED} != protected:
        raise RuntimeError('Approved source changed since scan; rebase the review')
    records, overview, highlights = [], {i: [] for i in [0,2,3,6,7,10,14]}, []
    (OUT/'candidate-cubes33').mkdir(exist_ok=True)
    for row in rows:
        luts = load(row)
        choice = selections[row['family']]
        if row['protected']:
            result, base, target = luts['current'], luts['current'], 'github'
        else:
            result, base, target = candidate(row, luts, choice)
        path = OUT/'candidate-cubes33'/f"{row['family']}.cube"
        # Protected/retained recipes copy the approved bytes, preserving their exact identity.
        same_current = bool(np.max(np.abs(result.table-luts['current'].table)) <= 1e-7)
        exact_current_bytes = same_current and parse_lut_file(row['sources']['current']).lut.size == 33
        path.write_bytes(row['sources']['current'].read_bytes() if exact_current_bytes else cube_bytes(result))
        readback = parse_lut_file(path).lut
        table_error = float(np.max(np.abs(readback.table-result.table)))
        if table_error > 1e-6 or not np.isfinite(readback.table).all():
            raise RuntimeError('Candidate failed round trip: ' + row['family'])
        # Final previews always use the exported, re-read CUBE.
        rendered = {k: [apply_lut(a, lut) for _, a, _ in photos] for k, lut in luts.items()}
        final = [apply_lut(a, readback) for _, a, _ in photos]
        chosen_label = ('保留现版' if same_current else '建议候选 · ' + choice['short'])
        for group, indices in [('old-six',range(6)),('local-a',range(6,12)),('local-b',range(12,18))]:
            image_rows=[]
            for i in indices:
                cells=[('原图 / 同底图',photos[i][1]), ('旧母版100%',rendered['local'][i]),
                       ('CXO100%',rendered['cxo'][i])]
                if 'github' in luts:
                    cells.append(('GitHub100%',rendered['github'][i]))
                cells.extend([(current_label(row),rendered['current'][i]),(chosen_label,final[i])])
                image_rows.append((photos[i][0],cells))
                if i in overview: overview[i].append((row['label'],cells))
                if row['family'] in ['astia','classic-chrome','portra-400'] and i in [0,10]:
                    highlights.append((row['label']+' · '+photos[i][0],cells))
            grid(row['label']+' · 最终建议对照',choice['reason'],image_rows,
                 OUT/'final'/f"{row['family']}-{group}.jpg",336,222)
        if not row['protected']:
            center = choice['percent']
            amounts = sorted(set([0, max(0,center-10),max(0,center-5), center,
                                  min(100,center+5),min(100,center+10),100]))
            fine_luts = {pct: blend_luts(base,luts[target],pct/100) for pct in amounts}
            image_rows=[]
            for i in [0,1,2,3,6,7,10,14]:
                cells=[('原图',photos[i][1])]
                cells += [(f'{target} {pct}%',apply_lut(photos[i][1],lut)) for pct,lut in fine_luts.items()]
                image_rows.append((photos[i][0],cells))
            grid(row['label']+' · 邻近比例复核',choice['short']+'；数值为目标来源占比',image_rows,
                 OUT/'fine'/f"{row['family']}.jpg",286,190)
        scene_metrics=[]
        for (name,source,_), prior, output in zip(photos,rendered['current'],final):
            m=metrics(source,output,name)
            a,b=lab(prior),lab(output)
            m.update(mean_delta_E76_from_current=float(np.linalg.norm(b-a,axis=-1).mean()),
                     new_high_clip_vs_current_percent=float(np.mean((output.max(-1)>=254.5/255)&(prior.max(-1)<254.5/255))*100),
                     new_low_clip_vs_current_percent=float(np.mean((output.min(-1)<=.5/255)&(prior.min(-1)>.5/255))*100),
                     contrast_L90_L10_delta=float(np.percentile(b[...,0],90)-np.percentile(b[...,0],10)
                                                -np.percentile(a[...,0],90)+np.percentile(a[...,0],10)),
                     mean_chroma_delta=float((np.linalg.norm(b[...,1:],axis=-1)-np.linalg.norm(a[...,1:],axis=-1)).mean()))
            scene_metrics.append(dict(scene=name,**m))
        grey=np.linspace(0,1,257,dtype=np.float32).reshape(-1,1,1).repeat(3,axis=2)
        gl=lab(apply_lut(grey,readback))[...,0].ravel()
        if choice.get('base_cxo_percent') is not None:
            c=choice['base_cxo_percent']/100
        else: c=CXO_WEIGHTS.get(row['family'],0)/100
        pct=choice['percent']/100
        weights=(dict(local=(1-c)*(1-pct),cxo=c*(1-pct),github=pct) if target=='github'
                 else dict(local=1-pct,cxo=pct,github=0))
        if row['protected']: weights=dict(local=.15,cxo=.85,github=0,postprocess='approved contrast/saturation .96 and OKLab refinement')
        records.append(dict(family=row['family'],label=row['label'],in_camera=row['in_camera'],selection=choice,
                            expanded_weights=weights,unchanged_current=same_current,
                            exact_current_bytes=exact_current_bytes,cube=path.relative_to(OUT).as_posix(),
                            grid=readback.size,roundtrip_max_error=table_error,
                            grey_L_decrease_max=float(max(0,-np.diff(gl).min())),scenes=scene_metrics))
        print('final '+row['family'],flush=True)
    for i, values in overview.items():
        grid('最终建议总览 · '+photos[i][0], '原图 / 旧母版 / CXO / GitHub / 当前配方 / 建议候选；详细六组图见 final',
             values,OUT/'final-overview'/f'scene-{i:02}.jpg',330,208)
    grid('三款优先审片 · 当前配方与新增来源',
         '原图 / 旧母版100% / CXO100% / GitHub100% / 当前机内配方 / 建议候选',
         highlights,OUT/'00-start-here.jpg',350,228)
    # Reuse the nine requested digital-camera styles, now on all 18 common inputs.
    ids=['nikon-dream','nikon-pure','sigma-powder-blue','sigma-forest-green','sigma-warm-gold',
         'sigma-fov-classic-blue','sigma-fov-classic-yellow','sigma-sunset-red','sigma-teal-and-orange']
    selected=[0,3,5,6,7,9,10,14,17]
    for start in range(0,9,3):
        image_rows=[]
        for key in ids[start:start+3]:
            lut=parse_lut_file(ROOT/'looks'/key/'source.cube').lut
            label=read_json(ROOT/'looks'/key/'look.json')['label']
            for a in range(0,len(selected),3):
                cells=[]
                names=[]
                for i in selected[a:a+3]:
                    cells += [('原图',photos[i][1]),(label,apply_lut(photos[i][1],lut))]
                    names.append(photos[i][0])
                image_rows.append((label+' | '+' / '.join(names),cells))
        grid('点名九款 · '+str(start+1)+'—'+str(start+3), '尼康 / 适马原版，独立审片；同名混合候选见对应 final 图',
             image_rows,OUT/'named-nine'/f'group-{start//3+1}.jpg',310,205)
    if {p:digest(ROOT/p) for p in PROTECTED} != protected:
        raise RuntimeError('Approved files changed')
    verification=dict(schema=1,scenes=len(photos),families=len(records),entries=records,
                      approved_catalogs_and_ektachrome_unchanged=True,
                      desktop_srgb_only=True,camera_runtime_verified=False,lightroom_runtime_verified=False,
                      camera_JPEG_caveat='F-drive inputs are camera JPEGs, not neutral RAW developments.',
                      github_native_source_grid=13,candidate_cube_grid=33,working_space=BLEND_WORKING_SPACE,
                      black_and_white_skin_hue_not_interpreted=True)
    write_json(OUT/'recommendations.json',verification)
    lines=['# Film-Luts 与当前定版：专项对比','',
           '12 组三方同名风格 + 尼康梦幻、纯净；旧六组和 F 盘 12 张同底图。',
           'F 盘底图为相机原 JPEG；所有结果均为桌面 sRGB 模拟。',
           'GitHub 原始 LUT 为 13³，本轮统一重采样至 33³后按编码 sRGB 输出数值混合。',
           '先扫描新增来源 0–100%、间隔5%，再看邻近档位；指标只描述剪裁、肤色和明暗，不自动决定审美。',
           'Ektachrome 最新细修保持不变。机内名单和正式包均保持当前状态。',
           '', '| 风格 | 建议配方（旧母版/CXO/GitHub） | 建议 |','|---|---|---|']
    for r in records:
        w=r['expanded_weights']
        formula='/'.join(f'{w[k]*100:g}%' for k in ['local','cxo','github'])
        lines.append(f"| {r['label']} | {formula} | {r['selection']['reason']} |")
    lines += ['', '## 文件', '',
              '- final：每款旧六组、F盘前六组/后六组，6列（尼康5列）。',
              '- final-overview：按场景汇总，适合快速筛选。',
              '- ratios / fine：粗筛与邻近比例复核。',
              '- named-nine：点名尼康、适马九款的原版效果。',
              '- candidate-cubes33：建议候选与保留现版的 CUBE33，尚未加入正式相机/LR包。',
              '- measurements.json：5%步进的18场景指标及源文件身份。',
              '- recommendations.json：精确权重、导出读回及18场景验证。',
              '', '数据来源：https://github.com/BigNerdCoding/Film-Luts',
              'commit af957b631a304e6be94778b96cb3395de3438e9f；README 的第三方资产说明随包保留。']
    (OUT/'README.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    for name in ['LICENSE','README.md']:
        (OUT/('Film-Luts-'+name)).write_bytes((GH/name).read_bytes())
    archive=OUT.with_suffix('.zip')
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for p in sorted(OUT.rglob('*')):
            if p.is_file(): z.write(p, p.relative_to(OUT))
    with zipfile.ZipFile(archive) as z:
        if z.testzip(): raise RuntimeError('Bad archive')
        count=len(z.namelist())
    print(json.dumps(dict(zip=str(archive),members=count,bytes=archive.stat().st_size)))


def four_columns(photos, rows):
    choices=read_json(OUT/'selections.json')
    dest=OUT/'four-columns'
    overview={i:[] for i in [0,3,6,10]}
    for row in rows:
        if 'github' not in row['sources']:
            continue
        luts=load(row)
        result=(luts['current'] if row['protected'] else candidate(row,luts,choices[row['family']])[0])
        rendered={k:[apply_lut(a,lut) for _,a,_ in photos] for k,lut in
                  [('local',luts['local']),('revised',result),('cxo',luts['cxo']),('github',luts['github'])]}
        first='机内旧母版100%' if row['in_camera'] else '本地原版 · 未入机'
        second=('最新细修 · 定版' if row['protected'] else
                ('修正版 · '+choices[row['family']]['short']))
        for group,indices in [('old-six',range(6)),('local-a',range(6,12)),('local-b',range(12,18))]:
            image_rows=[]
            for i in indices:
                cells=[(first,rendered['local'][i]),(second,rendered['revised'][i]),
                       ('CXO100%',rendered['cxo'][i]),('GitHub100%',rendered['github'][i])]
                image_rows.append((photos[i][0],cells))
                if i in overview: overview[i].append((row['label'],cells))
            grid(row['label']+' · 四版本对照',
                 '旧母版 / 最新修正版 / CXO100% / GitHub100%；本轮新混合仍为审片候选',
                 image_rows,dest/'by-look'/f"{row['family']}-{group}.jpg",400,262)
        print('four '+row['family'],flush=True)
    for i,values in overview.items():
        for start in [0,6]:
            grid('四版本对照 · '+photos[i][0]+' · '+str(start//6+1),
                 '旧母版 / 最新修正版 / CXO100% / GitHub100%；未入机原版已标注',
                 values[start:start+6],dest/f'scene-{i:02}-page-{start//6+1}.jpg',400,260)
    zip_path=OUT/'Four-Version-Comparisons.zip'
    with zipfile.ZipFile(zip_path,'w',zipfile.ZIP_DEFLATED) as z:
        for p in sorted(dest.rglob('*.jpg')): z.write(p,p.relative_to(dest))
    with zipfile.ZipFile(zip_path) as z:
        if z.testzip(): raise RuntimeError('Invalid four-column archive')
        print(json.dumps(dict(images=len(z.namelist()),zip=str(zip_path))))


def four_quick(photos,rows):
    choices=read_json(OUT/'selections.json')
    image_rows=[]
    for row in rows:
        if 'github' not in row['sources']: continue
        luts=load(row)
        result=luts['current'] if row['protected'] else candidate(row,luts,choices[row['family']])[0]
        source=photos[0][1]
        cells=[('机内旧母版' if row['in_camera'] else '本地原版 · 未入机',apply_lut(source,luts['local'])),
               ('修正版 · '+choices[row['family']]['short'],apply_lut(source,result)),
               ('CXO100%',apply_lut(source,luts['cxo'])),('GitHub100%',apply_lut(source,luts['github']))]
        image_rows.append((row['label'],cells))
    for start in [0,6]:
        grid('四版本对照 · 暖光人像 · '+str(start//6+1),
             '旧母版 / 最新修正版 / CXO100% / GitHub100%；新混合为候选，Ektachrome保持定版',
             image_rows[start:start+6],OUT/f'four-quick-{start//6+1}.jpg',400,260)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('stage',choices=['scan','final','four','quick'])
    args=parser.parse_args()
    if args.stage in ('four','quick'):
        from render_established_four_columns import main as established_four_columns
        established_four_columns()
    else:
        photos, rows=inputs(),entries()
        if args.stage=='scan': scan(photos,rows)
        else: finish(photos,rows)
