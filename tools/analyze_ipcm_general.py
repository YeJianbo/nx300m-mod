"""Independent all-strength comparison for the universal preview compiler."""
import argparse
import json
from pathlib import Path

import numpy as np

from fit_ipcm_preview import ROOT, PROFILES, apply_profile, error_metrics, load_cube, read_profile, sample_cube
from cube_to_d4 import compile_cube
from ipcm_preview_fit import perceptual_features


def measurements(target, prediction):
    result = error_metrics(target, prediction)
    lab_target = perceptual_features(target)[:, 3:]/np.array([1., 2., 2.])
    lab_output = perceptual_features(prediction)[:, 3:]/np.array([1., 2., 2.])
    distance = np.linalg.norm(lab_output-lab_target, axis=1)*100
    result.update({'mean_oklab_distance_x100': float(distance.mean()),
                   'p95_oklab_distance_x100': float(np.quantile(distance, .95)),
                   'maximum_absolute_channel_8bit': float(np.max(np.abs(target-prediction))*255)})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profiles-root', type=Path, default=ROOT/'outputs/deployment/ipcm-preview/evaluation/general-candidates/profiles')
    parser.add_argument('--baseline-root', type=Path, default=ROOT/'outputs/deployment/ipcm-preview/evaluation/baseline-v2/profiles')
    parser.add_argument('--output', type=Path, default=ROOT/'outputs/deployment/ipcm-preview/evaluation/general-fit.json')
    args = parser.parse_args()
    rng = np.random.default_rng(47629)
    uniform = rng.random((32000, 3))
    gray = np.repeat(np.linspace(0, 1, 1024)[:, None], 3, axis=1)
    near_gray = np.clip(rng.random((4096, 1))+rng.normal(0, .035, (4096, 3)), 0, 1)
    groups = {'uniform_rgb': uniform, 'gray': gray, 'near_gray': near_gray}
    masks = {
        'blue_codes': (uniform[:, 2] > uniform[:, 0]+.12) & (uniform[:, 2] > uniform[:, 1]+.03),
        'skin_like_codes': (uniform[:, 0] > uniform[:, 1]) & (uniform[:, 1] > uniform[:, 2]) &
            (uniform[:, 0] > .25) & (uniform[:, 0]-uniform[:, 2] < .55) & (uniform[:, 1]-uniform[:, 2] > .03)}
    groups.update({k: uniform[mask] for k, mask in masks.items()})
    report = {'test_seed': 47629, 'training_or_selection_data': False,
        'native_pixel_verified': False, 'style_specific_settings': False,
        'metrics': {'rgb': 'mean absolute 0..255 codes; not DeltaE',
                    'oklab': 'Euclidean OKLab distance x100 on the sRGB software proxy; not DeltaE00'},
        'source': 'full source.cube, fixed across baseline and new compiler',
        'rgb_9cube_reference': 'fresh compile_cube(source) in memory, decoded in RGB code coordinates; no ISP execution',
        'looks': []}
    for directory in sorted(args.profiles_root.iterdir()):
        if not directory.is_dir():
            continue
        key = directory.name
        baseline_path = args.baseline_root/key/'profile.bin'
        if not baseline_path.exists():
            continue
        baseline = read_profile(baseline_path)
        model = read_profile(directory/'profile.bin')
        cube = load_cube(ROOT/'looks'/key/'source.cube')
        reference_bytes = compile_cube(cube)
        words = np.frombuffer(reference_bytes, dtype='<u2').reshape(3,730)
        table = words[:,:729].reshape(3,9,9,9).transpose(3,2,1,0).reshape(-1,3)/1023
        reference_cube = (9,(0.,)*3,(1.,)*3,table)
        result = {}
        reference_9cube = {}
        for amount in (.25, .5, .75, 1.):
            result[str(int(amount*100))] = {}
            reference_9cube[str(int(amount*100))] = {}
            for name, x in groups.items():
                target = x+amount*(sample_cube(cube, x)-x)
                result[str(int(amount*100))][name] = {
                    'before': measurements(target, apply_profile(x, baseline, amount)),
                    'after': measurements(target, apply_profile(x, model, amount))}
                d4 = sample_cube(reference_cube, x)
                reference_9cube[str(int(amount*100))][name] = measurements(target, x+amount*(d4-x))
        metadata = json.loads((directory/'profile.json').read_text('utf-8'))
        report['looks'].append({'id': key, 'label': metadata['label'], 'selected': metadata['fit']['selected'],
                                'measurements': result, 'rgb_9cube_software_reference': reference_9cube,
                                'zero_strength_exact': bool(np.array_equal(apply_profile(uniform, model, 0), uniform))})
        print(json.dumps({'id': key, 'selected': metadata['fit']['selected'],
            'mean_rgb_100': [result['100']['uniform_rgb'][v]['mean_absolute_rgb_8bit'] for v in ('before','after')],
            'gray_100': [result['100']['gray'][v]['mean_absolute_rgb_8bit'] for v in ('before','after')],
            'perceptual_100': [result['100']['uniform_rgb'][v]['mean_oklab_distance_x100'] for v in ('before','after')],
            'rgb_9cube_reference_100': reference_9cube['100']['uniform_rgb']['mean_absolute_rgb_8bit']},ensure_ascii=False),flush=True)
    if not report['looks']:
        raise ValueError('No matching baseline/new profiles')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n',encoding='utf-8')
    lines = ['# 通用 IPCM 拟合对照', '',
        '同一套算法用于所有风格；32,000 个新 RGB 测试点，另测灰阶与近灰。以下为 100% 强度，RGB 误差标度为 0～255。', '',
        '| 风格 | 原拟合 → 自动选择 | 灰阶旧 → 新 | 9³ RGB 软件参考 | 选择 |',
        '|---|---:|---:|---:|---|']
    for look in report['looks']:
        metrics = look['measurements']['100']
        rgb = [metrics['uniform_rgb'][v]['mean_absolute_rgb_8bit'] for v in ('before','after')]
        gray = [metrics['gray'][v]['mean_absolute_rgb_8bit'] for v in ('before','after')]
        ref = look['rgb_9cube_software_reference']['100']['uniform_rgb']['mean_absolute_rgb_8bit']
        lines.append(f'| {look["label"]} | {rgb[0]:.3f} → {rgb[1]:.3f} | {gray[0]:.3f} → {gray[1]:.3f} | {ref:.3f} | {look["selected"]} |')
    lines += ['', '完整 25/50/75/100% 结果与 OKLab 距离见同目录 general-fit.json。', '',
        '自动选择包含旧模型；选择 baseline-v2 表示新候选没有通过共同指标胜出。9³ 软件参考使用相同源 LUT，未模拟硬件颜色域。', '',
        'baseline-v2 为试验中按共同预算重算的原算法，左列使用保存的上一版产物；迭代预算差异会产生微小数值变化。', '',
        '本轮候选用于算法研究，没有覆盖默认预览配置或修改相机。多起点试验没有证明全局最优。', '']
    args.output.with_suffix('.md').write_text('\n'.join(lines),encoding='utf-8')


if __name__ == '__main__':
    main()
