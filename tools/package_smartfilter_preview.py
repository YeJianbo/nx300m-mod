"""Package the independent ABI3 diagnostic candidate after matching VM probes."""
import hashlib
import json
from pathlib import Path
import struct
import zipfile


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/deployment/smartfilter-preview'
FILES = {
    'nx300m-mod/bin/smartfilter_loader.arm': OUT / 'smartfilter_loader.arm',
    'nx300m-mod/lib/smartfilter_ram.bin': OUT / 'smartfilter_ram.bin',
    'nx300m-mod/bin/lut_engine.arm': ROOT / 'outputs/metol/lut_engine.arm',
    'nx300m-mod/bin/nxui.arm': ROOT / 'outputs/metol/nxui.arm',
    'nx300m-mod/bin/nx-remote-controller.arm': ROOT / 'outputs/metol/remote/nx-remote-controller.arm',
    'nx300m-mod/smartfilter-preview.sh': ROOT / 'mod/deployment/mfw/smartfilter-preview.sh',
    'nx300m-mod/lut-with-preview.sh': ROOT / 'mod/deployment/mfw/lut-with-preview.sh',
    'nx300m-mod/mfw-panel-actions.sh': ROOT / 'mod/deployment/mfw/panel-actions.sh',
    'nx300m-mod/gui_mfw.NX300M': ROOT / 'mod/deployment/mfw/gui_mfw.NX300M',
    'nx300m-mod/gui_preview.NX300M': ROOT / 'mod/deployment/mfw/gui_preview.NX300M',
    'nx300m-mod/fn-panel/gui_mfw.NX300M': ROOT / 'mod/deployment/mfw/fn-gui_mfw.NX300M',
    'nx300m-mod/fn-panel/gui_preview.NX300M': ROOT / 'mod/deployment/mfw/gui_preview.NX300M',
    'nx300m-mod/preview-tests/identity.d4lut': OUT / 'ram-vm/source-files/identity.d4lut',
    'nx300m-mod/preview-tests/monochrome.d4lut': OUT / 'ram-vm/source-files/monochrome.d4lut',
    'README.md': ROOT / 'docs/SMARTFILTER_TIMING.md',
}

CONTROL_FILES = {
    'nx300m-mod/bin/nxui.arm',
    'nx300m-mod/mfw-panel-actions.sh',
    'nx300m-mod/gui_mfw.NX300M',
    'nx300m-mod/gui_preview.NX300M',
    'nx300m-mod/fn-panel/gui_mfw.NX300M',
    'nx300m-mod/fn-panel/gui_preview.NX300M',
    'nx300m-mod/smartfilter-preview.sh',
}
CONTROL_CHECKS = {
    'diag_export_dispatch', 's2_enable_dispatch', 'menu_navigation', 'not_enabled_rejected', 'enable_identity_monochrome_sync_restore',
    'success_returns_to_viewfinder', 'failure_stays_operable', 'status_stays_visible',
    'back', 'stock_focus_restored', 'orphan_focus_recovery', 'notice_overlap_returns_stock',
    'reopen_after_notice', 'foreign_focus_not_stolen',
}


def main():
    reports = {}
    for name in ('arm-vm', 'ram-vm', 's2-vm'):
        report = json.loads((OUT / name / 'report.json').read_text(encoding='utf-8'))
        if report.get('passed') is not True or report.get('private_vm_stopped') is not True:
            raise ValueError(f'{name} has not completed successfully')
        reports[name] = report
    s2_checks = reports['s2-vm'].get('checks', {})
    required_s2 = {'production_mode_1_and_ade_1', 'managed_work_prepare_and_execute_retry',
                   'queued_frame_completes_after_soft_stop', 'late_prepare_after_soft_stop',
                   'initialization_failure_passthrough', 'seven_slot_install_failure_rolls_back',
                   'invalid_work_mapping_rejected', 'synthetic_scope_covers_cached_stock_work',
                   'timing_three_groups_export','timing_restore_stages_present','timing_frozen_repeat_export',
                   'timing_pending_ycc_export','timing_disabled_preserves_frames','timing_persistence_readback_and_idempotence',
                   'timing_capture_wait_and_finish','timing_corruption_refuses_log_append'}
    if not required_s2.issubset(s2_checks) or any(value is not True for value in s2_checks.values()):
        raise ValueError('S2 lifecycle and rollback probe is incomplete')
    build = json.loads((OUT / 'ram-build-report.json').read_text(encoding='utf-8'))
    if (build.get('build_exit_code') != 0 or build.get('abi') != 3 or
            build.get('hook_count') != 7 or build.get('s1_hook_count') != 5 or
            build.get('s2_hook_count') != 7 or len(build.get('hooks', [])) != 7):
        raise ValueError('Expected a successful ABI3 seven-slot build (S1=5, S2=7)')
    for name, hook_count in (('ram-vm', 5), ('s2-vm', 7)):
        if (reports[name].get('actual_ram_loader') is not True or
                reports[name].get('actual_plt_hooks') != hook_count):
            raise ValueError(f'{name} did not verify the expected {hook_count} PLT hooks')
        tested = json.loads((OUT / name / 'candidate.json').read_text(encoding='utf-8'))['ram_build']
        if build['build'] != tested['build'] or tested.get('abi') != 3:
            raise ValueError(f'{name} used another module build/ABI')
        for relative, source in (('bin/smartfilter_loader.arm', OUT / 'smartfilter_loader.arm'),
                                 ('smartfilter_ram.bin', OUT / 'smartfilter_ram.bin')):
            if source.read_bytes() != (OUT / name / 'source-files' / relative).read_bytes():
                raise ValueError(f'Current artifact differs from {name}: {source.name}')
    blob = (OUT / 'smartfilter_ram.bin').read_bytes()
    header = struct.unpack('<18I', blob[:72])
    if (header[0] != 0x5246534e or header[1] != 3 or header[17] != build['build'] or
            header[2] != len(blob) or build['blob_bytes'] != len(blob) or
            header[14] != build['config_bytes'] or header[16] != build['runtime_bytes']):
        raise ValueError('ABI3 blob and build report differ')
    controls_path = json.loads((OUT / 'controls-menu-latest.json').read_text(encoding='utf-8'))['report']
    controls = json.loads(Path(controls_path).read_text(encoding='utf-8'))
    if controls.get('passed') is not True or controls.get('private_vm_stopped') is not True:
        raise ValueError('Controls-menu VM probe has not completed')
    candidates = controls.get('candidate_files', {})
    checks = controls.get('checks', {})
    if (set(candidates) != CONTROL_FILES or not CONTROL_CHECKS.issubset(checks) or
            any(value is not True for value in checks.values())):
        raise ValueError('Controls-menu VM probe is incomplete or has failed checks')
    for name, item in candidates.items():
        if hashlib.sha256(FILES[name].read_bytes().replace(b'\r\n', b'\n') if name.endswith('.sh') else FILES[name].read_bytes()).hexdigest() != item:
            raise ValueError('Menu candidate differs from its controls probe: ' + name)
    archive_path = OUT / 'nx300m-smartfilter-s2-timing.zip'
    inventory = []
    with zipfile.ZipFile(archive_path, 'w', compression=zipfile.ZIP_DEFLATED) as package:
        for name, source in FILES.items():
            data = source.read_bytes()
            if source.suffix in ('.sh', '.md'):
                data = data.replace(b'\r\n', b'\n')
            target = OUT / 'sd-timing' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = (0o100755 if '/bin/' in name or name.endswith('.sh') else 0o100644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            package.writestr(info, data)
            inventory.append(dict(path=name, bytes=len(data), sha256=hashlib.sha256(data).hexdigest()))
    with zipfile.ZipFile(archive_path) as package:
        for item in inventory:
            if package.read(item['path']) != (OUT / 'sd-timing' / item['path']).read_bytes():
                raise ValueError(f'Archive differs from SD overlay: {item["path"]}')
    report = dict(archive=str(archive_path), profile='S2', abi=3, diagnostic=True, build=build['build'], files=inventory,
                  hook_count=build['hook_count'], s1_hook_count=build['s1_hook_count'],
                  s2_hook_count=build['s2_hook_count'],
                  arm_boundary_checks=len(reports['arm-vm']['checks']),
                  ram_plt_hooks=reports['ram-vm']['actual_plt_hooks'],
                  s2_plt_hooks=reports['s2-vm']['actual_plt_hooks'],
                  validation_scope='VM only; camera hardware, ISP pixels and RAW remain unverified',
                  physical_camera_or_card_accessed=False, hardware_isp_pixels_verified=False,
                  raw_menu_restrictions_resolved=False, automatic_startup_changed=False,
                  prior_s2_hardware_test_passed=False,diagnostic_runtime_schema=1,
                  real_stage_timings_measured=False,hardware_parameter_reuse_enabled=False)
    report['camera_button_preview_menu_vm_verified'] = True
    report['controls_menu_report'] = controls_path
    (OUT / 'timing-package-report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(f'Packaged {len(inventory)} files: {archive_path}')


if __name__ == '__main__':
    main()
