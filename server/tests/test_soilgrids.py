"""Tiny prepared fixtures; no providers, engine or soil/friction calibration."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import maprasters
from provider_contract import PreparedStore, write_tile
from soilgrids import SoilGrids


class SoilGridsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def write(self, layer, data, valid=None, x0=0, **overrides):
        data = np.asarray(data, np.int16)
        metadata = dict(source_revision='fixture-soilgrids2-0-5cm-mean-v1',
                        license='CC-BY-4.0', acquired_at='2000-01-01T00:00:00Z',
                        source_crs='EPSG:4326', units='cm3/dm3' if layer == 'cfvo' else 'g/kg',
                        nodata=-32768, resolution=250, confidence='model_prediction',
                        source_sha256=hashlib.sha256(data.tobytes()).hexdigest(),
                        depth='0-5cm', statistic='mean', fixture=True,
                        source_urls=['https://docs.isric.org/globaldata/soilgrids/SoilGrids_faqs_01.html'])
        metadata.update(overrides)
        raster = maprasters.Raster(data, 'EPSG:4326', x0, 2, 1, 1)
        return write_tile(self.root, 'soilgrids', layer, raster,
                          data != -32768 if valid is None else np.asarray(valid, bool), metadata)

    def test_units_and_independent_missing_properties(self):
        self.write('sand', [[0, 500, 1000, -32768]])
        self.write('clay', [[200, -32768, 800, 50]])
        self.write('cfvo', [[100, 250, -32768, 0]])
        result = SoilGrids(PreparedStore(self.root)).sample(1.5, [.5, 1.5, 2.5, 3.5])
        np.testing.assert_allclose(result.values['sand_fraction'], [0, .5, 1, np.nan], equal_nan=True)
        np.testing.assert_allclose(result.values['clay_fraction'], [.2, np.nan, .8, .05], equal_nan=True)
        np.testing.assert_allclose(result.values['cfvo_fraction'], [.1, .25, np.nan, 0], equal_nan=True)
        self.assertTrue(result.valid['sand_fraction'][0], 'zero soil content is valid')
        self.assertEqual(result.quality, 'partial')
        self.assertEqual(result.provenance[0]['depth'], '0-5cm')
        by_layer = {p['layer_id']: p for p in result.provenance}
        self.assertEqual(by_layer['sand']['units'], 'g/kg')
        self.assertEqual(by_layer['sand']['normalization']['units'], 'mass_fraction')
        self.assertEqual(by_layer['cfvo']['normalization']['units'], 'volume_fraction')
        self.assertEqual(by_layer['cfvo']['normalization']['scale'], .001)
        self.assertNotIn('silt_fraction', result.values)
        self.assertFalse(any('friction' in key or 'material' in key for key in result.values))
        self.assertEqual(result.values['sand_fraction'][2] + result.values['clay_fraction'][2], 1.8,
                         'independent predictions are not silently renormalized')

    def test_missing_catalog_and_outside_coverage_are_not_zero(self):
        empty = SoilGrids(PreparedStore(self.root)).sample([1, 2], [.5, 5])
        self.assertEqual(empty.quality, 'missing')
        self.assertTrue(all(np.isnan(value).all() for value in empty.values.values()))
        self.assertTrue(all(not mask.any() for mask in empty.valid.values()))
        self.write('sand', [[500]])
        result = SoilGrids(PreparedStore(self.root)).sample([1.5, 1.5, np.nan], [.5, 1.5, .5])
        np.testing.assert_array_equal(result.valid['sand_fraction'], [True, False, False])
        self.assertTrue(np.isnan(result.values['sand_fraction'][1:]).all())

    def test_invalid_ranges_do_not_become_composition(self):
        self.write('sand', [[-1, 1001, 1000]])
        result = SoilGrids(PreparedStore(self.root)).sample(1.5, [.5, 1.5, 2.5])
        np.testing.assert_array_equal(result.valid['sand_fraction'], [False, False, True])
        np.testing.assert_allclose(result.values['sand_fraction'], [np.nan, np.nan, 1], equal_nan=True)

    def test_corrupt_payload_is_transient_and_same_snapshot_can_recover(self):
        row = self.write('sand', [[500]])
        path = self.root / row['path']
        original = path.read_bytes()
        adapter = SoilGrids(PreparedStore(self.root))
        revision = adapter.revision
        path.write_bytes(b'broken fixture')
        missing = adapter.sample(1.5, .5)
        self.assertEqual(missing.quality, 'transient_fallback')
        self.assertFalse(missing.valid['sand_fraction'])
        path.write_bytes(original)
        recovered = adapter.sample(1.5, .5)
        self.assertEqual(adapter.revision, revision)
        self.assertTrue(recovered.valid['sand_fraction'])
        self.assertEqual(recovered.values['sand_fraction'].item(), .5)
        self.assertEqual(recovered.quality, 'partial')

    def test_rejects_wrong_units_depth_or_statistic(self):
        for field, wrong in [('units', 'percent'), ('depth', '5-15cm'), ('statistic', 'Q0.95')]:
            with self.subTest(field=field):
                self.write('sand', [[500]], **{field: wrong})
                with self.assertRaises(ValueError):
                    SoilGrids(PreparedStore(self.root))

    def test_actual_selected_tile_metadata_is_checked(self):
        self.write('sand', [[500]], x0=0)
        self.write('sand', [[50]], x0=2, units='percent')
        adapter = SoilGrids(PreparedStore(self.root))
        with self.assertRaisesRegex(ValueError, 'units'):
            adapter.sample(1.5, 2.5)

    def test_broadcast_scalar_and_snapshot_revision(self):
        self.write('sand', [[500, 400], [300, 200]])
        before = SoilGrids(PreparedStore(self.root))
        scalar = before.sample(1.5, .5)
        self.assertEqual(scalar.values['sand_fraction'].shape, ())
        self.assertEqual(scalar.values['sand_fraction'].item(), .5)
        grid = before.sample([[1.5], [.5]], [.5, 1.5])
        np.testing.assert_allclose(grid.values['sand_fraction'], [[.5, .4], [.3, .2]])
        self.write('sand', [[100, 100], [100, 100]])
        after = SoilGrids(PreparedStore(self.root))
        self.assertNotEqual(before.revision, after.revision)
        self.assertEqual(before.sample(1.5, .5).values['sand_fraction'].item(), .5)
        self.assertEqual(after.sample(1.5, .5).values['sand_fraction'].item(), .1)

    @unittest.skipUnless(importlib.util.find_spec('rasterio'), 'optional offline Rasterio preparation dependency')
    def test_cli_native_igh_preparation_preserves_zero_and_nodata(self):
        import rasterio
        from rasterio.transform import from_origin
        from rasterio.warp import transform_bounds
        data = np.tile(np.array([0, 500, -32768, 1000], np.int16), (4, 1))
        native = '+proj=igh +datum=WGS84 +units=m +no_defs'
        source = self.root / 'fixture-native-igh.tif'
        with rasterio.open(source, 'w', driver='GTiff', width=4, height=4, count=1, dtype='int16',
                           crs=native, transform=from_origin(0, 1000, 250, 250), nodata=-32768) as dst:
            dst.write(data, 1)
        bounds = transform_bounds(native, 'EPSG:4326', 0, 0, 1000, 1000)
        catalog = self.root / 'cli-prepared'
        script = Path(__file__).resolve().parents[2] / 'tools/prepare_soilwater.py'
        run = subprocess.run([sys.executable, '-B', str(script), 'soilgrids', 'sand', str(source), str(catalog),
                              '--bounds', *map(str, bounds), '--shape', '4', '4',
                              '--source-revision', 'fixture-native-igh-v1',
                              '--acquired-at', '2000-01-01T00:00:00Z'],
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        row = json.loads(run.stdout)
        self.assertEqual(row['metadata']['source_sha256'], hashlib.sha256(source.read_bytes()).hexdigest())
        self.assertEqual(row['metadata']['nodata'], -32768)
        self.assertNotEqual(row['metadata']['source_crs'], 'EPSG:152160')
        self.assertEqual(row['metadata']['source_pixel_size'], [250, 250])
        west, south, east, north = bounds
        lat = north - (np.arange(4)[:, None] + .5) * (north-south) / 4
        lon = west + (np.arange(4)[None, :] + .5) * (east-west) / 4
        result = SoilGrids(PreparedStore(catalog)).sample(lat, lon)
        np.testing.assert_array_equal(result.valid['sand_fraction'], data != -32768)
        np.testing.assert_allclose(result.values['sand_fraction'],
                                   np.where(data != -32768, data / 1000, np.nan), equal_nan=True)


if __name__ == '__main__':
    unittest.main()
