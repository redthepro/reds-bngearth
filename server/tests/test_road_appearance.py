"""Lane curves survive the render pipeline; neighbouring terrain stays out of paved interiors."""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import roadmesh
import roads
import surface
from test_roads import FRAME, LAT, LON, M_LAT, M_LON, features, flat, rules


def max_turn(xy):
    d = np.diff(xy, axis=0)
    d = d[np.linalg.norm(d, axis=1) > 1e-6]
    d /= np.linalg.norm(d, axis=1)[:, None]
    return np.degrees(np.arccos(np.clip((d[:-1] * d[1:]).sum(1), -1, 1))).max()


class MarkingCurveTest(unittest.TestCase):
    def test_sparse_curve_stays_smooth_through_tile_and_mesh(self):
        theta = np.linspace(0, np.pi / 2, 7)
        xy = np.c_[80 + 100 * np.cos(theta), 80 + 100 * np.sin(theta)]
        f = features([xy], ["motorway"], widths=[(0, 11)])
        f.rules["oneway"] = rules([(0, 1)], "float")
        chunks = roads.build_tile(FRAME, 0, 0, [f], flat)
        marks = [c for c in chunks if c["m"] == roads.LANE_LINE["m"]]
        self.assertTrue(marks)
        for left, right, *_ in roadmesh.ribbons(marks):
            self.assertLess(max_turn((left + right) / 2), 9.0, "the source has 15-degree joins")

    def test_rounding_is_bounded_reversible_and_keeps_ends(self):
        pts = np.array([[0, 0, 0], [20, 0, 1], [40, 8, 2], [60, 8, 3]], float)
        curve = roads.smooth_marking(pts)
        np.testing.assert_array_equal(curve[[0, -1]], pts[[0, -1]])
        np.testing.assert_allclose(roads.smooth_marking(pts[::-1]), curve[::-1], atol=1e-12)
        distance = roads._seg_dist(curve[:, :2], pts[:-1, :2], pts[1:, :2]).min(1)
        self.assertLessEqual(distance.max(), roads.MARK_CURVE_M)
        self.assertTrue(np.isfinite(curve).all())
        self.assertTrue((np.diff(curve[:, 2]) >= 0).all())

    def test_neighbouring_tiles_meet_on_the_same_curve(self):
        # A source join directly on a tile boundary: rounding after clipping would give each
        # tile a different curve or leave a sharp point at the seam.
        f = features([[(180, 90), (256, 100), (330, 130)]], ["motorway"], widths=[(0, 11)])
        f.rules["oneway"] = rules([(0, 1)], "float")
        seams = []
        for tx in (0, 1):
            chunks = roads.build_tile(FRAME, tx, 0, [f], flat)
            marks = [c for c in chunks if c["m"] == roads.LANE_LINE["m"]]
            mesh = roadmesh.build(FRAME, marks, flat, (0, 0, 0), clip=roads.tile_bbox(tx, 0))
            p, tri = mesh[:2]
            used = p[np.unique(tri)]
            seam = used[np.abs(used[:, 0] - 256) < 1e-7]
            self.assertTrue(len(seam))
            seams.append((seam[:, 1].min(), seam[:, 1].max()))
        # Chunks round positions to millimetres; independently sampled strip normals can add
        # another millimetre. No visible break across a 12 cm painted line.
        np.testing.assert_allclose(seams[0], seams[1], atol=0.003)

    def test_straights_sharp_corners_and_duplicate_points(self):
        for pts in ([[0, 0, 0], [10, 0, 0], [20, 0, 0]],
                    [[0, 0, 0], [10, 0, 0], [10, 10, 0]],
                    [[0, 0, 0], [10, 0, 0], [10, 0, 0], [10, 10, 0]]):
            np.testing.assert_array_equal(roads.smooth_marking(pts), pts)

    def test_mesh_resampling_keeps_short_curve_vertices(self):
        pts = np.array([[0, 0, 0], [1.1, 0.1, 0.2], [1.4, 0.2, 0.3], [10, 3, 1]], float)
        xy, _, z = roadmesh._resample(pts, 2.0)
        sampled = np.c_[xy, z]
        for p in pts:
            self.assertLess(np.linalg.norm(sampled - p, axis=1).min(), 1e-10)

    def test_fine_paint_does_not_get_quarter_metre_shortcuts(self):
        t = np.linspace(0, np.pi / 2, 101)
        source = np.c_[50 * np.cos(t), 50 * np.sin(t), np.zeros(len(t))]
        line = roads.render_nodes(source, roads.LANE_LINE)
        distance = roads._seg_dist(source[:, :2], line[:-1, :2], line[1:, :2]).min(1)
        self.assertLessEqual(distance.max(), 0.021)


class PavementBlendTest(unittest.TestCase):
    def test_material_transition_moves_out_of_pavement(self):
        # A 6 m street on a 2 m material grid. At y=2.5 m (half a metre inside the edge),
        # the old y=2 asphalt / y=4 dirt samples blended dirt into the pavement.
        f = features([[(-50, 0), (50, 0)]], ["residential"], widths=[(0, 6)])
        x, y = np.meshgrid(np.arange(-32, 34, 2.0), np.arange(-32, 34, 2.0))
        la, lo = LAT + y / M_LAT, LON + x / M_LON
        original = np.full(x.shape, surface.GRASS, np.uint8)
        painted = surface.paint_roads(original, la, lo, [f], 2.0)
        mid = painted.shape[1] // 2
        for north in (-4, -2, 0, 2, 4):
            self.assertEqual(painted[int((north + 32) / 2), mid], surface.ASPHALT)
        self.assertEqual(painted[0, mid], surface.GRASS)
        self.assertTrue((original == surface.GRASS).all())
        self.assertEqual(roads.road_specs(f)[0].width, 6, "mapped widths stay unchanged")

    def test_grass_colour_does_not_show_through_paved_core(self):
        f = features([[(-50, 0), (50, 0)]], ["residential"], widths=[(0, 6)])
        rgb = np.full((128, 128, 3), (20, 200, 20), np.uint8)
        photo = surface.photo_roads(rgb, FRAME, -64, -64, 128, [f])
        core = photo[63:65, 30:90].astype(float)
        asphalt = np.array(surface.PHOTO_ROAD_RGBA["paved"][:3])
        self.assertLessEqual(np.abs(core - asphalt).max(), 3.0)
        np.testing.assert_array_equal(photo[0], rgb[0])


if __name__ == "__main__":
    unittest.main()
