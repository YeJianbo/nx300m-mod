"""Folder-based custom LUT API: retain masters, compile D4 data, track color space."""
import hashlib
import errno
import json
import os
from pathlib import Path
import re
import shutil
import time
import uuid

import numpy as np
from PIL import Image

from adobe_profiles import CreativeLook, DcpProfile
from cube_to_d4 import DEFAULT_METHOD, METHODS, compile_cube, load_cube
from metol_slot import write_cube as decode_metol_slot
from nx300m_lut_core import (BLEND_ALGORITHM, BLEND_GRID, BLEND_WORKING_SPACE,
                             apply_lut, blend_luts, cube_bytes, identity_lut,
                             load_image_array, parse_lut_file)
from stock_lut import factory_3d_lut

PROJECT = Path(__file__).resolve().parents[1]
LOOKS = PROJECT / 'looks'
COLOR_SPACES = ('srgb', 'linear-srgb', 'linear-prophoto-d50', 'nx300m-ipcs', 'unspecified')
# Keep the same everyday order in the CLI, simulator and SD catalogues.
PREFERRED_LOOKS = (
    'nx300-hard-mono', 'ricoh-negative-exact', 'ricoh-positive-exact',
    'nx300-fuji-classic-chrome', 'nx300-fuji-astia', 'nx300-fuji-eterna',
    'nx300-fuji-classic-negative', 'nx300-fuji-nostalgic-neg',
)

LOCK_TIMEOUT_SECONDS = 120.0


class LookLock:
    """Kernel-held same-look lock, released on close or process exit.

    Keep a stable inode outside the look directory. Age-based unlinking can
    steal a slow compiler's lock, and unlinking on release can split waiters
    between different inodes. The small lock files therefore stay in .locks.
    """

    def __init__(self, directory: Path):
        directory = Path(directory)
        self.path = directory.parent/'.locks'/(directory.name+'.lock')
        self.handle = None

    def __enter__(self):
        deadline = time.monotonic()+LOCK_TIMEOUT_SECONDS
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open('a+b')
        try:
            # Windows byte-range locking requires a byte to lock.
            if not self.path.stat().st_size:
                self.handle.write(b'\0'); self.handle.flush()
            while True:
                try:
                    self.handle.seek(0)
                    if os.name == 'nt':
                        import msvcrt
                        msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX|fcntl.LOCK_NB)
                    return self
                except OSError as error:
                    if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f'Look build lock busy beyond {LOCK_TIMEOUT_SECONDS:.0f}s: {self.path}') from error
                    time.sleep(0.05)
        except BaseException:
            self.handle.close(); self.handle = None
            raise

    def __exit__(self, *exc):
        if self.handle is not None:
            self.handle.close(); self.handle = None
        return False

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
        priority = {key: index for index, key in enumerate(PREFERRED_LOOKS)}
        paths = sorted(self.root.glob('*/look.json'),
                       key=lambda p: (priority.get(p.parent.name, len(priority)), p.parent.name))
        return [self.get(p.parent.name) for p in paths]

    def get(self, key):
        return json.loads((self.directory(key)/'look.json').read_text(encoding='utf-8'))

    def _lock(self, key):
        directory = self.directory(key)
        if not (directory/'look.json').is_file():
            raise FileNotFoundError(f'Look does not exist: {key}')
        return LookLock(directory)

    def rename(self, key, label):
        with self._lock(key):
            metadata = self.get(key)
            metadata['label'] = self.label(label)
            self._save(key, metadata)
            return metadata

    @staticmethod
    def _compiler_inputs(directory, metadata, method):
        if method not in METHODS:
            raise ValueError('Unknown compiler method: '+method)
        tools = Path(__file__).resolve().parent
        names = ['cube_to_d4.py', 'look_library.py']
        if method != 'legacy':
            names.append('lut_resample.py')
        if method == 'fit-integral':
            names.append('lut_integral.py')
        revision = hashlib.sha256(b''.join((tools/name).read_bytes() for name in names)).hexdigest()
        result = dict(method=method, revision=revision,
                      source_sha256=hashlib.sha256((directory/'source.cube').read_bytes()).hexdigest())
        if metadata['input_space'] == 'srgb':
            import struct
            result['stock_sha256'] = hashlib.sha256(struct.pack('<2190H', *factory_3d_lut())).hexdigest()
        return result

    def _compile_tables(self, directory, metadata, cube=None, *, method=DEFAULT_METHOD):
        """Compile both runtime tables and publish them as one unit.

        Callers must hold the look lock. All tables are compiled into memory
        first, staged in unique temp files, then swapped in; a failure restores
        the previous bytes so the library never keeps a mixed pair."""
        inputs = self._compiler_inputs(directory, metadata, method)
        cube = cube or load_cube(directory/'source.cube')
        tables = {'table.d4lut': compile_cube(cube, method=method)}
        remove = []
        composed = directory/'table-stock3d.d4lut'
        if metadata['input_space'] == 'srgb':
            tables[composed.name] = compile_cube(cube, factory_3d_lut(), method=method)
            metadata['native_stock_3d_table'] = composed.name
            metadata['native_composition'] = 'creative LUT after stock still 3D LUT; RGB encoding unconfirmed'
        else:
            remove.append(composed.name)
            metadata.pop('native_stock_3d_table', None)
            metadata.pop('native_composition', None)
        # Finish both fits before publishing either runtime input. Neither the
        # camera nor a packager needs to run the offline optimizer.
        metadata['compiler'] = dict(inputs,
            model='RGB-code trilinear; native encoding/interpolation unverified',
            tables={name: hashlib.sha256(data).hexdigest() for name, data in tables.items()})
        # Metadata is the consumer's commit record. Include it in the same
        # rollback plan, last, so a failed metadata write restores both tables.
        tables['look.json'] = (json.dumps(metadata, ensure_ascii=False, indent=2)+'\n').encode('utf-8')
        self._publish(directory, tables, remove)

    def _publish(self, directory, tables, remove):
        """Swap one consistent set of runtime files in; restore on any failure."""
        plan = {name: data for name, data in tables.items() if name != 'look.json'}
        for name in remove:
            plan[name] = None
        if 'look.json' in tables:
            plan['look.json'] = tables['look.json']
        previous = {}
        for name in plan:
            path = directory/name
            previous[name] = path.read_bytes() if path.exists() else None
        pending = {}
        try:
            for name, data in plan.items():
                if data is None:
                    if previous[name] is not None:
                        pending[name] = None
                else:
                    pending[name] = self._temp_write(directory/name, data)
            # Individual renames are atomic; the group is not. The consumer's
            # hash check rejects an intermediate set, including after a crash.
            for name, pending_path in pending.items():
                if pending_path is None:
                    (directory/name).unlink()
                else:
                    os.replace(pending_path, directory/name)
        except BaseException:
            for pending_path in pending.values():
                if pending_path is not None:
                    Path(pending_path).unlink(missing_ok=True)
            for name, data in previous.items():
                path = directory/name
                if data is None:
                    path.unlink(missing_ok=True)
                else:
                    self._atomic_write(path, data)
            raise

    def _compiled_fresh(self, directory, metadata, method=DEFAULT_METHOD):
        expected = self._compiler_inputs(directory, metadata, method)
        compiler = metadata.get('compiler', {})
        names = {'table.d4lut'}
        if metadata['input_space'] == 'srgb':
            names.add('table-stock3d.d4lut')
        return (all(compiler.get(k) == v for k, v in expected.items())
                and set(compiler.get('tables', {})) == names
                and all((directory/name).is_file()
                        and hashlib.sha256((directory/name).read_bytes()).hexdigest() == compiler['tables'][name]
                        for name in names))

    def ensure_compiled(self, key, *, method=DEFAULT_METHOD):
        directory = self.directory(key)
        metadata = self.get(key)
        # Native diagnostic tables contain hardware codes, not RGB masters.
        if metadata['input_space'] == 'nx300m-ipcs':
            return metadata
        with self._lock(key):
            # Another process may have published this look while we waited.
            metadata = self.get(key)
            if metadata['input_space'] == 'nx300m-ipcs':
                return metadata
            if self._compiled_fresh(directory, metadata, method):
                return metadata
            return self._build_locked(key, method=method)

    @staticmethod
    def _discard_partial(directory, created):
        """Remove only what this failed add created, plus any staged temps."""
        for name in created:
            (directory/name).unlink(missing_ok=True)
        for path in directory.glob('*.tmp'):
            path.unlink(missing_ok=True)
        for path in sorted((item for item in directory.rglob('*') if item.is_dir()),
                           key=lambda item: len(item.parts), reverse=True):
            try:
                path.rmdir()
            except OSError:
                pass
        try:
            if not any(directory.iterdir()):
                directory.rmdir()
        except OSError:
            pass

    def add_cube(self, path, key, *, label=None, input_space='unspecified', description='', method=DEFAULT_METHOD):
        path = Path(path)
        cube = load_cube(path)
        label = self.label(label or path.stem)
        directory = self.directory(key)
        if directory.exists(): raise FileExistsError('Look already exists; edit source.cube then run build: '+key)
        if input_space not in COLOR_SPACES: raise ValueError('Unsupported input color space')
        directory.mkdir(parents=True)
        created = []
        try:
            with LookLock(directory):
                if (directory/'look.json').is_file() or (directory/'source.cube').is_file():
                    raise FileExistsError('Look already exists; edit source.cube then run build: '+key)
                shutil.copyfile(path, directory/'source.cube')
                created.append('source.cube')
                metadata = {'id': key, 'label': label or path.stem, 'source_kind': 'cube', 'source_path': str(path.resolve()), 'input_space': input_space, 'output_space': input_space, 'description': description, 'master': 'source.cube', 'native_table': 'table.d4lut', 'native_format': {'grid': 9, 'channels': 3, 'bits': 10, 'bytes': 4380}, 'calibration': {'camera': 'NX300M', 'status': 'unmeasured'}, 'runtime_status': 'compiled-not-loaded'}
                self._compile_tables(directory, metadata, cube, method=method)
                return metadata
        except Exception:
            self._discard_partial(directory, created)
            raise

    def add_blend(self, source_a, source_b, key, *, label, amount_b,
                  source_a_id='original', source_b_id='original',
                  reference_image=None, description='', method=DEFAULT_METHOD):
        """Create a versioned two-source sRGB blend and compile both native tables."""
        if key in {item['id'] for item in self.list()}:
            raise FileExistsError('Look already exists: ' + key)
        label = self.label(str(label).strip())
        amount_b = float(amount_b)
        if not 0.0 <= amount_b <= 1.0:
            raise ValueError('Blend ratio must be between 0 and 1')
        a = identity_lut(BLEND_GRID, title='Original') if source_a is None else parse_lut_file(source_a).lut
        b = identity_lut(BLEND_GRID, title='Original') if source_b is None else parse_lut_file(source_b).lut
        a_cube = cube_bytes(a, title=a.title or str(source_a_id))
        b_cube = cube_bytes(b, title=b.title or str(source_b_id))
        blended = blend_luts(a, b, amount_b, size=BLEND_GRID, title=label)
        output_cube = cube_bytes(blended, title=label)
        source_a_hash = hashlib.sha256(a_cube).hexdigest()
        source_b_hash = hashlib.sha256(b_cube).hexdigest()
        output_hash = hashlib.sha256(output_cube).hexdigest()
        recipe = {
            'schema': 1,
            'mode': 'linear',
            'algorithm': BLEND_ALGORITHM,
            'working_space': BLEND_WORKING_SPACE,
            'grid': BLEND_GRID,
            'amount_b': round(amount_b, 8),
            'a': {
                'id': str(source_a_id), 'label': '原图' if source_a is None else (a.title or str(source_a_id)),
                'original': source_a is None, 'content_sha256': source_a_hash,
                'snapshot': 'sources/a.cube', 'source_grid': a.size,
            },
            'b': {
                'id': str(source_b_id), 'label': '原图' if source_b is None else (b.title or str(source_b_id)),
                'original': source_b is None, 'content_sha256': source_b_hash,
                'snapshot': 'sources/b.cube', 'source_grid': b.size,
            },
            'output_sha256': output_hash,
            'reference': 'reference.jpg',
        }
        reference = reference_image if reference_image is not None else None
        original = load_image_array(reference, max_edge=720)
        processed = apply_lut(original, blended)
        divider = np.zeros((original.shape[0], 8, 3), dtype=np.float32)
        comparison = np.concatenate((original, divider, processed), axis=1)
        reference_bytes = None
        import io
        buffer = io.BytesIO()
        Image.fromarray(np.clip(comparison * 255.0 + 0.5, 0, 255).astype(np.uint8)).save(
            buffer, format='JPEG', quality=88, optimize=True)
        reference_bytes = buffer.getvalue()
        directory = self.directory(key)
        if directory.exists():
            raise FileExistsError('Look already exists; edit source.cube then run build: ' + key)
        directory.mkdir(parents=True)
        created = []
        try:
            with LookLock(directory):
                if (directory/'look.json').is_file() or (directory/'source.cube').is_file():
                    raise FileExistsError('Look already exists: ' + key)
                for name, data in (
                        ('source.cube', output_cube),
                        ('blend.json', (json.dumps(recipe, ensure_ascii=False, indent=2) + '\n').encode('utf-8')),
                        ('sources/a.cube', a_cube),
                        ('sources/b.cube', b_cube),
                        ('reference.jpg', reference_bytes)):
                    path = directory/name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
                    created.append(name)
                metadata = {
                    'id': key, 'label': label, 'source_kind': 'blend-linear-srgb',
                    'description': description, 'input_space': 'srgb', 'output_space': 'srgb',
                    'source_grid': BLEND_GRID, 'source_sha256': output_hash,
                    'blend_recipe': recipe, 'recipe': 'blend.json',
                    'master': 'source.cube', 'native_table': 'table.d4lut',
                    'native_format': {'grid': 9, 'channels': 3, 'bits': 10, 'bytes': 4380},
                    'calibration': {'camera': 'NX300M', 'status': 'unmeasured'},
                    'runtime_status': 'compiled-not-loaded',
                }
                self._compile_tables(directory, metadata, load_cube(directory/'source.cube'), method=method)
                return metadata
        except Exception:
            self._discard_partial(directory, created)
            raise

    def build(self, key, *, method=DEFAULT_METHOD):
        self.get(key)  # Fail on a missing look before taking the lock.
        with self._lock(key):
            return self._build_locked(key, method=method)

    def _build_locked(self, key, *, method=DEFAULT_METHOD):
        directory = self.directory(key)
        metadata = self.get(key)
        metadata['runtime_status'] = 'compiled-not-loaded'
        self._compile_tables(directory, metadata, method=method)
        return metadata

    def preview(self, key, source, output, *, amount=1., max_edge=900):
        from look_preview import render_preview
        return render_preview(self, key, source, output, amount=amount, max_edge=max_edge)

    def add_dcp_look(self, path, key, *, label=None, size=33, method=DEFAULT_METHOD):
        profile = DcpProfile(path)
        look = CreativeLook(profile)
        label = self.label(label or profile.get(50936, Path(path).stem))
        directory = self.directory(key)
        if directory.exists(): raise FileExistsError('Look already exists: '+key)
        # Generate the master before publishing a library entry.
        directory.mkdir(parents=True)
        created = []
        try:
            with LookLock(directory):
                if (directory/'look.json').is_file() or (directory/'source.cube').is_file():
                    raise FileExistsError('Look already exists: '+key)
                look.write_cube(directory/'source.cube', size)
                created.append('source.cube')
                metadata = {'id': key, 'label': label or profile.get(50936, Path(path).stem), 'source_kind': 'dcp-creative-look', 'source_path': str(Path(path).resolve()), 'source_camera': profile.get(50708, ''), 'input_space': 'srgb', 'output_space': 'srgb', 'scope': 'creative LookTable only', 'excluded_components': ['camera ColorMatrix/ForwardMatrix', 'illuminant-dependent HueSatMap', 'profile ToneCurve', 'Adobe raw-development process'], 'master': 'source.cube', 'native_table': 'table.d4lut', 'native_format': {'grid': 9, 'channels': 3, 'bits': 10, 'bytes': 4380}, 'calibration': {'camera': 'NX300M', 'status': 'unmeasured'}, 'runtime_status': 'compiled-not-loaded'}
                metadata['source_profile'] = profile.get(50936, Path(path).stem)
                self._compile_tables(directory, metadata, method=method)
                return metadata
        except Exception:
            # Remove only our unpublished partial files, preserving user profiles.
            self._discard_partial(directory, created)
            raise

    def add_metol_slot(self, path, key, *, label=None, input_space='unspecified', method=DEFAULT_METHOD):
        label = self.label(label or Path(path).stem)
        if input_space not in COLOR_SPACES: raise ValueError('Unsupported input color space')
        directory = self.directory(key)
        if directory.exists(): raise FileExistsError('Look already exists: '+key)
        directory.mkdir(parents=True)
        created = []
        try:
            with LookLock(directory):
                if (directory/'look.json').is_file() or (directory/'source.cube').is_file():
                    raise FileExistsError('Look already exists: '+key)
                decode_metol_slot(path, directory/'source.cube')
                created.append('source.cube')
                metadata = {'id': key, 'label': label or Path(path).stem, 'source_kind': 'metol-17grid-slot', 'source_path': str(Path(path).resolve()), 'input_space': input_space, 'output_space': input_space, 'master': 'source.cube', 'native_table': 'table.d4lut', 'native_format': {'grid': 9, 'channels': 3, 'bits': 10, 'bytes': 4380}, 'calibration': {'camera': 'NX300M', 'status': 'unmeasured'}, 'runtime_status': 'compiled-not-loaded'}
                self._compile_tables(directory, metadata, method=method)
                return metadata
        except Exception:
            self._discard_partial(directory, created)
            raise

    def _save(self, key, metadata):
        self._atomic_write(self.directory(key)/'look.json', (json.dumps(metadata, ensure_ascii=False, indent=2)+'\n').encode('utf-8'))

    @staticmethod
    def _temp_write(path, data):
        """Stage bytes in a unique temp file next to the target; caller swaps."""
        pending = Path(str(path)+f'.{os.getpid()}.{uuid.uuid4().hex}.tmp')
        try:
            with open(pending, 'wb') as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            pending.unlink(missing_ok=True)
            raise
        return pending

    def _atomic_write(self, path, data):
        pending = self._temp_write(path, data)
        try:
            os.replace(pending, path)
        except BaseException:
            pending.unlink(missing_ok=True)
            raise
