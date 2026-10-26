"""Landmark selection, bundled asset validation, ground datum and stale-message protection."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import geodesy
import landmarks
import protocol


def row(ident="test", lon=-77.035, **kw):
    return dict(id=ident, title=ident, lat=38.889, lon=lon, height_m=169,
                shape=landmarks.VFS + ident + "/compiled.cdae",
                materials=landmarks.VFS + ident + "/main.materials.json",
                range_m=1000, exclude_ids=["exact-id"], **kw)


class CatalogTest(unittest.TestCase):
    def test_only_installed_scoped_complete_models_replace_visuals(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            r = row()
            manifest = root / landmarks.VFS.lstrip("/") / "manifest.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({"landmarks": [r]}))
            self.assertEqual(landmarks.load_catalog(root), ())
            for key in ("shape", "materials"):
                p = root / r[key].lstrip("/")
                p.parent.mkdir(parents=True, exist_ok=True)
                p.touch()
            good = landmarks.load_catalog(root)
            self.assertEqual(len(good), 1)
            with patch.object(landmarks, "catalog", return_value=good):
                landmarks._excluded.cache_clear()
                self.assertTrue(landmarks.excluded_feature("exact-id"))
                self.assertFalse(landmarks.excluded_feature("exact-id-part"))
            landmarks._excluded.cache_clear()
            for bad in ({"shape": landmarks.VFS + "test/../other.cdae"}, {"lat": float("nan")},
                        {"lon": True}, {"range_m": -1}, {"enabled": False}, {"exclude_ids": "not-a-list"}):
                manifest.write_text(json.dumps({"landmarks": [dict(r, **bad)]}))
                self.assertEqual(landmarks.load_catalog(root), (), bad)

    def test_selection_uses_both_foci_and_honors_far_range(self):
        frame = geodesy.EnuFrame(38.889, -77.035)
        r = row()
        self.assertEqual(landmarks.select([r], frame, (0, 0)), (r,))
        self.assertEqual(landmarks.select([r], frame, (5000, 0)), ())
        self.assertEqual(landmarks.select([r], frame, (5000, 0), (0, 0)), (r,))
        r["range_m"] = 40000
        self.assertEqual(landmarks.select([r], frame, (20000, 0)), (r,))

    def test_grading_and_earth_curvature_are_in_anchor_coordinates(self):
        frame = geodesy.EnuFrame(38.889, -77.3)
        order = []
        def sample(la, lo, z):
            order.append("dem")
            return np.full_like(la, 42)
        def ground(frame, la, lo, z):
            order.append("airport_sea")
            return z + 3
        def grade(la, lo, z):
            order.append("road_grade")
            return z - 2
        server = SimpleNamespace(dem=SimpleNamespace(sample=sample), ground=ground,
                                 grader_for=lambda a: SimpleNamespace(apply=grade))
        anchor = SimpleNamespace(frame=frame, zooms={0: 14})
        got = landmarks.grounded(server, anchor, row(ground_offset_m=0.5))
        np.testing.assert_allclose([got[k] for k in ("x", "y", "z")], frame.geodetic_to_enu(38.889, -77.035, 43.5))
        self.assertEqual(order, ["dem", "airport_sea", "road_grade"])
        self.assertNotAlmostEqual(got["z"], 43.5)  # tangent-plane curvature is not lost at long range


class TrackerTest(unittest.IsolatedAsyncioTestCase):
    def tracker(self):
        tracker = landmarks.Tracker([row()])
        self.addCleanup(tracker.close)
        return tracker

    def connection(self):
        frame = geodesy.EnuFrame(38.889, -77.035)
        self.jobs, self.messages = [], []
        return SimpleNamespace(s=SimpleNamespace(anchors={1: SimpleNamespace(frame=frame)}, ground_pool=None),
                               loop=asyncio.get_running_loop(), active=1, terrain_wait=0, teleport_pending=False,
                               spawn=lambda coro: self.jobs.append(asyncio.create_task(coro)), send=self.messages.append)

    async def test_membership_changes_only_and_protocol_roundtrip(self):
        conn = self.connection()
        tracker = self.tracker()
        with patch.object(landmarks, "grounded", side_effect=lambda s, a, r: dict(r, x=0, y=0, z=42)) as ground:
            tracker.on_pose(conn, 1, (0, 0))
            await asyncio.gather(*self.jobs)
            self.assertEqual(len(self.messages), 1)
            self.assertEqual(protocol.Decoder().feed(protocol.encode(self.messages[0])), self.messages)
            tracker.on_pose(conn, 1, (10, 0))
            self.assertEqual(len(self.jobs), 1)
            tracker.on_pose(conn, 1, (5000, 0))
            await asyncio.gather(*self.jobs)
            self.assertEqual(self.messages[-1]["rows"], [])
            tracker.on_pose(conn, 1, (0, 0))
            await asyncio.gather(*self.jobs)
            self.assertEqual(ground.call_count, 1, "fixed anchor ground is cached across range churn")

    async def test_terrain_priority_and_stale_anchor_results(self):
        conn = self.connection()
        tracker = self.tracker()
        conn.terrain_wait = 1
        tracker.on_pose(conn, 1, (0, 0))
        self.assertFalse(self.jobs)
        conn.terrain_wait = 0
        with patch.object(landmarks, "grounded", return_value={"id": "test", "z": 42}):
            tracker.on_pose(conn, 1, (0, 0))
            conn.active = 2
            await asyncio.gather(*self.jobs)
            self.assertFalse(self.messages)
        self.assertFalse(tracker.busy)

    async def test_range_change_supersedes_queued_result(self):
        conn = self.connection()
        tracker = self.tracker()
        with patch.object(landmarks, "grounded", return_value={"id": "test"}):
            tracker.on_pose(conn, 1, (0, 0))
            tracker.on_pose(conn, 1, (5000, 0))
            await asyncio.gather(*self.jobs)
            self.assertFalse(self.messages)
            tracker.on_pose(conn, 1, (5000, 0))
            await asyncio.gather(*self.jobs)
            self.assertEqual(self.messages[0]["rows"], [])

    async def test_failed_dem_has_backoff_but_leaving_range_still_clears(self):
        conn = self.connection()
        tracker = self.tracker()
        with patch.object(landmarks, "grounded", side_effect=OSError("offline")) as ground:
            tracker.on_pose(conn, 1, (0, 0))
            await asyncio.gather(*self.jobs)
            tracker.on_pose(conn, 1, (0, 0))
            self.assertEqual(len(self.jobs), 1, "do not hammer a failed DEM every pose")
            tracker.on_pose(conn, 1, (5000, 0))
            await asyncio.gather(*self.jobs)
            self.assertEqual(self.messages[0]["rows"], [])
            self.assertEqual(ground.call_count, 1)

    async def test_separate_worker_and_close_discards_pending_work(self):
        conn = self.connection()
        tracker = self.tracker()
        workers = []
        def ground(s, a, r):
            workers.append(threading.current_thread().name)
            return {"id": r["id"]}
        with patch.object(landmarks, "grounded", side_effect=ground):
            tracker.on_pose(conn, 1, (0, 0))
            await asyncio.gather(*self.jobs)
        self.assertTrue(workers[0].startswith("landmarks"))
        self.assertEqual(tracker.pool._max_workers, 1)
        tracker.close()
        self.assertIsNone(tracker.pool)
        tracker.on_pose(conn, 1, (5000, 0))
        self.assertEqual(len(self.jobs), 1)

        tracker = self.tracker()
        with patch.object(landmarks, "grounded") as ground:
            tracker.on_pose(conn, 1, (0, 0))
            tracker.close()  # disconnect before the queued coroutine can submit its work
            await asyncio.gather(*self.jobs)
            ground.assert_not_called()
        self.assertFalse(tracker.busy)


if __name__ == "__main__":
    unittest.main()
