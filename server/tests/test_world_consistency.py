"""Road joins, bridge metadata and roadside objects agree with their supporting geometry."""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import buildings
import furniture
import grading
import roads
from test_buildings import rect
from test_furniture import road
from test_osmtags import tagged
from test_roads import rules


class RoadJoinTest(unittest.TestCase):
    def test_split_slope_has_no_artificial_step(self):
        sp = roads.Spec(12, "paved", False, "motorway", True)
        for slope in (-0.08, 0.08):
            plans = []
            for start, end in ((-100, 0), (0, 100)):
                x = np.arange(start, end + 1, grading.PROFILE_STEP)
                p = grading._plan(np.c_[x, np.zeros(len(x))], 50 + slope * x, sp)
                np.testing.assert_allclose(p.z, 50 + slope * p.xy[:, 0], atol=1e-10)
                plans.append(p)
            self.assertEqual(plans[0].z[-1], plans[1].z[0])

    def test_curved_profile_keeps_endpoints_and_smooths_noise(self):
        x = np.arange(0, 400, 2.0)
        z = 0.05 * x + 0.3 * np.sin(x / 3)
        sm = grading.road_profile(z, 2, 12)
        self.assertEqual(sm[0], z[0])
        self.assertEqual(sm[-1], z[-1])
        self.assertLess(np.max(np.abs(sm[25:-25] - 0.05 * x[25:-25])), 0.02)
        np.testing.assert_allclose(grading.road_profile(z[::-1], 2, 12), sm[::-1], atol=1e-12)
        np.testing.assert_array_equal(grading.road_profile([4, 5], 2, 12), [4, 5])


class RoadMetadataTest(unittest.TestCase):
    def features(self, tags, structure=None, link=False):
        f = tagged([[(0, 0), (200, 0)]], ["motorway"], ["w1@1"], {1: tags})
        f.rules["oneway"] = rules([(0, 1)], "float")
        if structure:
            f.rules["flags"] = rules([(0, "is_" + structure)])
        if link:
            f.rules["subclass"] = rules([(0, "link")])
        return f

    def test_bridge_and_tunnel_keep_approach_width_and_lanes(self):
        for tags in ({"lanes": 4}, {"lanes": 3, "width": 13.5}):
            for link in (False, True):
                approach = roads.road_specs(self.features(tags, link=link))[0]
                for kind, fn in (("bridge", roads.bridge_specs), ("tunnel", roads.tunnel_specs)):
                    sp = fn(self.features(tags, kind, link))[0]
                    self.assertEqual(sp.width, approach.width, (kind, tags, link))
                    self.assertEqual(sp.lanes, approach.lanes)

    def test_building_setbacks_do_not_replace_mapped_lanes(self):
        f = tagged([[(0, 0), (200, 0)]], ["residential"], ["w1@1"], {1: {"lanes": 2}})
        plain = roads.road_specs(f)[0]
        measured = roads.road_specs(f, widths=lambda *args: (20.0, 4.5))[0]
        self.assertEqual(measured.width, plain.width)


class RoadsideHeightTest(unittest.TestCase):
    def test_poles_signs_and_wires_follow_the_verge(self):
        r = road("rural", "secondary", False, x0=0, x1=240)
        calls, wires = [], []

        def ground(x, y):
            calls.append(len(x))
            return 0.02 * x - 0.4 * (y - 50)

        rows = furniture.place([r], [], (-10, -100, 250, 200), wires=wires,
                               speeds={"rural": 40}, ground=ground)
        self.assertEqual(len(calls), 1, "one batched terrain lookup")
        poles = [r for r in rows if r[0] == furniture.G_POLE]
        signs = [r for r in rows if r[0].startswith("speed")]
        self.assertTrue(poles and signs and wires)
        for row in poles + signs:
            self.assertAlmostEqual(row[3], 0.02 * row[1] - 0.4 * (row[2] - 50), delta=0.011)
        for wire in wires[1::3]:  # centre wire sits directly above each pole
            for p in wire[[0, -1]]:
                self.assertAlmostEqual(p[2] - furniture.WIRE_Z,
                                       0.02 * p[0] - 0.4 * (p[1] - 50), delta=0.011)

    def test_sidewalk_lamps_keep_their_slab_height(self):
        r = road("urban", "residential", True)
        walks = [{"fid": r[0], "pts": roads.offset_polyline(r[2], side * 5.25) + [0, 0, 3],
                  "side": side, "w": 2.5} for side in (-1, 1)]
        rows = furniture.place([r], walks, (0, 0, 256, 256), ground=lambda x, y: np.full(len(x), -10.0))
        lamps = [r for r in rows if r[0] == furniture.G_LAMP]
        self.assertTrue(lamps)
        self.assertTrue(all(r[3] == 3.15 for r in lamps))

    def test_long_roads_only_query_the_local_ground(self):
        r = road("long", "secondary", False, x0=-15000, x1=15000)
        calls = []

        def ground(x, y):
            calls.append(x.copy())
            return np.zeros(len(x))

        furniture.place([r], [], (0, 0, 256, 256), wires=[], ground=ground)
        self.assertEqual(len(calls), 1)
        self.assertGreaterEqual(calls[0].min(), 0)
        self.assertLess(calls[0].max(), 256 + furniture.POLE_SPACING + 0.01)


class TownhouseTest(unittest.TestCase):
    def test_explicit_suburban_terraces_have_residential_defaults(self):
        for cls in buildings.ROW_CLASSES:
            for k in range(10):
                p = buildings.plan(rect(k * 8, 0, 7, 14), {"class": cls}, neighbours=2,
                                   lat=38.77, lon=-77.44)
                self.assertIn(p.floors, (2, 3))
                self.assertEqual(p.roof, "gabled")
                self.assertFalse(p.shop)
                self.assertNotIn(buildings.OFFICE, p.facade)

    def test_mapped_roof_height_and_dense_core_take_precedence(self):
        p = buildings.plan(rect(0, 0, 7, 14), {"class": "terrace", "roof_shape": "flat",
                           "height": 10.5, "num_floors": 3}, neighbours=2, lat=38.77, lon=-77.44)
        self.assertEqual((p.roof, p.height, p.floors), ("flat", 10.5, 3))
        p = buildings.plan(rect(0, 0, 7, 14), {"class": "terrace"}, neighbours=2,
                           lat=39.28, lon=-76.59, dense=True)
        self.assertEqual(p.roof, "flat")


if __name__ == "__main__":
    unittest.main()
