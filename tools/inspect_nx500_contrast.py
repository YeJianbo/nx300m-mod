"""Extract the NX500 1.12 Contrast consumer and compare stored NX300M knots.

This script does not approximate the 21 source levels with nine target levels.
The floating interpolation leaf and complete Gamma builder are not executed.
"""
import json
import os
from pathlib import Path
import struct
import sys

os.environ.setdefault('CONDA_PREFIX', sys.prefix)
from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM

ROOT = Path(__file__).resolve().parents[1]
BASE = 0x80000000
SPANS = (
    ('Contrast IQ decoder', 0x3865d8, 0x3866e4),
    ('Parameter pack: decoded Contrast -> +0x80', 0x38580c, 0x385844),
    ('CIpcGammaD5 index builder', 0x38d6c4, 0x38da1c),
    ('CIpcGammaD5 constructor and original 19-point copies', 0x38da5c, 0x38db14),
    ('CIpcGammaD5 SetParam: base19 and Contrast7 application', 0x38f90c, 0x38fa6c),
    ('ApplyLut19 using seven interpolation knots', 0x38dc68, 0x38dce0),
    ('Original interpolation helper including boundary segments', 0x3887f0, 0x388ca8),
    ('Softfloat subtraction entry and addition prefix', 0x4fa6f0, 0x4fa714),
    ('Softfloat signed-int conversion prefix', 0x4fa8b8, 0x4fa8d8),
    ('Softfloat multiplication prefix', 0x4fa994, 0x4fa9d4),
    ('Softfloat division prefix', 0x4fab2c, 0x4fab68),
    ('Softfloat truncation and saturation', 0x4fada0, 0x4fadfc),
)


def main():
    source = ROOT/'work/reference/nx500-stock-1.12/partition-05.bin'
    data = source.read_bytes()
    decoder = Cs(CS_ARCH_ARM, CS_MODE_ARM)
    decoder.skipdata = True

    def word(offset):
        return struct.unpack_from('<I', data, offset)[0]

    def ints(offset, count):
        if offset < 0 or offset+count*4 > len(data):
            raise ValueError('Table range outside partition')
        return list(struct.unpack_from('<'+str(count)+'i', data, offset))

    def string(address):
        offset = address-BASE
        return data[offset:data.index(b'\0', offset)].decode('ascii')

    # These literals are loaded by the constructor and SetParam instructions.
    contrast_y_address = word(0x38fbc4)
    contrast_x_address = contrast_y_address-0x1c
    gamma_x_address = word(0x38db0c)
    gamma_y_address = gamma_x_address+0x4c
    if word(0x38fbc0)-0x1c8 != gamma_x_address:
        raise ValueError('Constructor and SetParam base Gamma X disagree')
    contrast_x = ints(contrast_x_address-BASE, 7)
    contrast_y = [ints(contrast_y_address-BASE+index*28, 7) for index in range(21)]
    gamma_x = ints(gamma_x_address-BASE, 19)
    gamma_y = [ints(gamma_y_address-BASE+index*76, 19) for index in range(5)]
    if contrast_y[10] != contrast_x:
        raise ValueError('Original neutral row is not the shared X knots')
    # Recover the class identity through vtable[-1] -> typeinfo -> type name.
    typeinfo = word(0x62f6ac)
    class_name = string(word(typeinfo-BASE+4))
    if class_name != '11CIpcGammaD5':
        raise ValueError(f'Unexpected Gamma node RTTI {class_name}')

    target_path = ROOT/'outputs/metol/recipe-native-tables.json'
    target = json.loads(target_path.read_text(encoding='utf-8'))
    tables = {(item['class'], item['member']): item for item in target['tables']}
    comparisons = []
    for cls in ('CIpcsGammaD4', 'CIpcmGammaD4'):
        target_x = tables[(cls, 'm_iContrastX')]['raw_values']
        target_y = tables[(cls, 'm_iContrastY')]['access_view']['values']
        matches = [{'source_level': level, 'target_index': index,
                    'target_setting': index-4}
                   for level, row in enumerate(contrast_y)
                   for index, target_row in enumerate(target_y)
                   if contrast_x == target_x and row == target_row]
        comparisons.append({
            'target_class': cls, 'comparison': 'Exact integer equality of shared X and all seven Y values; no scaling or interpolation.',
            'shared_x_identical': contrast_x == target_x,
            'exact_matches': matches,
            'source_levels_without_exact_match': [level for level in range(21)
                if not any(item['source_level'] == level for item in matches)],
            'target_indices_without_exact_match': [index for index in range(len(target_y))
                if not any(item['target_index'] == index for item in matches)],
        })

    source_pw = json.loads((ROOT/'outputs/metol/nx500-pw-source.json').read_text())
    contrast_entries = source_pw['scalar_encoding']['entries']
    report = {
        'source': str(source.relative_to(ROOT)), 'load_base': hex(BASE),
        'analysis': 'Static original-byte extraction; no ARM execution, curve approximation or runtime changes.',
        'iq': {'variable_id': '0x111', 'iq_id': '0x57',
               'iq_name': string(word(0x5758e0+4*0x57)),
               'original_decoder': '0x803865d8',
               'ordinary_capture_modes': [1, 2, 3, 4, 8],
               'normalization': 'Decode signed 12-bit current from raw[31:20] and signed 10-bit bounds from raw[19:10]/raw[9:0]; when bounds differ return trunc_zero((current-lower)*20/(upper-lower)), otherwise 10.',
               'source_metol_levels': [{'level': item['value'], 'raw': item['raw']}
                                       for item in contrast_entries],
               'nonordinary': 'Default 10, or separate SmartArt contrast IQ0x96 path; not covered by the ordinary PW decode claim.'},
        'consumer_chain': [
            '0x8038582c calls original decoder; 0x80385830 stores decoded index in parameter pack+0x80.',
            'GammaD5 virtual GetIndex 0x8038d6c4 reads pack+0x80 at 0x8038d810; 0x8038d8c8-0x8038d8dc caps unsigned index at20 and writes subindex1.',
            'GammaD5 virtual SetParam 0x8038f90c reads subindex1 into r6 at 0x8038f94c-0x8038f960.',
            '0x8038fa18-0x8038fa24 computes row offset=(index*8-index)*4, stride28 bytes.',
            '0x8038fa28-0x8038fa40 invokes 0x8038dc68: output19 at sp+0x1ac, current GammaY19, shared ContrastX7, selected ContrastY7, output count19 and knot count7.',
            'ApplyLut 0x8038dc68 loops over all19 current GammaY values and calls 0x803887f0(X7,Y7,7,inputY); clamps only upper output to Y7[6]=1023.',
            '0x8038fa44-0x8038fa68 then applies a separate seven-knot table indexed by subindex2 to the already contrast-remapped output19.',
        ],
        'gamma_node': {'rtti': class_name, 'vtable': '0x8062f6b0',
                       'get_index_slot': '0x8062f6cc', 'get_index': '0x8038d6c4',
                       'set_param_slot': '0x8062f6c8', 'set_param': '0x8038f90c'},
        'contrast_knots': {'x_address': hex(contrast_x_address),
                           'y_address': hex(contrast_y_address), 'element_type': 'little-endian signed32',
                           'row_stride_bytes': 28, 'x': contrast_x, 'neutral_level': 10,
                           'rows': [{'level': level, 'y': row} for level, row in enumerate(contrast_y)]},
        'base_gamma_19': {'x_address': hex(gamma_x_address), 'y_address': hex(gamma_y_address),
                          'static_row_stride_bytes': 76, 'static_row_count': 5,
                          'x': gamma_x, 'y_rows': gamma_y,
                          'selection': '0x8038f984-0x8038f9b8 selects static row when subindex0<=4; otherwise object+0xedc and object+0xf28 supply dynamic19-point arrays.',
                          'domains': 'Stored common X spans0..16383; static GammaY spans0..1023. Contrast acts on the GammaY values, not directly on this 14-bit X axis.'},
        'interpolator': {'entry': '0x803887f0', 'end_exclusive': '0x80388ca8',
                         'abi': 'r0=X int32*, r1=Y int32*, r2=knot_count, r3=query; returns int32.',
                         'evidence': 'Exact-knot returns, linear outer extrapolation, separate first/last interval branches and interior software-float interpolation are visible in the original instructions.',
                         'transcription': 'runtime/nx500_contrast_math.h; preserves each original float32 operation, separate boundary intervals, integer extrapolation, +0.5 then f2iz.',
                         'softfloat': {
                             '0x804fa8b8': 'i2f: detect integer sign, negate negative magnitude, construct normalized floating exponent.',
                             '0x804fa6f0': 'fsub: flip r1 sign and fall through to fadd0x804fa6f4.',
                             '0x804fa6f4': 'fadd: compare and align floating exponent/significand, then combine.',
                             '0x804fa994': 'fmul: add input exponents and UMULL significands.',
                             '0x804fab2c': 'fdiv: subtract input exponents and divide significands.',
                             '0x804fada0': 'f2iz: discard fractional bits with exponent shifts, restore sign; NaN0 and signed overflow saturation.',
                         },
                         'not_yet_done': 'This extraction script does not execute numerical comparisons; the dedicated probe owns original-ARM validation. Complete Gamma builder port and final hardware LUT verification remain separate.'},
        'nx300m_exact_comparison': {'source': str(target_path.relative_to(ROOT)),
                                    'results': comparisons},
        'limits': [
            'Identical stored seven-knot curves establish only those table rows, not equality of the two Gamma interpolators, base Gamma curves, processing order or image output.',
            'The 19-point contrast result is intermediate; mode-dependent base construction, subsequent seven-knot remapping, later curve fitting and hardware packing remain in the source builder.',
            'No nearest-level mapping, downconversion or scaling is supplied for source levels without exact target tables.',
        ],
    }
    output = ROOT/'outputs/metol/nx500-contrast'
    output.with_suffix('.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    lines = ['# Original Samsung NX500 1.12 P7. VA = file offset + 0x80000000.',
             '# Static linear listing; literal pools may decode as instructions.']
    for name, start, end in SPANS:
        lines.append(f'\n## {name} [{BASE+start:#x},{BASE+end:#x})')
        for instruction in decoder.disasm(data[start:end], BASE+start):
            lines.append(f'{instruction.address:08x} [{instruction.address-BASE:08x}] '
                         f'{instruction.bytes.hex():8s} {instruction.mnemonic:8s} {instruction.op_str}')
    for name, address, rows in [('Contrast X7', contrast_x_address, [contrast_x]),
                                 ('Contrast Y21x7', contrast_y_address, contrast_y),
                                 ('Gamma X19', gamma_x_address, [gamma_x]),
                                 ('Gamma Y5x19', gamma_y_address, gamma_y)]:
        lines.append(f'\n## {name}')
        for index, row in enumerate(rows):
            at = address+index*len(row)*4
            lines.append(f'{at:08x} [{at-BASE:08x}] .word '+', '.join(map(str,row)))
    output.with_suffix('.asm').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    print(json.dumps({'iq_id': report['iq']['iq_id'], 'contrast_x': contrast_x,
                      'source_rows': len(contrast_y), 'base_gamma_rows': len(gamma_y),
                      'exact_comparison': comparisons,
                      'float_interpolation_ported': False}, indent=2))


if __name__ == '__main__':
    main()
