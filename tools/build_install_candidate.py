"""Bundle the current development installation inputs without flashing a device."""
import gzip
import io
import json
from pathlib import Path
import tarfile

from build_stock_app_loader import PERSISTENT_HOOKS

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = ROOT / 'outputs/deployment/app-loader/boot-chain/persistent-candidate'
OUT = ROOT / 'outputs/deployment/install-candidate'


def main():
    report = json.loads((CANDIDATE / 'report.json').read_text(encoding='utf-8'))
    if {Path(item['destination']).name for item in report['hooks']} != set(PERSISTENT_HOOKS):
        raise ValueError('Regenerate the persistent candidate for the current modules first')
    payload = {}
    for item in report['rootfs_changes']:
        name = 'rootfs' + item['destination']
        data = (CANDIDATE / name).read_bytes()
        if item.get('source') in PERSISTENT_HOOKS.values() and data != (ROOT / item['source']).read_bytes():
            raise ValueError('Persistent candidate differs from the current module: ' + name)
        metadata = item['metadata']
        payload[name] = (data, int(metadata['mode'], 8), metadata.get('uid', 0), metadata.get('gid', 0))
        if item['change'] == 'modified':
            name = 'original' + item['destination']
            payload[name] = ((CANDIDATE / name).read_bytes(), int(metadata['mode'], 8),
                             metadata.get('uid', 0), metadata.get('gid', 0))
    bundle = ROOT / 'outputs/deployment/sd-bundle/nx300m-mod-sd.tar.gz'
    sd_report = json.loads((bundle.parent / 'build-report.json').read_text(encoding='utf-8'))
    if not sd_report.get('runtime', {}).get('migrated'):
        raise ValueError('Complete SD service payload has not been migrated to the shared RAM stage')
    with tarfile.open(bundle) as sd:
        names = {member.name for member in sd if member.isfile()}
        missing = ['nx300m-mod/' + name for name in report['service_payload_requirements']
                   if 'nx300m-mod/' + name not in names]
        if missing:
            raise ValueError('Incomplete SD bundle: ' + ', '.join(missing))
        for name, source in {'ram-start.sh': 'autoexec-services.sh', 'mfw-ram-stage.sh': 'ram-stage.sh'}.items():
            packed = sd.extractfile('nx300m-mod/' + name).read()
            current = (ROOT / 'mod/deployment/mfw' / source).read_text(encoding='utf-8').replace('\r\n', '\n').encode()
            if packed != current:
                raise ValueError('Rebuild the SD bundle for the current shared RAM stage: ' + name)
    payload['sd/nx300m-mod-sd.tar.gz'] = (bundle.read_bytes(), 0o644, 0, 0)
    payload['rootfs-report.json'] = ((CANDIDATE / 'report.json').read_bytes(), 0o644, 0, 0)
    for name in ('APP_LOADER.md', 'SERVICE_LOADER.md', 'INSTALL.md', 'REMOTE.md', 'QUICKSHARE.md',
                 'CONTROLS.md', 'FIRMWARE_ENTRY_CONTROLS.md', 'LOOKS.md', 'NXCONNECT.md', 'DELIVERY.md',
                 'SOFTWARE_ISP.md', 'JPEG_ENCODE.md', 'MOD_MANAGEMENT.md',
                 'RECOVERY_AND_DEPLOYMENT.md', 'LAUNCH_PROFILES.md', 'OFFLINE_PREPARATION.md',
                 'LEGACY_EXPOSURE_AUDIT.md'):
        path = ROOT / 'docs' / name
        if path.is_file():
            payload['docs/' + name] = (path.read_bytes(), 0o644, 0, 0)
    readme = '''# NX300M 开发安装候选

此目录与同名 tar.gz 是一次完整候选：rootfs 增量、原厂恢复文件与配套完整 SD 资源。它不是 SLP 固件，不会自动刷机；本轮没有安装实体相机或重建休眠快照。

- `rootfs/`：按 rootfs-report.json 的路径、模式与属主审阅的增量；含九个原生模块和两个启动入口的共用 wrapper。
- `original/`：被修改的 hib2.sh 与 camera desktop 的原厂 1.15 原文；恢复时保留原元数据。
- `sd/nx300m-mod-sd.tar.gz`：解压后的 nx300m-mod 目录对应 `/mnt/mmc/nx300m-mod`，包含服务脚本、ARM remote、Quick Share、网页与 LUT。DCIM 照片不属于这个包。
- `docs/APP_LOADER.md`、`docs/SERVICE_LOADER.md`：加载链、唯一 remote 管理、完整服务要求与停用恢复说明。

九模块随包提供，默认 launch-profile 为 stock，不增加项目 preload。按 docs/LAUNCH_PROFILES.md 选择 services、controls、color 或 full；只检查所选组所需模块，缺项时保留原命令与环境。DISABLED 标记优先于配置，配置只影响下一次 app exec，不能卸载旧快照里的模块。

rootfs 与 SD 应来自同一份候选。service-loader 调用 ram-start.sh --service，复用 MFW 的已核对复制/比对机制，把完整服务脚本、二进制、网页和 LUT 资源复制到可执行 tmpfs，再从 RAM 启动；旧 SD 服务未退出时拒绝迁移，没有 SD 执行回退。常驻服务的代码、锁和日志使用 RAM；原厂 app 的资源/LUT root 保留原语义，不代表全 app 模块已经迁移。stop.sh 的手动停止状态会阻止挂载协调立即重启；需要操作 RAM runtime 内的 start.sh/stop.sh。

原厂 app 与 di-camera-app 符号链接没有修改。候选覆盖已确认的更新后 hib2 app 调用及 camera desktop 注册入口；普通冷启动的 camera 请求发起者仍待实机记录确认。运行中的旧休眠镜像不会因替换脚本而获得新映射，更新或恢复 SO 后必须按该机型已确认流程重建快照；当前包不执行该流程，也不擦除快照。

此前完整原厂 app/SD ARM VM 验证见工作区 `outputs/deployment/app-loader/installed-vm/probe-report.json`，九模块共载见 `co-load/lazy-report.json`；dispatch-probe/report.json 和 service-loader/verification-summary.json 也属于 RAM 入口迁移前的验证记录。本轮 RAM 服务模式结果见 `outputs/deployment/service-loader/ram-service-vm/report.json`，使用明确标记的内部入口和图库 helper fixture，不运行原厂 app；本轮未重跑完整原厂 app VM。VM 测试专用的 candidate-vm.tar.gz 含硬件 shim，不能用作实体安装资源。此候选不含这些实验室 shim。

服务生命周期 VM 快照早于随后完成的 LUT 源 root 兼容修补；当前 SD 包包含修补后重编的 remote/LUT engine。源 root 兼容专项见 `outputs/deployment/lut-data-root-probe/report.json`，camera PID 验证器使用测试替身，未验证实机图像；未重复整轮服务生命周期检查。
'''
    payload['README.md'] = (readme.encode('utf-8'), 0o644, 0, 0)
    manifest = {'status': 'development installation candidate; no physical installation or flash',
                'hook_count': len(report['hooks']), 'hooks': report['hooks'],
                'launch_policy': report['launch_policy'],
                'service_runtime': sd_report['runtime'],
                'sd_bundle_bytes': bundle.stat().st_size,
                'files': [{'path': name, 'bytes': len(data), 'mode': oct(mode), 'uid': uid, 'gid': gid}
                          for name, (data, mode, uid, gid) in sorted(payload.items())]}
    payload['manifest.json'] = ((json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode(), 0o644, 0, 0)
    OUT.mkdir(parents=True, exist_ok=True)
    body = io.BytesIO()
    with tarfile.open(fileobj=body, mode='w') as archive:
        for name, (data, mode, uid, gid) in sorted(payload.items()):
            target = OUT / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            member = tarfile.TarInfo('nx300m-mod-install-candidate/' + name)
            member.size, member.mode, member.uid, member.gid = len(data), mode, uid, gid
            archive.addfile(member, io.BytesIO(data))
    destination = OUT.parent / 'nx300m-mod-install-candidate.tar.gz'
    destination.write_bytes(gzip.compress(body.getvalue(), mtime=0))
    print(json.dumps({'directory': str(OUT), 'archive': str(destination), 'bytes': destination.stat().st_size,
                      'hook_count': len(report['hooks']), 'files': len(payload)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
