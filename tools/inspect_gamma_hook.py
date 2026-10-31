"""Prove NX300M Gamma Contrast call identity through ELF GOT relocations.

Read-only static analysis of stock 1.15 IQ/UDD and the existing LUT adapter.
No firmware execution or runtime edits are performed.
"""
import io
import json
from pathlib import Path
import re
import struct

from inspect_recipe_tables import all_symbols, disassemble, read_symbol
from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection
from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM

ROOT = Path(__file__).resolve().parents[1]


class Stock:
    def __init__(self, path):
        self.path = path
        self.elf = ELFFile(io.BytesIO(path.read_bytes()))
        self.symbols = all_symbols(self.elf)
        self.sections = [(section['sh_addr'], section.data())
                         for section in self.elf.iter_sections()
                         if section['sh_type'] != 'SHT_NOBITS' and section['sh_size']]
        self.decoder = Cs(CS_ARCH_ARM, CS_MODE_ARM)
        self.relocs = {}
        for section in self.elf.iter_sections():
            if isinstance(section, RelocationSection):
                symbols = self.elf.get_section(section['sh_link'])
                for relocation in section.iter_relocations():
                    self.relocs[relocation['r_offset']] = {
                        'symbol': symbols.get_symbol(relocation['r_info_sym']).name,
                        'type': relocation['r_info_type'], 'section': section.name}

    def read(self, address, count):
        for base, data in self.sections:
            offset = address-base
            if offset >= 0 and offset+count <= len(data):
                return data[offset:offset+count]
        raise ValueError(f'Unmapped stock VA {address:#x}')

    def word(self, address):
        return struct.unpack('<I', self.read(address, 4))[0]

    def instruction(self, address):
        return next(self.decoder.disasm(self.read(address, 4), address))

    def literal(self, address):
        instruction = self.instruction(address)
        match = re.fullmatch(r'(?:r\d+|ip|lr|sl), \[pc, #(-?(?:0x[0-9a-f]+|\d+))\]', instruction.op_str)
        if instruction.mnemonic != 'ldr' or not match:
            raise ValueError(f'Expected PC literal load at {address:#x}')
        pool = address+8+int(match.group(1), 0)
        return pool, self.word(pool)

    def table(self, name, width):
        symbol = self.symbols[name]
        raw, section, offset = read_symbol(self.elf, symbol)
        values = list(struct.unpack('<'+str(len(raw)//4)+'i', raw))
        if len(values) % width:
            raise ValueError(f'{name}: partial row')
        return {'symbol': name, 'address': hex(symbol['st_value']), 'size_bytes': len(raw),
                'section': section, 'file_offset': hex(offset),
                'rows': [values[i:i+width] for i in range(0,len(values),width)],
                'min': min(values), 'max': max(values)}


def sym(cls, member):
    return f'_ZN{len(cls)}{cls}{len(member)}{member}E'


def main():
    config = json.loads((ROOT/'project.local.json').read_text(encoding='utf-8-sig'))
    rootfs = Path(config['rootfs'])
    stock = Stock(rootfs/'usr/lib/libiq-fw-slpcam.so')
    udd = Stock(rootfs/'usr/lib/libudd.so.3.1')
    bindings = []

    def bind(cls, member, pic_load, pic_add, offset_load, dereference):
        base_pool, base_delta = stock.literal(pic_load)
        got_base = (pic_add+8+base_delta)&0xffffffff
        offset_pool, offset = stock.literal(offset_load)
        slot = (got_base+offset)&0xffffffff
        relocation = stock.relocs[slot]
        expected = sym(cls, member)
        if relocation['symbol'] != expected or relocation['type'] != 21:
            raise ValueError(f'GOT identity mismatch: {expected}: {relocation}')
        instruction = stock.instruction(dereference)
        if instruction.mnemonic != 'ldr' or not instruction.op_str.startswith(('r3, [r4,', 'sl, [r4,', 'sb, [r4,', 'ip, [r4,', 'lr, [r4,', 'r2, [r4,', 'r7, [r4,')):
            raise ValueError('Unexpected GOT dereference')
        evidence = {'symbol': expected, 'symbol_address': hex(stock.symbols[expected]['st_value']),
                    'pic_load': hex(pic_load), 'pic_base_literal': hex(base_pool),
                    'pic_add': hex(pic_add), 'got_base': hex(got_base),
                    'offset_load': hex(offset_load), 'offset_literal': hex(offset_pool),
                    'got_dereference': hex(dereference), 'got_slot': hex(slot),
                    'relocation': relocation}
        bindings.append(evidence)
        return expected

    still_pic, preview_pic = (0x33cabc,0x33cacc), (0x340e90,0x340ea0)
    names = {}
    for pipe, pic, specs in (
        ('CIpcsGammaD4', still_pic, [
            ('m_iContrastX',0x33cbb8,0x33cbd0), ('m_iContrastY',0x33cbb0,0x33cbc8),
            ('m_iEffectToneX',0x33cbf0,0x33cc08), ('m_iEffectToneY',0x33cbe8,0x33cc00),
            ('m_i3DLutToneIn',0x33cdb4,0x33cdc4), ('m_i3DLutToneOut',0x33cdc8,0x33cdcc),
            ('m_iBaseGammaX',0x33cb80,0x33cb88), ('m_iBaseGammaY',0x33cb74,0x33cb84)]),
        ('CIpcmGammaD4', preview_pic, [
            ('m_iContrastX',0x340f7c,0x340f90), ('m_iContrastY',0x340f68,0x340f80),
            ('m_iEffectToneX',0x340fac,0x340fc4), ('m_iEffectToneY',0x340fc0,0x340fcc),
            ('m_iBaseGammaX',0x340f18,0x340f3c), ('m_iBaseGammaY',0x340f1c,0x340f28)])):
        for member, offset, dereference in specs:
            names[(pipe,member)] = bind(pipe,member,*pic,offset,dereference)

    tables = []
    for (cls,member), name in names.items():
        width = (19 if cls == 'CIpcsGammaD4' else 15) if 'BaseGamma' in member else 9 if '3DLut' in member else 7
        tables.append(stock.table(name,width))

    calls = []
    plt_targets = {}
    plt = stock.elf.get_section_by_name('.plt')
    relplt = stock.elf.get_section_by_name('.rel.plt')
    dynamic = stock.elf.get_section_by_name('.dynsym')
    for index, relocation in enumerate(relplt.iter_relocations()):
        plt_targets[plt['sh_addr']+20+index*12] = dynamic.get_symbol(relocation['r_info_sym']).name
    for cls, count, entries in (
        ('CIpcsGammaD4',19,[(0x33cbe4,'contrast','m_iContrastX','m_iContrastY',7),
                           (0x33cc18,'effect_tone','m_iEffectToneX','m_iEffectToneY',7),
                           (0x33cde8,'3d_lut_tone','m_i3DLutToneIn','m_i3DLutToneOut',9)]),
        ('CIpcmGammaD4',15,[(0x340fa0,'contrast','m_iContrastX','m_iContrastY',7),
                           (0x340fd8,'effect_tone','m_iEffectToneX','m_iEffectToneY',7)])):
        for address, kind, x, y, knots in entries:
            instruction = stock.instruction(address)
            if instruction.mnemonic != 'bl':
                raise ValueError('Reviewed ApplyLut call is not BL')
            target = int(instruction.op_str[1:],16)
            called_symbol = sym(cls,'ApplyLut')+'PiPKiS2_S2_ii'
            if plt_targets[target] != called_symbol:
                raise ValueError('ApplyLut call resolves to a different original symbol')
            calls.append({'class': cls, 'call': hex(address), 'return_address': hex(address+4),
                          'plt_target': hex(target), 'called_symbol': called_symbol,
                          'kind': kind, 'x_symbol': names[(cls,x)], 'y_symbol': names[(cls,y)],
                          'output_count': count, 'knot_count': knots,
                          'y_row_stride_bytes': 28 if knots == 7 else None,
                          'row_index': 'subindex1 (Contrast), 0..8' if kind == 'contrast' else
                                       'subindex2 (effect tone)' if kind == 'effect_tone' else 'single row; conditional subindex5!=0'})
    # Recover adaptive Y19 from AtRgbGammaOutCalculation19's literal arithmetic.
    adaptive_base = (0x308700+8+stock.word(0x308728))&0xffffffff
    adaptive_y = (adaptive_base+stock.word(0x30872c)-0xd90-8)&0xffffffff
    adaptive_values = list(struct.unpack('<19i',stock.read(adaptive_y,76)))

    functions = []
    for cls in ('CIpcsGammaD4','CIpcmGammaD4'):
        functions.extend(sym(cls,member)+args for member,args in
                         [('SetParam','j'),('ApplyLut','PiPKiS2_S2_ii'),('GetIndex','Pv'),('EvShift','PiPKiii')])
    functions += ['_ZN8CIpcMath20GetInterpolatedValueEPKiS1_ii',
                  '_ZN8CIpcMath9IntSplineEPKiS1_Ptiii','_ZN8CIpcMath9IntSplineEPKiS1_Phiii',
                  '_ZN13CAdaptiveTone25AtRgbGammaInCalculation19EPi',
                  '_ZN13CAdaptiveTone26AtRgbGammaOutCalculation19EPi',
                  '_ZN12CIpcs3DLutD48GetIndexEPv','_ZN12CIpcs3DLutD48SetParamEj']
    asm, function_info = disassemble(stock.elf,stock.symbols,functions)
    udd_asm, udd_info = disassemble(udd.elf,udd.symbols,['ipcs_tone_lut','ipcm_tone_lut'])
    asm += '\n# UDD provider: '+str(udd.path)+'\n'+udd_asm
    report = {
        'iq_library': str(stock.path), 'udd_library': str(udd.path),
        'method': 'Original ELF symbols, R_ARM_GLOB_DAT GOT relocations, PC literals and reviewed register/stack data flow; static only.',
        'functions': function_info, 'udd_functions': udd_info,
        'got_bindings': bindings, 'apply_lut_calls': calls, 'tables': tables,
        'apply_lut_abi': {'registers': {'r0':'this','r1':'int32 *dst','r2':'const int32 *input','r3':'const int32 *knots_x'},
                          'entry_stack': {'+0':'const int32 *knots_y','+4':'int output_count','+8':'int knot_count'},
                          'original_function': 'The 40-byte register save makes incoming stack arguments appear at sp+0x28/0x2c/0x30. Each input[i] becomes query for CIpcMath::GetInterpolatedValue(knots_x,knots_y,knot_count,input[i]); its int result is stored directly with no clipping.',
                          'aliasing': 'The original effect/3D tone calls pass dst==input; one index is loaded before the matching store.'},
        'contrast_input_domain': {
            'still_static': '3x19 m_iBaseGammaY rows, each 0..1023; Contrast consumes these Y values. BaseGammaX19 is separate 0..8191, optionally EvShifted.',
            'still_adaptive': {'address': hex(adaptive_y),'values': adaptive_values,
                               'range': [min(adaptive_values),max(adaptive_values)],
                               'basis': 'subindex0>2 invokes IQ_AdaptiveTone_GetRGBGammaTable19_y -> AtRgbGammaOutCalculation19, which copies these19 immutable Y words; x is independently supplied by AtRgbGammaInCalculation19.'},
            'preview_static': 'm_iBaseGammaY15 is 0..255; X15 is separate 0..2047 and may be EvShifted.',
            'preview_adaptive': '0x341014..0x3410bc builds15 Y points by sampling adaptive19: query uses baseX15*8191/2047 and result scales by255/1023 with original integer magic division. This branch has no explicit pre-Contrast clamp; its actual values must be observed or executed to assert a universal bound.',
            'hook_input': 'At the Contrast ApplyLut entry, r2 points to fresh Gamma Y19/Y15, not the X axis, raw sensor samples, pixel buffers or the final dense LUT.'},
        'downstream': {
            'still': ['Contrast ApplyLut19 -> EffectTone ApplyLut19.',
                      'CIQParamInfo::SetGammaTable snapshots the current19-point curve before the optional3D tone remap.',
                      'If subindex5!=0, a separate9-knot m_i3DLutToneIn/Out remaps Y19 at0x33cde8.',
                      'Subindex4<=16 sets the last Y point to1023 and rewrites the last X point with an8190 upper cap; additional state-dependent branches can modify the first10 X points before IntSpline.',
                      '0x33cd2c calls IntSpline(const int*x,const int*y,uint16*out,19,0,1023); VFP float interpolation clips each dense output to0..1023.',
                      '0x33cd34 calls ipcs_tone_lut. UDD0x95548 copies0x4000 bytes (8192 uint16 entries) to IPCS context+0x4000.'],
            'preview': ['Contrast ApplyLut15 -> EffectTone ApplyLut15.',
                        '0x340ffc calls IntSpline(const int*x,const int*y,uint8*out,15,0,255); VFP interpolation clips each dense output to0..255.',
                        '0x341004 calls ipcm_tone_lut. UDD0x4b7d4 copies0x800 bytes (2048 uint8 entries) to IPCM context+0x800.'],
            'final_setter_has_no_acceptance_check': 'The two UDD tone setters print when open_count is0, then still perform memcpy; return/control flow is not evidence that an active ISP consumed it.'},
        'existing_lut_engine': {
            'source': 'runtime/lut_port.c',
            'hooks': ['CIpcParameterNode::Update','ipcs_3d_lut'],
            'shared_selector': 'Still 3D LUT node GetIndex reads parameter byte+0x83; Gamma GetIndex reads the same byte into subindex5. Gamma then conditionally applies the9-point tone curve. Preview has no corresponding 3D-tone ApplyLut call.',
            'composition': 'The adapter selects table-stock3d.d4lut when the original static3D table is the source, and otherwise uses table.d4lut. It changes the still3D LUT table/caching, not Gamma ApplyLut or tone LUT.',
            'ordering_boundary': 'These calls prove separate1D Gamma/tone and3D LUT programming plus shared selector. They do not alone establish the full hardware pixel ordering, color space or image equivalence.',
            'contrast_hook_scope': 'Matching exact m_iContrastX pointer, m_iContrastY row identity/alignment, outputCount19/15 and knotCount7 distinguishes Contrast from EffectTone and9-point3D tone. Values alone do not establish call identity.'},
        'limits': ['No runtime input sampled by this script; dynamic current arrays require the separate read-only hook.',
                   'IntSpline writes across the current X extent; fixed UDD memcpy lengths alone do not prove every copied output entry was freshly generated when SetParam rewrites the last X point.',
                   'The target GetInterpolatedValue is a separate implementation; no equality with NX500 interpolation is asserted.',
                   'Programming curve buffers does not establish ISP execution or image output.'],
    }
    output = ROOT/'outputs/metol/gamma-hook'
    output.with_suffix('.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    output.with_suffix('.asm').write_text(asm,encoding='utf-8')
    print(json.dumps({'got_relocations_verified':len(bindings),'calls':calls,
                      'adaptive_y19_range': report['contrast_input_domain']['still_adaptive']['range']},indent=2))


if __name__ == '__main__':
    main()
