"""LUT liveview fps cap (P2) in the private ARM/MFW VM.

Factory sensor rate is modeled at 60 fps. Covers: old-card default (no
--fps-limit) capped to 30 at Create, loader rejection of values outside the
30/45/60 enum, 30->45->60->30 switches through the stock ChangeUserInfo(0x39)
safe switch point while saved_sensor_fps keeps the factory value, preview-off
restore, factory rates below the cap kept, stock-side rate changes releasing
ownership, and capture-in-flight deferral. Physical camera/SD stay named
fixture boundaries.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time

import arm_vm
import probe_ipcs_session_vm as session
import probe_smartfilter_vm as boundary
import probe_smartfilter_ram_vm as ram

PREAMBLE = ram.PROBE[:ram.PROBE.index('request original-mode 2')].replace(
    '/mnt/mmc/bin/smartfilter_fixture.arm > /tmp/smartfilter-ram.stdout',
    'NX_SFR_FIXTURE_FACTORY_FPS=60 /mnt/mmc/bin/smartfilter_fixture.arm > /tmp/smartfilter-ram.stdout')

APPLY_IDENTITY = 'apply --s2 --session 0 --calltrace 0 --table /mnt/mmc/identity.d4lut --look identity --strength 100'
APPLY_MONO = 'apply --s2 --session 0 --calltrace 0 --table /mnt/mmc/monochrome.d4lut --look monochrome --strength 100'

PROBE = PREAMBLE + r'''
# A1: old-card default. No --fps-limit: config keeps 0, the blob caps the
# factory 60 to 30 at Create and remembers 60 as the factory value.
operation fps-default '''+APPLY_IDENTITY+r'''
request fps-no-forced-refresh 124
request fps-default-insert 121
operation fps-default-status status
request fps-default-state 150
# A2: values outside the enum are loader usage errors before any attach.
set +e
"$LOADER" '''+APPLY_MONO+r''' --fps-limit 50 --pid "$pid" --blob "$BLOB" --fixture > fps-invalid50.json 2> fps-invalid50.stderr
fps_invalid50=$?
"$LOADER" '''+APPLY_MONO+r''' --fps-limit 0 --pid "$pid" --blob "$BLOB" --fixture > fps-invalid0.json 2> fps-invalid0.stderr
fps_invalid0=$?
set -e
[ "$fps_invalid50" = 64 ] && [ "$fps_invalid0" = 64 ]
printf 'fps_invalid50=%s fps_invalid0=%s\n' "$fps_invalid50" "$fps_invalid0" > fps-invalid.rc
request fps-invalid-state 150
# A3: 30 -> 45. The control-stage config poke is the only change; the rate
# itself moves through the queued stock 0x39 rebuild's Create.
operation fps-45 '''+APPLY_MONO+r''' --fps-limit 45
request fps-45-converge 120
request fps-45-state 150
# A4: 45 -> 60. Factory value must still be 60, never the previous cap.
operation fps-60 '''+APPLY_MONO+r''' --fps-limit 60
request fps-60-converge 120
request fps-60-state 150
# A5: 60 -> 30. saved_sensor_fps must still hold the factory 60 here; a cap
# is never mistaken for the stock rate.
operation fps-30 '''+APPLY_MONO+r''' --fps-limit 30
request fps-30-converge 120
request fps-30-state 150
# A6: same-value confirm: new generation, but no extra topology rebuild.
request fps-again-before 150
operation fps-30-again '''+APPLY_MONO+r''' --fps-limit 30
request fps-again-converge 120
request fps-again-state 150
# A7: preview off restores the pre-preview factory rate.
operation fps-off restore
request fps-off-remove 120
request fps-off-state 150
# A8: factory rate below the cap is kept, and nothing is remembered.
request fps-low-set 151
operation fps-low '''+APPLY_IDENTITY+r''' --fps-limit 60
request fps-low-insert 121
request fps-low-state 150
operation fps-low-stop restore
request fps-low-remove 120
request fps-low-final 150
# A9: stock raises the rate while off, then the cap applies on enable.
request fps-takeover-set 152
operation fps-takeover '''+APPLY_IDENTITY+r''' --fps-limit 30
request fps-takeover-insert 121
request fps-takeover-state 150
# A10: stock raises the rate mid-preview. Real stock rate changes carry their
# own topology rebuild; the re-cap lands at that safe switch point on the next
# control event: Create releases the stale ownership and re-caps from the
# factory value it kept all along.
request fps-stock-change 152
operation fps-recap '''+APPLY_MONO+r''' --fps-limit 30
request fps-recap-converge 120
request fps-recap-state 150
# A11: stock drops the rate mid-preview below the cap: the same Create path
# releases ownership and the factory's own lower value is kept.
request fps-stock-drop 151
operation fps-recap2 '''+APPLY_MONO+r''' --fps-limit 30
request fps-recap2-converge 120
request fps-recap2-state 150
# A12: capture in flight defers a mid-run fps change (command 121 itself
# asserts no 0x39 request while captures is set), and a factory-owned rate
# below the new cap is kept until the factory raises it again.
operation fps-midrun '''+APPLY_MONO+r''' --fps-limit 45
request fps-midrun-gate 121
request fps-midrun-state 150
# A13: factory raises again above the cap: the next control's Create re-caps
# from the factory value.
request fps-stock-raise 152
operation fps-final45 '''+APPLY_MONO+r''' --fps-limit 45
request fps-final45-converge 120
request fps-final45-state 150
operation fps-final-stop restore
request fps-final-remove 120
request fps-final-state 150
request fps-refs 105
# B: the real control script reads the card fps preference at the control
# stage and the loader carries it into the resident session config. Same
# staged runtime layout as the camera card, fixture mode, verified launcher.
RUNTIME=/tmp/nx300m-fps-runtime
FPS_STATE=/tmp/nx300m-mfw-state-fps
mkdir -p "$RUNTIME/bin" "$RUNTIME/lib" "$RUNTIME/preview-tests" "$FPS_STATE" /perf/.state /perf/looks/monochrome
fps_source_id=$(stat -c '%d:%i' /perf)
export NX300M_MOD_SOURCE_ROOT=/perf NX300M_MOD_SOURCE_ID="$fps_source_id" NX_SF_PROBE_FIXTURE=1 NX_STATE_DIR="$FPS_STATE"
cp /mnt/mmc/monochrome.d4lut /perf/looks/monochrome/table.d4lut
for pair in /mnt/mmc/bin/smartfilter_probe_launcher.arm:bin/smartfilter_probe_launcher.arm /mnt/mmc/bin/smartfilter_loader.arm:bin/smartfilter_loader.arm /mnt/mmc/bin/ram_asset_verify.arm:bin/ram_asset_verify.arm /mnt/mmc/smartfilter_ram.bin:lib/smartfilter_ram.bin /mnt/mmc/monochrome.d4lut:preview-tests/monochrome.d4lut /mnt/mmc/smartfilter-probe-collector.sh:smartfilter-probe-collector.sh; do
    cp "${pair%%:*}" "$RUNTIME/${pair#*:}"
done
cp /mnt/mmc/smartfilter-live-preview.sh "$RUNTIME/smartfilter-live-preview.sh"
chmod 500 "$RUNTIME/"*.sh "$RUNTIME/bin/"*.arm
chmod 400 "$RUNTIME/lib/smartfilter_ram.bin"
script_wrap() {
    label=$1
    rc=0
    NX_STATE_DIR="$FPS_STATE" /bin/sh "$RUNTIME/smartfilter-live-preview.sh" test-monochrome > "$label.stdout" 2> "$label.stderr" || rc=$?
    printf '%s\n' "$rc" > "$label.exit"
    [ "$rc" = 0 ]
}
# B1: missing preference (old card) caps at 30 through the real chain.
script_wrap fps-script-default
operation fps-script-default-status status
grep -q '"fps_limit":30' fps-script-default-status.json
# B2: saved 45 rides the control event into the resident config.
printf '45\n' > /perf/.state/lut-fps
script_wrap fps-script-45
operation fps-script-45-status status
grep -q '"fps_limit":45' fps-script-45-status.json
# B3: a value outside the enum falls back to 30.
printf '90\n' > /perf/.state/lut-fps
script_wrap fps-script-fallback
operation fps-script-fallback-status status
grep -q '"fps_limit":30' fps-script-fallback-status.json
# B4: a symbolic link is a hard reject like every other state file.
rm -f /perf/.state/lut-fps
ln -s /perf/looks /perf/.state/lut-fps
fps_link_rc=0
NX_STATE_DIR="$FPS_STATE" /bin/sh "$RUNTIME/smartfilter-live-preview.sh" test-monochrome > fps-script-symlink.stdout 2> fps-script-symlink.stderr || fps_link_rc=$?
printf '%s\n' "$fps_link_rc" > fps-script-symlink.exit
[ "$fps_link_rc" != 0 ]
grep -q 'symbolic link' fps-script-symlink.stderr
# B5: the preference grammar is the settings-UI grammar (nxui_feature_pages.h):
# the whole file is exactly one enum value and a single LF. Every malformed
# byte sequence falls back to 30 through the same chain.
rm -f /perf/.state/lut-fps
printf '45xyz\n' > /perf/.state/lut-fps
script_wrap fps-script-45xyz
operation fps-script-45xyz-status status
grep -q '"fps_limit":30' fps-script-45xyz-status.json
printf ' 45\n' > /perf/.state/lut-fps
script_wrap fps-script-space45
operation fps-script-space45-status status
grep -q '"fps_limit":30' fps-script-space45-status.json
printf '45' > /perf/.state/lut-fps
script_wrap fps-script-nolf45
operation fps-script-nolf45-status status
grep -q '"fps_limit":30' fps-script-nolf45-status.json
printf '45\n60\n' > /perf/.state/lut-fps
script_wrap fps-script-multiline
operation fps-script-multiline-status status
grep -q '"fps_limit":30' fps-script-multiline-status.json
printf '45\n\n' > /perf/.state/lut-fps
script_wrap fps-script-twolf
operation fps-script-twolf-status status
grep -q '"fps_limit":30' fps-script-twolf-status.json
rm -f /perf/.state/lut-fps
cat /proc/$pid/maps > after.maps
seq=$((seq+1)); printf '%s 0\n' "$seq" >/tmp/smartfilter-ram.request
wait "$pid"; pid=
echo PREVIEW_FPS_PROBE_DONE
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    out = args.output_dir.resolve()
    if (out / 'report.json').exists():
        raise RuntimeError('Preserve recorded evidence; use a new output directory')
    session.OUT = boundary.OUT = out
    boundary.SOCKET = '/tmp/nx300m-preview-fps-private'
    boundary.MARKER = 'PREVIEW_FPS_PRIVATE_VM_READY'
    boundary.PROBE = PROBE
    boundary.call = session.call
    boundary.configure()
    session.prepare()
    report = dict(passed=False, physical_camera_verified=False,
                  scope='Actual ARM hooks, stock message dispatcher/memory matcher; hardware stream/ISP doubles')
    root = Path(__file__).resolve().parents[1]
    report['source_identity'] = {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                                 for name in ('runtime/smartfilter_ram.c', 'runtime/smartfilter_ram.h',
                                              'runtime/smartfilter_ram_fixture.c', 'runtime/smartfilter_loader.c',
                                              'mod/deployment/mfw/smartfilter-live-preview.sh',
                                              'mod/metol/nxui_feature_pages.h')}
    started = False
    try:
        boundary.call('start'); started = True
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            if (out / 'serial.log').exists() and boundary.MARKER in (out / 'serial.log').read_text(errors='replace'):
                break
            time.sleep(.5)
        else:
            raise TimeoutError('Preview fps private ARM VM startup')
        execution = boundary.call('exec', '/bin/sh /arm-vm/mfw-probe.sh')
        arm_vm.write_json(out / 'execution.json', execution)
        guest = boundary.collect()
        if execution['exit_code'] or 'PREVIEW_FPS_PROBE_DONE' not in execution['output']:
            raise RuntimeError('Preview fps fixture failed; see execution.json and guest evidence')
        rows = [json.loads(line) for line in (guest / 'smartfilter-ram.stdout').read_text(encoding='utf-8').splitlines()
                if line.startswith('{')]
        fps_rows = [row for row in rows if row['type'] == 'fixture_fps']
        no_forced = [row for row in rows if row['type'] == 'fixture_loader_no_forced_refresh']
        refs = next(row for row in rows if row['type'] == 'fixture_refs')
        # fixture_fps rows appear in the probe script's dump order; one entry
        # per labeled request A1..A13.
        order = dict(default=0, invalid=1, fps45=2, fps60=3, fps30=4, again_before=5, again=6,
                     off=7, low=8, low_final=9, takeover=10, recap=11, recap2=12,
                     midrun=13, final45=14, final=15)
        checks = dict(
            apply_alone_queues_nothing=bool(no_forced) and no_forced[0].get('passed') is True,
            invalid_values_rejected=(guest / 'fps-invalid.rc').read_text().split() == ['fps_invalid50=64', 'fps_invalid0=64'],
            refs_released=not refs['held'] and refs['opens'] == refs['closes'],
        )
        def pick(name):
            return fps_rows[order[name]]
        d = pick('default')
        checks['default_caps_to_30_saved_factory'] = (d['sensor'] == 30 and d['saved'] == 60 and
            d['active'] == 30 and d['limit'] == 0 and d['s2_path'] == 1 and d['enabled'] == 1)
        inv = pick('invalid')
        checks['invalid_leaves_config_untouched'] = (inv['generation'] == d['generation'] and
            inv['limit'] == 0 and inv['sensor'] == 30 and inv['look'] == 'identity')
        r45 = pick('fps45')
        checks['raise_45_via_safe_switch'] = (r45['sensor'] == 45 and r45['saved'] == 60 and
            r45['active'] == 45 and r45['limit'] == 45 and r45['look'] == 'monochrome' and
            r45['rebuilds'] > d['rebuilds'] and r45['generation'] > d['generation'])
        r60 = pick('fps60')
        checks['raise_60_keeps_factory_saved'] = (r60['sensor'] == 60 and r60['saved'] == 60 and
            r60['active'] == 60 and r60['limit'] == 60)
        r30 = pick('fps30')
        checks['cap_30_not_mistaken_for_factory'] = (r30['sensor'] == 30 and r30['saved'] == 60 and
            r30['active'] == 30 and r30['limit'] == 30)
        again_before, again = pick('again_before'), pick('again')
        checks['same_value_confirms_without_rebuild'] = (again['rebuilds'] == again_before['rebuilds'] and
            again['generation'] > again_before['generation'] and again['sensor'] == 30 and again['saved'] == 60)
        off = pick('off')
        checks['off_restores_pre_preview_factory'] = (off['sensor'] == 60 and off['saved'] == 0 and
            off['active'] == 60 and off['s2_path'] == 0 and off['enabled'] == 0 and off['strength'] == 0)
        low = pick('low')
        checks['factory_below_cap_kept'] = (low['sensor'] == 24 and low['saved'] == 0 and
            low['active'] == 24 and low['limit'] == 60 and low['s2_path'] == 1)
        low_final = pick('low_final')
        checks['factory_low_off_stays_factory'] = (low_final['sensor'] == 24 and low_final['saved'] == 0 and
            low_final['s2_path'] == 0)
        takeover = pick('takeover')
        checks['enable_after_stock_raise_caps'] = (takeover['sensor'] == 30 and takeover['saved'] == 60 and
            takeover['active'] == 30 and takeover['limit'] == 30)
        recap = pick('recap')
        checks['stock_raise_recapped_at_create'] = (recap['sensor'] == 30 and recap['saved'] == 60 and
            recap['active'] == 30 and recap['limit'] == 30 and recap['s2_path'] == 1 and
            recap['rebuilds'] > takeover['rebuilds'] and recap['generation'] > takeover['generation'])
        recap2 = pick('recap2')
        checks['stock_drop_releases_ownership'] = (recap2['sensor'] == 24 and recap2['saved'] == 0 and
            recap2['active'] == 24 and recap2['rebuilds'] > recap['rebuilds'])
        midrun = pick('midrun')
        checks['capture_gate_then_factory_value_kept'] = (midrun['sensor'] == 24 and midrun['saved'] == 0 and
            midrun['active'] == 24 and midrun['limit'] == 45 and midrun['s2_path'] == 1)
        final45 = pick('final45')
        checks['factory_raise_recaps_from_factory'] = (final45['sensor'] == 45 and final45['saved'] == 60 and
            final45['active'] == 45 and final45['limit'] == 45)
        final = pick('final')
        checks['final_off_restores_factory'] = (final['sensor'] == 60 and final['saved'] == 0 and
            final['active'] == 60 and final['s2_path'] == 0 and final['enabled'] == 0)
        # B: real control-script chain (fixture mode) carries the card
        # preference through the loader into the resident session config.
        def status_json(name):
            return json.loads((guest / (name + '-status.json')).read_text(encoding='utf-8'))
        script_default = status_json('fps-script-default')
        script_45 = status_json('fps-script-45')
        script_fallback = status_json('fps-script-fallback')
        checks['script_chain_missing_pref_caps_30'] = (script_default['fps_limit'] == 30 and
            script_default['installed'] and script_default['look_id'] == 'monochrome')
        checks['script_chain_pref_45_reaches_config'] = (script_45['fps_limit'] == 45 and
            script_45['requested_generation'] > script_default['requested_generation'])
        checks['script_chain_out_of_enum_falls_back_30'] = (script_fallback['fps_limit'] == 30 and
            script_fallback['requested_generation'] > script_45['requested_generation'])
        checks['script_chain_symlinked_pref_rejected'] = (int((guest / 'fps-script-symlink.exit').read_text()) != 0 and
            'symbolic link' in (guest / 'fps-script-symlink.stderr').read_text(encoding='utf-8'))
        # B5: the whole-file grammar — every malformed byte sequence falls
        # back to 30 exactly like the settings UI reads it.
        for label in ('fps-script-45xyz', 'fps-script-space45', 'fps-script-nolf45',
                      'fps-script-multiline', 'fps-script-twolf'):
            checks['script_chain_malformed_pref_' + label.removeprefix('fps-script-')] = (
                status_json(label)['fps_limit'] == 30)
        report['observed'] = {name: pick(name) for name in order}
        report['checks'] = checks
        report['passed'] = all(checks.values())
        if not report['passed']:
            raise AssertionError(checks)
    finally:
        if started:
            report['stop'] = boundary.call('stop')
        arm_vm.write_json(out / 'report.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
