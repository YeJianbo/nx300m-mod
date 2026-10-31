"""Adapt the companion NX500 film-simulation color recipes to standard cubes.

The upstream cube baker stores B fastest. Our library accepts standard .cube
files with R fastest, so generation changes storage order without exchanging
the input channels. Grain is spatial and is deliberately left out of the LUT.
"""
import argparse
import json
import math
from pathlib import Path
import subprocess
import tempfile

from look_library import LookLibrary

PROJECT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT/'work/reference/nx500-filmsim'
REPOSITORY = 'https://github.com/WApaperplane/nx500-filmsim'
LABELS = {
    'portra400': 'Metol Portra 400',
    'superia400': 'Metol Superia 400',
    'velvia50': 'Metol Velvia 50',
    'ektachrome': 'Metol Ektachrome E100',
    'trix400': 'Metol Tri-X 400',
    'hp5': 'Metol HP5 Plus',
}


def curve_table(curve):
    contrast = float(curve.get('contrast', 1.0))
    shadow = float(curve.get('shadow_lift', 0.0))
    rolloff = float(curve.get('highlight_rolloff', 0.0))
    if not all(math.isfinite(value) for value in (contrast, shadow, rolloff)):
        raise ValueError('Non-finite source curve')
    output = []
    for value in range(256):
        x = min(1.0, max(0.0, (value/255.0 - 0.5)*contrast + 0.5))
        x = 1.0/(1.0 + math.exp(-max(0.1, 6.0*contrast)*(x - 0.5)))
        x = min(1.0, max(0.0, x*(1.0 - rolloff) + shadow))
        output.append(round(x*255))
    return output


def bake_cube(recipe, size=33):
    if not 2 <= size <= 65:
        raise ValueError('Cube grid must be 2..65')
    if recipe.get('lut3d') or recipe.get('strength', 1.0) != 1.0:
        raise ValueError('This importer accepts the upstream matrix/curve bake at strength 1 only')
    matrix = recipe['matrix']
    if len(matrix) != 3 or any(len(row) != 3 for row in matrix):
        raise ValueError('Expected a 3x3 source matrix')
    saturation = float(recipe.get('saturation', 1.0))
    if not all(math.isfinite(value) for value in [saturation, *sum(matrix, [])]):
        raise ValueError('Non-finite source matrix or saturation')
    curve = curve_table(recipe.get('curve', {}))
    rows = ['# Companion filmsim color bake; grain excluded; R-fastest standard cube.',
            f'TITLE "{recipe["id"]} color bake"', f'LUT_3D_SIZE {size}',
            'DOMAIN_MIN 0 0 0', 'DOMAIN_MAX 1 1 1']
    for b in range(size):
        for g in range(size):
            for r in range(size):
                rgb = (r/(size - 1), g/(size - 1), b/(size - 1))
                # Preserve the upstream operation order at 8-bit rounding
                # boundaries; Python's compensated sum can select a new bin.
                transformed = [row[0]*rgb[0] + row[1]*rgb[1] + row[2]*rgb[2] for row in matrix]
                luminance = 0.299*transformed[0] + 0.587*transformed[1] + 0.114*transformed[2]
                result = []
                for value in transformed:
                    value = luminance + (value - luminance)*saturation
                    index = int(min(1.0, max(0.0, value))*255.0 + 0.5)
                    result.append(curve[index]/255.0)
                rows.append(' '.join(f'{value:.9f}' for value in result))
    return '\n'.join(rows) + '\n'


def import_recipes(source=SOURCE, library=None, *, refresh=False):
    source = Path(source).resolve()
    library = library or LookLibrary()
    commit = subprocess.run(['git', '-C', str(source), 'rev-parse', 'HEAD'],
                            check=True, capture_output=True, text=True).stdout.strip()
    entries = []
    for recipe_id, label in LABELS.items():
        recipe_path = source/'filmsim/recipes'/f'{recipe_id}.json'
        recipe = json.loads(recipe_path.read_text(encoding='utf-8'))
        if recipe['id'] != recipe_id:
            raise ValueError(f'Source recipe ID differs: {recipe_path}')
        text = bake_cube(recipe)
        key = 'metol-' + recipe_id
        directory = library.directory(key)
        existing = directory.exists()
        if existing:
            metadata = library.get(key)
            if metadata.get('source_kind') != 'nx500-filmsim-color-bake':
                raise FileExistsError(f'Existing user look uses ID {key}')
            if (directory/'source.cube').read_text(encoding='utf-8') != text and not refresh:
                raise FileExistsError(f'Existing look differs; preserve it and choose a new ID: {key}')
            if not refresh:
                entries.append({'id': key, 'result': 'existing-preserved'})
                continue
            library._atomic_write(directory/'source.cube', text.encode('utf-8'))
            metadata = library.build(key)
        else:
            with tempfile.TemporaryDirectory(prefix='nx300m-filmsim-') as temporary:
                cube = Path(temporary)/f'{recipe_id}.cube'
                cube.write_text(text, encoding='utf-8', newline='\n')
                metadata = library.add_cube(cube, key, label=label, input_space='srgb',
                                            description=recipe.get('description', ''))
        metadata.update({
            'source_kind': 'nx500-filmsim-color-bake',
            'source_path': str(recipe_path),
            'source_repository': REPOSITORY,
            'source_revision': commit,
            'source_recipe_name': recipe['name'],
            'source_algorithm': 'filmsim/make_cube.py matrix, luma saturation, 256-entry curve',
            'source_axis_order': 'R outer, G middle, B inner',
            'master_axis_order': 'B outer, G middle, R inner (standard cube)',
            'scope': 'upstream deterministic color bake only; not the seven-dimensional PW recipe set',
            'excluded_components': ['spatial random grain', 'upstream Pillow CPU image pipeline'],
            'source_grain': recipe.get('grain', {}),
            'input_space_basis': 'source processes rendered RGB; sRGB interpretation, no camera calibration',
            'source_license_note': 'README: research/learning use; no standalone license grant supplied',
        })
        library._save(key, metadata)
        entries.append({'id': key, 'result': 'refreshed' if existing else 'imported', 'native_bytes': (directory/'table.d4lut').stat().st_size})
    return {'repository': REPOSITORY, 'revision': commit, 'entries': entries,
            'scope': 'local source-recipe color adaptation; no grain or camera image verification'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=SOURCE)
    parser.add_argument('--refresh-imported', action='store_true', help='Replace looks created by this importer with the current source color bake')
    args = parser.parse_args()
    result = import_recipes(args.source, refresh=args.refresh_imported)
    output = PROJECT/'outputs/looks/metol-filmsim-import.json'
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
