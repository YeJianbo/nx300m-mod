"""Inventory defined DPC-related symbols in the extracted stock firmware libraries.

This is symbol discovery, not a proof that stripped pixel code is absent.
"""
import io
import json
from pathlib import Path

from elftools.elf.elffile import ELFFile

PROJECT = Path(__file__).resolve().parents[1]
ROOT = Path('//wsl.localhost/Debian/opt/nx300m-lab/stock-1.15')


def main():
    libraries = sorted((ROOT/'usr/lib').glob('*.so*'))
    libraries.append(ROOT/'usr/bin/di-camera-app-nx300')
    checked, skipped, hits = [], [], []
    for path in libraries:
        if not path.is_file():
            continue
        data = path.read_bytes()
        if not data.startswith(b'\x7fELF'):
            continue
        elf = ELFFile(io.BytesIO(data))
        if elf['e_machine'] != 'EM_ARM':
            continue
        table = elf.get_section_by_name('.dynsym')
        checked.append(str(path.relative_to(ROOT)))
        if table is None:
            skipped.append(str(path.relative_to(ROOT)))
            continue
        matches = []
        for symbol in table.iter_symbols():
            name = symbol.name
            low = name.lower()
            if not any(term in low for term in ('dpc', 'defect', 'hotpixel', 'hot_pixel')):
                continue
            if 'ssdpcp' in low:  # UPnP SSDP control-point network classes.
                continue
            if symbol['st_shndx'] == 'SHN_UNDEF':
                continue
            matches.append({'name': name, 'address': hex(symbol['st_value']),
                            'size': symbol['st_size'], 'type': symbol['st_info']['type']})
        if matches:
            hits.append({'library': str(path.relative_to(ROOT)), 'symbols': matches})
    result = {
        'scope': 'Top-level usr/lib/*.so* ARM ELF dynamic symbols and di-camera-app-nx300 only',
        'files_checked': checked, 'files_without_dynsym': skipped,
        'defined_symbol_matches': hits,
        'negative_search_limit': 'No match does not exclude stripped, statically linked, differently named, or nested plugin pixel algorithms.',
        'cpu_reference_algorithm_verified': False,
    }
    output = PROJECT/'outputs/firmware-runtime/pp-memory-dpc-symbol-inventory.json'
    output.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'elf_files_checked': len(checked),
                      'matching_libraries': [entry['library'] for entry in hits],
                      'defined_symbols': sum(len(entry['symbols']) for entry in hits)}, indent=2))


if __name__ == '__main__':
    main()
