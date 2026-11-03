"""Exercise formats introduced by expanding the LUT library, without a camera."""
import json
from pathlib import Path
import struct

import numpy as np

from cube_to_d4 import compile_cube
from lut_integral import volume_terms
from lut_resample import grid, sample_cube, sample_table, unpack
from stock_lut import factory_3d_lut


ROOT = Path(__file__).resolve().parents[1]


def run():
    rng = np.random.default_rng(20261103)
    points = rng.random((4096, 3))
    matrix = np.array([[.50, .17, .11], [.13, .52, .12], [.07, .19, .54]])
    offset = np.array([.08, .09, .07])
    low, high = np.array([-.12, -.05, -.18]), np.array([1.12, 1.2, 1.07])
    rows = []
    for size in (17, 33, 65):
        source = low+grid(size)[:, ::-1]*(high-low)
        cube = size, low, high, source@matrix.T+offset
        exact = points@matrix.T+offset
        source_error = float(np.max(np.abs(sample_cube(cube, points)-exact)))
        if source_error > 2e-14:
            raise AssertionError(('source axes/domain', size, source_error))
        errors = {}
        for method in ('fit', 'fit-integral'):
            data = compile_cube(cube, method=method)
            unpack(data)
            error = float(np.max(np.abs(sample_table(data, points)-exact)))
            # Integer coordinate descent minimizes joint squared error, and
            # can trade a >0.5-code node error for a lower interpolated RMSE.
            # An independently rounded node's pointwise bound is not its ABI.
            if len(data) != 4380 or error > 2/1023:
                raise AssertionError(('D4 affine', size, method, error))
            errors[method] = error*1023
        rows.append({'source_grid': size, 'source_affine_max_abs': source_error,
                     'd4_affine_max_abs_10bit_codes': errors})

    stock = factory_3d_lut()
    identity = (2, (0.,)*3, (1.,)*3, grid(2)[:, ::-1])
    same = {method: compile_cube(identity, stock, method=method) == struct.pack('<2190H', *stock)
            for method in ('fit', 'fit-integral')}
    if not all(same.values()):
        raise AssertionError(('identity stock composition', same))

    # Real imported .cube files may have negative or >1 outputs. The fitted
    # target clips *after* interpolation; clipping source nodes would differ.
    source = grid(5)[:, ::-1]
    clipped = 5, (0.,)*3, (1.,)*3, source*np.array([1.2, 1.4, 1.6])-.15
    _, rhs8, energy8, detail = volume_terms(clipped, order=8)
    _, rhs16, energy16, _ = volume_terms(clipped, order=16)
    rhs_error = float(np.max(np.abs(rhs8-rhs16)))
    energy_error = float(np.max(np.abs(energy8-energy16)))
    if rhs_error > 2e-6 or energy_error > 2e-4:
        raise AssertionError(('clipped cubature convergence', rhs_error, energy_error))
    fitted = compile_cube(clipped, method='fit-integral')
    unpack(fitted)
    if not np.isfinite(sample_table(fitted, points)).all():
        raise AssertionError('Nonfinite clipped fit')
    return {'grids': rows, 'identity_stock_bytes_equal': same,
            'out_of_range_source': {'integration': detail,
                                    'order8_vs_order16_rhs_max_abs': rhs_error,
                                    'order8_vs_order16_energy_max_abs': energy_error},
            'hardware_verified': False}


if __name__ == '__main__':
    report = run()
    output = ROOT/'outputs/looks/lightroom-import/compatibility.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, indent=2))
