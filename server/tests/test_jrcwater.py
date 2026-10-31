"""Bounded supplementary evidence fixtures; no shoreline/level inference."""
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import maprasters
from provider_contract import PreparedStore, write_tile
from jrcwater import JRCWater


class JRCWaterTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def write(self, layer, data, x0=0, **overrides):
        data = np.asarray(data, np.uint8)
        metadata = dict(source_revision='fixture-jrc-v1.5', license='Copernicus terms',
                        acquired_at='2000-01-01T00:00:00Z', source_crs='EPSG:4326',
                        units='percent' if layer == 'occurrence' else 'months', nodata=255,
                        resolution=30, confidence='historical_observations',
                        source_sha256=hashlib.sha256(data.tobytes()).hexdigest(),
                        period='1984-2024' if layer == 'occurrence' else '2024',
                        attribution='Source: EC JRC/Google; Pekel et al. (2016)', fixture=True,
                        source_urls=['https://global-surface-water.appspot.com/download'])
        metadata.update(overrides)
        raster = maprasters.Raster(data, 'EPSG:4326', x0, 2, 1, 1)
        return write_tile(self.root, 'jrc_gsw', layer, raster, data != 255, metadata)

    def test_valid_dry_zero_nodata_and_independent_periods(self):
        self.write('occurrence', [[0, 50, 100, 255]])
        self.write('seasonality', [[0, 12, 255, 4]])
        result = JRCWater(PreparedStore(self.root)).sample(1.5, [.5, 1.5, 2.5, 3.5])
        np.testing.assert_allclose(result.values['occurrence_fraction'], [0, .5, 1, np.nan], equal_nan=True)
        np.testing.assert_allclose(result.values['seasonality_months'], [0, 12, np.nan, 4], equal_nan=True)
        self.assertTrue(result.valid['occurrence_fraction'][0])
        self.assertTrue(result.valid['seasonality_months'][0])
        self.assertFalse(result.valid['occurrence_fraction'][3])
        by_layer = {p['layer_id']: p for p in result.provenance}
        self.assertEqual(by_layer['occurrence']['period'], '1984-2024')
        self.assertEqual(by_layer['seasonality']['period'], '2024')
        self.assertEqual(by_layer['occurrence']['normalization']['units'], 'observation_fraction')
        self.assertEqual(result.quality, 'partial')

    def test_missing_subset_has_no_fabricated_dry_water_level_or_shoreline(self):
        result = JRCWater(PreparedStore(self.root)).sample([0, 1], [1, 2])
        self.assertEqual(result.quality, 'missing')
        self.assertTrue(all(np.isnan(value).all() for value in result.values.values()))
        self.assertTrue(all(not value.any() for value in result.valid.values()))
        self.assertEqual(set(result.values), {'occurrence_fraction', 'seasonality_months'})
        self.assertFalse(hasattr(JRCWater, 'shoreline'))
        self.assertFalse(hasattr(JRCWater, 'water_level'))

    def test_range_and_coverage_gaps_stay_unknown(self):
        self.write('occurrence', [[101, 254, 100]])
        self.write('seasonality', [[13, 254, 12]])
        result = JRCWater(PreparedStore(self.root)).sample(1.5, [.5, 1.5, 2.5, 3.5])
        for mask in result.valid.values():
            np.testing.assert_array_equal(mask, [False, False, True, False])
        self.assertTrue(np.isnan(result.values['occurrence_fraction'][[0, 1, 3]]).all())

    def test_corrupt_payload_is_transient_and_same_snapshot_can_recover(self):
        row = self.write('occurrence', [[100]])
        self.write('seasonality', [[12]])
        path = self.root / row['path']
        original = path.read_bytes()
        adapter = JRCWater(PreparedStore(self.root))
        revision = adapter.revision
        path.write_bytes(b'broken fixture')
        missing = adapter.sample(1.5, .5)
        self.assertEqual(missing.quality, 'transient_fallback')
        self.assertFalse(missing.valid['occurrence_fraction'])
        self.assertTrue(missing.valid['seasonality_months'])
        path.write_bytes(original)
        recovered = adapter.sample(1.5, .5)
        self.assertEqual(adapter.revision, revision)
        self.assertTrue(recovered.valid['occurrence_fraction'])
        self.assertEqual(recovered.values['occurrence_fraction'].item(), 1)
        self.assertEqual(recovered.quality, 'observational_evidence')

    def test_rejects_wrong_units_or_missing_observation_period(self):
        for metadata in ({'units': 'metres'}, {'period': ''}):
            self.write('occurrence', [[0]], **metadata)
            with self.assertRaises(ValueError):
                JRCWater(PreparedStore(self.root))

    def test_actual_selected_tile_metadata_is_checked(self):
        self.write('occurrence', [[0]], x0=0)
        self.write('occurrence', [[0]], x0=2, units='metres')
        adapter = JRCWater(PreparedStore(self.root))
        with self.assertRaisesRegex(ValueError, 'units'):
            adapter.sample(1.5, 2.5)

    def test_scalar_and_broadcast_dont_invent_persistence_threshold(self):
        self.write('occurrence', [[89, 90], [99, 100]])
        self.write('seasonality', [[11, 12], [0, 1]])
        adapter = JRCWater(PreparedStore(self.root))
        scalar = adapter.sample(1.5, .5)
        self.assertEqual(scalar.values['occurrence_fraction'].shape, ())
        self.assertEqual(scalar.values['occurrence_fraction'].item(), .89)
        result = adapter.sample([[1.5], [.5]], [.5, 1.5])
        np.testing.assert_allclose(result.values['occurrence_fraction'], [[.89, .9], [.99, 1]])
        self.assertNotIn('permanent_water', result.values)
        self.assertEqual(result.quality, 'observational_evidence')


if __name__ == '__main__':
    unittest.main()
