"""Write the checked preview overlay with the existing backed-up SD deployer."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import zipfile

import prepare_lut_test_card as card
import package_smartfilter_probe as probe_package


def probe_vm_gates(base, report):
    """Recheck local evidence without opening a volume, starting VMs or rebuilding."""
    reports = {}
    for name in probe_package.VM_GATES:
        checked = json.loads((base / name / 'report.json').read_text(encoding='utf-8'))
        if checked.get('passed') is not True or checked.get('private_vm_stopped') is not True:
            raise RuntimeError(f'{name} verification has not passed')
        reports[name] = checked
    try:
        probe_package.require_checks(reports['arm-vm'], 'arm-vm', allow_list=True)
        checks = probe_package.require_checks(reports['s2-vm'], 's2-vm')
        if not probe_package.REQUIRED_S2_CHECKS <= set(checks):
            raise ValueError('S2 lifecycle, timing and rollback probe is incomplete')
        for name in ('probe-vm', 'collector-vm'):
            probe_package.require_checks(reports[name], name)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    for name, count in (('ram-vm', 5), ('s2-vm', 7)):
        if (reports[name].get('actual_ram_loader') is not True
                or reports[name].get('actual_plt_hooks') != count):
            raise RuntimeError(f'{name} did not verify the expected {count} PLT hooks')
    for name in probe_package.RUNTIME_GATES:
        tested = json.loads((base / name / 'candidate.json').read_text(encoding='utf-8'))['ram_build']
        if tested.get('abi') != 4 or tested.get('build') != report['build']:
            raise RuntimeError(f'{name} used another module build/ABI')
    controls_path = json.loads((base / 'controls-menu-latest.json').read_text(encoding='utf-8'))['report']
    controls = json.loads(Path(controls_path).read_text(encoding='utf-8'))
    if controls.get('passed') is not True or controls.get('private_vm_stopped') is not True:
        raise RuntimeError('Controls-menu VM probe has not completed')
    try:
        checks = probe_package.require_checks(controls, 'controls-menu')
        if not probe_package.REQUIRED_CONTROL_CHECKS <= set(checks):
            raise ValueError('Controls-menu navigation/probe/no-data checks are incomplete')
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    candidates = controls.get('candidate_files', {})
    if (not isinstance(candidates, dict) or not probe_package.CONTROL_FILES <= set(candidates)
            or not set(candidates) <= set(probe_package.FILES)):
        raise RuntimeError('Controls-menu candidate inventory is incomplete or unknown')
    return candidates


BASE = card.PROJECT / 'outputs/deployment/smartfilter-preview'


def start_guide(*, timing=False, probe=False, live=False, live_timing=False):
    return ('SMARTFILTER-LIVE-TIMING-START.md' if live_timing else
            'SMARTFILTER-LIVE-START.md' if live else
            'SMARTFILTER-PROBE-START.md' if probe else
            'SMARTFILTER-TIMING-START.md' if timing else 'SMARTFILTER-TEST-START.md')


def runtime_manifest_entries(*, probe=False, live=False):
    entries = {'gui_preview.NX300M', 'fn-panel/gui_preview.NX300M'}
    files = probe_package.FILES
    if live:
        from package_smartfilter_live import FILES
        files = FILES
    if probe or live:
        entries |= {name[len('nx300m-mod/'):] for name in files
                    if name.startswith('nx300m-mod/')}
    return entries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial')
    parser.add_argument('--s2', action='store_true', help='Explicitly select the experimental S2 package')
    parser.add_argument('--check-only', action='store_true', help='Validate the package without accessing a card')
    parser.add_argument('--quick-panel', action='store_true', help='Use the compact Custom panel and headless soft-stop shortcut')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--timing', action='store_true', help='Explicitly select the separate ABI3 S2 diagnostic package')
    mode.add_argument('--probe', action='store_true', help='Explicitly select the separate ABI4/schema2 S2 one-attempt probe package')
    mode.add_argument('--live', action='store_true', help='Explicitly select the separate ABI4 continuous S2 candidate')
    mode.add_argument('--live-timing', action='store_true', help='Select the ABI4 continuous S2 candidate with opt-in timing')
    args = parser.parse_args()
    continuous = args.live or args.live_timing
    if args.quick_panel and not continuous:
        parser.error('--quick-panel requires --live or --live-timing')
    if not args.check_only and not args.serial:
        parser.error('--serial is required for a card write')
    if (args.timing or args.probe or continuous) and not args.s2:
        parser.error(('--live-timing' if args.live_timing else '--live' if args.live else
                      '--probe' if args.probe else '--timing') + ' requires explicit --s2')
    report_name = ('live-timing-package-report.json' if args.live_timing else
                   'live-package-report.json' if args.live else
                   'probe-package-report.json' if args.probe else
                   'timing-package-report.json' if args.timing else 'package-report.json')
    report = json.loads((BASE / report_name).read_text(encoding='utf-8'))
    admitted_live = None
    if continuous:
        from package_smartfilter_live import validate_candidate
        _, _, _, admitted_live = validate_candidate()
        if (report.get('continuous') is not True or report.get('abi') != 4 or
                report.get('probe') or bool(report.get('diagnostic')) != args.live_timing):
            raise RuntimeError('Expected the separate ABI4 continuous S2 candidate')
        if args.live_timing and (report.get('opt_in_timing') is not True or
                                report.get('default_timing') is not False or
                                report.get('diagnostic_runtime_schema') != 2):
            raise RuntimeError('Expected opt-in ABI4/schema2 continuous timing with default timing disabled')
    if args.probe and (report.get('diagnostic') is not True or report.get('probe') is not True
                      or report.get('abi') != 4 or report.get('diagnostic_runtime_schema') != 2):
        raise RuntimeError('Expected the ABI4/schema2 diagnostic probe package')
    if args.probe and (report.get('hook_count') != 7 or report.get('s1_hook_count') != 5
                       or report.get('s2_hook_count') != 7):
        raise RuntimeError('Probe package must preserve S1=5 and S2=7 PLT hooks')
    if args.timing and (not report.get('diagnostic') or report.get('abi') != 3 or report.get('probe')):
        raise RuntimeError('Expected the ABI3 timing diagnostic package')
    if not continuous and report.get('continuous'):
        raise RuntimeError('Continuous package requires explicit --live or --live-timing')
    if not args.timing and not args.probe and not args.live_timing and (report.get('diagnostic') or report.get('probe')):
        raise RuntimeError('Diagnostic package requires explicit --timing, --probe or --live-timing')
    if (report.get('profile') == 'S2') != args.s2:
        raise RuntimeError('S2 package requires explicit --s2; S1/S2 reports cannot be mixed')
    menu_key = 'camera_button_preview_menu_vm_verified' if args.s2 else 'camera_button_preview_menu'
    if not report.get(menu_key):
        raise RuntimeError('Candidate package lacks the verified camera-button preview menu')
    controls = probe_vm_gates(BASE, report) if args.probe else {}
    if not args.probe:
        for name in (('arm-vm', 'ram-vm', 's2-vm') if args.s2 else ('arm-vm', 'ram-vm')):
            checked = json.loads((BASE / name / 'report.json').read_text(encoding='utf-8'))
            if not checked.get('passed') or not checked.get('private_vm_stopped'):
                raise RuntimeError(f'{name} verification has not passed')
    expected = {item['path']: item for item in report['files']}
    if len(expected) != len(report['files']):
        raise RuntimeError('Duplicate candidate report inventory')
    if args.probe and set(expected) != set(probe_package.FILES):
        raise RuntimeError('Probe package inventory is incomplete or unknown')
    package = {}
    if continuous and set(expected) != set(admitted_live):
        raise RuntimeError('Continuous package inventory is incomplete or unknown')
    archive = ('nx300m-smartfilter-s2-live-timing.zip' if args.live_timing else
               'nx300m-smartfilter-s2-live.zip' if args.live else
               'nx300m-smartfilter-s2-probe.zip' if args.probe else
               'nx300m-smartfilter-s2-timing.zip' if args.timing else
               'nx300m-smartfilter-s2-preview.zip' if args.s2 else 'nx300m-smartfilter-preview.zip')
    with zipfile.ZipFile(BASE / archive) as source:
        names = source.namelist()
        if len(names) != len(set(names)) or set(names) != set(expected):
            raise RuntimeError('Candidate archive inventory differs from its report')
        for name in names:
            parts = PurePosixPath(name)
            if parts.is_absolute() or '..' in parts.parts or '\\' in name or ':' in name:
                raise RuntimeError('Unexpected candidate path')
            data = source.read(name)
            item = expected[name]
            if continuous and data != admitted_live[name]:
                raise RuntimeError('Continuous candidate differs from admitted bytes: ' + name)
            if len(data) != item['bytes'] or hashlib.sha256(data).hexdigest() != item['sha256']:
                raise RuntimeError('Candidate bytes differ: ' + name)
            if name != 'README.md' and not name.startswith('nx300m-mod/'):
                raise RuntimeError('Candidate is outside the MOD directory')
            if name in ('nx300m-mod/bin/smartfilter_loader.arm', 'nx300m-mod/lib/smartfilter_ram.bin'):
                tested = 'bin/smartfilter_loader.arm' if name.endswith('.arm') else 'smartfilter_ram.bin'
                gates = probe_package.RUNTIME_GATES if args.probe else (('ram-vm', 's2-vm') if args.s2 else ('ram-vm',))
                for gate in gates:
                    if data != (BASE / gate / 'source-files' / tested).read_bytes():
                        raise RuntimeError('Candidate differs from the RAM-tested build: ' + gate)
            if args.probe and name in probe_package.COLLECTOR_FILES:
                tested = probe_package.COLLECTOR_FILES[name]
                if data != (BASE / 'collector-vm/source-files' / tested).read_bytes():
                    raise RuntimeError('Candidate differs from the collector-tested original: ' + name)
            if name in controls and hashlib.sha256(data).hexdigest() != controls[name]:
                raise RuntimeError('Menu candidate differs from its controls probe: ' + name)
            guide = start_guide(timing=args.timing, probe=args.probe, live=args.live, live_timing=args.live_timing)
            target = 'nx300m-mod/' + guide if name == 'README.md' else name
            package[target] = data
    if args.probe:
        try:
            probe_package.validate_media_fs(BASE, package)
        except (ValueError, FileNotFoundError) as exc:
            raise RuntimeError(str(exc)) from exc
    if args.check_only:
        print(f'Checked {len(expected)} files in {archive}; no card accessed or written.')
        return
    serial = args.serial.replace('-', '').upper()
    identity = card.volume(Path('H:/'), serial)
    if args.quick_panel:
        controls = json.loads(Path(report['controls_menu_report']).read_text(encoding='utf-8'))
        required_quick = {'compact_navigation_and_full_menu', 'compact_softstop_dispatch',
                          'compact_timing_dispatch', 'headless_softstop_without_x_or_ui',
                          'no_automatic_status_job', 'softstop_does_not_hold_menu_pid',
                          'menu_reopens_during_stop', 'failed_stop_does_not_auto_retry',
                          'manual_stop_retry_background', 'stop_survives_card_log_open_failure'}
        if not controls.get('passed') or not controls.get('private_vm_stopped') or not all(
                controls.get('checks', {}).get(name) is True for name in required_quick):
            raise RuntimeError('Compact panel and headless soft-stop VM checks have not passed')
        package['nx300m-mod/PREVIEW_QUICK_PANEL'] = b'1\n'
    # Existing MFW staging replaces only its owned service on the next entry.
    # This does not inject the preview, alter autoexec or restart the camera app.
    stamp = 'smartfilter-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    package['nx300m-mod/RESTART_SERVICE'] = (stamp + '\n').encode('ascii')
    # Preserve the current service's manifest and stage the new menu with it.
    manifest = Path('H:/nx300m-mod/runtime-files.list').read_text(encoding='ascii').splitlines()
    # Include the detached job's launcher/collector and matching loader/blob in
    # verified RAM staging rather than reopening live SD code after service exit.
    required = runtime_manifest_entries(probe=args.probe, live=continuous)
    if args.quick_panel:
        required.add('PREVIEW_QUICK_PANEL')
    for name in set(manifest) | required:
        parts = PurePosixPath(name)
        if not name or parts.is_absolute() or '..' in parts.parts or '\\' in name or ':' in name:
            raise RuntimeError('Unexpected current RAM runtime entry')
        if 'nx300m-mod/' + name not in package and not (Path('H:/nx300m-mod') / name).is_file():
            raise RuntimeError('Missing current RAM runtime asset: ' + name)
    package['nx300m-mod/runtime-files.list'] = ('\n'.join(sorted(set(manifest) | required)) + '\n').encode('ascii')
    card.OUT = BASE
    card.deploy(package, stamp, serial, preserve_autoexec=True)
    output = BASE / 'reader' / ('card-' + serial) / stamp
    deployed = json.loads((output / 'report.json').read_text(encoding='utf-8'))
    deployed.update(candidate_files=len(expected), candidate_build=report['build'],
                    owned_ram_service_restart_requested=True, preview_automatically_enabled=False,
                    autoexec_changed=False, original_lut_library_changed=False)
    deployed['compact_custom_panel'] = args.quick_panel
    deployed['camera_button_preview_menu'] = True
    card.save(output / 'report.json', deployed)
    card.save(BASE / 'latest-card-deployment.json', {
        'report': str(output / 'report.json'), 'backup': str(output / 'before'),
        'status': deployed['status'], 'volume': identity,
        'readback_verified': deployed['readback_verified'],
        'preview_automatically_enabled': False})
    print('Deployment report: ' + str(output / 'report.json'))


if __name__ == '__main__':
    main()
