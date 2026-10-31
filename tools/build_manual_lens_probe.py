"""Build a read-only, external ARM lens/PP observer against the old stock libc."""
import io
import json
from pathlib import Path
import shlex
import shutil
import struct
import subprocess

from elftools.elf.elffile import ELFFile
from inspect_recipe_tables import all_symbols, read_symbol

PROJECT = Path(__file__).resolve().parents[1]
OUT = PROJECT / 'outputs/controls/manual-lens'
STOCK = Path('//wsl.localhost/Debian/opt/nx300m-lab/stock-1.15')
MODULES = ('usr/bin/di-camera-app-nx300', 'usr/lib/libiq-fw-slpcam.so',
           'usr/lib/liblens-fw-prod.so', 'usr/lib/libversion-info-nx300.so')
SPECS = (
    (0, '_Z25UI_Callback_Lens_Detachedb', 0x211610, 44),
    (0, '_Z12UI_Get_Value14eUI_VALUE_ITEM', 0x1696a0, 316),
    (0, '_Z25UI_Get_Value_Capture_Menu14eUI_VALUE_ITEM', 0x155660, 76),
    (0, '_Z33UI_Get_Value_Pointer_Capture_Menu14eUI_VALUE_ITEM', 0x1570fc, 3384),
    (0, '_Z31UI_Check_WiFi_ModeChange_Enablei', 0x1cc870, 1312),
    (1, '_ZN15CPreProcessorD411InitCsParamEv', 0x34316c, 3192),
    (1, '_ZN15CPreProcessorD413UpdateCsParamEb', 0x343e74, 1940),
    (1, '_ZN11CIQLensInfoC1Ev', 0x30c63c, 192),
    (1, '_ZN15CPreProcessorD4C1Ev', 0x3415a4, 352),
    (2, '_ZN16CLensInfoControl14IsLensAttachedEv', 0x1dad4, 8),
    (2, '_ZN15CLensGateKeeper14IsLensAttachedEv', 0x25d48, 76),
    (2, '_ZN14CLensOperation13IsModuleReadyEv', 0x2b250, 64),
    (3, 'get_gpio_number', 0x1fa0, 52),
    (3, '_ZN19CVersionInformation13GetGpioNumberE9EGpioName', 0x32c8, 152),
    (3, '_ZN19CVersionInformation14gGpioMap_nx300E', 0xd25c, 352),
)


def main():
    compiler = shutil.which('clang')
    if not compiler:
        raise SystemExit('clang is required')
    OUT.mkdir(parents=True, exist_ok=True)
    images = [(STOCK / relative).read_bytes() for relative in MODULES]
    elves = [ELFFile(io.BytesIO(data)) for data in images]
    for index, elf in enumerate(elves):
        if elf.elfclass != 32 or not elf.little_endian or elf['e_machine'] != 'EM_ARM':
            raise ValueError(f'Unexpected architecture: {MODULES[index]}')
        if elf['e_type'] != ('ET_EXEC' if index == 0 else 'ET_DYN'):
            raise ValueError(f'Unexpected ELF type: {MODULES[index]}')
    symbols = [all_symbols(elf) for elf in elves]
    header = ['static const char *const probe_module_paths[]={',
              ','.join(json.dumps('/' + path) for path in MODULES), '};\n']
    header.append('static const unsigned char probe_exe_header[]={' + ','.join(hex(b) for b in images[0][:52]) + '};\n')
    counts = []
    for index, elf in enumerate(elves):
        segments = [s for s in elf.iter_segments() if s['p_type'] == 'PT_LOAD']
        counts.append(len(segments))
        header.append(f'static const struct load_segment probe_segments_{index}[]={{')
        header.append(','.join('{' + ','.join(hex(s[key]) + 'u' for key in ('p_vaddr', 'p_offset', 'p_filesz', 'p_memsz', 'p_flags')) + '}' for s in segments))
        header.append('};\n')
    header.append('static const struct load_segment *const probe_segments[]={' + ','.join(f'probe_segments_{i}' for i in range(4)) + '};\n')
    header.append('static const unsigned int probe_segment_counts[]={' + ','.join(map(str, counts)) + '};\n')
    evidence = []
    state_table = None
    for index, (module, name, address, size) in enumerate(SPECS):
        symbol = symbols[module][name]
        if (symbol['st_value'], symbol['st_size']) != (address, size):
            raise ValueError(f'Unreviewed stock symbol: {name}')
        data, _, _ = read_symbol(elves[module], symbol)
        if name == '_ZN14CLensOperation13IsModuleReadyEv':
            # Check every instruction and both PIC literals before interpreting
            # state+3 as an unsigned switch-table index. No target code runs.
            expected = (0xe5902070, 0xe59f302c, 0xe2822003, 0xe3520016,
                        0xe08f3003, 0x8a000004, 0xe59f101c, 0xe0833001,
                        0xe0832102, 0xe59202b8, 0xe12fff1e, 0xe3a00018,
                        0xe3420000, 0xe12fff1e, 0x537d8, 0xffff0e5c)
            words = struct.unpack('<16I', data)
            if words != expected:
                raise ValueError('Unreviewed IsModuleReady switch or PIC literals')
            table_address = (address + 0x18 + words[14] + words[15] + 0x2b8) & 0xffffffff
            if table_address != 0x6fb54:
                raise ValueError('Unreviewed IsModuleReady table address')
            segment = next(seg for seg in elves[module].iter_segments()
                           if seg['p_type'] == 'PT_LOAD' and seg['p_vaddr'] <= table_address
                           and table_address + 23 * 4 <= seg['p_vaddr'] + seg['p_filesz'])
            start = table_address - segment['p_vaddr']
            state_table = segment.data()[start:start + 23 * 4]
            values = struct.unpack('<23I', state_table)
            expected_values = (0x2000001b, 0x2000001a, 0x20000017) + (0x20000018,) * 12 + (0x20000019,) + (0x20000018,) * 6 + (0x20000019,)
            if values != expected_values:
                raise ValueError('Unreviewed IsModuleReady return table')
            header.append('#define PROBE_LENS_STATE_OFFSET 0x70u\n')
            header.append('#define PROBE_LENS_STATE_TABLE_ADDRESS 0x6fb54u\n')
            header.append('static const uint32_t probe_lens_state_results[]={' + ','.join(hex(v) + 'u' for v in values) + '};\n')
            header.append('static const unsigned char probe_lens_state_table[]={' + ','.join(hex(b) for b in state_table) + '};\n')
            evidence.append({'module': MODULES[module], 'kind': 'IsModuleReady_static_decode',
                             'state_offset': '0x70', 'unsigned_index': '(raw + 3) mod 2^32',
                             'max_index': 22, 'default_result': '0x20000018',
                             'table_address': hex(table_address), 'table_bytes': state_table.hex(),
                             'entries': [{'raw_state_signed': i - 3, 'result': hex(value)} for i, value in enumerate(values)],
                             'ready_result': '0x20000019', 'ready_raw_states': [12, 19],
                             'method_words': [hex(word) for word in words]})
        if name.endswith('InitCsParamEv') and struct.unpack_from('<III', data, 0x343534-address) != (0xe5d63008, 0xe3530000, 0x0affffa3):
            raise ValueError('Unexpected InitCsParam ready gate')
        if name.endswith('UpdateCsParamEb') and struct.unpack_from('<III', data, 0x344010-address) != (0xe5d93008, 0xe3530000, 0x0affffde):
            raise ValueError('Unexpected UpdateCsParam ready gate')
        if name.endswith('gGpioMap_nx300E'):
            pins = [struct.unpack_from('<I', data, (row*44+3)*4)[0] for row in (0, 1)]
            if pins != [88, 88]:
                raise ValueError('Physical detect GPIO differs across board rows')
        header.append(f'static const unsigned char probe_bytes_{index}[]={{' + ','.join(hex(b) for b in data) + '};\n')
        evidence.append({'module': MODULES[module], 'symbol': name, 'address': hex(address), 'size': size})
    header.append('static const struct stock_check probe_checks[]={')
    header.append(','.join(f'{{{module},0x{address:x}u,{size}u,probe_bytes_{index},{json.dumps(name)}}}' for index, (module, name, address, size) in enumerate(SPECS)))
    header.append('};\n#define PROBE_GPIO_NUMBER 88u\n')
    if state_table is None:
        raise ValueError('Missing electronic readiness table')
    ui_state = symbols[0]['g_ui_variable_info']
    if (ui_state['st_value'], ui_state['st_size'], ui_state['st_info']['type']) != (0x4daf44, 2724, 'STT_OBJECT'):
        raise ValueError('Unreviewed UI state object')
    header.append('#define PROBE_WIFI_LENS_CACHE 0x4db140u\n')
    evidence.append({'module': MODULES[0], 'symbol': 'g_ui_variable_info',
                     'address': '0x4daf44', 'size': 2724,
                     'lens_cache_offsets': ['0x1fc', '0x200', '0x204'],
                     'scope': 'cached UI values; no driver ioctl calls'})
    for label, name, address in (
            ('IQ', '_ZN13CIpcSingletonI11CIQLensInfoE11m_pInstanceE', 0x45f3ec),
            ('PP', '_ZN13CIpcSingletonI15CPreProcessorD4E11m_pInstanceE', 0x463fc8)):
        symbol = symbols[1][name]
        if (symbol['st_value'], symbol['st_size'], symbol['st_info']['type']) != (address, 4, 'STT_OBJECT'):
            raise ValueError(f'Unreviewed singleton: {name}')
        header.append(f'#define PROBE_{label}_SINGLETON 0x{address:x}u\n')
        evidence.append({'module': MODULES[1], 'symbol': name, 'address': hex(address), 'size': 4})
    name = '_ZN14CLensOperation11m_pInstanceE'
    symbol = symbols[2][name]
    if ((symbol['st_value'], symbol['st_size'], symbol['st_info']['type']) != (0x88164, 4, 'STT_OBJECT') or
            elves[2].get_section(symbol['st_shndx'])['sh_type'] != 'SHT_NOBITS'):
        raise ValueError('Unreviewed CLensOperation singleton BSS slot')
    header.append('#define PROBE_LENS_SINGLETON 0x88164u\n')
    evidence.append({'module': MODULES[2], 'symbol': name, 'address': '0x88164', 'size': 4,
                     'scope': 'read existing singleton pointer only; never create or call'})
    (OUT / 'manual_lens_probe_stock.h').write_text(''.join(header), encoding='ascii')
    obj = OUT / 'manual-lens-probe.o'
    subprocess.run([compiler, '--target=arm-linux-gnueabi', '-march=armv7-a', '-mfloat-abi=soft',
                    '-std=c99', '-O2', '-Wall', '-Wextra', '-Werror', '-ffreestanding', '-fno-builtin',
                    '-fno-stack-protector', '-fno-pie', '-ffunction-sections', '-fdata-sections',
                    '-I' + str(OUT), '-c', 'runtime/manual_lens_probe.c', '-o', str(obj)], cwd=PROJECT, check=True)
    base = '/mnt/' + PROJECT.drive[0].lower() + PROJECT.as_posix()[2:]
    script = f'''set -eu
cd {shlex.quote(base)}
guest=/opt/nx300m-lab/stock-1.15
tc=/opt/nx300m-toolchain/usr
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-ld" -m armelf_linux_eabi --gc-sections -e _start \\
 --dynamic-linker /lib/ld-linux.so.3 -rpath-link "$guest/lib" \\
 outputs/controls/manual-lens/manual-lens-probe.o "$guest/lib/libc.so.6" \\
 -o outputs/controls/manual-lens/manual-lens-probe.arm
'''
    subprocess.run(['wsl.exe', '-d', 'Debian', '-e', 'sh', '-c', script], check=True, timeout=30)
    target = OUT / 'manual-lens-probe.arm'
    (OUT / 'manual-lens-probe-build.json').write_text(json.dumps({
        'artifact': str(target), 'bytes': target.stat().st_size, 'stock_root': str(STOCK),
        'scope': 'external unlocked observation; no target calls, writes, attach, or injection',
        'stock_checks': evidence, 'process_executed': False, 'deployed': False,
    }, indent=2) + '\n', encoding='utf-8')
    print(f'Built {target} ({target.stat().st_size} bytes); not executed or deployed')


if __name__ == '__main__':
    main()
