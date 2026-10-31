"""Read Adobe DCP profiles and evaluate their explicitly scoped style stages.

HSV table addressing and interpolation follow Adobe DNG SDK's GetDelta and
RefBaselineHueSatMap (source provenance in work/reference/dng-sdk). Camera
Color/Forward matrices and illuminant-dependent HueSatMaps are not transplanted.
"""
import colorsys
import bisect
import math
from pathlib import Path
import struct
import xml.etree.ElementTree as ET

TYPES = {1: ('B', 1), 2: ('s', 1), 3: ('H', 2), 4: ('I', 4), 5: ('II', 8), 7: ('B', 1), 9: ('i', 4), 10: ('ii', 8), 11: ('f', 4), 12: ('d', 8)}
NAMES = {50708: 'camera_model', 50721: 'color_matrix_1', 50722: 'color_matrix_2', 50778: 'illuminant_1', 50779: 'illuminant_2', 50936: 'profile_name', 50937: 'hue_sat_dims', 50938: 'hue_sat_data_1', 50939: 'hue_sat_data_2', 50940: 'tone_curve', 50964: 'forward_matrix_1', 50965: 'forward_matrix_2', 50981: 'look_table_dims', 50982: 'look_table_data', 51107: 'hue_sat_encoding', 51108: 'look_table_encoding', 51109: 'baseline_exposure_offset', 51110: 'default_black_render'}

class DcpProfile:
    def __init__(self, path):
        self.path = Path(path)
        self.data = self.path.read_bytes()
        self.endian = '<' if self.data[:2] == b'II' else '>' if self.data[:2] == b'MM' else ''
        if not self.endian or len(self.data) < 8: raise ValueError('Invalid DCP byte order/header')
        if struct.unpack_from(self.endian+'H', self.data, 2)[0] != 0x4352: raise ValueError('Expected Adobe DCP IIRC/MMCR magic')
        offset = struct.unpack_from(self.endian+'I', self.data, 4)[0]
        if offset+2 > len(self.data): raise ValueError('Invalid DCP directory offset')
        count = struct.unpack_from(self.endian+'H', self.data, offset)[0]
        if offset+2+count*12+4 > len(self.data): raise ValueError('Truncated DCP directory')
        self.tags = {}
        for i in range(count):
            entry = offset+2+i*12
            tag, kind, number, pointer = struct.unpack_from(self.endian+'HHII', self.data, entry)
            if tag in self.tags: raise ValueError('Duplicate DCP tag')
            if kind not in TYPES: continue
            size = TYPES[kind][1]*number
            start = entry+8 if size <= 4 else pointer
            if start+size > len(self.data): raise ValueError('DCP tag outside file')
            self.tags[tag] = (kind, number, self.data[start:start+size])

    def get(self, tag, default=None):
        if tag not in self.tags: return default
        kind, count, raw = self.tags[tag]
        if kind == 2: return raw.rstrip(b'\0').decode('utf-8', 'replace')
        fmt = TYPES[kind][0]
        if kind in (5, 10):
            numbers = struct.unpack(self.endian+fmt*count, raw)
            if any(numbers[i+1] == 0 for i in range(0, len(numbers), 2)): raise ValueError('Zero DCP rational denominator')
            result = [numbers[i]/numbers[i+1] for i in range(0, len(numbers), 2)]
        else: result = list(struct.unpack(self.endian+fmt*count, raw))
        if not all(math.isfinite(x) for x in result): raise ValueError('Non-finite DCP value')
        return result

    def metadata(self):
        small = {name: self.get(tag) for tag, name in NAMES.items() if tag in self.tags and 'data' not in name and name != 'tone_curve'}
        small.update(path=str(self.path.resolve()), kind='dcp', bytes=len(self.data), has_creative_look=50982 in self.tags, has_tone_curve=50940 in self.tags, route='creative-look-extraction' if 50982 in self.tags else 'requires-profile-rendering')
        return small

def xmp_metadata(path):
    path = Path(path)
    root = ET.fromstring(path.read_bytes())
    ns = {'crs': 'http://ns.adobe.com/camera-raw-settings/1.0/', 'rdf': 'http://www.w3.org/1999/02/22-rdf-syntax-ns#'}
    description = root.find('rdf:RDF/rdf:Description', ns)
    if description is None: raise ValueError('XMP has no top-level RDF description')
    # A nested Look/Filter has its own UUID and CameraProfile. Flattening those
    # into the preset silently changes its identity and base profile.
    fields = {key.split('}', 1)[1]: value for key, value in description.attrib.items()
              if key.startswith('{'+ns['crs']+'}')}
    label = description.find('crs:Name//rdf:li', ns)
    return {'path': str(path.resolve()), 'kind': 'xmp', 'profile_name': label.text if label is not None else path.stem, 'camera_model': fields.get('CameraModelRestriction', ''), 'base_profile': fields.get('CameraProfile', ''), 'settings': fields, 'route': 'requires-lightroom-bake'}

def _matrix(matrix, rgb):
    return tuple(sum(a*b for a, b in zip(row, rgb)) for row in matrix)

def _inverse(m):
    a, b, c, d, e, f, g, h, i = [v for row in m for v in row]
    cof = ((e*i-f*h, c*h-b*i, b*f-c*e), (f*g-d*i, a*i-c*g, c*d-a*f), (d*h-e*g, b*g-a*h, a*e-b*d))
    determinant = a*cof[0][0]+b*cof[1][0]+c*cof[2][0]
    return tuple(tuple(v/determinant for v in row) for row in cof)

def _multiply(a, b):
    return tuple(tuple(sum(a[r][k]*b[k][c] for k in range(3)) for c in range(3)) for r in range(3))

def _normalize_rows(m): return tuple(tuple(v/sum(row) for v in row) for row in m)

# DNG SDK dng_color_space.cpp matrices and its white normalization. The common
# D50 PCS white cancels in inverse(ProPhoto-to-PCS) * sRGB-to-PCS.
_SRGB = _normalize_rows(((.4361, .3851, .1431), (.2225, .7169, .0606), (.0139, .0971, .7141)))
_PROPHOTO = _normalize_rows(((.7977, .1352, .0313), (.2880, .7119, .0001), (.0, .0, .8249)))
SRGB_TO_PROPHOTO_D50 = _multiply(_inverse(_PROPHOTO), _SRGB)
PROPHOTO_TO_SRGB_D50 = _inverse(SRGB_TO_PROPHOTO_D50)

def srgb_encode(v): return 12.92*v if v <= .0031308 else 1.055*v**(1/2.4)-.055
def srgb_decode(v): return v/12.92 if v <= .0031308*12.92 else ((v+.055)/1.055)**2.4

class CreativeLook:
    def __init__(self, profile):
        self.profile = profile
        dims = profile.get(50981)
        self.values = profile.get(50982)
        if not dims or not self.values: raise ValueError('This DCP has no creative LookTable')
        self.h, self.s, self.v = map(int, dims)
        if self.h < 1 or self.s < 2 or self.v < 1 or len(self.values) != self.h*self.s*self.v*3: raise ValueError('Invalid DCP LookTable dimensions')
        self.encoding = profile.get(51108, [0])[0]
        if self.encoding not in (0, 1): raise ValueError('Unsupported DCP LookTable encoding')

    def apply_prophoto(self, rgb):
        h, s, value = colorsys.rgb_to_hsv(*[max(0., min(1., x)) for x in rgb])
        encoded = srgb_encode(value) if self.encoding == 1 and self.v > 1 else value
        hc, sc, vc = h*self.h, s*(self.s-1), encoded*(self.v-1)
        hi, si, vi = min(int(hc), self.h-1), min(int(sc), self.s-2), min(int(vc), max(0, self.v-2))
        hf, sf, vf = hc-hi, sc-si, vc-vi
        delta = [0., 0., 0.]
        for dv in range(2 if self.v > 1 else 1):
            vw = (vf if dv else 1-vf) if self.v > 1 else 1.
            for dh in (0, 1):
                for ds in (0, 1):
                    offset = (((vi+dv)*self.h + (hi+dh)%self.h)*self.s+si+ds)*3
                    weight = vw*(hf if dh else 1-hf)*(sf if ds else 1-sf)
                    for c in range(3): delta[c] += self.values[offset+c]*weight
        encoded = max(0., min(1., encoded*delta[2]))
        out_value = srgb_decode(encoded) if self.encoding == 1 and self.v > 1 else encoded
        return colorsys.hsv_to_rgb((h+delta[0]/360)%1, max(0., min(1., s*delta[1])), out_value)

    def apply_srgb(self, rgb):
        linear = tuple(srgb_decode(x) for x in rgb)
        prophoto = _matrix(SRGB_TO_PROPHOTO_D50, linear)
        result = self.apply_prophoto(prophoto)
        linear = _matrix(PROPHOTO_TO_SRGB_D50, result)
        return tuple(srgb_encode(max(0., min(1., x))) for x in linear)

    def write_cube(self, path, size=33):
        if not 2 <= size <= 65: raise ValueError('Export cube size must be 2..65')
        with Path(path).open('w', encoding='utf-8', newline='\n') as stream:
            stream.write('# Creative DCP LookTable only; sRGB input/output; not a full raw profile.\n')
            stream.write(f'LUT_3D_SIZE {size}\n')
            for b in range(size):
                for g in range(size):
                    for r in range(size):
                        rgb = self.apply_srgb((r/(size-1), g/(size-1), b/(size-1)))
                        stream.write(' '.join(f'{x:.9f}' for x in rgb)+'\n')


class _DcpToneCurve:
    """Natural cubic spline from SDK dng_tone_curve/dng_spline, not linear knots."""

    def __init__(self, values):
        if len(values) < 4 or len(values) % 2:
            raise ValueError('DCP ProfileToneCurve needs at least two x/y points')
        self.x, self.y = list(values[::2]), list(values[1::2])
        if (not all(math.isfinite(v) and 0. <= v <= 1. for v in values)
                or any(b <= a for a, b in zip(self.x, self.x[1:]))):
            raise ValueError('Invalid DCP ProfileToneCurve coordinates')
        count = len(self.x)
        widths = [b-a for a, b in zip(self.x, self.x[1:])]
        secants = [(b-a)/h for a, b, h in zip(self.y, self.y[1:], widths)]
        # Natural endpoint conditions: 2*s0+s1=3*secant0. Interior
        # derivative equations are the tridiagonal system in SDK Solve().
        upper, rhs = [0.5], [1.5*secants[0]]
        for i in range(1, count):
            if i == count-1:
                lower, diagonal, high, value = 1., 2., 0., 3.*secants[-1]
            else:
                before, after = widths[i-1], widths[i]
                lower, diagonal, high = after, 2.*(before+after), before
                value = 3.*(after*secants[i-1]+before*secants[i])
            divisor = diagonal-lower*upper[-1]
            upper.append(high/divisor)
            rhs.append((value-lower*rhs[-1])/divisor)
        for i in range(count-2, -1, -1):
            rhs[i] -= upper[i]*rhs[i+1]
        self.slopes = rhs

    def evaluate(self, value):
        if value <= self.x[0]: return self.y[0]
        if value >= self.x[-1]: return self.y[-1]
        i = bisect.bisect_left(self.x, value)
        width = self.x[i]-self.x[i-1]
        right = (value-self.x[i-1])/width
        left = (self.x[i]-value)/width
        # SDK EvaluateSplineSegment, retaining natural-spline overshoot.
        return ((self.y[i-1]*(2.-left+right)+self.slopes[i-1]*width*right)*left*left
                +(self.y[i]*(2.-right+left)-self.slopes[i]*width*left)*right*right)


class DcpStyleStage:
    """Post-calibration DCP style stage for unrendered linear ProPhoto D50.

    Input must already have camera/WB/HueSatMap processing applied, but must
    not have profile exposure, LookTable, or tone rendering baked in. This is
    not a JPEG/sRGB transform or a complete Lightroom RAW renderer. The isolated
    stage uses RAW baseline exposure=0, stage3 gain=1 and black/shadows=0.

    Positive profile EV uses the exposure ramp before LookTable. Negative EV
    uses SDK exposure_tone after LookTable, before the profile spline. RGB tone
    maps channel extrema and interpolates the middle channel, as RefBaselineRGBTone.
    Curves/encoding use float64 analytic evaluation, not SDK float32 1D tables.
    """

    def __init__(self, profile, *, allow_identity_tone=False):
        self.profile = profile
        exposure = profile.get(51109, [0.])
        if len(exposure) != 1 or not math.isfinite(exposure[0]):
            raise ValueError('Invalid DCP BaselineExposureOffset')
        self.exposure_ev = exposure[0]
        try:
            self.exposure_gain = 2.**self.exposure_ev
        except OverflowError as exc:
            raise ValueError('DCP BaselineExposureOffset is too large') from exc
        self.look = CreativeLook(profile) if 50982 in profile.tags else None
        if 50981 in profile.tags and self.look is None:
            raise ValueError('DCP LookTable dimensions exist without table data')
        values = profile.get(50940)
        if values is None and not allow_identity_tone:
            raise ValueError('DCP has no ProfileToneCurve; renderer default is required '
                             '(allow_identity_tone=True explicitly selects identity)')
        self.tone = _DcpToneCurve(values) if values is not None else None

    def _tone_value(self, value):
        if self.exposure_ev < 0.:
            if value <= .25:
                value *= self.exposure_gain
            else:
                a = (16./9.)*(1.-self.exposure_gain)
                b = self.exposure_gain-.5*a
                value = (a*value+b)*value+(1.-a-b)
        value = max(0., min(1., value))
        return self.tone.evaluate(value) if self.tone is not None else value

    def apply_prophoto(self, rgb):
        rgb = tuple(rgb)
        if len(rgb) != 3 or not all(math.isfinite(v) and 0. <= v <= 1. for v in rgb):
            raise ValueError('DCP style input must be three finite linear ProPhoto values in [0,1]')
        if self.exposure_ev > 0.:
            rgb = tuple(min(1., v*self.exposure_gain) for v in rgb)
        if self.look is not None:
            rgb = self.look.apply_prophoto(rgb)
        # Sorting also handles all tied-channel cases of RefBaselineRGBTone.
        lo, mid, hi = sorted(range(3), key=lambda channel: rgb[channel])
        low, high = self._tone_value(rgb[lo]), self._tone_value(rgb[hi])
        middle = (low if rgb[hi] == rgb[lo] else
                  low+(high-low)*(rgb[mid]-rgb[lo])/(rgb[hi]-rgb[lo]))
        result = [0., 0., 0.]
        result[lo], result[mid], result[hi] = low, middle, high
        return tuple(result)

    def metadata(self):
        return {
            'input_space': 'linear-prophoto-d50',
            'output_space': 'linear-prophoto-d50',
            'input_stage': 'post-camera-calibration-and-huesatmap; pre-profile-style',
            'baseline_exposure_offset_ev': self.exposure_ev,
            'has_look_table': self.look is not None,
            'tone_curve_points': len(self.tone.x) if self.tone else 0,
            'tone_curve_policy': 'profile-natural-cubic-spline' if self.tone else 'explicit-identity',
            'rgb_tone_policy': 'curve-on-extrema; interpolate-middle-channel',
            'order': ['positive-exposure-ramp', 'look-table',
                      'negative-exposure-tone', 'profile-rgb-tone'],
            'assumptions': {'raw_baseline_exposure_ev': 0., 'stage3_gain': 1.,
                            'black_shadows': 0.},
            'profile_default_black_render': self.profile.get(51110, [0])[0],
            'excluded_components': ['camera ColorMatrix/ForwardMatrix and white balance',
                                    'illuminant-dependent HueSatMap',
                                    'RAW baseline exposure and stage3 gain',
                                    'renderer default black/shadows and missing-profile tone curve',
                                    'final color space conversion and output encoding',
                                    'Lightroom process-version/local adjustments'],
            'numerics': 'analytic float64 spline/encoding; not SDK sampled float32 1D tables',
            'sdk_sources': ['dng_render.cpp:33-132,910-949,1086-1134',
                            'dng_reference.cpp:1729-1845',
                            'dng_spline.cpp:69-240', 'dng_spline.h:27-48'],
        }
