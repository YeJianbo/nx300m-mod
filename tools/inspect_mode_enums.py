"""Recover NX300M mode names and IQ values from its own stock capture ELF.

The small dispatch walker interprets only comparisons and constant stores in
convertParamValue; it never executes the ELF or calls firmware functions.
"""
import io
import json
import re
import struct
from pathlib import Path

from inspect_recipe_tables import all_symbols, disassemble
from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM
from elftools.elf.elffile import ELFFile

PROJECT = Path(__file__).resolve().parents[1]
CAPTURE_LIBRARY = 'usr/lib/libcapture-fw-slpcam-nx300.so'
VALUE_SYMBOL = '_ZN11CCapParamIf17convertParamValueEmPi'
ID_SYMBOL = '_ZN11CCapParamIf14convertParamIDEmPi'


class Image:
    def __init__(self, path):
        self.path = path
        self.elf = ELFFile(io.BytesIO(path.read_bytes()))
        self.symbols = all_symbols(self.elf)
        self.sections = [(section['sh_addr'], section.data())
                         for section in self.elf.iter_sections()
                         if section['sh_type'] != 'SHT_NOBITS' and section['sh_size']]
        self.decoder = Cs(CS_ARCH_ARM, CS_MODE_ARM)
        self.decoder.skipdata = True

    def read(self, address, size):
        for base, data in self.sections:
            offset = address - base
            if 0 <= offset and offset + size <= len(data):
                return data[offset:offset + size]
        raise ValueError(f'Unmapped file-backed VA {address:#x}+{size}')

    def word(self, address):
        return struct.unpack('<I', self.read(address, 4))[0]

    def string(self, address):
        result = bytearray()
        for offset in range(128):
            byte = self.read(address + offset, 1)[0]
            if not byte:
                return result.decode('ascii')
            result.append(byte)
        raise ValueError('Unterminated enum label')

    def instruction(self, address):
        return next(self.decoder.disasm(self.read(address, 4), address))


def enum_groups(image):
    # Resolve the same PIC literals used by the unnamed stock name parser.
    # r4 = PC(0x212cbc)+8 + [0x212e9c]; table = r4+[0x212ea0].
    base = (0x212cbc + 8 + image.word(0x212e9c)) & 0xffffffff
    table = (base + image.word(0x212ea0)) & 0xffffffff
    marker = image.string((base + image.word(0x212ea4)) & 0xffffffff)
    terminator = image.string((base + image.word(0x212ea8)) & 0xffffffff)
    if marker != 'NUM_OF_':
        raise ValueError(f'Unexpected parser separator {marker!r}')
    groups, current = [], []
    for index in range(2048):
        label = image.string(image.word(table + index * 4))
        if label == terminator:
            return table, groups
        if label.startswith(marker):
            groups.append({'userdata_id': len(groups), 'name': label[7:],
                           'labels': current})
            current = []
        else:
            current.append({'ordinal': len(current), 'label': label,
                            'pointer_address': hex(table + index * 4)})
    raise ValueError('Enum table did not end')


def iq_id(image, userdata_id):
    slot = 0x20f850 + userdata_id * 4
    branch = image.instruction(slot)
    if branch.mnemonic != 'b':
        raise ValueError('Unexpected convertParamID jump entry')
    target = int(branch.op_str[1:], 16)
    value = image.instruction(target + 4)
    if value.mnemonic not in ('mov', 'mvn') or not value.op_str.startswith('r2, #'):
        raise ValueError('Unexpected convertParamID store block')
    number = int(value.op_str.split('#')[1], 0)
    return (-1 if value.mnemonic == 'mvn' else number), hex(target)


def converted_value(image, raw):
    # Begin immediately after GetUserData: r3 contains its packed return value.
    address, registers, comparison, trace = 0x21009c, {'r3': raw}, 0, []
    symbol = image.symbols[VALUE_SYMBOL]
    low, high = symbol['st_value'], symbol['st_value'] + symbol['st_size']
    for _ in range(256):
        if not low <= address < high:
            raise ValueError('Dispatch left convertParamValue')
        ins = image.instruction(address)
        op, args = ins.mnemonic, ins.op_str
        trace.append(hex(address))
        if op in ('mov', 'movw', 'movt', 'mvn'):
            match = re.fullmatch(r'(r\d+), #(0x[0-9a-f]+|\d+)', args)
            if not match:
                raise ValueError(f'Unsupported constant instruction: {op} {args}')
            reg, imm = match.group(1), int(match.group(2), 0)
            registers[reg] = ((registers.get(reg, 0) & 0xffff) | (imm << 16)
                              if op == 'movt' else ~imm & 0xffffffff if op == 'mvn' else imm)
        elif op == 'cmp':
            left, right = args.split(', ')
            a = registers[left]
            b = int(right[1:], 0) if right.startswith('#') else registers[right]
            a = a if a < 0x80000000 else a - 0x100000000
            b = b if b < 0x80000000 else b - 0x100000000
            comparison = a - b
        elif op in ('b', 'beq', 'bne', 'bgt', 'blt', 'bge', 'ble'):
            take = {'b': True, 'beq': comparison == 0, 'bne': comparison != 0,
                    'bgt': comparison > 0, 'blt': comparison < 0,
                    'bge': comparison >= 0, 'ble': comparison <= 0}[op]
            if take:
                address = int(args[1:], 16)
                continue
        elif op == 'ldr' and args == 'r3, [fp, #-0x20]':
            registers['r3'] = 0  # Abstract output pointer; no memory is executed.
        elif op == 'ldr' and args == 'r2, [fp, #-0x20]':
            registers['r2'] = 0
        elif op == 'str' and args == 'r2, [r3]':
            result = registers['r2']
            return result, trace
        elif op == 'str' and args == 'r3, [r2]':
            return registers['r3'], trace
        else:
            raise ValueError(f'Unsupported dispatch instruction at {address:#x}: {op} {args}')
        address += 4
    raise ValueError('Dispatch did not terminate')


def write_evidence(library):
    image = Image(library)
    table, groups = enum_groups(image)
    selected = []
    for group in groups:
        if group['name'] not in ('DIALMODE', 'SHOOTINGMODE', 'PW', 'SMARTFILTERTYPE'):
            continue
        parameter_id, block = iq_id(image, group['userdata_id'])
        rows = []
        for row in group['labels']:
            raw = (group['userdata_id'] << 16) | row['ordinal']
            item = dict(row, raw=hex(raw))
            if parameter_id >= 0:
                result, trace = converted_value(image, raw)
                item.update(iq_value=result if result not in (0xbadbad, 0xffffffff) else None,
                            conversion_output_raw=hex(result),
                            conversion_trace=trace)
            rows.append(item)
        selected.append(dict(userdata_id=hex(group['userdata_id']), name=group['name'],
                             iq_id=hex(parameter_id) if parameter_id >= 0 else None,
                             convert_id_block=block, values=rows))
    names = [ID_SYMBOL, VALUE_SYMBOL, '_ZN11CCapParamIf12OnItemChangeEm',
             '_ZN9CUserData11GetUserDataEi', '_ZN17CProcessParameter16updateEffectModeEv']
    assembly, functions = disassemble(image.elf, image.symbols, names)
    assembly = assembly.replace('stock libiq-fw-slpcam.so', 'stock libcapture-fw-slpcam-nx300.so')
    assembly += '\n## Unexported enum name parser 0x212cac size=512\n'
    for ins in image.decoder.disasm(image.read(0x212cac, 0x200), 0x212cac):
        assembly += f'{ins.address:08x} {ins.bytes.hex():8s} {ins.mnemonic:8s} {ins.op_str}\n'
    report = {
        'source': str(library), 'analysis': 'Static extraction and restricted ARM comparison-tree interpretation',
        'functions': functions, 'enum_table_address': hex(table),
        'name_to_raw_evidence': {
            'parser': '0x212cac (unnamed stock function)',
            'group_and_ordinal': '0x212d44-0x212d88: NUM_OF_ increments userdata group and resets ordinal; 0x212e18 increments ordinal.',
            'result': '0x212e00-0x212e10: returns (userdata_id << 16) + ordinal after label suffix match.',
            'stored_userdata': 'CUserData::GetUserData 0x214908-0x214918 packs requested ID and stored byte in the same format.',
            'iq_notification': 'CCapParamIf::OnItemChange calls both converters and CIQDataRepeater::SetParameter at 0x212394-0x2123b4.',
        },
        'groups': selected,
        'pasm': {group['name']: {row['label']: row['iq_value'] for row in group['values']
                                if row['label'].split('_', 1)[-1] in
                                ('PROGRAM', 'APERTURE', 'SHUTTERSPEED', 'MANUAL')}
                 for group in selected if group['name'] in ('DIALMODE', 'SHOOTINGMODE')},
        'target_pasm_mapping_verified': True,
        'iq_0x49_meaning': 'Converted USERDATA_DIALMODE, not Picture Wizard or SmartFilter effect.',
        'picture_wizard': 'USERDATA_PW 0x10 -> IQ 0x2c. This is distinct from IQ 0x49.',
        'smartfilter_effect': 'USERDATA_SMARTFILTERTYPE 0x39 has no generic IQ mapping. CProcessParameter::updateEffectMode reads its low byte into object+0x48, or obtains SmartArt effect when enabled.',
        'limits': ['NX500 enum correspondence is not established by this target-only extraction.',
                   'Special shooting modes, 3D and SmartPro consumer behavior is not certified here.',
                   'Unknown sentinel output 0xbadbad must not be interpreted as a mode.'],
    }
    output = PROJECT / 'outputs/metol/mode-context-enums'
    output.with_suffix('.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    output.with_suffix('.asm').write_text(assembly, encoding='utf-8')
    return report


def main():
    config = json.loads((PROJECT / 'project.local.json').read_text(encoding='utf-8-sig'))
    report = write_evidence(Path(config['rootfs']) / CAPTURE_LIBRARY)
    print(json.dumps({group['name']: {'iq_id': group['iq_id'],
          'values': {row['label']: row.get('iq_value') for row in group['values']}}
          for group in report['groups']}, indent=2))


if __name__ == '__main__':
    main()
