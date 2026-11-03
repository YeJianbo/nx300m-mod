"""Volume projection for the RGB-code trilinear 9^3 software model.

For bounded source cubes the direct cross-moments are analytic Gauss integrals
on the union of both knot sets. Compositions and clipped out-of-range sources
use deterministic cubature: their boundaries are not tensor grids.
"""
from functools import lru_cache

import numpy as np

from lut_resample import GRID, NODES, basis, target


def hats(size, low, high, x):
    coordinate = np.clip((x-low)/(high-low), 0, 1)*(size-1)
    base = np.minimum(np.floor(coordinate).astype(int), size-2)
    fraction = coordinate-base
    result = np.zeros((len(x), size))
    result[np.arange(len(x)), base] = 1-fraction
    result[np.arange(len(x)), base+1] += fraction
    return result


@lru_cache(maxsize=32)
def cross_moment(n0, lo0, hi0, n1, lo1, hi1):
    knots = np.unique(np.clip(np.r_[0., 1., np.linspace(lo0, hi0, n0),
                                   np.linspace(lo1, hi1, n1)], 0, 1))
    center = (knots[1:]+knots[:-1])/2
    radius = np.diff(knots)/2
    x = np.stack((center-radius/np.sqrt(3), center+radius/np.sqrt(3)), 1).ravel()
    weight = np.repeat(radius, 2)
    return hats(n0, lo0, hi0, x).T@(weight[:, None]*hats(n1, lo1, hi1, x))


def contract(matrices, values):
    return np.einsum('ia,jb,kc,abcd->ijkd', *matrices, values, optimize=True)


@lru_cache(maxsize=1)
def volume_gram():
    mass = cross_moment(GRID, 0., 1., GRID, 0., 1.)
    return np.kron(np.kron(mass, mass), mass)


@lru_cache(maxsize=2)
def cubature(order=8):
    x, weight = np.polynomial.legendre.leggauss(order)
    axis = ((np.arange(GRID-1)[:, None]+(x+1)/2)/(GRID-1)).ravel()
    weights = np.tile(weight/(2*(GRID-1)), GRID-1)
    points = np.stack(np.meshgrid(axis, axis, axis, indexing='ij'), -1).reshape(-1, 3)
    joint = np.einsum('i,j,k->ijk', weights, weights, weights).ravel()
    return points, joint


def volume_terms(cube, stock=None, *, order=8):
    size, low, high, values = cube
    values = np.asarray(values, dtype=float)
    bounded = not np.any((values < 0) | (values > 1))
    if stock is None and bounded:
        source = values.reshape(size, size, size, 3).transpose(2, 1, 0, 3)
        cross = [cross_moment(GRID, 0., 1., size, float(lo), float(hi))
                 for lo, hi in zip(low, high)]
        right = contract(cross, source).reshape(NODES, 3)
        mass = [cross_moment(size, float(lo), float(hi), size, float(lo), float(hi))
                for lo, hi in zip(low, high)]
        energy = np.sum(source*contract(mass, source), axis=(0, 1, 2))
        detail = {'volume_integral': 'exact for bounded trilinear source',
                  'source_grid': size, 'interpolation': 'RGB-code trilinear hypothesis'}
    else:
        points, weight = cubature(order)
        y = target(cube, points, stock)
        indices, weights = basis(points)
        right = np.zeros((NODES, 3))
        for corner in range(8):
            np.add.at(right, indices[:, corner], (weight*weights[:, corner])[:, None]*y)
        energy = np.sum(weight[:, None]*y*y, axis=0)
        detail = {'volume_integral': 'tensor Gauss cubature of composed target',
                  'order_per_cell_axis': order, 'samples': len(points),
                  'interpolation': 'RGB-code trilinear hypothesis'}
        if stock is None:
            detail['volume_integral'] = 'tensor Gauss cubature of output-clipped source'
        detail['source_nodes_bounded'] = bounded
    return volume_gram(), right, energy, detail


@lru_cache(maxsize=1)
def replace_interior_gram(count):
    # Importing here avoids constructing the fixed design at module import.
    from lut_resample import training_system
    _, weight, indices, weights, _, _ = training_system()
    gram = np.zeros((NODES, NODES))
    for a in range(8):
        for b in range(8):
            np.add.at(gram, (indices[:count, a], indices[:count, b]),
                      weight[:count]*weights[:count, a]*weights[:count, b])
    return float(weight[:count].sum())*volume_gram()-gram


def protected_terms(cube, stock, y, weight, indices, weights, h, right):
    # Keep the existing gray/face/node protection and regularisation exactly;
    # replace only the Monte-Carlo volume term with the integrated moment.
    count = (GRID-1)**3*32
    _, volume_right, _, detail = volume_terms(cube, stock)
    result = right.copy()
    for a in range(8):
        np.add.at(result, indices[:count, a],
                  -(weight[:count]*weights[:count, a])[:, None]*y[:count])
    result += weight[:count].sum()*volume_right
    return h+replace_interior_gram(count), result, detail
