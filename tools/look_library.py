"""Folder-based custom LUT API: retain masters, compile D4 data, track color space."""
import json
from pathlib import Path
import re
import shutil

from adobe_profiles import CreativeLook, DcpProfile
from cube_to_d4 import compile_cube, load_cube
from metol_slot import write_cube as decode_metol_slot
from stock_lut import factory_3d_lut

PROJECT = Path(__file__).resolve().parents[1]
LOOKS = PROJECT / 'looks'
COLOR_SPACES = ('srgb', 'linear-srgb', 'linear-prophoto-d50', 'nx300m-ipcs', 'unspecified')

class LookLibrary:
    def __init__(self, root=LOOKS): self.root = Path(root)

    @staticmethod
    def label(value):
        if not value or len(value.encode('utf-8')) > 191 or any(c in value for c in '|\r\n\0'):
            raise ValueError('Look label must be 1-191 UTF-8 bytes without pipe, newline or NUL')
        return value

    def directory(self, key):
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,62}', key): raise ValueError('Look id must be 1-63 lowercase letters, digits, underscore or hyphen, starting with a letter or digit')
        return self.root / key

    def list(self):
        return [self.get(p.parent.name) for p in sorted(self.root.glob('*/look.json'))]

    def get(self, key):
        return json.loads((self.directory(key)/'look.json').read_text(encoding='utf-8'))

    def _compile_tables(self, directory, metadata, cube=None):
        cube = cube or load_cube(directory/'source.cube')
        self._atomic_write(directory/'table.d4lut', compile_cube(cube))
        composed = directory/'table-stock3d.d4lut'
        if metadata['input_space'] == 'srgb':
            self._atomic_write(composed, compile_cube(cube, factory_3d_lut()))
            metadata['native_stock_3d_table'] = composed.name
            metadata['native_composition'] = 'creative LUT after stock still 3D LUT; RGB encoding unconfirmed'
        else:
            composed.unlink(missing_ok=True)
            metadata.pop('native_stock_3d_table', None)
            metadata.pop('native_composition', None)

    def add_cube(self, path, key, *, label=None, input_space='unspecified', description=''):
        path = Path(path)
        cube = load_cube(path)
        label = self.label(label or path.stem)
        directory = self.directory(key)
        if directory.exists(): raise FileExistsError('Look already exists; edit source.cube then run build: '+key)
        if input_space not in COLOR_SPACES: raise ValueError('Unsupported input color space')
        directory.mkdir(parents=True)
        try:
            shutil.copyfile(path, directory/'source.cube')
            metadata = {'id': key, 'label': label or path.stem, 'source_kind': 'cube', 'source_path': str(path.resolve()), 'input_space': input_space, 'output_space': input_space, 'description': description, 'master': 'source.cube', 'native_table': 'table.d4lut', 'native_format': {'grid': 9, 'channels': 3, 'bits': 10, 'bytes': 4380}, 'calibration': {'camera': 'NX300M', 'status': 'unmeasured'}, 'runtime_status': 'compiled-not-loaded'}
            self._compile_tables(directory, metadata, cube)
            self._save(key, metadata)
            return metadata
        except Exception:
            for name in ('source.cube', 'table.d4lut', 'table-stock3d.d4lut', 'table.d4lut.tmp', 'table-stock3d.d4lut.tmp', 'look.json.tmp'):
                (directory/name).unlink(missing_ok=True)
            directory.rmdir()
            raise

    def build(self, key):
        directory = self.directory(key)
        metadata = self.get(key)
        self._compile_tables(directory, metadata)
        metadata['runtime_status'] = 'compiled-not-loaded'
        self._save(key, metadata)
        return metadata

    def preview(self, key, source, output, *, amount=1., max_edge=900):
        from look_preview import render_preview
        return render_preview(self, key, source, output, amount=amount, max_edge=max_edge)

    def add_dcp_look(self, path, key, *, label=None, size=33):
        profile = DcpProfile(path)
        look = CreativeLook(profile)
        label = self.label(label or profile.get(50936, Path(path).stem))
        directory = self.directory(key)
        if directory.exists(): raise FileExistsError('Look already exists: '+key)
        # Generate the master before publishing a library entry.
        directory.mkdir(parents=True)
        try:
            look.write_cube(directory/'source.cube', size)
            metadata = {'id': key, 'label': label or profile.get(50936, Path(path).stem), 'source_kind': 'dcp-creative-look', 'source_path': str(Path(path).resolve()), 'source_camera': profile.get(50708, ''), 'input_space': 'srgb', 'output_space': 'srgb', 'scope': 'creative LookTable only', 'excluded_components': ['camera ColorMatrix/ForwardMatrix', 'illuminant-dependent HueSatMap', 'profile ToneCurve', 'Adobe raw-development process'], 'master': 'source.cube', 'native_table': 'table.d4lut', 'native_format': {'grid': 9, 'channels': 3, 'bits': 10, 'bytes': 4380}, 'calibration': {'camera': 'NX300M', 'status': 'unmeasured'}, 'runtime_status': 'compiled-not-loaded'}
            metadata['source_profile'] = profile.get(50936, Path(path).stem)
            self._compile_tables(directory, metadata)
            self._save(key, metadata)
            return metadata
        except Exception:
            # Remove only our unpublished partial files, preserving user profiles.
            for name in ('source.cube', 'table.d4lut', 'table-stock3d.d4lut', 'table.d4lut.tmp', 'table-stock3d.d4lut.tmp', 'look.json.tmp'):
                (directory/name).unlink(missing_ok=True)
            directory.rmdir()
            raise

    def add_metol_slot(self, path, key, *, label=None, input_space='unspecified'):
        label = self.label(label or Path(path).stem)
        if input_space not in COLOR_SPACES: raise ValueError('Unsupported input color space')
        directory = self.directory(key)
        if directory.exists(): raise FileExistsError('Look already exists: '+key)
        directory.mkdir(parents=True)
        try:
            decode_metol_slot(path, directory/'source.cube')
            metadata = {'id': key, 'label': label or Path(path).stem, 'source_kind': 'metol-17grid-slot', 'source_path': str(Path(path).resolve()), 'input_space': input_space, 'output_space': input_space, 'master': 'source.cube', 'native_table': 'table.d4lut', 'native_format': {'grid': 9, 'channels': 3, 'bits': 10, 'bytes': 4380}, 'calibration': {'camera': 'NX300M', 'status': 'unmeasured'}, 'runtime_status': 'compiled-not-loaded'}
            self._compile_tables(directory, metadata)
            self._save(key, metadata)
            return metadata
        except Exception:
            for name in ('source.cube', 'table.d4lut', 'table-stock3d.d4lut', 'table.d4lut.tmp', 'table-stock3d.d4lut.tmp', 'look.json.tmp'): (directory/name).unlink(missing_ok=True)
            directory.rmdir(); raise

    def _save(self, key, metadata):
        self._atomic_write(self.directory(key)/'look.json', (json.dumps(metadata, ensure_ascii=False, indent=2)+'\n').encode('utf-8'))

    @staticmethod
    def _atomic_write(path, data):
        pending = path.with_name(path.name+'.tmp')
        pending.write_bytes(data)
        pending.replace(path)
