"""Publishable menu/control upgrade, bound to the checked RAM/LUT service ABI."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def build(version: str) -> Path:
    if not re.fullmatch(r'v\d+\.\d+\.\d+-preview\.\d+', version):
        raise ValueError('Expected a preview version tag')
    output = ROOT/'outputs/releases'/version
    output.mkdir(parents=True, exist_ok=True)
    menu = (ROOT/'outputs/metol/nxui.arm').read_bytes()
    menu_hash = hashlib.sha256(menu).hexdigest()
    evidence = json.loads((ROOT/'outputs/performance/lut-menu-entry/list-up-navigation-vm/report.json').read_text())
    if (not all(evidence.get(k) is True for k in ('passed', 'private_vm_stopped',
            'list_up_moves_previous_before_entering_groups',
            'first_row_up_enters_group_and_down_returns',
            'lut_header_preview_dispatches_ram_service_with_data_identity'))
            or evidence['candidate_sha256'] != menu_hash):
        raise ValueError('Menu does not match its successful ARM evidence')
    session_run=ROOT/'outputs/deployment/smartfilter-session/preview-navigation-vm'
    session=json.loads((session_run/'report.json').read_text())
    if not all(session.get(k) is True for k in ('passed','private_vm_stopped',
            'normal_wrapper_stop_releases_session','current_preview_installs_still_before_first_frame')):
        raise ValueError('Session/control checks did not pass')
    payload={
        'bin/nxui.arm':menu,
        'lut-panel.sh':(ROOT/'mod/deployment/mfw/lut-panel.sh').read_bytes(),
        'smartfilter-preview.sh':(ROOT/'mod/deployment/mfw/smartfilter-live-preview.sh').read_bytes(),
        'bin/smartfilter_probe_launcher.arm':(ROOT/'outputs/deployment/smartfilter-session/smartfilter_probe_launcher.arm').read_bytes(),
    }
    payload['ipcs-diagnostic/bin/smartfilter_probe_launcher.arm']=payload['bin/smartfilter_probe_launcher.arm']
    for name, staged in [('lut-panel.sh','lut-panel.sh'),('smartfilter-preview.sh','smartfilter-live-preview.sh'),
                         ('bin/smartfilter_probe_launcher.arm','bin/smartfilter_probe_launcher.arm')]:
        if payload[name] != (session_run/'source-files'/staged).read_bytes():
            raise ValueError('Control payload differs from ARM checked files: '+name)
    payload={name:data.replace(b'\r\n',b'\n') if name.endswith('.sh') else data for name,data in payload.items()}
    base = ROOT/'outputs/deployment/smartfilter-session/diagnostic-card'
    record = json.loads((base/'package-report.json').read_text())
    if record['abi'] != 7 or record['diagnostics_enabled_by_default'] is not False:
        raise ValueError('Unsupported backend evidence')
    required = (
        'bin/lut_engine.arm', 'bin/lut_live_loader.arm', 'bin/nx-remote-controller.arm',
        'lib/lut_ram_blob.bin', 'ipcs-diagnostic/bin/smartfilter_loader.arm',
        'ipcs-diagnostic/lib/smartfilter_ram.bin',
        'lut-boot.sh', 'lut-load.sh', 'mfw-panel.sh',
        'mfw-services.sh', 'fn-panel/mfw-panel-actions.sh',
        'fn-panel/gui_mfw.NX300M', 'gui_mfw.NX300M',
    )
    expected = {r['path']: r['sha256'] for r in record['files']}
    requires = {}
    replaces = {}
    with zipfile.ZipFile(base/'nx300m-ipcs-open-diagnostic.zip') as archive:
        for name in required:
            key = 'nx300m-mod/'+name
            data = archive.read(key)
            if hashlib.sha256(data).hexdigest() != expected[key]:
                raise ValueError('Checked backend package changed: '+key)
            requires[key] = expected[key]
        for name,data in payload.items():
            if name=='bin/nxui.arm':continue
            key='nx300m-mod/'+name
            baseline_key=key
            if key not in expected:
                if name!='bin/smartfilter_probe_launcher.arm':
                    raise ValueError('Missing checked control baseline: '+key)
                baseline_key='nx300m-mod/ipcs-diagnostic/bin/smartfilter_probe_launcher.arm'
            old=archive.read(baseline_key)
            if hashlib.sha256(old).hexdigest()!=expected[baseline_key]:
                raise ValueError('Checked control baseline changed: '+key)
            replaces[key]=sorted({expected[baseline_key],hashlib.sha256(data).hexdigest()})
    for name, source in {
        'mfw-ram-stage.sh': ROOT/'mod/deployment/mfw/ram-stage.sh',
        'fonts/mfw-font.charset': ROOT/'outputs/metol/mfw-font.charset',
    }.items():
        requires['nx300m-mod/'+name] = hashlib.sha256(source.read_bytes()).hexdigest()
    manifest = dict(schema=2, kind='nx300m-menu-upgrade', version=version,
                    model='NX300M', firmware='1.15', menu_sha256=menu_hash,
                    preview_abi=record['abi'], preview_build=record['build'],
                    still_build=record['still_lut_build'], requires=requires, replaces=replaces,
                    payload={'nx300m-mod/'+name:dict(asset='payload/'+name,sha256=hashlib.sha256(data).hexdigest())
                             for name,data in payload.items()},
                    physical_transition_verified=False)
    guide = (ROOT/'docs/USER_GUIDE.md').read_text(encoding='utf-8')
    rights = (ROOT/'docs/RISKS_AND_RIGHTS.md').read_text(encoding='utf-8')
    for relative, destination in {
        '../THIRD_PARTY.md': 'THIRD_PARTY.md',
        '../mod/metol/assets/README.md': 'mod/metol/assets/README.md',
    }.items():
        rights = rights.replace(']('+relative+')',
            '](https://github.com/YeJianbo/nx300m-mod/blob/'+version+'/'+destination+')')
    for document in ('INSTALL.md', 'DELIVERY.md'):
        guide = guide.replace(']('+document+')', '](https://github.com/YeJianbo/nx300m-mod/blob/'+version+'/docs/'+document+')')
    files = {
        **{'payload/'+name:data for name,data in payload.items()},
        'update_card.py': (ROOT/'tools/update_release_card.py').read_bytes(),
        'manifest.json': (json.dumps(manifest, indent=2)+'\n').encode(),
        'USER_GUIDE.md': guide.encode('utf-8'),
        'RISKS_AND_RIGHTS.md': rights.encode('utf-8'),
        'README.md': ('# NX300M 菜单与预览控制升级包\n\n'
            '目前按已有匹配 RAM 服务、Still LUT 和 Session 预览后端的 NX300M 1.15 环境验证；'
            'NX300 可能兼容，但未验证，不保证其他机型或固件版本适用。'
            '更新菜单、面板脚本与预览身份扫描启动器，保留现有 IPCS/Still 硬件后端。'
            '不是首装或刷机包，不包含风格库、固件、字体及个人配置。\n\n'
            '关机取卡，在电脑解压本包。先 `python update_card.py --card H:/` 检查，'
            '通过后加 `--apply` 写入。完整按键、触摸、保存及回退步骤见 '
            '[使用指南](USER_GUIDE.md)。盘符请按实际修改。\n\n'
            '本项目非三星官方项目。测试版可能导致拍摄中断、异常退出或文件损坏，'
            '使用前先备份，重要拍摄前验证。第三方代码与素材沿用各自许可证；'
            '风格数据的格式转换不代表再分发授权。'
            '详见 [使用风险与版权说明](RISKS_AND_RIGHTS.md)。\n\n'
            '需要完整文档时查看 '
            'https://github.com/YeJianbo/nx300m-mod/blob/'+version+'/docs/USER_GUIDE.md\n').encode(),
        'LICENSES/Metol-AGPL-3.0.txt': (ROOT/'mod/metol/LICENSE').read_bytes(),
        'LICENSES/jsmn-MIT.txt': (ROOT/'mod/metol/vendor/jsmn-LICENSE').read_bytes(),
        'NOTICE.md': ('# 菜单来源\n\nNX300M nxui 适配自 WApaperplane/metol，固定提交 '
            '`ddbe8b119e046798e557add13837834f5e8d90ac`，沿用 AGPL-3.0。\n\n'
            'Nuklear 核心、model 和 services 使用该上游版本；许可证见 LICENSES。'
            'JSON 解析使用 zserge/jsmn（MIT）。参考图署名、许可及修改说明见 '
            'LICENSES/REFERENCE_IMAGES.md。\n').encode(),
        'LICENSES/Nuklear-MIT.txt': (ROOT/'work/reference/metol/test_server/nxui/vendor/LICENSE').read_bytes(),
        'LICENSES/REFERENCE_IMAGES.md': (ROOT/'mod/metol/assets/README.md').read_bytes(),
        'SOURCE.md': ('# 对应源码\n\n本包 nxui 为 Metol 的 AGPL-3.0 适配版本。对应版本源码及构建脚本：\n\n'
            'https://github.com/YeJianbo/nx300m-mod/tree/'+version+'\n\n'
            '菜单源码位于 `mod/metol/`，构建入口 `tools/build_metol.py --menu-only`。'
            '上游依赖、固定版本和许可证见仓库 `THIRD_PARTY.md`、`mod/metol/PORT.md` 与 '
            '`mod/metol/ATTRIBUTION.md`。同一 Release 的 `nx300m-menu-source-'+version+'.zip` '
            '另附本次构建使用的上游 model、services、Nuklear 和启动汇编。原厂 SDK 和系统库需使用者合法取得。\n').encode(),
    }
    path = output/('nx300m-menu-upgrade-'+version+'.zip')
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            archive.writestr(name, data)
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None or set(archive.namelist()) != set(files):
            raise ValueError('Release archive integrity failure')
    (output/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(dict(archive=str(path.relative_to(ROOT)), files=len(files),
                         menu_sha256=menu_hash, matching_backend_files=len(requires)), indent=2))
    return path


def source_bundle(version: str, checkout: Path) -> Path:
    checkout = checkout.resolve()
    if subprocess.check_output(['git', 'status', '--porcelain'], cwd=checkout).strip():
        raise ValueError('Source checkout must be committed and clean')
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=checkout).decode().strip()
    data = subprocess.check_output(['git', 'archive', 'HEAD'], cwd=checkout)
    path = ROOT/'outputs/releases'/version/('nx300m-menu-source-'+version+'.zip')
    upstream = (
        'test_server/nxui/nxui_model.c', 'test_server/nxui/nxui_model.h',
        'test_server/nxui/nxui_services.c', 'test_server/nxui/nxui_services.h',
        'test_server/nxui/vendor/nuklear.h', 'test_server/nxui/vendor/LICENSE',
        'test_server/filmlab/src/crt0_arm.S', 'scripts/filmlab/recipes.json', 'LICENSE',
    )
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        with tarfile.open(fileobj=io.BytesIO(data)) as source:
            for member in source:
                if member.isfile():
                    archive.writestr(member.name, source.extractfile(member).read())
        for name in upstream:
            archive.write(ROOT/'work/reference/metol'/name, 'work/reference/metol/'+name)
        archive.writestr('SOURCE-COMMIT.txt', commit+'\n')
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise ValueError('Source archive CRC failure')
    print('SOURCE_ARCHIVE='+str(path.relative_to(ROOT)))
    return path


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True)
    parser.add_argument('--source-checkout', type=Path, help='已提交的公开源码检出，附带构建所需上游源码')
    args = parser.parse_args()
    build(args.version)
    if args.source_checkout:
        source_bundle(args.version, args.source_checkout)
