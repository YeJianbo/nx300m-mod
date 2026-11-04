"""Package the ARM-checked Open diagnostic and write the dedicated test card."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import zipfile
import lut_build_contract
from cube_to_d4 import DEFAULT_METHOD

import prepare_lut_test_card as card

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT/'outputs/deployment/smartfilter-session'
OUT = BASE/'diagnostic-card'


def prepare(*, lut_method=DEFAULT_METHOD):
    vm = BASE/'arm-vm'
    checked = json.loads((vm/'report.json').read_text(encoding='utf-8'))
    required = ('passed', 'private_vm_stopped', 'button_wrapper_arms_real_session_calltrace',
                'loader_wakes_controller_without_first_frame',
                'admission_file_cache_reused_across_commands',
                'admission_new_process_requires_full_hash',
                'native_stop_callback_and_memory_matching_executed',
                'asynchronous_stop_callback_wakes_controller',
                'unchanged_map_resumes_without_capture',
                'unmatched_memory_waits_without_forcing_rebuild',
                'b2y_open_prepare_window_defers_memory_resume',
                'preview_stop_keeps_still_selection_and_hooks',
                'look_switch_while_preview_stopped_does_not_enable_preview',
                'automatic_topology_insert_without_gallery_or_capture',
                'automatic_topology_remove_on_soft_stop',
                'look_switch_does_not_rebuild_topology',
                'refresh_runs_on_controller_after_native_message_dispatch',
                'capture_defers_topology_refresh',
                'full_controller_queue_does_not_block_video_callback',
                'softstop_persists_full_open_window', 'diagnostic_session_reference_disabled',
                'native_rejection_stderr_visible_and_persisted',
                'stale_abi_rejection_visible_without_deleting_state',
                'libc_and_pthread_open_binding_lifecycle_verified',
                'read_only_kdd_syscall_observer_detaches_and_persists',
                'delayed_first_frame_observed_without_control_or_media_leases',
                'first_frame_wait_cancelled_by_softstop_without_tracer',
                'kdd_reuses_authenticated_target_without_second_full_hash',
                'cancelled_readonly_guard_recovered_and_unknown_guard_retained',
                'default_preview_enables_session_with_diagnostics_off',
                'normal_wrapper_stop_releases_session',
                'current_preview_installs_still_before_first_frame',
                'active_stream_switch_capture_and_off_checked',
                'failed_preview_sync_is_reported_without_reverting_choice',
                'control_cancel_reopens_admission_and_detaches',
                'control_drain_reuses_authenticated_files_with_live_rechecks',
                'off_preview_enable_requires_explicit_look',
                'panel_data_root_syncs_to_ram_service',
                'explicit_stock_does_not_revive_saved_lut',
                'matching_still_engine_ready_in_session_process',
                'mono_color_mono_selection_preview_still_synchronized',
                'still_lut_restored_after_preview_and_capture',
                'saved_choice_updated_without_directory_rename',
                'menu_does_not_submit_automatic_preview_restore',
                'actual_fn_menu_action_uses_continuous_backend_capability',
                'reduced_and_full_menu_route_current_lut',
                'probe_only_menu_rejects_continuous_actions',
                'off_restores_native_still_slots_and_stops_preview_processing',
                'look_after_off_lazily_reinstalls_still_adapter')
    if not all(checked.get(key) is True for key in required):
        raise RuntimeError('Current ARM/MFW button, persistence or lifecycle check is incomplete')
    build = json.loads((BASE/'ram-build-report.json').read_text(encoding='utf-8'))
    candidate = json.loads((vm/'candidate.json').read_text(encoding='utf-8'))
    if build['abi'] != 7 or candidate['ram_build']['build'] != build['build']:
        raise RuntimeError('Diagnostic ABI/build differs from its ARM evidence')
    still = ROOT/'outputs/deployment/lut-live-loader'
    for binary, record in ((still/'lut_live_loader.arm', still/'lut-build-report.json'),
                           (ROOT/'outputs/metol/lut_engine.arm', ROOT/'outputs/metol/lut-build-report.json'),
                           (ROOT/'outputs/metol/remote/nx-remote-controller.arm', ROOT/'outputs/metol/remote/lut-build-report.json')):
        lut_build_contract.require(binary, record)
    still_vm = still/'arm-vm'
    still_checked = json.loads((still_vm/'report.json').read_text(encoding='utf-8'))
    if not all(still_checked.get(key) is True for key in
               ('passed','private_vm_stopped','shared_ram_binding_checked','known_image_recovery_checked',
                'unchanged_selection_read_cached','selection_cache_same_size_edits_invalid_delete_recovery_checked')):
        raise RuntimeError('Still LUT installation/readiness checks are incomplete')
    for name, source in (('bin/lut_live_loader.arm',still/'lut_live_loader.arm'),
                         ('lut_ram_blob.bin',still/'lut_ram_blob.bin')):
        if source.read_bytes() != (still_vm/'source-files'/name).read_bytes():
            raise RuntimeError('Still LUT differs from its ARM evidence: '+name)
    panel = ROOT/'outputs/performance/lut-menu-entry/wait-vm'
    panel_checked = json.loads((panel/'report.json').read_text(encoding='utf-8'))
    panel_binary = (ROOT/'outputs/metol/nxui.arm').read_bytes()
    if (not all(panel_checked.get(key) is True for key in
                 ('passed', 'private_vm_stopped', 'hidden_backend_wait_does_not_spin', 'dry_commit_exits_without_wait', 'lut_off_invokes_disable',
                  'menu_close_retains_new_choice_without_legacy_restore', 'long_lut_control_finishes_without_group_kill',
                  'normal_menu_debug_navigation_and_preview_stop'))
            or hashlib.sha256(panel_binary).hexdigest() != panel_checked.get('candidate_sha256')):
        raise RuntimeError('Panel differs from its checked ARM event-loop fix')
    selection = ROOT/'outputs/performance/lut-menu-entry/selection-vm/report.json'
    selection_checked = json.loads(selection.read_text(encoding='utf-8'))
    if (not all(selection_checked.get(key) is True for key in ('passed', 'private_vm_stopped',
                 'browsing_waits_for_confirmation', 'only_final_choice_submitted_once',
                 'full_catalogue_keyboard_and_touch_select_mono', 'off_preview_explains_selection_required',
                 'direct_launcher_does_not_call_adapter_or_restore_selection', 'saved_choice_visible_without_camera_mutation'))
            or hashlib.sha256(panel_binary).hexdigest() != selection_checked.get('candidate_sha256')
            or hashlib.sha256((ROOT/'mod/deployment/mfw/lut-panel.sh').read_bytes()).hexdigest() != selection_checked.get('lut_panel_sha256')):
        raise RuntimeError('Panel differs from its checked native ARM LUT selection')
    entry_checked = json.loads((ROOT/'outputs/performance/lut-menu-entry/arm-vm/report.json').read_text(encoding='utf-8'))
    if (not all(entry_checked.get(key) is True for key in ('passed','private_vm_stopped',
                 'same_process_window_and_font_reused','browse_does_not_call_engine','return_and_reopen_without_apply',
                 'confirmation_uses_data_root'))
            or hashlib.sha256(panel_binary).hexdigest() != entry_checked.get('candidate_sha256')):
        raise RuntimeError('Panel differs from its checked in-process LUT navigation')
    sources = {
        'ipcs-diagnostic/bin/smartfilter_loader.arm': ('bin/smartfilter_loader.arm', BASE/'smartfilter_loader.arm'),
        'ipcs-diagnostic/bin/smartfilter_probe_launcher.arm': ('bin/smartfilter_probe_launcher.arm', BASE/'smartfilter_probe_launcher.arm'),
        'ipcs-diagnostic/lib/smartfilter_ram.bin': ('smartfilter_ram.bin', BASE/'smartfilter_ram.bin'),
        'bin/lut_engine.arm': ('bin/lut_engine.arm', ROOT/'outputs/metol/lut_engine.arm'),
        'bin/lut_live_loader.arm': ('bin/lut_live_loader.arm', still/'lut_live_loader.arm'),
        'lib/lut_ram_blob.bin': ('lut_ram_blob.bin', still/'lut_ram_blob.bin'),
        'smartfilter-preview.sh': ('smartfilter-live-preview.sh', ROOT/'mod/deployment/mfw/smartfilter-live-preview.sh'),
        'mfw-panel-actions.sh': ('panel-actions.sh', ROOT/'mod/deployment/mfw/panel-actions.sh'),
        'common.sh': ('common.sh', ROOT/'mod/deployment/common.sh'),
        'ipcs-kdd-collector.sh': ('ipcs-kdd-collector.sh', ROOT/'mod/deployment/mfw/ipcs-kdd-collector.sh'),
    }
    package = {}
    for target, (tested, source) in sources.items():
        data = source.read_bytes()
        if data != (vm/'source-files'/tested).read_bytes():
            raise RuntimeError('Diagnostic differs from checked ARM bytes: '+target)
        package['nx300m-mod/'+target] = data
    package['nx300m-mod/bin/nxui.arm'] = panel_binary
    package['nx300m-mod/bin/nx-remote-controller.arm'] = (ROOT/'outputs/metol/remote/nx-remote-controller.arm').read_bytes()
    for target, source in (('mfw-panel.sh','fn-panel.sh'), ('lut-load.sh','lut-load.sh'),
                           ('lut-panel.sh','lut-panel.sh'), ('lut-cycle.sh','lut-cycle.sh'),
                           ('fn-panel/mfw-panel-actions.sh','fn-panel-actions.sh')):
        data = (ROOT/'mod/deployment/mfw'/source).read_bytes()
        if data != (vm/'source-files'/source).read_bytes():
            raise RuntimeError('LUT/menu wrapper differs from its ARM check: '+source)
        package['nx300m-mod/'+target] = data
    for source in sorted((still/'compat').glob('blob-*.bin')):
        package['nx300m-mod/lib/lut-compat/'+source.name] = source.read_bytes()
    from build_lut_color_test import build as catalogue_build, OUT as catalogue_out
    catalogue = catalogue_build(method=lut_method)
    data_root = catalogue_out/'sd/nx300m-mod'
    for source in data_root.rglob('*'):
        if source.is_file():
            package['nx300m-mod/'+source.relative_to(data_root).as_posix()] = source.read_bytes()
    menu = (ROOT/'mod/deployment/mfw/gui_preview_ipcs_diagnostic.NX300M').read_bytes()
    if menu != (vm/'source-files/gui_preview_ipcs_diagnostic.NX300M').read_bytes():
        raise RuntimeError('Preview menu differs from checked ARM action route')
    for name in ('gui_preview.NX300M', 'fn-panel/gui_preview.NX300M'):
        package['nx300m-mod/'+name] = menu
    for source, names in (
            ('gui_mfw.NX300M', ('gui_mfw.NX300M',)),
            ('fn-gui_mfw.NX300M', ('fn-panel/gui_mfw.NX300M',)),
            ('gui_debug.NX300M', ('gui_debug.NX300M','fn-panel/gui_debug.NX300M'))):
        data = (ROOT/'mod/deployment/mfw'/source).read_bytes()
        if data != (vm/'source-files'/source).read_bytes():
            raise RuntimeError('Normal menu differs from checked ARM bytes: '+source)
        for name in names:
            package['nx300m-mod/'+name] = data
    quick_menu = (ROOT/'mod/deployment/mfw/gui_preview_quick_ipcs.NX300M').read_bytes()
    if quick_menu != (vm/'source-files/gui_preview_quick_ipcs.NX300M').read_bytes():
        raise RuntimeError('Quick menu differs from checked ARM action route')
    package['nx300m-mod/fn-panel/gui_preview_quick.NX300M'] = quick_menu
    package['nx300m-mod/IPCS_OPEN_DIAGNOSTIC'] = b'1\n'
    package['nx300m-mod/IPCS_SESSION_PREVIEW'] = b'1\n'
    package['nx300m-mod/IPCS-DIAGNOSTIC-START.md'] = (ROOT/'docs/IPCS_DIAGNOSTIC_CARD.md').read_bytes()
    for name, data in package.items():
        if name.endswith(('.sh', '.NX300M')) and b'\r' in data:
            raise RuntimeError('Shell/menu must use its checked LF bytes: '+name)
    OUT.mkdir(parents=True, exist_ok=True)
    archive = OUT/'nx300m-ipcs-open-diagnostic.zip'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        for name, data in sorted(package.items()):
            z.writestr(name, data)
    with zipfile.ZipFile(archive) as z:
        if z.testzip():
            raise RuntimeError('Diagnostic package CRC failed')
    report = dict(archive=str(archive), abi=7, build=build['build'],
                  session_mode=1, diagnostics_enabled_by_default=False, per_call_open_diagnostic=True,
                  still_lut_build=lut_build_contract.identity()['build'], catalogue_looks=catalogue['looks'],
                  lut_compiler_method=catalogue['compiler_method'],
                  physical_performance_measured=False, abi4_preview_binaries_replaced=False,
                  still_components_paired=True,
                  arm_report=str(vm/'report.json'), panel_report=str(panel/'report.json'),
                  selection_report=str(selection),
                  files=[dict(path=n, bytes=len(d), sha256=hashlib.sha256(d).hexdigest())
                         for n, d in sorted(package.items())])
    card.save(OUT/'package-report.json', report)
    return package, report


def deploy(package, report, serial, select_look=None):
    root = Path('H:/')
    card.volume(root, serial)
    # Keep the current matching service; only extend its existing RAM assets.
    manifest = [name for name in (root/'nx300m-mod/runtime-files.list').read_text(encoding='ascii').splitlines()
                if name != 'PREVIEW_QUICK_PANEL']
    extra = {name.removeprefix('nx300m-mod/') for name in package
             if not name.endswith('.md') and not name.startswith('nx300m-mod/looks/')
             and name != 'nx300m-mod/looks.txt'}
    for name in set(manifest) | extra:
        parts = PurePosixPath(name)
        if not name or parts.is_absolute() or '..' in parts.parts or '\\' in name or ':' in name:
            raise RuntimeError('Unexpected RAM manifest path: '+name)
        if 'nx300m-mod/'+name not in package and not (root/'nx300m-mod'/name).is_file():
            raise RuntimeError('Current service is missing an asset: '+name)
    package['nx300m-mod/runtime-files.list'] = ('\n'.join(sorted(set(manifest) | extra))+'\n').encode('ascii')
    stamp = 'lut-switch-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    package['nx300m-mod/RESTART_SERVICE'] = (stamp+'\n').encode('ascii')
    if select_look is not None:
        table='nx300m-mod/looks/'+select_look+'/table.d4lut'
        if table not in package:
            raise RuntimeError('Requested startup LUT is not in the checked package')
        package['nx300m-mod/.state/lut-choice'] = (
            '1 /mnt/mmc/'+table+' 100\n').encode('ascii')
    baseline = {name: (root/name).read_bytes() for name in (
        'nx300m-mod/bin/smartfilter_loader.arm', 'nx300m-mod/lib/smartfilter_ram.bin',
        'autoexec.sh')}
    card.OUT = OUT
    card.deploy(package, stamp, serial, preserve_autoexec=True,
                retire_markers=('nx300m-mod/PREVIEW_QUICK_PANEL',))
    for name, data in baseline.items():
        if (root/name).read_bytes() != data:
            raise RuntimeError('Baseline/looks/startup unexpectedly changed: '+name)
    path = OUT/'reader'/('card-'+serial)/stamp/'report.json'
    deployed = json.loads(path.read_text(encoding='utf-8'))
    for key in ('shortcuts_enabled','menu_shortcut','custom_shortcut','fn_shortcut'):
        deployed.pop(key, None)
    deployed.update(card_written=True, abi=7, build=report['build'], session_mode=1,
                    shortcut_configuration_preserved=True,
                    diagnostics_enabled_by_default=False,
                    per_call_open_diagnostic=True, abi4_preview_binaries_preserved=True,
                    lut_catalogue_looks=report['catalogue_looks'],
                    lut_compiler_method=report['lut_compiler_method'],
                    still_lut_build=report['still_lut_build'], physical_camera_tested=False,
                    selected_startup_look=select_look)
    card.save(path, deployed)
    print('DEPLOYMENT_REPORT='+str(path), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deploy', action='store_true')
    parser.add_argument('--serial', default='LOCAL_REDACTED')
    parser.add_argument('--select-look', help='Explicit startup LUT selection for this deployment')
    parser.add_argument('--lut-method', choices=('fit','legacy'), default=DEFAULT_METHOD,
                        help='离线编译正式库；legacy 恢复旧采样算法')
    args = parser.parse_args()
    package, report = prepare(lut_method=args.lut_method)
    print(f"Prepared {len(package)} files, ABI {report['abi']}, build {report['build']}, Session=1", flush=True)
    if args.deploy:
        deploy(package, report, args.serial.replace('-', '').upper(), args.select_look)
