"""Package first-connection tools and stock references; contains no installation payload."""
import io
import json
from pathlib import Path
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/deployment'


def main():
    report = json.loads((OUT / 'mtp-host-entry-report.json').read_text(encoding='utf-8'))
    if report['physical_camera_verified']:
        raise ValueError('This preparation bundle must describe the offline inspection')
    payload = {}
    for name in ('tools/inspect_camera_usb.ps1', 'tools/camera_readonly_preflight.sh',
                 'docs/OFFLINE_PREPARATION.md', 'docs/INSTALL.md',
                 'docs/RECOVERY_AND_DEPLOYMENT.md', 'docs/LAUNCH_PROFILES.md'):
        payload[name] = (ROOT / name).read_bytes()
    for name in ('mtp-host-entry-report.json', 'mtp-operation-dispatch.asm',
                 'mtp-command-validation.asm', 'mtp-command-receive.asm'):
        payload['analysis/' + name] = (OUT / name).read_bytes()

    originals = ('usr/bin/hib2.sh',
                 'opt/share/applications/com.samsung.di-camera-app-nx300.desktop')
    original_checks = []
    with tarfile.open(ROOT / 'work/stock-1.15/rootfs.tar') as archive:
        for name in originals:
            stock = archive.extractfile(name).read()
            candidate = OUT / 'install-candidate/original' / name
            matches = candidate.read_bytes() == stock
            if not matches:
                raise ValueError('Candidate recovery reference differs from stock: ' + name)
            payload['original/' + name] = stock
            original_checks.append({'path': name, 'bytes': len(stock), 'candidate_equals_stock': matches})

    readme = '''# NX300M 离线接机准备包

这是检查工具与参考文件，不是固件或安装器。没有自动运行入口、机身模块或刷机命令。

1. 相机开机并用USB连接电脑后，在解压目录执行：
   `powershell.exe -NoProfile -File .\\tools\\inspect_camera_usb.ps1`
   设备记录只保存在电脑 `outputs/deployment/camera-usb.json`。
2. 记录用于识别USB模式和驱动，不能证明已有shell。此时无需安装rootfs候选。
3. 只有取得经过核对的机身shell后，才使用 `tools/camera_readonly_preflight.sh`。
   它只输出内核、挂载、进程和模块映射。本轮未在实体相机执行。

`analysis/` 是原厂1.15程序的静态追踪结果，不代表生产命令或shell已在实体设备打通。
`original/` 的两份启动文件已与原厂TAR逐字节核对；保留原文不等于已验证救砖。
解压不会保留Linux属主或安装元数据，这些文件供核对；不要从ZIP直接覆盖相机。
`docs/` 描述完整项目的候选路径，所述Mod/SD候选并不包含在本准备包。

电脑端原始分析工具为项目 `tools/inspect_stock_usb_entry.py`，复现需要项目的原厂TAR、Conda环境和依赖。
'''
    payload['README.md'] = readme.encode('utf-8')
    manifest = {'scope': 'Offline preparation only; no camera connection or installation',
                'original_checks': original_checks,
                'files': [{'path': name, 'bytes': len(data)} for name, data in sorted(payload.items())]}
    payload['manifest.json'] = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    directory = OUT / 'offline-preparation'
    body = io.BytesIO()
    with zipfile.ZipFile(body, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(payload.items()):
            target = directory / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            archive.writestr(name, data)
    destination = OUT / 'nx300m-offline-preparation.zip'
    destination.write_bytes(body.getvalue())
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise ValueError('Preparation ZIP integrity check failed')
    print(json.dumps({'archive': str(destination), 'bytes': destination.stat().st_size,
                      'files': len(payload), 'original_files_verified': len(original_checks)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
