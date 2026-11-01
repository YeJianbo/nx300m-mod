"""Explicit assumptions and native parameter arithmetic for IPCM fitting.

This models submitted parameter words, not undocumented ISP pixel arithmetic.
The stock-gamma alternatives are sensitivity models until frames are measured.
"""
import numpy as np

from fit_ipcm_preview import (NATIVE_RGB_TO_YCC, NATIVE_YCC_TO_RGB,
                              round_away, strength_model)


class ColorModel:
    def __init__(self, gamma='code', stock_ccm=None, stock_ycc=None):
        if gamma not in ('code', 'srgb', 'stock-knots'):
            raise ValueError('Unknown gamma assumption')
        self.gamma = gamma
        self.stock_ccm = np.asarray(np.eye(3)*256 if stock_ccm is None else stock_ccm, dtype=float).reshape(3, 3)
        self.stock_ycc = np.asarray(NATIVE_RGB_TO_YCC*256 if stock_ycc is None else stock_ycc, dtype=float).reshape(3, 3)
        for matrix in (self.stock_ccm, self.stock_ycc):
            if np.any(matrix != np.trunc(matrix)) or np.any(matrix < -1024) or np.any(matrix > 1023):
                raise ValueError('Stock matrices must contain signed native words')
            if abs(np.linalg.det(matrix)) < 1e-6:
                raise ValueError('A baseline-coordinate model needs invertible stock matrices')
        self.ccm_inverse = np.linalg.inv(self.stock_ccm)
        self.ycc_inverse = np.linalg.inv(self.stock_ycc/256)
        # Actual immutable Gamma X15/Y15 knots. Interpolation here is explicitly
        # linear; it is not a recreation of the stock IntSpline implementation.
        self.gamma_x = np.array([0,1,2,4,8,16,32,64,128,256,512,1024,1536,1920,2047])/2047
        self.gamma_y = np.array([0,1,2,3,6,11,22,45,81,129,181,225,245,255,255])/255

    def describe(self):
        return {'parameter_model': 'Q12 profile composed with signed native Q8 matrices; round ties away; whole-matrix fallback',
                'gamma_assumption': self.gamma,
                'gamma_verified': False,
                'stock_ccm_q8': self.stock_ccm.astype(int).tolist(),
                'stock_ycc_q8': self.stock_ycc.astype(int).tolist(),
                'stock_ccm_measured': False,
                'stock_ycc_basis': 'IQ immutable default RGB/YCC matrix',
                'stock_knots_interpolation': 'linear sensitivity model, not stock IntSpline' if self.gamma == 'stock-knots' else None,
                'pixel_order_assumption': 'stock inverse tone -> modified CCM -> stock tone -> creative shared tone -> composed RGB/YCC -> display RGB proxy',
                'pixel_fixed_point_rounding': 'unknown; not emulated',
                'native_pixels_verified': False}

    def decode(self, rgb):
        rgb = np.clip(rgb, 0, 1)
        if self.gamma == 'code':
            return rgb
        if self.gamma == 'srgb':
            return np.where(rgb <= .04045, rgb/12.92, ((rgb+.055)/1.055)**2.4)
        # Choose the first coordinate of a clipped/flat branch. That lost
        # information cannot be reconstructed from a JPEG or LCD frame.
        y, indices = np.unique(self.gamma_y, return_index=True)
        return np.interp(rgb, y, self.gamma_x[indices])

    def encode(self, rgb, slope=False):
        raw = np.asarray(rgb)
        x = np.clip(raw, 0, 1)
        if self.gamma == 'code':
            value, derivative = x, np.ones_like(x)
        elif self.gamma == 'srgb':
            value = np.where(x <= .0031308, 12.92*x, 1.055*x**(1/2.4)-.055)
            derivative = np.where(x <= .0031308, 12.92, (1.055/2.4)*np.maximum(x, 1e-12)**(1/2.4-1))
        else:
            index = np.clip(np.searchsorted(self.gamma_x, x, side='right')-1, 0, len(self.gamma_x)-2)
            derivative = np.diff(self.gamma_y)[index]/np.diff(self.gamma_x)[index]
            value = np.interp(x, self.gamma_x, self.gamma_y)
        derivative = derivative*((raw >= 0) & (raw <= 1))
        return (value, derivative) if slope else value

    @staticmethod
    def compose(profile_q12, stock):
        submitted = round_away(np.asarray(profile_q12).reshape(3,3)@stock/4096)
        fallback = bool(np.any(submitted < -1024) or np.any(submitted > 1023))
        return (stock.copy() if fallback else submitted), fallback

    def apply(self, rgb, model, amount=1., return_submission=False):
        rgb = np.asarray(rgb, dtype=float)
        shape = rgb.shape
        if amount == 0:
            return (rgb.copy(), {}) if return_submission else rgb.copy()
        if 'pcse_delta' in model and np.any(model['pcse_delta']):
            raise ValueError('This tonal model needs measured PCSE responses for nonzero deltas')
        m = strength_model(model, amount)
        ccm, ccm_fallback = self.compose(m['ccm_q12'], self.stock_ccm)
        # Work in the coordinates of the baseline output, rather than silently
        # assuming a measured scene-specific stock CCM is the identity.
        a = ccm@self.ccm_inverse
        linear = self.decode(rgb.reshape(-1,3))
        x = self.encode(linear@a.T)
        x = np.interp(x, np.arange(256)/255, np.asarray(m['tone'])/255)
        if 'ycc_q12' in m:
            ycc, ycc_fallback = self.compose(m['ycc_q12'], self.stock_ycc)
            out = x@(ycc/256).T@self.ycc_inverse.T
        else:
            # v1 has a separate native CHM2x2. Its default stock CHM is unity;
            # preserve its historical Rec709 proxy for an honest v1 comparison.
            from fit_ipcm_preview import RGB_TO_YCC, YCC_TO_RGB
            post = round_away(np.asarray(m['chroma_q12']).reshape(2,2)*256/4096)/256
            ycc_fallback = bool(np.any(post*256 < -1024) or np.any(post*256 > 1023))
            if ycc_fallback:
                post = np.eye(2)
            ycc = None
            v = x@RGB_TO_YCC.T
            v[:,1:] = v[:,1:]@post.T
            out = v@YCC_TO_RGB.T
        result = np.clip(out, 0, 1).reshape(shape)
        submission = {'ccm_q8': ccm.tolist(), 'ycc_q8': None if ycc is None else ycc.tolist(),
                      'ccm_fallback': ccm_fallback, 'ycc_fallback': ycc_fallback}
        return (result, submission) if return_submission else result
