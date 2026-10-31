"""Provisional quality follows futures and cannot poison restart-safe input state."""
from concurrent.futures import Future
from pathlib import Path
import sys
import tempfile
import threading
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dem as demmod


class ProvisionalDem(demmod.Dem):
    def __init__(self, directory):
        super().__init__(directory, fetch_workers=1)
        self.calls = {}
        self._mirror_get = lambda key: None
        self._mirror_put = lambda key, array: None

    def _make_tile(self, key):
        n = self.calls[key] = self.calls.get(key, 0) + 1
        if key[1] == 1 and n == 1:
            self._partial()
            return np.full((demmod.TILE, demmod.TILE), 3, np.float32)
        if key[0] == 2:
            return self._from_ancestor(*key, 1)
        return np.full((demmod.TILE, demmod.TILE), 7, np.float32)


class ProvisionalResultTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.dem = ProvisionalDem(self.directory.name)
        for pool in (self.dem.pool, self.dem.urgent_pool, self.dem.probe_pool):
            self.addCleanup(pool.shutdown, wait=True)

    def test_partial_is_retried_then_complete_is_cached_in_same_instance(self):
        key = (1, 1, 0)
        first = self.dem.tiles([key])[key]
        self.dem.pool.submit(lambda: None).result()  # callback barrier
        self.assertEqual(float(first[0, 0]), 3)
        self.assertNotIn(key, self.dem._lru)
        epoch = self.dem.invalidation_epoch
        second = self.dem.tiles([key])[key]
        self.dem.pool.submit(lambda: None).result()
        self.assertEqual(float(second[0, 0]), 7)
        self.assertGreater(self.dem.invalidation_epoch, epoch)
        self.dem.tiles([key])
        self.assertEqual(self.dem.calls[key], 2)

    def test_worker_quality_does_not_leak_to_next_unrelated_complete_tile(self):
        self.dem.tiles([(1, 1, 0)])
        self.dem.pool.submit(lambda: None).result()
        clean = (1, 2, 0)
        self.dem.tiles([clean])
        self.dem.pool.submit(lambda: None).result()
        self.assertIn(clean, self.dem._lru)
        self.assertNotIn((1, 1, 0), self.dem._lru)

    def test_partial_ancestor_is_not_frozen_in_parent_or_child(self):
        child, ancestor = (2, 2, 0), (1, 1, 0)
        self.dem.tiles([child])
        self.dem.pool.submit(lambda: None).result()
        self.assertNotIn(child, self.dem._lru)
        self.assertNotIn(ancestor, self.dem._lru)
        self.assertEqual(float(self.dem.tiles([child])[child][0, 0]), 7)

    def test_explicit_snapshot_change_rejects_late_old_future(self):
        key = (1, 3, 0)
        old = Future()
        old.set_result(demmod._TileLoad(np.zeros((2, 2)), False, 0))
        self.dem._inflight[key] = old
        self.dem.invalidate_snapshot()
        self.dem._finish(key, old)
        self.assertNotIn(key, self.dem._lru)
        self.assertGreater(self.dem.invalidation_epoch, 0)

    def test_epoch_is_visible_before_delayed_callback_completes(self):
        entered, release = threading.Event(), threading.Event()
        original = self.dem._finish
        def delayed(key, future):
            entered.set()
            release.wait(2)
            original(key, future)
        self.dem._finish = delayed
        self.addCleanup(release.set)
        value = self.dem.tiles([(1, 1, 0)])[(1, 1, 0)]
        self.assertEqual(float(value[0, 0]), 3)
        self.assertGreater(self.dem.invalidation_epoch, 0)
        epoch = self.dem.invalidation_epoch
        release.set()
        self.dem.pool.submit(lambda: None).result()
        self.assertEqual(self.dem.invalidation_epoch, epoch)

    def test_polar_failure_and_upgrade_advance_epoch_before_return(self):
        class Polar:
            calls = 0
            def sample(self, lat, lon, pixel):
                self.calls += 1
                if self.calls == 1:
                    raise OSError('fixture unavailable')
                return np.full(np.shape(lat), 18.0)
        self.dem._polar = Polar()
        self.assertEqual(float(self.dem.sample(89.0, 0.0, 12)), 0.0)
        first = self.dem.invalidation_epoch
        self.assertGreater(first, 0)
        self.assertEqual(float(self.dem.sample(89.0, 0.0, 12)), 18.0)
        self.assertGreater(self.dem.invalidation_epoch, first)


if __name__ == '__main__':
    unittest.main()
