"""Native queue/client agreement and bounded performance feedback (no network)."""
import math
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import earthd
import farfield
import fast
import streaming


class StreamingTest(unittest.TestCase):
    def test_native_priorities_and_camera_turn(self):
        tiles = [farfield.Tile(0, i, 0) for i in (4, -4, 2)]
        cam = dict(x=0, y=0, fx=-1, fy=0, fovDeg=60)
        result = streaming.priorities(tiles, (0, 0), (89.408, 0), (0, 0), farfield.view_cone(cam))
        np.testing.assert_array_equal(result[:, 0], [2, 1, 0])
        cam['fx'] = 1
        result = streaming.priorities(tiles, (0, 0), (89.408, 0), (0, 0), farfield.view_cone(cam))
        np.testing.assert_array_equal(result[:, 0], [1, 2, 0])

    def test_native_matches_client_randomized(self):
        from lupa.luajit21 import LuaRuntime
        lua = LuaRuntime()
        path = Path(__file__).resolve().parents[2] / 'mod/beamng_earth/lua/ge/extensions/beamngEarth/streaming.lua'
        module = lua.execute(path.read_text())
        rng = np.random.default_rng(42)
        for _ in range(20):
            player = dict(x=float(rng.uniform(-500, 500)), y=float(rng.uniform(-500, 500)),
                          vx=float(rng.uniform(-90, 90)), vy=float(rng.uniform(-90, 90)))
            angle = rng.uniform(-math.pi, math.pi)
            cam = dict(x=float(rng.uniform(-3000, 3000)), y=float(rng.uniform(-3000, 3000)),
                       fx=math.cos(angle), fy=math.sin(angle), fovDeg=float(rng.uniform(20, 100)))
            tiles = [farfield.Tile(0, int(i), int(j)) for i, j in rng.integers(-20, 20, (100, 2))]
            result = streaming.priorities(tiles, (player['x'], player['y']), (player['vx'], player['vy']),
                                          (cam['x'], cam['y']), farfield.view_cone(cam))
            for tile, row in zip(tiles, result):
                x0, y0, x1, y1 = tile.bbox
                inst = lua.table_from(dict(x0=x0, y0=y0, x1=x1, y1=y1))
                self.assertEqual(row[0], module.priority(inst, lua.table_from(cam), lua.table_from(player)))

    def test_feedback_validation_and_expiry(self):
        for value in (None, {}, {'scale': True}, {'scale': float('nan')}, {'scale': '0'}, {'scale': 10**1000}):
            self.assertEqual(streaming.feedback(value), 1)
        self.assertEqual(streaming.feedback({'scale': -100}), 0.25)
        self.assertEqual(streaming.feedback({'scale': 100}), 1)
        self.assertEqual(streaming.worker_limit(8, 0.25, 10, 11), 2)
        self.assertEqual(streaming.worker_limit(1, 0.25, 10, 11), 1)
        self.assertEqual(streaming.worker_limit(8, 0.25, 10, 14), 8)
        self.assertEqual(streaming.feedback({'scale': 1, 'queued': 256}), 0.25)
        self.assertEqual(streaming.feedback({'scale': 1, 'queued': 256, 'loading': True}), 1)
        for queued in (-1, True, float('nan'), '256', 10**1000):
            self.assertEqual(streaming.feedback({'scale': 1, 'queued': queued}), 1)

    def test_invalid_native_arguments(self):
        for boxes, player, view, cone in ((np.zeros((2, 3)), [0]*4, [0]*2, []),
                                         (np.zeros((2, 4)), [0], [0]*2, []),
                                         (np.zeros((2, 4)), [0]*4, [0]*2, [0]),
                                         (np.full((2, 4), np.nan), [0]*4, [0]*2, [])):
            with self.assertRaises(ValueError):
                fast._ef.stream_priorities(boxes, player, view, cone, 150, 6)
        self.assertEqual(streaming.priorities([], (0, 0), (0, 0), (0, 0), None).shape, (0, 2))

    def test_server_build_queue_driving_before_camera(self):
        conn = earthd.Connection.__new__(earthd.Connection)
        conn.active, conn.focus, conn.vel, conn.view_at = 1, (0, 0), (89.408, 0), None
        conn.cam = dict(x=0, y=0, fx=-1, fy=0, fovDeg=60)
        conn.cam_aid, conn.block, conn.pending_block = 1, None, None
        conn.s = SimpleNamespace(near_visual='mesh', forced_level=-2)
        tiles = [farfield.Tile(0, i, 0) for i in (4, -4, 2)]
        conn.desired, conn.sent, conn.shown = {t.key: t for t in tiles}, {}, {}
        conn.failed = set()
        conn._regroup()
        self.assertEqual([t.i for t in conn.queue], [2, -4, 4])
        conn.cam['fx'] = 1
        conn._regroup()
        self.assertEqual([t.i for t in conn.queue], [2, 4, -4])

    def test_legacy_gate_keeps_nearest_before_partially_sent_groups(self):
        conn = earthd.Connection.__new__(earthd.Connection)
        conn.active, conn.focus, conn.vel, conn.view_at = 1, (0, 0), (0, 0), None
        conn.cam, conn.cam_aid, conn.pending_block = None, None, None
        conn.block = SimpleNamespace(bbox=(-4096, -4096, 4096, 4096))
        conn.s = SimpleNamespace(near_visual='terrain', forced_level=0)
        near = farfield.Tile(0, 0, 0)
        children = [farfield.Tile(0, i, j) for i in (8, 9) for j in (0, 1)]
        conn.desired = {t.key: t for t in [near] + children}
        conn.sent = {children[-1].key: children[-1]}
        conn.shown, conn.failed = {(1, '1_4_0'): (2048, 0, 2560, 512)}, set()
        conn._regroup()
        self.assertEqual(conn.queue[0].key, near.key)


if __name__ == '__main__':
    unittest.main()
