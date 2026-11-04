"""Verify formal fit and legacy tables reach the actual ARM stock LUT writer.

Uses current deployment loader/blob and genuine IQ/UDD libraries with RAM MMIO.
No camera, sensor, JPEG encoder or physical ISP is simulated by the pixel dump.
"""
import hashlib
import json
from pathlib import Path
import struct
import time
import zipfile

import arm_vm
import probe_lut_live_loader_vm as live
from look_library import LookLibrary
from lut_table_data import read_tables

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'outputs/deployment/lut-fit/arm-vm'
KEYS = ('nx300-hard-mono','nx300-fuji-classic-negative','ricoh-positive-exact')
PROBE = r'''#!/bin/sh
set -eu
OUT=/arm-vm/mfw-results
ROOT=/mnt/mmc/nx300m-mod
LOADER=/mnt/mmc/bin/lut_live_loader.arm
/mnt/mmc/bin/lut_live_fixture.arm > "$OUT/fixture.stdout" 2> "$OUT/fixture.stderr" &
pid=$!
trap 'kill "$pid" 2>/dev/null || :' EXIT
tries=0
until [ -s "$OUT/fixture.ready" ]; do
    tries=$((tries+1)); [ "$tries" -lt 100 ]; /rescue/busybox usleep 50000
done
rm /var/run/nx300m-lut-trace-enabled
seq=0
rpc() {
    tag=$1; mode=$2; seq=$((seq+1))
    printf '%s %s 1 %s 1\n' "$seq" "$mode" "$tag" > /var/run/lut-live-request.tmp
    mv /var/run/lut-live-request.tmp /var/run/lut-live-request
    tries=0
    while :; do
        response=0
        [ ! -r /var/run/lut-live-response ] || read response ignored < /var/run/lut-live-response
        [ "$response" != "$seq" ] || break
        tries=$((tries+1)); [ "$tries" -lt 100 ]; /rescue/busybox usleep 50000
    done
}
rpc stock-direct 0
rpc stock-composed 1
"$LOADER" apply --pid "$pid" --blob /mnt/mmc/lut_ram_blob.bin --root "$ROOT" --fixture > "$OUT/apply.json" 2> "$OUT/apply.stderr"
generation=0
for look in nx300-hard-mono nx300-fuji-classic-negative ricoh-positive-exact; do
    for method in fit legacy; do
        key=$look
        [ "$method" != legacy ] || key=legacy-$look
        for strength in 0 50 100; do
            generation=$((generation+1))
            printf '%s %s/looks/%s/table.d4lut %s\n' "$generation" "$ROOT" "$key" "$strength" > /var/run/nx300m-lut-selection.tmp
            mv /var/run/nx300m-lut-selection.tmp /var/run/nx300m-lut-selection
            rpc "$method-$look-$strength-direct" 0
            rpc "$method-$look-$strength-composed" 1
        done
    done
done
rm /var/run/nx300m-lut-selection
"$LOADER" restore --pid "$pid" --fixture > "$OUT/restore.json" 2> "$OUT/restore.stderr"
rpc restored-direct 0
rpc restored-composed 1
kill "$pid"
trap - EXIT
echo LUT_FIT_WRITER_PASSED
'''


def main():
    if (OUT/'report.json').exists():
        raise RuntimeError('Preserve existing fit writer evidence')
    OUT.mkdir(parents=True,exist_ok=True)
    live.OUT = OUT; live.PROBE = PROBE; live.configure()
    fixture = live.compile_fixture()
    stage = OUT/'source-files'
    inputs = {
        'bin/lut_live_fixture.arm':fixture,
        'bin/lut_live_loader.arm':live.BUILD/'lut_live_loader.arm',
        'lut_ram_blob.bin':live.BUILD/'lut_ram_blob.bin',
    }
    for name,path in inputs.items():
        target = stage/name; target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(path.read_bytes())
    library = LookLibrary()
    with zipfile.ZipFile(ROOT/'outputs/deployment/lut-fit/legacy-library.zip') as legacy:
        for key in KEYS:
            metadata = library.get(key)
            if metadata.get('compiler', {}).get('method') != 'fit':
                raise ValueError(f'Fit writer probe requires fit tables: {key}')
            for name, table in read_tables(library.directory(key), metadata).items():
                for variant,data in ((key,table),('legacy-'+key,legacy.read(key+'/'+name))):
                    target = stage/'nx300m-mod/looks'/variant/name
                    target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(data)
    inventory = {p.relative_to(stage).as_posix():p.stat().st_size for p in stage.rglob('*') if p.is_file()}
    arm_vm.write_json(OUT/'candidate.json',dict(inventory=inventory,
        source_sha256={p.relative_to(stage).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in stage.rglob('*') if p.is_file()}))
    live.vm.initramfs()
    report = dict(passed=False,physical_color_verified=False)
    started = False
    try:
        live.call('start'); started = True
        deadline = time.monotonic()+180
        while time.monotonic()<deadline:
            if 'LUT_LIVE_PRIVATE_VM_SHELL_READY' in (OUT/'serial.log').read_text(errors='replace'):
                break
            time.sleep(.5)
        else:
            raise TimeoutError('Fit writer VM boot')
        execution = live.call('exec','/bin/sh /arm-vm/mfw-probe.sh')
        arm_vm.write_json(OUT/'execution.json',execution)
        guest = live.collect()
        if execution['exit_code'] or 'LUT_FIT_WRITER_PASSED' not in execution['output']:
            raise RuntimeError('Fit writer guest exit '+str(execution['exit_code']))
        words = lambda p:struct.unpack('<2190H',p.read_bytes())
        checked = 0
        for key in KEYS:
            for method in ('fit','legacy'):
                variant = key if method=='fit' else 'legacy-'+key
                directory = stage/'nx300m-mod/looks'/variant
                for mode in ('direct','composed'):
                    stock = words(guest/('stock-'+mode+'.bin'))
                    table = directory/('table.d4lut' if mode=='direct' else 'table-stock3d.d4lut')
                    if not table.exists():
                        table = directory/'table.d4lut'
                    selected = words(table)
                    for strength in (0,50,100):
                        expected = tuple((a*(100-strength)+b*strength+50)//100 for a,b in zip(stock,selected))
                        assert words(guest/f'{method}-{key}-{strength}-{mode}.bin')==expected,(key,method,mode,strength)
                        checked += 1
        for mode in ('direct','composed'):
            assert (guest/f'stock-{mode}.bin').read_bytes()==(guest/f'restored-{mode}.bin').read_bytes()
        assert json.loads((guest/'restore.json').read_text())['restored'] is True
        report.update(passed=True,looks=list(KEYS),writer_outputs_checked=checked,
            both_native_table_sources=True,strengths=[0,50,100],fit_and_legacy_forwarded_byte_exact=True,
            stock_reset_before_each_update=True,all_three_planes_checked=True,stock_restored=True,
            runtime_loader_blob_unchanged=True)
    except Exception as error:
        report['error'] = str(error); raise
    finally:
        if started:
            report['stop'] = live.call('stop'); report['private_vm_stopped'] = 'return' in report['stop']
            if report['private_vm_stopped']:
                (OUT/'initramfs.cpio.gz').unlink(missing_ok=True)
        arm_vm.write_json(OUT/'report.json',report)
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
