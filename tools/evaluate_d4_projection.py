"""Measure the fixed 9^3 model's uniform-volume least-squares lower bounds.

This tests the RGB-code/trilinear hypothesis, not the camera ISP. The float
projection drops gray/face protections to give a lower bound for any bounded
9^3 node table under this one metric.
"""
import json
from pathlib import Path
import struct

import numpy as np

from cube_to_d4 import load_cube
from look_library import LookLibrary
from lut_integral import volume_terms, volume_gram
from lut_resample import basis, bounded_solve, compile_table, grid, sample_cube, unpack
from stock_lut import factory_3d_lut

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/looks/d4-optimization'


def cube_from_values(size, values, low=(0.,)*3, high=(1.,)*3):
    data = values.reshape(size, size, size, 3).transpose(2, 1, 0, 3).reshape(-1, 3)
    return size, low, high, data


def check_integrals():
    identity = cube_from_values(5, grid(5))
    h, b, energy, _ = volume_terms(identity)
    fitted = np.linalg.solve(h, b)
    affine_error = float(np.max(np.abs(fitted-grid())))
    energy_error = float(np.max(np.abs(energy-1/3)))
    partition_error = abs(float(volume_gram().sum())-1.)
    # Independent 4-point integration using the public .cube interpolator and
    # full source/coarse knot partition, including non-unit/clamped DOMAINs.
    rng = np.random.default_rng(71093)
    low, high = np.array([-.13, .12, -.07]), np.array([.87, 1.21, 1.11])
    source = cube_from_values(5, rng.random((125, 3)), low, high)
    _, analytic_b, analytic_e, _ = volume_terms(source)
    q, weight = np.polynomial.legendre.leggauss(4)
    axes, weights = [], []
    for lo, hi in zip(low, high):
        knots = np.unique(np.clip(np.r_[np.linspace(0, 1, 9), np.linspace(lo, hi, 5)], 0, 1))
        center, radius = (knots[1:]+knots[:-1])/2, np.diff(knots)/2
        axes.append((center[:, None]+radius[:, None]*q).ravel())
        weights.append((radius[:, None]*weight).ravel())
    x = np.stack(np.meshgrid(*axes, indexing='ij'), -1).reshape(-1, 3)
    w = np.einsum('i,j,k->ijk', *weights).ravel()
    y = sample_cube(source, x)
    indices, hat_weights = basis(x)
    numeric_b = np.zeros_like(analytic_b)
    for corner in range(8):
        np.add.at(numeric_b, indices[:, corner], (w*hat_weights[:, corner])[:, None]*y)
    rhs_error = float(np.max(np.abs(numeric_b-analytic_b)))
    numeric_e = np.sum(w[:, None]*y*y, axis=0)
    random_energy_error = float(np.max(np.abs(numeric_e-analytic_e)))
    stock = factory_3d_lut()
    identity_data, _ = compile_table(identity, stock, method='fit-integral')
    exact_stock = identity_data == struct.pack('<2190H', *stock)
    checks = {'affine_projection_node_error': affine_error,
              'identity_energy_error': energy_error, 'mass_partition_error': partition_error,
              'independent_domain_quadrature_samples': len(x),
              'cross_moment_max_error': rhs_error, 'energy_max_error': random_energy_error,
              'identity_composition_equals_factory_bytes': exact_stock}
    if max(affine_error, energy_error, partition_error, rhs_error, random_energy_error) > 2e-11 or not exact_stock:
        raise AssertionError(checks)
    return checks


def rmse(h, b, energy, nodes):
    squared = np.sum(energy)-2*np.sum(nodes*b)+np.sum(nodes*(h@nodes))
    if squared < -1e-11:
        raise ValueError(f'Negative integrated error {squared}')
    return float(np.sqrt(max(0., squared)/3)*255)


def main():
    checks = check_integrals()
    print(json.dumps({'math': checks}), flush=True)
    library = LookLibrary()
    report = {'model': 'bounded source, RGB code, trilinear, uniform RGB-volume L2',
              'hardware_verified': False, 'math': checks, 'looks': []}
    # This lower-bound report belongs to the saved evaluation cohort. Adding
    # library entries must not silently change the meaning of the old run.
    cohort = json.loads((OUT/'report.json').read_text(encoding='utf-8'))['looks']
    for recorded in cohort:
        meta = library.get(recorded['id'])
        key = meta['id']
        cube = load_cube(library.directory(key)/'source.cube')
        h, b, energy, detail = volume_terms(cube)
        raw = np.linalg.solve(h, b)
        bounded, status = bounded_solve(h, b)
        if not all(s['converged'] for s in status):
            raise RuntimeError('Volume lower-bound solve did not converge: '+key)
        row = {'id': key, 'label': meta['label'], 'integration': detail,
               'float_unbounded_rmse': rmse(h, b, energy, raw),
               'float_bounded_rmse': rmse(h, b, energy, bounded),
               'unbounded_node_min': float(raw.min()), 'unbounded_node_max': float(raw.max()),
               'unbounded_outside_nodes': int(np.count_nonzero((raw < 0) | (raw > 1))),
               'bounded_solver': status, 'tables': {}}
        for method in ('legacy', 'round', 'fit', 'fit-perceptual', 'fit-integral'):
            path = OUT/'profiles'/key/(method+'.d4lut')
            if path.exists():
                row['tables'][method] = rmse(h, b, energy, unpack(path.read_bytes()))
                if row['tables'][method] < row['float_bounded_rmse']-1e-7:
                    raise AssertionError('Candidate below its exact model lower bound: '+key)
        report['looks'].append(row)
        (OUT/'projection-limits.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n',
                                                  encoding='utf-8')
        print(json.dumps(row, ensure_ascii=False), flush=True)
    report['summary'] = {key: float(np.mean([row[key] for row in report['looks']]))
                         for key in ('float_unbounded_rmse', 'float_bounded_rmse')}
    report['summary']['tables'] = {method: float(np.mean([row['tables'][method] for row in report['looks']]))
                                  for method in report['looks'][0]['tables']
                                  if all(method in row['tables'] for row in report['looks'])}
    (OUT/'projection-limits.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'summary': report['summary']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
