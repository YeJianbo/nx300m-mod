#!/usr/bin/env python3
"""Offline acceptance checks for versioned two-LUT sRGB blending."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import unittest

import numpy as np

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))

from look_library import LookLibrary  # noqa: E402
from nx300m_lut_core import Lut3D, blend_luts, cube_bytes, parse_lut_file, resample_lut  # noqa: E402
from stock_lut import factory_3d_lut  # noqa: E402


def test_lut(size: int = 9) -> Lut3D:
    values = np.linspace(0.0, 1.0, size, dtype=np.float32)
    r = values[:, None, None]
    g = values[None, :, None]
    b = values[None, None, :]
    r, g, b = np.broadcast_arrays(r, g, b)
    table = np.stack((
        0.92 * r + 0.08 * b,
        0.85 * g + 0.10 * r + 0.05 * b,
        0.75 * b + 0.25 * g,
    ), axis=-1).astype(np.float32)
    return Lut3D(size, table, 'A', 'STD', 'cube')


class LutBlendTests(unittest.TestCase):
    def setUp(self):
        self.a = test_lut(9)
        b_table = np.clip(1.0 - self.a.table[::-1, :, :, :] * 0.85 + 0.03, 0.0, 1.0)
        self.b = Lut3D(9, b_table.astype(np.float32), 'B', 'STD', 'cube')

    def test_boundaries_match_resampled_sources(self):
        left = blend_luts(self.a, self.b, 0.0)
        right = blend_luts(self.a, self.b, 1.0)
        np.testing.assert_allclose(left.table, resample_lut(self.a).table, atol=1e-7)
        np.testing.assert_allclose(right.table, resample_lut(self.b).table, atol=1e-7)

    def test_swap_and_invert_ratio_are_equivalent(self):
        first = blend_luts(self.a, self.b, 0.27)
        second = blend_luts(self.b, self.a, 0.73)
        np.testing.assert_allclose(first.table, second.table, atol=1e-7)

    def test_same_source_is_identity_at_every_ratio(self):
        expected = resample_lut(self.a).table
        for ratio in (0.0, 0.35, 0.5, 1.0):
            actual = blend_luts(self.a, self.a, ratio).table
            np.testing.assert_allclose(actual, expected, atol=1e-7)

    def test_library_persists_recipe_snapshots_and_camera_tables(self):
        with tempfile.TemporaryDirectory(prefix='nx-lut-blend-test-') as temporary:
            root = Path(temporary)
            a_path = root / 'a.cube'
            b_path = root / 'b.cube'
            a_path.write_bytes(cube_bytes(self.a, title='A'))
            b_path.write_bytes(cube_bytes(self.b, title='B'))
            library = LookLibrary(root=root / 'looks')
            metadata = library.add_blend(a_path, b_path, 'blend-test', label='测试混合',
                                         amount_b=0.3, source_a_id='lut-a', source_b_id='lut-b')
            directory = library.directory('blend-test')
            recipe = json.loads((directory / 'blend.json').read_text(encoding='utf-8'))
            self.assertEqual(recipe['algorithm'], 'linear-srgb-output-v1')
            self.assertAlmostEqual(recipe['amount_b'], 0.3)
            self.assertEqual(recipe['a']['id'], 'lut-a')
            self.assertEqual(recipe['b']['id'], 'lut-b')
            self.assertEqual(metadata['source_kind'], 'blend-linear-srgb')
            for name in ('source.cube', 'sources/a.cube', 'sources/b.cube',
                         'reference.jpg', 'table.d4lut', 'table-stock3d.d4lut'):
                self.assertTrue((directory / name).is_file(), name)
            saved = parse_lut_file(directory / 'source.cube').lut
            expected = blend_luts(self.a, self.b, 0.3)
            np.testing.assert_allclose(saved.table, expected.table, atol=1e-6)
            self.assertEqual(saved.size, 33)
            self.assertEqual(len((directory / 'table.d4lut').read_bytes()), 4380)
            self.assertEqual(len((directory / 'table-stock3d.d4lut').read_bytes()), 4380)

    def test_noncanonical_domain_is_rejected(self):
        shifted = Lut3D(self.a.size, self.a.table, 'shifted', 'STD', 'cube',
                        domain_min=(0.1, 0.0, 0.0), domain_max=(1.0, 1.0, 1.0))
        with self.assertRaisesRegex(ValueError, '0..1 sRGB'):
            blend_luts(shifted, self.b, 0.5)

    def test_card_blend_rebuilds_from_snapshots_after_sources_disappear(self):
        import nx300m_installer as installer
        with tempfile.TemporaryDirectory(prefix='nx-card-blend-test-') as temporary:
            root = Path(temporary)
            card = root / 'card'
            module = card / 'nx300m-mod'
            looks = module / 'looks'
            app = root / 'app'
            app.mkdir()
            local = root / 'lut-library'
            (local / 'LUTs' / '测试').mkdir(parents=True)
            (local.parent / 'looks').mkdir()
            (local / 'catalog.json').write_text(json.dumps({
                'schema': 1, 'groups': [], 'entries': [],
            }), encoding='utf-8')
            (local.parent / 'looks' / 'catalog.json').write_text(json.dumps({
                'version': 1, 'groups': [],
            }), encoding='utf-8')
            looks.mkdir(parents=True)
            (module / '.state').mkdir()
            for key, lut in (('source-a', self.a), ('source-b', self.b)):
                directory = looks / key
                directory.mkdir()
                (directory / 'source.cube').write_bytes(cube_bytes(lut, title=key))
                metadata = {
                    'id': key, 'label': key, 'input_space': 'srgb', 'output_space': 'srgb',
                    'source_grid': lut.size, 'source_kind': 'test-srgb',
                }
                (directory / 'look.json').write_text(json.dumps(metadata), encoding='utf-8')
            baseline = looks / 'nx300-adjust-base'
            baseline.mkdir()
            (baseline / 'table-stock3d.d4lut').write_bytes(
                struct.pack('<2190H', *factory_3d_lut()))
            (module / 'looks.txt').write_text(
                'source-a|A|0|0|0|0|0|0|0\n'
                'source-b|B|0|0|0|0|0|0|0\n'
                'nx300-adjust-base|原图微调|0|0|0|0|0|0|0\n',
                encoding='utf-8')
            identity = {'filesystem': 'EXFAT', 'serial': 'TEST', 'label': 'TEST'}
            old_base, old_volume = installer.BASE, installer.volume
            old_local = os.environ.get('NX300M_LUT_LIBRARY')
            installer.BASE = app
            installer.volume = lambda _path: identity
            os.environ['NX300M_LUT_LIBRARY'] = str(local)
            try:
                created = installer.create_blended_look(
                    card, identity, 'source-a', 'source-b', 0.64, '持久混合', None)
                key = created['id']
                directory = looks / key
                self.assertEqual(created['group']['label'], '自制')
                local_result = created['local_repository']
                self.assertIn('path', local_result)
                self.assertTrue(Path(local_result['path']).is_file())
                card_metadata = json.loads((directory / 'look.json').read_text(encoding='utf-8'))
                self.assertEqual(card_metadata['compiler']['method'], 'round')
                self.assertEqual(len((directory / 'table.d4lut').read_bytes()), 4380)
                self.assertEqual(len((directory / 'table-stock3d.d4lut').read_bytes()), 4380)
                first = (directory / 'source.cube').read_bytes()
                shutil.rmtree(looks / 'source-a')
                shutil.rmtree(looks / 'source-b')
                updated = installer.update_blended_look(
                    card, identity, key, 0.35, '持久混合 · 已调', None)
                self.assertEqual(updated['label'], '持久混合 · 已调')
                self.assertNotEqual(first, (directory / 'source.cube').read_bytes())
                recipe = json.loads((directory / 'blend.json').read_text(encoding='utf-8'))
                self.assertAlmostEqual(recipe['amount_b'], 0.35)
                self.assertEqual(recipe['a']['id'], 'source-a')
                self.assertEqual(parse_lut_file(directory / 'source.cube').lut.size, 33)
                local_catalog = json.loads((local / 'catalog.json').read_text(encoding='utf-8'))
                local_entry = next(item for item in local_catalog['entries'] if item['id'] == key)
                self.assertEqual(local_entry['label'], '持久混合 · 已调')
                self.assertTrue((local / local_entry['file']).is_file())
            finally:
                installer.BASE = old_base
                installer.volume = old_volume
                if old_local is None:
                    os.environ.pop('NX300M_LUT_LIBRARY', None)
                else:
                    os.environ['NX300M_LUT_LIBRARY'] = old_local


if __name__ == '__main__':
    unittest.main(verbosity=2)
