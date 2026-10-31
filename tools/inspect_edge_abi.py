"""Extract NX300M edge SDK layouts and stock UDD validation/register evidence.

Read-only static inspection. Source Q8 factors are evaluated against native
base-gain arithmetic, not against a reconstructed spatial pixel operator.
"""
import json
from pathlib import Path
import re

from inspect_gamma_hook import Stock
from inspect_recipe_tables import disassemble, function_name, symbol_name

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sdk_layout(path, pipe):
    text = path.read_text(encoding='utf-8', errors='replace')
    result = {}
    for kind, name in [('enum', pipe+'_edge_sel'), ('struct', pipe+'_edge_extraction'),
                       ('struct', pipe+'_edge_enhancement')]:
        match = re.search(r'\b'+kind+r'\s+'+name+r'\s*\{(.*?)\};', text, re.S)
        require(match is not None, f'Missing SDK declaration {name}')
        entry = {'name': name, 'first_line': text[:match.start()].count('\n')+1,
                 'last_line': text[:match.end()].count('\n')+1, 'text': match[0]}
        if kind == 'struct':
            fields = re.findall(r'^\s*(unsigned int|enum \w+)\s+(\w+)\s*;', match[1], re.M)
            entry.update(size_bytes=4*len(fields), fields=[
                {'name': field, 'declared_type': typ, 'offset_bytes': index*4, 'storage_bits': 32}
                for index, (typ, field) in enumerate(fields)])
        else:
            entry['values'] = {name: index for index, name in enumerate(re.findall(r'\b'+pipe.upper()+r'_EDGE_[AB]\b', match[1]))}
        result[name] = entry
    return {'path': str(path.relative_to(ROOT)).replace('\\', '/'), 'declarations': result}


def main():
    config = json.loads((ROOT/'project.local.json').read_text(encoding='utf-8-sig'))
    rootfs = Path(config['rootfs'])
    iq = Stock(rootfs/'usr/lib/libiq-fw-slpcam.so')
    udd = Stock(rootfs/'usr/lib/libudd.so.3.1')
    sources = [{'library': label, 'path': str(stock.path), 'size_bytes': stock.path.stat().st_size}
               for label, stock in [('libiq-fw-slpcam.so', iq), ('libudd.so.3.1', udd)]]
    sdk = ROOT/config['sdk']/'usr/include/drime4/udd'
    headers = [sdk_layout(sdk/(pipe+'_submodule_type.h'), pipe) for pipe in ('ipcs', 'ipcm')]
    source_report = ROOT/'outputs/metol/nx500-sharpness.json'
    source = json.loads(source_report.read_text(encoding='utf-8'))
    factors = source['sharpness_factors']['values']
    tables = {}
    for cls in ('CIpcsEdgeD4', 'CIpcmEdgeD4'):
        for member in ('m_iSharpness', 'm_nGain', 'm_nCoring', 'm_nLimit'):
            name = symbol_name(cls, member)
            if name in iq.symbols:
                table = iq.table(name, 1)
                table['values'] = [row[0] for row in table.pop('rows')]
                tables[name] = table
    gains = tables[symbol_name('CIpcsEdgeD4', 'm_nGain')]['values']
    native = tables[symbol_name('CIpcsEdgeD4', 'm_iSharpness')]['values']
    require(native == [factors[level] for level in range(2, 19, 2)], 'Unexpected native/source table relation')

    anchors = []

    def check(stock, address, mnemonic, operands):
        ins = stock.instruction(address)
        require((ins.mnemonic, ins.op_str) == (mnemonic, operands), f'Unexpected instruction at {address:#x}')
        anchors.append({'library': stock.path.name, 'address': hex(address),
                        'bytes': ins.bytes.hex(), 'instruction': ins.mnemonic+' '+ins.op_str})

    # Accepted gain range and actual packed width intentionally differ.
    for address, mnemonic, operands in [
        (0x91d4c, 'cmp', 'r3, #0xff'), (0x91d50, 'bhi', '#0x91ddc'),
        (0x91d5c, 'cmp', 'r3, #0xff'), (0x91d60, 'bhi', '#0x91ddc'),
        (0x91e80, 'and', 'r3, r3, #0x7f'), (0x91ea0, 'and', 'r3, r3, #0x7f'),
        (0x91fe8, 'and', 'r3, r3, #0x7f'), (0x92008, 'and', 'r3, r3, #0x7f'),
        (0x47928, 'cmp', 'r3, #0xff'), (0x4792c, 'bhi', '#0x479b8'),
        (0x47938, 'cmp', 'r3, #0xff'), (0x4793c, 'bhi', '#0x479b8'),
        (0x47a04, 'and', 'r3, r3, #0x7f'), (0x47a24, 'and', 'r3, r3, #0x7f'),
        (0x91d80, 'mvn', 'r3, #0x1000'), (0x4795c, 'mvn', 'r3, #0x1000'),
        (0x91d3c, 'cmp', 'r3, #4'), (0x47918, 'cmp', 'r3, #4')]:
        check(udd, address, mnemonic, operands)
    for address, mnemonic, operands in [
        (0x33db6c, 'mul', 'r3, r3, r0'), (0x33db88, 'lsr', 'r3, r3, #8'),
        (0x33db8c, 'str', 'r3, [sp, #0x1c]'), (0x33db90, 'str', 'r3, [sp, #0x20]'),
        (0x33f878, 'asr', 'r3, r3, #8'), (0x33f87c, 'str', 'r3, [sp, #0xc]'),
        (0x33f880, 'str', 'r3, [sp, #0x10]')]:
        check(iq, address, mnemonic, operands)

    bindings = []
    for expected, pic_load, pic_add, offset_load, dereference in [
        ('ipcs_reg_base', 0x91cf8, 0x91cfc, 0x91e00, 0x91e04),
        ('ipcm_reg_core_base', 0x478d4, 0x478d8, 0x47a9c, 0x47aa0)]:
        base_pool, delta = udd.literal(pic_load)
        got_base = (pic_add+8+delta)&0xffffffff
        offset_pool, offset = udd.literal(offset_load)
        got_slot = (got_base+offset)&0xffffffff
        relocation = udd.relocs[got_slot]
        require(relocation['symbol'] == expected and relocation['type'] == 21, 'Unexpected register-base GOT binding')
        check(udd, dereference, 'ldr', 'r3, [r4, r3]')
        bindings.append({'symbol': expected, 'symbol_address': hex(udd.symbols[expected]['st_value']),
                         'pic_load': hex(pic_load), 'pic_base_literal': hex(base_pool),
                         'got_base': hex(got_base), 'offset_literal': hex(offset_pool),
                         'dereference': hex(dereference), 'got_slot': hex(got_slot), 'relocation': relocation})

    fields = {}
    register_specs = {
        'ipcs_A': [('coring', 4, 0x10bc, 16, 12), ('limit_hi', 8, 0x10c4, 0, 13),
                   ('limit_lo', 12, 0x10c4, 16, 13), ('gain_plus', 16, 0x10c0, 4, 7),
                   ('gain_minus', 20, 0x10c0, 12, 7), ('shift', 24, 0x10c0, 0, 3)],
        'ipcs_B': [('coring', 4, 0x10b4, 8, 12), ('limit_hi', 8, 0x10b8, 8, 13),
                   ('limit_lo', 12, 0x10bc, 0, 13), ('gain_plus', 16, 0x10b4, 24, 7),
                   ('gain_minus', 20, 0x10b8, 0, 7), ('shift', 24, 0x10b4, 20, 3)],
        'ipcm': [('coring', 0, 0x44, 0, 12), ('limit_hi', 4, 0x48, 0, 13),
                 ('limit_lo', 8, 0x48, 16, 13), ('gain_plus', 12, 0x44, 16, 7),
                 ('gain_minus', 16, 0x44, 24, 7), ('shift', 20, 0x48, 29, 3)],
    }
    for pipe, specs in register_specs.items():
        fields[pipe] = [{'field': name, 'struct_offset': hex(offset), 'register_offset': hex(reg),
                         'lsb': lsb, 'width_bits': width, 'mask': hex(((1 << width)-1) << lsb)}
                        for name, offset, reg, lsb, width in specs]
    preview = [20*factor//256 for factor in factors]
    capture = [{'base_gain': base, 'values': [base*factor//256 for factor in factors]}
               for base in sorted(set(gains))]
    aliases = [{'levels': [level for level, gain in enumerate(preview) if gain == value], 'gain': value}
               for value in sorted(set(preview)) if preview.count(value) > 1]
    report = {
        'sources': sources, 'sdk_headers': headers,
        'analysis': 'Static original ELF and SDK inspection with instruction anchors and GOT binding checks. Numerical rows below are Python evaluations of reviewed native integer formulas, not original ARM execution or pixel results.',
        'source_factor_report': str(source_report.relative_to(ROOT)),
        'instruction_anchors': anchors, 'register_base_bindings': bindings, 'stock_tables': tables,
        'abi': {
            'ipcs': {'symbol': 'ipcs_edge_enhancement_param', 'address': '0x91cec',
                     'argument': 'r0=pointer to28-byte SDK struct; sel@0 is EDGE_A=0 or EDGE_B=1.',
                     'iq_stack_buffer': 'CIpcsEdgeD4::SetParam sp+0x0c; gain_plus at sp+0x1c and gain_minus at sp+0x20.',
                     'selectors': 'Initial call0x33dc04 uses sel0. The later call uses sel1; branch adjustments can change the two gains before that call.',
                     'field_pack_evidence': {'EDGE_A': '0x91e00..0x91f48', 'EDGE_B': '0x91f50..0x920b0'},
                     'register_base': 'ipcs_reg_base',
                     'write_preservation': 'A preserves unrelated bits in0x10bc but rebuilds0x10c0/0x10c4 from zero. B preserves unrelated bits in0x10b4 and0x10bc but rebuilds0x10b8 from zero.'},
            'ipcm': {'symbol': 'ipcm_edge_enhancement_param', 'address': '0x478c8',
                     'argument': 'r0=pointer to24-byte SDK struct; enhancement has no selector.',
                     'iq_stack_buffer': 'CIpcmEdgeD4::SetParam sp; gain_plus at sp+0x0c and gain_minus at sp+0x10.',
                     'setter_call': '0x33f8a8', 'field_pack_evidence': '0x479c0..0x47ac8',
                     'register_base': 'ipcm_reg_core_base', 'write_preservation': 'Both0x44 and0x48 are rebuilt from zero.'},
            'validation': {
                'gain_plus_and_minus': {'accepted_unsigned_range': [0, 255], 'packed_bits': 7,
                    'lossless_nonnegative_range': [0, 127], 'packing': 'value &0x7f; no saturation',
                    'counterexamples': [{'input': 128, 'packed': 0}, {'input': 255, 'packed': 127}],
                    'evidence': {'ipcs': '0x91d44..0x91d60; masks0x91e80,0x91ea0,0x91fe8,0x92008',
                                 'ipcm': '0x47920..0x4793c; masks0x47a04,0x47a24'}},
                'coring_and_limits': {'declared_type': 'unsigned int',
                    'accepted_twos_complement_range': [-4096, 4095],
                    'u32_predicate': 'value<=0xfff || value>0xffffefff',
                    'coring_packed_bits': 12, 'limit_hi_and_lo_packed_bits': 13,
                    'coring_alias': 'Accepted coring4095 and coring-1 both pack0xfff; accepted-4096 packs0.',
                    'sign_scope': 'Setter accepts full32-bit two-complement negatives and masks low bits. Hardware signed interpretation and coring semantic range are not proved by SDK unsigned declarations or register writes.',
                    'evidence': {'ipcs': '0x91d64..0x91dd8', 'ipcm': '0x47940..0x479b4'}},
                'shift': {'accepted_unsigned_range': [0, 4], 'packed_bits': 3,
                          'evidence': {'ipcs': '0x91d34..0x91d40', 'ipcm': '0x47910..0x4791c'}},
                'return': '0 on successful write;2 on invalid parameter/selector; bounds checked before any enhancement register writes.',
                'null_pointer': 'No null-pointer validation established; caller supplies a valid complete struct.'},
            'register_fields': fields,
        },
        'spatial_semantics': {
            'two_gain_fields': 'SDK gain_plus/gain_minus are plus-edge/minus-edge magnitude controls. They are not separate A/B paths, RGB channels, or source GS/GD/GH/WS/WD/WH fields.',
            'extraction': {
                'struct': 'Both pipes use24-byte extraction struct: sel,a_bpf_indx,b_bpf_indx,b_lpf_indx,b_sft,b_clp, all32-bit fields.',
                'selectors': 'EDGE_A=0 chooses A band-pass index; EDGE_B=1 uses B band-pass, B low-pass and B noise-reduction range/gain.',
                'validation': {'a_bpf_indx': [0, 3], 'b_bpf_indx': [0, 3], 'b_lpf_indx': [0, 2],
                               'b_sft': [0, 12], 'b_clp': [0, 128]},
                'ipcs_registers': [
                    {'selector': 0, 'field': 'a_bpf_indx', 'register': '0x10b0', 'lsb': 16, 'bits': 2},
                    {'selector': 1, 'field': 'b_bpf_indx', 'register': '0x10b0', 'lsb': 8, 'bits': 2},
                    {'selector': 1, 'field': 'b_lpf_indx', 'register': '0x10b0', 'lsb': 12, 'bits': 2},
                    {'selector': 1, 'field': 'b_sft', 'register': '0x10b0', 'lsb': 20, 'bits': 4},
                    {'selector': 1, 'field': 'b_clp', 'register': '0x10b4', 'lsb': 0, 'bits': 8}],
                'ipcm_register': '0x40: bit0 selects A(0)/B(1); A BPF bits2..3; B BPF bits4..5; B LPF bits6..7; B NR shift bits8..11 and clip bits12..19.',
                'evidence': {'ipcs': '0x91ab4..0x91cd8', 'ipcm': '0x476ec..0x478b4'}},
            'gain_luts': {'ipcs': 'ipcs_edge_lut(sel,bytes): copies256 bytes to ipcs_reg_base+0x3b00 for A or+0x3d00 for B;0x95268..0x95318.',
                          'ipcm': 'ipcm_edge_lut(bytes): copies256 bytes to ipcm_reg_core_base+0x700;0x4b2c4..0x4b330.'},
            'unknown': ['Spatial filter coefficients behind A/B BPF and B LPF indices.',
                        'Pixel color domain, exact neighborhood, edge sign convention, gain fixed-point unit and hardware clipping/rounding order.',
                        'Whether and how the two IPCS edge paths combine at pixel output.',
                        'Any bijection from source six EE gains to target A/B and positive/negative fields.']},
        'source21_native_adapter_feasibility': {
            'conclusion': 'All21 source Q8 factors140..460 can be injected into the reviewed native gain calculations without exceeding the lossless7-bit gain range, for the stored native base gains and existing target branches. This extends native gain strength choices; it does not port source six-field EE or establish equal sharpening.',
            'source_factors_q8': factors,
            'preview': {'formula': 'floor(20*factor/256), mode3 doubles and caps to255',
                        'ordinary_gain': preview, 'mode3_gain': [2*gain for gain in preview],
                        'maximum': max(preview)*2, 'ordinary_quantization_aliases': aliases},
            'capture': {'formula': 'u32(base_gain*factor)>>8', 'stored_base_gains': gains,
                        'initial_gain_rows': capture, 'minimum': min(min(row['values']) for row in capture),
                        'maximum': max(max(row['values']) for row in capture),
                        'later_branches': 'Existing target branches zero, halve or floor(2*gain/3); none increases the initial gain. Preserve call order and separate A/B updates.'},
            'percentage_scope': 'These bounds use source table factors without an extra percentage multiplier (source default100). Arbitrary source subindex5 overrides are not covered.',
            'integration_point': 'Replace the Q8 factor at each native consumer calculation, using the fresh native base/context before truncation and its original later branches. The node native index remains nine-valued; carrying21 source levels needs separate control state or a deliberate consumer modification.',
            'setter_only_counterexample': 'A setter-only hook cannot generally reconstruct fresh base_gain from quantized gain: factor160 with base16 or17 yields10 in both cases, but factor460 should yield28 or30. Scaling the received10 loses this distinction.',
            'policy': 'Preserve neutral source level10=256; do not prepack source gains into target fields, merge plus/minus with A/B, or describe factor reuse as six-field/source spatial equivalence.',
            'unknown': 'No runtime implementation, original-ARM arithmetic execution, live register write or pixel validation is part of this inspection.'},
    }
    # Numeric bounds are calculations over extracted table values, not new tests
    # of an independently modeled image operator.
    require(report['source21_native_adapter_feasibility']['preview']['maximum'] <= 127,
            'Source factor extension exceeds preview lossless gain width')
    require(report['source21_native_adapter_feasibility']['capture']['maximum'] <= 127,
            'Source factor extension exceeds capture lossless gain width')
    listings, functions = [], {}
    names = [f'{pipe}_edge_{kind}' for pipe in ('ipcs', 'ipcm')
             for kind in ('enhancement_param', 'extraction_param', 'lut')]
    text, functions['libudd.so.3.1'] = disassemble(udd.elf, udd.symbols, names)
    listings.append('# Library: libudd.so.3.1\n'+text.replace('stock libiq-fw-slpcam.so', 'stock libudd.so.3.1'))
    names = [function_name(cls, method, args) for cls in ('CIpcsEdgeD4', 'CIpcmEdgeD4')
             for method, args in [('GetIndex', 'Pv'), ('SetParam', 'j')]]
    text, functions['libiq-fw-slpcam.so'] = disassemble(iq.elf, iq.symbols, names)
    listings.append('# Library: libiq-fw-slpcam.so\n'+text)
    report['functions'] = functions
    output = ROOT/'outputs/metol/edge-abi'
    output.with_suffix('.asm').write_text('\n'.join(listings), encoding='utf-8')
    output.with_suffix('.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'outputs': [str(output.with_suffix(ext).relative_to(ROOT)) for ext in ('.json', '.asm')],
                      'instruction_anchors_checked': len(anchors), 'register_base_bindings': bindings,
                      'gain_input_range': [0, 255], 'gain_packed_bits': 7,
                      'source21_preview_max': max(preview)*2,
                      'source21_capture_max': report['source21_native_adapter_feasibility']['capture']['maximum'],
                      'preview_aliases': aliases}, indent=2))


if __name__ == '__main__':
    main()
