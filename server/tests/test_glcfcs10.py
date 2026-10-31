"""Categorical vegetation semantics and actual prepared-store fallback, offline."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import glcfcs10 as glc
import maprasters
from provider_contract import PreparedStore, Sample, write_tile


def metadata():
    return dict(source_revision=glc.SOURCE_REVISION, license=glc.LICENSE,
                acquired_at="2026-10-26T00:00:00Z", source_crs="EPSG:4326",
                units="categorical_class", nodata=None, resolution={"nominal_m": 10},
                confidence="synthetic_fixture", source_sha256=hashlib.sha256(b"synthetic-glc-fixture").hexdigest())


class ClassificationTest(unittest.TestCase):
    def test_all_publisher_legend_values_and_no_extra_guessed_classes(self):
        fixture = json.loads((Path(__file__).parent / "fixtures/globaldata/glcfcs10/legend.json").read_text())
        codes, names = fixture["native_classes"], fixture["functional_names"]
        functional, valid = glc.decode(np.asarray(codes, np.float64).reshape(5, 6))
        self.assertTrue(valid.all())
        self.assertEqual([glc.FUNCTIONAL_TYPES[int(x)] for x in functional.flat], names)
        self.assertEqual(sorted(glc.CLASS_MAP), sorted(codes))
        self.assertEqual(fixture["publisher_file_sha256"], glc.LEGEND_SHA256)
        invalid = np.asarray([0, 10, 50, 120, 152, 153, 160, 170, 180, 190, 255, 999])
        out, known = glc.decode(invalid)
        self.assertFalse(known.any())
        self.assertTrue((out == glc.UNKNOWN).all())

    def test_no_unsigned_wrap_or_float_truncation(self):
        raw = np.asarray([-65, 447, 191 + 2**32, 191.5, np.inf, -np.inf, np.nan])
        functional, valid = glc.decode(raw)
        self.assertFalse(valid.any())
        self.assertTrue((functional == glc.UNKNOWN).all())

    def test_validity_mask_and_scalar_shape(self):
        scalar, valid = glc.decode(71)
        self.assertEqual(scalar.shape, ())
        self.assertEqual(int(scalar), glc.NEEDLELEAF_EVERGREEN)
        self.assertTrue(bool(valid))
        decoded, valid = glc.decode([51, 71], [True, False])
        np.testing.assert_array_equal(decoded, [glc.BROADLEAF_EVERGREEN, glc.UNKNOWN])
        np.testing.assert_array_equal(valid, [True, False])
        with self.assertRaises(TypeError): glc.decode([51], [1])
        for value in (["51"], [True], [None], [51j]):
            with self.assertRaises(TypeError): glc.decode(value)

    def test_worldcover_fallback_never_infers_leaf_type(self):
        codes = np.asarray([10, 20, 30, 40, 50, 60, 70, 80, 90, 95, 100, 0])
        values, valid = glc.worldcover_types(codes)
        self.assertEqual(int(values[0]), glc.TREE_UNSPECIFIED)
        self.assertNotIn(int(values[0]), glc.GROUP_HINTS)
        self.assertEqual(int(values[1]), glc.SHRUB_UNSPECIFIED)
        self.assertEqual(int(values[3]), glc.CROPLAND_UNSPECIFIED)
        self.assertTrue(valid[:-1].all())
        self.assertFalse(valid[-1])

    def test_pointwise_fallback_and_native_classes_preserved(self):
        selected = glc.select([51, 0, 255, 192], [True]*4, [10, 30, 0, 60], [True]*4)
        np.testing.assert_array_equal(selected.functional,
                                      [glc.BROADLEAF_EVERGREEN, glc.GRASSLAND, glc.UNKNOWN, glc.BUILT])
        np.testing.assert_array_equal(selected.source_index, [glc.SOURCE_GLC, glc.SOURCE_WORLDCOVER, 0, glc.SOURCE_GLC])
        np.testing.assert_array_equal(selected.valid, [True, True, False, True])
        np.testing.assert_allclose(selected.native_class, [51, 30, np.nan, 192], equal_nan=True)


class PreparedAdapterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def tile(self, values, valid=None, **metadata_changes):
        a = np.asarray(values, np.uint16)
        valid = np.ones(a.shape, bool) if valid is None else np.asarray(valid, bool)
        return write_tile(self.root, glc.PROVIDER_ID, glc.LAYER_ID,
                          maprasters.Raster(a, "EPSG:4326", 20, 12, 1, 1), valid,
                          dict(metadata(), **metadata_changes))

    def test_wrong_units_and_mapping_provenance_are_rejected(self):
        for key, value in (("units", "g/kg"), ("semantic_mapping_version", glc.VERSION + 1),
                           ("semantic_mapping_version", True), ("legend_sha256", "0" * 64)):
            with self.subTest(key=key, value=value):
                self.tile([[71]], **{key: value})
                with self.assertRaisesRegex(ValueError, key):
                    glc.Adapter(PreparedStore(self.root)).lookup(20.5, 11.5, 10)

    def test_every_contributing_tile_metadata_is_checked(self):
        self.tile([[51]])
        write_tile(self.root, glc.PROVIDER_ID, glc.LAYER_ID,
                   maprasters.Raster(np.asarray([[71]], np.uint8), "EPSG:4326", 22, 12, 1, 1),
                   np.asarray([[True]]), dict(metadata(), units="g/kg"))
        adapter = glc.Adapter(PreparedStore(self.root))
        self.assertEqual(adapter.store.metadata(glc.PROVIDER_ID, glc.LAYER_ID)["units"], "categorical_class")
        with self.assertRaisesRegex(ValueError, "units"):
            adapter.lookup([20.5, 22.5], [11.5, 11.5], 10)

    def test_conflicting_identity_and_crs_metadata_are_rejected(self):
        for key, value in (("crs", "EPSG:3857"), ("provider_id", "soilgrids"), ("layer_id", "other")):
            with self.subTest(key=key):
                with self.assertRaisesRegex(ValueError, key):
                    glc.Adapter._validate_metadata({key: value})

    def test_real_store_mixed_nodata_worldcover_and_provenance(self):
        self.tile([[51, 71], [185, 0]], [[True, False], [True, True]])
        adapter = glc.Adapter(PreparedStore(self.root))
        wc = maprasters.Raster(np.asarray([[10, 10], [90, 30]], np.uint8), "EPSG:4326", 20, 12, 1, 1,
                               0, ["fixture-worldcover"])
        with mock.patch("socket.socket", side_effect=AssertionError("lookup must not open network")):
            result = adapter.lookup([20.5, 21.5, 20.5, 21.5], [11.5, 11.5, 10.5, 10.5], wc)
        np.testing.assert_array_equal(result.functional,
                                      [glc.BROADLEAF_EVERGREEN, glc.TREE_UNSPECIFIED, glc.MANGROVE, glc.GRASSLAND])
        np.testing.assert_array_equal(result.source_index, [1, 2, 1, 2])
        self.assertEqual(len(result.provenance), 2)
        fine = next(p for p in result.provenance if p["provider_id"] == glc.PROVIDER_ID)
        self.assertEqual(fine["source_sha256"], metadata()["source_sha256"])
        self.assertEqual(fine["license"], glc.LICENSE)
        self.assertEqual(fine["source_revision"], glc.SOURCE_REVISION)
        self.assertIn("prepared_sha256", fine)

    def test_missing_store_is_worldcover_or_explicit_unknown(self):
        adapter = glc.Adapter(PreparedStore(self.root))
        missing = adapter.lookup([1, 2], [1, 2])
        self.assertFalse(missing.valid.any())
        self.assertTrue(np.isnan(missing.native_class).all())
        self.assertEqual(missing.provenance, ())
        fallback = adapter.lookup([1, 2], [1, 2], np.asarray([10, 0]))
        np.testing.assert_array_equal(fallback.valid, [True, False])
        np.testing.assert_array_equal(fallback.source_index, [glc.SOURCE_WORLDCOVER, glc.SOURCE_UNKNOWN])

    def test_corrupt_prepared_tile_falls_back_without_hiding_quality(self):
        row = self.tile([[71]])
        (self.root / row["path"]).write_bytes(b"truncated")
        result = glc.Adapter(PreparedStore(self.root)).lookup(20.5, 11.5, 10)
        self.assertEqual(int(result.functional), glc.TREE_UNSPECIFIED)
        self.assertEqual(int(result.source_index), glc.SOURCE_WORLDCOVER)
        self.assertEqual(len(result.provenance), 1)
        self.assertEqual(result.quality, "fallback")
        self.assertEqual(result.reason, "invalid_or_corrupt")

    def test_catalog_adoption_is_explicit_and_revision_changes(self):
        old = glc.Adapter(PreparedStore(self.root))
        self.tile([[81]])
        new = glc.Adapter(PreparedStore(self.root))
        self.assertNotEqual(old.revision, new.revision)
        self.assertFalse(bool(old.lookup(20.5, 11.5).valid))
        self.assertEqual(int(new.lookup(20.5, 11.5).functional), glc.NEEDLELEAF_DECIDUOUS)

    def test_invalid_coordinates_and_budget_never_create_valid_classes(self):
        adapter = glc.Adapter(PreparedStore(self.root))
        selected = adapter.lookup([181, 0, 0, np.nan], [0, 91, np.inf, 0], 10)
        self.assertFalse(selected.valid.any())
        self.assertTrue((selected.source_index == glc.SOURCE_UNKNOWN).all())
        with self.assertRaisesRegex(ValueError, "point budget"):
            adapter.lookup(np.zeros(262145), 0, 10)

    def test_raster_fallback_receives_only_valid_coordinates(self):
        seen = []
        class WorldCover:
            def sample(self, lon, lat):
                self_sample = (np.asarray(lon), np.asarray(lat))
                seen.append(self_sample)
                return np.full(np.shape(lon), 10)
        selected = glc.Adapter(PreparedStore(self.root)).lookup([0, np.nan, 181], [0, 0, 0], WorldCover())
        np.testing.assert_array_equal(seen[0][0], [0])
        np.testing.assert_array_equal(selected.valid, [True, False, False])
        self.assertEqual(selected.quality, "partial")

    def test_worldcover_sample_keeps_actual_provenance(self):
        wc = Sample(np.asarray([10.0]), np.asarray([True]), ({"id": "actual-fixture", "sha256": "abc"},))
        result = glc.Adapter(PreparedStore(self.root)).lookup([0], [0], wc)
        self.assertEqual(result.provenance, wc.provenance)


if __name__ == "__main__":
    unittest.main()
