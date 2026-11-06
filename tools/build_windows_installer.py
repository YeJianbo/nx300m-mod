"""Build an offline Windows updater around the checked cold-boot candidate."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
import argparse
from installer_backend import BACKEND_FILES, MARKERS, PREFIX, verify_backend

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/releases/windows-updater-20261106'
CANDIDATE = ROOT / 'outputs/review/2026-11-06-device-feedback/cold-boot-candidate'


def main(public=False):
    record = json.loads((CANDIDATE / 'manifest.json').read_text(encoding='utf-8'))
    evidence = json.loads((CANDIDATE / 'deployment-result.json').read_text(encoding='utf-8'))
    if not evidence.get('card_written') or evidence.get('readback_verified') != 6:
        raise ValueError('缺少已部署候选的文件回读记录')
    first = OUT / ('public-first-install' if public else 'first-install')
    first_record = json.loads((first / 'manifest.json').read_text(encoding='utf-8'))
    for name, item in first_record['files'].items():
        data = (first / 'payload' / name).read_bytes()
        if len(data) != item['bytes'] or hashlib.sha256(data).hexdigest() != item['sha256']:
            raise ValueError('首次安装候选文件不匹配：' + name)
    backend = verify_backend(lambda name: (first / 'payload' / name).read_bytes())
    package = OUT / ('public-package' if public else 'package')
    package.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env['PATH'] = os.pathsep.join((str(Path(sys.base_prefix) / 'Library/bin'),
                                   str(Path(sys.base_prefix)), os.environ.get('PATH', '')))
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir',
                    '--windowed', '--name', 'NX300M-MOD', '--distpath', str(OUT / 'dist'),
                    '--workpath', str(OUT / 'build'), '--specpath', str(OUT),
                    str(ROOT / 'tools/nx300m_installer.py')], check=True, env=env)
    shutil.copytree(OUT / 'dist/NX300M-MOD', package, dirs_exist_ok=True)
    if not (first / 'manifest.json').is_file():
        raise ValueError('请先生成本机首次安装候选文件')
    for name in ('manifest.json', *('payload/'+n for n in first_record['files'])):
        target=package/'first-install'/name
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(first/name,target)
    if public:
        shutil.copy2(ROOT/'outputs/looks/original-release/LICENSE.txt',package/'LUT-LICENSE.txt')
    names = ('nx300m-mod/bin/nxui.arm', 'nx300m-mod/fn-panel/bin/nxui.arm',
             'nx300m-mod/lut-boot.sh', 'nx300m-mod/mfw-services.sh',
             'nx300m-mod/mfw-panel-actions.sh')
    manifest = {'schema': 1, 'kind': 'nx300m-cold-boot-update', 'version': '2026-11-06-installer-r2',
                'requires': {k: v for k, v in record['requires'].items() if '/.state/' not in k},
                'backend': backend, 'payload': {}}
    manifest['requires'].pop(PREFIX + 'runtime-files.list', None)
    for name, expected in BACKEND_FILES.items():
        manifest['requires'][PREFIX + name] = expected
    for name in names:
        rec = record['payload'][name]
        data = (CANDIDATE / 'payload' / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != rec['sha256']:
            raise ValueError('候选内容不匹配：' + name)
        relative = 'payload/' + name
        target = package / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        manifest['payload'][name] = {'asset': relative, 'sha256': rec['sha256'],
                                     'accepted': sorted({rec['before_sha256'], rec['sha256']})}
    for relative in (*MARKERS, 'runtime-files.list'):
        name = PREFIX + relative
        data = (first / 'payload' / name).read_bytes()
        asset = 'payload/' + name
        target = package / asset
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        accepted = [None, digest] if relative in MARKERS else sorted({
            digest, record['requires'][name],
            # First installer accidentally removed the two ABI7 selectors.
            'f2b66a197fe42afeae32e7c4266f6fcb9cd571acdc95e7a02e6cb2db92806894',
        })
        manifest['payload'][name] = {'asset': asset, 'sha256': digest, 'accepted': accepted}
    (package / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    for name in ('USER_GUIDE.md', 'RISKS_AND_RIGHTS.md'):
        shutil.copy2(ROOT / 'docs' / name, package / name)
    shutil.copy2(ROOT / ('docs/WINDOWS_INSTALLER_PUBLIC.md' if public else 'docs/WINDOWS_INSTALLER.md'), package / '使用说明.md')
    shutil.copy2(ROOT / ('docs/WINDOWS_INSTALLER_PUBLIC.md' if public else 'docs/FIRST_INSTALL_LOCAL.md'), package / '首次安装说明.md')
    previous = ROOT / 'outputs/releases/v0.1.0-preview.2/nx300m-menu-upgrade-v0.1.0-preview.2.zip'
    with zipfile.ZipFile(previous) as old:
        for name in old.namelist():
            if name.startswith('LICENSES/') or name == 'NOTICE.md':
                target = package / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(old.read(name))
    shutil.copy2(Path(sys.base_prefix) / 'LICENSE_PYTHON.txt', package / 'LICENSES/Python.txt')
    for license_file in (ROOT/'docs/licenses').glob('*.txt'):
        shutil.copy2(license_file,package/'LICENSES'/license_file.name)
    shutil.copy2(ROOT/'mod/quickshare/LICENSE',package/'LICENSES/QuickShare-GPL-3.0.txt')
    shutil.copy2(ROOT/'mod/quickshare/NOTICE.md',package/'LICENSES/QuickShare-NOTICE.md')
    if public:
        with (package/'NOTICE.md').open('a',encoding='utf-8') as note:
            note.write('\n本完整安装包另含 GPL-3.0 Quick Share 核心；来源与修改见 LICENSES/QuickShare-NOTICE.md。Web 字体及库许可保存在 LICENSES。六款原创风格使用 CC0-1.0，配方见 source/build_original_looks.py。完整对应源码见同一 Release 的源码压缩包。\n')
    from importlib.metadata import distribution
    for project in ('pyinstaller', 'altgraph', 'numpy'):
        dist = distribution(project)
        license_file = next((dist.locate_file(f) for f in dist.files or ()
                             if f.name.upper() in ('LICENSE', 'LICENSE.TXT') and '.dist-info' in str(f)), None)
        if license_file:
            shutil.copy2(license_file, package / ('LICENSES/' + project + '.txt'))
    source = package / 'source'
    source.mkdir(exist_ok=True)
    for name in ('nx300m_installer.py', 'installer_backend.py', 'build_windows_installer.py', 'build_original_looks.py', 'build_public_first_install.py', 'cube_to_d4.py',
                 'lut_resample.py', 'lut_integral.py'):
        shutil.copy2(ROOT / 'tools' / name, source / name)
    source_ref = 'v0.1.0-preview.3' if public else '2fd4b828c35589c70ced4ec581ac411107d961f0'
    (package / 'SOURCE.md').write_text(
        '# 对应源码\n\n相机端源码： https://github.com/YeJianbo/nx300m-mod/tree/'+source_ref+'\n\n'
        'Windows 工具及 LUT 转换相关源码随包保存在 source/。原厂 SDK 和系统库不随包提供。\n', encoding='utf-8')
    archive = OUT / ('NX300M-MOD-Windows-v0.1.0-preview.3.zip' if public else 'NX300M-MOD-Windows-安装工具-20261106-r2.zip')
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as out:
        for path in sorted(package.rglob('*')):
            relative = path.relative_to(package)
            if path.is_file() and 'backups' not in relative.parts and path.name != 'installer.log':
                out.write(path, relative.as_posix())
    with zipfile.ZipFile(archive) as check:
        if check.testzip() is not None: raise ValueError('压缩包校验失败')
        if public:
            expected_first={'first-install/manifest.json',*('first-install/payload/'+n for n in first_record['files'])}
            actual_first={n for n in check.namelist() if n.startswith('first-install/')}
            if actual_first!=expected_first or any('table-stock3d.d4lut' in n for n in check.namelist()):
                raise ValueError('公开包存在非清单文件或原厂组合表。')
    print('ARCHIVE=' + str(archive))


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--public',action='store_true')
    main(public=parser.parse_args().public)
