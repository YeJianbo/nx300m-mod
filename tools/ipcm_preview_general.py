"""Style-independent fitting and selection for the existing preview hardware.

All settings are shared. The source cube supplies targets and automatic linear
initialisers; a look name or scene photograph is never used by the optimiser.
"""
import numpy as np

from fit_ipcm_preview import (apply_profile, identity_model, sample_cube,
                              NATIVE_RGB_TO_YCC, NATIVE_YCC_TO_RGB)
from ipcm_preview_fit import from_model, perceptual_features, solve

STRENGTHS = (.25, .5, .75, 1.)


def hsv_rgb(h, s, v):
    # Internal hue zero is yellow; the uniform ring has no preferred hue.
    k = (h[:, None]*6+np.array([0., 4., 2.])) % 6
    return v[:, None]*(1-s[:, None]*np.clip(np.minimum(k, 4-k), 0, 1))


def samples(selection=False):
    """Balance cube volume, hue/value coverage and neutral surfaces.

    Selection uses continuous, independently seeded coordinates. It is used
    for automatic model choice, so neither set is reported as a final test.
    """
    if selection:
        rng = np.random.default_rng(78429)
        cube = rng.random((3072, 3))
        hsv = hsv_rgb(*rng.random((3, 3072)))
        gray = np.repeat(rng.random((192, 1)), 3, axis=1)
    else:
        axis = np.linspace(0, 1, 13)
        cube = np.stack(np.meshgrid(axis, axis, axis, indexing='ij'), -1).reshape(-1, 3)
        h, s, v = np.meshgrid(np.arange(36)/36, np.linspace(.08, 1, 8), np.linspace(.04, 1, 8), indexing='ij')
        hsv = hsv_rgb(h.ravel(), s.ravel(), v.ravel())
        gray = np.repeat(np.linspace(0, 1, 192)[:, None], 3, axis=1)
    rgb = np.concatenate([cube, hsv, gray])
    weights = np.concatenate([np.full(len(cube), .4/len(cube)), np.full(len(hsv), .4/len(hsv)),
                              np.full(len(gray), .2/len(gray))])
    return rgb, weights


def selection_objective(cube, rgb, weights):
    target = sample_cube(cube, rgb)
    references = {a: perceptual_features(rgb+a*(target-rgb)) for a in STRENGTHS}

    def score(model):
        values = []
        for a in STRENGTHS:
            residual = perceptual_features(apply_profile(rgb, model, a))-references[a]
            squared = np.mean(residual**2, axis=1)
            # Weighted mean protects common low-chroma codes; an unweighted
            # upper-tail term prevents trading a few severe errors for it.
            tail = squared >= np.quantile(squared, .95)
            values.append(float(weights@squared+.15*np.mean(squared[tail])))
        return float(np.mean(values))
    return score


def choose(cube, candidates, baseline):
    """Select on common coverage while checking the RGB volume separately.

    HSV and neutral coverage must not hide a regression in the rest of the
    cube. The small absolute margins account for the uint8 tone quantisation.
    These bounds are identical for every source and every strength.
    """
    check, weights = samples(selection=True)
    score = selection_objective(cube, check, weights)
    rgb = check[:3072]
    target = sample_cube(cube, rgb)

    def quality(model):
        values = []
        for a in STRENGTHS:
            reference = rgb+a*(target-rgb)
            output = apply_profile(rgb, model, a)
            error = np.mean(np.abs(output-reference), axis=1)*255
            lab = (perceptual_features(output)-perceptual_features(reference))[:,3:]/[1.,2.,2.]
            values.append(np.array([error.mean(), np.quantile(error,.95), np.linalg.norm(lab,axis=1).mean()*100]))
        return np.asarray(values)

    base = quality(baseline)
    margin = np.maximum(np.array([.10,.20,.03]), base*np.array([.02,.03,.02]))
    for name, model, detail in candidates:
        detail['selection_score'] = score(model)
        metrics = quality(model)
        detail['uniform_selection_metrics'] = metrics.tolist()
        detail['uniform_nonregression'] = bool(np.all(metrics <= base+margin))
    eligible = [c for c in candidates if c[2]['uniform_nonregression']]
    return min(eligible, key=lambda item: item[2]['selection_score'])


def robust_refinement(cube, baseline, iterations=100):
    axis = np.linspace(0, 1, 13)
    grid = np.stack(np.meshgrid(axis, axis, axis, indexing='ij'), -1).reshape(-1,3)
    gray = np.repeat(np.linspace(0, 1, 192)[:, None],3,axis=1)
    rng = np.random.default_rng(39173)
    near_gray = np.clip(rng.random((1024,1))+rng.normal(0,.04,(1024,3)),0,1)
    rgb = np.concatenate([grid,gray,near_gray])
    weights = np.concatenate([np.full(len(grid),.75/len(grid)),np.full(len(gray),.125/len(gray)),
                               np.full(len(near_gray),.125/len(near_gray))])
    return solve(rgb,sample_cube(cube,rgb),weights,from_model(baseline,full_post=True,knots=65),
                 iterations=iterations,strengths=STRENGTHS,objective='rgb-mae')


def fit_general(cube, baseline, iterations=100):
    x, weights = samples()
    y = sample_cube(cube, x)
    check, check_weights = samples(selection=True)
    score = selection_objective(cube, check, check_weights)
    candidates = [('baseline-v2', baseline, {'selection_score': score(baseline)})]
    linear = np.linalg.lstsq(x*np.sqrt(weights[:, None]), y*np.sqrt(weights[:, None]), rcond=None)[0].T
    initialisers = [('baseline', from_model(baseline, full_post=True)),
                    ('identity', from_model(identity_model(), full_post=True))]
    # Factor the same automatic least-squares mapping before/after Tone. These
    # seeds explore different local minima rather than hand-adjusting a style.
    for name, a, h in [('linear-pre', linear, np.eye(3)),
                       ('linear-post', np.eye(3), linear)]:
        p = from_model(identity_model(), full_post=True)
        p[:9] = a.ravel()
        p[9:18] = (NATIVE_RGB_TO_YCC@h@NATIVE_YCC_TO_RGB).ravel()
        initialisers.append((name, p))
    for name, initial in initialisers:
        model, detail = solve(x, y, weights, initial, iterations=min(iterations, 100),
                              strengths=STRENGTHS, objective='perceptual')
        detail['selection_score'] = score(model)
        candidates.append(('perceptual-'+name, model, detail))
    model, detail = robust_refinement(cube, baseline, min(iterations,100))
    candidates.append(('robust-rgb65',model,detail))
    selected = choose(cube, candidates, baseline)
    # Refine the selected basin at a higher curve resolution. Payload size,
    # runtime math and the common monotonicity constraint remain unchanged.
    if 'ycc_q12' in selected[1]:
        model, detail = solve(x, y, weights, from_model(selected[1], full_post=True, knots=65),
                              iterations=min(iterations, 60), strengths=STRENGTHS, objective='perceptual')
        detail['selection_score'] = score(model)
        candidates.append(('perceptual-refined65', model, detail))
        selected = choose(cube, candidates, baseline)
    return selected[1], {
        'algorithm': 'universal perceptual multistart and robust RGB refinement with quantized independent selection',
        'training_samples': len(x), 'selection_samples': len(check),
        'training': 'synthetic RGB grid, uniform HSV coverage, neutral ramp; no image pixels; robust branch uses 75/12.5/12.5 RGB/neutral/near-neutral weights',
        'group_total_weights': {'rgb_volume': .4, 'hsv_coverage': .4, 'neutral': .2},
        'strengths': [25, 50, 75, 100], 'selected': selected[0],
        'selection_metric': 'RGB-anchor/OKLab weighted MSE plus 0.15 upper-5-percent mean, averaged over strengths',
        'selection_score': selected[2]['selection_score'],
        'uniform_nonregression_margin': {'mean_rgb_codes': 'max(0.10, 2%)',
            'p95_rgb_codes': 'max(0.20, 3%)', 'mean_oklab_x100': 'max(0.03, 2%)'},
        'baseline_model': baseline,
        'style_specific_rules': False,
        'fitted_components': ['full_3x3_ccm', 'monotone_shared_1d_tone',
                              'full_3x3_ycc' if 'ycc_q12' in selected[1] else 'full_2x2_chroma'],
        'candidates': {name: detail for name, _, detail in candidates}}
