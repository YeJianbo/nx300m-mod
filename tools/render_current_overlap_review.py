"""Compare approved overlapping camera looks without making new mixtures."""
import shutil

from compare_film_luts_current import ROOT, OUT, inputs, grid, read_json, write_json, digest
from nx300m_lut_core import apply_lut, parse_lut_file

DEST=OUT/'remaining-library/current-overlap'
GROUPS=[
    ('natural-portraits','自然人像三款',[
        'lr-custom-10e69cdb47b694fd2929e8ce7cacde7d',
        'panasonic-lumix-skin-tone-boost','nx300-fuji-astia-cxo50']),
    ('soft-cinema','柔和与电影三款',[
        'nx300-fuji-eterna-cxo40','custom-soft-bright','panasonic-lumix-smoky-color-s']),
    ('slide-film','反转片两款',[
        'cxo-kodakelitechrome200','blend-metol-ektachrome-cxo85-refined']),
]


def candidate_summary():
    parent=OUT/'remaining-library'
    (parent/'priority-source-cubes').mkdir(exist_ok=True)
    rows=read_json(parent/'shortlist-verification.json')['entries']
    picks=[('local','lr-custom-4546d428bd19332a9f69b40c3fdaa762'),
           ('github','film-luts-negative-new-fuji-400h--'),
           ('github','film-luts-negative-color-kodak-portra-160-nc')]
    photos=inputs()
    display=[]
    for provider,key in picks:
        row=next(e['source'] for e in rows if e['source']['provider']==provider and e['source']['id']==key)
        candidate_path=ROOT/row['path']
        shutil.copyfile(candidate_path,parent/'priority-source-cubes'/f'{key}.cube')
        candidate=parse_lut_file(candidate_path).lut
        prior_id=row['closest_current'][0]['id']
        prior_path=ROOT/'looks'/prior_id/'source.cube'
        prior=parse_lut_file(prior_path).lut
        label=read_json(prior_path.parent/'look.json')['label']
        for i in [0,6]:
            name,pixels,_=photos[i]
            display.append((row['label']+' · '+name,[('原图',pixels),('当前参照 · '+label,apply_lut(pixels,prior)),
                                                   ('候选原版100%',apply_lut(pixels,candidate))]))
    grid('其他LUT · 三款优先候选','原图 / 当前机内参照 / 候选原版100%；不使用新混合',
         display,parent/'priority-three-preview.jpg',410,270)


def main():
    DEST.mkdir(parents=True,exist_ok=True)
    (DEST/'cubes33').mkdir(exist_ok=True)
    source=read_json(OUT/'remaining-library/all-luts-screen.json')['entries']
    labels={e['id']:e['label'] for e in source if e['provider']=='local'}
    report=dict(status='existing approved versions only; no new mixing or adoption',groups=[])
    protected=read_json(OUT/'measurements.json')['protected_files']
    photos=inputs()
    for key,title,ids in GROUPS:
        luts=[]
        records=[]
        for ident in ids:
            path=ROOT/'looks'/ident/'source.cube'
            target=DEST/'cubes33'/f'{ident}.cube'
            shutil.copyfile(path,target)
            luts.append(parse_lut_file(target).lut)
            records.append(dict(id=ident,label=labels[ident],path=path.relative_to(ROOT).as_posix(),
                                cube=target.relative_to(DEST).as_posix(),sha256=digest(path)))
        rows=[]
        for i in [0,1,6,9,13,14]:
            name,pixels,_=photos[i]
            rows.append((name,[('原图',pixels)]+[(labels[ident],apply_lut(pixels,lut)) for ident,lut in zip(ids,luts)]))
        grid(title+' · 机内既定版横向对比','只比较当前source.cube；没有新建议或新混合参与',
             rows,DEST/f'{key}.jpg',410,270)
        report['groups'].append(dict(id=key,title=title,entries=records))
    assert all(digest(ROOT/p)==v for p,v in protected.items())
    report['protected_files_unchanged']=True
    write_json(DEST/'sources.json',report)
    candidate_summary()
    print('three_groups_rendered; eight_approved_cubes_copied; catalogs_unchanged')


if __name__=='__main__':main()
