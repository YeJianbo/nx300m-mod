"""UI-25 three-tier comparison: 66 / 50 / 40 ms paint targets.

Same source, same wheel burst, same guest, three binaries that differ only in
NX_UI_TARGET_MS. For each tier this records: how many of the 813 wheel presses
were consumed, how many mid-burst paints happened, the final field/tune value,
inter-paint percentiles, the trailing discrete-key latency, and CPU/wakeups.
Software rasterization only; no physical FPS, LCD or camera claim.
"""
import argparse
import hashlib
import json
import time
from pathlib import Path

import arm_vm
import probe_menu_startup_vm as vm
import probe_menu_coalesce_vm as co
from probe_lut_selection_vm import TAP_DRIVER

ROOT = Path(__file__).resolve().parents[1]
TIERS = (66, 50, 40)


def build_probe(tiers):
    runs = "\n".join(f'run_variant t{ms} /perf/nxui-{ms}.arm' for ms in tiers)
    return co.PROBE.replace('run_variant before /perf/before.arm\nrun_variant current /perf/mod/bin/nxui.arm', runs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--tiers', default='66,50,40',
                        help='comma-separated NX_UI_TARGET_MS values to build and compare')
    parser.add_argument('--shipped-for', type=int, default=None,
                        help='use the shipped outputs/metol/nxui.arm for this tier value instead of the measurement build')
    args = parser.parse_args()
    tiers = tuple(int(x) for x in args.tiers.split(','))
    out = args.output_dir.resolve()
    if (out/'report.json').exists():
        raise RuntimeError('Preserve earlier evidence; choose a new output directory')
    from zipfile import ZipFile
    archive = ROOT/'outputs/looks/lightroom-import/on-camera-test/nx300m-lut-color-test.zip'
    with ZipFile(archive) as package:
        catalogue = package.read('nx300m-mod/looks.txt').decode().rstrip()
        looks = {name: package.read(name) for name in package.namelist()
                 if '/looks/' in name and name.endswith('.d4lut')}
    binaries = {}
    for ms in tiers:
        path = (ROOT/'outputs/metol/nxui.arm' if args.shipped_for == ms
                else ROOT/f'outputs/review/2026-11-13-performance-25hz/ui-25/nxui-{ms}.arm')
        if not path.exists():
            raise SystemExit(f'missing tier binary {path}; run scratch/build-ui-25-tiers.sh')
        binaries[ms] = path.read_bytes()
    vm.OUT = out
    vm.PROBE = build_probe(tiers).replace('@CATALOGUE@', catalogue).replace('@BURST@', co.BURST_KEYS)
    vm.DRIVER = vm.DRIVER.replace('    if(argc==2) {', TAP_DRIVER+co.BURST_DRIVER+'\n    if(argc==2) {', 1)
    vm.DRIVER = vm.DRIVER.replace('    XEvent event={0};', '    if(capture_only){XCloseDisplay(d);return 0;}\n    XEvent event={0};', 1)
    vm.DRIVER = vm.DRIVER.replace('XStringToKeysym("Menu")', 'XStringToKeysym(argc==3 ? argv[2] : "Menu")')
    vm.EXTRA_FILES = {
        'perf/recorder.sh': b'#!/bin/sh\nprintf \'%s\\n\' "$*" >> /perf/actions.txt\nprintf \'STAGED %s\\n\' "${2:-off}"\n',
        'perf/mod/fonts/mfw-font.charset': (ROOT/'outputs/metol/mfw-font.charset').read_bytes(),
    }
    vm.EXTRA_FILES.update({f'perf/nxui-{ms}.arm': data for ms, data in binaries.items()})
    vm.EXTRA_FILES.update({'perf/mod/'+name.removeprefix('nx300m-mod/'): data for name, data in looks.items()})
    vm.prepare()
    report = dict(passed=False, hardware_fps=False, scope=__doc__.strip().splitlines()[0],
                  binaries={str(ms): hashlib.sha256(data).hexdigest() for ms, data in binaries.items()},
                  tiers={})
    started = False
    try:
        vm.call('start'); started = True
        deadline = time.monotonic()+180
        while time.monotonic() < deadline:
            if (out/'serial.log').exists() and 'MENU_PERF_VM_READY' in (out/'serial.log').read_text(errors='replace'):
                break
            time.sleep(.5)
        else:
            raise TimeoutError('UI-25 VM startup')
        execution = vm.call('exec', '/bin/sh /perf/probe.sh')
        arm_vm.write_json(out/'execution.json', execution)
        for ms in tiers:
            co.collect(out, f'coalesce-t{ms}.log')
        if execution['exit_code']:
            raise RuntimeError('UI-25 probe failed: '+execution['output'][-3000:])
        for ms in tiers:
            text = (out/f'coalesce-t{ms}.log').read_text(errors='replace')
            metrics = co.analyze(text)
            detail = co.details(text)
            report['tiers'][str(ms)] = dict(metrics=metrics, details=detail,
                                            within_tier_target=bool(metrics['interpaint_p50_ms'] is not None
                                                                    and metrics['interpaint_p50_ms'] <= ms + 15))
        def ok(ms):
            d = report['tiers'][str(ms)]
            return dict(all_input_consumed=d['details']['wheel_presses'] == 813,
                        final_value=d['details']['final'] is not None and d['details']['final']['field'] == 1
                                    and d['details']['final']['tune'] == [3, 0, 0, 0, 0, 0, 0],
                        mid_burst_paints=d['details']['full_batches'] >= 20 and d['details']['painted_during_full_batches'] >= 5,
                        final_latency=d['metrics']['final_latency_ms'] is not None and 0 <= d['metrics']['final_latency_ms'] <= 150)
        checks = {str(ms): ok(ms) for ms in tiers}
        report['checks'] = checks
        eligible = [ms for ms in tiers if all(checks[str(ms)].values())]
        report['eligible_tiers'] = eligible
        report['selected_tier_ms'] = min(eligible) if eligible else None
        report['passed'] = bool(eligible)
        report['selection_reason'] = ('Fastest tier that still consumed every input, painted during the burst, '
                                      'ended on the correct value and returned the final frame within 150 ms.'
                                      if eligible else 'No tier met every requirement; nothing is adopted.')
        if not eligible:
            raise RuntimeError('No tier met every requirement: '+json.dumps(checks))
    except BaseException as error:
        report['failure'] = str(error)
        arm_vm.write_json(out/'report.json', report)
        raise
    finally:
        if started:
            report['stop'] = vm.call('stop')
        arm_vm.write_json(out/'report.json', report)
    print(json.dumps({k: report[k] for k in ('passed', 'eligible_tiers', 'selected_tier_ms', 'selection_reason')},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
