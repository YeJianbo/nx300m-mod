"""Measure the preview correction against the preserved v1, without a camera."""
import json
from pathlib import Path

import numpy as np

from fit_ipcm_preview import (ROOT, PROFILES, apply_profile, error_metrics,
                              load_cube, read_profile, sample_cube, sample_d4)
from ipcm_preview_fit import forward

OUT = ROOT/'outputs/deployment/ipcm-preview/evaluation'
BASELINE = OUT/'baseline-v1/profiles'


def masks(rgb):
    return {'all': np.ones(len(rgb), bool),
            'blue': (rgb[:, 2] > rgb[:, 0]+.12) & (rgb[:, 2] > rgb[:, 1]+.03),
            'skin_like': (rgb[:, 0] > rgb[:, 1]) & (rgb[:, 1] > rgb[:, 2]) &
                (rgb[:, 0] > .25) & (rgb[:, 0]-rgb[:, 2] < .55) & (rgb[:, 1]-rgb[:, 2] > .03)}


def compare_set(rgb, target, before, after):
    return {str(round(alpha*100)): {
        region: {'before': error_metrics((rgb+alpha*(target-rgb))[mask], apply_profile(rgb, before, alpha)[mask]),
                 'after': error_metrics((rgb+alpha*(target-rgb))[mask], apply_profile(rgb, after, alpha)[mask])}
        for region, mask in masks(rgb).items()} for alpha in (.5, 1.)}


def main():
    rgb = np.random.default_rng(20261101).random((32000, 3))
    gray = np.repeat(np.linspace(0, 1, 1024)[:, None], 3, axis=1)
    report = {'status': 'offline-correction-evaluated', 'native_pixel_verified': False,
        'test_set': {'seed': 20261101, 'samples': len(rgb), 'training_images': False,
                     'purpose': 'fresh deterministic RGB sample, separate from training and prior fixed validation'},
        'metric': 'mean absolute RGB code error per pixel, scaled to 0..255; not DeltaE',
        'default_ycc_evidence': {'library': 'libiq-fw-slpcam.so', 'symbol': 'CIpcmColorD4::m_iRgb2Yuv',
            'offset': '0x36e494', 'q8_coefficients': [77, 150, 29, -43, -85, 128, 128, -107, -21]},
        'causes': ['v1 preserves luma after tone, limiting saturation-dependent lightness changes',
                   'the old fit substitutes raised tone and unequal CCM row sums for missing post-tone luma controls',
                   'v2 uses complete post-tone YCC matrix and joint 50/100 percent fitting',
                   'v2 defaults to the stock Q8 linear basis; v1 Rec709 profile semantics remain unchanged'],
        'remaining_model_limits': ['native ISP encoding, range, clipping and stage order are uncalibrated',
            'nonidentity stock CHM follows YCC and generally does not commute with the v2 matrix',
            'a global matrix/shared-curve model cannot express every local 3D LUT colour change'],
        'looks': []}
    for directory in sorted(PROFILES.iterdir()):
        if not directory.is_dir():
            continue
        key = directory.name
        before = read_profile(BASELINE/key/'profile.bin')
        after = read_profile(directory/'profile.bin')
        metadata = json.loads((directory/'profile.json').read_text('utf-8'))
        cube = load_cube(ROOT/'looks'/key/'source.cube')
        gt = sample_cube(cube, gray)
        selected = metadata['fit'].get('selected')
        details = metadata['fit'].get('candidates', {}).get(selected, {})
        noise = None
        if 'float_parameters' in details:
            noise = error_metrics(forward(rgb, np.array(details['float_parameters'])), apply_profile(rgb, after))
        item = {'id': key, 'label': metadata['label'], 'version': metadata.get('profile_version', 1),
            'profile_bytes': (directory/'profile.bin').stat().st_size,
            'fresh_rgb_test': compare_set(rgb, sample_cube(cube, rgb), before, after),
            'existing_9cube_target': compare_set(rgb, sample_d4(ROOT/'looks'/key/'table.d4lut', rgb), before, after),
            'gray': {str(round(alpha*100)): {'before': error_metrics(gray+alpha*(gt-gray), apply_profile(gray, before, alpha)),
                'after': error_metrics(gray+alpha*(gt-gray), apply_profile(gray, after, alpha))} for alpha in (.5, 1.)},
            'source_gray_to_identity': error_metrics(gray, gt),
            'ccm_row_sums_before': (np.array(before['ccm_q12']).reshape(3, 3)/4096).sum(axis=1).tolist(),
            'ccm_row_sums_after': (np.array(after['ccm_q12']).reshape(3, 3)/4096).sum(axis=1).tolist(),
            'black': {'target': sample_cube(cube, np.zeros((1, 3)))[0].tolist(),
                      'before': apply_profile(np.zeros((1, 3)), before)[0].tolist(),
                      'after': apply_profile(np.zeros((1, 3)), after)[0].tolist()},
            'quantization_versus_float_proxy': noise,
            'zero_strength_exact': bool(np.array_equal(apply_profile(rgb, after, 0), rgb))}
        report['looks'].append(item)
        print(json.dumps({'id': key, 'gray_before_after': [item['gray']['100'][v]['mean_absolute_rgb_8bit'] for v in ('before','after')],
                          'fresh_before_after': [item['fresh_rgb_test']['100']['all'][v]['mean_absolute_rgb_8bit'] for v in ('before','after')]}, ensure_ascii=False))
    (OUT/'fit-improvement.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


if __name__ == '__main__':
    main()
