"""Package continuous ABI4 S2 separately, reusing unchanged hardware/media gates."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import zipfile

import package_smartfilter_probe as probe
from probe_smartfilter_controls_vm import LIVE_CHECKS

ROOT, OUT = probe.ROOT, probe.OUT
FILES = dict(probe.FILES)
FILES.update({
    'nx300m-mod/smartfilter-preview.sh': ROOT / 'mod/deployment/mfw/smartfilter-live-preview.sh',
    'nx300m-mod/gui_preview.NX300M': ROOT / 'mod/deployment/mfw/gui_preview_live.NX300M',
    'nx300m-mod/fn-panel/gui_preview.NX300M': ROOT / 'mod/deployment/mfw/gui_preview_live.NX300M',
    'README.md': ROOT / 'docs/SMARTFILTER_LIVE_TIMING.md',
    'nx300m-mod/mfw-panel.sh': ROOT / 'mod/deployment/mfw/fn-panel.sh',
    'nx300m-mod/fn-panel/mfw-panel-actions.sh': ROOT / 'mod/deployment/mfw/fn-panel-actions.sh',
    'nx300m-mod/fn-panel/gui_preview_quick.NX300M': ROOT / 'mod/deployment/mfw/gui_preview_quick.NX300M',
    'nx300m-mod/preview-softstop.sh': ROOT / 'mod/deployment/mfw/preview-softstop.sh',
})


@contextmanager
def continuous_policy():
    # The original probe-only checks and inventory are restored even on failure.
    names = ('FILES', 'CONTROL_FILES', 'REQUIRED_CONTROL_CHECKS')
    previous = {name: getattr(probe, name) for name in names}
    try:
        probe.FILES = FILES
        probe.CONTROL_FILES = previous['CONTROL_FILES'] | {
            'nx300m-mod/mfw-panel.sh', 'nx300m-mod/fn-panel/mfw-panel-actions.sh',
            'nx300m-mod/fn-panel/gui_preview_quick.NX300M',
            'nx300m-mod/preview-softstop.sh',
        }
        probe.REQUIRED_CONTROL_CHECKS = set(LIVE_CHECKS)
        yield
    finally:
        for name, value in previous.items():
            setattr(probe, name, value)


def validate_candidate():
    with continuous_policy():
        build, reports, controls_path, payloads = probe.validate_candidate('controls-live-latest.json')
    live = probe.read_json(OUT / 'live-vm/report.json')
    if (live.get('passed') is not True or live.get('private_vm_stopped') is not True
            or live.get('source_and_candidate_bytes_stable') is not True):
        raise ValueError('Continuous wrapper ARM VM has not passed and stopped')
    checks = probe.require_checks(live, 'continuous-wrapper')
    required_checks = {
        'executed_wrapper_exact_bytes', 'wrapper_and_state_tmpfs',
        'sync_does_not_enable', 'monochrome_actual_s2_status',
        'monochrome_timing_disabled_in_actual_resident_config',
        'same_apply_two_consecutive_real_worker_frames',
        'still_selection_bytes_unchanged_by_test_monochrome', 'still_host_mode_unchanged',
        'enable_s2_reads_current_choice_and_strength', 'sync_updates_current_choice_and_strength',
        'restore_soft_stops_retains_routes_and_clears_backend',
        'existing_uncertain_guard_original_bytes_and_inode_retained',
        'timing_opt_in_continuous_not_probe', 'timing_capacity_keeps_continuous_lut',
        'timed_restore_old_window_new_generation', 'timed_restore_clears_backend_and_keeps_routes',
        'untimed_restore_does_not_export', 'wrapper_export_failure_does_not_prevent_soft_stop',
        'keep_alloc_failure_preserves_restore_success', 'keep_export_failure_preserves_restore_success',
        'kept_window_uses_existing_log_persistence',
        'kept_persistence_failure_leaves_valid_ram_and_soft_stop',
        'keep_timing_waits_for_inflight_restore', 'keep_timing_never_exports_rolled_back_restore',
        'kept_snapshot_has_old_identity_and_real_stages',
        'kept_capture_slots_independent_of_full_frame_storage', 'kept_persisted_log_valid',
        'wrapper_kept_window_persisted_via_source_log',
    }
    if (not required_checks <= set(checks) or live.get('actual_wrapper_launcher_loader') is not True
            or live.get('actual_ram_loader') is not True or live.get('actual_plt_hooks') != 7):
        raise ValueError('Continuous wrapper frame, selection and recovery checks are incomplete')
    if hashlib.sha256((ROOT / 'tools/probe_smartfilter_live_vm.py').read_bytes()).hexdigest() != live.get('tool_sha256'):
        raise ValueError('Continuous wrapper verification tool changed')
    tested_files = {
        'nx300m-mod/bin/smartfilter_loader.arm': 'bin/smartfilter_loader.arm',
        'nx300m-mod/lib/smartfilter_ram.bin': 'smartfilter_ram.bin',
        'nx300m-mod/bin/smartfilter_probe_launcher.arm': 'bin/smartfilter_probe_launcher.arm',
        'nx300m-mod/smartfilter-probe-collector.sh': 'smartfilter-probe-collector.sh',
        'nx300m-mod/smartfilter-preview.sh': 'smartfilter-live-preview.sh',
        'nx300m-mod/preview-tests/monochrome.d4lut': 'monochrome.d4lut',
    }
    artifacts = live.get('artifacts', {})
    candidate = OUT / 'live-vm/candidate.json'
    if hashlib.sha256(candidate.read_bytes()).hexdigest() != live.get('candidate_sha256'):
        raise ValueError('Continuous VM candidate manifest changed')
    for name, tested in tested_files.items():
        data, item = payloads[name], artifacts.get(tested, {})
        if (item.get('sha256') != hashlib.sha256(data).hexdigest() or item.get('bytes') != len(data)
                or data != (OUT / 'live-vm/source-files' / tested).read_bytes()):
            raise ValueError('Continuous wrapper bytes differ from VM: ' + name)
    selection = probe.read_json(ROOT / 'outputs/metol/lut-selection-probe/report.json')
    if selection.get('passed') is not True or selection.get('source_unchanged') is not True:
        raise ValueError('ARM selection and RAM sync dispatch must pass')
    sources = selection.get('sources', {})
    if 'mod/metol/lut_engine_shared.h' not in sources or 'tools/probe_lut_selection.py' not in sources:
        raise ValueError('Selection/sync report lacks source binding')
    for name, digest in sources.items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest:
            raise ValueError('Selection/sync source changed: ' + name)
    for target, sources in (
        ('nx300m-mod/bin/lut_engine.arm', ('mod/metol/lut_engine.c', 'mod/metol/lut_engine_shared.h')),
        ('nx300m-mod/bin/nxui.arm', ('mod/metol/nxui.c', 'mod/metol/lut_engine_shared.h')),
        ('nx300m-mod/bin/nx-remote-controller.arm', ('mod/metol/remote/remote.c', 'mod/metol/lut_engine_shared.h')),
    ):
        if any((ROOT / name).stat().st_mtime_ns > FILES[target].stat().st_mtime_ns for name in sources):
            raise ValueError('Rebuild current controls before packaging: ' + target)
    return build, reports, controls_path, payloads


def main():
    build, reports, controls_path, payloads = validate_candidate()
    archive = OUT / 'nx300m-smartfilter-s2-live-timing.zip'
    staging = OUT / 'sd-live-timing'
    inventory = []
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as package:
        for name, data in payloads.items():
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = (0o100755 if '/bin/' in name or name.endswith('.sh') else 0o100644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            package.writestr(info, data)
            inventory.append(dict(path=name, bytes=len(data), sha256=hashlib.sha256(data).hexdigest()))
    with zipfile.ZipFile(archive) as package:
        if package.namelist() != list(payloads):
            raise ValueError('Continuous archive inventory differs')
        for name, data in payloads.items():
            if package.read(name) != data or (staging / name).read_bytes() != data:
                raise ValueError('Continuous archive readback differs: ' + name)
    report = dict(
        archive=str(archive), archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        profile='S2', abi=4, diagnostic=True, probe=False, continuous=True,
        opt_in_timing=True, default_timing=False, performance_optimization=True,
        diagnostic_runtime_schema=2, build=build['build'], files=inventory,
        hook_count=7, s1_hook_count=5, s2_hook_count=7,
        camera_button_preview_menu_vm_verified=True, controls_menu_report=str(controls_path),
        live_vm_report=str(OUT / 'live-vm/report.json'),
        selection_sync_report=str(ROOT / 'outputs/metol/lut-selection-probe/report.json'),
        automatic_startup_changed=False, physical_camera_or_card_accessed=False,
        hardware_isp_pixels_verified=False, real_stage_timings_measured=False,
        hardware_parameter_reuse_enabled=False,
        validation_scope='ARM wrapper and menu integration with fixture/software pixels; camera live view remains unverified',
    )
    (OUT / 'live-timing-package-report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(f'Packaged {len(inventory)} continuous files: {archive}')


if __name__ == '__main__':
    main()
