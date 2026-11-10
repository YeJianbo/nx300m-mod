"""Publish the reviewed camera selection, native tables, cubes and LR looks."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from audit_camera_blend_ratios import DEST as AUDIT, pictures
from build_lumix_lightroom_profiles import ProfileSource, _encode_table, _resample, _verify_xmp, _xmp
from look_library import LookLibrary
from lut_resample import sample_table
from lut_table_data import read_tables
from manage_local_lut_library import scan, sync
from nx300m_lut_core import apply_lut, blend_luts, cube_bytes, identity_lut, parse_lut_file, resample_lut
from render_cxo_ratio_directions import SAMPLES, image_of, save_srgb
from review_camera_palette import CXO, OUT, ROOT
from refine_teal_softness import soften
from refine_ektachrome import KEY as EKTACHROME, LABEL as EKTACHROME_LABEL, PARAMETERS as EKTACHROME_PARAMETERS, refine
from render_four_target_ab import DEST as AB, trix_curve
from compare_film_luts_current import OUT as LIBRARY_REVIEW, grid, inputs
from curated_palette_choices import GROUPS, LABELS, REASONS, REVIEWED_IMPORTS, RECHECK, ledger


GROUP = 'NX300M 精选配方'
DECISIONS = [
    ('blend-metol-portra400-cxo50', 'blend-metol-portra400-cxo60', 'Portra 400 · CXO 60%', 60,
     '60% 收住现有灰黄与黑位抬升；70% 肤色与白色高光更浓亮，50% 尚有灰黄。'),
    ('blend-metol-ektachrome-cxo50', EKTACHROME, EKTACHROME_LABEL, 85,
     '用户定版：原版15%+CXO85%；混合后饱和度0.96、反差0.96，局部暖色、暗部和青色细修。'),
    ('blend-metol-superia400-cxo50', 'blend-metol-superia400-cxo35', 'Superia 400 · CXO 35%', 35,
     '35% 减轻现有红色增益，保留暖色与街景密度；50% 以上面颊黄色进一步减少，画面更冷亮。'),
    ('blend-cxo-teal-orange-mild50', 'blend-cxo-teal-orange-cxo60', '青橙 · CXO 60%', 60,
     '原图40%+CXO60%，提高青橙分色；55%较轻，65%以上橙肤色进一步增强。'),
]

AB_FINAL = {
    'blend-metol-portra400-cxo60': ('portra-400', 'blend-metol-portra400-cxo50',
        'Portra 400 · CXO 50%', '用户选择B：肤色红感减轻，接受更柔和的黑白端与中间调。'),
    'metol-velvia50-cxo80': ('velvia-50', 'metol-velvia50-cxo70',
        'Velvia 50 · CXO 70%', '用户选择B：偏风景与浓彩用途，保留更强的色彩和反差。'),
    'metol-trix400-cxo50': ('tri-x-400', 'metol-trix400-cxo50-refined',
        'Tri-X 400 · CXO 50% 细修', '用户选择B：保持50%混合与黑白端点，微调中间调曲线。'),
}


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def import_reviewed(library):
    records=[]
    for key,label,relative,reason in REVIEWED_IMPORTS:
        source=ROOT/relative
        if not (library.directory(key)/'look.json').exists():
            library.add_cube(source,key,label=label,input_space='srgb',description=reason,method='fit')
        actual=parse_lut_file(library.directory(key)/'source.cube').lut
        expected=parse_lut_file(source).lut
        if actual.size != expected.size or np.max(np.abs(actual.table-expected.table)) > 1e-7:
            raise ValueError('Imported look differs from reviewed source: '+key)
        records.append(dict(id=key,reviewed_source=source.relative_to(ROOT).as_posix(),
                            reviewed_sha256=sha(source),native_source_grid=expected.size))
    return records


def selection_previews(library):
    photos=inputs()
    paths=[]
    seasonal={
        'lr-custom-d7bd9f1cd3f2f3c15a6a231ef36955c0':13,
        'lr-custom-d47cc96ee6e5196e2313ed25c45c498c':6,
        'lr-custom-bb2eed1b68c4d7183ac6bb60020fba2b':6,
        'lr-custom-720de2dc0ef97b8c94c90b5073455e8b':14,
        'lr-artistic-03':6,
        'cxo-cineautumn':6,
        'cxo-cinedesaturatedgreen':6,
        'panasonic-lumix-dusk-moss':6,
    }
    for group_index,(group,keys) in enumerate(GROUPS,1):
        for start in range(0,len(keys),4):
            rows=[]
            for key in keys[start:start+4]:
                lut=parse_lut_file(library.directory(key)/'source.cube').lut
                label=LABELS[key]
                i=seasonal.get(key,3)
                _,a,_=photos[1]
                scene,b,_=photos[i]
                rows.append((label,[('原图 · 自然光人像',a),('配方100% · 人像',apply_lut(a,lut)),
                                    ('原图 · '+scene,b),('配方100% · 场景',apply_lut(b,lut))]))
            path=OUT/'selection-overview'/f'{group_index:02d}-{start//4+1}.jpg'
            grid(group+' · 入机配方 '+str(start//4+1),
                 '原图 / 入机母版100%；sRGB桌面预览，曝光与白平衡相同。',rows,path,370,230)
            paths.append(path)
    for path in (OUT/'selection-overview').glob('*.jpg'):
        if path not in paths:
            path.unlink()
    return paths


def recipe_specs():
    fixed = json.loads((CXO/'recipes/manifest.json').read_text(encoding='utf-8'))
    specs = []
    for entry in fixed['recipes']:
        a_path, b_path = ROOT/entry['existing_cube'], ROOT/entry['cxo_cube']
        if sha(a_path) != entry['existing_sha256'] or sha(b_path) != entry['cxo_cube_sha256']:
            raise ValueError('Approved source changed: '+entry['id'])
        specs.append(dict(id=entry['id'], label=entry['label'], a_path=a_path, b_path=b_path,
                          a_id=a_path.parent.name, b_id=Path(entry['cxo_cube']).stem,
                          percent_b=entry['cxo_percent'], authority='user-selected',
                          approved_cube=CXO/'recipes'/entry['cube'], reason='沿用用户已定比例。'))
    candidates = {entry['id']: entry for entry in json.loads((OUT/'candidates/manifest.json').read_text(encoding='utf-8'))['entries']}
    for candidate_id, key, label, percent, reason in DECISIONS:
        entry = candidates[candidate_id]
        for side in ('a', 'b'):
            if entry[side+'_cube'] and sha(ROOT/entry[side+'_cube']) != entry[side+'_sha256']:
                raise ValueError('Measured source changed: '+candidate_id)
        specs.append(dict(id=key, label=label, a_path=ROOT/entry['a_cube'] if entry['a_cube'] else None,
                          b_path=ROOT/entry['b_cube'], a_id=entry['a_id'], b_id=entry['b_id'],
                          percent_b=percent, authority='visual-choice-after-5-percent-sweep',
                          audit_id=candidate_id, reason=reason))
        if key == EKTACHROME:
            specs[-1]['authority'] = 'user-selected'
            specs[-1]['finishing'] = dict(saturation=.96, contrast=.96, mid_lift=0)
            specs[-1]['refinement'] = EKTACHROME_PARAMETERS
            specs[-1]['approved_cube'] = OUT/'ektachrome-refinement'/(EKTACHROME+'.cube')
    reviewed = {e['family']: e for e in json.loads((AB/'verification.json').read_text(encoding='utf-8'))['entries']}
    for spec in specs:
        if spec['id'] not in AB_FINAL:
            continue
        family, key, label, reason = AB_FINAL[spec['id']]
        row = reviewed[family]
        approved = AB/row['B_cube']
        if sha(approved) != row['B_sha256']:
            raise ValueError('Reviewed A/B file changed: '+family)
        for side, kind in [('a','local'),('b','cxo')]:
            snapshot = AB/row['sources'][kind]['file']
            if sha(snapshot) != row['sources'][kind]['sha256']:
                raise ValueError('Reviewed A/B source changed: '+family)
            spec[side+'_path'] = snapshot
        spec.update(id=key,label=label,reason=reason,percent_b=row['B_cxo_percent'],
                    authority='user-selected-after-18-scene-ab',approved_cube=approved,
                    ab_family=family,approved_sha256=row['B_sha256'])
        if family == 'tri-x-400':
            spec['output_curve'] = 'y=x-0.012*sin(2*pi*t)*sin(pi*t)^2; t=clip((x-0.14)/0.70,0,1)'
    return specs


def preview(spec, library, photos):
    a = identity_lut() if spec['a_path'] is None else parse_lut_file(spec['a_path']).lut
    b = parse_lut_file(spec['b_path']).lut
    final = parse_lut_file(library.directory(spec['id'])/'source.cube').lut
    old = a
    if spec['id'] == 'blend-cxo-teal-orange-cxo60':
        old = parse_lut_file(ROOT/'looks/lr-custom-a7602b1fc8661cdb132d707b6d613007/source.cube').lut
    width, height, margin, header, row_height = 370, 222, 26, 128, 278
    canvas = Image.new('RGB', (margin*2+width*4, header+row_height*len(photos)), '#11161b')
    draw = ImageDraw.Draw(canvas)
    title = ImageFont.truetype('C:/Windows/Fonts/msyhbd.ttc', 29)
    font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 17)
    draw.text((margin, 14), spec['label'], font=title, fill='#f5f1e8')
    draw.text((margin, 57), '原图 / 原机内风格 / CXO 100% / 本次配方；sRGB 桌面模拟。', font=font, fill='#aeb8c2')
    label = f"现有 {100-spec['percent_b']}% + CXO {spec['percent_b']}%"
    if 'finishing' in spec:
        label += ' · 反差/饱和 0.96'
    if 'refinement' in spec:
        label = '原版15% + CXO85% · 细修定版'
    if spec['id'] == 'blend-cxo-teal-orange-cxo60':
        label = '原图 40% + CXO 60%'
    if 'output_curve' in spec:
        label += ' · 中间调细修'
    for row, (scene, source) in enumerate(photos):
        top = header+row*row_height
        draw.text((margin, top-27), scene, font=font, fill='#d9b86a')
        variants = [('原图', source), ('原机内风格', apply_lut(source, old)),
                    ('CXO 100%', apply_lut(source, b)),
                    (label, apply_lut(source, final))]
        for col, (name, pixels) in enumerate(variants):
            left = margin+col*width
            canvas.paste(image_of(pixels, (width-4,height)), (left,top))
            draw.text((left+7,top+height+7),name,font=font,fill='#f5f1e8')
            if col == 3:
                draw.rectangle((left,top,left+width-5,top+height+32),outline='#d9b86a',width=2)
    path = OUT/'selected-previews'/(spec['id']+'.jpg')
    path.parent.mkdir(parents=True,exist_ok=True)
    save_srgb(canvas,path)
    return path


def exclusion_reason(key):
    records=json.loads((RECHECK/'decisions.json').read_text(encoding='utf-8'))['entries']
    return next(e['reason'] for e in records if e['id']==key)


def make_zip(path, files):
    with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for source, name in files:
            archive.write(source,name)
    with zipfile.ZipFile(path) as archive:
        bad = archive.testzip()
        if bad:
            raise ValueError('ZIP CRC failed: '+bad)
        for source,name in files:
            if archive.read(name) != source.read_bytes():
                raise ValueError('ZIP differs from source: '+name)


def native_previews(library,checks,photos):
    paths=[]
    largest=sorted(checks,key=lambda c:max(s['rgb_rmse_8bit'] for s in c['scenes']),reverse=True)[:4]
    for i,check in enumerate(largest,1):
        key=check['id'];directory=library.directory(key)
        lut=parse_lut_file(directory/'source.cube').lut
        table=read_tables(directory,library.get(key))['table.d4lut']
        worst=max(range(len(photos)),key=lambda j:check['scenes'][j]['rgb_rmse_8bit'])
        rows=[]
        for j in dict.fromkeys([worst,6]):
            scene,source=photos[j]
            coarse=sample_table(table,source.reshape(-1,3),interpolation='trilinear').reshape(source.shape)
            rows.append((scene,[('原图',source),('所选母版100%',apply_lut(source,lut)),('机内9³桌面模型',coarse)]))
        path=OUT/'native-comparisons'/f'{i:02d}.jpg'
        grid(LABELS[key]+' · 母版与9³',
             '同一配方，比较9³近似；包含本款18场景误差最大样张及F盘绿叶。',rows,path,400,255)
        paths.append(path)
    return paths


def main():
    full_review=ledger()
    library = LookLibrary()
    local_scan = scan()
    if any(local_scan[name] for name in ('changed','moved','renamed','unknown','missing','invalid','errors')):
        raise ValueError('Resolve local library edits before curation: '+json.dumps(local_scan))
    original_ids = {entry['id'] for entry in library.list()}
    old_catalog = json.loads((ROOT/'looks/catalog.json').read_text(encoding='utf-8'))
    unchanged = {key:sha(library.directory(key)/'source.cube')
                 for g in old_catalog['groups'] for key in g['ids'] if key not in AB_FINAL}
    imported=import_reviewed(library)
    review = json.loads((OUT/'review.json').read_text(encoding='utf-8'))
    before = review['before']
    write_json(OUT/'catalog-before.json',before)
    selected_ids={key for _,keys in GROUPS for key in keys}
    specs = [s for s in recipe_specs() if s['id'] in selected_ids]
    photos = pictures()
    validation_photos=[(name,photo) for name,photo,_ in inputs()]
    preview_paths = []
    for spec in specs:
        a = identity_lut() if spec['a_path'] is None else parse_lut_file(spec['a_path']).lut
        b = parse_lut_file(spec['b_path']).lut
        expected = blend_luts(a,b,spec['percent_b']/100,title=spec['label'])
        if 'finishing' in spec:
            expected = soften(expected, **spec['finishing'])
        if 'refinement' in spec:
            expected = refine(expected)
        if 'output_curve' in spec:
            from nx300m_lut_core import Lut3D
            expected = Lut3D(33,trix_curve(expected.table).astype(np.float32),spec['label'])
        if not (library.directory(spec['id'])/'look.json').exists():
            if 'ab_family' in spec:
                library.add_cube(spec['approved_cube'],spec['id'],label=spec['label'],input_space='srgb',
                                 description=spec['reason'],method='fit')
                with library._lock(spec['id']):
                    metadata=library.get(spec['id'])
                    metadata.update(source_kind='blend-srgb-approved-ab',source_grid=33,recipe='recipe.json')
                    library._save(spec['id'],metadata)
            elif 'finishing' in spec:
                finished_cube = OUT/'candidates'/(spec['id']+'.cube')
                finished_cube.write_bytes(cube_bytes(expected))
                library.add_cube(finished_cube,spec['id'],label=spec['label'],input_space='srgb',
                                 description=spec['reason'],method='fit')
                with library._lock(spec['id']):
                    metadata = library.get(spec['id'])
                    metadata.update(source_kind='blend-srgb-refined' if 'refinement' in spec else 'blend-srgb-finished',
                                    source_grid=33,recipe='recipe.json')
                    library._save(spec['id'],metadata)
            else:
                library.add_blend(spec['a_path'],spec['b_path'],spec['id'],label=spec['label'],
                                  amount_b=spec['percent_b']/100,source_a_id=spec['a_id'],source_b_id=spec['b_id'],
                                  description=spec['reason'],reference_image=SAMPLES/'34_09_before.jpg',method='fit')
        if 'finishing' in spec:
            write_json(library.directory(spec['id'])/'recipe.json',dict(
                id=spec['id'],algorithm='blend-then-encoded-srgb-chroma-tone-v1',
                source_a=spec['a_path'].relative_to(ROOT).as_posix(),source_a_sha256=sha(spec['a_path']),
                source_b=spec['b_path'].relative_to(ROOT).as_posix(),source_b_sha256=sha(spec['b_path']),
                percent_b=spec['percent_b'],finishing=spec['finishing'],
                **({'refinement':spec['refinement']} if 'refinement' in spec else {})))
        if 'ab_family' in spec:
            if sha(library.directory(spec['id'])/'source.cube') != spec['approved_sha256']:
                raise ValueError('Adopted master is not the reviewed B file: '+spec['id'])
            write_json(library.directory(spec['id'])/'recipe.json',dict(
                id=spec['id'],authority=spec['authority'],review='four-target-ab',selected='B',
                approved_cube=spec['approved_cube'].relative_to(ROOT).as_posix(),
                approved_sha256=spec['approved_sha256'],
                source_a=spec['a_path'].relative_to(ROOT).as_posix(),source_a_sha256=sha(spec['a_path']),
                source_b=spec['b_path'].relative_to(ROOT).as_posix(),source_b_sha256=sha(spec['b_path']),
                percent_b=spec['percent_b'],working_space='encoded sRGB output RGB',
                **({'output_curve':spec['output_curve']} if 'output_curve' in spec else {})))
        if spec['id'] == EKTACHROME:
            with library._lock(spec['id']):
                metadata = library.get(spec['id'])
                metadata.update(label=spec['label'],description=spec['reason'])
                library._save(spec['id'],metadata)
        actual = parse_lut_file(library.directory(spec['id'])/'source.cube').lut
        error = float(np.max(np.abs(actual.table-expected.table)))
        if error > 2e-7:
            raise ValueError('Recipe differs from selected ratio: '+spec['id'])
        if 'approved_cube' in spec:
            approved = parse_lut_file(spec['approved_cube']).lut
            if np.max(np.abs(actual.table-approved.table)) > 2e-7:
                raise ValueError('User-approved six recipes changed: '+spec['id'])
        spec['round_trip_max_error'] = error
        preview_paths.append(preview(spec,library,photos))
        print('compiled '+spec['id'],flush=True)
    soft_parameters=dict(saturation=.85,contrast=.92,mid_lift=.025)
    soft_cube=OUT/'soft-option.cube'
    soft_lut=soften(identity_lut(title='Soft Bright'),**soft_parameters)
    soft_cube.write_bytes(cube_bytes(soft_lut))
    soft_key='custom-soft-bright'
    if not (library.directory(soft_key)/'look.json').exists():
        library.add_cube(soft_cube,soft_key,label='柔和明亮',input_space='srgb',
                         description='独立柔和选项：低反差、提亮中间调、降低饱和；不含胶片或青橙调色。',method='fit')
        with library._lock(soft_key):
            metadata=library.get(soft_key)
            metadata.update(source_kind='creative-softening',source_grid=33,recipe='recipe.json')
            library._save(soft_key,metadata)
    soft_recipe=dict(id=soft_key,input='identity sRGB',algorithm='encoded-srgb-chroma-tone-v1',
                     parameters=soft_parameters,source_sha256=sha(library.directory(soft_key)/'source.cube'),
                     expression='grey=dot(rgb,[.2126,.7152,.0722]); chroma=grey+.85*(rgb-grey); out=(chroma-.5)*.92+.5+.025')
    write_json(library.directory(soft_key)/'recipe.json',soft_recipe)
    if np.max(np.abs(parse_lut_file(library.directory(soft_key)/'source.cube').lut.table-soft_lut.table)) > 2e-7:
        raise ValueError('Standalone soft LUT differs from its recipe')
    canvas=Image.new('RGB',(792,116+278*len(photos)),'#11161b')
    draw=ImageDraw.Draw(canvas)
    font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',18)
    title=ImageFont.truetype('C:/Windows/Fonts/msyhbd.ttc',28)
    draw.text((26,15),'柔和明亮 · 独立 LUT',font=title,fill='#f5f1e8')
    draw.text((26,59),'左：原图；右：柔和。sRGB 桌面模拟。',font=font,fill='#aeb8c2')
    for row,(scene,source) in enumerate(photos):
        top=116+row*278
        draw.text((26,top-26),scene,font=font,fill='#d9b86a')
        canvas.paste(image_of(source,(366,222)),(26,top))
        canvas.paste(image_of(apply_lut(source,soft_lut),(366,222)),(400,top))
        draw.text((34,top+229),'原图',font=font,fill='#f5f1e8')
        draw.text((408,top+229),'柔和明亮 100%',font=font,fill='#f5f1e8')
    soft_preview=OUT/'selected-previews/custom-soft-bright.jpg'
    save_srgb(canvas,soft_preview);preview_paths.append(soft_preview)
    candidates=json.loads((OUT/'candidates/manifest.json').read_text(encoding='utf-8'))['entries']
    cold_warm=next(e for e in candidates if e['id']=='blend-millennium-balanced')
    for side in ('a','b'):
        if sha(ROOT/cold_warm[side+'_cube']) != cold_warm[side+'_sha256']:
            raise ValueError('Original cold/warm LUT changed')
    selected = [key for _,keys in GROUPS for key in keys]
    if len(selected) != len(set(selected)):
        raise ValueError('Duplicate selected ID')
    for key in selected:
        library.rename(key,LABELS[key])
    write_json(ROOT/'looks/catalog.json',dict(version=1,groups=[dict(name=name,ids=keys) for name,keys in GROUPS]))
    synced = sync(compile_camera=False)
    if synced['preserved_edits'] or synced['camera']['errors']:
        raise ValueError('Local sync failed: '+json.dumps(synced))
    if not original_ids.issubset({entry['id'] for entry in library.list()}):
        raise ValueError('Source library entries lost')
    overview_paths=selection_previews(library)
    points = np.random.default_rng(0xC0A7E).random((20000,3),dtype=np.float32)
    cube_dir, lr_dir = OUT/'selected-cubes', OUT/'lightroom'/GROUP
    cube_dir.mkdir(parents=True,exist_ok=True); lr_dir.mkdir(parents=True,exist_ok=True)
    groups = {key:name for name,keys in GROUPS for key in keys}
    checks, entries, cube_files, lr_files = [], [], [], []
    for index,key in enumerate(selected,1):
        directory = library.directory(key); metadata=library.get(key)
        lut = parse_lut_file(directory/'source.cube').lut
        tables = read_tables(directory,metadata)
        reference = apply_lut(points[:,None,:],lut)[:,0,:]
        native = sample_table(tables['table.d4lut'],points,interpolation='trilinear')
        error = (reference-native)*255
        scene_checks = []
        for scene,photo in validation_photos:
            master = apply_lut(photo,lut)
            coarse = sample_table(tables['table.d4lut'],photo.reshape(-1,3),interpolation='trilinear').reshape(photo.shape)
            difference = (master-coarse)*255
            scene_checks.append(dict(scene=scene,rgb_rmse_8bit=float(np.sqrt(np.mean(difference**2))),
                                     rgb_max_abs_8bit=float(np.max(np.abs(difference)))))
        cube = cube_dir/(key+'.cube')
        cube.write_bytes(cube_bytes(resample_lut(lut,33,title=metadata['label'])))
        if parse_lut_file(cube).lut.size != 33:
            raise ValueError('Cube grid mismatch: '+key)
        table = _resample(lut,32)
        fingerprint,encoded,raw_size = _encode_table(table)
        label = f"{index:02d} {metadata['label']}"
        path = lr_dir/(label+'.xmp')
        path.write_text(_xmp(label,'本地精选；Amount 100为当前配方。33³ CUBE母版，LR嵌入32³ RGBTable。'+metadata.get('description',''),
                             fingerprint,encoded,group_name=GROUP),encoding='utf-8')
        source = ProfileSource(str(index),metadata['label'],'sRGB',directory/'source.cube',key,sha(directory/'source.cube'),lut)
        lr_check = _verify_xmp(path,source,table,fingerprint,raw_size,group_name=GROUP)
        checks.append(dict(id=key,source_grid=lut.size,native_tables=list(tables),
                           native_random_rgb_rmse_8bit=float(np.sqrt(np.mean(error**2))),
                           native_random_rgb_max_abs_8bit=float(np.max(np.abs(error))),scenes=scene_checks,lightroom=lr_check))
        entries.append(dict(id=key,label=metadata['label'],group=groups[key],source_cube=f'looks/{key}/source.cube',
                            source_sha256=sha(directory/'source.cube'),cube_33=f'selected-cubes/{cube.name}',
                            cube_33_sha256=sha(cube),xmp=f'{GROUP}/{path.name}',rgb_table_id=fingerprint))
        cube_files.append((cube,'CUBE-33/'+cube.name)); lr_files.append((path,f'{GROUP}/{path.name}'))
        print('verified '+key,flush=True)
    native_paths=native_previews(library,checks,validation_photos)
    if len({e['rgb_table_id'] for e in entries}) != len(entries):
        raise ValueError('Duplicate LR table')
    for path in lr_dir.glob('*.xmp'):
        if path not in {p for p,_ in lr_files}:
            path.unlink()
    for path in cube_dir.glob('*.cube'):
        if path not in {p for p,_ in cube_files}:
            path.unlink()
    measurements=json.loads((AUDIT/'measurements.json').read_text(encoding='utf-8'))
    decisions=[]
    for spec in specs:
        record={k:v.relative_to(ROOT).as_posix() if isinstance(v,Path) else v for k,v in spec.items()}
        record['source_a_sha256']=sha(spec['a_path']) if spec['a_path'] else None
        record['source_b_sha256']=sha(spec['b_path'])
        if 'audit_id' in spec:
            record['blend_measurement_before_finishing']=next(r for r in measurements['records']
                if r['id']==spec['audit_id'] and r['percent_b']==spec['percent_b'])
        decisions.append(record)
    before_ids=before['staged_ids']
    manifest=dict(schema=1,before_creative_count=len(before_ids),selected_creative_count=len(selected),
                  native_menu_count=len(selected)+1,full_library_count=len(library.list()),
                  full_library_preserved=True,camera_runtime_verified=False,lightroom_render_verified=False,
                  preference='Bright Japanese daily looks first; Pentax Miyabi and four seasons mandatory; distinctive color styles; existing blends unchanged',
                  cube_export_grid=33,lightroom_grid=32,native_grid=9,
                  groups=[dict(name=name,ids=keys) for name,keys in GROUPS],recipes=decisions,entries=entries,
                  reviewed_imports=imported,
                  soft_option=soft_recipe,
                  complete_review=dict(sources=full_review['source_count'],selected=full_review['selected_count'],
                                       scope=full_review['review_scope'],variant_policy=full_review['variant_policy']),
                  exclusions=[dict(id=key,reason=exclusion_reason(key)) for key in before_ids if key not in selected],
                  previews=[p.relative_to(ROOT).as_posix() for p in preview_paths+overview_paths+native_paths])
    if not all(sha(library.directory(k)/'source.cube') == h for k,h in unchanged.items()):
        raise ValueError('An unrelated selected master changed')
    verification=dict(schema=1,source_recipe_round_trips=True,approved_ab_byte_identity=True,
                      unchanged_selected_masters=len(unchanged),
                      original_cold_warm_numeric_identity=True,
                      preserved_existing_library=True,camera_stage=synced['camera'],
                      scene_count=len(validation_photos),
                      native_scope='9³ creative table RGB-code trilinear model; hardware interpolation unmeasured',
                      runtime_lightroom_render_verified=False,checks=checks)
    write_json(OUT/'selected-manifest.json',manifest); write_json(OUT/'selected-verification.json',verification)
    shared=[(OUT/'selected-manifest.json','selected-manifest.json'),(OUT/'selected-verification.json','selected-verification.json'),
            (ROOT/'mod/nxconnect/third_party/cxo-filmpack/LICENSE','licenses/CXO-FilmPack-LICENSE.txt')]
    shared += [(LIBRARY_REVIEW/name,'licenses/'+name) for name in ['Film-Luts-LICENSE','Film-Luts-README.md']]
    shared += [(RECHECK/'decisions.json','full-review-decisions.json')]
    make_zip(OUT/'NX300M-Curated-CUBE33.zip',cube_files+shared)
    make_zip(OUT/'NX300M-Curated-Lightroom.zip',lr_files+shared)
    camera_root=ROOT/'lut-library/camera'
    native_files=[(p,p.relative_to(camera_root).as_posix()) for p in camera_root.rglob('*') if p.is_file()]
    make_zip(OUT/'NX300M-Curated-Camera-LUTs.zip',native_files+shared)
    make_zip(OUT/'NX300M-Curated-Selection-Previews.zip',
             [(p,'selection/'+p.name) for p in overview_paths]+
             [(p,'native/'+p.name) for p in native_paths]+shared)
    preview_files=[(p,'selected/'+p.name) for p in preview_paths]
    preview_files += [(p,'selection/'+p.name) for p in overview_paths]
    preview_files += [(p,'native/'+p.name) for p in native_paths]
    preview_files += [(p,'ratio-audit/'+p.relative_to(AUDIT).as_posix()) for p in AUDIT.rglob('*')
                     if p.is_file() and not p.name.startswith('blend-millennium')
                     and 'teal-softness' not in p.relative_to(AUDIT).parts]
    make_zip(OUT/'NX300M-Curated-Comparisons.zip',preview_files+shared)
    review_files=[(p,p.relative_to(RECHECK).as_posix()) for p in RECHECK.rglob('*')
                  if p.is_file() and p.suffix in ('.json','.jpg','.md')]
    make_zip(OUT/'NX300M-Full-Library-Review.zip',review_files)
    final_scan=scan()
    if any(final_scan[name] for name in ('changed','moved','renamed','unknown','missing','invalid','errors')):
        raise ValueError('Final local library scan failed: '+json.dumps(final_scan))
    print(json.dumps(dict(selected=len(selected),native_rows=synced['camera']['rows'],library=len(library.list()),
                           max_scene_native_rmse_8bit=max(s['rgb_rmse_8bit'] for c in checks for s in c['scenes']),
                           max_lr_rgb_rmse_8bit=max(c['lightroom']['heldout_rgb_rmse']*255 for c in checks),
                           final_scan_clean=True),ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()
