"""Release checks against the actual shipped DC shapes, textures and attribution."""
import json
from pathlib import Path
import struct
import sys
import unittest

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'server'))
sys.path.insert(0, str(ROOT / 'tools'))
import cdae
import landmarks
import package


class BundledLandmarksTest(unittest.TestCase):
    def test_washington_shaft_keeps_surveyed_width_and_tip_in_both_lods(self):
        shape = cdae.read(ROOT / 'mod/beamng_earth' / landmarks.VFS.lstrip('/') /
                          'washington_monument' / 'compiled.cdae')
        for mesh in shape['meshes']:
            vertices = cdae.mesh_arrays(mesh)['verts']
            tip = vertices[np.abs(vertices[:, 2] - 169.294175) < .002]
            self.assertGreater(len(tip), 0, 'distance decimation must retain the apex')
            np.testing.assert_allclose(tip[:, :2], 0, atol=.002)
            top = vertices[np.abs(vertices[:, 2] - 152.4) < .002]
            self.assertGreaterEqual(len(top), 4)
            np.testing.assert_allclose(np.ptp(top[:, :2], axis=0), [10.5029, 10.5029], atol=.002)
            base = vertices[np.abs(vertices[:, 2]) < .002]
            for coordinate in (0, 1):
                self.assertTrue(np.any(np.abs(base[:, coordinate] - 8.40105) < .002))
                self.assertTrue(np.any(np.abs(base[:, coordinate] + 8.40105) < .002))

    def test_pack_is_complete_and_in_every_mod_package(self):
        rows = landmarks.load_catalog(ROOT / 'mod' / 'beamng_earth')
        self.assertEqual({r['id'] for r in rows}, {'washington_monument', 'lincoln_memorial', 'white_house'})
        packaged = set(package.collect())
        mod_files = set(package.tree_files('mod/beamng_earth'))
        for row in rows:
            for field in ('shape', 'materials'):
                rel = row[field].lstrip('/')
                self.assertIn('mod/beamng_earth/' + rel, packaged)
                self.assertIn(rel, mod_files)

    def test_real_shapes_have_bounded_useful_lods_and_complete_materials(self):
        total_bytes = 0
        names = set()
        for row in landmarks.load_catalog(ROOT / 'mod' / 'beamng_earth'):
            with self.subTest(asset=row['id']):
                root = ROOT / 'mod' / 'beamng_earth' / Path(row['shape'].lstrip('/')).parent
                meta = json.loads((root / 'landmark.json').read_text())
                shape = cdae.read(root / 'compiled.cdae')
                self.assertEqual(len(shape['meshes']), 2)
                self.assertTrue(100 <= meta['lod_pixels'] <= 300)
                self.assertIn('detail' + str(int(meta['lod_pixels'])), shape['names'])
                self.assertIn('detail2', shape['names'])
                self.assertFalse(any('Collision' in name for name in shape['names']))
                arrays = [cdae.mesh_arrays(mesh) for mesh in shape['meshes']]
                triangles = [len(m['indices']) // 3 for m in arrays]
                self.assertTrue(0 < triangles[1] < triangles[0] <= 40000)
                self.assertLessEqual(triangles[1], 5000)
                for mesh in arrays:
                    self.assertLessEqual(len(mesh['verts']), 65000)
                    self.assertTrue(np.isfinite(mesh['verts']).all())
                    self.assertTrue(np.isfinite(mesh['norms']).all())
                    self.assertLess(int(mesh['indices'].max()), len(mesh['verts']))
                self.assertAlmostEqual(float(np.ptp(arrays[0]['verts'][:, 2])), row['height_m'], delta=.02)
                self.assertTrue(meta['transform']['dimensions_source'].startswith('https://'))
                self.assertEqual(len(meta['source_sha256']), 64)
                self.assertIn('creativecommons.org/licenses/by/4.0', meta['attribution']['license_url'])
                for field in ('creator', 'creator_url', 'source_url'):
                    self.assertTrue(meta['attribution'][field])
                self.assertTrue(meta['modified'])
                materials = json.loads((root / 'main.materials.json').read_text())
                self.assertEqual({m['name'] for m in shape['materials']}, set(materials))
                for name, material in materials.items():
                    self.assertNotIn(name, names)
                    names.add(name)
                    self.assertEqual(name, material['mapTo'])
                    for stage in material['Stages']:
                        for field, path in stage.items():
                            if not field.endswith('Map'):
                                continue
                            self.assertTrue(path.startswith(landmarks.VFS + row['id'] + '/textures/'))
                            # BeamNG resolves a PNG material path to its precompiled DDS sibling.
                            source_path = ROOT / 'mod' / 'beamng_earth' / path.lstrip('/')
                            self.assertFalse(source_path.exists(), 'ship cooked textures, not first-load PNG work')
                            file = source_path.with_suffix('.dds')
                            payload = file.read_bytes()
                            self.assertEqual(payload[:4], b'DDS ')
                            self.assertEqual(payload[84:88], b'DX10')
                            expected_format = 99 if field == 'baseColorMap' else 83 if field == 'normalMap' else 80
                            self.assertEqual(struct.unpack_from('<I', payload, 128)[0], expected_format)
                            with Image.open(file) as image:
                                for dimension in image.size:
                                    self.assertTrue(16 <= dimension <= 2048 and not dimension & (dimension - 1))
                                self.assertEqual(struct.unpack_from('<I', payload, 28)[0], max(image.size).bit_length())
                total_bytes += sum(p.stat().st_size for p in root.rglob('*') if p.is_file())
        self.assertLess(total_bytes, 32 * 1024 * 1024, 'keep the first pack small enough to bundle')


if __name__ == '__main__':
    unittest.main()
