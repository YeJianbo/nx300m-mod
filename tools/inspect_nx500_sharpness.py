"""Extract NX500 1.12 PW Sharpness consumption from the original P7 bytes.

Writes a bounded static evidence report and ARM listing. No firmware execution,
pixel operator, runtime adaptation or camera deployment is performed.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import sys

os.environ.setdefault('CONDA_PREFIX', sys.prefix)
from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM

ROOT = Path(__file__).resolve().parents[1]
BASE = 0x80000000
SOURCE_SHA256 = 'f8e417cb5b55768851ba7e2b8017d3a98dc0b0c8444cc0a5061265cc08dbbf7f'
SPANS = (
    ('PW Sharpness IQ decoder', 0x3863c0, 0x3864cc),
    ('Parameter pack: Sharpness at +0x88', 0x385800, 0x385844),
    ('Nested pack: Sharpness at outer +0x98', 0x38d050, 0x38d068),
    ('CIpcEeD5 GetIndex', 0x397e8c, 0x398080),
    ('CIpcEeD5 constructor', 0x3980bc, 0x39811c),
    ('CIpcEeD5 text loader: named gains', 0x39ca00, 0x39cb04),
    ('Text loader: EE submission', 0x39d6cc, 0x39d700),
    ('CIpcEeD5 SetParam, including base interpolation and six gain stores', 0x3a209c, 0x3a2e58),
    ('D5 EE/detail/control wrappers', 0x17cb4c, 0x17cc24),
    ('D5 EE/detail/control writers', 0x49d828, 0x49d99c),
)
# Key load and conditional store addresses are connected by the text parser's
# return value; names establish packed field meaning independently of SetParam.
FIELDS = (
    ('EE_GAIN_GS', 4, 8, 0x39ca10, 0x39ca3c, 0x3a25d4),
    ('EE_GAIN_GD', 5, 8, 0x39ca50, 0x39ca7c, 0x3a25fc),
    ('EE_GAIN_GH', 6, 7, 0x39ca34, 0x39ca60, 0x3a2640),
    ('EE_GAIN_WS', 8, 8, 0x39ca90, 0x39cabc, 0x3a2664),
    ('EE_GAIN_WD', 9, 8, 0x39cad0, 0x39cafc, 0x3a268c),
    ('EE_GAIN_WH', 10, 7, 0x39cab4, 0x39cae0, 0x3a26f0),
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def signed(value, width):
    value &= (1 << width)-1
    return value-(1 << width) if value & (1 << (width-1)) else value


def decode_scalar(raw):
    current, lower, upper = signed(raw >> 20, 12), signed(raw >> 10, 10), signed(raw, 10)
    numerator = (current-lower)*20
    denominator = upper-lower
    level = (abs(numerator)//abs(denominator) *
             (-1 if (numerator < 0) != (denominator < 0) else 1)) if denominator else 10
    return {'current': current, 'lower': lower, 'upper': upper, 'decoded_level': level}


def main():
    source = ROOT/'work/reference/nx500-stock-1.12/partition-05.bin'
    data = source.read_bytes()
    actual_sha = hashlib.sha256(data).hexdigest()
    require(actual_sha == SOURCE_SHA256, 'Unexpected NX500 original partition identity')
    decoder = Cs(CS_ARCH_ARM, CS_MODE_ARM)
    decoder.skipdata = True

    def word(offset):
        return struct.unpack_from('<I', data, offset)[0]

    def string(address):
        offset = address-BASE
        return data[offset:data.index(b'\0', offset)].decode('ascii')

    def instruction(offset, mnemonic=None, operands=None):
        result = next(decoder.disasm(data[offset:offset+4], BASE+offset))
        require(mnemonic is None or result.mnemonic == mnemonic, f'Unexpected opcode at {offset:#x}')
        require(operands is None or result.op_str == operands, f'Unexpected operands at {offset:#x}')
        return result

    def literal_string(offset):
        ins = instruction(offset, 'ldr')
        match = re.fullmatch(r'r1, \[pc, #(0x[0-9a-f]+|[0-9]+)\]', ins.op_str)
        require(match is not None, f'Unexpected string literal load at {offset:#x}')
        pointer = word(offset+8+int(match[1], 0))
        return pointer, string(pointer)

    iq_name = string(word(0x5758e0+4*0x56))
    require(iq_name == 'eIQ_ID_PW_SHARPNESS', 'Unexpected IQ slot name')
    require(string(BASE+0x6f8540) == 'VARIABLE_PWSHARPNESS', 'Unexpected variable name')
    instruction(0x38641c, 'mov', 'r1, #0x56')
    instruction(0x38583c, 'bl', '#0x803863c0')
    instruction(0x385840, 'str', 'r0, [r4, #0x88]')
    instruction(0x397ea4, 'ldr', 'sl, [r1, #0x88]')
    instruction(0x397f1c, 'movhs', 'sl, #0x14')
    instruction(0x397f30, 'moveq', 'sb, #0x64')
    instruction(0x397f8c, 'mov', 'r2, #0')
    instruction(0x397ff4, 'mov', 'r2, #5')
    typeinfo = word(0x635784)
    class_name = string(word(typeinfo-BASE+4))
    require(class_name == '8CIpcEeD5', 'Unexpected node RTTI')
    require(word(0x6357a0) == BASE+0x3a209c and word(0x6357a4) == BASE+0x397e8c,
            'Unexpected virtual consumer functions')

    instruction(0x3a250c, 'movw', 'r6, #0x5780')
    instruction(0x3a2510, 'movt', 'r6, #0x8063')
    instruction(0x3a2514, 'add', 'r1, r6, r5, lsl #2')
    instruction(0x3a2520, 'ldr', 'r1, [r1, #0x3c4]')
    factor_address = 0x80635780+0x3c4
    factors = list(struct.unpack_from('<21I', data, factor_address-BASE))
    require(factors[10] == 256, 'Unexpected neutral Q8 factor')
    fields = []
    for name, offset, width, key_load, loader_store, consumer_store in FIELDS:
        label_address, label = literal_string(key_load)
        require(label == name, f'Unexpected gain label {label}')
        instruction(loader_store, 'strbne', f'r3, [sp, #{0x44+offset:#x}]')
        store = instruction(consumer_store, 'strb')
        require(store.op_str.endswith(f'[sp, #{0xb4+offset:#x}]'), 'Unexpected consumer gain offset')
        fields.append({'name': name, 'ee_buffer_offset': hex(offset), 'bits': width,
                       'bit_position': 0, 'maximum': (1 << width)-1,
                       'saturation_product_threshold': hex(1 << (width+8)),
                       'label_address': hex(label_address),
                       'loader_key_load': hex(BASE+key_load),
                       'loader_store': hex(BASE+loader_store),
                       'consumer_store': hex(BASE+consumer_store),
                       'other_bits': 'Preserved by BFI' if width == 7 else 'Whole byte replaced'})

    source_pw_path = ROOT/'outputs/metol/nx500-pw-source.json'
    source_pw = json.loads(source_pw_path.read_text(encoding='utf-8'))
    levels = []
    for entry in source_pw['scalar_encoding']['entries']:
        raw = int(entry['raw'], 16)
        level = entry['value']
        require(raw == ((((16*level-145) & 0xffff) << 16) | 0xd80a), 'Unexpected scalar encoding')
        decoded = decode_scalar(raw)
        require(decoded['decoded_level'] == level, 'Source scalar does not decode to original level')
        levels.append({'level': level, 'raw': entry['raw'], **decoded, 'factor_q8': factors[level]})

    target_path = ROOT/'outputs/metol/recipe-native-tables.json'
    target = json.loads(target_path.read_text(encoding='utf-8'))
    comparisons = []
    for item in target['tables']:
        if item['member'] != 'm_iSharpness' or item['class'] not in ('CIpcsEdgeD4', 'CIpcmEdgeD4'):
            continue
        target_values = item['raw_values']
        matches = [{'source_level': level, 'target_index': index, 'target_setting': index-4,
                    'factor_q8': factor} for level, factor in enumerate(factors)
                   for index, target_factor in enumerate(target_values) if factor == target_factor]
        comparisons.append({'target_class': item['class'], 'target_address': item['address'],
                            'target_factors_q8': target_values, 'exact_matches': matches,
                            'source_levels_without_exact_factor_match': [level for level in range(21)
                                if not any(m['source_level'] == level for m in matches)],
                            'target_consumer_evidence': item['interpretation']['index_evidence']})
    require(len(comparisons) == 2, 'Expected both target Edge table reports')
    target_asm_path = ROOT/'outputs/metol/recipe-native-tables.asm'
    target_asm = target_asm_path.read_text(encoding='utf-8')
    target_sections = []
    for symbol in ('_ZN11CIpcsEdgeD48SetParamEj', '_ZN11CIpcmEdgeD48SetParamEj'):
        start = target_asm.index('## '+symbol+' ')
        end = target_asm.find('\n## ', start+3)
        target_sections.append(target_asm[start:end if end >= 0 else len(target_asm)].strip())

    report = {
        'source': str(source.relative_to(ROOT)), 'source_sha256': actual_sha, 'load_base': hex(BASE),
        'analysis': 'Original NX500 bytes extracted and disassembled statically; target comparison uses existing NX300M table and assembly reports.',
        'iq': {'variable_id': '0x110', 'variable_name': 'VARIABLE_PWSHARPNESS', 'iq_id': '0x56',
               'iq_name': iq_name, 'decoder': '0x803863c0', 'ordinary_capture_modes': [1, 2, 3, 4, 8],
               'normalization': 'Signed current=raw[31:20], lower=raw[19:10], upper=raw[9:0]; trunc_zero((current-lower)*20/(upper-lower)), or10 when bounds coincide.',
               'encoding': '(((16*level-145)&0xffff)<<16)|0xd80a',
               'source_scalar_report': str(source_pw_path.relative_to(ROOT)), 'levels': levels,
               'other_modes': 'Default10 or separate SmartArt IQ0x98 path; not part of ordinary PW normalization.'},
        'consumer_chain': [
            '0x8038583c calls decoder; 0x80385840 writes pack+0x88. Another call/store at0x8038d060/64 writes outer+0x98 for a nested pack starting at+0x10.',
            'CIpcEeD5 GetIndex0x80397e8c reads pack+0x88, caps unsigned level to20 and stores it as subindex0 at0x80397f84..94.',
            'GetIndex reads pack+0xbc flag; nonzero selects pack+0xc0, zero selects100 (0x80397f20..30). This becomes subindex5 at0x80397fec..ff8.',
            'SetParam0x803a209c reads subindex0 into sp+0x30 and subindex5 into sp+0x34 at0x803a20a8..bc and0x803a2110..24.',
            '0x803a2508..20 loads Sharpness21[level]. 0x803a252c..70 computes the percentage-adjusted Q8 factor before six separate gain multiplications.',
            'Six results enter packed EE buffer sp+0xb4. Mode-specific helpers0x8039811c or0x8039d838 run before submission; their complete write sets are not audited here.',
            '0x803a2c7c calls d5_ipc_set_subparam_ee(channel, sp+0xb4), followed by EE control, detail and another parameter setter.',
        ],
        'node': {'rtti': class_name, 'typeinfo': hex(typeinfo), 'vtable': '0x80635788',
                 'set_param_slot': '0x806357a0', 'set_param': '0x803a209c',
                 'get_index_slot': '0x806357a4', 'get_index': '0x80397e8c',
                 'other_subindices': 'Subindices1..4 select other state-dependent parameter rows; subindex6 is a clamped -7..7 local interpolation remainder derived from pack+0x28. Sharpness is not the only EE input.'},
        'sharpness_factors': {'address': hex(factor_address), 'file_offset': hex(factor_address-BASE),
                              'encoding': '21 little-endian unsigned32 Q8 factors', 'neutral_level': 10,
                              'values': factors},
        'gain_arithmetic': {
            'percentage_subindex': 5, 'default_percentage': 100,
            'percentage_instruction_evidence': ['0x80397ef8 ldrb r3,[r5,#0xbc]', '0x80397f20 cmp r3,#0',
                '0x80397f28 ldrne sb,[r5,#0xc0]', '0x80397f30 moveq sb,#100'],
            'step1': 'product32=(factor_q8*percentage)&0xffffffff; effective_q8=((product32*0x51eb851f)>>32)>>5 = floor(product32/100). UMULL treats product32 as unsigned.',
            'step2_exact_pseudocode': 'p=signed32(base_gain*effective_q8); if p >= (1<<(bits+8)): value=(1<<bits)-1; else: adjusted=(p+255)&0xffffffff if p<0 else p; value=(adjusted>>8)&((1<<bits)-1). MUL wraps to32 bits; threshold comparison is signed; UBFX extracts bits8..(7+bits).',
            'step2_nonnegative_nonoverflow_domain': 'min((1<<bits)-1, floor(base_gain*effective_q8/256))',
            'order': 'Percentage division occurs first, then separate gain multiplication and truncation/saturation. Do not merge denominators or rounding steps.',
            'ee_buffer_stack_offset': '0xb4', 'fields': fields,
            'base_gain_scope': 'Selected/interpolated base gains and neighboring EE fields depend on other subindices. This report does not reconstruct all base rows or all intermediate interpolation.'},
        'submission': {
            'ee': {'wrapper': '0x8017cbdc', 'log_string': string(BASE+0x721bcc).rstrip('\n'),
                   'backend': '0x8049d914', 'abi': 'r0=channel, r1=packed EE buffer pointer',
                   'context_pointer_table': hex(word(0x49d994)),
                   'copies': [{'source_offset': '0x0', 'destination_context_offset': '0x1304', 'bytes': '0x394'},
                              {'source_offset': '0x394', 'destination_context_offset': '0x1700', 'bytes': '0x200'}]},
            'control': {'wrapper': '0x8017cb94', 'backend': '0x8049d8c8', 'context_offset': '0x1300'},
            'detail': {'wrapper': '0x8017cb4c', 'backend': '0x8049d828', 'context_offsets': '0x1298..0x12ac, six words'},
            'scope': 'Parameter/context writes are established; the context pointer target and spatial ISP pixel operation are not reconstructed.'},
        'nx300m_comparison': {
            'table_report': str(target_path.relative_to(ROOT)), 'assembly_report': str(target_asm_path.relative_to(ROOT)),
            'results': comparisons,
            'capture_abi': {'consumer': 'CIpcsEdgeD4::SetParam0x33da7c',
                'initial_gain': 'u32(Sharpness[subindex1]*Gain[subindex3])>>8 at0x33db5c..88',
                'fields': '32-bit words sp+0x1c and sp+0x20; offsets+0x10/+0x14 relative to enhancement buffer sp+0x0c.',
                'setter': 'ipcs_edge_enhancement_param@plt0x5a7f4; r0=buffer pointer; call sites0x33dc04,0x33dc34,0x33dccc.',
                'later_changes': 'May zero both gains (object+0x88 >50 or flag at+0x7d), halve them, or apply floor(2*gain/3) according to object+0x2c branches. First setter call at0x33dc04 precedes some later adjustments.'},
            'preview_abi': {'consumer': 'CIpcmEdgeD4::SetParam0x33f7b0',
                'initial_gain': 'trunc_zero(20*Sharpness[subindex1]/256) at0x33f85c..80; mode3 doubles and unsigned-caps to255.',
                'fields': '32-bit words sp+0x0c and sp+0x10; offsets+0x0c/+0x10 relative to enhancement buffer sp.',
                'setter': 'ipcm_edge_enhancement_param@plt0x5ea3c at0x33f8a8; r0=buffer pointer.'},
            'adaptation': 'Exact shared Q8 factor values can be reused at source levels2,4,...18. To support all21 factors on NX300M, a separate target gain adapter must reproduce target preview/capture base gains and branches while injecting the chosen source factor.',
            'missing': ['Target enhancement setters are imports in libiq-fw-slpcam.so; backing implementation, accepted value range and field semantics are not established by these call sites.',
                        'No mapping proves source six packed EE gains equivalent to target two word gains.',
                        'Source base gains, percentage override, other EE rows and both postprocessing helpers are not fully ported.',
                        'Source spatial kernel, target spatial kernel and rendered-pixel equality remain unverified.']},
        'limits': ['Factor equality alone does not establish equivalent sharpness, all21 levels, base gains, EE configuration or image output.',
                   'This static inspection does not authorize a nearest-level conversion or a camera runtime change.',
                   'No original ARM arithmetic execution, pixel simulation or hardware test is included.'],
    }
    output = ROOT/'outputs/metol/nx500-sharpness'
    output.with_suffix('.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    lines = ['# Original NX5001.12 P7; VA=file offset+0x80000000.',
             '# Static linear listing; literal pools may decode as instructions.',
             '# SHA256 '+actual_sha]
    for name, start, end in SPANS:
        lines.append(f'\n## {name} [{BASE+start:#x},{BASE+end:#x})')
        for ins in decoder.disasm(data[start:end], BASE+start):
            lines.append(f'{ins.address:08x} [{ins.address-BASE:08x}] {ins.bytes.hex():8s} {ins.mnemonic:8s} {ins.op_str}')
    lines.append(f'\n## Sharpness21 Q8 table\n{factor_address:08x} [{factor_address-BASE:08x}] .word '+', '.join(map(str, factors)))
    lines.append('\n## CIpcEeD5 vtable prefix and RTTI')
    for offset in range(0x635780, 0x6357a8, 4):
        lines.append(f'{BASE+offset:08x} [{offset:08x}] .word {word(offset):#010x}')
    for field in fields:
        lines.append(f"# {field['label_address']} {field['name']}: buffer+{field['ee_buffer_offset']}, low{field['bits']} bits")
    lines.append('\n# Target NX300M call-site evidence copied from '+str(target_asm_path.relative_to(ROOT)))
    lines.extend('\n'+section for section in target_sections)
    output.with_suffix('.asm').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    print(json.dumps({'iq_id': report['iq']['iq_id'], 'node': class_name, 'factors_q8': factors,
                      'scalar_levels_checked': len(levels), 'gain_fields': len(fields),
                      'target_exact_matches': {r['target_class']: r['exact_matches'] for r in comparisons},
                      'outputs': [str(output.with_suffix(s).relative_to(ROOT)) for s in ('.json', '.asm')]}, indent=2))


if __name__ == '__main__':
    main()
