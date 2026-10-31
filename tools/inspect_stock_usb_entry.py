"""Record the NX300M 1.15 USB command path without executing firmware or USB I/O."""
import io
import json
import os
from pathlib import Path
import struct
import sys
import tarfile

os.environ.setdefault('CONDA_PREFIX', sys.prefix)
from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM
from elftools.elf.elffile import ELFFile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/deployment'


def main():
    member = 'usr/bin/mtp-responder'
    with tarfile.open(ROOT / 'work/stock-1.15/rootfs.tar') as archive:
        raw = archive.extractfile(member).read()
    elf = ELFFile(io.BytesIO(raw))
    if elf['e_machine'] != 'EM_ARM' or not elf.little_endian or elf.elfclass != 32:
        raise ValueError('Expected the stock little-endian ARM32 ELF')
    segments = [s for s in elf.iter_segments() if s['p_type'] == 'PT_LOAD']

    def read(address, size):
        for segment in segments:
            if segment['p_vaddr'] <= address and address + size <= segment['p_vaddr'] + segment['p_filesz']:
                offset = segment['p_offset'] + address - segment['p_vaddr']
                return raw[offset:offset + size]
        raise ValueError(f'Address range not backed by ELF bytes: {address:#x}+{size:#x}')

    def word(address):
        return struct.unpack('<I', read(address, 4))[0]

    decoder = Cs(CS_ARCH_ARM, CS_MODE_ARM)
    decoder.skipdata = True
    dynamic = list(elf.get_section_by_name('.dynsym').iter_symbols())
    plt = elf.get_section_by_name('.plt')
    imports = {
        plt['sh_addr'] + 20 + index * 12: dynamic[r['r_info_sym']].name
        for index, r in enumerate(elf.get_section_by_name('.rel.plt').iter_relocations())
    }
    OUT.mkdir(parents=True, exist_ok=True)
    for name, start, size in (
        ('mtp-operation-dispatch.asm', 0x28da4, 0x7d0),
        ('mtp-command-validation.asm', 0x1dc0c, 0x230),
        ('mtp-command-receive.asm', 0x3ac10, 0x1c0),
    ):
        lines = [f'## {member} ARM range {start:#x}+{size:#x}; static listing only']
        for ins in decoder.disasm(read(start, size), start):
            annotation = ''
            if ins.mnemonic in ('bl', 'b') and ins.op_str.startswith('#0x'):
                target = int(ins.op_str[1:], 16)
                if target in imports:
                    annotation = f' ; {imports[target]}@plt'
            lines.append(f'{ins.address:08x} {ins.mnemonic:8} {ins.op_str}{annotation}')
        (OUT / name).write_text('\n'.join(lines) + '\n', encoding='utf-8')

    names = {0x1dc0c: 'validate/copy command', 0x1da20: 'decode command container',
             0x28da4: 'operation dispatcher', 0x2205c: 'selfTestMtpHandle',
             0x22758: 'TMS handler', 0x2288c: 'adjustment handler', 0x25b94: 'vendor handler'}
    section = elf.get_section_by_name('.text')
    data = section.data()
    calls = []
    for offset in range(0, len(data) - 3, 4):
        instruction = struct.unpack_from('<I', data, offset)[0]
        if instruction & 0x0e000000 != 0x0a000000 or instruction >> 28 == 15:
            continue
        displacement = instruction & 0xffffff
        if displacement & 0x800000:
            displacement -= 0x1000000
        address = section['sh_addr'] + offset
        target = address + 8 + 4 * displacement
        if target in names:
            calls.append({'address': hex(address), 'target': hex(target), 'name': names[target],
                          'kind': 'BL' if instruction & 0x1000000 else 'B'})

    # Both PC literals must identify the same buffer; BSS has no file bytes.
    copy_buffer = word(0x1de30)
    dispatch_buffer = word(0x226ec)
    if copy_buffer != dispatch_buffer:
        raise ValueError('Command copy and self-test dispatch buffers differ')
    local = OUT / 'stock' / member
    report = {
        'source': 'work/stock-1.15/rootfs.tar:' + member,
        'local_extraction_matches_stock': local.read_bytes() == raw if local.is_file() else None,
        'scope': 'Static ARM inspection; no firmware execution, USB requests or device writes',
        'direct_branches': calls,
        'reviewed_operation_routes': [
            {'operation': '0x1011', 'handler': '0x2205c', 'call_sites': ['0x2910c', '0x29338'],
             'meaning': 'SelfTest routes to the production script bridge; not a read-only probe'},
            {'operation': '0x9000', 'handler': '0x22758', 'call_sites': ['0x29380'],
             'meaning': 'TMS production subcommand dispatch'},
            {'operation': '0x9102', 'handler': '0x2288c', 'call_sites': ['0x292f0'],
             'meaning': 'Production adjustment path'},
            {'operation': '0x9001', 'handler': '0x25b94', 'call_sites': ['0x291ac'],
             'meaning': 'Vendor handler; no generic shell established'},
        ],
        'command_input': {
            'validation_function': '0x1dc0c',
            'selftest_branch': '0x1dd78',
            'copy_call': '0x1ddbc',
            'source': 'received buffer + 0x10',
            'length': 'received length - 0x10',
            'destination': hex(copy_buffer),
            'buffer_clear_bytes': 100,
            'dispatch': '0x22190 passes this buffer to vconf_set_str via 0x2f698',
        },
        'remaining_questions': [
            'Actual USB framing, endpoint access and host driver ownership on a physical NX300M',
            'Production-mode gate and interpreter readiness on unmodified firmware',
            'A bounded command with verified read-only behavior and reliable completion',
            'Whether a temporary process execution entry exists without persistent changes',
        ],
        'observed_cautions': [
            'The inspected SelfTest copy branch has no local length bound before memcpy',
            'selfTestMtpHandle waits for a shared completion flag without a local timeout',
            'A production script parser is not evidence of a general-purpose OS shell',
        ],
        'physical_camera_verified': False,
    }
    path = OUT / 'mtp-host-entry-report.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'report': str(path), 'direct_branches': len(calls),
                      'shared_command_buffer': hex(copy_buffer),
                      'local_extraction_matches_stock': report['local_extraction_matches_stock'],
                      'physical_camera_verified': False}, ensure_ascii=False))


if __name__ == '__main__':
    main()
