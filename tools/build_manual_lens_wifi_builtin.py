"""Prepare a two-instruction stock camera ELF delta; never install or flash it.

This is an alternative to loading the opt-in shared library: the candidate
contains the admission changes in the executable itself, so they would remain
active for that executable. It is not an SD autoexec payload or a firmware file.
"""
import io
import json
from pathlib import Path
import struct

from elftools.elf.elffile import ELFFile
from build_manual_lens_wifi import OUT, PROJECT, write_wifi_stock_header
from inspect_recipe_tables import all_symbols, read_symbol


def main():
    evidence = write_wifi_stock_header(OUT)
    relative = evidence['source']
    source = PROJECT / 'outputs/deployment/stock' / relative
    if not source.is_file():
        source = Path('//wsl.localhost/Debian/opt/nx300m-lab/stock-1.15') / relative
    original = source.read_bytes()
    elf = ELFFile(io.BytesIO(original))
    symbol = all_symbols(elf)[evidence['symbol']]
    function, _, _ = read_symbol(elf, symbol)
    if (symbol['st_value'], symbol['st_size']) != (int(evidence['address'], 16), evidence['size']):
        raise ValueError('Stock function changed since shared-module verification')
    changes = []
    patched = bytearray(original)
    sites = (
        ('Lensconnection', evidence['instruction_address'], evidence['original'], evidence['opt_in_replacement']),
        ('Kmount admission', evidence['kmount_instruction_address'], evidence['kmount_original'], evidence['kmount_opt_in_replacement']),
    )
    allowed = set()
    for label, address_text, before_hex, after_hex in sites:
        address = int(address_text, 16)
        before, after = bytes.fromhex(before_hex), bytes.fromhex(after_hex)
        if function[address-symbol['st_value']:address-symbol['st_value']+4] != before:
            raise ValueError('Unreviewed instruction: ' + address_text)
        segments = [seg for seg in elf.iter_segments() if seg['p_type'] == 'PT_LOAD'
                    and seg['p_flags'] & 1 and seg['p_vaddr'] <= address
                    and address+4 <= seg['p_vaddr']+seg['p_filesz']]
        if len(segments) != 1:
            raise ValueError('Expected one executable file-backed segment')
        seg = segments[0]
        offset = seg['p_offset']+address-seg['p_vaddr']
        if original[offset:offset+4] != before:
            raise ValueError('ELF instruction offset differs')
        patched[offset:offset+4] = after
        allowed.update(range(offset, offset+4))
        changes.append({'purpose': label, 'address': address_text, 'file_offset': hex(offset),
                        'before': before_hex, 'after': after_hex})
    differences = [i for i, (a, b) in enumerate(zip(original, patched)) if a != b]
    if len(original) != len(patched) or len(differences) != 2 or not set(differences) <= allowed:
        raise ValueError('Unexpected changes outside the two branch instructions')
    destination = OUT / 'builtin-candidate'
    for prefix, data in (('original', original), ('rootfs', patched)):
        target = destination / prefix / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        if target.read_bytes() != data:
            raise ValueError('Candidate readback differs')
    report = {
        'status': 'review-only stock ELF delta; no installation or firmware packaging',
        'source': str(source), 'rootfs_path': '/' + relative, 'bytes': len(original),
        'instructions': changes, 'changed_bytes': len(differences),
        'other_bytes_identical': True, 'elf_size_and_layout_unchanged': True,
        'activation': 'requires installed rootfs and new app execution or matching regenerated snapshot',
        'sd_autoexec_installable': False, 'runtime_opt_in': False,
        'policy': 'the two admission changes are always active in this candidate executable',
        'preserved': ['Lenslock', 'storage and busy checks', 'global lens state', 'RVF detach events'],
        'not_preserved_from_installed_mfw': 'built from verified stock 1.15; installed MFW edits are not included',
        'original_retained': 'original/' + relative,
        'code_path_evidence': '../wifi-gate-probe.json (original ARM tail with identical replacement instructions)',
        'firmware_flashed': False, 'camera_executed': False, 'physical_rvf_verified': False,
    }
    (destination/'report.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
