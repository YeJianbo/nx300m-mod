"""Inspect official NX500 1.12 P7 Picture Wizard bytes.

Reads the extracted original partition and its existing provenance manifest.
Writes only the requested JSON and annotated ARM disassembly reports.
Optional --emulate-color executes two unmodified arithmetic leaf functions
against bounded synthetic RAM; it does not start firmware or a camera VM.
"""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
import struct
import zlib

os.environ.setdefault('CONDA_PREFIX', sys.prefix)
from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM

ROOT = Path(__file__).resolve().parents[1]
BASE = 0x80000000
FIELDS = ('R_COLOR', 'G_COLOR', 'B_COLOR', 'HUE', 'SATURATION', 'SHARPNESS', 'CONTRAST')
VARIABLE_IDS = (0x130, 0x131, 0x132, 0x133, 0x10f, 0x110, 0x111)
SPANS = (
    ('scalar_encoder', 0x514ac, 0x514c8),
    ('apply_prologue', 0x51ab8, 0x51adc),
    ('apply_parameters_and_rgb_encoding', 0x51b14, 0x51e38),
    ('variable_service_instance', 0xd69cc, 0xd6a2c),
    ('variable_sender', 0xd6b20, 0xd6b58),
    ('variable_dispatch_header', 0xd6bac, 0xd6bcc),
    ('variable_router', 0x71cf8, 0x71d2c),
    ('variable_service_constructor_prefix', 0xd6438, 0xd6470),
    ('router_instance', 0x72128, 0x7217c),
    ('router_constructor', 0x720d4, 0x72108),
    ('id_high_byte_one_receiver_instance', 0x14bc30, 0x14bc84),
    ('id_high_byte_one_receiver_constructor', 0x14bbdc, 0x14bc14),
    ('id_high_byte_one_variable_setter', 0x14c8d8, 0x14c970),
    ('entry_set_first_value', 0x1dea80, 0x1dea8c),
    ('value_compare_and_copy', 0x1de8b4, 0x1de914),
    ('entry_notify_observers', 0x1dec38, 0x1dec88),
    ('entry_construction_prefix', 0x14bcd4, 0x14bdd0),
    ('entry_observer_registration', 0x14c824, 0x14c87c),
    ('router_observer_registration', 0x71f2c, 0x71f6c),
    ('pw_observer_registrations', 0xd68d0, 0xd6950),
    ('capture_parameter_observer', 0xe01b8, 0xe028c),
    ('iq_index_map_and_forward', 0x9cf10, 0x9cf7c),
    ('alternate_iq_setter_empty', 0xa0dd8, 0xa0de0),
    ('iq_data_instance', 0x2c01bc, 0x2c0210),
    ('iq_data_constructor', 0x2c0128, 0x2c01b8),
    ('iq_data_raw_setter', 0x2bff4c, 0x2c00e0),
    ('initialized_data_copy', 0x11620, 0x11640),
    ('color_consumer_a_read_sat_hue', 0x3826a4, 0x382724),
    ('color_consumer_a_compose_sat_then_hue', 0x382814, 0x382868),
    ('color_consumer_b_sat_hue', 0x3835d0, 0x38369c),
    ('signed_q8_matrix_multiply', 0x381c84, 0x381d40),
    ('chroma_mix_and_saturation', 0x381df0, 0x381f28),
    ('iq_data_raw_getter', 0x2bff30, 0x2bff4c),
    ('custom1_application_mode', 0x51da8, 0x51db8),
    ('custom1_mode_decode_branch', 0xd8ad8, 0xd8b20),
    ('custom1_mode_decode_result', 0xdb2b0, 0xdb2c4),
    ('effect_mode_iq_ordinal_case', 0xd7284, 0xd7298),
    ('color_consumer_a_selector_reads', 0x382724, 0x3827fc),
    ('color_consumer_a_selector_special_mode', 0x382868, 0x3828f0),
    ('color_consumer_b_selector_reads', 0x383524, 0x3835d0),
    ('color_consumer_b_selector_special_mode', 0x38369c, 0x3836d4),
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def u32(data, offset):
    return struct.unpack_from('<I', data, offset)[0]


def signed16(value):
    value &= 0xffff
    return value - 0x10000 if value & 0x8000 else value


def signed32(value):
    value &= 0xffffffff
    return value-0x100000000 if value & 0x80000000 else value


def multiply_q8(left, right):
    """Transcription of 0x80381c84: row-major left*right, signed halfwords.

    Each SMLABB accumulates modulo 2^32; ADD 128 then signed ASR 8;
    STRH keeps the low 16 bits. This is arithmetic transcription, not an
    independently executed ARM result. No coefficient clipping occurs here.
    """
    result = []
    for row in range(3):
        for column in range(3):
            total = 0
            for k in range(3):
                total = signed32(total + signed16(left[3*row+k])*signed16(right[3*k+column]))
            result.append(signed16(signed32(total+128) >> 8))
    return result


def chroma_mix_and_saturation(base, mix_q8, saturation_code_q5, row_sum_offsets):
    """Transcription of 0x80381df0, preserving signed shifts and STRH wrap.

    Mix the two chroma rows by a signed Q8 2x2 matrix, with ASR 8 and
    no rounding bias; scale entries 4,5,6,7 by the Q5 saturation code.
    Rebuild entries 3 and 8 to impose the supplied chroma row sums.
    The luminance row (entries 0..2) is untouched; there is no clipping.
    """
    result = [signed16(x) for x in base]
    a, b, c, d = map(signed16, mix_q8)
    for column in range(3):
        u, v = signed16(base[3+column]), signed16(base[6+column])
        result[3+column] = signed16(signed32(a*u+b*v) >> 8)
        result[6+column] = signed16(signed32(c*u+d*v) >> 8)
    for index in (4, 5, 6, 7):
        result[index] = signed16(signed32(result[index]*signed16(saturation_code_q5)) >> 5)
    result[3] = signed16(row_sum_offsets[0]-result[4]-result[5])
    result[8] = signed16(row_sum_offsets[1]-result[6]-result[7])
    return result


def source_color_selectors(effect_mode, capture_mode, app_mode, mode3d,
                           smartpro_mode, smartpro_mix, smartpro_offsets,
                           auto_scene=None):
    """Transcribe common selector conditions plus builder A's auto-scene cases.

    Leave auto_scene=None for builder B, which has no observed scene switch
    in its SAT/HUE tail. Inputs are decoded IQ values, not menu/packed enums.
    """
    mix, offset, saturation_delta = int(effect_mode == 15), 0, 0
    if app_mode == 1 and capture_mode == 6 and mode3d == 0:
        index = smartpro_mode-8
        mix = smartpro_mix[index] if 0 <= index <= 10 else 0
        offset = smartpro_offsets[index] if 0 <= index <= 10 else 0
    if capture_mode == 0 and auto_scene is not None:
        if auto_scene == 8:
            saturation_delta = 1
        elif auto_scene == 10:
            offset = 4
        elif auto_scene == 11:
            offset, saturation_delta = 5, 2
        elif auto_scene == 12:
            offset = mix = 6
        elif auto_scene == 13:
            saturation_delta = 2
    return {'mix_index': mix, 'row_sum_offset_index': offset,
            'saturation_q5_delta': saturation_delta}


def emulate_color_operators(data, hue_matrices, mix_matrices, chroma_offsets, sat_entries):
    """Compare the transcriptions with unmodified ARM leaves, RAM mappings only."""
    import unicorn
    from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM
    from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                                   UC_ARM_REG_R3, UC_ARM_REG_SP, UC_ARM_REG_LR,
                                   UC_ARM_REG_PC, UC_ARM_REG_CPSR)
    uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
    code_address = BASE+0x381000
    uc.mem_map(code_address, 0x1000)
    uc.mem_write(code_address, data[0x381000:0x382000])
    ram_address = 0x100000
    uc.mem_map(ram_address, 0x20000)
    output, left, right, offsets, mix = [ram_address+x for x in (0x100, 0x200, 0x300, 0x400, 0x500)]
    stop = ram_address+0xf00
    registers = (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)

    def pack(values):
        return struct.pack('<'+str(len(values))+'h', *map(signed16, values))

    def invoke(address, arguments):
        uc.reg_write(UC_ARM_REG_CPSR, 0x10)
        uc.reg_write(UC_ARM_REG_SP, ram_address+0x1f000)
        uc.reg_write(UC_ARM_REG_LR, stop)
        for register, value in zip(registers, arguments):
            uc.reg_write(register, value & 0xffffffff)
        uc.emu_start(address, stop, count=2000, timeout=1_000_000)
        require(uc.reg_read(UC_ARM_REG_PC) == stop, 'Original color leaf did not return to its RAM caller')
        return list(struct.unpack('<9h', uc.mem_read(output, 18)))

    bases = [
        [77, 150, 29, -43, -85, 128, 128, -107, -21],
        [256, 0, 0, -2, 1, 1, -1, 2, -1],
        [32767, -32768, 123, -32768, 32767, -123, 32767, -32768, 321],
    ]
    matrix_cases, sat_cases = 0, 0
    for index, matrix in enumerate(hue_matrices):
        for base_index, base in enumerate(bases):
            uc.mem_write(left, pack(matrix))
            uc.mem_write(right, pack(base))
            observed = invoke(BASE+0x381c84, (output, left, right, 0))
            expected = multiply_q8(matrix, base)
            require(observed == expected, f'Original Q8 matrix mismatch at HUE={index}, base={base_index}: {observed} != {expected}')
            matrix_cases += 1
    for entry in sat_entries:
        for variant, (variant_matrix, variant_offsets) in enumerate(zip(mix_matrices, chroma_offsets)):
            for base_index, base in enumerate(bases):
                uc.mem_write(output, pack(base))
                uc.mem_write(offsets, pack(variant_offsets))
                uc.mem_write(mix, pack(variant_matrix))
                observed = invoke(BASE+0x381df0, (output, offsets, entry['gain_q5'], mix))
                expected = chroma_mix_and_saturation(base, variant_matrix, entry['gain_q5'], variant_offsets)
                require(observed == expected,
                        f'Original SAT mismatch at SAT={entry["value"]}, variant={variant}, base={base_index}: {observed} != {expected}')
                sat_cases += 1
    # A concrete original-ARM counterexample to replacing the source algorithm
    # with floor-scaling every coefficient of both chroma rows.
    counterexample_base = bases[1]
    uc.mem_write(output, pack(counterexample_base))
    uc.mem_write(offsets, pack(chroma_offsets[0]))
    uc.mem_write(mix, pack(mix_matrices[0]))
    observed = invoke(BASE+0x381df0, (output, offsets, sat_entries[1]['gain_q5'], mix))
    naive_u = [signed16((x*sat_entries[1]['gain_q5']) >> 5) for x in counterexample_base[3:6]]
    require(observed[3:6] != naive_u, 'Expected original-ARM SAT scaling counterexample disappeared')
    return {'unicorn_version': unicorn.__version__, 'passed': True,
            'original_q8_matrix_cases': matrix_cases, 'original_sat_mix_cases': sat_cases,
            'mapped_code': [hex(code_address), hex(code_address+0x1000)],
            'mapped_synthetic_ram': [hex(ram_address), hex(ram_address+0x20000)],
            'mmio_mapped': False, 'external_call_stubs': [],
            'firmware_booted': False, 'camera_or_full_vm_started': False,
            'sat_global_scaling_counterexample': {'recipe_sat': 1, 'gain_q5': sat_entries[1]['gain_q5'],
                                                  'base_u_row': counterexample_base[3:6],
                                                  'original_arm_u_row': observed[3:6], 'naive_scaled_u_row': naive_u},
            'scope': 'Two original arithmetic leaves only; source selectors, ISP consumption and rendered images are outside this check'}


def decode_span(data, start, end):
    decoder = Cs(CS_ARCH_ARM, CS_MODE_ARM)
    result = list(decoder.disasm(data[start:end], BASE + start))
    require(sum(i.size for i in result) == end-start,
            f'Undecoded instruction bytes in {start:#x}..{end:#x}')
    return result


def string_at(data, address):
    offset = address-BASE
    require(0 <= offset < len(data), f'String pointer outside P7: {address:#x}')
    end = data.index(0, offset)
    return data[offset:end].decode('ascii')


def pointer_refs(data, address):
    needle = struct.pack('<I', address)
    result, start = [], 0
    while (offset := data.find(needle, start)) >= 0:
        if offset % 4 == 0:
            result.append(offset)
        start = offset+1
    return result


def variable_case(data, field, variable_id):
    table_offset = 0xd6bcc + 4*variable_id
    target = u32(data, table_offset)
    instructions = decode_span(data, target-BASE, target-BASE+20)
    # Each selected case writes its internal ordinal and resolves a name
    # through an ARM PC-relative literal. Keep that original evidence.
    first, _, load, _, branch = instructions
    require(first.mnemonic == 'mov' and first.op_str.startswith('r3, #'),
            f'{field}: unexpected variable ordinal assignment')
    ordinal = int(first.op_str.split('#')[1], 0)
    require(load.mnemonic == 'ldr' and load.op_str.startswith('r3, [pc, #'),
            f'{field}: unexpected variable name literal load')
    displacement = int(load.op_str.split('#')[1].rstrip(']'), 0)
    literal_offset = load.address-BASE+8+displacement
    name_address = u32(data, literal_offset)
    return {
        'field': field, 'variable_id': hex(variable_id), 'internal_ordinal': ordinal,
        'jump_table_entry_offset': hex(table_offset), 'case_address': hex(target),
        'name_pointer_literal_offset': hex(literal_offset),
        'name_address': hex(name_address), 'name': string_at(data, name_address),
        'case_return_branch': branch.op_str,
    }, instructions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--p7', type=Path,
                        default=ROOT/'work/reference/nx500-stock-1.12/partition-05.bin')
    parser.add_argument('--manifest', type=Path,
                        default=ROOT/'work/reference/nx500-stock-1.12/source.json')
    parser.add_argument('--output', type=Path, default=ROOT/'outputs/metol/nx500-pw-source.json')
    parser.add_argument('--asm', type=Path, default=ROOT/'outputs/metol/nx500-pw-source.asm')
    parser.add_argument('--emulate-color', action='store_true',
                        help='Execute original Q8 matrix/SAT leaves in bounded synthetic RAM using Unicorn')
    args = parser.parse_args()
    data = args.p7.read_bytes()
    manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
    partition = next(p for p in manifest['partitions'] if p['index'] == 5)
    digest = hashlib.sha256(data).hexdigest()
    require(manifest['model'] == 'NX500' and manifest['version'] == '1.12',
            'Expected official NX500 1.12 provenance')
    require(manifest['zip_entry_crc_verified'] and manifest['source']['zip_crc_verified'],
            'Official ZIP entry verification missing from provenance')
    require(len(data) == partition['length'] and digest == partition['sha256'],
            'P7 bytes differ from the verified extracted partition')
    jamcrc = zlib.crc32(data) ^ 0xffffffff
    require(jamcrc == int(partition['header_crc32'], 16), 'P7 partition JAMCRC mismatch')

    blocks = [(name, decode_span(data, start, end)) for name, start, end in SPANS]
    indexed = {i.address-BASE: i for _, instructions in blocks for i in instructions}
    expected = {
        0x514ac: ('cmp', 'r0, #0x14'),
        0x514b8: ('ldrls', 'r0, [r0, #0x220]'),
        0x51bf4: ('rsb', 'r0, r0, r0, lsl #8'),
        0x51c08: ('lsl', 'r3, r3, #0x14'),
        0x51d28: ('lsl', 'r3, r3, #0x14'),
        0x51d4c: ('lsl', 'r3, r3, #0x14'),
        0xd6b34: ('bl', '#0x80071cf8'),
        0x71d14: ('ldr', 'r3, [r3, #0x14]'),
        0x71d18: ('blx', 'r3'),
        0xd6448: ('bl', '#0x80072128'),
        0x72158: ('bl', '#0x800720d4'),
        0x720f8: ('bl', '#0x8014bc30'),
        0x720fc: ('str', 'r0, [r4, #8]'),
        0x14bc60: ('bl', '#0x8014bbdc'),
        0x14bbf0: ('str', 'r3, [r4]'),
        0x14c8ec: ('uxtb', 'r6, r1'),
        0x14c920: ('bl', '#0x801dea80'),
        0x14c958: ('bl', '#0x801dec38'),
        0x1dea88: ('b', '#0x801de8b4'),
        0x1dec68: ('ldr', 'r3, [r3, #8]'),
        0x1dec6c: ('blx', 'r3'),
        0xd6914: ('movw', 'r1, #0x133'),
        0xd6924: ('movw', 'r1, #0x10f'),
        0xe0288: ('bl', '#0x8009cf10'),
        0x9cf48: ('ldrb', 'r4, [r4, #0x30]'),
        0x2bff68: ('str', 'r2, [r1, #4]'),
        0x3826ac: ('mov', 'r1, #0x55'),
        0x3826cc: ('mov', 'r1, #0x54'),
        0x3826d8: ('asr', 'r2, r2, #0x14'),
        0x3826dc: ('add', 'r2, r2, r2, lsl #1'),
        0x3826fc: ('moveq', 'r2, #0'),
        0x382710: ('add', 'r2, r2, #0xa'),
        0x382830: ('bl', '#0x80381df0'),
        0x38284c: ('bl', '#0x80381c84'),
        0x381d00: ('add', 'r3, r2, #0x80'),
        0x381d04: ('asr', 'r3, r3, #8'),
        0x381ec4: ('asr', 'r6, r6, #5'),
        0x51da8: ('mov', 'r3, #9'),
        0x51dac: ('movt', 'r3, #0x14'),
        0xd8af8: ('beq', '#0x800db2b0'),
        0xdb2b0: ('mov', 'r3, #0x10'),
        0xdb2b4: ('str', 'r3, [r6]'),
        0xd7284: ('mov', 'r3, #0x4f'),
    }
    for offset, wanted in expected.items():
        instruction = indexed[offset]
        require((instruction.mnemonic, instruction.op_str) == wanted,
                f'Original instruction changed at {offset:#x}')

    receiver_vtable = u32(data, 0x14bc2c)
    receiver_setter = u32(data, receiver_vtable-BASE+0x14)
    require(receiver_setter == BASE+0x14c8d8, 'Original receiver vtable setter differs')
    receiver_typeinfo = u32(data, receiver_vtable-BASE-4)
    receiver_type_name = string_at(data, u32(data, receiver_typeinfo-BASE+4))

    table_pointer = u32(data, 0x51e38)
    require(table_pointer == u32(data, 0x514c8), 'Two scalar table base literals differ')
    table_offset = table_pointer-BASE+0x220
    table = list(struct.unpack_from('<21I', data, table_offset))
    scalar_entries = []
    for value, raw in enumerate(table):
        raw16 = signed16(raw >> 16)
        require(raw == (((16*value-145) & 0xffff) << 16 | 0xd80a),
                f'Scalar table value {value} differs from the encoded affine sequence')
        scalar_entries.append({'value': value, 'raw': f'0x{raw:08x}',
                               'signed_high16': raw16, 'relative_step': value-10})

    # Recover the original constant-division arithmetic, including its integer
    # quantization, rather than substituting the old filmlab shell encoder.
    rgb_entries, old_mismatches = [], []
    for value in range(201):
        product_high = (0x51eb851f * (255*value)) >> 32
        code = product_high >> 6
        require(code == 255*value//200, 'Unexpected constant-division result')
        raw = (code << 20) | 0xff
        old_raw = ((value*2032+99)//100 << 16) | 0xff
        rgb_entries.append({'value': value, 'code_12bit': code, 'raw': f'0x{raw:08x}'})
        if raw != old_raw:
            old_mismatches.append({'value': value, 'original': f'0x{raw:08x}',
                                   'legacy_shell': f'0x{old_raw:08x}'})

    variables = []
    for field, variable_id in zip(FIELDS, VARIABLE_IDS):
        case, instructions = variable_case(data, field, variable_id)
        require(case['name'] == 'VARIABLE_PW'+{
            'R_COLOR': 'COLOR_R', 'G_COLOR': 'COLOR_G', 'B_COLOR': 'COLOR_B',
        }.get(field, field), f'{field}: unexpected original variable name')
        variables.append(case)
        blocks.append((f'variable_case_{field}', instructions))

    calls = []
    for _, instructions in blocks[:3]:
        for at, instruction in enumerate(instructions):
            if instruction.mnemonic == 'bl' and instruction.op_str == '#0x800d6b20':
                window = instructions[max(0, at-5):at]
                assignment = next(i for i in reversed(window)
                                  if i.mnemonic in ('mov', 'movw') and i.op_str.startswith('r1, #'))
                require(any(i.mnemonic == 'mov' and i.op_str == 'r3, #4' for i in window),
                        'PW sender call lacks the observed four-byte argument')
                calls.append({'call_address': hex(instruction.address),
                              'variable_id': hex(int(assignment.op_str.split('#')[1], 0)),
                              'value_size_bytes': 4})
    require([int(c['variable_id'], 16) for c in calls] == [0x10e, *VARIABLE_IDS],
            'PW application sequence differs from original eight-variable chain')

    iq_names = {}
    for field in ('HUE', 'SATURATION', 'SHARPNESS', 'CONTRAST'):
        needle = ('eIQ_ID_PW_'+field).encode()+b'\0'
        offset = data.index(needle)
        iq_names[field] = {'string_offset': hex(offset),
                           'aligned_pointer_reference_offsets': [hex(x) for x in pointer_refs(data, BASE+offset)]}

    # Original startup instructions relocate initialized data beyond the file's
    # direct load range. Resolve runtime table pointers through that byte copy.
    copy_words = struct.unpack_from('<8I', data, 0x11620)
    require(copy_words == (0xe59f1138, 0xe59f2138, 0xe59f3138, 0xf5d1f0c0,
                            0xecb10b10, 0xeca20b10, 0xe1520003, 0x3afffffa),
            'Original initialized-data copy loop differs')
    copy_source, copy_destination, copy_end = struct.unpack_from('<3I', data, 0x11760)
    copy_size = copy_end-copy_destination
    require(copy_size > 0 and copy_size % 64 == 0 and
            0 <= copy_source-BASE <= len(data)-copy_size,
            'Invalid original initialized-data copy extent')
    delta = copy_destination-copy_source

    def runtime_offset(address, size):
        require(copy_destination <= address <= copy_end-size,
                f'Runtime table {address:#x} is outside the observed initialized-data copy')
        offset = address-delta-BASE
        require(0 <= offset <= len(data)-size, 'Truncated relocated table')
        return offset

    hue_address = u32(data, 0x3828f8)
    mix_address = u32(data, 0x3828f4)
    offsets_address = u32(data, 0x3828f0)
    require((hue_address, mix_address, offsets_address) ==
            tuple(u32(data, a) for a in (0x38351c, 0x383518, 0x383514)),
            'Two observed color consumers use different source tables')
    hue_file_offset = runtime_offset(hue_address, 21*18)
    mix_file_offset = runtime_offset(mix_address, 7*8)
    offsets_file_offset = runtime_offset(offsets_address, 7*4)
    hue_matrices = [list(struct.unpack_from('<9h', data, hue_file_offset+18*i)) for i in range(21)]
    mix_matrices = [list(struct.unpack_from('<4h', data, mix_file_offset+8*i)) for i in range(7)]
    chroma_offsets = [list(struct.unpack_from('<2h', data, offsets_file_offset+4*i)) for i in range(7)]
    for matrix in hue_matrices:
        require(matrix[:3] == [256, 0, 0] and matrix[3] == matrix[6] == 0 and
                matrix[4] == matrix[8] and matrix[5] == -matrix[7],
                'Original HUE table is not a luminance-preserving chroma block')
    require(hue_matrices[10] == [256, 0, 0, 0, 256, 0, 0, 0, 256],
            'Original HUE neutral matrix differs')
    sat_entries = []
    for value, raw in enumerate(table):
        step = signed32(raw) >> 20
        three_step = step*3
        q5 = 32 if not -30 <= three_step <= 30 else (0 if three_step == -30 else three_step+32)
        sat_entries.append({'value': value, 'decoded_step': step, 'gain_q5': q5,
                            'gain_numerator': q5, 'gain_denominator': 32})
    emulation = emulate_color_operators(data, hue_matrices, mix_matrices, chroma_offsets, sat_entries) if args.emulate_color else None

    selector_address = u32(data, 0x3828fc)
    selector_offset = selector_address-BASE
    require(0 <= selector_offset < len(data)-0x74, 'Selector pointer is outside original file mapping')
    smartpro_mix = list(struct.unpack_from('<11I', data, selector_offset+0x1c))
    smartpro_offsets = list(struct.unpack_from('<11I', data, selector_offset+0x48))
    scene_targets = list(struct.unpack_from('<6I', data, 0x3827fc))
    require(scene_targets == [BASE+x for x in (0x3828b0, 0x382814, 0x3828c0,
                                               0x3828cc, 0x3828e0, 0x3828a0)],
            'Original auto-scene selector jump table differs')
    iq_related_names = {}
    for iq_id in (0x4f, 0x50, 0x54, 0x55, 0x6f, 0x77, 0x7a, 0x9c, 0x9e):
        iq_related_names[hex(iq_id)] = string_at(data, u32(data, 0x5758e0+4*iq_id))

    iq_map_base = u32(data, 0x9cf7c)
    iq_ids = {case['field']: data[iq_map_base-BASE+0x30+case['internal_ordinal']]
              for case in variables}
    observer_vtable = u32(data, 0xd69c4)
    iq_vtable = u32(data, 0x2c01b8)
    require(u32(data, observer_vtable-BASE+8) == BASE+0xe01b8,
            'Original PW observer callback differs')
    require(u32(data, iq_vtable-BASE+0x14) == BASE+0x2bff4c,
            'Original IQ raw setter differs')
    report = {
        'source': {'file': str(args.p7.relative_to(ROOT)) if args.p7.is_relative_to(ROOT) else str(args.p7),
                   'manifest': str(args.manifest), 'model': 'NX500', 'version': '1.12',
                   'bytes': len(data), 'sha256': digest, 'jamcrc': f'{jamcrc:08x}',
                   'manifest_identity_matches': True, 'partition_crc_matches': True,
                   'load_base': hex(BASE), 'official_url': manifest['source']['url']},
        'verification': ('Static original-byte inspection plus bounded original color arithmetic leaf execution in synthetic RAM'
                         if emulation else 'Static inspection of original ARM instructions and data; no ARM code executed'),
        'scalar_encoding': {'fields': list(FIELDS[3:]), 'range': [0, 20], 'count': 21,
                            'neutral_value': 10, 'base_literal_address': '0x80051e38',
                            'table_file_offset': hex(table_offset),
                            'formula': '(((16*value - 145) & 0xffff) << 16) | 0xd80a',
                            'out_of_range_fallback_raw': '0x000fd80a', 'entries': scalar_entries,
                            'physical_units_established': False},
        'rgb_encoding': {'range': [0, 200], 'formula': '(floor(255*value/200) << 20) | 0xff',
                         'zero_raw': '0x000000ff', 'negative_sentinel_raw': '0x080000ff',
                         'original_shift_bits': 20, 'entries': rgb_entries,
                         'legacy_shell_formula': '(ceil(value*2032/100) << 16) | 0xff',
                         'legacy_shell_mismatch_count': len(old_mismatches),
                         'legacy_shell_first_mismatches': old_mismatches[:12],
                         'gain_normalization_or_color_domain_established': False},
        'variables': variables,
        'application': {'entry': '0x80051ab8', 'slot_getter': '0x80063eb4',
                        'mode_slot': '0xda', 'color_tone_slot': '0xdb',
                        'seven_parameter_slots': [hex(x) for x in range(0xdc, 0xe3)],
                        'sender': '0x800d6b20', 'calls': calls},
        'consumer_search': {'router': '0x80071cf8',
                            'observed_dispatch': 'id high byte selects object at router+4+4*byte; virtual method at vtable+0x14',
                            'service_constructor': '0x800d6438',
                            'router_instance': '0x80072128',
                            'router_constructor': '0x800720d4',
                            'high_byte_one_receiver_instance': '0x8014bc30',
                            'high_byte_one_receiver_constructor': '0x8014bbdc',
                            'receiver_vtable_literal_file_offset': '0x14bc2c',
                            'receiver_vtable': hex(receiver_vtable),
                            'receiver_typeinfo_name': receiver_type_name,
                            'receiver_virtual_setter': hex(receiver_setter),
                            'receiver_index': 'uxtb(id); entries from object+0x10, bounded by vector span to object+0x14',
                            'receiver_index_by_field': {field: hex(variable_id & 0xff)
                                                        for field, variable_id in zip(FIELDS, VARIABLE_IDS)},
                            'entry_set_first_value': '0x801dea80 -> 0x801de8b4',
                            'entry_storage_behavior': 'Compare current bytes using 0x80509e38; copy using 0x80509eec when first/changed; unchanged result is -20',
                            'entry_notify': '0x801dec38 walks observer vector at entry+0x18..0x1c and calls each vtable+8 with entry id',
                            'entry_construction': {'function': '0x8014bcd4',
                                                   'size_table': hex(u32(data, 0x14bfac)),
                                                   'count': 64,
                                                   'id_formula': '0x100 + size-table index',
                                                   'leaf_storage_initializer': '0x801de8ac'},
                            'pw_observer': {'class_rtti': '11CCapParamIf',
                                            'vtable': hex(observer_vtable),
                                            'callback': '0x800e01b8',
                                            'hue_registration': '0x800d6914 id=0x133; 0x800d691c -> 0x80071f2c',
                                            'sat_registration': '0x800d6924 id=0x10f; 0x800d692c -> 0x80071f2c',
                                            'forward': '0x800e027c..0x800e0288 -> 0x8009cf10 with internal ordinal and current raw value'},
                            'iq_cache': {'ordinal_map_base': hex(iq_map_base), 'ids': iq_ids,
                                         'instance': '0x802c01bc', 'vtable': hex(iq_vtable),
                                         'class_rtti': '17CIQ_SysDataIF_NX1',
                                         'setter': '0x802bff4c',
                                         'store': 'this[1 + iq_id] = raw, observed 0x802bff64..0x802bff68',
                                         'generic_getter': hex(u32(data, iq_vtable-BASE+0x10)),
                                         'alternate_mode': '0x8009c4a4 nonzero -> 0x800a0dd8, which returns 0 without storing'},
                            'next_calls_observed': ['0x8014c920 -> 0x801dea80 with selected entry and value pointer',
                                                    '0x8014c958 -> 0x801dec38 with selected entry when preceding result is zero'],
                            'iq_name_references': iq_names,
                            'hue_exact_chroma_table_established': True,
                            'hue_angle_unit_assumed': False,
                            'saturation_gain_table_established': True,
                            'next_step': 'Track callers and final hardware consumers of the two observed color matrix builders; contrast/sharpness and RGB gain effects remain separate'},
        'initialized_data_relocation': {'copy_code': '0x80011620..0x80011640',
                                         'literal_file_offset': '0x11760',
                                         'source': hex(copy_source), 'destination': hex(copy_destination),
                                         'end_exclusive': hex(copy_end), 'bytes': copy_size,
                                         'delta': hex(delta),
                                         'runtime_to_file': 'runtime_address - delta - 0x80000000',
                                         'verified_by': 'Original copy instructions and literals; copy loop was not executed'},
        'source_color_operators': {
            'observed_builders': ['0x803826a4..0x80382864', '0x803835d0..0x80383698'],
            'direct_iq_reads': {'HUE': '0x54', 'SATURATION': '0x55'},
            'hue': {'table_runtime_address': hex(hue_address),
                    'table_file_offset': hex(hue_file_offset), 'range': [0, 20],
                    'neutral_index': 10, 'coefficient_unit': 256,
                    'element_type': 'signed little-endian int16',
                    'layout': 'row-major 3x3; [256,0,0; 0,a,b; 0,-b,a]',
                    'all_matrices_preserve_first_axis': True,
                    'entries': [{'value': v, 'matrix_q8': matrix,
                                 'chroma_2x2_q8': [matrix[4], matrix[5], matrix[7], matrix[8]]}
                                for v, matrix in enumerate(hue_matrices)],
                    'index': '(signed32(raw) >> 20) + 10; if unsigned index > 20 use 10',
                    'composition': 'left-multiply HUE matrix by the already mixed/saturation-adjusted base matrix',
                    'multiply_function': '0x80381c84',
                    'arithmetic': 'signed int16 products; signed32 wrap per accumulate; signed32(sum+128) ASR 8; STRH low16 wrap; no clipping',
                    'rounding': 'Same +128 for both signs, including negative sums; exact half ties toward positive infinity unless int32 overflow',
                    'table_mirror_asymmetry': 'value 2 diagonal=235, value 18 diagonal=234; preserve the table, do not regenerate trigonometric coefficients'},
            'saturation': {'range': [0, 20], 'neutral_index': 10, 'gain_unit': 32,
                           'entries': sat_entries,
                           'decode': 't = 3 * (signed32(raw) >> 20); invalid t outside [-30,30] -> 32; t == -30 -> 0; otherwise t + 32',
                           'function': '0x80381df0',
                           'arithmetic': 'First signed Q8 2x2 left-mix rows 1 and 2 with ASR 8 and low16 wrap; multiply entries 4,5,6,7 by gain_q5, ASR 5 and low16 wrap; rebuild entry 3=offset0-entry4-entry5 and entry8=offset1-entry6-entry7; leave entries 0..2 unchanged; no clipping',
                           'ideal_real_arithmetic_gain_on_zero_row_sum_base': True,
                           'coefficientwise_floor_scaling_equivalent': False,
                           'not_unconditionally_a_single_coefficient_scaling': 'Two coefficients are rebuilt after rounding; variants can impose nonzero row sums'},
            'chroma_mix_variants': {'table_runtime_address': hex(mix_address),
                                    'table_file_offset': hex(mix_file_offset),
                                    'coefficient_unit': 256, 'matrix_shape': [2, 2],
                                    'entries': mix_matrices,
                                    'selector': 'builder A: r7, builder B: r6 at the 0x80381df0 call; affected by other style/mode branches, not determined by HUE/SAT alone'},
            'chroma_row_sum_offsets': {'table_runtime_address': hex(offsets_address),
                                       'table_file_offset': hex(offsets_file_offset),
                                       'entries': chroma_offsets,
                                       'selector': 'builder A/B: r4; other style/mode branches select variants'},
            'order': ['chroma 2x2 variant left-mix of the current base matrix',
                      'SAT Q5 scaling and chroma row-sum reconstruction',
                      'HUE Q8 3x3 left-multiplication with rounding'],
            'pure_function_transcription': ['chroma_mix_and_saturation', 'multiply_q8'],
            'pure_functions_executed_against_original_ARM': emulation is not None,
            'original_leaf_execution': emulation,
            'cross_camera_boundary': 'Exact source tables/arithmetic are established. Applying these to NX300M Q7 CHM introduces coefficient quantization and a different pipeline boundary; this is not evidence of image equivalence',
        },
        'source_mode_selectors': {
            'iq_ids_with_original_names': iq_related_names,
            'default': {'mix_index': 'int(EFFECT_MODE == 15)', 'row_sum_offset_index': 0,
                        'saturation_q5_delta': 0},
            'smartpro_override': {'condition': 'APP_MODE == 1 and CAPTURE_MODE == 6 and 3D_MODE == 0',
                                   'smartpro_range': [8, 18],
                                   'table_runtime_address': hex(selector_address),
                                   'table_direct_file_offset': hex(selector_offset),
                                   'mix_indices': smartpro_mix,
                                   'row_sum_offset_indices': smartpro_offsets,
                                   'outside_range': {'mix_index': 0, 'row_sum_offset_index': 0}},
            'builder_a_auto_scene': {'condition': 'CAPTURE_MODE == 0',
                                     'scene_jump_table_file_offset': '0x3827fc',
                                     'entries': [{'scene': scene,
                                                  'target': hex(scene_targets[scene-8]),
                                                  **source_color_selectors(16, 0, 1, 0, 0,
                                                                           smartpro_mix, smartpro_offsets, scene)}
                                                 for scene in range(8, 14)],
                                     'builder_b': 'No corresponding scene switch observed in builder B SAT/HUE tail'},
            'metol_custom1': {'upstream_a2_action': 'Write slot 0xda=10, then call original 0x80051ab8',
                              'upstream_design': 'https://github.com/WApaperplane/metol/blob/ddbe8b119e046798e557add13837834f5e8d90ac/docs/current/A2_PATCH_DESIGN_2026-10-14.md',
                              'original_ui_slot': 10, 'original_jump_entry_offset': '0x51b04',
                              'original_jump_target': hex(u32(data, 0x51b04)),
                              'packed_pw_userdata': '0x00140009',
                              'userdata_id': 20, 'iq_id': '0x4f',
                              'decoded_effect_mode': 16,
                              'decode_branch': '0x800d8aec..0x800d8af8 -> 0x800db2b0..0x800db2b4',
                              'pasm_capture_values_1_to_4_or_8': {'mix_index': 0, 'row_sum_offset_index': 0,
                                                                 'saturation_q5_delta': 0},
                              'scope': 'A2 sets PW slot only; it does not establish CAPTURE_MODE, APP_MODE, 3D_MODE or auto-scene. Variant 0 is conditional, not universal'},
            'pure_function_transcription': 'source_color_selectors',
            'source_sat_not_a_fixed_2x2': {'rounding_counterexample': {'base_u': [-2, 1, 1],
                                                                      'sat_recipe_value': 1,
                                                                      'source_rebuild_u': [0, 0, 0],
                                                                      'all_coefficients_scaled_u': [-1, 0, 0]},
                                          'nonzero_offset_obstruction': 'Any 2x2 left-mix of zero-sum chroma rows still has zero row sums; nonzero source row-sum offsets cannot be produced by that operation',
                                          'required_full_path': 'Use the current 3x3 RGB-to-YCC base, execute source mix/SAT reconstruction, then source HUE multiplication. A Q7 final CHM approximation cannot reproduce the full operation unconditionally'},
        },
        'image_verified': False, 'camera_or_vm_started': False,
    }
    asm = ['; Original Samsung NX500 1.12 P7; addresses = file offset + 0x80000000',
           '; Static bytes only. Inline jump tables/literals are emitted as data, not instructions.',
           f'; source SHA256 {digest}', '']
    for name, instructions in blocks:
        asm.append(f'## {name}')
        for instruction in instructions:
            asm.append(f'{instruction.address:08x} [{instruction.address-BASE:08x}] '
                       f'{instruction.bytes.hex():8} {instruction.mnemonic:9} {instruction.op_str}')
        asm.append('')
    asm.append('## apply_mode_jump_table')
    for value in range(14):
        offset = 0x51adc+4*value
        asm.append(f'{BASE+offset:08x} [{offset:08x}] .word 0x{u32(data, offset):08x} ; mode {value}')
    asm.extend(['', '## scalar_table_base_literal',
                f'80051e38 [00051e38] .word 0x{table_pointer:08x}',
                '', '## variable_receiver_vtable_literal',
                f'8014bc2c [0014bc2c] .word 0x{receiver_vtable:08x}',
                f'{receiver_vtable+0x14:08x} [{receiver_vtable-BASE+0x14:08x}] .word 0x{receiver_setter:08x} ; virtual setter',
                '', '## scalar_encoding_table'])
    for value, raw in enumerate(table):
        offset = table_offset+value*4
        asm.append(f'{BASE+offset:08x} [{offset:08x}] .word 0x{raw:08x} ; value {value}')
    asm.extend(['', '## initialized_data_copy_literals'])
    for index, value in enumerate((copy_source, copy_destination, copy_end)):
        offset = 0x11760+index*4
        asm.append(f'{BASE+offset:08x} [{offset:08x}] .word 0x{value:08x}')
    for label, address, offset, rows in (
            ('source_hue_q8', hue_address, hue_file_offset, hue_matrices),
            ('source_chroma_mix_q8', mix_address, mix_file_offset, mix_matrices),
            ('source_chroma_row_sum_offsets', offsets_address, offsets_file_offset, chroma_offsets)):
        asm.extend(['', f'## {label}; runtime pointers resolved through original initialized-data copy'])
        stride = len(rows[0])*2
        for index, row in enumerate(rows):
            asm.append(f'{address+index*stride:08x} [{offset+index*stride:08x}] .hword '+
                       ', '.join(str(x) for x in row)+f' ; index {index}')
    asm.extend(['', '## SmartPro selector arrays; direct original file mapping'])
    for label, add, values in (('mix', 0x1c, smartpro_mix), ('row_sum_offset', 0x48, smartpro_offsets)):
        for index, value in enumerate(values):
            offset = selector_offset+add+4*index
            asm.append(f'{BASE+offset:08x} [{offset:08x}] .word {value} ; {label}, SmartPro {index+8}')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.asm.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    args.asm.write_text('\n'.join(asm)+'\n', encoding='utf-8')
    print(json.dumps({'scalar_count': len(table), 'rgb_legacy_mismatches': len(old_mismatches),
                      'application_variables': len(calls), 'output': str(args.output)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
