"""Extract stock still Gamma evidence and bound the 3D LUT color-domain claim.

Static ELF/header inspection only. No ISP execution, camera state changes,
pixel simulation, or production LUT updates. Run using the project's Conda.
"""
import argparse
import json
import math
from pathlib import Path
import struct

from inspect_gamma_hook import Stock, sym
from inspect_recipe_tables import read_symbol

ROOT = Path(__file__).resolve().parents[1]
INCLUDE = ROOT/'work/vendor-source/TIZEN/project/NX300M/imagedev/usr/include/drime4/udd'


def plt_symbols(stock):
    plt = stock.elf.get_section_by_name('.plt')
    relocations = stock.elf.get_section_by_name('.rel.plt')
    dynamic = stock.elf.get_section_by_name('.dynsym')
    return {plt['sh_addr']+20+i*12: dynamic.get_symbol(r['r_info_sym']).name
            for i,r in enumerate(relocations.iter_relocations())}


def function(stock, name):
    symbol = stock.symbols[name]
    data, section, offset = read_symbol(stock.elf, symbol)
    address = symbol['st_value'] & ~1
    return {'symbol':name, 'address':hex(address), 'size_bytes':len(data),
            'section':section, 'file_offset':hex(offset)}, list(stock.decoder.disasm(data,address))


def calls(stock, name):
    info, instructions = function(stock,name)
    targets = plt_symbols(stock)
    targets.update({s['st_value'] & ~1:n for n,s in stock.symbols.items()
                    if s['st_info']['type'] == 'STT_FUNC'})
    info['calls'] = [{'address':hex(i.address), 'target':i.op_str,
                      'symbol':targets.get(int(i.op_str[1:],16),'unresolved')}
                     for i in instructions if i.mnemonic == 'bl' and i.op_str.startswith('#0x')]
    return info


def instructions_at(stock, addresses):
    return [{'address':hex(address), 'bytes':(i := stock.instruction(address)).bytes.hex(),
             'mnemonic':i.mnemonic, 'operands':i.op_str} for address in addresses]


def source_excerpt(path, first, last):
    lines = path.read_text(encoding='utf-8',errors='replace').splitlines()
    return {'path':str(path.relative_to(ROOT)), 'first_line':first, 'last_line':last,
            'text':'\n'.join(lines[first-1:last])}


def gamma_binding(stock, member, offset_load, dereference):
    # Re-evaluate original ARM PC literals and R_ARM_GLOB_DAT slots instead
    # of treating an old audit JSON or a descriptive symbol name as proof.
    base_pool, delta = stock.literal(0x33cabc)
    got_base = (0x33cacc+8+delta)&0xffffffff
    offset_pool, offset = stock.literal(offset_load)
    slot = (got_base+offset)&0xffffffff
    expected = sym('CIpcsGammaD4',member)
    relocation = stock.relocs[slot]
    if relocation['symbol'] != expected or relocation['type'] != 21:
        raise ValueError(f'Stock 1.15 Gamma GOT binding changed: {expected}')
    instruction = stock.instruction(dereference)
    if instruction.mnemonic != 'ldr' or '[r4,' not in instruction.op_str:
        raise ValueError(f'Unexpected Gamma GOT dereference at {dereference:#x}')
    return {'symbol':expected, 'symbol_address':hex(stock.symbols[expected]['st_value']),
            'pic_load':'0x33cabc', 'pic_add':'0x33cacc', 'base_literal':hex(base_pool),
            'offset_literal':hex(offset_pool), 'got_base':hex(got_base),
            'got_slot':hex(slot), 'dereference':hex(dereference), 'relocation':relocation}


def srgb_encode(value):
    return 12.92*value if value <= .0031308 else 1.055*value**(1/2.4)-.055


def inspect(rootfs):
    iq = Stock(rootfs/'usr/lib/libiq-fw-slpcam.so')
    udd = Stock(rootfs/'usr/lib/libudd.so.3.1')
    specs = [('m_iBaseGammaX',19,0x33cb80,0x33cb88),
             ('m_iBaseGammaY',19,0x33cb74,0x33cb84),
             ('m_iContrastX',7,0x33cbb8,0x33cbd0),
             ('m_iContrastY',7,0x33cbb0,0x33cbc8),
             ('m_iEffectToneX',7,0x33cbf0,0x33cc08),
             ('m_iEffectToneY',7,0x33cbe8,0x33cc00),
             ('m_i3DLutToneIn',9,0x33cdb4,0x33cdc4),
             ('m_i3DLutToneOut',9,0x33cdc8,0x33cdcc)]
    tables = {member:iq.table(sym('CIpcsGammaD4',member),width)
              for member,width,_,_ in specs}
    bindings = [gamma_binding(iq,member,load,deref) for member,_,load,deref in specs]
    x = tables['m_iBaseGammaX']['rows'][0]
    ys = tables['m_iBaseGammaY']['rows']
    if len(x) != 19 or len(ys) != 3 or x[0] != 0 or x[-1] != 8191:
        raise ValueError('Unexpected stock still Gamma table shape/range')
    comparisons = []
    for index,y in enumerate(ys):
        errors = [b/1023-srgb_encode(a/8191) for a,b in zip(x,y)]
        comparisons.append({'row':index, 'normalization':[8191,1023],
                            'max_abs_normalized_error':max(map(abs,errors)),
                            'rmse_normalized':math.sqrt(sum(e*e for e in errors)/len(errors)),
                            'claim':'These stored knots are not the normalized sRGB OETF. '
                                    'This does not identify the primaries or net pipeline transfer.'})
    tone_in = tables['m_i3DLutToneIn']['rows'][0]
    tone_out = tables['m_i3DLutToneOut']['rows'][0]
    neutral = []
    for channel in 'RGB':
        name = sym('CIpcs3DLutD4','m_s'+channel+'Lut')
        symbol = iq.symbols[name]
        raw,section,offset = read_symbol(iq.elf,symbol)
        if len(raw) != 1460:
            raise ValueError('Unexpected factory 3D LUT shape')
        values = struct.unpack('<730H',raw)
        diagonal = [values[i*(81+9+1)] for i in range(9)]
        neutral.append({'channel':channel, 'symbol':name,'address':hex(symbol['st_value']),
                        'section':section,'file_offset':hex(offset), 'grid':9,
                        'stored_words':730,'last_padding_word':values[-1],
                        'neutral_lattice_indices':list(range(9)),
                        'nominal_uniform_codes_for_comparison':tone_out,
                        'grid_code_limit':'Uniform10-bit node codes are a comparison convention; '
                                          'this static table read does not prove hardware input addressing.',
                        'neutral_output_codes':diagonal,
                        'difference_from_gamma_tone_input':[v-w for v,w in zip(diagonal,tone_in)]})

    gamma_name = sym('CIpcsGammaD4','SetParam')+'j'
    iq_names = [gamma_name, sym('CIpcsGammaD4','GetIndex')+'Pv',
                sym('CIpcs3DLutD4','GetIndex')+'Pv',sym('CIpcs3DLutD4','SetParam')+'j',
                sym('CIpcsColorD4','SetRgb2Rgb')+'i',sym('CIpcsColorD4','SetRgb2Ycc')+'i']
    udd_names = ['ipcs_tone_lut','ipcs_3d_lut','ipcs_color_correction_param',
                 'ipcs_rgb2ycc_param','ipcs_ycc2rgb_param','ipcs_hl_3d_lut_onoff',
                 'ipcs_opinfo_set_for_bayer_to_ycc','ipcs_opinfo_set_for_rgb_to_ycc',
                 'ipcs_opinfo_set_for_ycc_correction_to_ycc']
    iq_functions = [calls(iq,name) for name in iq_names]
    udd_functions = [calls(udd,name) for name in udd_names]
    # These reviewed calls must still resolve to the matching stock provider.
    targets = plt_symbols(iq)
    expected_calls = {0x33cbe4:sym('CIpcsGammaD4','ApplyLut')+'PiPKiS2_S2_ii',
                      0x33cc18:sym('CIpcsGammaD4','ApplyLut')+'PiPKiS2_S2_ii',
                      0x33cde8:sym('CIpcsGammaD4','ApplyLut')+'PiPKiS2_S2_ii',
                      0x33cd2c:'_ZN8CIpcMath9IntSplineEPKiS1_Ptiii',
                      0x33cd34:'ipcs_tone_lut'}
    for address,target in expected_calls.items():
        instruction = iq.instruction(address)
        if instruction.mnemonic != 'bl' or targets.get(int(instruction.op_str[1:],16)) != target:
            raise ValueError(f'Stock Gamma call identity changed at {address:#x}')

    return {
        'scope':'Read-only NX300M 1.15 stock ELF/header color-domain inspection; no pixel execution.',
        'sources':{'iq':str(iq.path),'udd':str(udd.path),
                   'prior_analysis':['tools/inspect_gamma_hook.py','tools/inspect_lut.py',
                                     'outputs/metol/gamma-pipeline.asm']},
        'still_gamma':{
            'got_bindings':bindings, 'tables':tables,
            'static_selection':'subindex0 <= 2 chooses BaseGammaY[subindex0] with 76-byte row stride; '
                               'BaseGammaX is shared. Other values use adaptive X/Y suppliers.',
            'generation_order':['static/adaptive X19 and Y19 selection',
                                'optional EvShift of X19',
                                'Contrast(Y19; 7 knots)', 'EffectTone(Y19; 7 knots)',
                                'CIQParamInfo::SetGammaTable snapshot',
                                'if subindex5 != 0: 3DLutToneIn/Out(Y19; 9 knots)',
                                'state-dependent X19 edits and last-point handling',
                                'IntSpline(X19,Y19,uint16 output,19,0,1023)',
                                'ipcs_tone_lut copies 8192 uint16 words'],
            'generation_order_is_pixel_order':False,
            'hardware_table':{'setter':'ipcs_tone_lut','setter_va':'0x95548',
                              'offset':'0x4000','bytes':0x4000,'entry_type':'uint16',
                              'entry_count':8192,'generated_clip_range':[0,1023],
                              'meaning':'13-bit table address span, 10-bit output code span; '
                                        'linear scene exposure and primaries are not established.',
                              'coverage_limit':'State-dependent final X edits can end below8191; '
                                               'a fixed memcpy size does not prove every copied entry is freshly generated.'},
            'srgb_oetf_knot_comparison':comparisons,
            'runtime_limit':'The active19-point arrays depend on mode, contrast, effect, adaptive tone '
                            'and exposure state. Stored base rows are not a captured active curve.'},
        'stock_3d_neutral_axis':neutral,
        'ordering':{
            'confirmed':[
                {'claim':'Gamma parameter generation remaps Y19 using9-knot ToneIn/Out when '
                         'the same +0x83 selection byte that selects the still3D LUT is nonzero.',
                 'addresses':['0x33c7a0','0x33c818-0x33c828','0x33cc60-0x33cc68',
                              '0x33cdb4-0x33cde8','0x33bbac']},
                {'claim':'Gamma tone table and RGB3D LUT are separately programmed memories, '
                         'not the same LUT. CCM and RGB/YCC matrices have separate setters.',
                 'addresses':['ipcs_tone_lut@0x95548: +0x4000,16384 bytes',
                              'ipcs_3d_lut@0x955c8: +0x9000/+0x9700/+0x9e00,1460 bytes/plane',
                              'ipcs_color_correction_param@0x911f8',
                              'ipcs_rgb2ycc_param@0x914c0','ipcs_ycc2rgb_param@0x934e4']},
                {'claim':'The source API has separate RGB3D_LUT_SW controls in Bayer->YCC, '
                         'RGB->YCC and YCC-correction->YCC operation structures. '
                         'External input format therefore does not determine the internal LUT domain.',
                 'evidence':'header_excerpts and stock opinfo function calls below'}],
            'supported_inference':{
                'claim':'Gamma before stock3D LUT is consistent with the approximately inverse '
                        'neutral-axis pair: Gamma maps ToneIn->uniform ToneOut, while stock3D '
                        'maps uniform neutral nodes back to ToneIn.',
                'status':'inference; not a verified hardware pixel path',
                'counterexample_or_limit':'This pair could be calibration compensation or coexist '
                                          'with routing, matrix and clipping stages. API call order, '
                                          'MMIO address order and table names cannot prove pixel order.'},
            'unresolved_relations':['CCM versus Gamma','Gamma versus3D LUT hardware order',
                                    'RGB2YCC/YCC2RGB matrices versus3D LUT',
                                    'PCSE/gamut mapping and clipping versus3D LUT'],
        },
        'dcp_style_stage_connection':{
            'ready':False, 'actual_3d_lut_color_space':'unidentified',
            'required_input':'unrendered linear ProPhoto D50 after camera calibration/HueSatMap, '
                             'before profile exposure/LookTable/tone',
            'finding':'Existing stock3D LUT cannot yet be treated as linear ProPhoto or sRGB. '
                      'Do not apply raw-profile exposure/tone directly to camera JPEG or reuse '
                      'a guessed sRGB decode as the working-space conversion.',
            'specific_missing_evidence':[
                'Routing selector semantics or hardware block documentation proving the active '
                'CCM/Gamma/RGB-YCC/3D LUT pixel order, including bypass paths.',
                'Active per-mode Gamma X/Y/dense table and matrix coefficients on the target '
                'capture path; static base rows alone omit conditional/adaptive edits.',
                '3D LUT input RGB primaries, reference white, transfer curve, range and clipping. '
                'A nominal10-bit code span and RGB channel names do not supply these.',
                'If the insertion point is already tone-rendered: a characterized reversible '
                'mapping to the required working domain; clipping and spatial/adaptive processing '
                'can prevent a single inverse3D transform.'],
            'next_bounded_step':'Resolve original IPCS route selector definitions/block diagram '
                                'and capture active parameter buffers for one fixed still mode '
                                'before deciding where a working-space conversion can be inserted.',
        },
        'instruction_evidence':{
            'iq':instructions_at(iq,[0x33bbac,0x33c7a0,0x33cb68,0x33cb78,0x33cb8c,
                                     0x33cbe4,0x33cc18,0x33cc60,0x33cc68,0x33cde8,
                                     0x33cd2c,0x33cd34,0x33ce20,0x33ce28]),
            'udd':instructions_at(udd,[0x9559c,0x955a8,0x955ac,0x95640,0x9564c,
                                       0x95664,0x95688,0x61bfc,0x61c0c,0x61c18])},
        'functions':{'iq':iq_functions,'udd':udd_functions},
        'header_excerpts':[source_excerpt(INCLUDE/'ipcs_submodule_type.h',92,123),
                           source_excerpt(INCLUDE/'ipcs_submodule_type.h',455,462),
                           source_excerpt(INCLUDE/'ipcs_type.h',643,650),
                           source_excerpt(INCLUDE/'ipcs_type.h',693,698),
                           source_excerpt(INCLUDE/'ipcs_type.h',854,864),
                           source_excerpt(INCLUDE/'ipcs_type.h',892,897),
                           source_excerpt(INCLUDE/'ipcs_type.h',938,947),
                           source_excerpt(INCLUDE/'ipcs_type.h',978,983)],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rootfs',type=Path)
    parser.add_argument('--output',type=Path,default=ROOT/'outputs/metol/isp-color-domain.json')
    args = parser.parse_args()
    config = json.loads((ROOT/'project.local.json').read_text(encoding='utf-8-sig'))
    rootfs = args.rootfs or Path(config['rootfs'])
    if not rootfs.is_absolute(): rootfs = ROOT/rootfs
    report = inspect(rootfs)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n',
                           encoding='utf-8')
    print(json.dumps({'output':str(args.output), 'got_bindings':len(report['still_gamma']['got_bindings']),
                      'static_gamma_rows':len(report['still_gamma']['tables']['m_iBaseGammaY']['rows']),
                      'dcp_style_connection_ready':report['dcp_style_stage_connection']['ready'],
                      'actual_lut_color_space':report['dcp_style_stage_connection']['actual_3d_lut_color_space'],
                      'neutral_axis_difference_from_tone_input':{
                          row['channel']:row['difference_from_gamma_tone_input']
                          for row in report['stock_3d_neutral_axis']}},indent=2))


if __name__ == '__main__':
    main()
