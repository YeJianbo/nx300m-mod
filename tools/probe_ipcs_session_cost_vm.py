"""Measure session CPU/syscall overhead in a private ARM/stock-library VM.

Physical IPCS, power, DMA and pixels are not timed. Production runtime sources
are unchanged; this measures the actual session C code and stock UDD refcounts.
"""
import argparse
import base64
import hashlib
import io
import json
from pathlib import Path
import shlex
import subprocess
import time

import arm_vm
import probe_lut_live_loader_vm as live
import probe_mfw_ram_vm as vm

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'outputs/performance/ipcs-session-cost/arm-vm'
BUILD = ROOT/'outputs/deployment/smartfilter-session'
PROBE = r'''#!/bin/sh
set -eu
cd /arm-vm/mfw-results
sha256sum /usr/lib/libudd.so.3.1 /lib/libpthread-2.13.so > libraries.sha256
/mnt/mmc/bin/ipcs_calltrace_fixture.arm > trace.ndjson 2> trace.stderr
/mnt/mmc/bin/ipcs_calltrace_fixture.arm --cost > udd-cost.ndjson 2> udd-cost.stderr
/mnt/mmc/bin/ipcs_session_cost_fixture.arm > session-cost.ndjson 2> session-cost.stderr
/mnt/mmc/bin/ipcs_session_failure_fixture.arm > failures.ndjson 2> failures.stderr
echo IPCS_SESSION_COST_PASSED
'''


def configure():
    vm.OUT = OUT
    vm.SOCKET = '/tmp/nx300m-session-cost-private'
    vm.INIT = live.INIT
    vm.PROBE = PROBE


def call(action, command=''):
    r = subprocess.run(['wsl.exe','-d','Debian','-u','root','-e','python3',
        arm_vm.wsl_path(__file__), '--output-dir',arm_vm.wsl_path(OUT),
        '--linux',action,base64.b64encode(command.encode()).decode()],
        capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=120)
    if r.returncode:
        raise RuntimeError(r.stderr or r.stdout)
    return json.loads(r.stdout)


def prepare():
    from elftools.elf.elffile import ELFFile
    OUT.mkdir(parents=True, exist_ok=True)
    rf = Path(json.loads((ROOT/'project.local.json').read_text(encoding='utf-8-sig'))['rootfs'])
    elf = ELFFile(io.BytesIO((rf/'usr/lib/libudd.so.3.1').read_bytes()))
    rels = elf.get_section_by_name('.rel.plt'); dyn = elf.get_section(rels['sh_link'])
    slots = {dyn.get_symbol(r['r_info_sym']).name:r['r_offset'] for r in rels.iter_relocations()}
    (OUT/'ipcs_trace_fixture_stock.h').write_text(
        f'#define FIXTURE_CLOSE_REG_SLOT {slots["ipcs_close_reg_base"]}U\n'
        f'#define FIXTURE_CLOSE_IRQ_SLOT {slots["ipcs_close_irq"]}U\n', encoding='ascii')
    (OUT/'smartfilter_stock.h').write_bytes((BUILD/'smartfilter_stock.h').read_bytes())
    stage = OUT/'source-files/bin'; stage.mkdir(parents=True, exist_ok=True)
    script = '''set -eu
tc=/opt/nx300m-toolchain/usr
rf=/opt/nx300m-lab/stock-1.15
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
'''
    for name, extra in (('ipcs_calltrace_fixture','ipcs_calltrace'),
                         ('ipcs_session_cost_fixture','ipcs_session'),
                         ('ipcs_session_failure_fixture','ipcs_session')):
        script += f'''
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \
 -std=gnu99 -O2 -Wall -Wextra -Werror -Wno-unused-function -Wno-unused-const-variable \
 -march=armv7-a -mfloat-abi=soft -DNX_SFR_SESSION=1 -D_TIME_BITS=32 -D_FILE_OFFSET_BITS=64 \
 -fno-pic -no-pie -fno-stack-protector -nostdlib -nostartfiles -Wl,-e,_start \
 -Wl,--dynamic-linker=/lib/ld-linux.so.3 -Wl,-z,defs -Wl,-rpath-link,"$rf/lib" \
 -I{shlex.quote(arm_vm.wsl_path(OUT))} \
 {shlex.quote(arm_vm.wsl_path(ROOT/'runtime'/(name+'.c')))} \
 {shlex.quote(arm_vm.wsl_path(ROOT/'runtime'/(extra+'.c')))} \
 {shlex.quote(arm_vm.wsl_path(ROOT/'work/reference/metol/test_server/filmlab/src/crt0_arm.S'))} \
 "$rf/lib/libpthread.so.0" "$rf/lib/libc-2.13.so" "$rf/lib/libdl.so.2" -lgcc \
 -o {shlex.quote(arm_vm.wsl_path(stage/(name+'.arm')))}
'''
    r = subprocess.run(['wsl.exe','-d','Debian','-u','root','-e','sh','-s'],
                       input=script.encode(),capture_output=True)
    (OUT/'build.stderr').write_bytes(r.stderr)
    if r.returncode:
        raise RuntimeError(f'ARM fixture build exit {r.returncode}: '+r.stderr.decode(errors='replace'))
    inventory = {p.relative_to(OUT/'source-files').as_posix():p.stat().st_size
                 for p in (OUT/'source-files').rglob('*') if p.is_file()}
    arm_vm.write_json(OUT/'candidate.json',dict(inventory=inventory,
        runtime_sha256={name:hashlib.sha256((ROOT/'runtime'/name).read_bytes()).hexdigest()
                        for name in ('ipcs_session.c','smartfilter_session.inc','smartfilter_ipcs.c','ipcs_calltrace.c')}))
    vm.initramfs()


def analyze():
    import numpy as np
    guest = OUT/'guest/mfw-results'
    execution = json.loads((OUT/'execution.json').read_text(encoding='utf-8'))
    if (type(execution.get('exit_code')) is not int or execution['exit_code'] != 0
            or 'IPCS_SESSION_COST_PASSED' not in execution.get('output', '').splitlines()):
        raise ValueError('Session cost guest did not complete successfully')
    rows = lambda name:[json.loads(s) for s in (guest/name).read_text().splitlines() if s.startswith('{')]
    modes = {'native-existing', 'wrapped-off', 'wrapped-on', 'ready-admission-pair'}
    cost = []
    for name, kind, allowed in (
            ('udd-cost.ndjson', 'udd-cost', modes-{'ready-admission-pair'}),
            ('session-cost.ndjson', 'session-cost', {'ready-admission-pair'})):
        for row in rows(name):
            if 'check' not in row and (row.get('mode') not in allowed or row.get('type') != kind):
                raise ValueError(f'Unexpected measurement record in {name}: {row}')
            cost.append(row)
    if {r['mode'] for r in cost if 'mode' in r} != modes:
        raise ValueError('Session cost measurements must include all four modes')
    stats = {}
    for mode in sorted(modes):
        samples = [r for r in cost if r.get('mode')==mode]
        if (len(samples) != 31 or any(type(r.get('sample')) is not int for r in samples)
                or {r['sample'] for r in samples} != set(range(31))):
            raise ValueError(f'{mode}: expected 31 unique samples numbered 0-30')
        fields = ('wall_ns', 'cpu_ns', 'process_cpu_ns') if mode=='ready-admission-pair' else ('wall_ns', 'cpu_ns')
        for row in samples:
            if (type(row.get('calls')) is not int or row['calls'] != 64
                    or any(type(row.get(k)) is not int or row[k] <= 0 for k in fields)):
                raise ValueError(f'{mode}: invalid call count or timing: {row}')
        stats[mode] = {field.replace('_ns','_us'):dict(zip(('p50','p95','p99'),
            np.percentile([r[field]/r['calls']/1e3 for r in samples],(50,95,99)).tolist()))
            for field in fields}
    checks = [r for r in cost if 'check' in r]+rows('failures.ndjson')
    expected_checks = {
        'real_udd_existing_reference_cost', 'session_stress',
        'open_failure_no_retry', 'close_failure_no_retry',
        'capture_drain_and_nested_admission', 'render_fault_releases_healthy_reference',
        'control_drain_before_snapshot', 'stream_stop_and_resume',
        'stream_drain_timeout_keeps_admission_closed',
    }
    if (len(checks) != len(expected_checks) or {r.get('check') for r in checks} != expected_checks
            or any(r.get('passed') is not True for r in checks)):
        raise ValueError(f'Incomplete, duplicate or failed session checks: {checks}')
    summaries = {r['check']:r for r in checks}
    udd = summaries['real_udd_existing_reference_cost']
    stress = summaries['session_stress']
    if any(udd.get(k) != v for k,v in dict(calls=5952, device_opens=1, remaining_references=0).items()):
        raise ValueError(f'Unbalanced UDD references or incomplete measurement: {udd}')
    if (any(stress.get(k) != v for k,v in dict(cycles=96, frames=3618, opens=98, closes=98, held=0).items())
            or stress.get('idle_releases', 0) < 1 or stress.get('owner_tid', 0) <= 0
            or stress.get('render_tid', 0) <= 0 or stress['owner_tid'] == stress['render_tid']):
        raise ValueError(f'Incomplete session stress or reference ownership failure: {stress}')
    from analyze_ipcs_calls import analyze as trace_analyze
    trace = trace_analyze(rows('trace.ndjson'))
    if [r['udd_branch'] for r in trace['calls']] != [
            'first_process_reference','existing_process_reference','first_process_reference']:
        raise ValueError(f'Unexpected stock UDD reference transitions: {trace}')
    return dict(passed=True, scope='ARM VM CPU/control calls; physical device, Reset/IQ, pixels, JPEG and SD excluded',
                statistics_us_per_pair_of_open_close_or_admission=stats,
                statistics_unit='microseconds per pair; 31 batches of 64 calls',
                validated_samples=124, validated_modes=sorted(modes),
                checks=checks, stock_udd_first_existing_first_verified=True,
                production_session_reset_iq_unchanged=True, physical_performance_measured=False)


def main():
    global OUT
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir',type=Path,default=OUT)
    p.add_argument('--linux',choices=('start','exec','stop'))
    p.add_argument('--analyze-only',action='store_true')
    p.add_argument('payload',nargs='?',default='')
    args = p.parse_args(); OUT = args.output_dir.resolve(); configure()
    if args.linux:
        print(json.dumps(vm.linux_worker(args.linux,args.payload))); return
    if args.analyze_only:
        report = json.loads((OUT/'report.json').read_text())
        try:
            report.update(analyze()); report.pop('error',None)
        except Exception as error:
            report.update(passed=False, error=str(error))
            for key in ('statistics_us_per_pair_of_open_close_or_admission',
                        'validated_samples', 'validated_modes'):
                report.pop(key,None)
            raise
        finally:
            arm_vm.write_json(OUT/'report.json',report)
        print(json.dumps(report,ensure_ascii=False,indent=2)); return
    if (OUT/'report.json').exists():
        raise RuntimeError('Preserve the existing measurement; use a new output directory')
    prepare(); report = dict(passed=False); started = False
    try:
        call('start'); started = True
        deadline = time.monotonic()+180
        while time.monotonic()<deadline:
            if 'LUT_LIVE_PRIVATE_VM_SHELL_READY' in (OUT/'serial.log').read_text(errors='replace'):
                break
            time.sleep(.5)
        else:
            raise TimeoutError('Session cost VM boot')
        execution = call('exec','/bin/sh /arm-vm/mfw-probe.sh')
        arm_vm.write_json(OUT/'execution.json',execution)
        live.OUT = OUT
        # Reuse the evidence transfer framing with this VM's command channel.
        live.call = call; live.collect()
        if execution['exit_code'] or 'IPCS_SESSION_COST_PASSED' not in execution['output']:
            raise RuntimeError('Session cost guest exit '+str(execution['exit_code']))
        report.update(analyze())
    except Exception as error:
        report['error'] = str(error); raise
    finally:
        if started:
            report['stop'] = call('stop'); report['private_vm_stopped'] = 'return' in report['stop']
            if report['private_vm_stopped']:
                (OUT/'initramfs.cpio.gz').unlink(missing_ok=True)
        arm_vm.write_json(OUT/'report.json',report)
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
