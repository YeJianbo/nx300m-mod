"""Add the four non-Standard Lumix Lab exports to the editable local library.

The camera catalogue does not treat these as Standard-base styles. They are
kept as explicit creative LUTs so a local manager can show, edit, and package
them without losing their original Photo Style metadata.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile

from look_library import LookLibrary
from nx300m_lut_core import cube_bytes, expand_lut_paths


ROOT = Path(__file__).resolve().parents[1]
SOURCES = (
    ('panasonic-lumix-backlight-n', 'Backlight-N', 'NAT', Path(r'./local/private-reference')),
    ('panasonic-lumix-filmlike-v2', 'Filmlike-V2', 'CNEV2', Path(r'./local/private-reference')),
    ('panasonic-lumix-summer-freshness', 'Summer Freshness', 'PORT', Path(r'./local/private-reference')),
    ('panasonic-lumix-txd-grainy-film', 'TXD Grainy Film LUT', 'LCLASN', Path(r'./local/private-reference')),
)


def import_one(library: LookLibrary, key: str, label: str, style: str, archive: Path) -> dict:
    files = expand_lut_paths([archive])
    if len(files) != 1:
        raise ValueError(f'{archive.name} must contain exactly one LUT')
    item = files[0]
    actual_style = item.lut.style or 'UNKNOWN'
    if actual_style != style:
        raise ValueError(f'{item.name}: expected {style}, found {actual_style}')
    source_hash = hashlib.sha256(item.source_bytes).hexdigest()
    directory = library.directory(key)
    if directory.exists():
        metadata = library.get(key)
        if metadata.get('source_sha256') != source_hash:
            raise ValueError(f'{key}: existing source differs')
        if metadata.get('source_style') != style or metadata.get('source_base_style') != style:
            metadata.update(source_style=style, source_base_style=style,
                            source_style_family='nonstandard',
                            local_group='Lumix Lab · 非 Standard')
            library._save(key, metadata)
        return {'id': key, 'status': 'existing', 'style': style}
    with tempfile.TemporaryDirectory(prefix='nx300m-local-lut-', dir=ROOT / 'scratch') as temporary:
        master = Path(temporary) / 'source.cube'
        master.write_bytes(cube_bytes(item.lut, title=label))
        metadata = library.add_cube(
            master, key, label=label, input_space='srgb',
            description=f'LUMIX Lab LUT. Original Photo Style input: {style}.',
            method='fit',
        )
    metadata.pop('source_path', None)
    metadata.update(
        source_kind='lumix-lab-cube',
        source_resource=item.name,
        source_archive=archive.name,
        source_style=style,
        source_base_style=style,
        source_style_family='nonstandard',
        source_style_evidence='cube-header',
        source_sha256=source_hash,
        source_cube_sha256=hashlib.sha256(cube_bytes(item.lut)).hexdigest(),
        source_grid=item.lut.size,
        input_space='srgb',
        output_space='srgb',
        local_group='Lumix Lab · 非 Standard',
        scope='creative LUT normalized as sRGB; the original Photo Style base rendering is not included',
        calibration={'camera': 'NX300M', 'status': 'unmeasured'},
        runtime_status='compiled-not-loaded',
    )
    library._save(key, metadata)
    return {'id': key, 'status': 'imported', 'style': style}


def main() -> None:
    library = LookLibrary()
    result = [import_one(library, key, label, style, archive)
              for key, label, style, archive in SOURCES]
    print('imported:', len(result))
    print('library entries:', len(library.list()))


if __name__ == '__main__':
    main()
