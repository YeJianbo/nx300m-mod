"""Offline bounded least-squares reduction to the existing D4 9^3/10-bit ABI.

The pixel model is explicitly RGB-code trilinear interpolation, not a measured
NX300M ISP model. No camera runtime or live-view module imports this file.
"""
from functools import lru_cache
import itertools
import struct

import numpy as np

GRID = 9
NODES = GRID**3
BITS = 1023
LMS = np.array([[.4122214708, .5363325363, .0514459929],
                [.2119034982, .6806995451, .1073969566],
                [.0883024619, .2817188376, .6299787005]])
LAB = np.array([[.2104542553, .7936177850, -.0040720468],
                [1.9779984951, -2.4285922050, .4505937099],
                [.0259040371, .7827717662, -.8086757660]])


def oklab(rgb):
    """sRGB-code reference metric; this is not a native ISP colour model."""
    rgb = np.clip(np.asarray(rgb), 0, 1)
    linear = np.where(rgb <= .04045, rgb/12.92, ((rgb+.055)/1.055)**2.4)
    return np.cbrt(linear@LMS.T)@LAB.T


def grid(size=GRID):
    return np.stack(np.meshgrid(*([np.linspace(0, 1, size)]*3), indexing='ij'), -1).reshape(-1, 3)


def sample_cube(cube, rgb, *, interpolation='trilinear', clip=True):
    """Standard .cube R-fastest order; handle DOMAIN in the source coordinate."""
    size, low, high, values = cube
    rgb = np.asarray(rgb, dtype=np.float64)
    shape = rgb.shape
    coord = np.clip((rgb.reshape(-1, 3)-low)/(np.asarray(high)-low), 0, 1)*(size-1)
    base = np.minimum(np.floor(coord).astype(np.int64), size-2)
    frac = coord-base
    table = np.asarray(values, dtype=np.float64)
    if interpolation == 'trilinear':
        out = np.zeros_like(coord)
        for corner in itertools.product((0, 1), repeat=3):
            p = base+corner
            weight = np.prod(np.where(corner, frac, 1-frac), axis=1)
            out += table[(p[:, 2]*size+p[:, 1])*size+p[:, 0]]*weight[:, None]
    elif interpolation == 'tetrahedral':
        axes = np.argsort(-frac, axis=1, kind='stable')
        sorted_f = np.take_along_axis(frac, axes, 1)
        weights = np.column_stack((1-sorted_f[:, 0], sorted_f[:, 0]-sorted_f[:, 1],
                                   sorted_f[:, 1]-sorted_f[:, 2], sorted_f[:, 2]))
        p = base.copy()
        out = np.zeros_like(coord)
        for i in range(4):
            if i:
                p[np.arange(len(p)), axes[:, i-1]] += 1
            out += table[(p[:, 2]*size+p[:, 1])*size+p[:, 0]]*weights[:, i, None]
    else:
        raise ValueError('Unsupported interpolation')
    return (np.clip(out, 0, 1) if clip else out).reshape(shape)


def unpack(data):
    if len(data) != 4380:
        raise ValueError('D4 data must contain 2190 uint16 words')
    words = np.array(struct.unpack('<2190H', data), dtype=np.float64).reshape(3, 730)
    if np.any(words > BITS) or np.any(words[:, -1]):
        raise ValueError('Invalid D4 word or padding')
    return words[:, :NODES].T/BITS


def pack(values):
    q = np.floor(np.clip(values, 0, 1)*BITS+.5).astype(np.uint16)
    return b''.join(struct.pack('<730H', *q[:, c].tolist(), 0) for c in range(3))


def as_cube(values):
    # D4 is R outer / B inner. Standard cube is B outer / R inner.
    values = np.asarray(values).reshape(GRID, GRID, GRID, 3).transpose(2, 1, 0, 3).reshape(-1, 3)
    return GRID, (0.,)*3, (1.,)*3, values


def sample_table(data, rgb, *, interpolation='trilinear'):
    return sample_cube(as_cube(unpack(data)), rgb, interpolation=interpolation)


def target(cube, rgb, stock=None):
    if stock is not None:
        rgb = sample_cube(as_cube(stock), rgb)
    return sample_cube(cube, rgb)


def basis(rgb):
    x = np.clip(np.asarray(rgb), 0, 1)*(GRID-1)
    lo = np.minimum(np.floor(x).astype(np.int64), GRID-2)
    frac = x-lo
    indices, weights = [], []
    for corner in itertools.product((0, 1), repeat=3):
        p = lo+corner
        indices.append((p[:, 0]*GRID+p[:, 1])*GRID+p[:, 2])
        weights.append(np.prod(np.where(corner, frac, 1-frac), axis=1))
    return np.array(indices).T, np.array(weights).T


@lru_cache(maxsize=1)
def training_system():
    rng = np.random.default_rng(61183)
    cells = np.stack(np.meshgrid(*([np.arange(8)]*3), indexing='ij'), -1).reshape(-1, 3)
    interior = (np.repeat(cells, 32, axis=0)+rng.random((len(cells)*32, 3)))/8
    # Gray targets retain the source LUT's deliberate tint, rather than forcing
    # an achromatic output. Faces constrain the entire boundary, not a photo.
    gray = np.repeat(np.linspace(0, 1, 513)[:, None], 3, axis=1)
    face = np.stack(np.meshgrid(np.linspace(0, 1, 25), np.linspace(0, 1, 25), indexing='ij'), -1).reshape(-1, 2)
    faces = []
    for axis in range(3):
        for edge in (0., 1.):
            p = np.empty((len(face), 3)); p[:, axis] = edge
            p[:, [c for c in range(3) if c != axis]] = face
            faces.append(p)
    x = np.concatenate([interior, gray, *faces, grid()])
    weight = np.concatenate([np.ones(len(interior)), np.full(len(gray), 6.),
                             np.full(sum(map(len, faces)), .4), np.full(NODES, .5)])
    weight /= weight.sum()
    idx, w = basis(x)
    h = np.zeros((NODES, NODES))
    for a in range(8):
        for b in range(8):
            np.add.at(h, (idx[:, a], idx[:, b]), weight*w[:, a]*w[:, b])
    # Regularize toward source samples, never toward identity or a weaker look.
    reg = np.eye(NODES)*1e-7
    lattice = np.arange(NODES).reshape(GRID, GRID, GRID)
    for axis in range(3):
        p = np.moveaxis(lattice, axis, 0)
        for triplet in np.stack((p[:-2], p[1:-1], p[2:]), -1).reshape(-1, 3):
            reg[np.ix_(triplet, triplet)] += np.outer([1., -2., 1.], [1., -2., 1.])*1e-8
    return x, weight, idx, w, h+reg, reg


def bounded_solve(h, rhs, initial=None):
    """Primal active-set box QP, independent output channels; no SciPy needed."""
    result, status = [], []
    count = len(h)
    for channel in range(rhs.shape[1]):
        b = rhs[:, channel]
        raw = np.linalg.solve(h, b) if initial is None else initial[:, channel]
        x = np.clip(raw, 0, 1)
        active = (x <= 0) | (x >= 1)
        converged = False
        for iteration in range(256):
            free = ~active
            opt = x.copy()
            if free.any():
                opt[free] = np.linalg.solve(h[np.ix_(free, free)], b[free]-h[np.ix_(free, active)]@x[active])
            outside = free & ((opt < 0) | (opt > 1))
            if outside.any():
                direction = opt-x
                ratios = np.full(count, np.inf)
                ratios[free & (direction < 0)] = -x[free & (direction < 0)]/direction[free & (direction < 0)]
                ratios[free & (direction > 0)] = (1-x[free & (direction > 0)])/direction[free & (direction > 0)]
                step = max(0., min(1., float(ratios.min())))
                x = np.clip(x+step*direction, 0, 1)
                hit = free & (ratios <= step+1e-12)
                x[hit] = (direction[hit] > 0).astype(float)
                active[hit] = True
                continue
            x = np.clip(opt, 0, 1)
            gradient = h@x-b
            violation = np.zeros(count)
            violation[active & (x <= 0)] = np.maximum(0, -gradient[active & (x <= 0)])
            violation[active & (x >= 1)] = np.maximum(0, gradient[active & (x >= 1)])
            if violation.max() < 1e-10:
                converged = True; break
            active[np.argmax(violation)] = False
        result.append(x)
        status.append({'converged': converged, 'iterations': iteration+1})
    return np.array(result).T, status


def refine_integer(values, h, rhs):
    """Coordinate minimization in integer node space, still the same full look."""
    q = np.floor(np.clip(values, 0, 1)*BITS+.5)
    gradient = h@q-rhs*BITS
    changes = 0
    for sweep in range(12):
        updates = 0
        for i in range(len(h)):
            new = np.floor(np.clip(q[i]-gradient[i]/h[i, i], 0, BITS)+.5)
            delta = new-q[i]
            if np.any(delta):
                q[i] = new; gradient += h[:, i, None]*delta
                updates += 1
        changes += updates
        if not updates:
            break
    return q/BITS, {'sweeps': sweep+1, 'updated_nodes': changes}


def perceptual_refine(values, initial, y, weight, idx, w, h, reg):
    """Bounded Gauss-Newton on RGB + OKLab loss, with full channel coupling.

    The RGB term anchors the complete source effect. Finite differences at one
    10-bit step avoid an infinite derivative at black. Every look uses the same
    objective and iteration budget; no photograph enters this solve.
    """
    def interpolate(v):
        return np.sum(v[idx]*w[:, :, None], axis=1)

    def objective(v):
        p = interpolate(v)
        residual = v-initial
        return float(np.sum(weight[:, None]*((p-y)**2+(oklab(p)-oklab(y))**2))
                     + np.sum(residual*(reg@residual)))

    # A source with identical output channels must stay achromatic. The
    # RGB-only solution already has this property, including integer rounding.
    if np.array_equal(y[:, 0], y[:, 1]) and np.array_equal(y[:, 1], y[:, 2]):
        return values, {'iterations': [], 'achromatic_mapping_preserved': True}
    rgb_right = reg@initial
    for a in range(8):
        np.add.at(rgb_right, idx[:, a], (weight*w[:, a])[:, None]*y)
    current = values.copy()
    history = []
    for iteration in range(3):
        pred = interpolate(current)
        jac = np.empty((len(y), 3, 3))
        for c in range(3):
            plus, minus = pred.copy(), pred.copy()
            plus[:, c] = np.minimum(1, plus[:, c]+1/BITS)
            minus[:, c] = np.maximum(0, minus[:, c]-1/BITS)
            jac[:, :, c] = (oklab(plus)-oklab(minus))/(plus[:, c]-minus[:, c])[:, None]
        metric = np.einsum('nki,nkj->nij', jac, jac)
        linear_target = oklab(y)-oklab(pred)+np.einsum('nij,nj->ni', jac, pred)
        right = rgb_right.copy()
        projected = np.einsum('nki,nk->ni', jac, linear_target)
        for a in range(8):
            np.add.at(right, idx[:, a], (weight*w[:, a])[:, None]*projected)
        coupled = np.zeros((NODES, NODES, 3, 3))
        for a in range(8):
            for b in range(8):
                np.add.at(coupled, (idx[:, a], idx[:, b]),
                          (weight*w[:, a]*w[:, b])[:, None, None]*metric)
        system = coupled.transpose(0, 2, 1, 3).reshape(NODES*3, NODES*3)
        system += np.kron(h, np.eye(3))
        solved, status = bounded_solve(system, right.reshape(-1, 1), current.reshape(-1, 1))
        if not status[0]['converged']:
            raise RuntimeError('Perceptual bounded solve did not converge')
        proposal = solved.reshape(-1, 3)
        previous = objective(current)
        step = 1.
        for _ in range(12):
            candidate = current+step*(proposal-current)
            if objective(candidate) <= previous:
                break
            step *= .5
        else:
            break
        current = candidate
        history.append({'iteration': iteration+1, 'step': step,
                        'objective_before': previous, 'objective_after': objective(current),
                        'solver': status[0]})
        if previous-objective(current) < 1e-12:
            break
    # Refine integer codes against the last local quadratic, then accept only
    # a non-increase in the actual nonlinear objective after quantisation.
    rounded = unpack(pack(current))
    quantized, integer = refine_integer(current.reshape(-1, 1), system, right.reshape(-1, 1))
    quantized = quantized.reshape(-1, 3)
    if objective(quantized) > objective(rounded):
        quantized = rounded
    if objective(quantized) > objective(values):
        quantized = values
    return quantized, {'iterations': history, 'integer_refinement': integer,
                       'objective': 'weighted RGB squared + OKLab squared + residual curvature'}


def preserve_monochrome_shape(values, cube, stock, y):
    """Keep a monotone achromatic source monotone on the interpolated gray axis.

    A diagonal through one trilinear cell is a cubic Bezier: its controls are
    the two diagonal nodes and the means of the two three-corner groups.
    Ordered controls are a sufficient monotonicity constraint. Weighted PAVA
    projects these controls; integer group sums preserve the constraint after
    10-bit quantisation. No style name or photograph is consulted.
    """
    if not (np.array_equal(y[:, 0], y[:, 1]) and np.array_equal(y[:, 1], y[:, 2])):
        return values, None
    gray = np.repeat(np.linspace(0, 1, 1025)[:, None], 3, axis=1)
    differences = np.diff(target(cube, gray, stock)[:, 0])
    if np.all(differences >= -1e-12):
        increasing = True
    elif np.all(differences <= 1e-12):
        increasing = False
    else:
        return values, None
    q = np.floor(values[:, 0]*BITS+.5).astype(int)
    if not increasing:
        q = BITS-q
    original = q.copy()
    groups = [[0]]
    for cell in range(8):
        base = (cell*GRID+cell)*GRID+cell
        for level in (1, 2):
            groups.append([base+c[0]*GRID*GRID+c[1]*GRID+c[2]
                           for c in itertools.product((0, 1), repeat=3) if sum(c) == level])
        groups.append([base+GRID*GRID+GRID+1])
    controls = np.array([q[g].mean() for g in groups])
    if np.all(np.diff(controls) >= 0):
        return values, {'monotone_achromatic': True, 'changed_nodes': 0}
    blocks = []
    for i, (value, group) in enumerate(zip(controls, groups)):
        blocks.append([len(group), value*len(group), i, i+1])
        while len(blocks) > 1 and blocks[-2][1]/blocks[-2][0] > blocks[-1][1]/blocks[-1][0]:
            right = blocks.pop(); left = blocks.pop()
            blocks.append([left[0]+right[0], left[1]+right[1], left[2], right[3]])
    for weight, total, begin, end in blocks:
        code = int(np.floor(total/weight+.5))
        for group in groups[begin:end]:
            current = q[group].copy(); prior = current.copy()
            delta = code*len(group)-int(current.sum())
            while delta:
                sign = 1 if delta > 0 else -1
                cost = 2*(current-prior)*sign+1
                cost[(current+sign < 0) | (current+sign > BITS)] = 1 << 30
                current[np.argmin(cost)] += sign
                delta -= sign
            q[group] = current
    detail = {'monotone_achromatic': True, 'changed_nodes': int(np.count_nonzero(q != original)),
              'max_delta_10bit_codes': int(np.max(np.abs(q-original)))}
    if not increasing:
        q = BITS-q
    return np.repeat((q/BITS)[:, None], 3, axis=1), detail


def compile_table(cube, stock_baseline=None, *, method='fit'):
    if method not in ('round', 'fit', 'fit-perceptual', 'fit-integral'):
        raise ValueError('Unknown LUT fit method: '+method)
    stock = None
    if stock_baseline is not None:
        words = np.asarray(stock_baseline, dtype=float)
        if words.shape != (2190,) or np.any(~np.isfinite(words)) or np.any((words < 0) | (words > BITS)):
            raise ValueError('Invalid stock baseline')
        if np.any(words.reshape(3, 730)[:, -1]):
            raise ValueError('Nonzero stock padding')
        stock = words.reshape(3, 730)[:, :NODES].T/BITS
    initial = target(cube, grid(), stock)
    detail = {'algorithm': method, 'model': 'RGB-code trilinear, native encoding/interpolation unverified',
              'composed': stock is not None, 'grid': GRID, 'bits': 10, 'bytes': 4380}
    if method == 'round':
        return pack(initial), detail
    x, weight, idx, w, h, reg = training_system()
    y = target(cube, x, stock)
    rhs = reg@initial
    for corner in range(8):
        np.add.at(rhs, idx[:, corner], (weight*w[:, corner])[:, None]*y)
    if method == 'fit-integral':
        from lut_integral import protected_terms
        h, rhs, detail['integration'] = protected_terms(cube, stock, y, weight, idx, w, h, rhs)
    fitted, status = bounded_solve(h, rhs)
    if not all(s['converged'] for s in status):
        raise RuntimeError('Bounded LUT fit did not converge')
    quantized, integer = refine_integer(fitted, h, rhs)
    if method == 'fit-perceptual':
        quantized, detail['perceptual_refinement'] = perceptual_refine(
            quantized, initial, y, weight, idx, w, h, reg)
    quantized, shape_detail = preserve_monochrome_shape(quantized, cube, stock, y)
    if shape_detail is not None:
        detail['shape_constraint'] = shape_detail
    detail.update(training_samples=len(x), solver=status, integer_refinement=integer,
                  training_rgb_rmse_initial_round=float(np.sqrt(np.mean((y-sample_cube(as_cube(unpack(pack(initial))), x))**2))*255),
                  training_rgb_rmse_after=float(np.sqrt(np.mean((y-sample_cube(as_cube(quantized), x))**2))*255))
    return pack(quantized), detail
