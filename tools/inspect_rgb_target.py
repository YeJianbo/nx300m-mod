"""Inspect NX300M RGB matrix stages from the unmodified IQ ELF; no execution."""
import json
import struct
from pathlib import Path

from inspect_gamma_hook import Stock
from inspect_recipe_tables import disassemble

ROOT = Path(__file__).resolve().parents[1]


def main():
    config = json.loads((ROOT / 'project.local.json').read_text(encoding='utf-8-sig'))
    stock = Stock(Path(config['rootfs']) / 'usr/lib/libiq-fw-slpcam.so')
    functions = sorted(name for name, symbol in stock.symbols.items()
                       if symbol['st_info']['type'] == 'STT_FUNC' and
                       ('CColorProcess' in name or 'Rgb2Rgb' in name or
                        name in ('_ZN17CIpcParameterNode6UpdateEPv', '_ZN17CIpcParameterNode5ResetEv',
                                 '_ZN17CIpcParameterNode11GetSubIndexEji', '_ZN17CIpcParameterNode11SetSubIndexERjij') or
                        (('CIpcsColorD4' in name or 'CIpcmColorD4' in name) and
                         ('GetIndex' in name or 'SetParam' in name)) or
                        name == '_Z18UpdateColorProcessP12tagCOLORINFO' or
                        name == '_ZN8CIpcMath13MatrixMult3x3EPiPKiS2_i'))
    asm, function_info = disassemble(stock.elf, stock.symbols, functions)
    tables = [stock.table(name, 9) for name, symbol in stock.symbols.items()
              if symbol['st_info']['type'] == 'STT_OBJECT' and
              ('CIpcsColorD4' in name or 'CIpcmColorD4' in name) and
              any(word in name for word in ('m_iMatrixE', 'm_iPssMatrixE', 'm_iColorToneE'))]
    output = ROOT / 'outputs/metol/rgb-target.json'
    output.with_suffix('.asm').write_text(asm, encoding='utf-8', newline='\n')
    result = {'source': str(stock.path), 'method': 'static ELF symbols, relocations and bounded disassembly',
              'functions': function_info, 'tables': tables,
              'relocations': [{'address': hex(address), **relocation}
                              for address, relocation in sorted(stock.relocs.items())
                              if any(name in relocation['symbol'] for name in
                                     ('CColorProcess', 'Rgb2Rgb', 'm_iMatrixE', 'm_iPssMatrixE', 'm_iColorToneE',
                                      'MatrixMult3x3', 'color_correction_param'))]}
    getter = '_Z16GetRgb2RgbMatrixv'
    symbol = stock.symbols[getter]
    result['getter_export'] = {'name': getter, 'address': hex(symbol['st_value']),
                               'binding': symbol['st_info']['bind'], 'visibility': symbol['st_other']['visibility'],
                               'signature': 'signed short *GetRgb2RgbMatrix(void)', 'plt': '0x5e3b8', 'got': '0x3c2748'}
    result['getter_arm_branch_callers'] = []
    result['getter_absolute_data_pointers'] = []
    for section in stock.elf.iter_sections():
        if not section['sh_flags'] & 2 or section['sh_type'] == 'SHT_NOBITS':
            continue
        data, base = section.data(), section['sh_addr']
        for offset in range(0, len(data) - 3, 4):
            word = struct.unpack_from('<I', data, offset)[0]
            if section['sh_flags'] & 4:
                if word & 0x0e000000 != 0x0a000000 or word >> 28 == 15:
                    continue
                displacement = word & 0xffffff
                if displacement & 0x800000:
                    displacement -= 1 << 24
                target = (base + offset + 8 + (displacement << 2)) & 0xffffffff
                if target in (symbol['st_value'], 0x5e3b8):
                    result['getter_arm_branch_callers'].append({'address': hex(base + offset), 'target': hex(target),
                                                                 'instruction': stock.instruction(base + offset).op_str})
            elif section.name not in ('.dynsym', '.symtab', '.hash', '.gnu.hash') and word in (symbol['st_value'], 0x5e3b8):
                result['getter_absolute_data_pointers'].append({'address': hex(base + offset), 'value': hex(word),
                                                                  'section': section.name})
    result['dynamic_symbolic_binding'] = [{'tag': tag.entry.d_tag, 'value': int(tag.entry.d_val)}
                                         for tag in stock.elf.get_section_by_name('.dynamic').iter_tags()
                                         if tag.entry.d_tag in ('DT_SYMBOLIC', 'DT_FLAGS', 'DT_FLAGS_1')]
    result['color_vtables'] = []
    for name in ('_ZTV12CIpcsColorD4', '_ZTV12CIpcmColorD4'):
        item = stock.table(name, 1)
        address = stock.symbols[name]['st_value']
        item['entries'] = [{'address': hex(address + 4 * index), 'raw_addend': hex(row[0] & 0xffffffff),
                            'relocation': stock.relocs.get(address + 4 * index)}
                           for index, row in enumerate(item.pop('rows'))]
        result['color_vtables'].append(item)
    result['base_lmw_storage'] = []
    # PC-relative literal arithmetic at 0x3347f0..0x334810 yields 0x3cf904.
    # Each LMW is six signed16 off-diagonals; the diagonal is reconstructed.
    for name, address in (('darkmod', 0x3cf904), ('illum_T', 0x3cf910),
                          ('illum_D', 0x3cf91c), ('mode_compensation', 0x3cf938)):
        raw = stock.read(address, 12)
        result['base_lmw_storage'].append({'name': name, 'address': hex(address), 'raw_hex': raw.hex(),
                                           'signed16_offdiagonals': list(struct.unpack('<6h', raw))})
    result['reviewed_findings'] = {
        'getter_pointer_lifetime': {
            'owner': 'CColorProcess singleton; GetInstance()+0x10, nine signed16 words',
            'refresh': 'UpdateColorProcess copies tagCOLORINFO into singleton+0x3c and synchronously calls CalculateColorMatrix.',
            'consumers': [
                {'pipe': 'capture', 'call': '0x33dfc0', 'copy': '0x33dff8..0x33e008', 'destination': 'ColorD4 node+0xa0, nine signed32 words'},
                {'pipe': 'preview', 'call': '0x33fb68', 'copy': '0x33fba0..0x33fbb0', 'destination': 'ColorD4 node+0x24, nine signed32 words'}],
            'retention': 'Both consumers keep the pointer only in a register through GetSubIndex, then copy nine signed16 entries. No pointer is stored in the node.',
            'intervening_function': 'GetSubIndex 0x351060 only extracts bits from the old index on its valid path; it does not call the getter or color processing.',
            'other_references': 'Native rg -l -a -F of the exact mangled getter in stock rootfs usr/lib, usr/bin, lib and opt returned only libiq-fw-slpcam.so.',
            'branch_scan_scope': 'Every aligned ARM B/BL word in executable ELF sections scanned against getter entry and getter PLT; only the two listed PLT callers found.',
            'remaining_limit': 'Static analysis does not establish absence of arbitrary computed/dlsym calls or concurrent readers; no runtime hook was executed.'},
        'base_before_getter': {
            'order': 'illum_D/illum_T six-entry LMW -> InterpolateLMW -> optional dark compensation MultLMW -> optional mode compensation MultLMW -> EncodeLMWtoD4COLMAT -> getter',
            'illum_interpolation': '0x334884: each signed16 ((D*(256-w)+T*w+128)>>8), stored signed16.',
            'lmw_composition': '0x3348c4: reconstruct row-normalized 3x3 at Q10, multiply, add512 then >>10, clamp [-8192,8191], retain six offdiagonals.',
            'encoding': '0x334a4c: quantize the six offdiagonals with +4 then >>3; reconstruct diagonal as 128 minus the two offdiagonals in each row.',
            'unit': 'Encoded full base is Q7 with normal row sum128; PSS and ColorTone matrices used later are Q8. Existing runtime/color_style.c Q8 description does not describe this base unit.',
            'early_injection_limit': 'Six-entry row-normalized LMW cannot represent arbitrary input-column gains: scaled columns generally change row sums. Scaling only the six stored offdiagonals reconstructs different diagonal entries.'},
        'color_node_composition': {
            'capture': {'first_multiply': '0x33e508', 'second_multiply': '0x33e51c', 'setter': '0x33e564'},
            'preview': {'first_multiply': '0x340078', 'second_multiply': '0x34008c', 'setter': '0x340094'},
            'formula': 'intermediate=trunc0(PSS[index]*B/256); final=trunc0(ColorTone(tone)*intermediate/256)',
            'tone_selection': 'tone is clamped to78; q=trunc0(tone/6), r=tone-6*q; r=0 copies knot[q], otherwise each coefficient trunc0(((6-r)*knot[q]+r*knot[q+1])/6).',
            'matrix_math': 'MatrixMult3x3 0x34a010 uses 32-bit MLA accumulation and __aeabi_idiv, without rounding bias.',
            'capture_side_effects': 'The final matrix is also copied to node+0x48, CAWB::SetCc and CIQParamInfo::SetColorMatrix before the SDK setter.',
            'late_column_scaling_limit': 'Right-column scaling commutes with these left multiplications over exact reals; it does not commute through the two integer truncations, source gain quantization/signed16 behavior, or earlier target interpolation/encoding. Final SDK setter column scaling is not an exact implementation of early base scaling.'},
        'update_transaction': {
            'entry': '_ZN17CIpcParameterNode6UpdateEPv at0x350dc0',
            'sync_chain': 'Update BLX [vptr+0x14] -> ColorD4::GetIndex -> UpdateColorProcess -> getter -> immediate copy -> return index -> Update BLX [vptr+0x10] -> ColorD4::SetParam -> SetRgb2Rgb -> SDK setter; all ordinary synchronous calls on the same stack.',
            'vptr': 'ColorD4 vptr is exported _ZTV12CIpcsColorD4/_ZTV12CIpcmColorD4 +8. Relocations at vptr+0x14 identify GetIndex; at vptr+0x10 identify SetParam.',
            'reset': 'Reset 0x350e0c writes self+4=0xffffffff. GetRgb2RgbIndex makes subindex1=(old_subindex1==0), and both SetParam methods compare new versus old subindex1 before SetRgb2Rgb. Reset therefore forces this path even for unchanged base coefficients.',
            'natural_updates': 'With a nonnull base pointer, subindex1 toggles on every GetRgb2RgbIndex call; this naturally changes the index and requests SetRgb2Rgb. Reset handles identity/generation/eligibility changes explicitly.',
            'candidate': 'Scope a TLS snapshot around the original Update only for the two ColorD4 vtables; getter returns a TLS copy with source column operation only while that context is active. Preserve original singleton pointer/content and restore previous TLS context after original Update returns.',
            'semantic_boundary': 'This injects the source RGB column operation into a target native Q7 base before PSS/ColorTone. Target selection/interpolation and encoding occur first, so it does not reproduce the complete source builder bit for bit.',
            'unverified': 'TLS runtime interposition, actual threading and image output were not exercised.'},
    }
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(f'Wrote {output.relative_to(ROOT)} and .asm; functions={len(function_info)} tables={len(tables)}')


if __name__ == '__main__':
    main()
