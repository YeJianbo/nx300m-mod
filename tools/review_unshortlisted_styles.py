"""Review unshortlisted LUTs for distinctive style coverage, not softness."""
import argparse
import shutil
import zipfile

import numpy as np

from compare_film_luts_current import ROOT, OUT, inputs, grid, read_json, write_json, digest
from nx300m_lut_core import apply_lut, parse_lut_file, resample_lut, cube_bytes
from audit_camera_blend_ratios import metrics

DEST=OUT/'remaining-library/unshortlisted-styles'
PICKS={
 'local':[
  'ricoh-negative-exact','ricoh-positive-exact','sony-fl-look',
  'sigma-cinema','sigma-fov-classic-yellow','sigma-sunset-red','sigma-duotone-b1','sigma-duotone-or1',
  'lr-custom-d47cc96ee6e5196e2313ed25c45c498c','lr-custom-d7bd9f1cd3f2f3c15a6a231ef36955c0',
  'lr-custom-53ef98a6a654bf9c2399cc5cfa4c386e','lr-custom-4a06e6310411a69955ad13c68d018b26',
  'lr-custom-5bb53593456883c7e78f1175d31cabad','lr-custom-83df813e897b32b6d31e07090c0c6ca3',
  'lr-custom-2e782fdfd2ef2004024975aad5295eb6','panasonic-lumix-agfaphoto-lebox',
  'panasonic-lumix-mossy-rain','panasonic-lumix-tibet-warm-light',
  'panasonic-lumix-magenta-old-film','panasonic-lumix-txd-grainy-film'],
 'cxo':[
  'cxo-kodakektar100','cxo-kodakkodachrome64','cxo-agfavista200',
  'cxo-negativelomochromemetropolis100400','cxo-negativeharmanphoenix200',
  'cxo-negativeambert200','cxo-negativecinestillt800','cxo-adoxcolorimplosion',
  'cxo-polaroid669','cxo-fujifp100c','cxo-lomoredscale100','cxo-kodakelite100c41',
  'cxo-cineautumn','cxo-cinemoodydarkgreen','cxo-cinepastel','cxo-cinetealandpink',
  'cxo-rolleiortho25','cxo-kodakhiefiltre','cxo-agfascala200x','cxo-fomapan100r','cxo-illfordpanfplus50',
  'cxo-digital-nikonz-denim','cxo-digital-nikonz-silence','cxo-digital-bw-nikonz-graphite','cxo-kodakeir'],
 'github':[
  'film-luts-negative-color-kodak-ektar-100','film-luts-negative-color-agfa-vista-200',
  'film-luts-negative-new-fuji-800z','film-luts-negative-color-kodak-portra-160-vc',
  'film-luts-colorslide-kodak-kodachrome-64','film-luts-instant-pro-polaroid-690',
  'film-luts-instant-consumer-polaroid-time-zero-expired','film-luts-instant-pro-fuji-fp-100c-cool',
  'film-luts-print-kodak-2383-constlmap','film-luts-print-fuji-3513-constlmap',
  'film-luts-bw-fuji-neopan-1600','film-luts-bw-rollei-retro-80s'],
}


def lookup():
    return {(e['provider'],e['id']):e for e in read_json(OUT/'remaining-library/all-luts-screen.json')['entries']}


def broad():
    DEST.mkdir(parents=True,exist_ok=True)
    photos=inputs()
    data=lookup()
    report=dict(criterion='Distinctive style coverage; no uniform saturation, contrast or skin-hue gate',
                scenes=[photos[i][0] for i in [0,3,6,9,13]],entries=[])
    for provider,keys in PICKS.items():
        values=[]
        for key in keys:
            e=data[provider,key]
            if e['in_camera']:raise ValueError('Already in camera: '+key)
            lut=parse_lut_file(ROOT/e['path']).lut
            values.append((e['label'],[(photos[i][0],apply_lut(photos[i][1],lut)) for i in [0,3,6,9,13]]))
            report['entries'].append(e)
        for start in range(0,len(values),5):
            original=('原图 / 五类场景',[(photos[i][0],photos[i][1]) for i in [0,3,6,9,13]])
            grid(provider+' · 未入围款重新审片 · '+str(start//5+1),
                 '候选原版100%；人像 / 城市 / 绿叶 / 夜灯 / 红叶；按独立风格审片',
                 [original]+values[start:start+5],DEST/f'{provider}-page-{start//5+1}.jpg',260,180)
        print(provider+' '+str(len(keys)),flush=True)
    write_json(DEST/'broad-review.json',report)


def detailed():
    photos=inputs()
    data=lookup()
    choices=read_json(DEST/'choices.json')
    protected=read_json(OUT/'measurements.json')['protected_files']
    report=dict(status='unshortlisted-source review only; no automatic adoption or new mixtures',
                native_sources=[],scene_count=8,entries=[])
    labels={e['id']:e['label'] for e in data.values() if e['provider']=='local'}
    (DEST/'source-cubes').mkdir(exist_ok=True)
    (DEST/'candidate-cubes33').mkdir(exist_ok=True)
    for choice in choices:
        e=data[choice['provider'],choice['id']]
        source=ROOT/e['path']
        native=parse_lut_file(source).lut
        target=DEST/'candidate-cubes33'/f"{choice['provider']}-{e['id']}.cube"
        converted=resample_lut(native,33)
        target.write_bytes(source.read_bytes() if native.size==33 else cube_bytes(converted))
        lut=parse_lut_file(target).lut
        error=float(np.abs(lut.table-converted.table).max())
        assert error<1e-6 and np.isfinite(lut.table).all()
        copy=DEST/'source-cubes'/target.name
        shutil.copyfile(source,copy)
        current_id=e['closest_current'][0]['id']
        current=parse_lut_file(ROOT/'looks'/current_id/'source.cube').lut
        display=[]
        measurements=[]
        for i in [0,1,2,3,6,9,13,14]:
            name,pixels,_=photos[i]
            b=apply_lut(pixels,lut)
            display.append((name,[('原图',pixels),('当前参照 · '+labels[current_id],apply_lut(pixels,current)),
                                   ('候选100% · '+choice['role'],b)]))
            m=metrics(pixels,b,name)
            if choice['role'].startswith('黑白'):
                for key in ['skin_hue_shift_deg','skin_chroma','skin_chroma_change']:
                    m.pop(key,None)
            measurements.append(dict(scene=name,**m))
        grid(e['label']+' · 未入围款复核',choice['reason'],display,
             DEST/'detailed'/f"{choice['provider']}-{e['id']}.jpg",390,250)
        record=dict(**choice,label=e['label'],path=e['path'],native_grid=native.size,
                    native_sha256=digest(source),cube33=target.relative_to(DEST).as_posix(),
                    roundtrip_error=error,scenes=measurements)
        report['entries'].append(record)
        print('detailed '+e['id'],flush=True)
    assert all(digest(ROOT/f)==h for f,h in protected.items())
    report['approved_files_unchanged']=True
    write_json(DEST/'detailed-verification.json',report)


def finish():
    photos=inputs()
    verified=read_json(DEST/'detailed-verification.json')
    data={e['id']:e for e in verified['entries']}
    # Roles are visual selections from the eight-scene review, not metric rankings.
    decisions=[
      ('lr-custom-5bb53593456883c7e78f1175d31cabad','优先候选','暖调消费负片','暖肤色、蓝天与黄绿有明确分工，适合日常复古快照；暖光下橙色更浓。'),
      ('panasonic-lumix-agfaphoto-lebox','优先候选','明亮复古快照','清亮蓝色、浅肤色、较实的冷暗部；比FP-100C更明快，夜景暗部余量较小。'),
      ('film-luts-print-kodak-2383-constlmap','优先候选','浓郁电影色彩','暖肤色与蓝青背景分离，暗部有力度；优先于3513保留浓郁定位。'),
      ('film-luts-instant-pro-fuji-fp-100c-cool','优先候选','冷调拍立得','蓝黑底、浅肤色、保留暖灯；比LeBox更柔和、黑位更抬，可保留独立角色。'),
      ('cxo-polaroid669','优先候选','柔色拍立得','灰调红色与棕灰植被有独立解释；人像偏冷粉，适合柔色复古而非自然还原。'),
      ('cxo-cineautumn','专项候选','棕金秋色','绿叶显著转棕金，季节辨识度强；暖光高光与皮肤略粉，不作通用人像。'),
      ('cxo-cinepastel','专项候选','明亮粉彩','浅彩、抬黑位和很低的反差形成独立选项；强光场景容易过白。'),
      ('film-luts-print-fuji-3513-constlmap','替代备选','清亮电影色彩','比2383稍清亮，肤色暖红较轻；与2383接近，先作为同位置替代。'),
      ('lr-custom-83df813e897b32b6d31e07090c0c6ca3','暂不入围','CineStill800T','城市天空有深蓝特色，但多组人像与现有Astia接近，新增覆盖有限；不产生卤光。'),
      ('lr-custom-d47cc96ee6e5196e2313ed25c45c498c','暂不入围','宾得夏天','人像色相稳定，但暗部较重，与已有浓彩款重复较多。'),
      ('sigma-fov-classic-yellow','暂不入围','适马经典黄','三组肤色明显偏黄，暖调角色由Gold承担更协调。'),
      ('cxo-adoxcolorimplosion','暂不入围','Adox Color Implosion','褪色有特色，但三组人像粉红倾向比所选拍立得更明显。'),
      ('cxo-negativelomochromemetropolis100400','暂不入围','LomoChrome Metropolis','脸部与阴天建筑明显黄绿，按本轮偏色筛选要求排除。'),
      ('film-luts-negative-color-kodak-ektar-100','暂不入围','Kodak Ektar100','肤色稳定、颜色可用，但相对现有库风格增量较小，不因胶片名字占位。'),
    ]
    selected=[]
    for key,status,role,reason in decisions:
        e=data[key]
        selected.append(dict(id=key,provider=e['provider'],label=e['label'],status=status,
                             role=role,reason=reason,source=e['path'],cube33=e['cube33'],
                             native_grid=e['native_grid']))
    included=selected[:8]
    luts={e['id']:parse_lut_file(DEST/e['cube33']).lut for e in included}
    overview=[]
    for e,i in zip(selected[:7],[3,3,3,9,13,6,14]):
        a,b=photos[1][1],photos[i][1]
        lut=luts[e['id']]
        overview.append((e['label']+' · '+e['role'],[
            ('原图 · 自然光人像',a),('候选100% · 人像',apply_lut(a,lut)),
            ('原图 · '+photos[i][0],b),('候选100% · 场景',apply_lut(b,lut))]))
    grid('新增彩色风格 · 七款候选','前五款优先；秋季与粉彩为专项。候选原配方100%，没有新增混合或修色。',
         overview,DEST/'overview-color-styles.jpg',370,230)
    for title,name,keys,indices in [
      ('电影色彩 · 两款同场对照','pair-movie',
       ['film-luts-print-kodak-2383-constlmap','film-luts-print-fuji-3513-constlmap'],[0,1,3,6,9]),
      ('冷调快照与拍立得 · 同场对照','pair-instant',
       ['panasonic-lumix-agfaphoto-lebox','film-luts-instant-pro-fuji-fp-100c-cool','cxo-polaroid669'],[0,1,3,6,9])]:
        rows=[]
        for i in indices:
            scene,a,_=photos[i]
            rows.append((scene,[('原图',a)]+[(data[k]['label']+' 100%',apply_lut(a,luts[k])) for k in keys]))
        grid(title,'相同底图、曝光、白平衡、sRGB编码与三线性插值；按风格差异选择。',
             rows,DEST/(name+'.jpg'),370,230)
    report=dict(criterion='排除黑白、双色染色与明显不协调偏色，增加有独立用途的彩色风格',
                priority_count=5,specialty_count=2,alternative_count=1,entries=selected,
                scene_count_per_detailed=8,adopted_to_camera=False)
    write_json(DEST/'selection-results.json',report)
    lines=['# 新增彩色风格候选','',
           '五款优先候选 + 两款专项 + 一款替代备选。正式机内目录未改，候选均为原配方100%，没有新增混合。','',
           '|款式|选择|定位与观察|','|---|---|---|']
    for e in selected:
        lines.append('|'+e['label']+'|'+e['status']+'|'+e['reason']+'|')
    lines.extend(['','## 如何看图','',
      '- overview-color-styles.jpg：七款候选，每款同底图的人像与代表场景前后对照。',
      '- pair-movie.jpg：2383与3513同场；pair-instant.jpg：LeBox、FP-100C与669同场。',
      '- detailed：八款各八场景，三列为原图、数值距离最近的当前机内参照、候选100%。参照列不是共同基底。',
      '- candidate-cubes33为33³母版；source-cubes为原始网格源文件。这不是相机9³安装包，也不是Lightroom XMP配置。','',
      '旧样张与F盘机内JPEG采用相同sRGB编码和三线性插值。这里评价创意色彩效果；电影打印源的输入空间未明确，不能作为严格胶片模拟校准。',
      '详细复核原记录包含14款，selection-results.json明确最终选择；压缩包只收所选八款图与CUBE。',
      'LUT只改变颜色和曲线，不生成颗粒、光晕或纸面质感。',''])
    (DEST/'README.md').write_text('\n'.join(lines),encoding='utf8')
    archive=OUT/'Color-Style-Candidates.zip'
    files=[DEST/'README.md',DEST/'selection-results.json',DEST/'detailed-verification.json',
           DEST/'overview-color-styles.jpg',DEST/'pair-movie.jpg',DEST/'pair-instant.jpg']
    for e in included:
        stem=e['provider']+'-'+e['id']
        files.extend([DEST/'detailed'/(stem+'.jpg'),DEST/e['cube33'],DEST/'source-cubes'/(stem+'.cube')])
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for f in files:z.write(f,f.relative_to(DEST).as_posix())
        for name in ['Film-Luts-README.md','Film-Luts-LICENSE']:z.write(OUT/name,'sources/'+name)
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        names=z.namelist()
        assert len(names)==len(set(names))
        assert sum(n.endswith('.jpg') for n in names)==11
        assert sum(n.endswith('.cube') for n in names)==16
    protected=read_json(OUT/'measurements.json')['protected_files']
    assert all(digest(ROOT/f)==h for f,h in protected.items())
    write_json(DEST/'delivery-verification.json',dict(zip=archive.name,zip_integrity='pass',
               jpeg_count=11,cube_count=16,approved_files_unchanged=True,
               cube33_readback_max_error=max(e['roundtrip_error'] for e in verified['entries'])))
    print(str(archive),flush=True)
    print('11 JPEG; 8 cube33 + 8 native sources; ZIP integrity PASS; approved files unchanged',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('stage',choices=['broad','detailed','finish'])
    args=parser.parse_args()
    {'broad':broad,'detailed':detailed,'finish':finish}[args.stage]()
