"""Rank overlap among the current camera LUT set on a fixed colour sample."""
from __future__ import annotations

import itertools
import json
from pathlib import Path

import numpy as np

from nx300m_lut_core import apply_lut, parse_lut_file


ROOT = Path(__file__).resolve().parents[1]
MACHINE = ROOT / 'lut-library/machine-catalog.json'
OUT = ROOT / 'outputs/looks/cxo-import/comparison/camera-lut-overlap.json'


def sample_colours() -> np.ndarray:
    coords = np.linspace(0.03, 0.97, 14, dtype=np.float32)
    r, g, b = np.meshgrid(coords, coords, coords, indexing='ij')
    lattice = np.stack((r.ravel(), g.ravel(), b.ravel()), axis=1)
    gray = np.repeat(np.linspace(0, 1, 257, dtype=np.float32)[:, None], 3, axis=1)
    rng = np.random.default_rng(0xC0DE)
    random = rng.random((24000, 3), dtype=np.float32)
    return np.concatenate((lattice, gray, random), axis=0)


def main() -> None:
    ids = json.loads(MACHINE.read_text(encoding='utf-8'))['ids']
    points = sample_colours()
    rendered = {}
    labels = {}
    for key in ids:
        path = ROOT / 'looks' / key / 'source.cube'
        item = parse_lut_file(path)
        rendered[key] = apply_lut(points[:, None, :], item.lut)[:, 0, :]
        metadata = json.loads((path.parent / 'look.json').read_text(encoding='utf-8'))
        labels[key] = metadata.get('label', key)

    rows = []
    for left, right in itertools.combinations(ids, 2):
        delta = np.abs(rendered[left] - rendered[right]) * 255.0
        per_pixel = delta.mean(axis=1)
        rows.append({
            'a': left,
            'a_label': labels[left],
            'b': right,
            'b_label': labels[right],
            'mean_abs_8bit': float(delta.mean()),
            'p95_pixel_abs_8bit': float(np.percentile(per_pixel, 95)),
            'max_channel_abs_8bit': float(delta.max()),
        })
    rows.sort(key=lambda row: row['mean_abs_8bit'])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        'schema': 1,
        'source': 'current camera machine-catalog',
        'count': len(ids),
        'samples': len(points),
        'limitation': 'uniform/random sRGB input comparison; follow with photo review',
        'pairs': rows,
    }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for row in rows[:25]:
        print(f"{row['mean_abs_8bit']:6.2f}  {row['p95_pixel_abs_8bit']:6.2f}  {row['a']} <> {row['b']}")


if __name__ == '__main__':
    main()
