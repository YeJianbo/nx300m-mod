"""Bound CCM encoding and provenance using captured arguments and original ARM code.

Static ELF/table extraction and integer arithmetic only. Does not run capture,
alter runtime libraries, or assign unverified RGB primaries to the coefficients.
"""
import argparse
import json
from pathlib import Path
import struct

from inspect_gamma_hook import Stock, sym
from inspect_recipe_tables import read_symbol

ROOT = Path(__file__).resolve().parents[1]
CAPTURE = ROOT/'outputs/firmware-runtime/capture-color/attempt3/observer'
OUTPUT = ROOT/'outputs/firmware-runtime/capture-color/matrix-analysis.json'


def signed(value, bits):
    value &= (1 << bits)-1
    return value-(1 << bits) if value & (1 << (bits-1)) else value


def instruction_evidence(stock, addresses):
    result = []
    for address in addresses:
        instruction = stock.instruction(address)
        result.append({'address':hex(address),'bytes':instruction.bytes.hex(),
                       'mnemonic':instruction.mnemonic,'operands':instruction.op_str})
    return result


def function_evidence(stock, name):
    symbol = stock.symbols[name]
    raw,section,offset = read_symbol(stock.elf,symbol)
    return {'symbol':name,'address':hex(symbol['st_value']),'bytes':len(raw),
            'section':section,'file_offset':hex(offset)}


def encode_lmw(values):
    # EncodeLMWtoD4COLMAT: signed16 load, +4, UBFX #3,#16, exceptional4096,
    # then STRH. Decode the resulting16-bit container back to signed values.
    off = []
    for value in values:
        encoded = (((signed(value,16)+4)&0xffffffff) >> 3)&0xffff
        if encoded == 4096: encoded = 4095
        off.append(signed(encoded,16))
    a,b,c,d,e,f = off
    return [signed(v,16) for v in [128-a-b,a,b,c,128-c-d,d,e,f,128-e-f]]


def inspect(directory):
    config = json.loads((ROOT/'project.local.json').read_text(encoding='utf-8-sig'))
    rootfs = Path(config['rootfs'])
    if not rootfs.is_absolute(): rootfs = ROOT/rootfs
    iq,udd = Stock(rootfs/'usr/lib/libiq-fw-slpcam.so'),Stock(rootfs/'usr/lib/libudd.so.3.1')
    records = [json.loads(path.read_text(encoding='utf-8')) for path in directory.glob('*.json')]
    ccm = next(record for record in records if record.get('kind') == 'ccm')
    gamma = next(record for record in records if record.get('kind') == 'gamma')
    if (ccm['caller_relative_offset'] != '0x33e568'
            or ccm['next_function_relative_offset'] != '0x911f8'
            or ccm['return_code'] != 0):
        raise ValueError('Capture CCM is not the reviewed original ColorD4->UDD edge')
    raw_values = ccm['values_uint32']
    values = [signed(value,32) for value in raw_values]
    if len(values) != 9 or any(not -1024 <= value <= 1023 for value in values):
        raise ValueError('Captured CCM is outside the original setter range')
    # Verify the original range branches and bit extraction anchors before
    # interpreting their meanings in the report.
    checks = {0x91258:('movw','r3, #0x3ff'),0x91260:('bls','#0x91284'),
              0x91270:('mvn','r3, #0x400'),0x91278:('bhi','#0x91284'),
              0x912d8:('lsl','r3, r3, #0x15'),0x912dc:('lsr','r3, r3, #0x15')}
    for address,expected in checks.items():
        i = udd.instruction(address)
        if (i.mnemonic,i.op_str) != expected:
            raise ValueError(f'Original UDD encoding anchor changed at {address:#x}')

    # Resolve table addresses through the original PC-relative getter literals.
    locations = {
        'illum_T':(0x3347d8+8+iq.word(0x3347e8)+iq.word(0x3347ec)+12)&0xffffffff,
        'illum_D':(0x3347fc+8+iq.word(0x33480c)+iq.word(0x334810)+24)&0xffffffff,
        'darkmod':(0x3347b8+8+iq.word(0x3347c4)+iq.word(0x3347c8))&0xffffffff,
        'mode_compensation':(0x334e44+8+iq.word(0x334fc8)+iq.word(0x334fcc)+0x34)&0xffffffff,
    }
    lmw = {name:{'address':hex(address),'initial_signed16_offdiagonals':
                  list(struct.unpack('<6h',iq.read(address,12)))}
           for name,address in locations.items()}
    tables = {member:iq.table(sym('CIpcsColorD4',member),9)
              for member in ('m_iMatrix','m_iPssMatrix','m_iColorTone')}
    base = (0x33e3c8+8+iq.word(0x33e5e0))&0xffffffff
    bindings = []
    for pool,expected in [(0x33e5e8,sym('CIpcsColorD4','m_iColorTone')),
                          (0x33e5ec,sym('CIpcsColorD4','m_iPssMatrix'))]:
        slot = (base+iq.word(pool))&0xffffffff
        relocation = iq.relocs[slot]
        if relocation['symbol'] != expected or relocation['type'] != 21:
            raise ValueError('ColorD4 composition table GOT binding changed')
        bindings.append({'literal_pool':hex(pool),'got_slot':hex(slot),'relocation':relocation})

    # A bounded compatibility check, not an identification of active AWB state.
    # GetIllumComp transforms its preliminary0..370 coordinate through the
    # original signed SMULL magic division; some outputs extrapolate T/D.
    compatible = []
    d,t = (lmw[name]['initial_signed16_offdiagonals'] for name in ('illum_D','illum_T'))
    for coordinate in range(371):
        numerator = (coordinate-73) << 8
        weight = (((numerator*0x342da7f3) >> 32) >> 5)-(numerator >> 31)
        mixed = [signed((a*(256-weight)+b*weight+128) >> 8,16) for a,b in zip(d,t)]
        if encode_lmw(mixed) == values:
            compatible.append({'preliminary_illum_coordinate':coordinate,
                               'interpolation_weight':weight,'mixed_lmw':mixed})
    packed = [(raw_values[i]&0x7ff) | (((raw_values[i+1]&0x7ff) << 16) if i+1<9 else 0)
              for i in range(0,9,2)]
    gamma_values = gamma['values_uint16']
    additional_capture = None
    r2y_path = ROOT/'outputs/firmware-runtime/capture-color/rgb2ycc/observer/72-2-rgb2ycc.json'
    if r2y_path.is_file():
        r2y = json.loads(r2y_path.read_text(encoding='utf-8'))
        if (r2y['kind'] != 'rgb2ycc' or r2y['caller_relative_offset'] != '0x33e75c'
                or r2y['next_function_relative_offset'] != '0x914c0' or r2y['return_code'] != 0):
            raise ValueError('Additional RGB2YCC sample is not the reviewed original edge')
        additional_capture = {
            'source':str(r2y_path.relative_to(ROOT)), 'kind':'rgb2ycc; original selector3 matrix',
            'raw_uint32':r2y['values_uint32'],
            'int32_bit_view':[signed(value,32) for value in r2y['values_uint32']],
            'caller_relative_offset':r2y['caller_relative_offset'],
            'provider_relative_offset':r2y['next_function_relative_offset'],
            'next_provider':r2y['next_provider'],'return_code':r2y['return_code'],
            'scope':'A separate original capture attempt confirms a distinct RGB/YCC matrix setter. '
                    'These weights do not identify the RGB primaries, white point, transfer or hardware pixel order.'}
    return {
        'scope':'Static original ARM code and captured attempt3 CCM/Gamma; no capture rerun or pixel processing.',
        'sources':{'capture_records':str(directory),'iq':str(iq.path),'udd':str(udd.path),
                   'existing_provenance_analysis':'outputs/metol/rgb-target.json/.asm'},
        'captured_ccm':{'raw_uint32':raw_values,'int32_bit_view':values,
                        'row_sums':[sum(values[i:i+3]) for i in range(0,9,3)],
                        'caller_relative_offset':ccm['caller_relative_offset'],
                        'provider_relative_offset':ccm['next_function_relative_offset'],
                        'returned':ccm['next_provider_returned'],'return_code':ccm['return_code']},
        'additional_capture_evidence':additional_capture,
        'setter_encoding':{
            'abi':'ipcs_color_correction { unsigned int col_mat[9]; }; enum return',
            'raw_accepted_intervals':['0x00000000..0x000003ff','0xfffffc00..0xffffffff'],
            'signed32_accepted_interval':[-1024,1023],
            'invalid_policy':'returns IPCS_INVALID_PARAM(2); does not saturate or rescale coefficients',
            'packing':'coefficient &0x7ff; two coefficients per32-bit word at bits0..10 and16..26',
            'sign_encoding':'32-bit sign-extended inputs map to11-bit two-complement bit patterns; bit10 is the sign bit under that signed interpretation',
            'register_offsets':['0x1078','0x107c','0x1080','0x1084','0x1088'],
            'predicted_words_from_original_code':[f'0x{word:08x}' for word in packed],
            'predicted_words_are_live_readback':False,
            'counterexample':'Coefficient -54 requires raw0xffffffca. Supplying masked0x000007ca is positive1994 and is rejected, despite having the same low11 bits.',
            'hardware_math_limit':'The setter proves serialization, range and sign-compatible packing, not how hardware multiplies, shifts, rounds or clips pixels.'},
        'software_coefficient_scale':{
            'confirmed_reference_unity':128,
            'evidence':[
                'CopyLMWtoLM@0x334814 reconstructs each diagonal as1024 minus two offdiagonals.',
                'MultLMW@0x3348c4 multiplies this representation then adds512 and arithmetic-shifts10 bits.',
                'EncodeLMWtoD4COLMAT@0x334a4c quantizes the six coefficients with+4 and bit extraction equivalent to a3-bit shift in the supported range; diagonals use128 minus offdiagonals.',
                'CIpcsColorD4 constructor@0x33de58 explicitly initializes base self+0xa0 to diag128 and color/R2Y working matrices separately to diag256.',
                'SetRgb2Rgb calls MatrixMult3x3 with divisor256 at0x33e508 and0x33e51c; PSS/ColorTone identity rows are diag256.'],
            'meaning':'The producer uses a normalized Q10 LMW representation and a Q7 base/CCM coefficient representation; Q8 style matrices preserve that Q7 scale through division256.',
            'software_normalized_capture_view':[value/128 for value in values],
            'hardware_divide128_or_shift7_directly_verified':False,
            'reason':'Original hardware pixel MAC/shift logic is not in the UDD setter, and this lab run did not execute equivalent IPCS pixel arithmetic.'},
        'matrix_provenance':{
            'CCMatrix_named_defined_symbols':[name for name in iq.symbols if 'CCMatrix' in name],
            'actual_class':'CIpcsColorD4; no defined CCMatrix-named symbol in this ELF',
            'chain':['CIpcsColorD4::GetIndex updates CColorProcess from tagCOLORINFO',
                     'CalculateColorMatrix: D/T LMW interpolation, optional dark/mode compensation, then EncodeLMWtoD4COLMAT',
                     'GetRgb2RgbMatrix returns CColorProcess singleton+0x10 (9 signed16 coefficients)',
                     'GetRgb2RgbIndex@0x33dff8 copies signed16 entries to node+0xa0 as signed32',
                     'SetRgb2Rgb: intermediate=trunc0(PSS[index]*base/256)',
                     'final=trunc0(ColorTone(interpolated_index)*intermediate/256)',
                     'copies final to node+0x48, CAWB::SetCc and CIQParamInfo::SetColorMatrix; original UDD call at0x33e564'],
            'lmw_initial_storage':lmw,'style_tables':tables,'table_got_bindings':bindings,
            'stored_m_iMatrix_row_matches_capture':[i for i,row in enumerate(tables['m_iMatrix']['rows']) if row == values],
            'm_iMatrix_limit':'This named two-row table is not the base loaded by the reviewed SetRgb2Rgb edge. The base is node+0xa0, refreshed from CColorProcess.',
            'compatible_initial_table_explanations':compatible,
            'compatibility_assumptions':'Pure D/T interpolation plus encoding, no dark/mode compensation and identity PSS/ColorTone. This reproduces all9 captured coefficients for listed choices.',
            'identified_active_illumination_or_table_branch':False,
            'attribution_limit':'The observer did not capture tagCOLORINFO, the selected style indices, pre-composition node+0xa0 or runtime LMW storage. Multiple producer states can quantize to this same final matrix; numerical compatibility is not unique provenance.'},
        'color_domain':{
            'confirmed':'An illuminant/state-dependent native RGB-to-RGB coefficient producer, followed by native style matrix composition and11-bit coefficient serialization.',
            'input_primaries':'unknown','output_primaries':'unknown','reference_white':'unknown',
            'scene_linearity':'not established','absolute_exposure_normalization':'not established',
            'not_supplied_by_code':['RGB primary chromaticities or RGB-to-XYZ basis',
                                    'CIE white point or proof that table names T/D denote particular standard illuminants',
                                    'Pixel order of CCM, Gamma, RGB/YCC conversion and3D LUT',
                                    'Hardware pixel accumulator scale, rounding and clipping'],
            'gamma':{'captured_words':len(gamma_values),'address_code_span':[0,len(gamma_values)-1],
                      'observed_output_code_range':[min(gamma_values),max(gamma_values)],
                      'descending_adjacent_pairs':sum(b<a for a,b in zip(gamma_values,gamma_values[1:])),
                      'sample_index_value':[[i,gamma_values[i]] for i in (0,6,44,177,1500,4096,8191)],
                      'standard_srgb_oetf_identified':False,
                      'scope':'This is the captured1D tone table. Its input code is not proven linear-light RGB, so it cannot yet be assigned a standard OETF/EOTF.'},
            'linear_prophoto_connection_ready':False},
        'instruction_evidence':{
            'udd':instruction_evidence(udd,[0x91258,0x91260,0x91270,0x91278,0x9127c,
                                             0x912d8,0x912dc,0x91304,0x91424,0x91428,
                                             0x91440,0x91444,0x9145c,0x91474,0x91478,0x91490,0x91494]),
            'iq_scale':instruction_evidence(iq,[0x33481c,0x334848,0x334874,0x334a1c,
                                                0x334a20,0x334a60,0x334a64,0x334a68,
                                                0x334a90,0x334aa4,0x334ab0,0x33debc,
                                                0x33def0,0x33deec,0x33dee8,0x33e4e0,0x33e518]),
            'iq_provenance':instruction_evidence(iq,[0x33dfc0,0x33dff8,0x33e004,0x334654,
                                                     0x33e508,0x33e51c,0x33e564,0x334ecc,
                                                     0x3346f0,0x334720,0x334728,0x334730,0x334738]),
        },
        'functions':[function_evidence(iq,name) for name in [
            '_ZN12CIpcsColorD4C1Ev','_ZN12CIpcsColorD48SetParamEj',
            '_ZN12CIpcsColorD410SetRgb2RgbEi','_ZN12CIpcsColorD415GetRgb2RgbIndexEPvRj',
            '_Z16GetRgb2RgbMatrixv','_ZN13CColorProcess20CalculateColorMatrixEv',
            '_ZN13CColorProcess19EncodeLMWtoD4COLMATEPsS0_',
            '_ZN13CColorProcess11CopyLMWtoLMEPsS0_','_ZN13CColorProcess7MultLMWEPsS0_S0_',
            '_ZN13CColorProcess14InterpolateLMWEPsS0_sS0_','_ZN13CColorProcess12GetIllumCompEv']],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture-directory',type=Path,default=CAPTURE)
    parser.add_argument('--output',type=Path,default=OUTPUT)
    args = parser.parse_args()
    report = inspect(args.capture_directory)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2,ensure_ascii=False,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps({'output':str(args.output),'signed_coefficient_range':[-1024,1023],
                      'software_reference_unity':report['software_coefficient_scale']['confirmed_reference_unity'],
                      'hardware_shift7_verified':False,
                      'compatible_initial_table_explanations':report['matrix_provenance']['compatible_initial_table_explanations'],
                      'input_output_primaries_known':False},indent=2))


if __name__ == '__main__':
    main()
