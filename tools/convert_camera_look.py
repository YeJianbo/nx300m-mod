"""Explicit DCP conversion APIs for RAW rendering and post-calibration RGB.

Rendered camera JPEGs belong to the empirical fit API, not the RAW style API.
The RAW renderer uses the actual NX300 camera DCP, including its calibration.
"""
import os
from pathlib import Path
import tempfile

import numpy as np

from adobe_profiles import (DcpProfile, DcpStyleStage, SRGB_TO_PROPHOTO_D50,
                            PROPHOTO_TO_SRGB_D50)

INPUT_STAGE = 'post-camera-calibration-and-huesatmap; pre-profile-style'
PROJECT = Path(__file__).resolve().parents[1]


def selected_profile(style):
    """Resolve only the user's selected Exact profiles or explicit blue variants."""
    styles = {
        'ricoh-positive-exact': ('Positive', False),
        'ricoh-negative-exact': ('Negative', False),
        'ricoh-positive-blue-adjusted': ('Positive', True),
        'ricoh-negative-blue-adjusted': ('Negative', True),
    }
    if style not in styles:
        raise ValueError('Unknown full DCP style: '+style)
    film, adjusted = styles[style]
    name = f'NX300 GR IV {film} Film'
    root = (PROJECT/'outputs/looks/ricoh-blue-candidate' if adjusted else
            Path(os.environ['APPDATA'])/'Adobe/CameraRaw/CameraProfiles/Imported/NX300_DCP_Camera_Profiles_v1')
    path = root/(name+(' Blue Adjusted' if adjusted else '')+'.dcp')
    profile = DcpProfile(path)
    expected = name+(' Blue Adjusted' if adjusted else ' Exact')
    if profile.get(50936) != expected or profile.get(50708) != 'Samsung NX300':
        raise ValueError('Selected DCP identity differs from the requested NX300 profile')
    return path


def _look(rgb, look):
    maximum = np.max(rgb, axis=-1)
    minimum = np.min(rgb, axis=-1)
    delta = maximum-minimum
    safe = np.where(delta == 0, 1., delta)
    r, g, b = np.moveaxis(rgb, -1, 0)
    hue = np.where(maximum == r, (g-b)/safe,
                   np.where(maximum == g, (b-r)/safe+2., (r-g)/safe+4.))/6.
    hue = np.where(delta == 0, 0., hue % 1.)
    saturation = delta/np.where(maximum == 0, 1., maximum)
    encoded = (np.where(maximum <= .0031308, maximum*12.92,
                        1.055*maximum**(1/2.4)-.055)
               if look.encoding == 1 and look.v > 1 else maximum)
    hc, sc, vc = hue*look.h, saturation*(look.s-1), encoded*(look.v-1)
    hi = np.minimum(hc.astype(int), look.h-1)
    si = np.minimum(sc.astype(int), look.s-2)
    vi = np.minimum(vc.astype(int), max(0, look.v-2))
    hf, sf, vf = hc-hi, sc-si, vc-vi
    table = np.asarray(look.values).reshape(look.v, look.h, look.s, 3)
    adjustment = np.zeros_like(rgb)
    for dv in range(2 if look.v > 1 else 1):
        vw = (vf if dv else 1-vf) if look.v > 1 else 1.
        for dh in (0, 1):
            for ds in (0, 1):
                weight = vw*(hf if dh else 1-hf)*(sf if ds else 1-sf)
                adjustment += table[vi+dv, (hi+dh) % look.h, si+ds]*np.asarray(weight)[..., None]
    h = (hue+adjustment[..., 0]/360.) % 1.
    s = np.clip(saturation*adjustment[..., 1], 0., 1.)
    v = np.clip(encoded*adjustment[..., 2], 0., 1.)
    if look.encoding == 1 and look.v > 1:
        v = np.where(v <= .040449936, v/12.92, ((v+.055)/1.055)**2.4)
    sector = np.floor(h*6).astype(int)
    fraction = h*6-sector
    p, q, t = v*(1-s), v*(1-s*fraction), v*(1-s*(1-fraction))
    colors = np.stack((np.stack((v,t,p), -1), np.stack((q,v,p), -1),
                       np.stack((p,v,t), -1), np.stack((p,q,v), -1),
                       np.stack((t,p,v), -1), np.stack((v,p,q), -1)), axis=-2)
    return np.take_along_axis(colors, (sector % 6)[..., None, None], axis=-2)[..., 0, :]


def _tone(values, stage):
    if stage.exposure_ev < 0:
        a = (16./9.)*(1-stage.exposure_gain)
        b = stage.exposure_gain-.5*a
        values = np.where(values <= .25, values*stage.exposure_gain,
                          (a*values+b)*values+(1-a-b))
    values = np.clip(values, 0, 1)
    if stage.tone is None:
        return values
    x, y, slope = (np.asarray(v) for v in (stage.tone.x, stage.tone.y, stage.tone.slopes))
    i = np.clip(np.searchsorted(x, values, side='left'), 1, len(x)-1)
    width = x[i]-x[i-1]
    right = (values-x[i-1])/width
    left = (x[i]-values)/width
    result = ((y[i-1]*(2-left+right)+slope[i-1]*width*right)*left*left
              +(y[i]*(2-right+left)-slope[i]*width*left)*right*right)
    return np.where(values <= x[0], y[0], np.where(values >= x[-1], y[-1], result))


def transform_linear(rgb, profile_path, *, input_space, input_stage, output_space='srgb'):
    """Apply the complete DCP style to calibrated, unrendered float RGB.

    Camera matrices, WB and illuminant HueSatMap must already be applied.
    Explicit stage declaration prevents accidental double toning of JPEGs.
    """
    if input_stage != INPUT_STAGE:
        raise ValueError('Input must declare the calibrated pre-style stage: '+INPUT_STAGE)
    if input_space not in ('linear-prophoto-d50', 'linear-srgb'):
        raise ValueError('DCP style needs unrendered linear RGB; use fit_camera_look for JPEG')
    if output_space not in ('linear-prophoto-d50', 'linear-srgb', 'srgb'):
        raise ValueError('Unsupported output color space')
    rgb = np.asarray(rgb, dtype=np.float64)
    if rgb.ndim < 1 or rgb.shape[-1] != 3 or not np.isfinite(rgb).all() or np.any((rgb < 0) | (rgb > 1)):
        raise ValueError('Expected finite float RGB in [0,1]')
    stage = DcpStyleStage(DcpProfile(profile_path))
    result = np.empty_like(rgb)
    source, destination = rgb.reshape(-1, 3), result.reshape(-1, 3)
    # LUT interpolation has several temporary planes; bound them independently
    # of the camera's 20-megapixel frame size.
    for start in range(0, len(source), 65536):
        destination[start:start+65536] = _transform_chunk(
            source[start:start+65536], stage, input_space, output_space)
    return result


def _transform_chunk(rgb, stage, input_space, output_space):
    if input_space == 'linear-srgb':
        rgb = rgb@np.asarray(SRGB_TO_PROPHOTO_D50).T
    rgb = np.clip(rgb, 0, 1)
    if stage.exposure_ev > 0:
        rgb = np.minimum(rgb*stage.exposure_gain, 1.)
    if stage.look is not None:
        rgb = _look(rgb, stage.look)
    low, high = rgb.min(axis=-1, keepdims=True), rgb.max(axis=-1, keepdims=True)
    lower, upper = _tone(low, stage), _tone(high, stage)
    rgb = lower+(upper-lower)*(rgb-low)/np.where(high == low, 1., high-low)
    if output_space != 'linear-prophoto-d50':
        rgb = np.clip(rgb@np.asarray(PROPHOTO_TO_SRGB_D50).T, 0, 1)
        if output_space == 'srgb':
            rgb = np.where(rgb <= .0031308, rgb*12.92, 1.055*rgb**(1/2.4)-.055)
    return rgb


def render_raw(raw, profile, output):
    """Develop a read-only SRW through the full DCP using isolated RawTherapee.

    Output is an ICC-tagged sRGB PNG; raw input and user settings remain intact.
    Requires the installed WSL Debian RawTherapee CLI, never Lightroom UI.
    """
    from render_ricoh_reference import wsl, linux_path, pp3, check_effective, inspect_image
    raw, profile, output = (Path(p).resolve() for p in (raw, profile, output))
    if not raw.is_file() or raw.suffix.lower() not in ('.srw', '.dng'):
        raise ValueError('Expected an existing original SRW or measured Bayer DNG file')
    if output.suffix.lower() != '.png' or output.exists():
        raise ValueError('Output must be a new .png path')
    parsed = DcpProfile(profile)
    if parsed.get(50708) != 'Samsung NX300':
        raise ValueError('RAW camera calibration must be the NX300 DCP')
    from photo_pairs import srw_metadata
    if srw_metadata(raw).get('model') not in ('NX300', 'NX300M'):
        raise ValueError('RAW is not a Samsung NX300/NX300M exposure')
    output.parent.mkdir(parents=True, exist_ok=True)
    # A fresh directory isolates defaults and sidecars and keeps partial output
    # away from the requested destination until rendering and ICC checks pass.
    with tempfile.TemporaryDirectory(prefix='dcp-render-', dir=output.parent) as folder:
        work = Path(folder)
        for name in ('config', 'cache'):
            (work/name).mkdir()
        settings, rendered = work/'render.pp3', work/'render.png'
        settings.write_text(pp3(profile), encoding='utf-8')
        result = wsl('env', 'XDG_CONFIG_HOME='+linux_path(work/'config'),
            'XDG_CACHE_HOME='+linux_path(work/'cache'), 'OMP_NUM_THREADS=4', 'LC_ALL=C.UTF-8',
            'rawtherapee-cli', '-q', '-Y', '-n', '-b8', '-O', linux_path(rendered),
            '-p', linux_path(settings), '-c', linux_path(raw), capture_output=True)
        effective_path = next((p for p in (Path(str(rendered)+'.out.pp3'), Path(str(rendered)+'.pp3')) if p.exists()), None)
        if effective_path is None:
            raise RuntimeError('Renderer did not retain effective DCP settings: '+result.stdout[-2000:])
        effective = check_effective(effective_path, profile)
        image, _ = inspect_image(rendered)
        report = {'raw': str(raw), 'profile': parsed.metadata(), 'output': str(output),
                  'input_stage': 'original-camera-raw', 'output_space': 'srgb',
                  'renderer': 'RawTherapee 5.11 full DCP pipeline',
                  'effective': effective, 'image': {**image, 'path': str(output)},
                  'scope': 'full RAW DCP software rendering; not Samsung ISP or Lightroom equivalence'}
        rendered.replace(output)
        return report
