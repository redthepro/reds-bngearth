"""Offline provenance, missing-value and bounded prepared raster invariants."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import maprasters
import provider_contract as pc


def metadata(**extra):
    return dict(source_revision='fixture-v1', license='fixture-only', acquired_at='2026-10-26',
                source_crs='EPSG:4326', units='fixture_units', nodata=-32768,
                resolution={'nominal_m': 250}, confidence='analytic_fixture',
                source_sha256=hashlib.sha256(b'analytic fixture').hexdigest(), **extra)


class PreparedContractTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def put(self, data=None, valid=None, **extra):
        data = np.array([[0, 100], [500, -32768]], np.int16) if data is None else data
        valid = data != -32768 if valid is None else valid
        return pc.write_tile(self.root, 'fixture', 'classes',
                             maprasters.Raster(data, 'EPSG:4326', -1, 1, 1, 1), valid, metadata(**extra))

    def test_zero_is_valid_outside_and_masked_values_are_missing(self):
        self.put()
        result = pc.PreparedStore(self.root).sample('fixture', 'classes', [.5, -.5, .5, np.nan], [-.5, .5, 2, 0])
        np.testing.assert_array_equal(result.valid, [True, False, False, False])
        self.assertEqual(result.values[0], 0)
        self.assertTrue(np.isnan(result.values[1:]).all())
        self.assertEqual(result.provenance[0]['license'], 'fixture-only')

    def test_catalog_adoption_is_explicit_and_changes_revision(self):
        old = pc.PreparedStore(self.root)
        self.put()
        new = pc.PreparedStore(self.root)
        self.assertNotEqual(old.revision, new.revision)
        self.assertFalse(old.sample('fixture', 'classes', .5, -.5).valid)
        self.assertTrue(new.sample('fixture', 'classes', .5, -.5).valid)

    def test_corrupt_payload_fails_closed_and_repair_recovers_same_snapshot(self):
        row = self.put()
        path = self.root / row['path']
        original = path.read_bytes()
        store = pc.PreparedStore(self.root)
        path.write_bytes(b'corrupt')
        self.assertEqual(store.sample('fixture', 'classes', .5, -.5).reason, 'invalid_or_corrupt')
        path.write_bytes(original)
        self.assertTrue(store.sample('fixture', 'classes', .5, -.5).valid)

    def test_catalog_rejects_path_escape_and_wrong_schema(self):
        self.put()
        path = self.root / 'index.json'
        value = json.loads(path.read_text())
        value['tiles'][0]['path'] = '../unrelated.npz'
        path.write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            pc.PreparedStore(self.root)

    def test_no_silent_invalid_mask_crs_or_nonfinite_metadata(self):
        with self.assertRaises(ValueError):
            self.put(valid=np.ones((1, 1), bool))
        with self.assertRaises(ValueError):
            self.put(valid=np.ones((2, 2), np.uint8))
        with self.assertRaises(ValueError):
            self.put(extra_value=float('nan'))
        with self.assertRaises(ValueError):
            pc.write_tile(self.root, 'fixture', 'classes',
                maprasters.Raster(np.ones((1, 1)), 'EPSG:152160', 0, 0, 1, 1), np.ones((1, 1), bool), metadata())

    def test_point_entry_and_retained_byte_limits(self):
        self.put()
        store = pc.PreparedStore(self.root)
        with self.assertRaises(ValueError):
            store.sample('fixture', 'classes', np.zeros(pc.MAX_POINTS + 1), 0)
        with mock.patch.object(pc, 'MAX_TILES', 1):
            with self.assertRaises(ValueError):
                pc.write_tile(self.root, 'other', 'classes',
                    maprasters.Raster(np.ones((1, 1)), 'EPSG:4326', 3, 4, 1, 1), np.ones((1, 1), bool), metadata())
        with mock.patch.object(pc, 'MAX_STORE_BYTES', 1):
            with self.assertRaises(ValueError):
                self.put()

    def test_repeat_identical_preparation_has_identical_catalog(self):
        self.put()
        first = pc.PreparedStore(self.root).revision
        self.put()
        self.assertEqual(first, pc.PreparedStore(self.root).revision)

    def test_preparer_reprojects_igh_and_preserves_valid_zero_and_native_nodata(self):
        import rasterio
        from rasterio.transform import from_origin
        from rasterio.warp import transform_bounds
        path = self.root / 'native.tif'
        crs = '+proj=igh +datum=WGS84 +units=m +no_defs'
        with rasterio.open(path, 'w', driver='GTiff', width=2, height=2, count=1,
                           dtype='int16', crs=crs, transform=from_origin(-500, 500, 250, 250), nodata=-32768) as dst:
            dst.write(np.array([[0, 500], [300, -32768]], np.int16), 1)
        with rasterio.open(path) as src:
            bounds = transform_bounds(src.crs, 'EPSG:4326', *src.bounds)
        row = pc.prepare_raster(path, self.root / 'prepared', 'fixture', 'classes', bounds, (2, 2), metadata())
        self.assertEqual(row['metadata']['native_nodata'], -32768)
        result = pc.PreparedStore(self.root / 'prepared').sample('fixture', 'classes',
            bounds[3] - (bounds[3]-bounds[1]) / 4, bounds[0] + (bounds[2]-bounds[0]) / 4)
        self.assertTrue(result.valid)
        self.assertEqual(result.values, 0)

    def test_preparer_normalizes_wrapped_longitudes_without_moving_pixels(self):
        import rasterio
        from rasterio.transform import from_origin
        path = self.root / 'wrapped.tif'
        with rasterio.open(path, 'w', driver='GTiff', width=2, height=2, count=1, dtype='int16',
                           crs='EPSG:4326', transform=from_origin(179.9, 1, .1, .5), nodata=-32768) as dst:
            dst.write(np.array([[11, 71], [61, -32768]], np.int16), 1)
        row = pc.prepare_raster(path, self.root / 'prepared', 'fixture', 'classes', (-180, 0, -179.9, 1), (2, 1), metadata())
        self.assertEqual(row['metadata']['source_longitude_shift_degrees'], -360)
        sample = pc.PreparedStore(self.root / 'prepared').sample('fixture', 'classes', [.75, .25], [-179.95, -179.95])
        np.testing.assert_array_equal(sample.valid, [True, False])
        self.assertEqual(sample.values[0], 71)


if __name__ == '__main__':
    unittest.main()
