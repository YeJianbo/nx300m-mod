"""Extract NX500 PW RGB column gains, baseline sources and ColorD5 consumers.

Static original-byte evidence only. The existing custom RGB-percent bridge is
not used as a reference implementation of this source operation.
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
SPANS = (
    ('RGB encoder and original variable sends', 0x51b14, 0x51e38),
    ('Builder A three CCM baselines, effect and RGB columns', 0x382180, 0x382650),
    ('Builder A separate R2Y SAT HUE construction', 0x382650, 0x382900),
    ('Builder A update order and matrix forwarding', 0x382920, 0x38299c),
    ('Builder B three CCM baselines, effect and RGB columns', 0x382eb0, 0x383380),
    ('Builder B separate R2Y construction', 0x383380, 0x383760),
    ('Builder B update order', 0x3837a0, 0x3837d0),
    ('Matrix getters and builder dispatch', 0x381a60, 0x381c84),
    ('Cache matrix offsets', 0x3820c4, 0x382114),
    ('Calibration coefficient copies', 0x383bf4, 0x383d2c),
    ('Q7 baseline construction', 0x3840fc, 0x384190),
    ('Baseline singleton initialization', 0x384814, 0x384950),
    ('Baseline matrix getters', 0x384c00, 0x384c74),
    ('IQ-facing matrix accessors', 0x29ba14, 0x29bb34),
    ('Three CCM parameter forwarding', 0x376fa0, 0x376ff8),
    ('Forwarding signed16 CCMs to word cache', 0x380990, 0x380a48),
    ('ColorD5 GetIndex and SetParam', 0x391d08, 0x39255c),
    ('Independent signed matrix multiply with SDIV', 0x388ea4, 0x388f1c),
    ('Final source wrappers', 0x17cf84, 0x17d018),
    ('Final two CCM context writer', 0x49e388, 0x49e4d4),
    ('Final R2Y context writer', 0x49e538, 0x49e5c0),
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def signed(value, bits):
    value &= (1 << bits)-1
    return value-(1 << bits) if value & (1 << (bits-1)) else value


def column_dataflow(decoder, data, start, end, matrix_offsets):
    """Follow only register/stack provenance through the unrolled coefficient code.

    This checks which input gain multiplies each output coefficient and the
    ADD64/ASR7/STRH order, without evaluating arithmetic or running ARM code.
    """
    registers = {'r3': ('gain', 'R'), 'r2': ('gain', 'G'), 'sl': ('gain', 'B')}
    stack, stores = {}, []
    for ins in decoder.disasm(data[start:end], BASE+start):
        memory = re.fullmatch(r'(\w+), \[(r4|sp)(?:, #(0x[\da-f]+|\d+))?\]', ins.op_str)
        if memory:
            reg, base, offset = memory.groups()
            offset = int(offset or '0', 0)
            if ins.mnemonic == 'ldrsh' and base == 'r4':
                registers[reg] = ('coefficient', offset)
            elif ins.mnemonic == 'ldr' and base == 'sp':
                registers[reg] = stack[offset]
            elif ins.mnemonic == 'str' and base == 'sp':
                stack[offset] = registers[reg]
            elif ins.mnemonic == 'strh' and base == 'r4':
                token = registers[reg]
                require(token[0] == 'asr7' and token[1][0] == 'add64' and token[1][1][0] == 'multiply',
                        f'Unexpected coefficient arithmetic at {ins.address:#x}')
                _, coefficient, gain = token[1][1]
                require(coefficient == ('coefficient', offset), 'Coefficient moved to a different output slot')
                matrix = next((n for n, at in enumerate(matrix_offsets) if at <= offset < at+18), None)
                require(matrix is not None, 'Store outside three CCM matrices')
                index = (offset-matrix_offsets[matrix])//2
                require(gain == ('gain', 'RGB'[index%3]), 'RGB does not follow the asserted column order')
                stores.append({'matrix_index': matrix, 'coefficient_index': index,
                               'row': index//3, 'column': index%3, 'gain': gain[1],
                               'object_offset': hex(offset), 'store_address': hex(ins.address)})
            continue
        operands = ins.op_str.split(', ')
        if ins.mnemonic == 'mul':
            dest, left, right = operands
            left, right = registers[left], registers[right]
            if left[0] == 'gain':
                left, right = right, left
            require(left[0] == 'coefficient' and right[0] == 'gain', 'Unexpected RGB product inputs')
            registers[dest] = ('multiply', left, right)
        elif ins.mnemonic == 'add' and operands[-1] == '#0x40':
            registers[operands[0]] = ('add64', registers[operands[1]])
        elif ins.mnemonic == 'asr' and operands[-1] == '#7':
            registers[operands[0]] = ('asr7', registers[operands[1]])
    require(len(stores) == 27 and len({store['object_offset'] for store in stores}) == 27,
            'Expected every coefficient of all three matrices exactly once')
    return sorted(stores, key=lambda item: (item['matrix_index'], item['coefficient_index']))


def main():
    source_report = ROOT/'outputs/metol/nx500-pw-source.json'
    upstream = json.loads(source_report.read_text(encoding='utf-8'))
    source_path = ROOT/'work/reference/nx500-stock-1.12/partition-05.bin'
    data = source_path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    require(digest == upstream['source']['sha256'], 'Original P7 differs from established source evidence')
    decoder = Cs(CS_ARCH_ARM, CS_MODE_ARM)
    decoder.skipdata = True

    def word(offset):
        return struct.unpack_from('<I', data, offset)[0]

    def text_at(address):
        offset = address-BASE
        return data[offset:data.index(b'\0', offset)].decode('ascii')

    anchors = []

    def check(offset, mnemonic, operands):
        ins = next(decoder.disasm(data[offset:offset+4], BASE+offset))
        require((ins.mnemonic, ins.op_str) == (mnemonic, operands), f'Instruction mismatch at {offset:#x}')
        anchors.append({'address': hex(ins.address), 'bytes': ins.bytes.hex(), 'instruction': mnemonic+' '+operands})

    channels = []
    for channel, variable, iq_id, loads in [
        ('R', 0x130, 0x51, [0x38228c, 0x382fbc]),
        ('G', 0x131, 0x52, [0x3822a4, 0x382fd4]),
        ('B', 0x132, 0x53, [0x3822c4, 0x382ff4])]:
        name = text_at(word(0x5758e0+4*iq_id))
        require(name == 'eIQ_ID_PW_COLOR_'+channel, 'Unexpected original IQ name')
        require(upstream['consumer_search']['iq_cache']['ids'][channel+'_COLOR'] == iq_id, 'Variable/IQ source evidence disagrees')
        for at in loads:
            check(at, 'mov', f'r1, #{iq_id:#x}')
        channels.append({'channel': channel, 'variable_id': hex(variable), 'iq_id': hex(iq_id),
                         'iq_name': name, 'read_sites': [hex(BASE+at) for at in loads]})
    for at, mnemonic, operands in [
        (0x3822d4, 'asr', 'r3, r3, #0x14'), (0x3822e0, 'asr', 'r2, r2, #0x14'),
        (0x3822e4, 'asr', 'sl, sl, #0x14'), (0x3822dc, 'cmp', 'r3, #0x100'),
        (0x3822e8, 'bhi', '#0x80382640'), (0x3822f0, 'bhi', '#0x80382640'),
        (0x3822f8, 'bhi', '#0x80382640'), (0x383004, 'asr', 'r3, r3, #0x14'),
        (0x383010, 'asr', 'r2, r2, #0x14'), (0x383014, 'asr', 'sl, sl, #0x14'),
        (0x3832c4, 'mul', 'r7, r7, r3'), (0x3832fc, 'add', 'r7, r7, #0x40'),
        (0x383324, 'asr', 'r7, r7, #7'), (0x38334c, 'strh', 'r7, [r4, #0x22]'),
        (0x382938, 'bl', '#0x80382180'), (0x382940, 'bl', '#0x80382650'),
        (0x3837b8, 'bl', '#0x80382eb0'), (0x3837c0, 'bl', '#0x80383380'),
        (0x391ff4, 'ldr', 'lr, [pc, #0x55c]'), (0x39202c, 'bl', '#0x80388ea4'),
        (0x392128, 'bl', '#0x80388ea4'), (0x388ef0, 'sdiv', 'ip, ip, r3')]:
        check(at, mnemonic, operands)
    mapping_a = column_dataflow(decoder, data, 0x3822fc, 0x382640, [0x58, 0x6a, 0x7c])
    mapping_b = column_dataflow(decoder, data, 0x38302c, 0x383370, [0x10, 0x22, 0x34])
    encoded = []
    for item in upstream['rgb_encoding']['entries']:
        value, raw = item['value'], int(item['raw'], 16)
        code = signed(raw, 32) >> 20
        require(code == (255*value)//200 and raw == (code << 20)|0xff, 'Unexpected upstream RGB encoding')
        encoded.append({'recipe_value': value, 'raw': item['raw'], 'consumed_code': code,
                        'gain_numerator': code, 'gain_denominator': 128})
    require(len(encoded) == 201, 'Expected all201 existing source encodings')
    sentinel = int(upstream['rgb_encoding']['negative_sentinel_raw'], 16)
    require(signed(sentinel, 32) >> 20 == 128, 'Unexpected sentinel gain')
    f_address = word(0x392558)
    fixed = list(struct.unpack_from('<9h', data, f_address-BASE))
    require(f_address == 0x8062fd40 and fixed == [128,96,-32,128,-32,-32,128,-32,96], 'Unexpected final CCM fixed matrix')
    typeinfo = word(0x62fd5c)
    require(text_at(word(typeinfo-BASE+4)) == '11CIpcColorD5', 'Unexpected Color node RTTI')
    require(word(0x62fd78) == BASE+0x391f60 and word(0x62fd7c) == BASE+0x391d08, 'Unexpected node virtual functions')
    leaf = list(decoder.disasm(data[0x388ea4:0x388f1c], BASE+0x388ea4))
    require(not any(ins.mnemonic in ('bl', 'blx') or 'pc' in ins.op_str for ins in leaf), 'Matrix leaf has external or PC dependency')
    require(leaf[-1].mnemonic == 'bx' and leaf[-1].op_str == 'lr', 'Matrix leaf end mismatch')

    report = {
        'source': str(source_path.relative_to(ROOT)), 'source_sha256': digest,
        'source_encoding_report': str(source_report.relative_to(ROOT)), 'load_base': hex(BASE),
        'analysis': 'Static original-byte extraction, instruction anchors and register/stack provenance. No ARM or pixel execution in this script.',
        'channels': channels, 'instruction_anchors': anchors,
        'encoding_and_consumption': {
            'recipe_range_from_existing_encoder_report': [0, 200],
            'encoder': '(floor(255*recipe_value/200)<<20)|0xff for the nonnegative recipe values listed below.',
            'consumer': 'code=signed32(raw)>>20; no second normalization, interpolation or conversion before the coefficient multiplication.',
            'consumer_accepted_code_range': [0, 256],
            'invalid_behavior': 'Each code is compared unsigned to256. If any R/G/B code fails, the builder skips RGB scaling for all three matrices; already copied/effect-adjusted baselines remain.',
            'decode_evidence': {'A': '0x803822cc..0x803822f8', 'B': '0x80382ffc..0x80383028'},
            'exact_examples': [encoded[100], encoded[200], {'recipe_value': 'negative sentinel',
                'raw': hex(sentinel), 'consumed_code': 128, 'gain_numerator': 128, 'gain_denominator': 128}],
            'consumer_only_endpoint': 'raw0x100000ff yields256 and is accepted. It is not an ordinary output among the listed0..200 recipes.',
            'entries': encoded},
        'rgb_operator': {
            'shape': 'Three separate row-major3x3 signed16 CCMs; input R/G/B selects matrix columns0/1/2.',
            'real_arithmetic_description': 'M_scaled = M * diag(code_R,code_G,code_B)/128, followed by independent coefficient rounding and signed16 wrapping.',
            'exact_coefficient': 'signed16_low(signed32(signed16(coefficient)*code+64) ASR7); MUL/ADD wrap32 and STRH keeps low16; no saturation.',
            'rounding': 'ADD64 for both signs; ASR7 rounds half ties toward positive infinity in the legal coefficient/code domain.',
            'builder_A': {'entry': '0x80382180', 'matrix_offsets': ['0x58','0x6a','0x7c'], 'stores': mapping_a},
            'builder_B': {'entry': '0x80382eb0', 'matrix_offsets': ['0x10','0x22','0x34'], 'stores': mapping_b},
            'single_coefficient_original_bytes': {'spans': ['0x803832c4..0x803832c8', '0x803832fc..0x80383300',
                '0x80383324..0x80383328', '0x8038334c..0x80383350'],
                'abi': 'r7=sign-extended coefficient, r3=code, r4=writable object pointer; output halfword at object+0x22.'},
            'not_output_row_gain': 'A left diagonal row gain changes output channels. The original instructions multiply every row column0 by R, column1 by G and column2 by B.'},
        'baselines': {
            'getters': [{'entry': '0x80384c5c', 'singleton_offset': '0x4', 'destination_A': '0x58', 'destination_B': '0x10'},
                        {'entry': '0x80384c44', 'singleton_offset': '0x28', 'destination_A': '0x6a', 'destination_B': '0x22'},
                        {'entry': '0x80384c2c', 'singleton_offset': '0x16', 'destination_A': '0x7c', 'destination_B': '0x34'}],
            'baseline_singleton': '0x803848ec returns runtime singleton0x81792be8; initializer0x80384814 reads six signed16 coefficients per baseline through0x80383bf4 from0x810c3428,+0x0c,+0x18.',
            'Q7_evidence': '0x803840fc..0x80384188 converts six off-diagonal coefficients and reconstructs each diagonal as128 minus the other two row terms, then stores signed16; row sum is128 modulo signed16 storage.',
            'effect_before_rgb': 'EFFECT_MODE1..7 maps through seven u32 entries[1,2,3,4,5,6,7] at0x8062d808(A)/0x8062d890(B). A selected signed16 3x3 at live0x81123b9c+18*index left-multiplies each copied CCM using0x80381c84 before RGB column scaling. Other modes skip this effect branch.',
            'why_no_fixed_numeric_baseline': 'The coefficient/profile addresses0x810c3428 and0x81123b9c are runtime RAM outside this P7 mapping. A stored nine-number baseline cannot be recovered from these pointers alone.',
            'unknown': 'Profile/calibration update provenance, physical meaning of the three baseline variants, and exact live matrices were not established.'},
        'hue_sat_relation': {
            'software_sequence': ['Copy three CCM baselines', 'Optional effect-mode left multiplication on the three CCMs',
                                  'RGB column scaling of all three CCMs', 'Build separate R2Y matrix and apply its chroma-mix/SAT/HUE operations'],
            'separate_r2y': 'Builder A0x80382650 stores its separate9xint16 R2Y at+0x8e; builder B0x80383380 uses+0x46. The R2Y baseline getter is0x80384c00, singleton+0x3a. RGB scaling does not directly alter these R2Y storage slots.',
            'order_evidence': 'A0x80382938 then0x80382940; B0x803837b8 then0x803837c0.',
            'sat_hue_source': 'outputs/metol/nx500-pw-source.json: source_color_operators',
            'boundary': 'These are separate matrix builders. Calling the RGB builder first does not independently prove hardware pixel order, clipping boundaries, or equivalence to composing RGB gains into the R2Y matrix.'},
        'GetIndex_and_final_consumer': {
            'class': 'CIpcColorD5', 'rtti': '0x8062fd90', 'vtable': '0x8062fd60',
            'get_index': '0x80391d08', 'set_param': '0x80391f60',
            'refresh': '0x8029ba14/0x8029ba24 trigger builder A/B via0x80381b88/0x80381b6c and0x80381c60.',
            'GetIndex_selection': 'pack byte0 chooses A when zero, B otherwise. Matrix accessor0x8029ba54/0x8029bb04 returns third CCM,0x8029ba44/0x8029baf4 returns second CCM,0x8029ba64/0x8029bb14 returns separate R2Y.',
            'cache_fields': [{'data': 'third CCM', 'offset': '0xe3c', 'subindex': 0},
                             {'data': 'second CCM', 'offset': '0xe4e', 'subindex': 1},
                             {'data': 'R2Y', 'offset': '0xe60', 'subindex': 2},
                             {'data': 'separate4xint16 block', 'offset': '0xe72', 'subindex': 3}],
            'cache_update': 'memcmp18/18/18/8 bytes; changed data copied and corresponding subindex XOR1. Thus RGB recipe values do not become their own scalar GetIndex indices; changed CCM contents trigger the node update.',
            'percentage': 'Subindex4 is pack+0xc0 when pack+0xbc flag is nonzero, otherwise100. In SetParam it scales the separate R2Y entries with signed trunc_zero(product/100); it is not the RGB column code.',
            'final_CCM_conversion': {'fixed_matrix_address': hex(f_address), 'fixed_matrix_q7': fixed,
                'literal_address': '0x80392558', 'calls': ['0x8039202c', '0x80392128'],
                'operation': 'third_CCM * F /128 and second_CCM * F /128; each coefficient signed32 wrap accumulation, signed SDIV toward zero, then STRH low16. Both nine-coefficient results pack low11 bits per halfword with BFI.',
                'fixed_matrix_domain': 'The actual F bytes and right-multiplication direction are established; F physical/color-domain interpretation is unknown.',
                'leaf': {'entry': '0x80388ea4', 'end_exclusive': '0x80388f1c',
                         'independent_position_relocatable_leaf': True,
                         'abi': 'r0=output int16[9],r1=left int16[9],r2=right int16[9],r3=signed divisor; leaf contains only internal branches, no PC literals or external calls.'}},
            'submission': {'two_CCMs': '0x803921fc ->0x8017cf84 ->0x8049e388; input contains two20-byte slots holding nine11-bit coefficient halfwords apiece. Backend copies40 bytes to channel context+0x1f00..0x1f24.',
                           'R2Y': '0x80392448 ->0x8017cfd0 (log d5_ipc_set_subparam_r2y) ->0x8049e538; writes five words to channel context+0x20d8..0x20e8.'},
            'first_CCM_other_path': 'Builder A also forwards its first/second/third CCMs through0x80376fa0/0x80376fd8 into signed32 parameter caches0x80380990/0x80380a00. Their complete downstream consumers are not audited here.'},
        'limits': ['Static matrix/parameter semantics, not measured pixel output.',
                   'The custom target percentage bridge scales output rows with its own percent/100 arithmetic; it must retain that definition unless explicitly replaced by a source-column implementation.',
                   'Known source coefficient arithmetic alone does not establish live baselines, F domain, target insertion point or full cross-camera equivalence.'],
    }
    output = ROOT/'outputs/metol/nx500-rgb'
    output.with_suffix('.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    lines = ['# Original NX5001.12 P7; VA=file offset+0x80000000.',
             '# Static linear listing; literal pools may decode as instructions.', '# SHA256 '+digest]
    for name, start, end in SPANS:
        lines.append(f'\n## {name} [{BASE+start:#x},{BASE+end:#x})')
        for ins in decoder.disasm(data[start:end], BASE+start):
            lines.append(f'{ins.address:08x} [{ins.address-BASE:08x}] {ins.bytes.hex():8s} {ins.mnemonic:8s} {ins.op_str}')
    lines.append(f'\n## ColorD5 fixed right matrix F\n{f_address:08x} [{f_address-BASE:08x}] .hword '+', '.join(map(str, fixed)))
    output.with_suffix('.asm').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    print(json.dumps({'outputs': [str(output.with_suffix(ext).relative_to(ROOT)) for ext in ('.json','.asm')],
                      'iq_ids': [item['iq_id'] for item in channels], 'coefficient_provenance_checks': len(mapping_a)+len(mapping_b),
                      'recipe_encodings_checked': len(encoded), 'exact_examples': report['encoding_and_consumption']['exact_examples'],
                      'final_CCM_fixed_matrix_address': hex(f_address), 'final_CCM_fixed_matrix': fixed,
                      'matrix_leaf': report['GetIndex_and_final_consumer']['final_CCM_conversion']['leaf']}, indent=2))


if __name__ == '__main__':
    main()
