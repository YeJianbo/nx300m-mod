"""Combine the checked connection kit and current manual LUT increment; optionally write SD files."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import zipfile

from prepare_manual_lens_debug_card import PROJECT, volume, inventory, save

OUT = PROJECT / 'outputs/deployment/lut-test-card'


def archive_files(path):
    result = {}
    with zipfile.ZipFile(path) as source:
        if source.testzip() is not None:
            raise RuntimeError('Corrupt package: ' + str(path))
        for entry in source.infolist():
            name = entry.filename
            parts = PurePosixPath(name)
            if (entry.is_dir() or name in result or parts.is_absolute() or '..' in parts.parts
                    or '\\' in name or ':' in name
                    or not (name == 'autoexec.sh' or name.startswith(('nx300m-mod/', 'nx300m-lens-debug/')))):
                raise RuntimeError('Unexpected package member: ' + name)
            result[name] = source.read(entry)
    return result


def build(custom_shortcut=False, pending_wifi=None, menu_shortcut=False, fn_shortcut=False):
    if sum((custom_shortcut, menu_shortcut, fn_shortcut)) > 1:
        raise ValueError('Select one panel entry')
    connected = PROJECT / 'outputs/deployment/mfw-connected'
    checked = json.loads((connected / 'arm-vm/report.json').read_text(encoding='utf-8'))
    base = connected / 'nx300m-connected.zip'
    if not checked['passed'] or hashlib.sha256(base.read_bytes()).hexdigest() != checked['archive_sha256']:
        raise RuntimeError('Connection base differs from the checked ARM boot package')
    package = archive_files(base)
    lut = PROJECT / 'outputs/deployment/lut-mod-diagnostics'
    report = json.loads((lut / 'build-report.json').read_text(encoding='utf-8'))
    if not report['diagnostics_included'] or report['look_count'] != 24:
        raise RuntimeError('Build the current 19-style plus 5-probe LUT package first')
    increment = archive_files(lut / 'nx300m-lut-mod.zip')
    for item in report['files']:
        name = 'nx300m-mod/' + item['path']
        if increment[name] != (lut / 'sd' / name).read_bytes():
            raise RuntimeError('LUT package differs from its current staging tree: ' + name)
    for name, source in {
        'bin/nxui.arm': 'outputs/metol/nxui.arm',
        'fonts/mfw-font.charset': 'outputs/metol/mfw-font.charset',
        'bin/lut_engine.arm': 'outputs/metol/lut_engine.arm',
        'bin/nx-remote-controller.arm': 'outputs/metol/remote/nx-remote-controller.arm',
        'bin/lut_live_loader.arm': 'outputs/deployment/lut-live-loader/lut_live_loader.arm',
        'lib/lut_ram_blob.bin': 'outputs/deployment/lut-live-loader/lut_ram_blob.bin',
        'lut-panel.sh': 'mod/deployment/mfw/lut-panel.sh',
        'mfw-panel.sh': 'mod/deployment/mfw/panel.sh',
    }.items():
        if increment['nx300m-mod/' + name] != (PROJECT / source).read_bytes():
            raise RuntimeError('Rebuild current LUT increment: ' + name)
    package.update(increment)
    for name in ('mfw-ram-stage.sh', 'gui_mfw.NX300M'):
        source = 'ram-stage.sh' if name == 'mfw-ram-stage.sh' else name
        package['nx300m-mod/' + name] = (PROJECT/'mod/deployment/mfw'/source).read_bytes().replace(b'\r\n',b'\n')
    # Check the full merged RAM manifest, including unchanged base service files.
    manifest = package['nx300m-mod/runtime-files.list'].decode('ascii').splitlines()
    if len(manifest) != len(set(manifest)):
        raise RuntimeError('Duplicate RAM runtime entries')
    for name in manifest:
        if name.startswith('/') or '..' in name.split('/') or 'nx300m-mod/' + name not in package:
            raise RuntimeError('Incomplete merged RAM runtime: ' + name)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    wifi_run = 'lut-test-' + stamp
    package['nx300m-lens-debug/WIFI_MEM_RUN_ID'] = (wifi_run + '\n').encode('ascii')
    if pending_wifi is not None:
        if pending_wifi not in ('apply', 'restore'):
            raise ValueError('Unknown pending Wi-Fi action')
        # A card request belongs to its run ID. Migrate both together; leaving
        # the old request causes request_mismatch before the patch is applied.
        package['nx300m-lens-debug/WIFI_MEM_REQUEST'] = (wifi_run + ' ' + pending_wifi + '\n').encode('ascii')
        if pending_wifi == 'restore':
            package['nx300m-lens-debug/WIFI_MEM_PROFILE'] = b'stock\n'
    if custom_shortcut:
        package.pop('nx300m-mod/NO_PANEL_SHORTCUT', None)
        entry = package['autoexec.sh']
        old = b'export NX_REMOTE_CUSTOM_KEY=0 NX_REMOTE_PANEL_SHORTCUT=0'
        if entry.count(old) != 1:
            raise RuntimeError('Unknown combined shortcut configuration')
        package['autoexec.sh'] = entry.replace(old, b'export NX_REMOTE_CUSTOM_KEY=1 NX_REMOTE_PANEL_SHORTCUT=0')
    if menu_shortcut:
        package.pop('nx300m-mod/NO_PANEL_SHORTCUT', None)
        entry = package['autoexec.sh']
        old = b'export NX_REMOTE_CUSTOM_KEY=0 NX_REMOTE_PANEL_SHORTCUT=0'
        if entry.count(old) != 1:
            raise RuntimeError('Unknown combined menu entry configuration')
        package['autoexec.sh'] = entry.replace(old, old + b'\nexport NX_REMOTE_MENU_KEY=1')
    if fn_shortcut:
        # These are the bytes recorded by the original 2026-10-24 deployment,
        # not the later CPU renderer. Keep the current nxui for the LUT panel.
        original = PROJECT / 'outputs/deployment/mfw-services/reader-deployment/20261025T022153Z/backup/nx300m-mod'
        binary = (original / 'bin/nxui.arm').read_bytes()
        recorded = json.loads((PROJECT / 'outputs/deployment/mfw-services/native-panel-deployment.json').read_text())
        expected = next(item['sha256'] for item in recorded['files'] if item['path'] == 'nx300m-mod\\bin\\nxui.arm')
        if hashlib.sha256(binary).hexdigest().upper() != expected:
            raise RuntimeError('Original Fn panel differs from its first deployment record')
        package['nx300m-mod/fn-panel/bin/nxui.arm'] = binary
        package['nx300m-mod/fn-panel/gui_mfw.NX300M'] = (PROJECT / 'mod/deployment/mfw/fn-gui_mfw.NX300M').read_bytes().replace(b'\r\n', b'\n')
        package['nx300m-mod/fn-panel/mfw-panel-actions.sh'] = (PROJECT / 'mod/deployment/mfw/fn-panel-actions.sh').read_bytes()
        package['nx300m-mod/mfw-panel.sh'] = (PROJECT / 'mod/deployment/mfw/fn-panel.sh').read_bytes()
        extra = ('fn-panel/bin/nxui.arm', 'fn-panel/gui_mfw.NX300M', 'fn-panel/mfw-panel-actions.sh')
        package['nx300m-mod/runtime-files.list'] = ('\n'.join(sorted(set(manifest).union(extra))) + '\n').encode('ascii')
        manifest = package['nx300m-mod/runtime-files.list'].decode('ascii').splitlines()
        package.pop('nx300m-mod/NO_PANEL_SHORTCUT', None)
        old = b'export NX_REMOTE_CUSTOM_KEY=0 NX_REMOTE_PANEL_SHORTCUT=0'
        if package['autoexec.sh'].count(old) != 1:
            raise RuntimeError('Unknown combined Fn entry configuration')
        package['autoexec.sh'] = package['autoexec.sh'].replace(old,
            b'export NX_REMOTE_CUSTOM_KEY=0 NX_REMOTE_PANEL_SHORTCUT=1\nexport NX_REMOTE_MENU_KEY=0')
    package['nx300m-mod/RESTART_SERVICE'] = ('lut-test-' + stamp + '\n').encode('ascii')
    note = '''NX300M 手动 LUT 验证包

开机入口：保留无线准入工具，启动 NXConnect 服务。
LUT 不自动加载，泛绿 CS 补丁不包含在启动流程；不刷固件。
已有机身 shell 中执行：
  /bin/sh /mnt/mmc/nx300m-mod/lut-load.sh status
  /bin/sh /mnt/mmc/nx300m-mod/lut-load.sh apply
  /bin/sh /mnt/mmc/nx300m-mod/lut-panel.sh
先拍原厂基线，再验证 0% 与 identity；不要先用复杂风格判断色彩。
清除选择：用 RAM runtime 的 lut_engine.arm stock，等待原厂重新写表。
撤销调用 Hook：/bin/sh /mnt/mmc/nx300m-mod/lut-load.sh restore
restore 仅撤销接入，不保证当前 ISP 表立即恢复；需拍摄并核对原厂重载。
手机相册可用不等于 LUT 已加载。无可用机身 shell 时，先测试连接服务并回收日志。
实体加载、缓存刷新、成片颜色、休眠恢复尚未验证。诊断说明见 LUT_CALIBRATION.md。
普通关机、取卡或恢复出厂设置不能当作已验证的 RAM 补丁清除方式。
'''
    note += ('Custom 按键打开服务菜单，Fn 监听关闭。\n' if custom_shortcut
             else 'Custom/Fn 监听关闭。\n')
    if menu_shortcut:
        note = '面板入口：长按 MENU 约 1.2 秒后松开。短按保留原厂功能。\nCustom/Fn 旧入口关闭；面板使用 CPU 绘制、旋转和不透明 XImage 上传。服务菜单可进入 LUT，未加载适配器时只浏览，不自动注入。\n面板先确认 X11 窗口未被遮挡再获取焦点；遮挡时至多抬升一次，持续遮挡或原厂收回焦点时退出，避免不可见面板占键。X11 可见不代表物理 LCD 已呈现；真实显示仍需本次机身日志确认。\nMENU 关闭面板；标准电源键事件交回原厂。显示请求阻塞超过 5 秒时面板进程退出；这不能恢复已挂起的内核或显示服务。\n\n' + note
    if fn_shortcut:
        note = ('面板入口：长按 Fn 约 1.2 秒后松开；MENU/Custom 监听关闭。\n'
                '恢复最初 2026-10-24 的 Xft 弹框程序和六项服务菜单；MENU 或“退出面板”关闭。\n'
                '当前连接服务和独立 LUT 程序保留。旧弹框没有 LUT 入口，不自动加载 LUT。\n'
                '历史文件与首次部署记录一致，不代表本次机身显示已验证。\n\n' + note.replace('Custom/Fn 监听关闭。\n', ''))
    package['nx300m-mod/LUT-TEST-START.txt'] = note.encode('utf-8')
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / 'nx300m-connected-lut-test.zip'
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(package.items()):
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = (0o100755 if name.endswith('.sh') or '/bin/' in name else 0o100644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    save(OUT / 'build-report.json', {
        'archive': str(target), 'files': len(package), 'runtime_entries': len(manifest),
        'connection_base_arm_checked': True, 'merged_package_arm_boot_checked': False,
        'current_ui_and_coverage_paired': True, 'look_count': 24,
        'lut_automatically_loaded': False, 'shortcuts_enabled': custom_shortcut or menu_shortcut or fn_shortcut,
        'menu_shortcut': menu_shortcut, 'custom_shortcut': custom_shortcut,
        'fn_shortcut': fn_shortcut, 'panel_renderer': 'original-20261024-xft' if fn_shortcut else 'current',
        'wifi_run_id': wifi_run, 'pending_wifi_action_migrated': pending_wifi,
        'physical_camera_verified': False, 'flash_written': False,
    })
    return package, stamp


def flushed(path, data):
    with path.open('xb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    if path.read_bytes() != data:
        raise RuntimeError('Readback differs: ' + str(path))


def deploy(package, stamp, serial, custom_shortcut=False, menu_shortcut=False, fn_shortcut=False):
    root = Path('H:/')
    identity = volume(root, serial)
    current = (root / 'autoexec.sh').read_bytes()
    known = [(PROJECT / 'mod/deployment/mfw' / name).read_bytes().replace(b'\r\n', b'\n')
             for name in ('autoexec-lens-wifi-mem.sh', 'autoexec-connected.sh', 'autoexec-services.sh')]
    known.append(known[1].replace(b'NX_REMOTE_CUSTOM_KEY=0', b'NX_REMOTE_CUSTOM_KEY=1'))
    known.append(known[1].replace(b'export NX_REMOTE_CUSTOM_KEY=0 NX_REMOTE_PANEL_SHORTCUT=0',
        b'export NX_REMOTE_CUSTOM_KEY=0 NX_REMOTE_PANEL_SHORTCUT=0\nexport NX_REMOTE_MENU_KEY=1'))
    known.append(known[1].replace(b'export NX_REMOTE_CUSTOM_KEY=0 NX_REMOTE_PANEL_SHORTCUT=0',
        b'export NX_REMOTE_CUSTOM_KEY=0 NX_REMOTE_PANEL_SHORTCUT=1\nexport NX_REMOTE_MENU_KEY=0'))
    if current not in known:
        raise RuntimeError('Unknown current startup entry; not replacing it')
    output = OUT / 'reader' / ('card-' + serial) / stamp
    output.mkdir(parents=True, exist_ok=False)
    report = {'status': 'preparing', 'volume': identity, 'files': [],
              'firmware_written': False, 'camera_code_executed': False,
              'lut_automatically_loaded': False, 'shortcuts_enabled': custom_shortcut or menu_shortcut or fn_shortcut,
              'menu_shortcut': menu_shortcut, 'custom_shortcut': custom_shortcut, 'fn_shortcut': fn_shortcut}
    save(output / 'report.json', report)
    before = inventory(root)
    staged = []
    retired = []
    try:
        if (custom_shortcut or menu_shortcut or fn_shortcut) and (root / 'nx300m-mod/NO_PANEL_SHORTCUT').exists():
            name = 'nx300m-mod/NO_PANEL_SHORTCUT'
            target = root / name
            if not target.is_file() or target.is_symlink():
                raise RuntimeError('Shortcut marker is not a regular file')
            backup = output / 'before' / name
            backup.parent.mkdir(parents=True, exist_ok=True)
            flushed(backup, target.read_bytes())
            retired.append(name)
        for name, data in sorted(package.items()):
            target = root / name
            if target.is_symlink() or not target.resolve().is_relative_to(root.resolve()):
                raise RuntimeError('Unexpected card target: ' + name)
            same = target.is_file() and target.read_bytes() == data
            report['files'].append({'path': name, 'bytes': len(data), 'changed': not same})
            if same:
                continue
            if target.exists():
                backup = output / 'before' / name
                backup.parent.mkdir(parents=True, exist_ok=True)
                old = target.read_bytes()
                flushed(backup, old)
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + '.lut-next')
            flushed(temporary, data)
            staged.append((name, target, temporary))
        volume(root, serial)
        if (root / 'autoexec.sh').read_bytes() != current:
            raise RuntimeError('Startup entry changed during staging')
        for name in retired:
            (root / name).unlink()
        # Publish the boot entry last, after all referenced assets are present.
        for name, target, temporary in sorted(staged, key=lambda row: row[0] == 'autoexec.sh'):
            temporary.replace(target)
        for name, data in package.items():
            if (root / name).read_bytes() != data:
                raise RuntimeError('Final card readback differs: ' + name)
        unchanged_before = [item for item in before if item['path'] not in package and item['path'] not in retired]
        unchanged_after = [item for item in inventory(root) if item['path'] not in package]
        if unchanged_before != unchanged_after:
            raise RuntimeError('Unrelated file metadata changed')
        volume(root, serial)
        report.update(status='deployed_not_executed', readback_verified=len(package),
                      changed_files=len(staged), unrelated_files=len(unchanged_before),
                      retired_markers=retired,
                      unrelated_file_metadata_unchanged=True, backup_directory=str(output / 'before'))
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        save(output / 'report.json', report)
    print(json.dumps({key: value for key, value in report.items() if key != 'files'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deploy', action='store_true')
    parser.add_argument('--serial', default='LOCAL_REDACTED')
    entries = parser.add_mutually_exclusive_group()
    entries.add_argument('--custom-shortcut', action='store_true')
    entries.add_argument('--menu-shortcut', action='store_true')
    entries.add_argument('--fn-shortcut', action='store_true', help='Restore original Fn hold/release and original Xft service box')
    parser.add_argument('--panel-entry-only', action='store_true',
        help='Deploy only the panel entry/UI files onto the existing matching service card; retain Wi-Fi configuration')
    args = parser.parse_args()
    pending = None
    if args.deploy:
        card = Path('H:/')
        volume(card, args.serial.upper())
        request = card / 'nx300m-lens-debug/WIFI_MEM_REQUEST'
        if request.exists():
            if request.is_symlink() or not request.is_file():
                raise RuntimeError('Pending Wi-Fi request is not a regular file')
            parts = request.read_text(encoding='ascii').split()
            if len(parts) != 2 or parts[1] not in ('apply', 'restore'):
                raise RuntimeError('Unknown pending Wi-Fi request')
            pending = parts[1]
    package, stamp = build(args.custom_shortcut, pending, args.menu_shortcut, args.fn_shortcut)
    print('Built merged manual test package:', len(package), 'files', flush=True)
    if args.deploy:
        if args.panel_entry_only:
            names = ('autoexec.sh', 'nx300m-mod/bin/nxui.arm', 'nx300m-mod/bin/nx-remote-controller.arm',
                'nx300m-mod/fonts/mfw-font.charset', 'nx300m-mod/mfw-panel.sh',
                'nx300m-mod/mfw-ram-stage.sh', 'nx300m-mod/gui_mfw.NX300M',
                'nx300m-mod/lut-panel.sh', 'nx300m-mod/RESTART_SERVICE', 'nx300m-mod/LUT-TEST-START.txt')
            # No runtime asset is added by this entry; reject a partial update
            # if the current full staged service lacks a declared resource.
            manifest = package['nx300m-mod/runtime-files.list']
            current_manifest = Path('H:/nx300m-mod/runtime-files.list').read_bytes()
            original_entries = set(current_manifest.decode('ascii').splitlines())
            new_entries = set(manifest.decode('ascii').splitlines())
            allowed_extra = {'fn-panel/bin/nxui.arm', 'fn-panel/gui_mfw.NX300M', 'fn-panel/mfw-panel-actions.sh'} if args.fn_shortcut else set()
            if (original_entries - new_entries or new_entries - original_entries - allowed_extra or
                    any(not (Path('H:/nx300m-mod')/line).is_file() for line in original_entries)):
                raise RuntimeError('Panel-only update requires the complete matching runtime manifest')
            if args.fn_shortcut:
                # Restore only the Fn box, leaving the existing independent LUT
                # renderer/menu and its metadata untouched on this card.
                names = ('autoexec.sh', 'nx300m-mod/bin/nx-remote-controller.arm',
                    'nx300m-mod/mfw-panel.sh', 'nx300m-mod/mfw-ram-stage.sh',
                    'nx300m-mod/runtime-files.list', 'nx300m-mod/RESTART_SERVICE',
                    'nx300m-mod/LUT-TEST-START.txt',
                    *('nx300m-mod/' + name for name in sorted(allowed_extra)))
            package = {name: package[name] for name in names}
        deploy(package, stamp, args.serial.upper(), args.custom_shortcut, args.menu_shortcut, args.fn_shortcut)
