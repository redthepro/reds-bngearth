"""Mapped building volumes and courtyards survive render/collision generation."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import buildings as bld
import geodesy
import mapgeom


def features(records):
    coords, part_off, ring_off, coord_off = [], [0], [0], [0]
    for record in records:
        for polygon in record["polygons"]:
            for ring in polygon:
                coords.extend(ring)
                coord_off.append(len(coords))
            ring_off.append(len(coord_off) - 1)
        part_off.append(len(ring_off) - 1)
    keys = {key for record in records for key in record["attrs"]}
    attrs = {}
    for key in keys:
        values = [r["attrs"].get(key) for r in records]
        text = any(isinstance(v, str) for v in values)
        attrs[key] = np.array([("" if text else np.nan) if v is None else v for v in values])
    return mapgeom.Features("buildings", np.array([r["id"] for r in records]),
                            np.full(len(records), mapgeom.POLYGON, np.uint8),
                            np.array(part_off), np.array(ring_off), np.array(coord_off),
                            np.asarray(coords, np.float64), attrs=attrs)


def rect(x, y, w, h):
    return np.array([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], np.float64)


def record(frame, key, polygons, **attrs):
    def lonlat(ring):
        ring = np.vstack([ring, ring[0]])
        lat, lon, _ = frame.enu_to_geodetic(ring[:, 0], ring[:, 1], 0.0)
        return np.stack([lon, lat], 1)
    return dict(id=key, polygons=[[lonlat(r) for r in p] for p in polygons], attrs=attrs)


class FootprintTest(unittest.TestCase):
    def setUp(self):
        self.frame = geodesy.EnuFrame(38.8719, -77.0563, 0)
        self.ground = lambda lat, lon: np.zeros(np.shape(lat))

    def build_items(self, parent, parts=(), box=(0, 0, 256, 256), **kwargs):
        return bld.tile_buildings(self.frame, *box, [features(parent)], self.ground,
                                  part_layers=[features(parts)] if parts else (), **kwargs)

    def assert_courtyards_open(self, item):
        spec, gmin, gmax = item
        expected = bld._area2(spec.ring) / 2 - sum(bld._area2(h) / 2 for h in spec.holes)
        top = bld.base_z(spec, gmin, gmax) + spec.min_h + spec.height
        for name, mesh in (("near", bld.build([item])), ("far", bld.build([item], lite=True)),
                           ("collision", bld.collision_mesh([item]))):
            points, triangles = mesh[0], mesh[2]
            roof = points[triangles][np.max(np.abs(points[triangles][:, :, 2] - top), axis=1) < 1e-6]
            cross = np.cross(roof[:, 1] - roof[:, 0], roof[:, 2] - roof[:, 0])
            self.assertTrue((cross[:, 2] > -1e-8).all(), name)
            self.assertAlmostEqual(float(cross[:, 2].sum() / 2), expected, delta=0.02, msg=name)
            for centre in roof[:, :, :2].mean(1):
                self.assertFalse(any(bld._inside(h, centre) for h in spec.holes), name)

    def test_pentagon_roof_parts_keep_23_metre_parent_and_42_courtyards(self):
        path = Path(__file__).parent / "fixtures" / "pentagon_building.json"
        fixture = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(fixture["polygons"][0]), 43)
        items = self.build_items([fixture], fixture["building_parts"], box=(0, -256, 256, 0))
        base = [item for item in items if item[0].min_h == 0]
        self.assertEqual(len(base), 1, "the parent walls must survive roof-only building parts")
        self.assertEqual((base[0][0].height, len(base[0][0].holes)), (23, 42))
        self.assertEqual(len(items), 20)
        self.assertTrue(all(s.min_h == 23 and s.height == 2 for s, _, _ in items if s.min_h))
        self.assert_courtyards_open(base[0])
        meshes = bld.build_parts(items, with_lod=True)
        self.assertTrue(all(len(mesh[0]) < 65536 for mesh in meshes))

    def test_grounded_podium_and_upper_parts_do_not_duplicate_parent(self):
        outline = rect(10, 10, 40, 40)
        parent = record(self.frame, "B", [[outline]], height=40, **{"class": "office"})
        parts = [record(self.frame, "p1", [[outline]], building_id="B", height=12, min_height=0),
                 record(self.frame, "p2", [[rect(20, 20, 20, 20)]], building_id="B", height=40, min_height=12)]
        items = self.build_items([parent], parts)
        self.assertEqual(sorted((s.min_h, s.height) for s, _, _ in items), [(0, 12), (12, 28)])

    def test_installed_landmark_replaces_visuals_but_keeps_mapped_collision(self):
        records = [record(self.frame, key, [[rect(x, 10, 30, 30)]], height=10)
                   for key, x in (("landmark", 10), ("neighbour", 70))]
        with patch("landmarks.excluded_feature", side_effect=lambda key: key == "landmark"):
            items = self.build_items(records)
            self.assertEqual(len(items), 2, "the collision footprint must remain")
            for lite in (False, True):
                mesh = bld.build(items, lite=lite)
                self.assertGreater(mesh[0][:, 0].min(), 60)
            self.assertLess(bld.collision_mesh(items)[0][:, 0].min(), 20)
            only_landmark = [i for i in items if i[0].source_id == "landmark"]
            tile = bld.road_tile(self.frame, 0, 0, None, self.ground, items=only_landmark)
            self.assertEqual(tile[0], [])
            self.assertTrue(tile[1], "collider bounds survive without procedural render meshes")
            self.assertIsNotNone(tile[2])
        with patch("landmarks.excluded_feature", return_value=False):
            self.assertLess(bld.build(items)[0][:, 0].min(), 20, "missing assets retain the visual fallback")

    def test_multipolygon_components_have_independent_owner_tiles_and_no_duplicates(self):
        parent = record(self.frame, "B", [[rect(10, 10, 30, 30)], [rect(300, 20, 40, 40)]], height=10)
        left = self.build_items([parent])
        right = self.build_items([parent, parent], box=(256, 0, 512, 256))
        self.assertEqual((len(left), len(right)), (1, 1))
        self.assertAlmostEqual(bld._area2(right[0][0].ring) / 2, 1600, delta=0.01)
        self.assertLess(right[0][0].ring[:, 0].max(), 256)

    def test_road_inside_courtyard_does_not_lift_the_building(self):
        parent = record(self.frame, "B", [[rect(10, 10, 80, 80), rect(25, 25, 50, 50)]], height=12)
        items = self.build_items([parent], roads_xy=[np.array([[30, 50], [70, 50]], np.float64)])
        self.assertEqual(items[0][0].min_h, 0)
        self.assert_courtyards_open(items[0])

    def test_lazy_parts_preserve_each_multipolygon_component_and_courtyard(self):
        polygons = [[rect(x, 10, 80, 80), rect(x+20, 30, 40, 40)] for x in (10, 130)]
        parent = record(self.frame, 'B', polygons, height=8)
        parts = [record(self.frame, 'P', polygons, building_id='B', height=8, min_height=0)]
        items = self.build_items([parent], parts)
        self.assertEqual(len(items), 2)
        for item in items:
            self.assertEqual(len(item[0].holes), 1)
            self.assert_courtyards_open(item)

    def test_native_road_samples_exclude_overlapping_courtyards_once(self):
        outer = rect(0, 0, 100, 100)
        holes = [rect(20, 20, 40, 60), rect(40, 20, 40, 60)]
        street = np.array([[0, 50], [100, 50]], np.float64)
        self.assertAlmostEqual(bld.road_through(outer, [street], holes=holes), 40)
        self.assertAlmostEqual(bld.road_through(outer, [street]), 100)

    def test_courtyards_are_clipped_with_bridge_passages(self):
        outer, hole = rect(0, 0, 40, 40), rect(10, 10, 20, 20)
        line = np.array([[20, -10], [20, 50]], np.float64)
        rings, attrs, _, _ = bld._passages([outer], [{"height": 20, "_holes": (hole,)}], [None], [0],
                                         [line], decks=[(line, np.array([5, 5]))], ground_at=lambda _: 0)
        sides = [(r, at["_holes"]) for r, at in zip(rings, attrs) if bld._area2(r) < bld._area2(outer)]
        self.assertEqual(len(sides), 2)
        for ring, holes in sides:
            for h in holes:
                self.assertTrue((h.min(0) >= ring.min(0) - 1e-8).all())
                self.assertTrue((h.max(0) <= ring.max(0) + 1e-8).all())
            pieces = list(bld._polygon_strips(ring, holes))
            expected = (bld._area2(ring) - sum(bld._area2(h) for h in holes)) / 2
            self.assertAlmostEqual(sum(bld._area2(p) / 2 for p in pieces), expected)


if __name__ == "__main__":
    unittest.main()
