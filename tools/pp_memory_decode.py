"""Decode a stock PP memory-pass register snapshot without treating it as bypass."""
import argparse
import json
from pathlib import Path
import re
import struct

PROJECT = Path(__file__).resolve().parents[1]
REGS = PROJECT/'work/vendor-source/TIZEN/packages_nx300m/linux/drivers/media/video/drime4/pp/pp_core/d4_pp_core_regs.h'


def decode(path, cs_source=None, compare=None):
    data = path.read_bytes()
    if len(data) != 0xb000:
        raise ValueError('Expected the complete 0xb000-byte PP register bank')
    text = REGS.read_text(encoding='utf-8')
    offsets = {name: int(value, 16) for name, value in re.findall(
        r'^#define\s+(PP_\w+)\s+(0x[0-9A-Fa-f]+)\s*$', text, re.M)}
    fields = re.findall(r'#define\s+(\w+)\(val(?:, x)?\)\s*\\\s*\n\s*'
                        r'(?:SET|GET)_REGISTER_VALUE\(val,\s*(?:x,\s*)?(\d+),\s*(\d+)\)', text)

    def word(offset):
        return struct.unpack_from('<I', data, offset)[0]

    def block(name, address, alias=None):
        value = word(address)
        prefix = alias or name
        decoded = {}
        for field, start, bits in fields:
            matched = next((form for form in ('D4_'+prefix+'_', prefix+'_')
                            if field.startswith(form)), None)
            if matched and '_GET_' not in field:
                start, bits = int(start), int(bits)
                decoded[field.removeprefix(matched).lower()] = (value >> start) & ((1 << bits)-1)
        return {'address': f'0x{address:04x}', 'value': f'0x{value:08x}', 'fields': decoded}

    processing = {}
    names = ['DPC_COMMON_CTRL', 'SMEAR_CTRL', 'XYS_CTRL_1', 'HBR_CTRL', 'VBR_CTRL',
             'BG_CTRL', 'HRN_CTRL', 'VFPN_CTRL', 'CS_CTRL', 'DAMP_CTRL',
             'OBD1_CTRL', 'OBD2_CTRL', 'OBD3_CTRL', 'OOC1_CTRL', 'OOC2_CTRL',
             'OBR1_CTRL', 'OBR2_CTRL', 'KNEE_CTRL', 'DEKNEE_CTRL']
    # Each entry names an actual enable field in the GPL register macros.
    # DEKNEE has no released field macro: stock bit_shift_on/off supplies the
    # missing bit-0 evidence. Do not infer enables from arbitrary words.
    enable_fields = {
        'DPC_COMMON_CTRL': 'on', 'SMEAR_CTRL': 'smear_on', 'XYS_CTRL_1': 'xys_on',
        'HBR_CTRL': 'on', 'VBR_CTRL': 'on', 'BG_CTRL': 'bg_on', 'HRN_CTRL': 'on',
        'VFPN_CTRL': 'vfpn_on', 'CS_CTRL': 'cs_on', 'DAMP_CTRL': 'damp_on',
        'OBD1_CTRL': 'obd_on', 'OBD2_CTRL': 'obd_on', 'OBD3_CTRL': 'obd_on',
        'OOC1_CTRL': 'ooc_on', 'OOC2_CTRL': 'ooc_on', 'OBR1_CTRL': 'obr_on',
        'OBR2_CTRL': 'obr_on', 'KNEE_CTRL': 'knee_on',
    }
    for name in names:
        key = 'PP_CORE_'+name
        alias = re.sub(r'(OBD|OOC|OBR)[123]', r'\1', key)
        item = block(key, 0x4000+offsets[key], alias)
        if name == 'DEKNEE_CTRL':
            item['enabled'] = bool(word(0x4550)&1)
            item['operation'] = 'RDMA bit shift (stock API name)'
            item['enable_evidence'] = 'libudd pp_dd_rdma_bit_shift_on 0x26310 OR bit0; pp_dd_rdma_bit_shift_off 0x26360 BIC bit0'
        else:
            field = enable_fields[name]
            item['enabled'] = bool(item['fields'][field])
            item['enable_evidence'] = f'GPL {alias} field {field}'
        processing[name.lower()] = item
    enabled = [name for name, item in processing.items() if item['enabled']]
    rd = word(0x3000)
    wd = word(0x3314)
    # libudd pp_dd_rdma_set_secc_info branches for PP_CH_4 at 0x2541c:
    # width_0 = width_1 = image_width >> 2; height stays full image height.
    mode = (word(0x3038)>>4)&7
    widths = [word(0x3004)&8191, (word(0x3004)>>16)&8191]
    height = word(0x3008)>>16
    full_width = widths[0]*4 if mode == 7 and widths[0] == widths[1] else None
    components = {'00': (0x300c, 0x3304, 0x3014, 0x330c),
                  '01': (0x3018, 0x3324, 0x3020, 0x332c),
                  '10': (0x3010, 0x3308, 0x3014, 0x330c),
                  '11': (0x301c, 0x3328, 0x3020, 0x332c)}
    planes = {name: {'rdma_address': f'0x{word(ra):08x}', 'wdma_address': f'0x{word(wa):08x}',
                     'rdma_stride': word(rs), 'wdma_stride': word(ws),
                     'width': full_width//2 if full_width else None, 'rows': height//2}
              for name, (ra, wa, rs, ws) in components.items()}
    cs_input = word(0x42d8)
    cs_sample = (word(0x42f0)>>16)&255
    cs_width, cs_height = cs_input&8191, (cs_input>>16)&8191
    cs_columns = ((cs_width+cs_sample-1)//cs_sample+1) if cs_sample else None
    cs_rows = (((cs_height+cs_sample-1)//cs_sample+2)&~1) if cs_sample else None
    cs_units = (word(0x42e0)>>8)&8191
    cs_info = {
        'sample_divider': block('PP_CORE_CS_SAMPLE_DIVIDER', 0x42d4),
        'input_size': block('PP_CORE_CS_INPUT_SIZE', 0x42d8),
        'start_position': block('PP_CORE_CS_START_POSITION', 0x42dc),
        'interpolation': block('PP_CORE_CS_INTERPOLATION_RATIO', 0x42e0),
        'lut_info': block('PP_CORE_CS_LUT_INFO', 0x42f0),
        'input_width': cs_width, 'input_height': cs_height,
        'sample_pixels': cs_sample, 'grid_columns': cs_columns, 'grid_rows_even_padded': cs_rows,
        'register_size_units_16_bytes': cs_units, 'register_size_bytes': cs_units*16,
        'geometry_size_bytes': cs_columns*cs_rows*8 if cs_columns and cs_rows else None,
        'source1_physical_address': f'0x{word(0x4608):08x}',
        'source2_physical_address': f'0x{word(0x461c):08x}',
        'source_layout': '8 bytes per node: four little-endian u16 coefficient lanes; spatial rows 0,2,...,1,3,...',
        'producer_unity_coefficient': 8192,
        'coefficient_color_lane_order_verified': False,
        'snapshot_is_current_on_chip_lut': False,
        'size_note': 'Original API size is bytes; 42e0 stores size >> 4 and loses the low four bits. Geometry independently gives the aligned size for this configuration.',
        'identity_note': 'Source allocation may be reused after upload. Capture bytes before the upload API call to identify its input.',
    }
    if cs_source is not None:
        lut = cs_source.read_bytes()
        expected = cs_info['geometry_size_bytes']
        if expected is None or len(lut) != expected:
            raise ValueError(f'CS source must contain exactly {expected} bytes for this bank, got {len(lut)}')
        words = struct.unpack('<'+'H'*(len(lut)//2), lut)
        cs_info['source_snapshot'] = str(cs_source)
        cs_info['source_statistics'] = {
            f'lane{i}': {'minimum': min(words[i::4]), 'maximum': max(words[i::4]),
                        'distinct_values': len(set(words[i::4])),
                        'unity_coefficients': words[i::4].count(8192)} for i in range(4)
        }
        cs_info['source_all_lanes_identical'] = all(words[i:i+4] == (words[i],)*4
                                                   for i in range(0, len(words), 4))
        cs_info['source_constant_unity'] = all(value == 8192 for value in words)
        cs_info['source_layout_distinguishable_from_values'] = len(set(words)) > 1
    result = {
        'snapshot': str(path), 'snapshot_bytes': len(data),
        'evidence': {'register_fields': str(REGS.relative_to(PROJECT)),
                     'stock_dma_setters': 'outputs/firmware-runtime/pp-memory-dma-config.asm',
                     'stock_correction_setters': 'outputs/firmware-runtime/pp-memory-corrections-config.asm'},
        'common': {'control_1': block('PP_CORE_COMMON_CTRL_1', 0),
                   'control_2': block('PP_CORE_COMMON_CTRL_2', 8)},
        'dma': {'rdma': block('PP_RDMA_CTRL', 0x3000),
                'rdma_channel_mode': mode, 'width_register_fields': widths,
                'full_width': full_width, 'full_height': height,
                'rdma_format_codes': [(rd>>4)&15, (rd>>8)&15],
                'wdma': block('PP_WDMA_CTRL', 0x3314),
                'wdma_save_each_color': bool(wd&1), 'planes': planes},
        'processing': processing, 'enabled_modules': enabled,
        'cs_lut': cs_info,
        'bypass': not enabled,
        'dpc_dynamic': {'static_enabled': bool(word(0x4000)&2), 'dynamic_enabled': bool(word(0x4000)&4),
                        'hot_pixel_control': f'0x{word(0x4040):08x}',
                        'hot_pixel_control_fields': {
                            key: value for key, value in block('PP_CORE_DPC_HP', 0x4040)['fields'].items()
                            if key in ('threshold', 'correction_mode', 'except_static_defect')},
                        'range_words': [f'0x{word(a):08x}' for a in range(0x4044, 0x4064, 4)],
                        'gain_words': [f'0x{word(a):08x}' for a in range(0x4064, 0x40a4, 4)]},
        'damp': {'offset_words': [f'0x{word(a):08x}' for a in (0x4314, 0x4318)],
                 'gain_words': [f'0x{word(a):08x}' for a in (0x431c, 0x4320)],
                 'pixel_arithmetic_verified': False},
        'output_quantization': {
            'wdma_bit_format_code': (wd>>4)&7,
            'wdma_storage_bits': {0:8, 1:10, 2:12, 3:14, 4:16}.get((wd>>4)&7),
            'wdma_bits_per_clock_ports': [word(0x3300), word(0x3320)],
            'knee_enabled': bool(word(0x4540)&1),
            'knee_output_code': (word(0x4540)>>8)&3,
            'knee_output_bits_when_enabled': {0:12, 1:10, 2:8}.get((word(0x4540)>>8)&3),
            'knee_code_note': 'KNEE codes differ from enum pp_bit_width; disabled KNEE output width behavior is not specified.',
            'explicit_wdma_shift_or_round_field_found': False,
            'scope': 'Released PP register definitions and stock WDMA/KNEE setters only.',
            'stock_evidence': 'outputs/firmware-runtime/pp-memory-output-width.asm; pp_dd_wdma_set_secc_info in pp-memory-dma-config.asm',
            'pixel_rule_verified': False,
            'missing': 'Which internal bits a 12-bit WDMA format retains, clipping/rounding, and disabled-KNEE output behavior.'},
        'rdma_bit_shift': {'stock_function': 'pp_dd_rdma_bit_shift_on',
                           'control': f'0x{word(0x4550):08x}',
                           'boundary_words': [f'0x{word(a):08x}' for a in range(0x4504, 0x4518, 4)],
                           'gradient_words': [f'0x{word(a):08x}' for a in range(0x4518, 0x452c, 4)],
                           'offset_words': [f'0x{word(a):08x}' for a in range(0x452c, 0x4540, 4)],
                           'linear_12_to_14_model': 'value << 2; isolated helper only, physical pixel behavior unverified'},
        'lut_dma': {key.removeprefix('PP_CORE_MDMA_').lower(): f'0x{word(0x4000+offset):08x}'
                    for key, offset in offsets.items() if key.startswith('PP_CORE_MDMA_')},
        'unresolved': [
            'Dynamic DPC neighborhood decision/replacement and fixed-point behavior are unavailable in the driver header.',
            ('The captured CS upload source is available; sampler/interpolation and fixed-point behavior remain unimplemented.'
             if cs_source is not None else
             'CS uses LUT contents outside this bank; sampler/interpolation and fixed-point behavior remain unimplemented.'),
            'The captured MnF input/output height is 3696; this PP read is 3714. The final 9 rows per plane were not written by that split. Their intended producer and optical-black semantics are unverified.',
            'Register names and a plausible bit-shift model do not prove bit-exact physical hardware arithmetic.'
        ],
        'complete_pp_pixels_implemented': False, 'completion_irq_generated': False,
    }
    if compare is not None:
        previous = compare.read_bytes()
        if len(previous) != len(data):
            raise ValueError('Comparison requires another complete PP register bank')
        differences = []
        for address in range(0, len(data), 4):
            old = struct.unpack_from('<I', previous, address)[0]
            new = word(address)
            if old != new:
                differences.append({'address': f'0x{address:04x}',
                                    'previous': f'0x{old:08x}', 'current': f'0x{new:08x}'})
        result['comparison'] = {
            'previous_snapshot': str(compare), 'word_differences': differences,
            'processing_page_equal': previous[0x4000:0x5000] == data[0x4000:0x5000],
            'dma_configuration_equal': previous[0x3000:0x3400] == data[0x3000:0x3400],
            'common_control_equal': previous[:12] == data[:12],
            'note': 'Raw differences alone do not assign a semantic meaning to common/3A result words.'
        }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--cs-source', type=Path, help='Optional raw CS upload-source bytes, not a rendered image')
    parser.add_argument('--compare', type=Path, help='Previous full PP bank to compare by register word')
    args = parser.parse_args()
    result = decode(args.snapshot, args.cs_source, args.compare)
    encoded = json.dumps(result, indent=2)+'\n'
    if args.output:
        args.output.write_text(encoded, encoding='utf-8')
    print(json.dumps({'enabled_modules': result['enabled_modules'], 'bypass': result['bypass'],
                      'full_width': result['dma']['full_width'], 'full_height': result['dma']['full_height'],
                      'complete_pp_pixels_implemented': False}, indent=2))


if __name__ == '__main__':
    main()
