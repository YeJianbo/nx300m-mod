"""Inspect only stock libsif's WriteChunks and its file-operation providers.

Read-only static evidence. Does not launch or enter any camera session.
"""
import argparse
import json
from pathlib import Path

from inspect_gamma_hook import Stock

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'outputs/firmware-runtime/capture-save'
WRITE_CHUNKS = '_ZN14DscFileHandler11WriteChunksEv'
MAKE = '_ZN11DscOsalFile4MakeEPKc'
CREATE = '_ZN11DscOsalFile6CreateEPKcNS_15DscOsalFileFlagE'
WRITE = '_ZN11DscOsalFile5WriteEPKvPm'
CLOSE = '_ZN11DscOsalFile5CloseEv'
FUNCTIONS = {
    WRITE_CHUNKS: 0x274a4, MAKE: 0x3c894, CREATE: 0x3c8bc,
    WRITE: 0x3caa4, CLOSE: 0x3cbe0,
    '_ZN11DscOsalFileC1Ev': 0x3c7a8, '_ZN11DscOsalFileD1Ev': 0x3c7fc,
    '_ZNKSt6vectorI6sChunkSaIS0_EE4sizeEv': 0x267d4,
    '_ZNSt6vectorI6sChunkSaIS0_EEixEj': 0x28468,
}
CALLS = {
    0x275f0: (MAKE, 'make_noop'), 0x27698: (CREATE, 'create_wb'),
    0x2786c: (WRITE, 'full_block_write'), 0x27918: (WRITE, 'remaining_block_write'),
    0x279cc: (WRITE, 'whole_chunk_write'),
    0x2762c: (CLOSE, 'make_failed'), 0x2766c: (CLOSE, 'stop_before_create'),
    0x276d4: (CLOSE, 'create_failed'), 0x277b4: (CLOSE, 'stop_in_block_loop'),
    0x278a8: (CLOSE, 'block_write_failed'), 0x27954: (CLOSE, 'remaining_write_failed'),
    0x27a08: (CLOSE, 'chunk_write_failed'), 0x27a48: (CLOSE, 'normal_loop_end'),
    0x3c90c: ('fopen64', 'create_flag_2'), 0x3c934: ('fopen64', 'create_flag_1'),
    0x3c95c: ('fopen64', 'create_flag_3'),
    0x3cadc: ('fwrite', 'size_1_count_requested'),
    0x3cc10: ('fflush', 'close_flush'), 0x3cc18: ('fclose', 'close_stream'),
    0x3c82c: (CLOSE, 'destructor_second_close'),
}
ANCHORS = {
    0x27508: ('mov', 'r4, #1'), 0x27640: ('cmp', 'r3, #2'),
    0x27788: ('cmp', 'r3, #2'), 0x27a7c: ('mov', 'r4, #1'),
    0x3c8a8: ('mov', 'r3, #1'), 0x3cad8: ('mov', 'r1, #1'),
    0x3caf4: ('cmp', 'r2, r3'), 0x3cb04: ('str', 'r2, [r3]'),
    0x3cb48: ('mov', 'r3, #0'), 0x3cb50: ('mov', 'r3, #1'),
    0x3cc24: ('str', 'r2, [r3, #4]'),
    0x267fc: ('asr', 'r3, r3, #3'), 0x28488: ('lsl', 'r3, r3, #3'),
}


def inspect(library):
    stock = Stock(library)
    dynamic = stock.elf.get_section_by_name('.dynsym')
    plt_section = stock.elf.get_section_by_name('.plt')
    plt = {}
    for index, relocation in enumerate(stock.elf.get_section_by_name('.rel.plt').iter_relocations()):
        symbol = dynamic.get_symbol(relocation['r_info_sym'])
        plt[plt_section['sh_addr']+20+index*12] = {
            'symbol': symbol.name, 'got_slot': hex(relocation['r_offset']),
            'relocation_type': relocation['r_info_type'],
        }
    flags = {tag.entry.d_tag: int(tag.entry.d_val)
             for tag in stock.elf.get_section_by_name('.dynamic').iter_tags()
             if tag.entry.d_tag in ('DT_SYMBOLIC', 'DT_FLAGS', 'DT_FLAGS_1')}
    if 'DT_SYMBOLIC' in flags or flags.get('DT_FLAGS', 0) & 2:
        raise ValueError('Symbolic binding changes LD_PRELOAD interposition assumptions')
    functions = []
    assembly = []
    for name, address in FUNCTIONS.items():
        symbol = stock.symbols[name]
        if symbol['st_value'] != address:
            raise ValueError(f'Stock symbol address changed: {name}')
        exported = dynamic.get_symbol_by_name(name)[0]
        functions.append({'symbol': name, 'address': hex(address), 'size': symbol['st_size'],
                          'binding': exported['st_info']['bind'],
                          'visibility': exported['st_other']['visibility']})
        assembly.append(f'\n{name} @ {address:#x}:')
        for instruction in stock.decoder.disasm(stock.read(address, symbol['st_size']), address):
            target = int(instruction.op_str[1:], 16) if instruction.mnemonic == 'bl' else None
            annotation = plt.get(target, {}).get('symbol', '')
            assembly.append(f'{instruction.address:08x}: {instruction.mnemonic:8} {instruction.op_str:38} {annotation}')
    calls = []
    for address, (expected, role) in CALLS.items():
        instruction = stock.instruction(address)
        if instruction.mnemonic != 'bl':
            raise ValueError(f'Not a BL call: {address:#x}')
        target = int(instruction.op_str[1:], 16)
        binding = plt[target]
        if binding['symbol'] != expected or binding['relocation_type'] != 22:
            raise ValueError(f'PLT identity changed: {address:#x}')
        calls.append({'call': hex(address), 'return_address': hex(address+4),
                      'plt_target': hex(target), 'role': role, **binding})
    for address, expected in ANCHORS.items():
        instruction = stock.instruction(address)
        if (instruction.mnemonic, instruction.op_str) != expected:
            raise ValueError(f'Instruction anchor changed: {address:#x}')
    pic_base = (0x3c8cc+8+stock.literal(0x3c8c8)[1]) & 0xffffffff
    modes = {}
    for flag, address in ((2, 0x3c900), (1, 0x3c928), (3, 0x3c950)):
        pointer = (pic_base+stock.literal(address)[1]) & 0xffffffff
        modes[str(flag)] = {'address': hex(pointer), 'mode': stock.read(pointer, 8).split(b'\0', 1)[0].decode('ascii')}
    return {
        'library': str(library), 'scope': 'one stock libsif ELF; static disassembly only',
        'camera_started': False, 'main_session_accessed': False,
        'functions': functions, 'dynamic_binding_flags': flags, 'plt_calls': calls,
        'anchors': {hex(address): list(expected) for address, expected in ANCHORS.items()},
        'create_modes': modes,
        'abi': {
            'Make': 'r0=this,r1=path; r0=1 unconditionally; no filesystem operation',
            'Create': 'r0=this,r1=path,r2=flag; FILE* at this+4; bool FILE*!=NULL',
            'Write': 'r0=this,r1=data,r2=uint32_t* in/out bytes; bool exact fwrite count; short count written back',
            'Close': 'r0=this; calls fflush then fclose if this+4 nonzero; clears pointer; no reliable return contract',
            'fflush': 'r0=FILE*; int 0 success, EOF failure',
            'fclose': 'r0=FILE*; int 0 success, EOF failure',
        },
        'chunk_layout': {'descriptor': 'self+0x64 -> image+0x2c', 'vector_begin': 0, 'vector_end': 4,
                         'chunk_stride': 8, 'chunk_buffer': 0, 'chunk_bytes': 4, 'path_offset': 12},
        'conclusions': [
            'Normal Close caller return offset 0x27a4c distinguishes loop end from both Stop-true exits.',
            'DscOsalFile::Close ignores fflush result; raw r0 is not a close-success API (empty stream leaves incoming r0).',
            'Destructor calls Close again after stream pointer cleared; this cannot establish successful closing.',
            'Write success proves exact fwrite acceptance; successful fflush and fclose are additionally required.',
            'A nested thread-local WriteChunks scope can bind OSAL object and FILE*; compare actual write bytes with a bounded, validated chunk-vector sum.',
            'All reviewed file/stdio edges use R_ARM_JUMP_SLOT PLT; wrappers can observe original calls without adding flush/close calls.',
            'Successful stdio writing and closing does not establish power-loss durability, photo validity, or capture-request association.',
        ],
    }, '\n'.join(assembly)+'\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path, default=Path('//wsl.localhost/Debian/opt/nx300m-lab/stock-1.15/usr/lib/libsif.so'))
    args = parser.parse_args()
    report, assembly = inspect(args.library)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT/'libsif-save.asm').write_text(assembly, encoding='utf-8')
    (OUTPUT/'evidence.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'output': str(OUTPUT), 'functions': len(report['functions']),
                      'verified_plt_calls': len(report['plt_calls']), 'create_modes': report['create_modes']}, indent=2))


if __name__ == '__main__':
    main()
