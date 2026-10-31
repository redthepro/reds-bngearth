"""Actual pipeline integration; analytic fixtures only, no engine/network."""
import hashlib
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import glcfcs10
import imagery
import mapdata
import mapfixtures as mf
import maprasters
import osmtags
from provider_contract import PreparedStore, write_tile
import vegetation
import test_imagery as ti
import test_mapdata as tm
import test_vegetation as tv
from test_roads import features


def put(root, provider, layer, value, units, bounds=mf.CELL.bounds(), **extra):
    west, south, east, north = bounds
    data = np.full((2, 2), value, np.float32)
    metadata = dict(source_revision='analytic-fixture-v1', acquired_at='2026-10-26',
                    license='CC-BY-4.0', source_crs='EPSG:4326', units=units,
                    nodata=None, resolution={'nominal_m': 250}, confidence='analytic_fixture_not_survey',
                    source_sha256=hashlib.sha256(data.tobytes()).hexdigest(), **extra)
    return write_tile(root, provider, layer, maprasters.Raster(data, 'EPSG:4326', west, north,
                      (east-west)/2, (north-south)/2), np.ones(data.shape, bool), metadata)


class SemanticPipelineTest(tm.Sources, tm.SmallBlocks, unittest.TestCase):
    def md(self, root):
        transport = mf.Transport(self.files)
        result = mapdata.MapData(os.path.join(self.tmp, 'cache'), mf.fetcher(transport), mf.RELEASE,
                                 semantic_root=root)
        self.addCleanup(result.close)
        return result

    def test_soil_water_roundtrip_preserves_finer_vector_geometry(self):
        root = Path(self.tmp) / 'prepared'
        for layer, value in (('sand', 500), ('clay', 300), ('cfvo', 100)):
            put(root, 'soilgrids', layer, value, 'cm3/dm3' if layer == 'cfvo' else 'g/kg', depth='0-5cm', statistic='mean')
        put(root, 'jrc_gsw', 'occurrence', 0, 'percent', period='1984-2024')
        put(root, 'jrc_gsw', 'seasonality', 6, 'months', period='2024')
        md = self.md(root)
        before = md.cell(mf.CELL, ('water',)).layers['water'].to_arrays('')
        cell = md.cell(mf.CELL, ('water', 'soil', 'water_evidence'))
        after = cell.layers['water'].to_arrays('')
        for key in before:
            np.testing.assert_array_equal(before[key], after[key])
        self.assertTrue((cell.layers['soil'].values['sand_fraction'] == .5).all())
        water = cell.layers['water_evidence']
        self.assertTrue(water.valid['occurrence_fraction'].all())
        self.assertTrue((water.values['occurrence_fraction'] == 0).all())
        self.assertTrue((water.values['seasonality_months'] == 6).all())
        # A second MapData actually reads normalized evidence from the existing cache.
        again = self.md(root).cell(mf.CELL, ('soil', 'water_evidence'))
        self.assertEqual(cell.layers['soil'].provenance, again.layers['soil'].provenance)
        self.assertEqual(md.worldplan_revision['semantic_catalog'], md.semantic_store.revision)
        self.assertIn('SoilGrids', cell.credit_line())

    def test_empty_prepared_coverage_uses_worldcover_and_missing_composition(self):
        md = self.md(Path(self.tmp) / 'empty')
        cd = md.cell(mf.CELL, ('vegetation_types', 'soil', 'water_evidence'))
        cover = cd.layers['vegetation_types']
        self.assertTrue(cover.valid['functional'].any())
        self.assertTrue((cover.values['source_index'][cover.valid['functional']] == glcfcs10.SOURCE_WORLDCOVER).all())
        self.assertTrue(cover.provenance[0]['files'])
        for layer in ('soil', 'water_evidence'):
            self.assertEqual(cd.layers[layer].quality, 'missing')
            self.assertFalse(any(v.any() for v in cd.layers[layer].valid.values()))
            self.assertTrue(all(np.isnan(v).all() for v in cd.layers[layer].values.values()))

    def test_new_catalog_invalidates_cached_missing_but_old_snapshot_stays_immutable(self):
        root = Path(self.tmp) / 'prepared'
        old = self.md(root)
        self.assertEqual(old.cell(mf.CELL, ('soil',)).layers['soil'].quality, 'missing')
        put(root, 'soilgrids', 'sand', 400, 'g/kg', depth='0-5cm', statistic='mean')
        new = self.md(root)
        self.assertNotEqual(old.path(mf.CELL, 'soil'), new.path(mf.CELL, 'soil'))
        self.assertEqual(old.cell(mf.CELL, ('soil',)).layers['soil'].quality, 'missing')
        self.assertTrue(new.cell(mf.CELL, ('soil',)).layers['soil'].valid['sand_fraction'].all())

    def test_corrupt_prepared_fallback_is_not_persisted_over_repair(self):
        root = Path(self.tmp) / 'prepared'
        row = put(root, 'soilgrids', 'sand', 400, 'g/kg', depth='0-5cm', statistic='mean')
        path = root / row['path']
        original = path.read_bytes()
        path.write_bytes(b'corrupt')
        md = self.md(root)
        self.assertEqual(md.cell(mf.CELL, ('soil',)).layers['soil'].quality, 'transient_fallback')
        self.assertFalse(md.has(mf.CELL, 'soil'))
        path.write_bytes(original)
        self.assertTrue(md.cell(mf.CELL, ('soil',)).layers['soil'].valid['sand_fraction'].all())
        self.assertTrue(md.has(mf.CELL, 'soil'))

    def test_no_catalog_is_missing_not_a_fabricated_zero_composition(self):
        md = self.md(None)
        evidence = md.soil_at([39, -20], [-76, 30])
        self.assertEqual(evidence.quality, 'missing')
        self.assertTrue(np.isnan(evidence.values['sand_fraction']).all())


class VegetationIntegrationTest(unittest.TestCase):
    def test_functional_types_change_groups_but_never_canopy_height_or_count(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'prepared'
            put(root, 'glc_fcs10', 'classification', 71, 'categorical_class', bounds=tv.KEY.bounds())
            base = vegetation.Vegetation(tv.FakeMapData(), directory)
            old = base.cell(tv.KEY)
            md = tv.FakeMapData()
            md.semantic_store = PreparedStore(root)
            md.semantic_adapter = lambda layer: glcfcs10.Adapter(md.semantic_store)
            new = vegetation.Vegetation(md, directory)
            refined = new.cell(tv.KEY)
            self.assertNotEqual(base._path(tv.KEY.x, tv.KEY.y), new._path(tv.KEY.x, tv.KEY.y))
            self.assertEqual(len(old), len(refined))
            for field in ('height', 'radius', 'lon', 'lat', 'yaw', 'rank', 'scale', 'kind'):
                np.testing.assert_array_equal(getattr(old, field), getattr(refined, field))
            self.assertTrue((refined.group[refined.kind == vegetation.KIND_TREE] == vegetation.G_CONIFER).all())
            self.assertIn('glc_fcs10', {p.get('provider_id') for p in refined.provenance})
            self.assertIn('esa_worldcover', {p.get('provider_id') for p in refined.take(slice(0, 1)).provenance})
            reloaded = vegetation.Vegetation(md, directory)
            with mock.patch.object(reloaded, '_build', side_effect=AssertionError('metadata must not build')):
                metadata = reloaded.provenance(tv.KEY)
                self.assertEqual(metadata, refined.provenance)
                self.assertEqual(reloaded.cell(tv.KEY).provenance, refined.provenance)
            metadata[0]['license'] = 'mutated'
            self.assertNotEqual(metadata, reloaded.provenance(tv.KEY))
            import credits
            frame = tv.geodesy.EnuFrame(*tv.KEY.center())
            server = SimpleNamespace(anchors={1: SimpleNamespace(frame=frame)}, mapdata=None,
                                     veg=reloaded, surface=None, airports=None)
            conn = SimpleNamespace(s=server, active=1, focus=(0, 0),
                                   veg=SimpleNamespace(aid=1, sent={(0, 0)}))
            with mock.patch.object(reloaded, '_build', side_effect=AssertionError('credits must not build')):
                self.assertTrue({'land:glc-fcs10', 'land:esa-worldcover'} <= credits.feature_ids(conn))
                conn.veg.sent.clear()
                self.assertNotIn('land:glc-fcs10', credits.feature_ids(conn))



class CreditsThrottleTest(unittest.TestCase):
    def test_attribution_work_does_not_consume_the_next_throttle_interval(self):
        import credits
        for emitted in (True, False):
            with self.subTest(emitted=emitted):
                now, delays = [0.0], []
                def message(conn, previous):
                    now[0] += 0.10  # deterministic metadata work, no wall-clock timing
                    return {'ids': ['land:glc-fcs10']} if emitted else None
                service = SimpleNamespace(interval=0.25, clock=lambda: now[0], message=message)
                conn = SimpleNamespace(s=SimpleNamespace(credits=service), send=lambda msg: None,
                    loop=SimpleNamespace(call_later=lambda delay, *args: delays.append(delay)))
                tracker = credits.Tracker()
                tracker.check(conn)
                tracker.poke(conn)
                self.assertAlmostEqual(delays[-1], 0.25)


class RegionalFallbackTest(unittest.TestCase):
    def test_regional_and_national_unavailable_global_pixels_still_render(self):
        with tempfile.TemporaryDirectory() as directory:
            def serve(name, z, x, y):
                self.assertEqual(name, 'eox2023', 'regional provider must never be contacted')
                return 200, ti.png(ti.background(z, x, y))
            image = ti.FakeNet(directory, serve, chain=('md6in', 'usgs', 'naip', 'eox2023'), regional=False)
            self.addCleanup(image.close)
            rgb, used = image.render(tv.geodesy.EnuFrame(39.29, -76.58), -50, -50, 100, 16)
            self.assertEqual(rgb.shape, (16, 16, 3))
            self.assertEqual(set(used), {'eox2023'})
            self.assertTrue(image.requests)
            count = len(image.requests)
            # Colour/deshadow reference requests use tiles() directly, not the chain.
            ref = (imagery.SOURCES['usgs'], 12, 1176, 1561)
            self.assertIsNone(image.tiles((ref,))[ref])
            self.assertEqual(len(image.requests), count)

    def test_all_existing_imagery_sources_declare_unknowns_explicitly(self):
        required = {'coverage', 'source_revision', 'acquired_at', 'license', 'crs', 'vertical_datum',
                    'units', 'resolution', 'nodata', 'confidence', 'sha256'}
        for source in imagery.SOURCES.values():
            metadata = source.provider_contract()
            self.assertTrue(required.issubset(metadata))
            self.assertIsNone(metadata['source_revision'], 'a mutable service URL is not a pinned release')


class RoadSemanticsTest(unittest.TestCase):
    def test_actual_parquet_query_optional_fields_and_exact_source_snapshot(self):
        import duckdb
        class Transport(mf.Transport):
            def request(self, url, headers, timeout):
                status, response, body = super().request(url, headers, timeout)
                return status, dict(response, ETag='"analytic-fixture-etag"'), body
        with tempfile.TemporaryDirectory() as directory:
            for rich in (False, True):
                path = Path(directory) / f'fixture-{rich}.parquet'
                extra = (", ' Grade3 ' AS tracktype, 'private' AS access, 'yes' AS motor_vehicle,"
                         " 'uncontrolled' AS crossing, 'lane' AS \"cycleway:left\", 'no' AS cycleway,"
                         " 'track' AS \"cycleway:right\", 'yes' AS bicycle, 'yes' AS foot,"
                         " 3 AS version, TIMESTAMPTZ '2026-01-02 03:04:05+00' AS timestamp") if rich else ''
                with duckdb.connect() as con:
                    con.execute(f"""COPY (SELECT 5::BIGINT AS id,
                        {{'xmin':-1.0,'ymin':-1.0,'xmax':1.0,'ymax':1.0}} AS bbox,
                        'residential' AS highway, '2' AS lanes, '7 m' AS width, '30' AS maxspeed,
                        'both' AS sidewalk, 'yes' AS lit, 'good' AS smoothness,
                        'asphalt' AS surface, 'yes' AS oneway, '0' AS layer {extra})
                        TO '{path.as_posix()}' (FORMAT PARQUET)""")
                transport = Transport({osmtags.URL: path.read_bytes()})
                reader = osmtags.OsmTags(str(Path(directory) / str(rich)), mf.fetcher(transport))
                tags = reader.cell((-.5, -.5, .5, .5))
                self.assertEqual(tags.provenance['source_revision'], '"analytic-fixture-etag"')
                self.assertEqual(tags.provenance['license'], 'ODbL-1.0')
                self.assertEqual(len(tags.provenance['schema_footer_sha256']), 64)
                self.assertEqual(tags[0][5]['width'], 7)
                if rich:
                    self.assertEqual(tags[0][5]['tracktype'], 'grade3')
                    self.assertEqual(tags[0][5]['cycleway_left'], 'lane')
                    self.assertEqual(tags[0][5]['osm_version'], 3)
                    self.assertTrue(tags[0][5]['osm_timestamp'].startswith('2026-01-02'))
                else:
                    self.assertNotIn('tracktype', tags[0][5])

    def test_explicit_motor_vehicle_overrides_generic_and_unknown_stays_unknown(self):
        f = features([[(0, 0), (10, 0)]] * 4, ['track'] * 4)
        f.attrs['src_record'] = np.array(['w1@2', 'w2@3', 'w3@1', 'w4@1'])
        osmtags.join(f, {1: {'access': 'private', 'motor_vehicle': 'yes', 'tracktype': 'grade3',
                             'crossing': 'uncontrolled', 'cycleway_left': 'lane'},
                        2: {'motor_vehicle': 'no'}, 3: {'access': 'destination'}})
        self.assertEqual([osmtags.motor_access(f, i) for i in range(4)], [True, False, None, None])
        self.assertEqual(f.attrs['osm_tracktype'][0], 'grade3')
        self.assertEqual(f.attrs['osm_cycleway_left'][0], 'lane')
        self.assertEqual(f.attrs['osm_tracktype_known'].tolist(), [True, False, False, False])
        self.assertTrue(np.isnan(f.attrs['osm_version'][3]))


if __name__ == '__main__':
    unittest.main()
