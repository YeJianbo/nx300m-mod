"""Read-only audit of selected Exact DCP sources and active LUT masters.

Writes one audit JSON only; never rebuilds a library entry or touches a camera.
"""
import argparse
import colorsys
import json
import math
from pathlib import Path

from adobe_profiles import CreativeLook, DcpProfile, DcpStyleStage, SRGB_TO_PROPHOTO_D50, PROPHOTO_TO_SRGB_D50
from adobe_profiles import _matrix, srgb_decode, srgb_encode
from cube_to_d4 import load_cube, compile_cube
from stock_lut import factory_3d_lut

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE = Path('./local/lightroom/Resources/CameraProfiles/Adobe Standard/Samsung NX300 Adobe Standard.dcp')
COMPONENTS = {
    50708: 'UniqueCameraModel', 50721: 'ColorMatrix1', 50722: 'ColorMatrix2',
    50723: 'CameraCalibration1', 50724: 'CameraCalibration2', 50727: 'AnalogBalance',
    50730: 'BaselineExposure', 50778: 'CalibrationIlluminant1', 50779: 'CalibrationIlluminant2',
    50932: 'ProfileCalibrationSignature', 50937: 'ProfileHueSatMapDims',
    50938: 'ProfileHueSatMapData1', 50939: 'ProfileHueSatMapData2',
    50940: 'ProfileToneCurve', 50964: 'ForwardMatrix1', 50965: 'ForwardMatrix2',
    51107: 'ProfileHueSatMapEncoding', 51109: 'BaselineExposureOffset', 51110: 'DefaultBlackRender',
}
SAMPLES = {'blue': (0.,0.,1.), 'cyan': (0.,1.,1.), 'sky-blue': (.2,.5,.9),
           'cyan-mid': (.1,.6,.7), 'red': (1.,0.,0.), 'green': (0.,1.,0.),
           'neutral-mid': (.5,.5,.5), 'white': (1.,1.,1.)}


def sdk_reference_prophoto(profile, rgb):
    """Independent scalar reading of SDK RefBaselineHueSatMap, linear ProPhoto.

    Uses the SDK's h in [0,6), row stepping and staged H/V/S interpolation;
    floating analytic transfer functions replace SDK's sampled 1D tables.
    """
    hd, sd, vd = profile.get(50981)
    table = profile.get(50982)
    encoding = profile.get(51108, [0])[0]
    h, s, v = colorsys.rgb_to_hsv(*(max(0., min(1., x)) for x in rgb))
    input_value = v
    h *= 6.
    ve = srgb_encode(v) if vd > 1 and encoding == 1 else v
    input_encoded_value = ve
    hs = h*(hd/6. if hd >= 2 else 0.)
    ss, vs = s*(sd-1), ve*(vd-1)
    h0, s0 = int(hs), min(int(ss), sd-2)
    h1 = h0+1
    if h0 >= hd-1: h0, h1 = hd-1, 0
    v0 = min(int(vs), vd-2) if vd > 1 else 0
    hf, sf, vf = hs-h0, ss-s0, vs-v0 if vd > 1 else 0.

    def entry(hi, si, vi):
        offset = (vi*hd*sd+hi*sd+si)*3
        return table[offset:offset+3]

    modifications = []
    for component in range(3):
        sat = []
        for si in (s0, s0+1):
            val = []
            for vi in ((v0, v0+1) if vd > 1 else (0,)):
                a, b = entry(h0,si,vi)[component], entry(h1,si,vi)[component]
                val.append((1-hf)*a+hf*b)
            sat.append((1-vf)*val[0]+vf*val[1] if vd > 1 else val[0])
        modifications.append((1-sf)*sat[0]+sf*sat[1])
    hue, saturation, value = modifications
    ve = max(0., min(1., ve*value))
    v = srgb_decode(ve) if vd > 1 and encoding == 1 else ve
    result = colorsys.hsv_to_rgb(((h+hue/60.)/6.)%1., min(s*saturation,1.), v)
    return result, {'hue_shift_degrees': hue, 'saturation_scale': saturation, 'value_scale': value,
                    'input_prophoto_hsv': [h/6.,s,input_value],
                    'input_encoded_value': input_encoded_value, 'output_encoded_value': ve}


def summarize(value):
    if value is None or isinstance(value, str): return value
    if len(value) <= 9: return value
    return {'count': len(value), 'minimum': min(value), 'maximum': max(value)}


def component_comparison(profile, base, tag):
    actual, reference = profile.get(tag), base.get(tag)
    result = {'source_present': tag in profile.tags, 'adobe_standard_present': tag in base.tags,
              'equal_decoded': actual == reference,
              'equal_tag_payload': profile.tags.get(tag) == base.tags.get(tag),
              'source': summarize(actual), 'adobe_standard': summarize(reference)}
    if isinstance(actual,list) and isinstance(reference,list) and len(actual)==len(reference):
        result['max_absolute_difference'] = max((abs(a-b) for a,b in zip(actual,reference)),default=0.)
    return result


def audit_entry(key, base):
    directory = ROOT/'looks'/key
    metadata = json.loads((directory/'look.json').read_text(encoding='utf-8'))
    profile = DcpProfile(metadata['source_path'])
    look = CreativeLook(profile)
    cube = load_cube(directory/metadata['master'])
    size, low, high, values = cube
    if low != (0.,0.,0.) or high != (1.,1.,1.): raise ValueError('Unexpected Exact master domain')
    maximum = [0.,0.,0.]
    worst = None
    for index, actual in enumerate(values):
        r, g, b = index%size, index//size%size, index//(size*size)
        expected = look.apply_srgb((r/(size-1),g/(size-1),b/(size-1)))
        difference = [abs(a-e) for a,e in zip(actual,expected)]
        if max(difference) > max(maximum): worst={'input':[r/(size-1),g/(size-1),b/(size-1)],'stored':actual,'current_source':expected}
        maximum = [max(a,b) for a,b in zip(maximum,difference)]
    source_samples = {}
    style = DcpStyleStage(profile)
    for name, rgb in SAMPLES.items():
        prophoto = _matrix(SRGB_TO_PROPHOTO_D50, tuple(srgb_decode(x) for x in rgb))
        reference, delta = sdk_reference_prophoto(profile, prophoto)
        converted = _matrix(PROPHOTO_TO_SRGB_D50, reference)
        reference_srgb = tuple(srgb_encode(max(0.,min(1.,x))) for x in converted)
        actual = look.apply_srgb(rgb)
        styled = style.apply_prophoto(prophoto)
        styled_linear_srgb = _matrix(PROPHOTO_TO_SRGB_D50, styled)
        styled_srgb = tuple(srgb_encode(max(0.,min(1.,v))) for v in styled_linear_srgb)
        source_samples[name] = {'input_srgb':rgb, 'creative_only_srgb':actual,
            'sdk_formula_srgb':reference_srgb,
            'sdk_formula_max_difference':max(abs(a-b) for a,b in zip(actual,reference_srgb)),
            'look_delta':delta,
            'unrendered_style_comparison': {
                'input_linear_prophoto':prophoto,
                'style_output_linear_prophoto':styled,
                'style_output_srgb':styled_srgb,
                'style_minus_look_only_srgb_255':[255*(a-b) for a,b in zip(styled_srgb,actual)],
                'output_linear_srgb_before_clip':styled_linear_srgb,
                'clipped_output_channels':[i for i,v in enumerate(styled_linear_srgb) if v<0 or v>1],
                'scope':'Same hypothetical pre-style working RGB; not an inverse-rendered JPEG or Lightroom photo comparison'}}
    comparisons = {name:component_comparison(profile,base,tag) for tag,name in COMPONENTS.items()}
    curve = profile.get(50940)
    curve_details = None
    if curve:
        pairs = list(zip(curve[0::2],curve[1::2]))
        curve_details = {'points':len(pairs),'first':pairs[0],'last':pairs[-1],
            'max_absolute_identity_difference':max(abs(x-y) for x,y in pairs),
            'near_midpoint':min(pairs,key=lambda p:abs(p[0]-.5))}
    return {'id':key,'metadata_source_path':metadata['source_path'],
        'source_profile_name':profile.get(50936),'metadata_source_profile':metadata.get('source_profile'),
        'source_name_matches_metadata':profile.get(50936)==metadata.get('source_profile'),
        'source_camera':profile.get(50708),'look_dims':profile.get(50981),
        'look_encoding':profile.get(51108,[0]),
        'look_table_components':{'hue_min':min(look.values[0::3]),'hue_max':max(look.values[0::3]),
            'saturation_min':min(look.values[1::3]),'saturation_max':max(look.values[1::3]),
            'value_min':min(look.values[2::3]),'value_max':max(look.values[2::3])},
        'master':{'path':str(directory/metadata['master']),'size':size,'max_absolute_difference_current_source':maximum,
            'matches_current_source_to_export_rounding':max(maximum)<=5.00001e-10,'worst':worst},
        'native_direct_matches_master_compile':(directory/metadata['native_table']).read_bytes()==compile_cube(cube),
        'native_stock_composed_matches_master_compile':(directory/metadata['native_stock_3d_table']).read_bytes()==compile_cube(cube,factory_3d_lut()),
        'excluded_components_vs_adobe_standard':comparisons,'source_tone_curve':curve_details,
        'style_stage':style.metadata(), 'sample_colors':source_samples}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adobe-standard',type=Path,default=DEFAULT_BASE)
    parser.add_argument('--output',type=Path,default=ROOT/'outputs/looks/ricoh-color-audit.json')
    args=parser.parse_args()
    base=DcpProfile(args.adobe_standard)
    entries=[audit_entry(key,base) for key in ('ricoh-positive-exact','ricoh-negative-exact')]
    forward=SRGB_TO_PROPHOTO_D50;inverse=PROPHOTO_TO_SRGB_D50
    identity=[[sum(inverse[r][k]*forward[k][c] for k in range(3)) for c in range(3)] for r in range(3)]
    report={'scope':'Read-only source DCP, active master and compiled-table audit; no RAW development, image preview, camera operation or LUT rebuild.',
        'adobe_standard':base.metadata(),'adobe_standard_look_encoding':base.get(51108,[0]),
        'adobe_standard_baseline_exposure_offset_default':base.get(51109,[0]),
        'adobe_standard_default_black_render_default':base.get(51110,[0]),
        'sdk_sources':{'white_normalization':'work/reference/dng-sdk/dng_color_space.cpp:210-231',
            'srgb_pcs_matrix':'work/reference/dng-sdk/dng_color_space.cpp:258-266',
            'prophoto_pcs_matrix':'work/reference/dng-sdk/dng_color_space.cpp:761-770',
            'table_order':'work/reference/dng-sdk/dng_hue_sat_map.cpp:155-158',
            'hsv_interpolation_encoding':'work/reference/dng-sdk/dng_reference.cpp:1401-1631',
            'look_then_tone':'work/reference/dng-sdk/dng_render.cpp:1107-1142'},
        'matrix':{'srgb_linear_to_prophoto_d50':forward,'inverse_roundtrip':identity,
            'white_maps_to':_matrix(forward,(1.,1.,1.)),
            'finding':'Direction and shared D50 PCS row normalization match SDK. Both PCS white diagonal factors cancel in inverse(ProPhotoPCS)*sRGBPCS; no extra Bradford transform belongs between those two already-adapted PCS matrices.'},
        'entries':entries,
        'findings':['No matrix-direction, D50-adaptation, HSV-axis or encoding defect found against inspected SDK code.',
            'Exact Color/Forward matrices and HueSatMaps are compared to Adobe Standard separately from style-specific ToneCurve/BaselineExposureOffset/DefaultBlackRender.',
            'Only Creative LookTable is currently extracted. Full Lightroom rendering and photographic reference matching are not established.']}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps({'output':str(args.output),'entries':[{
        'id':e['id'],'master_matches_current_source':e['master']['matches_current_source_to_export_rounding'],
        'direct_matches':e['native_direct_matches_master_compile'],
        'stock_composed_matches':e['native_stock_composed_matches_master_compile'],
        'baseline_exposure_offset':e['excluded_components_vs_adobe_standard']['BaselineExposureOffset']['source'],
        'default_black_render':e['excluded_components_vs_adobe_standard']['DefaultBlackRender']['source'],
        'tone_curve':e['source_tone_curve']} for e in entries]},ensure_ascii=False,indent=2))


if __name__=='__main__': main()
