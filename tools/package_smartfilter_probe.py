"""Package the independent ABI4/schema2 S2 one-attempt probe after matching VM gates.

This tool does not build binaries, boot a VM, or access a card. ABI3 archives,
reports and sd-timing staging are never replaced by this packager.
"""
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
    'README.md': ROOT / 'docs/SMARTFILTER_PROBE.md',
    'nx300m-mod/bin/smartfilter_probe_launcher.arm': OUT / 'smartfilter_probe_launcher.arm',
    'nx300m-mod/smartfilter-probe-collector.sh': ROOT / 'mod/deployment/mfw/smartfilter-probe-collector.sh',
}
VM_GATES = ('arm-vm', 'ram-vm', 's2-vm', 'probe-vm', 'collector-vm')
RUNTIME_GATES = ('ram-vm', 's2-vm', 'probe-vm', 'collector-vm')
RUNTIME_FILES = {
    'nx300m-mod/bin/smartfilter_loader.arm': 'bin/smartfilter_loader.arm',
    'nx300m-mod/lib/smartfilter_ram.bin': 'smartfilter_ram.bin',
}
COLLECTOR_FILES = {
    'nx300m-mod/bin/smartfilter_probe_launcher.arm': 'bin/smartfilter_probe_launcher.arm',
    'nx300m-mod/smartfilter-probe-collector.sh': 'smartfilter-probe-collector.sh',
}
CONTROL_FILES = {
    'nx300m-mod/bin/nxui.arm', 'nx300m-mod/mfw-panel-actions.sh',
    'nx300m-mod/gui_mfw.NX300M', 'nx300m-mod/gui_preview.NX300M',
    'nx300m-mod/fn-panel/gui_mfw.NX300M', 'nx300m-mod/fn-panel/gui_preview.NX300M',
    'nx300m-mod/smartfilter-preview.sh',
}
REQUIRED_CONTROL_CHECKS = {
    'diag_export_dispatch', 'diag_no_data_truthful', 'probe_once_dispatch',
    's2_enable_dispatch', 'menu_navigation', 'continuous_actions_rejected',
    'success_returns_to_viewfinder', 'failure_stays_operable', 'status_stays_visible',
    'back', 'stock_focus_restored', 'orphan_focus_recovery',
    'notice_overlap_returns_stock', 'reopen_after_notice', 'foreign_focus_not_stolen',
}
REQUIRED_S2_CHECKS = {
    'production_mode_1_and_ade_1', 'managed_work_prepare_and_execute_retry',
    'queued_frame_completes_after_soft_stop', 'late_prepare_after_soft_stop',
    'initialization_failure_passthrough', 'seven_slot_install_failure_rolls_back',
    'invalid_work_mapping_rejected', 'synthetic_scope_covers_cached_stock_work',
    'timing_three_groups_export', 'timing_restore_stages_present',
    'timing_frozen_repeat_export', 'timing_pending_ycc_export',
    'timing_disabled_preserves_frames', 'timing_persistence_readback_and_idempotence',
    'timing_capture_wait_and_finish', 'timing_corruption_refuses_log_append',
}


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def require_checks(report, label, *, allow_list=False):
    checks = report.get('checks')
    if isinstance(checks, dict):
        valid = bool(checks) and all(isinstance(key, str) and key and value is True
                                     for key, value in checks.items())
    elif allow_list and isinstance(checks, list):
        valid = bool(checks) and all(isinstance(item, dict) and item.get('passed') is True
                                     and isinstance(item.get('check'), str) and item['check']
                                     for item in checks)
    else:
        valid = False
    if not valid:
        raise ValueError(f'{label} requires nonempty checks with every result true')
    return checks


def package_bytes(name, source):
    data = source.read_bytes()
    # Collector evidence is explicitly bound to its ORIGINAL tested bytes.
    # Existing menu-script probes use LF-normalized payloads, as in ABI3.
    if name not in COLLECTOR_FILES and source.suffix in ('.sh', '.md'):
        data = data.replace(b'\r\n', b'\n')
    return data


MEDIA_SOURCES = (
    'runtime/smartfilter_media.h', 'runtime/smartfilter_persistence.h',
    'runtime/smartfilter_probe_control.h', 'runtime/smartfilter_loader.c',
    'runtime/live_loader_core.h',
    'mod/deployment/mfw/smartfilter-probe-launcher.c',
    'mod/deployment/mfw/smartfilter-probe-collector.sh',
    'tools/smartfilter_media_fs_host.c', 'tools/probe_smartfilter_media_fs.py',
)
MEDIA_REQUIRED_CONTROL_SOURCES = set(MEDIA_SOURCES[:6])
MEDIA_CANDIDATES = {
    'smartfilter_loader.arm': 'nx300m-mod/bin/smartfilter_loader.arm',
    'smartfilter_probe_launcher.arm': 'nx300m-mod/bin/smartfilter_probe_launcher.arm',
    'smartfilter_ram.bin': 'nx300m-mod/lib/smartfilter_ram.bin',
}
MEDIA_SOURCE_ROOT = ROOT


def validate_media_fs(base, payloads):
    """Require actual isolated FAT evidence bound to current sources/binaries."""
    report = read_json(base / 'media-fs/report.json')
    run = report.get('filesystem_run', {})
    fat = run.get('fat32', {})
    if (report.get('passed') is not True or report.get('namespace_exit') != 0
            or report.get('source_and_candidate_bytes_stable') is not True
            or report.get('physical_card_accessed') is not False
            or run.get('passed') is not True or run.get('private_mount_namespace') is not True
            or run.get('native_production_code') is not True
            or run.get('physical_card_accessed') is not False
            or run.get('image_created_exclusively') is not True
            or run.get('all_mounts_removed') is not True or run.get('loop_released') is not True
            or run.get('cleanup_errors') != [] or fat.get('tested') is not True
            or fat.get('passed') is not True or fat.get('native_only') is not True
            or fat.get('mocked_fs_or_uid') is not False):
        raise ValueError('Real FAT32 filesystem verification has not passed and cleaned up')
    require_checks(run, 'real FAT32 filesystem')
    observations = fat.get('observations')
    if (not isinstance(observations, list) or len(observations) != 2
            or {item.get('umask') for item in observations} != {'0022', '0000'}
            or any(type(item.get('uid')) is not int or type(item.get('euid')) is not int
                   or item['uid'] == item['euid'] or item.get('fs_type') != 0x4d44
                   for item in observations)):
        raise ValueError('Real FAT32 verification did not observe mismatched mount UID/euid')
    sources = report.get('sources', {})
    if set(sources) != set(MEDIA_SOURCES):
        raise ValueError('Real FAT32 source inventory is incomplete')
    for name in MEDIA_SOURCES:
        if hashlib.sha256((MEDIA_SOURCE_ROOT / name).read_bytes()).hexdigest() != sources[name]:
            raise ValueError('Real FAT32 tested source differs: ' + name)
    digests = {name: hashlib.sha256(payloads[target]).hexdigest()
               for name, target in MEDIA_CANDIDATES.items()}
    if report.get('candidates') != digests:
        raise ValueError('Real FAT32 candidate bytes differ')
    build = read_json(base / 'ram-build-report.json')
    built_sources = build.get('control_sources', {})
    if (not MEDIA_REQUIRED_CONTROL_SOURCES <= set(built_sources)
            or build.get('control_artifacts') != {name: digests[name] for name in
                                                 ('smartfilter_loader.arm', 'smartfilter_probe_launcher.arm')}):
        raise ValueError('Real FAT32 control build provenance is incomplete')
    for name, digest in built_sources.items():
        if (name not in MEDIA_SOURCES and not (name.startswith('runtime/') and name.endswith('.h')
                and '/' not in name[len('runtime/'):])):
            raise ValueError('Unexpected control build source path')
        if hashlib.sha256((MEDIA_SOURCE_ROOT / name).read_bytes()).hexdigest() != digest:
            raise ValueError('Control source changed since ARM build: ' + name)
    return report


def validate_candidate(controls_pointer='controls-menu-latest.json'):
    """Read-only admission; no output is created until every gate has passed."""
    reports = {}
    for name in VM_GATES:
        report = read_json(OUT / name / 'report.json')
        if report.get('passed') is not True or report.get('private_vm_stopped') is not True:
            raise ValueError(f'{name} has not completed successfully')
        reports[name] = report
    require_checks(reports['arm-vm'], 'arm-vm', allow_list=True)
    s2_checks = require_checks(reports['s2-vm'], 's2-vm')
    if not REQUIRED_S2_CHECKS <= set(s2_checks):
        raise ValueError('S2 lifecycle, timing and rollback probe is incomplete')
    # New scenario names are owned by their VM runners. Do not manufacture a
    # passing report or guess their names before the actual runners export it.
    for name in ('probe-vm', 'collector-vm'):
        require_checks(reports[name], name)
    build = read_json(OUT / 'ram-build-report.json')
    if (build.get('build_exit_code') != 0 or build.get('abi') != 4
            or build.get('hook_count') != 7 or build.get('s1_hook_count') != 5
            or build.get('s2_hook_count') != 7 or len(build.get('hooks', [])) != 7):
        raise ValueError('Expected a successful ABI4 seven-slot build (S1=5, S2=7)')
    payloads = {name: package_bytes(name, source) for name, source in FILES.items()}
    for name, hook_count in (('ram-vm', 5), ('s2-vm', 7)):
        if (reports[name].get('actual_ram_loader') is not True
                or reports[name].get('actual_plt_hooks') != hook_count):
            raise ValueError(f'{name} did not verify the expected {hook_count} PLT hooks')
    for name in RUNTIME_GATES:
        tested_build = read_json(OUT / name / 'candidate.json')['ram_build']
        if tested_build.get('build') != build['build'] or tested_build.get('abi') != 4:
            raise ValueError(f'{name} used another module build/ABI')
        for target, tested in RUNTIME_FILES.items():
            if payloads[target] != (OUT / name / 'source-files' / tested).read_bytes():
                raise ValueError(f'Current loader/blob differs from {name}: {target}')
    for target, tested in COLLECTOR_FILES.items():
        source_bytes = FILES[target].read_bytes()
        if source_bytes != (OUT / 'collector-vm/source-files' / tested).read_bytes():
            raise ValueError('Collector ORIGINAL source differs from collector-vm: ' + target)
        if payloads[target] != source_bytes:
            raise ValueError('Collector package bytes differ from tested original: ' + target)
    blob = payloads['nx300m-mod/lib/smartfilter_ram.bin']
    if len(blob) < 72:
        raise ValueError('Truncated ABI4 blob header')
    header = struct.unpack('<18I', blob[:72])
    if (header[0] != 0x5246534e or header[1] != 4 or header[17] != build['build']
            or header[2] != len(blob) or build.get('blob_bytes') != len(blob)
            or header[14] != build.get('config_bytes') or header[16] != build.get('runtime_bytes')):
        raise ValueError('ABI4 blob and build report differ')
    controls_path = Path(read_json(OUT / controls_pointer)['report'])
    controls = read_json(controls_path)
    if controls.get('passed') is not True or controls.get('private_vm_stopped') is not True:
        raise ValueError('Controls-menu VM probe has not completed')
    checks = require_checks(controls, 'controls-menu')
    if not REQUIRED_CONTROL_CHECKS <= set(checks):
        raise ValueError('Controls-menu navigation/probe/no-data checks are incomplete')
    candidates = controls.get('candidate_files', {})
    if (not isinstance(candidates, dict) or not CONTROL_FILES <= set(candidates)
            or not set(candidates) <= set(FILES)):
        raise ValueError('Controls-menu candidate inventory is incomplete or unknown')
    # No menu-item count and no hard-coded count of checks: validate every
    # reported candidate byte/hash and require the core menu assets instead.
    for name, digest in candidates.items():
        if hashlib.sha256(payloads[name]).hexdigest() != digest:
            raise ValueError('Menu candidate differs from its controls probe: ' + name)
    validate_media_fs(OUT, payloads)
    return build, reports, controls_path, payloads


def main():
    build, reports, controls_path, payloads = validate_candidate()
    archive_path = OUT / 'nx300m-smartfilter-s2-probe.zip'
    inventory = []
    with zipfile.ZipFile(archive_path, 'w', compression=zipfile.ZIP_DEFLATED) as package:
        for name, data in payloads.items():
            target = OUT / 'sd-probe' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = (0o100755 if '/bin/' in name or name.endswith('.sh') else 0o100644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            package.writestr(info, data)
            inventory.append(dict(path=name, bytes=len(data), sha256=hashlib.sha256(data).hexdigest()))
    with zipfile.ZipFile(archive_path) as package:
        if package.namelist() != list(payloads):
            raise ValueError('Archive inventory differs from the admitted candidate')
        for name, data in payloads.items():
            if package.read(name) != data or (OUT / 'sd-probe' / name).read_bytes() != data:
                raise ValueError('Archive/SD overlay differs from admitted candidate: ' + name)
    report = dict(
        archive=str(archive_path), profile='S2', abi=4, diagnostic=True, probe=True,
        diagnostic_runtime_schema=2, build=build['build'], files=inventory,
        hook_count=7, s1_hook_count=5, s2_hook_count=7,
        arm_boundary_checks=len(reports['arm-vm']['checks']),
        ram_plt_hooks=reports['ram-vm']['actual_plt_hooks'],
        s2_plt_hooks=reports['s2-vm']['actual_plt_hooks'],
        probe_vm_checks=len(reports['probe-vm']['checks']),
        collector_vm_checks=len(reports['collector-vm']['checks']),
        vm_reports={name: str(OUT / name / 'report.json') for name in VM_GATES},
        camera_button_preview_menu_vm_verified=True, controls_menu_report=str(controls_path),
        probe_contract='idle-no-capture', single_begin_attempt=True, background_collector=True,
        validation_scope='VM only; camera hardware, ISP pixels and RAW remain unverified',
        physical_camera_or_card_accessed=False, hardware_isp_pixels_verified=False,
        raw_menu_restrictions_resolved=False, automatic_startup_changed=False,
        prior_s2_hardware_test_passed=False, real_stage_timings_measured=False,
        hardware_parameter_reuse_enabled=False,
        media_policy='fixed_fat_anchor', real_fat32_filesystem_verified=True,
        media_fs_report=str(OUT / 'media-fs/report.json'),
        media_fs_report_sha256=hashlib.sha256((OUT / 'media-fs/report.json').read_bytes()).hexdigest(),
        real_exfat_filesystem_verified=False,
    )
    (OUT / 'probe-package-report.json').write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(f'Packaged {len(inventory)} files: {archive_path}')


if __name__ == '__main__':
    main()
