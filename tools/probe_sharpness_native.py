"""Verify original EdgeD4 consumers with scoped data-symbol interposition."""
import json
from pathlib import Path
import re
import shlex
import subprocess

from build_ycc_probe import wsl_path
from inspect_gamma_hook import Stock
from inspect_recipe_tables import read_symbol
from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'outputs/metol'


def static_bindings(stock):
    pic_base = (0x33dab4+8+stock.literal(0x33dab0)[1])&0xffffffff
    result = []
    for cls, expected_load in [('CIpcsEdgeD4', 0x33db4c), ('CIpcmEdgeD4', 0x33f830)]:
        table = f'_ZN11{cls}12m_iSharpnessE'
        consumer = f'_ZN11{cls}8SetParamEj'
        symbol = stock.symbols[consumer]
        start, end = symbol['st_value'], symbol['st_value']+symbol['st_size']
        relocs = [(at, item) for at, item in stock.relocs.items() if item['symbol'] == table]
        if len(relocs) != 1 or relocs[0][1]['type'] != 21:
            raise ValueError(f'Unexpected data-symbol relocation set: {table}')
        slot, relocation = relocs[0]
        offset = (slot-pic_base)&0xffffffff
        loads, direct_calls = [], []
        decoder = Cs(CS_ARCH_ARM, CS_MODE_ARM)
        decoder.skipdata = True
        for section in stock.elf.iter_sections():
            if not section['sh_flags'] & 4:
                continue
            for instruction in decoder.disasm(section.data(), section['sh_addr']):
                if instruction.mnemonic in ('b', 'bl', 'blx') and instruction.op_str == f'#{start:#x}':
                    direct_calls.append(hex(instruction.address))
                if instruction.mnemonic != 'ldr' or not re.fullmatch(r'\w+, \[pc, #(?:-?0x[\da-f]+|-?\d+)\]', instruction.op_str):
                    continue
                try:
                    pool, value = stock.literal(instruction.address)
                except ValueError:
                    continue
                if value == offset:
                    loads.append({'address': hex(instruction.address), 'pool': hex(pool)})
        if [int(item['address'], 16) for item in loads] != [expected_load]:
            raise ValueError(f'Additional or missing direct GOT-offset reader: {table}: {loads}')
        if direct_calls:
            raise ValueError(f'Direct original-entry calls bypass interposition: {consumer}: {direct_calls}')
        vtable = stock.symbols['_ZTV11'+cls]
        table_slots = [(at, item) for at, item in stock.relocs.items()
                       if vtable['st_value'] <= at < vtable['st_value']+vtable['st_size']
                       and item['symbol'] == consumer]
        if len(table_slots) != 1 or table_slots[0][1]['type'] != 2:
            raise ValueError('SetParam vtable entry is not dynamically preemptible')
        if not start <= expected_load < end:
            raise ValueError('Table reader outside its wrapped consumer')
        result.append({'table_symbol': table, 'table_address': hex(stock.symbols[table]['st_value']),
                       'table_storage_bytes': stock.symbols[table]['st_size'],
                       'got_slot': hex(slot), 'got_relocation': relocation,
                       'pic_base': hex(pic_base), 'all_direct_got_offset_loads': loads,
                       'direct_calls_bypassing_symbol_resolution': direct_calls,
                       'consumer': consumer, 'consumer_range': [hex(start), hex(end)],
                       'vtable_slot': hex(table_slots[0][0]), 'vtable_relocation': table_slots[0][1]})
    return result


def main():
    config = json.loads((ROOT/'project.local.json').read_text(encoding='utf-8-sig'))
    rootfs = Path(config['rootfs'])
    stock = Stock(rootfs/'usr/lib/libiq-fw-slpcam.so')
    bindings = static_bindings(stock)
    # Search installed executable/shared-library bytes for either exact exported
    # name, including a possible dynamic dlsym lookup. ELF metadata below then
    # identifies the defining/consuming library; arbitrary computed names are
    # outside this static search's scope.
    base, rf, sdk = wsl_path(ROOT), wsl_path(rootfs), wsl_path(ROOT/config['sdk'])
    patterns = ' '.join('-e '+shlex.quote(item['table_symbol']) for item in bindings)
    paths = shlex.quote(rf+'/usr/lib')+' '+shlex.quote(rf+'/usr/bin')
    search_script = f'''if command -v rg >/dev/null 2>&1; then
rg -a -l -F {patterns} {paths}
else
find {paths} -type f -print0 | xargs -0 grep -a -l -F {patterns}
fi'''
    search = subprocess.run(['wsl.exe', '-u', 'root', '-e', 'sh', '-c', search_script],
                            capture_output=True, timeout=60)
    if search.returncode not in (0, 1):
        raise RuntimeError(f'Table reference search exit{search.returncode}: {search.stderr.decode("utf-8", "replace")[:2000]}')
    references = search.stdout.decode('utf-8', 'replace').splitlines()
    unexpected = [name for name in references if not Path(name).name.startswith('libiq-fw-slpcam.so')]
    if unexpected:
        raise ValueError(f'Additional stock symbol-name readers need review: {unexpected}')
    script = f'''set -eu
tc=/opt/nx300m-toolchain/usr
rf={shlex.quote(rf)}
cd {shlex.quote(base)}
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \
 -march=armv7-a -mfloat-abi=soft -std=c99 -O2 -Wall -Wextra -Werror \
 -fno-pic -no-pie -fno-stack-protector -nostdlib -nostartfiles -Wl,-e,_start \
 -Wl,-E -Wl,--dynamic-linker=/lib/ld-linux.so.3 -Wl,--allow-shlib-undefined \
 -Wl,-rpath-link,"$rf/usr/lib" -Wl,-rpath-link,"$rf/lib" -I{shlex.quote(sdk+'/usr/include')} \
 runtime/sharpness_native_probe.c work/reference/metol/test_server/filmlab/src/crt0_arm.S \
 -L"$rf/usr/lib" -Wl,--no-as-needed -Wl,-l:libiq-fw-slpcam.so \
 -Wl,-l:libdiosal.so -Wl,-l:libproduction-mode.so "$rf/usr/lib/libudd.so.3.1" \
 "$rf/lib/libpthread.so.0" "$rf/lib/libc-2.13.so" "$rf/lib/libdl.so.2" -lgcc \
 -o outputs/metol/sharpness-native-probe.arm
'''
    build = subprocess.run(['wsl.exe', '-u', 'root', '-e', 'sh', '-c', script], capture_output=True, timeout=60)
    (OUT/'sharpness-native-build.log').write_bytes(build.stdout+build.stderr)
    if build.returncode:
        raise RuntimeError(f'Build exit{build.returncode}: {build.stderr.decode("utf-8", "replace")}')
    run = subprocess.run(['wsl.exe', '-u', 'root', '-e', rf+'/qemu-arm-static', '-cpu', 'cortex-a9',
                          '-L', rf, '-E', 'LD_LIBRARY_PATH='+rf+'/usr/lib:'+rf+'/lib',
                          base+'/outputs/metol/sharpness-native-probe.arm'], capture_output=True, timeout=60)
    stdout, stderr = (text.decode('utf-8', 'replace') for text in (run.stdout, run.stderr))
    (OUT/'sharpness-native-probe.stdout.log').write_text(stdout, encoding='utf-8')
    (OUT/'sharpness-native-probe.stderr.log').write_text(stderr, encoding='utf-8')
    rows = {line.split()[0]: dict(field.split('=', 1) for field in line.split()[1:])
            for line in stdout.splitlines() if line.startswith(('SERIAL ', 'SHARPNESS_NATIVE '))}
    passed = run.returncode == 0 and rows.get('SHARPNESS_NATIVE', {}).get('failures') == '0'
    if passed:
        exported = Stock(OUT/'sharpness-native-probe.arm')
        for binding in bindings:
            name = binding['table_symbol']
            raw, _, _ = read_symbol(exported.elf, exported.symbols[name])
            if len(raw) != 36:
                raise ValueError('Probe data symbol is not exactly9 words')
    report = {'build_exit_code': build.returncode, 'exit_code': run.returncode, 'passed': passed,
              'static_bindings': bindings, 'installed_symbol_name_occurrences': references,
              'reference_search_scope': 'Installed usr/lib and usr/bin raw exact symbol names, plus every executable section PC-literal direct GOT-offset load in original IQ ELF. Does not claim proof against computed pointers or future foreign modules.',
              'results': rows, 'stdout': stdout, 'stderr': stderr,
              'core': 'runtime/sharpness_native.h',
              'core_interface': 'unsigned int nx_sharpness_native_run(void*,unsigned int,int is_capture,unsigned int factor,int enabled,nx_sharpness_native_fn original,int*failure); original is unsigned int(*)(void*,unsigned int).',
              'locking': 'One mutual-exclusion boundary covers both consumers and each original call. TLS depth permits same-thread nesting; each call saves/restores its table. Enabled uses replicated selected Q8 factor, disabled uses original9 values.',
              'verification': 'Original constructors/GetIndex/virtual SetParam and real UDD setters. Whole16KiB per-path RAM compared with original consumer baseline after independently replacing only expected gain bitfields. Serial cases vary21 source factors, enable, native index, modes, branch thresholds and flags; two threads use separate node/parameter contexts and validate gain payloads within the serialized setter calls.',
              'boundaries': ['Unrelated CapIfDspSet and device-open-count are explicit fixture stubs; no camera or devices are opened.',
                             'No running simulator, stock library, production bridge or UI is modified.',
                             'Production Update/cache invalidation on request generation and eligibility remains the parent integration responsibility.',
                             'The core does not read mode/control state; caller snapshots enabled/factor and resolves original function.',
                             'Pixel kernels and source six-field equivalence are outside this native factor adapter.']}
    (OUT/'sharpness-native-probe.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, indent=2))
    if not passed:
        raise SystemExit(run.returncode or 1)


if __name__ == '__main__':
    main()
