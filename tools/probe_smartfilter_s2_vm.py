"""S2 seven-GOT RAM installation, original worker and retained-route soft stop.

Path probes execute original Create/B2Y/LDC prefixes through the whitelisted BL
and stop at the next environmental call. They do not emulate a full hardware
path creation, RAW sensor data, DMA or physical workspace/cache requirements.
"""
import base64
import json
import re
import subprocess
import arm_vm

import probe_smartfilter_ram_vm as ram
import probe_smartfilter_vm as boundary

OUT = ram.BUILD/'s2-vm'
PROBE = ram.PROBE[:ram.PROBE.index('request original-mode 2')]+r'''
mkdir -p /perf
request original-mode 2
operation baseline status
set +e
"$LOADER" apply --pid "$pid" --blob "$BLOB" --fixture --s2 --fixture-fail-slot 6 --table /mnt/mmc/identity.d4lut --look identity > rollback.stdout 2> rollback.stderr
rollback=$?
set -e
[ "$rollback" = 3 ]
operation rolled-back status
operation identity apply --table /mnt/mmc/identity.d4lut --look identity --strength 100
request inject-initialization-failure 23
request refused-paths 13
operation admission-refused status
request preinitialization-fallback 10
request clear-initialization-failure 24
request armed-paths 13
request outside-mode 14
operation timing-disabled stock --timing 0
request timing-disabled-frame 10
operation timing-disabled-status status
operation timing-stock stock --timing 1
request timing-stock-first 10
request timing-stock-steady 10
operation timing-stock-export diag-export
operation timing-identity apply --table /mnt/mmc/identity.d4lut --look identity --strength 100
request identity 10
request timing-identity-steady 10
operation timing-identity-export diag-export --diag-log /perf/fn-key.log
operation timing-identity-export-repeat diag-export --diag-log /perf/fn-key.log
cp /var/run/nx300m-sf-diag-*.ndjson /arm-vm/mfw-results/
request work-retry 19
operation identity-applied status
request still-mode 2
operation monochrome apply --table /mnt/mmc/monochrome.d4lut --look monochrome --strength 100
request monochrome 10
request timing-monochrome-steady 10
operation timing-monochrome-export diag-export
cp /var/run/nx300m-sf-diag-*.ndjson /arm-vm/mfw-results/
operation monochrome-applied status
operation timing-capture apply --table /mnt/mmc/identity.d4lut --look capture --strength 100
request timing-capture-cycle 6
operation timing-capture-export diag-export
operation timing-pending apply --table /mnt/mmc/identity.d4lut --look pending --strength 100
request timing-pending-frame 27
operation timing-pending-export diag-export
request timing-release-frame 28
operation timing-pending-export-repeat diag-export --diag-log /perf/fn-key.log
cp /perf/fn-key.log /tmp/timing-before-corruption.log
for dump in /var/run/nx300m-sf-diag-*.ndjson; do
    if grep -q '"look_id":"pending"' "$dump"; then
        cp "$dump" /tmp/timing-clean.ndjson
        printf 'corrupt\n' >> "$dump"
        set +e
        "$LOADER" diag-export --pid "$pid" --blob "$BLOB" --fixture --s2 --diag-log /perf/fn-key.log > timing-corrupt.stdout 2> timing-corrupt.stderr
        corrupt=$?
        set -e
        [ "$corrupt" = 3 ]
        cmp /perf/fn-key.log /tmp/timing-before-corruption.log
        cp /tmp/timing-clean.ndjson "$dump"
    fi
done
cp /var/run/nx300m-sf-diag-*.ndjson /arm-vm/mfw-results/
cp /perf/fn-key.log timing-persisted.log
operation half apply --table /mnt/mmc/monochrome.d4lut --look monochrome --strength 50
request half 10
request queued-before-stop 11
operation soft-stop restore
request queued-after-stop 12
request late-prepare 10
request cached-stock-work 25
operation before-stopped-paths status
request stopped-paths 13
operation stopped status
operation reapply apply --table /mnt/mmc/identity.d4lut --look identity --strength 100
request restarted-paths 13
request reapply 10
request initialization-fallback 15
request reset-injection 16
request profile-sync-fallback 26
request context-fallback 17
request origin-fallback 18
operation origin-exhausted status
request reset-injection-again 16
request real-filter-work 20
request capture-cycle 6
operation after-capture status
request busy 7
request operation-error 5
operation after-error status
request selection-inflight 8
operation selected-monochrome apply --table /mnt/mmc/monochrome.d4lut --look monochrome --strength 100
request selected-frame 9
operation final-restore restore
request final-paths 13
request final-frame 10
request protect-work 21
set +e
"$LOADER" status --pid "$pid" --blob "$BLOB" --fixture --s2 > rejected-mapping.stdout 2> rejected-mapping.stderr
rejected=$?
set -e
[ "$rejected" = 3 ]
request unprotect-work 22
operation final status
'''+ram.PROBE[ram.PROBE.index('cat /proc/$pid/maps > after.maps'):]
PROBE = PROBE.replace('--fixture > "$label.json"', '--fixture --s2 > "$label.json"')
PROBE = PROBE.replace('case $cmd in 1|5|7|9)', 'case $cmd in 1|5|7|9|10|12|15|17|18|19|25|26)')


def call(action,command=''):
    result=subprocess.run(['wsl.exe','-d','Debian','-u','root','-e','python3',
        arm_vm.wsl_path(__file__),'--linux',action,base64.b64encode(command.encode()).decode()],
        capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=375)
    if result.returncode: raise RuntimeError(result.stderr or result.stdout)
    return json.loads(result.stdout)


def prepare():
    ram.OUT = OUT
    ram.prepare()
    candidate = json.loads((OUT/'candidate.json').read_text())
    candidate.update(actual_plt_hooks=7, profile='S2',
        path_probe_scope='stock prefixes through mode getter; next environment call aborts probe',
        raw_sensor_or_full_path_construction=False)
    (OUT/'candidate.json').write_text(json.dumps(candidate, indent=2)+'\n')


def verify(guest):
    import numpy as np
    def record(name): return json.loads((guest/(name+'.json')).read_text())
    def response(name): return list(map(int,(guest/(name+'.response')).read_text().split()))
    def frame(name): return np.frombuffer((guest/(name+'.yuv')).read_bytes(),dtype='uint8').astype(int)
    original=np.frombuffer((OUT/'source-files/input.yuv').read_bytes(),dtype='uint8').astype(int)
    identity,mono,half=frame('identity'),frame('monochrome'),frame('half')
    assert np.abs(original-identity).max()<=1
    assert np.array_equal(identity,frame('reapply')) and np.array_equal(identity,frame('work-retry'))
    assert np.all(mono[64*48:]==128) and np.abs(mono[:64*48]-original[:64*48]).max()<=1
    assert np.abs((half[64*48:]-128)-(original[64*48:]-128)*.5).max()<=2
    for name in ('identity','work-retry','monochrome','half','reapply','selected-frame'):
        assert response(name)[1:]==[1,1],name
    for name in ('queued-after-stop','late-prepare','cached-stock-work','profile-sync-fallback',
                 'preinitialization-fallback','initialization-fallback','context-fallback',
                 'origin-fallback','busy','final-frame'):
        assert response(name)[1:]==[0,1],name
        assert np.array_equal(frame(name),original),name
    # A render error is an attempted hardware operation, followed by literal
    # passthrough and the original completion callback.
    assert response('operation-error')[1:]==[1,1]
    assert np.array_equal(frame('operation-error'),original)
    assert response('queued-before-stop')[1:]==[0,0]
    assert response('original-mode')[0]==response('still-mode')[0]==2
    applied=record('identity-applied')
    assert applied['profile']==1 and applied['abi']==4 and applied['s2_frames']>0
    assert applied['applied_generation']==applied['requested_generation']
    assert all(v>0 for v in applied['mode_forged'])
    assert applied['work_managed']>=3 and applied['work_size']==0x168000 and applied['work_base']%4096==0
    assert applied['hook_preview']['last']==1 and applied['hook_effect']['last']==1
    stopped=record('stopped'); before=record('before-stopped-paths')
    assert stopped['installed'] and not stopped['enabled'] and not stopped['insert_enabled']
    assert stopped['routing_retained'] and stopped['restart_required_for_full_restore']
    assert stopped['work_base']==applied['work_base'] and stopped['s2_path']==0
    assert stopped['mode_forged']==before['mode_forged']
    assert record('origin-exhausted')['s2_fault']==1
    capture=record('after-capture')
    assert capture['capture_waits']>0 and capture['backend_active']==capture['captures']==0
    assert capture['work_stock']>0
    assert record('after-error')['last_error']==-1
    final=record('final')
    assert final['installed'] and not final['enabled'] and final['routing_retained']
    assert final['work_base']==applied['work_base'] and final['s2_fallback']>=6
    assert final['backend_active']==final['captures']==final['inflight']==0
    assert not record('baseline')['installed']
    rollback=record('rolled-back')
    assert not rollback['installed'] and not rollback['enabled'] and rollback['requested_generation']==1
    assert rollback['work_base']==applied['work_base']
    refused=record('admission-refused')
    assert refused['initialized']==3 and refused['s2_path']==0 and refused['mode_forged']==[0,0,0,0]
    assert 'Resident module differs' in (guest/'rejected-mapping.stderr').read_text()
    from analyze_smartfilter_timing import parse_log, analyze, to_markdown
    dumps={}
    for path in guest.glob('nx300m-sf-diag-*.ndjson'):
        parsed=parse_log(path)
        assert parsed.ok, (path.name, parsed.errors, [d.errors for d in parsed.dumps.values()])
        meta=next(iter(parsed.dumps.values())).begin
        result=analyze(parsed)
        assert meta['measurement_source']=='vm'
        assert meta['clock_errors']==0 and meta['control_intervals_complete']
        if meta['look_id']=='pending':
            assert result['pending'] and any(r['current_stage_name']=='ycc' for r in result['pending'])
        else:
            assert result['frame_groups'], (path.name,result['excluded_records'])
        dumps[meta['look_id']]=result
    assert {'stock','identity','monochrome','pending','capture'} <= set(dumps)
    assert {'prepare','finish'} <= {g['key']['kind'] for g in dumps['capture']['lifecycle_groups']}
    assert any('drain' in g['metrics'] for g in dumps['capture']['lifecycle_groups'])
    corrupt=json.loads((guest/'timing-corrupt.stdout').read_text(encoding='utf-8'))
    assert corrupt['command']=='diag-export' and corrupt['status']=='export_failed'
    assert corrupt['snapshot_created'] is False and corrupt['persisted'] is False
    assert corrupt['error_stage']=='checksum' and corrupt['error_operation']=='validate'
    assert corrupt['error_reason']=='content_mismatch' and corrupt['error_errno']==0
    assert corrupt['error_errno_valid'] is False
    assert corrupt['write_attempted'] is False and corrupt['destination_may_have_changed'] is False
    assert corrupt['destination_bytes_verified'] is False
    corrupt_text=(guest/'timing-corrupt.stderr').read_text(encoding='utf-8')
    assert 'stage="checksum"' in corrupt_text and 'reason="content_mismatch"' in corrupt_text
    assert record('timing-disabled-status')['diag_frames']==0
    assert not record('timing-pending-export')['safe_to_persist']
    assert record('timing-pending-export-repeat')['reused'] and record('timing-pending-export-repeat')['persisted']
    persisted=parse_log(guest/'timing-persisted.log')
    assert persisted.ok and len(persisted.dumps)==2
    assert (guest/'timing-persisted.log').read_text().count('"type":"sf_diag_begin"')==2
    assert any(g['key']['path']=='passthrough' for g in dumps['stock']['frame_groups'])
    for look in ('identity','monochrome'):
        groups=[g for g in dumps[look]['frame_groups'] if g['key']['path']=='render']
        assert groups, (look,dumps[look])
        assert all({'ade_init','lut0','lut1','lut2','flush_in','ycc','flush_out','reset','iq_init','iq_process','close'} <= set(g['metrics']) for g in groups)
    assert record('timing-identity-export')['persisted']
    assert record('timing-identity-export-repeat')['reused'] and record('timing-identity-export-repeat')['persisted']
    (OUT/'timing-vm-analysis.json').write_text(json.dumps(dumps,indent=2)+'\n')
    (OUT/'timing-vm-analysis.md').write_text('\n\n'.join(to_markdown(r) for r in dumps.values()),encoding='utf-8')
    windows={p.stem:[int(v) for v in re.findall(r'Target stop window: (\d+) ms;',p.read_text())]
             for p in guest.glob('*.stderr') if 'Target stop window:' in p.read_text()}
    return dict(passed=True,actual_ram_loader=True,actual_plt_hooks=7,abi=4,profile='S2',
        checks=dict(seven_slot_install_failure_rolls_back=True,invalid_work_mapping_rejected=True,
            failed_initialization_does_not_insert=True,preinitialization_passthrough=True,
            production_mode_1_and_ade_1=True,managed_work_prepare_and_execute_retry=True,
            identity_monochrome_and_strength=True,exact_path_prefix_calls=True,outside_mode_unchanged=True,
            capture_mode_unchanged=True,queued_frame_completes_after_soft_stop=True,
            late_prepare_after_soft_stop=True,stopped_create_and_path_decisions=True,
            initialization_failure_passthrough=True,profile_sync_failure_passthrough=True,
            synthetic_scope_covers_cached_stock_work=True,context_exhaustion_passthrough=True,
            origin_exhaustion_passthrough=True,real_filter_uses_stock_work=True,
            capture_priority=True,busy_and_render_error_passthrough=True,selection_drains_inflight=True,
            work_and_hooks_retained_until_process_exit=True,
            timing_three_groups_export=True,timing_restore_stages_present=True,
            timing_frozen_repeat_export=True,timing_pending_ycc_export=True,
            timing_disabled_preserves_frames=True,timing_persistence_readback_and_idempotence=True,
            timing_capture_wait_and_finish=True,timing_corruption_refuses_log_append=True),
        target_stop_windows_ms=windows,
        reference_pixels=dict(identity_max_ycc_error_8bit=int(np.abs(original-identity).max()),monochrome_chroma=128),
        physical_camera_or_card_accessed=False,hardware_isp_pixels_verified=False,
        raw_sensor_or_full_path_construction=False,managed_work_dma_contract_verified=False,
        raw_menu_restrictions_resolved=False)


if __name__=='__main__':
    boundary.OUT=OUT
    boundary.SOCKET='/tmp/nx300m-smartfilter-s2-private'
    boundary.MARKER='SMARTFILTER_S2_PRIVATE_VM_READY'
    boundary.PROBE=PROBE
    boundary.prepare=prepare
    boundary.verify=verify
    boundary.call=call
    boundary.main()
