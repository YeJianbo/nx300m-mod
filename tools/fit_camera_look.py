"""Fit an offline sRGB LUT from same-exposure camera JPEG / RAW-render pairs.

Targets are existing full DCP renders. Originals and the active camera library
are read only. Scene holdouts measure transfer beyond the fitted scene; this
does not identify the Samsung ISP input space or reproduce RAW processing.
"""
import argparse
import json
from pathlib import Path
import re

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageCms

from cube_to_d4 import load_cube, compile_cube
from look_preview import srgb_image

ROOT = Path(__file__).resolve().parents[1]


def vertices(rgb, size):
    coordinates = np.clip(np.asarray(rgb, dtype=np.float64), 0, 1)*(size-1)
    low = np.minimum(coordinates.astype(np.int64), size-2)
    fraction = coordinates-low
    indices, weights = [], []
    for b in (0, 1):
        for g in (0, 1):
            for r in (0, 1):
                indices.append(((low[:, 2]+b)*size+low[:, 1]+g)*size+low[:, 0]+r)
                weights.append(np.prod(np.where((r, g, b), fraction, 1-fraction), axis=1))
    return np.stack(indices, axis=1), np.stack(weights, axis=1)


def apply(table, rgb):
    size = round(len(table)**(1/3))
    indices, weights = vertices(rgb, size)
    return np.sum(table[indices]*weights[:, :, None], axis=1)


def prior_table(path, size):
    source_size, low, high, values = load_cube(path)
    if low != (0.,)*3 or high != (1.,)*3:
        raise ValueError('The prior must use the unit sRGB domain')
    axis = np.linspace(0, 1, size)
    coordinates = np.array([(r, g, b) for b in axis for g in axis for r in axis])
    return apply(np.array(values).reshape(source_size**3, 3), coordinates)


def align(source, target):
    """Find a restricted affine transform; evaluate separate feature matches."""
    def features(image):
        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        gray = cv2.createCLAHE(2., (8, 8)).apply(gray)
        return cv2.SIFT_create(nfeatures=5000).detectAndCompute(gray, None)
    source_points, source_desc = features(source)
    target_points, target_desc = features(target)
    if source_desc is None or target_desc is None:
        raise ValueError('No alignment features')
    matches = cv2.BFMatcher().knnMatch(target_desc, source_desc, k=2)
    pairs = [a for pair in matches if len(pair) == 2 for a, b in [pair]
             if a.distance < .7*b.distance]
    if len(pairs) < 80:
        raise ValueError(f'Only {len(pairs)} unambiguous matches; not enough for color fitting')
    before = np.float32([target_points[m.queryIdx].pt for m in pairs])
    after = np.float32([source_points[m.trainIdx].pt for m in pairs])
    order = np.random.default_rng(1807).permutation(len(pairs))
    split = int(len(order)*.75)
    train, holdout = order[:split], order[split:]
    cv2.setRNGSeed(1807)
    matrix, inliers = cv2.estimateAffinePartial2D(before[train], after[train],
        method=cv2.RANSAC, ransacReprojThreshold=1.5, maxIters=5000, confidence=.999)
    if matrix is None:
        raise ValueError('Could not align this pair')
    errors = np.linalg.norm(before[holdout]@matrix[:, :2].T+matrix[:, 2]-after[holdout], axis=1)
    scale = float(np.hypot(matrix[0, 0], matrix[1, 0]))
    rotation = float(np.degrees(np.arctan2(matrix[1, 0], matrix[0, 0])))
    evidence = {'target_to_camera_affine': matrix.tolist(), 'matches': len(pairs),
                'training_inliers': int(inliers.sum()), 'heldout_matches': len(holdout),
                'heldout_median_error_pixels': float(np.median(errors)),
                'heldout_p95_error_pixels': float(np.quantile(errors, .95)),
                'scale': scale, 'rotation_degrees': rotation,
                'coordinate_space': 'resized sRGB images'}
    if (not .97 < scale < 1.03 or abs(rotation) > .5 or inliers.mean() < .7
            or np.median(errors) > .75 or np.quantile(errors, .95) > 2.):
        raise ValueError('Pair alignment unsuitable: '+json.dumps(evidence))
    height, width = source.shape[:2]
    aligned = cv2.warpAffine(target, matrix, (width, height), flags=cv2.INTER_LINEAR)
    valid = cv2.warpAffine(np.full(target.shape[:2], 255, np.uint8), matrix,
                           (width, height), flags=cv2.INTER_NEAREST)
    valid = cv2.erode(valid, np.ones((17, 17), np.uint8),
                      borderType=cv2.BORDER_CONSTANT, borderValue=0) > 0
    return aligned, valid, evidence, matrix


def samples(source, target, valid, limit):
    # Smooth/color regions reduce errors from demosaic, sharpening, subpixel
    # displacement and JPEG edges. Both sides must have usable geometry.
    source = cv2.GaussianBlur(source.astype(np.float64)/255., (5, 5), .8)
    target = cv2.GaussianBlur(target.astype(np.float64)/255., (5, 5), .8)
    keep = valid.copy()
    for image in (source, target):
        dx = np.max(np.abs(cv2.Sobel(image, cv2.CV_64F, 1, 0)), axis=2)
        dy = np.max(np.abs(cv2.Sobel(image, cv2.CV_64F, 0, 1)), axis=2)
        keep &= np.maximum(dx, dy) < .12
    keep &= np.all((source > .02) & (source < .98), axis=2)
    y, x = np.nonzero(keep)
    if len(x) < 1000:
        raise ValueError('Not enough unclipped, smooth aligned pixels')
    rng = np.random.default_rng(1807)
    order = rng.permutation(len(x))
    # Limit dense scene colors rather than allowing a large sky/wall to
    # dominate every fitted coefficient.
    counts = np.zeros(729, np.int64)
    chosen = []
    bins = np.minimum((source[y, x]*8).astype(np.int64), 8)
    bins = (bins[:, 2]*9+bins[:, 1])*9+bins[:, 0]
    for i in order:
        if counts[bins[i]] >= 96:
            continue
        chosen.append(i); counts[bins[i]] += 1
        if len(chosen) >= limit:
            break
    return source[y[chosen], x[chosen]], target[y[chosen], x[chosen]], {
        'eligible_pixels': len(x), 'sampled_pixels': len(chosen),
        'occupied_source_bins_9cube': int(np.count_nonzero(counts)),
        'sampling': 'smooth, unclipped source; deterministic color-bin cap 96'}


def fit(inputs, targets, prior, size, ridge, smooth):
    indices, weights = vertices(inputs, size)
    count = size**3
    gram = np.eye(count)*ridge
    right = np.zeros((count, 3))
    residual = targets-apply(prior, inputs)
    for a in range(8):
        np.add.at(right, indices[:, a], weights[:, a, None]*residual)
        for b in range(8):
            np.add.at(gram, (indices[:, a], indices[:, b]), weights[:, a]*weights[:, b])
    for index in range(count):
        r, g, b = index % size, (index//size) % size, index//(size*size)
        for coordinate, step in ((r, 1), (g, size), (b, size*size)):
            if coordinate+1 < size:
                other = index+step
                gram[index, index] += smooth; gram[other, other] += smooth
                gram[index, other] -= smooth; gram[other, index] -= smooth
    unconstrained = prior+np.linalg.solve(gram, right)
    table = np.clip(unconstrained, 0, 1)
    support = np.bincount(indices.ravel(), weights=weights.ravel(), minlength=count)
    return table, {'nodes': count, 'nodes_with_weight_below_one': int((support < 1).sum()),
                   'clipped_node_channels': int(((unconstrained < 0) | (unconstrained > 1)).sum()),
                   'ridge': ridge, 'neighbor_correction_smoothing': smooth}


def metrics(predicted, target):
    difference = (predicted-target)*255
    return {'mean_absolute_rgb_8bit': np.mean(np.abs(difference), axis=0).tolist(),
            'rmse_rgb_8bit': float(np.sqrt(np.mean(difference**2))),
            'p95_absolute_channel_error_8bit': float(np.quantile(np.abs(difference), .95))}


def save_preview(path, source, target, prior, fitted, *, heldout=False):
    shape = source.shape
    pixels = source.reshape(-1, 3)/255.
    images = [source, np.uint8(np.clip(apply(prior, pixels)*255+.5, 0, 255)).reshape(shape),
              np.uint8(np.clip(apply(fitted, pixels)*255+.5, 0, 255)).reshape(shape), target]
    width, height = shape[1], shape[0]
    canvas = Image.new('RGB', (width*2, (height+30)*2), '#202020')
    draw = ImageDraw.Draw(canvas)
    for i, (image, label) in enumerate(zip(images, ['Camera JPEG / sRGB', 'Creative slice prior',
            'Scene holdout candidate' if heldout else 'Fitted sRGB candidate / training scenes',
            'Full RAW + DCP reference'])):
        x, y = (i % 2)*width, (i//2)*(height+30)
        canvas.paste(Image.fromarray(image), (x, y+30)); draw.text((x+8, y+8), label, fill='white')
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
    canvas.save(path, quality=93, subsampling=0, icc_profile=icc)


def run(manifest_path, destination, *, size=9, max_edge=1000, limit=10000, ridge=2., smooth=4.):
    manifest = json.loads(Path(manifest_path).read_text(encoding='utf-8'))
    scenes = manifest['scenes']; styles = manifest['styles']
    if not styles or any(not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', key)
                         for key in [*styles, *(s['id'] for s in scenes)]):
        raise ValueError('Use lowercase alphanumeric/hyphen style and scene IDs, at most 64 characters')
    if len(scenes) < 2 or len({s['id'] for s in scenes}) != len(scenes):
        raise ValueError('At least two distinct scene IDs are required')
    if len({Path(s['raw']).resolve() for s in scenes}) != len(scenes):
        raise ValueError('Scene holdouts must use distinct RAW files')
    if size not in (5, 9) or not 320 <= max_edge <= 1600 or not 1000 <= limit <= 30000:
        raise ValueError('Supported grid 5/9, max-edge 320..1600, samples 1000..30000')
    if not np.isfinite([ridge, smooth]).all() or ridge <= 0 or smooth < 0:
        raise ValueError('Ridge must be positive and smoothing nonnegative')
    destination = Path(destination)
    sources = {Path(manifest_path).resolve(),
               *(Path(s['raw']).resolve() for s in scenes),
               *(Path(s['targets'][key]).resolve() for s in scenes for key in styles),
               *(Path(config['prior_cube']).resolve() for config in styles.values())}
    outputs = [destination/'report.json']
    for style in styles:
        outputs.extend(destination/(style+suffix) for suffix in ('.cube', '.d4lut'))
        outputs.extend(destination/(style+'-'+s['id']+suffix) for s in scenes
                       for suffix in ('.jpg', '-heldout.jpg'))
    if any(path.resolve() in sources for path in outputs):
        raise ValueError('Outputs must not overwrite the manifest, RAW, target images or prior LUTs')
    destination.mkdir(parents=True, exist_ok=True)
    data = []; report = {'scope': 'empirical camera-JPEG sRGB to full RAW+DCP rendering approximation',
        'manifest': str(Path(manifest_path).resolve()), 'camera_isp_verified': False,
        'active_library_updated': False, 'source_files_modified': False,
        'limitations': ['Only supplied scenes and camera settings are measured.',
            'A global LUT cannot recover clipped source channels, spatial processing or scene-adaptive rendering.',
            'Targets are RawTherapee DCP renders, not Lightroom or physical Ricoh measurements.',
            'sRGB correspondence does not identify the native ISP LUT input space.'],
        'scenes': [], 'styles': {}}
    for scene in scenes:
        source, scope, color = srgb_image(scene['raw'], max_edge)
        source = np.asarray(source)
        target_images = {}; matrix = None; geometry = None
        entry = {'id': scene['id'], 'raw': scene['raw'], 'source_scope': scope,
                 'source_color': color, 'targets': {}}
        for style in styles:
            target, _, target_color = srgb_image(scene['targets'][style], max_edge)
            target = np.asarray(target)
            if matrix is None:
                aligned, valid, geometry, matrix = align(source, target)
                target_shape = target.shape
            else:
                if target.shape != target_shape:
                    raise ValueError('Same-scene targets must have identical geometry')
                aligned = cv2.warpAffine(target, matrix, (source.shape[1], source.shape[0]))
            inputs, outputs, stats = samples(source, aligned, valid, limit)
            target_images[style] = (inputs, outputs, aligned)
            entry['targets'][style] = {'path': scene['targets'][style], 'color': target_color, **stats}
        entry['alignment'] = geometry; report['scenes'].append(entry)
        data.append((source, target_images))
    for style, config in styles.items():
        prior = prior_table(Path(config['prior_cube']), size)
        evaluations = []
        for heldout in range(len(scenes)):
            train_x = np.concatenate([d[1][style][0] for i, d in enumerate(data) if i != heldout])
            train_y = np.concatenate([d[1][style][1] for i, d in enumerate(data) if i != heldout])
            candidate, _ = fit(train_x, train_y, prior, size, ridge, smooth)
            x, y, _ = data[heldout][1][style]
            before, after = metrics(apply(prior, x), y), metrics(apply(candidate, x), y)
            evaluations.append({'heldout_scene': scenes[heldout]['id'],
                'training_scenes': [s['id'] for i, s in enumerate(scenes) if i != heldout],
                'identity': metrics(x, y), 'creative_prior': before, 'fitted': after,
                'rmse_improved': after['rmse_rgb_8bit'] < before['rmse_rgb_8bit']})
            save_preview(destination/(style+'-'+scenes[heldout]['id']+'-heldout.jpg'),
                data[heldout][0], data[heldout][1][style][2], prior, candidate, heldout=True)
        all_x = np.concatenate([d[1][style][0] for d in data])
        all_y = np.concatenate([d[1][style][1] for d in data])
        candidate, fit_info = fit(all_x, all_y, prior, size, ridge, smooth)
        output = destination/(style+'.cube')
        with output.open('w', encoding='utf-8', newline='\n') as stream:
            stream.write('# Empirical NX300M JPEG sRGB -> RAW DCP rendering approximation\n')
            stream.write('# Offline candidate; not an Exact profile or validated camera ISP transform\n')
            stream.write(f'LUT_3D_SIZE {size}\n')
            for rgb in candidate:
                stream.write(' '.join(f'{v:.9f}' for v in rgb)+'\n')
        table_bytes = compile_cube(load_cube(output))
        (destination/(style+'.d4lut')).write_bytes(table_bytes)
        for scene, (source, targets) in zip(scenes, data):
            save_preview(destination/(style+'-'+scene['id']+'.jpg'), source,
                         targets[style][2], prior, candidate)
        report['styles'][style] = {'cube': str(output), 'grid': size, 'native_bytes': len(table_bytes),
            'fit': fit_info, 'scene_holdouts': evaluations,
            'every_scene_holdout_improved': all(e['rmse_improved'] for e in evaluations),
            'all_scene_fit': metrics(apply(candidate, all_x), all_y)}
    (destination/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2,
        allow_nan=False)+'\n', encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('output_dir', type=Path)
    args = parser.parse_args()
    report = run(args.manifest, args.output_dir)
    print(json.dumps({'scenes': report['scenes'], 'styles': report['styles']},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
