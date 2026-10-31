"""Resample a .cube into NX300M's three 9x9x9 uint16 LUT planes.

Layout comes from stock CIpcs3DLutD4::SetParam and libudd!ipcs_3d_lut:
native axis 0 is outermost, axis 2 innermost; each plane has 729 words and a pad.
The r/g/b variable names describe the candidate RGB cube mapping. They do not
establish the hardware LUT's colour coordinates or encoding.
This creates table data only. It does not write registers or trigger reloads.
"""
import argparse
import json
import math
from pathlib import Path
import shlex
import struct

def load_cube(path):
    size = None
    low, high = (0.,) * 3, (1.,) * 3
    values = []
    for line_number, line in enumerate(path.read_text(encoding='utf-8-sig').splitlines(), 1):
        tokens = shlex.split(line, comments=True)
        if not tokens: continue
        if tokens[0] == 'TITLE': continue
        if tokens[0] == 'LUT_1D_SIZE': raise ValueError('1D shaper LUTs are not supported')
        if tokens[0] == 'LUT_3D_SIZE':
            if size is not None or len(tokens) != 2: raise ValueError('Duplicate or invalid LUT_3D_SIZE')
            size = int(tokens[1])
            if not 2 <= size <= 129: raise ValueError('LUT_3D_SIZE must be 2..129')
        elif tokens[0] in ('DOMAIN_MIN', 'DOMAIN_MAX'):
            if len(tokens) != 4: raise ValueError('Domain must have three components')
            vector = tuple(map(float, tokens[1:]))
            if tokens[0] == 'DOMAIN_MIN': low = vector
            else: high = vector
        else:
            if size is None or len(tokens) != 3: raise ValueError(f'Invalid cube row at line {line_number}')
            values.append(tuple(map(float, tokens)))
    if size is None or len(values) != size**3: raise ValueError('Incomplete 3D cube')
    if not all(math.isfinite(x) for vector in (*values, low, high) for x in vector): raise ValueError('Non-finite LUT value')
    if any(a >= b for a, b in zip(low, high)): raise ValueError('Invalid domain bounds')
    return size, low, high, values

def compile_cube(cube, stock_baseline=None):
    size, low, high, values = cube
    if stock_baseline is not None and (len(stock_baseline) != 2190 or any(x < 0 or x > 1023 for x in stock_baseline)):
        raise ValueError('Stock baseline must contain three 730-word 10-bit planes')
    planes = [[], [], []]
    def sample(rgb):
        coords = [max(0., min(size-1., (v-a)/(b-a)*(size-1))) for v, a, b in zip(rgb, low, high)]
        indices = [min(int(x), size-2) for x in coords]
        fractions = [x-i for x, i in zip(coords, indices)]
        result = [0., 0., 0.]
        for db in (0, 1):
            for dg in (0, 1):
                for dr in (0, 1):
                    # .cube stores red fastest; the D4 destination uses blue fastest.
                    index = ((indices[2]+db)*size + indices[1]+dg)*size + indices[0]+dr
                    weight = math.prod(f if d else 1-f for f, d in zip(fractions, (dr, dg, db)))
                    for c in range(3): result[c] += values[index][c] * weight
        return result
    for r in range(9):
        for g in range(9):
            for b in range(9):
                index = (r*9+g)*9+b
                source = tuple(stock_baseline[c*730+index]/1023 for c in range(3)) if stock_baseline is not None else (r/8, g/8, b/8)
                rgb = sample(source)
                for c in range(3): planes[c].append(math.floor(max(0., min(1., rgb[c])) * 1023 + 1e-9))
    return b''.join(struct.pack('<730H', *plane, 0) for plane in planes)

def convert(path):
    return compile_cube(load_cube(path))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cube', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    data = convert(args.cube)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(data)
    print(json.dumps({'source': str(args.cube), 'output': str(args.output), 'bytes': len(data), 'planes': 3, 'grid': 9, 'bits': 10, 'hardware_verified': False}))

if __name__ == '__main__': main()
