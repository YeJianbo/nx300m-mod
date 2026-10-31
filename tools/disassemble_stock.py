"""Inspect named functions in the freshly extracted stock ARM ELF files."""
import argparse
from bisect import bisect_right
import io
import os
from pathlib import Path
import struct
import sys
os.environ.setdefault('CONDA_PREFIX', sys.prefix)
from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM, CS_MODE_THUMB
from elftools.elf.elffile import ELFFile

ROOT = Path('//wsl.localhost/Debian/opt/nx300m-lab/stock-1.15')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('library')
    parser.add_argument('names', nargs='*')
    parser.add_argument('--range', nargs=2, type=lambda value: int(value, 0), metavar=('START', 'SIZE'),
                        help='Decode an ARM address range without requiring a named symbol')
    parser.add_argument('--list', action='store_true', help='List matching symbols without disassembly')
    parser.add_argument('--local', action='store_true', help='Read the named local ELF instead of a stock root-relative file')
    parser.add_argument('--callers', action='store_true', help='Find direct ARM BL callers of matching symbols/PLT entries')
    args = parser.parse_args()
    if not args.names and not args.range: parser.error('Supply symbol names or --range START SIZE')
    # pyelftools seeks repeatedly; read the WSL export once before decoding.
    source = Path(args.library) if args.local else ROOT / args.library.lstrip('/')
    with io.BytesIO(source.read_bytes()) as stream:
        elf = ELFFile(stream)
        dynamic = list(elf.get_section_by_name('.dynsym').iter_symbols())
        symbols = dynamic[:]
        static = elf.get_section_by_name('.symtab')
        if static is not None:
            known = {sym.name for sym in symbols}
            symbols.extend(sym for sym in static.iter_symbols() if sym.name not in known)
        targets = {sym['st_value'] & ~1: sym.name for sym in symbols if sym['st_shndx'] != 'SHN_UNDEF'}
        plt, relocations = elf.get_section_by_name('.plt'), elf.get_section_by_name('.rel.plt')
        if plt is not None and relocations is not None:
            # ARM GNU ld PLT: 20-byte resolver header followed by 12-byte slots.
            for index, relocation in enumerate(relocations.iter_relocations()):
                targets[plt['sh_addr'] + 20 + index * 12] = dynamic[relocation['r_info_sym']].name + '@plt'
        if args.callers:
            wanted = {address: name for address, name in targets.items() if any(key in name for key in args.names)}
            functions = sorted((s['st_value'] & ~1, s['st_size'], s.name) for s in symbols
                               if s['st_info']['type'] == 'STT_FUNC' and s['st_size'] and not s['st_value'] & 1)
            starts = [entry[0] for entry in functions]
            section = elf.get_section_by_name('.text')
            data, start = section.data(), section['sh_addr']
            for offset in range(0, len(data)-3, 4):
                word = struct.unpack_from('<I', data, offset)[0]
                if word & 0x0f000000 != 0x0b000000 or word >> 28 == 15: continue
                displacement = word & 0xffffff
                if displacement & 0x800000: displacement -= 0x1000000
                address = start+offset
                target = address+8+displacement*4
                if target not in wanted: continue
                index = bisect_right(starts, address)-1
                if index < 0: continue
                function, size, name = functions[index]
                if address >= function+size: continue
                print(f'{address:#010x} {name}+{address-function:#x} -> {wanted[target]}')
            return
        selected = []
        if args.range:
            address, size = args.range
            if size <= 0: parser.error('Range size must be positive')
            selected.append({'name': 'address-range', 'st_value': address, 'st_size': size})
        for symbol in symbols:
            if not any(name in symbol.name for name in args.names): continue
            selected.append(symbol)
        for symbol in selected:
            address, size = symbol['st_value'] & ~1, symbol['st_size']
            name = symbol['name'] if isinstance(symbol, dict) else symbol.name
            print(f'\n## {name} {address:#x} {size}')
            if args.list: continue
            if not size: continue
            if not isinstance(symbol, dict) and symbol['st_info']['type'] != 'STT_FUNC': continue
            segment = next(seg for seg in elf.iter_segments() if seg['p_type'] == 'PT_LOAD' and seg['p_vaddr'] <= address < seg['p_vaddr'] + seg['p_filesz'])
            stream.seek(segment['p_offset'] + address - segment['p_vaddr'])
            decoder = Cs(CS_ARCH_ARM, CS_MODE_THUMB if symbol['st_value'] & 1 else CS_MODE_ARM)
            # Stock GCC places literal pools inside long functions. An invalid
            # pool word must not truncate all later blocks in the symbol range.
            # This remains a linear listing: pool words can also decode as ARM
            # instructions, so control-flow reachability needs separate review.
            decoder.skipdata = True
            for instruction in decoder.disasm(stream.read(size), address):
                target = ''
                if instruction.mnemonic in ('bl', 'blx', 'b') and instruction.op_str.startswith('#0x'):
                    target = targets.get(int(instruction.op_str[1:], 16), '')
                print(f'{instruction.address:08x} {instruction.mnemonic:8s} {instruction.op_str}' + (f' ; {target}' if target else ''))

if __name__ == '__main__': main()
