"""Refresh full stock accessor bytes for the read-only Custom context reader."""
import io
from pathlib import Path
from elftools.elf.elffile import ELFFile

ROOT = Path(__file__).resolve().parents[1]
SPECS = [
    ('_ZN18CAPPGUIApplication11getInstanceEv', 0xf58c8, 172),
    ('_ZN19CAPPGUIPreviewState11getInstanceEv', 0xf8fec, 172),
    ('_ZNK16CSAFStateMachine15getCurrentStateEv', 0x85e04, 40),
    ('_Z20Ui_Get_Cap_Run_Statev', 0x1bbc54, 32),
]


def main():
    source = ROOT / 'outputs/deployment/stock/usr/bin/di-camera-app-nx300'
    elf = ELFFile(io.BytesIO(source.read_bytes()))
    if elf['e_type'] != 'ET_EXEC':
        raise ValueError('Expected fixed-address NX300M 1.15 executable')
    symbols = {symbol.name: symbol for symbol in elf.get_section_by_name('.dynsym').iter_symbols()}
    lines, entries = [], []
    for index, (name, address, size) in enumerate(SPECS):
        symbol = symbols[name]
        if (symbol['st_value'], symbol['st_size']) != (address, size):
            raise ValueError(f'Unexpected stock accessor: {name}')
        segment = next(s for s in elf.iter_segments() if s['p_type'] == 'PT_LOAD' and
                       s['p_vaddr'] <= address < s['p_vaddr'] + s['p_filesz'])
        data = segment.data()[address-segment['p_vaddr']:address-segment['p_vaddr']+size]
        lines.append(f'/* {name} */\nstatic const unsigned char custom_key_bytes_{index}[]={{' +
                     ','.join(f'0x{byte:02x}' for byte in data) + '};')
        entries.append(f'{{0x{address:x},sizeof custom_key_bytes_{index},custom_key_bytes_{index}}}')
    lines.append('struct custom_key_stock_entry { uint32_t address;size_t size;const unsigned char *bytes; };')
    lines.append('static const struct custom_key_stock_entry custom_key_stock[]={' + ','.join(entries) + '};')
    header = ROOT / 'mod/metol/remote/custom_key_context.h'
    text = header.read_text(encoding='utf-8')
    begin, end = '/* BEGIN STOCK ACCESSORS */', '/* END STOCK ACCESSORS */'
    text = text[:text.index(begin)+len(begin)] + '\n' + '\n'.join(lines) + '\n' + text[text.index(end):]
    header.write_text(text, encoding='utf-8', newline='\n')
    print(f'Updated {header}: {len(SPECS)} complete stock accessors')


if __name__ == '__main__':
    main()
