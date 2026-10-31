"""Record the statically reviewed native YCC ABI and original IQ consumers."""
import argparse
import io
import json
from pathlib import Path
import struct

from elftools.elf.elffile import ELFFile
from inspect_recipe_tables import all_symbols, disassemble, read_symbol, symbol_name, function_name

PROJECT = Path(__file__).resolve().parents[1]


def sdk_excerpt(path):
    lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
    start = next(index for index, line in enumerate(lines) if line.startswith('enum ipc') and 'rgb2ycc' in line)
    end = next(index for index in range(start, len(lines))
               if lines[index].startswith('struct ipc') and 'rgb2ycc' in lines[index])
    end = next(index for index in range(end, len(lines)) if lines[index].strip() == '};')
    return {'path': str(path.relative_to(PROJECT)).replace('\\', '/'),
            'first_line': start + 1, 'last_line': end + 1,
            'text': '\n'.join(lines[start:end + 1])}


def selector3_finding(objects):
    defaults = [item for item in objects if 'm_iRgb2Yuv' in item['symbol']]
    source_path = PROJECT / 'outputs/metol/nx500-pw-source.json'
    source_ops = json.loads(source_path.read_text(encoding='utf-8'))['source_color_operators']
    layouts = {}
    for path, start, evidence, base_symbol in (
            ('ipcs', 0x109c, '0x91868-0x91a84', 'ipcs_reg_base'),
            ('ipcm', 0x6c, '0x4819c-0x483a0', 'ipcm_reg_core_base')):
        fields = [{'coefficient_index': 0, 'struct_offset_bytes': 40,
                   'register_offset': hex(start), 'lsb': 16, 'width_bits': 11}]
        for index in range(1, 9):
            fields.append({'coefficient_index': index, 'struct_offset_bytes': 40 + index * 4,
                           'register_offset': hex(start + 4 + ((index - 1) // 2) * 4),
                           'lsb': ((index - 1) % 2) * 16, 'width_bits': 11})
        layouts[path] = {
            'base_symbol': base_symbol, 'packing_evidence': evidence,
            'first_word': '(old & ~0x07ff0000) | ((ycc_mat[0] & 0x7ff) << 16)',
            'next_four_words': '((ycc_mat[1+2*k]&0x7ff) | ((ycc_mat[2+2*k]&0x7ff)<<16)), k=0..3; words start at zero',
            'fields': fields,
        }
    return {
        'question': 'Can the native selector-3 nine-coefficient path represent the complete NX500 source RGB-to-YCC matrix without a 2x2 approximation?',
        'conclusion': 'Yes for every output coefficient in -1024..1023: sign-extend each source int16 to a 32-bit input word and submit all nine as ycc_mat with sel=3. The target software convention is also Q8, so there is no Q8-to-Q7 conversion at this boundary. The whole signed16 source domain is not representable by signed11 target fields.',
        'true_setter_symbols': ['ipcs_rgb2ycc_param', 'ipcm_rgb2ycc_param'],
        'selector': 3, 'struct_field': 'ycc_mat[0..8]',
        'struct_offset_bytes': 40, 'element_width_bytes': 4,
        'signed_source_conversion': 'u32_word = (uint32_t)(int32_t)source_int16; do not zero-extend a negative int16 and do not pass prepacked 11-bit values.',
        'range': {'minimum': -1024, 'maximum': 1023,
                  'all_nine_checked_before_register_write': True,
                  'invalid_value_result': 2,
                  'checks': {'ipcs': '0x91810-0x91864', 'ipcm': '0x48144-0x48198'},
                  'counterexample': 'source int16 coefficient 1024 or -1025 is valid source storage but is rejected by the genuine target setter.',
                  'implication': 'An implementation must preserve the full signed16 source arithmetic first, then handle any out-of-range final matrix explicitly; clipping changes the source operator.'},
        'default_symbols': [{'symbol': item['symbol'], 'address': item['address'],
                             'size_bytes': item['size_bytes'], 'raw_i32': item['raw_i32']}
                            for item in defaults],
        'default_interpretation': {
            'layout': 'row-major 3x3; input columns R,G,B; output rows Y,Cb(U),Cr(V)',
            'matrix_q8': [[77,150,29],[-43,-85,128],[128,-107,-21]],
            'unit': 256, 'row_sums': [256,0,0],
            'basis': ['The actual m_iRgb2Yuv objects in both pipes contain these nine signed 32-bit values.',
                      'Both ColorD4 constructors initialize their nine-word RGB-to-YCC cache to diag(256,256,256).',
                      'GetRgb2YccIndex copies nine signed16 values from GetRgb2YccMatrix into the same nine 32-bit slots without a permutation or scaling: IPCS 0x33e104-0x33e130, IPCM 0x33fc84-0x33fcb0.',
                      'SetRgb2Ycc copies those nine slots sequentially into the SDK ycc_mat field: IPCS 0x33e700-0x33e758, IPCM 0x3401f0-0x340258.'],
            'evidence_level': 'Native software convention and standard coefficient interpretation; no pixel/channel probe was run in this task.',
        },
        'register_layout': layouts,
        'relation_to_chm': {
            'software_intended_pixel_order': 'RGB --YCC_MAT(Q8, 3x3)--> Y,Cb,Cr --CHM_MAT(Q7, 2x2 on Cb/Cr)--> Y,Cb_prime,Cr_prime',
            'stock_call_order_is_different': 'SetRgb2Ycc submits CHM selector 1 before selector 3 when both need an update. Register programming order does not establish pixel processing order.',
            'evidence': [
                'CColorProcess::EncodeCMtoD4CHMMAT 0x334c04-0x334ca4 writes fixed RGB-to-YCC B=[77,150,29;-43,-85,128;128,-107,-21] and separately converts the four chroma CM entries to CHM by signed rounding of CM/8.',
                'CColorProcess::EncodeCMtoD4YCMAT 0x334ca8-0x334e24 embeds those chroma combinations into the two RGB-to-chroma rows, preserves the Y row [77,150,29], and explicitly outputs CHM identity [128,0,0,128] at 0x334e08-0x334e1c.',
                'The two encoders express the same intended split/folded color transform at the two matrix boundaries; rounding can differ between them.',
            ],
            'pipeline_limit': 'The instruction evidence establishes software intent. Hardware intermediate clipping, rounding and the real pixel channel convention were not measured.',
            'integration_consequence': 'A complete source matrix written to selector 3 is still followed by any non-identity selector-1 CHM. To carry exactly that coefficient matrix as the combined software transform, pair it with CHM identity, or explicitly account for the fresh target CHM in the chosen base and avoid applying it again. Cache/restore both original matrix states together.',
        },
        'complete_source_sat_expressibility': {
            'source_evidence': str(source_path.relative_to(PROJECT)).replace('\\', '/'),
            'source_order': source_ops['order'],
            'preserves_nine_element_algorithm': 'Run the source mix, SAT scaling/reconstruction and HUE multiplication on all nine signed16 Q8 elements with the actual source stage rounding/wrap. Submit the resulting nine coefficients to selector 3 when all fit the target signed11 fields.',
            'nonzero_row_sums': 'The target setter accepts nine coefficients independently and imposes no zero-row-sum constraint. Source row-sum variants such as (-25,22) can be encoded through the three terms in each chroma row; they are not independent affine pixel offsets.',
            'why_selector1_is_insufficient': 'A 2x2 left transform of zero-sum chroma rows keeps zero row sums, whereas source variants can reconstruct nonzero sums. Per-element truncation also prevents an unconditional reduction to a single scalar/2x2 transform.',
            'remaining_source_dependencies': 'The correct base matrix and mode/style-dependent mix and row-sum selectors must come from the source recipe context. This target ABI inspection does not choose a variant or substitute target mode numbers.',
            'not_established': ['Every source matrix for every recipe fits the target signed11 range.',
                                'The same coefficients in two different cameras yield equivalent images.',
                                'This report implements or deploys the complete SAT runtime.'],
        },
        'execution_scope': 'Static inspection only for this extension; no new ARM/pixel execution, no runtime edits and no shared session changes.',
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=PROJECT / 'outputs/metol/ycc-abi.json')
    args = parser.parse_args()
    config = json.loads((PROJECT / 'project.local.json').read_text(encoding='utf-8-sig'))
    root = Path(config['rootfs'])
    functions, objects, sources, listings = {}, [], [], []
    iq_names = ['_Z14GetCc2CcMatrixv', '_Z16GetRgb2YccMatrixv',
                '_ZN13CColorProcess18EncodeCMtoD4CHMMATEPsS0_S0_',
                '_ZN13CColorProcess17EncodeCMtoD4YCMATEPsS0_S0_',
                '_ZN13CColorProcessC1Ev']
    for pipe in ('CIpcs', 'CIpcm'):
        cls = pipe + 'ColorD4'
        iq_names.extend((function_name(cls, 'GetRgb2YccIndex', 'PvRj'),
                         function_name(cls, 'SetRgb2Ycc', 'i'), f'_ZN{len(cls)}{cls}C1Ev'))
    for relative, names in [('usr/lib/libudd.so.3.1', ['ipcs_rgb2ycc_param', 'ipcm_rgb2ycc_param']),
                             ('usr/lib/libiq-fw-slpcam.so', iq_names)]:
        source = root / relative
        data = source.read_bytes()
        elf = ELFFile(io.BytesIO(data))
        symbols = all_symbols(elf)
        listing, metadata = disassemble(elf, symbols, names)
        listings.append('# Library: ' + relative + '\n' + listing)
        functions[relative] = metadata
        sources.append({'library': relative, 'path': str(source.resolve()), 'size_bytes': len(data)})
        if 'libiq-' in relative:
            for pipe in ('CIpcs', 'CIpcm'):
                for member in ('m_uiUv2Uv', 'm_iRgb2Yuv', 'm_uiUvGain'):
                    symbol = symbols[symbol_name(pipe + 'ColorD4', member)]
                    raw, section, offset = read_symbol(elf, symbol)
                    objects.append({'symbol': symbol.name, 'address': hex(symbol['st_value']),
                                    'size_bytes': len(raw), 'file_offset': hex(offset),
                                    'section': section, 'raw_hex': raw.hex(),
                                    'raw_i32': list(struct.unpack('<' + 'i' * (len(raw) // 4), raw))})
    sdk = PROJECT / config['sdk'] / 'usr/include/drime4/udd'
    asm_path = args.output.with_suffix('.asm')
    report = {
        'sources': sources,
        'headers': [sdk_excerpt(sdk / (pipe + '_submodule_type.h')) for pipe in ('ipcs', 'ipcm')],
        'disassembly': str(asm_path.resolve()), 'functions': functions, 'stock_table_objects': objects,
        'struct_abi': {
            'structs': ['ipcs_rgb2ycc', 'ipcm_rgb2ycc'], 'size_bytes': 76,
            'fields': [{'name': 'sel', 'offset_bytes': 0, 'width_bytes': 4},
                       {'name': 'chm_sup_sub', 'offset_bytes': 4, 'width_bytes': 4},
                       {'name': 'chm_sup_gain', 'offset_bytes': 8, 'width_bytes': 4},
                       {'name': 'chm_sup_th_dark', 'offset_bytes': 12, 'width_bytes': 4},
                       {'name': 'chm_sup_gain_dark', 'offset_bytes': 16, 'width_bytes': 4},
                       {'name': 'chm_sup_gain_dark_sat', 'offset_bytes': 20, 'width_bytes': 4},
                       {'name': 'chm_mat', 'offset_bytes': 24, 'count': 4, 'element_width_bytes': 4},
                       {'name': 'ycc_mat', 'offset_bytes': 40, 'count': 9, 'element_width_bytes': 4}],
            'selectors': {'CHM_SUP_BRIGHT': 0, 'CHM_MAT': 1, 'CHM_SUP_DARK': 2, 'YCC_MAT': 3},
            'declared_matrix_element_type': 'unsigned int; negative coefficients are supplied as 32-bit two-complement bit patterns',
        },
        'signed_coefficient_abi': {
            'status': 'Static instruction proof for both selectors; the separate ycc-abi-probe checks CHM range boundaries. The production chroma bridge probe also checks selector-3 default-matrix passthrough, not its range boundaries.',
            'applies_to': ['chm_mat[0..3]', 'ycc_mat[0..8]'],
            'accepted_signed_range': [-1024, 1023], 'packed_bits': 11,
            'acceptance_predicate_u32': 'value <= 0x3ff || value > 0xfffffbff',
            'packing': 'low 11 bits of each 32-bit input word, in 16-bit-spaced fields',
            'error_return': 2, 'success_return': 0,
            'evidence': {
                'ipcs_CHM_validation': '0x915cc-0x91620',
                'ipcm_CHM_validation': '0x47ec0-0x47f14',
                'ipcs_YCC_validation': '0x91810-0x91864',
                'ipcm_YCC_validation': '0x48144-0x48198',
            },
            'caution': 'A prepacked 11-bit negative such as 0x7c0 for -64 is rejected as positive 1984. Pass the full 32-bit signed pattern; setter masks it.',
        },
        'chm_register_packing': {
            'basis': 'Only offsets and masks derived from genuine libudd setter instructions.',
            'ipcs': {
                'base_symbol': 'ipcs_reg_base', 'evidence': '0x91624-0x91700',
                'writes': [{'offset': '0x1090', 'value': '(m[0]&0x7ff) | ((m[1]&0x7ff)<<16)'},
                           {'offset': '0x1094', 'value': '(m[2]&0x7ff) | ((m[3]&0x7ff)<<16)'}],
                'other_bits': 'These two register words are initialized to zero before packing.',
            },
            'ipcm': {
                'base_symbol': 'ipcm_reg_core_base', 'evidence': '0x47f18-0x4802c',
                'writes': [{'offset': '0x60', 'value': '(old&~0x07ff0000) | ((m[0]&0x7ff)<<16)'},
                           {'offset': '0x64', 'value': '(m[1]&0x7ff) | ((m[2]&0x7ff)<<16)'},
                           {'offset': '0x68', 'value': '(old&~0x7ff) | (m[3]&0x7ff)'}],
                'other_bits': '0x60 and 0x68 preserve unrelated fields; 0x64 starts at zero.',
            },
        },
        'selector3_nine_coefficient_finding': selector3_finding(objects),
        'fixed_point': {
            'chm': 'Stock software uses unity 128 and a four-coefficient chroma matrix, consistent with signed Q7.',
            'rgb_to_ycc': 'Stock software uses unity 256; base [77,150,29,-43,-85,128,128,-107,-21] is consistent with Q8 RGB-to-Y,Cb,Cr rows.',
            'evidence': ['CIpcsColor constructor 0x33df1c-0x33df2c and CIpcmColor constructor 0x33fac0-0x33facc initialize the chroma matrix to [128,0,0,128].',
                         'CColorProcess::EncodeCMtoD4YCMAT 0x334e08-0x334e1c explicitly outputs CHM identity [128,0,0,128].',
                         'CColorProcess::EncodeCMtoD4CHMMAT 0x334c58-0x334c9c divides source chroma coefficients by 8 with signed rounding and caps to -1024..1023.',
                         'SetRgb2Ycc compares UV gain to 128; other gains scale all four CHM coefficients with (product+64) LSR 7.'],
            'pixel_datapath': 'Hardware fractional-bit arithmetic, clipping location, channel order and rotation direction were not executed or measured by the RAM probe.',
        },
        'two_existing_color_controls': {
            'subindices': [6, 7],
            'decode': 'Stored subindex values >64 are reduced by 128; the resulting signed integers are multiplied by 2.',
            'effect': ['ycc_mat[5] += 2*subindex6_signed', 'ycc_mat[6] += 2*subindex7_signed'],
            'meaning': ['RGB-to-Cb coefficient for B is changed.', 'RGB-to-Cr coefficient for R is changed.'],
            'not_affine_offsets': 'Neither value is added as an independent constant U/V offset. Both alter RGB-dependent matrix terms.',
            'evidence': {'ipcs': '0x33e700-0x33e758; stack struct starts sp+4, ycc_mat starts sp+0x2c; modified words sp+0x40 and sp+0x44.',
                         'ipcm': '0x3401f0-0x340258; the same stack offsets and matrix elements.'},
        },
        'hue_design_finding': {
            'native_signed_uv_matrix_path': True,
            'conclusion': 'The native CHM_MAT ABI can carry both positive and negative cross coefficients required by a 2x2 chroma rotation. A separate HUE control can therefore be expressed at this matrix boundary without substituting the two existing color coefficient increments.',
            'candidate_composition': 'At the final CHM_MAT setter boundary, compose R(theta)*M_current using signed arithmetic and Q7 rounding, then pass 32-bit coefficients to the genuine setter after checking all four values are in range.',
            'rotation_example_coefficients': [111, -64, 64, 111],
            'example_meaning': 'A quantized mathematical 30-degree rotation if the hardware interprets the four coefficients as conventional row-major Q7; RAM verification only confirms acceptance/packing.',
            'upstream_negative_coefficient_hazard': 'Original SetRgb2Ycc saturation scaling uses logical LSR #7 and caps positive results at 255. Negative CHM entries inserted upstream are not preserved when UV gain !=128: e.g. (-64*140+64) as uint32 LSR7 becomes 33554362, then 255. Compose at the final setter boundary or replace that scaling with signed math.',
            'state_considerations': 'Always compose from a fresh unmodified stock matrix; changes, reset and refresh require integration at this boundary. The inspection does not implement that state management.',
            'remaining': ['Confirm U/V ordering and hue direction from pixel evidence before choosing a user-facing sign convention.',
                          'Integrate both selector-1 setters and preserve selector-0/2/3 behavior.',
                          'Check hardware effect and neutral pass-through with actual images in a separately authorized session.'],
        },
        'probe_artifact': 'outputs/metol/ycc-abi-probe.json',
        'camera_started': False, 'stock_files_modified': False, 'image_verified': False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    asm_path.write_text('\n'.join(listings), encoding='utf-8')
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(args.output.resolve()), 'disassembly': str(asm_path.resolve()),
                      'function_count': sum(len(items) for items in functions.values()),
                      'native_signed_uv_matrix_path': True}, indent=2))


if __name__ == '__main__':
    main()
