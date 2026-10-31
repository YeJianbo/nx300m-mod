"""Decode legacy Metol DRIMeV 17-grid RGBX slots into a portable .cube.

The source grid is nonuniform at its final interval: 0,16,...,240,255.
Only the pinned 2026-10-18 RGBX layout with an all-zero trailer is supported.
Newer nxwa native CrCbY slots use different coordinates and a repeated final
entry trailer. They must not be decoded as RGBX by changing this label.
Trailer checks are validation of the declared legacy format, not reliable
colour-space detection; the caller must know the slot's source encoding.
"""
from pathlib import Path

def write_cube(source, destination, size=33):
    raw = Path(source).read_bytes()
    if len(raw) != 19712: raise ValueError('Expected a 19712-byte Metol LUT slot')
    if any(raw[i] for i in range(3, 19652, 4)) or any(raw[19652:]): raise ValueError('Expected legacy Metol RGBX layout with zero trailer; native CrCbY slots require a separate decoder')
    if not 2 <= size <= 65: raise ValueError('Master cube size must be 2..65')
    def sample(rgb):
        axes = []
        for v in rgb:
            level = v*255
            i = min(int(level/16), 15)
            axes.append((i, (level-i*16)/(15 if i == 15 else 16)))
        out = [0., 0., 0.]
        for db in (0, 1):
            for dg in (0, 1):
                for dr in (0, 1):
                    offset = (((axes[2][0]+db)*17+axes[1][0]+dg)*17+axes[0][0]+dr)*4
                    weight = 1.
                    for axis, delta in zip(axes, (dr, dg, db)): weight *= axis[1] if delta else 1-axis[1]
                    for c in range(3): out[c] += raw[offset+c]/255*weight
        return out
    with Path(destination).open('w', encoding='utf-8', newline='\n') as out:
        out.write('# Decoded from a Metol RGBX slot; original 0,16,...,240,255 grid.\n')
        out.write(f'LUT_3D_SIZE {size}\n')
        for b in range(size):
            for g in range(size):
                for r in range(size):
                    out.write(' '.join(f'{x:.9f}' for x in sample((r/(size-1), g/(size-1), b/(size-1))))+'\n')
