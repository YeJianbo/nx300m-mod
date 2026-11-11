"""Package the current named 58-look palette for camera, CUBE apps and Lightroom."""
from pathlib import Path
import hashlib
import json
import re
import shutil
import sys
import uuid
import xml.etree.ElementTree as ET
import zipfile

import numpy as np
from nx300m_lut_core import Lut3D, cube_bytes, parse_lut_file, apply_lut
from lut_resample import unpack, sample_table
from lut_table_data import read_tables
from build_lumix_lightroom_profiles import ProfileSource, _resample, _verify_xmp, _xmp, CRS, RDF
from import_lightroom_rgb_tables import decode_payload
from nx300m_installer import _group_state, _lut_group_camera_data

ROOT=Path(__file__).resolve().parents[1]
CURATION=ROOT/'outputs/looks/camera-curation'
OUT=CURATION/'delivery-58'
CONTENT=OUT/'NX300M-58款三端套装'


def write(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    if isinstance(data,str):data=data.encode('utf-8')
    path.write_bytes(data)


def digest(data):return hashlib.sha256(data).hexdigest()


def archive(path,files):
    files=list(files)
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for source,name in files:z.write(source,name)
    with zipfile.ZipFile(path) as z:
        assert len(z.namelist())==len(set(z.namelist()))==len(files)
        assert z.testzip() is None
    return dict(file=path.name,bytes=path.stat().st_size,files=len(files))


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    snapshot=json.loads((CURATION/'current-menu-overview/overview.json').read_text(encoding='utf-8'))
    selected=json.loads((CURATION/'selected-manifest.json').read_text(encoding='utf-8'))
    prior={e['id']:e for e in selected['entries']}
    entries=snapshot['entries']
    assert len(entries)==58 and all(e['id'] in prior for e in entries)
    group_names=list(dict.fromkeys(e['group'] for e in entries))
    assert group_names==['松下','富士','宾得','奥林巴斯','尼康','柯达','其他']
    camera=CONTENT/'01-相机'
    mobile=CONTENT/'02-手机-CUBE33'
    lightroom=CONTENT/'03-Lightroom-XMP'
    documents=CONTENT/'04-使用指南'
    groups_source=CURATION/'current-menu-overview/pending-card-menu'
    group_state=json.loads((groups_source/'lut-groups.json').read_text(encoding='utf-8'))
    rows=[line.split('|') for line in (groups_source/'looks.txt').read_text(encoding='utf-8').splitlines() if line]
    assert {r[0]:r[1] for r in rows}=={e['id']:e['label'] for e in entries}
    assert _group_state(json.dumps(group_state).encode('utf-8'),rows)==group_state
    assert len(group_state['members'])==58
    write(camera/'nx300m-mod/looks.txt','\n'.join('|'.join(r) for r in rows)+'\n')
    write(camera/'nx300m-mod/.state/lut-groups.json',json.dumps(group_state,ensure_ascii=False,indent=2)+'\n')
    write(camera/'nx300m-mod/.state/lut-groups.txt',_lut_group_camera_data(group_state))
    names={e['id']:e['label'] for e in entries}
    cycle=[]
    for line in (ROOT/'lut-library/camera/looks-cycle.txt').read_text(encoding='utf-8').splitlines():
        if line:
            r=line.split('|');r[1]=names[r[0]];cycle.append('|'.join(r))
    write(camera/'nx300m-mod/looks-cycle.txt','\n'.join(cycle)+'\n')
    menu=(ROOT/'outputs/metol/nxui.arm').read_bytes()
    tested=json.loads((ROOT/'outputs/deployment/lut-library-card/20261110T133759005830Z/group-menu-fix.json').read_text(encoding='utf-8'))
    assert digest(menu)==tested['menu_sha256']
    for name in ['bin/nxui.arm','fn-panel/bin/nxui.arm']:
        write(camera/'nx300m-mod'/name,menu)
    write(camera/'nx300m-mod/RESTART_SERVICE','curated58-20261110-seven-groups\n')
    checks=[]
    protected={}
    rng=np.random.default_rng(580033)
    points=rng.random((8000,3),dtype=np.float32)
    for e in entries:
        key=e['id'];n=e['number'];label=e['label'];group=e['group']
        group_dir=f'{group_names.index(group)+1:02d} {group}'
        filename=f'{n:02d} {label}'
        stage=ROOT/'lut-library/camera/looks'/key
        metadata=json.loads((stage/'look.json').read_text(encoding='utf-8'))
        tables=read_tables(stage,metadata)
        assert digest(tables['table.d4lut'])==e['native_table_sha256']
        for name,data in tables.items():
            protected[stage/name]=digest(data)
            write(camera/'nx300m-mod/looks'/key/name,data)
        metadata['label']=label
        write(camera/'nx300m-mod/looks'/key/'look.json',json.dumps(metadata,ensure_ascii=False,indent=2)+'\n')
        native=unpack(tables['table.d4lut']).reshape(9,9,9,3).astype(np.float32)
        nine=camera/'CUBE9-对应相机创意表'/group_dir/(filename+'.cube')
        write(nine,cube_bytes(Lut3D(9,native,label),title=label))
        read_nine=parse_lut_file(nine).lut
        nine_error=float(np.max(np.abs(apply_lut(points[:,None,:],read_nine)[:,0,:]-sample_table(tables['table.d4lut'],points))))
        assert read_nine.size==9 and nine_error<1e-6
        old=prior[key]
        cube_source=CURATION/old['cube_33']
        cube_data=cube_source.read_bytes()
        protected[cube_source]=digest(cube_data)
        assert digest(cube_data)==old['cube_33_sha256']
        text=cube_data.decode('utf-8-sig')
        text,count=re.subn(r'^TITLE[^\r\n]*','TITLE "'+label+'"',text,count=1,flags=re.M)
        assert count==1
        cube_target=mobile/group_dir/(filename+'.cube')
        write(cube_target,text)
        cube=parse_lut_file(cube_target).lut
        old_cube=parse_lut_file(cube_source).lut
        assert cube.size==33 and np.array_equal(cube.table,old_cube.table)
        master=ROOT/old['source_cube']
        protected[master]=digest(master.read_bytes())
        assert protected[master]==old['source_sha256']
        lut=parse_lut_file(master).lut
        original_xmp=CURATION/'lightroom'/old['xmp']
        original_xml=original_xmp.read_bytes()
        protected[original_xmp]=digest(original_xml)
        desc=next(ET.fromstring(original_xml).iter('{'+RDF+'}Description'))
        fingerprint=desc.attrib['{'+CRS+'}RGBTable']
        encoded=desc.attrib['{'+CRS+'}Table_'+fingerprint]
        raw=decode_payload(encoded,fingerprint)
        lr_group='NX300M '+group
        xml=_xmp(filename,'当前58款定版配方。Amount 100为该配方完整力度；sRGB创意色彩，未固定曝光或白平衡。',fingerprint,encoded,group_name=lr_group)
        # A renamed collection gets deterministic new preset identities so old
        # installations do not silently retain the former labels and grouping.
        identity=uuid.uuid5(uuid.NAMESPACE_URL,'nx300m:curated58:20261110:'+fingerprint+':'+group+':'+label).hex.upper()
        xml=re.sub(r'crs:UUID="[A-Fa-f0-9]+"','crs:UUID="'+identity+'"',xml,count=1)
        xmp=lightroom/group_dir/(filename+'.xmp')
        write(xmp,xml)
        rebuilt=next(ET.parse(xmp).getroot().iter('{'+RDF+'}Description'))
        assert rebuilt.attrib['{'+CRS+'}Table_'+fingerprint]==encoded
        profile=ProfileSource(str(n),label,'sRGB',master,key,old['source_sha256'],lut)
        verified=_verify_xmp(xmp,profile,_resample(lut,32),fingerprint,len(raw),group_name=lr_group)
        assert verified['grid']==32
        checks.append(dict(number=n,id=key,label=label,group=group,camera_tables=list(tables),
                           native_table_sha256=e['native_table_sha256'],cube9_max_rgb_error=nine_error,
                           cube33_numeric_identity=True,xmp_embedded_payload_identity=True,
                           xmp_grid=32,xmp_uuid=identity,lightroom=verified))
        if n%10==0:print(f'Packaged and verified {n}/58',flush=True)
    guide=CURATION/'scene-guide'
    for name in ['NX300M-LUT图解与机内参数-手机版.pdf','NX300M-LUT图解与机内参数.pdf','NX300M-LUT场景指南.pdf']:
        source=guide/name
        assert source.is_file()
        write(documents/name,source.read_bytes())
    write(documents/'58款总览对比.jpg',(CURATION/'current-menu-overview/NX300M-58-LUTs-Large-Comparison.jpg').read_bytes())
    licenses={
        'CXO-FilmPack-LICENSE.txt':ROOT/'mod/nxconnect/third_party/cxo-filmpack/LICENSE',
        'Film-Luts-LICENSE.txt':ROOT/'outputs/looks/library-review/20261110-FILM_LUTS_CURRENT_COMPARISON/Film-Luts-LICENSE',
        'Film-Luts-README.md':ROOT/'outputs/looks/library-review/20261110-FILM_LUTS_CURRENT_COMPARISON/Film-Luts-README.md',
        'CUBE-to-XMP-MIT.txt':ROOT/'docs/licenses/CUBE-to-XMP-MIT.txt',
    }
    for name,path in licenses.items():write(CONTENT/'来源与许可'/name,path.read_bytes())
    readme='''# NX300M 58款三端套装

当前定版58款，7组：松下12、富士14、宾得6、奥林巴斯3、尼康4、柯达7、其他12。名称和编号对应当前相机菜单、总览图及图解指南。

## 相机

01-相机/nx300m-mod 供已安装本项目 MOD 的 NX300M 使用：58款9x9x9、10-bit原生表，每款同时带直接表和原厂组合表；附7组分组、简称、快捷循环与已经修正自动分类的菜单程序。

先备份原卡 nx300m-mod，再将该目录合并到卡根目录，按提示覆盖。包内会替换滤镜菜单、分组与菜单程序；每款已保存的微调和收藏不在覆盖文件中。安全弹卡、插回相机并重新开机读取。

CUBE9-对应相机创意表 是58款直接创意表的标准CUBE表示，供支持9点CUBE的软件参照；相机自身读取 nx300m-mod/looks 内的 .d4lut。

## 手机 CUBE

02-手机-CUBE33 按7组放置58个33x33x33标准 .cube。在支持导入CUBE的手机调色应用中导入；选标准sRGB/普通照片工作流，强度100%为此款定版效果。Log、HDR或其他输入转换需在应用内另行匹配。

## Lightroom

03-Lightroom-XMP 是58个可调Amount的XMP Look，按7组显示。导入 Lightroom Classic/桌面版的“导入预设”，然后在 NX300M 分组中选择。已装旧精选组时，可先移除旧组再导入本包，避免新旧版本同时出现。手机版Lightroom可经支持同步的桌面版与Adobe账号同步；CUBE文件需要使用支持CUBE导入的应用。

XMP沿用已验证的32x32x32 RGBTable；通用CUBE为33x33x33。Amount 100表示当前混合定版的完整力度，不是CXO原始100%。XMP没有固定曝光或白平衡。

## 指南

04-使用指南 中的手机版PDF采用竖屏单列、大图和较大字号：每款先看色彩，再看机内参数。完整版每款提供三场景三列对照，简明场景指南便于快速选款。

指南中的右列参数是可选试用建议；本套装交付当前定版，未将试调参数烘焙为新LUT。三端色彩数据的数值和文件结构已检查，机身、各手机应用和Lightroom的实际渲染仍需实拍确认。
'''
    write(CONTENT/'使用说明.md',readme)
    verification=dict(schema=1,looks=58,groups=group_names,camera_grid=9,camera_bits=10,
                      cube9_count=58,cube33_count=58,xmp_count=58,xmp_grid=32,
                      original_sources_unchanged=True,includes_suggested_tuning=False,
                      application_render_verified=False,checks=checks)
    assert all(digest(p.read_bytes())==h for p,h in protected.items())
    for directory,suffix in [(camera/'.','*.d4lut'),(mobile,'*.cube'),(lightroom,'*.xmp')]:
        assert len(list(directory.rglob(suffix)))==(116 if suffix=='*.d4lut' else 58)
    write(CONTENT/'文件核对.json',json.dumps(verification,ensure_ascii=False,indent=2)+'\n')
    packages=[]
    for directory,name in [(camera,'NX300M-58款-相机.zip'),(mobile,'NX300M-58款-手机CUBE33.zip'),(lightroom,'NX300M-58款-Lightroom.zip')]:
        files=[(p,p.relative_to(directory).as_posix()) for p in directory.rglob('*') if p.is_file()]
        files += [(CONTENT/'使用说明.md','使用说明.md'),(CONTENT/'文件核对.json','文件核对.json')]
        files += [(p,'来源与许可/'+p.name) for p in (CONTENT/'来源与许可').iterdir() if p.is_file()]
        packages.append(archive(OUT/name,files))
    files=[(p,p.relative_to(OUT).as_posix()) for p in CONTENT.rglob('*') if p.is_file()]
    packages.append(archive(OUT/'NX300M-58款-三端合集.zip',files))
    write(OUT/'packaging-verification.json',json.dumps(dict(looks=58,packages=packages,source_files_preserved=len(protected)),ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(looks=58,groups=group_names,packages=packages),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
