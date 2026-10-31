"""Locate stock X11 key-name references without modifying firmware."""
import json
import io
import struct
from collections import Counter
from pathlib import Path
from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM, CS_MODE_THUMB
from elftools.elf.elffile import ELFFile

ROOT = Path('//wsl.localhost/Debian/opt/nx300m-lab/stock-1.15')
KEYS = ['Menu', 'KP_Left', 'KP_Right', 'KP_Up', 'KP_Down', 'KP_Enter', 'XF86Tools', 'XF86ScrollUp', 'XF86ScrollDown', 'XF86AudioMedia', 'XF86Display', 'XF86MonBrightnessDown', 'XF86MonBrightnessUp']

def main():
    with io.BytesIO((ROOT / 'usr/bin/di-camera-app-nx300').read_bytes()) as stream:
        elf = ELFFile(stream)
        sections = [(section.name, section['sh_addr'], section.data()) for section in elf.iter_sections() if section['sh_type'] in ('SHT_PROGBITS',)]
        strings = {}
        for name, base, data in sections:
            for key in KEYS:
                start = 0
                while (index := data.find(key.encode() + b'\0', start)) >= 0:
                    if index == 0 or data[index - 1] == 0: strings[base + index] = key
                    start = index + 1
        text = elf.get_section_by_name('.text')
        data, base = text.data(), text['sh_addr']
        decoder = Cs(CS_ARCH_ARM, CS_MODE_ARM); decoder.skipdata = True
        instructions = list(decoder.disasm(data, base))
        pic_bases = Counter()
        for before, after in zip(instructions, instructions[1:]):
            if before.mnemonic == 'ldr' and before.op_str.startswith('r4, [pc') and after.mnemonic == 'add' and after.op_str == 'r4, pc, r4':
                offset = int(before.op_str.split('#')[1].split(']')[0], 0) if '#' in before.op_str else 0
                position = before.address + 8 + offset - base
                if 0 <= position <= len(data)-4: pic_bases[(struct.unpack_from('<I', data, position)[0] + after.address + 8) & 0xffffffff] += 1
        print('PIC bases:', [(hex(value), count) for value, count in pic_bases.most_common(3)])
        refs = {}
        for name, base, data in sections:
            for address, key in strings.items():
                start = 0
                for pic in [0, *[value for value, _ in pic_bases.most_common(3)]]:
                    start = 0
                    while (index := data.find(struct.pack('<I', (address-pic)&0xffffffff), start)) >= 0:
                        refs[base + index] = (key, name, index)
                        start = index + 1
        print(json.dumps({hex(address): {'key':key, 'section':name} for address, (key, name, _) in refs.items()}, indent=2))
        data, base = text.data(), text['sh_addr']
        lines = []
        for mode, adjustment in ((CS_MODE_ARM, 8), (CS_MODE_THUMB, 4)):
            decoder = Cs(CS_ARCH_ARM, mode); decoder.skipdata = True
            decoded = list(decoder.disasm(data, base))
            for before, after in zip(decoded, decoded[1:]):
                if before.mnemonic == 'movw' and after.mnemonic == 'movt' and before.op_str.split(',')[0] == after.op_str.split(',')[0]:
                    target = int(before.op_str.split('#')[1], 0) | (int(after.op_str.split('#')[1], 0) << 16)
                    if target in strings:
                        lines.append(f'\n## {strings[target]} MOVW/MOVT reference at {before.address:#x} mode={mode}')
                        begin = max(before.address-16, base)
                        for near in decoder.disasm(data[begin-base:before.address-base+110], begin):
                            lines.append(f'{near.address:08x} {near.mnemonic:8s} {near.op_str}')
                if not before.mnemonic.startswith('ldr') or ', [pc' not in before.op_str or not after.mnemonic.startswith('add') or 'pc' not in after.op_str: continue
                register = before.op_str.split(',')[0]
                if register not in after.op_str: continue
                offset = int(before.op_str.split('#')[1].split(']')[0], 0) if '#' in before.op_str else 0
                position = ((before.address + adjustment) & (~3 if mode == CS_MODE_THUMB else -1)) + offset - base
                if not 0 <= position <= len(data)-4: continue
                literal = struct.unpack_from('<I', data, position)[0]
                target = (literal + after.address + adjustment) & 0xffffffff
                key = strings.get(target) or strings.get(target & ~3)
                if key:
                    lines.append(f'\n## {key} relative reference at {before.address:#x} mode={mode}')
                    begin = max(before.address-16, base)
                    for near in decoder.disasm(data[begin-base:before.address-base+110], begin):
                        lines.append(f'{near.address:08x} {near.mnemonic:8s} {near.op_str}')
        for ins in instructions:
            if ins.mnemonic != 'ldr' or ', [pc' not in ins.op_str: continue
            literal = ins.address + 8
            if '#' in ins.op_str:
                value = ins.op_str.split('#')[1].split(']')[0]
                literal += int(value, 0)
            if literal in refs:
                key = refs[literal][0]
                lines.append(f'\n## {key} load at {ins.address:#x}')
                begin = max(ins.address - 20, base)
                for near in Cs(CS_ARCH_ARM, CS_MODE_ARM).disasm(data[begin-base:ins.address-base+100], begin):
                    lines.append(f'{near.address:08x} {near.mnemonic:8s} {near.op_str}')
        output = Path(__file__).resolve().parents[1] / 'outputs/firmware-runtime/keymap-stock.asm'
        output.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        print(output)

if __name__ == '__main__': main()
