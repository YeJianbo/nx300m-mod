"""Fit an uncalibrated RGB-code-domain IPCM preview proxy from existing cubes.

The physical ISP stage order, encoding and channel order remain unverified.
Profile ABI is shared with the ARM runtime; fitting never accesses a camera.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import zlib

import numpy as np

from cube_to_d4 import load_cube
from look_library import LookLibrary

ROOT = Path(__file__).resolve().parents[1]
PROFILES = ROOT/'outputs/deployment/ipcm-preview/profiles'
MAGIC, VERSION, SIZE = 0x5056584e, 2, 360
MODEL_VERSION = 'rgb-code-proxy-ccm-sharedtone-ycc-v2'
# Rec.709 luma, full-range zero-centred chroma. These are proxy coordinates.
RGB_TO_YCC = np.array([[.2126, .7152, .0722],
                       [-.2126/1.8556, -.7152/1.8556, .5],
                       [.5, -.7152/1.5748, -.0722/1.5748]])
YCC_TO_RGB = np.linalg.inv(RGB_TO_YCC)
# CIpcmColorD4::m_iRgb2Yuv at IQ .rodata 0x36e494. These coefficients
# define a default linear proxy, not a measured native encoding/range.
NATIVE_RGB_TO_YCC = np.array([[77, 150, 29], [-43, -85, 128], [128, -107, -21]])/256
NATIVE_YCC_TO_RGB = np.linalg.inv(NATIVE_RGB_TO_YCC)


def round_away(value):
    value = np.asarray(value)
    return np.sign(value)*np.floor(np.abs(value)+.5)


def quantize_model(ccm, tone, chroma):
    return {'ccm_q12': round_away(np.asarray(ccm).reshape(9)*4096).astype(int).tolist(),
            'chroma_q12': round_away(np.asarray(chroma).reshape(4)*4096).astype(int).tolist(),
            'tone': round_away(np.clip(tone, 0, 1)*255).astype(int).tolist()}


def identity_model():
    return quantize_model(np.eye(3), np.arange(256)/255, np.eye(2))


def write_profile(path, model):
    full = 'ycc_q12' in model
    pcse = 'pcse_delta' in model
    if pcse and not full:
        raise ValueError('PCSE v3 requires the full YCC matrix')
    payload = struct.pack('<18i256B' if full else '<13i256B', *model['ccm_q12'],
                          *model['ycc_q12' if full else 'chroma_q12'], *model['tone'])
    if pcse:
        delta = np.asarray(model['pcse_delta'])
        if delta.shape != (2, 12) or not np.isfinite(delta).all() or np.any(delta != np.trunc(delta)) or np.any(np.abs(delta)>4095):
            raise ValueError('PCSE requires 2x12 integer native deltas in -4095..4095')
        payload += struct.pack('<24i', *delta.reshape(-1).astype(int))
    header = struct.pack('<8I', MAGIC, 3 if pcse else 2 if full else 1, 32+len(payload), 0, zlib.crc32(payload), 0, 0, 0)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header+payload)


def read_profile(path):
    data = Path(path).read_bytes()
    if len(data) not in (340, 360, 456):
        raise ValueError('Profile must be v1 340, v2 360 or v3 456 bytes')
    magic, version, size, flags, crc, *reserved = struct.unpack_from('<8I', data)
    if magic != MAGIC or (version, size) not in ((1, 340), (2, 360), (3, 456)) or size != len(data) or flags or any(reserved):
        raise ValueError('Unsupported IPCM profile header')
    if zlib.crc32(data[32:]) != crc:
        raise ValueError('IPCM profile payload CRC mismatch')
    count = 18 if version >= 2 else 13
    values = struct.unpack_from('<18i256B' if version >= 2 else '<13i256B', data, 32)
    if any(abs(x) > 16384 for x in values[:count]):
        raise ValueError('IPCM coefficient exceeds +/-4 Q12')
    if any(a > b for a, b in zip(values[count:], values[count+1:])):
        raise ValueError('IPCM tone must be monotone')
    result = {'ccm_q12': list(values[:9]), 'ycc_q12' if version >= 2 else 'chroma_q12': list(values[9:count]),
              'tone': list(values[count:])}
    if version==3:
        delta=struct.unpack_from('<24i', data, 360)
        if any(abs(x)>4095 for x in delta):
            raise ValueError('PCSE native delta outside -4095..4095')
        result['pcse_delta']=[list(delta[:12]),list(delta[12:])]
    return result


def strength_model(model, amount):
    if not np.isfinite(amount) or not 0 <= amount <= 1:
        raise ValueError('amount must be 0..1')
    result = {}
    full = 'ycc_q12' in model
    percent = int(round(amount*100))

    def blend(value, identity):
        if abs(amount*100-percent) < 1e-10:
            # Match the C integer numerator, including ties at e.g. 54%.
            # Binary-float alpha can move an exact n+.5 below its tie.
            numerator = np.asarray(identity,dtype=np.int64)*(100-percent)+np.asarray(value,dtype=np.int64)*percent
            return np.where(numerator<0,-((-numerator+50)//100),(numerator+50)//100).astype(int).tolist()
        return round_away(identity+amount*(np.asarray(value)-identity)).astype(int).tolist()

    for key, identity in [('ccm_q12', np.eye(3).reshape(-1)*4096),
                          ('ycc_q12' if full else 'chroma_q12', np.eye(3 if full else 2).reshape(-1)*4096),
                          ('tone', np.arange(256))]:
        result[key] = blend(model[key], identity)
    if 'pcse_delta' in model:
        result['pcse_delta']=blend(model['pcse_delta'],np.zeros((2,12),dtype=int))
    return result


def apply_profile(rgb, model, amount=1., pcse_response=None):
    rgb = np.asarray(rgb, dtype=np.float64)
    shape = rgb.shape
    if shape[-1] != 3:
        raise ValueError('RGB input must end with 3 channels')
    if amount == 0:
        return np.clip(rgb.copy(), 0, 1)
    m = strength_model(model, amount)
    x = np.clip(rgb.reshape(-1, 3)@np.asarray(m['ccm_q12']).reshape(3, 3).T/4096, 0, 1)
    x = np.interp(x, np.arange(256)/255, np.asarray(m['tone'])/255)
    if 'ycc_q12' in m:
        ycc = x@NATIVE_RGB_TO_YCC.T
        ycc = ycc@np.asarray(m['ycc_q12']).reshape(3, 3).T/4096
        output=np.clip(ycc@NATIVE_YCC_TO_RGB.T, 0, 1)
        if 'pcse_delta' in m and np.any(m['pcse_delta']):
            if pcse_response is None:
                raise ValueError('Nonzero PCSE parameters require a measured response model for pixel evaluation')
            output=pcse_response.apply(output,np.asarray(m['pcse_delta']).reshape(24))
        return output.reshape(shape)
    ycc = x@RGB_TO_YCC.T
    ycc[:, 1:] = ycc[:, 1:]@np.asarray(m['chroma_q12']).reshape(2, 2).T/4096
    return np.clip(ycc@YCC_TO_RGB.T, 0, 1).reshape(shape)


def sample_cube(cube, rgb):
    size, low, high, values = cube
    rgb = np.asarray(rgb, dtype=np.float64)
    shape = rgb.shape
    coord = np.clip((rgb.reshape(-1, 3)-low)/(np.asarray(high)-low), 0, 1)*(size-1)
    lower = np.minimum(coord.astype(int), size-2)
    frac = coord-lower
    table, output = np.asarray(values), np.zeros_like(coord)
    for db in (0, 1):
        for dg in (0, 1):
            for dr in (0, 1):
                index = ((lower[:, 2]+db)*size+lower[:, 1]+dg)*size+lower[:, 0]+dr
                weight = np.prod(np.where((dr, dg, db), frac, 1-frac), axis=1)
                output += table[index]*weight[:, None]
    return np.clip(output, 0, 1).reshape(shape)


def sample_d4(path, rgb):
    words = np.array(struct.unpack('<2190H', Path(path).read_bytes())).reshape(3, 730)
    table = words[:, :729].reshape(3, 9, 9, 9).transpose(3, 2, 1, 0).reshape(-1, 3)/1023
    return sample_cube((9, (0.,)*3, (1.,)*3, table), rgb)


def error_metrics(target, prediction):
    errors = np.abs(np.asarray(target)-prediction)*255
    per_pixel = errors.mean(axis=-1)
    return {'samples': int(len(per_pixel)), 'mean_absolute_rgb_8bit': float(per_pixel.mean()),
            'p95_absolute_rgb_8bit': float(np.quantile(per_pixel, .95)),
            'rms_rgb_8bit': float(np.sqrt(np.mean(errors**2))),
            'mean_absolute_channels_8bit': errors.mean(axis=0).tolist()}


def evaluation(cube, model, d4=None, color_model=None):
    # Continuous random holdout is independent of the discrete training grid.
    x = np.random.default_rng(53281).random((18000, 3))
    gray = np.repeat(np.linspace(0, 1, 1024)[:, None], 3, axis=1)
    masks = {'uniform_rgb_holdout': np.ones(len(x), bool),
             'blue_code_region': (x[:, 2] > x[:, 0]+.12) & (x[:, 2] > x[:, 1]+.03),
             'skin_like_code_region': (x[:, 0] > x[:, 1]) & (x[:, 1] > x[:, 2]) &
                (x[:, 0] > .25) & (x[:, 0]-x[:, 2] < .55) & (x[:, 1]-x[:, 2] > .03)}
    targets = {'source_cube': sample_cube(cube, x)}
    if d4 is not None and Path(d4).exists():
        targets['existing_9cube_10bit_table'] = sample_d4(d4, x)
    result = {}
    apply = apply_profile if color_model is None else color_model.apply
    for name, target in targets.items():
        result[name] = {}
        for amount in (0., .5, 1.):
            prediction = apply(x, model, amount)
            target_strength = x+amount*(target-x)
            regions = {region: error_metrics(target_strength[mask], prediction[mask]) for region, mask in masks.items()}
            gt = sample_cube(cube, gray) if name == 'source_cube' else sample_d4(d4, gray)
            regions['gray_ramp'] = error_metrics(gray+amount*(gt-gray), apply(gray, model, amount))
            result[name][str(round(amount*100))] = regions
    return result


def fit_model_legacy(cube, iterations=600, neutral_weighted=False):
    axis = np.linspace(0, 1, 17)
    grid = np.stack(np.meshgrid(axis, axis, axis, indexing='ij'), -1).reshape(-1, 3)
    gray = np.repeat(np.linspace(0, 1, 256)[:, None], 3, axis=1)
    x = np.concatenate([grid, gray])
    weights = np.full(len(x), 1/len(x))
    sampling = {'training_samples': len(x), 'training': '17^3 RGB grid plus 256 neutral samples',
                'group_total_weights': {'uniform_grid': len(grid)/len(x), 'neutral': len(gray)/len(x)}}
    if neutral_weighted:
        # Low-chroma codes occupy a small RGB-cube volume despite common gray
        # scene surfaces. Match the LUT there; do not impose neutral output.
        directions = np.array([[1, -.5, -.5], [-.5, 1, -.5], [-.5, -.5, 1],
                               [-1, .5, .5], [.5, -1, .5], [.5, .5, -1]])
        near_gray = np.clip(np.linspace(.01, .99, 64)[:, None, None, None]
                           + directions[None, :, None, :]*np.array([.01, .025, .05, .08])[None, None, :, None], 0, 1).reshape(-1, 3)
        x = np.concatenate([grid, gray, near_gray])
        weights = np.concatenate([np.full(len(grid), .70/len(grid)), np.full(len(gray), .15/len(gray)),
                                  np.full(len(near_gray), .15/len(near_gray))])
        sampling = {'training_samples': len(x),
                    'training': '17^3 RGB grid; 256 neutral samples; 64 luminance codes x 6 RGB directions x 4 small chroma offsets',
                    'group_total_weights': {'uniform_grid': .70, 'neutral': .15, 'near_gray': .15},
                    'near_gray_offsets': [.01, .025, .05, .08],
                    'rationale': 'increase synthetic low-chroma source coverage; preserve source LUT response, including intentional casts; no scene-image fitting'}
    y = sample_cube(cube, x)
    ccm = np.linalg.lstsq(x*np.sqrt(weights[:, None]), y*np.sqrt(weights[:, None]), rcond=None)[0].T
    tone, chroma = np.linspace(0, 1, 33), np.eye(2)
    # Adam with projected monotone knots; smoothness penalises tone curvature.
    params = [ccm, tone, chroma]
    m = [np.zeros_like(p) for p in params]
    v = [np.zeros_like(p) for p in params]
    best, best_loss, trajectory = None, float('inf'), []
    for step in range(1, iterations+1):
        raw = x@ccm.T
        u = np.clip(raw, 0, 1)*32
        index = np.minimum(u.astype(int), 31)
        fraction = u-index
        toned = tone[index]*(1-fraction)+tone[index+1]*fraction
        ycc = toned@RGB_TO_YCC.T
        out_ycc = ycc.copy()
        out_ycc[:, 1:] = ycc[:, 1:]@chroma.T
        out_raw = out_ycc@YCC_TO_RGB.T
        output = np.clip(out_raw, 0, 1)
        if step == 1 or step % 25 == 0 or step == iterations:
            quantized = quantize_model(ccm, np.interp(np.arange(256)/255, np.linspace(0, 1, 33), tone), chroma)
            qloss = float(np.sum(np.mean((apply_profile(x, quantized)-y)**2, axis=1)*weights))
            trajectory.append({'iteration': step, 'quantized_training_mse': qloss})
            if qloss < best_loss:
                best_loss, best = qloss, quantized
        grad_rgb = 2*(output-y)*weights[:, None]/3*((out_raw > 0) & (out_raw < 1))
        grad_ycc = grad_rgb@YCC_TO_RGB
        grad_chroma = grad_ycc[:, 1:].T@ycc[:, 1:]
        grad_ycc[:, 1:] = grad_ycc[:, 1:]@chroma
        grad_toned = grad_ycc@RGB_TO_YCC
        grad_tone = np.zeros(33)
        np.add.at(grad_tone, index.ravel(), (grad_toned*(1-fraction)).ravel())
        np.add.at(grad_tone, (index+1).ravel(), (grad_toned*fraction).ravel())
        curvature = np.diff(tone, 2)
        grad_tone[:-2] += .02*curvature
        grad_tone[1:-1] -= .04*curvature
        grad_tone[2:] += .02*curvature
        grad_u = grad_toned*(tone[index+1]-tone[index])*32*((raw > 0) & (raw < 1))
        grad_ccm = grad_u.T@x
        grads = [grad_ccm, grad_tone, grad_chroma]
        for i, (p, gradient) in enumerate(zip(params, grads)):
            m[i] = .9*m[i]+.1*gradient
            v[i] = .999*v[i]+.001*gradient*gradient
            p -= (.007 if i == 1 else .006)*(m[i]/(1-.9**step))/(np.sqrt(v[i]/(1-.999**step))+1e-8)
        np.clip(ccm, -4, 4, out=ccm)
        np.clip(chroma, -4, 4, out=chroma)
        tone[:] = np.maximum.accumulate(np.clip(tone, 0, 1))
    return best, {**sampling,
                  'iterations': iterations, 'optimizer': 'numpy Adam; projected monotone 33-knot shared tone; curvature regularization',
                  'fitted_components': ['full_3x3_ccm', 'monotone_shared_1d_tone', 'full_2x2_chroma'],
                  'quantized_training_mse': best_loss, 'trajectory': trajectory}


def fit_model(cube, iterations=160, neutral_weighted=False, method='native-ycc'):
    if method == 'legacy':
        return fit_model_legacy(cube, iterations, neutral_weighted)
    if method == 'general':
        from ipcm_preview_general import fit_general
        baseline, baseline_fit = fit_model(cube, iterations, method='native-ycc')
        model, fitting = fit_general(cube, baseline, iterations)
        fitting['baseline_fit'] = baseline_fit
        return model, fitting
    if method == 'tonal':
        from ipcm_color_model import ColorModel
        from ipcm_preview_tonal import fit_tonal
        baseline, baseline_fit = fit_model(cube, iterations, method='native-ycc')
        model, fitting = fit_tonal(cube, baseline, ColorModel(), iterations)
        fitting['baseline_fit'] = baseline_fit
        return model, fitting
    from ipcm_preview_fit import from_model, solve
    legacy, _ = fit_model_legacy(cube, 600)
    axis = np.linspace(0, 1, 17)
    grid = np.stack(np.meshgrid(axis, axis, axis, indexing='ij'), -1).reshape(-1, 3)
    gray = np.repeat(np.linspace(0, 1, 256)[:, None], 3, axis=1)
    directions = np.array([[1, -.5, -.5], [-.5, 1, -.5], [-.5, -.5, 1],
                           [-1, .5, .5], [.5, -1, .5], [.5, .5, -1]])
    near_gray = np.clip(np.linspace(.01, .99, 64)[:, None, None, None]
                       +directions[None, :, None, :]*np.array([.01, .025, .05, .08])[None, None, :, None], 0, 1).reshape(-1, 3)
    x = np.concatenate([grid, gray, near_gray])
    weights = np.concatenate([np.full(len(grid), .70/len(grid)), np.full(len(gray), .15/len(gray)),
                              np.full(len(near_gray), .15/len(near_gray))])
    y = sample_cube(cube, x)

    def score(model):
        return sum(np.sum(np.mean((apply_profile(x, model, alpha)-(x+alpha*(y-x)))**2, axis=1)*weights)/2
                   for alpha in (.5, 1.))

    candidates = [('legacy-v1', legacy, {'quantized_training_mse': score(legacy)})]
    for name, initial in [('legacy', legacy), ('identity', identity_model())]:
        model, details = solve(x, y, weights, from_model(initial, full_post=True), iterations=iterations, strengths=(1., .5))
        candidates.append(('full-ycc-'+name, model, details))
    selected = min(candidates, key=lambda item: score(item[1]))
    return selected[1], {'training_samples': len(x),
        'training': 'synthetic RGB grid, neutral ramp, near-neutral offsets; no image pixels',
        'group_total_weights': {'uniform': .70, 'neutral': .15, 'near_gray': .15},
        'selected': selected[0], 'strengths': [50, 100],
        'fitted_components': ['full_3x3_ccm', 'monotone_shared_1d_tone',
                              'full_3x3_ycc' if 'ycc_q12' in selected[1] else 'full_2x2_chroma'],
        'quantized_training_mse': score(selected[1]),
        'candidates': {name: {**details, 'selection_mse': score(model)} for name, model, details in candidates}}


def fit_look(library, key, profiles_root=PROFILES, iterations=160, neutral_weighted=False, method='native-ycc'):
    metadata = library.get(key)
    source = library.directory(key)/metadata['master']
    cube = load_cube(source)
    model, fitting = fit_model(cube, iterations, neutral_weighted, method)
    directory = Path(profiles_root)/key
    directory.mkdir(parents=True, exist_ok=True)
    write_profile(directory/'profile.bin', model)
    # Read back the exact runtime payload before reporting its errors.
    model = read_profile(directory/'profile.bin')
    color_model = None
    if method == 'tonal':
        from ipcm_color_model import ColorModel
        color_model = ColorModel()
    report = {'id': key, 'label': metadata['label'], 'status': 'compiled-unverified',
              'source': str(source.resolve()), 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
              'source_declared_input_space': metadata['input_space'], 'source_declared_output_space': metadata['output_space'],
              'compiler_version': 'ipcm-tonal-native-words-v1' if method == 'tonal' else 'ipcm-fit-v3-universal' if method == 'general' else 'ipcm-fit-v2-gn',
              'proxy_model_version': 'tonal-code-native-parameter-reference' if color_model else MODEL_VERSION if 'ycc_q12' in model else 'rgb-code-proxy-ccm-sharedtone-chroma-v1',
              'native_model_verified': False,
              'model_domain': 'RGB code values, sRGB proxy assumption; native colour encoding/stage order/channel order uncalibrated',
              'proxy_order': ('CCM A -> clip -> shared monotone tone T -> stock Q8-default RGB/YCC proxy -> full YCC C -> RGB -> clip'
                              if 'ycc_q12' in model else 'CCM A -> clip -> shared monotone tone T -> Rec709 RGB/YCC -> chroma B -> RGB -> clip'),
              'runtime_composition': ("CCM'=A*Mstock; tone'[i]=T[stocktone[i]]; YCC'=C*YCCstock; stock CHM retained; physical equivalence unverified"
                                      if 'ycc_q12' in model else "CCM'=A*Mstock; tone'[i]=T[stocktone[i]]; CHM'=B*CHMstock; physical equivalence unverified"),
              'strength': 'Q12 A/B and u8 tone interpolate against identity with ties away from zero; source target uses RGB crossfade',
              'error_definition': 'per pixel mean(abs(RGB error))*255; P95 over pixels; code-region masks are synthetic, not semantic segmentation',
              'profile_bytes': (directory/'profile.bin').stat().st_size, 'profile_version': 2 if 'ycc_q12' in model else 1,
              'profile_file': 'profile.bin', 'quantized_model': model,
              'fit': fitting, 'errors': evaluation(cube, model, library.directory(key)/metadata['native_table'], color_model)}
    if color_model is not None:
        report['color_model'] = color_model.describe()
    (directory/'profile.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--look', action='append', default=[])
    parser.add_argument('--all', action='store_true')
    parser.add_argument('--profiles-root', type=Path, default=PROFILES)
    parser.add_argument('--iterations', type=int, default=160)
    parser.add_argument('--method', choices=('general', 'tonal', 'native-ycc', 'legacy'), default='native-ycc',
                        help='tonal/general are experimental common-objective optimisers; native-ycc remains the deployed baseline')
    parser.add_argument('--neutral-weighted', action='store_true', help='Legacy optimizer: synthetic 70/15/15 percent objective')
    args = parser.parse_args()
    if args.neutral_weighted and args.method != 'legacy':
        parser.error('--neutral-weighted reproduces the legacy experiment; use --method legacy')
    library = LookLibrary()
    keys = args.look
    if args.all:
        keys = [item['id'] for item in library.list()]
    if not keys:
        parser.error('Specify --look ID or --all')
    for key in keys:
        report = fit_look(library, key, args.profiles_root, args.iterations, args.neutral_weighted, args.method)
        print(json.dumps({'id': key, 'profile': str(args.profiles_root/key/'profile.bin'),
                          'error_100': report['errors']['source_cube']['100']['uniform_rgb_holdout']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
