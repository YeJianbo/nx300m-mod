"""Read an ARM capture core with a same-process /proc/maps snapshot."""
import argparse
import io
import json
from pathlib import Path
import struct

from elftools.elf.elffile import ELFFile

ROOT = Path('//wsl.localhost/Debian/opt/nx300m-lab/stock-1.15')
PROJECT = Path(__file__).resolve().parents[1]
GUEST = '/opt/nx300m-lab/stock-1.15'


def analyze(core, maps):
    rows = []
    for line in maps.read_text().splitlines():
        fields = line.split(None, 5)
        if len(fields) != 6 or not fields[5].startswith(GUEST+'/'): continue
        path = fields[5].removeprefix(GUEST)
        if not ('.so' in path or path == '/usr/bin/di-camera-app-nx300'): continue
        start, end = (int(value, 16) for value in fields[0].split('-'))
        rows.append((start, end, int(fields[2], 16), path))
    app = ELFFile(io.BytesIO((ROOT/'usr/bin/di-camera-app-nx300').read_bytes()))
    first_load = next(s['p_vaddr'] for s in app.iter_segments() if s['p_type'] == 'PT_LOAD')
    base = next(a for a, _, offset, path in rows
                if path == '/usr/bin/di-camera-app-nx300' and offset == 0)-first_load
    cache = {}

    def resolve(address):
        for start, end, offset, path in rows:
            if not start <= address+base < end: continue
            relative = address+base-start+offset
            if path not in cache:
                local = ROOT/path.lstrip('/')
                if path.startswith('/tmp/'):
                    # This file is in the private namespace, not the host root.
                    local = PROJECT/'outputs/firmware-runtime'/Path(path).name
                if not local.is_file():
                    cache[path] = []
                else:
                    elf = ELFFile(io.BytesIO(local.read_bytes()))
                    symbols = elf.get_section_by_name('.dynsym')
                    cache[path] = [(s.name, s['st_value'], s['st_size'])
                                   for s in symbols.iter_symbols()
                                   if s['st_size'] and s['st_info']['type'] == 'STT_FUNC'] if symbols else []
            matched = next(((name, value) for name, value, size in cache[path]
                            if value <= relative < value+size), None)
            return {'address': hex(address), 'library': path, 'offset': hex(relative),
                    'symbol': matched[0] if matched else None,
                    'symbol_offset': hex(relative-matched[1]) if matched else None}
        return None

    with core.open('rb') as stream:
        elf = ELFFile(stream)
        status = next(note['n_desc'] for segment in elf.iter_segments()
                      if segment['p_type'] == 'PT_NOTE' for note in segment.iter_notes()
                      if note['n_type'] == 'NT_PRSTATUS')
        registers = struct.unpack_from('<18I', status, 72)
        sp = registers[13]
        segment = next(s for s in elf.iter_segments() if s['p_type'] == 'PT_LOAD'
                       and s['p_vaddr'] <= sp < sp+2048 <= s['p_vaddr']+s['p_filesz'])
        stream.seek(segment['p_offset']+sp-segment['p_vaddr'])
        stack = struct.unpack('<512I', stream.read(2048))
    candidates = []
    for index, value in enumerate(stack):
        resolved = resolve(value & ~1)
        if resolved and (resolved['symbol'] or 'libudd.so' in resolved['library']):
            candidates.append({'stack_offset': hex(index*4), **resolved})
    return {'core': str(core), 'maps': str(maps), 'guest_base': hex(base),
            'pc': resolve(registers[15]), 'lr': resolve(registers[14]), 'sp': hex(sp),
            'registers': [hex(value) for value in registers],
            'stack_code_candidates': candidates,
            'scope': 'actual core and pre-capture maps; stack candidates are not a reconstructed call stack; runtime .so files must be the same build as the captured session'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('core', type=Path)
    parser.add_argument('maps', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    report = analyze(args.core, args.maps)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({**report, 'stack_code_candidates': report['stack_code_candidates'][:24]}, ensure_ascii=False, indent=2))


if __name__ == '__main__': main()
