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
    """Promote the latest checked standard runtime into the first-install payload."""
    import lut_build_contract
    lut_build_contract.require(root / 'outputs/metol/lut_engine.arm', root / 'outputs/metol/lut-build-report.json')
    lut_build_contract.require(root / 'outputs/deployment/lut-live-loader/lut_live_loader.arm',
                               root / 'outputs/deployment/lut-live-loader/lut-build-report.json')
    menu = root / 'outputs/metol/nxui.arm'
    probe = json.loads((root / 'outputs/review/lut-client-ui/camera-group-panel-regression-vm/report.json').read_text(encoding='utf-8'))
    group_render = json.loads((root / 'outputs/review/lut-client-ui/camera-group-x11-vm/report.json').read_text(encoding='utf-8'))
    group_logic = json.loads((root / 'outputs/review/lut-client-ui/camera-group-arm-probe/report.json').read_text(encoding='utf-8'))
    menu_hash = hashlib.sha256(menu.read_bytes()).hexdigest()
    if not probe.get('passed') or probe.get('menu_sha256') != menu_hash:
        raise ValueError('最新菜单与通过的私有 VM 回归不一致。')
    if (not group_render.get('passed') or group_render.get('nxui_sha256') != menu_hash
            or not group_render.get('visual_pixels_differ')):
        raise ValueError('最新菜单与通过的自定义分组 X11 回归不一致。')
    expected_sources = {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in ('mod/metol/nxui.c', 'mod/metol/nxui_lut_panel.h')
    }
    if not group_logic.get('passed') or any(
            group_logic.get('source_sha256', {}).get(name) != digest
            for name, digest in expected_sources.items()):
        raise ValueError('最新菜单与通过的自定义分组 ARM 逻辑回归不一致。')
    sources = {
        PREFIX + 'start.sh': 'mod/deployment/start.sh',
        PREFIX + 'bin/ram_asset_verify.arm': 'outputs/controls/ram-asset-verify/ram_asset_verify.arm',
        PREFIX + 'mfw-ram-stage.sh': 'mod/deployment/mfw/ram-stage.sh',
        PREFIX + 'bin/nx-remote-controller.arm': 'outputs/metol/remote/nx-remote-controller.arm',
        PREFIX + 'web_root/index.html': 'outputs/metol/remote/web_root/index.html',
        PREFIX + 'bin/nxui.arm': menu,
        PREFIX + 'fn-panel/bin/nxui.arm': menu,
        PREFIX + 'bin/lut_engine.arm': 'outputs/metol/lut_engine.arm',
        PREFIX + 'bin/lut_live_loader.arm': 'outputs/deployment/lut-live-loader/lut_live_loader.arm',
        PREFIX + 'lib/lut_ram_blob.bin': 'outputs/deployment/lut-live-loader/lut_ram_blob.bin',
        PREFIX + 'smartfilter-preview.sh': 'mod/deployment/mfw/smartfilter-live-preview.sh',
        PREFIX + 'mfw-panel-actions.sh': 'mod/deployment/mfw/panel-actions.sh',
        PREFIX + 'mfw-services.sh': 'mod/deployment/mfw/services.sh',
    }
    files = {}
    for name, source in sources.items():
        path = source if isinstance(source, Path) else root / source
        if not path.is_file():
            raise ValueError('最新运行文件缺失：' + name)
        data = path.read_bytes()
        files[name] = data
    return files


def verify_complete(read, *, allow_stock_wifi=False, backend_files=None):
    """Check the actual runtime closure, including the separate Wi-Fi entry."""
    backend = verify_backend(read, backend_files)
    wifi = verify_wifi_payload(read, allow_stock=allow_stock_wifi)
    runtime = read(PREFIX + 'runtime-files.list').decode('ascii').splitlines()
    required = {
        'bin/nxui.arm', 'fn-panel/bin/nxui.arm', 'bin/lut_engine.arm', 'bin/lut_live_loader.arm',
        'bin/nx-remote-controller.arm', 'bin/ram_asset_verify.arm', 'lib/lut_ram_blob.bin',
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
