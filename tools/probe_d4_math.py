"""Check compiler mathematics against analytic functions and upstream baking."""
import argparse
import importlib.util
import json
from pathlib import Path
import struct

import numpy as np

from cube_to_d4 import compile_cube, load_cube
from lut_resample import compile_table, grid, sample_cube, sample_table, unpack
from stock_lut import factory_3d_lut
from compare_lut_backends import REFERENCE, table

ROOT = Path(__file__).resolve().parents[1]


def run():
    rng = np.random.default_rng(7213)
    x = rng.random((2048, 3))
    matrix = np.array([[.1, .6, .1], [.2, .1, .4], [.3, .2, .1]])
    offset = np.array([.08, .09, .1])
    low, high = np.array([-.2, -.1, -.3]), np.array([1.2, 1.1, 1.4])
    coordinates = low+grid(3)[:, ::-1]*(high-low)
    affine = (3, low, high, coordinates@matrix.T+offset)
    exact = x@matrix.T+offset
    axis_error = float(np.max(np.abs(sample_cube(affine, x)-exact)))
    assert axis_error < 1e-12, ('cube axes/domain', axis_error)
    data, metadata = compile_table(affine)
    affine_error = float(np.max(np.abs(sample_table(data, x)-exact)))
    assert affine_error <= 1/1023, ('D4 axes/quantisation', affine_error)
    identity = (2, (0.,)*3, (1.,)*3, grid(2)[:, ::-1])
    stock = factory_3d_lut()
    stock_bytes = struct.pack('<2190H', *stock)
    identity_results = {}
    for method in ('legacy', 'round', 'fit', 'fit-perceptual'):
        candidate = compile_cube(identity, stock, method=method)
        identity_results[method] = candidate == stock_bytes
        assert identity_results[method], ('identity/factory composition', method)
    mono = (3, (0.,)*3, (1.,)*3,
            np.repeat((grid(3)[:, ::-1]@np.array([.299, .587, .114]))[:, None], 3, axis=1))
    for method in ('fit', 'fit-perceptual'):
        values = unpack(compile_cube(mono, method=method))
        assert np.array_equal(values[:, 0], values[:, 1])
        assert np.array_equal(values[:, 1], values[:, 2])
    gray = np.repeat(np.linspace(0, 1, 8193)[:, None], 3, axis=1)
    tri_x, shape_metadata = compile_table(load_cube(ROOT/'looks/metol-trix400/source.cube'))
    rendered = sample_table(tri_x, gray)
    backtrack = float(np.max(np.maximum.accumulate(rendered[:, 0])-rendered[:, 0])*255)
    assert backtrack <= 1e-10, ('monotone monochrome regression', backtrack)
    assert np.array_equal(rendered[:, 0], rendered[:, 1])
    assert np.array_equal(rendered[:, 1], rendered[:, 2])
    upstream = REFERENCE/'new_nx300m/nx300m_cube_to_lut18.py'
    spec = importlib.util.spec_from_file_location('upstream_cube_to_18', upstream)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = ROOT/'looks/nx300-fuji-classic-negative/source.cube'
    size, values, _ = module.parse_cube(source)
    expected = np.array(module.make_nx18(size, values), dtype=np.uint16)
    actual = np.frombuffer(table(load_cube(source), 18), '<u2').reshape(-1, 4)[:, :3]
    mismatches = int(np.count_nonzero(expected != actual))
    assert mismatches == 0, ('upstream 18-grid baking', mismatches)
    return {'analytic_affine_max_abs': axis_error,
            'd4_affine_max_abs_codes': affine_error*1023,
            'identity_composed_equals_factory_bytes': identity_results,
            'achromatic_output_channels_identical': True,
            'monochrome_gray_max_backtrack_codes': backtrack,
            'monochrome_shape_constraint': shape_metadata.get('shape_constraint'),
            'upstream_18_all_5832_nodes_mismatched_channels': mismatches,
            'affine_solver': metadata['solver'],
            'native_hardware_model_verified': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path,
                        default=ROOT/'outputs/looks/d4-optimization/math-check.json')
    args = parser.parse_args()
    result = run()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(result, indent=2))
