"""Small constrained least-squares solver for the existing IPCM proxy ABI.

The solver models the same CCM/shared-tone/chroma operations as the runtime
profile. This does not calibrate the native ISP colour coordinates.
"""
import numpy as np

from fit_ipcm_preview import (RGB_TO_YCC, YCC_TO_RGB, NATIVE_RGB_TO_YCC,
                              NATIVE_YCC_TO_RGB, quantize_model, round_away)

KNOTS = 33

# OKLab acts on linear sRGB here. It describes the offline proxy only.
_LMS = np.array([[.4122214708, .5363325363, .0514459929],
                 [.2119034982, .6806995451, .1073969566],
                 [.0883024619, .2817188376, .6299787005]])
_LAB = np.array([[.2104542553, .7936177850, -.0040720468],
                 [1.9779984951, -2.4285922050, .4505937099],
                 [.0259040371, .7827717662, -.8086757660]])


def perceptual_features(rgb, jacobian=False):
    """RGB anchor plus luminance/chroma error, without hue-specific weights.

    The tiny LMS floor bounds the derivative at black. All LUTs use the same
    feature scales; the reference is always the source, including its casts.
    """
    rgb = np.asarray(rgb)
    linear = np.where(rgb <= .04045, rgb/12.92, ((rgb+.055)/1.055)**2.4)
    lms = linear@_LMS.T
    root = np.cbrt(np.maximum(lms, 1e-9))
    lab = (root@_LAB.T)*np.array([1., 2., 2.])
    result = np.concatenate([.5*rgb, lab], axis=1)
    if not jacobian:
        return result
    slope = np.where(rgb <= .04045, 1/12.92, 2.4/1.055*((rgb+.055)/1.055)**1.4)
    root_slope = np.where(lms > 1e-9, 1/(3*root**2), 0.)
    derivative = np.einsum('ci,ni,ij,nj->ncj', _LAB, root_slope, _LMS, slope)
    derivative *= np.array([1., 2., 2.])[None, :, None]
    return result, np.concatenate([np.broadcast_to(.5*np.eye(3), (len(rgb),3,3)), derivative], axis=1)


def tonal_features(rgb, source, jacobian=False):
    """Keep the source LUT's signed cast on low-chroma input surfaces.

    Opponent differences retain direction (a cold target is not a neutral
    target). The gate depends only on the input, so colour changes cannot
    reduce their own weight while fitting. All looks use the same scale.
    """
    opponents = np.array([[1., -1., 0.], [0., -1., 1.]])
    scale = 4*np.exp(-.5*(np.ptp(source, axis=1)/.06)**2)
    extra = (rgb@opponents.T)*scale[:, None]
    if not jacobian:
        return np.concatenate([perceptual_features(rgb), extra], axis=1)
    features, derivative = perceptual_features(rgb, True)
    return (np.concatenate([features, extra], axis=1),
            np.concatenate([derivative, scale[:, None, None]*opponents], axis=1))


def isotonic(values):
    """Euclidean projection onto nondecreasing knots, using pooled blocks."""
    means, sizes = [], []
    for value in np.clip(values, 0, 1):
        means.append(float(value))
        sizes.append(1)
        while len(means) > 1 and means[-2] > means[-1]:
            n = sizes[-2]+sizes[-1]
            mean = (means[-2]*sizes[-2]+means[-1]*sizes[-1])/n
            means[-2:] = [mean]
            sizes[-2:] = [n]
    return np.repeat(means, sizes)


def unpack(parameters):
    n, knots = parameter_shape(parameters)
    return parameters[:9].reshape(3, 3), parameters[9:9+n*n].reshape(n, n), parameters[9+n*n:]


def parameter_shape(parameters):
    for knots in (33, 65, 129, 256):
        for n in (2, 3):
            if len(parameters) == 9+n*n+knots:
                return n, knots
    raise ValueError('Unsupported preview parameter shape')


def project(parameters):
    p = parameters.copy()
    _, knots = parameter_shape(p)
    offset = len(p)-knots
    p[:offset] = np.clip(p[:offset], -4, 4)
    p[offset:] = isotonic(p[offset:])
    return p


def from_model(model, full_post=False, knots=KNOTS):
    if 'ycc_q12' in model:
        post = np.asarray(model['ycc_q12'])/4096
    elif full_post:
        post = np.eye(3)
        post[1:, 1:] = np.asarray(model['chroma_q12']).reshape(2, 2)/4096
        change = NATIVE_RGB_TO_YCC@YCC_TO_RGB
        post = change@post@np.linalg.inv(change)
        post = post.ravel()
    else:
        post = np.asarray(model['chroma_q12'])/4096
    return np.concatenate([np.asarray(model['ccm_q12'])/4096, post,
                           np.interp(np.linspace(0, 255, knots), np.arange(256), model['tone'])/255])


def to_model(parameters):
    a, b, tone = unpack(parameters)
    model = quantize_model(a, np.interp(np.arange(256)/255, np.linspace(0, 1, len(tone)), tone), np.eye(2) if len(b) == 3 else b)
    if len(b) == 3:
        del model['chroma_q12']
        model['ycc_q12'] = round_away(b.ravel()*4096).astype(int).tolist()
    return model


def forward(x, parameters, amount=1., jacobian=False, color_model=None):
    a, b, tone = unpack(parameters)
    knots = len(tone)
    a = np.eye(3)+amount*(a-np.eye(3))
    n = len(b)
    b = np.eye(n)+amount*(b-np.eye(n))
    tone = np.linspace(0, 1, knots)+amount*(tone-np.linspace(0, 1, knots))
    linear = color_model.decode(x) if color_model is not None else x
    raw = linear@a.T
    if color_model is None:
        encoded = np.clip(raw, 0, 1)
        stock_slope = ((raw >= 0) & (raw <= 1))
    else:
        encoded, stock_slope = color_model.encode(raw, True)
    u = encoded*(knots-1)
    index = np.minimum(u.astype(int), knots-2)
    fraction = u-index
    toned = tone[index]*(1-fraction)+tone[index+1]*fraction
    rgb_to_ycc = (color_model.stock_ycc/256 if color_model is not None else NATIVE_RGB_TO_YCC) if n == 3 else RGB_TO_YCC
    ycc_to_rgb = (color_model.ycc_inverse if color_model is not None else NATIVE_YCC_TO_RGB) if n == 3 else YCC_TO_RGB
    ycc = toned@rgb_to_ycc.T
    block = b if n == 3 else np.eye(3)
    if n == 2:
        block[1:, 1:] = b
    h = ycc_to_rgb@block@rgb_to_ycc
    output_raw = toned@h.T
    output = np.clip(output_raw, 0, 1)
    if not jacobian:
        return output
    slope = (tone[index+1]-tone[index])*(knots-1)*stock_slope
    ja = np.einsum('ci,ni,nj->ncij', h, slope, linear).reshape(len(x), 3, 9)*amount
    begin = 0 if n == 3 else 1
    jb = np.einsum('ci,nj->ncij', ycc_to_rgb[:, begin:], ycc[:, begin:]).reshape(len(x), 3, n*n)*amount
    jt = np.zeros((len(x), 3, knots))
    rows = np.arange(len(x))
    for i in range(3):
        for c in range(3):
            jt[rows, c, index[:, i]] += h[c, i]*(1-fraction[:, i])*amount
            jt[rows, c, index[:, i]+1] += h[c, i]*fraction[:, i]*amount
    jac = np.concatenate([ja, jb, jt], axis=2)
    jac *= ((output_raw >= 0) & (output_raw <= 1))[:, :, None]
    return output, jac


def solve(x, target, weights, initial, iterations=100, strengths=(1.,), smoothness=.002,
          objective='rgb', color_model=None, trainable='joint', matrix_ridge=0.):
    """Projected damped Gauss-Newton, with an objective-checked line search."""
    p = project(initial)
    _, knots = parameter_shape(p)
    weights = np.asarray(weights)/np.sum(weights)
    if objective not in ('rgb', 'perceptual', 'tonal', 'rgb-mae'):
        raise ValueError('Unknown fitting objective')
    dimensions = 8 if objective == 'tonal' else 6 if objective == 'perceptual' else 3
    roots = np.sqrt(weights/(dimensions*len(strengths)))[:, None]
    targets = {s: x+s*(target-x) for s in strengths}
    if objective == 'perceptual':
        targets = {s: perceptual_features(t) for s, t in targets.items()}
    elif objective == 'tonal':
        targets = {s: tonal_features(t, x) for s, t in targets.items()}

    def features(output):
        if objective == 'tonal':
            return tonal_features(output, x)
        return perceptual_features(output) if objective == 'perceptual' else output

    def squared_loss(residual):
        if objective == 'rgb-mae':
            # Smooth L1 limits the leverage of a few unrepresentable colours.
            return 2*.01*(np.sqrt(residual**2+.01**2)-.01)
        return residual**2
    curvature = np.diff(np.eye(knots), 2, axis=0)
    reg = np.zeros((knots-2, len(p)))
    reg[:, -knots:] = np.sqrt(smoothness*((knots-1)/32)**3)*curvature
    reg_target = np.zeros(len(reg))
    if matrix_ridge:
        count = len(p)-knots
        block = np.zeros((count,len(p)))
        block[:,:count] = np.eye(count)*np.sqrt(matrix_ridge)
        n, _ = parameter_shape(p)
        reg_target = np.concatenate([reg_target, np.concatenate([np.eye(3).ravel(),np.eye(n).ravel()])*np.sqrt(matrix_ridge)])
        reg = np.concatenate([reg,block])
    if trainable not in ('joint','tone','matrices'):
        raise ValueError('Unknown fitting stage')
    mask = np.ones(len(p),bool)
    if trainable == 'tone': mask[:-knots] = False
    if trainable == 'matrices': mask[-knots:] = False
    damping = .001
    trajectory = []

    def loss(parameters):
        total = 0.
        for strength in strengths:
            residual = features(forward(x, parameters, strength, color_model=color_model))-targets[strength]
            total += np.sum(squared_loss(residual)*roots**2)
        return total+np.sum((reg@parameters-reg_target)**2)

    current = loss(p)
    from fit_ipcm_preview import apply_profile

    def quantized_loss(model):
        apply = apply_profile if color_model is None else color_model.apply
        return sum(np.sum(squared_loss(features(apply(x, model, strength))-targets[strength])*roots**2)
                   for strength in strengths)

    best_model = to_model(p)
    best_quantized_loss = quantized_loss(best_model)
    best_float = p.copy()
    for step in range(iterations):
        hessian = reg.T@reg
        gradient = reg.T@(reg@p-reg_target)
        for strength in strengths:
            output, jac = forward(x, p, strength, True, color_model=color_model)
            if objective == 'perceptual':
                output, colour_jac = perceptual_features(output, True)
                jac = colour_jac@jac
            elif objective == 'tonal':
                output, colour_jac = tonal_features(output, x, True)
                jac = colour_jac@jac
            difference = output-targets[strength]
            scaling = roots
            if objective == 'rgb-mae':
                scaling = roots*np.sqrt(.01/np.sqrt(difference**2+.01**2))
            residual = difference*scaling
            jac = (jac*scaling[:, :, None]).reshape(-1, len(p))
            hessian += jac.T@jac
            gradient += jac.T@residual.ravel()
        if np.max(np.abs(gradient[mask])) < 1e-9:
            break
        accepted = False
        for _ in range(8):
            active_hessian = hessian[np.ix_(mask,mask)]
            delta = np.zeros(len(p))
            delta[mask] = np.linalg.solve(active_hessian+np.diag(damping*np.maximum(np.diag(active_hessian), 1e-5)), -gradient[mask])
            for fraction in (1., .5, .25, .125):
                proposed = project(p+delta*fraction)
                candidate = loss(proposed)
                if candidate < current-1e-12:
                    p, current, accepted = proposed, candidate, True
                    break
            if accepted:
                damping = max(damping/3, 1e-8)
                break
            damping *= 10
        trajectory.append({'iteration': step+1, 'loss': float(current), 'accepted': accepted})
        if accepted and ((step+1) % 5 == 0 or step == iterations-1):
            model = to_model(p)
            qloss = quantized_loss(model)
            if qloss < best_quantized_loss:
                best_quantized_loss, best_model, best_float = qloss, model, p.copy()
        if not accepted:
            break
    # A line search may stop between checkpoint iterations. Include its final
    # quantised payload instead of accidentally discarding those accepted steps.
    model = to_model(p)
    qloss = quantized_loss(model)
    if qloss < best_quantized_loss:
        best_quantized_loss, best_model, best_float = qloss, model, p.copy()
    return best_model, {'optimizer': 'numpy projected damped Gauss-Newton; isotonic tone projection',
                         'iterations': len(trajectory), 'strengths': list(strengths), 'objective': objective, 'tone_knots': knots,
                         'smoothness': smoothness, 'trajectory': trajectory, 'float_loss': float(current),
                         'stage': trainable, 'matrix_ridge': matrix_ridge,
                         'quantized_training_mse': float(best_quantized_loss), 'float_parameters': best_float.tolist()}
