"""Extract NX500 1.12 P/A/S/M and PW semantics from original ARM bytes.

Name claims are checked against strings loaded by each reached conversion
result's stock logging block, rather than inferred from another camera's enum.
"""
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


def main():
    source = ROOT / 'work/reference/nx500-stock-1.12/partition-05.bin'
    data = source.read_bytes()
    decoder = Cs(CS_ARCH_ARM, CS_MODE_ARM)
    decoder.skipdata = True
    evidence = set()

    def word(offset):
        return struct.unpack_from('<I', data, offset)[0]

    def ins(offset):
        evidence.add(offset)
        return next(decoder.disasm(data[offset:offset+4], BASE+offset))

    def string(address):
        offset = address-BASE
        if not 0 <= offset < len(data):
            raise ValueError(f'Unmapped string {address:#x}')
        return data[offset:data.index(b'\0', offset)].decode('ascii')

    def dispatch(raw):
        # r0 is the value returned by GetUserData at 0x800d7940.
        registers, comparison, offset, trace = {'r0': raw}, 0, 0xd7944, []
        for _ in range(256):
            instruction = ins(offset)
            op, args = instruction.mnemonic, instruction.op_str
            trace.append(hex(BASE+offset))
            if op in ('mov', 'movw', 'movt', 'mvn'):
                reg, rhs = args.split(', ')
                value = int(rhs[1:], 0) if rhs.startswith('#') else registers[rhs]
                registers[reg] = ((registers.get(reg, 0)&0xffff) | value<<16 if op == 'movt'
                                  else ~value&0xffffffff if op == 'mvn' else value)
            elif op == 'cmp':
                left, right = args.split(', ')
                value = int(right[1:], 0) if right.startswith('#') else registers[right]
                comparison = registers[left]-value
            elif op in ('b', 'beq', 'bne', 'bgt', 'blt', 'bge', 'ble'):
                take = {'b': True, 'beq': comparison == 0, 'bne': comparison != 0,
                        'bgt': comparison > 0, 'blt': comparison < 0,
                        'bge': comparison >= 0, 'ble': comparison <= 0}[op]
                if take:
                    offset = int(args[1:], 16)-BASE
                    continue
            elif op == 'str' and args == 'r0, [sp, #0x14]':
                pass
            elif op == 'str' and re.fullmatch(r'(?:r\d+|ip), \[r6\]', args):
                # Stock result blocks load their semantic enum label for logging.
                labels = []
                for at in range(offset+4, offset+0x44, 4):
                    tail = ins(at)
                    match = re.fullmatch(r'(?:r\d+|ip), \[pc, #(-?(?:0x[0-9a-f]+|\d+))\]', tail.op_str)
                    if tail.mnemonic == 'ldr' and match:
                        literal = at+8+int(match.group(1), 0)
                        evidence.add(literal)
                        try:
                            label = string(word(literal))
                        except (ValueError, UnicodeDecodeError):
                            continue
                        if label.startswith(('SHOOTINGMODE_', 'DIALMODE_', 'PW_')):
                            labels.append({'label': label, 'load': hex(BASE+at),
                                           'literal': hex(BASE+literal)})
                return registers[args.split(',')[0]], trace, labels
            else:
                raise ValueError(f'Unsupported reached dispatch instruction {BASE+offset:#x}: {op} {args}')
            offset += 4
        raise ValueError('Mode conversion did not terminate')

    rows = []
    requested = [(0, ordinal, 'DIALMODE_'+label) for ordinal, label in
                 ((2, 'PROGRAM'), (3, 'APERTURE'), (4, 'SHUTTERSPEED'), (5, 'MANUAL'))]
    requested += [(1, ordinal, 'SHOOTINGMODE_'+label) for ordinal, label in
                  ((1, 'PROGRAM'), (2, 'APERTURE'), (3, 'SHUTTERSPEED'), (4, 'MANUAL'))]
    requested += [(0x14, 0, 'PW_STANDARD'), (0x14, 9, 'PW_CUSTOM1')]
    for userdata_id, ordinal, expected_label in requested:
        raw = userdata_id<<16 | ordinal
        value, trace, labels = dispatch(raw)
        if expected_label not in [item['label'] for item in labels]:
            raise ValueError(f'Original result block did not name {expected_label}: {labels}')
        slot = 0xd6bcc+4*userdata_id
        target = word(slot)-BASE
        evidence.add(slot)
        id_instruction = ins(target)
        match = re.fullmatch(r'r3, #(0x[0-9a-f]+|\d+)', id_instruction.op_str)
        if id_instruction.mnemonic != 'mov' or not match or ins(target+4).mnemonic != 'strh':
            raise ValueError('Unexpected IQ ID conversion block')
        iq_id = int(match.group(1), 0)
        iq_name = string(word(0x5758e0+4*iq_id))
        rows.append({'name': expected_label, 'userdata_id': hex(userdata_id),
                     'raw': hex(raw), 'iq_id': hex(iq_id), 'iq_id_name': iq_name,
                     'iq_value': value, 'stock_name_loads': labels,
                     'convert_id_slot': hex(BASE+slot), 'convert_id_block': hex(BASE+target),
                     'conversion_trace': trace})

    target_report = json.loads((ROOT/'outputs/metol/mode-context-enums.json').read_text())
    target_values = {row['label']: (group['iq_id'], row.get('iq_value'))
                     for group in target_report['groups'] for row in group['values']}
    mapping = []
    for row in rows:
        if row['name'] not in target_values:
            continue
        target_id, target_value = target_values[row['name']]
        mapping.append({'semantic_name': row['name'], 'nx500_iq_id': row['iq_id'],
                        'nx500_value': row['iq_value'], 'nx300m_iq_id': target_id,
                        'nx300m_value': target_value})
    from inspect_nx500_pw import source_color_selectors
    pasm = [row for row in rows if row['name'].startswith('DIALMODE_')]
    app_modes = {row['name'].split('_', 1)[1]: row['iq_value'] for row in rows
                 if row['name'].startswith('SHOOTINGMODE_')}
    # Neither special branch can run for P/A/S/M: capture is neither 0 nor 6.
    selectors = [dict(name=row['name'], capture=row['iq_value'], effect_name=effect['name'],
                     effect=effect['iq_value'], app_mode=app_modes[row['name'].split('_', 1)[1]],
                     **source_color_selectors(effect['iq_value'], row['iq_value'],
                                               app_modes[row['name'].split('_', 1)[1]], 0,
                                               0, [], [], None))
                 for effect in rows if effect['name'] in ('PW_STANDARD', 'PW_CUSTOM1')
                 for row in pasm]
    if not all(row['mix_index'] == row['row_sum_offset_index'] == row['saturation_q5_delta'] == 0
               for row in selectors):
        raise ValueError('Unexpected ordinary PASM selectors')
    spans = [(0xd78f8, 0xd7944), (0xd6bac, 0xd6bcc), (0xe01b8, 0xe0294),
             (0x382650, 0x3828f0), (0x383524, 0x3836d4),
             (0xe0150, 0xe01b8), (0xe0750, 0xe07c0), (0x51da8, 0x51db8)]
    for start, end in spans:
        evidence.update(range(start, end, 4))
    report = {
        'source': str(source.relative_to(ROOT)), 'load_base': hex(BASE),
        'method': 'Static comparison-tree interpretation; each reached output is checked against its original semantic logging label. No ARM code executed.',
        'rows': rows, 'semantic_mapping': mapping,
        'field_correspondence': {
            'nx300m_dial_mode_0x49': 'NX500 CAPTURE_MODE 0x77; both convert USERDATA_DIALMODE.',
            'nx300m_capture_mode_0x41': 'NX500 APP_MODE 0x6f; both convert USERDATA_SHOOTINGMODE.',
            'nx300m_picture_wizard_0x2c': 'NX500 EFFECT_MODE 0x4f; both convert USERDATA_PW.',
        },
        'ordinary_pasm_standard_and_custom1_selectors': selectors,
        'selector_evidence': {
            'default': 'Builders read IQ EFFECT_MODE 0x4f and set mix=int(effect==15), row_sum_offset=0.',
            'smartpro': 'Only APP_MODE==1 and CAPTURE_MODE==6 and 3D_MODE==0 selects SmartPro arrays.',
            'auto': 'Only CAPTURE_MODE==0 enters builder A auto-scene overrides.',
            'ordinary_pasm': 'Verified source capture P/A/S/M=1/2/3/4 excludes both overrides; PW_STANDARD=0 and PW_CUSTOM1=16 both yield mix=0, offset=0, delta=0.',
        },
        'scope': 'Semantic enum correspondence and ordinary mode selector proof only; no cross-camera image equivalence claim.',
    }
    output = ROOT/'outputs/metol/nx500-mode-mapping'
    output.with_suffix('.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    lines = ['# NX500 1.12 original P7 static evidence, virtual address = BASE + file offset.',
             '# Sparse reached instruction listing; data words may decode as instructions.']
    for offset in sorted(evidence):
        instruction = next(decoder.disasm(data[offset:offset+4], BASE+offset))
        lines.append(f'{instruction.address:08x} [{offset:08x}] {instruction.bytes.hex():8s} '
                     f'{instruction.mnemonic:8s} {instruction.op_str}')
    output.with_suffix('.asm').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    print(json.dumps({'rows': [{k: row[k] for k in ('name','raw','iq_id','iq_value')}
                              for row in rows], 'semantic_mapping': mapping,
                      'ordinary_pasm_selectors_zero': True}, indent=2))


if __name__ == '__main__':
    main()
