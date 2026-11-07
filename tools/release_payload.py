"""Keep complete SD, first-install and upgrade payloads on the deployed code."""
import hashlib
import json
from pathlib import Path, PurePosixPath

from installer_backend import verify_backend
from manual_lens_wifi_payload import verify_wifi_payload


PREFIX = 'nx300m-mod/'
USER_FILES = {PREFIX + name for name in ('looks.txt', 'looks-cycle.txt', 'lenses.txt')}


def is_core(name):
    return name not in USER_FILES and not name.startswith(PREFIX + 'looks/')


def deployed_overlay(root):
    """Only promote the four rebuilt binaries actually read back from the card."""
    import lut_build_contract
    report = json.loads((root / 'outputs/review/2026-11-07-card/deployment-result.json').read_text(encoding='utf-8'))
    if report['status'] != 'verified' or not report['backend_preserved']:
        raise ValueError('最新菜单/关闭候选缺少完整写卡回读记录。')
    lut_build_contract.require(root / 'outputs/metol/lut_engine.arm', root / 'outputs/metol/lut-build-report.json')
    lut_build_contract.require(root / 'outputs/deployment/lut-live-loader/lut_live_loader.arm',
                               root / 'outputs/deployment/lut-live-loader/lut-build-report.json')
    sources = {
        PREFIX + 'bin/nxui.arm': 'outputs/metol/nxui.arm',
        PREFIX + 'fn-panel/bin/nxui.arm': 'outputs/metol/nxui.arm',
        PREFIX + 'bin/lut_engine.arm': 'outputs/metol/lut_engine.arm',
        PREFIX + 'bin/lut_live_loader.arm': 'outputs/deployment/lut-live-loader/lut_live_loader.arm',
    }
    files = {}
    for name, source in sources.items():
        data = (root / source).read_bytes()
        expected = report['files'][name]
        if len(data) != expected['bytes'] or hashlib.sha256(data).hexdigest() != expected['sha256']:
            raise ValueError('本地构建与已写卡候选不同：' + name)
        files[name] = data
    return files


def verify_complete(read, *, allow_stock_wifi=False):
    """Check the actual runtime closure, including the separate Wi-Fi entry."""
    backend = verify_backend(read)
    wifi = verify_wifi_payload(read, allow_stock=allow_stock_wifi)
    runtime = read(PREFIX + 'runtime-files.list').decode('ascii').splitlines()
    required = {
        'bin/nxui.arm', 'fn-panel/bin/nxui.arm', 'bin/lut_engine.arm', 'bin/lut_live_loader.arm',
        'bin/nx-remote-controller.arm', 'bin/nx-quickshare-armv7', 'lib/lut_ram_blob.bin',
        'mfw-ram-stage.sh', 'mfw-services.sh', 'mfw-panel.sh', 'mfw-panel-actions.sh',
        'common.sh', 'start.sh', 'stop.sh', 'service.sh', 'lut-boot.sh', 'lut-load.sh',
        'lut-panel.sh', 'fonts/mfw-font.charset', 'gui_mfw.NX300M', 'web_root/index.html',
    }
    if not required.issubset(runtime):
        raise ValueError('完整运行清单缺少：' + ', '.join(sorted(required - set(runtime))))
    try:
        entry = read('autoexec.sh')
        if b'"$lens/autoexec-wifi.sh"' not in entry or b'"$service/mfw-service-entry.sh"' not in entry:
            raise ValueError('根启动入口没有接通无线补丁及 MOD 服务。')
        read(PREFIX + 'mfw-service-entry.sh')
        for name in runtime:
            path = PurePosixPath(name)
            if not name or path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name:
                raise ValueError('运行清单路径无效：' + name)
            read(PREFIX + name)
    except (KeyError, FileNotFoundError) as exc:
        raise ValueError('完整运行资源缺失：' + str(exc)) from exc
    if read(PREFIX + 'bin/nxui.arm') != read(PREFIX + 'fn-panel/bin/nxui.arm'):
        raise ValueError('两个菜单程序版本不同。')
    return {'backend': backend, 'manual_lens_wifi': wifi, 'runtime_files': len(runtime)}
