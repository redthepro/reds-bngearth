"""Shared caller parity and exact materialized encoder-input reuse, offline."""
import os
import json
from pathlib import Path
import struct
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import blockworker
import earthd
import geodesy
import grading
import heightmap
import mapgeom
import terrainprep
from test_grading import FakeMD, slope_ground
from test_roads import FRAME, LAT, LON, features, rules


class TerrainPrepTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="terrain-prep-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.features = features([[(-300, 20), (300, 20)], [(80, -300), (80, 300)]],
                                 ["secondary", "residential"])

    def sample(self, la, lo):
        e, n, _ = FRAME.geodetic_to_enu(la, lo, 0.0)
        return 40.0 + slope_ground(np.asarray(e), np.asarray(n))

    def surface(self, road_features=None):
        road_features = self.features if road_features is None else road_features
        return SimpleNamespace(classes=lambda la, lo: (np.full(np.shape(la), 30, np.uint8), True),
                               roads=lambda la, lo: ([road_features], True),
                               urban=lambda la, lo: np.zeros(np.shape(la), bool))

    def caller(self, which, output, *, shared=True, compiled=False, progress=None, provisional=False, route=None):
        out = self.root / output
        out.mkdir()
        frame, lat, lon, feats, sample = FRAME, LAT, LON, self.features, self.sample
        if route is not None:
            points = np.asarray(route["coordinates_lat_lon"], np.float64)
            lat, lon = points.mean(axis=0)
            frame = geodesy.EnuFrame(float(lat), float(lon))
            feats = mapgeom.Features(layer="roads", ids=np.array([f'osm:{route["osm_way_id"]}@{route["osm_version"]}']),
                kind=np.array([mapgeom.LINE], np.uint8), part_off=np.array([0, 1], np.int64),
                ring_off=np.array([0, 1], np.int64), coord_off=np.array([0, len(points)], np.int64),
                lonlat=points[:, ::-1].copy(), attrs={"class": np.array([route["tags"]["highway"]]),
                    "subtype": np.array(["road"])},
                rules={"surface": rules([(0, route["tags"].get("surface", "asphalt"))]),
                       "width": rules([], "float"), "level": rules([], "float"), "flags": rules([])})
            def sample(la, lo):
                e, n, _ = frame.geodetic_to_enu(la, lo, 0.0)
                return 40.0 + slope_ground(np.asarray(e), np.asarray(n))
        md = FakeMD(feats)
        grader = grading.Grader(frame, md, sample)
        dem = SimpleNamespace(sample=lambda la, lo, zoom, callback=None:
                              ((callback(1, 1) if callback else None), sample(la, lo))[1])
        surf = self.surface(feats)
        env = {"BNGE_SHARED_TERRAIN": "on" if shared else "off",
               "BNGE_COMPILED_TERRAIN": "1" if compiled else "0"}
        with mock.patch.dict(os.environ, env):
            if which == "worker":
                state = SimpleNamespace(dem=dem, md=None, surface=surf, widths=None, sea=None, airports=None,
                                        cache_dir=str(self.root / "cache"), anchor=lambda *args: (grader, None))
                with mock.patch.object(blockworker, "_s", state):
                    info = blockworker.build(7, float(lat), float(lon), 15, 1 / 64, 0, 0, 128, str(out), "height.png", "terrain.ter")
            else:
                server = earthd.Server.__new__(earthd.Server)
                server.layout = SimpleNamespace(block=128)
                server.out_dir, server.cache_dir = str(out), str(self.root / "cache")
                server.mapdata, server.surface, server.road_widths = None, surf, None
                server.airports, server.sea = None, None
                server.dem = dem
                server.grader_for = lambda anchor: grader
                # This fixture isolates caller/preparation parity. Separate
                # generation tests exercise production snapshot binding.
                server._plan_anchor = lambda anchor: anchor
                server.ground = lambda frame, la, lo, z: z
                server.hm_name = lambda key, *args: "height.png"
                server.ter_name = lambda key, *args: "terrain.ter"
                server._finish_heightmap = lambda anchor, cx, cy, info, *args, **kw: info
                anchor = earthd.Anchor(7, float(lat), float(lon), frame, chunks=None)
                info = server._make_heightmap_here_(anchor, 0, 0, progress, time.perf_counter(), 1 / 64,
                                                    provisional=provisional)
        products = {p.name: p.read_bytes() for p in out.iterdir() if p.is_file()}
        return info, products

    def test_existing_callers_shared_and_compiled_preserve_every_product(self):
        reference_info, reference = self.caller("server", "reference", shared=False)
        for which, shared, compiled in (("server", True, False), ("worker", False, False),
                                       ("worker", True, False), ("server", True, True),
                                       ("worker", True, True)):
            info, products = self.caller(which, f"{which}-{shared}-{compiled}", shared=shared, compiled=compiled)
            self.assertEqual(products, reference, (which, shared, compiled))
            if which == "worker":
                self.assertIs(info.pop("_worker_epoch_changed"), False,
                              "this fixture has stable inputs throughout worker preparation")
            self.assertEqual({k: v for k, v in info.items() if k not in ("stages", "compiled")},
                             {k: v for k, v in reference_info.items() if k != "stages"})
        self.assertTrue(info["compiled"]["hit"], "same materialized inputs reuse across callers")
        self.assertFalse(info["compiled"]["decision_plan_persisted"])

    def test_source_resolved_osm_routes_preserve_both_caller_products(self):
        fixture = json.loads((Path(__file__).parent / "fixtures/worldplan/roads.json").read_text(encoding="utf-8"))
        self.assertEqual(fixture["license"], "ODbL-1.0")
        self.assertEqual(len(fixture["routes"]), 3)
        for route in fixture["routes"]:
            sid = route["scenario_id"]
            _, reference = self.caller("server", f"route-{sid}-legacy", shared=False, route=route)
            for which, compiled in (("server", False), ("worker", False), ("server", True), ("worker", True)):
                _, products = self.caller(which, f"route-{sid}-{which}-{compiled}", compiled=compiled, route=route)
                self.assertEqual(products, reference, (sid, which, compiled))

    def test_progress_cancellation_exception_is_unchanged(self):
        class Cancelled(Exception):
            pass
        def cancel(*args):
            raise Cancelled("request ended")
        for shared in (False, True):
            with self.assertRaisesRegex(Cancelled, "request ended"):
                self.caller("server", f"cancel-{shared}", shared=shared, progress=cancel)

    def test_provisional_skips_grading_and_has_same_legacy_bytes(self):
        _, legacy = self.caller("server", "provisional-legacy", shared=False, provisional=True)
        _, shared = self.caller("server", "provisional-shared", provisional=True)
        self.assertEqual(shared, legacy)

    def test_masks_precede_holes_and_cached_ter_uses_holes(self):
        outputs = []
        for enabled in (False, True, True):
            out = self.root / f"holes-{len(outputs)}"
            out.mkdir()
            size = 32
            def holes(elev, keep, idx, info):
                idx[size // 2, size // 2] = heightmap.TER_HOLE
                info["tunnelHoles"] = 1
                return idx
            with mock.patch.dict(os.environ, BNGE_COMPILED_TERRAIN="1" if enabled else "0"):
                info = terrainprep.prepare(FRAME, 0, 0, size, 1 / 64, str(out), "height.png", "terrain.ter",
                    sample=self.sample, surface=self.surface(), holes=holes, cache_dir=str(self.root / "holes-cache"))
            outputs.append({p.name: p.read_bytes() for p in out.iterdir()})
            ter = (out / "terrain.ter").read_bytes()
            layer_start = 5 + 2 * size * size
            self.assertEqual(ter[layer_start + (size // 2) * size + size // 2], heightmap.TER_HOLE)
            self.assertEqual(sum(np.asarray(Image.open(out / name))[size - 1 - size // 2, size // 2].item()
                                 for name in info["layers"]), 255)
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(outputs[1], outputs[2])
        self.assertTrue(info["compiled"]["hit"])

    def test_neighbor_input_and_quantum_changes_invalidate_compilation(self):
        results = []
        with mock.patch.dict(os.environ, BNGE_COMPILED_TERRAIN="1"):
            for i, (revision, q, uplift) in enumerate(((1, 1 / 64, 0), (2, 1 / 64, 0), (2, 1 / 32, 2))):
                out = self.root / f"changed-{i}"
                out.mkdir()
                info = terrainprep.prepare(FRAME, 0, 0, 32, q, str(out), "height.png", "terrain.ter",
                    sample=lambda la, lo, add=uplift: self.sample(la, lo) + add,
                    cache_dir=str(self.root / "changed-cache"),
                    plan_context={"facts": [{"cell": [14, 4706, 6244], "neighbor_revision": revision}],
                                  "dependencies": [], "completeness": "unknown"})
                results.append(info)
        self.assertTrue(all(not i["compiled"]["hit"] for i in results))
        self.assertEqual(len({i["compiled"]["key"] for i in results}), 3)

    def test_declared_halo_wraps_antimeridian_and_is_bounded(self):
        keys = terrainprep.dependency_keys(geodesy.EnuFrame(0, 179.999), 0, 0, 128)
        self.assertLessEqual(len(keys), 64)
        self.assertIn(0, {k.x for k in keys})
        self.assertIn((1 << 14) - 1, {k.x for k in keys})

    def test_neighboring_shared_blocks_have_identical_overlap_and_corner(self):
        grids = {}
        with mock.patch.dict(os.environ, BNGE_COMPILED_TERRAIN="1"):
            for cx, cy in ((0, 0), (64, 0), (0, 64), (64, 64)):
                out = self.root / f"neighbor-{cx}-{cy}"
                out.mkdir()
                info = terrainprep.prepare(FRAME, cx, cy, 64, 1 / 64, str(out), "height.png", "terrain.ter",
                                           sample=self.sample, cache_dir=str(self.root / "neighbor-cache"))
                with Image.open(out / "height.png") as image:
                    qgrid = np.array(image)[::-1]
                grids[cx, cy] = heightmap.heights(qgrid, info["zBase"], info["maxHeight"])
        np.testing.assert_array_equal(grids[0, 0][:, 32:], grids[64, 0][:, :32])
        np.testing.assert_array_equal(grids[0, 0][32:, :], grids[0, 64][:32, :])
        np.testing.assert_array_equal(grids[0, 0][32:, 32:], grids[64, 64][:32, :32])

    def test_anchor_change_retains_fact_identity_but_rebuilds_anchor_payload(self):
        context = {"facts": [{"cell": [14, 4706, 6244], "source": "same geographic snapshot"}],
                   "dependencies": [], "completeness": "unknown"}
        infos = []
        with mock.patch.dict(os.environ, BNGE_COMPILED_TERRAIN="1"):
            for i, frame in enumerate((FRAME, geodesy.EnuFrame(LAT + 0.001, LON + 0.001))):
                out = self.root / f"anchor-{i}"
                out.mkdir()
                infos.append(terrainprep.prepare(frame, 0, 0, 32, 1 / 64, str(out), "height.png", "terrain.ter",
                    sample=self.sample, cache_dir=str(self.root / "anchor-cache"), plan_context=context))
        self.assertEqual(infos[0]["compiled"]["canonical_id"], infos[1]["compiled"]["canonical_id"])
        self.assertNotEqual(infos[0]["compiled"]["key"], infos[1]["compiled"]["key"])
        self.assertFalse(infos[1]["compiled"]["hit"])

    def test_vertical_offset_has_distinct_prepared_identity_even_with_same_qgrid(self):
        infos, grids = [], []
        with mock.patch.dict(os.environ, BNGE_COMPILED_TERRAIN="1"):
            for index, uplift in enumerate((0.0, 64.0, 64.0)):
                out = self.root / f"vertical-offset-{index}"
                out.mkdir()
                info = terrainprep.prepare(FRAME, 0, 0, 32, 1 / 64, str(out), "height.png", "terrain.ter",
                    sample=lambda la, lo, add=uplift: np.full(np.shape(la), 40.0 + add),
                    cache_dir=str(self.root / "vertical-offset-cache"))
                infos.append(info)
                with Image.open(out / "height.png") as image:
                    grids.append(np.array(image))
        np.testing.assert_array_equal(grids[0], grids[1])
        self.assertNotEqual(infos[0]["zBase"], infos[1]["zBase"])
        self.assertAlmostEqual(infos[1]["groundZ"] - infos[0]["groundZ"], 64.0)
        self.assertNotEqual(infos[0]["compiled"]["key"], infos[1]["compiled"]["key"])
        self.assertFalse(infos[1]["compiled"]["hit"])
        self.assertEqual(infos[1]["compiled"]["key"], infos[2]["compiled"]["key"])
        self.assertTrue(infos[2]["compiled"]["hit"])


if __name__ == "__main__":
    unittest.main()
