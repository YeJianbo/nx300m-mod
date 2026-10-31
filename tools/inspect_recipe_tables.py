"""Export native recipe tables from the stock IQ ELF without executing it.

Run with the project's Conda Python. Symbol sizes delimit every read; decoded
views retain the original bytes and do not assert a source-recipe conversion.
"""
import argparse
import io
import json
import os
from pathlib import Path
import struct
import sys

os.environ.setdefault('CONDA_PREFIX', sys.prefix)
from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM, CS_MODE_THUMB
from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection


PROJECT = Path(__file__).resolve().parents[1]
LIBRARY = 'usr/lib/libiq-fw-slpcam.so'


def symbol_name(cls, member):
    return f'_ZN{len(cls)}{cls}{len(member)}{member}E'


def function_name(cls, member, args):
    return symbol_name(cls, member) + args


def table_specs():
    specs = []
    for pipe in ('CIpcs', 'CIpcm'):
        for member in ('m_iContrastX', 'm_iContrastY'):
            specs.append((pipe + 'GammaD4', member, 4))
        specs.append((pipe + 'EdgeD4', 'm_iSharpness', 4))
        if pipe == 'CIpcs':
            specs.extend((pipe + 'EdgeD4', member, 4)
                         for member in ('m_nGain', 'm_nCoring', 'm_nLimit'))
        specs.append((pipe + 'EdgeD4', 'm_ucLut', 1))
        specs.append((pipe + 'ColorD4', 'm_uiUvGain', 4))
    return specs


def selected_functions():
    names = []
    for pipe in ('CIpcs', 'CIpcm'):
        for kind in ('Gamma', 'Edge', 'Color'):
            cls = pipe + kind + 'D4'
            names.extend((function_name(cls, 'GetIndex', 'Pv'),
                          function_name(cls, 'SetParam', 'j')))
            names.append(f'_ZN{len(cls)}{cls}C1Ev')
            if kind == 'Gamma':
                names.append(function_name(cls, 'ApplyLut', 'PiPKiS2_S2_ii'))
            elif kind == 'Color':
                names.extend((function_name(cls, 'GetRgb2YccIndex', 'PvRj'),
                              function_name(cls, 'SetRgb2Ycc', 'i')))
    names.extend((function_name('CIpcParameterNode', 'GetSubIndex', 'ji'),
                  function_name('CIpcParameterNode', 'SetSubIndex', 'Rjij')))
    return names


def all_symbols(elf):
    found = {}
    for section_name in ('.dynsym', '.symtab'):
        section = elf.get_section_by_name(section_name)
        if section is None:
            continue
        for symbol in section.iter_symbols():
            if symbol['st_shndx'] != 'SHN_UNDEF':
                found.setdefault(symbol.name, symbol)
    return found


def read_symbol(elf, symbol):
    index = symbol['st_shndx']
    if not isinstance(index, int):
        raise ValueError(f'{symbol.name}: no file-backed section')
    section = elf.get_section(index)
    if section['sh_type'] == 'SHT_NOBITS':
        raise ValueError(f'{symbol.name}: no stored bytes')
    address = symbol['st_value']
    if symbol['st_info']['type'] == 'STT_FUNC':
        address &= ~1
    offset = address - section['sh_addr']
    size = symbol['st_size']
    if size <= 0 or offset < 0 or offset + size > section['sh_size']:
        raise ValueError(f'{symbol.name}: invalid symbol range')
    data = section.data()[offset:offset + size]
    if len(data) != size:
        raise ValueError(f'{symbol.name}: truncated symbol bytes')
    return data, section.name, section['sh_offset'] + offset


def relocations(elf):
    result = {}
    for section in elf.iter_sections():
        if not isinstance(section, RelocationSection):
            continue
        symbols = elf.get_section(section['sh_link'])
        for relocation in section.iter_relocations():
            name = symbols.get_symbol(relocation['r_info_sym']).name
            if name:
                result.setdefault(name, []).append({
                    'address': hex(relocation['r_offset']),
                    'type': relocation['r_info_type'],
                    'section': section.name,
                })
    return result


def disassemble(elf, symbols, names):
    targets = {symbol['st_value'] & ~1: name for name, symbol in symbols.items()}
    dynamic = elf.get_section_by_name('.dynsym')
    plt, relplt = elf.get_section_by_name('.plt'), elf.get_section_by_name('.rel.plt')
    if plt is not None and relplt is not None:
        for index, relocation in enumerate(relplt.iter_relocations()):
            targets[plt['sh_addr'] + 20 + index * 12] = (
                dynamic.get_symbol(relocation['r_info_sym']).name + '@plt')
    lines = [
        '# Static linear ARM disassembly of stock libiq-fw-slpcam.so.',
        '# Symbol sizes bound each listing. Literal pools may decode as instructions;',
        '# instruction-shaped pool words do not establish reachable code.',
    ]
    functions = {}
    for name in names:
        symbol = symbols[name]
        data, section, offset = read_symbol(elf, symbol)
        address = symbol['st_value'] & ~1
        functions[name] = {'address': hex(address), 'size': len(data),
                           'section': section, 'file_offset': hex(offset)}
        lines.append(f'\n## {name} {address:#x} size={len(data)}')
        decoder = Cs(CS_ARCH_ARM,
                     CS_MODE_THUMB if symbol['st_value'] & 1 else CS_MODE_ARM)
        decoder.skipdata = True
        for instruction in decoder.disasm(data, address):
            target = ''
            if instruction.mnemonic in ('b', 'bl', 'blx') and instruction.op_str.startswith('#0x'):
                target = targets.get(int(instruction.op_str[1:], 16), '')
            lines.append(f'{instruction.address:08x} {instruction.bytes.hex():8s} '
                         f'{instruction.mnemonic:8s} {instruction.op_str}'
                         + (f' ; {target}' if target else ''))
    return '\n'.join(lines) + '\n', functions


def annotations(cls, member):
    """Reviewed instruction locations in the stock NX300M 1.15 library."""
    capture = cls.startswith('CIpcs')
    if 'Gamma' in cls:
        base = '0x33c794-0x33c7d8' if capture else '0x340b90-0x340bcc'
        stride = '0x33cbb0-0x33cbe4' if capture else '0x340f60-0x340fa0'
        return {
            'use': 'contrast interpolation knots' if member.endswith('X') else 'contrast interpolation output knots',
            'index_evidence': [
                f'GetIndex {base}: input word at +0x60 is capped by unsigned comparison to 8 and stored in subindex 1.',
                f'SetParam {stride}: subindex 1 selects a 7-word row using (index*8-index)<<2, stride 28 bytes; ApplyLut knot count is 7.',
                'ApplyLut passes knot arrays to CIpcMath::GetInterpolatedValue(const int*, const int*, int, int).',
            ],
            'expected_row_width_elements': 7,
            'row_interpretation': 'shared X knots' if member.endswith('X') else 'one row per stored contrast index',
        }
    if 'Color' in cls:
        get = '0x33e0c0-0x33e0e0,0x33e1a8-0x33e1b8' if capture else '0x33fc40-0x33fc60,0x33fd28-0x33fd38'
        apply = '0x33e678-0x33e6d8' if capture else '0x340164-0x3401c4'
        return {
            'use': 'saturation-related UV matrix gain',
            'index_evidence': [
                f'GetRgb2YccIndex {get}: input word +0x64 is capped by unsigned comparison to 8 and stored in subindex 4.',
                f'SetRgb2Ycc {apply}: subindex 4 selects one 32-bit word; 128 bypasses scaling; otherwise four UV matrix coefficients use (coefficient*max(gain,0)+64)>>7 with upper cap 255.',
            ],
            'unity_gain_raw': 128,
            'gain_denominator': 128,
        }
    if member == 'm_iSharpness':
        return {
            'use': 'sharpness gain factor',
            'index_evidence': [
                ('CIpcs GetIndex 0x33d9f4,0x33da10-0x33da2c: input word +0x68 is capped to 8 and stored in subindex 1.' if capture else
                 'CIpcm GetIndex 0x33f76c-0x33f7a0: input word +0x68, incremented if input word +0x00 equals 3, is stored in subindex 1; SetParam 0x33f834-0x33f838 caps it to 8.'),
                ('SetParam 0x33db5c-0x33db88: one word from Sharpness[subindex1] times Gain[subindex3], shifted right 8, initializes two edge gains; later branches adjust these gains.' if capture else
                 'SetParam 0x33f85c-0x33f880: one word from Sharpness[subindex1], multiplied by 20 then signed-truncated after division by 256, initializes two edge gains; input mode 3 doubles and caps these to 255.'),
            ],
        }
    if member in ('m_nCoring', 'm_nLimit', 'm_nGain'):
        return {
            'use': {'m_nCoring': 'edge coring', 'm_nLimit': 'positive and negative edge limits', 'm_nGain': 'edge gain multiplier'}[member],
            'index_evidence': [
                ('GetIndex 0x33da54-0x33da6c: arithmetic (input_word[+0x2c]-56)>>4, capped by unsigned comparison to 9, becomes subindex 3.' if member == 'm_nGain' else
                 'GetIndex 0x33da30-0x33da50: arithmetic (input_word[+0x2c]-60)>>3, capped by unsigned comparison to 18, becomes subindex 2.'),
                {'m_nGain': 'SetParam 0x33db60-0x33db88: one word indexed by subindex 3 multiplies Sharpness[subindex1], followed by >>8.',
                 'm_nCoring': 'SetParam 0x33db70-0x33dba0: one word indexed by subindex 2 is stored as coring.',
                 'm_nLimit': 'SetParam 0x33db80-0x33dbc0: one word indexed by subindex 2 is stored as a positive limit and negated for the negative limit.'}[member],
            ],
            'input_unit': 'not established here for input word +0x2c',
        }
    return {
        'use': 'edge LUT bytes',
        'index_evidence': [
            ('GetIndex 0x33da00-0x33da18: subindex 0 is 0 when input word +0x2c >160, otherwise 1; SetParam 0x33dbb0 adds subindex0<<8 to LUT base before ipcs_edge_lut.' if capture else
             'SetParam 0x33f8ac-0x33f8b4 passes the complete LUT base to ipcm_edge_lut; no indexed row selection.'),
            ('SDK usr/include/drime4/udd/ipcs_submodule.h:62 declares unsigned char *p_lut.' if capture else
             'SDK usr/include/drime4/udd/ipcm_submodule.h:58 declares unsigned char *p_lut.'),
        ],
        'expected_row_width_elements': 256,
        'row_interpretation': '256-byte selectable LUT blocks' if capture else 'single 256-byte LUT',
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=PROJECT / 'project.local.json')
    parser.add_argument('--library', type=Path, help='Explicit stock ELF path; defaults to configured rootfs')
    parser.add_argument('--output', type=Path, default=PROJECT / 'outputs/metol/recipe-native-tables.json')
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding='utf-8-sig'))
    source = args.library or Path(config['rootfs']) / LIBRARY
    source = source.resolve()
    # Read once: pyelftools performs many seeks, costly across the WSL share.
    source_bytes = source.read_bytes()
    elf = ELFFile(io.BytesIO(source_bytes))
    if elf.elfclass != 32 or elf['e_machine'] != 'EM_ARM' or not elf.little_endian:
        raise ValueError('Expected a little-endian 32-bit ARM ELF')
    symbols = all_symbols(elf)
    relocs = relocations(elf)
    asm, functions = disassemble(elf, symbols, selected_functions())
    tables = []
    for cls, member, width in table_specs():
        name = symbol_name(cls, member)
        symbol = symbols[name]
        if symbol['st_info']['type'] != 'STT_OBJECT':
            raise ValueError(f'{name}: expected STT_OBJECT')
        raw, section, offset = read_symbol(elf, symbol)
        if len(raw) % width:
            raise ValueError(f'{name}: size not divisible by reviewed element width')
        values = list(raw) if width == 1 else list(struct.unpack('<' + 'I' * (len(raw) // 4), raw))
        notes = annotations(cls, member)
        row_width = notes.get('expected_row_width_elements')
        table = {
            'class': cls, 'member': member, 'symbol': name,
            'address': hex(symbol['st_value']), 'size_bytes': len(raw),
            'section': section, 'file_offset': hex(offset),
            'element_width_bytes': width,
            'raw_encoding': 'uint8' if width == 1 else 'little-endian unsigned 32-bit word view',
            'raw_hex': raw.hex(), 'raw_values': values,
            'relocations': relocs.get(name, []),
            'interpretation': notes,
        }
        if row_width and len(values) % row_width == 0:
            # This view is justified by the reviewed call stride/count, not
            # by factoring a symbol size into a plausible two-dimensional array.
            table['access_view'] = {
                'rows': len(values) // row_width, 'elements_per_row': row_width,
                'values': [values[i:i + row_width] for i in range(0, len(values), row_width)],
            }
        tables.append(table)
    absent = [symbol_name('CIpcmEdgeD4', name) for name in ('m_nGain', 'm_nCoring', 'm_nLimit')
              if symbol_name('CIpcmEdgeD4', name) not in symbols]
    asm_path = args.output.with_suffix('.asm')
    result = {
        'source': {'path': str(source), 'size_bytes': len(source_bytes),
                   'machine': elf['e_machine'], 'elf_class': elf.elfclass,
                   'endianness': 'little', 'config': str(args.config.resolve())},
        'scope': 'Static stored table extraction; no camera process or virtual machine is executed.',
        'bounds': 'Every object is read using its own ELF st_value and st_size. raw_values always stays flat; access_view is included only where reviewed call stride/count establishes grouping.',
        'analysis_basis': 'Interpretation instruction locations were reviewed in the configured NX300M 1.15 ELF. Re-review them for another firmware; ELF extraction remains symbol-bounded.',
        'disassembly': str(asm_path.resolve()),
        'functions': functions,
        'tables': tables,
        'missing_symbols': absent,
        'ci_pcm_edge_observation': 'No separate Gain/Coring/Limit table symbols found. Its SetParam initializes extraction fields and edge limits with immediate constants and scales the Sharpness table; see 0x33f7b0-0x33f8c8.',
        'limits': [
            'Input struct offsets and stored indices do not establish any Adobe/LR/Metol source-setting conversion.',
            'GetIndex uses unsigned caps and packed subindices; negative inputs must not be treated as an independently verified clamp to zero.',
            'UV gain 128 is unity in the reviewed matrix scaling operation. An index-to-UI-label mapping is not established by this export.',
            'Stored contrast knots and edge LUTs are not final scene-dependent output LUTs, image-equivalence evidence, or an image-quality evaluation.',
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    asm_path.write_text(asm, encoding='utf-8')
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(args.output.resolve()), 'disassembly': str(asm_path.resolve()),
                      'table_count': len(tables), 'total_table_bytes': sum(t['size_bytes'] for t in tables),
                      'absent_ci_pcm_edge_table_symbols': absent}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
